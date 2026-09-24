"""Optional local speech-to-text backend powered by faster-whisper.

The dependency is deliberately imported only when this backend is selected.
The main application can therefore be installed and used with the OpenAI
backend without downloading a local model or installing the optional package.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .base import TranscriptionResult


class LocalWhisperError(RuntimeError):
    """Base error for the optional local Whisper backend."""


class LocalWhisperDependencyError(LocalWhisperError):
    """Raised when faster-whisper is not installed."""


class LocalWhisperModelError(LocalWhisperError):
    """Raised when the requested model cannot be loaded or run."""


class LocalWhisperTranscriber:
    """Transcribe 16 kHz mono audio with a local faster-whisper model.

    ``faster-whisper`` is optional.  Constructing this class is the point at
    which the model is loaded, so dependency and model failures can be shown to
    the user as an actionable error rather than failing at module import time.
    """

    def __init__(
        self,
        model: str = "base",
        *,
        language: str | None = "en",
        device: str = "auto",
        compute_type: str = "default",
        **model_kwargs: Any,
    ) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise LocalWhisperDependencyError(
                "Local transcription requires the optional 'faster-whisper' "
                "package. Install it with: pip install 'orbit-ai[local-stt]'"
            ) from exc

        self.model = model
        self.language = language
        try:
            self._model = WhisperModel(
                model,
                device=device,
                compute_type=compute_type,
                **model_kwargs,
            )
        except Exception as exc:
            raise LocalWhisperModelError(
                f"Unable to load local Whisper model {model!r}: {exc}"
            ) from exc

    def transcribe(
        self, audio: np.ndarray, *, prompt: str | None = None
    ) -> TranscriptionResult:
        """Transcribe one mono float32 utterance and return its elapsed time."""
        if not isinstance(audio, np.ndarray):
            raise TypeError("audio must be a numpy.ndarray")
        if audio.ndim != 1:
            raise ValueError("audio must be a one-dimensional mono array")

        kwargs: dict[str, Any] = {
            "language": self.language,
            "beam_size": 5,
        }
        if prompt:
            kwargs["initial_prompt"] = prompt

        started = time.monotonic()
        try:
            segments, _info = self._model.transcribe(audio, **kwargs)
            # faster-whisper returns a lazy generator; consume it inside the
            # timed section so latency includes decoding, not just setup.
            text = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()
        except Exception as exc:
            raise LocalWhisperModelError(
                f"Local Whisper transcription failed: {exc}"
            ) from exc

        return TranscriptionResult(
            text=text,
            latency=time.monotonic() - started,
            model=self.model,
        )
