from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database.repositories import ClipRepository, MediaRepository, ProjectDatabase
from app.models.clip import Clip
from app.models.media import MediaItem, MediaKind
from app.models.project import utc_now
from app.services.export_service import ExportRequest, ExportService
from app.services.project_service import ProjectError, ProjectService
from app.services.video_service import VideoService
from app.utils.timecode import format_timecode, parse_timecode

SAMPLE = ROOT / "output" / "sample_video.mp4"


def ensure_sample() -> Path:
    if SAMPLE.exists():
        return SAMPLE
    import subprocess

    SAMPLE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc2=duration=30:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(SAMPLE),
        ],
        check=True,
        capture_output=True,
    )
    return SAMPLE


def test_timecode_roundtrip():
    for seconds in (0.0, 1.234, 74.5, 3661.001, 4475.0):
        text = format_timecode(seconds)
        back = parse_timecode(text)
        assert abs(back - seconds) < 0.001, (seconds, text, back)
    try:
        parse_timecode("not:a:time")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid timecode accepted")
    print("PASS timecode roundtrip")


def test_probe():
    ensure_sample()
    if not SAMPLE.exists():
        raise AssertionError(f"sample missing: {SAMPLE}")
    info = VideoService().probe(SAMPLE)
    assert abs(info.duration - 30.0) < 0.5, info.duration
    assert info.width == 1280 and info.height == 720, (info.width, info.height)
    assert info.has_audio
    print(f"PASS probe: {info.duration:.2f}s {info.width}x{info.height} "
          f"v={info.video_codec} a={info.audio_codec}")


def test_project_lifecycle():
    service = ProjectService()
    name = "Phase1Test"
    import shutil

    for old in sorted((ROOT / "projects").glob("Phase1Test*"), reverse=True):
        shutil.rmtree(old, ignore_errors=True)
    project = service.create_project(name)
    assert project.path.exists()
    assert project.media_dir.is_dir() and project.exports_dir.is_dir()

    db = ProjectDatabase(project.path)
    media = MediaItem(
        name="Sample",
        kind=MediaKind.local,
        source_path=str(SAMPLE),
        duration=30.0,
        width=1280,
        height=720,
        size_bytes=SAMPLE.stat().st_size,
        created_at=utc_now(),
    )
    MediaRepository(db).add(media, project.directory)
    assert media.id is not None
    db.close()

    project.media.append(media)
    clip = Clip(media_id=media.id, name="Sample clip 1", start=5.0, end=12.5,
                aspect="9:16", created_at=utc_now())
    db = ProjectDatabase(project.path)
    ClipRepository(db).add(clip)
    db.close()
    project.clips.append(clip)
    service.save_project(project)

    reopened = service.open_project(project.path)
    assert reopened.name == name
    assert len(reopened.media) == 1
    assert len(reopened.clips) == 1
    rc = reopened.clips[0]
    assert abs(rc.start - 5.0) < 1e-6 and abs(rc.end - 12.5) < 1e-6
    assert rc.aspect == "9:16"
    media_path = Path(reopened.media[0].source_path)
    assert media_path.exists(), media_path
    print(f"PASS project lifecycle: {reopened.path}")

    recent = service.recent_projects()
    assert any(r.name == name for r in recent)
    print(f"PASS recent registry: {len(recent)} entries")
    return project, media, clip


def test_export(project, media, clip):
    service = ExportService()
    request = ExportRequest(
        source=SAMPLE,
        output=project.exports_dir / "test_clip_9x16.mp4",
        start=5.0,
        end=12.5,
        aspect="9:16",
        settings=project.export_settings,
    )
    args = service.build_args(request)
    assert str(request.output) in args
    assert "1080" in args[args.index("-vf") + 1] if False else True
    vf = args[args.index("-vf") + 1]
    assert "1080:1920" in vf, vf
    assert args[args.index("-t") + 1] == "7.500"

    events: list[tuple[float, str]] = []
    output = service.export(request, progress_cb=lambda p, d: events.append((p, d)))
    assert output.exists() and output.stat().st_size > 0
    assert events and events[-1][0] == 100.0

    info = VideoService().probe(output)
    assert abs(info.duration - 7.5) < 0.4, info.duration
    assert info.width == 1080 and info.height == 1920, (info.width, info.height)
    print(f"PASS export 9:16 -> {info.width}x{info.height} "
          f"{info.duration:.2f}s {output.stat().st_size} bytes, "
          f"{len(events)} progress events")

    request2 = ExportRequest(
        source=SAMPLE,
        output=project.exports_dir / "test_clip_16x9.mp4",
        start=10.0,
        end=16.0,
        aspect="16:9",
        settings=project.export_settings,
    )
    out2 = service.export(request2)
    info2 = VideoService().probe(out2)
    assert (info2.width, info2.height) == (1920, 1080)
    assert abs(info2.duration - 6.0) < 0.4
    print(f"PASS export 16:9 -> {info2.width}x{info2.height} {info2.duration:.2f}s")

    request3 = ExportRequest(
        source=SAMPLE,
        output=project.exports_dir / "test_clip_1x1.mp4",
        start=2.0,
        end=5.0,
        aspect="1:1",
        settings=project.export_settings,
    )
    out3 = service.export(request3)
    info3 = VideoService().probe(out3)
    assert (info3.width, info3.height) == (1080, 1080)
    print(f"PASS export 1:1 -> {info3.width}x{info3.height}")


def test_clip_validation():
    clip = Clip(media_id=1, start=10.0, end=5.0)
    errors = clip.validate_range(100.0)
    assert any("End must be greater" in e for e in errors)
    clip2 = Clip(media_id=1, start=0.0, end=101.0)
    assert any("source duration" in e for e in clip2.validate_range(100.0))
    clip3 = Clip(media_id=1, start=10.0, end=20.0, aspect="16:9")
    assert clip3.validate_range(100.0) == []
    print("PASS clip validation")


def main():
    test_timecode_roundtrip()
    test_clip_validation()
    test_probe()
    project, media, clip = test_project_lifecycle()
    test_export(project, media, clip)
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
