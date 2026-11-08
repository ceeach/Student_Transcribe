"""In-App Lecture Q&A and Chat Assistant for Student Transcribe (Phase 2).

Provides an interactive 'Ask Lecture' chat grounded in the current lecture's
transcript, study notes, and syllabus metadata using Gemini or Ollama.
"""

from typing import Optional, List, Dict, Any
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
)
from qfluentwidgets import (
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    StrongBodyLabel,
    LineEdit,
    TextEdit,
    PushButton,
    PrimaryPushButton,
    TransparentPushButton,
    CardWidget,
    InfoBar,
    InfoBarPosition,
    ProgressBar,
    FluentIcon as FIF,
)

from ai_service import AIService
from config_manager import config_manager
from logger import get_logger

logger = get_logger("LectureChat")


class LectureChatWorker(QThread):
    """Background thread to query Gemini or Ollama with grounded lecture context."""

    response_ready = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        question: str,
        transcript_text: str,
        study_notes: str = "",
        subject: str = "",
        session_type: str = "",
        modules: Optional[List[str]] = None,
        filename: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.question = question
        self.transcript_text = transcript_text
        self.study_notes = study_notes
        self.subject = subject
        self.session_type = session_type
        self.modules = modules or []
        self.filename = filename

    def run(self):
        try:
            provider = config_manager.get("ai_provider", "gemini")
            self.status_changed.emit("Grounding question in lecture context...")

            context_header = (
                f"Course / Subject: {self.subject or 'General'}\n"
                f"Session Type: {self.session_type or 'Lecture'}\n"
                f"Modules Covered: {', '.join(self.modules) if self.modules else 'General Topics'}\n"
                f"Lecture Recording: {self.filename or 'Current Session'}\n"
            )

            # Build grounded conversational prompt
            prompt = (
                f"You are an expert academic tutor and interactive lecture assistant for Student Transcribe.\n"
                f"Your task is to answer the student's question accurately, clearly, and thoroughly based on the provided lecture transcript and study notes.\n\n"
                f"GUIDELINES:\n"
                f"1. Ground your answer in the lecture transcript and study guide below.\n"
                f"2. Cite timestamps (e.g. [14:20]) or speaker names when referencing specific points from the transcript.\n"
                f"3. If the user asks for a quiz or test, provide questions with an answer key and explanations.\n"
                f"4. Format your output cleanly in GitHub Flavored Markdown (bullet points, bold headers, code snippets, or tables).\n\n"
                f"=== LECTURE METADATA ===\n"
                f"{context_header}\n"
                f"=== STUDY NOTES (IF GENERATED) ===\n"
                f"{self.study_notes[:4000] if self.study_notes else 'None yet.'}\n\n"
                f"=== LECTURE TRANSCRIPT ===\n"
                f"{self.transcript_text[:120000]}\n"
                f"===========================\n\n"
                f"STUDENT QUESTION: {self.question}\n\n"
                f"EXPERT TUTOR ANSWER:"
            )

            if provider == "gemini":
                api_key = config_manager.get("gemini_api_key", "").strip()
                if not api_key:
                    raise ValueError("Gemini API key is required. Please configure it in Settings.")

                priority_models = config_manager.get(
                    "gemini_summary_priority",
                    "gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash, gemini-2.5-flash, gemini-flash-latest"
                )
                self.status_changed.emit("Consulting Gemini AI tutor...")
                answer = AIService.query_gemini_with_fallback(
                    prompt=prompt,
                    api_key=api_key,
                    model=priority_models,
                    status_callback=lambda msg: self.status_changed.emit(msg),
                    temperature=0.4,
                )
            else:
                host = config_manager.get("ollama_host", "http://localhost:11434")
                model = config_manager.get("ollama_model", "llama3.2")
                api_key = config_manager.get("ollama_api_key", "")
                self.status_changed.emit(f"Consulting Ollama ({model})...")
                answer = AIService.query_ollama(
                    prompt=prompt,
                    host=host,
                    model=model,
                    api_key=api_key if api_key else None,
                    temperature=0.4,
                )

            self.response_ready.emit(answer)

        except Exception as e:
            logger.error(f"Error answering lecture question: {e}")
            self.error_occurred.emit(str(e))


