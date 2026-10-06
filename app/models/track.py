from __future__ import annotations

from pydantic import BaseModel, Field


class TrackKeyframe(BaseModel):
    """One focus-box sample for smart crop.

    ``t`` is seconds since the start of the media; the box is normalized
    to the source frame (0..1, top-left origin).
    """

    t: float = Field(ge=0.0)
    x: float = Field(default=0.0, ge=0.0, le=1.0)
    y: float = Field(default=0.0, ge=0.0, le=1.0)
    w: float = Field(default=1.0, gt=0.0, le=1.0)
    h: float = Field(default=1.0, gt=0.0, le=1.0)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)


class TrackInfo(BaseModel):
    """A stored smart-crop track for one media file."""

    media_id: int
    keyframes: list[TrackKeyframe] = Field(default_factory=list)
    detector: str = ""
    frames: int = 0  # frames sampled
    hits: int = 0  # sampled frames with a detection
    width: int = 0
    height: int = 0
    updated_at: str = ""

    @property
    def has_faces(self) -> bool:
        return bool(self.keyframes) and self.hits > 0
