"""Video Folder Watcher module for NotionTranscribe.

Monitors the designated video inbox folder for new video drops, verifies that
file copying has stabilized, and extracts audio sequentially to prevent resource contention.
"""

from collections import deque
import logging
import os
from pathlib import Path
import time
from typing import Any, Deque, Dict, List, Optional, Set

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from config_manager import config_manager, VIDEO_INBOX_DIR
from video_processor import VideoExtractionWorker, is_video_file

logger = logging.getLogger(__name__)

# Helper type alias for known sizes: (size_bytes, timestamp)
Tuple_SizeTime = tuple[int, float]


def is_file_copy_finished(file_path: str, known_sizes: Dict[str, Tuple_SizeTime]) -> bool:
    """Check if a file has finished being written by comparing size across intervals."""
    if not os.path.exists(file_path):
        return False

    try:
        current_size = os.path.getsize(file_path)
        if current_size == 0:
            return False

        # Attempt to open file to ensure no active write locks
        with open(file_path, "rb") as f:
            f.read(1024)

        now = time.time()
        if file_path in known_sizes:
            prev_size, prev_time = known_sizes[file_path]
            # If size hasn't changed in at least 1.5 seconds, it has stabilized
            if current_size == prev_size and (now - prev_time) >= 1.5:
                return True

        known_sizes[file_path] = (current_size, now)
        return False
    except (PermissionError, OSError):
        # File is still open/locked by copying process
        return False


class VideoFolderWatcher(QObject):
    """Background watcher that monitors a folder for new video files."""

    video_detected = pyqtSignal(str)                   # video_path
    extraction_started = pyqtSignal(str)               # video_name
    extraction_progress = pyqtSignal(str)              # status message
    extraction_completed = pyqtSignal(str, str, bool)  # (video_name, audio_path, was_deleted)
    extraction_failed = pyqtSignal(str, str)           # (video_name, error_message)
    queue_updated = pyqtSignal(int)                    # remaining queue count

    def __init__(self, watch_folder: Optional[str] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.watch_folder = Path(watch_folder) if watch_folder else VIDEO_INBOX_DIR
        self._is_watching = False
        self._known_sizes: Dict[str, Tuple_SizeTime] = {}
        self._processing_queue: Deque[str] = deque()
        self._active_worker: Optional[VideoExtractionWorker] = None
        self._in_progress_file: Optional[str] = None
        self._seen_files: Set[str] = set()

        # Polling timer
        self._timer = QTimer(self)
        self._timer.setInterval(2000)  # Check every 2 seconds
        self._timer.timeout.connect(self._poll_folder)

    def set_watch_folder(self, folder_path: str):
        """Update watched folder directory."""
        new_dir = Path(folder_path)
        if new_dir != self.watch_folder:
            self.watch_folder = new_dir
            self._known_sizes.clear()
            self._seen_files.clear()
            self.watch_folder.mkdir(parents=True, exist_ok=True)
            self.scan_folder()

    def start_watching(self):
        """Start periodic folder monitoring."""
        self.watch_folder.mkdir(parents=True, exist_ok=True)
        self._is_watching = True
        self._timer.start()
        self._poll_folder()

    def stop_watching(self):
        """Stop periodic folder monitoring."""
        self._is_watching = False
        self._timer.stop()

    @property
    def is_watching(self) -> bool:
        return self._is_watching

    def scan_folder(self) -> List[str]:
        """Scan the folder and return list of valid video files."""
        if not self.watch_folder.exists():
            return []

        videos = []
        try:
            for item in self.watch_folder.iterdir():
                if item.is_file() and is_video_file(str(item)):
                    videos.append(str(item.resolve()))
        except Exception as e:
            logger.warning(f"Error scanning watch folder: {e}")
        return videos

    def queue_video(self, video_path: str):
        """Add a specific video file directly into the processing queue."""
        p = str(Path(video_path).resolve())
        if p not in self._processing_queue and p != self._in_progress_file:
            self._processing_queue.append(p)
            self.queue_updated.emit(len(self._processing_queue))
            self._process_next()

    def process_now(self):
        """Explicitly scan the folder and queue all detected video files immediately."""
        videos = self.scan_folder()
        for v in videos:
            self.queue_video(v)

    def _poll_folder(self):
        """Timer callback to check for new files and stabilize them."""
        if not self._is_watching or not self.watch_folder.exists():
            return

        videos = self.scan_folder()
        for v_path in videos:
            if v_path in self._processing_queue or v_path == self._in_progress_file:
                continue

            # Check if file has stabilized
            if is_file_copy_finished(v_path, self._known_sizes):
                self._seen_files.add(v_path)
                self.video_detected.emit(v_path)
                self._processing_queue.append(v_path)
                self.queue_updated.emit(len(self._processing_queue))

        self._process_next()

    def _process_next(self):
        """Process the next video in the queue if no extraction is currently active."""
        if self._active_worker is not None or not self._processing_queue:
            return

        next_video = self._processing_queue.popleft()
        self.queue_updated.emit(len(self._processing_queue))

        if not os.path.exists(next_video):
            self._process_next()
            return

        self._in_progress_file = next_video
        video_name = os.path.basename(next_video)
        delete_source = config_manager.get("delete_video_after_extraction", True)

        self.extraction_started.emit(video_name)

        self._active_worker = VideoExtractionWorker(
            video_path=next_video,
            delete_source=delete_source,
            parent=self,
        )
        self._active_worker.progress_status.connect(self._on_worker_status)
        self._active_worker.extraction_completed.connect(self._on_worker_completed)
        self._active_worker.extraction_failed.connect(self._on_worker_failed)
        self._active_worker.start()

    def _on_worker_status(self, msg: str):
        self.extraction_progress.emit(msg)

    def _on_worker_completed(self, video_name: str, audio_path: str, was_deleted: bool):
        self._active_worker = None
        self._in_progress_file = None
        self.extraction_completed.emit(video_name, audio_path, was_deleted)
        # Continue queue
        self._process_next()

    def _on_worker_failed(self, video_name: str, err_msg: str):
        self._active_worker = None
        self._in_progress_file = None
        self.extraction_failed.emit(video_name, err_msg)
        # Continue queue
        self._process_next()
