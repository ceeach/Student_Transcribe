"""Audio Recorder module for NotionTranscribe.

Handles microphone capture, system audio capture (Windows WASAPI Loopback),
dual-source mixing (Microphone + System Audio), RMS level monitoring for visual
VU meters, pause/resume, and saving to 16kHz mono WAV files optimized for Whisper.
"""

from datetime import datetime
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

RECORDINGS_DIR = Path.home() / ".notion_transcribe" / "recordings"

# Primary engine: PyAudioWPatch (for native Windows WASAPI loopback + mic)
try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None

# Fallback audio engine
try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import soundfile as sf
except ImportError:
    sf = None

# PyAV for high quality streaming audio resampling if available
try:
    import av
except ImportError:
    av = None

from PyQt6.QtCore import QObject, pyqtSignal


def to_mono_16k(audio_bytes_or_array: Any, in_rate: int, in_channels: int) -> np.ndarray:
    """Convert raw audio samples to 1D float32 mono array at 16000 Hz.

    Args:
        audio_bytes_or_array: Raw bytes from audio stream or numpy array.
        in_rate: Input sample rate (e.g. 48000, 44100, 16000).
        in_channels: Number of input channels (e.g. 1, 2).

    Returns:
        1D float32 numpy array normalized to [-1.0, 1.0] at 16000 Hz.
    """
    if isinstance(audio_bytes_or_array, (bytes, bytearray)):
        samples = np.frombuffer(audio_bytes_or_array, dtype=np.float32).copy()
    elif isinstance(audio_bytes_or_array, np.ndarray):
        samples = audio_bytes_or_array.astype(np.float32).copy()
    else:
        return np.zeros(0, dtype=np.float32)

    if samples.size == 0:
        return np.zeros(0, dtype=np.float32)

    # Downmix multi-channel to mono
    if in_channels > 1:
        samples = samples.reshape(-1, in_channels).mean(axis=1)

    # Resample to 16000 Hz if necessary
    in_rate = int(in_rate)
    if in_rate != 16000 and len(samples) > 1:
        num_out = int(round(len(samples) * 16000.0 / in_rate))
        if num_out <= 0:
            return np.zeros(0, dtype=np.float32)
        x_in = np.arange(len(samples))
        x_out = np.linspace(0, len(samples) - 1, num_out)
        samples = np.interp(x_out, x_in, samples).astype(np.float32)

    return samples


def mix_audio_frames(frame_a: np.ndarray, frame_b: np.ndarray) -> np.ndarray:
    """Mix two 1D float32 16kHz audio frames with soft limiting to prevent clipping."""
    len_a = len(frame_a)
    len_b = len(frame_b)
    target_len = max(len_a, len_b)

    if target_len == 0:
        return np.zeros(0, dtype=np.float32)

    if len_a < target_len:
        pad_a = np.zeros(target_len - len_a, dtype=np.float32)
        frame_a = np.concatenate([frame_a, pad_a])
    if len_b < target_len:
        pad_b = np.zeros(target_len - len_b, dtype=np.float32)
        frame_b = np.concatenate([frame_b, pad_b])

    # Mix with 0.7 gain and hard clip to [-1.0, 1.0]
    mixed = (frame_a + frame_b) * 0.7
    return np.clip(mixed, -1.0, 1.0).astype(np.float32)


def get_audio_input_devices() -> List[Dict[str, Any]]:
    """Enumerate all available audio input devices (microphones)."""
    devices: List[Dict[str, Any]] = []

    if pyaudio is not None:
        try:
            p = pyaudio.PyAudio()
            try:
                default_dev = p.get_default_input_device_info()
                default_idx = default_dev.get("index")
            except Exception:
                default_idx = None

            for i in range(p.get_device_count()):
                try:
                    info = p.get_device_info_by_index(i)
                    # Exclude loopback devices from microphone list
                    if info.get("isLoopbackDevice", False):
                        continue
                    if info.get("maxInputChannels", 0) > 0:
                        name = info.get("name", f"Microphone {i}")
                        devices.append({
                            "id": i,
                            "name": name,
                            "channels": int(info.get("maxInputChannels", 1)),
                            "default_rate": int(info.get("defaultSampleRate", 16000)),
                            "is_default": (i == default_idx),
                        })
                except Exception:
                    continue
            p.terminate()
            if devices:
                return devices
        except Exception:
            pass

    # Fallback to sounddevice if pyaudio is unavailable or failed
    if sd is not None:
        try:
            sd_devices = sd.query_devices()
            default_in = sd.default.device[0]
            for idx, d in enumerate(sd_devices):
                if d.get("max_input_channels", 0) > 0:
                    devices.append({
                        "id": idx,
                        "name": d.get("name", f"Device {idx}"),
                        "channels": int(d.get("max_input_channels", 1)),
                        "default_rate": int(d.get("default_samplerate", 16000)),
                        "is_default": (idx == default_in),
                    })
        except Exception:
            pass

    return devices


