"""Audio Snippet Player module for NotionTranscribe.

Extracts and plays precise audio windows (e.g. 2s before to 2s after a word)
using soundfile and sounddevice without freezing the GUI.
"""

import os
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    sd = None
    sf = None

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class AudioPlayer(QObject):
    """Audio player for auditioning specific time intervals and word snippets."""

    playback_started = pyqtSignal(float, float) # (start_sec, end_sec)
    playback_finished = pyqtSignal()
    playback_error = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._is_playing = False
        self._playback_timer = QTimer(self)
        self._playback_timer.setSingleShot(True)
        self._playback_timer.timeout.connect(self._on_playback_timeout)

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def play_snippet(
        self,
        audio_path: str,
        start_sec: float,
        end_sec: float,
        pad_before: float = 2.0,
        pad_after: float = 2.0,
    ) -> bool:
        """Play an audio slice centered around [start_sec, end_sec] with padding.

        Args:
            audio_path: Path to the audio WAV/MP3 file.
            start_sec: Start time of the target word/phrase in seconds.
            end_sec: End time of the target word/phrase in seconds.
            pad_before: Seconds of audio to include before the word (default: 2.0s).
            pad_after: Seconds of audio to include after the word (default: 2.0s).

        Returns:
            True if playback started successfully, False otherwise.
        """
        if sd is None or sf is None:
            self.playback_error.emit("Audio libraries (sounddevice/soundfile) are not available.")
            return False

        if not os.path.exists(audio_path):
            self.playback_error.emit(f"Audio file not found: {audio_path}")
            return False

        self.stop()

        try:
            with sf.SoundFile(audio_path) as sound_file:
                sr = sound_file.samplerate
                total_frames = sound_file.frames
                total_duration = total_frames / float(sr)

                actual_start = max(0.0, float(start_sec) - float(pad_before))
                actual_end = min(total_duration, float(end_sec) + float(pad_after))
                duration = actual_end - actual_start

                if duration <= 0:
                    self.playback_error.emit("Invalid snippet duration.")
                    return False

                start_frame = int(actual_start * sr)
                frames_to_read = int(duration * sr)

                sound_file.seek(start_frame)
                data = sound_file.read(frames_to_read, dtype="float32")

                sd.play(data, sr)
                self._is_playing = True
                self.playback_started.emit(actual_start, actual_end)

                # Timer to stop and emit finished signal
                self._playback_timer.stop()
                self._playback_timer.setInterval(int(duration * 1000) + 100)
                self._playback_timer.start()
                return True

        except Exception as e:
            self._is_playing = False
            self.playback_error.emit(f"Playback error: {e}")
            return False

    def stop(self):
        """Stop current audio playback immediately."""
        self._playback_timer.stop()
        if sd is not None and self._is_playing:
            try:
                sd.stop()
            except Exception:
                pass
        if self._is_playing:
            self._is_playing = False
            self.playback_finished.emit()

    def _on_playback_timeout(self):
        self._is_playing = False
        self.playback_finished.emit()
