import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from video_context.database import Database, LeaseLost, QueueFull, jobs, now
from video_context.service import create_app
from video_context.settings import Settings
from video_context.worker import export_vtt
from test_pipeline import video


def record(job_id, queue="default"):
    return {"id": job_id, "status": "queued", "stage": "queued", "queue": queue,
            "created_at": now(), "source_file": "video.mp4", "options": {}}


def test_concurrent_claims_and_restart(tmp_path):
    url = "sqlite:///" + (tmp_path / "service.db").as_posix()
    db = Database(url, capacity=10)
    for i in range(10):
        db.reserve(record(str(i)))
    with pytest.raises(QueueFull):
        db.reserve(record("overflow"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda n: db.claim(f"worker-{n}", ["default"]), range(10)))
    assert len({r["id"] for r in claimed}) == 10
    db.close()
    db = Database(url)
    assert len(db.snapshot()["running"]) == 10
    db.close()


def test_lease_recovery_fences_expired_worker_and_respects_queue(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "service.db").as_posix(), max_attempts=2)
    db.reserve(record("gpu-job", "gpu"))
    assert db.claim("cpu", ["cpu"]) is None
    first = db.claim("old-worker", ["gpu"])
    assert first["attempt"] == 1
    with db.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == "gpu-job").values(lease_until=time.time() - 1))
    with pytest.raises(LeaseLost):
        db.update("gpu-job", owner="old-worker", status="complete")
    second = db.claim("new-worker", ["gpu"])
    assert second["attempt"] == 2
    with pytest.raises(LeaseLost):
        db.heartbeat("old-worker", {}, "gpu-job")
    with db.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == "gpu-job").values(lease_until=time.time() - 1))
    assert db.claim("third-worker", ["gpu"]) is None
    assert db.get("gpu-job")["status"] == "failed"
    db.close()


def test_persistent_queued_job_is_not_lost_on_api_restart(tmp_path):
    settings = Settings(data=tmp_path, embedded_worker=False)
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"source bytes")
    with TestClient(create_app(settings=settings)) as client:
        job = client.post("/v1/jobs", json={"path": str(path)}).json()
    with TestClient(create_app(settings=settings)) as client:
        assert client.get(f"/v1/jobs/{job['id']}").json()["status"] == "queued"
        assert client.get("/v1/jobs").json()["counts"]["queued"] == 1


def test_auth_cookie_api_key_csrf_and_private_artifacts(tmp_path):
    settings = Settings(data=tmp_path, embedded_worker=False, api_key="test-secret")
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ui/").status_code == 200
        assert client.get("/v1/jobs").status_code == 401
        assert client.get("/docs").status_code == 401
        assert client.post("/session", json={"api_key": "bad"}).status_code == 401
        assert client.post("/session", json={"api_key": "کلید نادرست"}).status_code == 401
        assert client.get("/v1/jobs", headers={"x-api-key": "test-secret"}).status_code == 200
        response = client.post("/session", json={"api_key": "test-secret"})
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/v1/jobs").status_code == 200
        spec = client.get("/openapi.json").json()
        assert spec["components"]["securitySchemes"]["ServiceKey"]["name"] == "X-API-Key"
        assert spec["paths"]["/v1/jobs"]["get"]["security"] == [{"ServiceKey": []}]
        assert client.post("/v1/jobs/x/cancel", headers={"origin": "https://example.com"}).status_code == 403
        client.delete("/session")
        assert client.get("/v1/jobs").status_code == 401


def test_input_dedup_range_cancel_retry_and_path_policy(tmp_path):
    settings = Settings(data=tmp_path, embedded_worker=False)
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"0123456789")
    with TestClient(create_app(settings=settings)) as client:
        first = client.post("/v1/jobs", json={"path": str(path)}).json()
        second = client.post("/v1/jobs", json={"path": str(path)}).json()
        assert len(list((tmp_path / "objects/inputs").rglob("source.mp4"))) == 1
        base = f"/v1/jobs/{first['id']}"
        video = client.get(base + "/video", headers={"range": "bytes=2-5"})
        assert video.status_code == 206 and video.content == b"2345"
        assert video.headers["content-range"] == "bytes 2-5/10"
        assert client.get(base + "/video", headers={"range": "bytes=99-"}).status_code == 416
        assert client.post(base + "/cancel").json()["status"] == "cancelled"
        fresh = client.post(base + "/retry").json()
        assert fresh["id"] not in (first["id"], second["id"])
        assert fresh["retry_of"] == first["id"]
        assert client.post(base + "/cancel").status_code == 409
        assert client.get("/ui/jobs/" + fresh["id"], headers={"accept": "text/html"}).status_code == 200
        assert client.get("/v1/jobs?status=cancelled").json()["total_history"] == 1
    with TestClient(create_app(settings=replace(settings, allow_path=False))) as client:
        assert client.post("/v1/jobs", json={"path": str(path)}).status_code == 403


def test_upload_limit_and_web_captions(tmp_path):
    settings = Settings(data=tmp_path, embedded_worker=False, max_upload_bytes=8)
    with TestClient(create_app(settings=settings)) as client:
        assert client.post("/v1/jobs/upload", files={"file": ("video.mp4", b"x" * 9)}).status_code == 413
        assert not list((tmp_path / "incoming").glob("*"))
    export_vtt(tmp_path, {"speech_segments": [{"start_seconds": 1.25, "end_seconds": 3, "text": "سلام <graph>"}]})
    text = (tmp_path / "captions/speech.vtt").read_text(encoding="utf-8")
    assert text.startswith("WEBVTT\n")
    assert "00:00:01.250 --> 00:00:03.000" in text
    assert "سلام &lt;graph&gt;" in text


def test_stored_video_integrity_checked_before_publication(tmp_path, video):
    from video_context.worker import Worker
    settings = Settings(data=tmp_path, embedded_worker=False)
    with TestClient(create_app(settings=settings)) as client:
        job = client.post("/v1/jobs", json={"path": str(video), "options": {"speech": False, "ocr": False}}).json()
        manager = client.app.state.jobs
        saved = manager.get(job["id"])
        path = manager.storage.path(saved["input_key"])
        with path.open("ab") as handle:
            handle.write(b"modified after submission")
        worker = Worker(manager.db, manager.storage, settings.data)
        assert worker.run_one()
        record = manager.get(job["id"])
        assert record["status"] == "failed"
        assert "checksum" in record["error"]
        assert not record.get("result_prefix")


def test_unicode_filename_and_literal_history_search(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "service.db").as_posix())
    job = record("persian-job")
    job["source_file"] = "درس الگوریتم.mp4"
    db.reserve(job)
    db.update(job["id"], status="complete")
    assert db.snapshot(search="الگوریتم")["total_history"] == 1
    assert db.snapshot(search="persian")["total_history"] == 1
    assert db.snapshot(search="%")["total_history"] == 0
    db.close()
