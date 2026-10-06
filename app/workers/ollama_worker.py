from __future__ import annotations

import threading
from typing import Sequence

from PySide6.QtCore import QThread, Signal

from app.services.ollama_service import OllamaError, chat_stream, list_models
from app.utils.logging import get_logger

log = get_logger("ollama_worker")


class OllamaWorker(QThread):
    """Streams one chat completion from Ollama, token by token."""

    token = Signal(str)
    completed = Signal()
    error = Signal(str)

    def __init__(
        self,
        url: str,
        model: str,
        messages: Sequence[dict],
        *,
        options: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._url = url
        self._model = model
        self._messages = [dict(m) for m in messages]
        self._options = options
        self._cancel = threading.Event()
        self._handle: dict = {}

    def cancel(self) -> None:
        self._cancel.set()
        response = self._handle.get("response")
        if response is not None:
            try:
                response.close()
            except OSError:
                pass

    def run(self) -> None:  # noqa: C901 - single streaming loop
        try:
            for piece in chat_stream(
                self._url,
                self._model,
                self._messages,
                handle=self._handle,
                options=self._options,
            ):
                if self._cancel.is_set():
                    self.error.emit("cancelled")
                    return
                self.token.emit(piece)
            if self._cancel.is_set():
                self.error.emit("cancelled")
                return
            self.completed.emit()
        except OllamaError as exc:
            if self._cancel.is_set():
                self.error.emit("cancelled")
            else:
                log.warning("ollama chat failed: %s", exc)
                self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            if self._cancel.is_set():
                self.error.emit("cancelled")
                return
            log.exception("ollama chat crashed")
            self.error.emit(str(exc))


class ModelsWorker(QThread):
    """Fetches the installed model list in the background."""

    completed = Signal(list)
    error = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        try:
            models = list_models(self._url)
        except OllamaError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("listing ollama models failed")
            self.error.emit(str(exc))
            return
        self.completed.emit(models)
