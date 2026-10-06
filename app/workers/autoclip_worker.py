from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.models.caption import CaptionSegment
from app.services.ffmpeg_service import FFmpegService
from app.services.highlight_service import ai_rank_prompt, analyze, parse_ai_windows
from app.utils.logging import get_logger

log = get_logger("autoclip")


class AutoClipWorker(QThread):
    """Transcribe (if needed) + score highlight windows for one media file.

    Emits ``completed`` with a payload dict::

        {"candidates": [...], "transcript": [...], "language": str,
         "transcribed": bool, "note": str}
    """

    progress = Signal(str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        *,
        media_id: int,
        media_path: Path,
        media_duration: float,
        cached_transcript: list[CaptionSegment] | None,
        cached_language: str = "",
        min_len: float = 15.0,
        max_len: float = 45.0,
        count: int = 5,
        use_ai: bool = False,
        ollama_url: str = "",
        ollama_model: str = "",
        whisper_model: str = "base",
        whisper_language: str = "auto",
        download_root: Path | None = None,
        ffmpeg: FFmpegService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._media_id = media_id
        self._media_path = media_path
        self._duration = media_duration
        self._cached = cached_transcript
        self._cached_language = cached_language
        self._min_len = min_len
        self._max_len = max_len
        self._count = count
        self._use_ai = use_ai
        self._ollama_url = ollama_url
        self._ollama_model = ollama_model
        self._whisper_model = whisper_model
        self._whisper_language = whisper_language
        self._download_root = download_root
        self._ffmpeg = ffmpeg
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: C901 - pipeline with ordered steps
        try:
            segments: list[CaptionSegment] = list(self._cached or [])
            language = self._cached_language or ""
            transcribed = False
            note = ""

            if not segments:
                from app.services.caption_service import (
                    TranscriptionError,
                    transcribe_range,
                    whisper_available,
                )

                if not whisper_available():
                    self.error.emit(
                        "faster-whisper is not installed and there is no "
                        "cached transcript - run: pip install faster-whisper"
                    )
                    return
                if self._cancel.is_set():
                    self.error.emit("cancelled")
                    return
                self.progress.emit(
                    "Transcribing media (first time only; cached after "
                    "this run)..."
                )
                try:
                    segments, language = transcribe_range(
                        self._media_path,
                        0.0,
                        self._duration,
                        model_name=self._whisper_model,
                        language=self._whisper_language,
                        download_root=self._download_root,
                        ffmpeg=self._ffmpeg,
                        progress_cb=lambda message: self.progress.emit(message),
                        cancel_event=self._cancel,
                    )
                except TranscriptionError as exc:
                    self.error.emit("cancelled" if str(exc) == "cancelled" else str(exc))
                    return
                transcribed = True
                if not segments:
                    note = "No speech detected in this media"
            else:
                self.progress.emit(f"Using cached transcript ({len(segments)} segments)")

            if self._cancel.is_set():
                self.error.emit("cancelled")
                return

            self.progress.emit("Scoring highlight windows...")
            ai_windows: list = []
            if self._use_ai and segments:
                ai_notes: list[str] = []
                ai_windows = self._rank_with_ai(segments, ai_notes)
                if ai_notes:
                    note = "; ".join(filter(None, [note, *ai_notes]))

            if self._cancel.is_set():
                self.error.emit("cancelled")
                return

            candidates = analyze(
                segments,
                media_id=self._media_id,
                media_duration=self._duration,
                min_len=self._min_len,
                max_len=self._max_len,
                count=self._count,
                ai_windows=ai_windows,
            )
            self.completed.emit(
                {
                    "candidates": candidates,
                    "transcript": segments,
                    "language": language,
                    "transcribed": transcribed,
                    "note": note,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("auto-clip analysis failed")
            self.error.emit(str(exc))

    def _rank_with_ai(
        self, segments: list[CaptionSegment], note_holder: list[str]
    ) -> list:
        from app.services.ollama_service import OllamaError, chat_once, list_models

        model = self._ollama_model
        try:
            if not model:
                models = list_models(self._ollama_url)
                if not models:
                    note_holder.append("AI ranking skipped: no Ollama models")
                    return []
                model = sorted(models)[0]
            prompt = ai_rank_prompt(
                segments,
                count=self._count,
                min_len=self._min_len,
                max_len=self._max_len,
                duration=self._duration,
            )
            self.progress.emit(f"Ranking highlights with {model}...")
            reply = chat_once(
                self._ollama_url,
                model,
                [{"role": "user", "content": prompt}],
                options={"temperature": 0.2},
            )
        except OllamaError as exc:
            note_holder.append(f"AI ranking skipped ({exc})")
            return []
        if self._cancel.is_set():
            return []
        windows = parse_ai_windows(
            reply,
            duration=self._duration,
            min_len=self._min_len,
            max_len=self._max_len,
        )
        if not windows:
            note_holder.append("AI ranking returned no usable JSON")
        return windows
