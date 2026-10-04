from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app.models.clip import Clip, resolution_for
from app.models.media import MediaItem, MediaKind, MediaProbeResult
from app.models.project import Project, RecentProject, utc_now
from app.services.ffmpeg_service import FFmpegNotAvailable, FFmpegService
from app.services.project_service import ProjectError, ProjectService
from app.services.youtube_service import InvalidYouTubeURL, YouTubeError, YouTubeService
from app.services.video_service import VideoService
from app.ui.dashboard import ProjectDashboard
from app.ui.export_dialog import ExportDialog
from app.ui.inspector import Inspector
from app.ui.media_panel import MediaPanel
from app.ui.project_dialog import NewProjectDialog
from app.ui.project_panel import EditorPanel, ExportPanel, ProjectPanel
from app.ui.sidebar import Sidebar
from app.ui.status_bar import StatusBar
from app.ui.timeline import Timeline
from app.ui.video_player import VideoPlayer
from app.utils.logging import get_logger
from app.utils.paths import format_bytes, open_in_explorer, projects_dir, safe_name
from app.utils.timecode import format_clock, format_timecode
from app.workers.download_worker import DownloadWorker, MetadataWorker
from app.workers.probe_worker import ProbeWorker

log = get_logger("main_window")

PAGE_MEDIA = 0
PAGE_PROJECT = 1
PAGE_EDITOR = 2
PAGE_EXPORT = 3

SEEK_STEP_SECONDS = 5.0
MIN_CLIP_SECONDS = 0.05


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        self.project_service = ProjectService()
        self.ffmpeg = FFmpegService()
        self.video_service = VideoService(self.ffmpeg)
        self.youtube_service = YouTubeService(self.video_service, self.ffmpeg)

        self.project: Project | None = None
        self.current_media: MediaItem | None = None
        self.current_clip: Clip | None = None
        self.duration = 0.0

        self._bg_workers: list[QThread] = []
        self._download_worker: DownloadWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._export_dialog: ExportDialog | None = None
        self._pending_probe_count = 0
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []

        self._build_menu()
        self._build_central()
        self._connect_signals()
        self._build_shortcuts()

        self._refresh_ffmpeg_status()
        self.show_dashboard()

    # ------------------------------------------------------------------ UI
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        self.act_new = QAction("New Project", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_open = QAction("Open Project…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_save = QAction("Save", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save_as = QAction("Save As…", self)
        self.act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.act_import = QAction("Import Video…", self)
        self.act_import.setShortcut("Ctrl+I")
        self.act_quit = QAction("Quit", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_export = QAction("Export Clip…", self)
        self.act_export.setShortcut("Ctrl+E")
        file_menu.addSeparator()
        file_menu.addAction(self.act_import)
        file_menu.addAction(self.act_export)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        help_menu = self.menuBar().addMenu("&Help")
        act_about = QAction(f"About {APP_NAME}", self)
        act_about = QAction(f"About {APP_NAME}", self)
        act_about.triggered.connect(self._about)
        help_menu.addAction(act_about)

    def _about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> 0.1.0 (Phase 2)<br><br>"
            "Desktop video clipping studio with professional timeline editor.<br><br>"
            "Features: multi-clip timeline, split/duplicate/trim, undo/redo, "
            "FFmpeg export with progress tracking.",
        )
        act_ffmpeg = QAction("FFmpeg status", self)
        act_ffmpeg.triggered.connect(self._show_ffmpeg_status)
        help_menu.addAction(act_ffmpeg)

        self.act_new.triggered.connect(self.new_project)
        self.act_open.triggered.connect(self.open_project_dialog)
        self.act_save.triggered.connect(self.save_project)
        self.act_save_as.triggered.connect(self.save_project_as)
        self.act_import.triggered.connect(self.import_local_video)
        self.act_export.triggered.connect(self.open_export_dialog)
