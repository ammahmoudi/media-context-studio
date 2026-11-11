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
        "VIDEO_CONTEXT_DATA": value("VIDEO_CONTEXT_NATIVE_DATA", str(ROOT / "data/native-stack")),
        "VIDEO_CONTEXT_API_KEY": value("VIDEO_CONTEXT_API_KEY"),
        "VIDEO_CONTEXT_CPU_THREADS": value("VIDEO_CONTEXT_CPU_THREADS", "4"),
        "OMP_NUM_THREADS": value("VIDEO_CONTEXT_CPU_THREADS", "4"),
        "OPENBLAS_NUM_THREADS": value("VIDEO_CONTEXT_CPU_THREADS", "4"),
    }
    for key, configured in settings.items():
        if configured is not None:
            os.environ.setdefault(key, configured)
    for key, configured in values.items():
        # .env describes the Docker CPU worker; do not replace the laptop's
        # selected CUDA model/precision with the CPU profile's base/int8 values.
        if key.startswith("VIDEO_CONTEXT_") and key not in {
            "VIDEO_CONTEXT_WHISPER_MODEL", "VIDEO_CONTEXT_WHISPER_DEVICE", "VIDEO_CONTEXT_WHISPER_COMPUTE"}:
            os.environ.setdefault(key, configured)
    for suffix in ("MODEL", "DEVICE", "COMPUTE"):
        native = value("VIDEO_CONTEXT_NATIVE_WHISPER_" + suffix)
        if native is not None:
            os.environ.setdefault("VIDEO_CONTEXT_WHISPER_" + suffix, native)


if __name__ == "__main__":
    configure()
    from video_context.cli import main
    main()
