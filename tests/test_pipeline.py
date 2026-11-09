import json
import subprocess
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import pytest
from PIL import Image

from video_context import pipeline
from video_context.models import Options


def test_save_json_retries_temporary_windows_lock(tmp_path, monkeypatch):
    path = tmp_path / "job.json"
    path.write_text('{"status":"old"}', encoding="utf-8")
    replace = Path.replace
    calls = []

    def briefly_locked(source, target):
        calls.append(target)
        if len(calls) < 3:
            assert json.loads(path.read_text())["status"] == "old"
            raise PermissionError("sharing violation")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", briefly_locked)
    monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)
    pipeline.save_json(path, {"status": "new"})
    assert len(calls) == 3
    assert json.loads(path.read_text())["status"] == "new"
    assert not path.with_suffix(".tmp").exists()


def test_save_json_permanent_denial_is_bounded(tmp_path, monkeypatch):
    path = tmp_path / "job.json"
    path.write_text('{"status":"old"}', encoding="utf-8")
    calls = []

    def locked(*args):
        calls.append(True)
        raise PermissionError("permanent denial")

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(pipeline.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        pipeline.save_json(path, {"status": "new"})
    assert len(calls) == 8
    assert json.loads(path.read_text())["status"] == "old"


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "two-shots.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=320x240:r=10:d=2", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x240:r=10:d=2", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", str(path)], check=True)
    return path


def test_real_shots_frames_and_no_audio(video, tmp_path):
    out = tmp_path / "output"
    result = pipeline.analyze(video, out, Options(ocr=False))
    assert result["status"] == "complete"
    assert len(result["shots"]) == 2
    assert result["shots"][0]["end_seconds"] == pytest.approx(2, abs=.11)
    assert result["stages"]["speech"]["reason"] == "no audio stream"
    assert len(result["frames"]) == 2
    assert all((out / f["path"]).is_file() for f in result["frames"])
    assert json.loads((out / "context.json").read_text())["source"]["sha256"]
    assert (out / "captions/speech.srt").read_text() == ""


def test_stage_failure_preserves_evidence(video, tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError("OCR unavailable")

    monkeypatch.setattr(pipeline, "read_text", broken)
    result = pipeline.analyze(video, tmp_path / "out", Options())
    assert result["status"] == "partial"
    assert result["stages"]["ocr"]["error"] == "OCR unavailable"
    assert result["frames"]


def test_variable_frame_rate_preserves_pts(tmp_path):
    path = tmp_path / "vfr.mp4"
    timestamps = [0, 100, 500, 1200, 1700, 2500]
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height = 320, 240
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.codec_context.max_b_frames = 0
        for pts in timestamps:
            frame = av.VideoFrame.from_image(Image.new("RGB", (320, 240), "green"))
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    result = pipeline.analyze(path, tmp_path / "vfr-out", Options(ocr=False, speech=False, sample_interval_seconds=1))
    assert result["status"] == "complete"
    assert len(result["shots"]) == 1
    assert [f["timestamp_seconds"] for f in result["frames"]] == pytest.approx([.5, 1.7, 2.5])


def test_rotation(video, tmp_path):
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-display_rotation", "90",
                    "-i", str(video), "-c", "copy", str(rotated)], check=True)
    result = pipeline.analyze(rotated, tmp_path / "rot-out", Options(ocr=False, speech=False))
    assert (result["source"]["width"], result["source"]["height"]) == (240, 320)
    assert (result["frames"][0]["width"], result["frames"][0]["height"]) == (240, 320)


def test_corrupt_input(tmp_path):
    path = tmp_path / "bad.mp4"
    path.write_bytes(b"not a video")
    with pytest.raises(subprocess.CalledProcessError):
        pipeline.analyze(path, tmp_path / "out", Options(ocr=False))


def test_caption_rounding_and_full_recording_sampling():
    assert pipeline.caption_time(59.9996) == "00:01:00,000"
    shots = [{"start_seconds": 0, "end_seconds": 300}]
    targets, capped = pipeline.sample_targets(shots, Options(max_frames=3, sample_interval_seconds=10))
    assert capped and len(targets) == 3
    assert targets[-1] > 280


def test_audio_offset_uses_shared_timeline(video, tmp_path, monkeypatch):
    delayed = tmp_path / "delayed.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video), "-itsoffset", "1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", str(delayed)], check=True)

    class Model:
        def transcribe(self, path, **kwargs):
            return iter([SimpleNamespace(start=.2, end=.8, text="evidence", words=[])]), SimpleNamespace(language="en")

    monkeypatch.setattr(pipeline, "speech_model", lambda: Model())
    result = pipeline.analyze(delayed, tmp_path / "delayed-out", Options(ocr=False))
    offset = result["source"]["audio_offset_seconds"]
    assert offset == pytest.approx(1, abs=.1)
    assert result["speech_segments"][0]["start_seconds"] == pytest.approx(offset + .2)
    assert not (tmp_path / "delayed-out/audio.wav").exists()
