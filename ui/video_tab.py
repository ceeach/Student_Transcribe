"""Video Ingestion and Watch Folder UI Tab for NotionTranscribe.

Provides folder path visibility, one-click Windows Explorer opening, direct video file browsing,
and automatic extraction/deletion toggles with smooth scrolling and responsive design.
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
    FluentIcon as FIF,
    IconWidget,
    ListWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SingleDirectionScrollArea,
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
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Scroll area guarantees the page remains fully readable and never squished on any screen
        self.scroll_area = SingleDirectionScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(16, 14, 16, 16)
        container_layout.setSpacing(14)

        # 1. Info Banner Card
        banner = CardWidget(container)
        b_layout = QVBoxLayout(banner)
        b_layout.setContentsMargins(16, 14, 16, 14)
        b_layout.setSpacing(6)

        banner_header = QHBoxLayout()
        banner_header.setSpacing(8)
        banner_icon = IconWidget(FIF.MOVIE, banner)
        banner_icon.setFixedSize(20, 20)
        banner_header.addWidget(banner_icon)
        banner_title = StrongBodyLabel("Video Inbox: Audio Extraction & Auto-Deletion", banner)
        banner_header.addWidget(banner_title, 1)
        b_layout.addLayout(banner_header)

        desc = BodyLabel(
            "Drop or place video lecture files (.mp4, .mkv, .mov, .avi, .webm) into your Video Inbox folder.\n"
            "NotionTranscribe extracts speech audio, safely deletes the large video file to save disk space, "
            "and automatically initiates transcription.",
            banner,
        )
        desc.setWordWrap(True)
        b_layout.addWidget(desc)
        container_layout.addWidget(banner)

        # 2. Watch Folder Path & Action Buttons Card
        folder_card = CardWidget(container)
        fg_layout = QVBoxLayout(folder_card)
        fg_layout.setContentsMargins(16, 14, 16, 14)
        fg_layout.setSpacing(12)

        folder_header = QHBoxLayout()
        folder_icon = IconWidget(FIF.FOLDER, folder_card)
        folder_icon.setFixedSize(18, 18)
        folder_header.addWidget(folder_icon)
        folder_title = SubtitleLabel("Watched Video Folder", folder_card)
        folder_header.addWidget(folder_title, 1)
        fg_layout.addLayout(folder_header)

        path_card = CardWidget(folder_card)
        path_card_layout = QHBoxLayout(path_card)
        path_card_layout.setContentsMargins(12, 8, 12, 8)
        self.folder_path_label = StrongBodyLabel(folder_card)
        self.folder_path_label.setWordWrap(True)
        self._update_folder_label()
        path_card_layout.addWidget(self.folder_path_label, 1)

        self.change_folder_btn = PushButton("Change Folder...", folder_card)
        self.change_folder_btn.setIcon(FIF.EDIT)
        self.change_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_folder_btn.setFixedHeight(32)
        self.change_folder_btn.clicked.connect(self._choose_folder)
        path_card_layout.addWidget(self.change_folder_btn)
        fg_layout.addWidget(path_card)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.open_folder_btn = PrimaryPushButton("Open Video Folder in Explorer", folder_card)
        self.open_folder_btn.setIcon(FIF.FOLDER)
        self.open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_folder_btn.setFixedHeight(34)
        self.open_folder_btn.clicked.connect(self._open_folder_explorer)
        btn_row.addWidget(self.open_folder_btn)

        self.process_now_btn = PushButton("Process Videos Now", folder_card)
        self.process_now_btn.setIcon(FIF.SYNC)
        self.process_now_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.process_now_btn.setFixedHeight(34)
        self.process_now_btn.clicked.connect(self._process_now)
        btn_row.addWidget(self.process_now_btn)

        self.browse_video_btn = PushButton("Browse Video File...", folder_card)
        self.browse_video_btn.setIcon(FIF.VIDEO)
        self.browse_video_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_video_btn.setFixedHeight(34)
        self.browse_video_btn.clicked.connect(self._browse_single_video)
        btn_row.addWidget(self.browse_video_btn)

        btn_row.addStretch(1)
        fg_layout.addLayout(btn_row)
        container_layout.addWidget(folder_card)

        # 3. Preferences / Automation Options Card
        opts_card = CardWidget(container)
        og_layout = QVBoxLayout(opts_card)
        og_layout.setContentsMargins(16, 14, 16, 14)
        og_layout.setSpacing(12)

        opts_header = QHBoxLayout()
        opts_icon = IconWidget(FIF.SETTING, opts_card)
        opts_icon.setFixedSize(18, 18)
        opts_header.addWidget(opts_icon)
        opts_title = SubtitleLabel("Automation Preferences", opts_card)
        opts_header.addWidget(opts_title, 1)
        og_layout.addLayout(opts_header)

        # Auto watch toggle
        aw_row = QHBoxLayout()
        aw_row.setSpacing(12)
        self.auto_watch_cb = SwitchButton(opts_card)
        self.auto_watch_cb.setOnText("")
        self.auto_watch_cb.setOffText("")
        self.auto_watch_cb.setChecked(config_manager.get("auto_watch_video_folder", True))
        self.auto_watch_cb.checkedChanged.connect(self._on_auto_watch_toggled)
        aw_row.addWidget(self.auto_watch_cb)
        aw_desc_layout = QVBoxLayout()
        aw_desc_layout.setSpacing(2)
        aw_title = StrongBodyLabel("Auto-Watch Video Folder", opts_card)
        aw_label = BodyLabel("Continuously monitor the inbox folder in background and extract new videos immediately.", opts_card)
        aw_desc_layout.addWidget(aw_title)
        aw_desc_layout.addWidget(aw_label)
        aw_row.addLayout(aw_desc_layout, 1)
        og_layout.addLayout(aw_row)

        # Delete source toggle
        ds_row = QHBoxLayout()
        ds_row.setSpacing(12)
        self.delete_source_cb = SwitchButton(opts_card)
        self.delete_source_cb.setOnText("")
        self.delete_source_cb.setOffText("")
        self.delete_source_cb.setChecked(config_manager.get("delete_video_after_extraction", True))
        self.delete_source_cb.checkedChanged.connect(self._on_delete_source_toggled)
        ds_row.addWidget(self.delete_source_cb)
        ds_desc_layout = QVBoxLayout()
        ds_desc_layout.setSpacing(2)
        ds_title = StrongBodyLabel("Auto-Delete Video After Audio Extraction", opts_card)
        ds_label = BodyLabel("Safely delete the original large video file once 16kHz audio is extracted to free disk space.", opts_card)
        ds_desc_layout.addWidget(ds_title)
        ds_desc_layout.addWidget(ds_label)
        ds_row.addLayout(ds_desc_layout, 1)
        og_layout.addLayout(ds_row)

        # Auto transcribe toggle
        at_row = QHBoxLayout()
        at_row.setSpacing(12)
        self.auto_transcribe_cb = SwitchButton(opts_card)
        self.auto_transcribe_cb.setOnText("")
        self.auto_transcribe_cb.setOffText("")
        self.auto_transcribe_cb.setChecked(config_manager.get("auto_transcribe_video", True))
        self.auto_transcribe_cb.checkedChanged.connect(self._on_auto_transcribe_toggled)
        at_row.addWidget(self.auto_transcribe_cb)
        at_desc_layout = QVBoxLayout()
        at_desc_layout.setSpacing(2)
        at_title = StrongBodyLabel("Auto-Transcribe Extracted Audio", opts_card)
        at_label = BodyLabel("Automatically transcribe the extracted audio as soon as audio extraction finishes.", opts_card)
        at_desc_layout.addWidget(at_title)
        at_desc_layout.addWidget(at_label)
        at_row.addLayout(at_desc_layout, 1)
        og_layout.addLayout(at_row)

        container_layout.addWidget(opts_card)

        # 4. Activity & Queue Log Card
        log_card = CardWidget(container)
        lg_layout = QVBoxLayout(log_card)
        lg_layout.setContentsMargins(16, 14, 16, 14)
        lg_layout.setSpacing(10)

        log_header = QHBoxLayout()
        log_icon = IconWidget(FIF.HISTORY, log_card)
        log_icon.setFixedSize(18, 18)
        log_header.addWidget(log_icon)
        log_title = SubtitleLabel("Detected Videos & Activity Log", log_card)
        log_header.addWidget(log_title, 1)
        lg_layout.addLayout(log_header)

        self.activity_list = ListWidget(log_card)
        self.activity_list.setMinimumHeight(180)
        lg_layout.addWidget(self.activity_list)

        container_layout.addWidget(log_card)
        container_layout.addStretch(1)

        self.scroll_area.setWidget(container)
        root_layout.addWidget(self.scroll_area)

    def _update_folder_label(self):
        watch_path = str(self.watcher.watch_folder)
        self.folder_path_label.setText(watch_path)

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
            self._add_log(f"Watch folder updated to: {chosen}", icon=FIF.FOLDER)

    def _browse_single_video(self):
        filters = "Video Files (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts *.3gp);;All Files (*.*)"
        video_path, _ = QFileDialog.getOpenFileName(self, "Select Video File for Extraction", "", filters)
        if video_path:
            self.watcher.queue_video(video_path)
            self._add_log(f"Queued video: {os.path.basename(video_path)}", icon=FIF.VIDEO)

    def _process_now(self):
        self.watcher.process_now()
        self.refresh_file_list()

    def _on_auto_watch_toggled(self, checked: bool):
        config_manager.set("auto_watch_video_folder", checked)
        config_manager.save()
        if checked:
            self.watcher.start_watching()
            self._add_log("Folder watcher started.", icon=FIF.PLAY)
        else:
            self.watcher.stop_watching()
            self._add_log("Folder watcher stopped.", icon=FIF.PAUSE)

    def _on_delete_source_toggled(self, checked: bool):
        config_manager.set("delete_video_after_extraction", checked)
        config_manager.save()

    def _on_auto_transcribe_toggled(self, checked: bool):
        config_manager.set("auto_transcribe_video", checked)
        config_manager.save()

    def _add_log(self, text: str, icon: Optional[FIF] = None):
        item = QListWidgetItem(text)
        if icon:
            item.setIcon(icon.icon())
        else:
            item.setIcon(FIF.DOCUMENT.icon())
        self.activity_list.addItem(item)
        self.activity_list.scrollToBottom()

    def _on_video_detected(self, path: str):
        self._add_log(f"Detected video: {os.path.basename(path)}", icon=FIF.SEARCH)

    def _on_extraction_started(self, video_name: str):
        self._add_log(f"Extracting audio from: {video_name}...", icon=FIF.SYNC)
        self.status_updated.emit(f"Extracting audio from '{video_name}'...")

    def _on_extraction_progress(self, msg: str):
        self.status_updated.emit(msg)

    def _on_extraction_completed(self, video_name: str, audio_path: str, was_deleted: bool):
        del_msg = " (source video deleted to save disk space)" if was_deleted else ""
        self._add_log(f"Extracted: {os.path.basename(audio_path)}{del_msg}", icon=FIF.COMPLETED)
        self.status_updated.emit(f"Audio extracted from '{video_name}' successfully.")
        self.refresh_file_list()
        self.audio_ready_for_transcription.emit(audio_path, video_name)

    def _on_extraction_failed(self, video_name: str, err_msg: str):
        self._add_log(f"Extraction failed for '{video_name}': {err_msg}", icon=FIF.CLOSE)
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
