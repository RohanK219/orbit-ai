"""Session lifecycle: owns the worker thread and re-exposes its signals.

Kept separate from both the worker and the views so that neither has to know how
threads are started. The views connect to a stable set of signals here, and the
worker stays a plain pipeline with no knowledge of Qt window management.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from ..settings import Settings
from .worker import PipelineWorker

#: How long to wait for the worker to unwind before giving up on a clean stop.
#: A stop issued mid-request cannot interrupt an HTTP call already in flight, so
#: this needs to exceed a typical transcription round-trip.
_STOP_TIMEOUT_MS = 6000


class SessionController(QObject):
    """Starts and stops listening sessions."""

    # Forwarded from the worker.
    state_changed = Signal(str)
    level_changed = Signal(float)
    question_ready = Signal(str)
    answer_started = Signal()
    answer_delta = Signal(str)
    answer_finished = Signal()
    utterance_ignored = Signal()
    stats_changed = Signal(str)
    failed = Signal(str)

    # Owned by this class.
    session_started = Signal()
    session_stopped = Signal()

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._last_summary = ""

    # -- state -------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.isRunning()

    @property
    def last_summary(self) -> str:
        """Latency and cost summary from the most recent session."""
        return self._last_summary

    def update_settings(self, settings: Settings) -> None:
        """Replace settings. Takes effect on the next session, not this one."""
        self._settings = settings

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self.is_running:
            return

        thread = QThread()
        thread.setObjectName("orbit-pipeline")
        worker = PipelineWorker(self._settings)
        worker.moveToThread(thread)

        worker.state_changed.connect(self.state_changed)
        worker.level_changed.connect(self.level_changed)
        worker.question_ready.connect(self.question_ready)
        worker.answer_started.connect(self.answer_started)
        worker.answer_delta.connect(self.answer_delta)
        worker.answer_finished.connect(self.answer_finished)
        worker.utterance_ignored.connect(self.utterance_ignored)
        worker.stats_changed.connect(self.stats_changed)
        worker.failed.connect(self.failed)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_worker_finished)

        self._thread = thread
        self._worker = worker

        thread.start()
        self.session_started.emit()

    def stop(self) -> None:
        """Ask the session to end and wait briefly for it to unwind."""
        worker, thread = self._worker, self._thread
        if worker is None or thread is None:
            return

        worker.request_stop()
        thread.quit()
        if not thread.wait(_STOP_TIMEOUT_MS):
            # The worker is wedged in a network call. Leave the thread rather
            # than terminating it, since killing a thread mid-PortAudio-teardown
            # is how you get an access violation.
            self.failed.emit(
                "The session did not shut down cleanly within "
                f"{_STOP_TIMEOUT_MS // 1000} seconds. It will finish in the "
                "background."
            )

    def force_answer(self) -> None:
        if self._worker is not None:
            self._worker.request_force_answer()

    def clear_context(self) -> None:
        if self._worker is not None:
            self._worker.request_clear_context()

    # -- internals ---------------------------------------------------------

    def _on_worker_finished(self) -> None:
        worker = self._worker
        if worker is not None:
            self._last_summary = worker.summary()

        thread = self._thread
        if thread is not None:
            thread.quit()
            thread.wait(_STOP_TIMEOUT_MS)

        self._worker = None
        self._thread = None
        self.session_stopped.emit()

    def shutdown(self) -> None:
        """Stop unconditionally. Called on application exit."""
        if self.is_running:
            self.stop()
