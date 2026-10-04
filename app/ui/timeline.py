from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from app.utils.timecode import format_clock, format_timecode

RULER_HEIGHT = 26
TRACK_TOP = 34
TRACK_HEIGHT = 46
HANDLE_WIDTH = 10
MIN_CLIP_SECONDS = 0.05
DEFAULT_PPS = 8.0  # pixels per second at zoom 1x
MIN_PPS = 0.2
MAX_PPS = 400.0

COLOR_BG = QColor("#141418")
COLOR_BORDER = QColor("#26262c")
COLOR_RULER = QColor("#1a1a1f")
COLOR_TICK = QColor("#3a3a44")
COLOR_TEXT = QColor("#83838d")
COLOR_TRACK = QColor("#202027")
COLOR_TRACK_BORDER = QColor("#2c2c34")
COLOR_MEDIA = QColor("#2b3446")
COLOR_MEDIA_BORDER = QColor("#3a4a68")
COLOR_SELECTED = QColor(47, 111, 235, 200)
COLOR_SELECTED_BORDER = QColor("#4d84ff")
COLOR_HANDLE = QColor("#9ec1ff")
COLOR_PLAYHEAD = QColor("#ffffff")
COLOR_PLAYHEAD_BG = QColor(255, 255, 255, 40)
COLOR_CLIP_ADDED = QColor(70, 130, 180, 220)
COLOR_CLIP_REMOVED = QColor(180, 50, 50, 180)


