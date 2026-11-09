"""Organize the local course archives and submit resumable, numbered API jobs.

Private media and the resumable manifest remain in ignored data/courses/.
Original archives are preserved; the earlier root-level video stays accessible.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
COURSE = ROOT / "data/courses/combinatorial-algorithms"
SEVENZIP = Path("C:/Program Files/7-Zip/7z.exe")
OPTIONS = {"speech": True, "ocr": True, "language": "fa", "ocr_language": "en",
           "ocr_deduplicate": True, "sample_interval_seconds": 15, "max_frames": 300}


def write_manifest(manifest):
    temporary = COURSE / "batch.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(COURSE / "batch.json")
    lines = ["# Combinatorial algorithms course", "", "Original archive video indices 0–4 are presented as lectures 01–05.",
             "Persian speech, English slide OCR, scene detection and 15-second frame sampling (maximum 300 frames per lecture).", "",
             f"[Live dashboard]({manifest['base_url']}/ui/) · [Course API]({manifest['base_url']}/v1/jobs?search=combinatorial-algorithms)", "",
             "| Lecture | Duration | State at submission | Run |", "| --- | --- | --- | --- |"]
    for item in sorted(manifest["lectures"], key=lambda x: x["source_index"]):
        seconds = item.get("duration_seconds", 0)
        job_id = item.get("job_id")
        link = f"[Open run]({manifest['base_url']}/ui/jobs/{job_id})" if job_id else "Pending upload"
        lines.append(f"| {item['number']:02d} | {seconds / 60:.1f} min | {item.get('submitted_status', 'organized')} | {link} |")
    lines += ["", "Run states here are submission snapshots; use the dashboard/API for live progress.",
              "Archives are preserved under archives/. Numbered playable recordings are under videos/.",
              "batch.json maps archive names, original video names, source hashes, metadata and job IDs."]
    (COURSE / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def checksums(path):
    digest, crc = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024**2):
            digest.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return digest.hexdigest(), f"{crc:08X}"


def available_disk(required=0):
    free = shutil.disk_usage(COURSE).free
    if free < required + 2 * 1024**3:
        raise RuntimeError("Insufficient host disk headroom; manifest is resumable after freeing space")


def inspect_archive(path):
    result = subprocess.run([str(SEVENZIP), "l", "-slt", "-sccUTF-8", str(path)],
                            check=True, capture_output=True, encoding="utf-8")
    entries = result.stdout.split("\n----------\n", 1)[1].strip().split("\n\n")
    files = [dict(line.split(" = ", 1) for line in entry.splitlines() if " = " in line) for entry in entries]
    assert len(files) == 1 and files[0].get("Folder") == "-", "Expected one video per archive"
    entry = files[0]
    assert entry["Encrypted"] == "-" and not any(entry.get(key) for key in ("Symbolic Link", "Hard Link", "Copy Link"))
    assert Path(entry["Path"]).name == entry["Path"] and entry["Path"].lower().endswith(".mp4")
    match = re.search(r"_(\d+)_0\.mp4$", entry["Path"])
    assert match, "Unrecognized course video index"
    return {"source_index": int(match[1]), "original_video_name": entry["Path"],
            "bytes": int(entry["Size"]), "crc32": entry["CRC"]}


def main():
    COURSE.mkdir(parents=True, exist_ok=True)
    for name in ("archives", "videos"):
        (COURSE / name).mkdir(exist_ok=True)
    values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith("#") and "=" in line)
    base = "http://127.0.0.1:" + values.get("VIDEO_CONTEXT_HTTP_PORT", "18765")
    batch_path = COURSE / "batch.json"
    manifest = json.loads(batch_path.read_text(encoding="utf-8")) if batch_path.exists() else {
        "course": "combinatorial-algorithms", "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base, "queue": "gpu", "options": OPTIONS, "lectures": []}
    incoming = ROOT / "data/combinational algrothms"
    archives = list(incoming.glob("*.rar")) + list((COURSE / "archives").glob("*.rar"))
    entries = sorted([(path, inspect_archive(path)) for path in archives], key=lambda pair: pair[1]["source_index"])
    assert len(entries) == 5 and [e["source_index"] for _, e in entries] == list(range(5)), "Expected all five unique lectures"
    workspace = ROOT.resolve()
    for archive, metadata in entries:
        destination = COURSE / "archives" / archive.name
        # All moves are individual files, verified inside the intended workspace.
        assert archive.resolve().is_relative_to(workspace) and destination.resolve().is_relative_to(workspace)
        if archive.resolve() != destination.resolve():
            assert not destination.exists(), "Refusing to overwrite an archive"
            archive.rename(destination)
        number = metadata["source_index"] + 1
        item = next((r for r in manifest["lectures"] if r["source_index"] == metadata["source_index"]), None)
        if item is None:
            item = dict(metadata, number=number, archive=f"archives/{archive.name}",
                        video=f"videos/combinatorial-algorithms-{number:02d}.mp4")
            manifest["lectures"].append(item)
        video = COURSE / item["video"]
        if not video.exists():
            original = ROOT / "data" / item["original_video_name"]
            if original.is_file():
                # Preserve older test paths without another full video copy.
                os.link(original, video)
            else:
                available_disk(item["bytes"])
                partial = video.with_suffix(".mp4.part")
                with partial.open("wb") as output:
                    result = subprocess.run([str(SEVENZIP), "e", "-so", "-y", "-sccUTF-8", str(destination), item["original_video_name"]],
                                            stdout=output, stderr=subprocess.PIPE, timeout=600)
                if result.returncode:
                    raise RuntimeError(f"Archive extraction failed: {destination.name}")
                partial.replace(video)
        digest, crc = checksums(video)
        assert video.stat().st_size == item["bytes"] and crc == item["crc32"], "Extracted video failed archive integrity check"
        raw = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)],
                             check=True, capture_output=True, encoding="utf-8")
        probe = json.loads(raw.stdout)
        stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        item.update(sha256=digest, duration_seconds=float(probe["format"]["duration"]), width=stream["width"], height=stream["height"],
                    archive_sha256=checksums(destination)[0])
        write_manifest(manifest)
        print(f"Organized lecture {number:02d}: {item['duration_seconds']/60:.1f} min, {item['bytes']/1024**2:.0f} MiB", flush=True)
    if incoming.is_dir() and not any(incoming.iterdir()):
        assert incoming.resolve().is_relative_to(workspace)
        incoming.rmdir()
    with httpx.Client(base_url=base, headers={"x-api-key": values["VIDEO_CONTEXT_API_KEY"]},
                      timeout=httpx.Timeout(600, connect=10)) as client:
        client.get("/ready").raise_for_status()
        for item in manifest["lectures"]:
            if item.get("job_id"):
                client.get(f"/v1/jobs/{item['job_id']}").raise_for_status()
                continue
            # Recover an accepted POST if a previous client response was lost.
            response = client.get("/v1/jobs", params={"search": Path(item["video"]).name, "limit": 100})
            response.raise_for_status()
            snapshot = response.json()
            existing = [r for group in ("running", "queued", "uploading", "history") for r in snapshot[group]
                        if r.get("input_sha256") == item["sha256"] and r["source_file"] == Path(item["video"]).name
                        and r["created_at"] >= manifest["created_at"] and r.get("options") == manifest["options"]]
            if existing:
                record = existing[0]
            else:
                available_disk(item["bytes"] * 2)
                for attempt in range(6):
                    with (COURSE / item["video"]).open("rb") as source:
                        response = client.post("/v1/jobs/upload", files={"file": (Path(item["video"]).name, source, "video/mp4")},
                                               data={"queue": manifest["queue"], "options_json": json.dumps(manifest["options"])})
                    if response.status_code not in (429, 503):
                        break
                    print(f"Upload backpressure for lecture {item['number']:02d}; retrying", flush=True)
                    time.sleep(min(10, 2**attempt))
                response.raise_for_status()
                record = response.json()
            item.update(job_id=record["id"], submitted_status=record["status"],
                        ui_url=f"{base}/ui/jobs/{record['id']}", api_url=f"{base}/v1/jobs/{record['id']}")
            write_manifest(manifest)
            print(f"Queued lecture {item['number']:02d}: {record['id']} ({record['status']})", flush=True)
        snapshot = client.get("/v1/jobs").json()
        print(json.dumps({"submitted": len(manifest["lectures"]), "counts": snapshot["counts"],
                          "host_disk_free_gib": round(shutil.disk_usage(COURSE).free / 1024**3, 2)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
