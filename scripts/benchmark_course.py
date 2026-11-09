"""Benchmark updated stages against an existing context without rescanning video."""
import argparse
import copy
import hashlib
import json
import shutil
import time
from pathlib import Path

from video_context.config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE
from video_context.models import Options
from video_context.pipeline import read_text, save_json, transcribe, write_exports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=["ocr", "speech", "both"], default="both")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output must be empty")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    with args.video.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != baseline["source"]["sha256"]:
            parser.error("Video does not match the baseline source hash")
    args.output.mkdir(parents=True, exist_ok=True)
    context = copy.deepcopy(baseline)
    context.update(on_screen_text=[], speech_segments=[], warnings=[], status="running", stages={},
                   whisper_model=WHISPER_MODEL,
                   speech_runtime={"device": WHISPER_DEVICE, "compute_type": WHISPER_COMPUTE})
    options = Options.model_validate({**baseline["settings"], "ocr_deduplicate": True, "language": "fa",
                                     "ocr": args.stage in {"ocr", "both"}, "speech": args.stage in {"speech", "both"}})
    context["settings"] = options.model_dump()
    for frame in context["frames"]:
        frame.pop("ocr", None)
        target = args.output / frame["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.baseline.parent / frame["path"], target)
    started = time.perf_counter()

    def progress(stage):
        print(stage, flush=True)
        save_json(args.output / "progress.json", {"stage": stage, "elapsed_seconds": round(time.perf_counter() - started, 3)})

    if args.stage in {"ocr", "both"}:
        tick = time.perf_counter()
        context["ocr_stats"] = read_text(args.output, context["frames"], options, context["on_screen_text"], progress)
        context["stages"]["ocr"] = {"status": "complete", "elapsed_seconds": round(time.perf_counter() - tick, 3)}
    if args.stage in {"speech", "both"}:
        tick = time.perf_counter()
        context["speech_segments"] = transcribe(args.video.resolve(), args.output, context["source"], options, progress)
        context["stages"]["speech"] = {"status": "complete", "elapsed_seconds": round(time.perf_counter() - tick, 3)}
    context["status"] = "complete"
    context["benchmark"] = {"baseline": str(args.baseline), "reused_video_analysis": True,
                            "measured_stages": args.stage}
    context["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_exports(args.output, context)
    save_json(args.output / "context.json", context)
    print(json.dumps({"seconds": context["elapsed_seconds"], "stages": context["stages"],
                      "ocr_stats": context.get("ocr_stats"), "model": WHISPER_MODEL,
                      "device": WHISPER_DEVICE}, indent=2))


if __name__ == "__main__":
    main()
