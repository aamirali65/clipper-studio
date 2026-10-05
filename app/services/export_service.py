from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from app.models.clip import QUALITY_PRESETS, Clip, ExportSettings, resolution_for
from app.models.media import MediaItem
from app.models.project import Project
from app.services.ffmpeg_service import FFmpegCancelled, FFmpegError, FFmpegService
from app.utils.logging import get_logger
from app.utils.paths import safe_name
from app.utils.timecode import format_timecode

log = get_logger("export")


class ExportError(RuntimeError):
    pass


class ExportRequest:
    def __init__(
        self,
        source: Path,
        output: Path,
        start: float,
        end: float,
        aspect: str,
        settings: ExportSettings,
        has_audio: bool = True,
        subtitles: Path | None = None,
    ):
        self.source = Path(source)
        self.output = Path(output)
        self.start = start
        self.end = end
        self.aspect = aspect
        self.settings = settings
        self.has_audio = has_audio
        self.subtitles = Path(subtitles) if subtitles else None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def subtitles_filter(path: Path | str) -> str:
    """Build an ffmpeg ``subtitles=`` filter arg with Windows-safe escaping."""
    value = str(Path(path)).replace("\\", "/")
    value = value.replace("'", r"\'")
    value = value.replace(":", r"\:")
    return f"subtitles=filename='{value}'"


def video_filter(request: ExportRequest) -> str:
    """Scale/crop chain, with burned-in subtitles when the request has them."""
    width, height = resolution_for(request.aspect)
    chain = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
    if request.subtitles is not None:
        chain += "," + subtitles_filter(request.subtitles)
    return chain


class ExportService:
    """Builds and runs FFmpeg trim/export commands."""

    def __init__(self, ffmpeg: FFmpegService | None = None):
        self.ffmpeg = ffmpeg or FFmpegService()

    def build_args(self, request: ExportRequest) -> list[str]:
        width, height = resolution_for(request.aspect)
        preset = QUALITY_PRESETS.get(
            request.settings.preset, QUALITY_PRESETS["balanced"]
        )
        args = [
            "-ss", f"{request.start:.3f}",
            "-i", str(request.source),
            "-t", f"{request.duration:.3f}",
            "-vf",
            video_filter(request),
            "-c:v", "libx264",
            "-preset", preset["preset"],
            "-crf", str(request.settings.crf),
            "-pix_fmt", request.settings.pixel_format,
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "2",
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            "-nostats",
            str(request.output),
        ]
        if not request.has_audio:
            args = [a for a in args if a not in {"-c:a", "aac", "-b:a", "192k", "-ac", "2"}]
            index = args.index("-movflags")
            args[index:index] = ["-an"]
        return args

    def export(self, request: ExportRequest, progress_cb: Callable[[float, str], None] | None = None, cancel_event: threading.Event | None = None) -> Path:
        if not request.source.exists():
            raise ExportError(f"Source file not found: {request.source}")
        if request.duration <= 0:
            raise ExportError("Clip duration must be greater than zero")
        request.output.parent.mkdir(parents=True, exist_ok=True)

        info: dict = {}
        if self.ffmpeg.available():
            try:
                probe = self.ffmpeg.run_ffprobe(
                    [
                        "-v", "error",
                        "-print_format", "json",
                        "-show_streams",
                        str(request.source),
                    ]
                )
                import json

                info = json.loads(probe)
                request.has_audio = any(
                    s.get("codec_type") == "audio"
                    for s in info.get("streams", [])
                )
            except (FFmpegError, ValueError):
                pass

        args = self.build_args(request)
        log.info(
            "exporting %s [%s -> %s] -> %s",
            request.source.name,
            format_timecode(request.start),
            format_timecode(request.end),
            request.output,
        )
        if progress_cb:
            progress_cb(0.0, "starting")
        try:
            self.ffmpeg.run(
                args,
                duration=request.duration,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
        except FFmpegCancelled as exc:
            request.output.unlink(missing_ok=True)
            raise ExportError("Export cancelled") from exc
        except FFmpegError as exc:
            request.output.unlink(missing_ok=True)
            raise ExportError(str(exc)) from exc

        if not request.output.exists() or request.output.stat().st_size == 0:
            request.output.unlink(missing_ok=True)
            raise ExportError("Export finished but no output file was produced")
        log.info("export complete: %s", request.output)
        return request.output

    def export_multi_clips(
        self,
        project: Project,
        clips: list[Clip],
        progress_cb: Callable[[float, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[Path]:
        """Export multiple clips, either concatenated or individually.

        Each clip is exported using its own start/end range for the FFmpeg trim.
        Returns a list of output file paths.
        """
        output_paths: list[Path] = []
        for idx, clip in enumerate(clips):
            media = project.media_by_id(clip.media_id)
            if media is None:
                log.warning("Clip %s has no media, skipping", clip.name)
                continue
            source = media.resolve_path(project.directory)
            if not source.exists():
                source = Path(media.source_path)
            if not source.exists():
                log.warning("Source file not found for clip %s: %s", clip.name, media.source_path)
                continue
            out_path = project.exports_dir / f"{safe_name(clip.name)} {idx + 1}.mp4"
            request = ExportRequest(
                source=source,
                output=out_path,
                start=clip.start,
                end=clip.end,
                aspect=clip.aspect,
                settings=project.export_settings,
                has_audio=True,
            )
            try:
                result = self.export(request, progress_cb, cancel_event)
                output_paths.append(result)
                if progress_cb:
                    progress_cb(
                        (idx + 1) / max(len(clips), 1),
                        f"Exported {clip.name}",
                    )
            except ExportError as exc:
                log.error("Failed to export %s: %s", clip.name, exc)
                if progress_cb:
                    progress_cb(1.0, f"Error exporting {clip.name}: {exc}")
        return output_paths

    @staticmethod
    def default_output(project: Project, clip: Clip) -> Path:
        media = project.media_by_id(clip.media_id)
        base = safe_name(clip.name or "clip")
        if media:
            base = f"{safe_name(media.name)} - {base}"
        return project.exports_dir / f"{base}.mp4"
