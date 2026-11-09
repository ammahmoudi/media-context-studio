import argparse
import json
from pathlib import Path

from .config import DATA, prepare_dirs
from .models import Options


def main():
    parser = argparse.ArgumentParser(description="Local course video context")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the loopback HTTP API")
    serve.add_argument("--port", type=int, default=18765)
    setup = commands.add_parser("prepare-models", help="Download and load local models once")
    setup.add_argument("--ocr-language", default="en")
    extract = commands.add_parser("analyze", help="Extract context directly without the server")
    extract.add_argument("path", type=Path)
    extract.add_argument("--output", type=Path, default=DATA / "cli-result")
    extract.add_argument("--language", default=None)
    extract.add_argument("--ocr-language", default="en")
    extract.add_argument("--no-ocr", action="store_true")
    extract.add_argument("--no-speech", action="store_true")
    extract.add_argument("--sample-interval", type=float, default=30)
    args = parser.parse_args()
    prepare_dirs()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("video_context.service:app", host="127.0.0.1", port=args.port, workers=1)
    elif args.command == "prepare-models":
        from .pipeline import prepare_ocr, speech_model

        speech_model(download=True)
        prepare_ocr(args.ocr_language)
        print("Speech and OCR models are ready.")
    else:
        from .pipeline import analyze

        path = args.path.resolve()
        if not path.is_file():
            parser.error(f"Video not found: {path}")
        if args.output.exists() and any(args.output.iterdir()):
            parser.error("Output directory is not empty; choose a new output directory")
        options = Options(language=args.language, ocr_language=args.ocr_language,
                          speech=not args.no_speech, ocr=not args.no_ocr,
                          sample_interval_seconds=args.sample_interval)
        result = analyze(path, args.output.resolve(), options, lambda stage: print(stage, flush=True))
        print(json.dumps({"status": result["status"], "output": str(args.output.resolve())}))
        if result["status"] != "complete":
            raise SystemExit(2)


if __name__ == "__main__":
    main()
