"""Settings Dialog for Student Transcribe.

Configures Notion API secret, Database ID, Whisper model size,
compute device (CPU/CUDA), and tests API connection.
"""

from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

import sys
import webbrowser

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SingleDirectionScrollArea,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
    TransparentPushButton,
)

from audio_recorder import get_audio_input_devices, get_audio_loopback_devices
from config_manager import config_manager
from diarization import is_diarization_available
from transcriber import is_cuda_available
from ui.benchmark_dialog import BenchmarkDialog
from updater import APP_VERSION, CheckUpdateWorker, UpdateDialog, open_bug_report


class SettingsDialog(QDialog):
    """Modal dialog for application settings."""

    settings_saved = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Student Transcribe Settings")
        self.resize(640, 720)
        self.setModal(True)

        self._init_ui()
        self._load_values()

    def _create_form_row(self, label_text: str, widget, tooltip: str = "") -> QHBoxLayout:
        layout = QHBoxLayout()
        label = BodyLabel(label_text, self)
        if tooltip:
            label.setToolTip(tooltip)
            if hasattr(widget, "setToolTip"):
                widget.setToolTip(tooltip)
        layout.addWidget(label, 1)
        if hasattr(widget, "isWidgetType") and widget.isWidgetType():
            layout.addWidget(widget, 2)
        else:
            layout.addLayout(widget, 2)
        return layout

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # Title at the top (fixed)
        header = TitleLabel("Settings & Configuration", self)
        root_layout.addWidget(header)

        sub = BodyLabel("Configure local audio capture, Whisper transcription, and AI study assistants.", self)
        root_layout.addWidget(sub)

        # Smooth Scroll Area for settings cards
        scroll = SingleDirectionScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        scroll_content = QWidget()
        main_layout = QVBoxLayout(scroll_content)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(4, 4, 12, 4)

        # 1. Whisper Model Settings Group
        whisper_card = CardWidget(self)
        whisper_layout = QVBoxLayout(whisper_card)
        whisper_layout.setSpacing(12)
        
        whisper_title = SubtitleLabel("Whisper Speech-to-Text Model", self)
        whisper_layout.addWidget(whisper_title)

        # Model size
        self.model_combo = ComboBox(self)
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        whisper_layout.addLayout(self._create_form_row("Model Size:", self.model_combo, "Larger models provide higher accuracy but take longer to process."))

        # Compute device
        cuda_ok = is_cuda_available()
        self.device_combo = ComboBox(self)
        self.device_combo.addItem("cpu")
        if cuda_ok:
            self.device_combo.addItem("cuda (NVIDIA GPU detected)")
        else:
            self.device_combo.addItem("cuda (No GPU / cuDNN detected)")
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        whisper_layout.addLayout(self._create_form_row("Compute Device:", self.device_combo))

        # Quantization / Compute Type
        self.compute_combo = ComboBox(self)
        self.compute_combo.addItems(["int8", "float16", "float32", "default"])
        whisper_layout.addLayout(self._create_form_row("Compute Type:", self.compute_combo, "int8 is recommended for CPU. float16 is recommended for CUDA."))

        # Decoding Speed / Beam size
        self.beam_combo = ComboBox(self)
        self.beam_combo.addItem("1 (Fast Greedy - Recommended, ~2.5x faster)", userData=1)
        self.beam_combo.addItem("5 (Exhaustive Beam Search)", userData=5)
        whisper_layout.addLayout(self._create_form_row("Decoding Speed:", self.beam_combo, "Beam size 1 runs ~2.5x faster with 99%+ accuracy and prevents GPU memory stalls."))

        # Target / Forced Language
        self.language_combo = ComboBox(self)
        self.language_combo.addItem("English (Forced)", userData="en")
        self.language_combo.addItem("Auto-Detect (Multilingual)", userData="auto")
        self.language_combo.addItem("German (Deutsch)", userData="de")
        self.language_combo.addItem("French (Français)", userData="fr")
        self.language_combo.addItem("Spanish (Español)", userData="es")
        self.language_combo.addItem("Italian (Italiano)", userData="it")
        self.language_combo.addItem("Dutch (Nederlands)", userData="nl")
        self.language_combo.addItem("Portuguese (Português)", userData="pt")
        self.language_combo.addItem("Chinese (Mandarin)", userData="zh")
        self.language_combo.addItem("Japanese (日本語)", userData="ja")
        self.language_combo.addItem("Arabic (العربية)", userData="ar")
        self.language_combo.addItem("Russian (Русский)", userData="ru")
        self.language_combo.addItem("Korean (한국어)", userData="ko")
        whisper_layout.addLayout(self._create_form_row("Language:", self.language_combo, "Locks transcription strictly to English (recommended to prevent mid-lecture language hopping) or select Auto-Detect."))

        # Benchmark & Auto-Tune Button
        self.benchmark_btn = PrimaryPushButton("⚡ Benchmark Hardware & Auto-Tune...", self)
        self.benchmark_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.benchmark_btn.clicked.connect(self._open_benchmark_dialog)
        whisper_layout.addWidget(self.benchmark_btn)

        # Default tags
        self.tags_input = LineEdit(self)
        self.tags_input.setPlaceholderText("Meeting, Audio, Notes")
        whisper_layout.addLayout(self._create_form_row("Default Tags:", self.tags_input, "Comma-separated default tags to apply to new Notion pages."))

        main_layout.addWidget(whisper_card)

        # 3. Video Watch Folder Group
        video_card = CardWidget(self)
        video_layout = QVBoxLayout(video_card)
        video_layout.setSpacing(12)
        
        video_title = SubtitleLabel("Video Watch Folder & Automation", self)
        video_layout.addWidget(video_title)

        path_row = QHBoxLayout()
        self.video_folder_input = LineEdit(self)
        self.video_folder_input.setPlaceholderText("Path to watched video folder")
        path_row.addWidget(self.video_folder_input, 1)

        browse_folder_btn = PushButton("Browse...", self)
        browse_folder_btn.clicked.connect(self._browse_video_folder)
        path_row.addWidget(browse_folder_btn)
        video_layout.addLayout(path_row)
        
        delete_video_row = QHBoxLayout()
        self.delete_video_cb = SwitchButton(self)
        delete_video_label = BodyLabel("Delete video file after successful audio extraction", self)
        delete_video_row.addWidget(self.delete_video_cb)
        delete_video_row.addWidget(delete_video_label, 1)
        video_layout.addLayout(delete_video_row)
        
        auto_transcribe_row = QHBoxLayout()
        self.auto_transcribe_video_cb = SwitchButton(self)
        auto_transcribe_label = BodyLabel("Automatically transcribe audio immediately after extraction", self)
        auto_transcribe_row.addWidget(self.auto_transcribe_video_cb)
        auto_transcribe_row.addWidget(auto_transcribe_label, 1)
        video_layout.addLayout(auto_transcribe_row)

        main_layout.addWidget(video_card)

        # 4. Audio Capture & Devices Group
        audio_card = CardWidget(self)
        audio_layout = QVBoxLayout(audio_card)
        audio_layout.setSpacing(12)
        
        audio_title = SubtitleLabel("Audio Capture & Devices", self)
        audio_layout.addWidget(audio_title)

        # Default Audio Source
        self.audio_source_combo = ComboBox(self)
        self.audio_source_combo.addItem("🎙 Microphone Only", userData="mic")
        self.audio_source_combo.addItem("🔊 System Audio Only (Speakers Loopback)", userData="system")
        self.audio_source_combo.addItem("🎙+🔊 Both (Microphone + System Audio)", userData="both")
        audio_tooltip = (
            "Select default recording source:\n"
            "- Microphone Only: Standard voice recording\n"
            "- System Audio Only: Internal PC speaker output (YouTube, podcasts, meetings)\n"
            "- Both: Dual real-time mixing of your voice and computer audio"
        )
        audio_layout.addLayout(self._create_form_row("Default Audio Source:", self.audio_source_combo, audio_tooltip))

        # Preferred Microphone
        self.mic_device_combo = ComboBox(self)
        self.mic_device_combo.addItem("Default Microphone", userData=None)
        input_devs = get_audio_input_devices()
        for dev in input_devs:
            dev_label = f"{dev['name']}" + (" (Default)" if dev.get("is_default") else "")
            self.mic_device_combo.addItem(dev_label, userData=dev["id"])
        audio_layout.addLayout(self._create_form_row("Microphone Device:", self.mic_device_combo, "Select specific input microphone or keep system default."))

        # Preferred System Audio Loopback Device
        self.loopback_device_combo = ComboBox(self)
        self.loopback_device_combo.addItem("Default System Output (Speakers)", userData=None)
        loop_devs = get_audio_loopback_devices()
        for dev in loop_devs:
            dev_label = f"{dev['name']}" + (" (Default)" if dev.get("is_default") else "")
            self.loopback_device_combo.addItem(dev_label, userData=dev["id"])
        audio_layout.addLayout(self._create_form_row("System Audio Device:", self.loopback_device_combo, "Select specific system audio output device for loopback capture."))

        main_layout.addWidget(audio_card)

        # 5. Speaker Diarization Group
        diar_card = CardWidget(self)
        diar_layout = QVBoxLayout(diar_card)
        diar_layout.setSpacing(12)
        
        diar_title = SubtitleLabel("Speaker Diarization (Who Spoke When)", self)
        diar_layout.addWidget(diar_title)

        diar_row = QHBoxLayout()
        self.enable_diarization_cb = SwitchButton(self)
        diar_label = BodyLabel("Identify and label distinct speakers (Speaker 1, Speaker 2)", self)
        diar_label.setToolTip("Automatically separates multi-speaker conversations into distinct speaker turns.")
        diar_row.addWidget(self.enable_diarization_cb)
        diar_row.addWidget(diar_label, 1)
        diar_layout.addLayout(diar_row)

        self.num_speakers_combo = ComboBox(self)
        self.num_speakers_combo.addItem("Auto-Detect (-1)", userData=-1)
        self.num_speakers_combo.addItem("2 Speakers", userData=2)
        self.num_speakers_combo.addItem("3 Speakers", userData=3)
        self.num_speakers_combo.addItem("4 Speakers", userData=4)
        self.num_speakers_combo.addItem("5+ Speakers", userData=5)
        diar_layout.addLayout(self._create_form_row("Expected Speakers:", self.num_speakers_combo, "Specify exact number of speakers if known, or leave on Auto-Detect."))

        models_ready = is_diarization_available()
        status_text = "✅ ONNX Diarization Models Ready (44 MB)" if models_ready else "⏳ Models will auto-download on first use"
        self.diar_status_label = BodyLabel(status_text, self)
        diar_layout.addLayout(self._create_form_row("Model Status:", self.diar_status_label))

        main_layout.addWidget(diar_card)

        # 6. AI Study Assistant Group (Google Gemini & Ollama)
        ai_card = CardWidget(self)
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setSpacing(12)

        ai_title = SubtitleLabel("AI Study Assistant (Gemini & Ollama)", self)
        ai_layout.addWidget(ai_title)

        ai_desc = BodyLabel(
            "Automatically converts lecture transcripts into high-yield educational study guides, "
            "active recall flashcards, and NotebookLM-ready source documents.",
            self,
        )
        ai_desc.setWordWrap(True)
        ai_layout.addWidget(ai_desc)

        # AI Provider
        self.ai_provider_combo = ComboBox(self)
        self.ai_provider_combo.addItem("✨ Google Gemini API (Cloud / Free Tier)", userData="gemini")
        self.ai_provider_combo.addItem("🦙 Local Ollama (100% Offline)", userData="ollama")
        self.ai_provider_combo.currentIndexChanged.connect(self._on_ai_provider_changed)
        ai_layout.addLayout(self._create_form_row("AI Provider:", self.ai_provider_combo))

        # Gemini settings container
        self.gemini_widget = QWidget(self)
        gemini_layout = QVBoxLayout(self.gemini_widget)
        gemini_layout.setContentsMargins(0, 0, 0, 0)
        gemini_layout.setSpacing(8)

        self.gemini_key_input = PasswordLineEdit(self)
        self.gemini_key_input.setPlaceholderText("AIzaSy...")
        gemini_layout.addLayout(self._create_form_row("Gemini API Key:", self.gemini_key_input, "Get a free API key at aistudio.google.com"))

        self.gemini_model_combo = ComboBox(self)
        self.gemini_model_combo.addItems([
            "Gemini 3.5 Transcribe Live",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ])
        gemini_layout.addLayout(self._create_form_row(
            "Live Transcribe Model:",
            self.gemini_model_combo,
            "Real-time streaming speech-to-text model via WebSocket."
        ))

        # Summary & Notes Model Priority Fallback Chain
        summary_row = QHBoxLayout()
        summary_row.setSpacing(8)

        self.gemini_summary_priority_input = LineEdit(self)
        self.gemini_summary_priority_input.setPlaceholderText("gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash...")
        self.gemini_summary_priority_input.setClearButtonEnabled(True)

        self.fetch_models_btn = PushButton("Fetch Live Models", self)
        self.fetch_models_btn.setIcon(FIF.SYNC)
        self.fetch_models_btn.setToolTip("Query Google Gemini API to fetch and rank the newest active models on your account")
        self.fetch_models_btn.clicked.connect(self._fetch_gemini_models)

        summary_row.addWidget(self.gemini_summary_priority_input, 1)
        summary_row.addWidget(self.fetch_models_btn)

        gemini_layout.addLayout(self._create_form_row(
            "Summary Model Priority:",
            summary_row,
            "Ordered fallback chain. If your primary model runs out of credits or is rate-limited (429), it automatically cascades down the list in order."
        ))
        ai_layout.addWidget(self.gemini_widget)

        # Ollama settings container
        self.ollama_widget = QWidget(self)
        ollama_layout = QVBoxLayout(self.ollama_widget)
        ollama_layout.setContentsMargins(0, 0, 0, 0)
        ollama_layout.setSpacing(8)

        self.ollama_host_input = LineEdit(self)
        self.ollama_host_input.setPlaceholderText("http://localhost:11434")
        ollama_layout.addLayout(self._create_form_row("Ollama Host:", self.ollama_host_input, "URL of your local or remote Ollama server (default http://localhost:11434)"))

        self.ollama_model_input = LineEdit(self)
        self.ollama_model_input.setPlaceholderText("llama3.2")
        ollama_layout.addLayout(self._create_form_row("Ollama Model:", self.ollama_model_input, "Name of the Ollama model, e.g. llama3.2, qwen2.5, mistral"))

        self.ollama_key_input = PasswordLineEdit(self)
        self.ollama_key_input.setPlaceholderText("(Optional Bearer Token)")
        ollama_layout.addLayout(self._create_form_row("Ollama API Key:", self.ollama_key_input, "Optional token if your Ollama instance is behind a proxy or requires auth."))
        ai_layout.addWidget(self.ollama_widget)

        # Test AI connection button
        ai_test_row = QHBoxLayout()
        self.test_ai_btn = PushButton("⚡ Test AI Connection", self)
        self.test_ai_btn.clicked.connect(self._run_ai_test)
        self.test_ai_status_label = BodyLabel("", self)
        self.test_ai_status_label.setWordWrap(True)
        ai_test_row.addWidget(self.test_ai_btn)
        ai_test_row.addWidget(self.test_ai_status_label, 1)
        ai_layout.addLayout(ai_test_row)

        main_layout.addWidget(ai_card)

        # Academic Subjects & Courses Card
        academic_card = CardWidget(self)
        academic_layout = QVBoxLayout(academic_card)
        academic_layout.setSpacing(12)
        academic_layout.setContentsMargins(16, 16, 16, 16)
        academic_layout.addWidget(SubtitleLabel("Academic Subjects & Course Curriculum", self))

        subj_desc = BodyLabel(
            "Configure your subjects, course codes, and syllabus topics to automatically categorize recordings and enrich AI lecture study guides.",
            self,
        )
        subj_desc.setWordWrap(True)
        academic_layout.addWidget(subj_desc)

        self.manage_subjects_btn = PushButton("📚 Manage Academic Subjects & Modules...", self)
        self.manage_subjects_btn.setIcon(FIF.EDUCATION)
        self.manage_subjects_btn.clicked.connect(self._open_subject_manager_from_settings)
        academic_layout.addWidget(self.manage_subjects_btn)

        main_layout.addWidget(academic_card)

        # 8. Diagnostics & Application Logs Card
        logs_card = CardWidget(self)
        logs_layout = QVBoxLayout(logs_card)
        logs_layout.setSpacing(12)
        logs_layout.setContentsMargins(16, 16, 16, 16)
        logs_layout.addWidget(SubtitleLabel("Diagnostics & Application Logs", self))

        logs_desc = BodyLabel(
            "Access technical logs for speech recognition, Gemini Live WebSocket streaming, and application diagnostics.",
            self,
        )
        logs_desc.setWordWrap(True)
        logs_layout.addWidget(logs_desc)

        logs_btn_row = QHBoxLayout()
        logs_btn_row.setSpacing(10)
        self.open_logs_btn = PushButton("📄 View Application Logs (logs.txt)", self)
        self.open_logs_btn.setIcon(FIF.DOCUMENT)
        self.open_logs_btn.clicked.connect(self._open_logs_file)
        logs_btn_row.addWidget(self.open_logs_btn)

        self.open_logs_folder_btn = PushButton("📁 Open Logs Directory", self)
        self.open_logs_folder_btn.setIcon(FIF.FOLDER)
        self.open_logs_folder_btn.clicked.connect(self._open_logs_directory)
        logs_btn_row.addWidget(self.open_logs_folder_btn)
        logs_layout.addLayout(logs_btn_row)

        main_layout.addWidget(logs_card)

        # 9. About, Updates & Community Card
        about_card = CardWidget(self)
        about_layout = QVBoxLayout(about_card)
        about_layout.setSpacing(12)
        about_layout.setContentsMargins(16, 16, 16, 16)
        about_layout.addWidget(SubtitleLabel(f"About Student Transcribe (v{APP_VERSION})", self))

        about_desc = BodyLabel(
            "Student Transcribe is an open-source academic lecture recording, transcription, and study companion.",
            self,
        )
        about_desc.setWordWrap(True)
        about_layout.addWidget(about_desc)

        self.auto_check_updates_cb = CheckBox("Automatically check for updates on application launch", self)
        about_layout.addWidget(self.auto_check_updates_cb)

        updates_row = QHBoxLayout()
        updates_row.setSpacing(10)
        self.check_updates_btn = PrimaryPushButton("⚡ Check for Updates...", self)
        self.check_updates_btn.setIcon(FIF.SYNC)
        self.check_updates_btn.clicked.connect(self._check_for_updates_manually)
        updates_row.addWidget(self.check_updates_btn)

        self.report_bug_btn = PushButton("🐛 Report a Bug / Request Feature...", self)
        self.report_bug_btn.setIcon(FIF.FEEDBACK)
        self.report_bug_btn.clicked.connect(open_bug_report)
        updates_row.addWidget(self.report_bug_btn)

        self.github_btn = PushButton("⭐ Star on GitHub", self)
        self.github_btn.setIcon(FIF.SHARE)
        self.github_btn.clicked.connect(lambda: webbrowser.open("https://github.com/ceeach/Student_Transcribe"))
        updates_row.addWidget(self.github_btn)
        about_layout.addLayout(updates_row)

        self.update_status_label = CaptionLabel("", self)
        about_layout.addWidget(self.update_status_label)

        main_layout.addWidget(about_card)

        # Finish scroll setup
        scroll.setWidget(scroll_content)
        root_layout.addWidget(scroll, 1)

        # Dialog Buttons (fixed at the bottom)
        button_layout = QHBoxLayout()

        self.report_bug_bottom_btn = TransparentPushButton("🐛 Report a Bug", self)
        self.report_bug_bottom_btn.setToolTip("Open GitHub to report a problem or suggest a feature")
        self.report_bug_bottom_btn.clicked.connect(open_bug_report)
        button_layout.addWidget(self.report_bug_bottom_btn)

        button_layout.addStretch(1)
        
        self.cancel_btn = PushButton("Cancel", self)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.save_btn = PrimaryPushButton("Save", self)
        self.save_btn.clicked.connect(self._save_and_close)
        button_layout.addWidget(self.save_btn)
        
        root_layout.addLayout(button_layout)

    def _browse_video_folder(self):
        curr = self.video_folder_input.text().strip()
        chosen = QFileDialog.getExistingDirectory(self, "Select Video Watch Folder", curr)
        if chosen:
            self.video_folder_input.setText(chosen)

    def _on_device_changed(self):
        dev = self.device_combo.currentText()
        if "cuda" in dev.lower():
            self.compute_combo.setCurrentText("float16")
        else:
            self.compute_combo.setCurrentText("int8")

    def _load_values(self):
        model = config_manager.get("whisper_model", "base")
        idx = self.model_combo.findText(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)

        device = config_manager.get("device", "cpu")
        for i in range(self.device_combo.count()):
            if self.device_combo.itemText(i).startswith(device):
                self.device_combo.setCurrentIndex(i)
                break

        compute_type = config_manager.get("compute_type", "int8")
        idx = self.compute_combo.findText(compute_type)
        if idx >= 0:
            self.compute_combo.setCurrentIndex(idx)

        # Language selection
        saved_lang = config_manager.get("whisper_language", "en") or "en"
        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == saved_lang:
                self.language_combo.setCurrentIndex(i)
                break

        # Auto updates
        self.auto_check_updates_cb.setChecked(config_manager.get("auto_check_updates", True))

        self.tags_input.setText(config_manager.get("default_tags", "Meeting, Audio"))
        self.video_folder_input.setText(config_manager.get("video_watch_folder", ""))
        self.delete_video_cb.setChecked(config_manager.get("delete_video_after_extraction", True))
        self.auto_transcribe_video_cb.setChecked(config_manager.get("auto_transcribe_video", True))

        # Audio capture settings
        saved_source = config_manager.get("audio_source", "mic")
        for i in range(self.audio_source_combo.count()):
            if self.audio_source_combo.itemData(i) == saved_source:
                self.audio_source_combo.setCurrentIndex(i)
                break

        saved_mic_id = config_manager.get("input_device_id", None)
        for i in range(self.mic_device_combo.count()):
            if self.mic_device_combo.itemData(i) == saved_mic_id:
                self.mic_device_combo.setCurrentIndex(i)
                break

        saved_loop_id = config_manager.get("loopback_device_id", None)
        for i in range(self.loopback_device_combo.count()):
            if self.loopback_device_combo.itemData(i) == saved_loop_id:
                self.loopback_device_combo.setCurrentIndex(i)
                break

        # Diarization settings
        self.enable_diarization_cb.setChecked(config_manager.get("enable_diarization", True))
        saved_spk = config_manager.get("num_speakers", -1)
        for i in range(self.num_speakers_combo.count()):
            if self.num_speakers_combo.itemData(i) == saved_spk:
                self.num_speakers_combo.setCurrentIndex(i)
                break

        # Beam size settings
        saved_beam = config_manager.get("beam_size", 1)
        for i in range(self.beam_combo.count()):
            if self.beam_combo.itemData(i) == saved_beam:
                self.beam_combo.setCurrentIndex(i)
                break

        # AI Study Assistant settings
        saved_provider = config_manager.get("ai_provider", "gemini")
        for i in range(self.ai_provider_combo.count()):
            if self.ai_provider_combo.itemData(i) == saved_provider:
                self.ai_provider_combo.setCurrentIndex(i)
                break
        self._on_ai_provider_changed()

        self.gemini_key_input.setText(config_manager.get("gemini_api_key", ""))
        saved_gemini_model = config_manager.get("gemini_model", "Gemini 3.5 Transcribe Live")
        idx = self.gemini_model_combo.findText(saved_gemini_model)
        if idx >= 0:
            self.gemini_model_combo.setCurrentIndex(idx)
        else:
            self.gemini_model_combo.addItem(saved_gemini_model)
            self.gemini_model_combo.setCurrentText(saved_gemini_model)

        self.gemini_summary_priority_input.setText(
            config_manager.get(
                "gemini_summary_priority",
                "gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash, gemini-2.5-flash, gemini-flash-latest",
            )
        )

        self.ollama_host_input.setText(config_manager.get("ollama_host", "http://localhost:11434"))
        self.ollama_model_input.setText(config_manager.get("ollama_model", "llama3.2"))
        self.ollama_key_input.setText(config_manager.get("ollama_api_key", ""))

    def _fetch_gemini_models(self):
        key = self.gemini_key_input.text().strip()
        if not key:
            InfoBar.warning(
                title="API Key Required",
                content="Please enter your Gemini API Key first to fetch available models.",
                isClosable=True,
                duration=4000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            return

        self.fetch_models_btn.setEnabled(False)
        self.fetch_models_btn.setText("Fetching...")

        from ai_service import AIService
        try:
            models = AIService.get_gemini_models(key)
            if models:
                # Rank top Flash models for summary generation
                priority_chain = ", ".join(models[:6])
                self.gemini_summary_priority_input.setText(priority_chain)
                InfoBar.success(
                    title="Models Fetched Successfully",
                    content=f"Found {len(models)} active text models! Priority chain ranked with newest versions.",
                    isClosable=True,
                    duration=5000,
                    position=InfoBarPosition.TOP,
                    parent=self,
                )
            else:
                InfoBar.warning(title="No Models Found", content="No text models returned from API.", parent=self)
        except Exception as e:
            InfoBar.error(title="Fetch Failed", content=str(e), isClosable=True, duration=5000, parent=self)
        finally:
            self.fetch_models_btn.setEnabled(True)
            self.fetch_models_btn.setText("Fetch Live Models")

    def _on_ai_provider_changed(self):
        provider = self.ai_provider_combo.currentData() or "gemini"
        if provider == "gemini":
            self.gemini_widget.setVisible(True)
            self.ollama_widget.setVisible(False)
        else:
            self.gemini_widget.setVisible(False)
            self.ollama_widget.setVisible(True)

    def _run_ai_test(self):
        provider = self.ai_provider_combo.currentData() or "gemini"
        self.test_ai_btn.setEnabled(False)
        self.test_ai_status_label.setText("⏳ Testing AI connection...")

        from ai_service import AIService
        try:
            if provider == "gemini":
                key = self.gemini_key_input.text().strip()
                if not key:
                    self.test_ai_status_label.setText("⚠️ Enter Gemini API Key first.")
                    self.test_ai_btn.setEnabled(True)
                    return
                model = self.gemini_model_combo.currentText()
                test_resp = AIService.generate_study_notes_gemini("Hello test lecture.", key, model)
                if test_resp:
                    self.test_ai_status_label.setText("✅ Google Gemini connected successfully!")
                else:
                    self.test_ai_status_label.setText("❌ Empty response from Gemini.")
            else:
                host = self.ollama_host_input.text().strip() or "http://localhost:11434"
                models = AIService.get_ollama_models(host)
                if models:
                    self.test_ai_status_label.setText(f"✅ Ollama connected! ({len(models)} models available)")
                else:
                    self.test_ai_status_label.setText("⚠️ Connected to Ollama, but no models found.")
        except Exception as e:
            self.test_ai_status_label.setText(f"❌ {e}")
        finally:
            self.test_ai_btn.setEnabled(True)

    def _open_benchmark_dialog(self):
        dlg = BenchmarkDialog(self)
        dlg.settings_applied.connect(lambda _: self._load_values())
        dlg.exec()

    def _open_subject_manager_from_settings(self):
        from ui.subject_dialog import SubjectManagementDialog
        dlg = SubjectManagementDialog(self)
        dlg.exec()

    def _open_logs_file(self):
        from logger import open_log_file
        open_log_file()

    def _open_logs_directory(self):
        import subprocess
        from logger import LOGS_DIR
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(LOGS_DIR))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(LOGS_DIR)])
        else:
            subprocess.run(["xdg-open", str(LOGS_DIR)])

    def _check_for_updates_manually(self):
        self.check_updates_btn.setEnabled(False)
        self.update_status_label.setText("Checking GitHub for updates...")
        self._update_worker = CheckUpdateWorker(parent=self)
        self._update_worker.update_available.connect(self._on_update_available)
        self._update_worker.up_to_date.connect(self._on_up_to_date)
        self._update_worker.error_occurred.connect(self._on_update_error)
        self._update_worker.start()

    def _on_update_available(self, release_info: dict):
        self.check_updates_btn.setEnabled(True)
        self.update_status_label.setText(f"🚀 New version available: {release_info['tag_name']}!")
        dlg = UpdateDialog(release_info, self)
        dlg.exec()

    def _on_up_to_date(self):
        self.check_updates_btn.setEnabled(True)
        self.update_status_label.setText(f"✅ You are running the latest version (v{APP_VERSION}).")

    def _on_update_error(self, err_msg: str):
        self.check_updates_btn.setEnabled(True)
        self.update_status_label.setText(f"Could not check for updates ({err_msg}).")

    def _save_and_close(self):
        selected_device = "cuda" if "cuda" in self.device_combo.currentText().lower() else "cpu"

        new_settings = {
            "whisper_model": self.model_combo.currentText(),
            "device": selected_device,
            "compute_type": self.compute_combo.currentText(),
            "default_tags": self.tags_input.text().strip(),
            "video_watch_folder": self.video_folder_input.text().strip(),
            "delete_video_after_extraction": self.delete_video_cb.isChecked(),
            "auto_transcribe_video": self.auto_transcribe_video_cb.isChecked(),
            "audio_source": self.audio_source_combo.currentData(),
            "input_device_id": self.mic_device_combo.currentData(),
            "loopback_device_id": self.loopback_device_combo.currentData(),
            "enable_diarization": self.enable_diarization_cb.isChecked(),
            "num_speakers": self.num_speakers_combo.currentData(),
            "beam_size": self.beam_combo.currentData() or 1,
            "whisper_language": self.language_combo.currentData() or "en",
            "auto_check_updates": self.auto_check_updates_cb.isChecked(),
            "ai_provider": self.ai_provider_combo.currentData() or "gemini",
            "gemini_api_key": self.gemini_key_input.text().strip(),
            "gemini_model": self.gemini_model_combo.currentText(),
            "gemini_summary_priority": self.gemini_summary_priority_input.text().strip(),
            "ollama_host": self.ollama_host_input.text().strip(),
            "ollama_model": self.ollama_model_input.text().strip(),
            "ollama_api_key": self.ollama_key_input.text().strip(),
        }

        saved = config_manager.save(new_settings)
        if saved:
            self.settings_saved.emit()
            self.accept()
        else:
            InfoBar.error(
                title="Error Saving Settings",
                content="Failed to write configuration file to ~/.notion_transcribe/config.json",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self
            )
