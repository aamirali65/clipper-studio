from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QVBoxLayout, QWidget

from app.ui.theme import SIDEBAR_BUTTON

PAGES = [
    ("media", "MEDIA", True),
    ("project", "PROJECT", True),
    ("editor", "EDITOR", True),
    ("captions", "CAPTIONS", True),
    ("ai", "AI", True),
    ("auto", "AUTO", True),
    ("export", "EXPORT", True),
    ("queue", "QUEUE", True),
    ("settings", "SETTINGS", True),
]


class Sidebar(QWidget):
    pageChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(148)
        self.setObjectName("Sidebar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 14)
        layout.setSpacing(4)

        brand = QLabel("CLIPPER")
        brand.setStyleSheet(
            "color: #6f9dff; font-size: 13px; font-weight: 700; "
            "letter-spacing: 3px; padding-left: 6px; padding-bottom: 2px;"
        )
        layout.addWidget(brand)
        brand_sub = QLabel("STUDIO")
        brand_sub.setStyleSheet(
            "color: #63636e; font-size: 10px; font-weight: 600; "
            "letter-spacing: 4px; padding-left: 6px; padding-bottom: 10px;"
        )
        layout.addWidget(brand_sub)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for key, label, enabled in PAGES:
            button = QPushButton(f"  {label}")
            button.setCheckable(True)
            button.setEnabled(enabled)
            button.setFixedHeight(32)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(SIDEBAR_BUTTON)
            if not enabled:
                button.setToolTip("Available in a later phase")
            button.clicked.connect(lambda _=False, k=key: self.pageChanged.emit(k))
            self._group.addButton(button)
            self._buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)

        version = QLabel("Phase 6")
        version.setStyleSheet("color: #4d4d55; font-size: 10px; padding-left: 6px;")
        layout.addWidget(version)

    def select(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None and button.isEnabled():
            button.setChecked(True)
            self.pageChanged.emit(key)

    @property
    def current(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return "media"
