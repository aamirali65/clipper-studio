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
from app.database.repositories import CaptionRepository, ClipRepository  # noqa: E402
from app.models.caption import CaptionSegment  # noqa: E402
from app.models.clip import Clip, ExportSettings  # noqa: E402
from app.models.media import MediaItem, MediaKind  # noqa: E402
from app.models.project import utc_now  # noqa: E402
from app.services.caption_service import (  # noqa: E402
    format_srt_time,
    parse_srt,
    parse_srt_time,
    segments_to_srt,
    whisper_available,
    write_srt,
)
from app.services.export_service import (  # noqa: E402
    ExportRequest,
    ExportService,
    subtitles_filter,
)
from app.services.project_service import ProjectService  # noqa: E402
from app.services.video_service import VideoService  # noqa: E402

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "CaptionsTestProject"


def test_srt_roundtrip() -> None:
    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(65.432) == "00:01:05,432"
    assert format_srt_time(3661.5) == "01:01:01,500"
    assert parse_srt_time("00:01:05,432") == 65.432
    assert abs(parse_srt_time("1:02:03.250") - 3723.25) < 1e-9

    segments = [
        CaptionSegment(clip_id=1, start=0.0, end=1.5, text="Hello world"),
        CaptionSegment(clip_id=1, start=1.5, end=3.25, text="Second line"),
    ]
    srt = segments_to_srt(segments)
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello world" in srt
    parsed = parse_srt(srt)
    assert len(parsed) == 2
    assert parsed[0] == (0.0, 1.5, "Hello world")
    assert abs(parsed[1][0] - 1.5) < 1e-9
    assert parsed[1][2] == "Second line"

    # Windows line endings + BOM-ish tolerance
    windows = srt.replace("\n", "\r\n")
    assert len(parse_srt(windows)) == 2
    print("PASS srt: format/parse round-trip")


