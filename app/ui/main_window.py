from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread
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
from app.database.repositories import (
    CaptionRepository,
    ClipRepository,
    MediaRepository,
    TranscriptRepository,
    TrackRepository,
)
from app.models.clip import ASPECT_RATIOS, QUALITY_PRESETS, Clip
from app.models.media import MediaItem, MediaKind, MediaProbeResult
from app.models.project import Project, utc_now
from app.models.queue import JOB_RUNNING, ExportJob
from app.models.track import TrackKeyframe
from app.services.ai_service import action_prompt, build_messages, project_context
from app.services.face_track_service import availability as smart_availability
from app.services.caption_service import whisper_available, write_srt
from app.services.export_queue import ExportQueueWorker
from app.services.ffmpeg_service import FFmpegService
from app.services.project_service import ProjectError, ProjectService
from app.services.settings_service import SettingsService
from app.services.youtube_service import InvalidYouTubeURL, YouTubeService
from app.services.video_service import VideoService
from app.ui.ai_panel import AIPanel
from app.ui.auto_panel import AutoPanel
from app.ui.captions_panel import CaptionsPanel
from app.ui.dashboard import ProjectDashboard
from app.ui.export_dialog import ExportDialog
from app.ui.inspector import Inspector
from app.ui.media_panel import MediaPanel
from app.ui.project_dialog import NewProjectDialog
from app.ui.project_panel import EditorPanel, ExportPanel, ProjectPanel
from app.ui.queue_panel import QueuePanel
from app.ui.settings_panel import SettingsPanel
from app.ui.sidebar import Sidebar
from app.ui.smart_panel import SmartPanel
from app.ui.status_bar import StatusBar
from app.ui.timeline import Timeline
from app.ui.video_player import VideoPlayer
from app.utils.logging import get_logger
from app.utils.paths import (
    cache_dir,
    format_bytes,
    open_in_explorer,
    projects_dir,
    safe_name,
)
from app.utils.timecode import format_clock, format_timecode
from app.workers.download_worker import DownloadWorker, MetadataWorker
from app.workers.ollama_worker import ModelsWorker, OllamaWorker
from app.workers.probe_worker import ProbeWorker
from app.workers.transcribe_worker import TranscribeWorker
from app.workers.autoclip_worker import AutoClipWorker
from app.workers.smart_track_worker import SmartTrackWorker

log = get_logger("main_window")

PAGE_MEDIA = 0
PAGE_PROJECT = 1
PAGE_EDITOR = 2
PAGE_EXPORT = 3
PAGE_QUEUE = 4
PAGE_SETTINGS = 5
PAGE_CAPTIONS = 6
PAGE_AI = 7
PAGE_AUTO = 8
PAGE_SMART = 9

SEEK_STEP_SECONDS = 5.0
MIN_CLIP_SECONDS = 0.05
MAX_UNDO = 200

