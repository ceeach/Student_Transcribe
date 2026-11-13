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
from PyQt6.QtGui import QFont, QIcon, QColor, QTextCursor
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
    TransparentToolButton,
    setFont,
    isDarkTheme,
    setTheme,
    setThemeColor,
    Theme,
)

from audio_player import AudioPlayer
from audio_recorder import AudioRecorder, RECORDINGS_DIR
from config_manager import config_manager, VIDEO_INBOX_DIR
from slide_extractor import SlideExtractorWorker, build_whisper_context_prompts
from transcriber import WhisperWorker, format_timestamp
from transcript_diff import TranscriptDiffEngine
from transcript_storage import (
    TRANSCRIPTS_DIR,
    delete_transcript,
    export_to_markdown,
    export_to_srt,
    export_to_vtt,
    has_transcript,
    load_transcript,
    save_transcript,
)
from ai_worker import AIStudyNotesWorker
from ui.audio_meter import AudioMeter
from ui.compare_dialog import CompareDialog
from ui.drop_zone import DropZoneWidget
from ui.recordings_list import RecordingsListWidget
from ui.settings_dialog import SettingsDialog
from ui.slide_viewer import SlideCompanionWidget
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
    """Main transcription page with Pivot sub-tabs for Record/Upload/Video/Slides."""

    def __init__(
        self,
        recorder: AudioRecorder,
        video_watcher: VideoFolderWatcher,
        audio_player: Optional[AudioPlayer] = None,
        parent=None,
    ):
        super().__init__(parent=parent)
        self.setObjectName("transcribeInterface")
        self.recorder = recorder
        self.video_watcher = video_watcher
        self.audio_player = audio_player
        from subject_manager import subject_manager
        self.subject_manager = subject_manager
        self._current_record_elapsed: float = 0.0
        self._init_ui()

    def _init_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(24, 20, 24, 20)
        self.main_layout.setSpacing(16)

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

        # Tab 4: Live Class Slides & Annotations Companion
        self.slide_companion = SlideCompanionWidget(
            time_provider=lambda: self._current_record_elapsed,
            parent=self,
        )
        self.slide_companion.setObjectName("slidesPage")

        # Tab 5: Academic Course & Subjects Hub (Dedicated Subjects Page)
        from ui.subjects_hub import SubjectsHubWidget
        self.subjects_hub = SubjectsHubWidget(parent=self)
        self.subjects_hub.setObjectName("subjectsPage")

        # Tab 6: Recordings & Transcripts History (Dedicated History Page)
        from ui.recordings_list import RecordingsListWidget
        self.recordings_list = RecordingsListWidget(parent=self)
        self.recordings_list.setObjectName("historyPage")

        # Add to stack and pivot (compact labels for sleek titlebar integration)
        self._add_pivot_page(self.record_page, "recordPage", "Record", icon=FIF.MICROPHONE)
        self._add_pivot_page(self.upload_page, "uploadPage", "Upload", icon=FIF.FOLDER)
        self._add_pivot_page(self.video_tab, "videoPage", "Videos", icon=FIF.VIDEO)
        self._add_pivot_page(self.slide_companion, "slidesPage", "Slides", icon=FIF.ALBUM)
        self._add_pivot_page(self.subjects_hub, "subjectsPage", "Courses", icon=FIF.EDUCATION)
        self._add_pivot_page(self.recordings_list, "historyPage", "History", icon=FIF.HISTORY)

        self.pivot.setCurrentItem("recordPage")
        self.stacked_widget.setCurrentWidget(self.record_page)

        # self.pivot is embedded directly into MainWindow TitleBar centered
        self.main_layout.addWidget(self.stacked_widget)

        # ---- Notion Metadata Card ----
        self._create_metadata_card(self.main_layout)

        # ---- Transcript Preview Card ----
        self._create_transcript_card(self.main_layout)

        # ---- Progress Section ----
        self._create_progress_section(self.main_layout)

        # ---- Action Bar ----
        self._create_action_bar(self.main_layout)

    def _add_pivot_page(self, widget, route_key, text, icon=None):
        self.stacked_widget.addWidget(widget)
        item = self.pivot.addItem(
            routeKey=route_key,
            text=text,
            onClick=lambda: self._activate_pivot_subpage(widget, route_key),
            icon=icon,
        )
        if item:
            setFont(item, 13)

    def _activate_pivot_subpage(self, widget: QWidget, route_key: str):
        self.stacked_widget.setCurrentWidget(widget)
        self.set_full_screen_slides_mode(route_key in ("slidesPage", "videoPage", "subjectsPage", "historyPage"))

    def set_full_screen_slides_mode(self, enabled: bool):
        """Expand Class Slides or Video Inbox to take 100% of the UI screen by hiding bottom cards."""
        show_bottom = not enabled
        if hasattr(self, "meta_card"):
            self.meta_card.setVisible(show_bottom)
        if hasattr(self, "output_card"):
            self.output_card.setVisible(show_bottom)
        if hasattr(self, "progress_container"):
            self.progress_container.setVisible(show_bottom)
        if hasattr(self, "action_bar_container"):
            self.action_bar_container.setVisible(show_bottom)

        if enabled:
            self.main_layout.setContentsMargins(8, 4, 8, 6)
            self.main_layout.setSpacing(4)
            self.main_layout.setStretchFactor(self.stacked_widget, 1)
            if hasattr(self, "output_card"):
                self.main_layout.setStretchFactor(self.output_card, 0)
        else:
            self.main_layout.setContentsMargins(20, 16, 20, 16)
            self.main_layout.setSpacing(12)
            self.main_layout.setStretchFactor(self.stacked_widget, 0)
            if hasattr(self, "output_card"):
                self.main_layout.setStretchFactor(self.output_card, 1)

    def _create_record_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Audio source and Language selector
        source_row = QHBoxLayout()
        source_row.setSpacing(12)
        source_label = CaptionLabel("Audio Source:", self)
        source_row.addWidget(source_label)

        self.source_mode_combo = ComboBox(self)
        self.source_mode_combo.addItem("Microphone Only", icon=FIF.MICROPHONE, userData="mic")
        self.source_mode_combo.addItem("System Audio Only (Speakers Loopback)", icon=FIF.SPEAKERS, userData="system")
        self.source_mode_combo.addItem("Both (Microphone + System Audio)", icon=FIF.MIX_VOLUMES, userData="both")
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
        source_row.addWidget(self.source_mode_combo, 3)

        lang_label = CaptionLabel("Language:", self)
        source_row.addWidget(lang_label)

        self.language_combo = ComboBox(self)
        self.language_combo.addItem("🌐 English (Forced)", userData="en")
        self.language_combo.addItem("🌐 Auto-Detect (Multilingual)", userData="auto")
        self.language_combo.addItem("🌐 German (Deutsch)", userData="de")
        self.language_combo.addItem("🌐 French (Français)", userData="fr")
        self.language_combo.addItem("🌐 Spanish (Español)", userData="es")
        self.language_combo.addItem("🌐 Italian (Italiano)", userData="it")
        self.language_combo.addItem("🌐 Dutch (Nederlands)", userData="nl")
        self.language_combo.addItem("🌐 Portuguese (Português)", userData="pt")
        self.language_combo.addItem("🌐 Chinese (Mandarin)", userData="zh")
        self.language_combo.addItem("🌐 Japanese (日本語)", userData="ja")
        self.language_combo.addItem("🌐 Arabic (العربية)", userData="ar")
        self.language_combo.addItem("🌐 Russian (Русский)", userData="ru")
        self.language_combo.addItem("🌐 Korean (한국어)", userData="ko")
        self.language_combo.setToolTip(
            "Transcription Language:\n"
            "- English (Forced): Strictly locks transcription to English, preventing accidental language hopping during accented speech.\n"
            "- Auto-Detect: Detects language dynamically per speech segment.\n"
            "- Other: Locks transcription strictly to the chosen language."
        )
        saved_lang = config_manager.get("whisper_language", "en") or "en"
        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == saved_lang:
                self.language_combo.setCurrentIndex(i)
                break
        source_row.addWidget(self.language_combo, 2)
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

        self.record_btn = PrimaryPushButton("Start Recording", self)
        self.record_btn.setIcon(FIF.MICROPHONE)
        self.record_btn.setFixedSize(180, 44)

        self.pause_btn = PushButton("Pause", self)
        self.pause_btn.setIcon(FIF.PAUSE)
        self.pause_btn.setFixedSize(130, 44)
        self.pause_btn.setEnabled(False)

        self.stop_btn = PushButton("Stop", self)
        self.stop_btn.setIcon(FIF.CLOSE)
        self.stop_btn.setFixedSize(130, 44)
        self.stop_btn.setEnabled(False)

        btn_row.addWidget(self.record_btn)
        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row)

        # Saved file info
        saved_row = QHBoxLayout()
        self.saved_file_label = CaptionLabel("", self)
        self.open_folder_btn = TransparentPushButton("Open Recordings Folder", self)
        self.open_folder_btn.setIcon(FIF.FOLDER)
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
        self.meta_card = CardWidget(self)
        meta_layout = QVBoxLayout(self.meta_card)
        meta_layout.setContentsMargins(16, 14, 16, 14)
        meta_layout.setSpacing(10)

        meta_header = StrongBodyLabel("Session Details", self)
        meta_layout.addWidget(meta_header)

        # Title row
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.title_label = BodyLabel("Session Title:", self)
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

        # Academic Subject, Session Type & Modules Covered row
        row_academic = QHBoxLayout()
        row_academic.setSpacing(8)

        self.subject_label = BodyLabel("Subject:", self)
        row_academic.addWidget(self.subject_label)

        self.subject_combo = ComboBox(self)
        self.subject_combo.setMinimumWidth(160)
        self.subject_combo.currentIndexChanged.connect(self._on_subject_changed)
        row_academic.addWidget(self.subject_combo)

        self.session_type_label = BodyLabel("Type:", self)
        row_academic.addWidget(self.session_type_label)

        self.session_type_combo = ComboBox(self)
        self.session_type_combo.addItems(["📖 Theory Lecture", "✏️ Exercise / Problem Set", "🔬 Lab / Workshop"])
        row_academic.addWidget(self.session_type_combo)

        self.modules_label = BodyLabel("Modules:", self)
        row_academic.addWidget(self.modules_label)

        self.modules_input = LineEdit(self)
        self.modules_input.setPlaceholderText("e.g. Module 1: Intro, Module 2: Systems")
        self.modules_input.setClearButtonEnabled(True)
        row_academic.addWidget(self.modules_input, 1)

        self._populate_subject_dropdown()

        self.add_subject_btn = TransparentPushButton("+ Subject", self)
        self.add_subject_btn.setIcon(FIF.ADD)
        self.add_subject_btn.setToolTip("Add a new academic course or subject")
        self.add_subject_btn.clicked.connect(self._prompt_add_subject)
        row_academic.addWidget(self.add_subject_btn)

        self.manage_subject_btn = TransparentPushButton("Manage", self)
        self.manage_subject_btn.setIcon(FIF.SETTING)
        self.manage_subject_btn.setToolTip("Manage, edit, rename, or delete academic subjects and course modules")
        self.manage_subject_btn.clicked.connect(self._open_subject_manager)
        row_academic.addWidget(self.manage_subject_btn)

        meta_layout.addLayout(row_academic)

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

        self.import_slides_btn = PushButton("Import Slides / OCR...", self)
        self.import_slides_btn.setIcon(FIF.ALBUM)
        self.import_slides_btn.setToolTip(
            "Extract course jargon and technical vocabulary from PDF slides, PowerPoint (.pptx),\n"
            "or slide screenshots (.png, .jpg) using hybrid text + Windows OCR."
        )
        row4.addWidget(self.import_slides_btn)

        self.paste_slide_btn = TransparentPushButton("Paste Slide", self)
        self.paste_slide_btn.setIcon(FIF.PASTE)
        self.paste_slide_btn.setToolTip("Run OCR on a slide screenshot copied to your clipboard")
        row4.addWidget(self.paste_slide_btn)

        meta_layout.addLayout(row4)

        parent_layout.addWidget(self.meta_card)

    def _create_transcript_card(self, parent_layout):
        self.output_card = CardWidget(self)
        output_layout = QVBoxLayout(self.output_card)
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

        # View Switcher (Transcript vs Study Guide vs Ask Lecture AI)
        self.view_transcript_btn = PushButton("Transcript", self)
        self.view_transcript_btn.setIcon(FIF.DOCUMENT)
        self.view_transcript_btn.setCheckable(True)
        self.view_transcript_btn.setChecked(True)
        self.view_study_btn = PushButton("AI Study Guide", self)
        self.view_study_btn.setIcon(FIF.EDUCATION)
        self.view_study_btn.setCheckable(True)
        self.view_study_btn.setChecked(False)
        self.view_chat_btn = PushButton("💬 Ask Lecture", self)
        self.view_chat_btn.setIcon(FIF.CHAT)
        self.view_chat_btn.setCheckable(True)
        self.view_chat_btn.setChecked(False)
        self.view_chat_btn.setToolTip("Ask questions and get interactive explanations grounded in this lecture")

        def switch_to_transcript():
            self.view_transcript_btn.setChecked(True)
            self.view_study_btn.setChecked(False)
            self.view_chat_btn.setChecked(False)
            self.preview_stack.setCurrentIndex(0)

        def switch_to_study():
            self.view_transcript_btn.setChecked(False)
            self.view_study_btn.setChecked(True)
            self.view_chat_btn.setChecked(False)
            self.preview_stack.setCurrentIndex(1)

        def switch_to_chat():
            self.view_transcript_btn.setChecked(False)
            self.view_study_btn.setChecked(False)
            self.view_chat_btn.setChecked(True)
            self.preview_stack.setCurrentIndex(2)

        self.switch_to_transcript = switch_to_transcript
        self.switch_to_study = switch_to_study
        self.switch_to_chat = switch_to_chat

        self.view_transcript_btn.clicked.connect(switch_to_transcript)
        self.view_study_btn.clicked.connect(switch_to_study)
        self.view_chat_btn.clicked.connect(switch_to_chat)

        output_header.addWidget(self.view_transcript_btn)
        output_header.addWidget(self.view_study_btn)
        output_header.addWidget(self.view_chat_btn)

        self.stop_audio_btn = PushButton("Stop Audio", self)
        self.stop_audio_btn.setIcon(FIF.PAUSE)
        self.stop_audio_btn.setVisible(False)
        self.stop_audio_btn.setToolTip("Stop currently playing audio snippet")
        output_header.addWidget(self.stop_audio_btn)

        self.rename_speaker_btn = PushButton("Rename Speaker", self)
        self.rename_speaker_btn.setIcon(FIF.PEOPLE)
        self.rename_speaker_btn.setEnabled(False)
        self.rename_speaker_btn.setToolTip("Rename a diarized speaker (e.g. 'Speaker 1' -> 'Prof. Smith') across the entire transcript")
        output_header.addWidget(self.rename_speaker_btn)

        self.copy_btn = PushButton("Copy", self)
        self.copy_btn.setIcon(FIF.COPY)
        self.copy_btn.setFixedWidth(88)
        output_header.addWidget(self.copy_btn)

        self.export_btn = PushButton("Export", self)
        self.export_btn.setIcon(FIF.SAVE_AS)
        self.export_btn.setFixedWidth(96)
        self.export_btn.setEnabled(False)
        self.export_menu = RoundMenu(parent=self)

        from PyQt6.QtGui import QAction
        act_txt = QAction(FIF.DOCUMENT.icon(), "Export Plain Text (.txt)...", self)
        act_srt = QAction(FIF.DOCUMENT.icon(), "Export Subtitles (.srt)...", self)
        act_vtt = QAction(FIF.DOCUMENT.icon(), "Export WebVTT (.vtt)...", self)
        act_md = QAction(FIF.DOCUMENT.icon(), "Export Markdown (.md)...", self)
        act_json = QAction(FIF.DOCUMENT.icon(), "Export JSON (.json)...", self)
        act_folder = QAction(FIF.FOLDER.icon(), "Open Transcripts Folder", self)

        self.export_menu.addAction(act_txt)
        self.export_menu.addAction(act_srt)
        self.export_menu.addAction(act_vtt)
        self.export_menu.addAction(act_md)
        self.export_menu.addAction(act_json)
        self.export_menu.addSeparator()
        self.export_menu.addAction(act_folder)

        # Store actions for connecting in MainWindow
        self._export_actions = {
            "txt": act_txt, "srt": act_srt, "vtt": act_vtt,
            "md": act_md, "json": act_json,
            "folder": act_folder,
        }

        self.export_btn.clicked.connect(
            lambda: self.export_menu.exec(self.export_btn.mapToGlobal(
                self.export_btn.rect().bottomLeft()))
        )
        output_header.addWidget(self.export_btn)

        self.clear_btn = PushButton("Clear", self)
        self.clear_btn.setIcon(FIF.DELETE)
        self.clear_btn.setFixedWidth(80)
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

        # Page 2: In-App Lecture Chat (Phase 2)
        from ui.lecture_chat import LectureChatWidget
        self.lecture_chat_widget = LectureChatWidget(self)
        self.preview_stack.addWidget(self.lecture_chat_widget)

        output_layout.addWidget(self.preview_stack)

        if self.audio_player:
            from ui.audio_player_bar import AudioPlayerBar
            self.audio_player_bar = AudioPlayerBar(self.audio_player, self)
            output_layout.addWidget(self.audio_player_bar)

        parent_layout.addWidget(self.output_card, 1)

    def _populate_subject_dropdown(self, select_name: Optional[str] = None):
        curr_selected = select_name or self.subject_combo.currentData() or self.subject_combo.currentText()
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        subjects = self.subject_manager.get_all_subjects()
        for subj in subjects:
            code_prefix = f"[{subj.code}] " if subj.code else ""
            self.subject_combo.addItem(f"{code_prefix}{subj.name}", userData=subj.name)
        if curr_selected:
            idx = self.subject_combo.findData(curr_selected)
            if idx >= 0:
                self.subject_combo.setCurrentIndex(idx)
            else:
                idx = self.subject_combo.findText(curr_selected)
                if idx >= 0:
                    self.subject_combo.setCurrentIndex(idx)
        self.subject_combo.blockSignals(False)
        self._on_subject_changed(self.subject_combo.currentIndex())

    def _on_subject_changed(self, index: int):
        subj_name = self.subject_combo.currentData()
        if not subj_name:
            return
        subj = self.subject_manager.get_subject(subj_name)
        if subj and subj.modules and hasattr(self, "modules_input") and not self.modules_input.text().strip():
            self.modules_input.setPlaceholderText(f"Available: {', '.join(subj.modules[:3])}...")

    def _prompt_add_subject(self):
        from ui.subject_dialog import SubjectManagementDialog
        dlg = SubjectManagementDialog(self.window())
        dlg._start_new_subject()
        dlg.subjects_changed.connect(lambda: self._populate_subject_dropdown(getattr(dlg, "last_saved_subject", None)))
        dlg.exec()
        target = getattr(dlg, "last_saved_subject", None)
        self._populate_subject_dropdown(select_name=target)
        if hasattr(self.window(), "recordings_list"):
            self.window().recordings_list.refresh()

    def _open_subject_manager(self):
        from ui.subject_dialog import SubjectManagementDialog
        dlg = SubjectManagementDialog(self.window())
        curr_subj = self.subject_combo.currentData()
        if curr_subj:
            dlg._populate_list(select_name=curr_subj)
        dlg.subjects_changed.connect(lambda: self._populate_subject_dropdown(getattr(dlg, "last_saved_subject", None)))
        dlg.exec()
        target = getattr(dlg, "last_saved_subject", None)
        self._populate_subject_dropdown(select_name=target)
        if hasattr(self.window(), "recordings_list"):
            self.window().recordings_list.refresh()

    def _create_progress_section(self, parent_layout):
        self.progress_container = QWidget(self)
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
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

        parent_layout.addWidget(self.progress_container)

    def _create_action_bar(self, parent_layout):
        self.action_bar_container = QWidget(self)
        action_layout = QHBoxLayout(self.action_bar_container)
        action_layout.setContentsMargins(0, 0, 0, 0)
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
        self.benchmark_btn = PushButton("Auto-Tune", self)
        self.benchmark_btn.setIcon(FIF.SPEED_HIGH)
        self.benchmark_btn.setToolTip("Benchmark hardware & auto-select optimal model and speed settings")
        action_layout.addWidget(self.benchmark_btn)

        # Diarization toggle
        self.diarize_cb = CheckBox("Diarize Speakers", self)
        self.diarize_cb.setChecked(config_manager.get("enable_diarization", True))
        self.diarize_cb.setToolTip("Identify and label distinct speakers")
        action_layout.addWidget(self.diarize_cb)

        self.transcribe_only_btn = PushButton("Transcribe Audio", self)
        self.transcribe_only_btn.setIcon(FIF.PLAY)
        action_layout.addWidget(self.transcribe_only_btn)

        self.compare_models_btn = PushButton("Compare Models...", self)
        self.compare_models_btn.setIcon(FIF.CONSTRACT)
        self.compare_models_btn.setEnabled(False)
        self.compare_models_btn.setToolTip("Compare current transcript with a second model")
        action_layout.addWidget(self.compare_models_btn)

        self.study_guide_btn = PushButton("Study Guide", self)
        self.study_guide_btn.setIcon(FIF.EDUCATION)
        self.study_guide_btn.setEnabled(False)
        self.study_guide_btn.setToolTip("Generate comprehensive lecture notes, key concepts, and active recall flashcards using Gemini or Ollama")
        action_layout.addWidget(self.study_guide_btn)

        self.action_btn = PrimaryPushButton("✨ Transcribe & Generate Notes", self)
        self.action_btn.setIcon(FIF.DOCUMENT)
        self.action_btn.setToolTip("Transcribe audio and automatically generate comprehensive lecture notes and flashcards")
        action_layout.addWidget(self.action_btn, 1)

        parent_layout.addWidget(self.action_bar_container)


