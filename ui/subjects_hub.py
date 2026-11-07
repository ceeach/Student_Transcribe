"""Subjects Hub Widget for Transcribe Studio.

Provides a dedicated full-screen Course and Subject Management Hub where
students can view all recordings, videos, extracted transcripts, and AI summaries
organized cleanly by academic subject and course code.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional, List, Dict, Any

try:
    import soundfile as sf
except ImportError:
    sf = None

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QListWidgetItem,
    QFileDialog,
)
from qfluentwidgets import (
    TitleLabel,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    StrongBodyLabel,
    LineEdit,
    SearchLineEdit,
    TextEdit,
    PushButton,
    PrimaryPushButton,
    TransparentPushButton,
    CardWidget,
    SimpleCardWidget,
    ListWidget,
    TableWidget,
    SegmentedWidget,
    MessageBox,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
)

from config_manager import config_manager, VIDEO_INBOX_DIR
from subject_manager import Subject, subject_manager
from transcript_storage import (
    get_recording_academic_info,
    update_recording_academic_info,
    get_transcript_paths,
    save_transcript,
)
from logger import get_logger

logger = get_logger("SubjectsHub")

RECORDINGS_DIR = Path.home() / ".notion_transcribe" / "recordings"
TRANSCRIPTS_DIR = Path.home() / ".notion_transcribe" / "transcripts"


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def format_file_size(bytes_val: int) -> str:
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    return f"{bytes_val / (1024 * 1024):.1f} MB"


class SubjectsHubWidget(QWidget):
    """Dedicated full-screen Hub for subjects, lectures, videos, transcripts, and summaries."""

    load_recording_requested = pyqtSignal(str)
    chat_with_recording_requested = pyqtSignal(str)
    play_audio_requested = pyqtSignal(str)
    record_for_subject_requested = pyqtSignal(str)
    upload_for_subject_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current_subject: Optional[str] = None  # None means "All Subjects"
        self._subjects_data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._all_data: Dict[str, List[Dict[str, Any]]] = {
            "recordings": [],
            "videos": [],
            "transcripts": [],
            "summaries": [],
        }

        self._init_ui()
        self.refresh()

    def _init_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(14)

        # Main Splitter between Left Course List and Right Content Hub
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.setChildrenCollapsible(False)

        # ==========================================
        # LEFT PANE: Subject / Course Navigator
        # ==========================================
        left_card = CardWidget(self)
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        left_header = QHBoxLayout()
        left_header.addWidget(StrongBodyLabel("📚 Academic Courses", self), 1)
        self.refresh_subjs_btn = TransparentPushButton(self)
        self.refresh_subjs_btn.setIcon(FIF.SYNC)
        self.refresh_subjs_btn.setToolTip("Refresh subjects and media")
        self.refresh_subjs_btn.clicked.connect(self.refresh)
        left_header.addWidget(self.refresh_subjs_btn)
        left_layout.addLayout(left_header)

        self.search_subject_input = SearchLineEdit(self)
        self.search_subject_input.setPlaceholderText("Filter courses...")
        self.search_subject_input.textChanged.connect(self._filter_subjects_list)
        left_layout.addWidget(self.search_subject_input)

        self.subject_list = ListWidget(self)
        self.subject_list.itemSelectionChanged.connect(self._on_subject_selection_changed)
        left_layout.addWidget(self.subject_list, 1)

        left_actions = QHBoxLayout()
        left_actions.setSpacing(6)

        self.add_subject_btn = PushButton("+ Subject", self)
        self.add_subject_btn.setIcon(FIF.ADD)
        self.add_subject_btn.setToolTip("Add a new academic course")
        self.add_subject_btn.clicked.connect(self._prompt_add_subject)
        left_actions.addWidget(self.add_subject_btn, 1)

        self.manage_subject_btn = TransparentPushButton("Manage", self)
        self.manage_subject_btn.setIcon(FIF.SETTING)
        self.manage_subject_btn.setToolTip("Edit, rename, or delete subjects")
        self.manage_subject_btn.clicked.connect(self._open_subject_manager)
        left_actions.addWidget(self.manage_subject_btn)

        left_layout.addLayout(left_actions)
        main_splitter.addWidget(left_card)

        # ==========================================
        # RIGHT PANE: Subject Content Hub
        # ==========================================
        right_container = QWidget(self)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        # 1. Subject Header Banner Card
        self.header_card = CardWidget(self)
        header_layout = QVBoxLayout(self.header_card)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(10)

        banner_top = QHBoxLayout()
        banner_info = QVBoxLayout()
        banner_info.setSpacing(4)

        self.subject_title_label = TitleLabel("All Academic Subjects", self.header_card)
        banner_info.addWidget(self.subject_title_label)

        self.subject_meta_label = CaptionLabel("Viewing unified repository of all course lectures and materials.", self.header_card)
        self.subject_meta_label.setWordWrap(True)
        banner_info.addWidget(self.subject_meta_label)
        banner_top.addLayout(banner_info, 1)

        banner_actions = QHBoxLayout()
        banner_actions.setSpacing(8)

        self.record_for_subj_btn = PushButton("Record Session", self.header_card)
        self.record_for_subj_btn.setIcon(FIF.MICROPHONE)
        self.record_for_subj_btn.clicked.connect(self._on_record_for_subject)
        banner_actions.addWidget(self.record_for_subj_btn)

        self.upload_for_subj_btn = PushButton("Upload Media", self.header_card)
        self.upload_for_subj_btn.setIcon(FIF.FOLDER)
        self.upload_for_subj_btn.clicked.connect(self._on_upload_for_subject)
        banner_actions.addWidget(self.upload_for_subj_btn)

        banner_top.addLayout(banner_actions)
        header_layout.addLayout(banner_top)

        # Category Stats Chips
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(14)
        self.stat_rec_label = CaptionLabel("🎤 0 Recordings", self.header_card)
        self.stat_vid_label = CaptionLabel("🎥 0 Videos", self.header_card)
        self.stat_tr_label = CaptionLabel("📄 0 Transcripts", self.header_card)
        self.stat_sum_label = CaptionLabel("✨ 0 AI Summaries", self.header_card)

        self.stats_row.addWidget(self.stat_rec_label)
        self.stats_row.addWidget(self.stat_vid_label)
        self.stats_row.addWidget(self.stat_tr_label)
        self.stats_row.addWidget(self.stat_sum_label)
        self.stats_row.addStretch(1)
        header_layout.addLayout(self.stats_row)

        right_layout.addWidget(self.header_card)

        # 2. Segmented Navigation Bar
        nav_card = SimpleCardWidget(self)
        nav_layout = QHBoxLayout(nav_card)
        nav_layout.setContentsMargins(8, 6, 8, 6)

        self.segmented_nav = SegmentedWidget(self)
        self.segmented_nav.addItem("recordings", "🎤 Recordings (0)", onClick=lambda: self.stack.setCurrentIndex(0))
        self.segmented_nav.addItem("videos", "🎥 Videos (0)", onClick=lambda: self.stack.setCurrentIndex(1))
        self.segmented_nav.addItem("transcripts", "📄 Transcripts (0)", onClick=lambda: self.stack.setCurrentIndex(2))
        self.segmented_nav.addItem("summaries", "✨ Summaries (0)", onClick=lambda: self.stack.setCurrentIndex(3))
        self.segmented_nav.setCurrentItem("recordings")
        nav_layout.addWidget(self.segmented_nav, 0, Qt.AlignmentFlag.AlignLeft)
        nav_layout.addStretch(1)

        right_layout.addWidget(nav_card)

        # 3. Stacked Pages (Recordings, Videos, Transcripts, Summaries)
        self.stack = QStackedWidget(self)

        self.page_recordings = self._create_recordings_page()
        self.page_videos = self._create_videos_page()
        self.page_transcripts = self._create_transcripts_page()
        self.page_summaries = self._create_summaries_page()

        self.stack.addWidget(self.page_recordings)
        self.stack.addWidget(self.page_videos)
        self.stack.addWidget(self.page_transcripts)
        self.stack.addWidget(self.page_summaries)

        right_layout.addWidget(self.stack, 1)
        main_splitter.addWidget(right_container)

        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)

        root_layout.addWidget(main_splitter)

    # -------------------------------------------------------------
    # PAGE 1: RECORDINGS VIEW
    # -------------------------------------------------------------
    def _create_recordings_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.rec_table = TableWidget(self)
        self.rec_table.setColumnCount(6)
        self.rec_table.setHorizontalHeaderLabels([
            "Recording Name", "Session / Modules", "Date", "Duration", "Size", "Transcript Status"
        ])
        self.rec_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.rec_table.horizontalHeader().resizeSection(0, 220)
        self.rec_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.rec_table.horizontalHeader().resizeSection(1, 180)
        self.rec_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.rec_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.rec_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.rec_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.rec_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rec_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rec_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rec_table.itemSelectionChanged.connect(self._on_rec_selection_changed)
        layout.addWidget(self.rec_table, 1)

        btn_row = QHBoxLayout()
        self.rec_play_btn = PushButton("▶️ Play Audio", self)
        self.rec_play_btn.setEnabled(False)
        self.rec_play_btn.clicked.connect(self._play_selected_recording)
        btn_row.addWidget(self.rec_play_btn)

        self.rec_load_btn = PrimaryPushButton("⚡ Load in Studio", self)
        self.rec_load_btn.setEnabled(False)
        self.rec_load_btn.clicked.connect(self._load_selected_recording)
        btn_row.addWidget(self.rec_load_btn)

        self.rec_chat_btn = PushButton("💬 Ask Lecture AI", self)
        self.rec_chat_btn.setEnabled(False)
        self.rec_chat_btn.clicked.connect(self._chat_selected_recording)
        btn_row.addWidget(self.rec_chat_btn)

        self.rec_edit_btn = PushButton("🏷️ Allocate to Subject...", self)
        self.rec_edit_btn.setIcon(FIF.EDIT)
        self.rec_edit_btn.setEnabled(False)
        self.rec_edit_btn.setToolTip("Assign or move this recording to a course or subject")
        self.rec_edit_btn.clicked.connect(self._allocate_selected_recording)
        btn_row.addWidget(self.rec_edit_btn)

        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        return page

    # -------------------------------------------------------------
    # PAGE 2: VIDEOS VIEW
    # -------------------------------------------------------------
    def _create_videos_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.vid_table = TableWidget(self)
        self.vid_table.setColumnCount(4)
        self.vid_table.setHorizontalHeaderLabels([
            "Video File Name", "File Size", "Date Added", "Audio Extraction Status"
        ])
        self.vid_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.vid_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.vid_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.vid_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.vid_table.horizontalHeader().resizeSection(3, 180)
        self.vid_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.vid_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.vid_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vid_table.itemSelectionChanged.connect(self._on_vid_selection_changed)
        layout.addWidget(self.vid_table, 1)

        btn_row = QHBoxLayout()
        self.vid_extract_btn = PrimaryPushButton("⚡ Extract & Transcribe", self)
        self.vid_extract_btn.setEnabled(False)
        self.vid_extract_btn.clicked.connect(self._extract_selected_video)
        btn_row.addWidget(self.vid_extract_btn)

        self.vid_open_btn = PushButton("▶️ Open Video", self)
        self.vid_open_btn.setEnabled(False)
        self.vid_open_btn.clicked.connect(self._open_selected_video)
        btn_row.addWidget(self.vid_open_btn)

        self.vid_folder_btn = PushButton("📁 Open Videos Folder", self)
        self.vid_folder_btn.clicked.connect(self._open_videos_folder)
        btn_row.addWidget(self.vid_folder_btn)

        self.vid_allocate_btn = PushButton("🏷️ Allocate to Subject...", self)
        self.vid_allocate_btn.setIcon(FIF.EDIT)
        self.vid_allocate_btn.setEnabled(False)
        self.vid_allocate_btn.clicked.connect(self._allocate_selected_video)
        btn_row.addWidget(self.vid_allocate_btn)

        self.vid_import_btn = PushButton("+ Add Video to Course", self)
        self.vid_import_btn.setIcon(FIF.ADD)
        self.vid_import_btn.clicked.connect(self._import_video_file)
        btn_row.addWidget(self.vid_import_btn)

        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        return page

    # -------------------------------------------------------------
    # PAGE 3: TRANSCRIPTS VIEW
    # -------------------------------------------------------------
    def _create_transcripts_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.tr_table = TableWidget(self)
        self.tr_table.setColumnCount(6)
        self.tr_table.setHorizontalHeaderLabels([
            "Transcript Title", "Date Extracted", "Duration", "Words", "Speakers", "Model"
        ])
        self.tr_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tr_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.tr_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tr_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tr_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tr_table.itemSelectionChanged.connect(self._on_tr_selection_changed)
        layout.addWidget(self.tr_table, 1)

        btn_row = QHBoxLayout()
        self.tr_view_btn = PushButton("👁️ View Full Text", self)
        self.tr_view_btn.setEnabled(False)
        self.tr_view_btn.clicked.connect(self._view_selected_transcript)
        btn_row.addWidget(self.tr_view_btn)

        self.tr_load_btn = PrimaryPushButton("⚡ Open in Studio", self)
        self.tr_load_btn.setEnabled(False)
        self.tr_load_btn.clicked.connect(self._load_selected_transcript)
        btn_row.addWidget(self.tr_load_btn)

        self.tr_chat_btn = PushButton("💬 Ask Lecture AI", self)
        self.tr_chat_btn.setEnabled(False)
        self.tr_chat_btn.clicked.connect(self._chat_selected_transcript)
        btn_row.addWidget(self.tr_chat_btn)

        self.tr_export_btn = PushButton("💾 Export (.txt / .md)", self)
        self.tr_export_btn.setEnabled(False)
        self.tr_export_btn.clicked.connect(self._export_selected_transcript)
        btn_row.addWidget(self.tr_export_btn)

        self.tr_allocate_btn = PushButton("🏷️ Allocate to Subject...", self)
        self.tr_allocate_btn.setIcon(FIF.EDIT)
        self.tr_allocate_btn.setEnabled(False)
        self.tr_allocate_btn.clicked.connect(self._allocate_selected_transcript)
        btn_row.addWidget(self.tr_allocate_btn)

        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        return page

    # -------------------------------------------------------------
    # PAGE 4: SUMMARIES & STUDY GUIDES VIEW
    # -------------------------------------------------------------
    def _create_summaries_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Splitter: Left = list of summaries, Right = Markdown notes reader/editor
        sum_splitter = QSplitter(Qt.Orientation.Horizontal, page)
        sum_splitter.setChildrenCollapsible(False)

        # Left list
        left_box = QWidget(sum_splitter)
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(StrongBodyLabel("Saved Lecture Notes", left_box))
        self.sum_table = TableWidget(left_box)
        self.sum_table.setColumnCount(3)
        self.sum_table.setHorizontalHeaderLabels(["Lecture Session", "Date", "Words"])
        self.sum_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sum_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.sum_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.sum_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sum_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sum_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sum_table.itemSelectionChanged.connect(self._on_sum_selection_changed)
        left_layout.addWidget(self.sum_table, 1)
        sum_splitter.addWidget(left_box)

        # Right notes viewer
        right_box = CardWidget(sum_splitter)
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(8)

        top_notes_bar = QHBoxLayout()
        self.sum_title_label = StrongBodyLabel("Select a lecture summary to read or edit.", right_box)
        top_notes_bar.addWidget(self.sum_title_label, 1)

        self.sum_copy_btn = TransparentPushButton("Copy Notes", right_box)
        self.sum_copy_btn.setIcon(FIF.COPY)
        self.sum_copy_btn.setEnabled(False)
        self.sum_copy_btn.clicked.connect(self._copy_selected_summary)
        top_notes_bar.addWidget(self.sum_copy_btn)

        self.sum_save_btn = PushButton("Save Changes", right_box)
        self.sum_save_btn.setIcon(FIF.SAVE)
        self.sum_save_btn.setEnabled(False)
        self.sum_save_btn.clicked.connect(self._save_edited_summary)
        top_notes_bar.addWidget(self.sum_save_btn)

        right_layout.addLayout(top_notes_bar)

        self.sum_text_edit = TextEdit(right_box)
        self.sum_text_edit.setPlaceholderText("AI educational study guide, key definitions, and active recall flashcards will appear here...")
        right_layout.addWidget(self.sum_text_edit, 1)

        right_btn_row = QHBoxLayout()
        self.sum_chat_btn = PushButton("💬 Ask Lecture AI about these notes", right_box)
        self.sum_chat_btn.setIcon(FIF.CHAT)
        self.sum_chat_btn.setEnabled(False)
        self.sum_chat_btn.clicked.connect(self._chat_selected_summary)
        right_btn_row.addWidget(self.sum_chat_btn)

        self.sum_export_btn = PushButton("Export Markdown", right_box)
        self.sum_export_btn.setIcon(FIF.SAVE_AS)
        self.sum_export_btn.setEnabled(False)
        self.sum_export_btn.clicked.connect(self._export_selected_summary)
        right_btn_row.addWidget(self.sum_export_btn)

        right_btn_row.addStretch(1)
        right_layout.addLayout(right_btn_row)

        sum_splitter.addWidget(right_box)
        sum_splitter.setStretchFactor(0, 1)
        sum_splitter.setStretchFactor(1, 2)

        layout.addWidget(sum_splitter, 1)
        return page

    # =============================================================
    # DATA SCANNING & REFRESH LOGIC
    # =============================================================
    def refresh(self):
        """Scan all local directories and aggregate assets by course subject."""
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_INBOX_DIR.mkdir(parents=True, exist_ok=True)

        subjects = subject_manager.get_all_subjects()
        subject_names = [s.name for s in subjects]

        # Structure storage for subjects
        self._subjects_data = {
            s: {"recordings": [], "videos": [], "transcripts": [], "summaries": []}
            for s in subject_names
        }
        self._subjects_data["General / Other"] = {"recordings": [], "videos": [], "transcripts": [], "summaries": []}

        self._all_data = {
            "recordings": [],
            "videos": [],
            "transcripts": [],
            "summaries": [],
        }

        # 1. Scan Transcripts
        transcript_files = list(TRANSCRIPTS_DIR.glob("*.json"))
        recorded_audio_linked = set()

        for tpath in transcript_files:
            try:
                with open(tpath, "r", encoding="utf-8") as f:
                    tdata = json.load(f)
            except Exception:
                continue

            subj = tdata.get("subject") or "General / Other"
            if subj not in self._subjects_data:
                self._subjects_data[subj] = {"recordings": [], "videos": [], "transcripts": [], "summaries": []}

            fname = tdata.get("audio_filename", tpath.stem)
            audio_f = tdata.get("audio_file", "")
            if audio_f:
                recorded_audio_linked.add(Path(audio_f).name.lower())
            recorded_audio_linked.add(fname.lower())

            dur = format_duration(float(tdata.get("duration_seconds", 0.0)))
            dt_raw = tdata.get("created_at", "")
            try:
                dt_str = datetime.fromisoformat(dt_raw).strftime("%Y-%m-%d %H:%M")
            except Exception:
                dt_str = dt_raw[:16] if dt_raw else "—"

            tr_info = {
                "name": fname,
                "json_path": str(tpath),
                "audio_path": audio_f,
                "date_str": dt_str,
                "duration_str": dur,
                "words": tdata.get("word_count", 0),
                "speakers": tdata.get("speaker_count", 1),
                "model": tdata.get("model", "Whisper"),
                "has_notes": bool(tdata.get("study_notes")),
                "full_text": tdata.get("full_text", ""),
                "modules": tdata.get("modules", []),
                "session_type": tdata.get("session_type", "Lecture"),
                "subject": subj,
            }

            self._subjects_data[subj]["transcripts"].append(tr_info)
            self._all_data["transcripts"].append(tr_info)

            # Check if it has a summary/study guide
            notes = (tdata.get("study_notes") or "").strip()
            if notes:
                sum_info = {
                    "title": f"Notes - {fname}",
                    "json_path": str(tpath),
                    "audio_path": audio_f,
                    "date_str": dt_str,
                    "notes_text": notes,
                    "words": len(notes.split()),
                    "modules": tdata.get("modules", []),
                    "subject": subj,
                }
                self._subjects_data[subj]["summaries"].append(sum_info)
                self._all_data["summaries"].append(sum_info)

        # 2. Scan Recordings directory
        audio_exts = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
        if RECORDINGS_DIR.exists():
            for apath in RECORDINGS_DIR.iterdir():
                if apath.is_file() and apath.suffix.lower() in audio_exts:
                    stat = apath.stat()
                    dt_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    sz_str = format_file_size(stat.st_size)

                    dur_str = "--:--"
                    if sf is not None and apath.suffix.lower() == ".wav":
                        try:
                            info = sf.info(str(apath))
                            if info.duration:
                                dur_str = format_duration(info.duration)
                        except Exception:
                            pass

                    academic = get_recording_academic_info(str(apath))
                    subj = academic.get("subject") or "General / Other"
                    if subj not in self._subjects_data:
                        self._subjects_data[subj] = {"recordings": [], "videos": [], "transcripts": [], "summaries": []}

                    has_tr = (apath.name.lower() in recorded_audio_linked)

                    rec_info = {
                        "name": apath.name,
                        "path": str(apath),
                        "size_str": sz_str,
                        "duration_str": dur_str,
                        "date_str": dt_str,
                        "has_transcript": has_tr,
                        "modules": academic.get("modules", []),
                        "session_type": academic.get("session_type", "Lecture"),
                        "subject": subj,
                    }
                    self._subjects_data[subj]["recordings"].append(rec_info)
                    self._all_data["recordings"].append(rec_info)

        # 3. Scan Videos (watch folder + video_inbox)
        video_folders = [VIDEO_INBOX_DIR]
        user_watch_folder = config_manager.get("video_watch_folder")
        if user_watch_folder and Path(user_watch_folder).exists() and Path(user_watch_folder) not in video_folders:
            video_folders.append(Path(user_watch_folder))

        video_exts = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
        seen_videos = set()

        for vfolder in video_folders:
            if not vfolder.exists():
                continue
            for vpath in vfolder.iterdir():
                if vpath.is_file() and vpath.suffix.lower() in video_exts:
                    if str(vpath).lower() in seen_videos:
                        continue
                    seen_videos.add(str(vpath).lower())

                    stat = vpath.stat()
                    dt_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    sz_str = format_file_size(stat.st_size)

                    # Check sidecar meta or matched recording
                    audio_stem = vpath.stem
                    is_extracted = (f"{audio_stem}.wav".lower() in recorded_audio_linked)

                    # Check sidecar meta
                    meta_path = vpath.parent / f"{vpath.stem}.meta.json"
                    v_subj = "General / Other"
                    if meta_path.exists():
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                mdata = json.load(f)
                                v_subj = mdata.get("subject") or v_subj
                        except Exception:
                            pass

                    if v_subj not in self._subjects_data:
                        self._subjects_data[v_subj] = {"recordings": [], "videos": [], "transcripts": [], "summaries": []}

                    vid_info = {
                        "name": vpath.name,
                        "path": str(vpath),
                        "size_str": sz_str,
                        "date_str": dt_str,
                        "is_extracted": is_extracted,
                        "subject": v_subj,
                    }
                    self._subjects_data[v_subj]["videos"].append(vid_info)
                    self._all_data["videos"].append(vid_info)

        self._populate_subjects_list()
        self._render_current_subject()

    def _populate_subjects_list(self):
        curr_selection = self._current_subject
        self.subject_list.blockSignals(True)
        self.subject_list.clear()

        query = self.search_subject_input.text().strip().lower()

        # Add "All Subjects" at top
        all_count = sum(len(v) for v in self._all_data.values())
        all_item = QListWidgetItem(f"📚 All Subjects  ({all_count})")
        all_item.setData(Qt.ItemDataRole.UserRole, "__ALL__")
        self.subject_list.addItem(all_item)

        subjects = subject_manager.get_all_subjects()
        target_item = all_item

        for s in subjects:
            s_name = s.name
            s_data = self._subjects_data.get(s_name, {"recordings": [], "videos": [], "transcripts": [], "summaries": []})
            total_items = sum(len(lst) for lst in s_data.values())

            code_prefix = f"[{s.code}] " if s.code else ""
            label_text = f"{code_prefix}{s_name}  ({total_items})"

            if query and query not in label_text.lower():
                continue

            item = QListWidgetItem(label_text)
            item.setData(Qt.ItemDataRole.UserRole, s_name)
            self.subject_list.addItem(item)

            if curr_selection and s_name == curr_selection:
                target_item = item

        self.subject_list.blockSignals(False)
        self.subject_list.setCurrentItem(target_item)

    def _filter_subjects_list(self):
        self._populate_subjects_list()

    def _on_subject_selection_changed(self):
        item = self.subject_list.currentItem()
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        self._current_subject = None if data == "__ALL__" else data
        self._render_current_subject()

    def _render_current_subject(self):
        """Update banner, stats, and tables based on selected subject."""
        subj_name = self._current_subject
        if subj_name is None:
            # All Subjects
            data = self._all_data
            self.subject_title_label.setText("📚 All Academic Subjects")
            self.subject_meta_label.setText("Unified repository of recordings, videos, transcripts, and AI notes across all courses.")
        else:
            data = self._subjects_data.get(subj_name, {"recordings": [], "videos": [], "transcripts": [], "summaries": []})
            subj_obj = subject_manager.get_subject(subj_name)
            code_prefix = f"[{subj_obj.code}] " if subj_obj and subj_obj.code else ""
            self.subject_title_label.setText(f"{code_prefix}{subj_name}")

            mod_list = subj_obj.modules if subj_obj and subj_obj.modules else []
            mod_text = f"Modules: {', '.join(mod_list[:4])}{'...' if len(mod_list) > 4 else ''}" if mod_list else "General Syllabus"
            self.subject_meta_label.setText(f"Active Course  •  {mod_text}")

        # Update Stats Chips
        num_rec = len(data["recordings"])
        num_vid = len(data["videos"])
        num_tr = len(data["transcripts"])
        num_sum = len(data["summaries"])

        self.stat_rec_label.setText(f"🎤 {num_rec} Recording{'s' if num_rec != 1 else ''}")
        self.stat_vid_label.setText(f"🎥 {num_vid} Video{'s' if num_vid != 1 else ''}")
        self.stat_tr_label.setText(f"📄 {num_tr} Transcript{'s' if num_tr != 1 else ''}")
        self.stat_sum_label.setText(f"✨ {num_sum} AI Summar{'ies' if num_sum != 1 else 'y'}")

        # Update Segmented Nav Titles
        self.segmented_nav.setItemText("recordings", f"🎤 Recordings ({num_rec})")
        self.segmented_nav.setItemText("videos", f"🎥 Videos ({num_vid})")
        self.segmented_nav.setItemText("transcripts", f"📄 Transcripts ({num_tr})")
        self.segmented_nav.setItemText("summaries", f"✨ Summaries ({num_sum})")

        # Populate individual tables
        self._populate_recordings_table(data["recordings"])
        self._populate_videos_table(data["videos"])
        self._populate_transcripts_table(data["transcripts"])
        self._populate_summaries_table(data["summaries"])

    # -------------------------------------------------------------
    # POPULATING TABLES
    # -------------------------------------------------------------
    def _populate_recordings_table(self, items: List[Dict[str, Any]]):
        self.rec_table.setRowCount(len(items))
        for row, rec in enumerate(items):
            item_name = QTableWidgetItem(rec["name"])
            item_name.setData(Qt.ItemDataRole.UserRole, rec["path"])

            mods = ", ".join(rec.get("modules", [])) or rec.get("session_type", "Lecture")
            item_mods = QTableWidgetItem(mods)

            item_date = QTableWidgetItem(rec.get("date_str", "—"))
            item_dur = QTableWidgetItem(rec.get("duration_str", "—"))
            item_sz = QTableWidgetItem(rec.get("size_str", "—"))

            if rec.get("has_transcript"):
                item_st = QTableWidgetItem("✅ Transcribed")
                item_st.setForeground(QColor("#38bdf8"))
            else:
                item_st = QTableWidgetItem("⏳ Pending")
                item_st.setForeground(QColor("#94a3b8"))

            self.rec_table.setItem(row, 0, item_name)
            self.rec_table.setItem(row, 1, item_mods)
            self.rec_table.setItem(row, 2, item_date)
            self.rec_table.setItem(row, 3, item_dur)
            self.rec_table.setItem(row, 4, item_sz)
            self.rec_table.setItem(row, 5, item_st)

        self._on_rec_selection_changed()

    def _populate_videos_table(self, items: List[Dict[str, Any]]):
        self.vid_table.setRowCount(len(items))
        for row, vid in enumerate(items):
            item_name = QTableWidgetItem(vid["name"])
            item_name.setData(Qt.ItemDataRole.UserRole, vid["path"])

            item_sz = QTableWidgetItem(vid.get("size_str", "—"))
            item_date = QTableWidgetItem(vid.get("date_str", "—"))

            if vid.get("is_extracted"):
                item_st = QTableWidgetItem("✅ Audio Extracted")
                item_st.setForeground(QColor("#10b981"))
            else:
                item_st = QTableWidgetItem("⏳ Not Extracted")
                item_st.setForeground(QColor("#f59e0b"))

            self.vid_table.setItem(row, 0, item_name)
            self.vid_table.setItem(row, 1, item_sz)
            self.vid_table.setItem(row, 2, item_date)
            self.vid_table.setItem(row, 3, item_st)

        self._on_vid_selection_changed()

    def _populate_transcripts_table(self, items: List[Dict[str, Any]]):
        self.tr_table.setRowCount(len(items))
        for row, tr in enumerate(items):
            item_name = QTableWidgetItem(tr["name"])
            item_name.setData(Qt.ItemDataRole.UserRole, tr["json_path"])
            item_name.setData(Qt.ItemDataRole.UserRole + 1, tr.get("audio_path"))

            item_date = QTableWidgetItem(tr.get("date_str", "—"))
            item_dur = QTableWidgetItem(tr.get("duration_str", "—"))
            item_wc = QTableWidgetItem(f"{tr.get('words', 0):,} words")
            item_spk = QTableWidgetItem(f"{tr.get('speakers', 1)} spk")
            item_mod = QTableWidgetItem(tr.get("model", "Whisper"))

            self.tr_table.setItem(row, 0, item_name)
            self.tr_table.setItem(row, 1, item_date)
            self.tr_table.setItem(row, 2, item_dur)
            self.tr_table.setItem(row, 3, item_wc)
            self.tr_table.setItem(row, 4, item_spk)
            self.tr_table.setItem(row, 5, item_mod)

        self._on_tr_selection_changed()

    def _populate_summaries_table(self, items: List[Dict[str, Any]]):
        self.sum_table.setRowCount(len(items))
        for row, sm in enumerate(items):
            item_name = QTableWidgetItem(sm["title"])
            item_name.setData(Qt.ItemDataRole.UserRole, sm)

            item_date = QTableWidgetItem(sm.get("date_str", "—"))
            item_wc = QTableWidgetItem(f"{sm.get('words', 0):,} w")

            self.sum_table.setItem(row, 0, item_name)
            self.sum_table.setItem(row, 1, item_date)
            self.sum_table.setItem(row, 2, item_wc)

        self._on_sum_selection_changed()

    # -------------------------------------------------------------
    # SELECTION HANDLERS
    # -------------------------------------------------------------
    def _on_rec_selection_changed(self):
        has_sel = self.rec_table.currentRow() >= 0
        self.rec_play_btn.setEnabled(has_sel)
        self.rec_load_btn.setEnabled(has_sel)
        self.rec_chat_btn.setEnabled(has_sel)
        self.rec_edit_btn.setEnabled(has_sel)

    def _on_vid_selection_changed(self):
        has_sel = self.vid_table.currentRow() >= 0
        self.vid_extract_btn.setEnabled(has_sel)
        self.vid_open_btn.setEnabled(has_sel)
        self.vid_allocate_btn.setEnabled(has_sel)

    def _on_tr_selection_changed(self):
        has_sel = self.tr_table.currentRow() >= 0
        self.tr_view_btn.setEnabled(has_sel)
        self.tr_load_btn.setEnabled(has_sel)
        self.tr_chat_btn.setEnabled(has_sel)
        self.tr_export_btn.setEnabled(has_sel)
        self.tr_allocate_btn.setEnabled(has_sel)

    def _on_sum_selection_changed(self):
        row = self.sum_table.currentRow()
        has_sel = row >= 0
        self.sum_copy_btn.setEnabled(has_sel)
        self.sum_save_btn.setEnabled(has_sel)
        self.sum_chat_btn.setEnabled(has_sel)
        self.sum_export_btn.setEnabled(has_sel)

        if has_sel:
            item = self.sum_table.item(row, 0)
            data = item.data(Qt.ItemDataRole.UserRole) if item else None
            if data:
                self.sum_title_label.setText(data.get("title", "Lecture Notes"))
                self.sum_text_edit.setPlainText(data.get("notes_text", ""))
        else:
            self.sum_title_label.setText("Select a lecture summary to read or edit.")
            self.sum_text_edit.clear()

    # -------------------------------------------------------------
    # ACTION HANDLERS
    # -------------------------------------------------------------
    def _play_selected_recording(self):
        row = self.rec_table.currentRow()
        if row >= 0:
            item = self.rec_table.item(row, 0)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and os.path.exists(path):
                self.play_audio_requested.emit(path)

    def _load_selected_recording(self):
        row = self.rec_table.currentRow()
        if row >= 0:
            item = self.rec_table.item(row, 0)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and os.path.exists(path):
                self.load_recording_requested.emit(path)

    def _chat_selected_recording(self):
        row = self.rec_table.currentRow()
        if row >= 0:
            item = self.rec_table.item(row, 0)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and os.path.exists(path):
                self.chat_with_recording_requested.emit(path)

    def _allocate_selected_recording(self):
        row = self.rec_table.currentRow()
        if row < 0:
            return
        path = self.rec_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self._show_allocation_dialog(path)

    def _allocate_selected_video(self):
        row = self.vid_table.currentRow()
        if row < 0:
            return
        path = self.vid_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self._show_allocation_dialog(path)

    def _allocate_selected_transcript(self):
        row = self.tr_table.currentRow()
        if row < 0:
            return
        path = self.tr_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self._show_allocation_dialog(path)

    def _show_allocation_dialog(self, target_path: str):
        if not target_path or not os.path.exists(target_path):
            return

        from qfluentwidgets import MessageBoxBase, SubtitleLabel, CaptionLabel, BodyLabel, ComboBox, LineEdit

        academic = get_recording_academic_info(target_path)
        current_subj = academic.get("subject", "")
        current_type = academic.get("session_type", "Lecture")
        current_mods = academic.get("modules", [])

        dialog = MessageBoxBase(self.window())
        dialog.titleLabel = SubtitleLabel("🏷️ Allocate to Course Subject", dialog)
        dialog.viewLayout.addWidget(dialog.titleLabel)

        fname = Path(target_path).name
        dialog.viewLayout.addWidget(CaptionLabel(f"Target: {fname}", dialog))

        dialog.viewLayout.addWidget(BodyLabel("Select Course / Subject:", dialog))
        subj_combo = ComboBox(dialog)
        subjects = subject_manager.get_all_subjects()
        for s in subjects:
            code_prefix = f"[{s.code}] " if s.code else ""
            subj_combo.addItem(f"{code_prefix}{s.name}", userData=s.name)

        if current_subj:
            idx = subj_combo.findData(current_subj)
            if idx < 0:
                idx = subj_combo.findText(current_subj)
            if idx >= 0:
                subj_combo.setCurrentIndex(idx)
        dialog.viewLayout.addWidget(subj_combo)

        dialog.viewLayout.addWidget(BodyLabel("Session Type:", dialog))
        type_combo = ComboBox(dialog)
        type_combo.addItems(["📖 Theory Lecture", "✏️ Exercise / Problem Set", "🔬 Lab / Workshop"])
        t_idx = type_combo.findText(current_type)
        if t_idx >= 0:
            type_combo.setCurrentIndex(t_idx)
        dialog.viewLayout.addWidget(type_combo)

        dialog.viewLayout.addWidget(BodyLabel("Modules Covered:", dialog))
        mod_input = LineEdit(dialog)
        mod_input.setText(", ".join(current_mods))
        mod_input.setPlaceholderText("e.g. Module 1: Intro, Module 2: Systems")
        dialog.viewLayout.addWidget(mod_input)

        dialog.yesButton.setText("Allocate to Subject")
        dialog.cancelButton.setText("Cancel")

        if dialog.exec():
            new_subj = subj_combo.currentData() or subj_combo.currentText()
            new_type = type_combo.currentText()
            new_mods = [m.strip() for m in mod_input.text().split(",") if m.strip()]
            update_recording_academic_info(target_path, new_subj, new_type, new_mods)
            self.refresh()
            win = self.window()
            if hasattr(win, "recordings_list"):
                win.recordings_list.refresh()
            InfoBar.success(
                title="Subject Allocated",
                content=f"Successfully allocated '{fname}' to '{new_subj}'.",
                duration=3000,
                parent=self,
            )

    def _extract_selected_video(self):
        row = self.vid_table.currentRow()
        if row < 0:
            return
        vpath = self.vid_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not vpath or not os.path.exists(vpath):
            return

        # Queue video for audio extraction
        InfoBar.info(
            title="Video Queued",
            content=f"Queued '{Path(vpath).name}' for audio extraction.",
            duration=3000,
            parent=self,
        )
        self.upload_for_subject_requested.emit(vpath)

    def _open_selected_video(self):
        row = self.vid_table.currentRow()
        if row < 0:
            return
        vpath = self.vid_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if vpath and os.path.exists(vpath):
            if sys.platform == "win32":
                os.startfile(vpath)
            else:
                subprocess.run(["xdg-open", vpath])

    def _open_videos_folder(self):
        if VIDEO_INBOX_DIR.exists():
            if sys.platform == "win32":
                os.startfile(str(VIDEO_INBOX_DIR))
            else:
                subprocess.run(["xdg-open", str(VIDEO_INBOX_DIR)])

    def _import_video_file(self):
        fpath, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video Lecture",
            "",
            "Video Files (*.mp4 *.mkv *.mov *.avi *.webm);;All Files (*.*)",
        )
        if fpath:
            # Tag video sidecar with current subject
            subj = self._current_subject or "General / Other"
            meta_path = Path(fpath).parent / f"{Path(fpath).stem}.meta.json"
            try:
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump({"subject": subj, "session_type": "Lecture", "modules": []}, f, indent=2)
            except Exception:
                pass
            self.refresh()
            InfoBar.success(
                title="Video Added",
                content=f"Added '{Path(fpath).name}' to {subj}.",
                duration=3000,
                parent=self,
            )

    def _view_selected_transcript(self):
        row = self.tr_table.currentRow()
        if row < 0:
            return
        jpath = self.tr_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if jpath and os.path.exists(jpath):
            txt_path = Path(jpath).with_suffix(".txt")
            if txt_path.exists():
                if sys.platform == "win32":
                    os.startfile(str(txt_path))
                else:
                    subprocess.run(["xdg-open", str(txt_path)])

    def _load_selected_transcript(self):
        row = self.tr_table.currentRow()
        if row < 0:
            return
        apath = self.tr_table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)
        jpath = self.tr_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        target = apath if apath and os.path.exists(apath) else jpath
        if target:
            self.load_recording_requested.emit(target)

    def _chat_selected_transcript(self):
        row = self.tr_table.currentRow()
        if row < 0:
            return
        apath = self.tr_table.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)
        jpath = self.tr_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        target = apath if apath and os.path.exists(apath) else jpath
        if target:
            self.chat_with_recording_requested.emit(target)

    def _export_selected_transcript(self):
        row = self.tr_table.currentRow()
        if row < 0:
            return
        jpath = self.tr_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not jpath or not os.path.exists(jpath):
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Transcript",
            f"{Path(jpath).stem}.md",
            "Markdown Files (*.md);;Text Files (*.txt);;All Files (*.*)",
        )
        if out_path:
            try:
                with open(jpath, "r", encoding="utf-8") as f:
                    tdata = json.load(f)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(tdata.get("full_text", ""))
                InfoBar.success(title="Exported", content=f"Saved to {out_path}", duration=3000, parent=self)
            except Exception as e:
                InfoBar.error(title="Export Failed", content=str(e), duration=4000, parent=self)

    def _copy_selected_summary(self):
        from PyQt6.QtWidgets import QApplication
        text = self.sum_text_edit.toPlainText().strip()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
                InfoBar.success(title="Copied", content="Study notes copied to clipboard.", duration=3000, parent=self)

    def _save_edited_summary(self):
        row = self.sum_table.currentRow()
        if row < 0:
            return
        data = self.sum_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        jpath = data.get("json_path")
        if not jpath or not os.path.exists(jpath):
            return

        new_notes = self.sum_text_edit.toPlainText()
        try:
            with open(jpath, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            tdata["study_notes"] = new_notes
            tdata["updated_at"] = datetime.now().isoformat()
            with open(jpath, "w", encoding="utf-8") as f:
                json.dump(tdata, f, indent=2, ensure_ascii=False)
            InfoBar.success(title="Notes Saved", content="Lecture notes updated locally.", duration=3000, parent=self)
            self.refresh()
        except Exception as e:
            InfoBar.error(title="Save Failed", content=str(e), duration=4000, parent=self)

    def _chat_selected_summary(self):
        row = self.sum_table.currentRow()
        if row < 0:
            return
        data = self.sum_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if data:
            apath = data.get("audio_path")
            jpath = data.get("json_path")
            target = apath if apath and os.path.exists(apath) else jpath
            if target:
                self.chat_with_recording_requested.emit(target)

    def _export_selected_summary(self):
        text = self.sum_text_edit.toPlainText().strip()
        if not text:
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Lecture Summary",
            "Lecture_Study_Guide.md",
            "Markdown Files (*.md);;Text Files (*.txt);;All Files (*.*)",
        )
        if out_path:
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(text)
                InfoBar.success(title="Exported", content=f"Saved study notes to {out_path}", duration=3000, parent=self)
            except Exception as e:
                InfoBar.error(title="Export Failed", content=str(e), duration=4000, parent=self)

    def _prompt_add_subject(self):
        from ui.subject_dialog import SubjectManagementDialog
        dlg = SubjectManagementDialog(self.window())
        dlg._start_new_subject()
        dlg.subjects_changed.connect(self.refresh)
        dlg.exec()
        self.refresh()

    def _open_subject_manager(self):
        from ui.subject_dialog import SubjectManagementDialog
        dlg = SubjectManagementDialog(self.window())
        dlg.subjects_changed.connect(self.refresh)
        dlg.exec()
        self.refresh()

    def _on_record_for_subject(self):
        subj = self._current_subject or ""
        self.record_for_subject_requested.emit(subj)

    def _on_upload_for_subject(self):
        subj = self._current_subject or ""
        self.upload_for_subject_requested.emit(subj)
