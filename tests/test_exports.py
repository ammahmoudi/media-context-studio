import asyncio
import io
import json
import tarfile
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from video_context.exports import CHUNK, archive
from video_context.service import ExportResponse, create_app
from video_context.settings import Settings
from test_durable import record


@pytest.fixture
def export_client(tmp_path):
    settings = Settings(data=tmp_path, embedded_worker=False, api_key="secret")
    with TestClient(create_app(settings=settings)) as client:
        manager = client.app.state.jobs
        files = {"context.json": b'{"text":"hello"}', "captions/speech.srt": "سلام".encode(),
                 "frames/shot_001.jpg": b"jpeg"}
        job = record("download-test")
        job.update(status="complete", result_prefix="results/test/", input_key="inputs/test.mp4",
                   input_suffix=".mp4", input_bytes=5,
                   artifacts=[{"path": path, "bytes": len(data)} for path, data in files.items()])
        for path, data in {**{"results/test/" + p: d for p, d in files.items()}, "inputs/test.mp4": b"video"}.items():
            target = manager.storage.path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        manager.db.reserve(job)
        yield client, files


@pytest.mark.parametrize("format", ["zip", "tar.gz"])
def test_download_all_and_selected(export_client, format):
    client, files = export_client
    base = "/v1/jobs/download-test/exports"
    headers = {"X-API-Key": "secret"}
    assert client.get(base).status_code == 401
    catalog = client.get(base, headers=headers).json()
    assert {e["path"] for e in catalog["files"]} == set(files) | {"source/video.mp4"}
    assert all(set(e) == {"path", "bytes", "kind"} for e in catalog["files"])

    def unpack(response):
        assert response.status_code == 200, response.text
        assert "attachment" in response.headers["content-disposition"]
        assert client.app.state.export_slots._value == 1
        if format == "zip":
            with zipfile.ZipFile(io.BytesIO(response.content)) as output:
                return {p: output.read(p) for p in output.namelist()}
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as output:
            return {p.name: output.extractfile(p).read() for p in output.getmembers()}

    all_files = unpack(client.get(base + "/download", params={"format": format}, headers=headers))
    assert set(all_files) == set(files) | {"source/video.mp4", "export-manifest.json"}
    assert all_files["source/video.mp4"] == b"video"
    selected = list(files)[:2]
    subset = unpack(client.post(base + "/download", data={"format": format, "files_json": json.dumps(selected)}, headers=headers))
    assert set(subset) == set(selected) | {"export-manifest.json"}
    for path in selected:
        assert subset[path] == files[path]
    manifest = json.loads(subset["export-manifest.json"])
    assert {e["path"] for e in manifest["files"]} == set(selected)
    assert "results/test" not in subset["export-manifest.json"].decode()


def test_export_limits_invalid_selections_and_capacity(export_client, monkeypatch):
    client, _ = export_client
    base = "/v1/jobs/download-test/exports/download"
    headers = {"X-API-Key": "secret"}
    for paths in ([], ["../../.env"], ["missing"], {}, [1]):
        assert client.post(base, data={"files_json": json.dumps(paths)}, headers=headers).status_code == 422
        assert client.app.state.export_slots._value == 1
    assert client.post(base, data={"files_json": "bad-json"}, headers=headers).status_code == 422
    assert client.get(base, params={"format": "rar"}, headers=headers).status_code == 422
    assert client.get(base.replace("download-test", "missing"), headers=headers).status_code == 404
    assert client.app.state.export_slots._value == 1
    monkeypatch.setenv("VIDEO_CONTEXT_MAX_EXPORT_BYTES", "1")
    assert client.get(base, headers=headers).status_code == 413
    assert client.app.state.export_slots._value == 1
    client.app.state.export_slots._value = 0
    assert client.get(base, headers=headers).status_code == 429
    client.app.state.export_slots._value = 1


@pytest.mark.parametrize("format", ["zip", "tar.gz"])
def test_export_reads_in_chunks_and_closes_on_disconnect(format):
    class Source(io.BytesIO):
        reads = 0
        def read(self, size=-1):
            assert 0 < size <= CHUNK
            self.reads += 1
            return super().read(size)

    source = Source(b"x" * CHUNK * 20)
    storage = SimpleNamespace(bucket="test", client=SimpleNamespace(get_object=lambda **kw: {"Body": source}))
    stream = archive(storage, record("test"), [{"path": "source/video.mp4", "bytes": CHUNK * 20, "key": "test"}], format)
    while source.reads == 0:
        next(stream)
    assert source.reads == 1
    stream.close()
    assert source.closed


def test_export_response_releases_slot_and_iterator_on_send_failure():
    state = {"closed": False, "released": 0}
    def chunks():
        try:
            yield b"data"
        finally:
            state["closed"] = True
    def release():
        state["released"] += 1
    async def run():
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("disconnected")
        with pytest.raises(Exception):
            await ExportResponse(chunks(), release)({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    asyncio.run(run())
    assert state == {"closed": True, "released": 1}
