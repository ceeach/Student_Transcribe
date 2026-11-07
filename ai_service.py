"""Educational AI Study Assistant for NotionTranscribe.

Supports Google Gemini API and Local/Remote Ollama to transform raw lecture
transcripts into structured educational study guides, active recall flashcards,
and NotebookLM-ready source documents.
"""

import json
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

from logger import get_logger

logger = get_logger("AIService")


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
    def get_gemini_models(api_key: str) -> List[str]:
        """Fetch available text generation models from Google Gemini API and sort them by version descending."""
        fallback_models = [
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-2.5-flash",
            "gemini-flash-latest",
        ]
        if not api_key:
            return fallback_models

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key.strip()}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NotionTranscribe"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            summary_models = []
            for m in data.get("models", []):
                name = m.get("name", "").replace("models/", "").strip()
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" not in methods:
                    continue
                # Filter out specialized non-text models
                if any(k in name for k in ("tts", "image", "embedding", "bidi", "transcribe-live", "banana", "clip", "robotics", "computer-use")):
                    continue
                summary_models.append(name)

            import re

            def extract_sort_key(name):
                is_flash = 2 if ('flash' in name and 'lite' not in name) else (1 if 'flash' in name else 0)
                match = re.search(r'gemini-(\d+(?:\.\d+)?)', name)
                ver = float(match.group(1)) if match else (2.0 if 'latest' in name else 0.0)
                return (is_flash, ver)

            sorted_models = sorted(summary_models, key=extract_sort_key, reverse=True)
            return sorted_models if sorted_models else fallback_models
        except Exception as e:
            logger.warning(f"Error fetching Gemini models from API: {e}")
            return fallback_models

    @staticmethod
    def query_gemini_with_fallback(
        prompt: str,
        api_key: str,
        model: str = "gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash, gemini-2.5-flash, gemini-flash-latest",
        status_callback=None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Call Google Gemini API using an ordered fallback priority chain."""
        if not api_key:
            raise ValueError("Gemini API key is required. Please add it in Settings.")

        if isinstance(model, list):
            raw_chain = model
        else:
            raw_chain = [m.strip() for m in str(model).split(",") if m.strip()]

        if not raw_chain:
            raw_chain = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-latest"]

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }
        data_bytes = json.dumps(payload).encode("utf-8")

        errors = []
        for candidate in raw_chain:
            candidate_slug = candidate.lower().replace(" ", "-")
            if "transcribe" in candidate_slug:
                candidate_slug = "gemini-3.6-flash"

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{candidate_slug}:generateContent?key={api_key}"
            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            msg = f"Connecting to Gemini ({candidate_slug})..."
            if status_callback:
                status_callback(msg)
            logger.info(msg)

            try:
                with urllib.request.urlopen(req, timeout=18) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    candidates = result.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            text = parts[0].get("text", "")
                            if text:
                                logger.info(f"Successfully generated response using '{candidate_slug}'!")
                                return text
                    raise ValueError(f"Empty response from {candidate_slug}")

            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                logger.warning(f"Gemini model '{candidate_slug}' failed (HTTP {e.code}): {err_body[:180]}")
                errors.append(f"{candidate_slug} (HTTP {e.code})")
                if e.code in (429, 404, 503, 500, 400):
                    continue
                raise RuntimeError(f"Gemini API Error on {candidate_slug} ({e.code}): {err_body}")

            except Exception as e:
                logger.warning(f"Gemini model '{candidate_slug}' exception: {e}")
                errors.append(f"{candidate_slug} ({e})")
                continue

        raise RuntimeError(f"All models in priority chain failed. Attempted: {', '.join(errors)}")

    @staticmethod
    def generate_study_notes_gemini(
        transcript_text: str,
        api_key: str,
        model: str = "gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash, gemini-2.5-flash, gemini-flash-latest",
        status_callback=None,
    ) -> str:
        """Call Google Gemini API using an ordered fallback chain for summary generation."""
        prompt = EDUCATIONAL_PROMPT_TEMPLATE.format(transcript=transcript_text)
        return AIService.query_gemini_with_fallback(
            prompt=prompt,
            api_key=api_key,
            model=model,
            status_callback=status_callback,
        )

    @staticmethod
    def query_ollama(
        prompt: str,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        api_key: Optional[str] = None,
        temperature: float = 0.3,
    ) -> str:
        """Call Ollama generate or chat API."""
        url = f"{host.rstrip('/')}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
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
            raise RuntimeError(f"Ollama Error (HTTP {e.code}): {err_body}")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Ollama at {host}: {e}")

    @staticmethod
    def generate_study_notes_ollama(
        transcript_text: str,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        api_key: Optional[str] = None,
    ) -> str:
        prompt = EDUCATIONAL_PROMPT_TEMPLATE.format(transcript=transcript_text)
        return AIService.query_ollama(prompt=prompt, host=host, model=model, api_key=api_key)

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
        slide_context: str = "",
        course_jargon: str = "",
    ) -> str:
        """Format the content specifically for NotebookLM ingestion."""
        base = NOTEBOOKLM_HEADER_TEMPLATE.format(
            title=title,
            date=date,
            duration=duration,
            model=model,
            speakers=speakers,
            tags=tags,
            summary=summary or "Study notes not generated yet.",
            transcript=transcript,
        )
        extras = []
        if course_jargon and course_jargon.strip():
            extras.append(f"## 🔤 Course Vocabulary & Key Terms (from Slides)\n{course_jargon.strip()}")
        if slide_context and slide_context.strip():
            extras.append(f"## 📊 Extracted Lecture Slides Content\n{slide_context.strip()}")
        if extras:
            return base.rstrip() + "\n\n---\n\n" + "\n\n---\n\n".join(extras) + "\n"
        return base
