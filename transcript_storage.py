"""Transcript Storage module for NotionTranscribe.

Handles persistent local storage of transcripts alongside audio recordings
and in a centralized archive. Allows instantaneous reloading of previous
transcripts without re-transcription, format conversions (TXT, SRT, VTT, Markdown, JSON),
and tracking of Notion sync status.
"""

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


RECORDINGS_DIR = Path.home() / ".notion_transcribe" / "recordings"
TRANSCRIPTS_DIR = Path.home() / ".notion_transcribe" / "transcripts"


def ensure_directories():
    """Ensure storage directories exist."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp format: HH:MM:SS,mmm."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    """Format seconds into WebVTT timestamp format: HH:MM:SS.mmm."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


def get_transcript_paths(audio_path: str) -> List[Path]:
    """Return candidate JSON file paths where a transcript for audio_path might exist."""
    audio_p = Path(audio_path)
    stem = audio_p.stem
    candidates: List[Path] = []

    # 1. Primary: next to audio file with .json or .transcript.json
    candidates.append(audio_p.parent / f"{stem}.json")
    candidates.append(audio_p.parent / f"{stem}.transcript.json")

    # 2. In centralized TRANSCRIPTS_DIR
    candidates.append(TRANSCRIPTS_DIR / f"{stem}.json")

    # 3. If audio is not in RECORDINGS_DIR, also check RECORDINGS_DIR for matching stem
    candidates.append(RECORDINGS_DIR / f"{stem}.json")

    # Deduplicate preserving order
    seen = set()
    result = []
    for c in candidates:
        abs_c = c.resolve() if c.is_absolute() else c
        if str(abs_c) not in seen:
            seen.add(str(abs_c))
            result.append(c)

    return result


def find_transcript_file(audio_path: str) -> Optional[Path]:
    """Find the existing transcript JSON file for a given audio file path."""
    for candidate in get_transcript_paths(audio_path):
        if candidate.is_file():
            return candidate
    return None


def has_transcript(audio_path: str) -> bool:
    """Check if a saved transcript exists for the given audio path."""
    return find_transcript_file(audio_path) is not None


