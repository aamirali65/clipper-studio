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

from app.models.caption import CaptionSegment  # noqa: E402
from app.models.clip import Clip  # noqa: E402
from app.services.highlight_service import (  # noqa: E402
    ai_rank_prompt,
    analyze,
    build_units,
    build_windows,
    parse_ai_windows,
    score_window,
)

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "AutoClipTest"
DB_PROJECT_NAME = "AutoClipDBTest"


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


def test_units_and_windows() -> None:
    # pause split (> 0.7s silence)
    pause = [
        CaptionSegment(clip_id=0, start=0.0, end=2.0, text="Hello there."),
        CaptionSegment(clip_id=0, start=3.2, end=5.0, text="World again."),
    ]
    units = build_units(pause)
    assert len(units) == 2, units
    assert units[0][2] == "Hello there."
    assert units[1][0] == 3.2

    # sentence split at any gap >= 0.05 after .!?
    sentence = [
        CaptionSegment(clip_id=0, start=0.0, end=2.0, text="First sentence."),
        CaptionSegment(clip_id=0, start=2.3, end=4.0, text="Second one!"),
    ]
    assert len(build_units(sentence)) == 2

    # no punctuation and a small gap stays merged
    merged = [
        CaptionSegment(clip_id=0, start=0.0, end=2.0, text="part one"),
        CaptionSegment(clip_id=0, start=2.3, end=4.0, text="part two"),
    ]
    units = build_units(merged)
    assert len(units) == 1
    assert units[0][2] == "part one part two"

    # windows land inside [min, max]
    units = build_units(make_segments())
    windows = build_windows(units, 15.0, 45.0)
    assert windows, "expected windows"
    for start, end, _text in windows:
        assert 15.0 <= end - start <= 45.0, (start, end)
    assert build_windows([], 15.0, 45.0) == []
    print("PASS units/windows: pause + sentence splits, durations in range")


def test_scoring_and_nms() -> None:
    hook = score_window(0.0, 5.0, [(0.0, 5.0, "How to make money fast with this insane trick?")])
    neutral = score_window(0.0, 5.0, [(0.0, 5.0, "And then the table was near the door.")])
    assert hook is not None and neutral is not None
    assert hook.raw > neutral.raw, (hook.raw, neutral.raw)

    segments = make_segments()
    duration = segments[-1].end
    candidates = analyze(segments, media_id=7, media_duration=duration, count=4)
    assert 1 <= len(candidates) <= 4
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True), scores
    assert all(0 <= c.score <= 100 for c in candidates)
    assert all(c.media_id == 7 for c in candidates)
    assert all(c.order == i for i, c in enumerate(candidates))
    assert all(c.end <= duration + 0.001 for c in candidates)
    # non-max suppression: no two picks overlap by >= 1 second
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            overlap = max(0.0, min(a.end, b.end) - max(a.start, b.start))
            assert overlap < 1.0, (a.start, a.end, b.start, b.end)

    # AI windows join with a bonus and keep their own title/source
    from app.models.highlight import SOURCE_AI

    ai = [(10.0, 40.0, "AI title", "great story")]
    merged = analyze(segments, media_id=7, media_duration=duration, count=3, ai_windows=ai)
    assert any(c.source == SOURCE_AI for c in merged)
    best = merged[0]
    assert best.source == SOURCE_AI
    assert best.title == "AI title"
    assert best.score == max(c.score for c in merged)

    # empty transcript -> no candidates
    assert analyze([], media_id=1) == []
    print("PASS scoring: hook > neutral, sorted, NMS no overlap, AI bonus")


