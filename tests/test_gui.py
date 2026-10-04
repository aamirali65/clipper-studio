from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.media import MediaKind, MediaItem  # noqa: E402
from app.models.project import utc_now  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.services.video_service import VideoService  # noqa: E402

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "GuiTestProject"


def make_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    from app.ui.theme import APP_STYLESHEET

    app.setStyleSheet(APP_STYLESHEET)
    return app


def main() -> None:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first to create the sample"
    app = make_app()

    for old in sorted((ROOT / "projects").glob(PROJECT_NAME), reverse=True):
        shutil.rmtree(old, ignore_errors=True)

    window = MainWindow()
    window.show()
    app.processEvents()
    window._confirm_discard = lambda: True  # avoid modal dialogs in headless run

    # --- create project through the real service ---
    project = window.project_service.create_project(PROJECT_NAME)
    window._load_project(project, "Project created")
    app.processEvents()
    assert window.project is not None
    assert window.stack.currentWidget() is not window.dashboard, "editor page not shown"
    print("PASS create + open project (editor page active)")

    # --- import local media through the probe path ---
    info = VideoService().probe(SAMPLE)
    thumb = VideoService().thumbnail(SAMPLE, project.thumbnails_dir / "sample.jpg")
    info.thumbnail = str(thumb)
    assert thumb.exists() and thumb.stat().st_size > 0, "thumbnail not generated"
    print(f"PASS thumbnail generation: {thumb.name} ({thumb.stat().st_size} bytes)")

    window._on_probe_completed(info)
    app.processEvents()
    assert len(project.media) == 1, project.media
    media = project.media[0]
    assert media.id is not None
    assert window.media_panel.items(), "media panel empty"
    print(f"PASS import local video: {media.name} ({media.duration:.2f}s)")

    # media activation created a default clip and loaded the player
    assert window.current_media is not None
    assert window.current_clip is not None, "no clip created on load"
    assert window.duration == media.duration
    assert window.player.current_source, "player has no source"
    assert abs(window.timeline.clip_end - media.duration) < 0.01
    print("PASS media activated: player source + timeline loaded")

    # --- playback: play/pause/seek through real QMediaPlayer ---
    window.player.seek_seconds(10.0)
    app.processEvents()
    assert abs(window.player.position_ms / 1000 - 10.0) < 1.0, window.player.position_ms
    window.player.play()
    app.processEvents()
    window.player.pause()
    app.processEvents()
    print(f"PASS play/seek: position={window.player.position_ms}ms "
          f"duration={window.player.duration_ms}ms")

    # --- timeline in/out editing ---
    window.timeline.set_range(5.0, 12.5)
    window._apply_timeline_range(5.0, 12.5)
    app.processEvents()
    assert abs(window.current_clip.start - 5.0) < 1e-6
    assert abs(window.current_clip.end - 12.5) < 1e-6
    assert "00:00:05.000" in window.inspector.start_edit.text()
    assert "00:00:12.500" in window.inspector.end_edit.text()
    assert "00:00:07.500" in window.inspector.duration_label.text()
    print("PASS timeline range -> inspector (start/end/duration)")

    # --- inspector timecode editing (validation) ---
    window.inspector.start_edit.setText("00:00:08.000")
    window.inspector.end_edit.setText("00:00:04.000")
    window.inspector._apply_time_edits()
    assert window.inspector.error_label.isVisible() or window.inspector.error_label.text()
    window.inspector.end_edit.setText("00:00:20.000")
    window.inspector._apply_time_edits()
    assert window.inspector.error_label.text() == ""
    assert abs(window.current_clip.start - 8.0) < 1e-6
    assert abs(window.current_clip.end - 20.0) < 1e-6
    print("PASS inspector validation (rejects end<=start, accepts valid)")

    # --- aspect ratio ---
    window._on_aspect_changed("1:1")
    app.processEvents()
    assert window.status_bar.aspect == "1:1"
    assert window.current_clip.aspect == "1:1"
    window._on_aspect_changed("9:16")
    assert window.current_clip.aspect == "9:16"
    print("PASS aspect ratio change propagates to clip + status bar")

    # --- shortcuts (I/O set in/out at playhead) ---
    window.player.seek_seconds(3.0)
    window._shortcut_set_in()
    window.player.seek_seconds(9.0)
    window._shortcut_set_out()
    app.processEvents()
    assert abs(window.current_clip.start - 3.0) < 0.1, window.current_clip.start
    assert abs(window.current_clip.end - 9.0) < 0.1, window.current_clip.end
    print("PASS I/O shortcuts set in/out points")

    # --- second clip creation ---
    window.add_clip_from_range()
    app.processEvents()
    assert len(project.clips) == 2, len(project.clips)
    print("PASS new clip from range")

    # --- save + reopen ---
    window.save_project()
    app.processEvents()
    path = project.path
    reopened = window.project_service.open_project(path)
    assert len(reopened.media) == 1
    assert len(reopened.clips) == 2
    persisted = sorted((c.start, c.end) for c in reopened.clips)
    expected = sorted((c.start, c.end) for c in project.clips)
    assert persisted == expected, (persisted, expected)
    print("PASS save + reopen persists media, clips, ranges")

    # --- reopen into the window ---
    window.open_project_path(str(path))
    app.processEvents()
    assert window.project is not None
    assert len(window.project.clips) == 2
    assert window.current_clip is not None
    assert window.current_media is not None
    print("PASS reopen project into window (state restored)")

    # --- export through the real ExportWorker (no dialog) ---
    from app.services.export_service import ExportRequest
    from app.workers.export_worker import ExportWorker

    clip = window.current_clip
    clip.start, clip.end = 5.0, 12.5
    request = ExportRequest(
        source=SAMPLE,
        output=project.exports_dir / "gui_export.mp4",
        start=5.0,
        end=12.5,
        aspect="9:16",
        settings=project.export_settings,
    )
    worker = ExportWorker(request)
    events: list[float] = []
    worker.progress.connect(lambda p, d: events.append(p))
    results: list[str] = []
    errors: list[str] = []
    worker.completed.connect(results.append)
    worker.error.connect(errors.append)
    worker.start()
    while not worker.isFinished():
        app.processEvents()
        worker.wait(50)
    # queued cross-thread signals are delivered on the next event loop pass
    for _ in range(10):
        app.processEvents()
        worker.wait(20)
    assert not errors, errors
    assert results, "export produced no result"
    out = Path(results[0])
    assert out.exists() and out.stat().st_size > 0
    out_info = VideoService().probe(out)
    assert (out_info.width, out_info.height) == (1080, 1920)
    assert abs(out_info.duration - 7.5) < 0.4
    assert events and max(events) == 100.0
    print(f"PASS ExportWorker: {out.name} {out_info.width}x{out_info.height} "
          f"{out_info.duration:.2f}s ({len(events)} progress events)")

    # --- remove media/clip without dialogs (repo level) ---
    from app.database.repositories import ProjectDatabase, ClipRepository

    count_before = len(window.project.clips)
    doomed = window.project.clips[-1]
    db = ProjectDatabase(window.project.path)
    ClipRepository(db).delete(doomed.id)
    db.close()
    window.project.clips = [c for c in window.project.clips if c.id != doomed.id]
    window._refresh_clip_list()
    assert len(window.project.clips) == count_before - 1
    print("PASS clip removal + list refresh")

    # --- recent projects ---
    recent = window.project_service.recent_projects()
    assert any(r.name == PROJECT_NAME for r in recent)
    print(f"PASS recent projects registry ({len(recent)} entries)")

    # --- youtube metadata preview worker (real network) ---
    try:
        window._check_youtube_metadata("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        import time

        deadline = time.time() + 60
        while time.time() < deadline:
            text = window.media_panel.meta_preview.text()
            if text and text != "Checking URL…":
                break
            app.processEvents()
            time.sleep(0.1)
        for _ in range(5):
            app.processEvents()
            time.sleep(0.05)
        preview = window.media_panel.meta_preview.text()
        assert "Me at the zoo" in preview, f"unexpected preview: {preview!r}"
        print(f"PASS youtube metadata preview: {preview}")
    except AssertionError as exc:
        if "network" in str(exc).lower() or "unreachable" in str(exc).lower():
            print(f"SKIP youtube metadata (offline): {exc}")
        else:
            raise

    window.close()
    app.processEvents()
    print("\nALL GUI TESTS PASSED")


if __name__ == "__main__":
    main()
