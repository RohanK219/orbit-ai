"""The always-on-top overlay shown during a meeting.

Deliberately sparse. During a call this gets looked at for a second or two at a
time, so it carries exactly four controls and three information areas:

    status + level meter      is it hearing anything?
    the transcribed question  did it hear the right thing?
    the answer               the thing being read aloud

Everything else, including all configuration, lives in the setup window.

This class is a view. It emits intent signals and exposes slots for pipeline
state; it never owns the audio pipeline or a thread.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from ..core.worker import STATE_ERROR, STATE_IDLE
from ..settings import Settings
from . import theme
from .answer_view import AnswerView
from .hotkeys import HotkeyManager
from .widgets import LevelMeter, StatusIndicator

_IDLE_PLACEHOLDER = (
    "Not listening.\n\nPress Start, or use the start hotkey, when the meeting "
    "begins."
)
_LISTENING_PLACEHOLDER = (
    "Listening.\n\nAnswers appear here when someone finishes asking something."
)


class _DragHandle(QFrame):
    """Header strip that moves the frameless window when dragged."""

    def __init__(self, window: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._offset: QPoint | None = None
        self.setObjectName("Header")
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._offset = None
        event.accept()


class Overlay(QWidget):
    """Frameless, always-on-top answer panel."""

    toggle_listening = Signal()
    clear_requested = Signal()
    back_requested = Signal()
    force_answer_requested = Signal()
    closed = Signal()
    """Emitted when the overlay is dismissed, including via Alt+F4."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._listening = False
        self.hotkeys = HotkeyManager()

        self.setWindowTitle("orbit-ai")
        # Tool keeps it out of the taskbar and the alt-tab list, which is what
        # you want for a panel that sits on top of a meeting window.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self._build_ui()
        self.apply_settings(settings)

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header ------------------------------------------------------------
        header = _DragHandle(self, self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 7, 7, 7)
        header_layout.setSpacing(9)

        self.status = StatusIndicator(header)
        self.level = LevelMeter(header)
        self.level.setFixedWidth(64)

        self.toggle_button = QPushButton("Start", header)
        self.toggle_button.setToolTip("Start or stop listening")
        self.toggle_button.clicked.connect(self.toggle_listening.emit)

        self.clear_button = QPushButton("Clear", header)
        self.clear_button.setToolTip("Clear the answer and forget context")
        self.clear_button.clicked.connect(self.clear_requested.emit)

        self.back_button = QPushButton("Back", header)
        self.back_button.setToolTip("Return to the setup window")
        self.back_button.clicked.connect(self.back_requested.emit)

        header_layout.addWidget(self.status)
        header_layout.addWidget(self.level)
        header_layout.addStretch(1)
        header_layout.addWidget(self.toggle_button)
        header_layout.addWidget(self.clear_button)
        header_layout.addWidget(self.back_button)
        root.addWidget(header)

        # Conversation feed -------------------------------------------------
        # The transcribed question is rendered inline at the top of each turn
        # inside the feed, so there is no separate question band. That keeps the
        # whole conversation, questions and answers together, in one scrollable
        # place.
        self.answer = AnswerView(self._settings.font_size, self)
        self.answer.setContentsMargins(0, 0, 0, 0)
        answer_wrapper = QWidget(self)
        answer_layout = QVBoxLayout(answer_wrapper)
        answer_layout.setContentsMargins(10, 8, 6, 4)
        answer_layout.addWidget(self.answer)
        root.addWidget(answer_wrapper, 1)

        # Footer ------------------------------------------------------------
        footer = QFrame(self)
        footer.setObjectName("Footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 4, 4, 4)
        footer_layout.setSpacing(8)

        self.stats_label = QLabel("", footer)
        self.stats_label.setObjectName("FooterLabel")

        self.hint_label = QLabel("", footer)
        self.hint_label.setObjectName("FooterLabel")

        self.copy_button = QPushButton("Copy code", footer)
        self.copy_button.setObjectName("IconButton")
        self.copy_button.setToolTip("Copy the last code block in the answer")
        self.copy_button.clicked.connect(self._copy_code)
        self.copy_button.hide()

        footer_layout.addWidget(self.stats_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.copy_button)
        footer_layout.addWidget(self.hint_label)
        footer_layout.addWidget(QSizeGrip(footer), 0, Qt.AlignmentFlag.AlignBottom)
        root.addWidget(footer)

        self.answer.set_placeholder(_IDLE_PLACEHOLDER)

    # -- settings ----------------------------------------------------------

    def apply_settings(self, settings: Settings) -> None:
        """Push settings into the view. Safe to call while visible."""
        self._settings = settings
        self.setWindowOpacity(settings.opacity)
        self.answer.set_font_size(settings.font_size)
        self.resize(settings.overlay_width, settings.overlay_height)

        if settings.overlay_x is not None and settings.overlay_y is not None:
            self.move(settings.overlay_x, settings.overlay_y)
        else:
            self._move_to_default_corner()

        self._refresh_hints()

    def _move_to_default_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        margin = 24
        self.move(
            area.right() - self.width() - margin,
            area.top() + margin,
        )

    def capture_geometry(self) -> None:
        """Store the current position and size into settings."""
        geometry = self.geometry()
        self._settings.overlay_x = geometry.x()
        self._settings.overlay_y = geometry.y()
        self._settings.overlay_width = geometry.width()
        self._settings.overlay_height = geometry.height()

    def _refresh_hints(self) -> None:
        keys = self._settings.hotkeys
        parts = [
            f"{keys.get('toggle_overlay', '')} hide",
            f"{keys.get('force_answer', '')} ask",
        ]
        self.hint_label.setText("   ".join(part for part in parts if part.strip()))

    # -- pipeline slots ----------------------------------------------------

    def on_state_changed(self, state: str) -> None:
        self.status.set_state(state)

        listening = state not in (STATE_IDLE, STATE_ERROR)
        if listening != self._listening:
            self._listening = listening
            self.toggle_button.setText("Stop" if listening else "Start")

        if state == STATE_IDLE:
            self.level.reset()

    def on_level_changed(self, level: float) -> None:
        self.level.set_level(level)

    def on_question(self, text: str) -> None:
        # Opens a new turn in the feed. The prior conversation stays on screen.
        self.answer.add_question(text)

    def on_answer_started(self) -> None:
        self.answer.start_answer()

    def on_answer_delta(self, text: str) -> None:
        self.answer.append_delta(text)

    def on_answer_finished(self) -> None:
        self.answer.finish_answer()
        self.copy_button.setVisible(bool(self.answer.code_blocks()))

    def on_utterance_ignored(self) -> None:
        """Audio was captured but held no real question. Feed is unchanged."""

    def on_stats(self, text: str) -> None:
        self.stats_label.setText(text)

    def on_error(self, message: str) -> None:
        self.answer.show_message(message, colour=theme.STATE_COLOURS["error"])

    def on_session_started(self) -> None:
        self.stats_label.clear()
        self.answer.set_placeholder(_LISTENING_PLACEHOLDER)
        self.answer.clear_feed()

    def on_session_stopped(self) -> None:
        self.level.reset()
        # Leave the conversation on screen after stopping so it can still be
        # read and scrolled; only reset the placeholder for the next session.
        self.answer.set_placeholder(_IDLE_PLACEHOLDER)

    def clear_display(self) -> None:
        self.answer.clear_feed()
        self.copy_button.hide()

    # -- interactions ------------------------------------------------------

    def _copy_code(self) -> None:
        if self.answer.copy_last_code_block():
            self.copy_button.setText("Copied")
            # Restore the label shortly after, so the confirmation is visible
            # without leaving the button in a misleading state.
            QTimer.singleShot(1200, lambda: self.copy_button.setText("Copy code"))

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.capture_geometry()
            self.hide()
        else:
            self.show()
            self.raise_()

    # -- native events -----------------------------------------------------

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt naming
        """Route Windows hotkey messages to the hotkey manager."""
        if self.hotkeys.handle_native_event(message):
            return True, 0
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.capture_geometry()
        self.closed.emit()
        super().closeEvent(event)
