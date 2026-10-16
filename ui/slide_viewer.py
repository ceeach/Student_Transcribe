"""Interactive Class Slide Companion & Rich PDF Annotation Editor for Transcribe Notion.

Allows importing a class PDF during or before a lecture:
- Browse/preview any slide freely without changing the active lecture slide
- Touchpad two-finger horizontal/vertical scroll and keyboard arrows to move between slides
- Bottom-middle navigation bar with Prev, Next, and "We Are Here" timestamp marker
- Rich PDF Annotation Editor:
  - Select / Move tool (reposition or delete annotations, double-click text to edit)
  - Freehand Pen tool with customizable color & thickness
  - Chisel Highlighter tool with translucent marker ink
  - On-Slide Text Input tool (click anywhere on the slide to type text directly onto the slide)
  - Shapes: Rectangle, Circle/Ellipse, and Directional Arrow
  - Object Eraser (click/drag to erase individual strokes, shapes, or text boxes)
  - Undo / Redo stack + Clear All
  - Zoom In / Out / Fit (Ctrl + touchpad scroll) & Export Annotated PDF / Image
"""

import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QColorDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
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
    FluentIcon as FIF,
    IconWidget,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TextEdit,
    TogglePushButton,
    ToggleToolButton,
    ToolButton,
    TransparentPushButton,
    TransparentToolButton,
    isDarkTheme,
)

from logger import get_logger
from transcriber import format_timestamp

logger = get_logger("SlideViewer")


