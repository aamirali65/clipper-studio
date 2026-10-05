from __future__ import annotations

import json
from pathlib import Path

from app.models.settings import AppSettings
from app.utils.logging import get_logger
from app.utils.paths import settings_path

log = get_logger("settings")


class SettingsService:
    """Loads and saves the global settings.json file."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else settings_path()
        self.settings = self.load()

    def load(self) -> AppSettings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return AppSettings()
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.warning("could not read settings (%s); using defaults", exc)
            return AppSettings()
        try:
            return AppSettings.model_validate(data)
        except ValueError as exc:
            log.warning("invalid settings file (%s); using defaults", exc)
            return AppSettings()

    def save(self, settings: AppSettings | None = None) -> AppSettings:
        if settings is not None:
            self.settings = settings
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                self.settings.model_dump_json(indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.error("could not save settings: %s", exc)
        return self.settings
