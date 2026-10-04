from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.models.clip import Clip, EditorSettings, ExportSettings
from app.models.media import MediaItem


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Project(BaseModel):
    name: str
    file_path: str
    created_at: str = Field(default_factory=utc_now)
    modified_at: str = Field(default_factory=utc_now)
    media: list[MediaItem] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    export_settings: ExportSettings = Field(default_factory=ExportSettings)
    editor_settings: EditorSettings = Field(default_factory=EditorSettings)

    @property
    def path(self) -> Path:
        return Path(self.file_path)

    @property
    def directory(self) -> Path:
        return Path(self.file_path).parent

    @property
    def media_dir(self) -> Path:
        return self.directory / "media"

    @property
    def thumbnails_dir(self) -> Path:
        return self.directory / "thumbnails"

    @property
    def exports_dir(self) -> Path:
        return self.directory / "exports"

    @property
    def cache_dir(self) -> Path:
        return self.directory / "cache"

    def media_by_id(self, media_id: int | None) -> MediaItem | None:
        if media_id is None:
            return None
        for item in self.media:
            if item.id == media_id:
                return item
        return None

    def clip_by_id(self, clip_id: int | None) -> Clip | None:
        if clip_id is None:
            return None
        for clip in self.clips:
            if clip.id == clip_id:
                return clip
        return None

    def ensure_dirs(self) -> None:
        for target in (
            self.media_dir,
            self.thumbnails_dir,
            self.exports_dir,
            self.cache_dir,
        ):
            target.mkdir(parents=True, exist_ok=True)

    def touch(self) -> None:
        self.modified_at = utc_now()


class RecentProject(BaseModel):
    name: str
    file_path: str
    modified_at: str
    clip_count: int = 0
    exists: bool = True
