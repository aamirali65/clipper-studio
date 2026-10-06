from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.database.database import ProjectDatabase  # noqa: E402
from app.database.repositories import (  # noqa: E402
    CaptionRepository,
    MediaRepository,
    TranscriptRepository,
)
from app.models.caption import CaptionSegment  # noqa: E402
from app.models.media import MediaItem, MediaKind  # noqa: E402
from app.models.project import utc_now  # noqa: E402
from app.models.queue import ExportJob  # noqa: E402
from app.services.caption_service import parse_srt  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.video_service import VideoService  # noqa: E402

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "TikTokTestProject"


def make_segments(count: int = 60, step: float = 2.3, dur: float = 2.0) -> list[CaptionSegment]:
    segments = []
    t = 0.0
    for i in range(count):
        segments.append(
            CaptionSegment(
                clip_id=0,
                start=t,
                end=t + dur,
                text=f"How you can make money fast with number {i} is simple.",
            )
        )
        t += step
    return segments


def make_project() -> tuple[ProjectService, object]:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(PROJECT_NAME)
    project.ensure_dirs()

    info = VideoService().probe(SAMPLE)
    media = MediaItem(
        name="tiktok_source",
        kind=MediaKind.local,
        source_path=str(Path(info.path).resolve()),
        duration=info.duration,
        width=info.width,
        height=info.height,
        size_bytes=info.size_bytes,
        created_at=utc_now(),
    )
    database = ProjectDatabase(project.path)
    try:
        MediaRepository(database).add(media, project.directory)
    finally:
        database.close()
    project.media.append(media)

    database = ProjectDatabase(project.path)
    try:
        TranscriptRepository(database).upsert(
            media.id or 0, make_segments(), "en"
        )
    finally:
        database.close()
    return service, project


def test_phone_geometry_and_overlay() -> None:
    from app.ui.video_player import VideoPlayer, phone_geometry

    # wide widget: 9:16 screen centered, body contains it and fits the widget
    screen, body = phone_geometry(1000, 600, "9:16")
    sx, sy, sw, sh = screen
    bx, by, bw, bh = body
    assert abs(sw / sh - 9 / 16) < 1e-6, (sw, sh)
    assert abs(sx + sw / 2 - 500) < 1e-6 and abs(sy + sh / 2 - 300) < 1e-6
    assert bx <= sx and by <= sy
    assert bx + bw >= sx + sw and by + bh >= sy + sh
    assert bx >= -1e-6 and by >= -1e-6
    assert bx + bw <= 1000 + 1e-6 and by + bh <= 600 + 1e-6

    # tall widget + other ratios
    screen, _ = phone_geometry(400, 800, "9:16")
    assert abs(screen[2] / screen[3] - 9 / 16) < 1e-6
    screen, _ = phone_geometry(1000, 600, "16:9")
    assert abs(screen[2] / screen[3] - 16 / 9) < 1e-6

    app = QApplication.instance() or QApplication([])
    player = VideoPlayer()
    assert not player.phone_preview
    player.set_aspect("16:9", True)
    assert player.overlay.active

    player.set_phone_preview(True)
    assert player.phone_preview
    assert not player.overlay.active, "aspect mask hidden behind the mockup"

    # aspect changes while the phone preview is on stay phone-only
    player.set_aspect("9:16")
    assert player.phone_preview and not player.overlay.active

    player.set_phone_preview(False)
    assert not player.phone_preview
    assert player.overlay.active, "aspect mask restored after phone preview"

    # painting the mockup must not crash offscreen
    player.set_phone_preview(True)
    player.resize(640, 360)
    shot = player.grab()
    assert not shot.isNull()
    print("PASS phone geometry + overlay paint + toggle restore")


