import os
import asyncio
import shutil
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from video_context import pipeline, resources
from video_context.database import Database, LeaseLost, jobs, workers
from video_context.models import Options
from video_context.worker import Worker
from video_context.settings import Settings
from video_context.service import create_app
from video_context import service
from fastapi.testclient import TestClient
from test_durable import record
from test_pipeline import video


def test_cgroup_budget_uses_smaller_available_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.psutil, "virtual_memory", lambda: SimpleNamespace(total=16 * resources.MIB, available=10 * resources.MIB))
    (tmp_path / "memory.max").write_text(str(8 * resources.MIB))
    (tmp_path / "memory.current").write_text(str(7 * resources.MIB))
    assert resources.memory_budget(tmp_path) == {"available_mb": 1, "limit_mb": 8, "scope": "cgroup"}
    (tmp_path / "memory.stat").write_text(f"inactive_file {2 * resources.MIB}\n")
    assert resources.memory_budget(tmp_path)["available_mb"] == 3
    (tmp_path / "memory.max").write_text("max")
    assert resources.memory_budget(tmp_path)["available_mb"] == 10


def test_low_ram_disk_and_missing_gpu_block_admission(tmp_path, monkeypatch):
    guard = resources.ResourceGuard(tmp_path, "cpu")
    monkeypatch.setattr(resources, "memory_budget", lambda: {"available_mb": 100, "limit_mb": 4096, "scope": "cgroup"})
    with pytest.raises(resources.ResourceExhausted, match="RAM pressure"):
        guard.check(starting=True)
    monkeypatch.setattr(resources, "memory_budget", lambda: {"available_mb": 3000, "limit_mb": 4096, "scope": "cgroup"})
    monkeypatch.setattr(resources.shutil, "disk_usage", lambda p: SimpleNamespace(free=100))
    with pytest.raises(resources.ResourceExhausted, match="Scratch disk"):
        guard.check(starting=True)
    monkeypatch.setattr(resources.shutil, "disk_usage", lambda p: SimpleNamespace(free=10 * 1024**3))
    guard.device = "cuda"
    monkeypatch.setattr(resources, "gpu_memory", lambda: {})
    with pytest.raises(resources.ResourceExhausted, match="telemetry unavailable"):
        guard.check(starting=True)


