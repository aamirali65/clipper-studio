from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from app.models.clip import QUALITY_PRESETS, Clip, ExportSettings, resolution_for
from app.models.media import MediaItem
from app.models.project import Project
from app.services.export_service import ExportRequest, ExportService
from app.utils.paths import open_in_explorer
from app.utils.timecode import format_timecode
from app.workers.export_worker import ExportWorker


class ExportDialog(QDialog):
    """Options + live progress for a real FFmpeg export."""

    def __init__(
        self,
        project: Project,
        clip: Clip,
        media: MediaItem,
        parent=None,
        output_dir: str = "",
        captions_srt: Path | None = None,
        burn_captions: bool = False,
        smart_track: list | None = None,
        smart_crop_default: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Export Clip")
        self.setMinimumWidth(560)
        self._project = project
        self._clip = clip
        self._media = media
        self._output_dir = output_dir.strip()
        self._captions_srt = Path(captions_srt) if captions_srt else None
        self._burn_default = burn_captions and self._captions_srt is not None
        self._smart_track = list(smart_track) if smart_track else None
        self._worker: ExportWorker | None = None
        self._output: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("EXPORT CLIP")
        title.setStyleSheet(
            "color: #9a9aa4; font-size: 11px; font-weight: 700; letter-spacing: 2px;"
        )
        root.addWidget(title)

        summary = QLabel(
            f"<b>{media.name}</b><br>"
            f"{format_timecode(clip.start)} → {format_timecode(clip.end)}"
            f"  ·  {clip.duration:.2f}s  ·  aspect {clip.aspect}"
        )
        summary.setStyleSheet(
            "QLabel { background-color: #16161b; border: 1px solid #2a2a32;"
            " border-radius: 8px; padding: 10px 12px; color: #d4d4d8; }"
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(summary)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)

        self.format_box = QComboBox()
        self.format_box.addItem("MP4", "mp4")
        self.format_box.setEnabled(False)

        self.video_box = QComboBox()
        self.video_box.addItem("H.264 (libx264)", "libx264")
        self.video_box.setEnabled(False)

        self.audio_box = QComboBox()
        self.audio_box.addItem("AAC", "aac")
        self.audio_box.setEnabled(False)

        self.aspect_box = QComboBox()
        for key in ("16:9", "9:16", "1:1", "4:5"):
            self.aspect_box.addItem(f"{key}  {resolution_for(key)[0]}x{resolution_for(key)[1]}", key)
        index = self.aspect_box.findData(clip.aspect)
        self.aspect_box.setCurrentIndex(index if index >= 0 else 1)
        self.aspect_box.currentIndexChanged.connect(self._refresh_resolution)

        self.preset_box = QComboBox()
        for key, info in QUALITY_PRESETS.items():
            self.preset_box.addItem(info["label"], key)
        preset_index = self.preset_box.findData(
            project.export_settings.preset or "balanced"
        )
        self.preset_box.setCurrentIndex(preset_index if preset_index >= 0 else 1)

        rows = [
            ("Format", self.format_box),
            ("Video codec", self.video_box),
            ("Audio", self.audio_box),
            ("Aspect / resolution", self.aspect_box),
            ("Quality preset", self.preset_box),
        ]
        for row, (label, widget) in enumerate(rows):
            caption = QLabel(label)
            caption.setStyleSheet("color: #83838d;")
            grid.addWidget(caption, row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        self.resolution_label = QLabel("")
        self.resolution_label.setStyleSheet("color: #63636e; font-size: 11px;")
        root.addWidget(self.resolution_label)

        self.burn_box = QCheckBox("Burn captions into the video")
        if self._captions_srt is None:
            self.burn_box.setEnabled(False)
            self.burn_box.setToolTip("No captions yet - transcribe on the CAPTIONS page")
        else:
            self.burn_box.setToolTip(str(self._captions_srt))
        self.burn_box.setChecked(self._burn_default)
        root.addWidget(self.burn_box)

        self.smart_box = QCheckBox("Smart crop (follow tracked subject)")
        if self._smart_track:
            self.smart_box.setEnabled(True)
            self.smart_box.setChecked(bool(smart_crop_default))
            self.smart_box.setToolTip(
                "Uses the face track analyzed on the SMART page"
            )
        else:
            self.smart_box.setEnabled(False)
            self.smart_box.setToolTip(
                "No face track for this media - run Track faces on the "
                "SMART page first"
            )
        root.addWidget(self.smart_box)

        path_row = QHBoxLayout()
        path_caption = QLabel("Output")
        path_caption.setStyleSheet("color: #83838d;")
        self.path_edit = QLineEdit()
        self.path_edit.setText(str(self._default_output()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(path_caption)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        root.addLayout(path_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #83838d; font-size: 11px;")
        self.status.setVisible(False)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.open_button = QPushButton("Open Folder")
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(self._open_folder)
        self.cancel_button = QPushButton("Close")
        self.cancel_button.clicked.connect(self.reject)
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("PrimaryButton")
        self.export_button.clicked.connect(self._start)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.export_button)
        root.addLayout(buttons)

        self._refresh_resolution()

    # ---- helpers ----
    def _default_output(self) -> Path:
        media = self._project.media_by_id(self._clip.media_id)
        base = self._project.name or "project"
        clip_name = self._clip.name or "clip"
        if media:
            clip_name = f"{Path(media.name).stem} - {clip_name}"
        directory = (
            Path(self._output_dir)
            if self._output_dir
            else self._project.exports_dir
        )
        return directory / f"{base} - {clip_name}.mp4"

    def _refresh_resolution(self) -> None:
        aspect = self.aspect_box.currentData() or "9:16"
        width, height = resolution_for(aspect)
        self.resolution_label.setText(f"Output resolution: {width}x{height}  ({aspect})")

    def _browse(self) -> None:
        initial = Path(self.path_edit.text()).parent
        initial = initial if initial.exists() else self._project.exports_dir
        path, _ = QFileDialog.getSaveFileName(
            self, "Export clip", str(initial), "MP4 video (*.mp4)"
        )
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.path_edit.setText(path)

    def _build_request(self) -> ExportRequest:
        settings = ExportSettings(
            format="MP4",
            video_codec="H.264",
            audio_codec="AAC",
            preset=self.preset_box.currentData() or "balanced",
            crf=QUALITY_PRESETS[self.preset_box.currentData() or "balanced"]["crf"],
        )
        aspect = self.aspect_box.currentData() or "9:16"
        subtitles = (
            self._captions_srt
            if self.burn_box.isChecked() and self._captions_srt is not None
            else None
        )
        smart = (
            self._smart_track
            if self.smart_box.isChecked() and self._smart_track
            else None
        )
        source_size = None
        if smart and self._media.width and self._media.height:
            source_size = (self._media.width, self._media.height)
        return ExportRequest(
            source=Path(self._media.source_path),
            output=Path(self.path_edit.text().strip()),
            start=self._clip.start,
            end=self._clip.end,
            aspect=aspect,
            settings=settings,
            subtitles=subtitles,
            smart_track=smart,
            source_size=source_size,
        )

    # ---- flow ----
    def _start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        request = self._build_request()
        if not Path(request.source).exists():
            QMessageBox.warning(
                self, "Export", f"Source file not found:\n{request.source}"
            )
            return
        if request.duration <= 0:
            QMessageBox.warning(self, "Export", "Clip duration must be greater than zero.")
            return
        if request.output.exists():
            answer = QMessageBox.question(
                self,
                "Export",
                f"File already exists:\n{request.output}\n\nOverwrite?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._project.export_settings.preset = request.settings.preset
        self._project.export_settings.crf = request.settings.crf
        self._project.editor_settings.aspect = request.aspect

        self.export_button.setEnabled(False)
        self.cancel_button.setText("Cancel")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status.setVisible(True)
        self.status.setStyleSheet("color: #83838d; font-size: 11px;")
        self.status.setText("Starting export…")
        self.open_button.setVisible(False)

        service = ExportService()
        self._worker = ExportWorker(request, service)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_completed)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, percent: float, detail: str) -> None:
        self.progress.setValue(int(percent))
        self.status.setText(detail)

    def _on_completed(self, path: str) -> None:
        self._output = Path(path)
        self.progress.setValue(100)
        self.status.setStyleSheet("color: #7fd18a; font-size: 11px;")
        self.status.setText(f"Export complete\n{path}")
        self.open_button.setVisible(True)
        self.cancel_button.setText("Close")

    def _on_error(self, message: str) -> None:
        self.status.setStyleSheet("color: #e57373; font-size: 11px;")
        self.status.setText(message)
        QMessageBox.critical(self, "Export failed", message)

    def _on_finished(self) -> None:
        self.export_button.setEnabled(True)

    def _open_folder(self) -> None:
        if self._output is None:
            return
        try:
            open_in_explorer(self._output)
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output.parent)))

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            answer = QMessageBox.question(
                self, "Cancel export", "An export is running. Cancel it?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._worker.cancel()
            self._worker.wait(4000)
        super().reject()

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(4000)
        super().closeEvent(event)
