from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.database.database import ProjectDatabase  # noqa: E402
from app.database.repositories import MediaRepository, TrackRepository  # noqa: E402
from app.models.clip import ExportSettings, resolution_for  # noqa: E402
from app.models.media import MediaItem, MediaKind  # noqa: E402
from app.models.project import utc_now  # noqa: E402
from app.models.track import TrackKeyframe  # noqa: E402
from app.services.export_service import ExportRequest, ExportService  # noqa: E402
from app.services.face_track_service import (  # noqa: E402
    DETECT_MAX_SIDE,
    INTERVAL_STEP,
    MAX_GAP,
    availability,
    box_at,
    build_cmds,
    clip_keyframes,
    crop_intervals,
    crop_xy,
    detect_size,
    detect_track,
    pick_largest_face,
    plane_size,
    sendcmd_filter,
    smooth_keyframes,
)
from app.services.project_service import ProjectService  # noqa: E402
from app.services.video_service import VideoService  # noqa: E402

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "SmartTrackTestProject"


# ---------------------------------------------------------- pure math
def test_crop_math() -> None:
    # cover-fit plane for 16:9 -> 9:16
    assert plane_size(1280, 720, 1080, 1920) == (3413, 1920)
    assert plane_size(720, 1280, 1080, 1920) == (1080, 1920)
    assert plane_size(0, 0, 1080, 1920) == (1080, 1920)

    # crop clamped to the plane
    assert crop_xy((0.0, 0.0, 0.2, 0.2), 1080, 1920, 3413, 1920) == (0, 0)
    assert crop_xy((1.0, 1.0, 0.2, 0.2), 1080, 1920, 3413, 1920) == (
        3413 - 1080,
        0,
    )
    px, py = crop_xy((0.5, 0.5, 0.2, 0.2), 1080, 1920, 3413, 1920)
    assert 0 <= px <= 3413 - 1080 and py == 0

    # detection size stays even and bounded
    assert detect_size(1920, 1080) == (DETECT_MAX_SIDE, 360)
    assert detect_size(500, 300) == (500, 300)
    assert detect_size(501, 301) == (500, 300)
    w, h = detect_size(100, 2000)
    assert h == DETECT_MAX_SIDE and w % 2 == 0

    # largest face wins, normalized to the frame
    faces = [[10, 10, 40, 40], [100, 100, 80, 80]]
    box = pick_largest_face(faces, 200, 200)
    assert box is not None
    assert box == (0.5, 0.5, 0.4, 0.4)
    assert pick_largest_face(None, 200, 200) is None
    assert pick_largest_face([], 200, 200) is None
    assert pick_largest_face([[0, 0, 0, 0]], 200, 200) is None
    print("PASS crop math: plane_size/crop_xy/detect_size/pick_largest_face")


def test_smoothing() -> None:
    assert smooth_keyframes([], duration=5.0) == []
    assert smooth_keyframes([(0.0, 0.5, 0.5, 0.2, 0.2)], duration=0.0) == []

    raw = [(0.0, 0.1, 0.1, 0.2, 0.2), (1.0, 0.5, 0.5, 0.2, 0.2)]
    keyframes = smooth_keyframes(raw, duration=3.0)
    assert keyframes, "interpolated series"
    assert keyframes[0].t == 0.0
    assert keyframes[-1].t == 3.0
    times = [kf.t for kf in keyframes]
    assert times == sorted(times), "monotonic time"
    # between samples: interpolated gaps never exceed INTERVAL_STEP
    # (the final stretch to `duration` is a hold of the last box)
    covered = [t for t in times if t <= raw[-1][0]]
    gaps = [b - a for a, b in zip(covered, covered[1:]) if b - a > 0]
    assert gaps and max(gaps) <= INTERVAL_STEP + 1e-9
    for kf in keyframes:
        assert 0.0 <= kf.x <= 1.0 and 0.0 <= kf.y <= 1.0
        assert 0.0 < kf.w <= 1.0 and 0.0 < kf.h <= 1.0

    # holes larger than MAX_GAP are held, not interpolated across
    sparse = [(0.0, 0.2, 0.2, 0.2, 0.2), (20.0, 0.8, 0.8, 0.2, 0.2)]
    held = smooth_keyframes(sparse, duration=22.0)
    middle = [kf for kf in held if 0.1 < kf.t < 19.9]
    assert middle == [], "no interpolation across a >MAX_GAP hole"
    assert any(abs(kf.t - 20.0) < 1e-9 for kf in held)
    assert held[-1].t == 22.0
    assert MAX_GAP < 20.0
    print("PASS smoothing: interpolate <= STEP, hold beyond MAX_GAP")


