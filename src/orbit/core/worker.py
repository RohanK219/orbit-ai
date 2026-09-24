"""The pipeline worker: capture, segment, transcribe, answer.

Runs on a :class:`QThread` and communicates with the UI purely through Qt
signals, which are queued and thread-safe across threads by design.

Why a worker thread rather than asyncio: the transcription and answer layers
built in Phase 0 are synchronous, and blocking calls are exactly what a worker
thread is for. Introducing an event loop would mean rewriting verified code to
gain nothing, since there is only one audio stream and one request in flight at
a time.

Threading map:
    PortAudio callback thread  -> ring buffer          (inside SystemAudioCapture)
    this worker thread         -> VAD, HTTP, streaming
    Qt main thread             -> painting only

The only rule that matters: this class never touches a widget. Everything
crosses the boundary as a signal.
"""

from __future__ import annotations

import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from ..audio.capture import SystemAudioCapture
from ..audio.devices import AudioDeviceError, resolve_device
from ..audio.vad import Utterance, UtteranceSegmenter
from ..config import CAPTURE_CHUNK_MS, TARGET_SAMPLE_RATE, load_api_key
from ..llm.openai_llm import NO_QUESTION_MARKER, AnswerGenerator
from ..metrics import Measurement, MetricsCollector, endpoint_delay_seconds
from ..settings import Settings
from ..stt.openai_stt import OpenAITranscriber

# Pipeline states, surfaced to the UI as strings so the view layer needs no
# knowledge of the pipeline internals.
STATE_IDLE = "idle"
STATE_CALIBRATING = "calibrating"
STATE_LISTENING = "listening"
STATE_TRANSCRIBING = "transcribing"
STATE_THINKING = "thinking"
STATE_ANSWERING = "answering"
STATE_ERROR = "error"

#: Level meter refresh rate. Fast enough to look live, slow enough not to flood
#: the event loop with signals.
_LEVEL_EMIT_HZ = 15.0

#: Characters buffered before answer text is shown, so a "(no question
#: detected)" reply can be suppressed instead of flashing on screen.
_ANSWER_GATE_CHARS = 24


