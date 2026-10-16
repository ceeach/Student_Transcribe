"""Drag-and-Drop audio file upload zone for NotionTranscribe.

Supports .mp3, .wav, .m4a, .ogg, .flac, .aac with visual drop feedback
and file metadata extraction.
"""

import os
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from qfluentwidgets import (
    CardWidget,
    FluentIcon as FIF,
    IconWidget,
    SubtitleLabel,
    CaptionLabel,
    PushButton,
    StrongBodyLabel
)

from video_processor import SUPPORTED_VIDEO_EXTENSIONS, is_video_file

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}
SUPPORTED_EXTENSIONS = SUPPORTED_AUDIO_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS


def format_file_size(num_bytes: int) -> str:
    """Format bytes into readable human size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


class DropZoneWidget(CardWidget):
    """Interactive drag & drop box and file picker for audio and video files."""

    file_selected = pyqtSignal(str)
    video_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")

        self._selected_path: Optional[str] = None
        self._is_drag_active = False

        self._init_ui()
        self._update_style()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_row = QHBoxLayout()
        icon_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_widget = IconWidget(FIF.MUSIC, self)
        self.icon_widget.setFixedSize(40, 40)
        icon_row.addWidget(self.icon_widget)

        self.title_label = SubtitleLabel("Drag & drop your audio or video file here", self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.subtitle_label = CaptionLabel("Supports MP3, WAV, M4A, OGG, FLAC and MP4, MKV, MOV, AVI videos", self)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.browse_btn = PushButton("Browse Files...", self)
        self.browse_btn.setIcon(FIF.FOLDER_ADD)
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self._open_file_dialog)
        btn_layout.addWidget(self.browse_btn)

        self.file_info_label = StrongBodyLabel("", self)
        self.file_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_info_label.setWordWrap(True)

        layout.addLayout(icon_row)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addLayout(btn_layout)
        layout.addWidget(self.file_info_label)

    def _update_style(self):
        pass

    def _open_file_dialog(self):
        from audio_recorder import RECORDINGS_DIR
        initial_dir = str(RECORDINGS_DIR) if RECORDINGS_DIR.exists() else ""
        filters = (
            "All Supported Media (*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.mp4 *.mkv *.mov *.avi *.webm *.wmv);;"
            "Audio Files (*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.wma);;"
            "Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.wmv *.flv *.m4v *.ts);;"
            "All Files (*.*)"
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audio or Video File for Transcription",
            initial_dir,
            filters,
        )
        if file_path:
            self.set_file(file_path)

    def set_file(self, file_path: str):
        """Set the active file path and emit signal."""
        if not os.path.exists(file_path):
            return

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            self.file_info_label.setText(f"Unsupported format: {ext}")
            return

        self._selected_path = file_path
        filename = os.path.basename(file_path)
        size = format_file_size(os.path.getsize(file_path))

        if is_video_file(file_path):
            self.icon_widget.setIcon(FIF.MOVIE)
            self.title_label.setText(f"Selected Video: {filename}")
            self.file_info_label.setText(f"Video ready for extraction & transcription ({size})")
            self._update_style()
            self.video_dropped.emit(file_path)
        else:
            self.icon_widget.setIcon(FIF.MUSIC)
            self.title_label.setText(f"Selected Audio: {filename}")
            self.file_info_label.setText(f"Path: {file_path} ({size})")
            self._update_style()
            self.file_selected.emit(file_path)

    def clear(self):
        """Clear currently selected file."""
        self._selected_path = None
        self.icon_widget.setIcon(FIF.MUSIC)
        self.title_label.setText("Drag & drop your audio or video file here")
        self.file_info_label.setText("")
        self._update_style()

    def get_selected_path(self) -> Optional[str]:
        return self._selected_path

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                path = urls[0].toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    event.acceptProposedAction()
                    self._is_drag_active = True
                    self._update_style()
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._is_drag_active = False
        self._update_style()
        event.accept()

    def dropEvent(self, event: QDropEvent):
        self._is_drag_active = False
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.set_file(path)
            event.acceptProposedAction()
        else:
            self._update_style()
            event.ignore()
