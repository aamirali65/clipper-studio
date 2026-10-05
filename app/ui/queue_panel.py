from __future__ import annotations

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

from app.models.queue import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_ERROR,
    JOB_QUEUED,
    JOB_RUNNING,
    ExportJob,
)

STATUS_COLORS = {
    JOB_QUEUED: "#83838d",
    JOB_RUNNING: "#9ec1ff",
    JOB_DONE: "#7fd18a",
    JOB_ERROR: "#e57373",
    JOB_CANCELLED: "#5c5c66",
}

STATUS_LABELS = {
    JOB_QUEUED: "Queued",
    JOB_RUNNING: "Running",
    JOB_DONE: "Done",
    JOB_ERROR: "Failed",
    JOB_CANCELLED: "Cancelled",
}


class QueuePanel(QWidget):
    """QUEUE page: background export queue with per-job progress."""

    cancelRequested = Signal(int)
    cancelAllRequested = Signal()
    clearRequested = Signal()
    openFolderRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 10, 12)
        root.setSpacing(10)

        header_row = QHBoxLayout()
        header = QLabel("EXPORT QUEUE")
        header.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        header_row.addWidget(header)
        header_row.addStretch(1)
        root.addLayout(header_row)

        self.summary = QFrame()
        self.summary.setStyleSheet(
            "QFrame { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; }"
        )
        summary_layout = QVBoxLayout(self.summary)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        self.summary_label = QLabel("Queue is empty.")
        self.summary_label.setStyleSheet("color: #83838d; font-size: 11px;")
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)
        root.addWidget(self.summary)

        self.listw = QListWidget()
        self.listw.setStyleSheet(
            "QListWidget { background-color: #101014; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 4px; }"
        )
        root.addWidget(self.listw, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(8)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.cancel_selected = QPushButton("Cancel selected")
        self.cancel_selected.setEnabled(False)
        self.cancel_selected.clicked.connect(self._cancel_selected)
        self.cancel_all = QPushButton("Cancel all")
        self.cancel_all.clicked.connect(self.cancelAllRequested)
        row1.addWidget(self.cancel_selected)
        row1.addWidget(self.cancel_all)
        buttons.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.clear_button = QPushButton("Clear finished")
        self.clear_button.clicked.connect(self.clearRequested)
        self.open_button = QPushButton("Open output folder")
        self.open_button.clicked.connect(self.openFolderRequested)
        row2.addWidget(self.clear_button)
        row2.addWidget(self.open_button)
        buttons.addLayout(row2)
        root.addLayout(buttons)

        hint = QLabel(
            "Jobs run one at a time in the background while you keep editing. "
            "Each job exports its own clip range and aspect."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4d4d55; font-size: 10px;")
        root.addWidget(hint)

        self._jobs: list[ExportJob] = []
        self.listw.currentItemChanged.connect(self._on_selection)

    # ----------------------------------------------------------- refresh
    def refresh(self, jobs: list[ExportJob]) -> None:
        self._jobs = list(jobs)
        selected_id = self.selected_job_id()

        self.listw.blockSignals(True)
        self.listw.clear()
        tallies = {key: 0 for key in STATUS_LABELS}
        running = None
        for job in self._jobs:
            tallies[job.status] = tallies.get(job.status, 0) + 1
            if job.status == JOB_RUNNING:
                running = job
            item = QListWidgetItem(self._text_for(job))
            item.setData(Qt.ItemDataRole.UserRole, job.id)
            item.setForeground(
                self._brush(STATUS_COLORS.get(job.status, "#83838d"))
            )
            self.listw.addItem(item)
        if selected_id is not None:
            for row in range(self.listw.count()):
                if self.listw.item(row).data(Qt.ItemDataRole.UserRole) == selected_id:
                    self.listw.setCurrentRow(row)
                    break
        self.listw.blockSignals(False)

        parts = []
        if tallies.get(JOB_RUNNING):
            parts.append(f"{tallies[JOB_RUNNING]} running")
        if tallies.get(JOB_QUEUED):
            parts.append(f"{tallies[JOB_QUEUED]} queued")
        if tallies.get(JOB_DONE):
            parts.append(f"{tallies[JOB_DONE]} done")
        if tallies.get(JOB_ERROR):
            parts.append(f"{tallies[JOB_ERROR]} failed")
        if tallies.get(JOB_CANCELLED):
            parts.append(f"{tallies[JOB_CANCELLED]} cancelled")
        if running is not None:
            pct = int(running.progress * 100)
            self.summary_label.setText(
                f"Exporting <b style='color:#e4e4e8'>{running.clip_name}</b>"
                f"  -  {pct}%  -  {running.detail}<br>"
                f"{'  ·  '.join(parts)}"
            )
        elif parts:
            self.summary_label.setText("  ·  ".join(parts))
        else:
            self.summary_label.setText(
                "Queue is empty. Use <b>Add to queue</b> on the EXPORT page "
                "or export all clips (Ctrl+Shift+E)."
            )
        self.cancel_selected.setEnabled(
            self.selected_job_status() in {JOB_QUEUED, JOB_RUNNING}
        )

    def _text_for(self, job: ExportJob) -> str:
        status = STATUS_LABELS.get(job.status, job.status)
        if job.status == JOB_RUNNING:
            detail = f"{int(job.progress * 100)}%  {job.detail}"
        elif job.status == JOB_DONE:
            detail = "exported"
        elif job.status == JOB_ERROR:
            detail = job.error or "failed"
        elif job.status == JOB_CANCELLED:
            detail = "cancelled"
        else:
            detail = "waiting"
        return f"[{status}]  {job.clip_name}\n        {job.aspect}  ·  {detail}"

    @staticmethod
    def _brush(color: str):
        from PySide6.QtGui import QBrush, QColor

        return QBrush(QColor(color))

    # ---------------------------------------------------------- selection
    def selected_job_id(self) -> int | None:
        item = self.listw.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def selected_job_status(self) -> str | None:
        job_id = self.selected_job_id()
        if job_id is None:
            return None
        for job in self._jobs:
            if job.id == job_id:
                return job.status
        return None

    def _on_selection(self) -> None:
        self.cancel_selected.setEnabled(
            self.selected_job_status() in {JOB_QUEUED, JOB_RUNNING}
        )

    def _cancel_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is not None:
            self.cancelRequested.emit(job_id)