class Timeline(QWidget):
    """Multi-video-track timeline with playhead, clip blocks, trim handles, split/duplicate,
    snapping, and undo-aware range editing."""

    seekRequested = Signal(float)          # seconds
    rangeEdited = Signal(float, float)     # start, end of selected clip seconds
    rangeEditFinished = Signal(float, float)

    # Multi-clip signals
    clipSelectedChanged = Signal(int)      # index into self.clips, -1 if none
    clipMoved = Signal(int, float, float)  # clip_id, new_start, new_end
    clipSplit = Signal(int, float)         # clip_id, split_point (new end of left clip)
    clipDuplicate = Signal(int, float, float)  # new_clip_id, start, end

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self.duration = 0.0
        self.clips: list[dict] = []  # [{id, media_id, name, timeline_start, timeline_end, order}]
        self.selected_clip_idx: int | None = None
        self.pps = DEFAULT_PPS
        self._offset = 0.0  # scroll offset in seconds
        self._drag: str | None = None   # None | "playhead" | "clip_start" | "clip_end" | "clip_move"
        self._hover_clip_idx: int | None = None
        self._hover_handle: str | None = None  # "start" | "end"
        self.media_label = ""

    # ---- public API ----

    def set_media(self, duration: float, label: str = "") -> None:
        self.duration = max(0.0, duration)
        self.has_media = self.duration > 0
        self.media_label = label
        # Re‑layout all clips within [0, duration]
        for clip in self.clips:
            clip["timeline_end"] = min(clip["timeline_end"], self.duration)
            clip["timeline_start"] = max(clip["timeline_start"], 0.0)
        # Select first clip if none selected and we have clips
        if not self.selected_clip_idx and self.clips:
            self.selected_clip_idx = 0
        self._offset = 0.0
        self.pps = DEFAULT_PPS
        self.update()

    def set_duration(self, duration: float) -> None:
        self.duration = max(0.0, duration)
        self.has_media = self.duration > 0
        # Clip range validation
        for clip in self.clips:
            clip["timeline_end"] = min(clip["timeline_end"], self.duration)
            clip["timeline_start"] = max(clip["timeline_start"], 0.0)
        self.update()

    def set_position(self, seconds: float) -> None:
        self.position = max(0.0, min(seconds, self.duration or seconds))
        self._ensure_visible(self.position)
        self.update()

    def set_range(self, start: float, end: float) -> None:
        """Set the selected clip's timeline range. If no clip selected, creates first clip."""
        if not self.clips:
            # Create first clip spanning the media duration
            clip = {
                "id": max((c["id"] for c in self.clips), default=0) + 1,
                "media_id": 0,
                "name": "Clip 1",
                "timeline_start": max(0.0, start),
                "timeline_end": min(self.duration, end),
                "order": len(self.clips),
            }
            self.clips.append(clip)
            self.selected_clip_idx = len(self.clips) - 1
            self.clipSelectedChanged.emit(self.selected_clip_idx)
            self._offset = 0.0
            self.pps = DEFAULT_PPS
            self.update()
            return

        clip = self.clips[self.selected_clip_idx]
        clip["timeline_start"] = max(0.0, start)
        clip["timeline_end"] = min(self.duration, end)
        self.clipMoved.emit(clip["id"], clip["timeline_start"], clip["timeline_end"])
        self.update()

    def clear(self) -> None:
        self.clips = []
        self.selected_clip_idx = None
        self.duration = 0.0
        self.has_media = False
        self.media_label = ""
        self._offset = 0.0
        self.pps = DEFAULT_PPS
        self.update()

    def add_clip(self, start: float, end: float, name: str | None = None) -> int:
        """Add a new clip at the given timeline range. Returns the new clip's index."""
        start = max(0.0, start)
        end = min(self.duration, end) if self.duration else end
        if end <= start:
            end = start + MIN_CLIP_SECONDS
        new_id = max((c["id"] for c in self.clips), default=0) + 1
        new_clip = {
            "id": new_id,
            "media_id": 0,
            "name": name or "Clip",
            "timeline_start": start,
            "timeline_end": end,
            "order": len(self.clips),
        }
        # Insert in order position, shifting subsequent clips
        insert_idx = len(self.clips)
        self.clips.append(new_clip)
        # Re-number orders
        for i, c in enumerate(self.clips):
            c["order"] = i
        self.selected_clip_idx = insert_idx
        self.clipSelectedChanged.emit(self.selected_clip_idx)
        self._offset = 0.0
        self.pps = DEFAULT_PPS
        self.update()
        return insert_idx

    def remove_clip(self, clip_id: int) -> None:
        """Remove a clip by id, shifting subsequent clips left."""
        self.clips = [c for c in self.clips if c["id"] != clip_id]
        # Re-number orders
        for i, c in enumerate(self.clips):
            c["order"] = i
        # Deselect if we removed the selected clip
        if self.selected_clip_idx is not None and (
            self.selected_clip_idx >= len(self.clips)
            or self.clips[self.selected_clip_idx]["id"] != clip_id
        ):
            self.selected_clip_idx = None
            self.clipSelectedChanged.emit(-1)
        elif self.clips:
            self.selected_clip_idx = min(self.selected_clip_idx, len(self.clips) - 1)
            self.clipSelectedChanged.emit(self.selected_clip_idx)
        self.update()

    # ---- coordinate helpers ----

    def _time_to_x(self, seconds: float) -> float:
        return (seconds - self._offset) * self.pps + 6.0

    def _x_to_time(self, x: float) -> float:
        return max(0.0, (x - 6.0) / self.pps + self._offset)

    def _clamp_offset(self) -> None:
        if self.duration <= 0:
            self._offset = 0.0
            return
        max_offset = max(0.0, self.duration - 4.0 / self.pps)
        self._offset = max(0.0, min(self._offset, max_offset))

    def _ensure_visible(self, seconds: float) -> None:
        if not self.has_media or self.pps <= 0:
            return
        x = self._time_to_x(seconds)
        margin = 40
        if x < margin:
            self._offset = max(0.0, seconds - margin / self.pps)
        elif x > self.width() - margin:
            self._offset = seconds - (self.width() - margin) / self.pps
        self._clamp_offset()

    # ---- painting ----

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), COLOR_BG)

        if not self.has_media:
            painter.setPen(QColor("#4d4d55"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "No media loaded - import a video to use the timeline",
            )
            return

        width = self.width()
        self._draw_ruler(painter, width)
        self._draw_track(painter, width)
        self._draw_clips(painter, width)
        self._draw_playhead(painter, width)
        painter.end()

    def _draw_ruler(self, painter: QPainter, width: int) -> None:
        painter.fillRect(QRectF(0, 0, width, RULER_HEIGHT), COLOR_RULER)
        painter.setPen(QPen(COLOR_BORDER, 1))
        painter.drawLine(0, RULER_HEIGHT, width, RULER_HEIGHT)

        step = self._tick_step()
        first = int(self._offset / step) * step
        end_time = self._offset + width / self.pps
        font = QFont("Consolas", 8)
        painter.setFont(font)

        seconds = first
        while seconds <= end_time + step:
            x = self._time_to_x(seconds)
            if x >= -20:
                major = abs(seconds % (step * 5)) < 1e-6 or seconds == 0
                tick_len = 9 if major else 5
                painter.setPen(QPen(COLOR_TICK, 1))
                painter.drawLine(
                    QPointF(x, RULER_HEIGHT - tick_len),
                    QPointF(x, RULER_HEIGHT),
                )
                if major:
                    painter.setPen(COLOR_TEXT)
                    painter.drawText(
                        QPointF(x + 3, RULER_HEIGHT - 11), format_clock(seconds)
                    )
            seconds += step

    def _tick_step(self) -> float:
        targets = [
            0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600,
        ]
        min_seconds_per_pixel = 1.0 / max(self.pps, 1e-6)
        for target in targets:
            if target >= min_seconds_per_pixel * 80:
                return target
        return targets[-1]

    def _draw_track(self, painter: QPainter, width: int) -> None:
        rect = QRectF(0, TRACK_TOP, width, TRACK_HEIGHT)
        painter.setPen(QPen(COLOR_TRACK_BORDER, 1))
        painter.setBrush(COLOR_TRACK)
        painter.drawRoundedRect(rect, 6, 6)

        label_font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        painter.setFont(label_font)
        painter.setPen(QColor("#9a9aa4"))
        painter.drawText(
            QRectF(8, TRACK_TOP, width - 16, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "VIDEO   " + self.media_label,
        )

        # full media bar
        x0 = self._time_to_x(0)
        x1 = self._time_to_x(self.duration)
        media_rect = QRectF(
            x0, TRACK_TOP + 18, max(2.0, x1 - x0), TRACK_HEIGHT - 24
        )
        painter.setPen(QPen(COLOR_MEDIA_BORDER, 1))
        painter.setBrush(COLOR_MEDIA)
        painter.drawRoundedRect(media_rect, 4, 4)

        # waveform-ish ticks for texture
        painter.setPen(QPen(QColor("#38455e"), 1))
        step_px = 6
        x = media_rect.left() + 3
        while x < media_rect.right() - 3:
            import math

            seed = (x - media_rect.left()) / max(media_rect.width(), 1.0)
            h = 4 + 6 * abs(math.sin(seed * 60.0))
            painter.drawLine(
                QPointF(x, media_rect.center().y() - h / 2),
                QPointF(x, media_rect.center().y() + h / 2),
            )
            x += step_px

    def _clip_color(self, idx: int) -> QColor:
        """Distinct color for each clip."""
        hues = [
            QColor("#4287f5"),  # blue
            QColor("#f54242"),  # red
            QColor("#42f5a1"),  # green
            QColor("#f5c442"),  # yellow
            QColor("#a142f5"),  # purple
            QColor("#42e5f5"),  # cyan
        ]
        return hues[idx % len(hues)]

    def _draw_clips(self, painter: QPainter, width: int) -> None:
        for idx, clip in enumerate(self.clips):
            ccolor = self._clip_color(idx)
            ts = clip["timeline_start"]
            te = clip["timeline_end"]
            sx = self._time_to_x(ts)
            ex = self._time_to_x(te)
            clip_width = max(4.0, ex - sx)

            # clip rectangle
            clip_rect = QRectF(
                sx, TRACK_TOP + 14, clip_width, TRACK_HEIGHT - 16
            )

            # fill with distinct color
            painter.setPen(QPen(ccolor, 1.5))
            painter.setBrush(ccolor)
            painter.drawRoundedRect(clip_rect, 4, 4)

            # label inside clip
            text = f"{clip['name']}\n{format_timecode(ts)} → {format_timecode(te)}"
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            painter.drawText(
                QRectF(clip_rect.left() + 2, clip_rect.top() + 2,
                       clip_rect.width() - 4, clip_rect.height() - 4),
                Qt.AlignmentFlag.AlignCenter, text,
            )

            # draw handles if this is the selected clip
            if idx == self.selected_clip_idx:
                for handle_x in (sx, ex):
                    handle = QRectF(
                        handle_x - HANDLE_WIDTH / 2,
                        clip_rect.top() - 4,
                        HANDLE_WIDTH,
                        clip_rect.height() + 8,
                    )
                    painter.setPen(QPen(QColor("#1c2740"), 1))
                    painter.setBrush(COLOR_HANDLE)
                    painter.drawRoundedRect(handle, 3, 3)

                # draw selection border
                painter.setPen(QPen(COLOR_SELECTED_BORDER, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(clip_rect, 4, 4)

    def _draw_playhead(self, painter: QPainter, width: int) -> None:
        x = self._time_to_x(self.position)
        if x < -5 or x > width + 5:
            return
        painter.setPen(QPen(COLOR_PLAYHEAD, 1.5))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        path = QPainterPath()
        path.moveTo(x - 5, 0)
        path.lineTo(x + 5, 0)
        path.lineTo(x, 8)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_PLAYHEAD)
        painter.drawPath(path)

    # ---- interaction ----

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.has_media:
            return
        pos = event.position()
        x = int(pos.x())
        y = int(pos.y())
        in_track = TRACK_TOP - 6 <= y <= TRACK_TOP + TRACK_HEIGHT + 6

        if not in_track:
            return

        # Determine which clip (if any) is under the cursor
        hit_clip_idx = None
        hit_handle = None
        for i, clip in enumerate(reversed(self.clips)):
            idx = len(self.clips) - 1 - i
            ts = clip["timeline_start"]
            te = clip["timeline_end"]
            sx = self._time_to_x(ts)
            ex = self._time_to_x(te)
            clip_width = max(4.0, ex - sx)
            clip_rect = QRectF(sx, TRACK_TOP + 14, clip_width, TRACK_HEIGHT - 16)

            # Check handle hit (start or end)
            for handle_name, hx in (("start", sx), ("end", ex)):
                if abs(x - hx) <= HANDLE_WIDTH and clip_rect.contains(
                    QPointF(hx, y)
                ):
                    hit_clip_idx = idx
                    hit_handle = handle_name
                    break
            if hit_clip_idx is not None:
                break

            # Check clip body drag (move)
            if clip_rect.contains(QPointF(x, y)):
                hit_clip_idx = idx
                hit_handle = "clip_move"
                break

        if hit_clip_idx is not None:
            self.selected_clip_idx = hit_clip_idx
            self.clipSelectedChanged.emit(self.selected_clip_idx)
            self._drag = hit_handle  # "start" | "end" | "clip_move" | None
            self._hover_clip_idx = hit_clip_idx
            self._hover_handle = hit_handle
            self.update()
            return

        # Click on track background → select nearest clip or playhead
        self._drag = "playhead"
        self._emit_seek(x)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.has_media:
            return
        x = event.position().x()
        y = event.position().y()
        in_track = TRACK_TOP - 6 <= y <= TRACK_TOP + TRACK_HEIGHT + 6

        if not in_track:
            if self._drag is not None:
                self._drag = None
                self.update()
            return

        if self._drag is None:
            # Hover detection
            sx = self._time_to_x(self.clips[self.selected_clip_idx]["timeline_start"]) \
                if self.selected_clip_idx is not None else 0
            ex = self._time_to_x(self.clips[self.selected_clip_idx]["timeline_end"]) \
                if self.selected_clip_idx is not None else 0
            # ... simple hover, could expand
            self.update()
            return

        if self._drag == "playhead":
            self._emit_seek(x)
            return

        if self._drag in ("start", "end"):
            time = self._x_to_time(x)
            clip = self.clips[self.selected_clip_idx]
            if self._drag == "start":
                new_start = min(time, clip["timeline_end"] - MIN_CLIP_SECONDS)
                clip["timeline_start"] = max(0.0, new_start)
            else:  # "end"
                new_end = max(time, clip["timeline_start"] + MIN_CLIP_SECONDS)
                if self.duration:
                    new_end = min(new_end, self.duration)
                clip["timeline_end"] = new_end
            self.rangeEdited.emit(clip["timeline_start"], clip["timeline_end"])
            self._hover_handle = self._drag
            self.update()
            return

        if self._drag == "clip_move":
            time = self._x_to_time(x)
            clip = self.clips[self.selected_clip_idx]
            # Apply snapping
            snapped = self._snap_time(time, clip["timeline_start"], clip["timeline_end"])
            clip["timeline_start"] = snapped["start"]
            clip["timeline_end"] = snapped["end"]
            self.clipMoved.emit(clip["id"], snapped["start"], snapped["end"])
            self.update()
            return

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag in {"start", "end"}:
            self.rangeEditFinished.emit(self.clips[self.selected_clip_idx]["timeline_start"],
                                        self.clips[self.selected_clip_idx]["timeline_end"])
        if self._drag == "playhead":
            self.seekRequested.emit(self.position)
        if self._drag == "clip_move":
            # Emit final moved state
            if self.selected_clip_idx is not None:
                clip = self.clips[self.selected_clip_idx]
                self.clipMoved.emit(clip["id"], clip["timeline_start"], clip["timeline_end"])
        self._drag = None
        self._hover_clip_idx = None
        self._hover_handle = None
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self.has_media:
            self._emit_seek(event.position().x())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self.has_media:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.25 if delta > 0 else 0.8
        self.zoom(factor, anchor_x=int(event.position().x()))
        event.accept()

    def _snap_time(self, time: float, clip_start: float, clip_end: float) -> dict:
        """Snap timeline time to clip boundaries or grid."""
        # Snap to nearest 0.5s grid, but not closer than MIN_CLIP_SECONDS
        grid = 0.5
        snapped = round(time / grid) * grid
        snapped = max(MIN_CLIP_SECONDS, min(self.duration - MIN_CLIP_SECONDS, snapped))
        # Ensure start < end and minimum duration
        new_start = max(0.0, snapped - 0.5)
        new_end = min(self.duration, snapped + 0.5)
        # Clamp to not overlap other clips excessively (simple version)
        new_start = max(0.0, min(new_start, clip_end - MIN_CLIP_SECONDS))
        new_end = min(self.duration, max(new_end, clip_start + MIN_CLIP_SECONDS))
        return {"start": new_start, "end": new_end}

    # ---- signals emulation for backward compat ----
    # The signals seekRequested, rangeEdited, rangeEditFinished are already defined
    # at class level and emitted as-is. The main_window connects to them unchanged.

    @property
    def clip_start(self) -> float:
        """Backward‑compatible: start of the selected clip, or 0.0."""
        if self.has_media and self.selected_clip_idx is not None:
            return self.clips[self.selected_clip_idx]["timeline_start"]
        return 0.0

    @property
    def clip_end(self) -> float:
        """Backward‑compatible: end of the selected clip, or the media duration."""
        if self.has_media and self.selected_clip_idx is not None:
            return self.clips[self.selected_clip_idx]["timeline_end"]
        return self.duration if self.has_media else 0.0