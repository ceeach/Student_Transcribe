"""Audio Player module for NotionTranscribe.

Provides full audio playback, interactive scrubbing/seeking,
pitch-preserved speed adjustment (0.75x - 2.0x), and backward-compatible
snippet auditioning for timestamp links using PyQt6.QtMultimedia
with sounddevice fallback.
"""

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from logger import get_logger

logger = get_logger("AudioPlayer")

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    sd = None
    sf = None


class AudioPlayer(QObject):
    """Full-featured lecture audio player with speed controls, seeking, and snippet playback."""

    playback_started = pyqtSignal(float, float)   # (start_sec, end_sec)
    playback_finished = pyqtSignal()
    playback_error = pyqtSignal(str)
    position_changed = pyqtSignal(int)            # position in ms
    duration_changed = pyqtSignal(int)            # duration in ms
    playback_state_changed = pyqtSignal(bool)     # True if playing, False if paused/stopped
    playback_rate_changed = pyqtSignal(float)     # current speed multiplier

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._current_file: Optional[str] = None
        self._playback_speed: float = 1.0
        self._is_snippet_mode: bool = False
        self._snippet_end_ms: int = 0

        # Qt Multimedia engine
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)
        self._audio_output.setVolume(1.0)

        self._player.positionChanged.connect(self._on_player_position_changed)
        self._player.durationChanged.connect(self._on_player_duration_changed)
        self._player.playbackStateChanged.connect(self._on_player_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)

        # Fallback snippet timer for sounddevice
        self._fallback_timer = QTimer(self)
        self._fallback_timer.setSingleShot(True)
        self._fallback_timer.timeout.connect(self._on_fallback_timeout)
        self._using_fallback = False

    @property
    def is_playing(self) -> bool:
        if self._using_fallback:
            return self._fallback_timer.isActive()
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def current_file(self) -> Optional[str]:
        return self._current_file

    @property
    def playback_rate(self) -> float:
        return self._playback_speed

    @property
    def position_ms(self) -> int:
        if self._player:
            return self._player.position()
        return 0

    @property
    def position_seconds(self) -> float:
        return self.position_ms / 1000.0

    @property
    def duration_ms(self) -> int:
        if self._player:
            return self._player.duration()
        return 0

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0

    def load_audio(self, audio_path: str):
        """Set active audio file without auto-starting playback."""
        if not audio_path or not os.path.exists(audio_path):
            return
        if self._current_file == audio_path:
            return

        self._current_file = audio_path
        self._is_snippet_mode = False
        self._player.setSource(QUrl.fromLocalFile(str(Path(audio_path).resolve())))
        self._player.setPlaybackRate(self._playback_speed)
        logger.info(f"Loaded audio into player: {audio_path}")

    def play(self):
        """Resume or start audio playback."""
        if self._current_file and self._player.source().isEmpty():
            self._player.setSource(QUrl.fromLocalFile(str(Path(self._current_file).resolve())))
            self._player.setPlaybackRate(self._playback_speed)

        self._is_snippet_mode = False
        self._player.play()

    def pause(self):
        """Pause playback."""
        self._player.pause()

    def toggle_play_pause(self):
        """Toggle between playing and paused."""
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def stop(self):
        """Stop playback completely."""
        self._fallback_timer.stop()
        if self._using_fallback and sd is not None:
            try:
                sd.stop()
            except Exception:
                pass
            self._using_fallback = False

        self._player.stop()
        self._is_snippet_mode = False
        self.playback_finished.emit()

    def seek(self, position_ms: int):
        """Seek to specific position in milliseconds."""
        self._player.setPosition(max(0, int(position_ms)))

    def seek_relative(self, delta_seconds: float):
        """Seek forward or backward by delta_seconds."""
        current_ms = self._player.position()
        target_ms = current_ms + int(delta_seconds * 1000)
        dur_ms = self._player.duration()
        if dur_ms > 0:
            target_ms = max(0, min(dur_ms, target_ms))
        else:
            target_ms = max(0, target_ms)
        self.seek(target_ms)

    def set_speed(self, speed: float):
        """Set playback rate (e.g. 0.75, 1.0, 1.25, 1.5, 2.0)."""
        self._playback_speed = max(0.5, min(3.0, float(speed)))
        self._player.setPlaybackRate(self._playback_speed)
        self.playback_rate_changed.emit(self._playback_speed)
        logger.info(f"Audio playback rate set to {self._playback_speed}x")

    def play_snippet(
        self,
        audio_path: str,
        start_sec: float,
        end_sec: float,
        pad_before: float = 2.0,
        pad_after: float = 2.0,
    ) -> bool:
        """Play an audio slice centered around [start_sec, end_sec] with padding.

        Maintains 100% backward compatibility with interactive transcript timestamp links.
        """
        if not os.path.exists(audio_path):
            self.playback_error.emit(f"Audio file not found: {audio_path}")
            return False

        self.stop()
        self._current_file = audio_path

        actual_start = max(0.0, float(start_sec) - float(pad_before))
        actual_end = float(end_sec) + float(pad_after)

        # Try QMediaPlayer first for pitch-preserved, hardware-accelerated playback
        try:
            self._is_snippet_mode = True
            self._snippet_end_ms = int(actual_end * 1000)

            resolved_url = QUrl.fromLocalFile(str(Path(audio_path).resolve()))
            if self._player.source() != resolved_url:
                self._player.setSource(resolved_url)

            self._player.setPlaybackRate(self._playback_speed)
            start_ms = int(actual_start * 1000)
            self._player.setPosition(start_ms)
            self._player.play()
            self.playback_started.emit(actual_start, actual_end)
            return True
        except Exception as e:
            logger.warning(f"QMediaPlayer failed for snippet, trying sounddevice fallback: {e}")

        # Fallback to sounddevice if available
        if sd is not None and sf is not None:
            try:
                with sf.SoundFile(audio_path) as sound_file:
                    sr = sound_file.samplerate
                    total_duration = sound_file.frames / float(sr)
                    actual_end = min(total_duration, actual_end)
                    duration = actual_end - actual_start

                    if duration <= 0:
                        return False

                    start_frame = int(actual_start * sr)
                    frames_to_read = int(duration * sr)
                    sound_file.seek(start_frame)
                    data = sound_file.read(frames_to_read, dtype="float32")

                    sd.play(data, sr)
                    self._using_fallback = True
                    self.playback_started.emit(actual_start, actual_end)
                    self._fallback_timer.setInterval(int(duration * 1000) + 100)
                    self._fallback_timer.start()
                    return True
            except Exception as fb_err:
                self.playback_error.emit(f"Playback error: {fb_err}")
                return False

        self.playback_error.emit("Audio playback could not be initialized.")
        return False

    def _on_player_position_changed(self, pos_ms: int):
        self.position_changed.emit(pos_ms)
        # If snippet mode and reached end of snippet window, pause
        if self._is_snippet_mode and self._snippet_end_ms > 0:
            if pos_ms >= self._snippet_end_ms:
                self._is_snippet_mode = False
                self.pause()
                self.playback_finished.emit()

    def _on_player_duration_changed(self, dur_ms: int):
        self.duration_changed.emit(dur_ms)

    def _on_player_state_changed(self, state: QMediaPlayer.PlaybackState):
        is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self.playback_state_changed.emit(is_playing)
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._is_snippet_mode = False
            self.playback_finished.emit()

    def _on_player_error(self, error, error_string: str):
        logger.error(f"QMediaPlayer error: {error_string}")
        self.playback_error.emit(f"Audio Player Error: {error_string}")

    def _on_fallback_timeout(self):
        self._using_fallback = False
        self.playback_finished.emit()
