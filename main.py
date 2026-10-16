"""NotionTranscribe - Desktop Application Entry Point and Main GUI.

Cross-platform audio transcription desktop application using PyQt6,
PyQt-Fluent-Widgets (WinUI3 Fluent Design), faster-whisper, and Notion API.
"""

from datetime import datetime
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QTime, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    NavigationItemPosition,
    Pivot,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RoundMenu,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TextEdit,
    TitleLabel,
    ToolButton,
    TransparentPushButton,
    isDarkTheme,
    setTheme,
    setThemeColor,
    Theme,
)

from audio_player import AudioPlayer
from audio_recorder import AudioRecorder, RECORDINGS_DIR
from config_manager import config_manager, VIDEO_INBOX_DIR
from notion_worker import NotionPushWorker, NotionFetchPagesWorker
from slide_extractor import SlideExtractorWorker, build_whisper_context_prompts
from transcriber import WhisperWorker, format_timestamp
from transcript_diff import TranscriptDiffEngine
from transcript_storage import (
    TRANSCRIPTS_DIR,
    delete_transcript,
    export_to_markdown,
    export_to_notebooklm,
    export_to_srt,
    export_to_vtt,
    has_transcript,
    load_transcript,
    save_transcript,
    update_transcript_notion_info,
)
from ai_worker import AIStudyNotesWorker
from ui.audio_meter import AudioMeter
from ui.compare_dialog import CompareDialog
from ui.drop_zone import DropZoneWidget
from ui.recordings_list import RecordingsListWidget
from ui.settings_dialog import SettingsDialog
from ui.video_tab import VideoTabWidget
from video_watcher import VideoFolderWatcher
from logger import setup_logging, open_log_file

import subprocess
import tempfile


class ClickableTranscriptEdit(TextEdit):
    """Fluent TextEdit supporting clickable #play:start-end timestamp links and #speaker:Name links."""

    anchor_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewport().setMouseTracking(True)

    def mouseMoveEvent(self, event):
        anchor = self.anchorAt(event.pos())
        if anchor:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            anchor = self.anchorAt(event.pos())
            if anchor:
                self.anchor_clicked.emit(anchor)
                return
        super().mouseReleaseEvent(event)


SPEAKER_BADGE_COLORS = [
    ("#1e3a8a", "#93c5fd"),  # Speaker 1: Blue
    ("#064e3b", "#34d399"),  # Speaker 2: Emerald Green
    ("#78350f", "#fbbf24"),  # Speaker 3: Amber / Orange
    ("#581c87", "#c084fc"),  # Speaker 4: Purple
    ("#881337", "#fb7185"),  # Speaker 5: Rose / Pink
    ("#1e293b", "#cbd5e1"),  # Speaker 6+: Slate
]


def format_speaker_badge_html(speaker: str, speaker_id: int = 0) -> str:
    """Return clickable HTML span badge formatted for dark UI."""
    bg, fg = SPEAKER_BADGE_COLORS[speaker_id % len(SPEAKER_BADGE_COLORS)]
    return (
        f'<a href="speaker:{speaker}" style="text-decoration: none;">'
        f'<span style="background-color: {bg}; color: {fg}; '
        f'font-size: 11px; font-weight: bold; padding: 2px 6px; '
        f'border-radius: 4px; margin-right: 6px;">{speaker}</span></a>'
    )


def get_icon_path() -> str:
    """Get the absolute path to the optimal icon file for the current operating system."""
    base_dir = os.path.abspath(os.path.dirname(__file__))
    if sys.platform == "win32":
        ico = os.path.join(base_dir, "assets", "icon.ico")
        if os.path.exists(ico):
            return ico
    png = os.path.join(base_dir, "assets", "icon.png")
    if os.path.exists(png):
        return png
    ico = os.path.join(base_dir, "assets", "icon.ico")
    if os.path.exists(ico):
        return ico
    return ""


