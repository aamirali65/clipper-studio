from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Sequence

from app.models.track import TrackKeyframe
from app.utils.logging import get_logger
from app.utils.paths import app_root

log = get_logger("face_track")

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
DETECTOR_NAME = "yunet"
SAMPLE_FPS = 1.5  # detection samples per second of video
MAX_GAP = 4.0  # largest hole that still gets interpolated
INTERVAL_STEP = 0.5  # max seconds between interpolated keyframes
DETECT_MAX_SIDE = 640  # frames are downscaled to this before detection
SCORE_THRESHOLD = 0.5

Box = tuple[float, float, float, float]  # x, y, w, h (normalized)
Interval = tuple[float, float, int, int]  # t0, t1, crop x, crop y (pixels)


class TrackError(RuntimeError):
    pass


def model_path() -> Path:
    return app_root() / "assets" / MODEL_NAME


def cv2_available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def model_available() -> bool:
    return model_path().exists()


def availability() -> tuple[bool, str]:
    if not cv2_available():
        return (
            False,
            "opencv is not installed - run: python -m pip install "
            "\"opencv-python-headless>=4.9\"",
        )
    if not model_available():
        return False, f"Face model missing: {model_path().name} (assets/)"
    return True, ""


# ------------------------------------------------------------- detection
def _create_detector(cv2, model: Path, size: tuple[int, int]):
    try:  # OpenCV 5 signature: (model, config, input_size, ...)
        return cv2.FaceDetectorYN.create(
            str(model), "", size, SCORE_THRESHOLD, 0.3, 5000
        )
    except (cv2.error, TypeError):  # OpenCV 4 signature: (model, input_size, ...)
        return cv2.FaceDetectorYN.create(
            str(model), size, SCORE_THRESHOLD, 0.3, 5000
        )


def detect_size(width: int, height: int) -> tuple[int, int]:
    """Detection input size: the source aspect scaled down to DETECT_MAX_SIDE."""
    width = max(2, width)
    height = max(2, height)
    if max(width, height) <= DETECT_MAX_SIDE:
        return width - (width % 2), height - (height % 2)
    if width >= height:
        det_w = DETECT_MAX_SIDE
        det_h = max(2, int(round(height * DETECT_MAX_SIDE / width)))
    else:
        det_h = DETECT_MAX_SIDE
        det_w = max(2, int(round(width * DETECT_MAX_SIDE / height)))
    return det_w - (det_w % 2), det_h - (det_h % 2)


def pick_largest_face(
    faces: Sequence[Sequence[float]] | None, width: int, height: int
) -> Box | None:
    """Largest face (closest subject) as a normalized box, or None."""
    if not faces or width <= 0 or height <= 0:
        return None
    best: tuple[float, float, float, float] | None = None
    best_area = 0.0
    for face in faces:
        try:
            x, y, w, h = float(face[0]), float(face[1]), float(face[2]), float(face[3])
        except (IndexError, TypeError, ValueError):
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best = (x, y, w, h)
    if best is None or best_area <= 0:
        return None
    x, y, w, h = best
    return (
        min(1.0, max(0.0, x / width)),
        min(1.0, max(0.0, y / height)),
        min(1.0, max(0.0, w / width)),
        min(1.0, max(0.0, h / height)),
    )


