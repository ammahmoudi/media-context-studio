"""Runtime settings shared by independent API and worker processes."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA


@dataclass
class Settings:
    data: Path = field(default_factory=lambda: DATA)
    database_url: str | None = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_DATABASE_URL"))
    bucket: str | None = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_S3_BUCKET"))
    endpoint: str | None = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_S3_ENDPOINT"))
    embedded_worker: bool = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_EMBEDDED_WORKER", "1") == "1")
    capacity: int = field(default_factory=lambda: int(os.getenv("VIDEO_CONTEXT_QUEUE_CAPACITY", "8")))
    lease_seconds: int = field(default_factory=lambda: int(os.getenv("VIDEO_CONTEXT_LEASE_SECONDS", "120")))
    max_attempts: int = field(default_factory=lambda: int(os.getenv("VIDEO_CONTEXT_MAX_ATTEMPTS", "3")))
    api_key: str = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_API_KEY", ""))
    allow_path: bool = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_ALLOW_PATH", "1") == "1")
    secure_cookie: bool = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_SECURE_COOKIE", "0") == "1")
    max_upload_bytes: int = field(default_factory=lambda: int(os.getenv("VIDEO_CONTEXT_MAX_UPLOAD_BYTES", str(8 * 1024**3))))
    allowed_hosts: list[str] = field(default_factory=lambda: os.getenv("VIDEO_CONTEXT_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1],testserver").split(","))

    def __post_init__(self):
        self.data = Path(self.data).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = "sqlite:///" + (self.data / "service.db").as_posix()
        if self.capacity < 1 or self.lease_seconds < 30 or self.max_attempts < 1:
            raise ValueError("Queue capacity must be positive and leases at least 30 seconds")
