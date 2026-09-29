"""Notion Push and Fetch Worker threads for NotionTranscribe.

Runs page creation, block chunk upload, and database page queries in background QThreads
to avoid UI freezes.
"""

from typing import Any, Dict, List, Optional
from PyQt6.QtCore import QThread, pyqtSignal

from notion_service import NotionService


class NotionPushWorker(QThread):
    """Background worker thread for uploading or appending transcripts to Notion."""

    status_changed = pyqtSignal(str)
    finished = pyqtSignal(str)          # Emits page URL on success
    error_occurred = pyqtSignal(str)    # Emits error description

    def __init__(
        self,
        token: str,
        database_id: str,
        title: str,
        segments: List[Dict[str, Any]],
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        target_page_id: Optional[str] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.token = token
        self.database_id = database_id
        self.title = title
        self.segments = segments
        self.tags = tags or []
        self.metadata = metadata or {}
        self.target_page_id = target_page_id

    def run(self):
        self.status_changed.emit("Connecting to Notion database...")
        try:
            service = NotionService(self.token, self.database_id)

            if self.target_page_id and self.target_page_id != "new":
                self.status_changed.emit("Appending transcript blocks to existing page...")
                success, message, page_url = service.append_transcript_to_page(
                    page_id=self.target_page_id,
                    segments=self.segments,
                    metadata=self.metadata,
                )
            else:
                self.status_changed.emit("Creating page and publishing transcript blocks...")
                success, message, page_url = service.create_transcript_page(
                    title=self.title,
                    segments=self.segments,
                    tags=self.tags,
                    metadata=self.metadata,
                )

            if success and page_url:
                self.status_changed.emit("Published to Notion successfully!")
                self.finished.emit(page_url)
            else:
                self.error_occurred.emit(message)
        except Exception as e:
            self.error_occurred.emit(f"Failed to push to Notion: {e}")


class NotionFetchPagesWorker(QThread):
    """Background worker thread for retrieving pages from Notion database."""

    status_changed = pyqtSignal(str)
    pages_fetched = pyqtSignal(list)    # Emits list of page dicts
    error_occurred = pyqtSignal(str)    # Emits error description

    def __init__(
        self,
        token: str,
        database_id: str,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.token = token
        self.database_id = database_id

    def run(self):
        try:
            service = NotionService(self.token, self.database_id)
            success, msg, pages = service.get_database_pages(filter_today=False)
            if success:
                self.pages_fetched.emit(pages)
            else:
                self.error_occurred.emit(msg)
        except Exception as e:
            self.error_occurred.emit(f"Failed to query database pages: {e}")
