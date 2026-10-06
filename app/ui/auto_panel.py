from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.highlight import HighlightCandidate
from app.models.media import MediaItem
from app.utils.timecode import format_clock


class AutoPanel(QWidget):
    """AUTO CLIPS page: find clip-worthy moments, review, add to timeline."""

    analyzeRequested = Signal()
    cancelRequested = Signal()
    previewRequested = Signal(int)  # candidate index
    addRequested = Signal()
    clearRequested = Signal()
    mediaChanged = Signal(int)  # media id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._candidates: list[HighlightCandidate] = []
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header = QLabel("AUTO CLIPS")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(header)

        self.media_box = QComboBox()
        self.media_box.setToolTip("Media file to analyze")
        self.media_box.currentIndexChanged.connect(self._on_media_changed)
        root.addWidget(self.media_box)

        self.transcript_label = QLabel("No media selected.")
        self.transcript_label.setWordWrap(True)
        self.transcript_label.setStyleSheet(
            "QLabel { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 8px 12px; color: #83838d; }"
        )
        root.addWidget(self.transcript_label)

        options = QHBoxLayout()
        options.setSpacing(6)
        self.min_box = QSpinBox()
        self.min_box.setRange(5, 600)
        self.min_box.setSingleStep(5)
        self.min_box.setPrefix("Min ")
        self.min_box.setSuffix("s")
        self.min_box.setValue(15)
        self.max_box = QSpinBox()
        self.max_box.setRange(10, 900)
        self.max_box.setSingleStep(5)
        self.max_box.setPrefix("Max ")
        self.max_box.setSuffix("s")
        self.max_box.setValue(45)
        options.addWidget(self.min_box)
        options.addWidget(self.max_box)
        root.addLayout(options)

        options2 = QHBoxLayout()
        options2.setSpacing(6)
        self.count_box = QSpinBox()
        self.count_box.setRange(1, 20)
        self.count_box.setPrefix("Clips ")
        self.count_box.setValue(5)
        self.ai_box = QCheckBox("AI rank")
        self.ai_box.setToolTip(
            "Ask the local Ollama model to pick highlight moments from the "
            "transcript (results are validated and merged with the "
            "heuristic scoring)"
        )
        options2.addWidget(self.count_box, 1)
        options2.addWidget(self.ai_box, 1)
        root.addLayout(options2)

        self.force_box = QCheckBox("Force re-transcribe")
        self.force_box.setToolTip(
            "Ignore the cached transcript and run whisper on the whole "
            "media again (slow on CPU)"
        )
        root.addWidget(self.force_box)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.analyze_button = QPushButton("Find clips")
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

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Score", "Len", "Title"])
        self.tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        header_view = self.tree.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        root.addWidget(self.tree, 1)

        review = QHBoxLayout()
        review.setSpacing(8)
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self._on_preview)
        self.add_button = QPushButton("Add to timeline")
        self.add_button.setObjectName("PrimaryButton")
        self.add_button.clicked.connect(self.addRequested)
        review.addWidget(self.preview_button)
        review.addWidget(self.add_button)
        root.addLayout(review)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.clear_button = QPushButton("Clear results")
        self.clear_button.clicked.connect(self.clearRequested)
        bottom.addWidget(self.clear_button)
        bottom.addStretch(1)
        root.addLayout(bottom)

        hint = QLabel(
            "Finds clip-worthy moments from the media's transcript. "
            "Heuristics always run; AI rank adds Ollama picks. Check the "
            "results you want, preview them, then add to the timeline."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(hint)

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

    def set_transcript_status(self, text: str) -> None:
        self.transcript_label.setText(text)

    def set_candidates(self, candidates: list[HighlightCandidate]) -> None:
        self._candidates = list(candidates)
        self.tree.clear()
        for index, candidate in enumerate(self._candidates):
            item = QTreeWidgetItem(
                [
                    str(candidate.score),
                    f"{candidate.duration:.0f}s",
                    candidate.title or "Highlight",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(0, Qt.CheckState.Checked)
            source = "AI" if candidate.source == "ai" else "heuristic"
            item.setToolTip(
                0,
                f"{candidate.start:.1f}s - {candidate.end:.1f}s  |  "
                f"{candidate.reason}  |  {source}",
            )
            self.tree.addTopLevelItem(item)
        self._update_buttons()

    @property
    def candidates(self) -> list[HighlightCandidate]:
        return list(self._candidates)

    def checked_candidates(self) -> list[HighlightCandidate]:
        checked: list[HighlightCandidate] = []
        for row in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(row)
            if item.checkState(0) == Qt.CheckState.Checked:
                index = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(index, int) and 0 <= index < len(self._candidates):
                    checked.append(self._candidates[index])
        return checked

    def current_candidate_index(self) -> int | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        index = item.data(0, Qt.ItemDataRole.UserRole)
        return int(index) if isinstance(index, int) else None

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.analyze_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        for widget in (
            self.media_box,
            self.min_box,
            self.max_box,
            self.count_box,
            self.ai_box,
            self.force_box,
        ):
            widget.setEnabled(not busy)
        if busy:
            self.status_label.setStyleSheet("color: #9ec1ff; font-size: 11px;")
        self._update_buttons()

    @property
    def busy(self) -> bool:
        return self._busy

    def set_status(self, text: str, error: bool = False) -> None:
        color = "#e57373" if error else "#83838d"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status_label.setText(text)

    # ----------------------------------------------------------- options
    def min_len(self) -> float:
        return float(self.min_box.value())

    def max_len(self) -> float:
        return float(self.max_box.value())

    def clip_count(self) -> int:
        return int(self.count_box.value())

    def use_ai(self) -> bool:
        return self.ai_box.isChecked()

    def force_retranscribe(self) -> bool:
        return self.force_box.isChecked()

    # ------------------------------------------------------------ helpers
    def _on_media_changed(self, _index: int) -> None:
        data = self.media_box.currentData()
        if data is not None:
            self.mediaChanged.emit(int(data))

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(index, int):
            self.previewRequested.emit(index)

    def _on_preview(self) -> None:
        index = self.current_candidate_index()
        if index is not None:
            self.previewRequested.emit(index)

    def _update_buttons(self) -> None:
        has_media = self.media_box.count() > 0
        self.analyze_button.setEnabled(has_media and not self._busy)
        self.preview_button.setEnabled(bool(self._candidates) and not self._busy)
        self.add_button.setEnabled(bool(self._candidates) and not self._busy)
        self.clear_button.setEnabled(bool(self._candidates) and not self._busy)
