"""Video Processing and Audio Extraction module for NotionTranscribe.

Extracts high-fidelity 16kHz mono PCM audio from diverse video containers (.mp4, .mkv, .mov, .avi, etc.),
verifies audio stream validity, and safely deletes the source video file once extraction is validated.
"""

from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Optional, Tuple

import soundfile as sf
from PyQt6.QtCore import QThread, pyqtSignal

from audio_recorder import RECORDINGS_DIR

logger = logging.getLogger(__name__)

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".ts",
    ".3gp",
}


def is_video_file(file_path: str) -> bool:
    """Return True if the file has a recognized video extension."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_VIDEO_EXTENSIONS


def _extract_with_ffmpeg_cli(video_path: str, output_wav: str) -> bool:
    """Extract audio using system ffmpeg executable."""
    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe:
        return False

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_wav),
    ]

    try:
        # Hide console window on Windows
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo,
            check=False,
            timeout=300,
        )
        if result.returncode == 0 and os.path.exists(output_wav):
            return True
        logger.warning(f"ffmpeg returned non-zero ({result.returncode}): {result.stderr[:200]}")
        return False
    except Exception as e:
        logger.warning(f"ffmpeg extraction failed: {e}")
        return False


def _extract_with_pyav(video_path: str, output_wav: str) -> bool:
    """Fallback audio extraction using PyAV in-process library."""
    try:
        import av
        import numpy as np

        container = av.open(str(video_path))
        audio_streams = [s for s in container.streams if s.type == "audio"]
        if not audio_streams:
            logger.warning(f"No audio streams found in {video_path}")
            return False

        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        pcm_chunks = []

        for frame in container.decode(audio_streams[0]):
            resampled_frames = resampler.resample(frame)
            if resampled_frames:
                for rf in resampled_frames:
                    array = rf.to_ndarray()
                    pcm_chunks.append(array.tobytes())

        container.close()

        if not pcm_chunks:
            logger.warning(f"No audio frames decoded from {video_path}")
            return False

        raw_bytes = b"".join(pcm_chunks)
        data = np.frombuffer(raw_bytes, dtype=np.int16)

        sf.write(output_wav, data, 16000, subtype="PCM_16")
        return os.path.exists(output_wav)
    except Exception as e:
        logger.error(f"PyAV extraction failed: {e}")
        return False


def verify_audio_file(audio_path: str) -> bool:
    """Verify that an audio file exists, has content, and has valid header/sample data."""
    if not os.path.exists(audio_path):
        return False

    size = os.path.getsize(audio_path)
    if size < 512:  # WAV header alone is ~44 bytes; valid speech needs more
        return False

    try:
        info = sf.info(audio_path)
        return info.frames > 0 and info.samplerate > 0
    except Exception as e:
        logger.warning(f"Audio verification failed for {audio_path}: {e}")
        return False


def extract_audio_from_video(
    video_path: str,
    output_dir: Optional[str] = None,
    delete_source: bool = True,
) -> Tuple[str, bool]:
    """Extract audio from video file to 16kHz mono WAV, then safely delete video if requested.

    Args:
        video_path: Absolute path to source video file.
        output_dir: Optional target directory. Defaults to RECORDINGS_DIR.
        delete_source: If True and extraction is verified, deletes the video file.

    Returns:
        Tuple of (output_audio_path, was_source_deleted)

    Raises:
        FileNotFoundError: If video_path does not exist.
        ValueError: If video format is unsupported or file has no audio.
        RuntimeError: If audio extraction fails or verification fails.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if not is_video_file(video_path):
        raise ValueError(f"Unsupported video file format: {video_path}")

    target_dir = Path(output_dir) if output_dir else RECORDINGS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    video_stem = Path(video_path).stem
    # Sanitize stem
    safe_stem = "".join(c for c in video_stem if c.isalnum() or c in (" ", "-", "_")).strip()
    if not safe_stem:
        safe_stem = "Video_Audio"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"Audio_{safe_stem}_{timestamp}.wav"
    output_wav = str(target_dir / out_name)

    # 1. Run extraction (FFmpeg CLI primary, PyAV fallback)
    success = _extract_with_ffmpeg_cli(video_path, output_wav)
    if not success:
        logger.info("FFmpeg CLI failed or not available, trying PyAV...")
        success = _extract_with_pyav(video_path, output_wav)

    if not success or not os.path.exists(output_wav):
        if os.path.exists(output_wav):
            try:
                os.remove(output_wav)
            except Exception:
                pass
        raise RuntimeError(
            f"Failed to extract audio from video '{os.path.basename(video_path)}'. "
            "Please ensure the video file has an active audio stream."
        )

    # 2. Strict verification of audio integrity
    if not verify_audio_file(output_wav):
        if os.path.exists(output_wav):
            try:
                os.remove(output_wav)
            except Exception:
                pass
        raise RuntimeError(
            f"Extracted audio from '{os.path.basename(video_path)}' was corrupt or empty."
        )

    # 3. Safe deletion of source video
    was_deleted = False
    if delete_source:
        # Give Windows a brief moment to release file handles if any
        time.sleep(0.1)
        try:
            os.remove(video_path)
            was_deleted = True
            logger.info(f"Successfully deleted source video: {video_path}")
        except Exception as e:
            logger.warning(f"Failed to delete source video '{video_path}': {e}")
            was_deleted = False

    return output_wav, was_deleted


class VideoExtractionWorker(QThread):
    """Background worker for extracting audio from video without blocking the GUI."""

    progress_status = pyqtSignal(str)
    extraction_completed = pyqtSignal(str, str, bool)  # (video_name, audio_path, was_deleted)
    extraction_failed = pyqtSignal(str, str)           # (video_name, error_message)

    def __init__(
        self,
        video_path: str,
        output_dir: Optional[str] = None,
        delete_source: bool = True,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.video_path = video_path
        self.output_dir = output_dir
        self.delete_source = delete_source

    def run(self):
        video_name = os.path.basename(self.video_path)
        try:
            self.progress_status.emit(f"Extracting 16kHz audio from '{video_name}'...")
            audio_path, was_deleted = extract_audio_from_video(
                video_path=self.video_path,
                output_dir=self.output_dir,
                delete_source=self.delete_source,
            )
            self.progress_status.emit(f"Audio extracted successfully: {os.path.basename(audio_path)}")
            self.extraction_completed.emit(video_name, audio_path, was_deleted)
        except Exception as e:
            self.progress_status.emit("Audio extraction failed.")
            self.extraction_failed.emit(video_name, str(e))
