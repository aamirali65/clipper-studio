from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.clip import Clip
from app.models.project import Project
from app.utils.timecode import format_timecode


class ProjectPanel(QWidget):
    """PROJECT page: project facts and workspace actions."""

    saveRequested = Signal()
    saveAsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header = QLabel("PROJECT")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(header)

        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        self.name_label = QLabel("-")
        self.name_label.setStyleSheet("color: #e4e4e8; font-size: 14px; font-weight: 600;")
        self.name_label.setWordWrap(True)
        card_layout.addWidget(self.name_label)

        self.meta_label = QLabel("-")
        self.meta_label.setStyleSheet("color: #83838d; font-size: 11px;")
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(self.meta_label)

        self.path_label = QLabel("-")
        self.path_label.setStyleSheet("color: #5c5c66; font-size: 10px;")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(self.path_label)

        self.stats_label = QLabel("-")
        self.stats_label.setStyleSheet("color: #9ec1ff; font-size: 11px;")
        card_layout.addWidget(self.stats_label)

        root.addWidget(card)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        save = QPushButton("Save project  (Ctrl+S)")
        save.clicked.connect(self.saveRequested)
        save_as = QPushButton("Save as…  (Ctrl+Shift+S)")
        save_as.clicked.connect(self.saveAsRequested)
        actions.addWidget(save)
        actions.addWidget(save_as)
        actions.addStretch(1)
        root.addLayout(actions)

        note = QLabel(
            "Projects are stored as SQLite (.clipper) files inside their own "
            "workspace folder with media/, thumbnails/, exports/ and cache/."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(note)

    def refresh(self, project: Project | None) -> None:
        if project is None:
            self.name_label.setText("-")
            self.meta_label.setText("-")
            self.path_label.setText("-")
            self.stats_label.setText("-")
            return
        self.name_label.setText(project.name)
        self.meta_label.setText(
            f"Created {project.created_at}   ·   Modified {project.modified_at}"
        )
        self.path_label.setText(str(project.path))
        self.stats_label.setText(
            f"{len(project.media)} media   ·   {len(project.clips)} clips"
        )


class EditorPanel(QWidget):
    """EDITOR page: the clip list for the open project."""

    clipSelected = Signal(int)
    addClipRequested = Signal()
    removeClipRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header_row = QHBoxLayout()
        header = QLabel("EDITOR")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        header_row.addWidget(header)
        header_row.addStretch(1)
        root.addLayout(header_row)

        hint = QLabel(
            "Trim with the timeline handles, or press I / O at the playhead "
            "to set in/out points."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5c5c66; font-size: 10px;")
        root.addWidget(hint)

        self.listw = QListWidget()
        self.listw.itemSelectionChanged.connect(self._on_selection)
        self.listw.itemDoubleClicked.connect(lambda *_: None)
        root.addWidget(self.listw, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        add = QPushButton("New clip from range")
        add.setObjectName("PrimaryButton")
        add.clicked.connect(self.addClipRequested)
        remove = QPushButton("Delete clip")
        remove.setObjectName("DangerButton")
        remove.clicked.connect(self._remove_selected)
        buttons.addWidget(add, 1)
        buttons.addWidget(remove)
        root.addLayout(buttons)

        self._clips: list[Clip] = []

    def set_clips(self, clips: list[Clip], media_names: dict[int, str]) -> None:
        selected_id = self.selected_clip_id()
        self.listw.blockSignals(True)
        self.listw.clear()
        self._clips = list(clips)
        for clip in clips:
            media_name = media_names.get(clip.media_id, f"media #{clip.media_id}")
            text = (
                f"{clip.name}\n"
                f"{media_name}  ·  {format_timecode(clip.start)} → "
                f"{format_timecode(clip.end)}  ·  {clip.duration:.1f}s  ·  {clip.aspect}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, clip.id)
            self.listw.addItem(item)
        if selected_id is not None:
            for row in range(self.listw.count()):
                if self.listw.item(row).data(Qt.ItemDataRole.UserRole) == selected_id:
                    self.listw.setCurrentRow(row)
                    break
        self.listw.blockSignals(False)

    def selected_clip_id(self) -> int | None:
        item = self.listw.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def select_clip(self, clip_id: int | None) -> None:
        if clip_id is None:
            self.listw.clearSelection()
            return
        for row in range(self.listw.count()):
            if self.listw.item(row).data(Qt.ItemDataRole.UserRole) == clip_id:
                self.listw.setCurrentRow(row)
                return

    def _on_selection(self) -> None:
        clip_id = self.selected_clip_id()
        if clip_id is not None:
            self.clipSelected.emit(clip_id)

    def _remove_selected(self) -> None:
        clip_id = self.selected_clip_id()
        if clip_id is not None:
            self.removeClipRequested.emit(clip_id)


class ExportPanel(QWidget):
    """EXPORT page: settings summary + entry point to the export dialog."""

    exportRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header = QLabel("EXPORT")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(header)

        self.summary = QLabel("No clip selected.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            "QLabel { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 12px; color: #83838d; }"
        )
        root.addWidget(self.summary)

        self.export_button = QPushButton("Export clip…")
        self.export_button.setObjectName("PrimaryButton")
        self.export_button.setEnabled(False)
        self.export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_button.clicked.connect(self.exportRequested)
        root.addWidget(self.export_button)

        info = QLabel(
            "Export uses FFmpeg (H.264 + AAC in an MP4 container) and runs in "
            "the background, so the UI stays responsive."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(info)
        root.addStretch(1)

    def refresh(self, clip: Clip | None, project: Project | None, aspect: str) -> None:
        self.export_button.setEnabled(clip is not None)
        if clip is None or project is None:
            self.summary.setText("No clip selected.")
            return
        media = project.media_by_id(clip.media_id)
        from app.models.clip import resolution_for

        w, h = resolution_for(aspect)
        self.summary.setText(
            f"<b style='color:#e4e4e8'>Project:</b> {project.name}<br>"
            f"<b style='color:#e4e4e8'>Clip:</b> {clip.name}<br>"
            f"<b style='color:#e4e4e8'>Source:</b> {media.name if media else '-'}<br>"
            f"<b style='color:#e4e4e8'>Range:</b> {format_timecode(clip.start)} → "
            f"{format_timecode(clip.end)} ({clip.duration:.2f}s)<br>"
            f"<b style='color:#e4e4e8'>Output:</b> MP4 · H.264 · AAC · {w}x{h} ({aspect})"
        )
