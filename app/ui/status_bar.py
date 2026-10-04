from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from app.models.clip import ASPECT_RATIOS, resolution_for
from app.utils.timecode import format_timecode


class StatusBar(QWidget):
    """Bottom bar: transport time, aspect ratio, export action."""

    exportRequested = Signal()
    aspectChanged = Signal(str)
    playToggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(44)
        self.setStyleSheet(
            "StatusBar { background-color: #16161a; border-top: 1px solid #26262c; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        self.play_button = QPushButton("▶")
        self.play_button.setFixedSize(32, 28)
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setStyleSheet(
            "QPushButton { background-color: #26262e; border: 1px solid #33333d;"
            " border-radius: 6px; color: #d4d4d8; }"
            "QPushButton:hover { background-color: #2f2f39; }"
        )
        self.play_button.clicked.connect(self.playToggled)
        layout.addWidget(self.play_button)

        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        self.time_label.setStyleSheet(
            "color: #c9c9d1; font-family: Consolas, monospace; background: transparent;"
            " border: none;"
        )
        layout.addWidget(self.time_label)

        self.message = QLabel("")
        self.message.setStyleSheet(
            "color: #6f9dff; font-size: 11px; background: transparent; border: none;"
        )
        layout.addWidget(self.message, 1)

        aspect_caption = QLabel("Ratio")
        aspect_caption.setStyleSheet("color: #63636e; background: transparent; border: none;")
        layout.addWidget(aspect_caption)

        self.aspect_box = QComboBox()
        for key in ASPECT_RATIOS:
            w, h = resolution_for(key)
            self.aspect_box.addItem(f"{key}   {w}x{h}", key)
        self.aspect_box.setFixedWidth(150)
        self.aspect_box.currentIndexChanged.connect(self._on_aspect)
        layout.addWidget(self.aspect_box)

        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("PrimaryButton")
        self.export_button.setFixedHeight(30)
        self.export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.exportRequested)
        layout.addWidget(self.export_button)

    @property
    def aspect(self) -> str:
        return self.aspect_box.currentData() or "9:16"

    def set_aspect(self, aspect: str) -> None:
        index = self.aspect_box.findData(aspect)
        if index >= 0 and index != self.aspect_box.currentIndex():
            self.aspect_box.blockSignals(True)
            self.aspect_box.setCurrentIndex(index)
            self.aspect_box.blockSignals(False)

    def set_times(self, current: float, total: float) -> None:
        self.time_label.setText(
            f"{format_timecode(current)} / {format_timecode(total)}"
        )

    def set_playing(self, playing: bool) -> None:
        self.play_button.setText("⏸" if playing else "▶")

    def set_export_enabled(self, enabled: bool) -> None:
        self.export_button.setEnabled(enabled)

    def show_message(self, text: str) -> None:
        self.message.setText(text)

    def _on_aspect(self) -> None:
        self.aspectChanged.emit(self.aspect)