def test_clip_local_srt_and_derive() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True
    service, project = make_project()
    try:
        window._load_project(project, "loaded")
        clip = window.current_clip
        assert clip is not None and clip.id is not None
        # a clip in the middle of the source: rebase must kick in
        clip.start = 10.0
        clip.end = 15.0

        absolute = [
            CaptionSegment(clip_id=0, start=2.0, end=4.0, text="before clip"),
            CaptionSegment(clip_id=0, start=10.5, end=12.0, text="Hello"),
            CaptionSegment(clip_id=0, start=12.0, end=13.5, text="World"),
            CaptionSegment(clip_id=0, start=14.5, end=16.0, text="tail"),
        ]
        database = ProjectDatabase(project.path)
        try:
            CaptionRepository(database).replace_for_clip(clip.id, absolute)
        finally:
            database.close()

        srt_path = window._write_clip_srt(clip)
        assert srt_path is not None and srt_path.exists()
        parsed = parse_srt(srt_path.read_text(encoding="utf-8"))
        assert [text for _start, _end, text in parsed] == [
            "Hello",
            "World",
            "tail",
        ]
        assert abs(parsed[0][0] - 0.5) < 1e-6, parsed[0]
        assert abs(parsed[1][0] - 2.0) < 1e-6, parsed[1]
        assert abs(parsed[2][0] - 4.5) < 1e-6, parsed[2]
        assert abs(parsed[2][1] - 5.0) < 1e-6, parsed[2]

        # derive: only the parts inside the clip, stored on absolute times
        transcript = [
            CaptionSegment(clip_id=0, start=0.0, end=3.0, text="before"),
            CaptionSegment(clip_id=0, start=11.0, end=12.5, text="inside"),
            CaptionSegment(clip_id=0, start=16.0, end=18.0, text="after"),
        ]
        saved = window._derive_clip_captions(transcript, [clip])
        assert saved == 1, saved
        database = ProjectDatabase(project.path)
        try:
            stored = CaptionRepository(database).for_clip(clip.id)
        finally:
            database.close()
        assert [s.text for s in stored] == ["inside"]
        assert abs(stored[0].start - 11.0) < 1e-6, "storage stays absolute"
    finally:
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        _ = service
    print("PASS clip-local SRT rebase + transcript derive")


class FakeDownloadWorker(QObject):
    progress = Signal(float, str)
    completed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, url, target_dir, service=None, **_kwargs):
        super().__init__()
        self.url = url
        self.cancelled = False
        self._running = False

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self.cancelled = True
        self._running = False


class FakeAutoClipWorker(QObject):
    progress = Signal(str)
    completed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs

    def start(self) -> None:
        pass

    def isRunning(self) -> bool:
        return False

    def cancel(self) -> None:
        pass


