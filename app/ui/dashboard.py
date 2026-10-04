from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.project import RecentProject
from app.ui.theme import RECENT_ROW
from app.utils.timecode import format_clock


class RecentRow(QPushButton):
    def __init__(self, recent: RecentProject, parent=None):
        super().__init__(parent)
        self.recent = recent
        self.setObjectName("RecentRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(62)
        self.setStyleSheet(RECENT_ROW)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(14)

        name = QLabel(recent.name)
        name.setStyleSheet("color: #e4e4e8; font-weight: 600; font-size: 13px;")
        name.setFixedWidth(200)
        layout.addWidget(name)

        modified = QLabel(_format_modified(recent.modified_at))
        modified.setStyleSheet("color: #83838d; font-size: 11px;")
        modified.setFixedWidth(150)
        layout.addWidget(modified)

        clips = QLabel(f"{recent.clip_count} clip{'s' if recent.clip_count != 1 else ''}")
        clips.setStyleSheet("color: #83838d; font-size: 11px;")
        clips.setFixedWidth(80)
        layout.addWidget(clips)

        path = QLabel(recent.file_path)
        path.setStyleSheet("color: #5c5c66; font-size: 10px;")
        path.setToolTip(recent.file_path)
        layout.addWidget(path, 1)

        if not recent.exists:
            badge = QLabel("missing")
            badge.setStyleSheet("color: #e57373; font-size: 10px;")
            layout.addWidget(badge)


def _format_modified(iso: str) -> str:
    if not iso:
        return "unknown"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


class ProjectDashboard(QWidget):
    createRequested = Signal()
    openRequested = Signal()
    recentSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DashboardRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hero = QFrame()
        hero.setStyleSheet("QFrame { background-color: #131317; border: none; }")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(64, 72, 64, 40)
        hero_layout.setSpacing(6)

        brand = QLabel("CLIPPER STUDIO")
        brand.setStyleSheet(
            "color: #e8eaf0; font-size: 34px; font-weight: 700; letter-spacing: 6px;"
            " background: transparent; border: none;"
        )
        hero_layout.addWidget(brand)

        tagline = QLabel(
            "Professional video clipping - import, trim, and export short-form clips."
        )
        tagline.setStyleSheet(
            "color: #6f7078; font-size: 13px; background: transparent; border: none;"
        )
        hero_layout.addWidget(tagline)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        create = QPushButton("Create New Project")
        create.setObjectName("PrimaryButton")
        create.setFixedHeight(36)
        create.setCursor(Qt.CursorShape.PointingHandCursor)
        create.clicked.connect(self.createRequested)
        open_btn = QPushButton("Open Project")
        open_btn.setFixedHeight(36)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self.openRequested)
        actions.addWidget(create)
        actions.addWidget(open_btn)
        actions.addStretch(1)
        hero_layout.addSpacing(16)
        hero_layout.addLayout(actions)
        root.addWidget(hero)

        recent_wrap = QFrame()
        recent_wrap.setStyleSheet("QFrame { background-color: #101013; border: none; }")
        recent_layout = QVBoxLayout(recent_wrap)
        recent_layout.setContentsMargins(64, 24, 64, 48)
        recent_layout.setSpacing(8)

        header = QLabel("Recent Projects")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700;"
            " letter-spacing: 2px; border: none; background: transparent;"
        )
        recent_layout.addWidget(header)
        recent_layout.addSpacing(6)

        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(8)
        recent_layout.addLayout(self.list_layout)
        recent_layout.addStretch(1)

        self.empty_label = QLabel(
            "No recent projects yet. Create one to get started."
        )
        self.empty_label.setStyleSheet("color: #5c5c66; font-size: 12px;")
        recent_layout.addWidget(self.empty_label)

        root.addWidget(recent_wrap, 1)

    def set_projects(self, projects: list[RecentProject]) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for recent in projects:
            row = RecentRow(recent)
            if recent.exists:
                row.clicked.connect(
                    lambda _=False, path=recent.file_path: self.recentSelected.emit(path)
                )
            else:
                row.setEnabled(False)
            self.list_layout.addWidget(row)
        self.empty_label.setVisible(not projects)
