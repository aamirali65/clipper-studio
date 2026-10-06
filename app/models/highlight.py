from __future__ import annotations

from pydantic import BaseModel, Field

SOURCE_HEURISTIC = "heuristic"
SOURCE_AI = "ai"


class HighlightCandidate(BaseModel):
    """A suggested clip range found by the auto-clip analyzer."""

    media_id: int
    start: float
    end: float
    score: int = 0  # 0-100, relative within the batch
    title: str = ""
    reason: str = ""
    source: str = SOURCE_HEURISTIC
    order: int = Field(default=0)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)