def test_autopilot_url_to_clips() -> None:
    from app.ui import main_window as mw
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True
    service, project = make_project()
    original_download = mw.DownloadWorker
    original_autoclip = mw.AutoClipWorker
    try:
        window._load_project(project, "loaded")
        panel = window.autopilot_panel
        assert panel.clip_count() == 10, "TikTok default: 10 clips"
        assert panel.aspect() == "9:16", "TikTok default: 9:16"
        assert panel.burn_captions(), "captions default on"
        assert panel.youtube_url() == ""

        panel.track_box.setChecked(False)
        panel.export_box.setChecked(False)
        panel.count_box.setValue(4)
        panel.min_box.setValue(5)
        panel.max_box.setValue(20)
        panel.aspect_box.setCurrentIndex(panel.aspect_box.findData("1:1"))

        # invalid link: refused locally, no run started
        panel.url_box.setText("https://example.com/not-a-youtube-link")
        window._start_autopilot()
        assert window._autopilot is None
        assert not panel.busy
        assert panel.status_label.text(), "error message shown"
        assert "#e57373" in panel.status_label.styleSheet()

        mw.DownloadWorker = FakeDownloadWorker
        mw.AutoClipWorker = FakeAutoClipWorker

        # valid link: download stage first
        panel.url_box.setText("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        window._start_autopilot()
        run = window._autopilot
        assert run is not None and run["stage"] == "download"
        assert panel.busy
        assert panel.stage_text(0).startswith(">")
        assert "download" in panel.stage_text(0).lower()
        fake_download = window._download_worker
        assert isinstance(fake_download, FakeDownloadWorker)

        # cancel during download -> cancelled report
        window._cancel_autopilot()
        assert fake_download.cancelled
        window._on_download_error("cancelled")
        assert window._autopilot is None
        assert not panel.busy
        assert "cancelled during download" in panel.report_label.text().lower()

        # download completes -> analysis starts on the imported media
        panel.url_box.setText("https://youtu.be/abcDEF12345")
        window._start_autopilot()
        assert window._autopilot["stage"] == "download"
        downloaded = ROOT / "output" / "youtube_import.mp4"
        shutil.copy(SAMPLE, downloaded)
        item = MediaItem(
            name="youtube_import",
            kind=MediaKind.local,
            source_path=str(downloaded.resolve()),
            duration=30.0,
            width=640,
            height=360,
            size_bytes=downloaded.stat().st_size,
            created_at=utc_now(),
        )
        window._on_download_completed(item)
        run = window._autopilot
        assert run is not None
        assert run["stage"] == "analyze", run["stage"]
        assert panel.media_box.count() == 2, panel.media_box.count()
        assert panel.selected_media_id() == run["media_id"]
        assert window.status_bar.aspect == "1:1", "preview follows run ratio"
        worker = window._autopilot_worker
        assert isinstance(worker, FakeAutoClipWorker)
        assert worker.kwargs["media_duration"] == 30.0

        # analysis result -> 4 clips at 1:1 with derived captions
        clips_before = len(project.clips)
        payload = {
            "candidates": [
                SimpleNamespace(start=1.0, end=8.0),
                SimpleNamespace(start=9.0, end=16.0),
                SimpleNamespace(start=17.0, end=24.0),
                SimpleNamespace(start=25.0, end=30.0),
            ],
            "transcribed": False,
            "transcript": make_segments(),
            "language": "en",
            "note": "",
        }
        worker.completed.emit(payload)
        app.processEvents()

        created = project.clips[clips_before:]
        assert len(created) == 4, len(created)
        assert all(clip.aspect == "1:1" for clip in created)
        stage0 = panel.stage_text(0)
        assert stage0.startswith("+"), stage0
        assert "captioned" in stage0, stage0

        # captions written for the new clips -> clip-local SRT
        last_clip = created[-1]
        srt_path = window._write_clip_srt(last_clip)
        assert srt_path is not None and srt_path.exists()
        parsed = parse_srt(srt_path.read_text(encoding="utf-8"))
        assert parsed, "captions inside the clip"
        duration = last_clip.end - last_clip.start
        for start, end, _text in parsed:
            assert 0.0 <= start < end <= duration + 1e-6

        assert window._autopilot is None
        assert not panel.busy
        report = panel.report_label.text()
        assert "Autopilot done" in report and "Exports skipped" in report, report

        # status-bar phone toggle wires to the player and forces 9:16
        window.status_bar.phone_button.setChecked(True)
        assert window.player.phone_preview
        assert window.status_bar.aspect == "9:16"
        window.status_bar.phone_button.setChecked(False)
        assert not window.player.phone_preview

        # busy disables the new inputs
        panel.set_busy(True)
        assert not panel.url_box.isEnabled()
        assert not panel.aspect_box.isEnabled()
        assert not panel.captions_box.isEnabled()
        panel.set_busy(False)
        assert panel.url_box.isEnabled()
    finally:
        mw.DownloadWorker = original_download
        mw.AutoClipWorker = original_autoclip
        window.queue_worker.shutdown(2000)
        window.player.clear()
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        try:
            (ROOT / "output" / "youtube_import.mp4").unlink(missing_ok=True)
        except OSError:
            pass  # preview may still hold the file briefly
        _ = service
    print("PASS url flow: validate -> download -> analyze -> 1:1 clips + captions")


def test_enqueue_burn_flag() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True
    service, project = make_project()
    original = window.queue_worker.enqueue_clip
    captured: list[str] = []
    try:
        window._load_project(project, "loaded")
        clip = window.current_clip
        assert clip is not None and clip.id is not None

        database = ProjectDatabase(project.path)
        try:
            CaptionRepository(database).replace_for_clip(
                clip.id,
                [
                    CaptionSegment(
                        clip_id=0,
                        start=clip.start + 0.5,
                        end=clip.start + 1.5,
                        text="queued caption",
                    )
                ],
            )
        finally:
            database.close()

        def fake_enqueue(project_arg, clip_arg, output_dir="", captions_srt="",
                         smart_crop=False, **kwargs):
            captured.append(str(captions_srt or ""))
            return ExportJob(id=88, clip_name=clip_arg.name)

        window.queue_worker.enqueue_clip = fake_enqueue

        jobs = window._enqueue_clips([clip], burn=True)
        assert len(jobs) == 1
        assert captured[-1], "burn=True writes an SRT"
        assert Path(captured[-1]).exists()

        jobs = window._enqueue_clips([clip], burn=False)
        assert len(jobs) == 1
        assert captured[-1] == "", "burn=False skips captions"

        jobs = window._enqueue_clips([clip])
        expected = window.settings_service.settings.burn_captions
        assert bool(captured[-1]) is bool(expected)
    finally:
        window.queue_worker.enqueue_clip = original
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        _ = service
        _ = app
    print("PASS enqueue: burn override + settings default")


def main() -> None:
    _ = QApplication.instance() or QApplication([])
    test_phone_geometry_and_overlay()
    test_clip_local_srt_and_derive()
    test_autopilot_url_to_clips()
    test_enqueue_burn_flag()
    print("ALL TIKTOK/PHASE9 TESTS PASSED")


if __name__ == "__main__":
    main()