def test_parse_ai_windows() -> None:
    fenced = (
        "Here you go:\n```json\n"
        '[{"start": 10, "end": 40, "title": "AI pick", "reason": "story"}]'
        "\n```"
    )
    windows = parse_ai_windows(fenced, duration=100, min_len=15, max_len=45)
    assert windows == [(10.0, 40.0, "AI pick", "story")]

    bare = 'Result: [{"start": 0, "end": 60, "title": "a", "reason": "b"}] done'
    windows = parse_ai_windows(bare, duration=100, min_len=15, max_len=45)
    assert len(windows) == 1
    assert windows[0][1] - windows[0][0] == 45.0  # capped to max_len

    clamped = parse_ai_windows('[{"start": -20, "end": 999}]', duration=50, min_len=5, max_len=30)
    assert clamped
    assert clamped[0][0] == 0.0
    assert clamped[0][1] == 30.0  # inside duration, capped to max_len
    assert clamped[0][1] <= 50.0

    extended = parse_ai_windows('[{"start": 5, "end": 8}]', duration=100, min_len=15, max_len=45)
    assert extended and extended[0][1] - extended[0][0] >= 15.0

    assert parse_ai_windows("I cannot help with that.", duration=100) == []
    assert parse_ai_windows("", duration=100) == []
    assert parse_ai_windows('[{"start": "bad", "end": 3}]', duration=100) == []
    assert parse_ai_windows('[{"start": 30, "end": 20}]', duration=100) == []
    print("PASS parse: fences, clamping, extension, garbage rejected")


def test_ai_rank_prompt() -> None:
    segments = make_segments(count=5)
    prompt = ai_rank_prompt(segments, count=3, min_len=15, max_len=45, duration=140)
    assert "JSON array" in prompt
    assert "exactly 3 objects" in prompt
    assert "15-45 seconds" in prompt
    assert "[0.0-2.0]" in prompt
    assert "money fast with number 0" in prompt

    # char cap trumps long transcripts
    huge = [CaptionSegment(clip_id=0, start=i * 3.0, end=i * 3.0 + 2.5, text="x" * 300) for i in range(200)]
    capped = ai_rank_prompt(huge, count=5, min_len=15, max_len=45, duration=900)
    assert len(capped) < 8000 + 600
    print("PASS prompt: rules + transcript lines + char cap")


