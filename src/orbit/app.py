"""Application wiring.

Owns the two windows and the session controller, and connects them. Deliberately
the only module that knows about all three, so the views stay independent of each
other and the pipeline stays independent of both.

Window model, following the flow agreed during design:

    setup window  --Start-->  hides itself, shows the overlay
    overlay       --Back-->   stops the session, restores the setup window
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from .core.session import SessionController
from .settings import Settings
from .ui import theme
from .ui.main_window import MainWindow, show_consent_notice
from .ui.overlay import Overlay
from .phase3.export import export_json, export_markdown


class OrbitApp(QObject):
    """Ties the windows and the pipeline together."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self.settings = Settings.load()

        app.setStyleSheet(theme.stylesheet(self.settings.font_size))
        # The overlay is a tool window, so letting Qt quit on "last window
        # closed" would kill the app the moment the setup window hides.
        app.setQuitOnLastWindowClosed(False)

        self.session = SessionController(self.settings)
        self.main_window = MainWindow(self.settings)
        self.overlay = Overlay(self.settings)

        self._connect()

    # -- wiring ------------------------------------------------------------

    def _connect(self) -> None:
        self.main_window.start_requested.connect(self._on_start_requested)

        self.overlay.toggle_listening.connect(self._on_toggle_listening)
        self.overlay.clear_requested.connect(self._on_clear)
        self.overlay.back_requested.connect(self._on_back)
        self.overlay.closed.connect(self._on_back)
        self.overlay.force_answer_requested.connect(self.session.force_answer)
        self.overlay.export_requested.connect(self._export_session)

        session, overlay = self.session, self.overlay
        session.state_changed.connect(overlay.on_state_changed)
        session.level_changed.connect(overlay.on_level_changed)
        session.question_ready.connect(overlay.on_question)
        session.answer_started.connect(overlay.on_answer_started)
        session.answer_delta.connect(overlay.on_answer_delta)
        session.answer_finished.connect(overlay.on_answer_finished)
        session.utterance_ignored.connect(overlay.on_utterance_ignored)
        session.stats_changed.connect(overlay.on_stats)
        session.failed.connect(overlay.on_error)
        session.session_started.connect(overlay.on_session_started)
        session.session_stopped.connect(overlay.on_session_stopped)

        self._app.aboutToQuit.connect(self._on_quit)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self.main_window.show()

    def _on_start_requested(self) -> None:
        if not show_consent_notice(self.main_window, self.settings):
            self.main_window.show_message(
                "Listening cancelled. Nothing was captured."
            )
            return

        self.settings = self.main_window.collect_settings()
        self.session.update_settings(self.settings)
        self.overlay.apply_settings(self.settings)
        self._app.setStyleSheet(theme.stylesheet(self.settings.font_size))

        self.main_window.hide()
        self.overlay.show()
        self.overlay.raise_()
        self._register_hotkeys()
        self.session.start()

    def _on_toggle_listening(self) -> None:
        if self.session.is_running:
            self.session.stop()
        else:
            self.session.start()

    def _on_clear(self) -> None:
        self.session.clear_context()
        self.overlay.clear_display()

    def _on_back(self) -> None:
        if self.session.is_running:
            self.session.stop()
        self.overlay.capture_geometry()
        self.overlay.hotkeys.unregister_all()
        self.overlay.hide()
        self.settings.save()
        self.main_window.show()
        self.main_window.raise_()

        summary = self.session.last_summary
        if summary.strip():
            self.main_window.show_message(_condense(summary))

    def _on_quit(self) -> None:
        self.overlay.hotkeys.unregister_all()
        self.session.shutdown()
        self.overlay.capture_geometry()
        try:
            self.settings.save()
        except Exception:
            pass

    def _export_session(self, path: str, fmt: str) -> None:
        if not self.settings.allow_session_export:
            return
        turns = self.overlay.answer.turns_for_export()
        try:
            if fmt == "json":
                export_json(path, turns)
            else:
                export_markdown(path, turns)
            self.overlay.on_stats(f"Session exported to {path}")
        except OSError as exc:
            self.overlay.on_error(f"Export failed: {exc}")

    # -- hotkeys -----------------------------------------------------------

    def _register_hotkeys(self) -> None:
        """Bind global hotkeys once the overlay has a native window handle."""
        manager = self.overlay.hotkeys
        manager.unregister_all()
        manager.attach(int(self.overlay.winId()))

        actions = {
            "toggle_overlay": self.overlay.toggle_visibility,
            "force_answer": self.session.force_answer,
            "clear": self._on_clear,
            "stop_capture": self._on_toggle_listening,
        }

        problems: list[str] = []
        for name, callback in actions.items():
            spec = self.settings.hotkeys.get(name, "")
            if not spec:
                continue
            error = manager.register(name, spec, callback)
            if error:
                problems.append(error)

        if problems:
            # Not fatal: every action also has a button in the overlay.
            self.overlay.on_stats("Some hotkeys unavailable: " + "; ".join(problems))


def _condense(summary: str) -> str:
    """Pull the headline lines out of the session summary for the setup window."""
    interesting = [
        line.strip()
        for line in summary.splitlines()
        if "TIME TO FIRST TEXT" in line
        or "Estimated spend" in line
        or "Verdict" in line
    ]
    return "  ".join(interesting) if interesting else ""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("orbit-ai")
    app.setOrganizationName("orbit-ai")

    orbit = OrbitApp(app)
    orbit.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
