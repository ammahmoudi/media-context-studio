"""One inference slot per process; scale CPU processes or add a worker per GPU."""
import logging
import html
import os
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path

import psutil

from .config import WHISPER_DEVICE, WHISPER_MODEL
from .database import LeaseLost, now
from .models import Options
from .pipeline import analyze, caption_time, save_json, speech_model, write_exports
from .resources import ResourceGuard, ResourceExhausted, check_disk, is_resource_error, number

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
        self.guard = ResourceGuard(self.data, WHISPER_DEVICE)
        domain_setting = "VIDEO_CONTEXT_GPU_RESOURCE_DOMAIN" if WHISPER_DEVICE == "cuda" else "VIDEO_CONTEXT_CPU_RESOURCE_DOMAIN"
        self.resource_domain = os.getenv("VIDEO_CONTEXT_RESOURCE_DOMAIN", os.getenv(domain_setting, socket.gethostname() + ":" + WHISPER_DEVICE))
        self.max_parallel = int(number("MAX_PARALLEL", 1, 1))
        self.blocked_reason = None
        self.resource_error = None

    def resources(self):
        process = self.process
        payload = {"hostname": socket.gethostname(), "queues": self.queues, "device": WHISPER_DEVICE,
                "model": WHISPER_MODEL, "job_id": self.current,
                "memory_mb": round(process.memory_info().rss / 1024**2, 1),
                "cpu_percent": process.cpu_percent(), "cpu_threads": process.num_threads(),
                "heartbeat_at": time.time(), "stopping": self.stop_event.is_set()}
        payload.update(self.guard.sample(), blocked_reason=self.blocked_reason,
                       resource_domain=self.resource_domain, max_parallel=self.max_parallel)
        return payload

    def heartbeat(self):
        while not self.heartbeat_stop.is_set():
            job_id = self.current
            try:
                if job_id:
                    try:
                        self.guard.check()
                    except ResourceExhausted as error:
                        if self.current == job_id:
                            self.resource_error = error
                self.db.heartbeat(self.id, self.resources(), job_id)
            except LeaseLost:
                if self.current == job_id:
                    self.lost.set()
                # Continue reporting a draining/cancelled native call so another
                # replica cannot take its hardware slot prematurely.
                try:
                    self.db.heartbeat(self.id, self.resources())
                except Exception:
                    log.exception("Draining worker heartbeat failed")
            except Exception:
                log.exception("Worker heartbeat failed")
            self.heartbeat_stop.wait(min(2, self.db.lease_seconds / 4))

    def progress(self, job_id, stage):
        if self.lost.is_set():
            raise LeaseLost(job_id)
        if self.resource_error:
            raise self.resource_error
        self.guard.check(gpu=False)
        self.db.update(job_id, owner=self.id, stage=stage)

    def run_one(self):
        # Housekeeping must continue even while this worker is under pressure.
        self.db.claim(self.id, self.queues, admit=False)
        try:
            self.guard.check(starting=True, model_loaded=speech_model.cache_info().currsize > 0)
            self.blocked_reason = None
        except ResourceExhausted as error:
            self.blocked_reason = str(error)
            return False
        record = self.db.claim(self.id, self.queues, self.resource_domain, self.max_parallel, fail_on_worker_loss=True)
        if not record:
            return False
        self.current = record["id"]
        self.lost.clear()
        self.resource_error = None
        self.guard.reset()
        work_root = self.data / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix=f"{record['id']}-", dir=work_root) as temporary:
                folder = Path(temporary)
                source = folder / ("source" + record.get("input_suffix", ".mp4"))
                check_disk(folder, record.get("input_bytes", 0) * 2)
                self.storage.download(record["input_key"], source)
                output = folder / record["id"]
                context = self.analyze(source, output, Options.model_validate(record["options"]),
                                       lambda stage: self.progress(record["id"], stage))
                if record.get("input_sha256") and context.get("source") and context["source"].get("sha256") != record["input_sha256"]:
                    raise ValueError("Stored input checksum differs from the submitted source")
                output.mkdir(exist_ok=True)
                if context.get("source"):
                    context["source"]["file_name"] = record["source_file"]
                    context["resources"] = self.guard.sample()
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
                    warnings=context.get("warnings", []), stages=context.get("stages", {}), resources=self.guard.sample())
        except LeaseLost:
            log.warning("Lost lease for job %s; output will not be published", record["id"])
        except Exception as error:
            log.exception("Job %s failed", record["id"])
            try:
                self.db.update(record["id"], owner=self.id, status="failed", stage="failed", error=str(error),
                    error_code="resource_exhausted" if is_resource_error(error) else "processing_failed",
                    resources=self.guard.sample(gpu=False), finished_at=now())
            except LeaseLost:
                pass
        finally:
            self.current = None
            self.lost.clear()
            self.resource_error = None
            try:
                self.db.heartbeat(self.id, self.resources())
            except Exception:
                log.exception("Could not release worker resource slot")
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