# ============================================================
# Vector Icon Helpers (No Emojis)
# ============================================================
def create_color_swatch_icon(hex_color: str, size: int = 16) -> QIcon:
    """Create a clean circular color swatch QIcon without emojis."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(QPen(QColor("#94a3b8"), 1.2))
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.end()
    return QIcon(pix)


def create_shape_icon(shape_type: str, color_hex: Optional[str] = None, size: int = 18) -> QIcon:
    """Create crisp vector QIcons for editor shape tools (rect, ellipse, arrow)."""
    if color_hex is None:
        color_hex = "#f1f5f9" if isDarkTheme() else "#1e293b"
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color_hex), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    if shape_type == "rect":
        painter.drawRoundedRect(3, 4, size - 6, size - 8, 2, 2)
    elif shape_type == "ellipse":
        painter.drawEllipse(3, 3, size - 6, size - 6)
    elif shape_type == "arrow":
        painter.drawLine(4, size - 4, size - 4, 4)
        painter.drawLine(size - 4, 4, size - 10, 4)
        painter.drawLine(size - 4, 4, size - 4, 10)
    painter.end()
    return QIcon(pix)


# ============================================================
# Rich Annotatable PDF Slide Canvas
# ============================================================
class AnnotatableSlideCanvas(QWidget):
    """Interactive canvas rendering a PDF slide with Pen, Highlighter, Text Input, Shapes, Eraser, Zoom & Touchpad Scroll."""

    annotations_modified = pyqtSignal()
    slide_wheel_navigated = pyqtSignal(int)  # +1 for next slide, -1 for prev slide
    zoom_changed = pyqtSignal(float)         # Emits current zoom factor (e.g. 1.0 = 100%)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._base_pixmap: Optional[QPixmap] = None
        # Unified annotations list (strokes, shapes, text boxes) in normalized [0.0, 1.0] slide coordinates:
        # - Freehand: {"mode": "pen"|"highlighter", "color": str, "width": float, "points": [(nx, ny), ...]}
        # - Shape:    {"mode": "rect"|"ellipse"|"arrow", "color": str, "width": float, "points": [(nx0, ny0), (nx1, ny1)]}
        # - Text:     {"mode": "text", "color": str, "width": float, "points": [(nx, ny)], "text": str, "font_scale": float}
        self._strokes: List[Dict[str, Any]] = []
        self._redo_stack: List[List[Dict[str, Any]]] = []
        self._undo_stack: List[List[Dict[str, Any]]] = []

        self._current_stroke: Optional[Dict[str, Any]] = None
        self._selected_index: int = -1
        self._drag_last_norm: Optional[Tuple[float, float]] = None
        self._is_dragging_selection: bool = False

        # Tool & style state
        self._tool_mode: str = "pen"  # "select", "pen", "highlighter", "text", "rect", "ellipse", "arrow", "eraser", "none"
        self._pen_color: str = "#ef4444"
        self._size_preset: int = 1  # 0=Thin/Small, 1=Medium, 2=Thick/Large, 3=Extra Bold/XL

        # Zoom & Pan state
        self._zoom: float = 1.0
        self._pan_offset: QPointF = QPointF(0.0, 0.0)
        self._is_panning: bool = False
        self._pan_start_pos: QPointF = QPointF(0.0, 0.0)

        # Touchpad scroll debouncing
        self._last_wheel_nav_time: float = 0.0
        self._wheel_accum_x: float = 0.0
        self._wheel_accum_y: float = 0.0

        # Inline floating text input editor on the canvas
        self._inline_editor = QLineEdit(self)
        self._inline_editor.setVisible(False)
        self._inline_editor.setPlaceholderText("Type annotation and press Enter...")
        self._inline_editor.setStyleSheet(
            "QLineEdit {"
            "  background-color: rgba(15, 23, 42, 230);"
            "  color: #f8fafc;"
            "  border: 2px solid #38bdf8;"
            "  border-radius: 6px;"
            "  padding: 4px 8px;"
            "  font-size: 14px;"
            "  font-weight: 600;"
            "}"
        )
        self._inline_editor.returnPressed.connect(self._commit_inline_text)
        self._inline_editor.editingFinished.connect(self._commit_inline_text)
        self._editing_text_norm_pos: Optional[Tuple[float, float]] = None
        self._editing_existing_idx: int = -1

    # ---- State Cloning for Undo/Redo ----
    def _clone_strokes(self) -> List[Dict[str, Any]]:
        cloned = []
        for s in self._strokes:
            item = dict(s)
            if "points" in item:
                item["points"] = list(item["points"])
            cloned.append(item)
        return cloned

    def _push_undo_snapshot(self):
        self._undo_stack.append(self._clone_strokes())
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    # ---- Tool & Style Configuration ----
    def set_tool_mode(self, mode: str):
        """Set active editor tool: 'select', 'pen', 'highlighter', 'text', 'rect', 'ellipse', 'arrow', 'eraser', 'none'."""
        if self._inline_editor.isVisible():
            self._commit_inline_text()
        self._tool_mode = mode
        self._selected_index = -1
        if mode in ("pen", "highlighter", "rect", "ellipse", "arrow"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif mode == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif mode == "eraser":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif mode == "select":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_pen_color(self, hex_color: str):
        self._pen_color = hex_color
        # If an annotation is currently selected in 'select' mode, update its color live
        if 0 <= self._selected_index < len(self._strokes):
            self._push_undo_snapshot()
            self._strokes[self._selected_index]["color"] = hex_color
            self.annotations_modified.emit()
            self.update()

    def set_size_preset(self, preset_index: int):
        """0 = Thin/Small, 1 = Medium, 2 = Thick/Large, 3 = XL."""
        self._size_preset = max(0, min(3, int(preset_index)))
        if 0 <= self._selected_index < len(self._strokes):
            self._push_undo_snapshot()
            sel = self._strokes[self._selected_index]
            if sel.get("mode") == "text":
                sel["font_scale"] = self._get_font_scale()
            else:
                sel["width"] = self._get_relative_stroke_width(sel.get("mode", "pen"))
            self.annotations_modified.emit()
            self.update()

    def _get_relative_stroke_width(self, mode: str) -> float:
        if mode == "highlighter":
            return [0.018, 0.028, 0.042, 0.058][self._size_preset]
        return [0.0025, 0.0045, 0.0075, 0.012][self._size_preset]

    def _get_font_scale(self) -> float:
        return [0.022, 0.028, 0.038, 0.050][self._size_preset]

    # ---- Zoom Controls ----
    def set_zoom(self, zoom_factor: float):
        new_zoom = max(0.5, min(3.5, float(zoom_factor)))
        if abs(new_zoom - 1.0) < 0.05:
            new_zoom = 1.0
            self._pan_offset = QPointF(0.0, 0.0)
        self._zoom = new_zoom
        self.zoom_changed.emit(self._zoom)
        self.update()

    def zoom_in(self):
        self.set_zoom(self._zoom + 0.25)

    def zoom_out(self):
        self.set_zoom(self._zoom - 0.25)

    def reset_zoom(self):
        self._zoom = 1.0
        self._pan_offset = QPointF(0.0, 0.0)
        self.zoom_changed.emit(self._zoom)
        self.update()

    # ---- Slide & Annotation Data ----
    def set_slide(self, pixmap: Optional[QPixmap], strokes: Optional[List[Dict[str, Any]]] = None):
        if self._inline_editor.isVisible():
            self._commit_inline_text()
        self._base_pixmap = pixmap
        self._strokes = []
        if strokes:
            for s in strokes:
                item = dict(s)
                if "points" in item:
                    item["points"] = list(item["points"])
                self._strokes.append(item)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._current_stroke = None
        self._selected_index = -1
        self.update()

    def get_strokes(self) -> List[Dict[str, Any]]:
        return self._clone_strokes()

    def add_text_annotation(self, text: str, nx: float = 0.5, ny: float = 0.5, color: Optional[str] = None) -> Dict[str, Any]:
        """Programmatically add an on-slide text annotation at normalized coordinates (nx, ny)."""
        clean = text.strip()
        if not clean:
            return {}
        self._push_undo_snapshot()
        entry = {
            "mode": "text",
            "color": color or self._pen_color,
            "width": 0.004,
            "font_scale": self._get_font_scale(),
            "points": [(round(nx, 4), round(ny, 4))],
            "text": clean,
        }
        self._strokes.append(entry)
        self.annotations_modified.emit()
        self.update()
        return entry

    def undo_stroke(self):
        if self._inline_editor.isVisible():
            self._commit_inline_text()
        if self._undo_stack:
            self._redo_stack.append(self._clone_strokes())
            self._strokes = self._undo_stack.pop()
            self._selected_index = -1
            self.annotations_modified.emit()
            self.update()
        elif self._strokes:
            self._redo_stack.append(self._clone_strokes())
            self._strokes.pop()
            self._selected_index = -1
            self.annotations_modified.emit()
            self.update()

    def redo_stroke(self):
        if self._redo_stack:
            self._undo_stack.append(self._clone_strokes())
            self._strokes = self._redo_stack.pop()
            self._selected_index = -1
            self.annotations_modified.emit()
            self.update()

    def clear_strokes(self):
        if self._inline_editor.isVisible():
            self._inline_editor.hide()
        if self._strokes:
            self._push_undo_snapshot()
            self._strokes.clear()
            self._selected_index = -1
            self.annotations_modified.emit()
            self.update()

    # ---- Coordinate Geometry ----
    def _get_target_rect(self) -> QRectF:
        """Compute aspect-ratio-preserving target rectangle for the slide inside this widget (with zoom & pan)."""
        if not self._base_pixmap or self._base_pixmap.isNull():
            return QRectF(0, 0, self.width(), self.height())

        pw = float(self._base_pixmap.width())
        ph = float(self._base_pixmap.height())
        ww = float(self.width())
        wh = float(self.height())
        if pw <= 0 or ph <= 0 or ww <= 0 or wh <= 0:
            return QRectF(0, 0, ww, wh)

        padding = 12.0
        avail_w = max(40.0, ww - padding * 2)
        avail_h = max(40.0, wh - padding * 2)
        scale = min(avail_w / pw, avail_h / ph) * self._zoom
        tw = pw * scale
        th = ph * scale
        tx = (ww - tw) / 2.0 + self._pan_offset.x()
        ty = (wh - th) / 2.0 + self._pan_offset.y()
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

    def _normalized_to_screen(self, nx: float, ny: float, rect: Optional[QRectF] = None) -> QPointF:
        r = rect or self._get_target_rect()
        return QPointF(r.left() + nx * r.width(), r.top() + ny * r.height())

    # ---- Hit Testing for Select & Eraser Tools ----
    def _get_annotation_bounding_norm_rect(self, stroke: Dict[str, Any], target_rect: QRectF) -> QRectF:
        mode = stroke.get("mode", "pen")
        pts = stroke.get("points", [])
        if not pts:
            return QRectF()

        if mode == "text":
            nx, ny = pts[0]
            text_str = stroke.get("text", "")
            font_scale = float(stroke.get("font_scale", 0.028))
            pixel_font = max(11.0, font_scale * target_rect.height())
            font = QFont("Segoe UI", -1)
            font.setPixelSize(int(pixel_font))
            font.setBold(True)
            fm = QFontMetricsF(font)
            lines = text_str.splitlines() or [text_str]
            max_w = max((fm.horizontalAdvance(line) for line in lines), default=40.0) + 16.0
            total_h = fm.lineSpacing() * len(lines) + 10.0
            nw = max_w / max(1.0, target_rect.width())
            nh = total_h / max(1.0, target_rect.height())
            return QRectF(nx, ny, nw, nh)

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        pad = max(0.015, float(stroke.get("width", 0.005)))
        return QRectF(min_x - pad, min_y - pad, (max_x - min_x) + pad * 2, (max_y - min_y) + pad * 2)

    def _hit_test_annotation(self, norm_pos: Tuple[float, float]) -> int:
        """Return index of top-most annotation near norm_pos, or -1 if none."""
        target_rect = self._get_target_rect()
        px, py = norm_pos
        for idx in range(len(self._strokes) - 1, -1, -1):
            stroke = self._strokes[idx]
            mode = stroke.get("mode", "pen")
            pts = stroke.get("points", [])
            if not pts:
                continue
            bbox = self._get_annotation_bounding_norm_rect(stroke, target_rect)
            if mode in ("text", "rect", "ellipse", "arrow"):
                if bbox.contains(QPointF(px, py)):
                    return idx
            else:
                # Check point proximity for freehand pen/highlighter strokes
                threshold = max(0.022, float(stroke.get("width", 0.005)) * 1.4)
                for sx, sy in pts:
                    if math.hypot(px - sx, py - sy) <= threshold:
                        return idx
        return -1

    # ---- Inline Text Editor Overlay ----
    def _open_inline_text_editor(self, norm_pos: Tuple[float, float], existing_idx: int = -1):
        self._editing_text_norm_pos = norm_pos
        self._editing_existing_idx = existing_idx
        screen_pt = self._normalized_to_screen(norm_pos[0], norm_pos[1])

        initial_text = ""
        if 0 <= existing_idx < len(self._strokes):
            initial_text = self._strokes[existing_idx].get("text", "")

        editor_w = min(320, max(180, self.width() - int(screen_pt.x()) - 16))
        editor_x = max(8, min(int(screen_pt.x()), self.width() - editor_w - 8))
        editor_y = max(8, min(int(screen_pt.y()) - 14, self.height() - 42))

        self._inline_editor.setGeometry(editor_x, editor_y, editor_w, 34)
        self._inline_editor.setText(initial_text)
        self._inline_editor.setVisible(True)
        self._inline_editor.setFocus()
        self._inline_editor.selectAll()

    def _commit_inline_text(self):
        if not self._inline_editor.isVisible():
            return
        text_val = self._inline_editor.text().strip()
        norm_pos = self._editing_text_norm_pos
        existing_idx = self._editing_existing_idx

        self._inline_editor.hide()
        self._editing_text_norm_pos = None
        self._editing_existing_idx = -1

        if 0 <= existing_idx < len(self._strokes):
            self._push_undo_snapshot()
            if text_val:
                self._strokes[existing_idx]["text"] = text_val
                self._strokes[existing_idx]["color"] = self._pen_color
            else:
                self._strokes.pop(existing_idx)
            self.annotations_modified.emit()
            self.update()
        elif text_val and norm_pos is not None:
            self.add_text_annotation(text_val, norm_pos[0], norm_pos[1], self._pen_color)

    # ---- Mouse & Touchpad Events ----
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._base_pixmap is not None:
            norm = self._pos_to_normalized(event.position())
            if norm:
                hit_idx = self._hit_test_annotation(norm)
                if hit_idx >= 0 and self._strokes[hit_idx].get("mode") == "text":
                    pts = self._strokes[hit_idx].get("points", [norm])
                    self._open_inline_text_editor(pts[0], existing_idx=hit_idx)
                    return
                elif self._tool_mode in ("select", "text", "none"):
                    self._open_inline_text_editor(norm, existing_idx=-1)
                    return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        self.setFocus()
        if self._inline_editor.isVisible():
            self._commit_inline_text()

        if self._base_pixmap is None:
            super().mousePressEvent(event)
            return

        # Middle mouse button or Shift+LeftClick pans when zoomed in
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier or self._tool_mode == "none")
        ):
            self._is_panning = True
            self._pan_start_pos = event.position() - self._pan_offset
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        norm = self._pos_to_normalized(event.position())
        if not norm:
            return

        # 1. Text Input Tool
        if self._tool_mode == "text":
            hit_idx = self._hit_test_annotation(norm)
            if hit_idx >= 0 and self._strokes[hit_idx].get("mode") == "text":
                pts = self._strokes[hit_idx].get("points", [norm])
                self._open_inline_text_editor(pts[0], existing_idx=hit_idx)
            else:
                self._open_inline_text_editor(norm, existing_idx=-1)
            return

        # 2. Eraser Tool
        if self._tool_mode == "eraser":
            hit_idx = self._hit_test_annotation(norm)
            if hit_idx >= 0:
                self._push_undo_snapshot()
                self._strokes.pop(hit_idx)
                self._selected_index = -1
                self.annotations_modified.emit()
                self.update()
            return

        # 3. Select / Move Tool
        if self._tool_mode == "select":
            hit_idx = self._hit_test_annotation(norm)
            self._selected_index = hit_idx
            if hit_idx >= 0:
                self._push_undo_snapshot()
                self._is_dragging_selection = True
                self._drag_last_norm = norm
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            return

        # 4. Freehand Pen / Highlighter or Shapes (rect, ellipse, arrow)
        if self._tool_mode in ("pen", "highlighter", "rect", "ellipse", "arrow"):
            self._push_undo_snapshot()
            color = self._pen_color
            if self._tool_mode == "highlighter" and self._pen_color == "#ef4444":
                # Default highlighter to bright yellow unless user chose another color
                color = "#facc15"
            self._current_stroke = {
                "mode": self._tool_mode,
                "color": color,
                "width": self._get_relative_stroke_width(self._tool_mode),
                "points": [norm, norm] if self._tool_mode in ("rect", "ellipse", "arrow") else [norm],
            }
            self.update()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_panning and (event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton)):
            self._pan_offset = event.position() - self._pan_start_pos
            self.update()
            return

        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return

        norm = self._pos_to_normalized(event.position())
        if not norm:
            return

        if self._tool_mode == "eraser":
            hit_idx = self._hit_test_annotation(norm)
            if hit_idx >= 0:
                self._push_undo_snapshot()
                self._strokes.pop(hit_idx)
                self.annotations_modified.emit()
                self.update()
            return

        if self._tool_mode == "select" and self._is_dragging_selection and 0 <= self._selected_index < len(self._strokes):
            if self._drag_last_norm:
                dx = norm[0] - self._drag_last_norm[0]
                dy = norm[1] - self._drag_last_norm[1]
                stroke = self._strokes[self._selected_index]
                new_pts = []
                for px, py in stroke.get("points", []):
                    nx = max(0.0, min(1.0, round(px + dx, 4)))
                    ny = max(0.0, min(1.0, round(py + dy, 4)))
                    new_pts.append((nx, ny))
                stroke["points"] = new_pts
                self._drag_last_norm = norm
                self.update()
            return

        if self._current_stroke is not None:
            mode = self._current_stroke.get("mode", "pen")
            if mode in ("rect", "ellipse", "arrow"):
                self._current_stroke["points"][1] = norm
            else:
                self._current_stroke["points"].append(norm)
            self.update()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._is_panning:
            self._is_panning = False
            self.set_tool_mode(self._tool_mode)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_dragging_selection:
                self._is_dragging_selection = False
                self._drag_last_norm = None
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                self.annotations_modified.emit()
                self.update()
                return

            if self._current_stroke is not None:
                pts = self._current_stroke.get("points", [])
                if len(pts) > 0:
                    self._strokes.append(self._current_stroke)
                    self.annotations_modified.emit()
                self._current_stroke = None
                self.update()
                return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        """Support touchpad two-finger horizontal & vertical scroll to flip slides, and Ctrl+Scroll to zoom."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y()
            if delta_y > 0:
                self.zoom_in()
            elif delta_y < 0:
                self.zoom_out()
            event.accept()
            return

        # Two-finger touchpad scroll (horizontal or vertical) to navigate slides
        ad = event.angleDelta()
        pd = event.pixelDelta()

        dx = float(ad.x()) if ad.x() != 0 else float(pd.x()) * 2.0
        dy = float(ad.y()) if ad.y() != 0 else float(pd.y()) * 2.0

        now = time.monotonic()
        if now - self._last_wheel_nav_time < 0.22:
            event.accept()
            return

        self._wheel_accum_x += dx
        self._wheel_accum_y += dy

        threshold = 40.0
        dominant = self._wheel_accum_x if abs(self._wheel_accum_x) >= abs(self._wheel_accum_y) else self._wheel_accum_y

        if abs(dominant) >= threshold:
            self._last_wheel_nav_time = now
            self._wheel_accum_x = 0.0
            self._wheel_accum_y = 0.0
            # Negative delta (swipe left / scroll down) -> Next slide (+1)
            # Positive delta (swipe right / scroll up)  -> Previous slide (-1)
            if dominant < 0:
                self.slide_wheel_navigated.emit(1)
            else:
                self.slide_wheel_navigated.emit(-1)
            event.accept()
            return

        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if self._inline_editor.isVisible():
            if event.key() == Qt.Key.Key_Escape:
                self._inline_editor.hide()
                return
            super().keyPressEvent(event)
            return

        if event.matches(Qt.Key.Key_Undo) or (
            event.key() == Qt.Key.Key_Z and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            self.undo_stroke()
            event.accept()
            return

        if event.matches(Qt.Key.Key_Redo) or (
            event.key() == Qt.Key.Key_Y and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            self.redo_stroke()
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and 0 <= self._selected_index < len(self._strokes):
            self._push_undo_snapshot()
            self._strokes.pop(self._selected_index)
            self._selected_index = -1
            self.annotations_modified.emit()
            self.update()
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.slide_wheel_navigated.emit(1)
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.slide_wheel_navigated.emit(-1)
            event.accept()
            return

        super().keyPressEvent(event)

    # ---- Rendering ----
    def render_annotations_onto_painter(self, painter: QPainter, target_rect: QRectF, include_selection: bool = False):
        """Render all strokes, highlighter marks, shapes, and text boxes onto a QPainter within target_rect."""
        all_strokes = list(self._strokes)
        if self._current_stroke:
            all_strokes.append(self._current_stroke)

        for idx, stroke in enumerate(all_strokes):
            pts = stroke.get("points", [])
            if not pts:
                continue
            mode = stroke.get("mode", "pen")
            color_str = stroke.get("color", "#ef4444")
            rel_width = float(stroke.get("width", 0.0045))

            color = QColor(color_str)
            if mode == "highlighter":
                color.setAlpha(95)
            else:
                color.setAlpha(240)

            pixel_width = max(2.0, rel_width * target_rect.width())
            cap_style = Qt.PenCapStyle.SquareCap if mode == "highlighter" else Qt.PenCapStyle.RoundCap
            pen = QPen(
                color,
                pixel_width,
                Qt.PenStyle.SolidLine,
                cap_style,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            if mode == "text":
                text_str = stroke.get("text", "")
                if not text_str:
                    continue
                nx, ny = pts[0]
                sx = target_rect.left() + nx * target_rect.width()
                sy = target_rect.top() + ny * target_rect.height()
                font_scale = float(stroke.get("font_scale", 0.028))
                pixel_font = max(12, int(font_scale * target_rect.height()))
                font = QFont("Segoe UI", -1)
                font.setPixelSize(pixel_font)
                font.setBold(True)
                painter.setFont(font)

                fm = QFontMetricsF(font)
                lines = text_str.splitlines() or [text_str]
                max_w = max((fm.horizontalAdvance(line) for line in lines), default=20.0)
                total_h = fm.lineSpacing() * len(lines)

                bg_rect = QRectF(sx - 6, sy - 4, max_w + 14, total_h + 8)
                painter.save()
                bg_color = QColor("#0f172a")
                bg_color.setAlpha(185)
                painter.setBrush(bg_color)
                border_pen = QPen(color, 1.5)
                painter.setPen(border_pen)
                painter.drawRoundedRect(bg_rect, 5.0, 5.0)

                painter.setPen(QColor(color_str))
                painter.drawText(
                    QRectF(sx + 1, sy, max_w + 8, total_h + 4),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    text_str,
                )
                painter.restore()

            elif mode == "rect" and len(pts) >= 2:
                x0 = target_rect.left() + pts[0][0] * target_rect.width()
                y0 = target_rect.top() + pts[0][1] * target_rect.height()
                x1 = target_rect.left() + pts[1][0] * target_rect.width()
                y1 = target_rect.top() + pts[1][1] * target_rect.height()
                r = QRectF(QPointF(min(x0, x1), min(y0, y1)), QPointF(max(x0, x1), max(y0, y1)))
                painter.drawRoundedRect(r, 4.0, 4.0)

            elif mode == "ellipse" and len(pts) >= 2:
                x0 = target_rect.left() + pts[0][0] * target_rect.width()
                y0 = target_rect.top() + pts[0][1] * target_rect.height()
                x1 = target_rect.left() + pts[1][0] * target_rect.width()
                y1 = target_rect.top() + pts[1][1] * target_rect.height()
                r = QRectF(QPointF(min(x0, x1), min(y0, y1)), QPointF(max(x0, x1), max(y0, y1)))
                painter.drawEllipse(r)

            elif mode == "arrow" and len(pts) >= 2:
                x0 = target_rect.left() + pts[0][0] * target_rect.width()
                y0 = target_rect.top() + pts[0][1] * target_rect.height()
                x1 = target_rect.left() + pts[1][0] * target_rect.width()
                y1 = target_rect.top() + pts[1][1] * target_rect.height()
                painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

                angle = math.atan2(y1 - y0, x1 - x0)
                head_len = max(10.0, pixel_width * 3.8)
                arrow_angle = math.pi / 6.0
                p1 = QPointF(
                    x1 - head_len * math.cos(angle - arrow_angle),
                    y1 - head_len * math.sin(angle - arrow_angle),
                )
                p2 = QPointF(
                    x1 - head_len * math.cos(angle + arrow_angle),
                    y1 - head_len * math.sin(angle + arrow_angle),
                )
                painter.save()
                painter.setBrush(color)
                painter.drawPolygon(QPolygonF([QPointF(x1, y1), p1, p2]))
                painter.restore()

            else:
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

            # Draw selection outline if selected
            if include_selection and idx == self._selected_index and idx < len(self._strokes):
                norm_bbox = self._get_annotation_bounding_norm_rect(stroke, target_rect)
                sel_rect = QRectF(
                    target_rect.left() + norm_bbox.left() * target_rect.width() - 4,
                    target_rect.top() + norm_bbox.top() * target_rect.height() - 4,
                    norm_bbox.width() * target_rect.width() + 8,
                    norm_bbox.height() * target_rect.height() + 8,
                )
                painter.save()
                sel_pen = QPen(QColor("#38bdf8"), 1.5, Qt.PenStyle.DashLine)
                painter.setPen(sel_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(sel_rect, 4.0, 4.0)
                painter.restore()

    def render_annotated_slide_pixmap(self) -> Optional[QPixmap]:
        """Render the current slide along with all annotations into a full-resolution QPixmap."""
        if not self._base_pixmap or self._base_pixmap.isNull():
            return None
        result = self._base_pixmap.copy()
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        full_rect = QRectF(0, 0, result.width(), result.height())
        self.render_annotations_onto_painter(painter, full_rect, include_selection=False)
        painter.end()
        return result

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        painter.fillRect(self.rect(), QColor("#0f172a"))

        if not self._base_pixmap or self._base_pixmap.isNull():
            painter.setPen(QColor("#64748b"))
            font = QFont("Segoe UI", 11)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Load a Class PDF Deck to preview slides, scroll with your touchpad,\n"
                "mark 'We Are Here' during lecture, and annotate with Pen, Highlighter, Text, and Shapes.",
            )
            painter.end()
            return

        target_rect = self._get_target_rect()
        painter.drawPixmap(target_rect.toRect(), self._base_pixmap)
        self.render_annotations_onto_painter(painter, target_rect, include_selection=True)
        painter.end()


# ============================================================
# Main Slide Companion & Full-Screen PDF Editor Widget
# ============================================================
# ============================================================
# Slide Deck Model for Multi-Deck Class Support
# ============================================================
class SlideDeck:
    """Represents an imported PDF slide deck in a class lecture session."""

    def __init__(self, pdf_path: str, name: str, pixmaps: List[QPixmap], titles: List[str]):
        self.pdf_path: str = pdf_path
        self.name: str = name
        self.pixmaps: List[QPixmap] = pixmaps
        self.titles: List[str] = titles
        self.preview_idx: int = 0 if pixmaps else -1
        self.active_idx: int = -1
        self.slide_data: Dict[int, Dict[str, Any]] = {
            i: {"timestamps": [], "notes": "", "strokes": []} for i in range(len(pixmaps))
        }


# ============================================================
# Main Slide Companion & Full-Screen PDF Editor Widget
# ============================================================
class SlideCompanionWidget(QWidget):
    """Live Lecture Slide Companion with multi-deck support, full-screen PDF Editor, bottom-middle navigation, and touchpad slide scrolling."""

    pdf_imported_for_jargon = pyqtSignal(str)       # Emits PDF path so MainWindow extracts OCR/jargon
    slide_marked_here = pyqtSignal(int, float, str)  # (slide_number_1based, timestamp_sec, slide_title)

    # Tool index mapping for tool_combo / tool buttons
    TOOL_MODES = ["pen", "highlighter", "text", "rect", "ellipse", "arrow", "select", "eraser", "none"]

    def __init__(self, time_provider=None, parent=None):
        super().__init__(parent)
        self._time_provider = time_provider or (lambda: 0.0)
        self._decks: List[SlideDeck] = []
        self._current_deck_idx: int = -1
        self._is_loading_slide = False

        self._init_ui()

    # ---- Backwards Compatible Deck & Slide Properties ----
    @property
    def current_deck(self) -> Optional[SlideDeck]:
        if 0 <= self._current_deck_idx < len(self._decks):
            return self._decks[self._current_deck_idx]
        return None

    @property
    def _pdf_path(self) -> Optional[str]:
        deck = self.current_deck
        return deck.pdf_path if deck else None

    @property
    def _slide_pixmaps(self) -> List[QPixmap]:
        deck = self.current_deck
        return deck.pixmaps if deck else []

    @property
    def _slide_titles(self) -> List[str]:
        deck = self.current_deck
        return deck.titles if deck else []

    @property
    def _preview_idx(self) -> int:
        deck = self.current_deck
        return deck.preview_idx if deck else -1

    @_preview_idx.setter
    def _preview_idx(self, val: int):
        deck = self.current_deck
        if deck:
            deck.preview_idx = val

    @property
    def _active_idx(self) -> int:
        deck = self.current_deck
        return deck.active_idx if deck else -1

    @_active_idx.setter
    def _active_idx(self, val: int):
        deck = self.current_deck
        if deck:
            deck.active_idx = val

    @property
    def _slide_data(self) -> Dict[int, Dict[str, Any]]:
        deck = self.current_deck
        return deck.slide_data if deck else {}

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 2, 0, 2)
        main_layout.setSpacing(6)

        # ---- Top Bar: Add Slide Deck, Deck Selector, Remove Deck, Export, Banner, Live Timer, Full Screen ----
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(4, 2, 4, 2)
        top_bar.setSpacing(6)

        self.open_pdf_btn = PushButton("Add Slide Deck...", self)
        self.open_pdf_btn.setIcon(FIF.FOLDER_ADD)
        self.open_pdf_btn.setFixedHeight(32)
        self.open_pdf_btn.setToolTip("Add one or more PDF slide decks for this class lecture session")
        self.open_pdf_btn.clicked.connect(self._prompt_open_pdf)
        top_bar.addWidget(self.open_pdf_btn)

        # Multi-deck selector dropdown
        self.deck_combo = ComboBox(self)
        self.deck_combo.setFixedHeight(32)
        self.deck_combo.setMinimumWidth(160)
        self.deck_combo.setToolTip("Switch between loaded class slide decks")
        self.deck_combo.currentIndexChanged.connect(self._on_deck_combo_changed)
        top_bar.addWidget(self.deck_combo)

        self.remove_deck_btn = TransparentToolButton(FIF.DELETE, self)
        self.remove_deck_btn.setFixedSize(32, 32)
        self.remove_deck_btn.setEnabled(False)
        self.remove_deck_btn.setToolTip("Remove current slide deck from class session")
        self.remove_deck_btn.clicked.connect(self.remove_current_deck)
        top_bar.addWidget(self.remove_deck_btn)

        self.export_pdf_btn = PushButton("Export Annotated PDF...", self)
        self.export_pdf_btn.setIcon(FIF.SAVE_AS)
        self.export_pdf_btn.setFixedHeight(32)
        self.export_pdf_btn.setEnabled(False)
        self.export_pdf_btn.setToolTip("Save a copy of the PDF (or current slide PNG) with all your highlights, drawings, and text burned in")
        self.export_pdf_btn.clicked.connect(self._prompt_export_annotated)
        top_bar.addWidget(self.export_pdf_btn)

        self.preview_banner_label = CaptionLabel("No slide deck loaded", self)
        top_bar.addWidget(self.preview_banner_label, 1)

        # Live Recording Timer indicator while in full-screen slides mode
        self.timer_icon = IconWidget(FIF.STOP_WATCH, self)
        self.timer_icon.setFixedSize(16, 16)
        top_bar.addWidget(self.timer_icon)

        self.live_timer_badge = StrongBodyLabel("00:00", self)
        self.live_timer_badge.setToolTip("Current live recording timestamp used when marking 'We Are Here'")
        top_bar.addWidget(self.live_timer_badge)

        # Full-Screen OS Window Toggle Button
        self.fullscreen_btn = PushButton("Full Screen", self)
        self.fullscreen_btn.setIcon(FIF.FULL_SCREEN)
        self.fullscreen_btn.setFixedHeight(32)
        self.fullscreen_btn.setToolTip("Toggle full-screen lecture mode")
        self.fullscreen_btn.clicked.connect(self.toggle_window_fullscreen)
        top_bar.addWidget(self.fullscreen_btn)

        main_layout.addLayout(top_bar)

        # ---- Main Body Splitter: Left Slide List | Center PDF Editor & Bottom Nav | Right Notes ----
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(True)

        # 1. Left: Slide List (for fast preview browsing)
        self.slide_list = ListWidget(self)
        self.slide_list.setMinimumWidth(180)
        self.slide_list.setMaximumWidth(320)
        self.slide_list.currentRowChanged.connect(self._on_list_row_changed)
        self.splitter.addWidget(self.slide_list)

        # 2. Center: Upgraded PDF Editor Card + Bottom-Middle Navigation Bar
        canvas_card = CardWidget(self)
        canvas_layout = QVBoxLayout(canvas_card)
        canvas_layout.setContentsMargins(6, 6, 6, 6)
        canvas_layout.setSpacing(4)

        # ---- Tier 1: Drawing & Shape Tools + Color & Size Pickers ----
        draw_bar_1 = QHBoxLayout()
        draw_bar_1.setContentsMargins(0, 0, 0, 0)
        draw_bar_1.setSpacing(4)

        # Compact tool buttons with icons and tooltips using ToggleToolButton
        self.pen_tool_btn = ToggleToolButton(FIF.PENCIL_INK, self)
        self.pen_tool_btn.setFixedSize(36, 32)
        self.pen_tool_btn.setChecked(True)
        self.pen_tool_btn.setToolTip("Pen (Freehand Drawing)")
        self.pen_tool_btn.clicked.connect(lambda: self._select_editor_tool("pen"))
        draw_bar_1.addWidget(self.pen_tool_btn)

        self.highlighter_tool_btn = ToggleToolButton(FIF.HIGHTLIGHT, self)
        self.highlighter_tool_btn.setFixedSize(36, 32)
        self.highlighter_tool_btn.setToolTip("Highlighter (Translucent Marker)")
        self.highlighter_tool_btn.clicked.connect(lambda: self._select_editor_tool("highlighter"))
        draw_bar_1.addWidget(self.highlighter_tool_btn)

        self.text_tool_btn = ToggleToolButton(FIF.FONT, self)
        self.text_tool_btn.setFixedSize(36, 32)
        self.text_tool_btn.setToolTip("Text Box (Click slide to type text, double-click existing text to edit)")
        self.text_tool_btn.clicked.connect(lambda: self._select_editor_tool("text"))
        draw_bar_1.addWidget(self.text_tool_btn)

        self.rect_tool_btn = ToggleToolButton(create_shape_icon("rect"), self)
        self.rect_tool_btn.setFixedSize(36, 32)
        self.rect_tool_btn.setToolTip("Box / Rectangle Shape")
        self.rect_tool_btn.clicked.connect(lambda: self._select_editor_tool("rect"))
        draw_bar_1.addWidget(self.rect_tool_btn)

        self.ellipse_tool_btn = ToggleToolButton(create_shape_icon("ellipse"), self)
        self.ellipse_tool_btn.setFixedSize(36, 32)
        self.ellipse_tool_btn.setToolTip("Circle / Ellipse Shape")
        self.ellipse_tool_btn.clicked.connect(lambda: self._select_editor_tool("ellipse"))
        draw_bar_1.addWidget(self.ellipse_tool_btn)

        self.arrow_tool_btn = ToggleToolButton(create_shape_icon("arrow"), self)
        self.arrow_tool_btn.setFixedSize(36, 32)
        self.arrow_tool_btn.setToolTip("Arrow Shape")
        self.arrow_tool_btn.clicked.connect(lambda: self._select_editor_tool("arrow"))
        draw_bar_1.addWidget(self.arrow_tool_btn)

        self.select_tool_btn = ToggleToolButton(FIF.MOVE, self)
        self.select_tool_btn.setFixedSize(36, 32)
        self.select_tool_btn.setToolTip("Move / Select Tool (Drag to reposition, Delete key to remove)")
        self.select_tool_btn.clicked.connect(lambda: self._select_editor_tool("select"))
        draw_bar_1.addWidget(self.select_tool_btn)

        self.eraser_tool_btn = ToggleToolButton(FIF.ERASE_TOOL, self)
        self.eraser_tool_btn.setFixedSize(36, 32)
        self.eraser_tool_btn.setToolTip("Eraser (Click or drag over annotations to delete)")
        self.eraser_tool_btn.clicked.connect(lambda: self._select_editor_tool("eraser"))
        draw_bar_1.addWidget(self.eraser_tool_btn)

        self._tool_buttons = {
            "pen": self.pen_tool_btn,
            "highlighter": self.highlighter_tool_btn,
            "text": self.text_tool_btn,
            "rect": self.rect_tool_btn,
            "ellipse": self.ellipse_tool_btn,
            "arrow": self.arrow_tool_btn,
            "select": self.select_tool_btn,
            "eraser": self.eraser_tool_btn,
        }

        self.tool_combo = ComboBox(self)
        self.tool_combo.addItems(["Pen", "Highlighter", "Text", "Box", "Circle", "Arrow", "Move", "Eraser", "View Only"])
        self.tool_combo.setVisible(False)
        self.tool_combo.currentIndexChanged.connect(self._on_tool_changed)

        draw_bar_1.addStretch(1)

        # Color Selector with swatches
        self.color_combo = ComboBox(self)
        self.color_combo.setFixedHeight(30)
        self.color_combo.setMinimumWidth(95)
        color_presets = [
            ("Red", "#ef4444"),
            ("Yellow", "#facc15"),
            ("Sky Blue", "#38bdf8"),
            ("Emerald", "#10b981"),
            ("Purple", "#c084fc"),
            ("Orange", "#fb923c"),
            ("White", "#f8fafc"),
        ]
        for label, hex_val in color_presets:
            self.color_combo.addItem(label, icon=create_color_swatch_icon(hex_val), userData=hex_val)
        self.color_combo.currentIndexChanged.connect(self._on_color_changed)
        draw_bar_1.addWidget(self.color_combo)

        self.custom_color_btn = ToolButton(FIF.PALETTE, self)
        self.custom_color_btn.setFixedSize(32, 32)
        self.custom_color_btn.setToolTip("Pick a custom color...")
        self.custom_color_btn.clicked.connect(self._pick_custom_color)
        draw_bar_1.addWidget(self.custom_color_btn)

        # Size Selector
        self.size_combo = ComboBox(self)
        self.size_combo.setFixedHeight(30)
        self.size_combo.setMinimumWidth(85)
        self.size_combo.addItems(["Thin / S", "Medium / M", "Thick / L", "Bold / XL"])
        self.size_combo.setCurrentIndex(1)
        self.size_combo.setToolTip("Stroke thickness & text font size")
        self.size_combo.currentIndexChanged.connect(lambda idx: self.canvas.set_size_preset(idx))
        draw_bar_1.addWidget(self.size_combo)

        canvas_layout.addLayout(draw_bar_1)

        # ---- Tier 2: Undo/Redo/Clear + Zoom + Sidebar Toggles ----
        draw_bar_2 = QHBoxLayout()
        draw_bar_2.setContentsMargins(0, 0, 0, 0)
        draw_bar_2.setSpacing(4)

        self.undo_draw_btn = PushButton("Undo", self)
        self.undo_draw_btn.setIcon(FIF.RETURN)
        self.undo_draw_btn.setFixedHeight(28)
        self.undo_draw_btn.setToolTip("Undo last annotation change (Ctrl+Z)")
        self.undo_draw_btn.clicked.connect(lambda: self.canvas.undo_stroke())
        draw_bar_2.addWidget(self.undo_draw_btn)

        self.redo_draw_btn = PushButton("Redo", self)
        self.redo_draw_btn.setIcon(FIF.ROTATE)
        self.redo_draw_btn.setFixedHeight(28)
        self.redo_draw_btn.setToolTip("Redo annotation change (Ctrl+Y)")
        self.redo_draw_btn.clicked.connect(lambda: self.canvas.redo_stroke())
        draw_bar_2.addWidget(self.redo_draw_btn)

        self.clear_draw_btn = PushButton("Clear", self)
        self.clear_draw_btn.setIcon(FIF.BROOM)
        self.clear_draw_btn.setFixedHeight(28)
        self.clear_draw_btn.setToolTip("Clear all annotations on this slide")
        self.clear_draw_btn.clicked.connect(lambda: self.canvas.clear_strokes())
        draw_bar_2.addWidget(self.clear_draw_btn)

        # Zoom buttons
        self.zoom_out_btn = TransparentToolButton(FIF.ZOOM_OUT, self)
        self.zoom_out_btn.setFixedSize(30, 30)
        self.zoom_out_btn.setToolTip("Zoom Out (Ctrl + Scroll Down)")
        self.zoom_out_btn.clicked.connect(lambda: self.canvas.zoom_out())
        draw_bar_2.addWidget(self.zoom_out_btn)

        self.zoom_reset_btn = TransparentPushButton("100%", self)
        self.zoom_reset_btn.setFixedHeight(30)
        self.zoom_reset_btn.setToolTip("Reset Zoom to Fit Slide")
        self.zoom_reset_btn.clicked.connect(lambda: self.canvas.reset_zoom())
        draw_bar_2.addWidget(self.zoom_reset_btn)

        self.zoom_in_btn = TransparentToolButton(FIF.ZOOM_IN, self)
        self.zoom_in_btn.setFixedSize(30, 30)
        self.zoom_in_btn.setToolTip("Zoom In (Ctrl + Scroll Up)")
        self.zoom_in_btn.clicked.connect(lambda: self.canvas.zoom_in())
        draw_bar_2.addWidget(self.zoom_in_btn)

        draw_bar_2.addStretch(1)

        # Quick Pane Toggles
        self.toggle_deck_btn = PushButton("Deck", self)
        self.toggle_deck_btn.setIcon(FIF.TILES)
        self.toggle_deck_btn.setFixedHeight(28)
        self.toggle_deck_btn.setToolTip("Toggle Left Slide Deck Pane")
        self.toggle_deck_btn.clicked.connect(self._toggle_deck_visibility)
        draw_bar_2.addWidget(self.toggle_deck_btn)

        self.toggle_notes_btn = PushButton("Notes", self)
        self.toggle_notes_btn.setIcon(FIF.QUICK_NOTE)
        self.toggle_notes_btn.setFixedHeight(28)
        self.toggle_notes_btn.setToolTip("Toggle Right Slide Notes Pane")
        self.toggle_notes_btn.clicked.connect(self._toggle_notes_visibility)
        draw_bar_2.addWidget(self.toggle_notes_btn)

        canvas_layout.addLayout(draw_bar_2)

        # ---- Slide Canvas ----
        self.canvas = AnnotatableSlideCanvas(self)
        self.canvas.annotations_modified.connect(self._save_current_slide_strokes)
        self.canvas.slide_wheel_navigated.connect(self._on_canvas_wheel_navigated)
        self.canvas.zoom_changed.connect(lambda z: self.zoom_reset_btn.setText(f"{int(round(z * 100))}%"))
        canvas_layout.addWidget(self.canvas, 1)

        # ---- Bottom-Middle Navigation & "We Are Here" Control Bar ----
        bottom_nav_bar = QHBoxLayout()
        bottom_nav_bar.setContentsMargins(6, 2, 6, 2)
        bottom_nav_bar.setSpacing(10)
        bottom_nav_bar.addStretch(1)

        self.prev_btn = PushButton("Previous", self)
        self.prev_btn.setIcon(FIF.PAGE_LEFT)
        self.prev_btn.setFixedHeight(32)
        self.prev_btn.setEnabled(False)
        self.prev_btn.setToolTip("Preview previous slide (Left Arrow or Touchpad Scroll Left/Up)")
        self.prev_btn.clicked.connect(self.preview_prev_slide)
        bottom_nav_bar.addWidget(self.prev_btn)

        self.slide_counter_label = StrongBodyLabel("No Deck", self)
        self.slide_counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slide_counter_label.setMinimumWidth(85)
        bottom_nav_bar.addWidget(self.slide_counter_label)

        self.next_btn = PushButton("Next", self)
        self.next_btn.setIcon(FIF.PAGE_RIGHT)
        self.next_btn.setFixedHeight(32)
        self.next_btn.setEnabled(False)
        self.next_btn.setToolTip("Preview next slide (Right Arrow or Touchpad Scroll Right/Down)")
        self.next_btn.clicked.connect(self.preview_next_slide)
        bottom_nav_bar.addWidget(self.next_btn)

        # "We Are Here" Action Button right in the bottom middle
        self.we_are_here_btn = PrimaryPushButton("We Are Here", self)
        self.we_are_here_btn.setIcon(FIF.PIN)
        self.we_are_here_btn.setFixedHeight(32)
        self.we_are_here_btn.setEnabled(False)
        self.we_are_here_btn.setToolTip(
            "Set the currently previewed slide as the active class slide and log the current recording timestamp"
        )
        self.we_are_here_btn.clicked.connect(self.mark_current_slide_here)
        bottom_nav_bar.addWidget(self.we_are_here_btn)

        # Active vs Preview Status Badge + Jump back to Active
        self.active_status_btn = TransparentPushButton("Active: None", self)
        self.active_status_btn.setIcon(FIF.FLAG)
        self.active_status_btn.setFixedHeight(32)
        self.active_status_btn.setEnabled(False)
        self.active_status_btn.setToolTip("Click to jump back to the current 'We Are Here' slide")
        self.active_status_btn.clicked.connect(self.jump_to_active_slide)
        bottom_nav_bar.addWidget(self.active_status_btn)

        bottom_nav_bar.addStretch(1)
        canvas_layout.addLayout(bottom_nav_bar)

        self.splitter.addWidget(canvas_card)

        # 3. Right: Per-Slide Text Annotations & Timestamp Log
        self.notes_card = CardWidget(self)
        self.notes_card.setMinimumWidth(220)
        self.notes_card.setMaximumWidth(400)
        notes_layout = QVBoxLayout(self.notes_card)
        notes_layout.setContentsMargins(10, 10, 10, 10)
        notes_layout.setSpacing(6)

        notes_header_row = QHBoxLayout()
        notes_icon = IconWidget(FIF.QUICK_NOTE, self)
        notes_icon.setFixedSize(16, 16)
        notes_header_row.addWidget(notes_icon)
        self.notes_header_label = StrongBodyLabel("Slide Notes", self)
        notes_header_row.addWidget(self.notes_header_label, 1)
        notes_layout.addLayout(notes_header_row)

        self.timestamps_badge = CaptionLabel("Timestamps: Not marked yet", self)
        self.timestamps_badge.setWordWrap(True)
        notes_layout.addWidget(self.timestamps_badge)

        self.slide_notes_edit = TextEdit(self)
        self.slide_notes_edit.setPlaceholderText(
            "Type lecture notes for this slide...\n\n"
            "Tip: Select 'Text' icon above and click anywhere on the slide to type directly onto the PDF!"
        )
        self.slide_notes_edit.setEnabled(False)
        self.slide_notes_edit.textChanged.connect(self._on_slide_notes_changed)
        notes_layout.addWidget(self.slide_notes_edit, 1)

        self.splitter.addWidget(self.notes_card)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([200, 750, 240])

        main_layout.addWidget(self.splitter, 1)

    def _toggle_deck_visibility(self):
        show = self.slide_list.isHidden()
        self.slide_list.setVisible(show)
        self.toggle_deck_btn.setText("Deck" if show else "Show Deck")

    def _toggle_notes_visibility(self):
        show = self.notes_card.isHidden()
        self.notes_card.setVisible(show)
        self.toggle_notes_btn.setText("Notes" if show else "Show Notes")

    def set_recording_time_display(self, seconds: float, is_recording: bool = True):
        """Update the live recording clock badge inside the full-screen slide toolbar."""
        ts_str = format_timestamp(seconds)
        prefix = "REC" if is_recording else "IDLE"
        self.live_timer_badge.setText(f"{prefix} {ts_str}")

    def toggle_window_fullscreen(self):
        """Toggle the top-level application window between full-screen and normal/maximized."""
        win = self.window()
        if not win:
            return
        if win.isFullScreen():
            win.showMaximized()
            self.fullscreen_btn.setText("Full Screen")
            self.fullscreen_btn.setIcon(FIF.FULL_SCREEN)
        else:
            win.showFullScreen()
            self.fullscreen_btn.setText("Exit Full Screen")
            self.fullscreen_btn.setIcon(FIF.BACK_TO_WINDOW)

    # ==========================
    # Multi-Deck PDF Loading & Management
    # ==========================
    def _prompt_open_pdf(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Class Lecture PDF Slides (select one or multiple decks)",
            "",
            "PDF Documents (*.pdf)",
        )
        if file_paths:
            for p in file_paths:
                self.load_pdf(p)

    def load_pdf(self, pdf_path: str, switch_to: bool = True) -> bool:
        """Render pages of a PDF slide deck and add it to the multi-deck class lecture session."""
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
            # Check if this deck is already loaded
            for idx, d in enumerate(self._decks):
                if os.path.abspath(d.pdf_path) == os.path.abspath(pdf_path):
                    if switch_to:
                        self.switch_to_deck(idx)
                    return True

            doc = fitz.open(pdf_path)
            if len(doc) == 0:
                doc.close()
                return False

            pixmaps: List[QPixmap] = []
            titles: List[str] = []

            for idx in range(len(doc)):
                page = doc[idx]
                raw_txt = page.get_text("text").strip()
                first_line = ""
                for line in raw_txt.splitlines():
                    clean_l = line.strip()
                    if len(clean_l) > 2:
                        first_line = clean_l[:32]
                        break
                title_str = first_line or f"Slide {idx + 1}"
                titles.append(title_str)

                # Render slide pixmap at crisp 160 DPI
                pix = page.get_pixmap(dpi=160, alpha=False)
                qimg = QImage(
                    pix.samples,
                    pix.width,
                    pix.height,
                    pix.stride,
                    QImage.Format.Format_RGB888,
                ).copy()
                pixmaps.append(QPixmap.fromImage(qimg))

            doc.close()

            deck_name = Path(pdf_path).name
            new_deck = SlideDeck(
                pdf_path=pdf_path,
                name=deck_name,
                pixmaps=pixmaps,
                titles=titles,
            )
            self._decks.append(new_deck)
            self._update_deck_combo()

            if switch_to or len(self._decks) == 1:
                self.switch_to_deck(len(self._decks) - 1)

            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
            self.we_are_here_btn.setEnabled(True)
            self.export_pdf_btn.setEnabled(True)
            self.slide_notes_edit.setEnabled(True)
            self.remove_deck_btn.setEnabled(True)

            # Notify MainWindow so it also extracts Course Jargon for Whisper
            self.pdf_imported_for_jargon.emit(pdf_path)
            return True
        except Exception as e:
            logger.exception(f"Failed to load PDF into SlideCompanionWidget: {e}")
            return False

    def _update_deck_combo(self):
        self.deck_combo.blockSignals(True)
        self.deck_combo.clear()
        for idx, deck in enumerate(self._decks):
            count = len(deck.pixmaps)
            self.deck_combo.addItem(f"Deck {idx + 1}: {deck.name} ({count} slides)", icon=FIF.DOCUMENT, userData=idx)
        if 0 <= self._current_deck_idx < len(self._decks):
            self.deck_combo.setCurrentIndex(self._current_deck_idx)
        self.deck_combo.blockSignals(False)
        self.deck_combo.setEnabled(len(self._decks) > 1)
        self.remove_deck_btn.setEnabled(len(self._decks) > 0)

    def _on_deck_combo_changed(self, index: int):
        if 0 <= index < len(self._decks) and index != self._current_deck_idx:
            self.switch_to_deck(index)

    def switch_to_deck(self, index: int):
        """Switch active slide deck in the class session, saving any current slide annotations first."""
        if index < 0 or index >= len(self._decks):
            return

        self._save_current_slide_strokes()
        self._save_current_slide_notes()

        self._current_deck_idx = index
        deck = self._decks[index]

        self.deck_combo.blockSignals(True)
        self.deck_combo.setCurrentIndex(index)
        self.deck_combo.blockSignals(False)

        # Repopulate slide_list
        self._is_loading_slide = True
        self.slide_list.blockSignals(True)
        self.slide_list.clear()
        for idx, title_str in enumerate(deck.titles):
            data = deck.slide_data.get(idx, {})
            ts_list = data.get("timestamps", [])
            has_notes = bool(data.get("notes", "").strip() or data.get("strokes"))

            item = QListWidgetItem(f"Slide {idx + 1}: {title_str}")
            if idx == deck.active_idx:
                item.setIcon(FIF.PIN.icon())
            elif has_notes:
                item.setIcon(FIF.EDIT.icon())
            else:
                item.setIcon(FIF.DOCUMENT.icon())
            self.slide_list.addItem(item)
        self.slide_list.blockSignals(False)
        self._is_loading_slide = False

        target_row = deck.preview_idx if 0 <= deck.preview_idx < len(deck.pixmaps) else 0
        self.slide_list.setCurrentRow(target_row)
        self._refresh_preview_ui()

    def remove_current_deck(self):
        """Remove the currently selected slide deck from this class session."""
        if not self._decks or self._current_deck_idx < 0:
            return
        del self._decks[self._current_deck_idx]
        self._update_deck_combo()
        if self._decks:
            new_idx = min(self._current_deck_idx, len(self._decks) - 1)
            self.switch_to_deck(new_idx)
        else:
            self._current_deck_idx = -1
            self.slide_list.clear()
            self.canvas.set_slide(None, [])
            self.slide_notes_edit.clear()
            self.slide_notes_edit.setEnabled(False)
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.we_are_here_btn.setEnabled(False)
            self.export_pdf_btn.setEnabled(False)
            self.remove_deck_btn.setEnabled(False)
            self.slide_counter_label.setText("No Deck")
            self.active_status_btn.setText("Active: None")
            self.active_status_btn.setEnabled(False)
            self.preview_banner_label.setText("No slide deck loaded")

    def _prompt_export_annotated(self):
        deck = self.current_deck
        if not deck or not deck.pixmaps:
            return
        base_stem = Path(deck.pdf_path).stem if deck.pdf_path else "Class_Slides"
        save_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Annotated Slides",
            f"{base_stem}_Annotated.pdf",
            "Annotated PDF Deck (*.pdf);;Current Slide PNG Image (*.png)",
        )
        if not save_path:
            return
        self.export_annotated_slides(save_path)

    def export_annotated_slides(self, output_path: str) -> bool:
        """Export either the current annotated slide (.png) or the annotated deck (.pdf)."""
        deck = self.current_deck
        if not deck or not deck.pixmaps:
            return False
        try:
            if output_path.lower().endswith(".png"):
                rendered = self.canvas.render_annotated_slide_pixmap()
                if rendered:
                    return rendered.save(output_path, "PNG")
                return False

            import pymupdf as fitz
            from PyQt6.QtCore import QBuffer, QIODevice

            out_doc = fitz.open()
            temp_canvas = AnnotatableSlideCanvas()
            for idx, base_pix in enumerate(deck.pixmaps):
                strokes = deck.slide_data.get(idx, {}).get("strokes", [])
                temp_canvas.set_slide(base_pix, strokes)
                annotated_pix = temp_canvas.render_annotated_slide_pixmap()
                if not annotated_pix:
                    continue
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                annotated_pix.save(buf, "PNG")
                img_bytes = bytes(buf.data())
                buf.close()

                page = out_doc.new_page(width=annotated_pix.width(), height=annotated_pix.height())
                page.insert_image(fitz.Rect(0, 0, annotated_pix.width(), annotated_pix.height()), stream=img_bytes)

            out_doc.save(output_path)
            out_doc.close()
            temp_canvas.deleteLater()
            return True
        except Exception as e:
            logger.exception(f"Failed to export annotated slides to {output_path}: {e}")
            return False

    # ==========================
    # Navigation, Touchpad & "We Are Here"
    # ==========================
    def _on_canvas_wheel_navigated(self, direction: int):
        if direction > 0:
            self.preview_next_slide()
        elif direction < 0:
            self.preview_prev_slide()

    def _on_list_row_changed(self, row: int):
        deck = self.current_deck
        if not deck or row < 0 or row >= len(deck.pixmaps):
            return
        deck.preview_idx = row
        self._refresh_preview_ui()

    def preview_prev_slide(self):
        deck = self.current_deck
        if deck and deck.preview_idx > 0:
            self.slide_list.setCurrentRow(deck.preview_idx - 1)

    def preview_next_slide(self):
        deck = self.current_deck
        if deck and deck.preview_idx + 1 < len(deck.pixmaps):
            self.slide_list.setCurrentRow(deck.preview_idx + 1)

    def jump_to_active_slide(self):
        """Return preview to whichever slide was last marked 'We Are Here' in the current deck."""
        deck = self.current_deck
        if deck and 0 <= deck.active_idx < len(deck.pixmaps):
            self.slide_list.setCurrentRow(deck.active_idx)

    def mark_current_slide_here(self) -> Optional[Tuple[int, float]]:
        """Mark currently previewed slide as the active class slide ('We Are Here') with timestamp."""
        deck = self.current_deck
        if not deck or deck.preview_idx < 0 or deck.preview_idx >= len(deck.pixmaps):
            return None

        deck.active_idx = deck.preview_idx
        elapsed_sec = float(self._time_provider() or 0.0)
        entry = deck.slide_data.setdefault(
            deck.active_idx, {"timestamps": [], "notes": "", "strokes": []}
        )
        entry["timestamps"].append(round(elapsed_sec, 1))

        self._update_list_item_labels()
        self._refresh_preview_ui()

        slide_num = deck.active_idx + 1
        slide_title = deck.titles[deck.active_idx] if deck.active_idx < len(deck.titles) else f"Slide {slide_num}"
        prefix = f"[{deck.name}] " if len(self._decks) > 1 else ""
        full_title = f"{prefix}{slide_title}"
        self.slide_marked_here.emit(slide_num, elapsed_sec, full_title)
        return slide_num, elapsed_sec

    def _update_list_item_labels(self):
        deck = self.current_deck
        if not deck:
            return
        for idx in range(self.slide_list.count()):
            item = self.slide_list.item(idx)
            if not item:
                continue
            title = deck.titles[idx] if idx < len(deck.titles) else f"Slide {idx + 1}"
            data = deck.slide_data.get(idx, {})
            ts_list = data.get("timestamps", [])
            has_notes = bool(data.get("notes", "").strip() or data.get("strokes"))

            if idx == deck.active_idx:
                item.setIcon(FIF.PIN.icon())
            elif has_notes:
                item.setIcon(FIF.EDIT.icon())
            else:
                item.setIcon(FIF.DOCUMENT.icon())

            suffix = f" [{format_timestamp(ts_list[-1])}]" if ts_list else ""
            item.setText(f"Slide {idx + 1}: {title}{suffix}")

    def _refresh_preview_ui(self):
        deck = self.current_deck
        if not deck or deck.preview_idx < 0 or len(deck.pixmaps) == 0:
            self.slide_counter_label.setText("No Deck")
            self.preview_banner_label.setText("No slide deck loaded")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.we_are_here_btn.setEnabled(False)
            self.active_status_btn.setEnabled(False)
            self.active_status_btn.setText("Active: None")
            return

        total = len(deck.pixmaps)
        slide_num = deck.preview_idx + 1
        self.slide_counter_label.setText(f"Slide {slide_num} / {total}")
        self.prev_btn.setEnabled(deck.preview_idx > 0)
        self.next_btn.setEnabled(deck.preview_idx < total - 1)
        self.we_are_here_btn.setEnabled(True)

        deck_prefix = f"[{deck.name}] " if len(self._decks) > 1 else ""

        if deck.active_idx >= 0:
            active_num = deck.active_idx + 1
            self.active_status_btn.setEnabled(True)
            if deck.preview_idx == deck.active_idx:
                self.active_status_btn.setText(f"Active: Slide {active_num}")
                self.preview_banner_label.setText(f"{deck_prefix}Viewing Active Class Slide {slide_num}")
            else:
                self.active_status_btn.setText(f"Active: Slide {active_num} (Return)")
                self.preview_banner_label.setText(
                    f"{deck_prefix}Previewing Slide {slide_num} (Class is currently on Slide {active_num})"
                )
        else:
            self.active_status_btn.setText("Active: Not marked")
            self.active_status_btn.setEnabled(False)
            self.preview_banner_label.setText(f"{deck_prefix}Previewing Slide {slide_num} — Click 'We Are Here' when class reaches this slide")

        data = deck.slide_data.get(deck.preview_idx, {"timestamps": [], "notes": "", "strokes": []})
        pix = deck.pixmaps[deck.preview_idx]
        self.canvas.set_slide(pix, data.get("strokes", []))

        self._is_loading_slide = True
        self.notes_header_label.setText(f"Slide {slide_num} Notes")
        self.slide_notes_edit.setPlainText(data.get("notes", ""))
        ts_list = data.get("timestamps", [])
        if ts_list:
            formatted_ts = ", ".join(f"[{format_timestamp(t)}]" for t in ts_list)
            self.timestamps_badge.setText(f"Marked 'We Are Here' at: {formatted_ts}")
        else:
            self.timestamps_badge.setText("Timestamps: Not marked as 'We Are Here' yet")
        self._is_loading_slide = False

    # ==========================
    # Editor Tools & Persistence
    # ==========================
    def _select_editor_tool(self, mode: str):
        for m, btn in self._tool_buttons.items():
            btn.setChecked(m == mode)
        self.canvas.set_tool_mode(mode)
        # If switching to Highlighter and current color is default Red, auto-select Yellow
        if mode == "highlighter" and self.color_combo.currentData() == "#ef4444":
            self.color_combo.setCurrentIndex(1)  # Yellow

    def _on_tool_changed(self, index: int):
        if 0 <= index < len(self.TOOL_MODES):
            self._select_editor_tool(self.TOOL_MODES[index])

    def _on_color_changed(self, index: int):
        color = self.color_combo.currentData()
        if color:
            self.canvas.set_pen_color(color)

    def _pick_custom_color(self):
        initial = QColor(self.canvas._pen_color)
        chosen = QColorDialog.getColor(initial, self, "Select Annotation Color")
        if chosen.isValid():
            hex_col = chosen.name()
            self.color_combo.addItem(
                hex_col.upper(),
                icon=create_color_swatch_icon(hex_col),
                userData=hex_col,
            )
            self.color_combo.setCurrentIndex(self.color_combo.count() - 1)
            self.canvas.set_pen_color(hex_col)

    def _save_current_slide_strokes(self):
        deck = self.current_deck
        if self._is_loading_slide or not deck or deck.preview_idx < 0:
            return
        entry = deck.slide_data.setdefault(
            deck.preview_idx, {"timestamps": [], "notes": "", "strokes": []}
        )
        entry["strokes"] = self.canvas.get_strokes()
        self._update_list_item_labels()

    def _save_current_slide_notes(self):
        deck = self.current_deck
        if self._is_loading_slide or not deck or deck.preview_idx < 0:
            return
        entry = deck.slide_data.setdefault(
            deck.preview_idx, {"timestamps": [], "notes": "", "strokes": []}
        )
        entry["notes"] = self.slide_notes_edit.toPlainText().strip()
        self._update_list_item_labels()

    def _on_slide_notes_changed(self):
        self._save_current_slide_notes()

    def get_timeline_markers(self) -> List[Dict[str, Any]]:
        """Return chronological list of all 'We Are Here' slide markers and slide annotations for the current deck."""
        deck = self.current_deck
        if not deck:
            return []
        markers: List[Dict[str, Any]] = []
        for idx, data in sorted(deck.slide_data.items()):
            ts_list = data.get("timestamps", [])
            notes = data.get("notes", "").strip()
            strokes = data.get("strokes", [])
            on_slide_texts = [
                s.get("text", "").strip()
                for s in strokes
                if s.get("mode") == "text" and s.get("text", "").strip()
            ]
            title = deck.titles[idx] if idx < len(deck.titles) else f"Slide {idx + 1}"
            if ts_list or notes or strokes:
                markers.append({
                    "slide_number": idx + 1,
                    "title": title,
                    "timestamps": ts_list,
                    "first_timestamp": ts_list[0] if ts_list else None,
                    "notes": notes,
                    "on_slide_texts": on_slide_texts,
                    "ink_strokes_count": len(strokes),
                })
        return markers

    def get_all_timeline_markers(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return timeline markers organized by deck name across all loaded decks in the class."""
        all_markers: Dict[str, List[Dict[str, Any]]] = {}
        for deck in self._decks:
            markers: List[Dict[str, Any]] = []
            for idx, data in sorted(deck.slide_data.items()):
                ts_list = data.get("timestamps", [])
                notes = data.get("notes", "").strip()
                strokes = data.get("strokes", [])
                on_slide_texts = [
                    s.get("text", "").strip()
                    for s in strokes
                    if s.get("mode") == "text" and s.get("text", "").strip()
                ]
                title = deck.titles[idx] if idx < len(deck.titles) else f"Slide {idx + 1}"
                if ts_list or notes or strokes:
                    markers.append({
                        "slide_number": idx + 1,
                        "title": title,
                        "timestamps": ts_list,
                        "first_timestamp": ts_list[0] if ts_list else None,
                        "notes": notes,
                        "on_slide_texts": on_slide_texts,
                        "ink_strokes_count": len(strokes),
                    })
            if markers:
                all_markers[deck.name] = markers
        return all_markers

    def format_annotations_summary_markdown(self) -> str:
        """Build a Markdown summary of all marked slides, timestamps, on-slide text labels, and notes across all decks."""
        all_deck_markers = self.get_all_timeline_markers()
        if not all_deck_markers:
            return ""

        lines = ["### Lecture Slide Timeline & Annotations"]
        is_multi_deck = len(all_deck_markers) > 1

        for deck_name, markers in all_deck_markers.items():
            if is_multi_deck:
                lines.append(f"\n#### Deck: {deck_name}")
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
                for txt_item in m.get("on_slide_texts", []):
                    lines.append(f"  - [On-Slide Text] {txt_item}")
                if m["notes"]:
                    for note_line in m["notes"].splitlines():
                        lines.append(f"  - {note_line.strip()}")
        return "\n".join(lines)
