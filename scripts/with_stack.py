"""Connect a native laptop worker/migration command to the Docker infrastructure.

Secrets stay in the ignored .env file and are never passed as command arguments.
"""
import os
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def configure():
    values = {}
    file = ROOT / ".env"
    if file.is_file():
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"\'')
    def value(name, default=None):
        return os.environ.get(name, values.get(name, default))
    password = value("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("Configure .env first; see .env.example")
    settings = {
        "VIDEO_CONTEXT_DATABASE_URL": f"postgresql+psycopg://video:{quote(password, safe='')}@127.0.0.1:{value('VIDEO_CONTEXT_DB_PORT','55433')}/video",
        "VIDEO_CONTEXT_S3_ENDPOINT": f"http://127.0.0.1:{value('VIDEO_CONTEXT_S3_PORT','19020')}",
        "VIDEO_CONTEXT_S3_BUCKET": "video-context",
        "AWS_ACCESS_KEY_ID": value("S3_ACCESS_KEY"), "AWS_SECRET_ACCESS_KEY": value("S3_SECRET_KEY"),
        "AWS_DEFAULT_REGION": "us-east-1", "VIDEO_CONTEXT_EMBEDDED_WORKER": "0",
        "VIDEO_CONTEXT_DATA": str(ROOT / "data/native-stack"),
        "VIDEO_CONTEXT_API_KEY": value("VIDEO_CONTEXT_API_KEY"),
        "VIDEO_CONTEXT_CPU_THREADS": value("VIDEO_CONTEXT_CPU_THREADS", "4"),
        "OMP_NUM_THREADS": value("VIDEO_CONTEXT_CPU_THREADS", "4"),
        "OPENBLAS_NUM_THREADS": value("VIDEO_CONTEXT_CPU_THREADS", "4"),
    }
    for key, value in settings.items():
        if value is not None:
            os.environ.setdefault(key, value)


if __name__ == "__main__":
    configure()
    from video_context.cli import main
    main()
