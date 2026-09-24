"""Utterance segmentation via short-term energy.

The job: decide when someone started talking and, more importantly, when they
*stopped*, because the end of a question is what triggers the whole downstream
pipeline.

Why energy and not a neural VAD: meeting audio arriving over loopback is already
clean, mixed, and free of room noise, so an RMS threshold with hysteresis is
adequate and adds zero dependencies. :class:`UtteranceSegmenter` deliberately
exposes a narrow push/emit interface so a Silero or WebRTC VAD can replace the
internals later without touching callers.

The dominant latency knob in the entire application lives here:
``silence_frames_to_end``. Everything downstream is fast by comparison.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ..config import TARGET_SAMPLE_RATE, VAD_FRAME_MS, VadSettings

_FRAME_SAMPLES = TARGET_SAMPLE_RATE * VAD_FRAME_MS // 1000  # 320 samples @ 16 kHz

#: Audio retained before detected speech onset. Without this the onset debounce
#: would clip the first syllable, which measurably hurts transcription accuracy.
_PREROLL_FRAMES = 10  # 200 ms


@dataclass(slots=True)
class Utterance:
    """A contiguous span of speech, ready to transcribe."""

    audio: np.ndarray
    """Mono float32 samples at :data:`~orbit.config.TARGET_SAMPLE_RATE`."""

    started_at: float
    """``time.monotonic()`` when speech onset was detected."""

    ended_at: float
    """``time.monotonic()`` when the endpoint was detected."""

    truncated: bool = False
    """True when emitted because of the max-length cap rather than a silence gap."""

    speech_seconds: float = 0.0
    """Seconds of actual above-threshold audio, excluding pre-roll and silence."""

    @property
    def duration(self) -> float:
        return len(self.audio) / TARGET_SAMPLE_RATE

    def to_int16(self) -> np.ndarray:
        """Convert to int16 PCM, clipped to avoid wraparound on loud passages."""
        return (np.clip(self.audio, -1.0, 1.0) * 32767.0).astype(np.int16)


@dataclass(slots=True)
class _State:
    calibrating: bool = True
    noise_samples: list[float] = field(default_factory=list)
    threshold: float = 0.0
    in_speech: bool = False
    speech_run: int = 0
    silence_run: int = 0
    buffer: list[np.ndarray] = field(default_factory=list)
    buffered_samples: int = 0
    started_at: float = 0.0
    speech_frames: int = 0
    """Frames within the current utterance that exceeded the threshold.

    Tracked separately from ``buffered_samples`` because the buffer also holds
    pre-roll and trailing silence, which must not count toward the minimum
    length guard."""


class UtteranceSegmenter:
    """Splits a continuous audio stream into discrete utterances."""

    def __init__(self, settings: VadSettings | None = None) -> None:
        self.settings = settings or VadSettings()
        self._pending = np.empty(0, dtype=np.float32)
        self._preroll: deque[np.ndarray] = deque(maxlen=_PREROLL_FRAMES)
        self._s = _State()
        self._calibration_frames = max(
            1, int(self.settings.calibration_seconds * 1000 / VAD_FRAME_MS)
        )

    # -- introspection -----------------------------------------------------

    @property
    def is_calibrating(self) -> bool:
        return self._s.calibrating

    @property
    def threshold(self) -> float:
        return self._s.threshold

    @property
    def in_speech(self) -> bool:
        return self._s.in_speech

    # -- main entry point --------------------------------------------------

    def push(self, chunk: np.ndarray) -> list[Utterance]:
        """Feed captured audio in. Returns any utterances completed by this chunk.

        Chunks may be any length; they are rebuffered into fixed VAD frames
        internally.
        """
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        self._pending = (
            chunk if self._pending.size == 0 else np.concatenate((self._pending, chunk))
        )

        completed: list[Utterance] = []
        while self._pending.size >= _FRAME_SAMPLES:
            frame = self._pending[:_FRAME_SAMPLES]
            self._pending = self._pending[_FRAME_SAMPLES:]
            utterance = self._process_frame(frame)
            if utterance is not None:
                completed.append(utterance)
        return completed

    def flush(self) -> Utterance | None:
        """Emit whatever speech is buffered. Call on shutdown."""
        if self._s.in_speech and self._s.buffered_samples > 0:
            return self._emit(truncated=True)
        return None

    # -- internals ---------------------------------------------------------

    def _process_frame(self, frame: np.ndarray) -> Utterance | None:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        s = self._s

        if s.calibrating:
            s.noise_samples.append(rms)
            if len(s.noise_samples) >= self._calibration_frames:
                # Median, not mean: robust to a stray loud frame landing during
                # the calibration window.
                noise_floor = float(np.median(s.noise_samples))
                s.threshold = max(
                    noise_floor * self.settings.speech_threshold_multiplier,
                    self.settings.min_speech_rms,
                )
                s.calibrating = False
            self._preroll.append(frame)
            return None

        is_speech = rms > s.threshold

        if not s.in_speech:
            self._preroll.append(frame)
            if is_speech:
                s.speech_run += 1
                if s.speech_run >= self.settings.speech_frames_to_start:
                    self._open_utterance()
            else:
                s.speech_run = 0
            return None

        # Already inside an utterance.
        s.buffer.append(frame)
        s.buffered_samples += frame.size

        if is_speech:
            s.speech_frames += 1
            s.silence_run = 0
        else:
            s.silence_run += 1
            if s.silence_run >= self.settings.silence_frames_to_end:
                return self._close_utterance()

        max_samples = int(self.settings.max_utterance_seconds * TARGET_SAMPLE_RATE)
        if s.buffered_samples >= max_samples:
            # Long monologue: emit what we have and keep listening rather than
            # buffering without bound.
            emitted = self._emit(truncated=True)
            s.buffer = []
            s.buffered_samples = 0
            s.speech_frames = 0
            s.silence_run = 0
            s.started_at = time.monotonic()
            return emitted

        return None

    def _open_utterance(self) -> None:
        s = self._s
        s.in_speech = True
        s.silence_run = 0
        # The frames that triggered onset were speech, so they count.
        s.speech_frames = s.speech_run
        s.speech_run = 0
        # Prepend the pre-roll so the first syllable is not clipped.
        s.buffer = list(self._preroll)
        s.buffered_samples = sum(f.size for f in s.buffer)
        self._preroll.clear()
        s.started_at = time.monotonic() - (s.buffered_samples / TARGET_SAMPLE_RATE)

    def _close_utterance(self) -> Utterance | None:
        s = self._s
        emitted = self._emit(truncated=False)
        s.in_speech = False
        s.buffer = []
        s.buffered_samples = 0
        s.speech_frames = 0
        s.silence_run = 0
        s.speech_run = 0
        self._preroll.clear()
        return emitted

    def _emit(self, *, truncated: bool) -> Utterance | None:
        s = self._s
        if not s.buffer:
            return None

        audio = np.concatenate(s.buffer)

        # Trailing silence carries no information and costs transcription time,
        # so trim all but a short tail.
        keep_tail = _FRAME_SAMPLES * 4  # 80 ms
        trim = max(0, (s.silence_run * _FRAME_SAMPLES) - keep_tail)
        if trim > 0 and trim < audio.size:
            audio = audio[: audio.size - trim]

        # Gate on actual speech content, not buffer length. The buffer includes
        # pre-roll and a silence tail, so a brief notification chime can easily
        # produce a half-second buffer containing 150 ms of sound. Billing the
        # API for those adds up fast in a meeting full of Teams and Slack pings.
        speech_seconds = s.speech_frames * VAD_FRAME_MS / 1000.0
        if speech_seconds < self.settings.min_utterance_seconds:
            return None  # Click, chime, cough. Not worth an API call.

        return Utterance(
            audio=audio,
            started_at=s.started_at,
            ended_at=time.monotonic(),
            truncated=truncated,
            speech_seconds=speech_seconds,
        )
