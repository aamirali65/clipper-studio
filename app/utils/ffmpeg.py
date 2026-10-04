from __future__ import annotations

import shutil
from pathlib import Path

FFMPEG_CANDIDATES = [
    "ffmpeg",
    "ffmpeg.exe",
]
FFPROBE_CANDIDATES = [
    "ffprobe",
    "ffprobe.exe",
]

_EXTRA_DIRS = [
    Path(r"C:\ffmpeg\bin"),
    Path(r"C:\Program Files\ffmpeg\bin"),
    Path(r"C:\Program Files (x86)\ffmpeg\bin"),
]


def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in _EXTRA_DIRS:
        candidate = directory / name
        if candidate.exists():
            return str(candidate)
    return None


def find_ffmpeg() -> str | None:
    for name in FFMPEG_CANDIDATES:
        found = _which(name)
        if found:
            return found
    return None


def find_ffprobe() -> str | None:
    for name in FFPROBE_CANDIDATES:
        found = _which(name)
        if found:
            return found
    return None
