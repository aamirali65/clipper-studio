from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.models.caption import CaptionSegment
from app.services.caption_service import transcribe_range
from app.services.ffmpeg_service import FFmpegService
from app.utils.logging import get_logger

log = get_logger("worker.transcribe")


class TranscribeWorker(QThread):
    """Runs whisper transcription off the UI thread."""

    progress = Signal(str)
    completed = Signal(object, str)  # list[CaptionSegment], language
    error = Signal(str)

    def __init__(
        self,
        source: Path | str,
        start: float,
        end: float,
        *,
        model_name: str = "base",
        language: str = "auto",
        download_root: Path | str,
        ffmpeg: FFmpegService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._source = Path(source)
        self._start = start
        self._end = end
        self._model_name = model_name
        self._language = language
        self._download_root = download_root
        self._ffmpeg = ffmpeg or FFmpegService()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            segments, language = transcribe_range(
                self._source,
                self._start,
                self._end,
                model_name=self._model_name,
                language=self._language,
                download_root=self._download_root,
                ffmpeg=self._ffmpeg,
                progress_cb=lambda message: self.progress.emit(message),
                cancel_event=self._cancel,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("transcription failed: %s", exc)
            self.error.emit(str(exc))
        else:
            self.completed.emit(segments, language)
