"""Educational AI Study Assistant for NotionTranscribe.

Supports Google Gemini API and Local/Remote Ollama to transform raw lecture
transcripts into structured educational study guides, active recall flashcards,
and NotebookLM-ready source documents.
"""

import json
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request


EDUCATIONAL_PROMPT_TEMPLATE = """You are an expert academic tutor and lecture note synthesizer.
Your task is to convert the following spoken lecture/study transcript into a comprehensive, high-yield educational study guide.

TRANSCRIPT:
{transcript}

Please structure your response into the following clear Markdown sections:

# 📌 1. Lecture Overview & Core Thesis
Provide a 2-3 sentence high-level summary of the main subject and key takeaway of this lecture.

# 🧠 2. Key Concepts & Definitions
List every core concept, term, formula, or theory introduced. For each:
- **Term / Concept**: Definition explained clearly with any practical examples or analogies the instructor gave.

# 💡 3. Important Takeaways & Detailed Notes
Break down the main topics covered into logical bullet points. Emphasize cause-and-effect relationships and critical mechanisms.

# ❓ 4. Q&A & Instructor Clarifications
If questions were asked during the lecture, or if the speaker addressed common points of confusion:
- **Question / Confusion**:
- **Explanation**:

# 🎯 5. Active Recall & Exam Prep Flashcards
Generate 5 to 10 high-yield flashcard questions for self-testing. Use this exact format so they can be turned into toggleable study cards:
- Question: [Concept question]
  Answer: [Detailed clear answer]

# 📝 6. Homework, Deadlines & Exam Warnings
List any mentioned assignments, upcoming deadlines, exam hints, or specific topics the instructor emphasized to study. If none were mentioned, write "None mentioned."
"""

NOTEBOOKLM_HEADER_TEMPLATE = """# Source: {title}
- **Date**: {date}
- **Duration**: {duration}
- **Model / Diarization**: {model} ({speakers} speakers)
- **Tags**: {tags}

---

## 🎯 Executive Study Summary
{summary}

---

## 📜 Full Verbatim Transcript
{transcript}
"""


class AIService:
    """Manages AI requests to Google Gemini or Ollama."""

    @staticmethod
    def get_ollama_models(host: str = "http://localhost:11434") -> List[str]:
        """Fetch list of installed models from local Ollama instance."""
        url = f"{host.rstrip('/')}/api/tags"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NotionTranscribe"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return ["llama3.2", "qwen2.5", "mistral", "phi3"]

    @staticmethod
    def generate_study_notes_gemini(
        transcript_text: str,
        api_key: str,
        model: str = "gemini-1.5-flash",
    ) -> str:
        """Call Google Gemini API using native REST request (no external pip dependency required)."""
        if not api_key:
            raise ValueError("Gemini API key is required. Please add it in Settings.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        prompt = EDUCATIONAL_PROMPT_TEMPLATE.format(transcript=transcript_text)

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096,
            }
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                candidates = result.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
                raise ValueError("Gemini returned an empty response.")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini API Error ({e.code}): {err_body}")

    @staticmethod
    def generate_study_notes_ollama(
        transcript_text: str,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        api_key: Optional[str] = None,
    ) -> str:
        """Call Ollama generate or chat API."""
        url = f"{host.rstrip('/')}/api/generate"
        prompt = EDUCATIONAL_PROMPT_TEMPLATE.format(transcript=transcript_text)

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
            }
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("response", "")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama API Error ({e.code}): {err_body}")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Ollama at {host}: {e}")

    @staticmethod
    def format_for_notebooklm(
        title: str,
        date: str,
        duration: str,
        model: str,
        speakers: int,
        tags: str,
        summary: str,
        transcript: str,
    ) -> str:
        """Format the content specifically for NotebookLM ingestion."""
        return NOTEBOOKLM_HEADER_TEMPLATE.format(
            title=title,
            date=date,
            duration=duration,
            model=model,
            speakers=speakers,
            tags=tags,
            summary=summary or "Study notes not generated yet.",
            transcript=transcript,
        )