def test_clip_intervals_cmds() -> None:
    track = [
        TrackKeyframe(t=0.0, x=0.1, y=0.1, w=0.2, h=0.2),
        TrackKeyframe(t=1.0, x=0.6, y=0.4, w=0.2, h=0.2),
        TrackKeyframe(t=2.0, x=0.6, y=0.4, w=0.2, h=0.2),
        TrackKeyframe(t=3.0, x=0.6, y=0.4, w=0.2, h=0.2),
    ]

    rel = clip_keyframes(track, 0.5, 2.5)
    duration = 2.0
    assert rel, "clip segment has keyframes"
    assert rel[0].t == 0.0 and rel[-1].t == duration
    assert all(0.0 <= kf.t <= duration for kf in rel)
    assert rel[1].t == 0.5  # rebased from media t=1.0

    out_size = resolution_for("9:16")
    src_size = (1280, 720)
    intervals = crop_intervals(
        track, start=0.5, end=2.5, out_size=out_size, src_size=src_size
    )
    assert intervals, "crop intervals produced"
    assert intervals[0][0] == 0.0
    assert abs(intervals[-1][1] - duration) < 1e-9
    for (t0a, t1a, _, _), (t0b, _, _, _) in zip(intervals, intervals[1:]):
        assert t0b >= t1a - 1e-9, "intervals ordered and non-overlapping"
    plane_w, plane_h = plane_size(*src_size, *out_size)
    max_x, max_y = plane_w - out_size[0], plane_h - out_size[1]
    for _, _, x, y in intervals:
        assert 0 <= x <= max_x and 0 <= y <= max_y

    text = build_cmds(intervals)
    lines = [line for line in text.splitlines() if line]
    assert len(lines) == len(intervals)
    pattern = re.compile(r"^\d+\.\d{3} crop x \d+, crop y \d+;$")
    assert all(pattern.match(line) for line in lines), lines
    assert text.endswith("\n")

    value = sendcmd_filter(r"C:\cache\clip_1000.cmds")
    assert value == "sendcmd=f='C\\:/cache/clip_1000.cmds'"
    assert sendcmd_filter(r"C:\a'b:c.cmds") == "sendcmd=f='C\\:/a\\'b\\:c.cmds'"

    # box_at holds the last known sample
    assert box_at([], 0.5) is None
    box = box_at(track, 1.5)
    assert box == (0.6, 0.4, 0.2, 0.2)
    print("PASS intervals/cmds: clip rebase, merged intervals, sendcmd file")


# ------------------------------------------------------------- database
def test_track_db_roundtrip() -> None:
    # media_tracks.media_id is a foreign key - the media row must exist
    service, project = make_project()
    media_id = project.media[0].id or 0
    assert media_id > 0
    keyframes = [
        TrackKeyframe(t=0.0, x=0.1, y=0.2, w=0.3, h=0.4),
        TrackKeyframe(t=1.5, x=0.5, y=0.5, w=0.2, h=0.2),
    ]
    try:
        database = ProjectDatabase(project.path)
        try:
            TrackRepository(database).upsert(
                media_id,
                keyframes,
                detector="yunet",
                frames=20,
                hits=12,
                width=1280,
                height=720,
            )
        finally:
            database.close()

        database = ProjectDatabase(project.path)
        try:
            info = TrackRepository(database).get(media_id)
            assert info is not None
            assert info.keyframes == keyframes
            assert info.detector == "yunet"
            assert info.hits == 12 and info.frames == 20
            assert info.width == 1280 and info.height == 720
            assert info.has_faces
            assert TrackRepository(database).get(999999) is None

            # upsert replaces, delete removes
            TrackRepository(database).upsert(
                media_id, keyframes[:1], detector="yunet", frames=4, hits=1
            )
            replaced = TrackRepository(database).get(media_id)
            assert replaced is not None and len(replaced.keyframes) == 1
            TrackRepository(database).delete_for_media(media_id)
            assert TrackRepository(database).get(media_id) is None
        finally:
            database.close()
        print("PASS track db: upsert/get/replace/delete round-trip")
    finally:
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        _ = service