class LectureChatWidget(QWidget):
    """Interactive Chat UI embedded in the Student Transcribe interface."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._transcript_text: str = ""
        self._study_notes: str = ""
        self._subject: str = ""
        self._session_type: str = ""
        self._modules: List[str] = []
        self._filename: str = ""
        self._worker: Optional[LectureChatWorker] = None

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. Header Toolbar
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(StrongBodyLabel("💬 Ask Lecture AI (In-App Q&A)", self))
        self.context_label = CaptionLabel("Ready to answer questions about the current lecture.", self)
        title_col.addWidget(self.context_label)
        top_row.addLayout(title_col, 1)

        self.copy_btn = TransparentPushButton("Copy All", self)
        self.copy_btn.setIcon(FIF.COPY)
        self.copy_btn.setToolTip("Copy the entire conversation history to clipboard")
        self.copy_btn.clicked.connect(self._copy_chat_history)
        top_row.addWidget(self.copy_btn)

        self.clear_btn = TransparentPushButton("Clear Chat", self)
        self.clear_btn.setIcon(FIF.DELETE)
        self.clear_btn.setToolTip("Clear conversation history")
        self.clear_btn.clicked.connect(self._clear_chat)
        top_row.addWidget(self.clear_btn)

        layout.addLayout(top_row)

        # 2. Suggested Prompt Chips (Quick Actions)
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)

        prompts = [
            ("💡 Key Takeaways", "Summarize the top 5 most important takeaways and concepts from this lecture."),
            ("❓ 5-Question Quiz", "Generate a 5-question active-recall quiz based on this lecture, with answers and explanations."),
            ("🔍 Explain Main Concepts", "What are the core technical concepts introduced in this lecture? Explain them simply."),
            ("📝 Action Items / Formulas", "List all formulas, definitions, and action items discussed in this session."),
        ]

        for label, text in prompts:
            btn = PushButton(label, self)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, t=text: self._send_quick_prompt(t))
            chips_row.addWidget(btn)

        chips_row.addStretch(1)
        layout.addLayout(chips_row)

        # 3. Chat History Display
        self.chat_display = TextEdit(self)
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText(
            "Welcome to Ask Lecture AI!\n\n"
            "• Ask anything about what was said in the lecture.\n"
            "• Click any quick action above or type a custom question below.\n"
            "• Answers are directly grounded in your transcript and saved notes with timestamps."
        )
        layout.addWidget(self.chat_display, 1)

        # 4. Status Bar / Progress
        self.status_bar = CaptionLabel("", self)
        layout.addWidget(self.status_bar)

        # 5. Input Bar
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.input_edit = LineEdit(self)
        self.input_edit.setPlaceholderText("Ask a question about this lecture... (Press Enter to send)")
        self.input_edit.setClearButtonEnabled(True)
        self.input_edit.returnPressed.connect(self._on_send_clicked)
        input_row.addWidget(self.input_edit, 1)

        self.send_btn = PrimaryPushButton("Send", self)
        self.send_btn.setIcon(FIF.SEND)
        self.send_btn.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self.send_btn)

        layout.addLayout(input_row)

    def set_lecture_context(
        self,
        transcript_text: str,
        study_notes: str = "",
        subject: str = "",
        session_type: str = "",
        modules: Optional[List[str]] = None,
        filename: str = "",
    ):
        """Update active lecture context when a recording or transcript is loaded."""
        self._transcript_text = transcript_text
        self._study_notes = study_notes
        self._subject = subject
        self._session_type = session_type
        self._modules = modules or []
        self._filename = filename

        words = len(transcript_text.split()) if transcript_text else 0
        self.context_label.setText(
            f"Grounded in: {filename or 'Active Recording'} ({words:,} words)  •  Subject: {subject or 'General'}"
        )

    def _send_quick_prompt(self, text: str):
        self.input_edit.setText(text)
        self._on_send_clicked()

    def _on_send_clicked(self):
        question = self.input_edit.text().strip()
        if not question:
            return

        if not self._transcript_text:
            InfoBar.warning(
                title="No Transcript Available",
                content="Please transcribe audio or select a past recording before asking questions.",
                isClosable=True,
                duration=4000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            return

        self.input_edit.clear()
        self._append_user_message(question)

        self.send_btn.setEnabled(False)
        self.input_edit.setEnabled(False)
        self.status_bar.setText("Thinking...")

        self._worker = LectureChatWorker(
            question=question,
            transcript_text=self._transcript_text,
            study_notes=self._study_notes,
            subject=self._subject,
            session_type=self._session_type,
            modules=self._modules,
            filename=self._filename,
            parent=self,
        )
        self._worker.status_changed.connect(lambda s: self.status_bar.setText(s))
        self._worker.response_ready.connect(self._on_response_ready)
        self._worker.error_occurred.connect(self._on_error_occurred)
        self._worker.start()

    def _on_response_ready(self, answer: str):
        self._append_ai_message(answer)
        self.send_btn.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.input_edit.setFocus()
        self.status_bar.setText("Ready")

    def _on_error_occurred(self, err_msg: str):
        self._append_error_message(err_msg)
        self.send_btn.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.status_bar.setText("Error occurred")
        InfoBar.error(
            title="Chat Error",
            content=f"Could not generate answer: {err_msg[:200]}",
            isClosable=True,
            duration=6000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

    def _append_user_message(self, text: str):
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        html = (
            f'<div style="margin-top: 14px; margin-bottom: 8px;">'
            f'<span style="color: #38bdf8; font-weight: bold; font-size: 13px;">👤 You:</span>'
            f'<div style="margin-top: 4px; padding: 10px 14px; background: rgba(56, 189, 248, 0.12); '
            f'border-left: 3px solid #38bdf8; border-radius: 6px; color: #f8fafc; font-size: 13px;">'
            f'{escaped}'
            f'</div></div>'
        )
        self.chat_display.append(html)
        self._scroll_to_bottom()

    def _append_ai_message(self, text: str):
        # Format markdown-style bold, bullet points, headers into clean HTML
        formatted = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        # Replace basic markdown
        import re
        formatted = re.sub(r'### (.*?)\n', r'<h4 style="color: #38bdf8; margin: 8px 0 4px 0;">\1</h4>', formatted)
        formatted = re.sub(r'## (.*?)\n', r'<h3 style="color: #38bdf8; margin: 10px 0 4px 0;">\1</h3>', formatted)
        formatted = re.sub(r'# (.*?)\n', r'<h2 style="color: #38bdf8; margin: 12px 0 6px 0;">\1</h2>', formatted)
        formatted = re.sub(r'\*\*(.*?)\*\*', r'<b style="color: #f1f5f9;">\1</b>', formatted)
        formatted = re.sub(r'\*(.*?)\*', r'<i>\1</i>', formatted)
        formatted = re.sub(r'^\s*[-*]\s+(.*?)$', r'<li style="margin-bottom: 3px;">\1</li>', formatted, flags=re.MULTILINE)
        formatted = formatted.replace("\n", "<br>")

        html = (
            f'<div style="margin-top: 10px; margin-bottom: 16px;">'
            f'<span style="color: #10b981; font-weight: bold; font-size: 13px;">✨ Lecture AI:</span>'
            f'<div style="margin-top: 4px; padding: 12px 16px; background: rgba(30, 41, 59, 0.8); '
            f'border-left: 3px solid #10b981; border-radius: 6px; color: #e2e8f0; font-size: 13px; line-height: 1.5;">'
            f'{formatted}'
            f'</div></div>'
        )
        self.chat_display.append(html)
        self._scroll_to_bottom()

    def _append_error_message(self, err_text: str):
        html = (
            f'<div style="margin-top: 8px; margin-bottom: 12px; padding: 8px 12px; '
            f'background: rgba(239, 68, 68, 0.15); border-left: 3px solid #ef4444; border-radius: 6px; '
            f'color: #fca5a5; font-size: 12px;">'
            f'⚠️ {err_text}'
            f'</div>'
        )
        self.chat_display.append(html)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        sb = self.chat_display.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _clear_chat(self):
        self.chat_display.clear()
        self.status_bar.setText("Chat cleared.")

    def _copy_chat_history(self):
        from PyQt6.QtWidgets import QApplication
        text = self.chat_display.toPlainText().strip()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
                InfoBar.success(
                    title="Copied",
                    content="Chat history copied to clipboard.",
                    duration=3000,
                    parent=self,
                )
