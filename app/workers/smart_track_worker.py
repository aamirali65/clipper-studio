from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.services.face_track_service import TrackError, detect_track
from app.utils.logging import get_logger

log = get_logger("smart_track")


class SmartTrackWorker(QThread):
    """Face-tracks one media file for smart crop.

    Emits ``completed`` with the detect_track payload dict.
    """

    progress = Signal(str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        *,
        media_id: int,
        media_path: Path,
        duration: float = 0.0,
        parent=None,
    ):
        super().__init__(parent)
        self._media_id = media_id
        self._media_path = media_path
        self._duration = duration
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            payload = detect_track(
                self._media_path,
                duration=self._duration,
                progress_cb=lambda message: self.progress.emit(message),
                cancel_event=self._cancel,
            )
            payload["media_id"] = self._media_id
            self.completed.emit(payload)
        except TrackError as exc:
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("face tracking failed")
            self.error.emit(str(exc))
