"""Colours and stylesheet.

Dark only. The overlay sits on top of a meeting window and gets glanced at for a
second or two at a time, so everything here serves legibility: high contrast
body text, restrained accent use, and no decoration that competes with the
answer.

Contrast ratios against :data:`BG` were chosen to clear WCAG AA for body text
(4.5:1) with headroom. ``TEXT`` is roughly 13:1 and ``TEXT_DIM`` roughly 6:1.
"""

from __future__ import annotations

# -- palette ---------------------------------------------------------------
BG = "#14161a"
PANEL = "#1b1f26"
PANEL_RAISED = "#222835"
BORDER = "#2b323c"
TEXT = "#e6e9ef"
TEXT_DIM = "#949cab"
ACCENT = "#4da3ff"
CODE_BG = "#0f1115"

# -- state colours ---------------------------------------------------------
STATE_COLOURS: dict[str, str] = {
    "idle": "#6b7280",
    "calibrating": "#a78bfa",
    "listening": "#34d399",
    "transcribing": "#60a5fa",
    "thinking": "#fbbf24",
    "answering": "#34d399",
    "error": "#f87171",
}

STATE_LABELS: dict[str, str] = {
    "idle": "idle",
    "calibrating": "calibrating",
    "listening": "listening",
    "transcribing": "transcribing",
    "thinking": "thinking",
    "answering": "answering",
    "error": "error",
}

UI_FONT = "Segoe UI"
CODE_FONT = "Cascadia Mono, Consolas, monospace"


def stylesheet(font_size: int = 15) -> str:
    """Build the application stylesheet at the user's chosen text size."""
    small = max(9, font_size - 4)
    return f"""
    QWidget {{
        background-color: {BG};
        color: {TEXT};
        font-family: "{UI_FONT}";
        font-size: {font_size}px;
    }}

    QFrame#Header, QFrame#Footer {{
        background-color: {PANEL};
        border: none;
    }}

    QLabel#QuestionLabel {{
        color: {TEXT_DIM};
        font-size: {max(10, font_size - 2)}px;
        padding: 6px 10px;
        background-color: {PANEL};
        border-left: 2px solid {ACCENT};
    }}

    QLabel#StatusLabel {{
        color: {TEXT_DIM};
        font-size: {small}px;
    }}

    QLabel#FooterLabel {{
        color: {TEXT_DIM};
        font-size: {small}px;
    }}

    QLabel#SectionLabel {{
        color: {TEXT_DIM};
        font-size: {small}px;
        text-transform: uppercase;
    }}

    QLabel#Heading {{
        font-size: {font_size + 6}px;
        font-weight: 600;
    }}

    QTextBrowser, QTextEdit {{
        background-color: {BG};
        border: none;
        selection-background-color: {ACCENT};
        selection-color: {BG};
    }}

    QPushButton {{
        background-color: {PANEL_RAISED};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 5px 12px;
        color: {TEXT};
    }}
    QPushButton:hover {{
        border-color: {ACCENT};
    }}
    QPushButton:pressed {{
        background-color: {PANEL};
    }}
    QPushButton:disabled {{
        color: {TEXT_DIM};
        border-color: {BORDER};
    }}

    QPushButton#PrimaryButton {{
        background-color: {ACCENT};
        color: #08101c;
        border: none;
        border-radius: 4px;
        padding: 9px 18px;
        font-weight: 600;
        font-size: {font_size + 1}px;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: #6cb4ff;
    }}
    QPushButton#PrimaryButton:disabled {{
        background-color: {PANEL_RAISED};
        color: {TEXT_DIM};
    }}

    QPushButton#IconButton {{
        background: transparent;
        border: none;
        padding: 2px 7px;
        color: {TEXT_DIM};
        font-size: {font_size + 1}px;
    }}
    QPushButton#IconButton:hover {{
        color: {TEXT};
    }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 6px 8px;
        color: {TEXT};
        selection-background-color: {ACCENT};
        selection-color: {BG};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {ACCENT};
    }}

    QComboBox QAbstractItemView {{
        background-color: {PANEL};
        border: 1px solid {BORDER};
        selection-background-color: {ACCENT};
        selection-color: {BG};
    }}

    QCheckBox {{
        spacing: 8px;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 9px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {TEXT_DIM};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        height: 0;
        background: none;
    }}

    QGroupBox {{
        border: 1px solid {BORDER};
        border-radius: 5px;
        margin-top: 14px;
        padding-top: 10px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px;
        color: {TEXT_DIM};
        font-size: {small}px;
    }}
    """
