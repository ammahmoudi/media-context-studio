import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("VIDEO_CONTEXT_DATA", str(ROOT / "data"))).resolve()
MODELS = Path(os.environ.get("VIDEO_CONTEXT_MODELS", str(ROOT / ".models"))).resolve()
# Set before importing the model libraries. All caches stay inside the project.
os.environ.setdefault("HF_HOME", str(MODELS / "huggingface"))
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(MODELS / "paddlex"))
os.environ.setdefault("PADDLE_HOME", str(MODELS / "paddle"))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

settings_path = ROOT / "config.local.json"
SETTINGS = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
WHISPER_MODEL = os.environ.get("VIDEO_CONTEXT_WHISPER_MODEL", SETTINGS.get("whisper_model", "base"))
WHISPER_DEVICE = os.environ.get("VIDEO_CONTEXT_WHISPER_DEVICE", SETTINGS.get("whisper_device", "cpu"))
WHISPER_COMPUTE = os.environ.get("VIDEO_CONTEXT_WHISPER_COMPUTE", SETTINGS.get("whisper_compute", "int8"))


def prepare_dirs():
    DATA.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
