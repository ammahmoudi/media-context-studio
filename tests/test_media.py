"""Audio and image ingestion reuse the durable video workflow without video stages."""
import io
import json
import subprocess
from types import SimpleNamespace
import zipfile

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from video_context import pipeline
from video_context.models import Options
from video_context.resources import ResourceExhausted
from video_context.service import create_app
from test_service import finish


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "speech.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", str(path)], check=True)
    return path


@pytest.fixture
def speech_model(monkeypatch):
    class Model:
        def transcribe(self, samples, **kwargs):
            assert len(samples) > 0
            return iter([SimpleNamespace(start=.2, end=1.2, text="Audio evidence", words=[])]), SimpleNamespace(language="en")
    monkeypatch.setattr(pipeline, "speech_model", lambda: Model())


def test_audio_extracts_speech_without_video_stages(audio, speech_model, tmp_path, monkeypatch):
    def unexpected(*args):
        pytest.fail("Audio must not invoke video decoding or OCR")
    for name in ("detect_shots", "extract_frames", "read_text"):
        monkeypatch.setattr(pipeline, name, unexpected)
    result = pipeline.analyze(audio, tmp_path / "result", Options())
    assert result["status"] == "complete"
    assert result["source"]["media_type"] == "audio"
    assert result["source"]["duration_seconds"] == pytest.approx(2)
    assert result["speech_segments"][0]["text"] == "Audio evidence"
    assert result["shots"] == result["frames"] == result["on_screen_text"] == []
    assert all(result["stages"][s]["status"] == "skipped" for s in ("shots", "frames", "ocr"))
    assert not (tmp_path / "result/audio.wav").exists()
    assert "Audio evidence" in (tmp_path / "result/captions/speech.srt").read_text()


def test_audio_cover_art_is_not_a_video(audio, tmp_path):
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (100, 100), "white").save(cover)
    path = tmp_path / "covered.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(audio),
                    "-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame",
                    "-c:v", "copy", "-id3v2_version", "3", "-disposition:v", "attached_pic", str(path)], check=True)
    source = pipeline.inspect(path)
    assert source["media_type"] == "audio"
    assert source["has_audio"] and source["video_stream_index"] is None


@pytest.mark.parametrize("format,suffix", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"), ("BMP", ".bmp"), ("TIFF", ".tiff")])
def test_image_formats_and_ocr(tmp_path, monkeypatch, format, suffix):
    path = tmp_path / ("slide" + suffix)
    Image.new("RGB", (320, 200), "white").save(path, format=format)
    def ocr(folder, frames, options, output, progress):
        assert len(frames) == 1
        assert Image.open(folder / frames[0]["path"]).size == (320, 200)
        output.append({"id": "text_1", "frame_id": frames[0]["id"], "frame_ids": [frames[0]["id"]], "text": "Image evidence"})
        return {"full_frame_passes": 1}
    monkeypatch.setattr(pipeline, "read_text", ocr)
    result = pipeline.analyze(path, tmp_path / "result", Options())
    assert result["status"] == "complete"
    assert result["source"]["media_type"] == "image"
    assert result["source"]["duration_seconds"] == 0
    assert result["shots"] == result["speech_segments"] == []
    assert result["frames"][0]["timestamp_seconds"] == 0
    assert result["stages"]["speech"]["status"] == "skipped"
    assert result["stages"]["ocr"]["status"] == "complete"
    assert "Image evidence" in (tmp_path / "result/context.md").read_text()


def test_image_orientation_content_detection_and_pixel_limit(tmp_path, monkeypatch):
    path = tmp_path / "renamed.bin"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (320, 200), "white").save(path, format="JPEG", exif=exif)
    result = pipeline.analyze(path, tmp_path / "result", Options(ocr=False))
    assert (result["source"]["width"], result["source"]["height"]) == (200, 320)
    assert Image.open(tmp_path / "result" / result["frames"][0]["path"]).size == (200, 320)
    monkeypatch.setenv("VIDEO_CONTEXT_MAX_VIDEO_PIXELS", "100")
    with pytest.raises(ResourceExhausted, match="pixel limit"):
        pipeline.inspect(path)


def test_reject_multipage_images_and_corrupt_images(tmp_path):
    path = tmp_path / "pages.tiff"
    image = Image.new("RGB", (10, 10))
    image.save(path, save_all=True, append_images=[image])
    with pytest.raises(ValueError, match="multipage"):
        pipeline.inspect(path)
    corrupt = tmp_path / "bad.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        pipeline.inspect(corrupt)


@pytest.mark.parametrize("kind", ["image", "audio"])
def test_media_upload_worker_playback_context_and_export(kind, audio, speech_model, tmp_path):
    path = audio
    if kind == "image":
        path = tmp_path / "slide.png"
        Image.new("RGB", (100, 80), "white").save(path)
    with TestClient(create_app(tmp_path / "service")) as client:
        response = client.post("/v1/jobs/upload", files={"file": (path.name, path.read_bytes())},
                               data={"options_json": json.dumps({"ocr": False})})
        assert response.status_code == 202
        job = response.json()
        status = finish(client, job)
        assert status["status"] == "complete", status
        assert status["source"]["media_type"] == kind
        assert status["media_available"] and status["video_available"]
        base = f"/v1/jobs/{job['id']}"
        assert client.get(base + "/media").content == path.read_bytes()
        assert client.head(base + "/media").status_code == 200
        assert client.get(base + "/video").content == path.read_bytes()  # compatibility alias
        ranged = client.get(base + "/media", headers={"Range": "bytes=0-9"})
        assert ranged.status_code == 206 and ranged.content == path.read_bytes()[:10]
        context = client.get(base + "/context").json()
        assert len(context["frames"]) == (1 if kind == "image" else 0)
        assert len(context["speech_segments"]) == (1 if kind == "audio" else 0)
        catalog = client.get(base + "/exports").json()["files"]
        source = next(f for f in catalog if f["kind"] == kind)
        archive = client.get(base + "/exports/download")
        with zipfile.ZipFile(io.BytesIO(archive.content)) as exported:
            assert exported.read(source["path"]) == path.read_bytes()
            assert "context.json" in exported.namelist()