# ---------------------------------------------------------- export args
def test_export_build_args() -> None:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    info = VideoService().probe(SAMPLE)
    out_w, out_h = resolution_for("9:16")
    plane_w, plane_h = plane_size(info.width, info.height, out_w, out_h)
    track = [
        TrackKeyframe(t=0.0, x=0.1, y=0.1, w=0.2, h=0.2),
        TrackKeyframe(t=1.0, x=0.5, y=0.5, w=0.2, h=0.2),
        TrackKeyframe(t=2.5, x=0.8, y=0.5, w=0.2, h=0.2),
    ]

    service = ExportService()
    smart = ExportRequest(
        source=SAMPLE,
        output=ROOT / "cache" / "smart_args_test.mp4",
        start=0.5,
        end=2.5,
        aspect="9:16",
        settings=ExportSettings(),
        smart_track=track,
        source_size=(info.width, info.height),
    )
    try:
        args = service.build_args(smart)
        vf = args[args.index("-vf") + 1]
        assert "sendcmd=f=" in vf, vf
        assert f"scale={plane_w}:{plane_h}" in vf, vf
        assert f"crop={out_w}:{out_h}:x=0:y=0" in vf, vf
        assert "force_original_aspect_ratio" not in vf, vf

        assert smart._smart_cmds is not None, "cmds file registered"
        cmds = Path(smart._smart_cmds)
        assert cmds.exists() and cmds.suffix == ".cmds"
        content = cmds.read_text(encoding="ascii")
        assert content == build_cmds(
            crop_intervals(
                track,
                start=0.5,
                end=2.5,
                out_size=(out_w, out_h),
                src_size=(info.width, info.height),
            )
        )
    finally:
        service._cleanup_smart(smart)
    assert smart._smart_cmds is None
    assert not cmds.exists(), "cmds file cleaned up"

    # no track -> classic center crop
    classic = ExportRequest(
        source=SAMPLE,
        output=ROOT / "cache" / "classic_args_test.mp4",
        start=0.5,
        end=2.5,
        aspect="9:16",
        settings=ExportSettings(),
    )
    classic_args = service.build_args(classic)
    classic_vf = classic_args[classic_args.index("-vf") + 1]
    assert "force_original_aspect_ratio=increase" in classic_vf
    assert "sendcmd=" not in classic_vf
    assert classic._smart_cmds is None
    print("PASS export args: smart sendcmd chain + classic fallback")


# -------------------------------------------------------- real detection
def test_real_face_track_sample() -> None:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    ok, message = availability()
    assert ok, message

    app = QApplication.instance() or QApplication([])
    from app.workers.smart_track_worker import SmartTrackWorker

    info = VideoService().probe(SAMPLE)
    seen: list[dict] = []
    errors: list[str] = []
    progress: list[str] = []

    worker = SmartTrackWorker(
        media_id=3, media_path=SAMPLE, duration=info.duration
    )
    worker.completed.connect(lambda payload: seen.append(dict(payload)))
    worker.error.connect(lambda message: errors.append(message))
    worker.progress.connect(lambda message: progress.append(message))
    worker.run()  # synchronous: same thread, slots run inline

    assert not errors, errors
    assert len(seen) == 1, "completed emitted once"
    payload = seen[0]
    assert payload["media_id"] == 3
    assert payload["frames"] > 0, "frames sampled"
    assert payload["width"] == info.width and payload["height"] == info.height
    assert payload["detector"] == "yunet"
    assert payload["hits"] == 0, "sample video has no faces"
    assert payload["keyframes"] == [], "no track without face hits"
    assert any(message.startswith("Scanning") or "Smoothing" in message
               for message in progress), progress
    _ = app
    print("PASS real track: worker on sample (0 face hits, graceful empty)")


