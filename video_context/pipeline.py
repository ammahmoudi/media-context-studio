import hashlib
import importlib.metadata
import json
import math
import os
import subprocess
import time
import wave
from functools import lru_cache
from pathlib import Path

from . import __version__
from .config import MODELS, WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL
from .models import Options


def save_json(path: Path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers/indexers can briefly deny replacement of an open file.
    # Preserve atomic writes, but allow a short sharing conflict to clear.
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(.05 * 2 ** attempt, .5))


def run(command):
    return subprocess.run(command, check=True, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=3600).stdout


def inspect(path: Path):
    raw = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                          "-of", "json", str(path)]))
    videos = [s for s in raw["streams"] if s["codec_type"] == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError("Input has no playable video stream")
    video = videos[0]
    audio = next((s for s in raw["streams"] if s["codec_type"] == "audio"), None)
    origin = float(raw["format"].get("start_time", 0))
    duration = float(raw["format"].get("duration", video.get("duration", 0)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Video duration is missing or invalid")
    rotation = next((float(s["rotation"]) for s in video.get("side_data_list", [])
                     if "rotation" in s), float(video.get("tags", {}).get("rotate", 0)))
    width, height = video["width"], video["height"]
    if round(rotation) % 180:
        width, height = height, width
    return {"file_name": path.name, "duration_seconds": duration, "width": width,
            "height": height, "has_audio": audio is not None,
            "timeline_origin_seconds": origin, "rotation_degrees": rotation,
            "video_stream_index": video["index"],
            "video_offset_seconds": float(video.get("start_time", origin)) - origin,
            "audio_offset_seconds": float(audio.get("start_time", origin)) - origin if audio else None,
            "audio_stream_index": audio["index"] if audio else None}


def detect_shots(path, source):
    import av
    from scenedetect import ContentDetector, detect

    # Convert detector frame indices to actual PTS: nominal FPS gives wrong shot
    # times for variable-frame-rate recordings. Only internal boundaries matter.
    pairs = detect(str(path), ContentDetector(), start_in_scene=True)
    offset, duration = source["video_offset_seconds"], source["duration_seconds"]
    cut_indices = {pair[0].frame_num for pair in pairs[1:]}
    cuts = []
    if cut_indices:
        with av.open(str(path)) as container:
            stream = next(s for s in container.streams.video if s.index == source["video_stream_index"])
            for index, frame in enumerate(container.decode(stream)):
                if index in cut_indices and frame.pts is not None:
                    timestamp = float(frame.pts * frame.time_base) - source["timeline_origin_seconds"]
                    if max(0, offset) < timestamp < duration:
                        cuts.append(timestamp)
                if index >= max(cut_indices):
                    break
    boundaries = [max(0, offset), *sorted(set(cuts)), duration]
    bounds = zip(boundaries, boundaries[1:])
    return [{"id": f"shot_{i:05d}", "start_seconds": a, "end_seconds": b, "frame_ids": []}
            for i, (a, b) in enumerate(bounds, 1) if b > a]


def sample_targets(shots, options):
    targets = []
    for shot in shots:
        start, end = shot["start_seconds"], shot["end_seconds"]
        first = min(start + 0.5, (start + end) / 2)
        targets.append(first)
        point = first + options.sample_interval_seconds
        while point < end:
            targets.append(point)
            point += options.sample_interval_seconds
    # If capped, spread samples across the whole recording rather than its beginning.
    capped = len(targets) > options.max_frames
    if capped:
        if options.max_frames == 1:
            targets = [targets[len(targets) // 2]]
        else:
            targets = [targets[round(i * (len(targets) - 1) / (options.max_frames - 1))]
                       for i in range(options.max_frames)]
    return targets, capped


def extract_frames(path, folder, source, shots, options):
    import av

    targets, capped = sample_targets(shots, options)
    frames = []
    (folder / "frames").mkdir(exist_ok=True)
    target_idx = 0

    def append_frame(decoded, timestamp):
        shot = next((s for s in shots if s["start_seconds"] <= timestamp < s["end_seconds"]), None)
        if shot is None:
            return
        image = decoded.to_image()
        if source["rotation_degrees"]:
            image = image.rotate(source["rotation_degrees"], expand=True)
        image.thumbnail((1600, 1600))
        frame_id = f"frame_{len(frames) + 1:05d}"
        relative = f"frames/{frame_id}.jpg"
        image.save(folder / relative, quality=92)
        frames.append({"id": frame_id, "shot_id": shot["id"], "timestamp_seconds": timestamp,
                       "source_pts": decoded.pts, "source_time_base": str(decoded.time_base),
                       "width": image.width, "height": image.height, "path": relative})
        shot["frame_ids"].append(frame_id)

    # A sequential decode keeps real presentation timestamps, including VFR media.
    last_frame = None
    with av.open(str(path)) as container:
        stream = next(s for s in container.streams.video if s.index == source["video_stream_index"])
        stream.thread_type = "AUTO"
        for decoded in container.decode(stream):
            if target_idx >= len(targets):
                break
            if decoded.pts is None:
                continue
            timestamp = float(decoded.pts * decoded.time_base) - source["timeline_origin_seconds"]
            last_frame = (decoded, timestamp)
            if timestamp < targets[target_idx]:
                continue
            # Multiple targets can map to one frame in low frame rate media.
            while target_idx < len(targets) and targets[target_idx] <= timestamp:
                target_idx += 1
            append_frame(decoded, timestamp)
        # A one-frame clip can end before the first desired sample timestamp.
        if not frames and last_frame is not None:
            append_frame(*last_frame)
    if not frames:
        raise ValueError("No timestamped frames could be extracted")
    return frames, capped


@lru_cache(maxsize=1)
def speech_model(download=False):
    if WHISPER_DEVICE == "cuda":
        from .gpu import prepare_cuda

        prepare_cuda()
    from faster_whisper import WhisperModel

    return WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE,
                        download_root=str(MODELS / "whisper"), local_files_only=not download,
                        cpu_threads=int(os.getenv("VIDEO_CONTEXT_CPU_THREADS", "4")))


@lru_cache(maxsize=1)
def ocr_model(language="en"):
    from paddleocr import PaddleOCR

    manifest = MODELS / "ocr" / f"{language}.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"OCR models are not prepared for {language}. Run prepare-models --ocr-language {language}")
    models = json.loads(manifest.read_text(encoding="utf-8"))
    # Model manifests are portable across Windows laptop and Linux containers.
    for model in models.values():
        portable = MODELS / "ocr" / model["name"]
        if portable.is_dir():
            model["path"] = str(portable)
    return PaddleOCR(text_detection_model_name=models["detector"]["name"],
                     text_detection_model_dir=models["detector"]["path"],
                     text_recognition_model_name=models["recognizer"]["name"],
                     text_recognition_model_dir=models["recognizer"]["path"],
                     device="cpu", use_doc_orientation_classify=False,
                     use_doc_unwarping=False, use_textline_orientation=False,
                     enable_mkldnn=False, cpu_threads=int(os.getenv("VIDEO_CONTEXT_CPU_THREADS", "4")))


def prepare_ocr(language):
    from huggingface_hub import HfApi, snapshot_download

    # Use compact official models for the first laptop configuration.
    recognizers = {"en": "en_PP-OCRv5_mobile_rec", "fa": "arabic_PP-OCRv5_mobile_rec",
                   "ar": "arabic_PP-OCRv5_mobile_rec"}
    if language not in recognizers:
        raise ValueError("Initial OCR profiles support en, fa, ar; add and verify a model profile for other languages")
    root = MODELS / "ocr"
    root.mkdir(parents=True, exist_ok=True)
    models = {}
    for kind, name in {"detector": "PP-OCRv5_mobile_det", "recognizer": recognizers[language]}.items():
        repo = f"PaddlePaddle/{name}"
        revision = HfApi().model_info(repo).sha
        path = root / name
        snapshot_download(repo_id=repo, revision=revision, local_dir=str(path),
                          allow_patterns=["*.json", "*.pdiparams", "*.yml", "*.yaml"])
        models[kind] = {"name": name, "path": str(path), "repository": repo, "revision": revision}
    save_json(root / f"{language}.json", models)
    return ocr_model(language)


def transcribe(path, folder, source, options, progress=lambda stage: None):
    import numpy as np

    wav = folder / "audio.wav"
    try:
        run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(path), "-map",
             f"0:{source['audio_stream_index']}", "-vn", "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", str(wav)])
        # FFmpeg has already decoded the stream. Use Whisper's documented array
        # input rather than decoding it again through its older PyAV wrapper.
        with wave.open(str(wav), "rb") as audio_file:
            if (audio_file.getnchannels(), audio_file.getsampwidth(), audio_file.getframerate()) != (1, 2, 16000):
                raise ValueError("Expected 16 kHz mono PCM16 audio")
            samples = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        segments, info = speech_model().transcribe(samples, language=options.language,
                                                   vad_filter=True, word_timestamps=True)
        output = []
        offset = source["audio_offset_seconds"]
        duration = source["duration_seconds"]
        for segment in segments:
            progress(f"speech {min(duration, segment.end + offset):.1f}/{duration:.1f}s")
            start, end = max(0, segment.start + offset), min(duration, segment.end + offset)
            if end <= start:
                continue
            output.append({"id": f"speech_{len(output) + 1:05d}", "start_seconds": start,
                           "end_seconds": end, "text": segment.text.strip(), "language": info.language,
                           "words": [{"text": w.word, "start_seconds": max(0, w.start + offset),
                                      "end_seconds": min(duration, w.end + offset), "probability": w.probability}
                                     for w in segment.words or [] if w.end + offset > 0 and w.start + offset < duration]})
        return output
    finally:
        wav.unlink(missing_ok=True)


def read_text(folder, frames, options, output, progress):
    from .ocr import read_incremental

    return read_incremental(folder, frames, options, output, progress, lambda: ocr_model(options.ocr_language))


def caption_time(seconds):
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_exports(folder, context):
    (folder / "captions").mkdir(exist_ok=True)
    speech = context["speech_segments"]
    srt = "\n\n".join(f"{i}\n{caption_time(s['start_seconds'])} --> {caption_time(s['end_seconds'])}\n{s['text']}"
                        for i, s in enumerate(speech, 1))
    (folder / "captions/speech.srt").write_text(srt, encoding="utf-8")
    lines = ["# Course video context", "", context["source"]["file_name"], "",
             "Extracted evidence. Visual descriptions and subject summaries are not generated.", "",
             f"Status: {context['status']}", ""]
    for warning in context["warnings"]:
        lines.extend([f"Warning: {warning}", ""])
    for shot in context["shots"]:
        lines.extend([f"## {shot['id']} | {shot['start_seconds']:.2f}–{shot['end_seconds']:.2f} seconds", ""])
        for frame in context["frames"]:
            if frame["shot_id"] == shot["id"]:
                lines.extend([f"### Frame at {frame['timestamp_seconds']:.2f} seconds", "",
                              f"[View frame]({frame['path']})", ""])
                for item in context["on_screen_text"]:
                    if item["frame_id"] == frame["id"]:
                        lines.append(f"- {item['text']}")
                reused = frame.get("ocr", {}).get("reused_text_ids", [])
                if reused:
                    lines.append(f"{len(reused)} unchanged text observations reused; see JSON evidence frame_ids.")
                lines.append("")
    lines.extend(["## Speech transcript", ""])
    for segment in speech:
        lines.extend([f"[{segment['start_seconds']:.2f}–{segment['end_seconds']:.2f}] {segment['text']}", ""])
    (folder / "context.md").write_text("\n".join(lines), encoding="utf-8")


def analyze(path: Path, folder: Path, options: Options, progress=lambda stage: None):
    folder.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    progress("metadata")
    source = inspect(path)
    with path.open("rb") as handle:
        source["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    packages = ["fastapi", "faster-whisper", "scenedetect", "paddleocr", "paddlepaddle", "av"]
    context = {"schema_version": "0.1.0", "source": source, "status": "running", "stages": {},
               "shots": [], "frames": [], "on_screen_text": [], "speech_segments": [],
               "visual_descriptions": [], "events": [], "warnings": [],
               "settings": options.model_dump(), "versions": {p: importlib.metadata.version(p) for p in packages},
               "service_version": __version__, "whisper_model": WHISPER_MODEL,
               "speech_runtime": {"device": WHISPER_DEVICE, "compute_type": WHISPER_COMPUTE},
               "exports": {"speech_captions": "captions/speech.srt", "readable_context": "context.md"},
               "timing_convention": "seconds relative to format start; intervals are [start, end)"}

    def stage(name, engine, action):
        progress(name)
        tick = time.perf_counter()
        record = {"engine": engine, "status": "running"}
        context["stages"][name] = record
        try:
            action()
            record["status"] = "complete"
        except Exception as error:
            record.update(status="failed", error=str(error))
            context["status"] = "partial"
            context["warnings"].append(f"{name}: {error}")
        record["elapsed_seconds"] = round(time.perf_counter() - tick, 3)
        save_json(folder / "context.json", context)

    def shots():
        context["shots"] = detect_shots(path, source)

    stage("shots", "pyscenedetect", shots)
    if not context["shots"]:
        context["shots"] = [{"id": "shot_00001", "start_seconds": max(0, source["video_offset_seconds"]),
                             "end_seconds": source["duration_seconds"], "frame_ids": []}]

    def frames():
        context["frames"], capped = extract_frames(path, folder, source, context["shots"], options)
        context["sampling"] = {"interval_seconds": options.sample_interval_seconds,
                               "max_frames": options.max_frames, "cap_applied": capped,
                               "extracted_frames": len(context["frames"])}
        if capped:
            context["warnings"].append("Frame cap applied: samples spread across recording; some shots may have no frames.")

    stage("frames", "pyav", frames)
    if options.speech and source["has_audio"]:
        stage("speech", "faster-whisper", lambda: context.update(speech_segments=transcribe(path, folder, source, options, progress)))
    else:
        context["stages"]["speech"] = {"status": "skipped", "reason": "disabled" if not options.speech else "no audio stream"}
    if options.ocr and context["frames"]:
        stage("ocr", "paddleocr", lambda: context.update(ocr_stats=read_text(folder, context["frames"], options, context["on_screen_text"], progress)))
    else:
        context["stages"]["ocr"] = {"status": "skipped", "reason": "disabled" if not options.ocr else "no extracted frames"}
    context["stages"]["vision"] = {"status": "skipped", "reason": "visual model enrichment not implemented"}
    ocr_manifest = MODELS / "ocr" / f"{options.ocr_language}.json"
    if options.ocr and ocr_manifest.is_file():
        context["ocr_models"] = {kind: {k: v for k, v in model.items() if k != "path"}
                                 for kind, model in json.loads(ocr_manifest.read_text(encoding="utf-8")).items()}
    if context["status"] == "running":
        context["status"] = "complete"
    context["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_exports(folder, context)
    save_json(folder / "context.json", context)
    return context
