from __future__ import annotations

from pydantic import BaseModel


class AppSettings(BaseModel):
    """Global application settings, persisted as JSON (settings.json)."""

    default_aspect: str = "9:16"
    default_preset: str = "balanced"
    output_dir: str = ""  # "" -> use each project's exports/ folder
