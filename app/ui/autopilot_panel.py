from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.media import MediaItem
from app.utils.timecode import format_clock

STAGE_NAMES = [
    "Find + add clips",
    "Face track (smart crop)",
    "Export clips",
]

STAGE_MARKS = {
    "pending": ("  ", "#4d4d55"),
    "running": ("> ", "#9ac1ff"),
    "done": ("+ ", "#7dcea0"),
    "failed": ("x ", "#e57373"),
    "skipped": ("~ ", "#83838d"),
}


class AutopilotPanel(QWidget):
    """AUTOPILOT page: one click - analyze -> track -> add clips -> export."""

    runRequested = Signal()
    cancelRequested = Signal()
    mediaChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._track_available = True

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header = QLabel("AUTOPILOT")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(header)

        self.media_box = QComboBox()
        self.media_box.setToolTip("Media file to run through the pipeline")
        self.media_box.currentIndexChanged.connect(self._on_media_changed)
        root.addWidget(self.media_box)

        self.url_box = QLineEdit()
        self.url_box.setPlaceholderText(
            "Paste a YouTube link - imports it, then runs the pipeline"
        )
        self.url_box.setClearButtonEnabled(True)
        self.url_box.setToolTip(
            "Optional: when filled, the link is downloaded first and the "
            "pipeline runs on the imported video (overrides the media above)"
        )
        root.addWidget(self.url_box)

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
        self.count_box.setValue(10)
        self.ai_box = QCheckBox("AI rank")
        self.ai_box.setToolTip(
            "Ask the local Ollama model to help pick highlights (same as "
            "the AUTO page)"
        )
        options2.addWidget(self.count_box, 1)
        options2.addWidget(self.ai_box, 1)
        root.addLayout(options2)

        options3 = QHBoxLayout()
        options3.setSpacing(6)
        self.aspect_box = QComboBox()
        aspect_labels = {
            "9:16": "9:16  TikTok",
            "16:9": "16:9  YouTube",
            "1:1": "1:1  Square",
            "4:5": "4:5  Portrait",
        }
        for key in ("9:16", "16:9", "1:1", "4:5"):
            self.aspect_box.addItem(aspect_labels[key], key)
        self.aspect_box.setCurrentIndex(self.aspect_box.findData("9:16"))
        self.aspect_box.setFixedWidth(150)
        self.aspect_box.setToolTip(
            "Aspect ratio for the new clips and their export (preview "
            "follows it)"
        )
        self.captions_box = QCheckBox("Burn captions")
        self.captions_box.setToolTip(
            "Write captions from the transcript for every new clip (shown "
            "on the CAPTIONS page) and burn them into the exports"
        )
        self.captions_box.setChecked(True)
        options3.addWidget(self.aspect_box)
        options3.addWidget(self.captions_box, 1)
        root.addLayout(options3)

        self.track_box = QCheckBox("Track faces before export")
        self.track_box.setToolTip(
            "Run face tracking on the media so the queued exports can "
            "follow the subject (SMART page track)"
        )
        self.track_box.setChecked(True)
        root.addWidget(self.track_box)

        self.export_box = QCheckBox("Export the new clips when done")
        self.export_box.setToolTip(
            "Queue every clip this run creates with the current export "
            "settings (smart crop follows the track option, captions the "
            "option above)"
        )
        self.export_box.setChecked(True)
        root.addWidget(self.export_box)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.run_button = QPushButton("Run autopilot")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.runRequested)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancelRequested)
        actions.addWidget(self.run_button)
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

        self.stage_labels: list[QLabel] = []
        for name in STAGE_NAMES:
            label = QLabel(name)
            label.setWordWrap(True)
            label.setStyleSheet("color: #4d4d55; font-size: 11px;")
            self.stage_labels.append(label)
            root.addWidget(label)

        self.report_label = QLabel("")
        self.report_label.setWordWrap(True)
        self.report_label.setVisible(False)
        self.report_label.setStyleSheet(
            "QLabel { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 8px 12px; color: #c9c9d1; }"
        )
        root.addWidget(self.report_label)

        hint = QLabel(
            "Paste a YouTube link (or pick a media file), then one click "
            "runs the whole pipeline: transcribe (first time) + find "
            "highlights, add them as clips with captions, track faces for "
            "smart crop, then queue the exports. Cancel stops the current "
            "stage (queued exports can be cancelled too)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(hint)
        root.addStretch(1)

        self.reset_stages()
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

    def youtube_url(self) -> str:
        return self.url_box.text().strip()

    def aspect(self) -> str:
        data = self.aspect_box.currentData()
        return str(data) if data else "9:16"

    def burn_captions(self) -> bool:
        return self.captions_box.isChecked()

    def min_len(self) -> float:
        return float(self.min_box.value())

    def max_len(self) -> float:
        return float(self.max_box.value())

    def clip_count(self) -> int:
        return int(self.count_box.value())

    def use_ai(self) -> bool:
        return self.ai_box.isChecked()

    def track_faces(self) -> bool:
        return self.track_box.isChecked() and self.track_box.isEnabled()

    def export_when_done(self) -> bool:
        return self.export_box.isChecked()

    def set_track_available(self, available: bool, message: str = "") -> None:
        self._track_available = available
        if not available:
            self.track_box.setChecked(False)
            self.track_box.setToolTip(
                message or "Face tracking unavailable on this machine"
            )
        else:
            self.track_box.setToolTip(
                "Run face tracking on the media so the queued exports can "
                "follow the subject (SMART page track)"
            )
        self.track_box.setEnabled(available and not self._busy)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.run_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        for widget in (
            self.media_box,
            self.url_box,
            self.min_box,
            self.max_box,
            self.count_box,
            self.ai_box,
            self.aspect_box,
            self.captions_box,
            self.export_box,
        ):
            widget.setEnabled(not busy)
        self.track_box.setEnabled(self._track_available and not busy)
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

    def reset_stages(self) -> None:
        for index, label in enumerate(self.stage_labels):
            self.set_stage(index, "pending")

    def set_stage(self, index: int, state: str, detail: str = "") -> None:
        if not (0 <= index < len(self.stage_labels)):
            return
        mark, color = STAGE_MARKS.get(state, STAGE_MARKS["pending"])
        text = f"{mark}{STAGE_NAMES[index]}"
        if detail:
            text += f"  -  {detail}"
        label = self.stage_labels[index]
        label.setText(text)
        label.setStyleSheet(f"color: {color}; font-size: 11px;")

    def stage_text(self, index: int) -> str:
        if not (0 <= index < len(self.stage_labels)):
            return ""
        return self.stage_labels[index].text()

    def set_report(self, text: str) -> None:
        self.report_label.setText(text)
        self.report_label.setVisible(bool(text))

    # ------------------------------------------------------------ helpers
    def _on_media_changed(self, _index: int) -> None:
        data = self.media_box.currentData()
        if data is not None:
            self.mediaChanged.emit(int(data))

    def _update_buttons(self) -> None:
        has_media = self.media_box.count() > 0
        self.run_button.setEnabled(has_media and not self._busy)
