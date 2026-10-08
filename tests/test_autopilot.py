from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.database.database import ProjectDatabase  # noqa: E402
from app.database.repositories import MediaRepository, TranscriptRepository  # noqa: E402
from app.models.caption import CaptionSegment  # noqa: E402
from app.models.media import MediaItem, MediaKind  # noqa: E402
from app.models.project import utc_now  # noqa: E402
from app.models.queue import JOB_DONE, JOB_ERROR, JOB_RUNNING, ExportJob  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.video_service import VideoService  # noqa: E402

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "AutopilotTestProject"


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


def wait_thread(app: QApplication, thread, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while thread.isRunning() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def make_project() -> tuple[ProjectService, object]:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(PROJECT_NAME)
    project.ensure_dirs()

    info = VideoService().probe(SAMPLE)
    media = MediaItem(
        name="autopilot_source",
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

    # seed the transcript cache so analysis never needs whisper
    database = ProjectDatabase(project.path)
    try:
        TranscriptRepository(database).upsert(
            media.id or 0, make_segments(), "en"
        )
    finally:
        database.close()
    return service, project


def test_gui_autopilot_page() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True

    assert window.pages.count() == 11, window.pages.count()
    assert window.sidebar._buttons["autopilot"].isEnabled()
    window.sidebar.select("autopilot")
    assert window.pages.currentIndex() == 10

    service, project = make_project()
    try:
        window._load_project(project, "loaded")
        window.sidebar.select("autopilot")
        panel = window.autopilot_panel
        assert panel.media_box.count() == 1
        assert panel.busy is False
        assert panel.track_box.isEnabled(), "face model should be available"
        assert panel.export_when_done()

        # idle cancel is harmless
        window._cancel_autopilot()

        # start-guard: a fabricated active run blocks a second start
        window._autopilot = {"stage": "analyze"}
        window._start_autopilot()
        assert window._autopilot == {"stage": "analyze"}, "double start blocked"
        window._autopilot = None

        # full run without track/export: analyze (cached) -> clips -> report
        clips_before = len(project.clips)
        panel.export_box.setChecked(False)
        panel.track_box.setChecked(False)
        panel.count_box.setValue(3)
        panel.min_box.setValue(5)
        panel.max_box.setValue(20)

        window._start_autopilot()
        assert panel.busy is True
        assert window._autopilot is not None
        assert window._autopilot["media_id"] == project.media[0].id, (
            "local run must carry the media id for transcript/track saves"
        )
        assert panel.stage_text(0).startswith(">"), panel.stage_text(0)
        worker = window._autopilot_worker
        assert worker is not None
        wait_thread(app, worker, 60.0)

        deadline = time.time() + 15.0
        while panel.busy and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()

        assert not panel.busy, "autopilot run finished"
        assert window._autopilot is None
        assert window._autopilot_worker is None

        report = panel.report_label.text()
        assert "Autopilot done" in report, report
        assert "Exports skipped" in report, report
        assert len(project.clips) > clips_before, "clips were added"

        stage0 = panel.stage_text(0)
        assert stage0.startswith("+") and "clips" in stage0, stage0
        assert "cached transcript" in stage0, stage0
        assert panel.stage_text(1).startswith("~"), panel.stage_text(1)
        assert panel.stage_text(2).startswith("~"), panel.stage_text(2)

        # analysis result was cached for the AUTO page too
        cached = window._load_transcript(project.media[0].id or 0)
        assert cached is not None and cached[0]
    finally:
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        _ = service
    print("PASS gui: autopilot page + full analyze-only run (cached transcript)")


def _make_job(job_id: int, status: str) -> ExportJob:
    return ExportJob(
        id=job_id,
        clip_name=f"Clip {job_id}",
        output=str(ROOT / "output" / f"autopilot_{job_id}.mp4"),
        status=status,
    )


def test_autopilot_queue_tracking() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True

    service, project = make_project()
    original_snapshot = window.queue_worker.jobs_snapshot
    try:
        window._load_project(project, "loaded")
        panel = window.autopilot_panel
        media = project.media[0]

        def run_state(job_ids: list[int]) -> dict:
            return {
                "media": media,
                "media_id": media.id or 0,
                "source": SAMPLE,
                "track": False,
                "export": True,
                "exported_track": True,
                "created": [object(), object()],
                "job_ids": job_ids,
                "early_errors": 0,
                "stage": "waiting",
            }

        # one running + one done -> still waiting, progress shown
        window.queue_worker.jobs_snapshot = lambda: [
            _make_job(1, JOB_RUNNING),
            _make_job(2, JOB_DONE),
        ]
        window._autopilot = run_state([1, 2])
        panel.set_busy(True)
        panel.reset_stages()
        window._autopilot_check_queue()
        assert window._autopilot is not None, "waits for the running job"
        assert panel.stage_text(2).startswith(">"), panel.stage_text(2)
        assert "1/2 finished" in panel.stage_text(2), panel.stage_text(2)

        # all terminal, one failed -> finished-with-errors report
        window.queue_worker.jobs_snapshot = lambda: [
            _make_job(1, JOB_DONE),
            _make_job(2, JOB_ERROR),
        ]
        window._autopilot_check_queue()
        assert window._autopilot is None
        report = panel.report_label.text()
        assert "finished with errors" in report, report
        assert "1 failed" in report, report
        assert panel.stage_text(2).startswith("x"), panel.stage_text(2)
        assert not panel.busy

        # queue cleared mid-run -> failure report
        window._autopilot = run_state([3])
        panel.set_busy(True)
        panel.reset_stages()
        window.queue_worker.jobs_snapshot = lambda: []
        window._autopilot_check_queue()
        assert window._autopilot is None
        assert "cleared" in panel.report_label.text()
        assert panel.stage_text(2).startswith("x")
        assert not panel.busy

        # all exported -> success report with track note
        window._autopilot = run_state([4, 5])
        panel.set_busy(True)
        panel.reset_stages()
        window.queue_worker.jobs_snapshot = lambda: [
            _make_job(4, JOB_DONE),
            _make_job(5, JOB_DONE),
        ]
        window._autopilot_check_queue()
        assert window._autopilot is None
        report = panel.report_label.text()
        assert "Autopilot done" in report and "2 exported" in report, report
        assert "face track saved" in report, report
        assert panel.stage_text(2).startswith("+"), panel.stage_text(2)
        assert not panel.busy
    finally:
        window.queue_worker.jobs_snapshot = original_snapshot
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        _ = service
        _ = app
    print("PASS queue tracking: progress, errors, cleared, success reports")


def test_enqueue_smart_flag() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True
    service, project = make_project()
    calls: list[bool] = []
    original = window.queue_worker.enqueue_clip
    try:
        window._load_project(project, "loaded")
        clip = window.current_clip
        assert clip is not None

        def fake_enqueue(project_arg, clip_arg, output_dir="", captions_srt="",
                         smart_crop=False, **kwargs):
            calls.append(bool(smart_crop))
            return ExportJob(id=77, clip_name=clip_arg.name)

        window.queue_worker.enqueue_clip = fake_enqueue

        jobs = window._enqueue_clips([clip], smart_crop=True)
        assert len(jobs) == 1 and calls[-1] is True

        jobs = window._enqueue_clips([clip])  # None -> settings default
        assert len(jobs) == 1
        assert calls[-1] == window.settings_service.settings.smart_crop

        jobs = window._enqueue_clips([clip], smart_crop=False)
        assert calls[-1] is False
    finally:
        window.queue_worker.enqueue_clip = original
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        _ = service
        _ = app
    print("PASS enqueue: smart_crop override + settings default")


def main() -> None:
    _ = QApplication.instance() or QApplication([])
    test_gui_autopilot_page()
    test_autopilot_queue_tracking()
    test_enqueue_smart_flag()
    print("ALL AUTOPILOT TESTS PASSED")


if __name__ == "__main__":
    main()