def get_audio_loopback_devices() -> List[Dict[str, Any]]:
    """Enumerate all available system audio loopback devices (WASAPI Loopback)."""
    devices: List[Dict[str, Any]] = []

    if pyaudio is None:
        return devices

    try:
        p = pyaudio.PyAudio()
        default_loopback_idx = None
        try:
            def_loop = p.get_default_wasapi_loopback()
            default_loopback_idx = def_loop.get("index")
        except Exception:
            pass

        for loopback in p.get_loopback_device_info_generator():
            try:
                idx = loopback.get("index")
                name = loopback.get("name", f"Loopback {idx}")
                # Clean up name if it has duplicate [Loopback]
                devices.append({
                    "id": idx,
                    "name": name,
                    "channels": int(loopback.get("maxInputChannels", 2)),
                    "default_rate": int(loopback.get("defaultSampleRate", 48000)),
                    "is_default": (idx == default_loopback_idx),
                })
            except Exception:
                continue

        p.terminate()
    except Exception:
        pass

    return devices


class AudioRecorder(QObject):
    """Thread-safe live audio recorder supporting Microphone, System Audio, or Both."""

    level_changed = pyqtSignal(float)          # Emits normalized audio level [0.0, 1.0]
    duration_changed = pyqtSignal(float)       # Emits elapsed recording time in seconds
    recording_started = pyqtSignal()
    recording_paused = pyqtSignal(bool)        # True if paused, False if resumed
    recording_finished = pyqtSignal(str)       # Emits output wav file path
    error_occurred = pyqtSignal(str)
    source_changed = pyqtSignal(str)           # Emits new active audio source ("mic", "system", "both")

    def __init__(
        self,
        parent: Optional[QObject] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        audio_source: str = "mic",
        input_device_id: Optional[int] = None,
        loopback_device_id: Optional[int] = None,
    ):
        super().__init__(parent)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.audio_source = audio_source  # "mic", "system", or "both"
        self.input_device_id = input_device_id
        self.loopback_device_id = loopback_device_id

        self._pyaudio_instance: Optional[Any] = None
        self._mic_stream: Optional[Any] = None
        self._loop_stream: Optional[Any] = None
        self._sd_stream: Optional[Any] = None

        self._mic_queue: queue.Queue = queue.Queue()
        self._loop_queue: queue.Queue = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None

        self._is_recording = False
        self._is_paused = False
        self._stop_event = threading.Event()

        self._output_path: Optional[str] = None
        self._start_time: float = 0.0
        self._accumulated_time: float = 0.0

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def set_audio_source(self, source: str):
        """Set capture mode: 'mic', 'system', or 'both'."""
        if source in ["mic", "system", "both"]:
            self.audio_source = source
            self.source_changed.emit(source)

    def set_input_device(self, device_id: Optional[int]):
        """Set specific microphone device ID (or None for system default)."""
        self.input_device_id = device_id

    def set_loopback_device(self, device_id: Optional[int]):
        """Set specific system audio loopback device ID (or None for system default)."""
        self.loopback_device_id = device_id

    def get_duration(self) -> float:
        """Return total elapsed recording time in seconds (excluding paused intervals)."""
        if not self._is_recording:
            return self._accumulated_time
        if self._is_paused:
            return self._accumulated_time
        return self._accumulated_time + (time.time() - self._start_time)

    # -------------------------------------------------------------------------
    # PyAudio Callbacks (Non-blocking)
    # -------------------------------------------------------------------------
    def _mic_callback(self, in_data, frame_count, time_info, status):
        if not self._is_recording or self._is_paused:
            return (None, pyaudio.paContinue)
        self._mic_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def _loop_callback(self, in_data, frame_count, time_info, status):
        if not self._is_recording or self._is_paused:
            return (None, pyaudio.paContinue)
        self._loop_queue.put(in_data)
        return (None, pyaudio.paContinue)

    # -------------------------------------------------------------------------
    # Sounddevice Fallback Callback
    # -------------------------------------------------------------------------
    def _sd_callback(self, indata, frames, time_info, status):
        if not self._is_recording or self._is_paused:
            self.level_changed.emit(0.0)
            return
        rms = np.sqrt(np.mean(indata**2))
        self.level_changed.emit(float(np.clip(rms * 5.0, 0.0, 1.0)))
        self._mic_queue.put(indata.copy())

    # -------------------------------------------------------------------------
    # Disk Writer Thread
    # -------------------------------------------------------------------------
    def _disk_writer(
        self,
        file_path: str,
        mic_rate: int,
        mic_channels: int,
        loop_rate: int,
        loop_channels: int,
    ):
        """Worker thread converting, mixing, and writing audio to 16kHz mono WAV."""
        buf_mic = np.zeros(0, dtype=np.float32)
        buf_loop = np.zeros(0, dtype=np.float32)

        try:
            with sf.SoundFile(
                file_path,
                mode="w",
                samplerate=self.sample_rate,
                channels=self.channels,
                subtype="PCM_16",
            ) as sound_file:
                while not self._stop_event.is_set() or not self._mic_queue.empty() or not self._loop_queue.empty():
                    # Drain mic queue
                    while not self._mic_queue.empty():
                        try:
                            raw = self._mic_queue.get_nowait()
                            converted = to_mono_16k(raw, mic_rate, mic_channels)
                            buf_mic = np.concatenate([buf_mic, converted])
                        except queue.Empty:
                            break

                    # Drain loopback queue
                    while not self._loop_queue.empty():
                        try:
                            raw = self._loop_queue.get_nowait()
                            converted = to_mono_16k(raw, loop_rate, loop_channels)
                            buf_loop = np.concatenate([buf_loop, converted])
                        except queue.Empty:
                            break

                    if self._is_paused:
                        # Drop buffers while paused
                        buf_mic = np.zeros(0, dtype=np.float32)
                        buf_loop = np.zeros(0, dtype=np.float32)
                        self.level_changed.emit(0.0)
                        time.sleep(0.05)
                        continue

                    # Process audio based on active source
                    if self.audio_source == "mic":
                        if len(buf_mic) > 0:
                            chunk = buf_mic
                            buf_mic = np.zeros(0, dtype=np.float32)
                            sound_file.write(chunk)
                            rms = float(np.sqrt(np.mean(chunk**2))) if len(chunk) > 0 else 0.0
                            self.level_changed.emit(float(np.clip(rms * 5.0, 0.0, 1.0)))

                    elif self.audio_source == "system":
                        if len(buf_loop) > 0:
                            chunk = buf_loop
                            buf_loop = np.zeros(0, dtype=np.float32)
                            sound_file.write(chunk)
                            rms = float(np.sqrt(np.mean(chunk**2))) if len(chunk) > 0 else 0.0
                            self.level_changed.emit(float(np.clip(rms * 5.0, 0.0, 1.0)))

                    else:  # "both"
                        n = min(len(buf_mic), len(buf_loop))
                        if n > 0:
                            chunk_m = buf_mic[:n]
                            chunk_l = buf_loop[:n]
                            mixed = np.clip((chunk_m + chunk_l) * 0.7, -1.0, 1.0).astype(np.float32)
                            sound_file.write(mixed)
                            rms = float(np.sqrt(np.mean(mixed**2)))
                            self.level_changed.emit(float(np.clip(rms * 5.0, 0.0, 1.0)))
                            buf_mic = buf_mic[n:]
                            buf_loop = buf_loop[n:]
                        elif len(buf_mic) > 16000 * 0.4:
                            # Loopback queue starved or quiet: write mic with silence padding
                            chunk_m = buf_mic
                            buf_mic = np.zeros(0, dtype=np.float32)
                            sound_file.write(chunk_m * 0.7)
                            rms = float(np.sqrt(np.mean(chunk_m**2)))
                            self.level_changed.emit(float(np.clip(rms * 5.0, 0.0, 1.0)))
                        elif len(buf_loop) > 16000 * 0.4:
                            # Mic queue starved: write system with silence padding
                            chunk_l = buf_loop
                            buf_loop = np.zeros(0, dtype=np.float32)
                            sound_file.write(chunk_l * 0.7)
                            rms = float(np.sqrt(np.mean(chunk_l**2)))
                            self.level_changed.emit(float(np.clip(rms * 5.0, 0.0, 1.0)))

                    # Update duration periodically
                    self.duration_changed.emit(self.get_duration())
                    time.sleep(0.02)

        except Exception as e:
            self.error_occurred.emit(f"Audio recording disk write error: {e}")

    # -------------------------------------------------------------------------
    # Public Control API
    # -------------------------------------------------------------------------
    def start(self, output_path: Optional[str] = None) -> bool:
        """Start a new recording session based on configured audio_source."""
        if self._is_recording:
            return False

        if sf is None:
            self.error_occurred.emit("Missing 'soundfile' library. Please check installation.")
            return False

        if output_path is None:
            RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            source_tag = "System" if self.audio_source == "system" else ("MeetingDual" if self.audio_source == "both" else "Mic")
            filename = f"Recording_{source_tag}_{timestamp}.wav"
            self._output_path = str(RECORDINGS_DIR / filename)
        else:
            self._output_path = output_path

        self._stop_event.clear()
        while not self._mic_queue.empty():
            try:
                self._mic_queue.get_nowait()
            except queue.Empty:
                break
        while not self._loop_queue.empty():
            try:
                self._loop_queue.get_nowait()
            except queue.Empty:
                break

        # Check PyAudio availability for WASAPI capture
        if pyaudio is not None:
            try:
                self._pyaudio_instance = pyaudio.PyAudio()

                mic_rate = 16000
                mic_channels = 1
                loop_rate = 48000
                loop_channels = 2

                # 1. Setup Microphone stream if needed
                if self.audio_source in ["mic", "both"]:
                    mic_info = None
                    if self.input_device_id is not None:
                        try:
                            mic_info = self._pyaudio_instance.get_device_info_by_index(self.input_device_id)
                        except Exception:
                            pass
                    if mic_info is None:
                        mic_info = self._pyaudio_instance.get_default_input_device_info()

                    mic_rate = int(mic_info.get("defaultSampleRate", 16000))
                    mic_channels = min(2, max(1, int(mic_info.get("maxInputChannels", 1))))
                    mic_idx = mic_info.get("index")

                    self._mic_stream = self._pyaudio_instance.open(
                        format=pyaudio.paFloat32,
                        channels=mic_channels,
                        rate=mic_rate,
                        input=True,
                        input_device_index=mic_idx,
                        stream_callback=self._mic_callback,
                        frames_per_buffer=1024,
                    )

                # 2. Setup Loopback stream if needed
                if self.audio_source in ["system", "both"]:
                    loop_info = None
                    if self.loopback_device_id is not None:
                        try:
                            loop_info = self._pyaudio_instance.get_device_info_by_index(self.loopback_device_id)
                        except Exception:
                            pass
                    if loop_info is None:
                        loop_info = self._pyaudio_instance.get_default_wasapi_loopback()

                    loop_rate = int(loop_info.get("defaultSampleRate", 48000))
                    loop_channels = int(loop_info.get("maxInputChannels", 2))
                    loop_idx = loop_info.get("index")

                    self._loop_stream = self._pyaudio_instance.open(
                        format=pyaudio.paFloat32,
                        channels=loop_channels,
                        rate=loop_rate,
                        input=True,
                        input_device_index=loop_idx,
                        stream_callback=self._loop_callback,
                        frames_per_buffer=1024,
                    )

                # Start writer thread
                self._writer_thread = threading.Thread(
                    target=self._disk_writer,
                    args=(self._output_path, mic_rate, mic_channels, loop_rate, loop_channels),
                    daemon=True,
                )
                self._writer_thread.start()

                # Start audio streams
                if self._mic_stream:
                    self._mic_stream.start_stream()
                if self._loop_stream:
                    self._loop_stream.start_stream()

                self._is_recording = True
                self._is_paused = False
                self._start_time = time.time()
                self._accumulated_time = 0.0

                self.recording_started.emit()
                self.duration_changed.emit(0.0)
                return True

            except Exception as e:
                self._cleanup_streams()
                # If PyAudio failed on mic-only, attempt sounddevice fallback
                if self.audio_source == "mic" and sd is not None:
                    return self._start_sounddevice_fallback(self._output_path)
                self.error_occurred.emit(f"Failed to open audio capture stream: {e}")
                return False

        # Fallback to sounddevice (only supports mic)
        if self.audio_source in ["system", "both"]:
            self.error_occurred.emit(
                "System audio loopback capture requires 'PyAudioWPatch' on Windows.\n"
                "Please install PyAudioWPatch or select 'Microphone Only'."
            )
            return False

        return self._start_sounddevice_fallback(self._output_path)

    def _start_sounddevice_fallback(self, output_path: str) -> bool:
        """Fallback to sounddevice for standard microphone recording."""
        if sd is None:
            self.error_occurred.emit("No audio capture engine available (neither PyAudioWPatch nor sounddevice).")
            return False

        try:
            devices = sd.query_devices()
            input_device = self.input_device_id
            if input_device is None or input_device >= len(devices) or devices[input_device].get("max_input_channels", 0) <= 0:
                default_in = sd.default.device[0]
                if 0 <= default_in < len(devices) and devices[default_in].get("max_input_channels", 0) > 0:
                    input_device = default_in
                else:
                    input_devs = [i for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]
                    if input_devs:
                        input_device = input_devs[0]
                    else:
                        self.error_occurred.emit("No audio input device (microphone) found.")
                        return False

            self._sd_stream = sd.InputStream(
                samplerate=int(self.sample_rate),
                channels=int(self.channels),
                device=input_device,
                dtype="float32",
                callback=self._sd_callback,
                blocksize=1024,
            )
            self._sd_stream.start()

            self._writer_thread = threading.Thread(
                target=self._disk_writer,
                args=(output_path, self.sample_rate, self.channels, 48000, 2),
                daemon=True,
            )
            self._writer_thread.start()

            self._is_recording = True
            self._is_paused = False
            self._start_time = time.time()
            self._accumulated_time = 0.0

            self.recording_started.emit()
            self.duration_changed.emit(0.0)
            return True
        except Exception as e:
            self.error_occurred.emit(f"Sounddevice recording failed: {e}")
            return False

    def pause(self):
        """Pause active recording."""
        if not self._is_recording or self._is_paused:
            return
        self._is_paused = True
        self._accumulated_time += time.time() - self._start_time
        self.recording_paused.emit(True)
        self.level_changed.emit(0.0)

    def resume(self):
        """Resume paused recording."""
        if not self._is_recording or not self._is_paused:
            return
        self._is_paused = False
        self._start_time = time.time()
        self.recording_paused.emit(False)

    def stop(self) -> Optional[str]:
        """Stop recording session, flush and close WAV file."""
        if not self._is_recording:
            return None

        self._is_recording = False
        self._is_paused = False

        self._cleanup_streams()

        self._stop_event.set()
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=3.0)
            self._writer_thread = None

        self.level_changed.emit(0.0)
        output_file = self._output_path
        self.recording_finished.emit(output_file or "")
        return output_file

    def _cleanup_streams(self):
        """Safely close active audio streams and release hardware handles."""
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop_stream()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None

        if self._loop_stream is not None:
            try:
                self._loop_stream.stop_stream()
                self._loop_stream.close()
            except Exception:
                pass
            self._loop_stream = None

        if self._sd_stream is not None:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None

        if self._pyaudio_instance is not None:
            try:
                self._pyaudio_instance.terminate()
            except Exception:
                pass
            self._pyaudio_instance = None
