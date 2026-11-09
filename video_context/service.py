import json
import math
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import DATA, MODELS, WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL, prepare_dirs
from .models import AnalyzeRequest, Options
from .pipeline import analyze, save_json


def now():
    return datetime.now(timezone.utc).isoformat()


class Jobs:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-context")
        self.slots = threading.BoundedSemaphore(8)
        self.lock = threading.Lock()
        self.records = {}
        for file in root.glob("*/job.json"):
            try:
                job = json.loads(file.read_text(encoding="utf-8"))
                if job["status"] in {"queued", "running"}:
                    job.update(status="failed", error="Service restarted before job completed", updated_at=now())
                    save_json(file, job)
                    context_file = file.parent / "context.json"
                    if context_file.is_file():
                        context = json.loads(context_file.read_text(encoding="utf-8"))
                        context["status"] = "partial"
                        context.setdefault("warnings", []).append(job["error"])
                        save_json(context_file, context)
                self.records[job["id"]] = job
            except (ValueError, KeyError):
                continue

    def get(self, job_id):
        with self.lock:
            if job_id not in self.records:
                raise HTTPException(404, "Unknown job ID")
            return self.records[job_id].copy()

    def update(self, job_id, **values):
        with self.lock:
            self.records[job_id].update(**values, updated_at=now())
            save_json(self.root / job_id / "job.json", self.records[job_id])

    def snapshot(self, limit, offset):
        with self.lock:
            records = [record.copy() for record in self.records.values()]
        running = [r for r in records if r["status"] == "running"]
        queued = [dict(r, queue_position=i) for i, r in enumerate(
            (r for r in records if r["status"] == "queued"), 1)]
        history = [r for r in records if r["status"] not in {"running", "queued"}]
        history.sort(key=lambda r: datetime.fromisoformat(r["created_at"]).timestamp()
                     if r.get("created_at") else 0, reverse=True)
        return {"running": running, "queued": queued, "history": history[offset:offset + limit],
                "total_history": len(history), "limit": limit, "offset": offset,
                "counts": {status: sum(r["status"] == status for r in records)
                           for status in ("running", "queued", "complete", "partial", "failed")},
                "capacity": 8, "available_slots": max(0, 8 - len(running) - len(queued))}

    def submit(self, path, options, job_id=None):
        if not self.slots.acquire(blocking=False):
            raise HTTPException(429, "Job queue is full; retry after a job finishes")
        job_id = job_id or uuid.uuid4().hex
        folder = self.root / job_id
        folder.mkdir(exist_ok=True)
        record = {"id": job_id, "status": "queued", "stage": "queued", "created_at": now(),
                  "updated_at": now(), "source_file": path.name,
                  "result_url": f"/v1/jobs/{job_id}/result", "options": options.model_dump()}
        try:
            with self.lock:
                self.records[job_id] = record
                save_json(folder / "job.json", record)
                # Keep visible queue order aligned with executor submission order.
                self.executor.submit(self._work, job_id, path, options)
        except Exception:
            self.slots.release()
            raise
        return record.copy()

    def _work(self, job_id, path, options):
        try:
            self.update(job_id, status="running", started_at=now())
            result = analyze(path, self.root / job_id, options,
                             lambda stage: self.update(job_id, stage=stage))
            self.update(job_id, status=result["status"], stage="finished", elapsed_seconds=result["elapsed_seconds"], finished_at=now())
        except Exception as error:
            self.update(job_id, status="failed", stage="failed", error=str(error), finished_at=now())
        finally:
            self.slots.release()


