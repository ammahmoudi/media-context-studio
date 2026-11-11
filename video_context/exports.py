"""Bounded, private ZIP/TAR.GZ exports: no archive-sized RAM or scratch files."""
import io
import json
import re
import tarfile
import zipfile
import zlib
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from .resources import number

CHUNK = 256 * 1024


def allowed(path):
    return path in {"context.json", "context.md", "captions/speech.srt", "captions/speech.vtt"} or bool(
        re.fullmatch(r"frames/[A-Za-z0-9_-]+\.jpg", path))


def catalog(manager, record):
    entries = []
    if record.get("result_prefix"):
        for item in record.get("artifacts", []):
            if allowed(item["path"]):
                entries.append({"path": item["path"], "bytes": item["bytes"],
                                "kind": "frames" if item["path"].startswith("frames/") else "captions" if item["path"].startswith("captions/") else "context",
                                "key": record["result_prefix"] + item["path"]})
    elif record.get("legacy_dir"):
        root = Path(record["legacy_dir"]).resolve()
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and allowed(relative) and path.resolve().is_relative_to(root):
                entries.append({"path": relative, "bytes": path.stat().st_size,
                                "kind": "frames" if relative.startswith("frames/") else "captions" if relative.startswith("captions/") else "context", "local": path})
    if record.get("input_key"):
        suffix = record.get("input_suffix", ".mp4")
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        size = record.get("input_bytes")
        if size is None:
            size = manager.storage.size(record["input_key"])
        entries.append({"path": "source/video" + suffix, "bytes": size,
                        "kind": "video", "key": record["input_key"]})
    unique = {e["path"]: e for e in entries}
    if len(unique) > 4096:
        raise HTTPException(413, "Too many files in this export")
    return list(unique.values())


def select(entries, paths):
    if paths is None:
        selected = entries
    else:
        if not isinstance(paths, list) or len(paths) > 4096 or not all(isinstance(p, str) for p in paths):
            raise HTTPException(422, "files must be a JSON array of up to 4096 catalog paths")
        wanted = set(paths)
        if wanted - {e["path"] for e in entries}:
            raise HTTPException(422, "Selection includes files unavailable for this run")
        selected = [e for e in entries if e["path"] in wanted]
    if not selected:
        raise HTTPException(409 if paths is None else 422, "No files selected or available")
    if sum(e["bytes"] for e in selected) > number("MAX_EXPORT_BYTES", 16 * 1024**3, 1):
        raise HTTPException(413, "Export exceeds configured byte limit; select fewer files")
    return selected


@contextmanager
def open_entry(storage, entry):
    if "local" in entry:
        with entry["local"].open("rb") as stream:
            yield stream
    elif storage.client:
        stream = storage.client.get_object(Bucket=storage.bucket, Key=entry["key"])["Body"]
        try:
            yield stream
        finally:
            stream.close()
    else:
        with storage.path(entry["key"]).open("rb") as stream:
            yield stream


def contents(storage, entry):
    with open_entry(storage, entry) as stream:
        remaining = entry["bytes"]
        while remaining:
            chunk = stream.read(min(CHUNK, remaining))
            if not chunk:
                raise IOError("Artifact ended before its recorded size")
            remaining -= len(chunk)
            yield chunk


class Sink(io.RawIOBase):
    def __init__(self):
        self.pending = deque()
        self.position = 0

    def writable(self):
        return True

    def tell(self):
        return self.position

    def write(self, data):
        self.position += len(data)
        if data:
            self.pending.append(bytes(data))
        return len(data)

    def drain(self):
        while self.pending:
            yield self.pending.popleft()


def archive(storage, record, entries, format):
    metadata = json.dumps({"job_id": record["id"], "source_file": record["source_file"],
                           "status": record["status"], "files": [{"path": e["path"], "bytes": e["bytes"]} for e in entries]},
                          ensure_ascii=False, indent=2).encode("utf-8")
    all_entries = [{"path": "export-manifest.json", "bytes": len(metadata), "data": metadata}, *entries]
    if format == "zip":
        sink = Sink()
        with zipfile.ZipFile(sink, "w", allowZip64=True) as output:
            for entry in all_entries:
                info = zipfile.ZipInfo(entry["path"])
                info.compress_type = zipfile.ZIP_DEFLATED if entry["path"].endswith((".json", ".md", ".srt", ".vtt")) else zipfile.ZIP_STORED
                with output.open(info, "w", force_zip64=True) as target:
                    chunks = [entry["data"]] if "data" in entry else contents(storage, entry)
                    try:
                        for chunk in chunks:
                            target.write(chunk)
                            yield from sink.drain()
                    finally:
                        if hasattr(chunks, "close"):
                            chunks.close()
                yield from sink.drain()
        yield from sink.drain()
    else:
        compressor = zlib.compressobj(3, zlib.DEFLATED, 31)
        for entry in all_entries:
            info = tarfile.TarInfo(entry["path"])
            info.size = entry["bytes"]
            yield compressor.compress(info.tobuf(format=tarfile.PAX_FORMAT))
            chunks = [entry["data"]] if "data" in entry else contents(storage, entry)
            try:
                for chunk in chunks:
                    yield compressor.compress(chunk)
            finally:
                if hasattr(chunks, "close"):
                    chunks.close()
            yield compressor.compress(b"\0" * (-entry["bytes"] % 512))
        yield compressor.compress(b"\0" * 1024)
        yield compressor.flush()
