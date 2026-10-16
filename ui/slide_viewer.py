"""Interactive Class Slide Companion & Annotation Canvas for Transcribe Notion.

Allows importing a class PDF (or slide images) during or before a lecture:
- Browse/preview any slide freely without changing the active lecture slide
- Click "📍 We Are Here" to stamp the current recording timestamp onto that slide
  (decoupling "Previewed Slide" from "Active Class Slide")
- Draw freehand pen or highlighter annotations directly on the slide image
- Type per-slide text notes that persist and merge into the transcript, Notion push, and NotebookLM export
- Automatically extracts course jargon/vocabulary from the PDF via text + OCR for Whisper accuracy
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TextEdit,
    TransparentPushButton,
)

from logger import get_logger
from transcriber import format_timestamp

logger = get_logger("SlideViewer")


class AnnotatableSlideCanvas(QWidget):
    """Interactive canvas that renders a PDF slide and supports pen/highlighter drawing."""

    annotations_modified = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 220)
        self._base_pixmap: Optional[QPixmap] = None
        # Each stroke: {"mode": "pen"|"highlighter", "color": str, "width": float, "points": List[Tuple[float, float]]}
        # Coordinates are normalized [0.0, 1.0] relative to the slide image rect so resizing never shifts drawings!
        self._strokes: List[Dict[str, Any]] = []
        self._current_stroke: Optional[Dict[str, Any]] = None
        self._tool_mode: str = "pen"  # "none", "pen", "highlighter"
        self._pen_color: str = "#ef4444"  # Red default

    def set_tool_mode(self, mode: str):
        """Set drawing mode: 'none', 'pen', or 'highlighter'."""
        self._tool_mode = mode
        if mode in ("pen", "highlighter"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_pen_color(self, hex_color: str):
        self._pen_color = hex_color

    def set_slide(self, pixmap: Optional[QPixmap], strokes: Optional[List[Dict[str, Any]]] = None):
        self._base_pixmap = pixmap
        self._strokes = list(strokes) if strokes else []
        self._current_stroke = None
        self.update()

    def get_strokes(self) -> List[Dict[str, Any]]:
        return list(self._strokes)

    def undo_stroke(self):
        if self._strokes:
            self._strokes.pop()
            self.annotations_modified.emit()
            self.update()

    def clear_strokes(self):
        if self._strokes:
            self._strokes.clear()
            self.annotations_modified.emit()
            self.update()

    def _get_target_rect(self) -> QRectF:
        """Compute aspect-ratio-preserving target rectangle for the slide inside this widget."""
        if not self._base_pixmap or self._base_pixmap.isNull():
            return QRectF(0, 0, self.width(), self.height())

        pw = float(self._base_pixmap.width())
        ph = float(self._base_pixmap.height())
        ww = float(self.width())
        wh = float(self.height())
        if pw <= 0 or ph <= 0 or ww <= 0 or wh <= 0:
            return QRectF(0, 0, ww, wh)

        scale = min(ww / pw, wh / ph)
        tw = pw * scale
        th = ph * scale
        tx = (ww - tw) / 2.0
        ty = (wh - th) / 2.0
        return QRectF(tx, ty, tw, th)

    def _pos_to_normalized(self, pos: QPointF) -> Optional[Tuple[float, float]]:
        rect = self._get_target_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        nx = (pos.x() - rect.left()) / rect.width()
        ny = (pos.y() - rect.top()) / rect.height()
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        return (round(nx, 4), round(ny, 4))

    def mousePressEvent(self, event: QMouseEvent):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._base_pixmap is not None
            and self._tool_mode in ("pen", "highlighter")
        ):
            norm = self._pos_to_normalized(event.position())
            if norm:
                self._current_stroke = {
                    "mode": self._tool_mode,
                    "color": "#facc15" if self._tool_mode == "highlighter" else self._pen_color,
                    "width": 0.028 if self._tool_mode == "highlighter" else 0.005,
                    "points": [norm],
                }
                self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._current_stroke is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            norm = self._pos_to_normalized(event.position())
            if norm:
                self._current_stroke["points"].append(norm)
                self.update()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._current_stroke is not None:
            if len(self._current_stroke["points"]) > 0:
                self._strokes.append(self._current_stroke)
                self.annotations_modified.emit()
            self._current_stroke = None
            self.update()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Dark slate background
        painter.fillRect(self.rect(), QColor("#0f172a"))

        if not self._base_pixmap or self._base_pixmap.isNull():
            painter.setPen(QColor("#64748b"))
            font = QFont("Segoe UI", 11)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "📊 Import a Class PDF Deck to preview slides,\n"
                "mark '📍 We Are Here' during lecture, and draw annotations.",
            )
            painter.end()
            return

        target_rect = self._get_target_rect()
        painter.drawPixmap(target_rect.toRect(), self._base_pixmap)

        # Draw saved strokes + active stroke
        all_strokes = list(self._strokes)
        if self._current_stroke:
            all_strokes.append(self._current_stroke)

        for stroke in all_strokes:
            pts = stroke.get("points", [])
            if not pts:
                continue
            mode = stroke.get("mode", "pen")
            color_str = stroke.get("color", "#ef4444")
            rel_width = float(stroke.get("width", 0.005))

            color = QColor(color_str)
            if mode == "highlighter":
                color.setAlpha(95)
            else:
                color.setAlpha(235)

            pixel_width = max(2.0, rel_width * target_rect.width())
            pen = QPen(
                color,
                pixel_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)

            if len(pts) == 1:
                x = target_rect.left() + pts[0][0] * target_rect.width()
                y = target_rect.top() + pts[0][1] * target_rect.height()
                painter.drawPoint(QPointF(x, y))
            else:
                path = QPainterPath()
                x0 = target_rect.left() + pts[0][0] * target_rect.width()
                y0 = target_rect.top() + pts[0][1] * target_rect.height()
                path.moveTo(x0, y0)
                for nx, ny in pts[1:]:
                    px = target_rect.left() + nx * target_rect.width()
                    py = target_rect.top() + ny * target_rect.height()
                    path.lineTo(px, py)
                painter.drawPath(path)

        painter.end()


class SlideCompanionWidget(QWidget):
    """Live Lecture Slide Companion allowing independent previewing, 'We Are Here' timestamping, and annotations."""

    pdf_imported_for_jargon = pyqtSignal(str)  # Emits PDF path so MainWindow extracts OCR/jargon
    slide_marked_here = pyqtSignal(int, float, str)  # (slide_number_1based, timestamp_sec, slide_title)

    def __init__(self, time_provider=None, parent=None):
        """Initialize the Slide Companion.

        Args:
            time_provider: Callable returning current recording elapsed seconds (float).
        """
        super().__init__(parent)
        self._time_provider = time_provider or (lambda: 0.0)
        self._pdf_path: Optional[str] = None
        self._slide_pixmaps: List[QPixmap] = []
        self._slide_titles: List[str] = []
        self._preview_idx: int = -1       # 0-based index of currently previewed slide
        self._active_idx: int = -1        # 0-based index of "We Are Here" active class slide

        # Per-slide metadata keyed by 0-based slide_idx:
        # {"timestamps": List[float], "notes": str, "strokes": List[dict]}
        self._slide_data: Dict[int, Dict[str, Any]] = {}
        self._is_loading_slide = False

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 4, 0, 0)
        main_layout.setSpacing(8)

        # ---- Top Control Bar ----
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.open_pdf_btn = PushButton("📊 Load Class PDF...", self)
        self.open_pdf_btn.setToolTip("Load lecture slides (.pdf) to preview, annotate, and track timestamps during class")
        self.open_pdf_btn.clicked.connect(self._prompt_open_pdf)
        top_bar.addWidget(self.open_pdf_btn)

        self.prev_btn = PushButton("◀ Prev", self)
        self.prev_btn.setEnabled(False)
        self.prev_btn.setToolTip("Preview previous slide (does not change active 'We Are Here' slide)")
        self.prev_btn.clicked.connect(self.preview_prev_slide)
        top_bar.addWidget(self.prev_btn)

        self.slide_counter_label = StrongBodyLabel("No Deck Loaded", self)
        self.slide_counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_bar.addWidget(self.slide_counter_label)

        self.next_btn = PushButton("Next ▶", self)
        self.next_btn.setEnabled(False)
        self.next_btn.setToolTip("Preview next slide (does not change active 'We Are Here' slide)")
        self.next_btn.clicked.connect(self.preview_next_slide)
        top_bar.addWidget(self.next_btn)

        # Active vs Preview Status Badge + Jump back to Active
        self.active_status_btn = TransparentPushButton("📍 Active: None", self)
        self.active_status_btn.setEnabled(False)
        self.active_status_btn.setToolTip("Click to jump back to the current 'We Are Here' slide")
        self.active_status_btn.clicked.connect(self.jump_to_active_slide)
        top_bar.addWidget(self.active_status_btn, 1)

        # Live Recording Timer indicator while in full-screen slides mode
        self.live_timer_badge = CaptionLabel("🎙 00:00", self)
        self.live_timer_badge.setToolTip("Current live recording timestamp used when marking '📍 We Are Here'")
        top_bar.addWidget(self.live_timer_badge)

        # "We Are Here" Action Button
        self.we_are_here_btn = PrimaryPushButton("📍 We Are Here (Mark Slide)", self)
        self.we_are_here_btn.setEnabled(False)
        self.we_are_here_btn.setToolTip(
            "Set the currently previewed slide as the active class slide and log the current recording timestamp!"
        )
        self.we_are_here_btn.clicked.connect(self.mark_current_slide_here)
        top_bar.addWidget(self.we_are_here_btn)

        # Full-Screen OS Window Toggle Button
        self.fullscreen_btn = PushButton("⛶ Full Screen", self)
        self.fullscreen_btn.setToolTip("Toggle full-screen lecture mode (F11)")
        self.fullscreen_btn.clicked.connect(self.toggle_window_fullscreen)
        top_bar.addWidget(self.fullscreen_btn)

        main_layout.addLayout(top_bar)

        # ---- Main Body Splitter: Left Slide List | Center Canvas | Right Notes & Draw Tools ----
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(True)

        # 1. Left: Slide List (for fast preview browsing)
        self.slide_list = ListWidget(self)
        self.slide_list.setMinimumWidth(160)
        self.slide_list.setMaximumWidth(320)
        self.slide_list.currentRowChanged.connect(self._on_list_row_changed)
        self.splitter.addWidget(self.slide_list)

        # 2. Center: Annotatable Slide Canvas
        canvas_card = CardWidget(self)
        canvas_layout = QVBoxLayout(canvas_card)
        canvas_layout.setContentsMargins(8, 8, 8, 8)
        canvas_layout.setSpacing(6)

        # Annotation Drawing Toolbar
        draw_bar = QHBoxLayout()
        draw_bar.setSpacing(6)

        self.preview_banner_label = CaptionLabel("Previewing: —", self)
        draw_bar.addWidget(self.preview_banner_label, 1)

        self.tool_combo = ComboBox(self)
        self.tool_combo.addItems(["✏️ Pen Draw", "🖍 Highlighter", "👆 View Only"])
        self.tool_combo.currentIndexChanged.connect(self._on_tool_changed)
        draw_bar.addWidget(self.tool_combo)

        self.color_combo = ComboBox(self)
        self.color_combo.addItem("🔴 Red", userData="#ef4444")
        self.color_combo.addItem("🔵 Sky Blue", userData="#38bdf8")
        self.color_combo.addItem("🟢 Emerald", userData="#10b981")
        self.color_combo.addItem("🟣 Purple", userData="#c084fc")
        self.color_combo.currentIndexChanged.connect(self._on_color_changed)
        draw_bar.addWidget(self.color_combo)

        self.undo_draw_btn = PushButton("↩ Undo", self)
        self.undo_draw_btn.setToolTip("Undo last ink stroke on this slide")
        self.undo_draw_btn.clicked.connect(lambda: self.canvas.undo_stroke())
        draw_bar.addWidget(self.undo_draw_btn)

        self.clear_draw_btn = PushButton("🧹 Clear Ink", self)
        self.clear_draw_btn.setToolTip("Clear all ink drawings on this slide")
        self.clear_draw_btn.clicked.connect(lambda: self.canvas.clear_strokes())
        draw_bar.addWidget(self.clear_draw_btn)

        canvas_layout.addLayout(draw_bar)

        self.canvas = AnnotatableSlideCanvas(self)
        self.canvas.annotations_modified.connect(self._save_current_slide_strokes)
        canvas_layout.addWidget(self.canvas, 1)

        self.splitter.addWidget(canvas_card)

        # 3. Right: Per-Slide Text Annotations & Timestamp Log
        notes_card = CardWidget(self)
        notes_card.setMinimumWidth(220)
        notes_card.setMaximumWidth(420)
        notes_layout = QVBoxLayout(notes_card)
        notes_layout.setContentsMargins(12, 12, 12, 12)
        notes_layout.setSpacing(8)

        self.notes_header_label = StrongBodyLabel("📝 Slide Annotations", self)
        notes_layout.addWidget(self.notes_header_label)

        self.timestamps_badge = CaptionLabel("Timestamps: Not marked yet", self)
        self.timestamps_badge.setWordWrap(True)
        notes_layout.addWidget(self.timestamps_badge)

        self.slide_notes_edit = TextEdit(self)
        self.slide_notes_edit.setPlaceholderText(
            "Type your annotations or professor's remarks for this specific slide...\n\n"
            "(You can freely browse and annotate other slides without changing the 'We Are Here' slide!)"
        )
        self.slide_notes_edit.setEnabled(False)
        self.slide_notes_edit.textChanged.connect(self._on_slide_notes_changed)
        notes_layout.addWidget(self.slide_notes_edit, 1)

        self.splitter.addWidget(notes_card)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([190, 860, 280])

        main_layout.addWidget(self.splitter, 1)

    def set_recording_time_display(self, seconds: float, is_recording: bool = True):
        """Update the live recording clock badge inside the full-screen slide toolbar."""
        ts_str = format_timestamp(seconds)
        prefix = "🔴 REC" if is_recording else "🎙"
        self.live_timer_badge.setText(f"{prefix} {ts_str}")

    def toggle_window_fullscreen(self):
        """Toggle the top-level application window between full-screen and normal/maximized."""
        win = self.window()
        if not win:
            return
        if win.isFullScreen():
            win.showMaximized()
            self.fullscreen_btn.setText("⛶ Full Screen")
        else:
            win.showFullScreen()
            self.fullscreen_btn.setText("🗗 Exit Full Screen")

    # ==========================
    # PDF Loading & Rendering
    # ==========================
    def _prompt_open_pdf(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Class Lecture PDF Slides",
            "",
            "PDF Documents (*.pdf)",
        )
        if file_path:
            self.load_pdf(file_path)

    def load_pdf(self, pdf_path: str) -> bool:
        """Render pages of a PDF slide deck for interactive previewing and annotation."""
        if not pdf_path or not os.path.exists(pdf_path):
            return False

        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError:
                logger.error("PyMuPDF (fitz) is required to render PDF slides.")
                return False

        try:
            doc = fitz.open(pdf_path)
            if len(doc) == 0:
                doc.close()
                return False

            self._pdf_path = pdf_path
            self._slide_pixmaps.clear()
            self._slide_titles.clear()
            self._slide_data.clear()
            self.slide_list.clear()
            self._active_idx = -1
            self._preview_idx = -1

            for idx in range(len(doc)):
                page = doc[idx]
                # Extract first non-empty line as slide title snippet
                raw_txt = page.get_text("text").strip()
                first_line = ""
                for line in raw_txt.splitlines():
                    clean_l = line.strip()
                    if len(clean_l) > 2:
                        first_line = clean_l[:32]
                        break
                title_str = first_line or f"Slide {idx + 1}"
                self._slide_titles.append(title_str)

                # Render slide pixmap at crisp 160 DPI for full-screen clarity
                pix = page.get_pixmap(dpi=160, alpha=False)
                qimg = QImage(
                    pix.samples,
                    pix.width,
                    pix.height,
                    pix.stride,
                    QImage.Format.Format_RGB888,
                ).copy()
                qpix = QPixmap.fromImage(qimg)
                self._slide_pixmaps.append(qpix)

                self._slide_data[idx] = {
                    "timestamps": [],
                    "notes": "",
                    "strokes": [],
                }

                item = QListWidgetItem(f"Slide {idx + 1}: {title_str}")
                self.slide_list.addItem(item)

            doc.close()

            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            self.we_are_here_btn.setEnabled(True)
            self.slide_notes_edit.setEnabled(True)
            self.active_status_btn.setText("📍 Active: Not marked yet")
            self.active_status_btn.setEnabled(False)

            # Select Slide 1 for preview
            self.slide_list.setCurrentRow(0)

            # Notify MainWindow so it also extracts Course Jargon for Whisper!
            self.pdf_imported_for_jargon.emit(pdf_path)
            return True
        except Exception as e:
            logger.exception(f"Failed to load PDF into SlideCompanionWidget: {e}")
            return False

    # ==========================
    # Navigation & "We Are Here"
    # ==========================
    def _on_list_row_changed(self, row: int):
        if row < 0 or row >= len(self._slide_pixmaps):
            return
        self._preview_idx = row
        self._refresh_preview_ui()

    def preview_prev_slide(self):
        if self._preview_idx > 0:
            self.slide_list.setCurrentRow(self._preview_idx - 1)

    def preview_next_slide(self):
        if self._preview_idx + 1 < len(self._slide_pixmaps):
            self.slide_list.setCurrentRow(self._preview_idx + 1)

    def jump_to_active_slide(self):
        """Return preview to whichever slide was last marked 'We Are Here'."""
        if 0 <= self._active_idx < len(self._slide_pixmaps):
            self.slide_list.setCurrentRow(self._active_idx)

    def mark_current_slide_here(self) -> Optional[Tuple[int, float]]:
        """Mark the currently previewed slide as the active class slide ('We Are Here') with current timestamp."""
        if self._preview_idx < 0 or self._preview_idx >= len(self._slide_pixmaps):
            return None

        self._active_idx = self._preview_idx
        elapsed_sec = float(self._time_provider() or 0.0)
        entry = self._slide_data.setdefault(
            self._active_idx, {"timestamps": [], "notes": "", "strokes": []}
        )
        entry["timestamps"].append(round(elapsed_sec, 1))

        self._update_list_item_labels()
        self._refresh_preview_ui()

        slide_num = self._active_idx + 1
        slide_title = self._slide_titles[self._active_idx] if self._active_idx < len(self._slide_titles) else f"Slide {slide_num}"
        self.slide_marked_here.emit(slide_num, elapsed_sec, slide_title)
        return slide_num, elapsed_sec

    def _update_list_item_labels(self):
        for idx in range(self.slide_list.count()):
            item = self.slide_list.item(idx)
            if not item:
                continue
            title = self._slide_titles[idx] if idx < len(self._slide_titles) else f"Slide {idx + 1}"
            data = self._slide_data.get(idx, {})
            ts_list = data.get("timestamps", [])
            has_notes = bool(data.get("notes", "").strip() or data.get("strokes"))

            prefix = "📍 " if idx == self._active_idx else ""
            suffix_parts = []
            if ts_list:
                suffix_parts.append(f"@{format_timestamp(ts_list[-1])}")
            if has_notes:
                suffix_parts.append("📝")
            suffix = f" ({' '.join(suffix_parts)})" if suffix_parts else ""
            item.setText(f"{prefix}Slide {idx + 1}: {title}{suffix}")

    def _refresh_preview_ui(self):
        total = len(self._slide_pixmaps)
        if self._preview_idx < 0 or total == 0:
            self.slide_counter_label.setText("No Deck Loaded")
            return

        slide_num = self._preview_idx + 1
        self.slide_counter_label.setText(f"Slide {slide_num} / {total}")
        self.prev_btn.setEnabled(self._preview_idx > 0)
        self.next_btn.setEnabled(self._preview_idx < total - 1)

        # Show clearly whether the user is looking at the Active "We Are Here" slide or browsing another slide
        if self._active_idx >= 0:
            active_num = self._active_idx + 1
            self.active_status_btn.setEnabled(True)
            if self._preview_idx == self._active_idx:
                self.active_status_btn.setText(f"📍 We Are Here: Slide {active_num}")
                self.preview_banner_label.setText(f"📍 Viewing Active Class Slide {slide_num}")
            else:
                self.active_status_btn.setText(f"📍 Active: Slide {active_num} (Click to return)")
                self.preview_banner_label.setText(
                    f"👁 Previewing Slide {slide_num} (Class is currently on Slide {active_num})"
                )
        else:
            self.preview_banner_label.setText(f"👁 Previewing Slide {slide_num} (Click '📍 We Are Here' when class reaches this slide)")

        data = self._slide_data.get(self._preview_idx, {"timestamps": [], "notes": "", "strokes": []})
        pix = self._slide_pixmaps[self._preview_idx]
        self.canvas.set_slide(pix, data.get("strokes", []))

        self._is_loading_slide = True
        self.notes_header_label.setText(f"📝 Slide {slide_num} Notes")
        self.slide_notes_edit.setPlainText(data.get("notes", ""))
        ts_list = data.get("timestamps", [])
        if ts_list:
            formatted_ts = ", ".join(f"[{format_timestamp(t)}]" for t in ts_list)
            self.timestamps_badge.setText(f"📍 Marked 'We Are Here' at: {formatted_ts}")
        else:
            self.timestamps_badge.setText("Timestamps: Not marked as 'We Are Here' yet")
        self._is_loading_slide = False

    # ==========================
    # Annotations & Persistence
    # ==========================
    def _on_tool_changed(self, index: int):
        modes = ["pen", "highlighter", "none"]
        if 0 <= index < len(modes):
            self.canvas.set_tool_mode(modes[index])

    def _on_color_changed(self, index: int):
        color = self.color_combo.currentData()
        if color:
            self.canvas.set_pen_color(color)

    def _save_current_slide_strokes(self):
        if self._is_loading_slide or self._preview_idx < 0:
            return
        entry = self._slide_data.setdefault(
            self._preview_idx, {"timestamps": [], "notes": "", "strokes": []}
        )
        entry["strokes"] = self.canvas.get_strokes()
        self._update_list_item_labels()

    def _on_slide_notes_changed(self):
        if self._is_loading_slide or self._preview_idx < 0:
            return
        entry = self._slide_data.setdefault(
            self._preview_idx, {"timestamps": [], "notes": "", "strokes": []}
        )
        entry["notes"] = self.slide_notes_edit.toPlainText().strip()
        self._update_list_item_labels()

    def get_timeline_markers(self) -> List[Dict[str, Any]]:
        """Return chronological list of all 'We Are Here' slide markers and slide annotations."""
        markers: List[Dict[str, Any]] = []
        for idx, data in sorted(self._slide_data.items()):
            ts_list = data.get("timestamps", [])
            notes = data.get("notes", "").strip()
            strokes = data.get("strokes", [])
            title = self._slide_titles[idx] if idx < len(self._slide_titles) else f"Slide {idx + 1}"
            if ts_list or notes or strokes:
                markers.append({
                    "slide_number": idx + 1,
                    "title": title,
                    "timestamps": ts_list,
                    "first_timestamp": ts_list[0] if ts_list else None,
                    "notes": notes,
                    "ink_strokes_count": len(strokes),
                })
        return markers

    def format_annotations_summary_markdown(self) -> str:
        """Build a Markdown summary of all marked slides, their timestamps, and user annotations."""
        markers = self.get_timeline_markers()
        if not markers:
            return ""

        lines = ["### 📊 Lecture Slide Timeline & Annotations"]
        for m in markers:
            s_num = m["slide_number"]
            title = m["title"]
            ts_list = m["timestamps"]
            ts_str = (
                " @ " + ", ".join(f"[{format_timestamp(t)}]" for t in ts_list)
                if ts_list
                else " (Previewed / Annotated)"
            )
            ink_str = f" *(+{m['ink_strokes_count']} ink annotations)*" if m["ink_strokes_count"] else ""
            lines.append(f"- **Slide {s_num}: {title}**{ts_str}{ink_str}")
            if m["notes"]:
                for note_line in m["notes"].splitlines():
                    lines.append(f"  - 📝 {note_line.strip()}")
        return "\n".join(lines)