class PipelineWorker(QObject):
    """Owns the audio-to-answer pipeline for one listening session."""

    # -- signals -----------------------------------------------------------
    state_changed = Signal(str)
    level_changed = Signal(float)
    """Normalised 0.0-1.0 audio level, for the meter."""

    question_ready = Signal(str)
    answer_started = Signal()
    answer_delta = Signal(str)
    answer_finished = Signal()
    utterance_ignored = Signal()
    """Emitted when audio was captured but held no real question."""

    stats_changed = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._stop = threading.Event()
        self._force_answer = threading.Event()
        self._clear_context = threading.Event()
        self._capture: SystemAudioCapture | None = None
        self._metrics: MetricsCollector | None = None

    # -- control surface, called from the UI thread -------------------------

    def request_stop(self) -> None:
        self._stop.set()

    def request_force_answer(self) -> None:
        self._force_answer.set()

    def request_clear_context(self) -> None:
        self._clear_context.set()

    # -- main entry point --------------------------------------------------

    @Slot()
    def run(self) -> None:
        """Session main loop. Runs on the worker thread."""
        try:
            self._run_session()
        except AudioDeviceError as exc:
            self.state_changed.emit(STATE_ERROR)
            self.failed.emit(str(exc))
        except RuntimeError as exc:
            # Missing credentials land here with actionable guidance already.
            self.state_changed.emit(STATE_ERROR)
            self.failed.emit(str(exc))
        except Exception as exc:  # keep the UI alive whatever happens (R9.3)
            self.state_changed.emit(STATE_ERROR)
            self.failed.emit(f"Unexpected {type(exc).__name__}: {exc}")
        finally:
            self._teardown()
            self.state_changed.emit(STATE_IDLE)
            self.finished.emit()

    # -- internals ---------------------------------------------------------

    def _run_session(self) -> None:
        import pyaudiowpatch as pyaudio
        from openai import OpenAI, OpenAIError

        settings = self._settings
        model_settings = settings.to_model_settings()
        vad_settings = settings.to_vad_settings()

        api_key = load_api_key()
        client = OpenAI(api_key=api_key)
        transcriber = OpenAITranscriber(
            client,
            model=model_settings.stt_model,
            language=model_settings.stt_language,
        )
        generator = AnswerGenerator(client, model_settings)
        self._metrics = MetricsCollector(
            stt_model=model_settings.stt_model, llm_model=model_settings.llm_model
        )
        endpoint_delay = endpoint_delay_seconds(vad_settings)
        segmenter = UtteranceSegmenter(vad_settings)

        with pyaudio.PyAudio() as pa:
            device = resolve_device(pa, settings.audio_device_index)

            self.state_changed.emit(STATE_CALIBRATING)
            with SystemAudioCapture(pa, device) as capture:
                self._capture = capture
                calibrated = False
                next_level_at = 0.0

                for chunk in capture.frames(timeout=0.2, idle_timeout=25.0):
                    if self._stop.is_set():
                        break

                    now = time.monotonic()
                    if now >= next_level_at:
                        self.level_changed.emit(_rms(chunk))
                        next_level_at = now + 1.0 / _LEVEL_EMIT_HZ

                    if self._clear_context.is_set():
                        self._clear_context.clear()
                        generator.reset_context()

                    utterances = segmenter.push(chunk)

                    if self._force_answer.is_set():
                        self._force_answer.clear()
                        forced = segmenter.flush()
                        if forced is not None:
                            utterances.append(forced)

                    if not calibrated and not segmenter.is_calibrating:
                        calibrated = True
                        self.state_changed.emit(STATE_LISTENING)

                    for utterance in utterances:
                        if self._stop.is_set():
                            break
                        if not self._within_budget():
                            return
                        self._handle_utterance(
                            utterance=utterance,
                            transcriber=transcriber,
                            generator=generator,
                            endpoint_delay=endpoint_delay,
                            error_type=OpenAIError,
                        )
                        # Audio kept arriving while that answer generated. That
                        # backlog is the follow-up question the person asked
                        # while still speaking, so it must NOT be dropped. It
                        # gets segmented on the next loop iterations and shown
                        # as a new turn in the feed.
                        #
                        # The only case worth discarding is when the backlog has
                        # grown beyond the segmenter's max utterance, meaning a
                        # long answer left a large stale buffer. drop_stale_audio
                        # (opt-in, off by default) covers that.
                        if settings.drop_stale_audio and (
                            capture.backlog * CAPTURE_CHUNK_MS / 1000.0
                            > settings.max_utterance_seconds
                        ):
                            capture.drain()
                        if not self._stop.is_set():
                            self.state_changed.emit(STATE_LISTENING)

                if capture.timed_out_idle and not self._stop.is_set():
                    self.state_changed.emit(STATE_ERROR)
                    self.failed.emit(
                        f"No audio received from {device.name!r}.\n\n"
                        "Loopback devices deliver nothing while the endpoint is "
                        "idle on some drivers. Start playing meeting audio, then "
                        "start listening again."
                    )

    def _handle_utterance(
        self,
        *,
        utterance: Utterance,
        transcriber: OpenAITranscriber,
        generator: AnswerGenerator,
        endpoint_delay: float,
        error_type: type[Exception],
    ) -> None:
        self.state_changed.emit(STATE_TRANSCRIBING)
        try:
            result = transcriber.transcribe(
                utterance.audio, prompt=self._settings.transcription_prompt
            )
        except error_type as exc:
            self.failed.emit(f"Transcription failed: {_short_error(exc)}")
            return

        if not result.text:
            self.utterance_ignored.emit()
            return

        self.question_ready.emit(result.text)
        self.state_changed.emit(STATE_THINKING)

        try:
            generation = generator.answer(result.text)
        except error_type as exc:
            self.failed.emit(f"Answer request failed: {_short_error(exc)}")
            return

        # The model replies with a sentinel when the audio held no real
        # question. Buffer the opening characters so that reply can be dropped
        # instead of flickering onto the overlay and then vanishing. Answers
        # that obviously are not the sentinel start rendering on the very first
        # delta, so this costs no perceptible latency in the normal case.
        buffer = ""
        decided = False
        suppress = False

        def begin_answer(text: str) -> None:
            self.state_changed.emit(STATE_ANSWERING)
            self.answer_started.emit()
            if text:
                self.answer_delta.emit(text)

        try:
            for delta in generation:
                if self._stop.is_set():
                    break

                if not decided:
                    buffer += delta
                    if len(buffer) < _ANSWER_GATE_CHARS and _could_be_marker(buffer):
                        continue
                    decided = True
                    suppress = NO_QUESTION_MARKER in buffer.lower()
                    if not suppress:
                        begin_answer(buffer)
                    continue

                if not suppress:
                    self.answer_delta.emit(delta)
        except error_type as exc:
            self.failed.emit(f"Answer stream failed: {_short_error(exc)}")
            return

        if not decided:
            # Stream ended while still inside the gate.
            suppress = NO_QUESTION_MARKER in buffer.lower()
            if not suppress:
                begin_answer(buffer)

        if suppress or not generation.is_question:
            self.utterance_ignored.emit()
            return

        self.answer_finished.emit()

        if self._metrics is not None:
            self._metrics.add(
                Measurement(
                    speech_duration=utterance.duration,
                    endpoint_delay=endpoint_delay,
                    stt_latency=result.latency,
                    llm_first_token_latency=generation.first_token_latency,
                    llm_total_latency=generation.total_latency,
                    transcript_chars=len(result.text),
                    answer_chars=len(generation.text),
                )
            )
            self.stats_changed.emit(self._format_stats(generation, result.latency))

    def _format_stats(self, generation, stt_latency: float) -> str:
        metrics = self._metrics
        assert metrics is not None
        ttft = generation.first_token_latency
        visible = stt_latency + (ttft or 0.0)
        return (
            f"{visible:.1f}s to first text  ·  "
            f"${metrics.estimated_cost_usd():.3f} this session"
        )

    def _within_budget(self) -> bool:
        limit = self._settings.session_cost_limit_usd
        if limit <= 0 or self._metrics is None:
            return True
        if self._metrics.estimated_cost_usd() < limit:
            return True
        self.state_changed.emit(STATE_ERROR)
        self.failed.emit(
            f"Session spend limit of ${limit:.2f} reached, so listening stopped.\n\n"
            "Raise or clear the limit in Settings to continue."
        )
        return False

    def _teardown(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.stop()

    # -- reporting ---------------------------------------------------------

    def summary(self) -> str:
        """Latency and cost summary for the finished session."""
        return self._metrics.summary() if self._metrics else ""


def _could_be_marker(text: str) -> bool:
    """True while ``text`` might still turn out to be the no-question sentinel."""
    candidate = text.lower().strip()
    marker = NO_QUESTION_MARKER.lower()
    if not candidate:
        return True
    return marker.startswith(candidate) or candidate.startswith(marker)


def _rms(chunk: np.ndarray) -> float:
    """Perceptual-ish level for the meter.

    Raw RMS on speech sits very low on a 0-1 scale, so a square root spreads the
    useful range across the widget instead of hugging the bottom.
    """
    if chunk.size == 0:
        return 0.0
    raw = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
    return min(1.0, raw**0.5 * 2.2)


def _short_error(exc: Exception) -> str:
    """Trim provider errors to something that fits in the overlay.

    Also guards against an API key appearing in an error payload reaching the UI
    or a log, per R7.2.
    """
    text = str(exc).replace("\n", " ").strip()
    import re

    text = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", text)
    return text if len(text) <= 200 else text[:197] + "..."
