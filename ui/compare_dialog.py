"""Compare and Audition Dialog for NotionTranscribe.

Interactive modal allowing word-by-word comparison between two Whisper models,
auditioning 2s-before / 2s-after audio snippets, and selecting or correcting the right words.
"""

import os
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
)

from audio_player import AudioPlayer
from transcriber import format_timestamp
from transcript_diff import Discrepancy, TranscriptDiffEngine


class CompareDialog(QDialog):
    """Interactive modal for resolving discrepancies between two transcription models."""

    resolved_transcript_ready = pyqtSignal(list, str)  # (merged_segments, merged_text)

    def __init__(
        self,
        diff_engine: TranscriptDiffEngine,
        audio_path: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.diff_engine = diff_engine
        self.audio_path = audio_path
        self.discrepancies = diff_engine.discrepancies
        self.current_idx = 0

        self.audio_player = AudioPlayer(self)
        self.audio_player.playback_started.connect(self._on_playback_started)
        self.audio_player.playback_finished.connect(self._on_playback_finished)
        self.audio_player.playback_error.connect(self._on_playback_error)

        self.setWindowTitle("Transcript Comparison & Audio Audition")
        self.resize(780, 700)
        self.setModal(True)

        self._init_ui()
        if self.discrepancies:
            self._display_discrepancy(0)
        else:
            self._show_no_discrepancies()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # 1. Header
        header_row = QHBoxLayout()
        header_col = QVBoxLayout()
        header_title = TitleLabel("⚖ Multi-Model Transcript Comparison", self)
        header_col.addWidget(header_title)

        count = len(self.discrepancies)
        sub_text = (
            f"Comparing '{self.diff_engine.model_a_name}' vs '{self.diff_engine.model_b_name}' "
            f"• {count} difference{'s' if count != 1 else ''} found"
        )
        self.sub_label = SubtitleLabel(sub_text, self)
        header_col.addWidget(self.sub_label)
        header_row.addLayout(header_col, 1)

        main_layout.addLayout(header_row)

        # 2. Main Discrepancy Card
        self.card = CardWidget(self)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        
        self.card_title = SubtitleLabel("Discrepancy Review", self.card)
        card_layout.addWidget(self.card_title)

        # Stepper Row
        stepper_row = QHBoxLayout()
        self.prev_btn = PushButton("◀ Previous", self)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.clicked.connect(self._prev_discrepancy)
        stepper_row.addWidget(self.prev_btn)

        self.step_label = StrongBodyLabel("Discrepancy 1 of 1", self)
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stepper_row.addWidget(self.step_label, 1)

        self.next_btn = PushButton("Next ▶", self)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(self._next_discrepancy)
        stepper_row.addWidget(self.next_btn)

        card_layout.addLayout(stepper_row)

        # Context View (Highlighted Sentence)
        self.context_box = BodyLabel(self)
        self.context_box.setWordWrap(True)
        card_layout.addWidget(self.context_box)

        # Audio Playback Toolbar
        audio_frame = CardWidget(self)
        audio_layout = QHBoxLayout(audio_frame)
        audio_layout.setContentsMargins(8, 6, 8, 6)

        self.play_btn = PrimaryPushButton("▶ Play Audio (-2s to +2s)", self)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self._play_snippet)
        audio_layout.addWidget(self.play_btn)

        self.stop_btn = PushButton("■ Stop", self)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_audio)
        audio_layout.addWidget(self.stop_btn)

        self.audio_timing_label = CaptionLabel("Audition Window: --:--", self)
        audio_layout.addWidget(self.audio_timing_label, 1)

        card_layout.addWidget(audio_frame)

        # Choice Selection Options
        choice_group = CardWidget(self)
        choice_layout = QVBoxLayout(choice_group)
        choice_layout.setSpacing(8)
        
        choice_title = StrongBodyLabel("Choose Correct Word / Phrase", choice_group)
        choice_layout.addWidget(choice_title)

        # Radio Model A
        self.radio_a = RadioButton(self)
        self.radio_a.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio_a.toggled.connect(self._on_choice_changed)
        choice_layout.addWidget(self.radio_a)

        # Radio Model B
        self.radio_b = RadioButton(self)
        self.radio_b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio_b.toggled.connect(self._on_choice_changed)
        choice_layout.addWidget(self.radio_b)

        # Custom Input Row
        custom_row = QHBoxLayout()
        self.radio_custom = RadioButton("Custom Correction:", self)
        self.radio_custom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio_custom.toggled.connect(self._on_choice_changed)
        custom_row.addWidget(self.radio_custom)

        self.custom_input = LineEdit(self)
        self.custom_input.setPlaceholderText("Type correct word if neither model is accurate...")
        self.custom_input.textChanged.connect(self._on_custom_text_changed)
        custom_row.addWidget(self.custom_input, 1)
        choice_layout.addLayout(custom_row)

        card_layout.addWidget(choice_group)
        main_layout.addWidget(self.card)

        # 3. Discrepancies Overview Table
        table_label = StrongBodyLabel("All Discrepancies Overview:", self)
        main_layout.addWidget(table_label)

        self.overview_table = TableWidget(self)
        self.overview_table.setColumnCount(5)
        self.overview_table.setHorizontalHeaderLabels([
            "#",
            "Time",
            f"{self.diff_engine.model_a_name}",
            f"{self.diff_engine.model_b_name}",
            "Selected Resolution",
        ])
        self.overview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.overview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.overview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.overview_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.overview_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.overview_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.overview_table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        self.overview_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.overview_table.setMaximumHeight(150)
        self.overview_table.itemSelectionChanged.connect(self._on_table_row_selected)
        main_layout.addWidget(self.overview_table)

        self._populate_overview_table()

        # 4. Bottom Action Bar
        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self.cancel_btn = PushButton("Cancel", self)
        self.cancel_btn.clicked.connect(self.reject)
        action_row.addWidget(self.cancel_btn)

        action_row.addStretch()

        self.apply_btn = PrimaryPushButton("✔ Apply Verified Transcript", self)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self._apply_resolutions)
        action_row.addWidget(self.apply_btn)

        main_layout.addLayout(action_row)

    def _show_no_discrepancies(self):
        self.card.setEnabled(False)
        self.context_box.setText("🎉 Both models produced 100% identical transcripts! No discrepancies found.")
        self.step_label.setText("0 Differences")
        self.apply_btn.setText("✔ Apply Transcript")

    def _populate_overview_table(self):
        self.overview_table.setRowCount(len(self.discrepancies))
        for row, d in enumerate(self.discrepancies):
            ts_str = format_timestamp(d.start_sec)
            self.overview_table.setItem(row, 0, QTableWidgetItem(f"#{d.diff_id}"))
            self.overview_table.setItem(row, 1, QTableWidgetItem(ts_str))
            self.overview_table.setItem(row, 2, QTableWidgetItem(d.text_a or "(omitted)"))
            self.overview_table.setItem(row, 3, QTableWidgetItem(d.text_b or "(omitted)"))
            self.overview_table.setItem(row, 4, QTableWidgetItem(d.chosen_text or "(none)"))

    def _update_table_row(self, row: int):
        if 0 <= row < len(self.discrepancies):
            d = self.discrepancies[row]
            item = QTableWidgetItem(d.chosen_text or "(none)")
            # In Fluent Design, colors can be preserved or removed, we will keep the custom green indicator.
            item.setForeground(QColor("#10b981"))
            self.overview_table.setItem(row, 4, item)

    def _display_discrepancy(self, index: int):
        if not self.discrepancies:
            return

        self.audio_player.stop()
        self.current_idx = max(0, min(len(self.discrepancies) - 1, index))
        d = self.discrepancies[self.current_idx]

        self.prev_btn.setEnabled(self.current_idx > 0)
        self.next_btn.setEnabled(self.current_idx < len(self.discrepancies) - 1)
        self.step_label.setText(f"Discrepancy {self.current_idx + 1} of {len(self.discrepancies)}")

        # Build context HTML with highlighted difference
        conflicting_label = f"{d.text_a or '∅'} ⚡ {d.text_b or '∅'}"
        html_context = (
            f'<span style="color: #94a3b8;">... {d.context_before} </span>'
            f'<span style="background-color: #f59e0b; color: #0f172a; font-weight: bold; '
            f'padding: 3px 8px; border-radius: 4px; font-size: 15px;"> {conflicting_label} </span>'
            f'<span style="color: #94a3b8;"> {d.context_after} ...</span>'
        )
        self.context_box.setText(html_context)

        # Audio window
        start_w = max(0.0, d.start_sec - 2.0)
        end_w = d.end_sec + 2.0
        dur_w = end_w - start_w
        self.audio_timing_label.setText(
            f"Audition Window: [{format_timestamp(start_w)} - {format_timestamp(end_w)}] ({dur_w:.1f}s)"
        )

        # Radios
        prob_a_pct = f" ({int(d.prob_a * 100)}% confidence)" if d.prob_a > 0 else ""
        prob_b_pct = f" ({int(d.prob_b * 100)}% confidence)" if d.prob_b > 0 else ""

        self.radio_a.blockSignals(True)
        self.radio_b.blockSignals(True)
        self.radio_custom.blockSignals(True)

        self.radio_a.setText(
            f"Model A ({self.diff_engine.model_a_name}): \"{d.text_a or '(omitted)'}\"{prob_a_pct}"
        )
        self.radio_b.setText(
            f"Model B ({self.diff_engine.model_b_name}): \"{d.text_b or '(omitted)'}\"{prob_b_pct}"
        )

        if d.chosen_source == "model_a":
            self.radio_a.setChecked(True)
        elif d.chosen_source == "model_b":
            self.radio_b.setChecked(True)
        else:
            self.radio_custom.setChecked(True)
            self.custom_input.setText(d.chosen_text)

        self.radio_a.blockSignals(False)
        self.radio_b.blockSignals(False)
        self.radio_custom.blockSignals(False)

        # Highlight row in overview table
        self.overview_table.blockSignals(True)
        self.overview_table.selectRow(self.current_idx)
        self.overview_table.blockSignals(False)

    def _prev_discrepancy(self):
        if self.current_idx > 0:
            self._display_discrepancy(self.current_idx - 1)

    def _next_discrepancy(self):
        if self.current_idx < len(self.discrepancies) - 1:
            self._display_discrepancy(self.current_idx + 1)

    def _on_table_row_selected(self):
        row = self.overview_table.currentRow()
        if 0 <= row < len(self.discrepancies) and row != self.current_idx:
            self._display_discrepancy(row)

    def _on_choice_changed(self):
        if not self.discrepancies:
            return
        d = self.discrepancies[self.current_idx]

        if self.radio_a.isChecked():
            d.set_choice("model_a")
        elif self.radio_b.isChecked():
            d.set_choice("model_b")
        elif self.radio_custom.isChecked():
            d.set_choice("custom", self.custom_input.text())

        self._update_table_row(self.current_idx)

    def _on_custom_text_changed(self, text: str):
        if self.radio_custom.isChecked() and self.discrepancies:
            d = self.discrepancies[self.current_idx]
            d.set_choice("custom", text)
            self._update_table_row(self.current_idx)

    def _play_snippet(self):
        if not self.discrepancies:
            return
        d = self.discrepancies[self.current_idx]

        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.play_btn.setText("🔊 Playing (-2s to +2s)...")

        started = self.audio_player.play_snippet(
            audio_path=self.audio_path,
            start_sec=d.start_sec,
            end_sec=d.end_sec,
            pad_before=2.0,
            pad_after=2.0,
        )
        if not started:
            self._on_playback_finished()

    def _stop_audio(self):
        self.audio_player.stop()
        self._on_playback_finished()

    def _on_playback_started(self, start: float, end: float):
        pass

    def _on_playback_finished(self):
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.play_btn.setText("▶ Play Audio (-2s to +2s)")

    def _on_playback_error(self, err_msg: str):
        self._on_playback_finished()
        InfoBar.warning(
            title="Audio Playback Error",
            content=f"Could not play snippet:\n{err_msg}",
            parent=self,
            duration=5000
        )

    def _apply_resolutions(self):
        self.audio_player.stop()
        merged_segments, merged_text = self.diff_engine.merge()
        self.resolved_transcript_ready.emit(merged_segments, merged_text)
        self.accept()

    def closeEvent(self, event):
        self.audio_player.stop()
        event.accept()
