from __future__ import annotations

from pydantic import BaseModel, Field

LANGUAGES: dict[str, str] = {
    "auto": "Auto detect",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
    "ar": "Arabic",
}

WHISPER_MODELS: dict[str, str] = {
    "tiny": "Tiny (~75 MB, fastest)",
    "base": "Base (~145 MB, balanced)",
    "small": "Small (~466 MB, better)",
    "medium": "Medium (~1.5 GB, best CPU)",
}


class CaptionSegment(BaseModel):
    id: int | None = None
    clip_id: int
    start: float = 0.0
    end: float = 0.0
    text: str = ""
    language: str = ""
    order: int = Field(default=0)
