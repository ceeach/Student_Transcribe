# Student Transcribe 🎙️🎓

> **An all-in-one, intelligent academic lecture recording, transcription, and study studio for students and researchers.**

Student Transcribe is a modern, self-contained desktop application designed to capture lectures, webinars, and meetings. It seamlessly synchronizes audio with class presentation slides, transcribes speech in real time, organizes curriculum materials by academic subjects, and provides an in-app grounded "Ask Lecture AI" assistant.

---

## ✨ Features

- **⚡ Dual Speech Recognition Engines**:
  - **Google Gemini Multimodal Live**: Sub-second, streaming transcription via WebSockets with continuous audio streaming and automatic session rotation.
  - **Offline Faster-Whisper**: 100% private, on-device transcription with GPU CUDA and CPU quantization (int8/float16).
  - **Forced Language Locking**: Locks transcription to English (or other chosen target languages) to prevent language-hopping during accented speech.
  - **Speaker Diarization**: Separates and badges individual speakers (`Speaker 1`, `Speaker 2`, etc.) with one-click full-transcript speaker renaming.

- **📊 Class Slides Companion & Annotation Canvas**:
  - Import PDF slide decks or lecture handouts.
  - **Semantic Auto-Advance & Live Tracking**: Automatically detects what the professor is discussing and highlights or switches to the active slide.
  - **Canvas Annotation Suite**: Pen, highlighter, shape tools, and inline text notes.
  - **Zoom & Pan Controls**: Smooth mouse wheel and touchpad panning when zoomed in.

- **📚 Academic Subjects Hub**:
  - Organize recordings, videos, extracted transcripts, and AI study guides cleanly by course code (e.g. `CS101`, `MATH202`).
  - Set syllabus modules and session types (*Lecture, Lab, Seminar, Workshop*).
  - Allocate older recordings, videos, or transcripts to courses with a single click.

- **💬 Grounded "Ask Lecture AI" (Built-In NotebookLM Alternative)**:
  - Chat directly with your lecture transcripts, audio timestamps, and slides.
  - Built-in prompt chips: *💡 Key Takeaways*, *❓ 5-Question Quiz*, *🔍 Explain Concepts*, *📝 Action Items & Formulas*.
  - Powered by Gemini Flash or local Ollama models.

- **🎥 Video Inbox & Watch Folder Automation**:
  - Drop or auto-watch folders for Zoom recordings, lecture MP4s, MKVs, or MOVs.
  - Automatically extracts 16kHz audio tracks and runs transcription.

- **🔒 100% Local & Private**:
  - Transcripts, audio, and study notes are stored securely on your local computer (`~/.notion_transcribe/`). Zero mandatory cloud lock-in.

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.10 or 3.11**
- Microphone or WASAPI loopback speakers

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ceeach/Student_Transcribe.git
   cd Student_Transcribe
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate   # Windows
   # source venv/bin/activate # macOS/Linux
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the application**:
   ```bash
   python main.py
   ```

---

## 🛠️ Windows Installer Compilation

Student Transcribe includes an automated build pipeline using PyInstaller and Inno Setup:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1
```
The compiled installer will be saved to:
`output/StudentTranscribe-Setup-v1.4.3.exe`

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
