"""Background worker for asynchronous AI study notes generation."""

from typing import Optional
from PyQt6.QtCore import QThread, pyqtSignal

from ai_service import AIService
from config_manager import config_manager


class AIStudyNotesWorker(QThread):
    """Worker thread that runs Gemini or Ollama notes generation in the background."""

    status_changed = pyqtSignal(str)
    notes_ready = pyqtSignal(str)  # Generated markdown notes
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        transcript_text: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.transcript_text = transcript_text
        self.provider = provider or config_manager.get("ai_provider", "gemini")
        self.model = model

    def run(self):
        try:
            if not self.transcript_text or not self.transcript_text.strip():
                raise ValueError("Transcript is empty. Nothing to analyze.")

            if self.provider == "gemini":
                api_key = config_manager.get("gemini_api_key", "").strip()
                if not api_key:
                    raise ValueError(
                        "Google Gemini API key not found.\n"
                        "Please go to Settings > AI Study Assistant and paste your Gemini API key."
                    )
                model_name = self.model or config_manager.get("gemini_model", "gemini-1.5-flash")
                self.status_changed.emit(f"Synthesizing study guide with Google Gemini ({model_name})...")
                notes = AIService.generate_study_notes_gemini(
                    transcript_text=self.transcript_text,
                    api_key=api_key,
                    model=model_name,
                )
            elif self.provider == "ollama":
                host = config_manager.get("ollama_host", "http://localhost:11434")
                model_name = self.model or config_manager.get("ollama_model", "llama3.2")
                api_key = config_manager.get("ollama_api_key", "")
                self.status_changed.emit(f"Synthesizing study guide with Local Ollama ({model_name})...")
                notes = AIService.generate_study_notes_ollama(
                    transcript_text=self.transcript_text,
                    host=host,
                    model=model_name,
                    api_key=api_key if api_key else None,
                )
            else:
                raise ValueError(f"Unknown AI provider: {self.provider}")

            self.notes_ready.emit(notes)

        except Exception as e:
            self.error_occurred.emit(str(e))