def apply_windows_taskbar_icon(window, icon_path: str):
    """Explicitly assign Win32 WM_SETICON to window HWND for guaranteed Windows taskbar rendering."""
    if sys.platform != "win32" or not icon_path or not os.path.exists(icon_path):
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        hicon_small = ctypes.windll.user32.LoadImageW(
            None, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE
        )
        if not hicon_small:
            hicon_small = ctypes.windll.user32.LoadImageW(
                None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
        hicon_big = ctypes.windll.user32.LoadImageW(
            None, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE
        )
        if not hicon_big:
            hicon_big = ctypes.windll.user32.LoadImageW(
                None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
        if hicon_small:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
        if hicon_big:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
    except Exception:
        pass


# ============================================================
# Transcribe Interface - Main page with Pivot tabs
# ============================================================
class TranscribeInterface(QWidget):
    """Main transcription page with Pivot sub-tabs for Record/Upload/Video."""

    def __init__(self, recorder: AudioRecorder, video_watcher: VideoFolderWatcher, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("transcribeInterface")
        self.recorder = recorder
        self.video_watcher = video_watcher
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # ---- Pivot Navigation for Source Tabs ----
        self.pivot = Pivot(self)
        self.stacked_widget = QStackedWidget(self)

        # Tab 1: Live Recording
        self.record_page = self._create_record_page()
        self.record_page.setObjectName("recordPage")

        # Tab 2: File Upload
        self.upload_page = self._create_upload_page()
        self.upload_page.setObjectName("uploadPage")

        # Tab 3: Video Inbox
        self.video_tab = VideoTabWidget(watcher=self.video_watcher, parent=self)
        self.video_tab.setObjectName("videoPage")

        # Add to stack and pivot
        self._add_pivot_page(self.record_page, "recordPage", "🎙 Record Live")
        self._add_pivot_page(self.upload_page, "uploadPage", "📁 Upload File")
        self._add_pivot_page(self.video_tab, "videoPage", "📹 Video Inbox")

        self.pivot.setCurrentItem("recordPage")
        self.stacked_widget.setCurrentWidget(self.record_page)

        main_layout.addWidget(self.pivot)
        main_layout.addWidget(self.stacked_widget)

        # ---- Notion Metadata Card ----
        self._create_metadata_card(main_layout)

        # ---- Transcript Preview Card ----
        self._create_transcript_card(main_layout)

        # ---- Progress Section ----
        self._create_progress_section(main_layout)

        # ---- Action Bar ----
        self._create_action_bar(main_layout)

    def _add_pivot_page(self, widget, route_key, text):
        self.stacked_widget.addWidget(widget)
        self.pivot.addItem(routeKey=route_key, text=text,
                          onClick=lambda: self.stacked_widget.setCurrentWidget(widget))

    def _create_record_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Audio source selector
        source_row = QHBoxLayout()
        source_row.setSpacing(10)
        source_label = CaptionLabel("Audio Source:", self)
        source_row.addWidget(source_label)

        self.source_mode_combo = ComboBox(self)
        self.source_mode_combo.addItem("🎙 Microphone Only", userData="mic")
        self.source_mode_combo.addItem("🔊 System Audio Only (Speakers Loopback)", userData="system")
        self.source_mode_combo.addItem("🎙+🔊 Both (Microphone + System Audio)", userData="both")
        self.source_mode_combo.setToolTip(
            "Select audio capture source:\n"
            "- Microphone Only: Standard voice recording\n"
            "- System Audio Only: Record internal computer sound\n"
            "- Both: Dual real-time mixing"
        )
        saved_source = config_manager.get("audio_source", "mic")
        for i in range(self.source_mode_combo.count()):
            if self.source_mode_combo.itemData(i) == saved_source:
                self.source_mode_combo.setCurrentIndex(i)
                break
        source_row.addWidget(self.source_mode_combo, 1)
        layout.addLayout(source_row)

        # Timer and VU meter card
        meter_card = CardWidget(self)
        meter_card_layout = QVBoxLayout(meter_card)
        meter_card_layout.setContentsMargins(16, 12, 16, 12)
        meter_card_layout.setSpacing(8)

        timer_row = QHBoxLayout()
        self.timer_label = TitleLabel("00:00:00", self)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont("Cascadia Code", 28)
        font.setWeight(QFont.Weight.Bold)
        self.timer_label.setFont(font)

        self.record_status_badge = CaptionLabel("READY TO RECORD", self)

        timer_row.addWidget(self.timer_label, 1)
        timer_row.addWidget(self.record_status_badge)
        meter_card_layout.addLayout(timer_row)

        # Meter header
        self.meter_level_label = CaptionLabel("Mic Level:", self)
        meter_card_layout.addWidget(self.meter_level_label)

        self.audio_meter = AudioMeter(self)
        meter_card_layout.addWidget(self.audio_meter)
        layout.addWidget(meter_card)

        # Control buttons
        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_row.setSpacing(12)

        self.record_btn = PrimaryPushButton("● Start Recording", self)
        self.record_btn.setFixedSize(180, 44)

        self.pause_btn = PushButton("❚❚ Pause", self)
        self.pause_btn.setFixedSize(130, 44)
        self.pause_btn.setEnabled(False)

        self.stop_btn = PushButton("■ Stop", self)
        self.stop_btn.setFixedSize(130, 44)
        self.stop_btn.setEnabled(False)

        btn_row.addWidget(self.record_btn)
        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row)

        # Saved file info
        saved_row = QHBoxLayout()
        self.saved_file_label = CaptionLabel("", self)
        self.open_folder_btn = TransparentPushButton("📂 Open Recordings Folder", self)
        saved_row.addWidget(self.saved_file_label, 1)
        saved_row.addWidget(self.open_folder_btn)
        layout.addLayout(saved_row)

        return page

    def _create_upload_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        self.drop_zone = DropZoneWidget(self)
        layout.addWidget(self.drop_zone)
        return page

    def _create_metadata_card(self, parent_layout):
        meta_card = CardWidget(self)
        meta_layout = QVBoxLayout(meta_card)
        meta_layout.setContentsMargins(16, 14, 16, 14)
        meta_layout.setSpacing(10)

        meta_header = StrongBodyLabel("Notion Target Page & Details", self)
        meta_layout.addWidget(meta_header)

        # Target page row
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(BodyLabel("Target Page:", self))

        self.target_page_combo = ComboBox(self)
        self.target_page_combo.addItem("✨ [+ Create New Page in Database]", userData=None)
        row1.addWidget(self.target_page_combo, 1)

        self.today_filter_cb = CheckBox("Created Today only", self)
        self.today_filter_cb.setChecked(True)
        self.today_filter_cb.setToolTip("Show only Notion pages created today")
        row1.addWidget(self.today_filter_cb)

        self.clear_filter_btn = TransparentPushButton("Clear Filter", self)
        self.clear_filter_btn.setToolTip("Clear filter to show all database pages")
        row1.addWidget(self.clear_filter_btn)

        self.refresh_pages_btn = PushButton("🔄 Refresh", self)
        self.refresh_pages_btn.setToolTip("Fetch latest pages from Notion database")
        row1.addWidget(self.refresh_pages_btn)
        meta_layout.addLayout(row1)

        # Title row
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.title_label = BodyLabel("Page Title:", self)
        row2.addWidget(self.title_label)
        self.title_input = LineEdit(self)
        default_title = f"Transcript - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        self.title_input.setText(default_title)
        self.title_input.setPlaceholderText("Meeting / Audio Note Title")
        row2.addWidget(self.title_input, 1)
        meta_layout.addLayout(row2)

        # Tags row
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self.tags_label = BodyLabel("Tags / Category:", self)
        row3.addWidget(self.tags_label)
        self.tags_input = LineEdit(self)
        self.tags_input.setText(config_manager.get("default_tags", "Meeting, Audio"))
        self.tags_input.setPlaceholderText("Comma-separated tags (e.g., Meeting, Memo, Interview)")
        row3.addWidget(self.tags_input, 1)
        meta_layout.addLayout(row3)

        # Slide OCR / Course Jargon row (biases Whisper initial_prompt & hotwords)
        row4 = QHBoxLayout()
        row4.setSpacing(8)
        self.jargon_label = BodyLabel("Course Jargon:", self)
        self.jargon_label.setToolTip(
            "Domain vocabulary, acronyms, formulas, and proper nouns passed directly to Whisper's\n"
            "initial_prompt & hotwords so technical terms are spelled accurately."
        )
        row4.addWidget(self.jargon_label)

        self.jargon_input = LineEdit(self)
        self.jargon_input.setPlaceholderText(
            "Import lecture slides (.pdf, .pptx, images) via OCR or type custom terms (e.g., Eigenvalue, Navier-Stokes, NADH)..."
        )
        self.jargon_input.setClearButtonEnabled(True)
        row4.addWidget(self.jargon_input, 1)

        self.import_slides_btn = PushButton("📊 Import Slides / OCR...", self)
        self.import_slides_btn.setToolTip(
            "Extract course jargon and technical vocabulary from PDF slides, PowerPoint (.pptx),\n"
            "or slide screenshots (.png, .jpg) using hybrid text + Windows OCR."
        )
        row4.addWidget(self.import_slides_btn)

        self.paste_slide_btn = TransparentPushButton("📋 Paste Slide", self)
        self.paste_slide_btn.setToolTip("Run OCR on a slide screenshot copied to your clipboard")
        row4.addWidget(self.paste_slide_btn)

        meta_layout.addLayout(row4)

        parent_layout.addWidget(meta_card)

    def _create_transcript_card(self, parent_layout):
        output_card = CardWidget(self)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(14, 12, 14, 12)
        output_layout.setSpacing(8)

        # Header with view toggle and toolbar
        output_header = QHBoxLayout()
        output_header_left = QVBoxLayout()
        output_header_left.setSpacing(2)
        output_header_left.addWidget(StrongBodyLabel("Live Transcript & AI Study Guide", self))
        self.stats_label = CaptionLabel("0 segments | 0 words  •  Tip: Click any [timestamp] to play audio or [Speaker] badge to rename", self)
        output_header_left.addWidget(self.stats_label)
        output_header.addLayout(output_header_left, 1)

        # View Switcher (Transcript vs Study Guide)
        self.view_transcript_btn = PushButton("📜 Transcript", self)
        self.view_transcript_btn.setCheckable(True)
        self.view_transcript_btn.setChecked(True)
        self.view_study_btn = PushButton("📚 AI Study Guide", self)
        self.view_study_btn.setCheckable(True)
        self.view_study_btn.setChecked(False)

        def switch_to_transcript():
            self.view_transcript_btn.setChecked(True)
            self.view_study_btn.setChecked(False)
            self.preview_stack.setCurrentIndex(0)

        def switch_to_study():
            self.view_transcript_btn.setChecked(False)
            self.view_study_btn.setChecked(True)
            self.preview_stack.setCurrentIndex(1)

        self.view_transcript_btn.clicked.connect(switch_to_transcript)
        self.view_study_btn.clicked.connect(switch_to_study)

        output_header.addWidget(self.view_transcript_btn)
        output_header.addWidget(self.view_study_btn)

        self.stop_audio_btn = PushButton("⏹ Stop Audio", self)
        self.stop_audio_btn.setVisible(False)
        self.stop_audio_btn.setToolTip("Stop currently playing audio snippet")
        output_header.addWidget(self.stop_audio_btn)

        self.rename_speaker_btn = PushButton("🗣 Rename Speaker", self)
        self.rename_speaker_btn.setEnabled(False)
        self.rename_speaker_btn.setToolTip("Rename a diarized speaker (e.g. 'Speaker 1' -> 'Prof. Smith') across the entire transcript")
        output_header.addWidget(self.rename_speaker_btn)

        self.copy_btn = PushButton("📋 Copy", self)
        self.copy_btn.setFixedWidth(80)
        output_header.addWidget(self.copy_btn)

        self.export_btn = PushButton("💾 Export ▾", self)
        self.export_btn.setFixedWidth(90)
        self.export_btn.setEnabled(False)
        self.export_menu = RoundMenu(parent=self)

        from PyQt6.QtGui import QAction
        act_txt = QAction("Export Plain Text (.txt)...", self)
        act_srt = QAction("Export Subtitles (.srt)...", self)
        act_vtt = QAction("Export WebVTT (.vtt)...", self)
        act_md = QAction("Export Markdown (.md)...", self)
        act_json = QAction("Export JSON (.json)...", self)
        act_notebooklm = QAction("📓 Export for NotebookLM (.md)...", self)
        act_copy_notebooklm = QAction("📋 Copy for NotebookLM", self)
        act_folder = QAction("📂 Open Transcripts Folder", self)

        self.export_menu.addAction(act_txt)
        self.export_menu.addAction(act_srt)
        self.export_menu.addAction(act_vtt)
        self.export_menu.addAction(act_md)
        self.export_menu.addAction(act_json)
        self.export_menu.addSeparator()
        self.export_menu.addAction(act_notebooklm)
        self.export_menu.addAction(act_copy_notebooklm)
        self.export_menu.addSeparator()
        self.export_menu.addAction(act_folder)

        # Store actions for connecting in MainWindow
        self._export_actions = {
            "txt": act_txt, "srt": act_srt, "vtt": act_vtt,
            "md": act_md, "json": act_json, "notebooklm": act_notebooklm,
            "copy_notebooklm": act_copy_notebooklm, "folder": act_folder,
        }

        self.export_btn.clicked.connect(
            lambda: self.export_menu.exec(self.export_btn.mapToGlobal(
                self.export_btn.rect().bottomLeft()))
        )
        output_header.addWidget(self.export_btn)

        self.clear_btn = PushButton("Clear", self)
        self.clear_btn.setFixedWidth(70)
        output_header.addWidget(self.clear_btn)

        output_layout.addLayout(output_header)

        # Stacked view: page 0 = transcript, page 1 = study notes
        self.preview_stack = QStackedWidget(self)

        self.output_text = ClickableTranscriptEdit(self)
        self.output_text.setReadOnly(True)
        self.output_text.setPlaceholderText(
            "Transcribed text with clickable [mm:ss] audio timestamps and speaker badges will appear here..."
        )
        self.preview_stack.addWidget(self.output_text)

        self.study_notes_text = TextEdit(self)
        self.study_notes_text.setReadOnly(False)
        self.study_notes_text.setPlaceholderText("AI educational study guide, key concepts, and active recall flashcards will appear here...")
        self.preview_stack.addWidget(self.study_notes_text)

        output_layout.addWidget(self.preview_stack)

        parent_layout.addWidget(output_card, 1)

    def _create_progress_section(self, parent_layout):
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(4)

        status_row = QHBoxLayout()
        self.status_label = CaptionLabel("Ready", self)
        status_row.addWidget(self.status_label, 1)
        self.pct_label = CaptionLabel("0%", self)
        status_row.addWidget(self.pct_label)
        progress_layout.addLayout(status_row)

        self.progress_bar = ProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        parent_layout.addLayout(progress_layout)

    def _create_action_bar(self, parent_layout):
        action_layout = QHBoxLayout()
        action_layout.setSpacing(10)

        # Quick Model Selector
        action_layout.addWidget(CaptionLabel("Model:", self))
        self.quick_model_combo = ComboBox(self)
        self.quick_model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        current_model = config_manager.get("whisper_model", "base")
        m_idx = self.quick_model_combo.findText(current_model)
        if m_idx >= 0:
            self.quick_model_combo.setCurrentIndex(m_idx)
        self.quick_model_combo.setToolTip("Select Whisper model size for transcription")
        action_layout.addWidget(self.quick_model_combo)

        # Benchmark button
        self.benchmark_btn = PushButton("⚡ Auto-Tune", self)
        self.benchmark_btn.setToolTip("Benchmark hardware & auto-select optimal model and speed settings")
        action_layout.addWidget(self.benchmark_btn)

        # Diarization toggle
        self.diarize_cb = CheckBox("👥 Diarize Speakers", self)
        self.diarize_cb.setChecked(config_manager.get("enable_diarization", True))
        self.diarize_cb.setToolTip("Identify and label distinct speakers")
        action_layout.addWidget(self.diarize_cb)

        self.transcribe_only_btn = PushButton("Transcribe Audio", self)
        action_layout.addWidget(self.transcribe_only_btn)

        self.compare_models_btn = PushButton("⚖ Compare Models...", self)
        self.compare_models_btn.setEnabled(False)
        self.compare_models_btn.setToolTip("Compare current transcript with a second model")
        action_layout.addWidget(self.compare_models_btn)

        self.study_guide_btn = PushButton("🎓 Study Guide", self)
        self.study_guide_btn.setEnabled(False)
        self.study_guide_btn.setToolTip("Generate comprehensive lecture notes, key concepts, and active recall flashcards using Gemini or Ollama")
        action_layout.addWidget(self.study_guide_btn)

        self.push_only_btn = PushButton("Push to Notion", self)
        self.push_only_btn.setEnabled(False)
        action_layout.addWidget(self.push_only_btn)

        self.action_btn = PrimaryPushButton("⚡ Transcribe & Push to Notion", self)
        action_layout.addWidget(self.action_btn, 1)

        parent_layout.addLayout(action_layout)


# ============================================================
# Main Window - FluentWindow with sidebar navigation
# ============================================================
class MainWindow(FluentWindow):
    """Main Application Window for NotionTranscribe with WinUI3 Fluent Design."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transcribe Notion v1.2.0")
        self.resize(960, 820)
        self.setMinimumSize(780, 680)

        # Set application icon
        self._icon_path = get_icon_path()
        if self._icon_path:
            self.setWindowIcon(QIcon(self._icon_path))

        # State
        self._current_audio_file: Optional[str] = None
        self._audio_source_type: str = "recorded"
        self._transcribed_segments: List[Dict[str, Any]] = []
        self._transcription_text: str = ""
        self._transcription_worker: Optional[WhisperWorker] = None
        self._comparison_worker: Optional[WhisperWorker] = None
        self._notion_worker: Optional[NotionPushWorker] = None
        self._auto_push_after_transcription = False
        self._last_model_name: str = "base"
        self._model_a_name: str = "Model A"
        self._model_b_name: str = "Model B"
        self._model_a_segments: List[Dict[str, Any]] = []
        self._notion_pages: List[Dict[str, Any]] = []
        self._pages_worker: Optional[NotionFetchPagesWorker] = None
        self._study_notes: str = ""
        self._ai_worker: Optional[AIStudyNotesWorker] = None
        self._slide_worker: Optional[SlideExtractorWorker] = None
        self._extracted_slide_text: str = ""

        # Audio Snippet Player for click-to-play timestamps
        self.audio_player = AudioPlayer(self)
        self.audio_player.playback_started.connect(self._on_snippet_playback_started)
        self.audio_player.playback_finished.connect(self._on_snippet_playback_finished)
        self.audio_player.playback_error.connect(self._on_snippet_playback_error)

        # Audio Recorder
        init_source = config_manager.get("audio_source", "mic")
        init_mic_id = config_manager.get("input_device_id", None)
        init_loop_id = config_manager.get("loopback_device_id", None)
        self.recorder = AudioRecorder(
            parent=self,
            audio_source=init_source,
            input_device_id=init_mic_id,
            loopback_device_id=init_loop_id,
        )
        self.recorder.level_changed.connect(self._on_audio_level)
        self.recorder.duration_changed.connect(self._on_record_duration)
        self.recorder.recording_started.connect(self._on_record_started)
        self.recorder.recording_paused.connect(self._on_record_paused)
        self.recorder.recording_finished.connect(self._on_record_finished)
        self.recorder.error_occurred.connect(self._on_record_error)

        # Video Watcher
        watch_folder = config_manager.get("video_watch_folder", str(VIDEO_INBOX_DIR))
        self.video_watcher = VideoFolderWatcher(watch_folder=watch_folder, parent=self)
        self.video_watcher.extraction_completed.connect(self._on_video_audio_extracted)
        self.video_watcher.extraction_failed.connect(self._on_video_extraction_failed)
        self.video_watcher.extraction_progress.connect(self._on_worker_status)
        if config_manager.get("auto_watch_video_folder", True):
            self.video_watcher.start_watching()

        # Create interface pages
        self._init_navigation()
        self._connect_signals()

        # Initial state
        self._refresh_notion_status()
        if config_manager.is_notion_configured():
            self._fetch_notion_pages()

    def _init_navigation(self):
        """Set up FluentWindow sidebar navigation with sub-interfaces."""
        # 1. Transcribe Interface (main page)
        self.transcribe_page = TranscribeInterface(
            recorder=self.recorder,
            video_watcher=self.video_watcher,
            parent=self,
        )
        self.addSubInterface(
            self.transcribe_page,
            FIF.MICROPHONE,
            "Transcribe",
            position=NavigationItemPosition.TOP,
        )

        # 2. Past Recordings Interface
        self.recordings_page = QWidget()
        self.recordings_page.setObjectName("recordingsInterface")
        rec_layout = QVBoxLayout(self.recordings_page)
        rec_layout.setContentsMargins(24, 20, 24, 20)
        self.recordings_list = RecordingsListWidget(self.recordings_page)
        rec_layout.addWidget(self.recordings_list)

        self.addSubInterface(
            self.recordings_page,
            FIF.HISTORY,
            "Recordings",
            position=NavigationItemPosition.TOP,
        )

        # 3. Settings (bottom navigation)
        self.navigationInterface.addItem(
            routeKey="settings",
            icon=FIF.SETTING,
            text="Settings",
            onClick=self._open_settings,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )

        # 4. Diagnostic Logs (bottom navigation)
        self.navigationInterface.addItem(
            routeKey="logs",
            icon=FIF.DOCUMENT,
            text="Logs",
            onClick=self._open_logs,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )

        # Set default page
        self.switchTo(self.transcribe_page)

    def _connect_signals(self):
        """Wire up all signal connections for the transcribe interface."""
        tp = self.transcribe_page

        # Pivot tab changes
        tp.pivot.currentItemChanged.connect(self._on_pivot_changed)

        # Recording controls
        tp.source_mode_combo.currentIndexChanged.connect(self._on_audio_source_mode_changed)
        tp.record_btn.clicked.connect(self._toggle_recording)
        tp.pause_btn.clicked.connect(self._toggle_pause)
        tp.stop_btn.clicked.connect(self._stop_recording)
        tp.open_folder_btn.clicked.connect(self._open_recordings_folder)

        # File upload
        tp.drop_zone.file_selected.connect(self._on_file_selected)
        tp.drop_zone.video_dropped.connect(self._on_video_dropped)

        # Video tab
        tp.video_tab.audio_ready_for_transcription.connect(self._on_video_audio_extracted)
        tp.video_tab.status_updated.connect(self._on_worker_status)

        # Recordings list
        self.recordings_list.recording_selected.connect(self._on_recording_loaded)

        # Metadata & Slide OCR
        tp.target_page_combo.currentIndexChanged.connect(self._on_target_page_changed)
        tp.today_filter_cb.toggled.connect(self._repopulate_page_dropdown)
        tp.clear_filter_btn.clicked.connect(lambda: tp.today_filter_cb.setChecked(False))
        tp.refresh_pages_btn.clicked.connect(self._fetch_notion_pages)
        tp.import_slides_btn.clicked.connect(self._import_slides_for_ocr)
        tp.paste_slide_btn.clicked.connect(self._paste_slide_from_clipboard)

        # Interactive transcript actions (click timestamp to play, click speaker to rename)
        tp.output_text.anchor_clicked.connect(self._on_transcript_anchor_clicked)
        tp.stop_audio_btn.clicked.connect(self.audio_player.stop)
        tp.rename_speaker_btn.clicked.connect(self._prompt_rename_speaker)
        tp.copy_btn.clicked.connect(self._copy_to_clipboard)
        tp._export_actions["txt"].triggered.connect(lambda: self._export_transcript("txt"))
        tp._export_actions["srt"].triggered.connect(lambda: self._export_transcript("srt"))
        tp._export_actions["vtt"].triggered.connect(lambda: self._export_transcript("vtt"))
        tp._export_actions["md"].triggered.connect(lambda: self._export_transcript("md"))
        tp._export_actions["json"].triggered.connect(lambda: self._export_transcript("json"))
        tp._export_actions["notebooklm"].triggered.connect(lambda: self._export_transcript("notebooklm"))
        tp._export_actions["copy_notebooklm"].triggered.connect(self._copy_for_notebooklm)
        tp._export_actions["folder"].triggered.connect(self._open_transcripts_folder)
        tp.clear_btn.clicked.connect(self._clear_transcript)

        # Action bar
        tp.quick_model_combo.currentTextChanged.connect(self._on_quick_model_changed)
        tp.benchmark_btn.clicked.connect(self._open_benchmark_dialog)
        tp.diarize_cb.toggled.connect(self._on_diarize_toggled)
        tp.transcribe_only_btn.clicked.connect(self._start_transcribe_only)
        tp.compare_models_btn.clicked.connect(self._open_model_comparison_flow)
        tp.study_guide_btn.clicked.connect(self._generate_study_guide)
        tp.push_only_btn.clicked.connect(self._start_notion_push)
        tp.action_btn.clicked.connect(self._start_transcribe_and_push)

    # Convenience accessors for transcribe page widgets
    @property
    def _tp(self):
        return self.transcribe_page

    @property
    def source_tabs(self):
        return self._tp.stacked_widget

    @property
    def source_mode_combo(self):
        return self._tp.source_mode_combo

    @property
    def output_text(self):
        return self._tp.output_text

    @property
    def status_label(self):
        return self._tp.status_label

    @property
    def progress_bar(self):
        return self._tp.progress_bar

    @property
    def pct_label(self):
        return self._tp.pct_label

    @property
    def stats_label(self):
        return self._tp.stats_label

    @property
    def record_btn(self):
        return self._tp.record_btn

    @property
    def pause_btn(self):
        return self._tp.pause_btn

    @property
    def stop_btn(self):
        return self._tp.stop_btn

    @property
    def record_status_badge(self):
        return self._tp.record_status_badge

    @property
    def timer_label(self):
        return self._tp.timer_label

    @property
    def meter_level_label(self):
        return self._tp.meter_level_label

    @property
    def audio_meter(self):
        return self._tp.audio_meter

    @property
    def saved_file_label(self):
        return self._tp.saved_file_label

    @property
    def drop_zone(self):
        return self._tp.drop_zone

    @property
    def video_tab(self):
        return self._tp.video_tab

    @property
    def target_page_combo(self):
        return self._tp.target_page_combo

    @property
    def today_filter_cb(self):
        return self._tp.today_filter_cb

    @property
    def clear_filter_btn(self):
        return self._tp.clear_filter_btn

    @property
    def refresh_pages_btn(self):
        return self._tp.refresh_pages_btn

    @property
    def title_label(self):
        return self._tp.title_label

    @property
    def title_input(self):
        return self._tp.title_input

    @property
    def tags_label(self):
        return self._tp.tags_label

    @property
    def tags_input(self):
        return self._tp.tags_input

    @property
    def jargon_input(self):
        return self._tp.jargon_input

    @property
    def import_slides_btn(self):
        return self._tp.import_slides_btn

    @property
    def paste_slide_btn(self):
        return self._tp.paste_slide_btn

    @property
    def rename_speaker_btn(self):
        return self._tp.rename_speaker_btn

    @property
    def stop_audio_btn(self):
        return self._tp.stop_audio_btn

    @property
    def quick_model_combo(self):
        return self._tp.quick_model_combo

    @property
    def benchmark_btn(self):
        return self._tp.benchmark_btn

    @property
    def diarize_cb(self):
        return self._tp.diarize_cb

    @property
    def transcribe_only_btn(self):
        return self._tp.transcribe_only_btn

    @property
    def compare_models_btn(self):
        return self._tp.compare_models_btn

    @property
    def push_only_btn(self):
        return self._tp.push_only_btn

    @property
    def action_btn(self):
        return self._tp.action_btn

    @property
    def export_btn(self):
        return self._tp.export_btn

    @property
    def study_guide_btn(self):
        return self._tp.study_guide_btn

    @property
    def study_notes_text(self):
        return self._tp.study_notes_text

    # ==========================
    # Tab & Audio Source Handling
    # ==========================
    def _on_pivot_changed(self, route_key: str):
        if route_key == "recordPage":
            self._audio_source_type = "recorded"
        elif route_key == "uploadPage":
            self._audio_source_type = "file"
            selected = self.drop_zone.get_selected_path()
            if selected:
                self._current_audio_file = selected
        elif route_key == "videoPage":
            self._audio_source_type = "video"
            self.video_tab.refresh_file_list()

    def _on_video_dropped(self, video_path: str):
        fname = os.path.basename(video_path)
        self.status_label.setText(f"Video detected: '{fname}'. Queuing for audio extraction & deletion...")
        self.video_watcher.queue_video(video_path)

    def _render_transcript_segments(self, segments: List[Dict[str, Any]]):
        """Render segment list into output text preview with clickable speaker badges and audio timestamps."""
        self.output_text.clear()
        has_speakers = any("speaker" in seg for seg in segments)
        self.rename_speaker_btn.setEnabled(len(segments) > 0)

        for seg in segments:
            ts = seg.get("timestamp_str", "")
            start_s = float(seg.get("start", 0.0))
            end_s = float(seg.get("end", start_s + 2.0))
            text = seg.get("text", "").strip()
            ts_link = (
                f'<a href="play:{start_s:.2f}-{end_s:.2f}" style="text-decoration: none;">'
                f'<span style="color: #38bdf8; font-weight: bold; font-family: monospace; margin-right: 6px;">▶ {ts}</span></a>'
            )
            if has_speakers and "speaker" in seg:
                speaker = seg.get("speaker", "Speaker 1")
                spk_id = seg.get("speaker_id", 0)
                badge_html = format_speaker_badge_html(speaker, spk_id)
                html_line = (
                    f'<p style="margin: 5px 0; line-height: 1.5;">'
                    f'{badge_html}'
                    f'{ts_link}'
                    f'<span style="color: #f8fafc;">{text}</span>'
                    f'</p>'
                )
            else:
                html_line = (
                    f'<p style="margin: 4px 0;">'
                    f'{ts_link} '
                    f'<span style="color: #f8fafc;">{text}</span>'
                    f'</p>'
                )
            self.output_text.append(html_line)

        # Update stats
        unique_speakers = len(set(s.get("speaker", "Speaker 1") for s in segments)) if segments else 0
        words = sum(len(s.get("text", "").split()) for s in segments)
        if unique_speakers > 1:
            self.stats_label.setText(f"{len(segments)} segments | {words} words | 👥 {unique_speakers} speakers  •  Click ▶ [time] to play or [Speaker] to rename")
        else:
            self.stats_label.setText(f"{len(segments)} segments | {words} words  •  Click ▶ [time] to play audio")

    def _load_saved_transcript(self, file_path: str, prompt_append: bool = True) -> bool:
        """Check for and load any existing local transcript for this audio file."""
        if not file_path or not has_transcript(file_path):
            return False

        data = load_transcript(file_path)
        if not data or not isinstance(data, dict):
            return False

        segments = data.get("segments", [])
        full_text = data.get("full_text", "")
        if not segments and not full_text:
            return False

        should_append = False
        if prompt_append and len(self._transcribed_segments) > 0:
            existing_count = len(self._transcribed_segments)
            w = MessageBox(
                "Append or Replace Transcript?",
                f"You already have {existing_count} transcript segment(s) loaded in the preview.\n\n"
                "Would you like to APPEND this recording's transcript to the current one?\n\n"
                "• Click 'Yes' to APPEND to existing transcript\n"
                "• Click 'No' to REPLACE with this recording only",
                self,
            )
            w.yesButton.setText("Append")
            w.cancelButton.setText("Replace")
            if w.exec():
                should_append = True
            else:
                should_append = False

        if should_append:
            offset = 0.0
            if self._transcribed_segments:
                offset = float(self._transcribed_segments[-1].get("end", 0.0))

            appended_segs = []
            for s in segments:
                new_s = dict(s)
                new_s["start"] = round(float(s.get("start", 0.0)) + offset, 2)
                new_s["end"] = round(float(s.get("end", 0.0)) + offset, 2)
                new_s["timestamp_str"] = f"[{format_timestamp(new_s['start'])} - {format_timestamp(new_s['end'])}]"
                appended_segs.append(new_s)

            self._transcribed_segments.extend(appended_segs)
            self._transcription_text += "\n\n" + full_text
            self._model_a_segments = list(self._transcribed_segments)
            self._render_transcript_segments(self._transcribed_segments)

            self.push_only_btn.setEnabled(True)
            self.compare_models_btn.setEnabled(True)
            self.export_btn.setEnabled(True)

            self.status_label.setText(
                f"Appended {len(segments)} segments to transcript (Total: {len(self._transcribed_segments)} segments)."
            )
            return True
        else:
            self._transcription_text = full_text
            self._transcribed_segments = list(segments)
            self._model_a_segments = list(segments)
            self._model_a_name = data.get("model", self._last_model_name)

            self._render_transcript_segments(segments)

            self.push_only_btn.setEnabled(True)
            self.compare_models_btn.setEnabled(True)
            self.study_guide_btn.setEnabled(True)
            self.export_btn.setEnabled(True)

            # Load saved study notes if present
            saved_notes = data.get("study_notes", "")
            self._study_notes = saved_notes
            self.study_notes_text.setPlainText(saved_notes)

            filename = os.path.basename(file_path)
            words = data.get("word_count", sum(len(s.get("text", "").split()) for s in segments))
            speakers = data.get("speaker_count", len(set(s.get("speaker", "Speaker 1") for s in segments)))
            pushed = data.get("notion", {}).get("pushed", False)
            page_url = data.get("notion", {}).get("page_url")

            if pushed and page_url:
                self.status_label.setText(
                    f"Loaded saved transcript for '{filename}' ({words} words, {speakers} spk) — previously synced to Notion."
                )
            else:
                self.status_label.setText(
                    f"Loaded saved transcript for '{filename}' ({words} words, {speakers} spk). Ready to push or export."
                )
            return True

    def _on_video_audio_extracted(self, audio_path: str, video_name: str):
        self._current_audio_file = audio_path
        self._audio_source_type = "file"
        video_stem = os.path.splitext(video_name)[0]
        self.title_input.setText(f"Transcript - {video_stem}")
        self.recordings_list.refresh()

        loaded = self._load_saved_transcript(audio_path)
        if not loaded:
            self.status_label.setText(f"Extracted audio from '{video_name}'. Ready for transcription.")
            if config_manager.get("auto_transcribe_video", True):
                self._start_transcription(auto_push=False)

    def _on_video_extraction_failed(self, video_name: str, err_msg: str):
        self.status_label.setText(f"Failed to extract audio from '{video_name}'.")

    def _on_file_selected(self, file_path: str):
        self._current_audio_file = file_path
        filename = os.path.basename(file_path)
        base_name = os.path.splitext(filename)[0]
        self.title_input.setText(f"Transcript - {base_name}")
        loaded = self._load_saved_transcript(file_path)
        if not loaded:
            self.status_label.setText(f"Selected audio file: {filename}")

    def _on_recording_loaded(self, file_path: str):
        self._current_audio_file = file_path
        self._audio_source_type = "file"
        filename = os.path.basename(file_path)
        base_name = os.path.splitext(filename)[0]
        self.title_input.setText(f"Transcript - {base_name}")
        # Switch to transcribe page
        self.switchTo(self.transcribe_page)
        loaded = self._load_saved_transcript(file_path)
        if not loaded:
            self.status_label.setText(f"Loaded archive recording: {filename}. Ready to transcribe.")

    # ==========================
    # Audio Recording Handlers
    # ==========================
    def _toggle_recording(self):
        if not self.recorder.is_recording:
            started = self.recorder.start()
            if not started:
                return
        else:
            self._stop_recording()

    def _toggle_pause(self):
        if not self.recorder.is_recording:
            return
        if self.recorder.is_paused:
            self.recorder.resume()
        else:
            self.recorder.pause()

    def _stop_recording(self):
        saved_file = self.recorder.stop()
        if saved_file and os.path.exists(saved_file):
            self._current_audio_file = saved_file
            fname = os.path.basename(saved_file)
            self.record_status_badge.setText("RECORDING SAVED")
            self.status_label.setText(f"Recording saved: {fname}")
            self.saved_file_label.setText(f"💾 {fname}")
            self.recordings_list.refresh()

    def _open_recordings_folder(self):
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(RECORDINGS_DIR))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(RECORDINGS_DIR)])
        else:
            subprocess.run(["xdg-open", str(RECORDINGS_DIR)])

    def _on_quick_model_changed(self, model_name: str):
        config_manager.set("whisper_model", model_name)
        config_manager.save()
        self.status_label.setText(f"Whisper model changed to: '{model_name}'")

    def _on_diarize_toggled(self, checked: bool):
        config_manager.set("enable_diarization", checked)
        config_manager.save()
        status = "enabled" if checked else "disabled"
        self.status_label.setText(f"Speaker Diarization {status}.")

    def _on_audio_source_mode_changed(self, index: int):
        source = self.source_mode_combo.currentData() or "mic"
        self.recorder.set_audio_source(source)
        config_manager.set("audio_source", source)
        config_manager.save()

        if source == "mic":
            self.meter_level_label.setText("Mic Level:")
            self.status_label.setText("Audio source set to: Microphone Only")
        elif source == "system":
            self.meter_level_label.setText("System Audio Level:")
            self.status_label.setText("Audio source set to: System Audio Only (WASAPI Loopback)")
        else:
            self.meter_level_label.setText("Combined Level (Mic + System):")
            self.status_label.setText("Audio source set to: Both (Microphone + System Audio mixed)")

    def _on_record_started(self):
        self.record_btn.setText("● Recording...")
        self.record_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.source_mode_combo.setEnabled(False)
        self.record_status_badge.setText("RECORDING")

        src = self.recorder.audio_source
        if src == "system":
            self.status_label.setText("Recording system audio (speakers loopback)...")
        elif src == "both":
            self.status_label.setText("Recording both microphone and system audio...")
        else:
            self.status_label.setText("Recording microphone audio...")

    def _on_record_paused(self, is_paused: bool):
        if is_paused:
            self.pause_btn.setText("▶ Resume")
            self.record_status_badge.setText("PAUSED")
            self.status_label.setText("Recording paused.")
        else:
            self.pause_btn.setText("❚❚ Pause")
            self.record_status_badge.setText("RECORDING")
            self.status_label.setText("Recording resumed.")

    def _on_record_finished(self, file_path: str):
        self.record_btn.setText("● Start Recording")
        self.record_btn.setEnabled(True)
        self.pause_btn.setText("❚❚ Pause")
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.source_mode_combo.setEnabled(True)
        self.audio_meter.reset()

    def _on_audio_level(self, level: float):
        self.audio_meter.set_level(level)

    def _on_record_duration(self, seconds: float):
        self.timer_label.setText(format_timestamp(seconds))

    def _on_record_error(self, err_msg: str):
        self.record_status_badge.setText("ERROR")
        InfoBar.error(
            title="Audio Recording Error",
            content=f"Could not record audio: {err_msg}",
            isClosable=True,
            duration=8000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    # ==========================
    # Notion & Settings Handling
    # ==========================
    def _refresh_notion_status(self):
        """Update Notion status — no badge widget in FluentWindow, use status label."""
        if config_manager.is_notion_configured():
            self.status_label.setText("Notion: Configured ✓")
        else:
            self.status_label.setText("Notion: Unconfigured — open Settings to configure")

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.exec()

    def _open_logs(self):
        """Open the application diagnostics log file in default viewer."""
        open_log_file()

    def _open_benchmark_dialog(self):
        from ui.benchmark_dialog import BenchmarkDialog
        dlg = BenchmarkDialog(self)
        dlg.settings_applied.connect(self._on_benchmark_settings_applied)
        dlg.exec()

    def _on_benchmark_settings_applied(self, rec: Dict[str, Any]):
        model = rec.get("whisper_model", "medium")
        idx = self.quick_model_combo.findText(model)
        if idx >= 0:
            self.quick_model_combo.blockSignals(True)
            self.quick_model_combo.setCurrentIndex(idx)
            self.quick_model_combo.blockSignals(False)
        self.status_label.setText(
            f"Auto-tuned for {rec.get('device', 'cuda').upper()}: Model '{model}' (beam size {rec.get('beam_size', 1)})."
        )

    def _on_settings_saved(self):
        self._refresh_notion_status()
        current_model = config_manager.get("whisper_model", "base")
        idx = self.quick_model_combo.findText(current_model)
        if idx >= 0:
            self.quick_model_combo.setCurrentIndex(idx)

        # Update Diarization checkbox
        self.diarize_cb.blockSignals(True)
        self.diarize_cb.setChecked(config_manager.get("enable_diarization", True))
        self.diarize_cb.blockSignals(False)

        # Update Video Watcher settings
        watch_folder = config_manager.get("video_watch_folder", str(VIDEO_INBOX_DIR))
        self.video_watcher.set_watch_folder(watch_folder)
        if config_manager.get("auto_watch_video_folder", True):
            if not self.video_watcher.is_watching:
                self.video_watcher.start_watching()
        else:
            self.video_watcher.stop_watching()
        self.video_tab._update_folder_label()

        # Update Audio Recorder settings
        audio_src = config_manager.get("audio_source", "mic")
        for i in range(self.source_mode_combo.count()):
            if self.source_mode_combo.itemData(i) == audio_src:
                self.source_mode_combo.blockSignals(True)
                self.source_mode_combo.setCurrentIndex(i)
                self.source_mode_combo.blockSignals(False)
                break
        self.recorder.set_audio_source(audio_src)
        self.recorder.set_input_device(config_manager.get("input_device_id", None))
        self.recorder.set_loopback_device(config_manager.get("loopback_device_id", None))

        # Refresh Notion pages
        if config_manager.is_notion_configured():
            self._fetch_notion_pages()

    def _fetch_notion_pages(self):
        """Fetch pages from Notion database in background."""
        token = config_manager.get("notion_token")
        db_id = config_manager.get("database_id")
        if not token or not db_id:
            return

        self.refresh_pages_btn.setEnabled(False)
        self.refresh_pages_btn.setText("⏳ Loading...")

        self._pages_worker = NotionFetchPagesWorker(token, db_id, parent=self)
        self._pages_worker.pages_fetched.connect(self._on_pages_fetched)
        self._pages_worker.error_occurred.connect(self._on_pages_fetch_failed)
        self._pages_worker.start()

    def _on_pages_fetched(self, pages: List[Dict[str, Any]]):
        self.refresh_pages_btn.setEnabled(True)
        self.refresh_pages_btn.setText("🔄 Refresh")
        self._notion_pages = pages
        self._repopulate_page_dropdown()

    def _on_pages_fetch_failed(self, err_msg: str):
        self.refresh_pages_btn.setEnabled(True)
        self.refresh_pages_btn.setText("🔄 Refresh")
        self.status_label.setText(f"Notion pages query error: {err_msg}")

    def _repopulate_page_dropdown(self):
        """Re-render the items in target_page_combo based on filter state."""
        prev_selected_id = self.target_page_combo.currentData()
        self.target_page_combo.blockSignals(True)
        self.target_page_combo.clear()

        self.target_page_combo.addItem("✨ [+ Create New Page in Database]", userData=None)

        filter_today = self.today_filter_cb.isChecked()
        displayed_count = 0
        today_count = sum(1 for p in self._notion_pages if p.get("is_today"))
        total_count = len(self._notion_pages)

        for p in self._notion_pages:
            if filter_today and not p.get("is_today"):
                continue

            time_str = f" ({p.get('created_display', '')})" if p.get("created_display") else ""
            item_text = f"📄 {p['title']}{time_str}"
            self.target_page_combo.addItem(item_text, userData=p["id"])
            displayed_count += 1

        if prev_selected_id:
            for i in range(self.target_page_combo.count()):
                if self.target_page_combo.itemData(i) == prev_selected_id:
                    self.target_page_combo.setCurrentIndex(i)
                    break

        self.target_page_combo.blockSignals(False)
        self._on_target_page_changed(self.target_page_combo.currentIndex())

        filter_status = "Today only" if filter_today else "All pages"
        self.today_filter_cb.setToolTip(
            f"Showing {displayed_count} of {total_count} pages ({today_count} created today). Mode: {filter_status}."
        )

    def _on_target_page_changed(self, index: int):
        target_page_id = self.target_page_combo.currentData()
        if target_page_id:
            self.title_label.setText("Section Heading:")
            self.title_input.setEnabled(True)
            self.title_input.setPlaceholderText("e.g. Part 2 - Post-Break (or leave blank for timestamp)")
            self.title_input.setText("")
            self.tags_input.setEnabled(False)
            self.push_only_btn.setText("Push (Append to Page)")
            self.action_btn.setText("⚡ Transcribe & Append to Page")
            self.status_label.setText("Target set to existing Notion page. Ready to append.")
        else:
            self.title_label.setText("Page Title:")
            self.title_input.setEnabled(True)
            self.title_input.setPlaceholderText("Meeting / Audio Note Title")
            if not self.title_input.text() and self._current_audio_file:
                base = os.path.splitext(os.path.basename(self._current_audio_file))[0]
                self.title_input.setText(f"Transcript - {base}")
            self.tags_input.setEnabled(True)
            self.push_only_btn.setText("Push to Notion")
            self.action_btn.setText("⚡ Transcribe & Push to Notion")

    # ==========================
    # Transcription Logic
    # ==========================
    def _validate_audio_source(self) -> Optional[str]:
        """Verify an audio file is ready for transcription."""
        if self.recorder.is_recording:
            self._stop_recording()

        current_route = self._tp.pivot.currentItem().routeKey if self._tp.pivot.currentItem() else "recordPage"

        if current_route == "recordPage":
            if not self._current_audio_file or not os.path.exists(self._current_audio_file):
                InfoBar.warning(
                    title="No Audio Recorded",
                    content="Please record audio first or switch to Upload/Video tab.",
                    isClosable=True,
                    duration=5000,
                    position=InfoBarPosition.TOP,
                    parent=self,
                )
                return None
            return self._current_audio_file
        elif current_route == "uploadPage":
            path = self.drop_zone.get_selected_path()
            if not path or not os.path.exists(path):
                InfoBar.warning(
                    title="No Audio File Selected",
                    content="Please select or drop an audio file to transcribe.",
                    isClosable=True,
                    duration=5000,
                    position=InfoBarPosition.TOP,
                    parent=self,
                )
                return None
            return path
        elif current_route == "videoPage":
            if not self._current_audio_file or not os.path.exists(self._current_audio_file):
                InfoBar.warning(
                    title="No Video Audio Ready",
                    content="Please drop a video into the Video Inbox or browse a video file.",
                    isClosable=True,
                    duration=5000,
                    position=InfoBarPosition.TOP,
                    parent=self,
                )
                return None
            return self._current_audio_file
        else:
            # Fallback
            if self._current_audio_file and os.path.exists(self._current_audio_file):
                return self._current_audio_file
            InfoBar.warning(
                title="No Audio Available",
                content="Please select or record audio first.",
                isClosable=True,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            return None

    def _start_transcription(self, auto_push: bool = False):
        audio_path = self._validate_audio_source()
        if not audio_path:
            return

        self._auto_push_after_transcription = auto_push
        self._transcribed_segments.clear()
        self._transcription_text = ""
        self.output_text.clear()
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")

        self._set_ui_processing_state(True)

        model_size = config_manager.get("whisper_model", "base")
        self._last_model_name = model_size
        device = config_manager.get("device", "cpu")
        compute_type = config_manager.get("compute_type", "int8")
        enable_diarization = self.diarize_cb.isChecked()
        num_speakers = config_manager.get("num_speakers", -1)
        diarization_threshold = float(config_manager.get("diarization_threshold", 0.5))
        beam_size = int(config_manager.get("beam_size", 1))

        # Build Whisper initial_prompt and hotwords from Course Jargon input
        raw_jargon = self.jargon_input.text().strip()
        jargon_terms = [t.strip() for t in raw_jargon.split(",") if t.strip()]
        initial_prompt, hotwords = build_whisper_context_prompts(jargon_terms)

        self._transcription_worker = WhisperWorker(
            audio_path=audio_path,
            model_size=model_size,
            device=device,
            compute_type=compute_type,
            enable_diarization=enable_diarization,
            num_speakers=num_speakers,
            diarization_threshold=diarization_threshold,
            beam_size=beam_size,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
            parent=self,
        )
        self._transcription_worker.status_changed.connect(self._on_worker_status)
        self._transcription_worker.progress_percentage.connect(self._on_transcribe_progress)
        self._transcription_worker.segment_received.connect(self._on_segment_received)
        self._transcription_worker.transcription_completed.connect(self._on_transcription_completed)
        self._transcription_worker.error_occurred.connect(self._on_transcription_error)
        self._transcription_worker.start()

    def _start_transcribe_only(self):
        self._start_transcription(auto_push=False)

    def _start_transcribe_and_push(self):
        if not config_manager.is_notion_configured():
            w = MessageBox(
                "Notion Credentials Required",
                "Notion Integration Token and Database ID have not been configured.\n\n"
                "Would you like to configure them now in Settings?",
                self,
            )
            w.yesButton.setText("Open Settings")
            w.cancelButton.setText("Cancel")
            if w.exec():
                self._open_settings()
            return

        self._start_transcription(auto_push=True)

    def _on_worker_status(self, text: str):
        self.status_label.setText(text)

    def _on_transcribe_progress(self, percent: int):
        self.progress_bar.setValue(percent)
        self.pct_label.setText(f"{percent}%")

    def _on_segment_received(self, seg: Dict[str, Any]):
        self._transcribed_segments.append(seg)

        ts = seg.get("timestamp_str", "")
        start_s = float(seg.get("start", 0.0))
        end_s = float(seg.get("end", start_s + 2.0))
        text = seg.get("text", "")
        html_segment = (
            f'<p style="margin: 4px 0;">'
            f'<a href="play:{start_s:.2f}-{end_s:.2f}" style="text-decoration: none;">'
            f'<span style="color: #38bdf8; font-weight: bold; font-family: monospace;">▶ {ts}</span></a> '
            f'<span style="color: #f8fafc;">{text}</span>'
            f'</p>'
        )
        self.output_text.append(html_segment)

        words = sum(len(s.get("text", "").split()) for s in self._transcribed_segments)
        self.stats_label.setText(f"{len(self._transcribed_segments)} segments | {words} words")

    def _on_transcription_completed(self, segments: List[Dict[str, Any]], full_text: str):
        self._transcription_text = full_text
        self._transcribed_segments = list(segments)
        self._model_a_segments = list(segments)
        self._model_a_name = self._last_model_name
        self._set_ui_processing_state(False)
        self.push_only_btn.setEnabled(len(segments) > 0)
        self.compare_models_btn.setEnabled(len(segments) > 0)
        self.study_guide_btn.setEnabled(len(segments) > 0)
        self.export_btn.setEnabled(len(segments) > 0)
        self.rename_speaker_btn.setEnabled(len(segments) > 0)

        self._render_transcript_segments(segments)

        # Auto-save locally
        if self._current_audio_file:
            try:
                save_transcript(
                    audio_path=self._current_audio_file,
                    segments=segments,
                    full_text=full_text,
                    model=self._last_model_name,
                    device=config_manager.get("whisper_device", "cuda"),
                    compute_type=config_manager.get("compute_type", "float16"),
                    language=config_manager.get("whisper_language") or None,
                    study_notes=self._study_notes or self.study_notes_text.toPlainText().strip(),
                )
                self.recordings_list.refresh()
            except Exception as e:
                print(f"[Main] Failed to save local transcript: {e}")

        unique_speakers = len(set(s.get("speaker", "Speaker 1") for s in segments)) if segments else 0
        words = sum(len(s.get("text", "").split()) for s in segments)
        if unique_speakers > 1:
            self.status_label.setText(
                f"Transcription complete & saved locally ({unique_speakers} speakers, {words} words)."
            )
        else:
            self.status_label.setText(
                f"Transcription complete & saved locally ({words} words)."
            )

        if self._auto_push_after_transcription:
            self._start_notion_push()

    def _on_transcription_error(self, err_message: str):
        self._set_ui_processing_state(False)
        self.status_label.setText("Transcription failed.")
        InfoBar.error(
            title="Transcription Error",
            content=f"An error occurred during speech recognition: {err_message[:200]}",
            isClosable=True,
            duration=10000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    # ==========================
    # Model Comparison & Audition Flow
    # ==========================
    def _open_model_comparison_flow(self):
        if not self._current_audio_file or not os.path.exists(self._current_audio_file):
            InfoBar.warning(
                title="No Audio Available",
                content="Cannot compare models: No audio file or recording is loaded.",
                isClosable=True, duration=5000, position=InfoBarPosition.TOP, parent=self,
            )
            return

        if not self._transcribed_segments:
            InfoBar.warning(
                title="No Initial Transcript",
                content="Please transcribe the audio first before running comparison.",
                isClosable=True, duration=5000, position=InfoBarPosition.TOP, parent=self,
            )
            return

        all_models = ["tiny", "base", "small", "medium", "large-v3"]
        current_model = self._model_a_name or self._last_model_name
        suggested = [m for m in all_models if m != current_model]
        if not suggested:
            suggested = all_models

        default_choice = "small" if current_model == "base" else suggested[0]
        default_idx = suggested.index(default_choice) if default_choice in suggested else 0

        model_b_name, ok = QInputDialog.getItem(
            self,
            "Select Second Model for Comparison",
            f"Current transcript was generated using '{current_model}'.\n\n"
            "Select a second Whisper model to run on the same audio,\n"
            "detect word discrepancies, and audition audio snippets:",
            suggested,
            default_idx,
            False,
        )

        if ok and model_b_name:
            self._start_second_model_transcription(model_b_name)

    def _start_second_model_transcription(self, model_b_name: str):
        self._model_b_name = model_b_name
        self._model_a_name = self._last_model_name
        self._model_a_segments = list(self._transcribed_segments)

        self._set_ui_processing_state(True)
        self.status_label.setText(f"Running comparison model '{model_b_name}' on audio...")
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")

        device = config_manager.get("device", "cpu")
        compute_type = config_manager.get("compute_type", "int8")
        enable_diarization = self.diarize_cb.isChecked()
        num_speakers = config_manager.get("num_speakers", -1)
        diarization_threshold = float(config_manager.get("diarization_threshold", 0.5))
        beam_size = int(config_manager.get("beam_size", 1))

        raw_jargon = self.jargon_input.text().strip()
        jargon_terms = [t.strip() for t in raw_jargon.split(",") if t.strip()]
        initial_prompt, hotwords = build_whisper_context_prompts(jargon_terms)

        self._comparison_worker = WhisperWorker(
            audio_path=self._current_audio_file,
            model_size=model_b_name,
            device=device,
            compute_type=compute_type,
            enable_diarization=enable_diarization,
            num_speakers=num_speakers,
            diarization_threshold=diarization_threshold,
            beam_size=beam_size,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
            parent=self,
        )
        self._comparison_worker.status_changed.connect(self._on_worker_status)
        self._comparison_worker.progress_percentage.connect(self._on_transcribe_progress)
        self._comparison_worker.transcription_completed.connect(self._on_comparison_transcription_completed)
        self._comparison_worker.error_occurred.connect(self._on_comparison_transcription_error)
        self._comparison_worker.start()

    def _on_comparison_transcription_completed(self, segments_b: List[Dict[str, Any]], full_text_b: str):
        self._set_ui_processing_state(False)
        self.status_label.setText(
            f"Comparison model '{self._model_b_name}' complete. Opening comparison review..."
        )

        diff_engine = TranscriptDiffEngine(
            segments_a=self._model_a_segments,
            segments_b=segments_b,
            model_a_name=self._model_a_name,
            model_b_name=self._model_b_name,
        )

        dialog = CompareDialog(
            diff_engine=diff_engine,
            audio_path=self._current_audio_file,
            parent=self,
        )
        dialog.resolved_transcript_ready.connect(self._on_resolved_transcript_applied)
        dialog.exec()

    def _on_comparison_transcription_error(self, err_message: str):
        self._set_ui_processing_state(False)
        self.status_label.setText("Comparison transcription failed.")
        InfoBar.error(
            title="Comparison Model Error",
            content=f"Failed to transcribe with second model '{self._model_b_name}': {err_message[:200]}",
            isClosable=True, duration=10000, position=InfoBarPosition.TOP, parent=self,
        )

    def _on_resolved_transcript_applied(self, merged_segments: List[Dict[str, Any]], merged_text: str):
        self._transcribed_segments = merged_segments
        self._transcription_text = merged_text

        self.output_text.clear()
        for seg in merged_segments:
            start_ts = format_timestamp(seg.get("start", 0.0))
            end_ts = format_timestamp(seg.get("end", 0.0))
            text = seg.get("text", "").strip()
            html_segment = (
                f'<p style="margin: 4px 0;">'
                f'<span style="background-color: #064e3b; color: #34d399; font-size: 10px; font-weight: bold; padding: 1px 4px; border-radius: 3px; margin-right: 4px;">VERIFIED</span> '
                f'<span style="color: #38bdf8; font-weight: bold; font-family: monospace;">[{start_ts} - {end_ts}]</span> '
                f'<span style="color: #f8fafc;">{text}</span>'
                f'</p>'
            )
            self.output_text.append(html_segment)

        words = sum(len(s.get("text", "").split()) for s in self._transcribed_segments)
        self.stats_label.setText(f"{len(self._transcribed_segments)} segments | {words} words (Verified ✓)")
        self.push_only_btn.setEnabled(True)
        self.compare_models_btn.setEnabled(True)
        self.status_label.setText(
            f"Verified multi-model transcript applied! ({len(merged_segments)} segments). Ready to push to Notion."
        )

    # ==========================
    # Notion Push Logic
    # ==========================
    def _start_notion_push(self):
        if not config_manager.is_notion_configured():
            InfoBar.warning(
                title="Notion Not Configured",
                content="Please configure your Notion credentials in Settings first.",
                isClosable=True, duration=5000, position=InfoBarPosition.TOP, parent=self,
            )
            self._open_settings()
            return

        if not self._transcribed_segments:
            InfoBar.warning(
                title="No Transcript",
                content="There is no transcribed text to push. Please transcribe audio first.",
                isClosable=True, duration=5000, position=InfoBarPosition.TOP, parent=self,
            )
            return

        target_page_id = self.target_page_combo.currentData()
        title = self.title_input.text().strip()
        if not target_page_id and not title:
            title = f"Transcript - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        raw_tags = self.tags_input.text().strip()
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

        source_name = os.path.basename(self._current_audio_file or "Microphone Recording")
        metadata = {
            "source": source_name,
            "model": config_manager.get("whisper_model", "base"),
            "section_title": title if (target_page_id and title) else None,
        }
        if self._transcribed_segments:
            last_end = self._transcribed_segments[-1].get("end", 0)
            metadata["duration"] = format_timestamp(last_end)

        current_study_notes = self._study_notes or self.study_notes_text.toPlainText().strip()
        if current_study_notes:
            metadata["study_notes"] = current_study_notes

        self._set_ui_processing_state(True)
        if target_page_id:
            self.status_label.setText("Appending transcript to existing Notion page...")
        else:
            self.status_label.setText("Uploading transcript to Notion...")
        self.progress_bar.setValue(50)
        self.pct_label.setText("Pushing...")

        token = config_manager.get("notion_token")
        db_id = config_manager.get("database_id")

        self._notion_worker = NotionPushWorker(
            token=token,
            database_id=db_id,
            title=title,
            segments=self._transcribed_segments,
            tags=tags,
            metadata=metadata,
            target_page_id=target_page_id,
            parent=self,
        )
        self._notion_worker.status_changed.connect(self._on_worker_status)
        self._notion_worker.finished.connect(self._on_notion_push_success)
        self._notion_worker.error_occurred.connect(self._on_notion_push_error)
        self._notion_worker.start()

    def _on_notion_push_success(self, page_url: str):
        self._set_ui_processing_state(False)
        self.progress_bar.setValue(100)
        self.pct_label.setText("100%")

        target_page_id = self.target_page_combo.currentData()
        if target_page_id:
            msg = "Successfully appended transcript to Notion page!"
        else:
            msg = "Successfully published to Notion database!"

        self.status_label.setText(msg)

        # Show fluent success InfoBar with Open link
        info_bar = InfoBar.success(
            title="Notion Upload Complete",
            content=msg,
            isClosable=True,
            duration=15000,
            position=InfoBarPosition.TOP,
            parent=self,
        )
        # Add an "Open in Notion" button to the InfoBar
        open_btn = PushButton("🔗 Open in Notion")
        open_btn.clicked.connect(lambda: webbrowser.open(page_url))
        info_bar.addWidget(open_btn)

        self._fetch_notion_pages()

        if self._current_audio_file:
            try:
                update_transcript_notion_info(self._current_audio_file, page_url)
                self.recordings_list.refresh()
            except Exception as e:
                print(f"[Main] Error updating local transcript with Notion info: {e}")

        if config_manager.get("auto_open_notion", True):
            webbrowser.open(page_url)

    def _on_notion_push_error(self, error_message: str):
        self._set_ui_processing_state(False)
        self.status_label.setText("Notion upload failed.")
        InfoBar.error(
            title="Notion API Error",
            content=f"Failed to push transcript to Notion: {error_message[:200]}",
            isClosable=True,
            duration=10000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    # ==========================
    # Utilities
    # ==========================
    def _set_ui_processing_state(self, is_processing: bool):
        self.action_btn.setEnabled(not is_processing)
        self.transcribe_only_btn.setEnabled(not is_processing)
        self.compare_models_btn.setEnabled(not is_processing and len(self._transcribed_segments) > 0)
        self.study_guide_btn.setEnabled(not is_processing and len(self._transcribed_segments) > 0)
        self.push_only_btn.setEnabled(not is_processing and len(self._transcribed_segments) > 0)
        self._tp.pivot.setEnabled(not is_processing)
        self.source_mode_combo.setEnabled(not is_processing)
        self.quick_model_combo.setEnabled(not is_processing)
        self.benchmark_btn.setEnabled(not is_processing)
        self.diarize_cb.setEnabled(not is_processing)
        self.target_page_combo.setEnabled(not is_processing)
        self.today_filter_cb.setEnabled(not is_processing)
        self.clear_filter_btn.setEnabled(not is_processing)
        self.refresh_pages_btn.setEnabled(not is_processing)
        target_page_id = self.target_page_combo.currentData()
        self.title_input.setEnabled(not is_processing and not target_page_id)
        self.tags_input.setEnabled(not is_processing and not target_page_id)

    def _copy_to_clipboard(self):
        plain_text = self.output_text.toPlainText()
        if plain_text:
            QApplication.clipboard().setText(plain_text)
            InfoBar.success(
                title="Copied",
                content="Transcript copied to clipboard!",
                isClosable=True,
                duration=2000,
                position=InfoBarPosition.BOTTOM_RIGHT,
                parent=self,
            )
        else:
            InfoBar.info(
                title="Nothing to Copy",
                content="No transcript text available.",
                isClosable=True,
                duration=2000,
                position=InfoBarPosition.BOTTOM_RIGHT,
                parent=self,
            )

    def _clear_transcript(self):
        self.output_text.clear()
        self.study_notes_text.clear()
        self._study_notes = ""
        self._transcribed_segments.clear()
        self._model_a_segments.clear()
        self._transcription_text = ""
        self.stats_label.setText("0 segments | 0 words")
        self.push_only_btn.setEnabled(False)
        self.compare_models_btn.setEnabled(False)
        self.study_guide_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")
        self.status_label.setText("Transcript cleared.")

    def _export_transcript(self, format_type: str):
        """Export current transcript to user-chosen file location."""
        if not self._transcribed_segments and not self._transcription_text:
            InfoBar.info(
                title="Export Transcript",
                content="No transcript available to export.",
                isClosable=True, duration=3000, position=InfoBarPosition.TOP, parent=self,
            )
            return

        base_name = "transcript"
        if self._current_audio_file:
            base_name = Path(self._current_audio_file).stem

        filters = {
            "txt": "Plain Text (*.txt)",
            "srt": "SubRip Subtitles (*.srt)",
            "vtt": "WebVTT Subtitles (*.vtt)",
            "md": "Markdown Document (*.md)",
            "json": "JSON Data (*.json)",
        }
        file_filter = filters.get(format_type, "Plain Text (*.txt)")
        default_name = f"{base_name}.{format_type}"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            f"Export Transcript as .{format_type.upper()}",
            default_name,
            file_filter,
        )
        if not save_path:
            return

        try:
            if format_type == "txt":
                from transcript_storage import generate_formatted_txt
                content = generate_formatted_txt(
                    audio_path=self._current_audio_file or "",
                    segments=self._transcribed_segments,
                    full_text=self._transcription_text,
                    model=self._last_model_name,
                )
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(content)
            elif format_type == "srt":
                export_to_srt(self._transcribed_segments, save_path)
            elif format_type == "vtt":
                export_to_vtt(self._transcribed_segments, save_path)
            elif format_type == "md":
                meta = {
                    "title": self.title_input.text() or f"Transcript: {base_name}",
                    "model": self._last_model_name,
                }
                export_to_markdown(
                    audio_path=self._current_audio_file or "",
                    segments=self._transcribed_segments,
                    full_text=self._transcription_text,
                    output_path=save_path,
                    metadata=meta,
                )
            elif format_type == "notebooklm":
                meta = {
                    "title": self.title_input.text() or f"Lecture: {base_name}",
                    "model": self._last_model_name,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "tags": self.tags_input.text().strip() or "Education, Lecture",
                    "duration": self.timer_label.text(),
                }
                study_notes = self._study_notes or self.study_notes_text.toPlainText().strip()
                export_to_notebooklm(
                    audio_path=self._current_audio_file or "",
                    segments=self._transcribed_segments,
                    full_text=self._transcription_text,
                    output_path=save_path,
                    study_notes=study_notes,
                    metadata=meta,
                )
            elif format_type == "json":
                import json
                from transcript_storage import load_transcript
                data = None
                if self._current_audio_file:
                    data = load_transcript(self._current_audio_file)
                if not data:
                    data = {
                        "audio_file": self._current_audio_file,
                        "full_text": self._transcription_text,
                        "segments": self._transcribed_segments,
                        "model": self._last_model_name,
                        "study_notes": self._study_notes or self.study_notes_text.toPlainText().strip(),
                    }
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

            self.status_label.setText(f"Exported transcript to: {os.path.basename(save_path)}")
            InfoBar.success(
                title="Export Complete",
                content=f"Transcript saved to {os.path.basename(save_path)}",
                isClosable=True,
                duration=4000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
        except Exception as e:
            InfoBar.error(
                title="Export Failed",
                content=f"Could not export transcript: {e}",
                isClosable=True,
                duration=8000,
                position=InfoBarPosition.TOP,
                parent=self,
            )

    def _copy_for_notebooklm(self):
        """Format the current lecture transcript and study guide for NotebookLM and copy to clipboard."""
        if not self._transcription_text:
            InfoBar.info(
                title="Nothing to Copy",
                content="Please transcribe audio first.",
                isClosable=True,
                duration=2000,
                position=InfoBarPosition.BOTTOM_RIGHT,
                parent=self,
            )
            return

        from ai_service import AIService
        base_name = Path(self._current_audio_file).stem if self._current_audio_file else "Lecture"
        title = self.title_input.text() or f"Lecture: {base_name}"
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        model = self._last_model_name
        tags = self.tags_input.text().strip() or "Education, Lecture"
        duration = self.timer_label.text()
        unique_speakers = list(dict.fromkeys(s.get("speaker", "Speaker 1") for s in self._transcribed_segments if "speaker" in s))
        speaker_count = len(unique_speakers) or 1
        study_notes = self._study_notes or self.study_notes_text.toPlainText().strip()
        course_jargon = self.jargon_input.text().strip()

        formatted = AIService.format_for_notebooklm(
            title=title,
            date=date_str,
            duration=duration,
            model=model,
            speakers=speaker_count,
            tags=tags,
            summary=study_notes,
            transcript=self._transcription_text,
            slide_context=self._extracted_slide_text,
            course_jargon=course_jargon,
        )

        QApplication.clipboard().setText(formatted)
        InfoBar.success(
            title="Copied for NotebookLM",
            content="Formatted source document (with transcript & slide context) copied to clipboard!",
            isClosable=True,
            duration=4000,
            position=InfoBarPosition.BOTTOM_RIGHT,
            parent=self,
        )

    # ==========================
    # Slide OCR & Course Jargon
    # ==========================
    def _import_slides_for_ocr(self):
        """Open file dialog to import PDF, PPTX, or slide images and extract course jargon via OCR."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Lecture Slides or Screenshots for OCR Jargon Extraction",
            "",
            "Slides & Images (*.pdf *.pptx *.png *.jpg *.jpeg *.bmp *.webp *.txt *.md);;"
            "PDF & PowerPoint (*.pdf *.pptx);;"
            "Images & Screenshots (*.png *.jpg *.jpeg *.bmp *.webp);;"
            "All Files (*.*)",
        )
        if not files:
            return
        self._run_slide_extraction(files)

    def _paste_slide_from_clipboard(self):
        """Extract course jargon from a slide image or text copied to the clipboard."""
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.close()
                image.save(tmp.name, "PNG")
                self._run_slide_extraction([tmp.name])
                return
        if mime.hasUrls():
            local_files = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if local_files:
                self._run_slide_extraction(local_files)
                return
        if mime.hasText() and mime.text().strip():
            from slide_extractor import distill_course_jargon
            terms = distill_course_jargon(mime.text())
            if terms:
                self._merge_jargon_terms(terms)
                InfoBar.success(
                    title="Extracted Jargon from Clipboard Text",
                    content=f"Added {len(terms)} domain terms to Course Jargon.",
                    isClosable=True, duration=3500, position=InfoBarPosition.TOP, parent=self,
                )
                return
        InfoBar.info(
            title="No Slide Image or Text in Clipboard",
            content="Take a screenshot of a slide (Win+Shift+S) or copy slide text first, then click Paste Slide.",
            isClosable=True, duration=4000, position=InfoBarPosition.TOP, parent=self,
        )

    def _run_slide_extraction(self, file_paths: List[str]):
        self.import_slides_btn.setEnabled(False)
        self.import_slides_btn.setText("⏳ Extracting OCR...")
        self.paste_slide_btn.setEnabled(False)
        self.status_label.setText(f"Extracting text & OCR jargon from {len(file_paths)} file(s)...")

        self._slide_worker = SlideExtractorWorker(file_paths, parent=self)
        self._slide_worker.status_changed.connect(self._on_worker_status)
        self._slide_worker.extraction_completed.connect(self._on_slide_extraction_completed)
        self._slide_worker.error_occurred.connect(self._on_slide_extraction_error)
        self._slide_worker.start()

    def _merge_jargon_terms(self, new_terms: List[str]):
        existing_raw = self.jargon_input.text().strip()
        existing_list = [t.strip() for t in existing_raw.split(",") if t.strip()]
        seen = {t.lower() for t in existing_list}
        for term in new_terms:
            if term.lower() not in seen:
                existing_list.append(term)
                seen.add(term.lower())
        self.jargon_input.setText(", ".join(existing_list[:85]))

    def _on_slide_extraction_completed(self, jargon_terms: List[str], raw_text: str, file_names: List[str]):
        self.import_slides_btn.setEnabled(True)
        self.import_slides_btn.setText("📊 Import Slides / OCR...")
        self.paste_slide_btn.setEnabled(True)

        if raw_text:
            if self._extracted_slide_text:
                self._extracted_slide_text += "\n\n" + raw_text
            else:
                self._extracted_slide_text = raw_text

        if jargon_terms:
            self._merge_jargon_terms(jargon_terms)
            files_label = ", ".join(file_names[:2]) + (f" +{len(file_names)-2} more" if len(file_names) > 2 else "")
            self.status_label.setText(
                f"Extracted {len(jargon_terms)} course jargon terms from {files_label} for Whisper accuracy."
            )
            InfoBar.success(
                title="Slide OCR & Jargon Ready",
                content=f"Extracted {len(jargon_terms)} technical terms from {files_label}. Whisper will now prioritize these spellings!",
                isClosable=True, duration=5000, position=InfoBarPosition.TOP, parent=self,
            )
        else:
            self.status_label.setText("No domain jargon detected in the selected slide(s).")
            InfoBar.warning(
                title="No Jargon Found",
                content="Could not detect technical text in the selected file(s).",
                isClosable=True, duration=4000, position=InfoBarPosition.TOP, parent=self,
            )

    def _on_slide_extraction_error(self, err_msg: str):
        self.import_slides_btn.setEnabled(True)
        self.import_slides_btn.setText("📊 Import Slides / OCR...")
        self.paste_slide_btn.setEnabled(True)
        self.status_label.setText("Slide OCR extraction failed.")
        InfoBar.error(
            title="Slide Extraction Error",
            content=f"Could not extract text from slides: {err_msg[:200]}",
            isClosable=True, duration=6000, position=InfoBarPosition.TOP, parent=self,
        )

    # ==========================
    # Interactive Audio Sync & Speaker Renaming
    # ==========================
    def _on_transcript_anchor_clicked(self, anchor: str):
        """Handle clicks on timestamp links (`play:start-end`) or speaker badges (`speaker:Name`)."""
        if anchor.startswith("play:"):
            if not self._current_audio_file or not os.path.exists(self._current_audio_file):
                InfoBar.warning(
                    title="Audio File Unavailable",
                    content="No local audio file is associated with this transcript.",
                    isClosable=True, duration=3000, position=InfoBarPosition.TOP, parent=self,
                )
                return
            try:
                span = anchor.split("play:", 1)[1]
                start_str, end_str = span.split("-", 1)
                start_s = float(start_str)
                end_s = float(end_str)
                self.audio_player.play_snippet(
                    self._current_audio_file,
                    start_sec=start_s,
                    end_sec=end_s,
                    pad_before=0.3,
                    pad_after=0.5,
                )
            except Exception as e:
                self.status_label.setText(f"Could not play segment: {e}")
        elif anchor.startswith("speaker:"):
            old_speaker = anchor.split("speaker:", 1)[1]
            self._rename_specific_speaker(old_speaker)

    def _on_snippet_playback_started(self, start_sec: float, end_sec: float):
        self.stop_audio_btn.setVisible(True)
        self.status_label.setText(
            f"🔊 Playing audio segment [{format_timestamp(start_sec)} - {format_timestamp(end_sec)}]..."
        )

    def _on_snippet_playback_finished(self):
        self.stop_audio_btn.setVisible(False)
        self.status_label.setText("Audio playback finished.")

    def _on_snippet_playback_error(self, err_msg: str):
        self.stop_audio_btn.setVisible(False)
        self.status_label.setText(f"Audio playback error: {err_msg}")

    def _prompt_rename_speaker(self):
        """Let user pick a speaker from current transcript and rename them across all segments."""
        if not self._transcribed_segments:
            return
        speakers = list(dict.fromkeys(
            s.get("speaker", "Speaker 1") for s in self._transcribed_segments
        ))
        if not speakers:
            speakers = ["Speaker 1"]

        if len(speakers) == 1:
            self._rename_specific_speaker(speakers[0])
        else:
            chosen, ok = QInputDialog.getItem(
                self,
                "Select Speaker to Rename",
                "Choose which speaker label you want to rename across the transcript:",
                speakers,
                0,
                False,
            )
            if ok and chosen:
                self._rename_specific_speaker(chosen)

    def _rename_specific_speaker(self, old_name: str):
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Speaker",
            f"Enter new name for '{old_name}' (e.g., Prof. Smith, TA, Student):",
            text=old_name,
        )
        new_name = (new_name or "").strip()
        if not ok or not new_name or new_name == old_name:
            return

        count = 0
        lines = []
        for seg in self._transcribed_segments:
            curr_spk = seg.get("speaker", "Speaker 1")
            if curr_spk == old_name:
                seg["speaker"] = new_name
                count += 1
            elif "speaker" not in seg and old_name == "Speaker 1":
                seg["speaker"] = new_name
                seg["speaker_id"] = 0
                count += 1
            spk_prefix = f"[{seg.get('speaker')}] " if "speaker" in seg else ""
            lines.append(f"{spk_prefix}{seg.get('timestamp_str', '')} {seg.get('text', '')}")

        self._transcription_text = "\n".join(lines)
        self._render_transcript_segments(self._transcribed_segments)

        # Persist updated speaker names to local transcript storage
        if self._current_audio_file:
            try:
                save_transcript(
                    audio_path=self._current_audio_file,
                    segments=self._transcribed_segments,
                    full_text=self._transcription_text,
                    model=self._last_model_name,
                    device=config_manager.get("whisper_device", "cuda"),
                    compute_type=config_manager.get("compute_type", "float16"),
                    language=config_manager.get("whisper_language") or None,
                    study_notes=self._study_notes or self.study_notes_text.toPlainText().strip(),
                )
                self.recordings_list.refresh()
            except Exception:
                pass

        self.status_label.setText(f"Renamed '{old_name}' → '{new_name}' across {count} segment(s).")
        InfoBar.success(
            title="Speaker Renamed",
            content=f"Updated '{old_name}' to '{new_name}' across {count} segment(s).",
            isClosable=True, duration=3000, position=InfoBarPosition.TOP, parent=self,
        )

    def _generate_study_guide(self):
        """Invoke AIStudyNotesWorker using Gemini or Ollama to generate study guide."""
        if not self._transcription_text:
            InfoBar.warning(
                title="No Transcript",
                content="Please transcribe an audio or video lecture first.",
                isClosable=True,
                duration=4000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            return

        provider = config_manager.get("ai_provider", "gemini")
        if provider == "gemini" and not config_manager.get("gemini_api_key"):
            w = MessageBox(
                "Gemini API Key Required",
                "Google Gemini API key has not been configured.\n\n"
                "Would you like to open Settings to add your free Gemini API key?",
                self,
            )
            w.yesButton.setText("Open Settings")
            w.cancelButton.setText("Cancel")
            if w.exec():
                self._open_settings()
            return

        self._set_ui_processing_state(True)
        self.status_label.setText("Synthesizing educational study guide with AI...")
        self.progress_bar.setValue(50)
        self.pct_label.setText("AI Thinking...")

        self._ai_worker = AIStudyNotesWorker(
            transcript_text=self._transcription_text,
            parent=self,
        )
        self._ai_worker.status_changed.connect(lambda msg: self.status_label.setText(msg))
        self._ai_worker.notes_ready.connect(self._on_study_notes_ready)
        self._ai_worker.error_occurred.connect(self._on_study_notes_error)
        self._ai_worker.start()

    def _on_study_notes_ready(self, notes_markdown: str):
        self._set_ui_processing_state(False)
        self.progress_bar.setValue(100)
        self.pct_label.setText("100%")
        self._study_notes = notes_markdown
        self.study_notes_text.setPlainText(notes_markdown)

        # Automatically switch preview to Study Guide view
        self._tp.view_study_btn.click()

        self.status_label.setText("AI Study Guide generated successfully! Ready to push or export.")
        InfoBar.success(
            title="Study Guide Ready",
            content="Generated lecture summary, key definitions, and active recall flashcards!",
            isClosable=True,
            duration=5000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

        # Update local storage with new study notes
        if self._current_audio_file:
            try:
                save_transcript(
                    audio_path=self._current_audio_file,
                    segments=self._transcribed_segments,
                    full_text=self._transcription_text,
                    model=self._last_model_name,
                    device=config_manager.get("whisper_device", "cuda"),
                    compute_type=config_manager.get("compute_type", "float16"),
                    language=config_manager.get("whisper_language") or None,
                    study_notes=notes_markdown,
                )
            except Exception as e:
                print(f"[Main] Error updating saved transcript with study notes: {e}")

    def _on_study_notes_error(self, err_msg: str):
        self._set_ui_processing_state(False)
        self.status_label.setText("AI Study Guide generation failed.")
        InfoBar.error(
            title="AI Generation Error",
            content=f"Failed to generate study guide: {err_msg[:250]}",
            isClosable=True,
            duration=10000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    def _open_transcripts_folder(self):
        """Open ~/.notion_transcribe/transcripts/ in file explorer."""
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(TRANSCRIPTS_DIR))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(TRANSCRIPTS_DIR)])
        else:
            subprocess.run(["xdg-open", str(TRANSCRIPTS_DIR)])

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_icon_path") and self._icon_path:
            apply_windows_taskbar_icon(self, self._icon_path)

    def closeEvent(self, event):
        if hasattr(self, "audio_player") and self.audio_player:
            self.audio_player.stop()
        if hasattr(self, "video_watcher") and self.video_watcher:
            self.video_watcher.stop_watching()
        if hasattr(self, "_pages_worker") and self._pages_worker:
            try:
                self._pages_worker.quit()
                self._pages_worker.wait(1000)
            except Exception:
                pass
        if hasattr(self, "recorder") and self.recorder and self.recorder.is_recording:
            try:
                self.recorder.stop()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    # Initialize rotating file logging, safe stream redirection, and crash diagnostics
    setup_logging()

    # Set Windows AppUserModelID so taskbar displays the custom icon
    if sys.platform == "win32":
        try:
            import ctypes
            myappid = "transcribenotion.app.1.2.0"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

    # Prevent modal dialog window freezing on some Windows installations
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)

    app = QApplication(sys.argv)

    # Set Fluent Design dark theme with custom accent color
    setTheme(Theme.DARK)
    setThemeColor("#0284c7")  # Sky blue accent

    icon_path = get_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    # No custom QSS — fluent theme handles everything
    window = MainWindow()
    window.show()
    if icon_path:
        apply_windows_taskbar_icon(window, icon_path)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
