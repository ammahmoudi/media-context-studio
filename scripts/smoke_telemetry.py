"""Opt-in real GPU/OCR regression: monitoring loss must not destroy CPU OCR.

Uses a private local scratch database, never production queue/history. Requires
the laptop's prepared models and data/course-test/excerpt.mp4.
"""
import json
import time
from pathlib import Path

from with_stack import configure


def main():
    configure()
    from video_context import pipeline, resources
    from video_context.config import ROOT, WHISPER_MODEL
    from video_context.database import Database, now
    from video_context.models import Options
    from video_context.storage import Storage
    from video_context.worker import Worker

    limit_handle = resources.install_native_memory_limit()
    folder = ROOT / "data" / ("telemetry-regression-" + str(int(time.time())))
    folder.mkdir()
    db = Database("sqlite:///" + (folder / "jobs.db").as_posix())
    storage = Storage(folder / "objects")
    source = ROOT / "data/course-test/excerpt.mp4"
    storage.put_file("input.mp4", source)
    job_id = "telemetry-regression"
    db.reserve({"id": job_id, "status": "queued", "queue": "gpu", "stage": "queued", "created_at": now(),
                "source_file": source.name, "input_key": "input.mp4", "input_bytes": source.stat().st_size,
                "options": Options(language="fa", max_frames=4).model_dump()})
    original = resources.gpu_memory
    outage_started = []
    def analyze(path, output, options, progress):
        def report(stage):
            if stage == "ocr":
                resources.gpu_memory = lambda: {}
                outage_started.append(time.monotonic())
                print("Simulating unavailable GPU monitoring throughout CPU OCR", flush=True)
            progress(stage)
        return pipeline.analyze(path, output, options, report)
    try:
        worker = Worker(db, storage, folder, queues=["gpu"], analyze_fn=analyze)
        worker.run(once=True)
        record = db.get(job_id)
        assert record["status"] == "complete", record.get("error", record)
        context = storage.read_json(record["result_prefix"] + "context.json")
        assert context["whisper_model"] == WHISPER_MODEL == "large-v3-turbo"
        assert context["speech_segments"] and context["on_screen_text"]
        report = {"status": record["status"], "model": WHISPER_MODEL, "runtime": context["speech_runtime"],
                  "speech_segments": len(context["speech_segments"]), "ocr_observations": len(context["on_screen_text"]),
                  "elapsed_seconds": record["elapsed_seconds"], "resources": record["resources"],
                  "telemetry_outage_seconds": round(time.monotonic() - outage_started[0], 1)}
        (ROOT / "data/telemetry-regression.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        resources.gpu_memory = original
        db.close()


if __name__ == "__main__":
    main()
