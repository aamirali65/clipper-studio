from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.clip import ASPECT_RATIOS, Clip
from app.models.media import MediaItem
from app.utils.timecode import format_timecode, parse_timecode


class Inspector(QWidget):
    """Clip properties: source, in/out, duration, aspect ratio."""

    rangeApplied = Signal(float, float)   # start, end
    aspectChanged = Signal(str)
    removeRequested = Signal()
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(264)
        self._source_duration = 0.0
        self._clip: Clip | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 12, 12)
        root.setSpacing(10)

        header = QLabel("INSPECTOR")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(header)

        clip_badge = QFrame()
        clip_badge.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        badge_layout = QVBoxLayout(clip_badge)
        badge_layout.setContentsMargins(10, 8, 10, 8)
        badge_layout.setSpacing(2)
        self.clip_title = QLabel("No clip selected")
        self.clip_title.setStyleSheet("color: #e4e4e8; font-weight: 600;")
        self.clip_title.setWordWrap(True)
        badge_layout.addWidget(self.clip_title)
        self.source_label = QLabel("-")
        self.source_label.setStyleSheet("color: #63636e; font-size: 11px;")
        self.source_label.setToolTip("")
        self.source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        badge_layout.addWidget(self.source_label)
        root.addWidget(clip_badge)

        # start
        root.addWidget(self._section_label("START"))
        start_row = QHBoxLayout()
        self.start_edit = QLineEdit("00:00:00.000")
        self.start_edit.setPlaceholderText("HH:MM:SS.mmm")
        self.start_edit.editingFinished.connect(self._apply_time_edits)
        goto_start = QPushButton("⌂")
        goto_start.setFixedWidth(30)
        goto_start.setToolTip("Jump playhead to start")
        goto_start.clicked.connect(lambda: self.seekRequested.emit(self._current_start()))
        start_row.addWidget(self.start_edit, 1)
        start_row.addWidget(goto_start)
        root.addLayout(start_row)

        # end
        root.addWidget(self._section_label("END"))
        end_row = QHBoxLayout()
        self.end_edit = QLineEdit("00:00:00.000")
        self.end_edit.setPlaceholderText("HH:MM:SS.mmm")
        self.end_edit.editingFinished.connect(self._apply_time_edits)
        goto_end = QPushButton("⌂")
        goto_end.setFixedWidth(30)
        goto_end.setToolTip("Jump playhead to end")
        goto_end.clicked.connect(lambda: self.seekRequested.emit(self._current_end()))
        end_row.addWidget(self.end_edit, 1)
        end_row.addWidget(goto_end)
        root.addLayout(end_row)

        # duration
        root.addWidget(self._section_label("DURATION"))
        self.duration_label = QLineEdit("00:00:00.000")
        self.duration_label.setReadOnly(True)
        self.duration_label.setStyleSheet(
            "QLineEdit { color: #9ec1ff; background-color: #17171c; }"
        )
        root.addWidget(self.duration_label)

        # aspect
        root.addWidget(self._section_label("ASPECT RATIO"))
        self.aspect_combo = QComboBox()
        for key in ASPECT_RATIOS:
            self.aspect_combo.addItem(f"{key}", key)
        self.aspect_combo.setCurrentText("9:16")
        self.aspect_combo.currentTextChanged.connect(self._on_aspect)
        root.addWidget(self.aspect_combo)

        self.resolution_label = QLabel("")
        self.resolution_label.setStyleSheet("color: #63636e; font-size: 11px;")
        root.addWidget(self.resolution_label)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #e57373; font-size: 11px;")
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        root.addStretch(1)

        actions = QHBoxLayout()
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("PrimaryButton")
        self.apply_button.clicked.connect(self._apply_time_edits)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("DangerButton")
        self.remove_button.clicked.connect(self.removeRequested)
        actions.addWidget(self.apply_button, 1)
        actions.addWidget(self.remove_button)
        root.addLayout(actions)

        self.set_clip(None, None)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            "color: #63636e; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
            " padding-top: 4px;"
        )
        return label

    # ---- state ----
    @property
    def aspect(self) -> str:
        return self.aspect_combo.currentData() or "9:16"

    def set_clip(self, clip: Clip | None, media: MediaItem | None) -> None:
        self._clip = clip
        self._source_duration = media.duration if media else 0.0
        enabled = clip is not None
        for widget in (
            self.start_edit, self.end_edit, self.aspect_combo,
            self.apply_button, self.remove_button,
        ):
            widget.setEnabled(enabled)

        if clip is None:
            self.clip_title.setText("No clip selected")
            self.source_label.setText("-")
            self.start_edit.setText("00:00:00.000")
            self.end_edit.setText("00:00:00.000")
            self.duration_label.setText("00:00:00.000")
            self.error_label.setVisible(False)
            return

        self.clip_title.setText(clip.name)
        source_text = media.name if media else f"media #{clip.media_id}"
        if media and media.duration:
            source_text += f"  ({format_timecode(media.duration)})"
        self.source_label.setText(source_text)
        self.source_label.setToolTip(source_text)
        self._refresh_times()
        index = self.aspect_combo.findData(clip.aspect)
        if index >= 0:
            self.aspect_combo.blockSignals(True)
            self.aspect_combo.setCurrentIndex(index)
            self.aspect_combo.blockSignals(False)
        self._refresh_resolution()

    def _refresh_times(self) -> None:
        if self._clip is None:
            return
        self.start_edit.setText(format_timecode(self._clip.start))
        self.end_edit.setText(format_timecode(self._clip.end))
        self.duration_label.setText(format_timecode(self._clip.duration))

    def update_range(self, start: float, end: float) -> None:
        if self._clip is None:
            return
        self._clip.start = start
        self._clip.end = end
        self._refresh_times()

    def set_aspect_value(self, aspect: str) -> None:
        index = self.aspect_combo.findData(aspect)
        if index >= 0 and index != self.aspect_combo.currentIndex():
            self.aspect_combo.blockSignals(True)
            self.aspect_combo.setCurrentIndex(index)
            self.aspect_combo.blockSignals(False)
            self._refresh_resolution()

    def show_error(self, message: str | None) -> None:
        self.error_label.setText(message or "")
        self.error_label.setVisible(bool(message))

    # ---- internals ----
    def _current_start(self) -> float:
        try:
            return parse_timecode(self.start_edit.text())
        except ValueError:
            return 0.0

    def _current_end(self) -> float:
        try:
            return parse_timecode(self.end_edit.text())
        except ValueError:
            return 0.0

    def _apply_time_edits(self) -> None:
        if self._clip is None:
            return
        try:
            start = parse_timecode(self.start_edit.text())
            end = parse_timecode(self.end_edit.text())
        except ValueError as exc:
            self.show_error(f"Invalid timecode: {exc}")
            return

        if start < 0:
            self.show_error("Start must be >= 00:00:00.000")
            return
        if end <= start:
            self.show_error("End must be greater than start")
            return
        if self._source_duration > 0 and end > self._source_duration + 0.001:
            self.show_error(
                f"End exceeds source duration ({format_timecode(self._source_duration)})"
            )
            return
        self.show_error(None)
        self._clip.start = start
        self._clip.end = end
        self._refresh_times()
        self.rangeApplied.emit(start, end)

    def _on_aspect(self, text: str) -> None:
        self._refresh_resolution()
        data = self.aspect_combo.currentData()
        if data and self._clip is not None:
            self.aspectChanged.emit(str(data))

    def _refresh_resolution(self) -> None:
        from app.models.clip import resolution_for

        width, height = resolution_for(self.aspect)
        self.resolution_label.setText(f"Output: {width}x{height}")
