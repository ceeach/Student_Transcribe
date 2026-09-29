"""Recordings List Widget for NotionTranscribe.

Displays an in-app list/table of past audio recordings saved in
~/.notion_transcribe/recordings/ with date, duration, size, and one-click
loading for transcription.
"""

from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

try:
    import soundfile as sf
except ImportError:
    sf = None

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
)

from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    SubtitleLabel,
    CaptionLabel,
    TableWidget,
    MessageBox
)

from audio_recorder import RECORDINGS_DIR
from transcript_storage import (
    delete_transcript,
    has_transcript,
    load_transcript,
)
from ui.drop_zone import format_file_size


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class RecordingsListWidget(QWidget):
    """Widget displaying a browsable table of previous recordings."""

    recording_selected = pyqtSignal(str)  # Emits file path to load

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Top Toolbar
        top_bar = QHBoxLayout()
        title_label = SubtitleLabel("Recorded Audio Archive", self)
        top_bar.addWidget(title_label, 1)

        self.refresh_btn = PushButton("🔄 Refresh", self)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        top_bar.addWidget(self.refresh_btn)

        self.open_folder_btn = PushButton("📂 Open Folder", self)
        self.open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_folder_btn.clicked.connect(self._open_folder)
        top_bar.addWidget(self.open_folder_btn)

        layout.addLayout(top_bar)

        # Recordings Table
        self.table = TableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Recording Name", "Date Recorded", "Duration", "File Size", "Transcript Status"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        # Bottom Action Bar
        bottom_bar = QHBoxLayout()
        self.count_label = CaptionLabel("0 recordings found", self)
        bottom_bar.addWidget(self.count_label, 1)

        self.play_btn = PushButton("▶ Play Audio", self)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._play_selected)
        bottom_bar.addWidget(self.play_btn)

        self.view_transcript_btn = PushButton("📄 View Text (.txt)", self)
        self.view_transcript_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_transcript_btn.setEnabled(False)
        self.view_transcript_btn.clicked.connect(self._view_transcript)
        bottom_bar.addWidget(self.view_transcript_btn)

        self.delete_btn = PushButton("🗑 Delete", self)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        bottom_bar.addWidget(self.delete_btn)

        self.load_btn = PrimaryPushButton("⚡ Load for Transcription", self)
        self.load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self._load_selected)
        bottom_bar.addWidget(self.load_btn)

        layout.addLayout(bottom_bar)

    def refresh(self):
        """Scan ~/.notion_transcribe/recordings/ and populate table."""
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.table.setRowCount(0)

        extensions = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
        files = []
        try:
            for p in RECORDINGS_DIR.iterdir():
                if p.is_file() and p.suffix.lower() in extensions:
                    files.append(p)
        except Exception as e:
            print(f"[RecordingsList] Error reading recordings: {e}")

        # Sort newest first
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        self.table.setRowCount(len(files))
        for row, fpath in enumerate(files):
            stat = fpath.stat()
            dt_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            size_str = format_file_size(stat.st_size)

            # Estimate duration
            duration_str = "--:--"
            if sf is not None and fpath.suffix.lower() == ".wav":
                try:
                    info = sf.info(str(fpath))
                    if info.duration:
                        duration_str = format_duration(info.duration)
                except Exception:
                    pass

            item_name = QTableWidgetItem(fpath.name)
            item_name.setData(Qt.ItemDataRole.UserRole, str(fpath))

            item_date = QTableWidgetItem(dt_str)
            item_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_duration = QTableWidgetItem(duration_str)
            item_duration.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_size = QTableWidgetItem(size_str)
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Check transcript status
            tr_exists = has_transcript(str(fpath))
            if tr_exists:
                t_data = load_transcript(str(fpath))
                if t_data:
                    wc = t_data.get("word_count", 0)
                    spk = t_data.get("speaker_count", 1)
                    pushed = t_data.get("notion", {}).get("pushed", False)
                    if pushed:
                        item_tr = QTableWidgetItem(f"✅ Synced ({wc}w)")
                        item_tr.setForeground(QColor("#10b981"))
                    else:
                        item_tr = QTableWidgetItem(f"✅ Saved ({wc}w)")
                        item_tr.setForeground(QColor("#38bdf8"))
                    item_tr.setToolTip(
                        f"Transcript saved locally:\n"
                        f"Words: {wc} | Speakers: {spk} | Model: {t_data.get('model', 'Whisper')}\n"
                        f"Double-click to load without re-transcribing."
                    )
                else:
                    item_tr = QTableWidgetItem("✅ Saved")
                    item_tr.setForeground(QColor("#38bdf8"))
            else:
                item_tr = QTableWidgetItem("⏳ Pending")
                item_tr.setForeground(QColor("#64748b"))
                item_tr.setToolTip("Audio has not been transcribed yet.")
            item_tr.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_date)
            self.table.setItem(row, 2, item_duration)
            self.table.setItem(row, 3, item_size)
            self.table.setItem(row, 4, item_tr)

        self.count_label.setText(f"{len(files)} recording(s) in archive")
        self._on_selection_changed()

    def _get_selected_path(self) -> Optional[str]:
        current_row = self.table.currentRow()
        if current_row >= 0:
            item = self.table.item(current_row, 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _on_selection_changed(self):
        has_sel = self.table.currentRow() >= 0
        path = self._get_selected_path() if has_sel else None
        has_tr = bool(path and has_transcript(path))

        self.play_btn.setEnabled(has_sel)
        self.delete_btn.setEnabled(has_sel)
        self.load_btn.setEnabled(has_sel)
        self.view_transcript_btn.setEnabled(has_tr)

        if has_tr:
            self.load_btn.setText("⚡ Load Recording & Transcript")
        else:
            self.load_btn.setText("⚡ Load for Transcription")

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        self._load_selected()

    def _load_selected(self):
        path = self._get_selected_path()
        if path and os.path.exists(path):
            self.recording_selected.emit(path)

    def _view_transcript(self):
        """Open the human-readable .txt transcript or load into app."""
        path = self._get_selected_path()
        if not path or not os.path.exists(path):
            return

        audio_p = Path(path)
        txt_path = audio_p.parent / f"{audio_p.stem}.txt"
        if not txt_path.exists():
            from transcript_storage import TRANSCRIPTS_DIR
            txt_path = TRANSCRIPTS_DIR / f"{audio_p.stem}.txt"

        if txt_path.exists():
            if sys.platform == "win32":
                os.startfile(str(txt_path))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(txt_path)])
            else:
                subprocess.run(["xdg-open", str(txt_path)])
        else:
            # Fallback to loading into the main app view
            self._load_selected()

    def _play_selected(self):
        path = self._get_selected_path()
        if path and os.path.exists(path):
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])

    def _delete_selected(self):
        path = self._get_selected_path()
        if not path or not os.path.exists(path):
            return

        fname = os.path.basename(path)
        w = MessageBox(
            "Delete Recording",
            f"Are you sure you want to permanently delete:\n{fname}\n(This will also remove any saved local transcripts)?",
            self
        )
        w.yesButton.setText('Delete')
        if w.exec():
            try:
                os.remove(path)
                delete_transcript(path)
                self.refresh()
            except Exception as e:
                err_w = MessageBox("Error", f"Failed to delete recording: {e}", self)
                err_w.exec()

    def _open_folder(self):
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(RECORDINGS_DIR))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(RECORDINGS_DIR)])
        else:
            subprocess.run(["xdg-open", str(RECORDINGS_DIR)])