def detect_track(
    source: Path | str,
    *,
    duration: float = 0.0,
    sample_fps: float = SAMPLE_FPS,
    progress_cb: Callable[[str], None] | None = None,
    cancel_event=None,
) -> dict:
    """Scan a video for faces and return the payload for TrackRepository.

    Raises ``TrackError("cancelled")`` when ``cancel_event`` is set.
    """
    ok, message = availability()
    if not ok:
        raise TrackError(message)
    import cv2

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise TrackError(f"Cannot open video: {source}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 1.0:
            fps = 25.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if duration <= 0 and frame_count > 0:
            duration = frame_count / fps
        step = max(1, int(round(fps / max(0.25, sample_fps))))
        det_w, det_h = detect_size(width, height)
        detector = _create_detector(cv2, model_path(), (det_w, det_h))

        raw: list[tuple[float, float, float, float, float]] = []
        index = 0
        sampled = 0
        hits = 0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise TrackError("cancelled")
            if not cap.grab():
                break
            if index % step == 0:
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    break
                sampled += 1
                if frame.shape[1] != det_w or frame.shape[0] != det_h:
                    frame = cv2.resize(
                        frame, (det_w, det_h), interpolation=cv2.INTER_AREA
                    )
                _, faces = detector.detect(frame)
                box = pick_largest_face(faces, det_w, det_h)
                if box is not None:
                    hits += 1
                    raw.append((index / fps, *box))
                if progress_cb and sampled % 4 == 0:
                    if frame_count > 0:
                        percent = min(100, int(index * 100 / frame_count))
                        progress_cb(f"Scanning faces {percent}% ({sampled} frames)")
                    else:
                        progress_cb(f"Scanning faces ({sampled} frames)")
            index += 1
    finally:
        cap.release()

    if progress_cb:
        progress_cb("Smoothing track...")
    keyframes = smooth_keyframes(raw, duration=max(duration, 0.0))
    log.info(
        "face track: %d/%d sampled frames hit on %s",
        hits,
        sampled,
        Path(source).name,
    )
    return {
        "keyframes": [kf.model_dump() for kf in keyframes],
        "detector": DETECTOR_NAME,
        "frames": sampled,
        "hits": hits,
        "width": width,
        "height": height,
        "duration": duration,
    }


# ------------------------------------------------------------- smoothing
def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def smooth_keyframes(
    raw: Sequence[tuple[float, float, float, float, float]],
    *,
    duration: float,
    max_gap: float = MAX_GAP,
) -> list[TrackKeyframe]:
    """Turn sparse face samples into a dense, jitter-free keyframe series.

    ``raw`` is ``(t, x, y, w, h)`` in media seconds + normalized box.
    Positions get a 3-sample moving average, gaps up to ``max_gap`` are
    linearly interpolated, longer holds keep the previous box.
    """
    if not raw or duration <= 0:
        return []
    samples = sorted(
        (float(t), float(x), float(y), float(w), float(h))
        for t, x, y, w, h in raw
        if 0.0 <= float(t) <= duration
    )
    if not samples:
        return []
    smoothed: list[tuple[float, float, float, float, float]] = []
    for index, (t, x, y, w, h) in enumerate(samples):
        window = samples[max(0, index - 1) : index + 2]
        smoothed.append(
            (
                t,
                sum(s[1] for s in window) / len(window),
                sum(s[2] for s in window) / len(window),
                sum(s[3] for s in window) / len(window),
                sum(s[4] for s in window) / len(window),
            )
        )

    def make(t: float, sample) -> TrackKeyframe:
        _st, x, y, w, h = sample
        w = min(1.0, max(0.02, w))
        h = min(1.0, max(0.02, h))
        return TrackKeyframe(
            t=min(duration, max(0.0, t)),
            x=_clamp01(x),
            y=_clamp01(y),
            w=w,
            h=h,
        )

    keyframes: list[TrackKeyframe] = [make(0.0, smoothed[0])]
    for index in range(len(smoothed) - 1):
        t0, *_ = smoothed[index]
        t1, *_ = smoothed[index + 1]
        gap = t1 - t0
        if gap <= 0:
            continue
        if gap <= max_gap:
            steps = max(1, int(math.ceil(gap / INTERVAL_STEP)))
            for step in range(1, steps + 1):
                f = step / steps
                lerped = tuple(
                    smoothed[index][k]
                    + (smoothed[index + 1][k] - smoothed[index][k]) * f
                    for k in range(1, 5)
                )
                keyframes.append(make(t0 + gap * f, (t0, *lerped)))
        else:
            keyframes.append(make(t1, smoothed[index + 1]))
    tail = make(duration, smoothed[-1])
    if tail.t > keyframes[-1].t:
        keyframes.append(tail)
    else:
        keyframes[-1] = tail
    return keyframes


# ------------------------------------------------------------- crop math
def plane_size(
    src_w: int, src_h: int, out_w: int, out_h: int
) -> tuple[int, int]:
    """Cover-fit size of ``scale=W:H:force_original_aspect_ratio=increase``."""
    if src_w <= 0 or src_h <= 0:
        return out_w, out_h
    factor = max(out_w / src_w, out_h / src_h)
    return (
        max(out_w, int(round(src_w * factor))),
        max(out_h, int(round(src_h * factor))),
    )


def crop_xy(box: Box, out_w: int, out_h: int, plane_w: int, plane_h: int) -> tuple[int, int]:
    """Center a fixed ``out_w x out_h`` crop on a normalized box, clamped."""
    x, y, w, h = box
    center_x = (x + w / 2.0) * plane_w
    center_y = (y + h / 2.0) * plane_h
    crop_x = int(round(center_x - out_w / 2.0))
    crop_y = int(round(center_y - out_h / 2.0))
    crop_x = min(max(0, crop_x), max(0, plane_w - out_w))
    crop_y = min(max(0, crop_y), max(0, plane_h - out_h))
    return crop_x, crop_y


def box_at(
    keyframes: Sequence[TrackKeyframe], t: float
) -> Box | None:
    """Piecewise-constant box at media time ``t`` (hold between samples)."""
    if not keyframes:
        return None
    if t <= keyframes[0].t:
        first = keyframes[0]
        return (first.x, first.y, first.w, first.h)
    for kf in keyframes:
        if kf.t <= t:
            current = kf
        else:
            break
    return (current.x, current.y, current.w, current.h)


def clip_keyframes(
    keyframes: Sequence[TrackKeyframe], start: float, end: float
) -> list[TrackKeyframe]:
    """Rebase a media track onto ``[start, end)`` clip time (0-based)."""
    duration = end - start
    if not keyframes or duration <= 0:
        return []
    rebased: list[TrackKeyframe] = []
    for kf in keyframes:
        if kf.t < start:
            rebased = [TrackKeyframe(t=0.0, x=kf.x, y=kf.y, w=kf.w, h=kf.h)]
            continue
        if kf.t >= end:
            break
        rebased.append(
            TrackKeyframe(t=kf.t - start, x=kf.x, y=kf.y, w=kf.w, h=kf.h)
        )
    if not rebased:
        box = box_at(keyframes, start)
        if box is None:
            return []
        rebased = [
            TrackKeyframe(t=0.0, x=box[0], y=box[1], w=box[2], h=box[3])
        ]
    if rebased[0].t > 0.0:
        first = rebased[0]
        rebased.insert(
            0,
            TrackKeyframe(t=0.0, x=first.x, y=first.y, w=first.w, h=first.h),
        )
    if rebased[-1].t < duration:
        last = rebased[-1]
        rebased.append(
            TrackKeyframe(
                t=duration, x=last.x, y=last.y, w=last.w, h=last.h
            )
        )
    return rebased


def crop_intervals(
    keyframes: Sequence[TrackKeyframe],
    *,
    start: float,
    end: float,
    out_size: tuple[int, int],
    src_size: tuple[int, int],
) -> list[Interval]:
    """Piecewise-constant crop rectangles covering the clip, merged."""
    duration = end - start
    if duration <= 0:
        return []
    out_w, out_h = out_size
    src_w, src_h = src_size
    if out_w <= 0 or out_h <= 0 or src_w <= 0 or src_h <= 0:
        return []
    rel = clip_keyframes(keyframes, start, end)
    if not rel:
        return []
    plane_w, plane_h = plane_size(src_w, src_h, out_w, out_h)

    points: list[tuple[float, int, int]] = []
    for kf in rel:
        x, y = crop_xy((kf.x, kf.y, kf.w, kf.h), out_w, out_h, plane_w, plane_h)
        t = min(duration, max(0.0, kf.t))
        if points and abs(t - points[-1][0]) < 1e-6:
            points[-1] = (t, x, y)
            continue
        points.append((t, x, y))
    if not points:
        return []
    if points[0][0] > 0.0:
        points.insert(0, (0.0, points[0][1], points[0][2]))

    intervals: list[Interval] = []
    for index in range(len(points) - 1):
        t0, x, y = points[index]
        t1 = points[index + 1][0]
        if intervals and intervals[-1][2] == x and intervals[-1][3] == y:
            intervals[-1] = (intervals[-1][0], t1, x, y)
        else:
            intervals.append((t0, t1, x, y))
    last_t, last_x, last_y = points[-1]
    if intervals:
        intervals[-1] = (intervals[-1][0], duration, intervals[-1][2], intervals[-1][3])
    else:
        intervals.append((last_t, duration, last_x, last_y))
    return intervals


def build_cmds(intervals: Sequence[Interval]) -> str:
    """FFmpeg sendcmd file moving the crop window at interval starts."""
    lines = [f"{t0:.3f} crop x {x}, crop y {y};" for t0, _t1, x, y in intervals]
    return "\n".join(lines) + "\n"


def sendcmd_filter(cmds_path: Path | str) -> str:
    """``sendcmd=`` filter arg with Windows-safe escaping (like subtitles)."""
    value = str(Path(cmds_path)).replace("\\", "/")
    value = value.replace("'", r"\'")
    value = value.replace(":", r"\:")
    return f"sendcmd=f='{value}'"
