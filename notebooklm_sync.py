"""NotebookLM 1-Click Sync & Webhook Integration for Transcribe Notion.

Syncs lecture transcripts, AI study guides, and slide timelines to:
1. Local Google Drive folder (for instant automatic ingestion in Google NotebookLM)
2. Custom Webhook URL (Make.com, Zapier, Google Apps Script, Pipedream, n8n)
"""

from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

from ai_service import AIService
from logger import get_logger

logger = get_logger("NotebookLMSync")


def sanitize_filename(title: str) -> str:
    """Clean string for safe cross-platform file naming."""
    clean = re.sub(r'[\\/*?:"<>|]', "", title)
    clean = re.sub(r"\s+", "_", clean.strip())
    return clean[:80] or "Lecture"


def sync_to_notebooklm(
    title: str,
    transcript_text: str,
    study_notes: str = "",
    segments: Optional[List[Dict[str, Any]]] = None,
    slide_timeline: Optional[List[Dict[str, Any]]] = None,
    jargon_terms: Optional[List[str]] = None,
    drive_folder: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> Tuple[bool, str, List[str]]:
    """Sync lecture content to Google Drive folder and/or HTTP Webhook.

    Returns:
        Tuple of (success_bool, status_message, list_of_actions_taken)
    """
    if not transcript_text and not study_notes:
        return False, "No transcript or study notes available to sync.", []

    actions: List[str] = []
    errors: List[str] = []

    # 1. Prepare structured NotebookLM Markdown
    from transcriber import format_timestamp
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    jargon_str = ", ".join(jargon_terms) if jargon_terms else ""
    slide_summary = ""
    if slide_timeline:
        timeline_lines = ["### Lecture Slide Timeline & Key Moments"]
        for entry in slide_timeline:
            s_num = entry.get("slide_idx", 0) + 1
            s_title = entry.get("title", f"Slide {s_num}")
            st = float(entry.get("start_time", 0.0))
            timeline_lines.append(f"- **Slide {s_num}: {s_title}** at `[{format_timestamp(st)}]`")
        slide_summary = "\n".join(timeline_lines)

    speakers_count = len(set(s.get("speaker", "Speaker 1") for s in (segments or []))) if segments else 1

    formatted_md = AIService.format_for_notebooklm(
        title=title or "Class Lecture",
        date=date_str,
        duration="",
        model="Whisper",
        speakers=speakers_count,
        tags="Education, Lecture",
        summary=study_notes,
        transcript=transcript_text,
        slide_context=slide_summary,
        course_jargon=jargon_str,
    )

    # 2. Sync to Local Google Drive / Cloud Sync Folder if configured
    if drive_folder and os.path.exists(drive_folder):
        try:
            folder_path = Path(drive_folder)
            folder_path.mkdir(parents=True, exist_ok=True)

            date_str = datetime.now().strftime("%Y-%m-%d")
            file_name = f"{sanitize_filename(title)}_{date_str}_NotebookLM.md"
            target_file = folder_path / file_name

            with open(target_file, "w", encoding="utf-8") as f:
                f.write(formatted_md)

            actions.append(f"Saved file to Google Drive: {target_file.name}")
            logger.info(f"Saved NotebookLM source to {target_file}")
        except Exception as e:
            err = f"Failed to save to Drive folder: {e}"
            logger.error(err)
            errors.append(err)

    # 3. Dispatch to Webhook URL if configured
    if webhook_url and webhook_url.strip().startswith(("http://", "https://")):
        try:
            payload = {
                "title": title or "Class Lecture",
                "date": datetime.now().isoformat(),
                "formatted_markdown": formatted_md,
                "summary": study_notes[:1000] if study_notes else "",
                "transcript": transcript_text,
                "slide_timeline": slide_timeline or [],
                "jargon_terms": jargon_terms or [],
                "source": "Transcribe Notion v1.2",
            }

            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                webhook_url.strip(),
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "TranscribeNotion-NotebookLM/1.2",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=12) as response:
                status_code = response.getcode()
                if 200 <= status_code < 300:
                    actions.append(f"Dispatched to Webhook (HTTP {status_code})")
                    logger.info(f"NotebookLM webhook dispatched successfully: {status_code}")
                else:
                    err = f"Webhook returned HTTP {status_code}"
                    logger.warning(err)
                    errors.append(err)
        except Exception as e:
            err = f"Webhook dispatch error: {e}"
            logger.error(err)
            errors.append(err)

    if not drive_folder and not webhook_url:
        return False, "Neither Google Drive folder nor Webhook URL is configured.", []

    if actions and not errors:
        return True, "Synced successfully: " + "; ".join(actions), actions
    elif actions and errors:
        return True, f"Partial sync ({'; '.join(actions)}). Errors: {'; '.join(errors)}", actions
    else:
        return False, f"Sync failed: {'; '.join(errors)}", []


def test_notebooklm_sync(
    drive_folder: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> Tuple[bool, str]:
    """Test folder write access and/or webhook endpoint connectivity."""
    results: List[str] = []
    has_error = False

    if drive_folder:
        clean_dir = drive_folder.strip()
        if not os.path.exists(clean_dir):
            try:
                os.makedirs(clean_dir, exist_ok=True)
            except Exception as e:
                return False, f"Folder does not exist and could not be created: {e}"

        test_file = os.path.join(clean_dir, ".transcribe_notion_test.tmp")
        try:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("test")
            os.remove(test_file)
            results.append("Google Drive / Sync folder is writable")
        except Exception as e:
            has_error = True
            results.append(f"Folder permission error: {e}")

    if webhook_url:
        clean_url = webhook_url.strip()
        if not clean_url.startswith(("http://", "https://")):
            has_error = True
            results.append("Webhook URL must start with http:// or https://")
        else:
            try:
                test_payload = json.dumps({"test": True, "ping": "Transcribe Notion"}).encode("utf-8")
                req = urllib.request.Request(
                    clean_url,
                    data=test_payload,
                    headers={"Content-Type": "application/json", "User-Agent": "TranscribeNotion-Test/1.2"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as res:
                    results.append(f"Webhook responded with HTTP {res.getcode()}")
            except Exception as e:
                has_error = True
                results.append(f"Webhook test failed: {e}")

    if not drive_folder and not webhook_url:
        return False, "Please specify a Drive folder path or Webhook URL."

    success = not has_error
    return success, " | ".join(results)
