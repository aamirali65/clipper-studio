from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP_NAME = "Clipper Studio"
PROJECT_EXTENSION = ".clipper"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def logs_dir() -> Path:
    override = os.environ.get("CLIPPER_LOG_DIR")
    if override:
        return Path(override)
    return app_root() / "logs"


def projects_dir() -> Path:
    override = os.environ.get("CLIPPER_PROJECTS_DIR")
    if override:
        return Path(override)
    return app_root() / "projects"


def output_dir() -> Path:
    return app_root() / "output"


def cache_dir() -> Path:
    return app_root() / "cache"


def ensure_dirs() -> None:
    for target in (logs_dir(), projects_dir(), output_dir(), cache_dir()):
        target.mkdir(parents=True, exist_ok=True)


def log_file_path() -> Path:
    return logs_dir() / "clipper.log"


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip())
    cleaned = cleaned.strip(". ")
    return cleaned or "Untitled"


def project_workspace(name: str) -> Path:
    return projects_dir() / safe_name(name)


def unique_workspace(name: str) -> Path:
    base = project_workspace(name)
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = project_workspace(f"{name} ({counter})")
        if not candidate.exists():
            return candidate
        counter += 1


def to_portable(path: Path | str, base: Path) -> str:
    path = Path(path).resolve()
    base = Path(base).resolve()
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def from_portable(value: str, base: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (Path(base) / candidate).resolve()


def format_bytes(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    value = float(size)
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}" if index else f"{int(value)} B"


def open_in_explorer(path: Path) -> None:
    target = Path(path)
    if target.is_file():
        target = target.parent
    os.startfile(str(target))  # noqa: S606