def test_pressure_leaves_job_queued_without_consuming_attempt(tmp_path, monkeypatch):
    db = Database("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    db.reserve(record("waiting"))
    worker = Worker(db, None, tmp_path)
    def pressure(**kwargs):
        raise resources.ResourceExhausted("RAM pressure")
    monkeypatch.setattr(worker.guard, "check", pressure)
    assert not worker.run_one()
    assert db.get("waiting")["status"] == "queued"
    assert worker.blocked_reason == "RAM pressure"
    db.close()


def test_disk_reservation_waits_before_claiming_without_using_attempt(tmp_path, monkeypatch):
    from video_context import worker as worker_module
    db = Database("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    db.reserve(dict(record("disk-wait"), input_bytes=500 * resources.MIB))
    worker = Worker(db, None, tmp_path)
    monkeypatch.setattr(worker.guard, "check", lambda **kwargs: {})
    requested = []
    def no_capacity(path, extra=0):
        requested.append(extra)
        raise resources.ResourceExhausted("Scratch disk needs more space")
    monkeypatch.setattr(worker_module, "check_disk", no_capacity)
    assert not worker.run_one()
    assert requested[0] > 1000 * resources.MIB  # input, decoded audio, frames
    assert db.get("disk-wait")["status"] == "queued"
    assert "Scratch disk" in worker.blocked_reason
    assert db.claim("manual", ["default"])["attempt"] == 1
    db.close()


def test_peek_race_does_not_claim_a_different_unbudgeted_record(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    db.reserve(record("small"))
    db.reserve(record("large"))
    assert db.peek(["default"])["id"] == "small"
    assert db.claim("other", ["default"])["id"] == "small"
    assert db.claim("ours", ["default"], expected_id="small") is None
    assert db.get("large")["status"] == "queued"
    assert db.claim("ours", ["default"], expected_id="large")["attempt"] == 1
    db.close()


def test_runtime_gpu_telemetry_loss_warns_but_new_admission_still_waits(tmp_path, monkeypatch):
    guard = resources.ResourceGuard(tmp_path, "cuda")
    monkeypatch.setattr(resources, "memory_budget", lambda: {"available_mb": 4000, "limit_mb": 8192, "scope": "host"})
    monkeypatch.setattr(resources, "check_disk", lambda *args: None)
    monkeypatch.setattr(resources, "gpu_memory", lambda: {"gpu_memory_used_mb": 3000, "gpu_memory_total_mb": 8000, "gpu_memory_free_mb": 5000})
    guard.check(starting=True)
    monkeypatch.setattr(resources, "gpu_memory", lambda: {})
    assert guard.check()["gpu_telemetry_cached"]
    with pytest.raises(resources.ResourceExhausted, match="telemetry unavailable"):
        guard.check(starting=True)
    guard.last_gpu_at -= 11
    assert "gpu_telemetry_warning" in guard.check()
    with pytest.raises(resources.ResourceExhausted, match="telemetry unavailable"):
        guard.check(starting=True)


def test_gpu_checkpoint_waits_for_fresh_telemetry_without_failing_job(tmp_path, monkeypatch):
    updates = []
    class DB:
        def update(self, job_id, **values):
            updates.append(values["stage"])
    worker = Worker(DB(), None, tmp_path)
    worker.guard.device = "cuda"
    readings = iter([{"gpu_memory_free_mb": 4000, "gpu_telemetry_cached": True},
                     {"gpu_telemetry_warning": "unavailable"},
                     {"gpu_memory_free_mb": 4000, "gpu_telemetry_cached": False}])
    monkeypatch.setattr(worker.guard, "check", lambda gpu=True, **kwargs: next(readings) if gpu else {})
    monkeypatch.setattr(worker.stop_event, "wait", lambda seconds: False)
    worker.progress("job", "speech chunk 120.0/300.0s")
    assert updates == ["waiting for GPU telemetry", "waiting for GPU telemetry", "speech chunk 120.0/300.0s"]
    assert worker.resource_error is None and worker.blocked_reason is None
    # CPU OCR must continue even when GPU monitoring is absent.
    worker.progress("job", "ocr 1/30")
    assert updates[-1] == "ocr 1/30"


def test_confirmed_gpu_pressure_still_fails(tmp_path, monkeypatch):
    guard = resources.ResourceGuard(tmp_path, "cuda")
    monkeypatch.setattr(resources, "memory_budget", lambda: {"available_mb": 4000, "limit_mb": 8192, "scope": "host"})
    monkeypatch.setattr(resources, "check_disk", lambda *args: None)
    monkeypatch.setattr(resources, "gpu_memory", lambda: {"gpu_memory_free_mb": 100, "gpu_memory_used_mb": 7900})
    with pytest.raises(resources.ResourceExhausted, match="GPU pressure"):
        guard.check()


def test_allocation_failure_marks_job_failed_with_peak_evidence(tmp_path, monkeypatch):
    db = Database("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    db.reserve(dict(record("oom"), input_key="source", input_bytes=10))
    class Storage:
        def download(self, key, path):
            path.write_bytes(b"source")
    def allocation_failure(*args):
        raise MemoryError("allocation failed")
    worker = Worker(db, Storage(), tmp_path, analyze_fn=allocation_failure)
    monkeypatch.setattr(worker.guard, "check", lambda **kwargs: {})
    monkeypatch.setattr(worker.guard, "sample", lambda **kwargs: {"peak_memory_mb": 42})
    assert worker.run_one()
    job = db.get("oom")
    assert job["status"] == "failed" and job["error_code"] == "resource_exhausted"
    assert job["resources"]["peak_memory_mb"] == 42
    assert db.claim("replacement", ["default"]) is None
    assert not list((tmp_path / "work").glob("*"))
    db.close()


def test_shared_resource_slots_atomic_and_crash_requires_manual_retry(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    for i in range(4):
        db.reserve(record(str(i)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: db.claim(str(i), ["default"], "same-gpu", 1, True), range(4)))
    claimed = [r for r in results if r]
    assert len(claimed) == 1
    assert len(db.snapshot()["queued"]) == 3
    first = claimed[0]
    with db.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == first["id"]).values(lease_until=time.time() - 1))
        conn.execute(update(workers).values(seen=time.time() - 121))
    assert db.claim("replacement", ["default"], "same-gpu", 1, True)
    assert db.get(first["id"])["status"] == "failed"
    assert "explicit retry" in db.get(first["id"])["error"]
    db.close()


def test_cancel_keeps_hardware_slot_until_worker_finishes_native_call(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "jobs.db").as_posix())
    db.reserve(record("first"))
    db.reserve(record("second"))
    assert db.claim("old", ["default"], "gpu-0")
    db.cancel("first")
    assert db.claim("new", ["default"], "gpu-0") is None
    db.heartbeat("old", {"resource_domain": "gpu-0", "job_id": None})
    assert db.claim("new", ["default"], "gpu-0")["id"] == "second"
    db.close()


def test_chunked_audio_is_bounded_deduplicates_boundary_words_and_preserves_offset(tmp_path, monkeypatch):
    original = tmp_path / "original.wav"
    with wave.open(str(original), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * (26 * 16000))
    monkeypatch.setattr(pipeline, "run", lambda args: shutil.copyfile(original, args[-1]))
    monkeypatch.setenv("VIDEO_CONTEXT_AUDIO_CHUNK_SECONDS", "10")
    durations, calls = [], []
    global_words = [(8.8, 9.2, " A"), (9.8, 10.2, " boundary"), (10.8, 11.2, " B"),
                    (19.8, 20.2, " C"), (24.8, 25.2, " last")]
    class Model:
        def transcribe(self, audio, **kwargs):
            index = len(durations)
            start = [0, 8, 18][index]
            durations.append(len(audio) / 16000)
            calls.append(kwargs)
            words = [SimpleNamespace(start=a-start, end=b-start, word=t, probability=.9)
                     for a, b, t in global_words if start <= a and b <= start + durations[-1]]
            return iter([SimpleNamespace(start=words[0].start, end=words[-1].end, text="unused", words=words)]), SimpleNamespace(language="en")
    monkeypatch.setattr(pipeline, "speech_model", lambda: Model())
    result = pipeline.transcribe(original, tmp_path, {"duration_seconds": 27, "audio_offset_seconds": 1, "audio_stream_index": 0}, Options())
    assert durations == [12, 14, 8]
    words = [w for s in result for w in s["words"]]
    assert [w["text"] for w in words] == [w[2] for w in global_words]
    assert [w["start_seconds"] for w in words] == pytest.approx([w[0] + 1 for w in global_words])
    assert calls[0]["language"] is None and calls[1]["language"] == "en"
    assert not (tmp_path / "audio.wav").exists()


@pytest.mark.parametrize("error", [MemoryError("allocation failed"), RuntimeError("CUDA out of memory"), LeaseLost("cancelled")])
def test_resource_and_lease_errors_are_not_swallowed_as_partial(video, tmp_path, monkeypatch, error):
    def fail(*args):
        raise error
    monkeypatch.setattr(pipeline, "detect_shots", fail)
    with pytest.raises(type(error)):
        pipeline.analyze(video, tmp_path / "out", Options(ocr=False, speech=False))


def test_media_and_context_limits(tmp_path, monkeypatch):
    with pytest.raises(resources.ResourceExhausted, match="duration"):
        resources.validate_media({"duration_seconds": 21601, "width": 10, "height": 10})
    with pytest.raises(resources.ResourceExhausted, match="pixel"):
        resources.validate_media({"duration_seconds": 1, "width": 8000, "height": 8000})
    monkeypatch.setenv("VIDEO_CONTEXT_MAX_CONTEXT_MB", "1")
    with pytest.raises(resources.ResourceExhausted, match="context"):
        pipeline.save_json(tmp_path / "large.json", {"text": "x" * 1024**2})


def test_chunked_upload_rejected_before_full_multipart_spooling(tmp_path):
    settings = Settings(data=tmp_path, embedded_worker=False, max_upload_bytes=1024)
    def body():
        yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="x.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
        for _ in range(3):
            yield b"x" * 1024**2
        yield b"\r\n--boundary--\r\n"
    with TestClient(create_app(settings=settings)) as client:
        response = client.post("/v1/jobs/upload", content=body(), headers={"content-type": "multipart/form-data; boundary=boundary"})
        assert response.status_code == 413
        assert client.get("/v1/jobs").json()["counts"]["uploading"] == 0


def test_context_backpressure_keeps_health_and_history_available(tmp_path, monkeypatch):
    with TestClient(create_app(settings=Settings(data=tmp_path, embedded_worker=False))) as client:
        client.app.state.result_slots = asyncio.Semaphore(0)
        assert client.get("/v1/jobs/example/result").status_code == 429
        assert client.get("/v1/jobs").status_code == 200
        client.app.state.result_slots = asyncio.Semaphore(1)
        monkeypatch.setattr(service, "memory_budget", lambda: {"available_mb": 10})
        assert client.get("/v1/jobs/example/context").status_code == 503
        assert client.get("/health").status_code == 200


@pytest.mark.skipif(os.name != "nt", reason="Windows job object hard limit")
def test_windows_native_commit_limit_blocks_large_allocation():
    code = "from video_context.resources import install_native_memory_limit; h=install_native_memory_limit();\ntry: a=bytearray(512*1024**2)\nexcept MemoryError: print('blocked')"
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "VIDEO_CONTEXT_NATIVE_MEMORY_LIMIT_MB": "256"}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"
