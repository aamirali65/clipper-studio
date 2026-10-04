from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app.database.database import ProjectDatabase
from app.database.repositories import ClipRepository, MediaRepository
from app.models.clip import ASPECT_RATIOS, Clip
from app.models.media import MediaItem, MediaKind, MediaProbeResult
from app.models.project import Project, utc_now
from app.services.export_service import ExportService
from app.services.ffmpeg_service import FFmpegService
from app.services.project_service import ProjectError, ProjectService
from app.services.youtube_service import InvalidYouTubeURL, YouTubeService
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
MAX_UNDO = 200

PAGE_INDEX = {
    "media": PAGE_MEDIA,
    "project": PAGE_PROJECT,
    "editor": PAGE_EDITOR,
    "export": PAGE_EXPORT,
}


class MultiExportWorker(QThread):
    """Exports every clip in a project off the UI thread."""

    progress = Signal(float, str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, project: Project, clips: list[Clip], parent=None):
        super().__init__(parent)
        self._project = project
        self._clips = list(clips)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            service = ExportService()
            paths = service.export_multi_clips(
                self._project,
                self._clips,
                progress_cb=lambda p, d: self.progress.emit(float(p), d),
                cancel_event=self._cancel,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("export-all failed")
            self.error.emit(str(exc))
        else:
            self.completed.emit([str(p) for p in paths])


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
        self._metadata_worker: MetadataWorker | None = None
        self._export_multi_worker: MultiExportWorker | None = None
        self._export_dialog: ExportDialog | None = None
        self._pending_probe_count = 0
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._trim_snapshot: dict | None = None
        self._move_snapshot: dict | None = None
        self._committed_range: tuple[float, float] | None = None

        self._build_menu()
        self._build_central()
        self._connect_signals()
        self._build_shortcuts()

        self.show_dashboard()
        self._refresh_ffmpeg_status()

    # ------------------------------------------------------------------ UI
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        self.act_new = QAction("New Project", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_open = QAction("Open Project...", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_save = QAction("Save", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save_as = QAction("Save As...", self)
        self.act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.act_import = QAction("Import Video...", self)
        self.act_import.setShortcut("Ctrl+I")
        self.act_export = QAction("Export Clip...", self)
        self.act_export.setShortcut("Ctrl+E")
        self.act_export_all = QAction("Export All Clips...", self)
        self.act_export_all.setShortcut("Ctrl+Shift+E")
        self.act_quit = QAction("Quit", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)

        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_open)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save)
        file_menu.addAction(self.act_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self.act_import)
        file_menu.addAction(self.act_export)
        file_menu.addAction(self.act_export_all)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        help_menu = self.menuBar().addMenu("&Help")
        self.act_about = QAction(f"About {APP_NAME}", self)
        self.act_ffmpeg = QAction("FFmpeg Status", self)
        help_menu.addAction(self.act_about)
        help_menu.addAction(self.act_ffmpeg)

        self.act_new.triggered.connect(self.new_project)
        self.act_open.triggered.connect(self.open_project_dialog)
        self.act_save.triggered.connect(self.save_project)
        self.act_save_as.triggered.connect(self.save_project_as)
        self.act_import.triggered.connect(self.import_local_video)
        self.act_export.triggered.connect(self.open_export_dialog)
        self.act_export_all.triggered.connect(self._export_all_clips)
        self.act_quit.triggered.connect(self.close)
        self.act_about.triggered.connect(self._about)
        self.act_ffmpeg.triggered.connect(self._show_ffmpeg_status)

    def _build_central(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.dashboard = ProjectDashboard()
        self.sidebar = Sidebar()
        self.media_panel = MediaPanel()
        self.project_panel = ProjectPanel()
        self.editor_panel = EditorPanel()
        self.export_panel = ExportPanel()
        self.player = VideoPlayer()
        self.timeline = Timeline()
        self.inspector = Inspector()
        self.status_bar = StatusBar()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.dashboard)  # index 0

        editor = QWidget()
        editor_layout = QHBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_layout.addWidget(self.sidebar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 8)
        body_layout.setSpacing(8)

        columns = QHBoxLayout()
        columns.setSpacing(10)

        self.pages = QStackedWidget()
        self.pages.setMinimumWidth(296)
        self.pages.setMaximumWidth(360)
        self.pages.addWidget(self.media_panel)    # PAGE_MEDIA
        self.pages.addWidget(self.project_panel)  # PAGE_PROJECT
        self.pages.addWidget(self.editor_panel)   # PAGE_EDITOR
        self.pages.addWidget(self.export_panel)   # PAGE_EXPORT
        columns.addWidget(self.pages)

        center = QVBoxLayout()
        center.setSpacing(8)
        center.addWidget(self.player, 1)
        self.timeline.setFixedHeight(176)
        center.addWidget(self.timeline)
        columns.addLayout(center, 1)

        columns.addWidget(self.inspector)

        body_layout.addLayout(columns, 1)
        editor_layout.addWidget(body, 1)
        self.stack.addWidget(editor)  # index 1

        root.addWidget(self.stack, 1)
        root.addWidget(self.status_bar)
        self.setCentralWidget(central)

    def _connect_signals(self) -> None:
        self.dashboard.createRequested.connect(self.new_project)
        self.dashboard.openRequested.connect(self.open_project_dialog)
        self.dashboard.recentSelected.connect(self.open_project_path)

        self.sidebar.pageChanged.connect(self._on_page_changed)

        self.media_panel.importLocalRequested.connect(self.import_local_video)
        self.media_panel.downloadRequested.connect(self.download_youtube)
        self.media_panel.cancelDownloadRequested.connect(self.cancel_download)
        self.media_panel.metadataRequested.connect(self._check_youtube_metadata)
        self.media_panel.mediaSelected.connect(self._on_media_selected)
        self.media_panel.mediaActivated.connect(self._on_media_activated)
        self.media_panel.removeMediaRequested.connect(self._remove_media)

        self.player.positionChanged.connect(self._on_player_position)
        self.player.durationChanged.connect(self._on_player_duration)
        self.player.stateChanged.connect(self.status_bar.set_playing)
        self.player.mediaError.connect(self._on_player_error)

        self.timeline.seekRequested.connect(self._on_timeline_seek)
        self.timeline.rangeEdited.connect(self._on_timeline_range_edited)
        self.timeline.rangeEditFinished.connect(self._on_timeline_range_finished)
        self.timeline.clipSelectedChanged.connect(self._on_timeline_clip_selected)
        self.timeline.clipMoved.connect(self._on_clip_moved)
        self.timeline.clipSplit.connect(self._on_timeline_split_signal)
        self.timeline.clipDuplicate.connect(self._on_timeline_duplicate_signal)

        self.inspector.rangeApplied.connect(self._on_inspector_range)
        self.inspector.aspectChanged.connect(self._on_aspect_changed)
        self.inspector.removeRequested.connect(self._remove_selected_clip)
        self.inspector.seekRequested.connect(self._seek_seconds)

        self.status_bar.exportRequested.connect(self.open_export_dialog)
        self.status_bar.aspectChanged.connect(self._on_aspect_changed)
        self.status_bar.playToggled.connect(self.player.toggle_play)

        self.project_panel.saveRequested.connect(self.save_project)
        self.project_panel.saveAsRequested.connect(self.save_project_as)

        self.editor_panel.clipSelected.connect(self._on_clip_selected)
        self.editor_panel.addClipRequested.connect(self.add_clip_from_range)
        self.editor_panel.removeClipRequested.connect(self._remove_clip_by_id)

        self.export_panel.exportRequested.connect(self.open_export_dialog)

    def _build_shortcuts(self) -> None:
        def add(key: str, slot, name: str) -> None:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)
            setattr(self, name, shortcut)

        add("Space", self._shortcut_play_pause, "sc_space")
        add("Left", lambda: self._shortcut_seek(-SEEK_STEP_SECONDS), "sc_left")
        add("Right", lambda: self._shortcut_seek(SEEK_STEP_SECONDS), "sc_right")
        add("Shift+Left", lambda: self._shortcut_seek(-1.0), "sc_shift_left")
        add("Shift+Right", lambda: self._shortcut_seek(1.0), "sc_shift_right")
        add("I", self._shortcut_set_in, "sc_in")
        add("O", self._shortcut_set_out, "sc_out")
        add("Delete", self._shortcut_delete, "sc_delete")
        add("S", self._shortcut_split, "sc_split")
        add("Ctrl+D", self._shortcut_duplicate, "sc_duplicate")
        add("Ctrl+Z", self._undo, "sc_undo")
        add("Ctrl+Shift+Z", self._redo, "sc_redo")

    # -------------------------------------------------------------- pages
    def show_dashboard(self) -> None:
        self.stack.setCurrentWidget(self.dashboard)
        self.dashboard.set_projects(self.project_service.recent_projects())
        self.status_bar.set_export_enabled(False)
        self.status_bar.set_times(0.0, 0.0)
        self.status_bar.show_message("Ready")

    def _on_page_changed(self, key: str) -> None:
        index = PAGE_INDEX.get(key)
        if index is None:
            return
        self.pages.setCurrentIndex(index)
        if self.project is None:
            return
        if key == "project":
            self.project_panel.refresh(self.project)
        elif key == "editor":
            self._refresh_clip_list()
        elif key == "export":
            self.export_panel.refresh(
                self.current_clip, self.project, self.status_bar.aspect
            )
        elif key == "media":
            if len(self.media_panel.items()) != len(self.project.media):
                self.media_panel.set_media(self.project.media)

    # ------------------------------------------------------------ project
    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        dialog = NewProjectDialog(self.project_service, self)
        if dialog.exec():
            project = dialog.result_project
            if project is not None:
                self._load_project(project, "Project created")

    def open_project_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            str(projects_dir()),
            "Clipper project (*.clipper)",
        )
        if path:
            self._open_project(path)

    def open_project_path(self, path: str) -> None:
        if not self._confirm_discard():
            return
        self._open_project(path)

    def _open_project(self, path: str) -> None:
        try:
            project = self.project_service.open_project(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Open Project", str(exc))
            return
        self._load_project(project, f"Opened {project.name}")

    def save_project(self) -> bool:
        if not self._require_project():
            return False
        self._sync_settings()
        try:
            self.project_service.save_project(self.project)
        except ProjectError as exc:
            QMessageBox.warning(self, "Save Project", str(exc))
            return False
        self.dashboard.set_projects(self.project_service.recent_projects())
        self._push_status(f"Saved {self.project.name}")
        return True

    def save_project_as(self) -> None:
        if not self._require_project():
            return
        self._sync_settings()
        default = str(self.project.directory / f"{self.project.name}.clipper")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", default, "Clipper project (*.clipper)"
        )
        if not path:
            return
        try:
            self.project = self.project_service.save_project_as(self.project, path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Save Project As", str(exc))
            return
        self.project_panel.refresh(self.project)
        self.dashboard.set_projects(self.project_service.recent_projects())
        self._push_status(f"Saved as {self.project.name}")

    def _confirm_discard(self) -> bool:
        if self.project is None:
            return True
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Save changes to the current project before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def _sync_settings(self) -> None:
        if self.project is None:
            return
        settings = self.project.editor_settings
        settings.aspect = self.status_bar.aspect
        settings.volume = self.player.volume_value / 100.0
        settings.muted = self.player.audio.isMuted()
        settings.timeline_px_per_second = self.timeline.pps
        settings.last_media_id = (
            self.current_media.id if self.current_media is not None else None
        )

    def _load_project(self, project: Project, message: str = "") -> None:
        self.project = project
        project.ensure_dirs()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._clear_edit_snapshots()
        self._committed_range = None

        settings = project.editor_settings
        aspect = settings.aspect if settings.aspect in ASPECT_RATIOS else "9:16"
        self.status_bar.set_aspect(aspect)
        self.inspector.set_aspect_value(aspect)
        self.player.set_aspect(aspect)
        if 0 < settings.volume <= 1.0:
            self.player.set_volume_value(int(round(settings.volume * 100)))
        self.player.set_muted(settings.muted)

        self.media_panel.set_media(project.media)
        self.media_panel.set_metadata_preview("")
        self.project_panel.refresh(project)
        self._refresh_clip_list()

        self.stack.setCurrentIndex(1)  # editor page (dashboard is index 0)
        self.sidebar.select("media")

        target: MediaItem | None = None
        if project.media:
            if settings.last_media_id:
                target = project.media_by_id(settings.last_media_id)
            if target is None:
                target = project.media[0]
        if target is not None:
            self._activate_media(target)
        else:
            self.current_media = None
            self.current_clip = None
            self.duration = 0.0
            self.player.clear()
            self.timeline.clear()
            self.inspector.set_clip(None, None)
            self.status_bar.set_export_enabled(False)
            self.status_bar.set_times(0.0, 0.0)
            self.export_panel.refresh(None, project, aspect)

        self.project_panel.refresh(project)
        self.dashboard.set_projects(self.project_service.recent_projects())
        self._push_status(message or f"Opened {project.name}")
        log.info("project loaded into window: %s", project.name)

    # --------------------------------------------------------------- media
    def import_local_video(self) -> None:
        if not self._require_project():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Video",
            str(Path.home()),
            "Video files (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.flv *.ts *.mpg *.mpeg);;All files (*.*)",
        )
        for path in files:
            self._start_probe(Path(path))

    def _start_probe(self, path: Path) -> None:
        if self.project is None:
            return
        worker = ProbeWorker(path, service=VideoService(self.ffmpeg))
        worker.completed.connect(self._on_probe_completed)
        worker.error.connect(self._on_probe_error)
        self._pending_probe_count += 1
        self._track_worker(worker)
        self._push_status(f"Reading {path.name}...")
        worker.start()

    def _on_probe_completed(self, info: MediaProbeResult) -> None:
        self._pending_probe_count = max(0, self._pending_probe_count - 1)
        if self.project is None:
            return
        path = Path(info.path)
        resolved = str(path.resolve()) if path.exists() else str(path)

        existing = next(
            (
                item
                for item in self.project.media
                if item.id is not None and item.source_path == resolved
            ),
            None,
        )
        if existing is not None:
            self._activate_media(existing)
            self._push_status(f"{existing.name} is already in this project")
            return

        item = MediaItem(
            name=safe_name(path.stem),
            kind=MediaKind.local,
            source_path=resolved,
            duration=info.duration,
            width=info.width,
            height=info.height,
            size_bytes=info.size_bytes,
            thumbnail=info.thumbnail,
            created_at=utc_now(),
        )
        database = ProjectDatabase(self.project.path)
        try:
            MediaRepository(database).add(item, self.project.directory)
        finally:
            database.close()

        self.project.media.append(item)
        self.media_panel.add_media(item)
        self.project_panel.refresh(self.project)
        self._activate_media(item)
        self._push_status(
            f"Imported {item.name}  -  {format_clock(item.duration)}  -  "
            f"{format_bytes(item.size_bytes)}"
        )

    def _on_probe_error(self, message: str) -> None:
        self._pending_probe_count = max(0, self._pending_probe_count - 1)
        self._push_status(f"Import failed: {message}")
        QMessageBox.warning(self, "Import Video", message)

    def _on_media_selected(self, item: MediaItem | None) -> None:
        if item is None:
            return
        self._push_status(f"{item.name}  -  {format_clock(item.duration)}")

    def _on_media_activated(self, item: MediaItem | None) -> None:
        if item is None:
            return
        self._activate_media(item)

    def _activate_media(self, item: MediaItem, clip: Clip | None = None) -> None:
        if self.project is None:
            return
        self.current_media = item
        self.project.editor_settings.last_media_id = item.id

        path = item.resolve_path(self.project.directory)
        if not path.exists():
            path = Path(item.source_path)
        if not path.exists():
            self._push_status(f"Media file not found: {item.source_path}")
            QMessageBox.warning(
                self,
                "Missing Media",
                f"The video file could not be found:\n{item.source_path}",
            )
            return

        self.duration = item.duration
        self.timeline.set_media(self.duration, item.name)

        if clip is None:
            clip = next(
                (c for c in self.project.clips if c.media_id == item.id), None
            )
        if clip is None:
            clip = self._new_clip_for_media(item)
        self.current_clip = clip

        self.player.load(path)
        self.player.wait_until_loaded()
        self._refresh_timeline()
        self._refresh_clip_list()
        self.media_panel.select_media(item.id)
        self.inspector.set_clip(clip, item)
        self.status_bar.set_export_enabled(True)
        self.player.set_aspect(self.status_bar.aspect)
        self.status_bar.set_times(0.0, self.duration)
        self.export_panel.refresh(clip, self.project, self.status_bar.aspect)
        self.project_panel.refresh(self.project)
        self._committed_range = (clip.start, clip.end)
        self._push_status(f"Loaded {item.name}  -  {format_clock(self.duration)}")

    def _new_clip_for_media(self, item: MediaItem) -> Clip:
        duration = item.duration or 0.0
        clip = Clip(
            media_id=item.id or 0,
            name=f"Clip {len(self.project.clips) + 1}",
            start=0.0,
            end=duration,
            aspect=self.status_bar.aspect,
            timeline_start=0.0,
            timeline_end=duration,
            order=len(self.project.clips),
            created_at=utc_now(),
        )
        self._persist_clip_new(clip)
        self.project.clips.append(clip)
        return clip

    def _remove_media(self, item: MediaItem | None) -> None:
        if self.project is None or item is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove Media",
            f"Remove {item.name} from this project?\n\nIts clips will be removed too.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        for clip in [c for c in self.project.clips if c.media_id == item.id]:
            self.project.clips.remove(clip)
            if clip.id is not None:
                self._persist_clip_delete(clip.id)
        if item.id is not None:
            database = ProjectDatabase(self.project.path)
            try:
                MediaRepository(database).delete(item.id)
            finally:
                database.close()
        self.project.media = [m for m in self.project.media if m.id != item.id]
        self.media_panel.remove_media(item)

        was_current = self.current_media is not None and self.current_media.id == item.id
        if was_current:
            self.current_media = None
            self.current_clip = None
            self.duration = 0.0
            self.player.clear()
            self.timeline.clear()
            self.inspector.set_clip(None, None)
            self.status_bar.set_export_enabled(False)
            if self.project.media:
                self._activate_media(self.project.media[0])

        # media removal is confirmed up front; drop stale history
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._clear_edit_snapshots()
        self._refresh_timeline()
        self._refresh_clip_list()
        self.project_panel.refresh(self.project)
        self._push_status(f"Removed {item.name}")

    # ------------------------------------------------------------- youtube
    def download_youtube(self, url: str) -> None:
        if not self._require_project():
            return
        url = (url or "").strip()
        if not url:
            self.media_panel.set_download_error("Paste a YouTube URL first.")
            return
        try:
            url = YouTubeService.validate(url)
        except InvalidYouTubeURL as exc:
            self.media_panel.set_download_error(str(exc))
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            self._push_status("A download is already running")
            return

        self.media_panel.set_downloading(True)
        worker = DownloadWorker(
            url, self.project.media_dir, service=self.youtube_service
        )
        worker.progress.connect(self._on_download_progress)
        worker.completed.connect(self._on_download_completed)
        worker.error.connect(self._on_download_error)
        self._download_worker = worker
        self._track_worker(worker)
        worker.start()
        self._push_status("Downloading from YouTube...")

    def _on_download_progress(self, percent: float, detail: str) -> None:
        self.media_panel.set_download_progress(percent, detail)

    def _on_download_completed(self, item: MediaItem) -> None:
        self._download_worker = None
        if self.project is None:
            self.media_panel.set_download_info("Download finished")
            return

        resolved = str(Path(item.source_path))
        existing = next(
            (
                m
                for m in self.project.media
                if m.id is not None and m.source_path == resolved
            ),
            None,
        )
        if existing is not None:
            target = existing
        else:
            database = ProjectDatabase(self.project.path)
            try:
                MediaRepository(database).add(item, self.project.directory)
            finally:
                database.close()
            self.project.media.append(item)
            self.media_panel.add_media(item)
            target = item

        self.media_panel.set_download_info(f"Downloaded {target.name}")
        self.project_panel.refresh(self.project)
        self._activate_media(target)
        self._push_status(f"Downloaded {target.name}")

    def _on_download_error(self, message: str) -> None:
        self._download_worker = None
        self.media_panel.set_download_error(message)
        self._push_status(f"Download failed: {message}")

    def cancel_download(self) -> None:
        if self._download_worker is not None and self._download_worker.isRunning():
            self._download_worker.cancel()
            self._push_status("Cancelling download...")

    def _check_youtube_metadata(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        self.media_panel.set_metadata_preview("Checking URL…")
        try:
            YouTubeService.validate(url)
        except InvalidYouTubeURL as exc:
            self.media_panel.set_metadata_preview(str(exc), error=True)
            return
        worker = MetadataWorker(url, service=self.youtube_service)
        worker.completed.connect(self._on_metadata_completed)
        worker.error.connect(self._on_metadata_error)
        self._metadata_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_metadata_completed(self, info) -> None:
        bits = [info.title]
        if info.duration > 0:
            bits.append(format_clock(info.duration))
        if info.resolution_label:
            bits.append(info.resolution_label)
        if info.uploader:
            bits.append(info.uploader)
        self.media_panel.set_metadata_preview("  -  ".join(bits))

    def _on_metadata_error(self, message: str) -> None:
        self.media_panel.set_metadata_preview(message, error=True)
        self._push_status(f"YouTube error: {message}")

    # ------------------------------------------------------------ playback
    def _on_player_position(self, ms: int) -> None:
        seconds = ms / 1000.0
        self.timeline.set_position(seconds)
        total = self.duration or (self.player.duration_ms / 1000.0)
        self.status_bar.set_times(seconds, total)

    def _on_player_duration(self, ms: int) -> None:
        # media duration (self.duration) stays authoritative for the timeline;
        # the player's duration is only used for the transport display.
        total = self.duration or (ms / 1000.0)
        self.status_bar.set_times(self.player.position_ms / 1000.0, total)

    def _on_player_error(self, message: str) -> None:
        self._push_status(message)
        log.warning("player error: %s", message)

    def _seek_seconds(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        if self.duration > 0:
            seconds = min(seconds, self.duration)
        self.player.seek_seconds(seconds)
        self.timeline.set_position(seconds)

    # ------------------------------------------------------------- timeline
    def _on_timeline_seek(self, seconds: float) -> None:
        self._seek_seconds(seconds)

    def _on_timeline_clip_selected(self, index: int) -> None:
        if not (0 <= index < len(self.timeline.clips)):
            return
        clip_id = self.timeline.clips[index]["id"]
        if self.current_clip is not None and self.current_clip.id == clip_id:
            self.editor_panel.select_clip(clip_id)
        else:
            self._select_clip(clip_id)

    def _on_timeline_range_edited(self, start: float, end: float) -> None:
        if self.current_clip is None:
            return
        if self._trim_snapshot is None:
            self._trim_snapshot = self.current_clip.model_dump()
        self._apply_timeline_range(start, end)

    def _on_timeline_range_finished(self, start: float, end: float) -> None:
        if self.current_clip is None:
            return
        before = self._trim_snapshot
        self._trim_snapshot = None
        self._apply_timeline_range(start, end)
        after = self.current_clip.model_dump()
        if before is not None and any(
            before[field] != after[field]
            for field in ("start", "end", "timeline_start", "timeline_end")
        ):
            self._push_undo(
                {
                    "type": "clip_update",
                    "clip_id": self.current_clip.id,
                    "before": before,
                    "after": after,
                    "label": "Trim clip",
                }
            )

    def _on_clip_moved(self, clip_id: int, new_start: float, new_end: float) -> None:
        if self.project is None:
            return
        clip = self.project.clip_by_id(clip_id)
        if clip is None:
            return
        if self._move_snapshot is None or self._move_snapshot.get("clip_id") != clip_id:
            before = clip.model_dump()
            self._move_snapshot = {"clip_id": clip_id, "before": before}
            self._push_undo(
                {
                    "type": "clip_update",
                    "clip_id": clip_id,
                    "before": before,
                    "after": None,  # filled lazily at undo time (LIFO-correct)
                    "label": "Move clip",
                }
            )
        clip.timeline_start = max(0.0, new_start)
        clip.timeline_end = max(clip.timeline_start + MIN_CLIP_SECONDS, new_end)
        self._persist_clip_update(clip)

    def _on_timeline_split_signal(self, clip_id: int, at: float) -> None:
        if self.project is None:
            return
        clip = self.project.clip_by_id(clip_id)
        if clip is None:
            return
        self._select_clip(clip_id)
        self._split_clip_at(clip, at)

    def _on_timeline_duplicate_signal(
        self, clip_id: int, _start: float, _end: float
    ) -> None:
        if self.project is None or self.project.clip_by_id(clip_id) is None:
            return
        self._select_clip(clip_id)
        self._duplicate_current_clip()

    def _apply_timeline_range(
        self,
        start: float,
        end: float,
        *,
        push_undo: bool = False,
        undo_label: str = "Trim clip",
        undo_before: dict | None = None,
    ) -> None:
        if self.current_clip is None or self.project is None:
            return
        clip = self.current_clip
        start = max(0.0, float(start))
        end = float(end)
        if self.duration > 0:
            end = min(end, self.duration)
        if end - start < MIN_CLIP_SECONDS:
            end = start + MIN_CLIP_SECONDS
            if self.duration > 0 and end > self.duration:
                start = max(0.0, self.duration - MIN_CLIP_SECONDS)
                end = self.duration
        if end <= start:
            return

        before = (
            undo_before
            if undo_before is not None
            else (clip.model_dump() if push_undo else None)
        )
        clip.start = start
        clip.end = end
        clip.timeline_start = start
        clip.timeline_end = end

        self._sync_timeline_clip(clip)
        self._committed_range = (start, end)

        if push_undo and before is not None:
            self._push_undo(
                {
                    "type": "clip_update",
                    "clip_id": clip.id,
                    "before": before,
                    "after": clip.model_dump(),
                    "label": undo_label,
                }
            )
        self._persist_clip_update(clip)

        self.inspector.set_clip(clip, self.current_media)
        self.status_bar.set_times(self.player.position_ms / 1000.0, self.duration)
        self.export_panel.refresh(clip, self.project, self.status_bar.aspect)

    def _on_inspector_range(self, start: float, end: float) -> None:
        if self.current_clip is None:
            return
        before = self.current_clip.model_dump()
        if self._committed_range is not None:
            # the inspector mutates the clip before emitting; restore the last
            # values we committed so undo has a correct "before" state
            before["start"], before["end"] = self._committed_range
            before["timeline_start"] = self._committed_range[0]
            before["timeline_end"] = self._committed_range[1]
        self._apply_timeline_range(
            start,
            end,
            push_undo=True,
            undo_label="Edit timecode",
            undo_before=before,
        )

    # ------------------------------------------------------------ clip ops
    def _select_clip(self, clip_id: int | None) -> None:
        if self.project is None:
            return
        if clip_id is None or self.current_clip is not None and self.current_clip.id == clip_id:
            if clip_id is not None:
                self.editor_panel.select_clip(clip_id)
                for idx, item in enumerate(self.timeline.clips):
                    if item["id"] == clip_id:
                        self.timeline.selected_clip_idx = idx
                        break
            return
        clip = self.project.clip_by_id(clip_id)
        if clip is None:
            return
        self.current_clip = clip
        media = self.project.media_by_id(clip.media_id) or self.current_media
        self._clear_edit_snapshots()
        if media is not None and media != self.current_media:
            self._activate_media(media, clip=clip)
        else:
            self._refresh_timeline()
            self._refresh_clip_list()
            self.inspector.set_clip(clip, media)
            self.status_bar.set_aspect(clip.aspect)
            self.export_panel.refresh(clip, self.project, clip.aspect)
            self._committed_range = (clip.start, clip.end)
        self._push_status(f"Selected {clip.name}")

    def _on_clip_selected(self, clip_id: int) -> None:
        self._select_clip(clip_id)

    def add_clip_from_range(self) -> None:
        if self.project is None or self.current_media is None:
            return
        start, end = self._committed_range or (0.0, self.current_media.duration)
        start = max(0.0, start)
        end = max(start + MIN_CLIP_SECONDS, end)

        order = (max((c.order for c in self.project.clips), default=-1) + 1)
        clip = Clip(
            media_id=self.current_media.id or 0,
            name=f"Clip {len(self.project.clips) + 1}",
            start=start,
            end=end,
            aspect=self.status_bar.aspect,
            timeline_start=self._next_timeline_position(),
            timeline_end=self._next_timeline_position() + (end - start),
            order=order,
            created_at=utc_now(),
        )
        self._persist_clip_new(clip)
        self.project.clips.append(clip)
        self._push_undo(
            {
                "type": "clip_add",
                "clip": clip.model_dump(),
                "index": len(self.project.clips) - 1,
                "label": "Add clip",
            }
        )
        self._refresh_timeline()
        self._refresh_clip_list()
        self._select_clip(clip.id)
        self._push_status(f"Added {clip.name}")

    def _next_timeline_position(self) -> float:
        if not self.project.clips:
            return 0.0
        return max(c.timeline_end for c in self.project.clips)

    def _remove_clip_by_id(self, clip_id: int) -> None:
        if self.project is None:
            return
        clip = self.project.clip_by_id(clip_id)
        if clip is None:
            return
        if len(self.project.clips) <= 1:
            self._push_status("A project needs at least one clip")
            return
        index = self.project.clips.index(clip)
        self.project.clips.remove(clip)
        if clip.id is not None:
            self._persist_clip_delete(clip.id)
        self._push_undo(
            {
                "type": "clip_remove",
                "clip": clip.model_dump(),
                "index": index,
                "label": "Delete clip",
            }
        )
        self._clear_edit_snapshots()
        self._refresh_timeline()
        self._refresh_clip_list()
        if self.current_clip is not None and self.current_clip.id == clip_id:
            fallback = self.project.clips[min(index, len(self.project.clips) - 1)]
            self._select_clip(fallback.id)
        self._push_status(f"Deleted {clip.name}")

    def _remove_selected_clip(self) -> None:
        if self.current_clip is not None:
            self._remove_clip_by_id(self.current_clip.id)

    def _shortcut_split(self) -> None:
        if self._typing_focus() or self._button_focus():
            return
        if self.current_clip is None:
            return
        at = self.player.position_ms / 1000.0
        self._split_clip_at(self.current_clip, at)

    def _split_clip_at(self, clip: Clip, at: float) -> None:
        if self.project is None:
            return
        if not (clip.start + MIN_CLIP_SECONDS < at < clip.end - MIN_CLIP_SECONDS):
            self._push_status("Move the playhead inside the clip to split")
            return
        index = self.project.clips.index(clip)
        before = clip.model_dump()
        clip.end = at
        clip.timeline_end = clip.timeline_start + (at - clip.start)
        self._persist_clip_update(clip)

        tail = clip.model_copy(
            update={
                "id": None,
                "name": f"{clip.name} (B)",
                "start": at,
                "end": before["end"],
                "timeline_start": clip.timeline_end,
                "timeline_end": clip.timeline_end + (before["end"] - at),
                "created_at": utc_now(),
            }
        )
        clip.name = f"{clip.name} (A)"
        self._persist_clip_new(tail)
        self.project.clips.insert(index + 1, tail)
        self._renumber_orders()
        self._push_undo(
            {
                "type": "group",
                "actions": [
                    {
                        "type": "clip_update",
                        "clip_id": clip.id,
                        "before": before,
                        "after": clip.model_dump(),
                    },
                    {
                        "type": "clip_add",
                        "clip": tail.model_dump(),
                        "index": index + 1,
                    },
                ],
                "label": "Split clip",
            }
        )
        self._clear_edit_snapshots()
        self._refresh_timeline()
        self._refresh_clip_list()
        self._select_clip(clip.id)
        self._push_status(f"Split at {format_timecode(at)}")

    def _shortcut_duplicate(self) -> None:
        if self._typing_focus() or self._button_focus():
            return
        self._duplicate_current_clip()

    def _duplicate_current_clip(self) -> None:
        if self.project is None or self.current_clip is None:
            return
        source = self.current_clip
        length = source.end - source.start
        copy = source.model_copy(
            update={
                "id": None,
                "name": f"{source.name} copy",
                "timeline_start": self._next_timeline_position(),
                "timeline_end": self._next_timeline_position() + length,
                "order": (max((c.order for c in self.project.clips), default=-1) + 1),
                "created_at": utc_now(),
            }
        )
        self._persist_clip_new(copy)
        self.project.clips.append(copy)
        self._push_undo(
            {
                "type": "clip_add",
                "clip": copy.model_dump(),
                "index": len(self.project.clips) - 1,
                "label": "Duplicate clip",
            }
        )
        self._clear_edit_snapshots()
        self._refresh_timeline()
        self._refresh_clip_list()
        self._select_clip(copy.id)
        self._push_status(f"Duplicated as {copy.name}")

    def _shortcut_delete(self) -> None:
        if self._typing_focus():
            return
        self._remove_selected_clip()

    # ------------------------------------------------------------ undo/redo
    def _push_undo(self, action: dict) -> None:
        self._undo_stack.append(action)
        if len(self._undo_stack) > MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._after_action()

    def _fill_lazy_after(self, action: dict) -> None:
        if action.get("type") != "clip_update" or action.get("after") is not None:
            return
        if self.project is None:
            return
        clip = self.project.clip_by_id(action["clip_id"])
        action["after"] = clip.model_dump() if clip else None

    def _apply_action(self, action: dict, reverse: bool) -> None:
        if self.project is None:
            return
        kind = action.get("type")
        if kind == "group":
            for sub in reversed(action["actions"]) if reverse else action["actions"]:
                self._apply_action(sub, reverse)
            return
        if kind == "clip_update":
            clip = self.project.clip_by_id(action["clip_id"])
            data = action["after"] if reverse else action["before"]
            if clip is None or data is None:
                return
            for field in ("name", "start", "end", "aspect",
                          "timeline_start", "timeline_end", "order"):
                setattr(clip, field, data[field])
            self._persist_clip_update(clip)
            if self.current_clip is not None and self.current_clip.id == clip.id:
                self.current_clip = clip
            return
        if kind == "clip_add":
            data = action["clip"]
            if reverse:
                clip = self.project.clip_by_id(data["id"])
                if clip is not None:
                    self.project.clips.remove(clip)
                    if clip.id is not None:
                        self._persist_clip_delete(clip.id)
            else:
                restored = Clip.model_validate(data)
                restored.id = None
                self._persist_clip_new(restored)
                index = min(action.get("index", len(self.project.clips)),
                            len(self.project.clips))
                self.project.clips.insert(index, restored)
            return
        if kind == "clip_remove":
            data = action["clip"]
            if reverse:
                restored = Clip.model_validate(data)
                restored.id = None
                self._persist_clip_new(restored)
                index = min(action.get("index", len(self.project.clips)),
                            len(self.project.clips))
                self.project.clips.insert(index, restored)
            else:
                clip = self.project.clip_by_id(data["id"])
                if clip is not None:
                    self.project.clips.remove(clip)
                    if clip.id is not None:
                        self._persist_clip_delete(clip.id)
            return

    def _undo(self) -> None:
        if self._typing_focus():
            return
        if not self._undo_stack:
            self._push_status("Nothing to undo")
            return
        action = self._undo_stack.pop()
        self._fill_lazy_after(action)
        self._apply_action(action, reverse=True)
        self._redo_stack.append(action)
        self._after_action(label=f"Undo {action.get('label', 'change')}".lower())

    def _redo(self) -> None:
        if self._typing_focus():
            return
        if not self._redo_stack:
            self._push_status("Nothing to redo")
            return
        action = self._redo_stack.pop()
        self._fill_lazy_after(action)
        self._apply_action(action, reverse=False)
        self._undo_stack.append(action)
        self._after_action(label=f"Redo {action.get('label', 'change')}".lower())

    def _after_action(self, label: str | None = None) -> None:
        if self.project is None:
            return
        self._renumber_orders()
        self._clear_edit_snapshots()
        self._refresh_timeline()
        self._refresh_clip_list()
        self.project_panel.refresh(self.project)
        if self.current_clip is not None:
            refreshed = self.project.clip_by_id(self.current_clip.id)
            if refreshed is None and self.project.clips:
                refreshed = self.project.clips[0]
            self.current_clip = refreshed
            media = (
                self.project.media_by_id(refreshed.media_id)
                if refreshed is not None
                else None
            )
            self.inspector.set_clip(refreshed, media)
            if refreshed is not None:
                self._committed_range = (refreshed.start, refreshed.end)
            self.export_panel.refresh(
                refreshed, self.project, self.status_bar.aspect
            )
        if label:
            self._push_status(label)

    def _renumber_orders(self) -> None:
        if self.project is None:
            return
        for index, clip in enumerate(self.project.clips):
            if clip.order != index:
                clip.order = index
                self._persist_clip_update(clip)

    def _insert_clip_from_dict(self, data: dict) -> None:  # kept for clarity
        restored = Clip.model_validate(data)
        restored.id = None
        self._persist_clip_new(restored)
        self.project.clips.append(restored)

    def _remove_clip_from_project(self, clip_id: int) -> None:
        if self.project is None:
            return
        clip = self.project.clip_by_id(clip_id)
        if clip is None:
            return
        self.project.clips.remove(clip)
        if clip.id is not None:
            self._persist_clip_delete(clip.id)

    # --------------------------------------------------------- sync/refresh
    def _on_aspect_changed(self, aspect: str) -> None:
        if aspect not in ASPECT_RATIOS:
            return
        if self.project is not None:
            self.project.editor_settings.aspect = aspect
        self.status_bar.set_aspect(aspect)
        self.player.set_aspect(aspect)
        self.inspector.set_aspect_value(aspect)
        if self.current_clip is not None and self.project is not None:
            before = self.current_clip.model_dump()
            self.current_clip.aspect = aspect
            self._persist_clip_update(self.current_clip)
            self._push_undo(
                {
                    "type": "clip_update",
                    "clip_id": self.current_clip.id,
                    "before": before,
                    "after": self.current_clip.model_dump(),
                    "label": "Change aspect",
                }
            )
            self.export_panel.refresh(
                self.current_clip, self.project, aspect
            )

    def _refresh_clip_list(self) -> None:
        if self.project is None:
            return
        clips = sorted(
            self.project.clips,
            key=lambda c: (c.order, c.timeline_start, c.start, c.id or 0),
        )
        media_names = {
            m.id: m.name for m in self.project.media if m.id is not None
        }
        self.editor_panel.set_clips(clips, media_names)
        if self.current_clip is not None:
            self.editor_panel.select_clip(self.current_clip.id)

    def _refresh_timeline(self) -> None:
        if self.project is None:
            return
        clips = sorted(
            self.project.clips,
            key=lambda c: (c.order, c.timeline_start, c.start, c.id or 0),
        )
        data = []
        for clip in clips:
            data.append(
                {
                    "id": clip.id if clip.id is not None else -1,
                    "name": clip.name,
                    "start": clip.start,
                    "end": clip.end,
                    "timeline_start": clip.timeline_start,
                    "timeline_end": clip.timeline_end,
                    "aspect": clip.aspect,
                }
            )
        self.timeline.clips = data
        selected_id = self.current_clip.id if self.current_clip is not None else None
        self.timeline.selected_clip_idx = next(
            (i for i, c in enumerate(clips) if c.id == selected_id), None
        )
        self.timeline.update()

    def _sync_timeline_clip(self, clip: Clip) -> None:
        for item in self.timeline.clips:
            if item["id"] == clip.id:
                item.update(
                    {
                        "name": clip.name,
                        "start": clip.start,
                        "end": clip.end,
                        "timeline_start": clip.timeline_start,
                        "timeline_end": clip.timeline_end,
                        "aspect": clip.aspect,
                    }
                )
                break
        self.timeline.update()

    def _clear_edit_snapshots(self) -> None:
        self._trim_snapshot = None
        self._move_snapshot = None

    def update_clip_reference(self, clip: Clip) -> None:
        self.current_clip = clip

    def _clips_from_project(self) -> list[Clip]:
        if self.project is None:
            return []
        return sorted(
            self.project.clips,
            key=lambda c: (c.order, c.timeline_start, c.start, c.id or 0),
        )

    # ------------------------------------------------------------- export
    def open_export_dialog(self) -> None:
        if self.project is None or self.current_clip is None:
            if self.project is None:
                self._push_status("Open or create a project first")
            else:
                self._push_status("Select a clip first")
            return
        self._sync_settings()
        media = self.project.media_by_id(self.current_clip.media_id)
        if media is None:
            self._push_status("Clip's media is missing from the project")
            return
        if self._export_dialog is not None and self._export_dialog.isVisible():
            self._export_dialog.raise_()
            return
        dialog = ExportDialog(self.project, self.current_clip, media, self)
        self._export_dialog = dialog
        dialog.exec()
        self._export_dialog = None

    def _export_all_clips(self) -> None:
        if not self._require_project():
            return
        if not self.project.clips:
            self._push_status("No clips to export")
            return
        if self._export_multi_worker is not None and self._export_multi_worker.isRunning():
            self._push_status("An export is already running")
            return
        self._sync_settings()
        if not self.save_project():
            return

        self._push_status(
            f"Exporting {len(self.project.clips)} clips...  0%"
        )
        self.status_bar.set_export_enabled(False)
        worker = MultiExportWorker(self.project, self.project.clips, self)
        worker.progress.connect(self._on_export_all_progress)
        worker.completed.connect(self._on_export_all_completed)
        worker.error.connect(self._on_export_all_error)
        self._export_multi_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_export_all_progress(self, progress: float, detail: str) -> None:
        percent = int(max(0.0, min(100.0, progress)))
        self._push_status(f"Exporting...  {percent}%  -  {detail}")

    def _on_export_all_completed(self, paths: list) -> None:
        self._export_multi_worker = None
        self.status_bar.set_export_enabled(True)
        count = len(paths)
        self._push_status(f"Exported {count} clip{'s' if count != 1 else ''}")
        if paths:
            reply = QMessageBox.question(
                self,
                "Export All",
                f"Exported {count} clips.\n\nOpen the output folder?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                open_in_explorer(Path(paths[0]).parent)
        log.info("export-all finished: %s", [str(p) for p in paths])

    def _on_export_all_error(self, message: str) -> None:
        self._export_multi_worker = None
        self.status_bar.set_export_enabled(True)
        self._push_status(f"Export failed: {message}")
        QMessageBox.warning(self, "Export All", message)

    # ------------------------------------------------------------ guards
    def _typing_focus(self) -> bool:
        focused = QApplication.focusWidget()
        return isinstance(
            focused, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox)
        )

    def _button_focus(self) -> bool:
        from PySide6.QtWidgets import QPushButton

        return isinstance(QApplication.focusWidget(), QPushButton)

    def _shortcut_play_pause(self) -> None:
        if self._button_focus():
            return
        self.player.toggle_play()

    def _shortcut_seek(self, delta: float) -> None:
        if self._typing_focus():
            return
        current = self.player.position_ms / 1000.0
        self._seek_seconds(current + delta)

    def _shortcut_set_in(self) -> None:
        if self._typing_focus():
            return
        if self.current_clip is None:
            return
        position = self.player.position_ms / 1000.0
        end = max(self.current_clip.end, position + MIN_CLIP_SECONDS)
        self._apply_timeline_range(position, end, push_undo=True, undo_label="Set in point")

    def _shortcut_set_out(self) -> None:
        if self._typing_focus():
            return
        if self.current_clip is None:
            return
        position = self.player.position_ms / 1000.0
        start = min(self.current_clip.start, max(0.0, position - MIN_CLIP_SECONDS))
        self._apply_timeline_range(start, position, push_undo=True, undo_label="Set out point")

    # -------------------------------------------------------------- misc
    def _push_status(self, message: str) -> None:
        self.status_bar.show_message(message)

    def _require_project(self) -> bool:
        if self.project is None:
            self._push_status("Open or create a project first")
            return False
        return True

    def _track_worker(self, worker: QThread) -> None:
        self._bg_workers.append(worker)

        def cleanup() -> None:
            if worker in self._bg_workers:
                self._bg_workers.remove(worker)

        worker.finished.connect(cleanup)

    def _untrack_worker(self, worker: QThread) -> None:
        if worker in self._bg_workers:
            self._bg_workers.remove(worker)

    def _refresh_ffmpeg_status(self) -> None:
        available = self.ffmpeg.available()
        self._show_ffmpeg_status(available)

    def _show_ffmpeg_status(self, available: bool | None = None) -> None:
        if available is None:
            available = self.ffmpeg.available()
        if available:
            version = self.ffmpeg.version() or "available"
            self._push_status(f"FFmpeg {version}")
        else:
            self._push_status("FFmpeg not found - exports will fail")

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b><br>"
            "Local-first AI clip studio.<br>"
            "Import videos, trim clips, export vertical shorts - all on your machine.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._download_worker is not None and self._download_worker.isRunning():
            self._download_worker.cancel()
            self._download_worker.wait(2000)
        if self._export_multi_worker is not None and self._export_multi_worker.isRunning():
            self._export_multi_worker.cancel()
            self._export_multi_worker.wait(3000)
        if not self._confirm_discard():
            event.ignore()
            return
        for worker in list(self._bg_workers):
            if worker.isRunning():
                worker.terminate()
                worker.wait(1000)
        event.accept()

    # --------------------------------------------------------- DB helpers
    def _persist_clip_new(self, clip: Clip) -> None:
        if self.project is None:
            return
        database = ProjectDatabase(self.project.path)
        try:
            ClipRepository(database).add(clip)
        finally:
            database.close()

    def _persist_clip_update(self, clip: Clip) -> None:
        if self.project is None or clip.id is None:
            return
        database = ProjectDatabase(self.project.path)
        try:
            ClipRepository(database).update(clip)
        finally:
            database.close()

    def _persist_clip_delete(self, clip_id: int) -> None:
        if self.project is None:
            return
        database = ProjectDatabase(self.project.path)
        try:
            ClipRepository(database).delete(clip_id)
        finally:
            database.close()

