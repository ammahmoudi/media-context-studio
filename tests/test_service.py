import time
import json
import threading

import pytest
from fastapi.testclient import TestClient

from video_context.service import create_app
from test_pipeline import video


def test_live_queue_capacity_order_and_history(video, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    execution_order = []

    def controlled_analyze(path, folder, options, progress):
        execution_order.append(folder.name)
        entered.set()
        assert release.wait(10)
        return {"status": "complete", "elapsed_seconds": .1}

    monkeypatch.setattr("video_context.service.analyze", controlled_analyze)
    with TestClient(create_app(tmp_path / "queue")) as client:
        jobs = []
        try:
            jobs.append(client.post("/v1/jobs", json={"path": str(video)}).json())
            assert entered.wait(5)
            for _ in range(7):
                jobs.append(client.post("/v1/jobs", json={"path": str(video)}).json())
            snapshot = client.get("/v1/jobs").json()
            assert snapshot["running"][0]["id"] == jobs[0]["id"]
            assert [r["id"] for r in snapshot["queued"]] == [j["id"] for j in jobs[1:]]
            assert [r["queue_position"] for r in snapshot["queued"]] == list(range(1, 8))
            assert snapshot["available_slots"] == 0
            assert client.post("/v1/jobs", json={"path": str(video)}).status_code == 429
            assert client.get("/v1/jobs?limit=0").status_code == 422
            assert client.get("/v1/jobs?offset=-1").status_code == 422
        finally:
            release.set()
        for job in jobs:
            assert finish(client, job)["status"] == "complete"
        assert execution_order == [j["id"] for j in jobs]
        snapshot = client.get("/v1/jobs?limit=2&offset=1").json()
        assert not snapshot["running"] and not snapshot["queued"]
        assert snapshot["counts"]["complete"] == 8
        assert snapshot["available_slots"] == 8
        assert snapshot["total_history"] == 8 and len(snapshot["history"]) == 2
        assert [r["id"] for r in snapshot["history"]] == [jobs[6]["id"], jobs[5]["id"]]
    with TestClient(create_app(tmp_path / "queue")) as client:
        assert client.get("/v1/jobs").json()["counts"]["complete"] == 8


def test_dashboard_and_swagger_routes(tmp_path):
    with TestClient(create_app(tmp_path / "ui")) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"
        assert client.get("/ui/").status_code == 200
        assert client.get("/docs").status_code == 200
        docs = client.get("/docs").text
        assert "/ui/swagger/swagger-ui-bundle.js" in docs
        assert "cdn.jsdelivr.net" not in docs
        assert client.get("/ui/swagger/swagger-ui.css").status_code == 200
        assert client.get("/ui/swagger/swagger-ui-bundle.js").status_code == 200
        assert "get" in client.get("/openapi.json").json()["paths"]["/v1/jobs"]
        assert client.get("/ui/missing.js").status_code == 404


def finish(client, job):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = client.get(f"/v1/jobs/{job['id']}").json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(.05)
    pytest.fail("Job did not complete")


def test_job_api_upload_artifacts_and_restart(video, tmp_path):
    root = tmp_path / "service"
    with TestClient(create_app(root)) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/v1/jobs", json={"path": "missing.mp4"}).status_code == 422
        response = client.post("/v1/jobs", json={"path": str(video), "options": {"ocr": False}})
        assert response.status_code == 202
        job = response.json()
        assert finish(client, job)["status"] == "complete"
        result = client.get(job["result_url"]).json()
        assert len(result["shots"]) == 2
        scoped = client.get(f"/v1/jobs/{job['id']}/context", params={"start": 2, "end": 4}).json()
        assert len(scoped["shots"]) == 1
        assert all(f["timestamp_seconds"] >= 2 for f in scoped["frames"])
        assert client.get(f"/v1/jobs/{job['id']}/context", params={"start": 4, "end": 1}).status_code == 422
        assert client.get(f"/v1/jobs/{job['id']}/artifacts/context.md").status_code == 200
        assert client.get(f"/v1/jobs/{job['id']}/artifacts/input.mp4").status_code == 404
        assert client.get(f"/v1/jobs/{job['id']}/artifacts/frames/../../job.json").status_code == 404
        with video.open("rb") as handle:
            uploaded = client.post("/v1/jobs/upload", files={"file": ("../../evil.mp4", handle, "video/mp4")},
                                   data={"options_json": '{"ocr":false,"speech":false}'}).json()
        assert finish(client, uploaded)["status"] == "complete"
        assert client.get("/health", headers={"host": "example.com"}).status_code == 400
    with TestClient(create_app(root)) as client:
        assert client.get(f"/v1/jobs/{job['id']}").json()["status"] == "complete"


def test_failed_job_is_reported(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"broken")
    with TestClient(create_app(tmp_path / "service")) as client:
        job = client.post("/v1/jobs", json={"path": str(bad)}).json()
        assert finish(client, job)["status"] == "failed"
        assert client.get(job["result_url"]).status_code == 409


def test_interval_includes_text_reused_on_a_later_frame(tmp_path):
    folder = tmp_path / "jobs" / "reused"
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"id": "reused", "status": "complete"}))
    context = {"source": {"duration_seconds": 60}, "shots": [], "speech_segments": [],
               "frames": [{"id": "later", "timestamp_seconds": 30}],
               "on_screen_text": [{"frame_id": "earlier", "frame_ids": ["earlier", "later"], "text": "stable slide"}]}
    (folder / "context.json").write_text(json.dumps(context))
    with TestClient(create_app(tmp_path)) as client:
        result = client.get("/v1/jobs/reused/context?start=29&end=31").json()
        assert result["on_screen_text"][0]["text"] == "stable slide"
