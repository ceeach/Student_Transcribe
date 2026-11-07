"""Configuration manager for NotionTranscribe.

Handles reading and writing user configuration from ~/.notion_transcribe/config.json
with fallback to environment variables and .env.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

CONFIG_DIR = Path.home() / ".notion_transcribe"
CONFIG_FILE = CONFIG_DIR / "config.json"
VIDEO_INBOX_DIR = CONFIG_DIR / "video_inbox"

DEFAULT_CONFIG: Dict[str, Any] = {
    "notion_token": "",
    "database_id": "",
    "whisper_model": "base",
    "device": "cpu",
    "compute_type": "int8",
    "default_tags": "Transcription, Audio",
    "auto_open_notion": True,
    "video_watch_folder": str(VIDEO_INBOX_DIR),
    "auto_watch_video_folder": True,
    "delete_video_after_extraction": True,
    "auto_transcribe_video": True,
    "audio_source": "mic",  # "mic", "system", or "both"
    "input_device_id": None,  # int index or None for default
    "loopback_device_id": None,  # int index or None for default
    "enable_diarization": True,  # automatically diarize speakers
    "num_speakers": -1,  # -1 for auto-detect, or integer count (e.g. 2, 3, 4)
    "diarization_threshold": 0.5,  # clustering threshold (0.1 to 0.9)
    "beam_size": 1,  # 1 for fast greedy decoding (~2.5x faster), 5 for exhaustive search
    "whisper_language": "en",  # Default forced transcription language ("en", "auto", "de", "fr", "es", etc.)
    "auto_advance_slides": False,  # Suggestion mode by default; True for automatic slide transitions
    "notebooklm_drive_folder": "",  # Local Google Drive or synced cloud folder for NotebookLM
    "notebooklm_webhook_url": "",  # Optional HTTP POST webhook (Make/Zapier/custom)
    "auto_sync_notebooklm": False,  # Automatically sync lecture to NotebookLM on transcription completion
    "ai_provider": "gemini",
    "gemini_api_key": "",
    "gemini_model": "Gemini 3.5 Transcribe Live",
    "gemini_summary_priority": "gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash, gemini-2.5-flash, gemini-flash-latest",
    "gemini_live_enabled": True,  # Automatically run Gemini Live when recording while connected to internet
    "auto_check_updates": True,   # Automatically check for updates on application launch
}


def sanitize_database_id(raw_id: str) -> str:
    """Normalize Notion database ID from full URL, plain 32-hex, or hyphenated UUID.

    Notion database URLs look like:
    https://www.notion.so/workspace/8a93e970b8a34bc3a18a93e970b8a34b?v=...
    Or UUID:
    8a93e970-b8a3-4bc3-a18a-93e970b8a34b
    """
    if not raw_id:
        return ""

    cleaned = raw_id.strip()

    # Extract hex string if it's a URL
    url_match = re.search(r"notion\.so/(?:[^/]+/)?([0-9a-fA-F]{32})", cleaned)
    if url_match:
        cleaned = url_match.group(1)
    else:
        # Also check for hyphenated UUID inside URL
        uuid_match = re.search(
            r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})",
            cleaned,
        )
        if uuid_match:
            cleaned = uuid_match.group(1)

    # Strip existing dashes
    hex_only = cleaned.replace("-", "")
    if len(hex_only) == 32:
        # Convert to standard 8-4-4-4-12 UUID format expected by Notion API
        return f"{hex_only[0:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:32]}"

    return cleaned


class ConfigManager:
    """Singleton-style configuration manager for NotionTranscribe."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self._config: Dict[str, Any] = self._load()

    def _ensure_dir(self) -> None:
        """Ensure configuration and video inbox directories exist."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        VIDEO_INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, Any]:
        """Load configuration from JSON file or environment variables."""
        cfg = DEFAULT_CONFIG.copy()

        # Fallback to environment variables if available
        env_token = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
        if env_token:
            cfg["notion_token"] = env_token

        env_db = os.getenv("NOTION_DATABASE_ID")
        if env_db:
            cfg["database_id"] = sanitize_database_id(env_db)

        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        cfg.update(data)
            except Exception as e:
                print(f"[ConfigManager] Error reading config file {self.config_path}: {e}")

        # Sanitize database_id in memory
        if cfg.get("database_id"):
            cfg["database_id"] = sanitize_database_id(cfg["database_id"])

        return cfg

    def save(self, new_config: Optional[Dict[str, Any]] = None) -> bool:
        """Save current configuration to JSON file."""
        if new_config:
            self._config.update(new_config)

        if self._config.get("database_id"):
            self._config["database_id"] = sanitize_database_id(self._config["database_id"])

        try:
            self._ensure_dir()
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[ConfigManager] Error saving config to {self.config_path}: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._config[key] = value

    def to_dict(self) -> Dict[str, Any]:
        return self._config.copy()

    def is_notion_configured(self) -> bool:
        """Returns True if both Notion Token and Database ID are non-empty."""
        token = self.get("notion_token", "").strip()
        db_id = self.get("database_id", "").strip()
        return bool(token and db_id)


# Global instance
config_manager = ConfigManager()