PAGE_INDEX = {
    "media": PAGE_MEDIA,
    "project": PAGE_PROJECT,
    "editor": PAGE_EDITOR,
    "export": PAGE_EXPORT,
    "queue": PAGE_QUEUE,
    "settings": PAGE_SETTINGS,
    "captions": PAGE_CAPTIONS,
    "ai": PAGE_AI,
    "auto": PAGE_AUTO,
    "smart": PAGE_SMART,
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        self.project_service = ProjectService()
        self.settings_service = SettingsService()
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
        self._export_dialog: ExportDialog | None = None
        self._pending_probe_count = 0
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._trim_snapshot: dict | None = None
        self._move_snapshot: dict | None = None
        self._committed_range: tuple[float, float] | None = None
        self._transcribe_worker: TranscribeWorker | None = None
        self._transcribe_clip_id: int | None = None
        self._ai_worker: OllamaWorker | None = None
        self._models_worker: ModelsWorker | None = None
        self._auto_worker: AutoClipWorker | None = None
        self._auto_project_path: str | None = None
        self._smart_worker: SmartTrackWorker | None = None

        self.queue_worker = ExportQueueWorker(self)
        self.queue_worker.jobAdded.connect(self._on_queue_added)
        self.queue_worker.jobUpdated.connect(self._on_queue_changed)
        self.queue_worker.jobsCleared.connect(self._on_queue_changed)
        self.queue_worker.queueEmpty.connect(self._on_queue_empty)

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
        self.queue_panel = QueuePanel()
        self.settings_panel = SettingsPanel(self.settings_service)
        self.captions_panel = CaptionsPanel()
        self.ai_panel = AIPanel()
        self.auto_panel = AutoPanel()
        self.smart_panel = SmartPanel()
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
        self.pages.addWidget(self.media_panel)     # PAGE_MEDIA
        self.pages.addWidget(self.project_panel)   # PAGE_PROJECT
        self.pages.addWidget(self.editor_panel)    # PAGE_EDITOR
        self.pages.addWidget(self.export_panel)    # PAGE_EXPORT
        self.pages.addWidget(self.queue_panel)     # PAGE_QUEUE
        self.pages.addWidget(self.settings_panel)  # PAGE_SETTINGS
        self.pages.addWidget(self.captions_panel)  # PAGE_CAPTIONS
        self.pages.addWidget(self.ai_panel)        # PAGE_AI
        self.pages.addWidget(self.auto_panel)      # PAGE_AUTO
        self.pages.addWidget(self.smart_panel)      # PAGE_SMART
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
        self.export_panel.queueRequested.connect(self._queue_current_clip)

        self.queue_panel.cancelRequested.connect(self.queue_worker.cancel_job)
        self.queue_panel.cancelAllRequested.connect(self.queue_worker.cancel_all)
        self.queue_panel.clearRequested.connect(self.queue_worker.clear_finished)
        self.queue_panel.openFolderRequested.connect(self._open_queue_output_folder)

        self.settings_panel.settingsSaved.connect(self._on_settings_saved)
        self.settings_panel.openOutputFolderRequested.connect(
            self._open_default_output_folder
        )

        self.captions_panel.transcribeRequested.connect(self._start_transcription)
        self.captions_panel.cancelRequested.connect(self._cancel_transcription)
        self.captions_panel.exportSrtRequested.connect(self._on_caption_export_srt)
        self.captions_panel.clearRequested.connect(self._on_caption_clear)
        self.captions_panel.segmentEdited.connect(self._on_caption_segment_edited)

        self.ai_panel.sendRequested.connect(self._on_ai_send)
        self.ai_panel.actionRequested.connect(self._on_ai_action)
        self.ai_panel.cancelRequested.connect(self._on_ai_cancel)
        self.ai_panel.refreshRequested.connect(self._refresh_ai_models)
        self.ai_panel.clearRequested.connect(self._on_ai_clear)

        self.auto_panel.analyzeRequested.connect(self._start_auto_analysis)
        self.auto_panel.cancelRequested.connect(self._cancel_auto_analysis)
        self.auto_panel.previewRequested.connect(self._on_auto_preview)
        self.auto_panel.addRequested.connect(self._on_auto_add)
        self.auto_panel.clearRequested.connect(self._on_auto_clear)
        self.auto_panel.mediaChanged.connect(self._on_auto_media_changed)

        self.smart_panel.analyzeRequested.connect(self._start_smart_analysis)
        self.smart_panel.cancelRequested.connect(self._cancel_smart_analysis)
        self.smart_panel.clearRequested.connect(self._on_smart_clear)
        self.smart_panel.mediaChanged.connect(self._on_smart_media_changed)
        self.smart_panel.overlayChanged.connect(self._on_smart_overlay)

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
        elif key == "queue":
            self.queue_panel.refresh(self.queue_worker.jobs_snapshot())
        elif key == "settings":
            self.settings_panel.load_values()
        elif key == "captions":
            self._refresh_captions_panel()
        elif key == "ai":
            self.ai_panel.set_clip(self.current_clip, self._current_media_name())
            self._refresh_ai_models()
        elif key == "auto":
            self._refresh_auto_panel()
        elif key == "smart":
            self._refresh_smart_panel()
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
                defaults = self.settings_service.settings
                if defaults.default_aspect in ASPECT_RATIOS:
                    project.editor_settings.aspect = defaults.default_aspect
                if defaults.default_preset in QUALITY_PRESETS:
                    project.export_settings.preset = defaults.default_preset
                    project.export_settings.crf = QUALITY_PRESETS[
                        defaults.default_preset
                    ]["crf"]
                self.project_service.save_project(project)
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

        defaults = self.settings_service.settings
        self.captions_panel.set_whisper_available(whisper_available())
        self.captions_panel.set_model_value(defaults.whisper_model)
        self.captions_panel.set_language_value(defaults.whisper_language)
        self._refresh_captions_panel()
        self._refresh_auto_panel()
        self._refresh_smart_panel()

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
        self._apply_smart_overlay()
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
        if self.pages.currentIndex() == PAGE_CAPTIONS:
            self._refresh_captions_panel()
        elif self.pages.currentIndex() == PAGE_AI:
            self.ai_panel.set_clip(clip, self._current_media_name())
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
        captions_srt = self._write_clip_srt(self.current_clip)
        smart_track = self._load_track_keys(media.id or 0)
        dialog = ExportDialog(
            self.project,
            self.current_clip,
            media,
            self,
            output_dir=self.settings_service.settings.output_dir,
            captions_srt=captions_srt,
            burn_captions=self.settings_service.settings.burn_captions,
            smart_track=smart_track,
            smart_crop_default=self.settings_service.settings.smart_crop,
        )
        self._export_dialog = dialog
        dialog.exec()
        self._export_dialog = None

    # -------------------------------------------------------------- queue
    def _enqueue_clips(self, clips: list[Clip]) -> int:
        if self.project is None:
            return 0
        output_dir = self.settings_service.settings.output_dir
        burn = self.settings_service.settings.burn_captions
        count = 0
        for clip in clips:
            captions_srt = self._write_clip_srt(clip) if burn else None
            job = self.queue_worker.enqueue_clip(
                self.project,
                clip,
                output_dir=output_dir,
                captions_srt=captions_srt or "",
                smart_crop=self.settings_service.settings.smart_crop,
            )
            if job is not None:
                count += 1
        return count

    def _queue_current_clip(self) -> None:
        if not self._require_project():
            return
        if self.current_clip is None:
            self._push_status("Select a clip first")
            return
        self._sync_settings()
        clip_name = self.current_clip.name
        if self._enqueue_clips([self.current_clip]):
            self.sidebar.select("queue")
            self._push_status(f"Queued {clip_name}")

    def _export_all_clips(self) -> None:
        if not self._require_project():
            return
        if not self.project.clips:
            self._push_status("No clips to export")
            return
        self._sync_settings()
        clips = self._clips_from_project()
        count = self._enqueue_clips(clips)
        self.sidebar.select("queue")
        self._push_status(f"Queued {count} clips")

    def _on_queue_added(self, job: ExportJob) -> None:
        self.queue_panel.refresh(self.queue_worker.jobs_snapshot())
        if job.status == "error":
            self._push_status(f"Could not queue {job.clip_name}: {job.error}")

    def _on_queue_changed(self, _job=None) -> None:
        jobs = self.queue_worker.jobs_snapshot()
        self.queue_panel.refresh(jobs)
        running = next((job for job in jobs if job.status == JOB_RUNNING), None)
        if running is not None:
            self._push_status(
                f"Exporting {running.clip_name}  -  "
                f"{int(running.progress * 100)}%  -  {running.detail}"
            )

    def _on_queue_empty(self, done: int, errors: int) -> None:
        if done == 0 and errors == 0:
            return
        parts = []
        if done:
            parts.append(f"{done} exported")
        if errors:
            parts.append(f"{errors} failed")
        message = "Queue finished: " + ", ".join(parts)
        self._push_status(message)
        log.info(message)
        if done:
            reply = QMessageBox.question(
                self,
                "Export Queue",
                f"Exported {done} clip{'s' if done != 1 else ''}.\n\n"
                "Open the output folder?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._open_queue_output_folder()

    def _open_queue_output_folder(self) -> None:
        for job in reversed(self.queue_worker.jobs_snapshot()):
            target = Path(job.output)
            if job.status == "done" and target.exists():
                open_in_explorer(target)
                return
        if self.project is not None:
            open_in_explorer(self.project.exports_dir)
        else:
            self._push_status("No exports yet")

    def _open_default_output_folder(self) -> None:
        target = self.settings_service.settings.output_dir
        if target and Path(target).exists():
            open_in_explorer(Path(target))
        elif self.project is not None:
            open_in_explorer(self.project.exports_dir)
        else:
            self._push_status("No output folder yet")

    def _on_settings_saved(self, settings) -> None:
        self.captions_panel.set_model_value(settings.whisper_model)
        self.captions_panel.set_language_value(settings.whisper_language)
        self.ai_panel.set_status("Settings saved - press Refresh on the AI page")
        self._push_status(
            f"Settings saved  -  {settings.default_aspect}  ·  "
            f"{settings.default_preset}"
        )

    # ----------------------------------------------------------- captions
    def _load_captions(self, clip_id: int | None) -> list:
        if clip_id is None:
            return []
        database = ProjectDatabase(self.project.path)
        try:
            return CaptionRepository(database).for_clip(clip_id)
        finally:
            database.close()

    def _refresh_captions_panel(self) -> None:
        clip = self.current_clip
        media_name = ""
        if self.project is not None and clip is not None:
            media = self.project.media_by_id(clip.media_id)
            media_name = media.name if media else ""
        self.captions_panel.set_clip(clip, media_name)
        if clip is None or clip.id is None:
            self.captions_panel.set_segments([])
            return
        self.captions_panel.set_segments(self._load_captions(clip.id))

    def _start_transcription(self) -> None:
        if not self._require_project() or self.current_clip is None:
            return
        if self._transcribe_worker is not None and self._transcribe_worker.isRunning():
            self._push_status("Transcription is already running")
            return
        if not whisper_available():
            self.captions_panel.set_status(
                "faster-whisper is not installed - run: "
                "python -m pip install faster-whisper",
                error=True,
            )
            return
        clip = self.current_clip
        media = self.project.media_by_id(clip.media_id)
        if media is None:
            self._push_status("Clip's media is missing from the project")
            return
        source = media.resolve_path(self.project.directory)
        if not source.exists():
            source = Path(media.source_path)
        if not source.exists():
            self.captions_panel.set_status(
                f"Source file not found: {media.source_path}", error=True
            )
            return

        worker = TranscribeWorker(
            source,
            clip.start,
            clip.end,
            model_name=self.captions_panel.selected_model(),
            language=self.captions_panel.selected_language(),
            download_root=cache_dir() / "whisper",
            ffmpeg=self.ffmpeg,
            parent=self,
        )
        worker.progress.connect(self._on_transcribe_progress)
        worker.completed.connect(self._on_transcribe_completed)
        worker.error.connect(self._on_transcribe_error)
        self._transcribe_worker = worker
        self._transcribe_clip_id = clip.id
        self._track_worker(worker)
        self.captions_panel.set_busy(True)
        self.captions_panel.set_status(
            f"Transcribing {clip.name} with "
            f"'{self.captions_panel.selected_model()}' model…"
        )
        worker.start()

    def _cancel_transcription(self) -> None:
        if self._transcribe_worker is not None and self._transcribe_worker.isRunning():
            self._transcribe_worker.cancel()
            self.captions_panel.set_status("Cancelling…")

    def _on_transcribe_progress(self, message: str) -> None:
        self.captions_panel.set_status(message)

    def _on_transcribe_completed(self, segments, language: str) -> None:
        self.captions_panel.set_busy(False)
        if self.project is None:
            return
        clip_id = self._transcribe_clip_id
        self._transcribe_clip_id = None
        if clip_id is None or self.project.clip_by_id(clip_id) is None:
            self.captions_panel.set_status(
                "Clip no longer exists - result discarded", error=True
            )
            return
        if not segments:
            existing = self._load_captions(clip_id)
            self.captions_panel.set_segments(existing)
            message = "No speech detected"
            if existing:
                message += f" (kept {len(existing)} existing segments)"
            self.captions_panel.set_status(message)
            return
        database = ProjectDatabase(self.project.path)
        try:
            saved = CaptionRepository(database).replace_for_clip(
                clip_id, list(segments)
            )
        finally:
            database.close()
        self.captions_panel.set_segments(saved)
        lang = f" · {language}" if language else ""
        self.captions_panel.set_status(f"Transcribed {len(saved)} segments{lang}")
        self._push_status(f"Captions ready for {self.current_clip.name if self.current_clip else 'clip'}")
        if self.pages.currentIndex() != PAGE_CAPTIONS:
            self.sidebar.select("captions")

    def _on_transcribe_error(self, message: str) -> None:
        self.captions_panel.set_busy(False)
        self.captions_panel.set_status(message, error=True)
        self._push_status(f"Transcription failed: {message}")

    def _on_caption_export_srt(self) -> None:
        if not self._require_project() or self.current_clip is None:
            return
        segments = self.captions_panel.segments
        if not segments:
            self._push_status("No captions to export")
            return
        clip = self.current_clip
        default = self.project.exports_dir / f"{safe_name(clip.name)}.srt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SRT", str(default), "SubRip subtitles (*.srt)"
        )
        if not path:
            return
        if not path.lower().endswith(".srt"):
            path += ".srt"
        write_srt(segments, path)
        self._push_status(f"Saved {path}")

    def _on_caption_clear(self) -> None:
        if not self._require_project() or self.current_clip is None:
            return
        if not self.captions_panel.segments:
            return
        answer = QMessageBox.question(
            self,
            "Clear Captions",
            "Delete all captions for this clip?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        database = ProjectDatabase(self.project.path)
        try:
            CaptionRepository(database).delete_for_clip(self.current_clip.id)
        finally:
            database.close()
        self.captions_panel.set_segments([])
        self._push_status("Captions cleared")

    def _on_caption_segment_edited(self, segment_id: int, text: str) -> None:
        if self.project is None:
            return
        database = ProjectDatabase(self.project.path)
        try:
            CaptionRepository(database).update_text(segment_id, text)
        finally:
            database.close()
        self._push_status("Caption updated")

    def _write_clip_srt(self, clip: Clip | None) -> Path | None:
        """Snapshot a clip's captions to an SRT file (for burn-in exports)."""
        if self.project is None or clip is None or clip.id is None:
            return None
        segments = self._load_captions(clip.id)
        if not segments:
            return None
        target = self.project.cache_dir / f"captions_clip_{clip.id}.srt"
        try:
            return write_srt(segments, target)
        except OSError as exc:
            log.warning("could not write srt: %s", exc)
            return None

    # ----------------------------------------------------------------- ai
    def _current_media_name(self) -> str:
        if self.project is None or self.current_clip is None:
            return ""
        media = self.project.media_by_id(self.current_clip.media_id)
        return media.name if media else ""

    def _refresh_ai_models(self) -> None:
        if self._models_worker is not None and self._models_worker.isRunning():
            return
        url = self.settings_service.settings.ollama_url
        worker = ModelsWorker(url, parent=self)
        worker.completed.connect(self._on_ai_models_loaded)
        worker.error.connect(self._on_ai_models_error)
        self._models_worker = worker
        self._track_worker(worker)
        self.ai_panel.set_status("Checking Ollama server...")
        worker.start()

    def _on_ai_models_loaded(self, models: list) -> None:
        preferred = self.settings_service.settings.ollama_model
        self.ai_panel.set_models(list(models), preferred)
        if models:
            self.ai_panel.set_status(f"{len(models)} model(s) ready")
        else:
            self.ai_panel.set_status(
                "Ollama is running but has no models - run: "
                "ollama pull qwen2.5:3b",
                error=True,
            )

    def _on_ai_models_error(self, message: str) -> None:
        self.ai_panel.set_models([])
        self.ai_panel.set_status(message, error=True)

    def _ai_context(self) -> str:
        clip = self.current_clip
        captions = (
            self._load_captions(clip.id)
            if clip is not None and clip.id is not None
            else []
        )
        return project_context(
            self.project, clip, self._current_media_name(), captions
        )

    def _on_ai_send(self, text: str, display: str | None = None) -> None:
        if not self._require_project():
            return
        if self.ai_panel.busy:
            self._push_status("The assistant is already responding")
            return
        model = self.ai_panel.selected_model()
        if not model:
            self.ai_panel.set_status(
                "No model selected - is the Ollama server running? "
                "Check the URL on SETTINGS, then press Refresh.",
                error=True,
            )
            return
        settings = self.settings_service.settings
        if model != settings.ollama_model:
            settings.ollama_model = model
            self.settings_service.save(settings)
        messages = build_messages(
            self._ai_context(), self.ai_panel.history, text
        )
        worker = OllamaWorker(
            settings.ollama_url, model, messages, parent=self
        )
        worker.token.connect(self.ai_panel.append_token)
        worker.completed.connect(self._on_ai_completed)
        worker.error.connect(self._on_ai_error)
        self._ai_worker = worker
        self._track_worker(worker)
        self.ai_panel.append_user(display or text)
        self.ai_panel.begin_assistant()
        self.ai_panel.set_busy(True)
        self.ai_panel.set_status(f"Thinking with {model}...")
        worker.start()

    def _on_ai_action(self, action: str) -> None:
        if not self._require_project():
            return
        if self.current_clip is None:
            self.ai_panel.set_status(
                "Select a clip first - quick actions use its transcript",
                error=True,
            )
            return
        prompt = action_prompt(action, self._ai_context())
        if prompt is None:
            return
        label, message = prompt
        self._on_ai_send(message, display=label)

    def _on_ai_completed(self) -> None:
        self.ai_panel.finish_assistant()
        self.ai_panel.set_busy(False)
        self.ai_panel.set_status("Done")

    def _on_ai_error(self, message: str) -> None:
        self.ai_panel.finish_assistant()
        self.ai_panel.set_busy(False)
        if message == "cancelled":
            self.ai_panel.set_status("Cancelled")
            self._push_status("AI response cancelled")
        else:
            self.ai_panel.set_status(message, error=True)
            self._push_status("AI request failed")

    def _on_ai_cancel(self) -> None:
        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            self.ai_panel.set_status("Cancelling...")

    def _on_ai_clear(self) -> None:
        self.ai_panel.clear_chat()
        self._push_status("AI chat cleared")

    # --------------------------------------------------------- auto clips
    def _load_transcript(self, media_id: int) -> tuple[list, str]:
        if self.project is None:
            return [], ""
        database = ProjectDatabase(self.project.path)
        try:
            return TranscriptRepository(database).get(media_id) or ([], "")
        finally:
            database.close()

    def _refresh_auto_panel(self) -> None:
        if self.project is None or self.auto_panel.busy:
            return
        project_path = str(self.project.path)
        if self._auto_project_path != project_path:
            self._auto_project_path = project_path
            self.auto_panel.set_candidates([])
            self.auto_panel.set_status("")
        preferred = self.current_media.id if self.current_media else None
        self.auto_panel.set_media(self.project.media, preferred)
        self._refresh_auto_transcript_status()

    def _refresh_auto_transcript_status(self) -> None:
        media_id = self.auto_panel.selected_media_id()
        if self.project is None or media_id is None:
            self.auto_panel.set_transcript_status("No media selected.")
            return
        transcript, language = self._load_transcript(media_id)
        if transcript:
            lang = f"  ·  {language}" if language else ""
            self.auto_panel.set_transcript_status(
                f"Cached transcript: {len(transcript)} segments{lang}"
            )
        else:
            self.auto_panel.set_transcript_status(
                "No cached transcript - the first analysis transcribes "
                "the whole media (cached afterwards)."
            )

    def _on_auto_media_changed(self, _media_id: int) -> None:
        if not self.auto_panel.busy:
            self._refresh_auto_transcript_status()

    def _start_auto_analysis(self) -> None:
        if not self._require_project():
            return
        if self._auto_worker is not None and self._auto_worker.isRunning():
            self._push_status("Auto-clip analysis is already running")
            return
        media_id = self.auto_panel.selected_media_id()
        media = (
            self.project.media_by_id(media_id) if media_id is not None else None
        )
        if media is None:
            self.auto_panel.set_status("Select a media file first", error=True)
            return
        if media.duration <= 0:
            self.auto_panel.set_status(
                "Media duration unknown - reopen the project to probe it",
                error=True,
            )
            return
        min_len = self.auto_panel.min_len()
        max_len = self.auto_panel.max_len()
        if max_len <= min_len:
            self.auto_panel.set_status(
                "Max clip length must be greater than min", error=True
            )
            return
        source = media.resolve_path(self.project.directory)
        if not source.exists():
            source = Path(media.source_path)
        if not source.exists():
            self.auto_panel.set_status(
                f"Source file not found: {media.source_path}", error=True
            )
            return

        cached: list = []
        language = ""
        if not self.auto_panel.force_retranscribe():
            cached, language = self._load_transcript(media.id or 0)
        if not cached and not whisper_available():
            self.auto_panel.set_status(
                "faster-whisper is not installed and there is no cached "
                "transcript - run: python -m pip install faster-whisper",
                error=True,
            )
            return

        settings = self.settings_service.settings
        worker = AutoClipWorker(
            media_id=media.id or 0,
            media_path=source,
            media_duration=media.duration,
            cached_transcript=cached,
            cached_language=language,
            min_len=min_len,
            max_len=max_len,
            count=self.auto_panel.clip_count(),
            use_ai=self.auto_panel.use_ai(),
            ollama_url=settings.ollama_url,
            ollama_model=settings.ollama_model,
            whisper_model=settings.whisper_model,
            whisper_language=settings.whisper_language,
            download_root=cache_dir() / "whisper",
            ffmpeg=self.ffmpeg,
            parent=self,
        )
        worker.progress.connect(self._on_auto_progress)
        worker.completed.connect(self._on_auto_completed)
        worker.error.connect(self._on_auto_error)
        self._auto_worker = worker
        self._track_worker(worker)
        self.auto_panel.set_busy(True)
        self.auto_panel.set_status("Starting analysis...")
        worker.start()

    def _cancel_auto_analysis(self) -> None:
        if self._auto_worker is not None and self._auto_worker.isRunning():
            self._auto_worker.cancel()
            self.auto_panel.set_status("Cancelling...")

    def _on_auto_progress(self, message: str) -> None:
        self.auto_panel.set_status(message)

    def _on_auto_completed(self, payload: object) -> None:
        self.auto_panel.set_busy(False)
        data = payload if isinstance(payload, dict) else {}
        candidates = list(data.get("candidates") or [])
        if data.get("transcribed") and self.project is not None:
            media_id = self.auto_panel.selected_media_id()
            if media_id is not None:
                database = ProjectDatabase(self.project.path)
                try:
                    TranscriptRepository(database).upsert(
                        media_id,
                        list(data.get("transcript") or []),
                        str(data.get("language") or ""),
                    )
                finally:
                    database.close()
                self._refresh_auto_transcript_status()
        self.auto_panel.set_candidates(candidates)
        note = str(data.get("note") or "")
        if candidates:
            status = f"Found {len(candidates)} highlight(s)"
            if note:
                status += f" - {note}"
            self.auto_panel.set_status(status)
            self._push_status(f"Auto-clip: {len(candidates)} highlight(s) found")
        else:
            message = note or "No highlights found - try a wider min/max range"
            self.auto_panel.set_status(message, error=not note)
            self._push_status(message)

    def _on_auto_error(self, message: str) -> None:
        self.auto_panel.set_busy(False)
        if message == "cancelled":
            self.auto_panel.set_status("Cancelled")
            self._push_status("Auto-clip analysis cancelled")
            return
        self.auto_panel.set_status(message, error=True)
        log.warning("auto-clip analysis failed: %s", message)
        self._push_status("Auto-clip analysis failed")

    def _on_auto_preview(self, index: int) -> None:
        if self.project is None:
            return
        candidates = self.auto_panel.candidates
        if not (0 <= index < len(candidates)):
            return
        media_id = self.auto_panel.selected_media_id()
        media = (
            self.project.media_by_id(media_id) if media_id is not None else None
        )
        if media is None:
            return
        if media != self.current_media:
            self._activate_media(media)
        candidate = candidates[index]
        self._seek_seconds(candidate.start)
        self._push_status(
            f"Preview {format_clock(candidate.start)} - "
            f"{format_clock(candidate.end)}: {candidate.title}"
        )

    def _on_auto_add(self) -> None:
        if self.project is None:
            return
        candidates = self.auto_panel.checked_candidates()
        if not candidates:
            self._push_status("Check at least one highlight first")
            return
        media_id = self.auto_panel.selected_media_id()
        media = (
            self.project.media_by_id(media_id) if media_id is not None else None
        )
        if media is None:
            media = self.current_media
        if media is None:
            self._push_status("Select a media file first")
            return

        order = max((c.order for c in self.project.clips), default=-1)
        aspect = self.status_bar.aspect
        created: list[Clip] = []
        number = len(self.project.clips) + 1
        for candidate in candidates:
            start = max(0.0, candidate.start)
            end = max(start + MIN_CLIP_SECONDS, candidate.end)
            if media.duration > 0:
                start = min(start, max(0.0, media.duration - MIN_CLIP_SECONDS))
                end = min(end, media.duration)
                end = max(end, start + MIN_CLIP_SECONDS)
            order += 1
            timeline_start = self._next_timeline_position()
            clip = Clip(
                media_id=media.id or 0,
                name=f"Clip {number}",
                start=start,
                end=end,
                aspect=aspect,
                timeline_start=timeline_start,
                timeline_end=timeline_start + (end - start),
                order=order,
                created_at=utc_now(),
            )
            self._persist_clip_new(clip)
            self.project.clips.append(clip)
            created.append(clip)
            number += 1
            self._push_undo(
                {
                    "type": "clip_add",
                    "clip": clip.model_dump(),
                    "index": len(self.project.clips) - 1,
                    "label": "Add highlight",
                }
            )
        if not created:
            return
        self._refresh_timeline()
        self._refresh_clip_list()
        if created[0].id is not None:
            self._select_clip(created[0].id)
        self._push_status(f"Added {len(created)} highlight clip(s)")

    def _on_auto_clear(self) -> None:
        self.auto_panel.set_candidates([])
        self.auto_panel.set_status("")

    # --------------------------------------------------------- smart crop
    def _load_track(self, media_id: int):
        if self.project is None or media_id <= 0:
            return None
        database = ProjectDatabase(self.project.path)
        try:
            return TrackRepository(database).get(media_id)
        finally:
            database.close()

    def _load_track_keys(self, media_id: int) -> list[TrackKeyframe] | None:
        info = self._load_track(media_id)
        if info is None or not info.keyframes:
            return None
        return info.keyframes

    def _refresh_smart_panel(self) -> None:
        if self.project is None or self.smart_panel.busy:
            return
        preferred = self.current_media.id if self.current_media else None
        self.smart_panel.set_media(self.project.media, preferred)
        self._refresh_smart_track_status()

    def _refresh_smart_track_status(self) -> None:
        media_id = self.smart_panel.selected_media_id()
        if self.project is None or media_id is None:
            self.smart_panel.set_track_status("No media selected.")
            return
        info = self._load_track(media_id)
        if info is None or not info.keyframes:
            text = "No face track yet - press Track faces to analyze this media."
            if info is not None and info.hits == 0:
                text = "Last run found no faces - exports will center-crop."
            self.smart_panel.set_track_status(text)
            return
        percent = int(round(info.hits * 100 / max(1, info.frames)))
        self.smart_panel.set_track_status(
            f"Track: {len(info.keyframes)} keyframes  -  "
            f"{info.hits}/{info.frames} frames with a face ({percent}%, "
            f"{info.detector})"
        )

    def _on_smart_media_changed(self, _media_id: int) -> None:
        if not self.smart_panel.busy:
            self._refresh_smart_track_status()
            self._apply_smart_overlay()

    def _on_smart_overlay(self, checked: bool) -> None:
        if checked:
            self._apply_smart_overlay()
        else:
            self.player.clear_track_overlay()

    def _apply_smart_overlay(self) -> None:
        if not self.smart_panel.overlay_checked() or self.project is None:
            self.player.clear_track_overlay()
            return
        media_id = self.smart_panel.selected_media_id()
        media = (
            self.project.media_by_id(media_id) if media_id is not None else None
        )
        if media is None or not (media.width and media.height):
            self.player.clear_track_overlay()
            return
        keys = self._load_track_keys(media.id or 0)
        if not keys:
            self.player.clear_track_overlay()
            return
        self.player.set_track_overlay(
            keys, (media.width, media.height), self.status_bar.aspect
        )

    def _start_smart_analysis(self) -> None:
        if not self._require_project():
            return
        if self._smart_worker is not None and self._smart_worker.isRunning():
            self._push_status("Face tracking is already running")
            return
        ok, message = smart_availability()
        if not ok:
            self.smart_panel.set_status(message, error=True)
            return
        media_id = self.smart_panel.selected_media_id()
        media = (
            self.project.media_by_id(media_id) if media_id is not None else None
        )
        if media is None:
            self.smart_panel.set_status("Select a media file first", error=True)
            return
        if media.duration <= 0:
            self.smart_panel.set_status(
                "Media duration unknown - reopen the project to probe it",
                error=True,
            )
            return
        source = media.resolve_path(self.project.directory)
        if not source.exists():
            source = Path(media.source_path)
        if not source.exists():
            self.smart_panel.set_status(
                f"Source file not found: {media.source_path}", error=True
            )
            return
        worker = SmartTrackWorker(
            media_id=media.id or 0,
            media_path=source,
            duration=media.duration,
            parent=self,
        )
        worker.progress.connect(self._on_smart_progress)
        worker.completed.connect(self._on_smart_completed)
        worker.error.connect(self._on_smart_error)
        self._smart_worker = worker
        self._track_worker(worker)
        self.smart_panel.set_busy(True)
        self.smart_panel.set_status("Starting face tracking...")
        worker.start()

    def _cancel_smart_analysis(self) -> None:
        if self._smart_worker is not None and self._smart_worker.isRunning():
            self._smart_worker.cancel()
            self.smart_panel.set_status("Cancelling...")

    def _on_smart_progress(self, message: str) -> None:
        self.smart_panel.set_status(message)

    def _on_smart_completed(self, payload: object) -> None:
        self.smart_panel.set_busy(False)
        data = payload if isinstance(payload, dict) else {}
        media_id = int(
            data.get("media_id") or self.smart_panel.selected_media_id() or 0
        )
        keyframes: list[TrackKeyframe] = []
        for entry in data.get("keyframes") or []:
            try:
                keyframes.append(TrackKeyframe(**entry))
            except (TypeError, ValueError):
                continue
        hits = int(data.get("hits") or 0)
        if self.project is not None and media_id > 0:
            database = ProjectDatabase(self.project.path)
            try:
                TrackRepository(database).upsert(
                    media_id,
                    keyframes,
                    detector=str(data.get("detector") or ""),
                    frames=int(data.get("frames") or 0),
                    hits=hits,
                    width=int(data.get("width") or 0),
                    height=int(data.get("height") or 0),
                )
            finally:
                database.close()
        if hits:
            message = f"Tracked {len(keyframes)} keyframes ({hits} face hits)"
        else:
            message = "No faces found - exports will center-crop"
        self.smart_panel.set_status(message)
        self._refresh_smart_track_status()
        self._apply_smart_overlay()
        self._push_status(f"Smart crop: {message}")

    def _on_smart_error(self, message: str) -> None:
        self.smart_panel.set_busy(False)
        if "cancel" in message.lower():
            self.smart_panel.set_status("Cancelled")
            self._push_status("Face tracking cancelled")
        else:
            self.smart_panel.set_status(message, error=True)
            log.warning("face tracking failed: %s", message)

    def _on_smart_clear(self) -> None:
        if not self._require_project():
            return
        media_id = self.smart_panel.selected_media_id()
        if media_id is None:
            self.smart_panel.set_status("No media selected.", error=True)
            return
        database = ProjectDatabase(self.project.path)
        try:
            TrackRepository(database).delete_for_media(media_id)
        finally:
            database.close()
        self.smart_panel.set_status("Track cleared")
        self._refresh_smart_track_status()
        self._apply_smart_overlay()
        self._push_status("Face track cleared")

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
        if not self._confirm_discard():
            event.ignore()
            return
        if self._transcribe_worker is not None and self._transcribe_worker.isRunning():
            self._transcribe_worker.cancel()
            self._transcribe_worker.wait(4000)
        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            self._ai_worker.wait(3000)
        if self._auto_worker is not None and self._auto_worker.isRunning():
            self._auto_worker.cancel()
            self._auto_worker.wait(4000)
        if self._smart_worker is not None and self._smart_worker.isRunning():
            self._smart_worker.cancel()
            self._smart_worker.wait(4000)
        self.queue_worker.shutdown()
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

