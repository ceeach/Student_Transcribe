"""Video Ingestion and Watch Folder UI Tab for NotionTranscribe.

Provides folder path visibility, one-click Windows Explorer opening, direct video file browsing,
and automatic extraction/deletion toggles.
"""

import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ListWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
)

from config_manager import config_manager, VIDEO_INBOX_DIR
from video_watcher import VideoFolderWatcher


class VideoTabWidget(QWidget):
    """Tab widget for managing the Video Watch Folder and automatic extraction."""

    audio_ready_for_transcription = pyqtSignal(str, str)  # (audio_path, original_video_name)
    status_updated = pyqtSignal(str)

    def __init__(self, watcher: VideoFolderWatcher, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.watcher = watcher

        self._init_ui()
        self._connect_watcher()
        self.refresh_file_list()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 1. Info Banner
        banner = CardWidget(self)
        b_layout = QVBoxLayout(banner)
        b_layout.setContentsMargins(12, 12, 12, 12)
        b_layout.setSpacing(4)

        title = StrongBodyLabel("📹 Video Inbox: Audio Extraction & Auto-Deletion", self)
        desc = BodyLabel(
            "Place any video file (.mp4, .mkv, .mov, .avi, .webm) into your Video Inbox folder.\n"
            "NotionTranscribe will extract 16kHz speech audio, safely delete the video file to save disk space, "
            "and transcribe the audio.",
            self,
        )
        desc.setWordWrap(True)

        b_layout.addWidget(title)
        b_layout.addWidget(desc)
        layout.addWidget(banner)

        # 2. Watch Folder Path & Action Buttons
        folder_card = CardWidget(self)
        fg_layout = QVBoxLayout(folder_card)
        fg_layout.setContentsMargins(12, 12, 12, 12)
        fg_layout.setSpacing(10)
        
        folder_title = SubtitleLabel("Watched Video Folder", self)
        fg_layout.addWidget(folder_title)

        path_row = QHBoxLayout()
        self.folder_path_label = BodyLabel(self)
        self._update_folder_label()
        path_row.addWidget(self.folder_path_label, 1)

        self.change_folder_btn = PushButton("Change Folder...", self)
        self.change_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_folder_btn.clicked.connect(self._choose_folder)
        path_row.addWidget(self.change_folder_btn)

        fg_layout.addLayout(path_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.open_folder_btn = PrimaryPushButton("📂 Open Video Folder in Explorer", self)
        self.open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_folder_btn.clicked.connect(self._open_folder_explorer)
        btn_row.addWidget(self.open_folder_btn)

        self.process_now_btn = PushButton("⚡ Process Videos Now", self)
        self.process_now_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.process_now_btn.clicked.connect(self._process_now)
        btn_row.addWidget(self.process_now_btn)

        self.browse_video_btn = PushButton("Browse Video File...", self)
        self.browse_video_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_video_btn.clicked.connect(self._browse_single_video)
        btn_row.addWidget(self.browse_video_btn)

        fg_layout.addLayout(btn_row)
        layout.addWidget(folder_card)

        # 3. Preferences / Automation Options
        opts_card = CardWidget(self)
        og_layout = QVBoxLayout(opts_card)
        og_layout.setContentsMargins(12, 10, 12, 10)
        og_layout.setSpacing(10)
        
        opts_title = SubtitleLabel("Automation Preferences", self)
        og_layout.addWidget(opts_title)

        # Auto watch
        aw_row = QHBoxLayout()
        self.auto_watch_cb = SwitchButton(self)
        self.auto_watch_cb.setChecked(config_manager.get("auto_watch_video_folder", True))
        self.auto_watch_cb.checkedChanged.connect(self._on_auto_watch_toggled)
        aw_label = BodyLabel("Automatically watch folder and process new video drops", self)
        aw_row.addWidget(self.auto_watch_cb)
        aw_row.addWidget(aw_label, 1)
        og_layout.addLayout(aw_row)

        # Delete source
        ds_row = QHBoxLayout()
        self.delete_source_cb = SwitchButton(self)
        self.delete_source_cb.setChecked(config_manager.get("delete_video_after_extraction", True))
        self.delete_source_cb.checkedChanged.connect(self._on_delete_source_toggled)
        ds_label = BodyLabel("Delete video file after successful audio extraction (frees disk space)", self)
        ds_row.addWidget(self.delete_source_cb)
        ds_row.addWidget(ds_label, 1)
        og_layout.addLayout(ds_row)

        # Auto transcribe
        at_row = QHBoxLayout()
        self.auto_transcribe_cb = SwitchButton(self)
        self.auto_transcribe_cb.setChecked(config_manager.get("auto_transcribe_video", True))
        self.auto_transcribe_cb.checkedChanged.connect(self._on_auto_transcribe_toggled)
        at_label = BodyLabel("Automatically transcribe audio immediately after extraction", self)
        at_row.addWidget(self.auto_transcribe_cb)
        at_row.addWidget(at_label, 1)
        og_layout.addLayout(at_row)

        layout.addWidget(opts_card)

        # 4. Activity & Queue Log
        log_card = CardWidget(self)
        lg_layout = QVBoxLayout(log_card)
        lg_layout.setContentsMargins(10, 10, 10, 10)
        
        log_title = SubtitleLabel("Detected Videos & Activity Log", self)
        lg_layout.addWidget(log_title)

        self.activity_list = ListWidget(self)
        self.activity_list.setMaximumHeight(140)
        lg_layout.addWidget(self.activity_list)

        layout.addWidget(log_card)

    def _update_folder_label(self):
        watch_path = str(self.watcher.watch_folder)
        self.folder_path_label.setText(f"📁 {watch_path}")

    def _connect_watcher(self):
        self.watcher.extraction_started.connect(self._on_extraction_started)
        self.watcher.extraction_progress.connect(self._on_extraction_progress)
        self.watcher.extraction_completed.connect(self._on_extraction_completed)
        self.watcher.extraction_failed.connect(self._on_extraction_failed)
        self.watcher.video_detected.connect(self._on_video_detected)

    def _open_folder_explorer(self):
        watch_dir = self.watcher.watch_folder
        watch_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(watch_dir))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(watch_dir)])
        else:
            subprocess.run(["xdg-open", str(watch_dir)])

    def _choose_folder(self):
        current = str(self.watcher.watch_folder)
        chosen = QFileDialog.getExistingDirectory(self, "Select Video Watch Folder", current)
        if chosen:
            self.watcher.set_watch_folder(chosen)
            config_manager.set("video_watch_folder", chosen)
            config_manager.save()
            self._update_folder_label()
            self.refresh_file_list()
            self._add_log(f"Watch folder updated to: {chosen}")

    def _browse_single_video(self):
        filters = "Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts *.3gp);;All Files (*.*)"
        video_path, _ = QFileDialog.getOpenFileName(self, "Select Video File for Extraction", "", filters)
        if video_path:
            self.watcher.queue_video(video_path)
            self._add_log(f"Queued video: {os.path.basename(video_path)}")

    def _process_now(self):
        self.watcher.process_now()
        self.refresh_file_list()

    def _on_auto_watch_toggled(self, checked: bool):
        config_manager.set("auto_watch_video_folder", checked)
        config_manager.save()
        if checked:
            self.watcher.start_watching()
            self._add_log("Folder watcher started.")
        else:
            self.watcher.stop_watching()
            self._add_log("Folder watcher stopped.")

    def _on_delete_source_toggled(self, checked: bool):
        config_manager.set("delete_video_after_extraction", checked)
        config_manager.save()

    def _on_auto_transcribe_toggled(self, checked: bool):
        config_manager.set("auto_transcribe_video", checked)
        config_manager.save()

    def _add_log(self, text: str):
        item = QListWidgetItem(text)
        self.activity_list.addItem(item)
        self.activity_list.scrollToBottom()

    def _on_video_detected(self, path: str):
        self._add_log(f"🔍 Detected video: {os.path.basename(path)}")

    def _on_extraction_started(self, video_name: str):
        self._add_log(f"⏳ Extracting audio from: {video_name}...")
        self.status_updated.emit(f"Extracting audio from '{video_name}'...")

    def _on_extraction_progress(self, msg: str):
        self.status_updated.emit(msg)

    def _on_extraction_completed(self, video_name: str, audio_path: str, was_deleted: bool):
        del_msg = " (source video deleted)" if was_deleted else ""
        self._add_log(f"✅ Extracted: {os.path.basename(audio_path)}{del_msg}")
        self.status_updated.emit(f"Audio extracted from '{video_name}' successfully.")
        self.refresh_file_list()
        self.audio_ready_for_transcription.emit(audio_path, video_name)

    def _on_extraction_failed(self, video_name: str, err_msg: str):
        self._add_log(f"❌ Extraction failed for '{video_name}': {err_msg}")
        self.status_updated.emit(f"Failed to extract audio from '{video_name}'.")
        msg_box = MessageBox(
            "Video Extraction Error",
            f"Could not extract audio from video '{video_name}':\n\n{err_msg}",
            self
        )
        msg_box.cancelButton.hide()
        msg_box.exec()

    def refresh_file_list(self):
        videos = self.watcher.scan_folder()
        if not videos:
            pass
