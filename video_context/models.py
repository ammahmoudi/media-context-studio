from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speech: bool = True
    ocr: bool = True
    language: str | None = Field(default=None, min_length=2, max_length=8)
    ocr_language: str = Field(default="en", min_length=2, max_length=32)
    ocr_deduplicate: bool = True
    sample_interval_seconds: float = Field(default=30, ge=1, le=600)
    max_frames: int = Field(default=300, ge=1, le=2000)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    options: Options = Field(default_factory=Options)


JobStatus = Literal["queued", "running", "complete", "partial", "failed"]
