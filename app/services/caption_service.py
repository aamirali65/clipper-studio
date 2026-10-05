from __future__ import annotations

import re
import tempfile
import threading
from pathlib import Path

from app.models.caption import CaptionSegment
from app.services.ffmpeg_service import FFmpegCancelled, FFmpegError, FFmpegService
from app.utils.logging import get_logger

log = get_logger("captions")


class CaptionsUnavailable(RuntimeError):
    """faster-whisper is not installed."""


class TranscriptionError(RuntimeError):
    """Transcription failed (audio extraction or model inference)."""


def format_srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


_SRT_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def parse_srt_time(text: str) -> float:
    match = _SRT_TIME.match(text.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {text!r}")
    hours, minutes, secs, millis = (int(g) for g in match.groups())
    return hours * 3600 + minutes * 60 + secs + millis / 1000


def segments_to_srt(segments: list[CaptionSegment]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, 1):
        text = segment.text.strip() or "..."
        blocks.append(
            f"{index}\n"
            f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


def parse_srt(content: str) -> list[tuple[float, float, str]]:
    """Parse SRT content into (start, end, text) tuples."""
    entries: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_text, _, end_text = lines[0].partition("-->")
        try:
            start = parse_srt_time(start_text)
            end = parse_srt_time(end_text)
        except ValueError:
            continue
        text = "\n".join(lines[1:]).strip()
        entries.append((start, end, text))
    return entries


def write_srt(segments: list[CaptionSegment], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(segments_to_srt(segments), encoding="utf-8")
    log.info("srt written: %s (%d segments)", target, len(segments))
    return target


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def extract_audio(
    source: Path | str,
    start: float,
    end: float,
    *,
    ffmpeg: FFmpegService | None = None,
    out_path: Path | str | None = None,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Extract a clip range as mono 16 kHz WAV for whisper."""
    service = ffmpeg or FFmpegService()
    duration = max(0.1, end - start)
    if out_path is None:
        handle = tempfile.NamedTemporaryFile(
            suffix=".wav", prefix="clipper_audio_", delete=False
        )
        out_path = Path(handle.name)
        handle.close()
    args = [
        "-ss", f"{start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        str(out_path),
    ]
    try:
        service.run(
            args,
            duration=duration,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
        )
    except FFmpegCancelled as exc:
        raise TranscriptionError("cancelled") from exc
    except FFmpegError as exc:
        raise TranscriptionError(f"Audio extraction failed: {exc}") from exc
    return Path(out_path)


_MODEL_CACHE: dict[tuple[str, str], object] = {}


def load_wav(path: Path | str):
    """Read a mono 16-bit PCM WAV into a float32 array for whisper.

    faster-whisper's own loader (``av.open``) rejects ``metadata_errors``
    on newer PyAV builds, so we decode the WAV ourselves instead.
    """
    import wave

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - numpy ships with faster-whisper
        raise TranscriptionError("numpy is required for transcription") from exc
    try:
        with wave.open(str(path), "rb") as reader:
            if reader.getsampwidth() != 2 or reader.getnchannels() != 1:
                raise TranscriptionError(
                    "Unexpected audio format from FFmpeg "
                    f"({reader.getsampwidth() * 8}-bit "
                    f"{reader.getnchannels()}ch)"
                )
            frames = reader.readframes(reader.getnframes())
    except wave.Error as exc:
        raise TranscriptionError(f"Could not read extracted audio: {exc}") from exc
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return samples


def get_model(model_name: str, download_root: Path | str):
    """Load (or reuse) a faster-whisper model, downloading on first use."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise CaptionsUnavailable(
            "faster-whisper is not installed. Run: "
            "python -m pip install faster-whisper"
        ) from exc
    key = (model_name, str(download_root))
    model = _MODEL_CACHE.get(key)
    if model is None:
        log.info("loading whisper model %s (download_root=%s)", model_name, download_root)
        model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(download_root),
        )
        _MODEL_CACHE[key] = model
    return model


def transcribe_range(
    source: Path | str,
    start: float,
    end: float,
    *,
    model_name: str = "base",
    language: str = "auto",
    download_root: Path | str,
    ffmpeg: FFmpegService | None = None,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[CaptionSegment], str]:
    """Transcribe one clip range. Returns (segments in absolute time, language).

    Segment timestamps are offset by ``start`` so they match the source
    timeline, not the extracted audio file.
    """
    if not whisper_available():
        raise CaptionsUnavailable(
            "faster-whisper is not installed. Run: "
            "python -m pip install faster-whisper"
        )
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionError("cancelled")

    if progress_cb:
        progress_cb("Extracting audio…")
    wav = extract_audio(
        source,
        start,
        end,
        ffmpeg=ffmpeg,
        progress_cb=lambda p, d: progress_cb(f"Extracting audio… {int(p)}%")
        if progress_cb
        else None,
        cancel_event=cancel_event,
    )
    try:
        if progress_cb:
            progress_cb("Loading model (first run downloads it)…")
        model = get_model(model_name, download_root)
        if progress_cb:
            progress_cb("Transcribing…")
        lang = None if language in ("", "auto") else language
        try:
            audio = load_wav(wav)
            segments_iter, info = model.transcribe(
                audio,
                language=lang,
                vad_filter=True,
                beam_size=5,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("whisper failed")
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

        detected = getattr(info, "language", "") or ""
        total = float(getattr(info, "duration", 0.0) or max(0.1, end - start))
        results: list[CaptionSegment] = []
        order = 0
        for segment in segments_iter:
            if cancel_event is not None and cancel_event.is_set():
                raise TranscriptionError("cancelled")
            text = (segment.text or "").strip()
            if not text:
                continue
            results.append(
                CaptionSegment(
                    clip_id=0,
                    start=start + float(segment.start),
                    end=start + float(segment.end),
                    text=text,
                    language=detected,
                    order=order,
                )
            )
            order += 1
            if progress_cb:
                done = min(float(segment.end), total)
                progress_cb(f"Transcribing… {int(done / total * 100)}%")
        if progress_cb:
            progress_cb(f"Done ({len(results)} segments)")
        log.info(
            "transcribed %s [%.2f-%.2f]: %d segments, language=%s",
            Path(source).name,
            start,
            end,
            len(results),
            detected,
        )
        return results, detected
    finally:
        try:
            Path(wav).unlink(missing_ok=True)
        except OSError:
            pass
