from __future__ import annotations

import json
from pathlib import Path

from app.models.media import MediaProbeResult, VIDEO_EXTENSIONS
from app.services.ffmpeg_service import FFmpegError, FFmpegService
from app.utils.logging import get_logger
from app.utils.paths import format_bytes

log = get_logger("video")


def _parse_rate(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            denominator = float(den)
            return float(num) / denominator if denominator else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


class VideoService:
    """Reads real media metadata using ffprobe."""

    def __init__(self, ffmpeg: FFmpegService | None = None):
        self.ffmpeg = ffmpeg or FFmpegService()

    @staticmethod
    def is_video(path: Path | str) -> bool:
        return Path(path).suffix.lower() in VIDEO_EXTENSIONS

    def probe(self, path: Path | str) -> MediaProbeResult:
        path = Path(path)
        if not path.exists():
            raise FFmpegError(f"File not found: {path}")
        if not self.is_video(path):
            raise FFmpegError(f"Unsupported file type: {path.suffix}")
        raw = self.ffmpeg.run_ffprobe(
            [
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FFmpegError("ffprobe returned unreadable metadata") from exc

        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0) or 0)
        size = int(fmt.get("size", 0) or 0)
        if not size:
            size = path.stat().st_size

        width = height = 0
        video_codec = audio_codec = ""
        has_audio = False
        frame_rate = 0.0
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video" and not width:
                width = int(stream.get("width", 0) or 0)
                height = int(stream.get("height", 0) or 0)
                video_codec = stream.get("codec_name", "") or ""
                frame_rate = _parse_rate(stream.get("avg_frame_rate"))
                if not duration:
                    duration = float(stream.get("duration", 0) or 0)
            elif stream.get("codec_type") == "audio" and not has_audio:
                has_audio = True
                audio_codec = stream.get("codec_name", "") or ""

        result = MediaProbeResult(
            path=str(path),
            duration=duration,
            width=width,
            height=height,
            size_bytes=size,
            video_codec=video_codec,
            audio_codec=audio_codec,
            has_audio=has_audio,
            frame_rate=round(frame_rate, 3),
            raw=data,
        )
        log.info(
            "probed %s: %s @ %sx%s, %s",
            path.name,
            f"{duration:.1f}s",
            width,
            height,
            format_bytes(size),
        )
        return result

    def thumbnail(self, source: Path | str, output: Path | str) -> Path:
        source = Path(source)
        at = 1.0
        try:
            info = self.probe(source)
            if info.duration > 2:
                at = min(3.0, info.duration / 4)
        except FFmpegError:
            pass
        return self.ffmpeg.extract_thumbnail(source, output, at)