def test_transcript_db() -> None:
    from app.database.database import ProjectDatabase
    from app.database.repositories import MediaRepository, TranscriptRepository
    from app.models.media import MediaItem, MediaKind
    from app.models.project import utc_now
    from app.services.project_service import ProjectService

    shutil.rmtree(ROOT / "projects" / DB_PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(DB_PROJECT_NAME)
    project.ensure_dirs()
    media = MediaItem(
        name="transcript_src",
        kind=MediaKind.local,
        source_path=str(SAMPLE),
        duration=30.0,
        created_at=utc_now(),
    )
    database = ProjectDatabase(project.path)
    try:
        MediaRepository(database).add(media, project.directory)
        repo = TranscriptRepository(database)
        assert repo.get(media.id or 0) is None

        segments = make_segments(count=6)
        repo.upsert(media.id or 0, segments, "en")
        cached = repo.get(media.id or 0)
        assert cached is not None
        got, language = cached
        assert language == "en"
        assert len(got) == 6
        assert got[0].text == segments[0].text
        assert got[-1].start == segments[-1].start

        # upsert replaces in place (media_id is UNIQUE)
        repo.upsert(media.id or 0, segments[:3], "de")
        got, language = repo.get(media.id or 0)
        assert language == "de" and len(got) == 3

        # media delete cascades to the transcript
        MediaRepository(database).delete(media.id or 0)
        assert repo.get(media.id or 0) is None
        print("PASS transcript db: upsert/get/replace + cascade delete")
    finally:
        database.close()
        shutil.rmtree(ROOT / "projects" / DB_PROJECT_NAME, ignore_errors=True)


def make_gui_project() -> tuple:
    from app.database.database import ProjectDatabase
    from app.database.repositories import ClipRepository, MediaRepository
    from app.models.media import MediaItem, MediaKind
    from app.models.project import utc_now
    from app.services.project_service import ProjectService
    from app.services.video_service import VideoService

    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(PROJECT_NAME)
    project.ensure_dirs()

    info = VideoService().probe(SAMPLE)
    media = MediaItem(
        name="auto_source",
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

    clip = Clip(
        media_id=media.id or 0,
        name="Auto Clip",
        start=0.0,
        end=3.0,
        aspect="9:16",
        timeline_start=0.0,
        timeline_end=3.0,
        order=0,
        created_at=utc_now(),
    )
    database = ProjectDatabase(project.path)
    try:
        ClipRepository(database).add(clip)
    finally:
        database.close()
    project.clips.append(clip)
    return service, project


def test_gui_auto_page() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True

    assert "auto" in window.sidebar._buttons, "auto page missing from sidebar"
    assert window.sidebar._buttons["auto"].isEnabled(), "auto page disabled"
    assert window.pages.count() == 10, window.pages.count()
    window.sidebar.select("auto")
    assert window.pages.currentIndex() == 8

    panel = window.auto_panel
    pushed: list[str] = []
    window._push_status = pushed.append

    service, project = make_gui_project()
    try:
        window._load_project(project, "loaded")
        window.sidebar.select("auto")
        assert window.pages.currentIndex() == 8
        assert panel.media_box.count() == 1
        assert panel.selected_media_id() == project.media[0].id
        assert "No cached transcript" in panel.transcript_label.text()

        media = project.media[0]
        duration = media.duration
        segments = make_segments()

        # no media selected guard starts no worker
        panel.media_box.clear()
        window._start_auto_analysis()
        assert window._auto_worker is None
        assert "Select a media file" in panel.status_label.text()
        window._refresh_auto_panel()
        assert panel.media_box.count() == 1

        # min > max guard starts no worker
        panel.min_box.setValue(100)
        panel.max_box.setValue(50)
        window._start_auto_analysis()
        assert window._auto_worker is None
        assert "greater than min" in panel.status_label.text()
        panel.min_box.setValue(5)
        panel.max_box.setValue(20)

        # simulated completion persists the transcript and fills the tree
        candidates = analyze(
            segments, media_id=media.id or 0, media_duration=duration, count=3
        )
        assert candidates
        window._on_auto_completed(
            {
                "candidates": candidates,
                "transcript": segments,
                "language": "en",
                "transcribed": True,
                "note": "",
            }
        )
        assert not panel.busy
        assert panel.tree.topLevelItemCount() == len(candidates)
        assert len(panel.checked_candidates()) == len(candidates)
        assert "Found" in panel.status_label.text()
        cached = window._load_transcript(media.id or 0)
        assert cached is not None
        assert len(cached[0]) == len(segments)
        assert cached[1] == "en"
        assert "Cached transcript" in panel.transcript_label.text()

        # real worker run using the cached transcript (no whisper needed)
        panel.count_box.setValue(3)
        window._start_auto_analysis()
        assert window._auto_worker is not None
        assert panel.busy
        # duplicate start is refused while running
        window._start_auto_analysis()
        worker = window._auto_worker
        wait_thread(app, worker, 60.0)
        assert not worker.isRunning(), "auto worker did not finish"
        assert not panel.busy
        assert panel.tree.topLevelItemCount() == 3, panel.tree.topLevelItemCount()
        assert "Found 3" in panel.status_label.text()

        # preview seeks and reports without touching the results
        panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
        pushed.clear()
        window._on_auto_preview(0)
        assert any(msg.startswith("Preview") for msg in pushed), pushed
        assert panel.tree.topLevelItemCount() == 3

        # add checked highlights to the timeline
        from app.database.database import ProjectDatabase
        from app.database.repositories import ClipRepository

        before = len(project.clips)
        window._on_auto_add()
        assert len(project.clips) == before + 3
        assert all(entry["type"] == "clip_add" for entry in window._undo_stack[-3:])
        database = ProjectDatabase(project.path)
        try:
            rows = ClipRepository(database).list()
        finally:
            database.close()
        assert len(rows) == before + 3, len(rows)
        added = project.clips[before:]
        assert all(c.media_id == (media.id or 0) for c in added)
        assert all(c.start >= 0.0 and c.end <= duration + 0.001 for c in added)
        assert len(added) == 1 or added[-1].timeline_start >= added[0].timeline_end

        # nothing checked -> refused with a hint
        from PySide6.QtCore import Qt

        for row in range(panel.tree.topLevelItemCount()):
            panel.tree.topLevelItem(row).setCheckState(0, Qt.CheckState.Unchecked)
        pushed.clear()
        window._on_auto_add()
        assert len(project.clips) == before + 3
        assert any("Check at least one" in msg for msg in pushed), pushed

        # clear resets results
        window._on_auto_clear()
        assert panel.tree.topLevelItemCount() == 0
        assert panel.candidates == []

        # cancel + error paths
        panel.set_busy(True)
        window._on_auto_error("cancelled")
        assert not panel.busy
        assert panel.status_label.text() == "Cancelled"
        panel.set_busy(True)
        window._on_auto_error("boom failure")
        assert not panel.busy
        assert "boom failure" in panel.status_label.text()

        # busy guard: refresh does nothing mid-run
        panel.set_busy(True)
        window._refresh_auto_panel()
        assert panel.media_box.count() == 1
        panel.set_busy(False)

        # progress updates the status line
        window._on_auto_progress("Transcribing media...")
        assert panel.status_label.text() == "Transcribing media..."
        print("PASS gui: auto page, analysis flow, transcript cache, add clips")
    finally:
        if window._auto_worker is not None and window._auto_worker.isRunning():
            window._auto_worker.cancel()
            window._auto_worker.wait(4000)
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def test_real_worker() -> None:
    from app.services.caption_service import whisper_available
    from app.services.ffmpeg_service import FFmpegService
    from app.services.video_service import VideoService
    from app.workers.autoclip_worker import AutoClipWorker
    from app.utils.paths import cache_dir

    app = QApplication.instance() or QApplication([])
    if os.environ.get("CLIPPER_SKIP_WHISPER"):
        print("SKIP worker run (CLIPPER_SKIP_WHISPER set)")
        return
    if not whisper_available():
        print("SKIP worker run (faster-whisper not installed)")
        return
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    info = VideoService().probe(SAMPLE)

    results: dict = {}
    errors: list[str] = []

    worker = AutoClipWorker(
        media_id=1,
        media_path=SAMPLE,
        media_duration=info.duration,
        cached_transcript=None,
        whisper_model="tiny",
        whisper_language="auto",
        download_root=cache_dir() / "whisper",
        ffmpeg=FFmpegService(),
    )
    worker.completed.connect(lambda payload: results.update(payload))
    worker.error.connect(errors.append)
    worker.start()
    wait_thread(app, worker, 300.0)
    if worker.isRunning():
        worker.cancel()
        worker.wait(5000)
        raise AssertionError("real auto worker did not finish in time")
    assert not errors, errors
    assert results.get("transcribed") is True
    assert results.get("candidates") == []
    assert "No speech" in str(results.get("note", "")), results.get("note")
    print("PASS worker: full-media whisper run cached an empty transcript")

    # cached path: no whisper, quick scoring
    segments = make_segments()
    results2: dict = {}
    worker2 = AutoClipWorker(
        media_id=1,
        media_path=SAMPLE,
        media_duration=info.duration,
        cached_transcript=segments,
        cached_language="en",
        min_len=5.0,
        max_len=20.0,
        count=3,
    )
    worker2.completed.connect(lambda payload: results2.update(payload))
    worker2.error.connect(errors.append)
    worker2.start()
    wait_thread(app, worker2, 30.0)
    assert not worker2.isRunning(), "cached auto worker did not finish"
    assert not errors, errors
    assert results2.get("transcribed") is False
    assert len(results2.get("candidates") or []) == 3

    # missing source without a cache reports an error, never a crash
    errors3: list[str] = []
    worker3 = AutoClipWorker(
        media_id=1,
        media_path=ROOT / "output" / "does_not_exist.mp4",
        media_duration=10.0,
        cached_transcript=None,
        whisper_model="tiny",
        download_root=cache_dir() / "whisper",
        ffmpeg=FFmpegService(),
    )
    worker3.error.connect(errors3.append)
    worker3.start()
    wait_thread(app, worker3, 60.0)
    assert not worker3.isRunning(), "failing auto worker did not finish"
    assert errors3 and errors3[0] != "cancelled", errors3
    print("PASS worker: cached scoring + missing-source error path")


def main() -> None:
    _ = QApplication.instance() or QApplication([])
    test_units_and_windows()
    test_scoring_and_nms()
    test_parse_ai_windows()
    test_ai_rank_prompt()
    test_transcript_db()
    test_gui_auto_page()
    test_real_worker()
    print("ALL AUTOCLIP TESTS PASSED")


if __name__ == "__main__":
    main()
