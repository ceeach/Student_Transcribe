"""Minimalistic modern dark theme palette and QSS styling for Student Transcribe."""

ACCENT_HEX = "#6366f1"       # Indigo 500 — calm, modern accent
ACCENT_HOVER = "#7578f7"
ACCENT_PRESSED = "#4f46e5"

BG_BASE = "#0f1117"          # Deep matte obsidian canvas
BG_CARD = "#161922"          # Subtle elevated surface
BG_ELEVATED = "#1e222d"      # Inputs and secondary controls
BG_HOVER = "#262b38"
BORDER_SUBTLE = "rgba(255, 255, 255, 0.05)"
BORDER_STRONG = "rgba(255, 255, 255, 0.11)"

TEXT_PRIMARY = "#f1f5f9"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED = "#64748b"


def get_app_stylesheet() -> str:
    """Return a clean, minimalistic QSS stylesheet layered over qfluentwidgets."""
    return f"""
    QMainWindow, MainWindow {{
        background-color: {BG_BASE};
        color: {TEXT_PRIMARY};
    }}
    QWidget#recordPage, QWidget#uploadPage, QWidget#videoPage,
    QWidget#slidesPage, QWidget#subjectsPage, QWidget#historyPage {{
        background-color: transparent;
    }}
    CardWidget, SimpleCardWidget {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 12px;
    }}
    DropZoneWidget#dropZone {{
        background-color: {BG_CARD};
        border: 1px dashed {BORDER_STRONG};
        border-radius: 12px;
    }}
    LineEdit, TextEdit, PlainTextEdit {{
        background-color: {BG_ELEVATED};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 8px;
        color: {TEXT_PRIMARY};
        selection-background-color: {ACCENT_HEX};
    }}
    LineEdit:focus, TextEdit:focus, PlainTextEdit:focus {{
        border: 1px solid {ACCENT_HEX};
    }}
    ComboBox {{
        background-color: {BG_ELEVATED};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 8px;
        color: {TEXT_PRIMARY};
    }}
    ComboBox:hover {{
        background-color: {BG_HOVER};
        border: 1px solid {BORDER_STRONG};
    }}
    PushButton, ToolButton {{
        background-color: {BG_ELEVATED};
        border: 1px solid {BORDER_SUBTLE};
        border-radius: 8px;
        color: {TEXT_PRIMARY};
    }}
    PushButton:hover, ToolButton:hover {{
        background-color: {BG_HOVER};
        border: 1px solid {BORDER_STRONG};
    }}
    PushButton:checked, ToolButton:checked {{
        background-color: rgba(99, 102, 241, 0.20);
        border: 1px solid {ACCENT_HEX};
        color: #ffffff;
    }}
    PushButton:disabled, ToolButton:disabled {{
        color: {TEXT_MUTED};
        background-color: rgba(30, 34, 45, 0.40);
        border: 1px solid rgba(255, 255, 255, 0.03);
    }}
    PrimaryPushButton {{
        background-color: {ACCENT_HEX};
        border: 1px solid {ACCENT_HOVER};
        border-radius: 8px;
        color: #ffffff;
        font-weight: 600;
    }}
    PrimaryPushButton:hover {{
        background-color: {ACCENT_HOVER};
    }}
    PrimaryPushButton:pressed {{
        background-color: {ACCENT_PRESSED};
    }}
    PrimaryPushButton:disabled {{
        background-color: rgba(99, 102, 241, 0.25);
        border: 1px solid rgba(99, 102, 241, 0.15);
        color: rgba(255, 255, 255, 0.40);
    }}
    QSplitter::handle {{
        background-color: {BORDER_SUBTLE};
        width: 2px;
    }}
    QSplitter::handle:hover {{
        background-color: {ACCENT_HEX};
    }}
    CaptionLabel {{
        color: {TEXT_SECONDARY};
    }}
    PivotItem {{
        padding: 4px 8px;
    }}
    """
