from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.clip import Clip, ExportSettings  # noqa: E402
from app.models.settings import AppSettings  # noqa: E402
from app.services.export_queue import ExportQueueWorker  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402
from app.services.settings_service import SettingsService  # noqa: E402
from app.services.video_service import VideoService  # noqa: E402
from app.models.project import utc_now  # noqa: E402

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "QueueTestProject"


def test_settings_roundtrip() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="clipper_settings_"))
    try:
        path = tmp / "settings.json"
        service = SettingsService(path)
        assert service.settings == AppSettings(), "fresh defaults"

        service.settings.default_aspect = "16:9"
        service.settings.default_preset = "high"
        service.settings.output_dir = str(tmp / "exports")
        service.save()

        reloaded = SettingsService(path)
        assert reloaded.settings.default_aspect == "16:9"
        assert reloaded.settings.default_preset == "high"
        assert reloaded.settings.output_dir.endswith("exports")

        # corrupt file -> defaults, no crash
        path.write_text("{not json", encoding="utf-8")
        broken = SettingsService(path)
        assert broken.settings == AppSettings(), "corrupt file falls back"

        # unknown fields / wrong types -> defaults
        path.write_text(json.dumps({"default_aspect": 123}), encoding="utf-8")
        invalid = SettingsService(path)
        assert invalid.settings.default_aspect == "9:16"
        print("PASS settings: save/load/corrupt fallback")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_project():
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(PROJECT_NAME)
    project.ensure_dirs()

    info = VideoService().probe(SAMPLE)
    from app.models.media import MediaItem, MediaKind

    media = MediaItem(
        name="queue_source",
        kind=MediaKind.local,
        source_path=str(Path(info.path).resolve()),
        duration=info.duration,
        width=info.width,
        height=info.height,
        size_bytes=info.size_bytes,
        created_at=utc_now(),
    )
    from app.database.database import ProjectDatabase
    from app.database.repositories import MediaRepository

    database = ProjectDatabase(project.path)
    try:
        MediaRepository(database).add(media, project.directory)
    finally:
        database.close()
    project.media.append(media)

    clip = Clip(
        media_id=media.id or 0,
        name="Queue Clip A",
        start=0.0,
        end=2.0,
        aspect="9:16",
        timeline_start=0.0,
        timeline_end=2.0,
        order=0,
        created_at=utc_now(),
    )
    from app.database.database import ProjectDatabase as DB
    from app.database.repositories import ClipRepository

    database = DB(project.path)
    try:
        ClipRepository(database).add(clip)
    finally:
        database.close()
    project.clips.append(clip)
    return service, project


def wait_for_empty(worker: ExportQueueWorker, timeout: float = 90.0) -> tuple[int, int]:
    app = QApplication.instance()
    result: list[tuple[int, int]] = []
    worker.queueEmpty.connect(lambda d, e: result.append((d, e)))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if result:
            return result[0]
        if app is not None:
            app.processEvents()
        time.sleep(0.02)
    statuses = [(j.id, j.status, j.error) for j in worker.jobs_snapshot()]
    raise AssertionError(f"queue did not drain in time: {statuses}")


