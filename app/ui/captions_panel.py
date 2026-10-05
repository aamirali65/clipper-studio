from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.caption import LANGUAGES, WHISPER_MODELS, CaptionSegment
from app.models.clip import Clip
from app.utils.timecode import format_timecode


class CaptionsPanel(QWidget):
    """CAPTIONS page: whisper transcription, editable segments, SRT export."""

    transcribeRequested = Signal()
    cancelRequested = Signal()
    exportSrtRequested = Signal()
    clearRequested = Signal()
    segmentEdited = Signal(int, str)  # segment id, new text

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[CaptionSegment] = []
        self._clip: Clip | None = None
        self._busy = False
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header_row = __import__("PySide6.QtWidgets", fromlist=["QHBoxLayout"]).QHBoxLayout()
        header = QLabel("CAPTIONS")
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

        options = __import__("PySide6.QtWidgets", fromlist=["QHBoxLayout"]).QHBoxLayout()
        options.setSpacing(8)
        self.model_box = QComboBox()
        for key, label in WHISPER_MODELS.items():
            self.model_box.addItem(label, key)
        self.language_box = QComboBox()
        for code, label in LANGUAGES.items():
            self.language_box.addItem(label, code)
        options.addWidget(self.model_box, 1)
        options.addWidget(self.language_box, 1)
        root.addLayout(options)

        actions = __import__("PySide6.QtWidgets", fromlist=["QHBoxLayout"]).QHBoxLayout()
        actions.setSpacing(8)
        self.transcribe_button = QPushButton("Transcribe clip")
        self.transcribe_button.setObjectName("PrimaryButton")
        self.transcribe_button.clicked.connect(self.transcribeRequested)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancelRequested)
        actions.addWidget(self.transcribe_button)
        actions.addWidget(self.cancel_button)
        root.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #83838d; font-size: 11px;")
        root.addWidget(self.status_label)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Start", "End", "Text"])
        self.tree.setEditTriggers(
            QTreeWidget.EditTrigger.DoubleClicked
            | QTreeWidget.EditTrigger.SelectedClicked
        )
        self.tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        header_view = self.tree.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.tree, 1)

        buttons = __import__("PySide6.QtWidgets", fromlist=["QHBoxLayout"]).QHBoxLayout()
        buttons.setSpacing(8)
        self.export_button = QPushButton("Export SRTâ€¦")
        self.export_button.clicked.connect(self.exportSrtRequested)
        self.clear_button = QPushButton("Clear captions")
        self.clear_button.clicked.connect(self.clearRequested)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.clear_button)
        root.addLayout(buttons)

        hint = QLabel(
            "Transcription runs locally with faster-whisper (CPU). The model "
            "downloads once on first use, then works offline. Double-click a "
            "row's text to edit it - edits save automatically."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(hint)

        self.set_clip(None)
        self._update_buttons()

    # -------------------------------------------------------------- state
    def set_clip(self, clip: Clip | None, media_name: str = "") -> None:
        self._clip = clip
        if clip is None:
            self.clip_label.setText("No clip selected.")
        else:
            where = f"  Â·  {media_name}" if media_name else ""
            self.clip_label.setText(
                f"<b style='color:#e4e4e8'>{clip.name}</b>{where}<br>"
                f"{format_timecode(clip.start)} â†’ {format_timecode(clip.end)}"
                f"  Â·  {clip.duration:.2f}s  Â·  {clip.aspect}"
            )
        self._update_buttons()

    def set_segments(self, segments: list[CaptionSegment]) -> None:
        self._loading = True
        self._segments = list(segments)
        self.tree.clear()
        for segment in self._segments:
            item = QTreeWidgetItem(
                [
                    format_timecode(segment.start),
                    format_timecode(segment.end),
                    segment.text,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, segment.id)
            self.tree.addTopLevelItem(item)
        self._loading = False
        self._update_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.transcribe_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if busy:
            self.status_label.setStyleSheet("color: #9ec1ff; font-size: 11px;")
        self._update_buttons()

    def set_status(self, text: str, error: bool = False) -> None:
        color = "#e57373" if error else "#83838d"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status_label.setText(text)

    def set_whisper_available(self, available: bool) -> None:
        self.transcribe_button.setEnabled(available and self._clip is not None)
        if not available:
            self.transcribe_button.setToolTip(
                "faster-whisper is not installed (pip install faster-whisper)"
            )
            self.set_status(
                "faster-whisper is not installed - run: "
                "python -m pip install faster-whisper",
                error=True,
            )
        else:
            self.transcribe_button.setToolTip("")
        self._whisper_available = available
        self._update_buttons()

    def selected_model(self) -> str:
        return self.model_box.currentData() or "base"

    def selected_language(self) -> str:
        return self.language_box.currentData() or "auto"

    def set_model_value(self, model: str) -> None:
        index = self.model_box.findData(model)
        if index >= 0:
            self.model_box.setCurrentIndex(index)

    def set_language_value(self, language: str) -> None:
        index = self.language_box.findData(language)
        if index >= 0:
            self.language_box.setCurrentIndex(index)

    @property
    def segments(self) -> list[CaptionSegment]:
        return list(self._segments)

    # ------------------------------------------------------------ helpers
    def _whisper_ok(self) -> bool:
        return getattr(self, "_whisper_available", True)

    def _update_buttons(self) -> None:
        has_clip = self._clip is not None
        has_segments = bool(self._segments)
        if not self._busy:
            self.transcribe_button.setEnabled(has_clip and self._whisper_ok())
        self.export_button.setEnabled(has_segments)
        self.clear_button.setEnabled(has_segments)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading or column != 2:
            return
        segment_id = item.data(0, Qt.ItemDataRole.UserRole)
        if segment_id is None:
            return
        text = item.text(2)
        for segment in self._segments:
            if segment.id == segment_id:
                if segment.text != text:
                    segment.text = text
                    self.segmentEdited.emit(int(segment_id), text)
                break