# ----------------------------------------------------- real ffmpeg export
def test_real_smart_export() -> None:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    info = VideoService().probe(SAMPLE)
    assert info.duration > 3.0, "sample must cover the test clip"

    out_dir = ROOT / "output" / "smart_export_test"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "smart_out.mp4"
    track = [
        TrackKeyframe(t=0.0, x=0.05, y=0.2, w=0.25, h=0.25),
        TrackKeyframe(t=1.5, x=0.5, y=0.3, w=0.25, h=0.25),
        TrackKeyframe(t=3.0, x=0.7, y=0.4, w=0.25, h=0.25),
    ]
    request = ExportRequest(
        source=SAMPLE,
        output=output,
        start=0.5,
        end=2.5,
        aspect="9:16",
        settings=ExportSettings(),
        smart_track=track,
        source_size=(info.width, info.height),
    )
    service = ExportService()
    try:
        result = service.export(request)
        assert result == output and output.exists()
        assert output.stat().st_size > 0

        out_info = VideoService().probe(output)
        assert (out_info.width, out_info.height) == (1080, 1920)
        assert abs(out_info.duration - 2.0) <= 0.25, out_info.duration
    finally:
        assert request._smart_cmds is None, "cmds file cleaned up"
        leftover = list(
            (ROOT / "cache" / "smart").glob("smart_out_*.cmds")
        )
        assert not leftover, leftover
        shutil.rmtree(out_dir, ignore_errors=True)
    print(
        "PASS real smart export: sendcmd crop 1080x1920, "
        "duration ~2.0s, cmds cleaned"
    )


# ------------------------------------------------------------- gui page
def make_project() -> tuple[ProjectService, object]:
    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(PROJECT_NAME)
    project.ensure_dirs()

    info = VideoService().probe(SAMPLE)
    media = MediaItem(
        name="smart_source",
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
    return service, project


def test_gui_smart_page() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True

    assert window.pages.count() == 11, window.pages.count()
    assert window.sidebar._buttons["smart"].isEnabled()
    window.sidebar.select("smart")
    assert window.pages.currentIndex() == 9

    service, project = make_project()
    try:
        window._load_project(project, "loaded")
        window.sidebar.select("smart")
        assert window.pages.currentIndex() == 9
        assert window.smart_panel.media_box.count() == 1
        assert window.smart_panel.busy is False
        assert "No face track" in window.smart_panel.track_label.text()

        media = project.media[0]
        media_id = media.id or 0

        # simulated worker completion -> stored + status + overlay
        window.smart_panel.set_overlay_checked(True)
        window._on_smart_completed(
            {
                "media_id": media_id,
                "keyframes": [
                    {"t": 0.0, "x": 0.2, "y": 0.3, "w": 0.2, "h": 0.2},
                    {"t": 1.0, "x": 0.5, "y": 0.3, "w": 0.2, "h": 0.2},
                ],
                "detector": "yunet",
                "frames": 10,
                "hits": 5,
                "width": media.width,
                "height": media.height,
                "duration": media.duration,
            }
        )
        assert "Tracked 2 keyframes" in window.smart_panel.status_label.text()
        assert "keyframes" in window.smart_panel.track_label.text()
        keys = window._load_track_keys(media_id)
        assert keys is not None and len(keys) == 2
        assert window.player.track_overlay.active

        # clear -> DB row gone, overlay removed
        window._on_smart_clear()
        assert window._load_track_keys(media_id) is None
        assert not window.player.track_overlay.active
        assert "No face track" in window.smart_panel.track_label.text()

        # no track yet -> overlay request clears instead of crashing
        window.smart_panel.set_overlay_checked(True)
        assert not window.player.track_overlay.active
    finally:
        window.smart_panel.set_overlay_checked(False)
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
        _ = service
        _ = app
    print("PASS gui: smart page, simulated completion, overlay, clear")


def main() -> None:
    test_crop_math()
    test_smoothing()
    test_clip_intervals_cmds()
    test_track_db_roundtrip()
    test_export_build_args()
    test_real_face_track_sample()
    test_real_smart_export()
    test_gui_smart_page()
    print("ALL SMART CROP TESTS PASSED")


if __name__ == "__main__":
    main()
