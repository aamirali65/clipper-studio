from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.media import MediaItem
from app.utils.paths import format_bytes
from app.utils.timecode import format_clock
from app.ui.theme import MEDIA_CARD


class MediaCard(QFrame):
    def __init__(self, item: MediaItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("MediaCard")
        self.setProperty("selected", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(76)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        self.thumb = QLabel()
        self.thumb.setFixedSize(92, 54)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(
            "background-color: #0e0e11; border: 1px solid #26262c; "
            "border-radius: 6px; color: #4d4d55; font-size: 10px;"
        )
        pixmap = self._load_pixmap(item)
        if pixmap is not None:
            self.thumb.setPixmap(
                pixmap.scaled(
                    self.thumb.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.thumb.setText("VIDEO")
        layout.addWidget(self.thumb)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(item.name)
        name.setStyleSheet("color: #e4e4e8; font-weight: 600; font-size: 12px;")
        name.setToolTip(item.name)
        name.setWordWrap(False)
        info.addWidget(name)

        meta_bits = []
        if item.duration > 0:
            meta_bits.append(format_clock(item.duration))
        if item.width and item.height:
            meta_bits.append(f"{item.width}x{item.height}")
        if item.size_bytes:
            meta_bits.append(format_bytes(item.size_bytes))
        meta_bits.append("YouTube" if item.kind.value == "youtube" else "Local")
        meta = QLabel("  |  ".join(meta_bits))
        meta.setStyleSheet("color: #83838d; font-size: 11px;")
        info.addWidget(meta)

        path_label = QLabel(item.source_path)
        path_label.setStyleSheet("color: #5c5c66; font-size: 10px;")
        path_label.setToolTip(item.source_path)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        info.addWidget(path_label)
        info.addStretch(1)

        layout.addLayout(info, 1)

    @staticmethod
    def _load_pixmap(item: MediaItem) -> QPixmap | None:
        if not item.thumbnail or not Path(item.thumbnail).exists():
            return None
        pixmap = QPixmap(item.thumbnail)
        return pixmap if not pixmap.isNull() else None

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class MediaPanel(QWidget):
    importLocalRequested = Signal()
    downloadRequested = Signal(str)
    cancelDownloadRequested = Signal()
    metadataRequested = Signal(str)
    mediaSelected = Signal(object)
    mediaActivated = Signal(object)
    removeMediaRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[MediaItem] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("MEDIA")
        title.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        header.addWidget(title)
        header.addStretch(1)
        self.count_label = QLabel("0 items")
        self.count_label.setStyleSheet("color: #5c5c66; font-size: 11px;")
        header.addWidget(self.count_label)
        root.addLayout(header)

        self.import_button = QPushButton("Import video")
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.clicked.connect(self.importLocalRequested)
        root.addWidget(self.import_button)

        yt_frame = QFrame()
        yt_frame.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        yt_layout = QVBoxLayout(yt_frame)
        yt_layout.setContentsMargins(10, 10, 10, 10)
        yt_layout.setSpacing(8)

        yt_title = QLabel("YouTube")
        yt_title.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 1px;"
        )
        yt_layout.addWidget(yt_title)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.returnPressed.connect(self._on_check_url)
        yt_layout.addWidget(self.url_edit)

        self.meta_preview = QLabel("")
        self.meta_preview.setWordWrap(True)
        self.meta_preview.setStyleSheet("color: #83838d; font-size: 11px;")
        self.meta_preview.setVisible(False)
        yt_layout.addWidget(self.meta_preview)

        actions = QHBoxLayout()
        self.download_button = QPushButton("Download")
        self.download_button.setObjectName("PrimaryButton")
        self.download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_button.clicked.connect(self._on_download_clicked)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancelDownloadRequested)
        actions.addWidget(self.download_button, 1)
        actions.addWidget(self.cancel_button)
        yt_layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        yt_layout.addWidget(self.progress)

        self.download_status = QLabel("")
        self.download_status.setStyleSheet("color: #83838d; font-size: 11px;")
        self.download_status.setVisible(False)
        yt_layout.addWidget(self.download_status)

        root.addWidget(yt_frame)

        self.listw = QListWidget()
        self.listw.setSpacing(6)
        self.listw.itemClicked.connect(self._on_item_clicked)
        self.listw.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.addWidget(self.listw, 1)

        hint = QLabel("Select a clip source. Double-click to load it.")
        hint.setStyleSheet("color: #5c5c66; font-size: 10px;")
        root.addWidget(hint)

        self.setStyleSheet(MEDIA_CARD)

    # ---- data ----
    def set_media(self, items: list[MediaItem]) -> None:
        self._items = list(items)
        self._rebuild()

    def add_media(self, item: MediaItem) -> None:
        self._items.append(item)
        self._rebuild()
        row = len(self._items) - 1
        self.listw.setCurrentRow(row)

    def remove_media(self, item: MediaItem) -> None:
        self._items = [m for m in self._items if m.id != item.id]
        self._rebuild()

    def items(self) -> list[MediaItem]:
        return list(self._items)

    def selected_item(self) -> MediaItem | None:
        row = self.listw.currentRow()
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def select_media(self, media_id: int | None) -> None:
        if media_id is None:
            return
        for index, item in enumerate(self._items):
            if item.id == media_id:
                self.listw.setCurrentRow(index)
                return

    def _rebuild(self) -> None:
        from PySide6.QtCore import QSize

        self.listw.clear()
        for item in self._items:
            card = MediaCard(item)
            list_item = QListWidgetItem(self.listw)
            list_item.setSizeHint(card.sizeHint() + QSize(0, 8))
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            self.listw.addItem(list_item)
            self.listw.setItemWidget(list_item, card)
        self.count_label.setText(f"{len(self._items)} item{'s' if len(self._items) != 1 else ''}")

    def _on_item_clicked(self, list_item: QListWidgetItem) -> None:
        media_id = list_item.data(Qt.ItemDataRole.UserRole)
        for item in self._items:
            if item.id == media_id:
                self._highlight(media_id)
                self.mediaSelected.emit(item)
                return

    def _on_item_double_clicked(self, list_item: QListWidgetItem) -> None:
        media_id = list_item.data(Qt.ItemDataRole.UserRole)
        for item in self._items:
            if item.id == media_id:
                self.mediaActivated.emit(item)
                return

    def _highlight(self, media_id: int | None) -> None:
        for row in range(self.listw.count()):
            list_item = self.listw.item(row)
            card = self.listw.itemWidget(list_item)
            if isinstance(card, MediaCard):
                card.set_selected(list_item.data(Qt.ItemDataRole.UserRole) == media_id)

    # ---- download flow ----
    def _on_check_url(self) -> None:
        url = self.url_edit.text().strip()
        if url:
            self.metadataRequested.emit(url)

    def set_metadata_preview(self, text: str, error: bool = False) -> None:
        if not text:
            self.meta_preview.setVisible(False)
            return
        self.meta_preview.setStyleSheet(
            "color: #e57373; font-size: 11px;" if error else "color: #9ec1ff; font-size: 11px;"
        )
        self.meta_preview.setText(text)
        self.meta_preview.setVisible(True)

    def _on_download_clicked(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self.download_status.setText("Paste a YouTube URL first.")
            self.download_status.setVisible(True)
            return
        self.downloadRequested.emit(url)

    def set_downloading(self, active: bool) -> None:
        self.download_button.setEnabled(not active)
        self.cancel_button.setVisible(active)
        self.progress.setVisible(active)
        self.download_status.setVisible(active)
        self.url_edit.setEnabled(not active)
        if not active:
            self.progress.setValue(0)

    def set_download_progress(self, percent: float, detail: str) -> None:
        self.progress.setValue(int(percent))
        self.download_status.setText(detail)

    def set_download_error(self, message: str) -> None:
        self.set_downloading(False)
        self.download_status.setText(message)
        self.download_status.setStyleSheet("color: #e57373; font-size: 11px;")
        self.download_status.setVisible(True)

    def set_download_info(self, message: str) -> None:
        self.set_downloading(False)
        self.download_status.setStyleSheet("color: #83838d; font-size: 11px;")
        self.download_status.setText(message)
        self.download_status.setVisible(True)