def create_app(data_root=DATA):
    @asynccontextmanager
    async def lifespan(app):
        prepare_dirs()
        app.state.jobs = Jobs(Path(data_root) / "jobs")
        yield
        app.state.jobs.executor.shutdown(wait=True)

    app = FastAPI(title="Local Video Context", version="0.1.0", lifespan=lifespan, docs_url=None,
                  description="Local course video extraction. Submit a video, poll its job, retrieve timestamped evidence.")
    # This local-file API is deliberately limited to local hostnames; no browser CORS access.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    static = Path(__file__).parent / "static"
    app.mount("/ui", StaticFiles(directory=static, html=True, check_dir=False), name="dashboard")

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/ui/")

    @app.get("/docs", include_in_schema=False)
    def swagger():
        return get_swagger_ui_html(openapi_url="/openapi.json", title="Video Context API · Swagger",
                                   swagger_js_url="/ui/swagger/swagger-ui-bundle.js",
                                   swagger_css_url="/ui/swagger/swagger-ui.css",
                                   swagger_favicon_url="/ui/swagger/favicon-32x32.png")

    @app.get("/v1/jobs", tags=["Jobs"])
    def list_jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        """Live running jobs, FIFO queue positions, and paginated newest-first history."""
        return app.state.jobs.snapshot(limit, offset)

    @app.get("/health")
    def health():
        return {"status": "ok", "ffmpeg": bool(shutil.which("ffmpeg")),
                "ffprobe": bool(shutil.which("ffprobe")), "worker_count": 1,
                "speech": {"model": WHISPER_MODEL, "device": WHISPER_DEVICE, "compute_type": WHISPER_COMPUTE},
                "prepared_ocr_profiles": sorted(p.stem for p in (MODELS / "ocr").glob("*.json"))}

    @app.post("/v1/jobs", status_code=202)
    def submit(request: AnalyzeRequest):
        path = Path(request.path).expanduser().resolve()
        if not path.is_file():
            raise HTTPException(422, "Local video file does not exist")
        return app.state.jobs.submit(path, request.options)

    @app.post("/v1/jobs/upload", status_code=202)
    def upload(file: UploadFile = File(...), options_json: str = Form("{}")):
        try:
            options = Options.model_validate_json(options_json)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        job_id = uuid.uuid4().hex
        folder = app.state.jobs.root / job_id
        folder.mkdir()
        # Keep the original filename only as a safe basename; never trust upload paths.
        name = re.sub(r"[^\w. -]", "_", Path((file.filename or "video.mp4").replace("\\", "/")).name)
        path = folder / ("input_" + name[:160])
        try:
            total = 0
            with path.open("wb") as target:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 8 * 1024**3:
                        raise HTTPException(413, "Upload exceeds 8 GiB; use the local path endpoint")
                    target.write(chunk)
            if not total:
                raise HTTPException(422, "Uploaded file is empty")
            return app.state.jobs.submit(path, options, job_id=job_id)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            file.file.close()

    @app.get("/v1/jobs/{job_id}")
    def status(job_id: str):
        return app.state.jobs.get(job_id)

    @app.get("/v1/jobs/{job_id}/result")
    def result(job_id: str):
        job = app.state.jobs.get(job_id)
        if job["status"] not in {"complete", "partial", "failed"}:
            raise HTTPException(409, "Job is not finished; poll the status endpoint")
        file = app.state.jobs.root / job_id / "context.json"
        if not file.is_file():
            raise HTTPException(409, job.get("error", "No context was produced"))
        return FileResponse(file, media_type="application/json")

    @app.get("/v1/jobs/{job_id}/artifacts/{artifact:path}")
    def artifact(job_id: str, artifact: str):
        app.state.jobs.get(job_id)
        folder = (app.state.jobs.root / job_id).resolve()
        path = (folder / artifact).resolve()
        allowed = (artifact in {"context.json", "context.md", "captions/speech.srt"}
                   or (artifact.startswith("frames/") and path.suffix == ".jpg"))
        if not path.is_relative_to(folder) or not allowed or not path.is_file():
            raise HTTPException(404, "Unknown artifact")
        return FileResponse(path)

    @app.get("/v1/jobs/{job_id}/context")
    def interval(job_id: str, start: float = 0, end: float | None = None):
        app.state.jobs.get(job_id)
        file = app.state.jobs.root / job_id / "context.json"
        if not file.is_file():
            raise HTTPException(409, "Context is not available yet")
        context = json.loads(file.read_text(encoding="utf-8"))
        end = end if end is not None else context["source"]["duration_seconds"]
        if start < 0 or end <= start or not all(map(math.isfinite, (start, end))):
            raise HTTPException(422, "Expected finite times with 0 <= start < end")
        frames = [f for f in context["frames"] if start <= f["timestamp_seconds"] < end]
        ids = {f["id"] for f in frames}
        return {"start_seconds": start, "end_seconds": end,
                "shots": [s for s in context["shots"] if s["start_seconds"] < end and s["end_seconds"] > start],
                "frames": frames,
                "on_screen_text": [t for t in context["on_screen_text"] if ids.intersection(t.get("frame_ids", [t["frame_id"]]))],
                "speech_segments": [s for s in context["speech_segments"] if s["start_seconds"] < end and s["end_seconds"] > start]}

    return app


app = create_app()