# ============================================================
# Main Window - FluentWindow with sidebar navigation
# ============================================================
class MainWindow(FluentWindow):
    """Main Application Window for Student Transcribe with WinUI3 Fluent Design."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Student Transcribe v1.4.5")
        self.resize(960, 820)
        self.setMinimumSize(840, 520)

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
        self._auto_generate_notes_after_transcription: bool = False
        self._last_model_name: str = "base"
        self._model_a_name: str = "Model A"
        self._model_b_name: str = "Model B"
        self._model_a_segments: List[Dict[str, Any]] = []
        self._study_notes: str = ""
        self._ai_worker: Optional[AIStudyNotesWorker] = None
        self._slide_worker: Optional[SlideExtractorWorker] = None
        self._extracted_slide_text: str = ""
        self.gemini_live = None
        self._is_gemini_live_active: bool = False
        self._current_interim: str = ""

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

        # Check for updates in the background if enabled
        if config_manager.get("auto_check_updates", True):
            QTimer.singleShot(3500, self._check_updates_silently)

        # Initial state
        self.status_label.setText("Ready to record or import audio.")

    def _init_navigation(self):
        """Set up FluentWindow sidebar navigation with sub-interfaces."""
        # 1. Transcribe Interface (main page)
        self.transcribe_page = TranscribeInterface(
            recorder=self.recorder,
            video_watcher=self.video_watcher,
            audio_player=self.audio_player,
            parent=self,
        )

        # Embed Source Tabs Pivot centered directly into the window TitleBar without app title text or app icon
        self.transcribe_page.pivot.setParent(self.titleBar)
        self.titleBar.titleLabel.hide()
        self.titleBar.titleLabel.setText("")
        self.titleBar.iconLabel.hide()

        # Top Report Bug button
        from updater import open_bug_report
        self.top_report_btn = TransparentToolButton(FIF.FEEDBACK, self.titleBar)
        self.top_report_btn.setFixedSize(36, 32)
        self.top_report_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.top_report_btn.setToolTip("Report a Bug or Suggest Feature on GitHub")
        self.top_report_btn.clicked.connect(open_bug_report)

        # Top Settings button
        self.top_settings_btn = TransparentToolButton(FIF.SETTING, self.titleBar)
        self.top_settings_btn.setFixedSize(36, 32)
        self.top_settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.top_settings_btn.setToolTip("Settings, Updates & Diagnostics")
        self.top_settings_btn.clicked.connect(self._open_settings)

        # Rebuild titleBar.hBoxLayout with centered pivot, report button, settings button, and no app icon
        while self.titleBar.hBoxLayout.count() > 0:
            self.titleBar.hBoxLayout.takeAt(0)

        self.titleBar.hBoxLayout.addSpacing(16)
        self.titleBar.hBoxLayout.addStretch(1)
        self.titleBar.hBoxLayout.addWidget(self.transcribe_page.pivot, 0, Qt.AlignmentFlag.AlignCenter)
        self.titleBar.hBoxLayout.addStretch(1)
        self.titleBar.hBoxLayout.addWidget(self.top_report_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.titleBar.hBoxLayout.addSpacing(4)
        self.titleBar.hBoxLayout.addWidget(self.top_settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.titleBar.hBoxLayout.addSpacing(8)
        self.titleBar.hBoxLayout.addLayout(self.titleBar.vBoxLayout, 0)

        # Ensure title bar buttons are always in front in Z-order above Pivot
        self.titleBar.minBtn.raise_()
        self.titleBar.maxBtn.raise_()
        self.titleBar.closeBtn.raise_()
        self.top_report_btn.raise_()
        self.top_settings_btn.raise_()

        # Constrain initial pivot maximum width to protect right-side window buttons
        available_width = max(320, self.width() - 240)
        self.transcribe_page.pivot.setMaximumWidth(available_width)

        # Reference to recordings list inside transcribe_page
        self.recordings_list = self.transcribe_page.recordings_list

        self.addSubInterface(
            self.transcribe_page,
            FIF.MICROPHONE,
            "Transcribe",
            position=NavigationItemPosition.TOP,
        )

        # Completely hide the left navigation bar to maximize workspace
        self.navigationInterface.hide()
        self.navigationInterface.setFixedWidth(0)

        # Set default page
        self.switchTo(self.transcribe_page)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "titleBar"):
            if hasattr(self.titleBar, "minBtn"):
                self.titleBar.minBtn.raise_()
            if hasattr(self.titleBar, "maxBtn"):
                self.titleBar.maxBtn.raise_()
            if hasattr(self.titleBar, "closeBtn"):
                self.titleBar.closeBtn.raise_()
            if hasattr(self, "top_report_btn"):
                self.top_report_btn.raise_()
            if hasattr(self, "top_settings_btn"):
                self.top_settings_btn.raise_()
            if hasattr(self, "transcribe_page") and hasattr(self.transcribe_page, "pivot"):
                available_width = max(320, self.width() - 240)
                self.transcribe_page.pivot.setMaximumWidth(available_width)

    def _open_class_slides_view(self):
        """Switch directly to the full-screen Class Slides & Annotate view."""
        self.switchTo(self.transcribe_page)
        self._tp.pivot.setCurrentItem("slidesPage")
        self._on_pivot_changed("slidesPage")

    def _open_subjects_tab(self):
        """Switch directly to the dedicated Subjects Hub tab on top."""
        self.switchTo(self.transcribe_page)
        self._tp.pivot.setCurrentItem("subjectsPage")
        self._on_pivot_changed("subjectsPage")
        if hasattr(self._tp, "subjects_hub"):
            self._tp.subjects_hub.refresh()

    def _on_record_for_subject_from_hub(self, subj_name: str):
        """Pre-select subject and switch to Live Record tab."""
        if subj_name and hasattr(self, "subject_combo"):
            idx = self.subject_combo.findData(subj_name)
            if idx < 0:
                idx = self.subject_combo.findText(subj_name)
            if idx >= 0:
                self.subject_combo.setCurrentIndex(idx)
        self.switchTo(self.transcribe_page)
        self._tp.pivot.setCurrentItem("recordPage")
        self._on_pivot_changed("recordPage")

    def _on_upload_for_subject_from_hub(self, file_or_subj: str):
        """Pre-select subject or video and switch to Upload tab."""
        if file_or_subj and os.path.exists(file_or_subj):
            self._on_file_selected(file_or_subj)
        self.switchTo(self.transcribe_page)
        self._tp.pivot.setCurrentItem("uploadPage")
        self._on_pivot_changed("uploadPage")

    def _open_subjects_dialog(self):
        """Open the Academic Subject & Course Management dialog."""
        from ui.subject_dialog import SubjectManagementDialog
        dlg = SubjectManagementDialog(self)
        dlg.subjects_changed.connect(self.transcribe_page._populate_subject_dropdown)
        dlg.subjects_changed.connect(self.recordings_list.refresh)
        dlg.exec()
        self.transcribe_page._populate_subject_dropdown()
        self.recordings_list.refresh()
        if hasattr(self._tp, "subjects_hub"):
            self._tp.subjects_hub.refresh()

    def _connect_signals(self):
        """Wire up all signal connections for the transcribe interface."""
        tp = self.transcribe_page

        # Pivot tab changes
        tp.pivot.currentItemChanged.connect(self._on_pivot_changed)

        # Recording controls
        tp.source_mode_combo.currentIndexChanged.connect(self._on_audio_source_mode_changed)
        tp.language_combo.currentIndexChanged.connect(self._on_language_changed)
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

        # Slide Companion tab
        tp.slide_companion.pdf_imported_for_jargon.connect(lambda p: self._run_slide_extraction([p]))
        tp.slide_companion.slide_marked_here.connect(self._on_slide_marked_here)
        tp.slide_companion.transcript_segments_provider = lambda: self._transcribed_segments
        tp.slide_companion.set_time_provider(self._get_active_companion_timestamp)
        tp.slide_companion.lecture_metadata_provider = lambda: {
            "subject": tp.subject_combo.currentText(),
            "session_type": tp.session_type_combo.currentText(),
            "modules": [m.strip() for m in tp.modules_input.text().split(",") if m.strip()],
        }
        self.audio_player.position_changed.connect(self._on_audio_player_position_changed)

        # Recordings list
        self.recordings_list.recording_selected.connect(self._on_recording_loaded)
        self.recordings_list.chat_with_recording.connect(self._on_chat_with_recording)

        # Subjects Hub signals
        sh = tp.subjects_hub
        sh.load_recording_requested.connect(self._on_recording_loaded)
        sh.chat_with_recording_requested.connect(self._on_chat_with_recording)
        sh.play_audio_requested.connect(self.audio_player.load_audio)
        sh.record_for_subject_requested.connect(self._on_record_for_subject_from_hub)
        sh.upload_for_subject_requested.connect(self._on_upload_for_subject_from_hub)

        # Slide OCR & Imports
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
        tp._export_actions["folder"].triggered.connect(self._open_transcripts_folder)
        tp.clear_btn.clicked.connect(self._clear_transcript)

        # Action bar
        tp.quick_model_combo.currentTextChanged.connect(self._on_quick_model_changed)
        tp.benchmark_btn.clicked.connect(self._open_benchmark_dialog)
        tp.diarize_cb.toggled.connect(self._on_diarize_toggled)
        tp.transcribe_only_btn.clicked.connect(self._start_transcribe_only)
        tp.compare_models_btn.clicked.connect(self._open_model_comparison_flow)
        tp.study_guide_btn.clicked.connect(self._generate_study_guide)
        tp.action_btn.clicked.connect(self._start_transcribe_and_generate_notes)

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
    def language_combo(self):
        return self._tp.language_combo

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
    def slide_companion(self):
        return self._tp.slide_companion

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

    @property
    def lecture_chat_widget(self):
        return self._tp.lecture_chat_widget

    @property
    def view_chat_btn(self):
        return self._tp.view_chat_btn

    @property
    def subject_combo(self):
        return self._tp.subject_combo

    @property
    def session_type_combo(self):
        return self._tp.session_type_combo

    @property
    def modules_input(self):
        return self._tp.modules_input

    @property
    def audio_player_bar(self):
        return getattr(self._tp, "audio_player_bar", None)

    def _get_active_companion_timestamp(self) -> float:
        """Return the current active lecture timestamp for 'We Are Here' marking."""
        # 1. Live recording in progress
        if hasattr(self, "recorder") and self.recorder and self.recorder.is_recording:
            return float(self.recorder.get_duration())

        # 2. Audio player active / scrubbed
        if hasattr(self, "audio_player") and self.audio_player:
            if self.audio_player.is_playing or self.audio_player.position_ms > 0:
                return float(self.audio_player.position_seconds)

        # 3. Last recorded elapsed time before reset
        if getattr(self, "_tp", None) and getattr(self._tp, "_current_record_elapsed", 0.0) > 0.0:
            return float(self._tp._current_record_elapsed)

        return 0.0

    def _on_audio_player_position_changed(self, pos_ms: int):
        """Update slide companion time badge while reviewing/listening to audio recordings."""
        if hasattr(self, "recorder") and self.recorder and self.recorder.is_recording:
            return
        if hasattr(self, "slide_companion"):
            self.slide_companion.set_playback_time_display(
                pos_ms / 1000.0,
                is_playing=self.audio_player.is_playing,
            )

    # ==========================
    # Tab & Audio Source Handling
    # ==========================
    def _on_pivot_changed(self, route_key: str):
        if hasattr(self, "stackedWidget") and self.stackedWidget.currentWidget() != self.transcribe_page:
            self.switchTo(self.transcribe_page)
        is_fullscreen_tab = (route_key in ("slidesPage", "videoPage", "subjectsPage", "historyPage"))
        self._tp.set_full_screen_slides_mode(is_fullscreen_tab)

        if route_key == "slidesPage":
            self._tp.stacked_widget.setCurrentWidget(self._tp.slide_companion)
            if self.isVisible() and not self.isMaximized() and not self.isFullScreen():
                self.showMaximized()
        elif route_key == "recordPage":
            self._tp.stacked_widget.setCurrentWidget(self._tp.record_page)
            self._audio_source_type = "recorded"
        elif route_key == "uploadPage":
            self._tp.stacked_widget.setCurrentWidget(self._tp.upload_page)
            self._audio_source_type = "file"
            selected = self.drop_zone.get_selected_path()
            if selected:
                self._current_audio_file = selected
        elif route_key == "videoPage":
            self._tp.stacked_widget.setCurrentWidget(self._tp.video_tab)
            self._audio_source_type = "video"
            self.video_tab.refresh_file_list()
        elif route_key == "subjectsPage":
            self._tp.stacked_widget.setCurrentWidget(self._tp.subjects_hub)
            self._tp.subjects_hub.refresh()
        elif route_key == "historyPage":
            self._tp.stacked_widget.setCurrentWidget(self._tp.recordings_list)
            self._tp.recordings_list.refresh()

    def _on_slide_marked_here(self, slide_num: int, elapsed_sec: float, slide_title: str):
        ts_str = format_timestamp(elapsed_sec)
        self.status_label.setText(f"Marked 'We Are Here': Slide {slide_num} ({slide_title}) at [{ts_str}]")
        if getattr(self, "_is_gemini_live_active", False) and getattr(self, "gemini_live", None):
            self.gemini_live.update_context(slide_context=slide_title)
        # Also append a live visual marker into the transcript preview if user is recording
        marker_html = (
            f'<p style="margin: 6px 0; padding: 4px 8px; background-color: #1e293b; border-left: 3px solid #38bdf8;">'
            f'<span style="color: #38bdf8; font-weight: bold;">[Slide {slide_num} @ {ts_str}]</span> '
            f'<span style="color: #e2e8f0; font-weight: bold;">{slide_title}</span>'
            f'</p>'
        )
        self.output_text.append(marker_html)

    def _on_video_dropped(self, video_path: str):
        fname = os.path.basename(video_path)
        self.status_label.setText(f"Video detected: '{fname}'. Queuing for audio extraction & deletion...")
        self.video_watcher.queue_video(video_path)

    def _render_transcript_segments(
        self,
        segments: List[Dict[str, Any]],
        interim: str = "",
        scroll_to_end: bool = True,
    ):
        """Render segment list into output text preview with clickable speaker badges, audio timestamps, and live interim speech."""
        has_speakers = any("speaker" in seg for seg in segments)
        self.rename_speaker_btn.setEnabled(len(segments) > 0)

        lines = []
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
                    f'{badge_html} {ts_link}'
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
            lines.append(html_line)

        if interim:
            lines.append(
                f'<p style="margin: 6px 0; padding: 4px 8px; background-color: #0f172a; border-left: 3px solid #38bdf8; border-radius: 4px;">'
                f'<span style="color: #38bdf8; font-weight: bold; font-family: monospace;">⚡ Live:</span> '
                f'<span style="color: #cbd5e1; font-style: italic;">{interim}</span>'
                f'</p>'
            )

        self.output_text.setHtml("".join(lines))

        # Auto-scroll handling: live streaming follows newest lines to bottom; loading past file stays at top
        if scroll_to_end:
            cursor = self.output_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.output_text.setTextCursor(cursor)
            self.output_text.ensureCursorVisible()

            sb = self.output_text.verticalScrollBar()
            sb.setValue(sb.maximum())
            QTimer.singleShot(25, lambda: sb.setValue(sb.maximum()))
        else:
            sb = self.output_text.verticalScrollBar()
            sb.setValue(0)
            cursor = self.output_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.output_text.setTextCursor(cursor)
            self.output_text.ensureCursorVisible()

        # Update stats
        unique_speakers = len(set(s.get("speaker", "Speaker 1") for s in segments)) if segments else 0
        words = sum(len(s.get("text", "").split()) for s in segments)
        if interim:
            words += len(interim.split())
        if unique_speakers > 1:
            self.stats_label.setText(f"{len(segments)} segments | {words} words | {unique_speakers} speakers  •  Click [time] to play or [Speaker] to rename")
        else:
            self.stats_label.setText(f"{len(segments)} segments | {words} words  •  Click [time] to play audio")

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

            self._render_transcript_segments(segments, scroll_to_end=False)

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
            self.status_label.setText(
                f"Loaded saved transcript for '{filename}' ({words} words, {speakers} spk)."
            )
            self._update_lecture_chat_context()
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
        self.audio_player.load_audio(file_path)

        from transcript_storage import get_recording_academic_info
        academic = get_recording_academic_info(file_path)
        subj = academic.get("subject", "")
        if subj and hasattr(self, "subject_combo"):
            idx = self.subject_combo.findText(subj)
            if idx >= 0:
                self.subject_combo.setCurrentIndex(idx)
        stype = academic.get("session_type", "")
        if stype and hasattr(self, "session_type_combo"):
            idx = self.session_type_combo.findText(stype)
            if idx >= 0:
                self.session_type_combo.setCurrentIndex(idx)
        mods = academic.get("modules", [])
        if mods and hasattr(self, "modules_input"):
            self.modules_input.setText(", ".join(mods))

        loaded = self._load_saved_transcript(file_path)
        if not loaded:
            self.status_label.setText(f"Selected audio file: {filename}")

    def _on_recording_loaded(self, file_path: str):
        self._current_audio_file = file_path
        self._audio_source_type = "file"
        filename = os.path.basename(file_path)
        base_name = os.path.splitext(filename)[0]
        self.title_input.setText(f"Transcript - {base_name}")
        self.audio_player.load_audio(file_path)

        from transcript_storage import get_recording_academic_info
        academic = get_recording_academic_info(file_path)
        subj = academic.get("subject", "")
        if subj and hasattr(self, "subject_combo"):
            idx = self.subject_combo.findText(subj)
            if idx >= 0:
                self.subject_combo.setCurrentIndex(idx)
        stype = academic.get("session_type", "")
        if stype and hasattr(self, "session_type_combo"):
            idx = self.session_type_combo.findText(stype)
            if idx >= 0:
                self.session_type_combo.setCurrentIndex(idx)
        mods = academic.get("modules", [])
        if mods and hasattr(self, "modules_input"):
            self.modules_input.setText(", ".join(mods))

        # Update file indicators across Record and Upload views
        if hasattr(self, "saved_file_label"):
            self.saved_file_label.setText(f"Loaded: {filename}")
        if hasattr(self, "drop_zone"):
            self.drop_zone.set_file(file_path)

        # Switch to transcribe page and show Upload tab with the loaded file
        self.switchTo(self.transcribe_page)
        if hasattr(self._tp, "pivot"):
            self._tp.pivot.setCurrentItem("uploadPage")

        loaded = self._load_saved_transcript(file_path)
        if not loaded:
            self.status_label.setText(f"Loaded archive recording: {filename}. Ready to transcribe.")
        else:
            self.status_label.setText(
                f"Loaded archive recording: {filename} (previous transcript loaded). Click 'Transcribe Only' to re-transcribe."
            )
        self._update_lecture_chat_context()

    def _update_lecture_chat_context(self):
        """Pass the current lecture's transcript, notes, and metadata to Ask Lecture AI."""
        if hasattr(self, "lecture_chat_widget"):
            subj = self.subject_combo.currentData() or self.subject_combo.currentText()
            stype = self.session_type_combo.currentText()
            mods = [m.strip() for m in self.modules_input.text().split(",") if m.strip()]
            fname = os.path.basename(self._current_audio_file or "")
            self.lecture_chat_widget.set_lecture_context(
                transcript_text=self._transcription_text,
                study_notes=self._study_notes,
                subject=subj,
                session_type=stype,
                modules=mods,
                filename=fname,
            )

    def _on_chat_with_recording(self, file_path: str):
        """Immediately open an archive recording in the Ask Lecture AI panel."""
        self._on_recording_loaded(file_path)
        self._update_lecture_chat_context()
        self.switchTo(self.transcribe_page)
        if hasattr(self._tp, "switch_to_chat"):
            self._tp.switch_to_chat()

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
            self.saved_file_label.setText(f"Saved: {fname}")
            if not self._is_gemini_live_active:
                self.record_status_badge.setText("RECORDING SAVED")
                self.status_label.setText(f"Recording saved: {fname}")
                self.recordings_list.refresh()
                if hasattr(self._tp, "subjects_hub"):
                    self._tp.subjects_hub.refresh()

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

    def _on_language_changed(self, index: int):
        lang = self.language_combo.currentData() or "en"
        config_manager.set("whisper_language", lang)
        config_manager.save()
        if self._is_gemini_live_active and self.gemini_live:
            self.gemini_live.update_context(language=lang)
        self.status_label.setText(f"Transcription language set to: {self.language_combo.currentText()}")

    def _on_record_started(self):
        self.record_btn.setText("Recording...")
        self.record_btn.setIcon(FIF.MICROPHONE)
        self.record_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.source_mode_combo.setEnabled(False)
        self.language_combo.setEnabled(False)

        # Check internet connectivity & Gemini API configuration for automatic live transcription
        from gemini_live import GeminiLiveTranscriber, is_internet_available
        is_online = is_internet_available(timeout=1.2)
        api_key = config_manager.get("gemini_api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()

        if is_online and api_key:
            self._is_gemini_live_active = True
            model = config_manager.get("gemini_model", "Gemini 3.5 Transcribe Live")
            self.record_status_badge.setText("RECORDING • GEMINI LIVE ✨")
            self.status_label.setText(f"⚡ Live transcription active with Google Gemini ({model})")

            # Collect active course jargon & slide context for Gemini
            raw_jargon = self.jargon_input.text().strip() if hasattr(self, "jargon_input") else ""
            slide_topic = ""
            if hasattr(self, "slide_companion") and self.slide_companion and self.slide_companion.current_deck:
                deck = self.slide_companion.current_deck
                if 0 <= deck.preview_idx < len(deck.titles):
                    slide_topic = deck.titles[deck.preview_idx]

            # Clear transcript view for the fresh recording session
            self._transcribed_segments = []
            self._transcription_text = ""
            self.output_text.clear()

            selected_lang = config_manager.get("whisper_language", "en") or "en"
            self.gemini_live = GeminiLiveTranscriber(
                api_key=api_key,
                model=model,
                sample_rate=self.recorder.sample_rate,
                language=selected_lang,
                jargon_prompt=raw_jargon,
                slide_context=slide_topic,
                parent=self,
            )
            self.gemini_live.segment_received.connect(self._on_segment_received)
            self.gemini_live.interim_received.connect(self._on_interim_received)
            self.gemini_live.transcription_completed.connect(self._on_gemini_live_completed)
            self.gemini_live.status_changed.connect(self._on_worker_status)
            self.gemini_live.error_occurred.connect(self._on_gemini_live_error)
            self.recorder.audio_chunk_recorded.connect(self.gemini_live.process_audio_chunk)
        elif is_online and not api_key:
            self._is_gemini_live_active = False
            self.record_status_badge.setText("RECORDING • LOCAL")
            self.status_label.setText("Recording audio locally. Tip: Add Gemini API key in Settings for real-time live transcription.")
        else:
            self._is_gemini_live_active = False
            self.record_status_badge.setText("RECORDING • OFFLINE")
            self.status_label.setText("Recording audio locally (Offline). Whisper will transcribe when finished.")

    def _on_gemini_live_completed(self, segments: List[Dict[str, Any]], full_text: str):
        self._is_gemini_live_active = False
        self._last_model_name = "gemini-live"
        self._current_interim = ""

        # If segments is empty but we had segments accumulated in self._transcribed_segments, keep them!
        if not segments and self._transcribed_segments:
            segments = list(self._transcribed_segments)
            full_text = " ".join(s.get("text", "") for s in segments).strip()

        # If still empty but an audio file was recorded, auto-fallback to local Whisper so zero transcript is lost!
        if not segments and self._current_audio_file and os.path.exists(self._current_audio_file):
            logger.info("Gemini Live produced 0 segments. Initiating automatic local Whisper transcription.")
            self.status_label.setText("Live stream produced 0 segments; transcribing recording with Whisper...")
            self.record_status_badge.setText("RECORDING SAVED • TRANSCRIBING...")
            self._start_transcription()
            return

        self._on_transcription_completed(segments, full_text)
        self.record_status_badge.setText("RECORDING SAVED • TRANSCRIBED ✨")
        if segments:
            self.status_label.setText(
                f"✅ Gemini Live complete! Transcribed {len(segments)} segments in real-time."
            )
        else:
            self.status_label.setText("✅ Gemini Live complete (audio saved locally).")

    def _on_gemini_live_error(self, err_msg: str):
        self.record_status_badge.setText("RECORDING • LOCAL FALLBACK")
        self.status_label.setText(f"Gemini Live notice: {err_msg}. Continuing local recording.")
        InfoBar.warning(
            title="Gemini Live Notice",
            content=err_msg,
            isClosable=True,
            duration=8000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    def _on_record_paused(self, is_paused: bool):
        if is_paused:
            self.pause_btn.setText("Resume")
            self.pause_btn.setIcon(FIF.PLAY)
            self.record_status_badge.setText("PAUSED")
            self.status_label.setText("Recording paused.")
        else:
            self.pause_btn.setText("Pause")
            self.pause_btn.setIcon(FIF.PAUSE)
            if self._is_gemini_live_active:
                self.record_status_badge.setText("RECORDING • GEMINI LIVE ✨")
            else:
                self.record_status_badge.setText("RECORDING")
            self.status_label.setText("Recording resumed.")

    def _on_record_finished(self, file_path: str):
        self._current_audio_file = file_path
        self.record_btn.setText("Start Recording")
        self.record_btn.setIcon(FIF.MICROPHONE)
        self.record_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self.pause_btn.setIcon(FIF.PAUSE)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.source_mode_combo.setEnabled(True)
        self.language_combo.setEnabled(True)
        self.audio_meter.reset()
        self.slide_companion.set_recording_time_display(self._tp._current_record_elapsed, is_recording=False)
        self.audio_player.load_audio(file_path)

        if self._is_gemini_live_active and self.gemini_live:
            try:
                self.recorder.audio_chunk_recorded.disconnect(self.gemini_live.process_audio_chunk)
            except Exception:
                pass
            self.status_label.setText("Finalizing Gemini Live transcript...")
            self.gemini_live.finish()
        else:
            self.record_status_badge.setText("RECORDING SAVED")
            fname = os.path.basename(file_path)
            self.status_label.setText(f"Recording saved: {fname}. Ready to transcribe.")
            self.saved_file_label.setText(f"Saved: {fname}")
            self.recordings_list.refresh()

    def _on_audio_level(self, level: float):
        self.audio_meter.set_level(level)

    def _on_record_duration(self, seconds: float):
        self._tp._current_record_elapsed = float(seconds)
        self.timer_label.setText(format_timestamp(seconds))
        self.slide_companion.set_recording_time_display(seconds, is_recording=self.recorder.is_recording)

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
    # Settings & Diagnostics
    # ==========================
    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.exec()

    def _open_logs(self):
        """Open the application diagnostics log file in default viewer."""
        open_log_file()

    def _check_updates_silently(self):
        from updater import CheckUpdateWorker
        self._startup_update_worker = CheckUpdateWorker(parent=self)
        self._startup_update_worker.update_available.connect(self._on_startup_update_available)
        self._startup_update_worker.start()

    def _on_startup_update_available(self, release_info: dict):
        from updater import UpdateDialog
        tag = release_info.get("tag_name", "New release")
        bar = InfoBar.info(
            title=f"🚀 Update Available ({tag})",
            content=f"Student Transcribe {tag} is available! Click Settings → 'Check for Updates' to install.",
            isClosable=True,
            duration=12000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

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

        # Update Language combo
        current_lang = config_manager.get("whisper_language", "en") or "en"
        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == current_lang:
                self.language_combo.blockSignals(True)
                self.language_combo.setCurrentIndex(i)
                self.language_combo.blockSignals(False)
                break

    # ==========================
    # Transcription Logic
    # ==========================
    def _validate_audio_source(self) -> Optional[str]:
        """Verify an audio file is ready for transcription."""
        if self.recorder.is_recording:
            self._stop_recording()

        current_route = "recordPage"
        if hasattr(self._tp, "pivot") and hasattr(self._tp.pivot, "currentRouteKey"):
            current_route = self._tp.pivot.currentRouteKey() or "recordPage"

        # 1. If upload tab is active and user selected a file via drop zone, use it
        if current_route == "uploadPage" and hasattr(self, "drop_zone"):
            path = self.drop_zone.get_selected_path()
            if path and os.path.exists(path):
                self._current_audio_file = path
                return path

        # 2. If an active audio file is already loaded (from recordings archive, upload, or recording)
        if self._current_audio_file and os.path.exists(self._current_audio_file):
            return self._current_audio_file

        # 3. If no audio file is available, show contextual guide
        if current_route == "recordPage":
            InfoBar.warning(
                title="No Audio Recorded",
                content="Please record audio first, load a recording from Archive, or switch to Upload tab.",
                isClosable=True,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
        elif current_route == "uploadPage":
            InfoBar.warning(
                title="No Audio File Selected",
                content="Please select or drop an audio file to transcribe.",
                isClosable=True,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
        elif current_route == "videoPage":
            InfoBar.warning(
                title="No Video Audio Ready",
                content="Please drop a video into the Video Inbox or browse a video file.",
                isClosable=True,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
        else:
            InfoBar.warning(
                title="No Audio Available",
                content="Please select or record audio first.",
                isClosable=True,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
        return None

    def _start_transcription(self):
        audio_path = self._validate_audio_source()
        if not audio_path:
            return

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
        self._auto_generate_notes_after_transcription = False
        self._start_transcription()

    def _start_transcribe_and_generate_notes(self):
        if not self._current_audio_file and not self._transcribed_segments:
            InfoBar.warning(
                title="No Audio or Transcript",
                content="Please record audio or upload a media file first.",
                isClosable=True,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            return

        if self._transcribed_segments:
            # Transcript already generated, jump straight to generating study notes!
            self._generate_study_guide()
        else:
            self._auto_generate_notes_after_transcription = True
            self._start_transcription()

    def _on_worker_status(self, text: str):
        self.status_label.setText(text)

    def _on_transcribe_progress(self, percent: int):
        self.progress_bar.setValue(percent)
        self.pct_label.setText(f"{percent}%")

    def _on_interim_received(self, interim_text: str):
        """Handle streaming sub-second interim speech words from Gemini Live."""
        self._current_interim = interim_text
        self._render_transcript_segments(self._transcribed_segments, interim=interim_text)
        self.status_label.setText(f"⚡ Live: {interim_text}")

    def _on_segment_received(self, seg: Dict[str, Any]):
        self._transcribed_segments.append(seg)
        self._current_interim = ""
        self._render_transcript_segments(self._transcribed_segments)

        # Real-time auto slide suggestion / tracking
        if hasattr(self, "slide_companion"):
            self.slide_companion.process_spoken_text(seg.get("text", ""), float(seg.get("start", 0.0)))

    def _on_transcription_completed(self, segments: List[Dict[str, Any]], full_text: str):
        self._transcription_text = full_text
        self._transcribed_segments = list(segments)
        self._model_a_segments = list(segments)
        self._model_a_name = self._last_model_name
        self._set_ui_processing_state(False)
        self.compare_models_btn.setEnabled(len(segments) > 0)
        self.study_guide_btn.setEnabled(len(segments) > 0)
        self.export_btn.setEnabled(len(segments) > 0)
        self.rename_speaker_btn.setEnabled(len(segments) > 0)

        self._render_transcript_segments(segments)

        # Auto-align slide timeline if slides are loaded
        if hasattr(self, "slide_companion"):
            self.slide_companion.auto_sync_timeline_from_transcript(segments)

        # If user clicked "Transcribe & Generate Notes", kick off study guide generation
        if getattr(self, "_auto_generate_notes_after_transcription", False) and len(segments) > 0:
            self._auto_generate_notes_after_transcription = False
            self.status_label.setText("Transcription complete! Generating AI study notes...")
            self._generate_study_guide()

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
                    prompt=self.jargon_input.text().strip(),
                    subject=self.subject_combo.currentText() if hasattr(self, "subject_combo") else "",
                    session_type=self.session_type_combo.currentText() if hasattr(self, "session_type_combo") else "",
                    modules=[m.strip() for m in self.modules_input.text().split(",") if m.strip()] if hasattr(self, "modules_input") else [],
                    study_notes=self._study_notes or self.study_notes_text.toPlainText().strip(),
                )
                self.recordings_list.refresh()
                if hasattr(self._tp, "subjects_hub"):
                    self._tp.subjects_hub.refresh()
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
        self._update_lecture_chat_context()

        # Auto-generate study notes if requested from primary action
        if self._auto_generate_notes_after_transcription:
            self._auto_generate_notes_after_transcription = False
            self._generate_study_guide()

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

        self._render_transcript_segments(merged_segments, scroll_to_end=False)

        words = sum(len(s.get("text", "").split()) for s in self._transcribed_segments)
        self.stats_label.setText(f"{len(self._transcribed_segments)} segments | {words} words (Verified ✓)")
        self.compare_models_btn.setEnabled(True)
        self.study_guide_btn.setEnabled(True)
        self.status_label.setText(
            f"Verified multi-model transcript applied! ({len(merged_segments)} segments). Ready to generate notes."
        )

    # ==========================
    # Utilities
    # ==========================
    def _set_ui_processing_state(self, is_processing: bool):
        self.action_btn.setEnabled(not is_processing)
        self.transcribe_only_btn.setEnabled(not is_processing)
        self.compare_models_btn.setEnabled(not is_processing and len(self._transcribed_segments) > 0)
        self.study_guide_btn.setEnabled(not is_processing and len(self._transcribed_segments) > 0)
        self.export_btn.setEnabled(not is_processing and len(self._transcribed_segments) > 0)
        self._tp.pivot.setEnabled(not is_processing)
        self.source_mode_combo.setEnabled(not is_processing)
        self.language_combo.setEnabled(not is_processing)
        self.quick_model_combo.setEnabled(not is_processing)
        self.benchmark_btn.setEnabled(not is_processing)
        self.diarize_cb.setEnabled(not is_processing)
        self.title_input.setEnabled(not is_processing)
        self.tags_input.setEnabled(not is_processing)

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
        self._current_interim = ""
        self.output_text.clear()
        self.study_notes_text.clear()
        self._study_notes = ""
        self._transcribed_segments.clear()
        self._model_a_segments.clear()
        self._transcription_text = ""
        self.stats_label.setText("0 segments | 0 words")
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

    # ==========================
    # Slide OCR & Course Jargon
    # ==========================
    def _import_slides_for_ocr(self):
        """Open file dialog to import PDF, PPT, PPTX, Word, or slide images and extract course jargon via OCR."""
        from document_converter import SUPPORTED_SLIDE_EXTENSIONS
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Lecture Slides, Documents, or Screenshots for OCR Jargon Extraction",
            "",
            "All Lecture Slides & Documents (*.pdf *.ppt *.pptx *.doc *.docx *.png *.jpg *.jpeg *.bmp *.webp *.txt *.md);;"
            "PDF & Office Presentations (*.pdf *.ppt *.pptx *.doc *.docx);;"
            "Images & Screenshots (*.png *.jpg *.jpeg *.bmp *.webp);;"
            "All Files (*.*)",
        )
        if not files:
            return
        # If user selected slides or documents (PDF, PPT, Word), load the first deck into the Class Slides tab
        slide_docs = [f for f in files if Path(f).suffix.lower() in SUPPORTED_SLIDE_EXTENSIONS]
        if slide_docs:
            self.slide_companion.load_pdf(slide_docs[0])
            other_files = [f for f in files if f != slide_docs[0]]
            if other_files:
                self._run_slide_extraction(other_files)
        else:
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
        self.import_slides_btn.setText("Extracting OCR...")
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
        self.import_slides_btn.setText("Import Slides / OCR...")
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
        self.import_slides_btn.setText("Import Slides / OCR...")
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
                if hasattr(self._tp, "subjects_hub"):
                    self._tp.subjects_hub.refresh()
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
        self._update_lecture_chat_context()

        # Automatically switch preview to Study Guide view
        self._tp.view_study_btn.click()

        self.status_label.setText("AI Study Guide generated successfully! Saved locally.")
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
        # Prevent accidental closure without exporting annotated lecture slides
        if hasattr(self, "slide_companion") and self.slide_companion:
            try:
                if self.slide_companion.has_unexported_slides():
                    # Switch to Class Slides tab so user can review what they have
                    if hasattr(self, "_tp") and hasattr(self._tp, "pivot"):
                        self._tp.pivot.setCurrentItem("slidesPage")
                        self._on_pivot_changed("slidesPage")

                    w = MessageBox(
                        "Export Annotated Slides Before Exiting?",
                        "You have class lecture slides and notes that have not been exported as a PDF yet.\n\n"
                        "Would you like to export your annotated PDF now before closing?",
                        self,
                    )
                    w.yesButton.setText("Export PDF & Exit")
                    w.cancelButton.setText("Cancel (Stay in App)")

                    discard_btn = PushButton("Discard & Exit", w)
                    w.buttonLayout.insertWidget(1, discard_btn)

                    user_decision = {"action": "cancel"}

                    def on_discard():
                        user_decision["action"] = "discard"
                        w.reject()

                    discard_btn.clicked.connect(on_discard)

                    if w.exec():
                        user_decision["action"] = "export"

                    if user_decision["action"] == "export":
                        exported = self.slide_companion._prompt_export_annotated()
                        if not exported:
                            # User cancelled file picker or export failed: do NOT close the app
                            event.ignore()
                            return
                    elif user_decision["action"] == "discard":
                        # User explicitly chose to discard and exit
                        pass
                    else:
                        # User clicked Cancel, pressed Esc, or closed dialog: do NOT close the app
                        event.ignore()
                        return
            except Exception as e:
                print(f"[Main] Error in closeEvent slide check: {e}")

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
            myappid = "studenttranscribe.app.1.4.5"
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
