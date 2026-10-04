from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.models.media import MediaItem
from app.services.ffmpeg_service import FFmpegError, FFmpegNotAvailable
from app.services.youtube_service import YouTubeError, YouTubeService
from app.utils.logging import get_logger

log = get_logger("worker.download")


class DownloadWorker(QThread):
    """Downloads a YouTube video off the UI thread."""

    progress = Signal(float, str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        url: str,
        target_dir: Path,
        service: YouTubeService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._url = url
        self._target_dir = Path(target_dir)
        self._service = service or YouTubeService()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _run(self) -> MediaItem:
        return self._service.download(
            self._url,
            self._target_dir,
            progress_cb=lambda p, d: self.progress.emit(p, d),
            cancel_check=lambda: self._cancelled,
        )

    def run(self) -> None:  # noqa: D401
        try:
            item = self._run()
        except (YouTubeError, FFmpegNotAvailable, FFmpegError) as exc:
            log.error("download failed: %s", exc)
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected download failure")
            self.error.emit(f"Download failed: {exc}")
        else:
            self.completed.emit(item)


class MetadataWorker(QThread):
    """Fetches YouTube metadata without downloading."""

    completed = Signal(object)
    error = Signal(str)

    def __init__(self, url: str, service: YouTubeService | None = None, parent=None):
        super().__init__(parent)
        self._url = url
        self._service = service or YouTubeService()

    def run(self) -> None:
        try:
            info = self._service.fetch_metadata(self._url)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
        else:
            self.completed.emit(info)
