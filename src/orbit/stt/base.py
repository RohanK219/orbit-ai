"""The transcription seam.

Transcription is the one component with a genuine cost/latency tradeoff:

  - OpenAI API: works on any machine, no model download, costs roughly
    $0.003/minute of audio, and every request is a network round-trip.
  - Local faster-whisper: free per-minute and removes the network hop entirely,
    but needs a capable GPU to keep up with live audio.

Keeping both behind this interface means the choice is a runtime setting rather
than an architectural commitment. Phase 0 measures the API path; a local backend
can be added later by implementing :class:`Transcriber` and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(slots=True)
class TranscriptionResult:
    """Transcribed text plus the timing needed for latency accounting."""

    text: str
    latency: float
    """Seconds spent inside the transcription call itself."""

    model: str = ""


@runtime_checkable
class Transcriber(Protocol):
    """Turns mono float32 audio at 16 kHz into text."""

    def transcribe(
        self, audio: np.ndarray, *, prompt: str | None = None
    ) -> TranscriptionResult:
        """Transcribe one utterance.

        Args:
            audio: Mono float32 samples in the range -1.0 to 1.0 at 16 kHz.
            prompt: Optional vocabulary hint to bias decoding, useful for
                domain jargon and proper nouns.
        """
        ...