def test_queue_logic() -> None:
    service, project = make_project()
    try:
        worker = ExportQueueWorker()
        fast = ExportSettings(preset="fast", crf=26)

        job1 = worker.enqueue_clip(
            project, project.clips[0], settings=fast, autostart=False
        )
        job2 = worker.enqueue_clip(
            project, project.clips[0], settings=fast, autostart=False
        )
        assert job1.status == "queued"
        assert job1.output != job2.output, "duplicate clip needs unique output"
        assert worker.counts()["queued"] == 2

        snapshot = worker.jobs_snapshot()
        assert [j.id for j in snapshot] == [job1.id, job2.id]
        snapshot[0].progress = 0.5
        assert worker.jobs_snapshot()[0].progress == 0.0, "snapshot is a copy"

        worker.cancel_job(job1.id)
        assert worker.jobs_snapshot()[0].status == "cancelled"

        removed = worker.clear_finished()
        assert removed == 1
        assert len(worker.jobs_snapshot()) == 1

        # missing source -> immediate error job, never queued
        bad_clip = project.clips[0].model_copy(update={"media_id": 9999})
        error_job = worker.enqueue_clip(
            project, bad_clip, settings=fast, autostart=False
        )
        assert error_job.status == "error"
        assert error_job.error

        worker.cancel_all()
        assert all(j.status == "cancelled" for j in worker.jobs_snapshot())
        print("PASS queue logic: enqueue/unique output/cancel/clear/error")
    finally:
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def test_queue_end_to_end() -> None:
    service, project = make_project()
    try:
        fast = ExportSettings(preset="fast", crf=26)
        worker = ExportQueueWorker()

        job1 = worker.enqueue_clip(project, project.clips[0], settings=fast)
        clip_b = project.clips[0].model_copy(
            update={"id": None, "name": "Queue Clip B", "start": 2.0, "end": 4.0}
        )
        job2 = worker.enqueue_clip(project, clip_b, settings=fast)
        assert job1.output != job2.output

        done, errors = wait_for_empty(worker)
        assert done == 2, f"expected 2 done, got {done} (errors={errors})"
        assert errors == 0

        jobs = {j.id: j for j in worker.jobs_snapshot()}
        for job_id in (job1.id, job2.id):
            job = jobs[job_id]
            assert job.status == "done", f"job {job_id}: {job.status} {job.error}"
            out = Path(job.output)
            assert out.exists() and out.stat().st_size > 0, job.output

        # outputs were written outside the project folder when output_dir set
        custom = Path(tempfile.mkdtemp(prefix="clipper_queue_out_"))
        try:
            job3 = worker.enqueue_clip(
                project, project.clips[0], output_dir=str(custom), settings=fast
            )
            done2, errors2 = wait_for_empty(worker)
            state3 = next(
                (j for j in worker.jobs_snapshot() if j.id == job3.id), None
            )
            assert done2 == 1 and errors2 == 0, (
                f"batch2 empty=({done2},{errors2}) job3="
                f"{state3.status if state3 else 'missing'} "
                f"{state3.error if state3 else ''} out={job3.output}"
            )
            assert Path(job3.output).parent == custom
            assert Path(job3.output).exists()
        finally:
            shutil.rmtree(custom, ignore_errors=True)

        worker.shutdown()
        assert not worker.isRunning()
        print(
            f"PASS queue end-to-end: 2 exports "
            f"({Path(jobs[job1.id].output).stat().st_size} + "
            f"{Path(jobs[job2.id].output).stat().st_size} bytes)"
        )
    finally:
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def test_gui_panels() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True

    assert window.pages.count() == 8, window.pages.count()
    assert window.sidebar._buttons["queue"].isEnabled()
    assert window.sidebar._buttons["settings"].isEnabled()
    assert window.sidebar._buttons["captions"].isEnabled()
    assert window.sidebar._buttons["ai"].isEnabled()

    window.sidebar.select("queue")
    assert window.pages.currentIndex() == 4
    assert "empty" in window.queue_panel.summary_label.text().lower()

    window.sidebar.select("settings")
    assert window.pages.currentIndex() == 5
    assert window.settings_panel.aspect_box.currentData() in ("16:9", "9:16", "1:1", "4:5")

    # settings panel save round-trip through the real service
    tmp = Path(tempfile.mkdtemp(prefix="clipper_gui_settings_"))
    try:
        from app.services.settings_service import SettingsService

        window.settings_service = SettingsService(tmp / "settings.json")
        window.settings_panel._service = window.settings_service
        window.settings_panel.load_values()
        window.settings_panel.aspect_box.setCurrentIndex(
            window.settings_panel.aspect_box.findData("16:9")
        )
        window.settings_panel._save()
        assert window.settings_service.settings.default_aspect == "16:9"
        assert (tmp / "settings.json").exists()

        # export panel exposes the queue action
        window.settings_panel.save_button.click()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    window.queue_worker.shutdown(2000)
    print("PASS gui: queue + settings pages wired into MainWindow")


def main() -> None:
    _ = QApplication.instance() or QApplication([])
    test_settings_roundtrip()
    test_queue_logic()
    test_queue_end_to_end()
    test_gui_panels()
    print("ALL QUEUE/SETTINGS TESTS PASSED")


if __name__ == "__main__":
    main()
