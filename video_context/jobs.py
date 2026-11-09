"""Job submission and artifact lookup, independent of an embedded worker."""
import json
import hashlib
import logging
import re
import mimetypes
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .database import Database, QueueFull, now
from .storage import Storage
from .worker import Worker, export_vtt

log = logging.getLogger(__name__)


class Jobs:
    def __init__(self, settings, analyze_fn):
        self.settings = settings
        self.root = settings.data / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.database_url, settings.capacity, settings.lease_seconds, settings.max_attempts)
        self.storage = Storage(settings.data / "objects", settings.bucket, settings.endpoint)
        # Import is explicit in production, automatic and idempotent for laptop SQLite.
        if self.db.sqlite and not settings.bucket:
            self.db.import_legacy(self.root)
        self.worker = Worker(self.db, self.storage, settings.data, analyze_fn=analyze_fn) if settings.embedded_worker else None
        if self.worker:
            self.worker.start()

    def close(self):
        if self.worker:
            self.worker.close()
        self.db.close()

    def get(self, job_id):
        record = self.db.get(job_id)
        if record is None:
            raise HTTPException(404, "Unknown job ID")
        return record

    def reserve(self, filename, options, queue):
        job_id = uuid.uuid4().hex
        record = {"id": job_id, "status": "uploading", "stage": "saving input", "queue": queue,
                  "created_at": now(), "updated_at": now(), "source_file": filename,
                  "result_url": f"/v1/jobs/{job_id}/result", "options": options.model_dump(), "attempt": 0}
        try:
            self.db.reserve(record)
        except QueueFull as error:
            raise HTTPException(429, str(error)) from error
        return record

    def ingest(self, path, record):
        suffix = path.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        try:
            key, digest = self.store_input(path, suffix)
            return self.db.update(record["id"], status="queued", stage="queued", input_key=key,
                                  input_suffix=suffix, input_bytes=path.stat().st_size, input_sha256=digest)
        except Exception as error:
            self.db.update(record["id"], status="failed", stage="failed", error=str(error), finished_at=now())
            raise

    def store_input(self, path, suffix):
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        key = f"inputs/{digest}/source{suffix}"
        if not self.storage.exists(key):
            self.storage.put_file(key, path)
        return key, digest

    def submit(self, path, options, queue="default"):
        record = self.reserve(path.name, options, queue)
        return self.ingest(path, record)

    def context(self, record):
        if record.get("result_prefix"):
            return self.storage.read_json(record["result_prefix"] + "context.json")
        if record.get("legacy_dir"):
            file = Path(record["legacy_dir"]) / "context.json"
            if file.is_file():
                return json.loads(file.read_text(encoding="utf-8"))
        raise HTTPException(409, record.get("error") or "Context is not available yet")

    def artifact(self, record, artifact, request):
        allowed = artifact in {"context.json", "context.md", "captions/speech.srt", "captions/speech.vtt"}
        allowed |= artifact.startswith("frames/") and artifact.endswith(".jpg")
        if not allowed or ".." in artifact.split("/") or "\\" in artifact:
            raise HTTPException(404, "Unknown artifact")
        if record.get("result_prefix"):
            return self.storage.response(record["result_prefix"] + artifact, request,
                                         "text/vtt" if artifact.endswith(".vtt") else None)
        if record.get("legacy_dir"):
            folder = Path(record["legacy_dir"]).resolve()
            path = (folder / artifact).resolve()
            if path.is_relative_to(folder) and path.is_file():
                return FileResponse(path, media_type="text/vtt" if artifact.endswith(".vtt") else None)
        raise HTTPException(404, "Unknown artifact")

    def migrate_legacy(self, source_root):
        """Copy first, commit storage pointers last. Original evidence is never removed."""
        self.db.import_legacy(source_root)
        report = []
        for file in sorted(Path(source_root).glob("*/job.json")):
            record = self.get(file.parent.name)
            if record.get("result_prefix") and record.get("input_key"):
                continue
            context_file = file.parent / "context.json"
            if context_file.is_file():
                context = json.loads(context_file.read_text(encoding="utf-8"))
                export_vtt(file.parent, context)
                prefix = f"results/{record['id']}/legacy/"
                manifest = []
                for item in file.parent.rglob("*"):
                    relative = item.relative_to(file.parent).as_posix()
                    if item.is_file() and (relative in ("context.json", "context.md", "captions/speech.srt", "captions/speech.vtt")
                                            or (relative.startswith("frames/") and relative.endswith(".jpg"))):
                        self.storage.put_file(prefix + relative, item)
                        manifest.append({"path": relative, "bytes": item.stat().st_size})
                values = {"result_prefix": prefix, "artifacts": manifest, "source": context.get("source"),
                          "stages": context.get("stages", {}), "warnings": context.get("warnings", [])}
            else:
                values = {}
            # Search only the original basename recorded by our trusted legacy job.
            name = record.get("source_file", "")
            candidates = [file.parent / name, Path(source_root).parent / name,
                          Path(source_root).parent / "course-test" / name, Path(source_root).parent / "sample" / name]
            original = next((p for p in candidates if p.is_file()), None)
            if original:
                key, digest = self.store_input(original, original.suffix)
                values.update(input_key=key, input_suffix=original.suffix, input_bytes=original.stat().st_size, input_sha256=digest)
            if values:
                self.db.update(record["id"], **values)
            report.append({"id": record["id"], "context_migrated": bool(values.get("result_prefix")), "video_migrated": bool(original)})
        return report

    def retry(self, record, queue=None):
        if record["status"] not in ("failed", "partial", "cancelled"):
            raise HTTPException(409, "Only failed, partial or cancelled jobs can be retried")
        if not record.get("input_key"):
            raise HTTPException(409, "Input video unavailable; migrate or upload the video first")
        from .models import Options
        fresh = self.reserve(record["source_file"], Options.model_validate(record["options"]), queue or record["queue"])
        return self.db.update(fresh["id"], status="queued", stage="queued", input_key=record["input_key"],
                              input_suffix=record.get("input_suffix", ".mp4"), input_bytes=record.get("input_bytes"), retry_of=record["id"])
