"""Recordings List Widget for NotionTranscribe.

Displays an in-app list/table of past audio recordings saved in
~/.notion_transcribe/recordings/ with date, duration, size, transcript status,
and a dedicated interactive notes editor panel for recording annotations.
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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    IconWidget,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    TransparentPushButton,
    SubtitleLabel,
    TableWidget,
    TextEdit,
)

from audio_recorder import RECORDINGS_DIR
from logger import get_logger, open_log_file
from subject_manager import SubjectManager
from transcript_storage import (
    delete_transcript,
    get_recording_academic_info,
    get_recording_notes,
    has_transcript,
    load_transcript,
    save_recording_notes,
    update_recording_academic_info,
)
from ui.drop_zone import format_file_size

logger = get_logger("RecordingsList")


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class RecordingsListWidget(QWidget):
    """Widget displaying a browsable table of previous recordings with side notes panel."""

    recording_selected = pyqtSignal(str)  # Emits file path to load
    chat_with_recording = pyqtSignal(str)  # Emits file path to open in Lecture Chat

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_selected_path: Optional[str] = None
        self._is_populating_notes = False

        # Debounced auto-save timer for notes
        self._notes_autosave_timer = QTimer(self)
        self._notes_autosave_timer.setSingleShot(True)
        self._notes_autosave_timer.setInterval(800)  # 800ms debounce
        self._notes_autosave_timer.timeout.connect(self._save_current_notes)

        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Top Toolbar
        top_bar = QHBoxLayout()
        title_label = SubtitleLabel("Recorded Audio Archive & History", self)
        top_bar.addWidget(title_label)

        self.search_input = LineEdit(self)
        self.search_input.setPlaceholderText("Search across filenames, transcripts & notes...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)
        top_bar.addWidget(self.search_input, 1)

        self.view_logs_btn = PushButton("View App Logs", self)
        self.view_logs_btn.setIcon(FIF.DOCUMENT)
        self.view_logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_logs_btn.setToolTip("Open diagnostic log file (useful for troubleshooting crashes or errors)")
        self.view_logs_btn.clicked.connect(self._open_logs)
        top_bar.addWidget(self.view_logs_btn)

        self.refresh_btn = PushButton("Refresh", self)
        self.refresh_btn.setIcon(FIF.SYNC)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        top_bar.addWidget(self.refresh_btn)

        self.open_folder_btn = PushButton("Open Folder", self)
        self.open_folder_btn.setIcon(FIF.FOLDER)
        self.open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_folder_btn.clicked.connect(self._open_folder)
        top_bar.addWidget(self.open_folder_btn)

        layout.addLayout(top_bar)

        # Filter Toolbar: Academic Subject & Session Type
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        filter_bar.addWidget(CaptionLabel("Subject Filter:", self))
        self.subject_filter_combo = ComboBox(self)
        self.subject_filter_combo.addItem("All Subjects")
        self.subject_filter_combo.currentIndexChanged.connect(lambda: self._apply_search_filter(self.search_input.text()))
        filter_bar.addWidget(self.subject_filter_combo)

        filter_bar.addWidget(CaptionLabel("Session Type:", self))
        self.type_filter_combo = ComboBox(self)
        self.type_filter_combo.addItems(["All Types", "📖 Theory Lecture", "✏️ Exercise / Problem Set", "🔬 Lab / Workshop"])
        self.type_filter_combo.currentIndexChanged.connect(lambda: self._apply_search_filter(self.search_input.text()))
        filter_bar.addWidget(self.type_filter_combo)

        filter_bar.addStretch(1)
        self.library_stats_label = CaptionLabel("📚 0 lectures in archive", self)
        filter_bar.addWidget(self.library_stats_label)
        layout.addLayout(filter_bar)

        # Horizontal Splitter between Recordings Table and Notes Editor Card
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        # --- Left Side: Recordings Table ---
        table_container = QWidget(self)
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(6)

        self.table = TableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Recording Name", "Subject", "Type & Modules", "Date Recorded", "Duration", "File Size", "Transcript Status", "Notes Preview"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().resizeSection(0, 160)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().resizeSection(2, 160)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)

        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        table_layout.addWidget(self.table)

        splitter.addWidget(table_container)

        # --- Right Side: Notes Editor Panel ---
        self.notes_card = CardWidget(self)
        notes_layout = QVBoxLayout(self.notes_card)
        notes_layout.setContentsMargins(14, 12, 14, 12)
        notes_layout.setSpacing(8)

        notes_header = QHBoxLayout()
        notes_icon = IconWidget(FIF.QUICK_NOTE, self)
        notes_icon.setFixedSize(18, 18)
        notes_header.addWidget(notes_icon)
        notes_header_title = SubtitleLabel("Recording Notes", self)
        notes_header.addWidget(notes_header_title, 1)

        self.save_notes_btn = PushButton("Save", self)
        self.save_notes_btn.setIcon(FIF.SAVE)
        self.save_notes_btn.setEnabled(False)
        self.save_notes_btn.clicked.connect(self._save_current_notes)
        notes_header.addWidget(self.save_notes_btn)
        notes_layout.addLayout(notes_header)

        self.notes_target_label = CaptionLabel("Select a recording to view or write notes.", self)
        self.notes_target_label.setWordWrap(True)
        notes_layout.addWidget(self.notes_target_label)

        self.notes_edit = TextEdit(self)
        self.notes_edit.setPlaceholderText(
            "Add personal notes, lecture reminders, action items, or context for this recording...\n\n"
            "Notes auto-save as you type and persist with your recording history."
        )
        self.notes_edit.setEnabled(False)
        self.notes_edit.textChanged.connect(self._on_notes_text_changed)
        notes_layout.addWidget(self.notes_edit, 1)

        self.notes_status_label = CaptionLabel("", self)
        notes_layout.addWidget(self.notes_status_label)

        splitter.addWidget(self.notes_card)

        # 60% table, 40% notes panel
        splitter.setSizes([600, 360])
        layout.addWidget(splitter, 1)

        # Bottom Action Bar
        bottom_bar = QHBoxLayout()
        self.count_label = CaptionLabel("0 recordings found", self)
        bottom_bar.addWidget(self.count_label, 1)

        self.play_btn = PushButton("Play Audio", self)
        self.play_btn.setIcon(FIF.PLAY)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._play_selected)
        bottom_bar.addWidget(self.play_btn)

        self.view_transcript_btn = PushButton("View Text (.txt)", self)
        self.view_transcript_btn.setIcon(FIF.DOCUMENT)
        self.view_transcript_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_transcript_btn.setEnabled(False)
        self.view_transcript_btn.clicked.connect(self._view_transcript)
        bottom_bar.addWidget(self.view_transcript_btn)

        self.delete_btn = PushButton("Delete", self)
        self.delete_btn.setIcon(FIF.DELETE)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        bottom_bar.addWidget(self.delete_btn)

        self.edit_academic_btn = PushButton("🏷️ Allocate to Subject...", self)
        self.edit_academic_btn.setIcon(FIF.EDIT)
        self.edit_academic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_academic_btn.setEnabled(False)
        self.edit_academic_btn.setToolTip("Allocate or change the course subject and modules for this recording")
        self.edit_academic_btn.clicked.connect(self._prompt_edit_academic)
        bottom_bar.addWidget(self.edit_academic_btn)

        self.chat_lecture_btn = PushButton("💬 Ask Lecture AI", self)
        self.chat_lecture_btn.setIcon(FIF.CHAT)
        self.chat_lecture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chat_lecture_btn.setEnabled(False)
        self.chat_lecture_btn.setToolTip("Open this lecture in interactive AI chat to ask questions or quiz yourself")
        self.chat_lecture_btn.clicked.connect(self._chat_selected)
        bottom_bar.addWidget(self.chat_lecture_btn)

        self.load_btn = PrimaryPushButton("Load for Transcription", self)
        self.load_btn.setIcon(FIF.SEND_FILL)
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
            logger.error(f"Error reading recordings from {RECORDINGS_DIR}: {e}")

        # Sort newest first
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # Update Subject filter dropdown options
        if hasattr(self, "subject_filter_combo"):
            curr_sel = self.subject_filter_combo.currentText()
            self.subject_filter_combo.blockSignals(True)
            self.subject_filter_combo.clear()
            self.subject_filter_combo.addItem("All Subjects")
            try:
                subj_mgr = SubjectManager()
                for s in subj_mgr.get_all_subjects():
                    self.subject_filter_combo.addItem(s.name)
            except Exception:
                pass
            idx = self.subject_filter_combo.findText(curr_sel)
            if idx >= 0:
                self.subject_filter_combo.setCurrentIndex(idx)
            self.subject_filter_combo.blockSignals(False)

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

            academic = get_recording_academic_info(str(fpath))
            subj = academic.get("subject", "")
            stype = academic.get("session_type", "")
            mods = academic.get("modules", [])

            item_name = QTableWidgetItem(fpath.name)
            item_name.setData(Qt.ItemDataRole.UserRole, str(fpath))

            item_subj = QTableWidgetItem(subj or "—")
            item_subj.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if subj:
                item_subj.setForeground(QColor("#38bdf8"))

            if stype and mods:
                mod_display = f"{stype}: {', '.join(mods)}"
            elif stype:
                mod_display = stype
            elif mods:
                mod_display = ", ".join(mods)
            else:
                mod_display = "—"
            item_modules = QTableWidgetItem(mod_display)
            item_modules.setToolTip(f"Subject: {subj}\nType: {stype}\nModules: {', '.join(mods)}")

            item_date = QTableWidgetItem(dt_str)
            item_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_duration = QTableWidgetItem(duration_str)
            item_duration.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_size = QTableWidgetItem(size_str)
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Check transcript status
            full_transcript_text = ""
            tr_exists = has_transcript(str(fpath))
            if tr_exists:
                t_data = load_transcript(str(fpath))
                if t_data:
                    wc = t_data.get("word_count", 0)
                    spk = t_data.get("speaker_count", 1)
                    pushed = t_data.get("notion", {}).get("pushed", False)
                    full_transcript_text = (t_data.get("full_text", "") + " " + t_data.get("study_notes", "")).lower()
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

            # Check for user notes
            notes_str = get_recording_notes(str(fpath))
            if notes_str:
                first_line = notes_str.split("\n", 1)[0].strip()
                preview = (first_line[:40] + "...") if len(first_line) > 40 else first_line
                item_notes = QTableWidgetItem(f"📝 {preview}")
                item_notes.setToolTip(notes_str)
            else:
                item_notes = QTableWidgetItem("—")
                item_notes.setForeground(QColor("#64748b"))

            # Store combined searchable corpus in UserRole + 1 on item_name
            searchable_blob = f"{fpath.name.lower()} {subj.lower()} {stype.lower()} {' '.join(mods).lower()} {dt_str.lower()} {notes_str.lower()} {full_transcript_text}"
            item_name.setData(Qt.ItemDataRole.UserRole + 1, searchable_blob)

            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_subj)
            self.table.setItem(row, 2, item_modules)
            self.table.setItem(row, 3, item_date)
            self.table.setItem(row, 4, item_duration)
            self.table.setItem(row, 5, item_size)
            self.table.setItem(row, 6, item_tr)
            self.table.setItem(row, 7, item_notes)

        active_subjs = set()
        for r in range(self.table.rowCount()):
            s_text = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            if s_text and s_text != "—":
                active_subjs.add(s_text)
        if hasattr(self, "library_stats_label"):
            self.library_stats_label.setText(
                f"📚 Library: {len(files)} lectures across {len(active_subjs)} course subjects"
            )

        self._apply_search_filter(self.search_input.text() if hasattr(self, "search_input") else "")
        self._on_selection_changed()

    def _on_search_changed(self, query: str):
        self._apply_search_filter(query)

    def _apply_search_filter(self, query: str = ""):
        q = (query or "").strip().lower()
        selected_subj = self.subject_filter_combo.currentText() if hasattr(self, "subject_filter_combo") else "All Subjects"
        selected_type = self.type_filter_combo.currentText() if hasattr(self, "type_filter_combo") else "All Types"

        total = self.table.rowCount()
        visible = 0
        for r in range(total):
            item = self.table.item(r, 0)
            blob = (item.data(Qt.ItemDataRole.UserRole + 1) or "") if item else ""
            item_subj = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            item_type_mod = self.table.item(r, 2).text() if self.table.item(r, 2) else ""

            match_text = (not q) or (q in blob)
            match_subj = (selected_subj == "All Subjects") or (selected_subj.lower() == item_subj.lower())
            match_type = (selected_type == "All Types") or (selected_type.lower() in item_type_mod.lower())

            match = match_text and match_subj and match_type
            self.table.setRowHidden(r, not match)
            if match:
                visible += 1
        if q or selected_subj != "All Subjects" or selected_type != "All Types":
            self.count_label.setText(f"Showing {visible} of {total} recording(s)")
        else:
            self.count_label.setText(f"{total} recording(s) in archive")

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
        self.edit_academic_btn.setEnabled(has_sel)
        self.chat_lecture_btn.setEnabled(has_tr)
        self.view_transcript_btn.setEnabled(has_tr)

        if has_tr:
            self.load_btn.setText("⚡ Load Recording & Transcript")
        else:
            self.load_btn.setText("⚡ Load for Transcription")

        # Update notes panel for newly selected recording
        self._current_selected_path = path
        if path and os.path.exists(path):
            fname = os.path.basename(path)
            self.notes_target_label.setText(f"Notes for: {fname}")
            self.notes_edit.setEnabled(True)
            self.save_notes_btn.setEnabled(True)

            self._is_populating_notes = True
            current_notes = get_recording_notes(path)
            self.notes_edit.setPlainText(current_notes)
            self._is_populating_notes = False
            self.notes_status_label.setText(f"Loaded notes ({len(current_notes)} chars)")
        else:
            self.notes_target_label.setText("Select a recording to view or write notes.")
            self.notes_edit.setEnabled(False)
            self.save_notes_btn.setEnabled(False)
            self._is_populating_notes = True
            self.notes_edit.clear()
            self._is_populating_notes = False
            self.notes_status_label.setText("")

    def _on_notes_text_changed(self):
        if self._is_populating_notes or not self._current_selected_path:
            return
        self.notes_status_label.setText("Typing... (auto-saving)")
        self._notes_autosave_timer.start()

    def _save_current_notes(self):
        if not self._current_selected_path:
            return
        text = self.notes_edit.toPlainText().strip()
        saved = save_recording_notes(self._current_selected_path, text)
        if saved:
            self.notes_status_label.setText("Notes saved ✓")
            # Update preview in current table row
            curr_row = self.table.currentRow()
            if curr_row >= 0:
                if text:
                    first_line = text.split("\n", 1)[0].strip()
                    preview = (first_line[:40] + "...") if len(first_line) > 40 else first_line
                    item_notes = QTableWidgetItem(f"📝 {preview}")
                    item_notes.setToolTip(text)
                else:
                    item_notes = QTableWidgetItem("—")
                    item_notes.setForeground(QColor("#64748b"))
                self.table.setItem(curr_row, 7, item_notes)
        else:
            self.notes_status_label.setText("Failed to save notes")

    def _prompt_edit_academic(self):
        path = self._get_selected_path()
        if not path or not os.path.exists(path):
            return

        academic = get_recording_academic_info(path)
        current_subj = academic.get("subject", "")
        current_type = academic.get("session_type", "")
        current_mods = academic.get("modules", [])

        dialog = MessageBoxBase(self.window())
        dialog.titleLabel = SubtitleLabel("🏷️ Allocate Recording to Course Subject", dialog)
        dialog.viewLayout.addWidget(dialog.titleLabel)

        fname_label = CaptionLabel(f"Recording: {Path(path).name}", dialog)
        dialog.viewLayout.addWidget(fname_label)

        subj_row = QHBoxLayout()
        subj_row.addWidget(BodyLabel("Subject / Course:", dialog), 1)
        manage_subjs_btn = TransparentPushButton("Manage Courses", dialog)
        manage_subjs_btn.setIcon(FIF.SETTING)
        subj_row.addWidget(manage_subjs_btn)
        dialog.viewLayout.addLayout(subj_row)

        subj_combo = ComboBox(dialog)
        subj_mgr = SubjectManager()

        def _reload_combo():
            curr = subj_combo.currentData() or current_subj
            subj_combo.blockSignals(True)
            subj_combo.clear()
            for s in subj_mgr.get_all_subjects():
                code_prefix = f"[{s.code}] " if s.code else ""
                subj_combo.addItem(f"{code_prefix}{s.name}", userData=s.name)
            if curr:
                idx = subj_combo.findData(curr)
                if idx < 0:
                    idx = subj_combo.findText(curr)
                if idx >= 0:
                    subj_combo.setCurrentIndex(idx)
                else:
                    subj_combo.addItem(curr, userData=curr)
                    subj_combo.setCurrentIndex(subj_combo.count() - 1)
            subj_combo.blockSignals(False)

        def _open_mgr():
            from ui.subject_dialog import SubjectManagementDialog
            m_dlg = SubjectManagementDialog(dialog)
            m_dlg.subjects_changed.connect(_reload_combo)
            m_dlg.exec()
            _reload_combo()

        manage_subjs_btn.clicked.connect(_open_mgr)
        _reload_combo()
        dialog.viewLayout.addWidget(subj_combo)

        dialog.viewLayout.addWidget(BodyLabel("Session Type:", dialog))
        type_combo = ComboBox(dialog)
        types = ["📖 Theory Lecture", "✏️ Exercise / Problem Set", "🔬 Lab / Workshop"]
        type_combo.addItems(types)
        if current_type:
            t_idx = type_combo.findText(current_type)
            if t_idx >= 0:
                type_combo.setCurrentIndex(t_idx)
        dialog.viewLayout.addWidget(type_combo)

        dialog.viewLayout.addWidget(BodyLabel("Modules Covered:", dialog))
        mod_input = LineEdit(dialog)
        mod_input.setText(", ".join(current_mods))
        mod_input.setPlaceholderText("Comma-separated modules (e.g. Module 1: Intro, Module 2: Systems)")
        dialog.viewLayout.addWidget(mod_input)

        dialog.yesButton.setText("Allocate to Subject")
        dialog.cancelButton.setText("Cancel")

        if dialog.exec():
            new_subj = subj_combo.currentData() or subj_combo.currentText()
            new_type = type_combo.currentText()
            new_mods = [m.strip() for m in mod_input.text().split(",") if m.strip()]
            update_recording_academic_info(path, new_subj, new_type, new_mods)
            self.refresh()
            win = self.window()
            if hasattr(win, "transcribe_page") and hasattr(win.transcribe_page, "subjects_hub"):
                win.transcribe_page.subjects_hub.refresh()

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        self._load_selected()

    def _load_selected(self):
        path = self._get_selected_path()
        if path and os.path.exists(path):
            self.recording_selected.emit(path)

    def _chat_selected(self):
        path = self._get_selected_path()
        if path and os.path.exists(path):
            self.chat_with_recording.emit(path)

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
            f"Are you sure you want to permanently delete:\n{fname}\n\n(This will also remove any saved transcripts and companion notes)?",
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

    def _open_logs(self):
        """Open the app log file in default viewer."""
        open_log_file()
