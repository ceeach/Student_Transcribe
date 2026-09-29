"""Whisper Transcription Worker module for NotionTranscribe.

Runs faster-whisper inference in a background QThread, streaming transcribed
segments and progress updates to the GUI without freezing the application.
"""

import os
import sys
import traceback
from typing import Any, Dict, List, Optional

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

from PyQt6.QtCore import QThread, pyqtSignal


def format_timestamp(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS string."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def is_cuda_available() -> bool:
    """Check if CUDA device is available for ctranslate2."""
    try:
        import ctranslate2
        supported = ctranslate2.get_supported_compute_types("cuda")
        return len(supported) > 0
    except Exception:
        return False


class WhisperWorker(QThread):
    """Background worker thread for running Whisper speech-to-text inference."""

    status_changed = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    segment_received = pyqtSignal(dict)           # {"start": float, "end": float, "text": str, "timestamp_str": str}
    transcription_completed = pyqtSignal(list, str) # (segments_list, full_text)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        audio_path: str,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: Optional[str] = None,
        enable_diarization: bool = True,
        num_speakers: int = -1,
        diarization_threshold: float = 0.5,
        beam_size: int = 1,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.audio_path = audio_path
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self.diarization_threshold = diarization_threshold
        self.beam_size = int(beam_size)

        self._is_cancelled = False

    def cancel(self):
        """Request cancellation of transcription task."""
        self._is_cancelled = True

    def _get_audio_duration(self, file_path: str) -> float:
        """Estimate audio duration in seconds."""
        if sf is not None:
            try:
                info = sf.info(file_path)
                if info.duration and info.duration > 0:
                    return float(info.duration)
            except Exception:
                pass

        # Fallback to file size heuristic (~32KB/sec for 16kHz 16-bit mono wav)
        try:
            size_bytes = os.path.getsize(file_path)
            return max(1.0, size_bytes / 32000.0)
        except Exception:
            return 60.0

    def run(self):
        """Execute the transcription in background."""
        if WhisperModel is None:
            self.error_occurred.emit(
                "The 'faster-whisper' package is not installed. Please install it via pip install faster-whisper."
            )
            return

        if not os.path.exists(self.audio_path):
            self.error_occurred.emit(f"Audio file not found: {self.audio_path}")
            return

        try:
            # 1. Inspect duration
            self.status_changed.emit("Analyzing audio file...")
            total_duration = self._get_audio_duration(self.audio_path)
            self.progress_percentage.emit(5)

            if self._is_cancelled:
                return

            # 2. Load Model
            self.status_changed.emit(
                f"Loading Whisper '{self.model_size}' model ({self.device.upper()})..."
            )
            try:
                # Note: On CPU, int8 or float32 are standard. On CUDA, float16 or int8_float16.
                actual_compute = self.compute_type
                if self.device == "cpu" and self.compute_type == "float16":
                    actual_compute = "int8"

                model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=actual_compute,
                    download_root=None,
                )
            except Exception as e:
                err_str = str(e)
                if "out of memory" in err_str.lower() or "cuda" in err_str.lower():
                    self.error_occurred.emit(
                        f"CUDA / GPU Error:\n{err_str}\n\n"
                        "Recommendation: Switch device to 'cpu' or use a smaller model "
                        "(e.g., 'tiny' or 'base') in the Settings dialog."
                    )
                else:
                    self.error_occurred.emit(f"Failed to load Whisper model: {err_str}")
                return

            self.progress_percentage.emit(15)
            if self._is_cancelled:
                return

            # 3. Transcribe
            self.status_changed.emit("Transcribing speech...")
            transcribe_kwargs: Dict[str, Any] = {
                "beam_size": self.beam_size,
                "vad_filter": True,
                "vad_parameters": dict(min_silence_duration_ms=500),
                "word_timestamps": True,
            }
            if self.language:
                transcribe_kwargs["language"] = self.language

            segments_gen, info = model.transcribe(self.audio_path, **transcribe_kwargs)

            all_segments: List[Dict[str, Any]] = []
            formatted_lines: List[str] = []

            # 4. Process segments stream
            for seg in segments_gen:
                if self._is_cancelled:
                    self.status_changed.emit("Transcription cancelled.")
                    return

                start_ts = format_timestamp(seg.start)
                end_ts = format_timestamp(seg.end)
                ts_label = f"[{start_ts} - {end_ts}]"
                text = seg.text.strip()

                # Extract word-level timestamps
                word_dicts = []
                if hasattr(seg, "words") and seg.words:
                    for w in seg.words:
                        word_dicts.append({
                            "word": w.word,
                            "start": float(w.start),
                            "end": float(w.end),
                            "prob": float(getattr(w, "probability", 1.0)),
                        })

                seg_dict = {
                    "start": seg.start,
                    "end": seg.end,
                    "text": text,
                    "timestamp_str": ts_label,
                    "words": word_dicts,
                }
                all_segments.append(seg_dict)

                formatted_line = f"{ts_label} {text}"
                formatted_lines.append(formatted_line)

                # Calculate progress based on audio timestamp
                if total_duration > 0:
                    pct = 15 + int((seg.end / total_duration) * 80)
                    pct = min(98, max(15, pct))
                    self.progress_percentage.emit(pct)

            # 5. Speaker Diarization
            if self.enable_diarization and all_segments and not self._is_cancelled:
                try:
                    self.status_changed.emit("Diarizing speakers (identifying who spoke when)...")
                    self.progress_percentage.emit(95)
                    from diarization import SpeakerDiarizer, assign_speakers_to_segments
                    diarizer = SpeakerDiarizer(
                        num_speakers=self.num_speakers,
                        threshold=self.diarization_threshold,
                    )
                    turns = diarizer.diarize(self.audio_path)
                    all_segments = assign_speakers_to_segments(all_segments, turns)

                    # Update formatted lines with speaker labels
                    formatted_lines = [
                        f"[{seg.get('speaker', 'Speaker 1')}] {seg.get('timestamp_str', '')} {seg.get('text', '')}"
                        for seg in all_segments
                    ]
                except Exception as de:
                    print(f"Speaker diarization skipped: {de}")
                    for seg in all_segments:
                        seg.setdefault("speaker", "Speaker 1")
                        seg.setdefault("speaker_id", 0)

            full_text = "\n\n".join(formatted_lines)
            self.progress_percentage.emit(100)
            speakers_found = len(set(s.get("speaker", "Speaker 1") for s in all_segments))
            if self.enable_diarization and speakers_found > 1:
                status_msg = f"Completed: {len(all_segments)} segment(s) | {speakers_found} speakers identified."
            else:
                status_msg = f"Completed: {len(all_segments)} segment(s) transcribed."
            self.status_changed.emit(status_msg)
            self.transcription_completed.emit(all_segments, full_text)

        except Exception as e:
            traceback.print_exc()
            err_str = str(e)
            if "cuda" in err_str.lower() or "cublas" in err_str.lower():
                self.error_occurred.emit(
                    f"CUDA execution error: {err_str}\n\n"
                    "Try switching device to 'cpu' in the Settings dialog."
                )
            else:
                self.error_occurred.emit(f"Transcription failed: {err_str}")
