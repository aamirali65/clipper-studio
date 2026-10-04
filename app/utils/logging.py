from __future__ import annotations

import logging
import traceback
from logging.handlers import RotatingFileHandler

from app.utils.paths import ensure_dirs, log_file_path

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    ensure_dirs()
    root = logging.getLogger("clipper")
    root.setLevel(level)
    handler = RotatingFileHandler(
        log_file_path(), maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)
    _CONFIGURED = True
    root.info("application starting")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"clipper.{name}")


def format_exception(exc: BaseException) -> str:
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).strip()
