from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_ERROR = "error"
JOB_CANCELLED = "cancelled"

FINISHED_STATUSES = {JOB_DONE, JOB_ERROR, JOB_CANCELLED}


class ExportJob(BaseModel):
    """One snapshot of a clip export in the background queue.

    All values are captured at enqueue time so later edits to the clip or
    project cannot change a job that is already waiting to run.
    """

    id: int
    clip_name: str
    media_name: str = ""
    media_id: int = 0  # used to load the smart-crop face track
    aspect: str = "9:16"
    source: str = ""
    output: str = ""
    start: float = 0.0
    end: float = 0.0
    preset: str = "balanced"
    crf: int = 21
    pixel_format: str = "yuv420p"
    subtitles_path: str = ""  # SRT burned into the export when set
    smart_crop: bool = False  # follow the media's face track when present
    status: str = JOB_QUEUED
    progress: float = 0.0  # 0.0 .. 1.0
    detail: str = ""
    error: str = ""
    project_name: str = ""
    project_path: str = ""
    created_at: str = Field(default_factory=_utc_now)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUSES
