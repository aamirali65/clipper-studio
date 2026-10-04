from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.services.ffmpeg_service import FFmpegError, FFmpegNotAvailable
from app.services.video_service import VideoService
from app.utils.logging import get_logger

log = get_logger("worker.probe")


class ProbeWorker(QThread):
    """Reads media metadata off the UI thread."""

    completed = Signal(object)
    error = Signal(str)

    def __init__(self, path: Path | str, service: VideoService | None = None, parent=None):
        super().__init__(parent)
        self._path = Path(path)
        self._service = service or VideoService()

    def run(self) -> None:
        try:
            info = self._service.probe(self._path)
            thumb = self._service.thumbnail(
                self._path, self._path.parent / f".clipper_thumb_{self._path.stem}.jpg"
            )
            info.thumbnail = str(thumb) if thumb else None
        except (FFmpegNotAvailable, FFmpegError) as exc:
            log.error("probe failed for %s: %s", self._path, exc)
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected probe failure")
            self.error.emit(f"Could not read video: {exc}")
        else:
            self.completed.emit(info)