def load_transcript(audio_path: str) -> Optional[Dict[str, Any]]:
    """Load the saved transcript dictionary for the given audio path.

    Returns:
        Dict with transcript data, or None if no transcript found or failed to parse.
    """
    found_path = find_transcript_file(audio_path)
    if not found_path:
        return None

    try:
        with open(found_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[TranscriptStorage] Error loading transcript from {found_path}: {e}")

    return None


def generate_formatted_txt(
    audio_path: str,
    segments: List[Dict[str, Any]],
    full_text: str,
    model: str = "",
    speakers: Optional[List[str]] = None,
) -> str:
    """Generate human-readable plain text with header, timestamps, and speaker labels."""
    audio_fname = Path(audio_path).name if audio_path else "Recording"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    unique_speakers = speakers or []
    if not unique_speakers and segments:
        unique_speakers = list(dict.fromkeys(s.get("speaker", "Speaker 1") for s in segments))

    header_lines = [
        "================================================================================",
        "  NOTIONTRANSCRIBE - AUDIO TRANSCRIPT",
        f"  File: {audio_fname}",
        f"  Date: {now_str} | Model: {model or 'whisper'} | Speakers: {len(unique_speakers) or 1}",
        "================================================================================",
        "",
    ]

    body_lines: List[str] = []
    for seg in segments:
        ts = seg.get("timestamp_str", "")
        speaker = seg.get("speaker")
        text = seg.get("text", "").strip()

        if speaker:
            body_lines.append(f"{ts} [{speaker}]:")
            body_lines.append(f"  {text}\n")
        else:
            body_lines.append(f"{ts} {text}\n")

    return "\n".join(header_lines) + "\n" + "\n".join(body_lines).strip() + "\n"


def save_transcript(
    audio_path: str,
    segments: List[Dict[str, Any]],
    full_text: str,
    model: str = "medium",
    device: str = "cuda",
    compute_type: str = "float16",
    duration_seconds: float = 0.0,
    language: Optional[str] = None,
    notion_info: Optional[Dict[str, Any]] = None,
    study_notes: Optional[str] = None,
) -> Tuple[str, str]:
    """Save transcript data to JSON and human-readable TXT files.

    Saves alongside the audio file (if directory is writable) and also
    into ~/.notion_transcribe/transcripts/ as a persistent central copy.

    Returns:
        Tuple of (json_path, txt_path) pointing to the saved files.
    """
    ensure_directories()
    audio_p = Path(audio_path)
    stem = audio_p.stem

    # Extract speaker list
    unique_speakers = list(dict.fromkeys(s.get("speaker", "Speaker 1") for s in segments if "speaker" in s))
    word_count = sum(len(s.get("text", "").split()) for s in segments)

    now_iso = datetime.now().isoformat()

    transcript_data: Dict[str, Any] = {
        "version": "1.0",
        "audio_file": str(audio_p.resolve()) if audio_p.exists() else str(audio_path),
        "audio_filename": audio_p.name,
        "audio_stem": stem,
        "created_at": now_iso,
        "updated_at": now_iso,
        "model": model,
        "device": device,
        "compute_type": compute_type,
        "language": language or "auto",
        "duration_seconds": float(duration_seconds),
        "segment_count": len(segments),
        "word_count": word_count,
        "speaker_count": len(unique_speakers),
        "speakers": unique_speakers,
        "full_text": full_text,
        "study_notes": study_notes or "",
        "segments": segments,
        "notion": notion_info or {
            "pushed": False,
            "page_url": None,
            "page_id": None,
            "pushed_at": None,
        },
    }

    txt_content = generate_formatted_txt(
        audio_path=audio_path,
        segments=segments,
        full_text=full_text,
        model=model,
        speakers=unique_speakers,
    )

    saved_json_path = ""
    saved_txt_path = ""

    # 1. Try saving alongside the audio file
    try:
        target_dir = audio_p.parent
        if target_dir.exists() and os.access(target_dir, os.W_OK):
            target_json = target_dir / f"{stem}.json"
            target_txt = target_dir / f"{stem}.txt"

            with open(target_json, "w", encoding="utf-8") as f:
                json.dump(transcript_data, f, indent=2, ensure_ascii=False)

            with open(target_txt, "w", encoding="utf-8") as f:
                f.write(txt_content)

            saved_json_path = str(target_json)
            saved_txt_path = str(target_txt)
    except Exception as e:
        print(f"[TranscriptStorage] Could not write next to audio file: {e}")

    # 2. Always write a backup to TRANSCRIPTS_DIR
    try:
        central_json = TRANSCRIPTS_DIR / f"{stem}.json"
        central_txt = TRANSCRIPTS_DIR / f"{stem}.txt"

        with open(central_json, "w", encoding="utf-8") as f:
            json.dump(transcript_data, f, indent=2, ensure_ascii=False)

        with open(central_txt, "w", encoding="utf-8") as f:
            f.write(txt_content)

        if not saved_json_path:
            saved_json_path = str(central_json)
            saved_txt_path = str(central_txt)
    except Exception as e:
        print(f"[TranscriptStorage] Could not write to TRANSCRIPTS_DIR: {e}")

    return saved_json_path, saved_txt_path


def update_transcript_notion_info(
    audio_path: str,
    page_url: str,
    page_id: Optional[str] = None,
) -> bool:
    """Update saved transcript files with Notion push success details."""
    data = load_transcript(audio_path)
    if not data:
        return False

    data["updated_at"] = datetime.now().isoformat()
    data["notion"] = {
        "pushed": True,
        "page_url": page_url,
        "page_id": page_id,
        "pushed_at": datetime.now().isoformat(),
    }

    # Write back to all existing candidates
    updated = False
    for candidate in get_transcript_paths(audio_path):
        if candidate.is_file():
            try:
                with open(candidate, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                updated = True
            except Exception as e:
                print(f"[TranscriptStorage] Failed to update {candidate}: {e}")

    return updated


def delete_transcript(audio_path: str) -> bool:
    """Delete all local transcript files (.json and .txt) associated with audio_path."""
    deleted_any = False
    audio_p = Path(audio_path)
    stem = audio_p.stem

    for candidate in get_transcript_paths(audio_path):
        if candidate.is_file():
            try:
                candidate.unlink(missing_ok=True)
                deleted_any = True
            except Exception:
                pass

        # Also remove matching .txt
        txt_candidate = candidate.with_suffix(".txt")
        if txt_candidate.is_file():
            try:
                txt_candidate.unlink(missing_ok=True)
                deleted_any = True
            except Exception:
                pass

    return deleted_any


def export_to_srt(segments: List[Dict[str, Any]], output_path: str) -> None:
    """Export segments to SubRip (.srt) subtitle format."""
    lines: List[str] = []
    idx = 1
    for seg in segments:
        start_sec = float(seg.get("start", 0.0))
        end_sec = float(seg.get("end", start_sec + 2.0))
        speaker = seg.get("speaker")
        text = seg.get("text", "").strip()
        speaker_prefix = f"[{speaker}] " if speaker else ""

        start_srt = format_srt_timestamp(start_sec)
        end_srt = format_srt_timestamp(end_sec)

        lines.append(str(idx))
        lines.append(f"{start_srt} --> {end_srt}")
        lines.append(f"{speaker_prefix}{text}")
        lines.append("")
        idx += 1

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def export_to_vtt(segments: List[Dict[str, Any]], output_path: str) -> None:
    """Export segments to WebVTT (.vtt) format."""
    lines: List[str] = ["WEBVTT", ""]
    for seg in segments:
        start_sec = float(seg.get("start", 0.0))
        end_sec = float(seg.get("end", start_sec + 2.0))
        speaker = seg.get("speaker")
        text = seg.get("text", "").strip()
        speaker_prefix = f"<v {speaker}>" if speaker else ""
        speaker_suffix = f"</v>" if speaker else ""

        start_vtt = format_vtt_timestamp(start_sec)
        end_vtt = format_vtt_timestamp(end_sec)

        lines.append(f"{start_vtt} --> {end_vtt}")
        lines.append(f"{speaker_prefix}{text}{speaker_suffix}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def export_to_markdown(
    audio_path: str,
    segments: List[Dict[str, Any]],
    full_text: str,
    output_path: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Export transcript to rich GitHub-flavored Markdown."""
    meta = metadata or {}
    audio_fname = Path(audio_path).name if audio_path else "Audio Recording"
    title = meta.get("title", f"Transcript: {Path(audio_fname).stem}")
    date_str = meta.get("date", datetime.now().strftime("%Y-%m-%d %H:%M"))
    model = meta.get("model", "Whisper")

    unique_speakers = list(dict.fromkeys(s.get("speaker", "Speaker 1") for s in segments if "speaker" in s))
    speaker_summary = ", ".join(unique_speakers) if unique_speakers else "1 Speaker"

    md_lines = [
        f"# {title}",
        "",
        "| Attribute | Value |",
        "|---|---|",
        f"| **Audio File** | `{audio_fname}` |",
        f"| **Date** | {date_str} |",
        f"| **Model** | {model} |",
        f"| **Speakers** | {speaker_summary} |",
        f"| **Total Segments** | {len(segments)} |",
        "",
        "---",
        "",
        "## Transcript",
        "",
    ]

    for seg in segments:
        ts = seg.get("timestamp_str", "")
        speaker = seg.get("speaker", "")
        text = seg.get("text", "").strip()

        if speaker:
            md_lines.append(f"> **{speaker}** `{ts}`  \n> {text}\n")
        else:
            md_lines.append(f"`{ts}` {text}\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))


def export_to_notebooklm(
    audio_path: str,
    segments: List[Dict[str, Any]],
    full_text: str,
    output_path: str,
    study_notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Export transcript and study guide specifically structured for NotebookLM source ingestion."""
    meta = metadata or {}
    audio_fname = Path(audio_path).name if audio_path else "Lecture Recording"
    title = meta.get("title", f"Lecture: {Path(audio_fname).stem}")
    date_str = meta.get("date", datetime.now().strftime("%Y-%m-%d %H:%M"))
    model = meta.get("model", "Whisper")
    tags = meta.get("tags", "Education, Lecture")
    duration = meta.get("duration", "--:--")

    unique_speakers = list(dict.fromkeys(s.get("speaker", "Speaker 1") for s in segments if "speaker" in s))
    speaker_count = len(unique_speakers) or 1

    from ai_service import AIService
    content = AIService.format_for_notebooklm(
        title=title,
        date=date_str,
        duration=duration,
        model=model,
        speakers=speaker_count,
        tags=tags,
        summary=study_notes,
        transcript=full_text,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

