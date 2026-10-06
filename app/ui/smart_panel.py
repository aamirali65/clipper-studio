from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.media import MediaItem
from app.utils.timecode import format_clock


class SmartPanel(QWidget):
    """SMART CROP page: face-track a media file for smart framing on export."""

    analyzeRequested = Signal()
    cancelRequested = Signal()
    clearRequested = Signal()
    mediaChanged = Signal(int)
    overlayChanged = Signal(bool)  # show framing on the preview

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header = QLabel("SMART CROP")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(header)

        self.media_box = QComboBox()
        self.media_box.setToolTip("Media file to face-track")
        self.media_box.currentIndexChanged.connect(self._on_media_changed)
        root.addWidget(self.media_box)

        self.track_label = QLabel("No media selected.")
        self.track_label.setWordWrap(True)
        self.track_label.setStyleSheet(
            "QLabel { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 8px 12px; color: #83838d; }"
        )
        root.addWidget(self.track_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.analyze_button = QPushButton("Track faces")
        self.analyze_button.setObjectName("PrimaryButton")
        self.analyze_button.clicked.connect(self.analyzeRequested)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancelRequested)
        actions.addWidget(self.analyze_button)
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

        self.overlay_box = QCheckBox("Show framing on preview")
        self.overlay_box.setToolTip(
            "Draw the tracked face box and the export crop window over the "
            "video preview (updates while playing/scrubbing)"
        )
        self.overlay_box.toggled.connect(self.overlayChanged)
        root.addWidget(self.overlay_box)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.clear_button = QPushButton("Clear track")
        self.clear_button.clicked.connect(self.clearRequested)
        bottom.addWidget(self.clear_button)
        bottom.addStretch(1)
        root.addLayout(bottom)

        hint = QLabel(
            "Face tracking samples frames locally (YuNet via OpenCV) and "
            "stores a focus track in the project. Exports with smart crop "
            "on follow the subject instead of always center-cropping."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(hint)
        root.addStretch(1)

        self._update_buttons()

    # ------------------------------------------------------------- state
    def set_media(self, items: list[MediaItem], preferred_id: int | None = None) -> None:
        current = self.selected_media_id()
        target = preferred_id if preferred_id is not None else current
        self.media_box.blockSignals(True)
        self.media_box.clear()
        for item in items:
            duration = f"  {format_clock(item.duration)}" if item.duration else ""
            self.media_box.addItem(f"{item.name}{duration}", item.id)
        self.media_box.blockSignals(False)
        if target is not None:
            index = self.media_box.findData(target)
            if index >= 0:
                self.media_box.setCurrentIndex(index)
        if self.media_box.currentData() is not None:
            self.mediaChanged.emit(int(self.media_box.currentData()))

    def selected_media_id(self) -> int | None:
        data = self.media_box.currentData()
        return int(data) if data is not None else None

    def set_track_status(self, text: str) -> None:
        self.track_label.setText(text)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.analyze_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        for widget in (self.media_box, self.overlay_box):
            widget.setEnabled(not busy)
        if busy:
            self.status_label.setStyleSheet("color: #9ac1ff; font-size: 11px;")
        self._update_buttons()

    @property
    def busy(self) -> bool:
        return self._busy

    def set_status(self, text: str, error: bool = False) -> None:
        color = "#e57373" if error else "#83838d"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status_label.setText(text)

    def overlay_checked(self) -> bool:
        return self.overlay_box.isChecked()

    def set_overlay_checked(self, checked: bool) -> None:
        self.overlay_box.setChecked(checked)

    # ------------------------------------------------------------ helpers
    def _on_media_changed(self, _index: int) -> None:
        data = self.media_box.currentData()
        if data is not None:
            self.mediaChanged.emit(int(data))

    def _update_buttons(self) -> None:
        has_media = self.media_box.count() > 0
        self.analyze_button.setEnabled(has_media and not self._busy)
        self.clear_button.setEnabled(has_media and not self._busy)
