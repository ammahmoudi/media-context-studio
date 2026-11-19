import argparse
import json
import logging
import signal
import time
import shutil
from dataclasses import replace
from pathlib import Path

from .config import DATA, prepare_dirs
from .models import Options


def main():
    parser = argparse.ArgumentParser(description="Media Context Studio: video, audio and image extraction")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the loopback HTTP API")
    serve.add_argument("--port", type=int, default=18765)
    serve.add_argument("--host", default="127.0.0.1")
    worker = commands.add_parser("worker", help="Run an independent durable queue worker")
    worker.add_argument("--queues", default=None)
    worker.add_argument("--once", action="store_true")
    commands.add_parser("db-init", help="Initialize database schema and private S3 bucket")
    migrate = commands.add_parser("migrate-legacy", help="Copy existing JSON history and artifacts into durable storage")
    migrate.add_argument("--from-directory", type=Path, default=DATA / "jobs")
    cleanup = commands.add_parser("gc-work", help="List abandoned local scratch directories; never delete stored inputs/results")
    cleanup.add_argument("--older-than-hours", type=float, default=24)
    cleanup.add_argument("--apply", action="store_true")
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
    if args.command in {"worker", "db-init", "migrate-legacy", "gc-work"}:
        from .settings import Settings
        from .database import Database
        from .storage import Storage
        from .worker import Worker
        settings = Settings()
        if args.command == "gc-work":
            if args.older_than_hours < 1:
                parser.error("Cleanup age must be at least one hour")
            work = (settings.data / "work").resolve()
            candidates = [p for p in work.glob("*") if p.is_dir() and not p.is_symlink()
                          and p.resolve().is_relative_to(work) and p.stat().st_mtime < time.time() - args.older_than_hours * 3600]
            print(json.dumps({"apply": args.apply, "directories": [str(p) for p in candidates]}))
            if args.apply:
                db = Database(settings.database_url)
                live = {r['id'] for r in db.snapshot()["running"]}
                try:
                    for path in candidates:
                        if not any(path.name.startswith(job + "-") for job in live):
                            shutil.rmtree(path)
                finally:
                    db.close()
            return
        if args.command == "migrate-legacy":
            from .jobs import Jobs
            from .pipeline import analyze
            manager = Jobs(replace(settings, embedded_worker=False), analyze)
            try:
                manager.storage.ensure_bucket()
                print(json.dumps(manager.migrate_legacy(args.from_directory), ensure_ascii=False))
            finally:
                manager.close()
            return
        database = Database(settings.database_url, settings.capacity, settings.lease_seconds, settings.max_attempts)
        storage = Storage(settings.data / "objects", settings.bucket, settings.endpoint)
        try:
            if args.command == "db-init":
                storage.ensure_bucket()
                print("Database schema version 1 and object storage are ready.")
            else:
                from .resources import install_native_memory_limit
                # Keep this handle alive: the Windows worker and its children
                # share a hard commit budget even outside Docker.
                native_memory_limit = install_native_memory_limit()
                logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
                runner = Worker(database, storage, settings.data, args.queues.split(",") if args.queues else None)
                signal.signal(signal.SIGINT, lambda *_: runner.stop_event.set())
                signal.signal(signal.SIGTERM, lambda *_: runner.stop_event.set())
                runner.run(once=args.once)
        finally:
            database.close()
        return
    if args.command == "serve":
        import uvicorn

        uvicorn.run("video_context.service:app", host=args.host, port=args.port, workers=1, limit_concurrency=8)
    elif args.command == "prepare-models":
        from .pipeline import prepare_ocr, speech_model

        speech_model(download=True)
        prepare_ocr(args.ocr_language)
        print("Speech and OCR models are ready.")
    else:
        from .pipeline import analyze

        path = args.path.resolve()
        if not path.is_file():
            parser.error(f"Media not found: {path}")
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
