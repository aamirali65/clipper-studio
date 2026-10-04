from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.services.export_service import ExportError, ExportRequest, ExportService
from app.services.ffmpeg_service import FFmpegNotAvailable
from app.utils.logging import get_logger

log = get_logger("worker.export")


class ExportWorker(QThread):
    """Runs an FFmpeg export off the UI thread."""

    progress = Signal(float, str)
    completed = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        request: ExportRequest,
        service: ExportService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._request = request
        self._service = service or ExportService()
        self._cancel = threading.Event()

    @property
    def output_path(self) -> Path:
        return self._request.output

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            output = self._service.export(
                self._request,
                progress_cb=lambda p, d: self.progress.emit(p, d),
                cancel_event=self._cancel,
            )
        except (ExportError, FFmpegNotAvailable) as exc:
            log.error("export failed: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected export failure")
            self.error.emit(f"Export failed: {exc}")
        else:
            self.completed.emit(str(output))
