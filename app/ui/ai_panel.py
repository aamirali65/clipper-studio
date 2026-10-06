from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.clip import Clip
from app.services.ai_service import ACTIONS
from app.utils.timecode import format_timecode


class AIPanel(QWidget):
    """AI page: local Ollama chat with clip/transcript context."""

    sendRequested = Signal(str)  # full user message text
    actionRequested = Signal(str)  # quick action id
    cancelRequested = Signal()
    refreshRequested = Signal()
    clearRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[dict] = []
        self._log = ""
        self._streaming = False
        self._stream_text = ""
        self._busy = False
        self._clip: Clip | None = None
        self._preferred_model = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header_row = QHBoxLayout()
        header = QLabel("AI ASSISTANT")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        header_row.addWidget(header)
        header_row.addStretch(1)
        root.addLayout(header_row)

        self.clip_label = QLabel("No clip selected.")
        self.clip_label.setWordWrap(True)
        self.clip_label.setStyleSheet(
            "QLabel { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 10px 12px; color: #83838d; }"
        )
        root.addWidget(self.clip_label)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.model_box = QComboBox()
        self.model_box.setToolTip("Ollama model (refresh the list on the AI page)")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-check the Ollama server for installed models")
        self.refresh_button.clicked.connect(self.refreshRequested)
        model_row.addWidget(self.model_box, 1)
        model_row.addWidget(self.refresh_button)
        root.addLayout(model_row)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        button_labels = {"summary": "Summary", "titles": "Titles", "hashtags": "Hashtags"}
        self._action_buttons: dict[str, QPushButton] = {}
        for action_id, (label, _instruction) in ACTIONS.items():
            button = QPushButton(button_labels.get(action_id, label))
            button.setToolTip(label)
            button.clicked.connect(
                lambda _=False, key=action_id: self.actionRequested.emit(key)
            )
            self._action_buttons[action_id] = button
            actions.addWidget(button)
        root.addLayout(actions)

        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_log.setAcceptRichText(False)
        self.chat_log.setPlaceholderText(
            "Ask about your project, clips or transcript...\n"
            "Quick actions above use the selected clip's transcript."
        )
        self.chat_log.setStyleSheet(
            "QTextEdit { background-color: #121216; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 8px; color: #d6d6dc;"
            " font-size: 12px; }"
        )
        root.addWidget(self.chat_log, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message the assistant...")
        self.input.setClearButtonEnabled(True)
        self.input.returnPressed.connect(self._send)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.clicked.connect(self._send)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancelRequested)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        input_row.addWidget(self.cancel_button)
        root.addLayout(input_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.clear_button = QPushButton("Clear chat")
        self.clear_button.clicked.connect(self.clearRequested)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #83838d; font-size: 11px;")
        meta_row.addWidget(self.clear_button)
        meta_row.addWidget(self.status_label, 1)
        root.addLayout(meta_row)

        hint = QLabel(
            "Answers stream from a local Ollama server - install it from "
            "ollama.com, run `ollama serve` and pull a model. Chat history "
            "is kept for this session only."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(hint)

        self._render()
        self._update_buttons()

    # ------------------------------------------------------------- state
    def set_clip(self, clip: Clip | None, media_name: str = "") -> None:
        self._clip = clip
        if clip is None:
            self.clip_label.setText(
                "No clip selected - quick actions need a clip with captions."
            )
        else:
            where = f"  \u00b7  {media_name}" if media_name else ""
            self.clip_label.setText(
                f"<b style='color:#e4e4e8'>{clip.name}</b>{where}<br>"
                f"{format_timecode(clip.start)} \u2192 {format_timecode(clip.end)}"
                f"  \u00b7  {clip.duration:.2f}s  \u00b7  {clip.aspect}"
            )
        self._update_buttons()

    def set_models(self, models: list[str], preferred: str = "") -> None:
        if preferred:
            self._preferred_model = preferred
        current = self.model_box.currentData()
        self.model_box.clear()
        for name in models:
            self.model_box.addItem(name, name)
        target = self._preferred_model or current
        if target:
            index = self.model_box.findData(target)
            if index >= 0:
                self.model_box.setCurrentIndex(index)
        self._update_buttons()

    def selected_model(self) -> str:
        return self.model_box.currentData() or ""

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.input.setEnabled(not busy)
        if busy:
            self.status_label.setStyleSheet("color: #9ec1ff; font-size: 11px;")
        self._update_buttons()

    def set_status(self, text: str, error: bool = False) -> None:
        color = "#e57373" if error else "#83838d"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status_label.setText(text)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def history(self) -> list[dict]:
        return [dict(item) for item in self._history]

    # ------------------------------------------------------------ chat io
    def append_user(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})
        self._log += f"You:\n{text}\n\n"
        self._render()

    def begin_assistant(self) -> None:
        self._streaming = True
        self._stream_text = ""
        self._render()

    def append_token(self, token: str) -> None:
        if not self._streaming or not token:
            return
        self._stream_text += token
        self._render()

    def finish_assistant(self) -> None:
        if not self._streaming:
            return
        text = self._stream_text.strip()
        self._streaming = False
        self._stream_text = ""
        if text:
            self._log += f"Assistant:\n{text}\n\n"
            self._history.append({"role": "assistant", "content": text})
        self._render()

    def clear_chat(self) -> None:
        self._history.clear()
        self._log = ""
        self._streaming = False
        self._stream_text = ""
        self.input.clear()
        self.set_status("")
        self._render()

    def log_text(self) -> str:
        return self.chat_log.toPlainText()

    # ------------------------------------------------------------ helpers
    def _send(self) -> None:
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            self.set_status("Type a message first", error=True)
            return
        self.input.clear()
        self.sendRequested.emit(text)

    def _render(self) -> None:
        text = self._log
        if self._streaming:
            text += f"Assistant:\n{self._stream_text}"
        scrollbar = self.chat_log.verticalScrollBar()
        at_end = scrollbar.value() >= scrollbar.maximum() - 4
        self.chat_log.setPlainText(text)
        if at_end:
            scrollbar.setValue(scrollbar.maximum())

    def _update_buttons(self) -> None:
        has_clip = self._clip is not None
        for button in self._action_buttons.values():
            button.setEnabled(has_clip and not self._busy)
        self.send_button.setEnabled(not self._busy)
        self.cancel_button.setEnabled(self._busy)
        self.clear_button.setEnabled(bool(self._history) and not self._busy)
        self.model_box.setEnabled(not self._busy)
        self.refresh_button.setEnabled(not self._busy)
