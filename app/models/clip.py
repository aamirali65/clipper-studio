from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
}

RESOLUTIONS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def resolution_for(aspect: str) -> tuple[int, int]:
    if aspect in RESOLUTIONS:
        return RESOLUTIONS[aspect]
    ratio = ASPECT_RATIOS.get(aspect)
    if not ratio:
        return RESOLUTIONS["16:9"]
    return RESOLUTIONS["16:9"]


class Clip(BaseModel):
    id: int | None = None
    media_id: int
    name: str = "Clip"
    start: float = 0.0
    end: float = 0.0
    aspect: str = "9:16"
    created_at: str = ""
    updated_at: str = ""
    timeline_start: float = 0.0
    timeline_end: float = 0.0
    order: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def validate_range(self, source_duration: float) -> list[str]:
        errors: list[str] = []
        if self.start < 0:
            errors.append("Start must be >= 00:00:00.000")
        if self.end <= self.start:
            errors.append("End must be greater than start")
        if source_duration > 0 and self.end > source_duration + 0.001:
            errors.append("End cannot exceed the source duration")
        if self.aspect not in ASPECT_RATIOS:
            errors.append(f"Unsupported aspect ratio: {self.aspect}")
        return errors

    def validate_timeline(self, source_duration: float) -> list[str]:
        errors: list[str] = []
        if self.timeline_start < 0:
            errors.append("Timeline start must be >= 00:00:00.000")
        if self.timeline_end <= self.timeline_start:
            errors.append("Timeline end must be greater than timeline start")
        if source_duration > 0 and self.timeline_end > source_duration + 0.001:
            errors.append("Timeline end cannot exceed the source duration")
        return errors


class ExportSettings(BaseModel):
    format: str = "MP4"
    video_codec: str = "H.264"
    audio_codec: str = "AAC"
    preset: str = "balanced"
    crf: int = 21
    pixel_format: str = "yuv420p"


class EditorSettings(BaseModel):
    aspect: str = "9:16"
    volume: float = 1.0
    muted: bool = False
    timeline_px_per_second: float = 0.0
    last_media_id: int | None = None


QUALITY_PRESETS: dict[str, dict] = {
    "fast": {"label": "Fast (draft)", "preset": "veryfast", "crf": 26},
    "balanced": {"label": "Balanced", "preset": "medium", "crf": 21},
    "high": {"label": "High quality", "preset": "slow", "crf": 17},
}
