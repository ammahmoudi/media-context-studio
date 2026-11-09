"""Authenticated end-to-end smoke check for the running local deployment.

Uses this project's existing short sample videos; never prints deployment secrets.
"""
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines()
              if line.strip() and not line.lstrip().startswith("#") and "=" in line)
base = f"http://127.0.0.1:{values.get('VIDEO_CONTEXT_HTTP_PORT','18765')}"
report = {"base_url": base}
assert httpx.get(base + "/v1/jobs").status_code == 401
with httpx.Client(base_url=base, headers={"x-api-key": values["VIDEO_CONTEXT_API_KEY"]}, timeout=120) as client:
    assert client.get("/ready").status_code == 200
    submitted = {}
    for queue, path, options in (
        ("cpu", ROOT / "data/sample/silent.mp4", {"speech": False, "ocr": False}),
        ("gpu", ROOT / "data/course-test/excerpt.mp4", {"language": "fa", "speech": True, "ocr": False}),
    ):
        with path.open("rb") as source:
            response = client.post("/v1/jobs/upload", files={"file": (path.name, source, "video/mp4")},
                                   data={"queue": queue, "options_json": json.dumps(options)})
        response.raise_for_status()
        submitted[queue] = response.json()["id"]
        print(f"Submitted {queue} job {submitted[queue]}", flush=True)
    deadline = time.monotonic() + 240
    while submitted and time.monotonic() < deadline:
        for queue, job_id in list(submitted.items()):
            record = client.get(f"/v1/jobs/{job_id}").json()
            if record["status"] in ("complete", "partial", "failed", "cancelled"):
                assert record["status"] == "complete", record.get("error", record)
                context = client.get(f"/v1/jobs/{job_id}/result").json()
                if queue == "gpu":
                    assert context["speech_runtime"]["device"] == "cuda"
                    assert context["whisper_model"] == "large-v3-turbo"
                    assert context["speech_segments"]
                video = client.get(f"/v1/jobs/{job_id}/video", headers={"range": "bytes=0-31"})
                assert video.status_code == 206 and len(video.content) == 32
                assert client.get(f"/v1/jobs/{job_id}/artifacts/captions/speech.vtt").status_code == 200
                report[queue] = {"id": job_id, "status": record["status"], "elapsed_seconds": record["elapsed_seconds"],
                                 "worker": record["worker_id"], "frames": len(context["frames"]),
                                 "speech_segments": len(context["speech_segments"]), "runtime": context["speech_runtime"]}
                del submitted[queue]
                print(f"Completed {queue} job in {record['elapsed_seconds']} seconds", flush=True)
        time.sleep(1)
    assert not submitted, "Timed out waiting for workers"
    old = "0231b5bb002148dba9f3ccc9a333b38f"
    assert client.get(f"/v1/jobs/{old}/video", headers={"range": "bytes=0-31"}).status_code == 206
    assert client.get(f"/v1/jobs/{old}/artifacts/captions/speech.vtt").text.count(" --> ") == 1151
    snapshot = client.get("/v1/jobs").json()
    report.update(auth_enforced=True, database=snapshot["database"], storage=snapshot["storage"],
                  online_workers=sum(w["online"] for w in snapshot["workers"]), migrated_course_video=True,
                  migrated_course_caption_cues=1151)
(ROOT / "data/production-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2), flush=True)
