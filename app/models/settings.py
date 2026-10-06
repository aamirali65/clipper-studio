from __future__ import annotations

from pydantic import BaseModel


class AppSettings(BaseModel):
    """Global application settings, persisted as JSON (settings.json)."""

    default_aspect: str = "9:16"
    default_preset: str = "balanced"
    output_dir: str = ""  # "" -> use each project's exports/ folder
    whisper_model: str = "base"
    whisper_language: str = "auto"
    burn_captions: bool = False
    smart_crop: bool = False  # follow face tracks on new exports when tracked
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""  # "" -> first model reported by the server
