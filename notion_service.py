"""Notion API Service for NotionTranscribe.

Handles chunking, block formatting, database schema inspection,
and page creation using the official notion-client.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError, RequestTimeoutError
except ImportError:
    Client = None
    APIResponseError = Exception
    RequestTimeoutError = Exception


def chunk_text(text: str, max_chars: int = 1900) -> List[str]:
    """Split a string into chunks of at most `max_chars` characters.

    Attempts to split cleanly along sentence boundaries (.?! or newline),
    then whitespace, and only falls back to hard slicing if necessary.
    """
    if not text:
        return []

    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_chars:
        # Candidate slice
        candidate = remaining[:max_chars]

        # Try to find last sentence boundary (.!?) followed by space or newline
        sentence_break = -1
        for match in re.finditer(r"[.!?]\s+|\n+", candidate):
            sentence_break = match.end()

        if sentence_break > max_chars // 2:
            split_idx = sentence_break
        else:
            # Fall back to last space
            last_space = candidate.rfind(" ")
            if last_space > max_chars // 3:
                split_idx = last_space + 1
            else:
                # Fall back to hard boundary
                split_idx = max_chars

        chunk = remaining[:split_idx].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_idx:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


def format_timestamp(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS string."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class NotionService:
    """Service client for Notion database interactions and page creation."""

    def __init__(self, token: str, database_id: str):
        if not Client:
            raise RuntimeError(
                "The 'notion-client' library is not installed. Please run: pip install notion-client"
            )
        self.token = token.strip()
        self.database_id = database_id.strip()
        self.client = Client(auth=self.token)

    def _get_database_properties(self, db_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Retrieve database properties handling both legacy and modern data_sources schemas."""
        if not db_meta:
            try:
                db_meta = self.client.databases.retrieve(database_id=self.database_id)
            except Exception:
                db_meta = {}

        # 1. Direct properties on database object (legacy schema)
        properties = db_meta.get("properties", {})
        if properties:
            return properties

        # 2. Modern Notion API (data_sources)
        data_sources = db_meta.get("data_sources", [])
        if data_sources and hasattr(self.client, "data_sources"):
            for ds_entry in data_sources:
                ds_id = ds_entry.get("id")
                if ds_id:
                    try:
                        ds = self.client.data_sources.retrieve(data_source_id=ds_id)
                        ds_props = ds.get("properties", {})
                        if ds_props:
                            return ds_props
                    except Exception:
                        pass

        # 3. Fallback: inspect the properties of an existing page in the database
        try:
            results = []
            if data_sources and hasattr(self.client, "data_sources"):
                ds_id = data_sources[0].get("id")
                resp = self.client.data_sources.query(data_source_id=ds_id, page_size=1)
                results = resp.get("results", [])
            elif hasattr(self.client.databases, "query"):
                resp = self.client.databases.query(database_id=self.database_id, page_size=1)
                results = resp.get("results", [])

            if results:
                page_props = results[0].get("properties", {})
                if page_props:
                    return page_props
        except Exception:
            pass

        return {}

    def test_connection(self) -> Tuple[bool, str, Dict[str, Any]]:
        """Validate Notion connection and retrieve database metadata.

        Returns:
            Tuple of (success, message, schema_info)
        """
        if not self.token:
            return False, "Notion Integration Token is missing.", {}
        if not self.database_id:
            return False, "Notion Database ID is missing.", {}

        try:
            db = self.client.databases.retrieve(database_id=self.database_id)
            title_prop = None
            properties = self._get_database_properties(db)

            for prop_name, prop_meta in properties.items():
                if prop_meta.get("type") == "title":
                    title_prop = prop_name
                    break

            db_title_arr = db.get("title", [])
            db_title = "".join(item.get("plain_text", "") for item in db_title_arr) or "Untitled Database"

            return True, f"Successfully connected to '{db_title}'", {
                "title": db_title,
                "title_property": title_prop or "Name",
                "properties": properties,
            }
        except APIResponseError as e:
            msg = self._humanize_api_error(e)
            return False, msg, {}
        except Exception as e:
            return False, f"Unexpected error connecting to Notion: {e}", {}

    def _humanize_api_error(self, error: Any) -> str:
        """Translate Notion API errors into actionable user instructions."""
        code = getattr(error, "code", "")
        message = str(error)

        if code == "unauthorized" or "unauthorized" in message.lower():
            return (
                "Invalid Notion Integration Token. Please check that your token "
                "starts with 'secret_' and has been copied correctly."
            )
        if code == "object_not_found" or "Could not find database" in message:
            return (
                "Database not found or access denied. Ensure:\n"
                "1. The Database ID is correct.\n"
                "2. You have shared the database with your integration in Notion:\n"
                "   Click '...' at top right of your database -> Connections -> Add your integration."
            )
        if code == "restricted_resource":
            return (
                "Access restricted. Please verify your Notion integration has read/write permissions."
            )
        if code == "validation_error":
            return f"Notion Validation Error: {message}"

        return f"Notion API error: {message}"

    def build_paragraph_blocks(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert whisper segments into Notion paragraph blocks with timestamps, speakers, and chunking.

        Each segment is formatted as:
        [Speaker X] [HH:MM:SS - HH:MM:SS] Segment text here...
        If formatted text exceeds 1,900 chars, it is cleanly split into multiple paragraph blocks.
        """
        blocks: List[Dict[str, Any]] = []
        SPEAKER_COLORS = ["blue", "green", "orange", "purple", "pink", "red"]

        for seg in segments:
            start_str = format_timestamp(seg.get("start", 0))
            end_str = format_timestamp(seg.get("end", 0))
            text = seg.get("text", "").strip()
            speaker = seg.get("speaker", "")
            spk_id = seg.get("speaker_id", 0)

            if not text:
                continue

            full_line = f"[{start_str} - {end_str}] {text}"
            chunks = chunk_text(full_line, max_chars=1900)

            for idx, chunk in enumerate(chunks):
                rich_text_items = []
                if idx == 0:
                    # 1. Speaker badge if available
                    if speaker:
                        spk_color = SPEAKER_COLORS[spk_id % len(SPEAKER_COLORS)]
                        rich_text_items.append({
                            "type": "text",
                            "text": {"content": f"[{speaker}] "},
                            "annotations": {"bold": True, "color": spk_color},
                        })

                    # 2. Timestamp part
                    if chunk.startswith("[") and "]" in chunk:
                        ts_part, rest = chunk.split("]", 1)
                        rich_text_items.append({
                            "type": "text",
                            "text": {"content": ts_part + "]"},
                            "annotations": {"bold": True, "color": "gray"},
                        })
                        if rest:
                            rich_text_items.append({
                                "type": "text",
                                "text": {"content": rest},
                            })
                    else:
                        rich_text_items.append({
                            "type": "text",
                            "text": {"content": chunk},
                        })
                else:
                    rich_text_items.append({
                        "type": "text",
                        "text": {"content": chunk},
                    })

                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": rich_text_items
                    }
                })

        return blocks

    def create_transcript_page(
        self,
        title: str,
        segments: List[Dict[str, Any]],
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """Create a new page in the Notion database containing the transcript.

        Args:
            title: Page title
            segments: List of transcribed segments with 'start', 'end', and 'text'
            tags: List of tag strings to attach to multi-select property
            metadata: Additional metadata (model, duration, audio source file)

        Returns:
            Tuple of (success, message, page_url)
        """
        if not self.token or not self.database_id:
            return False, "Notion credentials are not configured.", None

        # 1. Fetch database properties to match schema safely
        try:
            db_meta = self.client.databases.retrieve(database_id=self.database_id)
            db_props = self._get_database_properties(db_meta)
        except APIResponseError as e:
            return False, self._humanize_api_error(e), None
        except Exception as e:
            return False, f"Could not retrieve Notion database: {e}", None

        # Identify title property name
        title_prop_name = None
        for name, prop in db_props.items():
            if prop.get("type") == "title":
                title_prop_name = name
                break

        # Fallback to candidate title names if type wasn't explicitly found
        if not title_prop_name:
            for candidate in ["Session", "Title", "Name", "Topic"]:
                if candidate in db_props:
                    title_prop_name = candidate
                    break
        if not title_prop_name:
            title_prop_name = "Name"

        # Build Properties
        properties: Dict[str, Any] = {
            title_prop_name: {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        }

        # Date / Created property detection
        # Check if database has a date property named 'Created' or 'Date'
        now_iso = datetime.now(timezone.utc).isoformat()
        for candidate_date in ["Created", "Date", "Recorded", "Timestamp"]:
            if candidate_date in db_props and db_props[candidate_date].get("type") == "date":
                properties[candidate_date] = {"date": {"start": now_iso}}
                break

        # Multi-select / Select property detection for tags
        if tags:
            clean_tags = [t.strip() for t in tags if t.strip()]
            if clean_tags:
                for candidate_tag in ["Tags", "Category", "Categories", "Type", "Topic"]:
                    if candidate_tag in db_props:
                        prop_type = db_props[candidate_tag].get("type")
                        if prop_type == "multi_select":
                            properties[candidate_tag] = {
                                "multi_select": [{"name": t} for t in clean_tags]
                            }
                            break
                        elif prop_type == "select":
                            properties[candidate_tag] = {
                                "select": {"name": clean_tags[0]}
                            }
                            break

        # 2. Build Page Children Blocks
        children_blocks: List[Dict[str, Any]] = []

        # Optional metadata callout
        if metadata:
            meta_lines = []
            if "source" in metadata:
                meta_lines.append(f"📁 Source: {metadata['source']}")
            if "duration" in metadata:
                meta_lines.append(f"⏱ Duration: {metadata['duration']}")
            if "model" in metadata:
                meta_lines.append(f"🎙 Whisper Model: {metadata['model']}")
            distinct_speakers = list(dict.fromkeys(s["speaker"] for s in segments if s.get("speaker")))
            if distinct_speakers:
                meta_lines.append(f"👥 Speakers ({len(distinct_speakers)}): {', '.join(distinct_speakers)}")
            meta_lines.append(f"📅 Transcribed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            children_blocks.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "🎙"},
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": "\n".join(meta_lines)},
                        }
                    ],
                },
            })

        # Heading 2: "Transcript"
        children_blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": "Transcript"},
                    }
                ]
            },
        })

        # Divider
        children_blocks.append({"object": "block", "type": "divider", "divider": {}})

        # Paragraph blocks from segments
        paragraph_blocks = self.build_paragraph_blocks(segments)
        if not paragraph_blocks:
            # Empty transcript placeholder
            paragraph_blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "(No speech detected or empty audio)"}}]
                },
            })

        children_blocks.extend(paragraph_blocks)

        # 3. Create Page with Batching Support
        # Notion allows max 100 children in pages.create
        initial_children = children_blocks[:100]
        remaining_children = children_blocks[100:]

        try:
            created_page = self.client.pages.create(
                parent={"database_id": self.database_id},
                properties=properties,
                children=initial_children,
            )
            page_id = created_page.get("id")
            page_url = created_page.get("url", f"https://notion.so/{page_id.replace('-', '')}")

            # Append remaining children in batches of 100
            while remaining_children:
                batch = remaining_children[:100]
                remaining_children = remaining_children[100:]
                self.client.blocks.children.append(
                    block_id=page_id,
                    children=batch,
                )

            return True, f"Successfully published to Notion!", page_url

        except APIResponseError as e:
            return False, self._humanize_api_error(e), None
        except RequestTimeoutError:
            return False, "Notion API request timed out. Please check your network connection.", None
        except Exception as e:
            return False, f"Failed to push to Notion: {e}", None

    def get_database_pages(self, filter_today: bool = False) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Query and return pages in the database, with option to filter to pages created today.

        Returns:
            Tuple of (success, message, pages_list)
        """
        if not self.token or not self.database_id:
            return False, "Notion credentials are not configured.", []

        try:
            db_meta = self.client.databases.retrieve(database_id=self.database_id)
            results = []

            # Check if database has data_sources (Notion API v2024+)
            if db_meta.get("data_sources"):
                ds_id = db_meta["data_sources"][0]["id"]
                resp = self.client.data_sources.query(data_source_id=ds_id, page_size=100)
                results = resp.get("results", [])
            elif hasattr(self.client.databases, "query"):
                resp = self.client.databases.query(database_id=self.database_id, page_size=100)
                results = resp.get("results", [])
            else:
                resp = self.client.search(
                    filter={"value": "page", "property": "object"},
                    page_size=100,
                )
                results = resp.get("results", [])

            today_local = datetime.now().strftime("%Y-%m-%d")
            today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            pages = []
            for p in results:
                if p.get("in_trash") or p.get("archived"):
                    continue

                page_id = p.get("id", "")
                title = ""
                for prop in p.get("properties", {}).values():
                    if prop.get("type") == "title":
                        title = "".join(t.get("plain_text", "") for t in prop.get("title", [])).strip()
                        break
                if not title:
                    title = "Untitled Page"

                created_time = p.get("created_time", "")
                is_today = False
                created_display = ""
                if created_time:
                    date_part = created_time[:10]
                    is_today = (date_part == today_local or date_part == today_utc)
                    time_part = created_time[11:16] if len(created_time) >= 16 else ""
                    if is_today:
                        created_display = f"Today {time_part}" if time_part else "Today"
                    else:
                        created_display = f"{date_part} {time_part}".strip()

                page_url = p.get("url", f"https://notion.so/{page_id.replace('-', '')}")

                page_item = {
                    "id": page_id,
                    "title": title,
                    "created_time": created_time,
                    "created_display": created_display,
                    "is_today": is_today,
                    "url": page_url,
                }

                if filter_today and not is_today:
                    continue

                pages.append(page_item)

            return True, f"Found {len(pages)} pages", pages

        except APIResponseError as e:
            return False, self._humanize_api_error(e), []
        except Exception as e:
            return False, f"Failed to retrieve pages: {e}", []

    def append_transcript_to_page(
        self,
        page_id: str,
        segments: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """Append transcript blocks into an existing Notion page."""
        if not self.token:
            return False, "Notion token not configured.", None
        if not page_id:
            return False, "Target page ID was not specified.", None

        page_url = f"https://notion.so/{page_id.replace('-', '')}"

        blocks: List[Dict[str, Any]] = []

        # Divider
        blocks.append({"object": "block", "type": "divider", "divider": {}})

        # Heading 2 with timestamp or custom section title
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        section_heading = None
        if metadata:
            section_heading = metadata.get("section_title")
        if not section_heading:
            section_heading = f"Appended Transcript - {now_str}"

        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": section_heading},
                    }
                ]
            },
        })

        # Metadata Callout
        if metadata:
            meta_lines = []
            if "source" in metadata:
                meta_lines.append(f"📁 Source: {metadata['source']}")
            if "duration" in metadata:
                meta_lines.append(f"⏱ Duration: {metadata['duration']}")
            if "model" in metadata:
                meta_lines.append(f"🎙 Whisper Model: {metadata['model']}")
            distinct_speakers = list(dict.fromkeys(s["speaker"] for s in segments if s.get("speaker")))
            if distinct_speakers:
                meta_lines.append(f"👥 Speakers ({len(distinct_speakers)}): {', '.join(distinct_speakers)}")
            meta_lines.append(f"📅 Appended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            blocks.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "🎙"},
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": "\n".join(meta_lines)},
                        }
                    ],
                },
            })

        # Paragraph blocks
        paragraph_blocks = self.build_paragraph_blocks(segments)
        if not paragraph_blocks:
            paragraph_blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "(No speech detected or empty audio)"}}]
                },
            })
        blocks.extend(paragraph_blocks)

        # Append in batches of 100
        try:
            while blocks:
                batch = blocks[:100]
                blocks = blocks[100:]
                self.client.blocks.children.append(
                    block_id=page_id,
                    children=batch,
                )

            return True, "Successfully appended transcript to Notion page!", page_url

        except APIResponseError as e:
            return False, self._humanize_api_error(e), None
        except RequestTimeoutError:
            return False, "Notion API request timed out.", None
        except Exception as e:
            return False, f"Failed to append to page: {e}", None
