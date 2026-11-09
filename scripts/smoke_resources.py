"""Measure sampled resource peaks on real CPU OCR and multi-window GPU speech.

Uses existing local course media; results stay in ignored data/.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", choices=["cpu", "gpu"], required=True)
    args = parser.parse_args()
    values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#") and "=" in line)
    if args.queue == "gpu":
        source = next(p for p in (ROOT / "data").glob("*.mp4") if p.stat().st_size > 100 * 1024**2)
        video = ROOT / "data/course-test/memory-five-minutes.mp4"
        if not video.exists():
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source), "-t", "300", "-c", "copy", str(video)], check=True)
        options = {"speech": True, "ocr": True, "language": "fa", "max_frames": 6, "sample_interval_seconds": 60}
    else:
        video = ROOT / "data/course-test/excerpt.mp4"
        options = {"speech": False, "ocr": True, "max_frames": 4}
    base = "http://127.0.0.1:" + values.get("VIDEO_CONTEXT_HTTP_PORT", "18765")
    with httpx.Client(base_url=base, headers={"x-api-key": values["VIDEO_CONTEXT_API_KEY"]}, timeout=120) as client:
        with video.open("rb") as handle:
            response = client.post("/v1/jobs/upload", files={"file": (video.name, handle, "video/mp4")},
                                   data={"queue": args.queue, "options_json": json.dumps(options)})
        response.raise_for_status()
        job_id = response.json()["id"]
        print(f"Submitted {args.queue} memory smoke {job_id}", flush=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            record = client.get(f"/v1/jobs/{job_id}").json()
            if record["status"] in ("complete", "partial", "failed", "cancelled"):
                assert record["status"] == "complete", record.get("error", record)
                context = client.get(f"/v1/jobs/{job_id}/result").json()
                assert context["on_screen_text"]
                if args.queue == "gpu":
                    assert context["speech_runtime"]["audio_chunk_seconds"] == 120
                    assert max(s["end_seconds"] for s in context["speech_segments"]) > 240
                assert record["resources"]["peak_memory_mb"] > 0
                report = {"job_id": job_id, "queue": args.queue, "status": record["status"],
                          "duration_seconds": context["source"]["duration_seconds"],
                          "elapsed_seconds": record["elapsed_seconds"], "resources": record["resources"],
                          "speech_segments": len(context["speech_segments"]), "ocr_observations": len(context["on_screen_text"]),
                          "speech_runtime": context["speech_runtime"]}
                target = ROOT / f"data/memory-smoke-{args.queue}.json"
                target.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report, indent=2), flush=True)
                return
            time.sleep(2)
        raise TimeoutError(f"Memory smoke timed out: {job_id}")


if __name__ == "__main__":
    main()
