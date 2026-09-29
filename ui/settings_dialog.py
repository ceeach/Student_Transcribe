"""Settings Dialog for NotionTranscribe.

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

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    SwitchButton,
    TitleLabel,
)

from audio_recorder import get_audio_input_devices, get_audio_loopback_devices
from config_manager import config_manager, sanitize_database_id
from diarization import is_diarization_available
from notion_service import NotionService
from transcriber import is_cuda_available
from ui.benchmark_dialog import BenchmarkDialog


class ConnectionTestWorker(QThread):
    """Background thread to test Notion credentials without freezing UI."""

    finished = pyqtSignal(bool, str, dict)

    def __init__(self, token: str, database_id: str, parent=None):
        super().__init__(parent)
        self.token = token
        self.database_id = database_id

    def run(self):
        service = NotionService(token=self.token, database_id=self.database_id)
        success, msg, schema = service.test_connection()
        self.finished.emit(success, msg, schema)


class SettingsDialog(QDialog):
    """Modal dialog for application settings."""

    settings_saved = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("NotionTranscribe Settings")
        self.resize(640, 800)
        self.setModal(True)

        self._test_worker: Optional[ConnectionTestWorker] = None
        self._init_ui()
        self._load_values()

    def _create_form_row(self, label_text: str, widget: QWidget, tooltip: str = "") -> QHBoxLayout:
        layout = QHBoxLayout()
        label = BodyLabel(label_text, self)
        if tooltip:
            label.setToolTip(tooltip)
            widget.setToolTip(tooltip)
        layout.addWidget(label, 1)
        layout.addWidget(widget, 2)
        return layout

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(18)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Title
        header = TitleLabel("Settings & Configuration", self)
        main_layout.addWidget(header)

        sub = BodyLabel("Configure Notion API credentials and Whisper model inference options.", self)
        main_layout.addWidget(sub)

        # 1. Notion API Settings Group
        notion_card = CardWidget(self)
        notion_layout = QVBoxLayout(notion_card)
        notion_layout.setSpacing(12)
        
        notion_title = SubtitleLabel("Notion API Integration", self)
        notion_layout.addWidget(notion_title)

        # Notion Token row
        self.token_input = PasswordLineEdit(self)
        self.token_input.setPlaceholderText("secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.token_input.setToolTip("Notion Internal Integration Token starting with 'secret_'")
        notion_layout.addLayout(self._create_form_row("Integration Token:", self.token_input))

        # Database ID row
        self.db_input = LineEdit(self)
        self.db_input.setPlaceholderText("8a93e970b8a34bc3a18a93e970b8a34b or full URL")
        tooltip = "Copy the 32-character ID from your Notion database URL\n(or paste the full database URL)"
        notion_layout.addLayout(self._create_form_row("Database ID / URL:", self.db_input, tooltip))

        # Test Connection button
        test_row = QHBoxLayout()
        self.test_btn = PushButton("⚡ Test Connection", self)
        self.test_btn.clicked.connect(self._run_connection_test)
        self.test_status_label = BodyLabel("", self)
        self.test_status_label.setWordWrap(True)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_status_label, 1)
        notion_layout.addLayout(test_row)

        main_layout.addWidget(notion_card)

        # 2. Whisper Model Settings Group
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
        self.gemini_model_combo.addItems(["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"])
        gemini_layout.addLayout(self._create_form_row("Gemini Model:", self.gemini_model_combo, "gemini-1.5-flash is fast with 1M context. gemini-1.5-pro has deep conceptual reasoning."))
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

        # Dialog Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        
        self.cancel_btn = PushButton("Cancel", self)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.save_btn = PrimaryPushButton("Save", self)
        self.save_btn.clicked.connect(self._save_and_close)
        button_layout.addWidget(self.save_btn)
        
        main_layout.addLayout(button_layout)

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
        self.token_input.setText(config_manager.get("notion_token", ""))
        self.db_input.setText(config_manager.get("database_id", ""))

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
        saved_gemini_model = config_manager.get("gemini_model", "gemini-1.5-flash")
        idx = self.gemini_model_combo.findText(saved_gemini_model)
        if idx >= 0:
            self.gemini_model_combo.setCurrentIndex(idx)

        self.ollama_host_input.setText(config_manager.get("ollama_host", "http://localhost:11434"))
        self.ollama_model_input.setText(config_manager.get("ollama_model", "llama3.2"))
        self.ollama_key_input.setText(config_manager.get("ollama_api_key", ""))

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

    def _run_connection_test(self):
        token = self.token_input.text().strip()
        db_raw = self.db_input.text().strip()
        db_id = sanitize_database_id(db_raw)

        if not token:
            self.test_status_label.setText("⚠️ Enter a Notion Token first.")
            return
        if not db_id:
            self.test_status_label.setText("⚠️ Enter a Database ID first.")
            return

        self.test_btn.setEnabled(False)
        self.test_status_label.setText("⏳ Testing connection...")

        self._test_worker = ConnectionTestWorker(token, db_id, self)
        self._test_worker.finished.connect(self._on_test_finished)
        self._test_worker.start()

    def _on_test_finished(self, success: bool, message: str, schema: dict):
        self.test_btn.setEnabled(True)
        if success:
            db_title = schema.get("title", "Database")
            self.test_status_label.setText(f"✅ Connected to '{db_title}'!")
        else:
            self.test_status_label.setText(f"❌ {message}")

    def _save_and_close(self):
        token = self.token_input.text().strip()
        db_raw = self.db_input.text().strip()
        db_id = sanitize_database_id(db_raw)

        selected_device = "cuda" if "cuda" in self.device_combo.currentText().lower() else "cpu"

        new_settings = {
            "notion_token": token,
            "database_id": db_id,
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
            "ai_provider": self.ai_provider_combo.currentData() or "gemini",
            "gemini_api_key": self.gemini_key_input.text().strip(),
            "gemini_model": self.gemini_model_combo.currentText(),
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
