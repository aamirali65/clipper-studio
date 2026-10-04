from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from app.utils.ffmpeg import find_ffmpeg, find_ffprobe
from app.utils.logging import get_logger

log = get_logger("ffmpeg")


class FFmpegError(RuntimeError):
    pass


class FFmpegNotAvailable(FFmpegError):
    def __init__(self) -> None:
        super().__init__(
            "FFmpeg was not found on this system. Install FFmpeg and make sure "
            "ffmpeg.exe and ffprobe.exe are available on your PATH."
        )


class FFmpegCancelled(FFmpegError):
    def __init__(self) -> None:
        super().__init__("Operation cancelled")


def _tail(text: str, lines: int = 12) -> str:
    parts = [line for line in text.splitlines() if line.strip()]
    return "\n".join(parts[-lines:])


class FFmpegService:
    """Thin, safe wrapper around the ffmpeg/ffprobe binaries."""

    def __init__(self) -> None:
        self._ffmpeg = find_ffmpeg()
        self._ffprobe = find_ffprobe()

    @property
    def ffmpeg_path(self) -> str:
        if not self._ffmpeg:
            raise FFmpegNotAvailable()
        return self._ffmpeg

    @property
    def ffprobe_path(self) -> str:
        if not self._ffprobe:
            raise FFmpegNotAvailable()
        return self._ffprobe

    def available(self) -> bool:
        return bool(self._ffmpeg and self._ffprobe)

    def version(self) -> str:
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FFmpegError(f"Could not run FFmpeg: {exc}") from exc
        first = (result.stdout or "").splitlines()
        return first[0] if first else "unknown"

    def run(
        self,
        args: list[str],
        *,
        duration: float | None = None,
        progress_cb: Callable[[float, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> str:
        """Run ffmpeg with an argument list. Never through a shell string."""
        cmd = [self.ffmpeg_path, "-hide_banner", "-y", "-nostdin", *args]
        if progress_cb is not None and "-progress" not in args:
            cmd = [self.ffmpeg_path, "-hide_banner", "-y", "-nostdin",
                   "-progress", "pipe:1", "-nostats", *args]
        log.info("ffmpeg: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise FFmpegError(f"Could not start FFmpeg: {exc}") from exc

        stderr_buf: list[str] = []

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_buf.append(line)
                if len(stderr_buf) > 400:
                    del stderr_buf[:200]

        err_thread = threading.Thread(target=_drain_stderr, daemon=True)
        err_thread.start()

        cancelled = False
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    proc.kill()
                    break
                line = raw.strip()
                if not progress_cb or "=" not in line or duration is None or duration <= 0:
                    continue
                key, _, value = line.partition("=")
                if key in {"out_time_us", "out_time_ms"}:
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    percent = max(0.0, min(100.0, seconds / duration * 100.0))
                    progress_cb(percent, f"{seconds:.1f}s / {duration:.1f}s")
                elif key == "progress" and value == "end":
                    progress_cb(100.0, "finalizing")
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            raise FFmpegError("FFmpeg timed out") from exc
        finally:
            err_thread.join(timeout=2)

        if cancelled:
            raise FFmpegCancelled()
        if proc.returncode != 0:
            tail = _tail("".join(stderr_buf))
            raise FFmpegError(f"FFmpeg failed (exit {proc.returncode}):\n{tail}")
        if progress_cb is not None:
            progress_cb(100.0, "done")
        return "".join(stderr_buf)

    def run_ffprobe(self, args: list[str], timeout: float = 30) -> str:
        cmd = [self.ffprobe_path, *args]
        log.info("ffprobe: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FFmpegError(f"Could not run ffprobe: {exc}") from exc
        if result.returncode != 0:
            raise FFmpegError(
                f"ffprobe failed (exit {result.returncode}):\n{_tail(result.stderr)}"
            )
        return result.stdout

    def extract_thumbnail(
        self,
        source: Path | str,
        output: Path | str,
        at_seconds: float = 1.0,
    ) -> Path:
        source = Path(source)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run(
            [
                "-ss", f"{max(0.0, at_seconds):.3f}",
                "-i", str(source),
                "-frames:v", "1",
                "-q:v", "3",
                str(output),
            ],
            timeout=60,
        )
        return output
