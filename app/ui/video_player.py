from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QElapsedTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.utils.timecode import format_timecode

ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
}


class AspectOverlay(QWidget):
    """Paints the target aspect framing over the preview (letterbox mask)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._aspect = "16:9"
        self._active = False

    def set_aspect(self, aspect: str) -> None:
        if aspect in ASPECT_RATIOS:
            self._aspect = aspect
            self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def paintEvent(self, event) -> None:
        if not self._active:
            return
        ratio_w, ratio_h = ASPECT_RATIOS.get(self._aspect, (16, 9))
        target = ratio_w / ratio_h
        widget_ratio = self.width() / max(1, self.height())
        if widget_ratio > target:
            frame_h = self.height()
            frame_w = frame_h * target
        else:
            frame_w = self.width()
            frame_h = frame_w / target
        x = (self.width() - frame_w) / 2
        y = (self.height() - frame_h) / 2
        frame = QRectF(x, y, frame_w, frame_h)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        mask = QColor(0, 0, 0, 165)
        painter.fillRect(QRectF(0, 0, self.width(), frame.top()), mask)
        painter.fillRect(QRectF(0, frame.bottom(), self.width(), self.height() - frame.bottom()), mask)
        painter.fillRect(QRectF(0, frame.top(), frame.left(), frame.height()), mask)
        painter.fillRect(QRectF(frame.right(), frame.top(), self.width() - frame.right(), frame.height()), mask)

        painter.setPen(QPen(QColor(158, 193, 255, 190), 1))
        painter.drawRect(frame)
        painter.setPen(QPen(QColor(158, 193, 255, 70), 1, Qt.PenStyle.DashLine))
        painter.drawLine(
            int(frame.center().x()), int(frame.top()),
            int(frame.center().x()), int(frame.bottom()),
        )
        painter.end()


class VideoPlayer(QWidget):
    """Real video preview backed by Qt Multimedia (QMediaPlayer)."""

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    stateChanged = Signal(bool)
    mediaError = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._seeking = False
        self._pending_seek_ms: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(240)
        self.video_widget.setStyleSheet(
            "QVideoWidget { background-color: #000000; border: 1px solid #26262c;"
            " border-radius: 8px; }"
        )
        self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)

        self.audio = QAudioOutput()
        self.audio.setVolume(0.8)

        self.player = QMediaPlayer()
        self.player.setVideoOutput(self.video_widget)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)

        self.placeholder = QLabel("Import a video to begin", self.video_widget)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet(
            "color: #4d4d55; background: transparent; font-size: 13px; border: none;"
        )

        self.overlay = AspectOverlay(self.video_widget)
        self.overlay.set_active(False)
        self._overlay_geom = None

        root.addWidget(self.video_widget, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.play_button = QPushButton("▶")
        self.play_button.setFixedSize(36, 30)
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setStyleSheet(
            "QPushButton { background-color: #2f6feb; border: 1px solid #3b7cf0;"
            " border-radius: 6px; color: white; font-size: 13px; }"
            "QPushButton:hover { background-color: #3b7cf0; }"
            "QPushButton:disabled { background-color: #24365c; border-color: #24365c; }"
        )
        self.play_button.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_button)

        self.time_label = QLabel("00:00:00.000")
        self.time_label.setFixedWidth(96)
        self.time_label.setStyleSheet("color: #c9c9d1; font-family: Consolas, monospace;")
        controls.addWidget(self.time_label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(self._on_slider_press)
        self.slider.sliderReleased.connect(self._on_slider_release)
        self.slider.sliderMoved.connect(self._on_slider_move)
        controls.addWidget(self.slider, 1)

        self.duration_label = QLabel("00:00:00.000")
        self.duration_label.setFixedWidth(96)
        self.duration_label.setStyleSheet("color: #83838d; font-family: Consolas, monospace;")
        controls.addWidget(self.duration_label)

        self.mute_button = QPushButton("🔊")
        self.mute_button.setFixedSize(34, 30)
        self.mute_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_button.clicked.connect(self.toggle_mute)
        controls.addWidget(self.mute_button)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setFixedWidth(90)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.valueChanged.connect(self._on_volume)
        controls.addWidget(self.volume)

        root.addLayout(controls)
        self._set_controls_enabled(False)

    # ---- public API ----
    def set_aspect(self, aspect: str, active: bool = True) -> None:
        self.overlay.set_aspect(aspect)
        self.overlay.set_active(active)
        self._relayout_overlay()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_overlay()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._relayout_overlay()

    def _relayout_overlay(self) -> None:
        if hasattr(self, "overlay"):
            self.overlay.setGeometry(self.video_widget.rect())
            self.placeholder.setGeometry(self.video_widget.rect())

    @property
    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def position_ms(self) -> int:
        return self.player.position()

    @property
    def current_source(self) -> str:
        return self.player.source().toLocalFile()

    def load(self, path: Path | str) -> None:
        path = Path(path)
        if not path.exists():
            self.mediaError.emit(f"Video file not found: {path}")
            return
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self.placeholder.setVisible(False)
        self._set_controls_enabled(True)
        self.play()

    def clear(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.placeholder.setVisible(True)
        self._set_controls_enabled(False)

    def play(self) -> None:
        if self.current_source:
            self.player.play()

    def pause(self) -> None:
        if self.is_playing:
            self.player.pause()

    def toggle_play(self) -> None:
        if not self.current_source:
            return
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def seek_ms(self, ms: int) -> None:
        ms = max(0, min(ms, self._duration_ms if self._duration_ms > 0 else ms))
        ready = (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        )
        if self.player.mediaStatus() not in ready:
            self._pending_seek_ms = ms
        self.player.setPosition(ms)
        self._sync_slider(ms)

    def seek_seconds(self, seconds: float) -> None:
        self.seek_ms(int(round(seconds * 1000)))

    def seek_relative(self, delta_ms: int) -> None:
        self.seek_ms(self.player.position() + delta_ms)

    def set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)
        self.mute_button.setText("🔇" if muted else "🔊")

    def toggle_mute(self) -> None:
        self.set_muted(not self.audio.isMuted())

    @property
    def volume_value(self) -> int:
        return self.volume.value()

    def set_volume_value(self, value: int) -> None:
        self.volume.setValue(max(0, min(100, value)))

    # ---- internals ----
    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.play_button, self.slider, self.mute_button, self.volume):
            widget.setEnabled(enabled)

    def _on_position(self, ms: int) -> None:
        if not self._seeking:
            self._sync_slider(ms)
        self.time_label.setText(format_timecode(ms / 1000))
        self.positionChanged.emit(ms)

    def _sync_slider(self, ms: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(ms)
        self.slider.blockSignals(False)

    def _on_duration(self, ms: int) -> None:
        self._duration_ms = ms
        self.slider.setRange(0, max(0, ms))
        self.duration_label.setText(format_timecode(ms / 1000))
        self.durationChanged.emit(ms)

    def _on_media_status(self, status) -> None:
        if self._pending_seek_ms is None:
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            ms = self._pending_seek_ms
            self._pending_seek_ms = None
            self.player.setPosition(ms)

    def wait_until_loaded(self, timeout_ms: int = 1500) -> bool:
        """Spin the event loop until the media is ready (used by import flow)."""
        ready = (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.InvalidMedia,
        )
        timer = QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            if self.player.mediaStatus() in ready:
                QApplication.processEvents()
                return self.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadedMedia
            QApplication.processEvents()
        return False

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("⏸" if playing else "▶")
        self.stateChanged.emit(playing)

    def _on_error(self, _error=None, message: str = "") -> None:
        text = message or getattr(self.player.errorString(), "strip", lambda: "")()
        if not text and self.player.error() != QMediaPlayer.Error.NoError:
            text = str(self.player.error())
        text = text or "Video cannot be opened"
        self.mediaError.emit(f"Video cannot be opened: {text}")

    def _on_slider_press(self) -> None:
        self._seeking = True

    def _on_slider_release(self) -> None:
        self._seeking = False
        self.seek_ms(self.slider.value())

    def _on_slider_move(self, value: int) -> None:
        self.time_label.setText(format_timecode(value / 1000))

    def _on_volume(self, value: int) -> None:
        self.audio.setVolume(value / 100.0)
        self.set_muted(value == 0)
