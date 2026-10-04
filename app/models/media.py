from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".wmv",
    ".flv",
    ".ts",
    ".mts",
    ".mpg",
    ".mpeg",
}


class MediaKind(str, Enum):
    local = "local"
    youtube = "youtube"


class MediaItem(BaseModel):
    id: int | None = None
    name: str
    kind: MediaKind = MediaKind.local
    source_path: str
    rel_path: str | None = None
    url: str | None = None
    duration: float = 0.0
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    thumbnail: str | None = None
    created_at: str = ""

    def resolve_path(self, base: Path) -> Path:
        candidate = Path(self.rel_path or self.source_path)
        if candidate.is_absolute():
            return candidate
        return (Path(base) / candidate).resolve()

    @property
    def resolution_label(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "unknown"

    def is_video_file(self) -> bool:
        return Path(self.source_path).suffix.lower() in VIDEO_EXTENSIONS


class MediaProbeResult(BaseModel):
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    has_audio: bool = False
    frame_rate: float = 0.0
    thumbnail: str | None = None
    raw: dict = Field(default_factory=dict)