def make_project() -> tuple[ProjectService, object]:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(PROJECT_NAME)
    project.ensure_dirs()

    info = VideoService().probe(SAMPLE)
    media = MediaItem(
        name="captions_source",
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
        from app.database.repositories import MediaRepository

        MediaRepository(database).add(media, project.directory)
    finally:
        database.close()
    project.media.append(media)

    clip = Clip(
        media_id=media.id or 0,
        name="Caption Clip",
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


def test_caption_db() -> None:
    service, project = make_project()
    try:
        clip_id = project.clips[0].id
        assert clip_id is not None
        segments = [
            CaptionSegment(clip_id=clip_id, start=0.1, end=1.0, text="one", language="en"),
            CaptionSegment(clip_id=clip_id, start=1.0, end=2.0, text="two", language="en"),
        ]
        database = ProjectDatabase(project.path)
        try:
            repo = CaptionRepository(database)
            saved = repo.replace_for_clip(clip_id, segments)
            assert len(saved) == 2 and all(s.id is not None for s in saved)

            loaded = repo.for_clip(clip_id)
            assert [s.text for s in loaded] == ["one", "two"]
            assert loaded[0].language == "en"

            repo.update_text(saved[0].id, "ONE edited")
            assert repo.for_clip(clip_id)[0].text == "ONE edited"

            # replace wipes the old set
            repo.replace_for_clip(
                clip_id, [CaptionSegment(clip_id=clip_id, start=0, end=1, text="only")]
            )
            assert len(repo.for_clip(clip_id)) == 1

            # cascade: deleting the clip removes captions
            ClipRepository(database).delete(clip_id)
            assert repo.for_clip(clip_id) == []
        finally:
            database.close()
        print("PASS caption db: replace/update/cascade-delete")
    finally:
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def test_subtitles_filter_escaping() -> None:
    result = subtitles_filter(r"C:\My Projects\clip 1.srt")
    assert result == "subtitles=filename='C\\:/My Projects/clip 1.srt'"
    assert ":" in result and "\\" in result
    print("PASS subtitles filter escaping (windows path)")


def test_burn_in_export() -> None:
    service, project = make_project()
    try:
        clip = project.clips[0]
        media = project.media[0]
        srt = write_srt(
            [
                CaptionSegment(
                    clip_id=clip.id, start=0.0, end=1.5, text="Burned caption line"
                ),
                CaptionSegment(
                    clip_id=clip.id, start=1.5, end=3.0, text="Second burned line"
                ),
            ],
            project.cache_dir / "srt test" / "captions.srt",
        )
        assert srt.exists()

        out = project.exports_dir / "burned output.mp4"
        request = ExportRequest(
            source=Path(media.source_path),
            output=out,
            start=0.0,
            end=2.0,
            aspect="9:16",
            settings=ExportSettings(preset="fast", crf=26),
            subtitles=srt,
        )
        progress_events: list[float] = []
        ExportService().export(
            request,
            progress_cb=lambda p, d: progress_events.append(p),
        )
        assert out.exists() and out.stat().st_size > 0
        assert progress_events, "no progress events"
        print(
            f"PASS burn-in export: {out.name} {out.stat().st_size} bytes "
            f"({len(progress_events)} progress events)"
        )
    finally:
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def test_export_dialog_burn_checkbox() -> None:
    from app.ui.export_dialog import ExportDialog

    service, project = make_project()
    try:
        clip = project.clips[0]
        media = project.media[0]
        srt = project.cache_dir / "dialog.srt"
        write_srt(
            [CaptionSegment(clip_id=clip.id, start=0, end=1, text="hi")], srt
        )

        dialog = ExportDialog(
            project, clip, media, None, captions_srt=srt, burn_captions=True
        )
        assert dialog.burn_box.isEnabled()
        assert dialog.burn_box.isChecked()
        request = dialog._build_request()
        assert request.subtitles == srt

        dialog.burn_box.setChecked(False)
        assert dialog._build_request().subtitles is None
        dialog.deleteLater()

        no_captions = ExportDialog(project, clip, media, None, captions_srt=None)
        assert not no_captions.burn_box.isEnabled()
        assert no_captions._build_request().subtitles is None
        no_captions.deleteLater()
        print("PASS export dialog: burn checkbox on/off/disabled")
    finally:
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def test_whisper_engine() -> None:
    if os.environ.get("CLIPPER_SKIP_WHISPER"):
        print("SKIP whisper engine (CLIPPER_SKIP_WHISPER set)")
        return
    if not whisper_available():
        print("SKIP whisper engine (faster-whisper not installed)")
        return
    from app.services.caption_service import transcribe_range
    from app.utils.paths import cache_dir

    try:
        segments, language = transcribe_range(
            SAMPLE,
            0.0,
            2.0,
            model_name="tiny",
            language="auto",
            download_root=cache_dir() / "whisper",
        )
    except Exception as exc:  # noqa: BLE001 - offline/download failures skip
        print(f"SKIP whisper engine ({type(exc).__name__}: {exc})")
        return
    assert isinstance(segments, list)
    for segment in segments:
        assert segment.start >= 0.0
        assert segment.end >= segment.start
        assert segment.text
    print(
        f"PASS whisper engine: {len(segments)} segments, language={language!r}"
    )


def test_gui_captions_page() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True

    assert window.sidebar._buttons["captions"].isEnabled(), "captions page disabled"
    assert window.pages.count() == 8, window.pages.count()
    window.sidebar.select("captions")
    assert window.pages.currentIndex() == 6

    service, project = make_project()
    try:
        window._load_project(project, "loaded")
        window.sidebar.select("captions")
        clip = project.clips[0]

        # simulate a completed transcription without running whisper
        fake = [
            CaptionSegment(clip_id=0, start=0.0, end=1.2, text="Hello from test"),
            CaptionSegment(clip_id=0, start=1.2, end=2.4, text="Second sentence"),
        ]
        window._transcribe_clip_id = clip.id
        window._on_transcribe_completed(fake, "en")
        assert window.captions_panel.tree.topLevelItemCount() == 2

        # persisted?
        database = ProjectDatabase(project.path)
        try:
            stored = CaptionRepository(database).for_clip(clip.id)
        finally:
            database.close()
        assert [s.text for s in stored] == ["Hello from test", "Second sentence"]

        # SRT snapshot for burn-in
        srt_path = window._write_clip_srt(clip)
        assert srt_path is not None and srt_path.exists()
        assert "Hello from test" in srt_path.read_text(encoding="utf-8")

        # inline edit persists
        window._on_caption_segment_edited(stored[0].id, "Edited text")
        database = ProjectDatabase(project.path)
        try:
            assert CaptionRepository(database).for_clip(clip.id)[0].text == "Edited text"
        finally:
            database.close()

        # clearing removes all
        window.captions_panel.set_segments(stored)
        database = ProjectDatabase(project.path)
        try:
            CaptionRepository(database).delete_for_clip(clip.id)
            assert CaptionRepository(database).for_clip(clip.id) == []
        finally:
            database.close()
        print("PASS gui: captions page, transcribe-complete flow, srt, edits")
    finally:
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def main() -> None:
    _ = QApplication.instance() or QApplication([])
    test_srt_roundtrip()
    test_subtitles_filter_escaping()
    test_caption_db()
    test_burn_in_export()
    test_export_dialog_burn_checkbox()
    test_gui_captions_page()
    test_whisper_engine()
    print("ALL CAPTIONS TESTS PASSED")


if __name__ == "__main__":
    main()
