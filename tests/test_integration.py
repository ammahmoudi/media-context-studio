"""Opt-in integration checks against this project's Docker PostgreSQL and S3.

VIDEO_CONTEXT_INTEGRATION=1 python -m pytest tests/test_integration.py
Every test owns a temporary database and bucket; normal service data is untouched.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from video_context.database import Database, QueueFull
from video_context.service import create_app
from video_context.settings import Settings
from video_context.storage import Storage
from video_context.worker import Worker
from test_durable import record
from test_pipeline import video

pytestmark = pytest.mark.skipif(os.getenv("VIDEO_CONTEXT_INTEGRATION") != "1", reason="Requires Docker integration infrastructure")


@pytest.fixture
def stack(tmp_path, monkeypatch):
    import psycopg
    from psycopg import sql
    values = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#") and "=" in line)
    dbname = "vc_test_" + uuid.uuid4().hex
    bucket = "vc-test-" + uuid.uuid4().hex
    password = values["POSTGRES_PASSWORD"]
    port = int(values.get("VIDEO_CONTEXT_DB_PORT", "55433"))
    admin = psycopg.connect(host="127.0.0.1", port=port, user="video", password=password, dbname="video", autocommit=True)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", values["S3_ACCESS_KEY"])
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", values["S3_SECRET_KEY"])
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    settings = Settings(data=tmp_path, database_url=f"postgresql+psycopg://video:{quote(password, safe='')}@127.0.0.1:{port}/{dbname}",
                        bucket=bucket, endpoint=f"http://127.0.0.1:{values.get('VIDEO_CONTEXT_S3_PORT','19020')}",
                        embedded_worker=False, api_key="", allow_path=False)
    storage = Storage(tmp_path / "objects", bucket, settings.endpoint)
    storage.ensure_bucket()
    try:
        yield settings, storage
    finally:
        paginator = storage.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            objects = [{"Key": x["Key"]} for x in page.get("Contents", [])]
            if objects:
                storage.client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
        storage.client.delete_bucket(Bucket=bucket)
        # Generated database name, owned solely by this fixture.
        assert dbname.startswith("vc_test_") and len(dbname) == 40
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))
        admin.close()


def test_postgres_claims_and_capacity_are_atomic(stack):
    settings, _ = stack
    db = Database(settings.database_url, capacity=8)
    def reserve(i):
        try:
            db.reserve(record(str(i)))
            return True
        except QueueFull:
            return False
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(reserve, range(16))) == 8
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda i: db.claim(f"worker-{i}", ["default"]), range(8)))
        assert len({r["id"] for r in claims}) == 8
        assert db.snapshot()["available_slots"] == 0
    finally:
        db.close()


def test_independent_worker_s3_artifacts_and_ranged_video(stack, video):
    settings, storage = stack
    with TestClient(create_app(settings=settings)) as client:
        with video.open("rb") as source:
            response = client.post("/v1/jobs/upload", files={"file": ("lecture.mp4", source, "video/mp4")},
                                   data={"options_json": '{"speech":false,"ocr":false}'})
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "queued"
        assert client.get("/ready").status_code == 200
        db = Database(settings.database_url)
        try:
            worker = Worker(db, storage, settings.data, queues=["default"])
            assert worker.run_one()
        finally:
            db.close()
        final = client.get(f"/v1/jobs/{job['id']}").json()
        assert final["status"] == "complete" and final["attempt"] == 1
        context = client.get(f"/v1/jobs/{job['id']}/result").json()
        assert len(context["shots"]) == 2
        assert context["source"]["file_name"] == "lecture.mp4"
        assert not list((settings.data / "work").glob("*"))
        for artifact in ("context.json", "context.md", "captions/speech.srt", "captions/speech.vtt", context["frames"][0]["path"]):
            assert client.get(f"/v1/jobs/{job['id']}/artifacts/{artifact}").status_code == 200
        base = f"/v1/jobs/{job['id']}/video"
        data = video.read_bytes()
        assert client.get(base, headers={"range": "bytes=2-9"}).content == data[2:10]
        response = client.get(base, headers={"range": "bytes=-8"})
        assert response.status_code == 206 and response.content == data[-8:]
        assert client.head(base).headers["content-length"] == str(len(data))
        assert client.get(base, headers={"range": "bytes=99999999-"}).status_code == 416
    with TestClient(create_app(settings=settings)) as client:
        assert client.get(f"/v1/jobs/{job['id']}/result").status_code == 200
