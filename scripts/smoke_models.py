"""Run real OCR and speech inference with outbound sockets blocked in this process."""
import argparse
import json
import os
import socket
from pathlib import Path


def deny_network(*args, **kwargs):
    raise RuntimeError("Network is disabled during the local inference smoke check")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/model-smoke"))
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Choose an empty output directory")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network

    from video_context.models import Options
    from video_context.pipeline import analyze

    result = analyze(Path("data/sample/course.mp4").resolve(), args.output.resolve(),
                     Options(language="en"), lambda stage: print(stage, flush=True))
    recognized = " ".join(t["text"] for t in result["on_screen_text"]).lower()
    spoken = " ".join(t["text"] for t in result["speech_segments"]).lower()
    assert result["status"] == "complete", result["warnings"]
    assert len(result["shots"]) == 2, result["shots"]
    assert "graph" in recognized and "search" in recognized, recognized
    assert "vertices" in spoken and "edges" in spoken, spoken
    print(json.dumps({"status": result["status"], "shots": len(result["shots"]),
                      "frames": len(result["frames"]), "ocr_observations": len(result["on_screen_text"]),
                      "speech_segments": len(result["speech_segments"]), "elapsed_seconds": result["elapsed_seconds"],
                      "outbound_sockets": "blocked"}, indent=2))


if __name__ == "__main__":
    main()
