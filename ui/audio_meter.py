"""Audio Meter Widget for NotionTranscribe.

Provides real-time visual VU volume metering with smooth falloff and
green-to-red color grading for microphone input feedback.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget


class AudioMeter(QWidget):
    """Custom VU Audio Level Meter widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(18)
        self.setMaximumHeight(24)

        self._level: float = 0.0          # Target level [0.0, 1.0]
        self._display_level: float = 0.0  # Smoothly decayed display level

        # Decay timer for smooth drop
        self._decay_timer = QTimer(self)
        self._decay_timer.setInterval(30)  # ~33 fps
        self._decay_timer.timeout.connect(self._update_decay)
        self._decay_timer.start()

    def set_level(self, level: float):
        """Set current audio level (0.0 to 1.0)."""
        clamped = max(0.0, min(1.0, float(level)))
        self._level = clamped
        if clamped > self._display_level:
            self._display_level = clamped
        self.update()

    def reset(self):
        """Reset meter back to zero."""
        self._level = 0.0
        self._display_level = 0.0
        self.update()

    def _update_decay(self):
        """Smoothly decay meter down when audio quietens."""
        if self._display_level > self._level:
            self._display_level = max(self._level, self._display_level - 0.04)
            self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        width = rect.width()
        height = rect.height()

        # Background track
        bg_color = QColor("#0f172a")
        border_color = QColor("#334155")
        painter.setBrush(bg_color)
        painter.setPen(border_color)
        painter.drawRoundedRect(0, 0, width - 1, height - 1, 4, 4)

        # Active meter fill
        active_width = int((width - 4) * self._display_level)
        if active_width > 2:
            # Gradient: Green (quiet) -> Amber (moderate) -> Red (peak)
            gradient = QLinearGradient(2, 0, width - 2, 0)
            gradient.setColorAt(0.0, QColor("#10b981"))  # Emerald green
            gradient.setColorAt(0.65, QColor("#3b82f6")) # Blue
            gradient.setColorAt(0.85, QColor("#f59e0b")) # Amber
            gradient.setColorAt(1.0, QColor("#ef4444"))  # Red

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(2, 2, active_width, height - 4, 3, 3)

        # Tick lines (segmented meter feel)
        num_ticks = 20
        painter.setPen(QColor(15, 23, 42, 160))
        for i in range(1, num_ticks):
            x = int((width / num_ticks) * i)
            painter.drawLine(x, 2, x, height - 3)

        painter.end()
