"""One inference slot per process; scale CPU processes or add a worker per GPU."""
import logging
import html
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import psutil

from .config import WHISPER_DEVICE, WHISPER_MODEL
from .database import LeaseLost, now
from .models import Options
from .pipeline import analyze, caption_time, save_json, write_exports

log = logging.getLogger(__name__)


def export_vtt(folder, context):
    cues = [f"{caption_time(s['start_seconds']).replace(',', '.')} --> {caption_time(s['end_seconds']).replace(',', '.')}\n{html.escape(s['text'])}"
            for s in context.get("speech_segments", [])]
    target = Path(folder) / "captions/speech.vtt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("WEBVTT\n\n" + "\n\n".join(cues) + "\n", encoding="utf-8")


class Worker:
    def __init__(self, database, storage, data, queues=None, analyze_fn=analyze):
        self.db, self.storage, self.data = database, storage, Path(data)
        default = "default,gpu" if WHISPER_DEVICE == "cuda" else "default,cpu"
        self.queues = queues or os.getenv("VIDEO_CONTEXT_WORKER_QUEUES", default).split(",")
        self.id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.stop_event = threading.Event()
        self.heartbeat_stop = threading.Event()
        self.current = None
        self.lost = threading.Event()
        self.analyze = analyze_fn
        self.thread = None
        self.process = psutil.Process()
        self.process.cpu_percent()

    def resources(self):
        process = self.process
        payload = {"hostname": socket.gethostname(), "queues": self.queues, "device": WHISPER_DEVICE,
                "model": WHISPER_MODEL, "job_id": self.current,
                "memory_mb": round(process.memory_info().rss / 1024**2, 1),
                "cpu_percent": process.cpu_percent(), "cpu_threads": process.num_threads(),
                "heartbeat_at": time.time(), "stopping": self.stop_event.is_set()}
        if WHISPER_DEVICE == "cuda":
            try:
                output = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits", "--id=" + os.getenv("VIDEO_CONTEXT_GPU_INDEX", "0")],
                    check=True, capture_output=True, text=True, timeout=2).stdout
                used, total, utilization = (float(value.strip()) for value in output.strip().split(","))
                payload.update(gpu_memory_used_mb=used, gpu_memory_total_mb=total, gpu_utilization_percent=utilization)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        return payload

    def heartbeat(self):
        while not self.heartbeat_stop.is_set():
            job_id = self.current
            try:
                self.db.heartbeat(self.id, self.resources(), job_id)
            except LeaseLost:
                if self.current == job_id:
                    self.lost.set()
            except Exception:
                log.exception("Worker heartbeat failed")
            self.heartbeat_stop.wait(min(5, self.db.lease_seconds / 4))

    def progress(self, job_id, stage):
        if self.lost.is_set():
            raise LeaseLost(job_id)
        self.db.update(job_id, owner=self.id, stage=stage)

    def run_one(self):
        record = self.db.claim(self.id, self.queues)
        if not record:
            return False
        self.current = record["id"]
        self.lost.clear()
        work_root = self.data / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix=f"{record['id']}-", dir=work_root) as temporary:
                folder = Path(temporary)
                source = folder / ("source" + record.get("input_suffix", ".mp4"))
                self.storage.download(record["input_key"], source)
                output = folder / record["id"]
                context = self.analyze(source, output, Options.model_validate(record["options"]),
                                       lambda stage: self.progress(record["id"], stage))
                if record.get("input_sha256") and context.get("source") and context["source"].get("sha256") != record["input_sha256"]:
                    raise ValueError("Stored input checksum differs from the submitted source")
                output.mkdir(exist_ok=True)
                if context.get("source"):
                    context["source"]["file_name"] = record["source_file"]
                    write_exports(output, context)
                    save_json(output / "context.json", context)
                export_vtt(output, context)
                prefix = f"results/{record['id']}/attempt-{record['attempt']}/"
                manifest = []
                for path in output.rglob("*"):
                    if path.is_file() and path.suffix != ".tmp":
                        self.progress(record["id"], "saving artifacts")
                        relative = path.relative_to(output).as_posix()
                        self.storage.put_file(prefix + relative, path)
                        manifest.append({"path": relative, "bytes": path.stat().st_size})
                self.db.update(record["id"], owner=self.id, status=context["status"], stage="finished",
                    elapsed_seconds=context["elapsed_seconds"], finished_at=now(),
                    result_prefix=prefix, artifacts=manifest, source=context.get("source"),
                    warnings=context.get("warnings", []), stages=context.get("stages", {}))
        except LeaseLost:
            log.warning("Lost lease for job %s; output will not be published", record["id"])
        except Exception as error:
            log.exception("Job %s failed", record["id"])
            try:
                self.db.update(record["id"], owner=self.id, status="failed", stage="failed", error=str(error), finished_at=now())
            except LeaseLost:
                pass
        finally:
            self.current = None
            self.lost.clear()
        return True

    def run(self, once=False):
        heart = threading.Thread(target=self.heartbeat, daemon=True)
        heart.start()
        try:
            while not self.stop_event.is_set():
                try:
                    worked = self.run_one()
                    if once:
                        break
                    if not worked:
                        self.stop_event.wait(float(os.getenv("VIDEO_CONTEXT_POLL_SECONDS", "1")))
                except Exception:
                    log.exception("Queue polling failed")
                    self.stop_event.wait(2)
        finally:
            self.stop_event.set()
            self.heartbeat_stop.set()
            heart.join(timeout=10)

    def start(self):
        self.thread = threading.Thread(target=self.run, name="video-worker", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
