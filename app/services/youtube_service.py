from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from app.models.media import MediaItem, MediaKind, MediaProbeResult
from app.models.project import utc_now
from app.services.ffmpeg_service import FFmpegError, FFmpegService
from app.services.video_service import VideoService
from app.utils.logging import get_logger
from app.utils.paths import safe_name

log = get_logger("youtube")

try:
    import yt_dlp
except ImportError:  # pragma: no cover - handled at runtime
    yt_dlp = None

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class YouTubeError(RuntimeError):
    pass


class YouTubeUnavailable(YouTubeError):
    def __init__(self) -> None:
        super().__init__(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
        )


class InvalidYouTubeURL(YouTubeError):
    def __init__(self, url: str) -> None:
        super().__init__(f"Not a valid YouTube URL: {url}")


class YouTubeInfo(BaseModel):
    title: str = "YouTube Video"
    duration: float = 0.0
    thumbnail_url: str = ""
    uploader: str = ""
    webpage_url: str = ""
    width: int = 0
    height: int = 0

    @property
    def resolution_label(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return ""


class YouTubeService:
    def __init__(
        self,
        video_service: VideoService | None = None,
        ffmpeg: FFmpegService | None = None,
    ):
        self.ffmpeg = ffmpeg or FFmpegService()
        self.video_service = video_service or VideoService(self.ffmpeg)

    @staticmethod
    def normalize_url(url: str) -> str:
        url = url.strip()
        if url and not _URL_RE.match(url):
            url = "https://" + url
        return url

    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        url = cls.normalize_url(url)
        if not _URL_RE.match(url):
            return False
        host = (urlparse(url).hostname or "").lower()
        if host not in YOUTUBE_HOSTS:
            return False
        return bool(urlparse(url).path.strip("/"))

    @classmethod
    def validate(cls, url: str) -> str:
        normalized = cls.normalize_url(url)
        if not cls.is_valid_url(normalized):
            raise InvalidYouTubeURL(url)
        return normalized

    def fetch_metadata(self, url: str) -> YouTubeInfo:
        if yt_dlp is None:
            raise YouTubeUnavailable()
        url = self.validate(url)
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
        log.info("fetching metadata for %s", url)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # yt_dlp raises many custom types
            raise YouTubeError(f"Could not read video info: {exc}") from exc
        if info is None:
            raise YouTubeError("YouTube returned no information for this URL")
        return YouTubeInfo(
            title=info.get("title") or "YouTube Video",
            duration=float(info.get("duration") or 0),
            thumbnail_url=info.get("thumbnail") or "",
            uploader=info.get("uploader") or info.get("channel") or "",
            webpage_url=info.get("webpage_url") or url,
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
        )

    def download(
        self,
        url: str,
        target_dir: Path,
        progress_cb: Callable[[float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        probe: bool = True,
    ) -> MediaItem:
        if yt_dlp is None:
            raise YouTubeUnavailable()
        url = self.validate(url)
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        state = {"percent": 0.0}

        def hook(d: dict) -> None:
            if cancel_check and cancel_check():
                raise YouTubeError("cancelled")
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                if total:
                    state["percent"] = max(
                        state["percent"], min(99.0, done / total * 100.0)
                    )
                    speed = d.get("_speed_str") or ""
                    eta = d.get("_eta_str") or ""
                    detail = f"downloading {speed} ETA {eta}".strip()
                    if progress_cb:
                        progress_cb(state["percent"], detail)
            elif status == "finished":
                state["percent"] = 99.0
                if progress_cb:
                    progress_cb(99.0, "processing download")

        opts = {
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "outtmpl": str(target_dir / "%(title).120s [%(id)s].%(ext)s"),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            "retries": 3,
            "fragment_retries": 3,
            "writethumbnail": False,
            "windowsfilenames": True,
        }
        log.info("downloading %s -> %s", url, target_dir)
        before = set(target_dir.glob("*"))
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except YouTubeError:
            raise
        except Exception as exc:
            raise YouTubeError(f"Download failed: {exc}") from exc
        if info is None:
            raise YouTubeError("Download produced no file")

        new_files = [p for p in set(target_dir.glob("*")) - before if p.is_file()]
        filepath = info.get("filepath")
        path = Path(filepath) if filepath else None
        if path is None or not path.exists():
            path = max(new_files, key=lambda p: p.stat().st_size, default=None)
        if path is None or not path.exists():
            raise YouTubeError("Download finished but the file could not be located")

        title = info.get("title") or path.stem
        duration = float(info.get("duration") or 0)
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)

        if probe and self.ffmpeg.available():
            try:
                meta: MediaProbeResult = self.video_service.probe(path)
                duration = meta.duration or duration
                width = meta.width or width
                height = meta.height or height
            except FFmpegError as exc:
                log.warning("probe after download failed: %s", exc)

        thumb_path = target_dir / f"{path.stem}.jpg"
        thumbnail: str | None = None
        thumb_url = info.get("thumbnail")
        if thumb_url:
            try:
                self._download_thumbnail(thumb_url, thumb_path)
                thumbnail = str(thumb_path)
            except Exception as exc:  # noqa: BLE001
                log.warning("thumbnail download failed: %s", exc)
        if thumbnail is None:
            try:
                thumbnail = str(self.video_service.thumbnail(path, thumb_path))
            except FFmpegError:
                thumbnail = None

        item = MediaItem(
            name=safe_name(title),
            kind=MediaKind.youtube,
            source_path=str(path.resolve()),
            url=url,
            duration=duration,
            width=width,
            height=height,
            size_bytes=path.stat().st_size,
            thumbnail=thumbnail,
            created_at=utc_now(),
        )
        if progress_cb:
            progress_cb(100.0, "done")
        log.info("downloaded: %s (%.1fs)", item.name, item.duration)
        return item

    @staticmethod
    def _download_thumbnail(url: str, output: Path) -> None:
        import urllib.request

        request = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 ClipperStudio/0.1"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            data = response.read()
        output.write_bytes(data)
