"""Incremental OCR: retain evidence for stable pixels and recognize dirty regions."""
from pathlib import Path
from .resources import ResourceExhausted


def intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def merge_rectangles(rectangles):
    result = []
    for rectangle in rectangles:
        rectangle = list(rectangle)
        while True:
            hit = next((i for i, other in enumerate(result) if intersects(rectangle, other)), None)
            if hit is None:
                break
            other = result.pop(hit)
            rectangle = [min(rectangle[0], other[0]), min(rectangle[1], other[1]),
                         max(rectangle[2], other[2]), max(rectangle[3], other[3])]
        result.append(rectangle)
    return result


def pixel_box(item, width, height):
    x1, y1, x2, y2 = item["bbox_xyxy_normalized"]
    return [int(x1 * width), int(y1 * height), min(width, int(x2 * width) + 1),
            min(height, int(y2 * height) + 1)]


def changed_regions(previous, current, active):
    import cv2
    import numpy as np

    height, width = current.shape
    if previous is None or previous.shape != current.shape:
        return [[0, 0, width, height]]
    mask = (cv2.absdiff(previous, current) > 22).astype(np.uint8)
    expanded = cv2.dilate(mask, np.ones((11, 21), np.uint8))
    contours, _ = cv2.findContours(expanded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rectangles = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if np.count_nonzero(mask[y:y + h, x:x + w]) < 6:
            continue
        rectangles.append([max(0, x - 12), max(0, y - 12), min(width, x + w + 12), min(height, y + h + 12)])
    rectangles = merge_rectangles(rectangles)
    # Include complete previous text boxes so an edited word is never recognized
    # as only its changed letter. Re-run after merging until geometry stabilizes.
    previous_rectangles = None
    boxes = [pixel_box(item, width, height) for item in active]
    while previous_rectangles != rectangles:
        previous_rectangles = [r[:] for r in rectangles]
        for rectangle in rectangles:
            for box in boxes:
                if intersects(rectangle, box):
                    rectangle[:] = [min(rectangle[0], max(0, box[0] - 4)),
                                    min(rectangle[1], max(0, box[1] - 4)),
                                    max(rectangle[2], min(width, box[2] + 4)),
                                    max(rectangle[3], min(height, box[3] + 4))]
        rectangles = merge_rectangles(rectangles)
    if sum((r[2] - r[0]) * (r[3] - r[1]) for r in rectangles) > width * height * .45:
        return [[0, 0, width, height]]
    return rectangles


def read_incremental(folder: Path, frames, options, output, progress, model_factory):
    import cv2
    import numpy as np
    from PIL import Image

    model = None
    previous = None
    active = []
    stats = {"full_frame_passes": 0, "patch_passes": 0, "reused_frames": 0,
             "incremental_frames": 0, "inference_pixels": 0, "total_frame_pixels": 0,
             "difference_threshold": 22, "minimum_changed_pixels": 6}
    temporary = folder / "ocr-patch.png"
    try:
        for index, frame in enumerate(frames):
            progress(f"ocr {index + 1}/{len(frames)}")
            with Image.open(folder / frame["path"]) as loaded:
                image = loaded.convert("RGB")
            width, height = image.size
            gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
            full = [[0, 0, width, height]]
            rectangles = changed_regions(previous, gray, active) if options.ocr_deduplicate else full
            is_full = rectangles == full
            stats["total_frame_pixels"] += width * height
            if is_full:
                stats["full_frame_passes"] += 1
            elif rectangles:
                stats["incremental_frames"] += 1
            else:
                stats["reused_frames"] += 1
            reused = [] if is_full else [item for item in active if not any(
                intersects(pixel_box(item, width, height), rectangle) for rectangle in rectangles)]
            for item in reused:
                item["frame_ids"].append(frame["id"])
                item["observed_at_seconds"].append(frame["timestamp_seconds"])
            current = list(reused)
            for x1, y1, x2, y2 in rectangles:
                if model is None:
                    model = model_factory()
                if is_full:
                    path = folder / frame["path"]
                else:
                    image.crop((x1, y1, x2, y2)).save(temporary)
                    path = temporary
                    stats["patch_passes"] += 1
                stats["inference_pixels"] += (x2 - x1) * (y2 - y1)
                for result in model.predict(str(path)):
                    for text, score, polygon in zip(result["rec_texts"], result["rec_scores"], result["rec_polys"]):
                        if not text.strip():
                            continue
                        xs = [float(p[0]) + x1 for p in polygon]
                        ys = [float(p[1]) + y1 for p in polygon]
                        box = [min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height]
                        item = {"id": f"text_{len(output) + 1:06d}", "frame_id": frame["id"],
                                "frame_ids": [frame["id"]], "observed_at_seconds": [frame["timestamp_seconds"]],
                                "text": text, "confidence": float(score),
                                "bbox_xyxy_normalized": [min(1, max(0, x)) for x in box]}
                        output.append(item)
                        if len(output) > 50000:
                            raise ResourceExhausted("Too many text observations; reduce sampled frames or split the recording")
                        current.append(item)
            frame["ocr"] = {"method": "full" if is_full else "patches" if rectangles else "reuse",
                            "text_ids": [item["id"] for item in current],
                            "reused_text_ids": [item["id"] for item in reused],
                            "changed_regions_xyxy_normalized": [[r[0] / width, r[1] / height, r[2] / width, r[3] / height]
                                                                for r in rectangles]}
            previous, active = gray, current
    finally:
        temporary.unlink(missing_ok=True)
    stats["inference_pixel_fraction"] = stats["inference_pixels"] / max(1, stats["total_frame_pixels"])
    return stats
