from PIL import Image, ImageDraw

from video_context.models import Options
from video_context.ocr import read_incremental


class Recognizer:
    def __init__(self):
        self.calls = []

    def predict(self, path):
        with Image.open(path) as image:
            self.calls.append(image.size)
            w, h = image.size
        return [{"rec_texts": ["recognized text"], "rec_scores": [.9],
                 "rec_polys": [[[10, 10], [min(150, w - 1), 10], [min(150, w - 1), min(30, h - 1)], [10, min(30, h - 1)]]]}]


def frames_for(tmp_path, images):
    frames = []
    for i, image in enumerate(images):
        name = f"{i}.png"
        image.save(tmp_path / name)
        frames.append({"id": f"frame_{i}", "timestamp_seconds": i * 15, "path": name,
                       "width": image.width, "height": image.height})
    return frames


def test_unchanged_frames_share_observations_and_evidence(tmp_path):
    model = Recognizer()
    image = Image.new("RGB", (800, 600), "white")
    frames = frames_for(tmp_path, [image, image, image])
    output = []
    stats = read_incremental(tmp_path, frames, Options(), output, lambda _: None, lambda: model)
    assert len(model.calls) == 1
    assert stats["reused_frames"] == 2
    assert len(output) == 1
    assert output[0]["frame_ids"] == [f["id"] for f in frames]
    assert output[0]["observed_at_seconds"] == [0, 15, 30]
    assert frames[-1]["ocr"]["text_ids"] == [output[0]["id"]]


def test_localized_change_reuses_stable_text_and_maps_patch_coordinates(tmp_path):
    model = Recognizer()
    first = Image.new("RGB", (800, 600), "white")
    second = first.copy()
    ImageDraw.Draw(second).rectangle((650, 450, 700, 480), fill="black")
    frames = frames_for(tmp_path, [first, second])
    output = []
    stats = read_incremental(tmp_path, frames, Options(), output, lambda _: None, lambda: model)
    assert stats["incremental_frames"] == 1
    assert model.calls[1][0] < 200 and model.calls[1][1] < 100
    assert output[0]["frame_ids"] == ["frame_0", "frame_1"]
    assert output[1]["bbox_xyxy_normalized"][0] > .7
    assert not (tmp_path / "ocr-patch.png").exists()


def test_editing_recognized_text_invalidates_previous_observation(tmp_path):
    model = Recognizer()
    first = Image.new("RGB", (800, 600), "white")
    second = first.copy()
    ImageDraw.Draw(second).rectangle((30, 15, 38, 25), fill="black")
    frames = frames_for(tmp_path, [first, second])
    output = []
    read_incremental(tmp_path, frames, Options(), output, lambda _: None, lambda: model)
    assert output[0]["frame_ids"] == ["frame_0"]
    assert len(output) == 2
    assert frames[-1]["ocr"]["reused_text_ids"] == []


def test_large_change_resolution_change_and_disable_force_full_pass(tmp_path):
    for options, images in [
        (Options(), [Image.new("RGB", (800, 600), "white"), Image.new("RGB", (800, 600), "black")]),
        (Options(), [Image.new("RGB", (800, 600), "white"), Image.new("RGB", (400, 300), "white")]),
        (Options(ocr_deduplicate=False), [Image.new("RGB", (800, 600), "white")] * 2),
    ]:
        model, output = Recognizer(), []
        frames = frames_for(tmp_path, images)
        stats = read_incremental(tmp_path, frames, options, output, lambda _: None, lambda: model)
        assert stats["full_frame_passes"] == 2
        assert len(output) == 2
