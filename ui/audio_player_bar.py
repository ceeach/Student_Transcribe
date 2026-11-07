"""Audio Player Control Bar widget for NotionTranscribe.

Provides a Fluent WinUI3 toolbar with playback controls, interactive scrubber slider,
variable speed selection (0.75x - 2.0x), and quick 5s skip buttons.
"""

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    PushButton,
    Slider,
    StrongBodyLabel,
    CaptionLabel,
    ToolButton,
    TransparentToolButton,
)

from audio_player import AudioPlayer


def format_ms(ms: int) -> str:
    """Format milliseconds into MM:SS or HH:MM:SS format."""
    total_sec = max(0, int(ms // 1000))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class AudioPlayerBar(CardWidget):
    """Interactive media player bar with scrubber and speed control."""

    def __init__(self, player: AudioPlayer, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.player = player
        self._is_user_scrubbing = False
        self._current_duration_ms = 0
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        # 1. Play / Pause Button
        self.play_btn = PushButton(self)
        self.play_btn.setIcon(FIF.PLAY)
        self.play_btn.setText("Play")
        self.play_btn.setFixedWidth(82)
        self.play_btn.setToolTip("Play / Pause audio (Space)")
        layout.addWidget(self.play_btn)

        # 2. Skip -5s button
        self.skip_back_btn = TransparentToolButton(FIF.ROTATE, self)
        self.skip_back_btn.setToolTip("Skip back 5 seconds (J or Left Arrow)")
        self.skip_back_btn.setFixedSize(32, 32)
        layout.addWidget(self.skip_back_btn)

        # 3. Skip +5s button
        self.skip_fwd_btn = TransparentToolButton(FIF.PAGE_RIGHT, self)
        self.skip_fwd_btn.setToolTip("Skip forward 5 seconds (L or Right Arrow)")
        self.skip_fwd_btn.setFixedSize(32, 32)
        layout.addWidget(self.skip_fwd_btn)

        # 4. Stop Button
        self.stop_btn = TransparentToolButton(FIF.CLOSE, self)
        self.stop_btn.setToolTip("Stop playback")
        self.stop_btn.setFixedSize(32, 32)
        layout.addWidget(self.stop_btn)

        # 5. Elapsed / Total Time Label
        self.time_label = CaptionLabel("00:00 / 00:00", self)
        self.time_label.setFixedWidth(95)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.time_label)

        # 6. Interactive Scrubber Slider
        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setToolTip("Drag or click to seek audio")
        layout.addWidget(self.slider, 1)

        # 7. Speed Selector ComboBox
        speed_layout = QHBoxLayout()
        speed_layout.setSpacing(4)
        speed_label = CaptionLabel("Speed:", self)
        speed_layout.addWidget(speed_label)

        self.speed_combo = ComboBox(self)
        self.speed_combo.addItems(["0.75x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.setFixedWidth(78)
        self.speed_combo.setToolTip("Pitch-preserved lecture playback speed")
        speed_layout.addWidget(self.speed_combo)

        layout.addLayout(speed_layout)

    def _connect_signals(self):
        # UI -> Player
        self.play_btn.clicked.connect(self.player.toggle_play_pause)
        self.stop_btn.clicked.connect(self.player.stop)
        self.skip_back_btn.clicked.connect(lambda: self.player.seek_relative(-5.0))
        self.skip_fwd_btn.clicked.connect(lambda: self.player.seek_relative(5.0))
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)

        # Slider scrubbing
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)

        # Player -> UI
        self.player.position_changed.connect(self._on_position_changed)
        self.player.duration_changed.connect(self._on_duration_changed)
        self.player.playback_state_changed.connect(self._on_playback_state_changed)
        self.player.playback_finished.connect(self._on_playback_finished)

    def _on_speed_changed(self, text: str):
        try:
            val = float(text.replace("x", ""))
            self.player.set_speed(val)
        except ValueError:
            pass

    def _on_slider_pressed(self):
        self._is_user_scrubbing = True

    def _on_slider_released(self):
        if self._current_duration_ms > 0:
            target_ms = int((self.slider.value() / 1000.0) * self._current_duration_ms)
            self.player.seek(target_ms)
        self._is_user_scrubbing = False

    def _on_slider_moved(self, value: int):
        if self._current_duration_ms > 0:
            current_ms = int((value / 1000.0) * self._current_duration_ms)
            self._update_time_display(current_ms, self._current_duration_ms)

    def _on_position_changed(self, pos_ms: int):
        if not self._is_user_scrubbing and self._current_duration_ms > 0:
            fraction = min(1.0, max(0.0, pos_ms / float(self._current_duration_ms)))
            self.slider.blockSignals(True)
            self.slider.setValue(int(fraction * 1000))
            self.slider.blockSignals(False)
            self._update_time_display(pos_ms, self._current_duration_ms)

    def _on_duration_changed(self, dur_ms: int):
        self._current_duration_ms = max(0, dur_ms)
        self._update_time_display(self.player._player.position(), self._current_duration_ms)

    def _update_time_display(self, cur_ms: int, dur_ms: int):
        cur_str = format_ms(cur_ms)
        dur_str = format_ms(dur_ms)
        self.time_label.setText(f"{cur_str} / {dur_str}")

    def _on_playback_state_changed(self, is_playing: bool):
        if is_playing:
            self.play_btn.setIcon(FIF.PAUSE)
            self.play_btn.setText("Pause")
        else:
            self.play_btn.setIcon(FIF.PLAY)
            self.play_btn.setText("Play")

    def _on_playback_finished(self):
        self.play_btn.setIcon(FIF.PLAY)
        self.play_btn.setText("Play")
        if self._current_duration_ms > 0:
            self._update_time_display(0, self._current_duration_ms)
            self.slider.setValue(0)
