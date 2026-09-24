"""OpenAI transcription backend.

Uses the batch transcription endpoint, one request per completed utterance,
rather than a persistent Realtime WebSocket session.

That is a deliberate Phase 0 choice. Batch is far simpler and it is the thing we
need to *measure*: if one request per utterance lands within a few seconds, the
extra complexity of a streaming session buys nothing. If it does not, Phase 1
upgrades this one class and the rest of the pipeline is unaffected.
"""

from __future__ import annotations

import io
import time
import wave
from typing import Callable

import numpy as np
from openai import OpenAI

from ..config import TARGET_SAMPLE_RATE
from ..core.retry import call_with_retry
from .base import TranscriptionResult


def encode_wav(audio_int16: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Wrap raw PCM in a WAV container.

    The API infers format from the container and filename, so raw PCM will be
    rejected. WAV is uncompressed, meaning zero encode latency, which matters
    more here than the extra bytes on the wire.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # int16
        wav.setframerate(sample_rate)
        wav.writeframes(audio_int16.tobytes())
    return buffer.getvalue()


class OpenAITranscriber:
    """Transcribes utterances with an OpenAI speech-to-text model."""

    def __init__(
        self,
        client: OpenAI,
        model: str = "gpt-4o-mini-transcribe",
        language: str | None = "en",
        *,
        retry_attempts: int = 3,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self.language = language
        #: Bounded so a dead network fails fast with a clear error rather than
        #: leaving a meeting-time worker thread retrying indefinitely.
        self._retry_attempts = retry_attempts
        #: Optional cancellation check, consulted between retry attempts, so a
        #: user-requested stop does not have to wait out the full backoff.
        self._should_stop = should_stop

    def transcribe(
        self, audio: np.ndarray, *, prompt: str | None = None
    ) -> TranscriptionResult:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
        wav_bytes = encode_wav(pcm)

        # Tuple form (filename, content, mimetype) is the most reliable way to
        # upload in-memory audio; a bare BytesIO has no name for format sniffing.
        file_payload = ("utterance.wav", wav_bytes, "audio/wav")

        kwargs: dict[str, object] = {
            "model": self.model,
            "file": file_payload,
            "response_format": "text",
        }
        if self.language:
            kwargs["language"] = self.language
        if prompt:
            kwargs["prompt"] = prompt

        started = time.monotonic()
        response = call_with_retry(
            lambda: self._client.audio.transcriptions.create(**kwargs),
            attempts=self._retry_attempts,
            should_stop=self._should_stop,
        )
        latency = time.monotonic() - started

        # response_format="text" yields a plain string, but be tolerant in case
        # the SDK returns a model object instead.
        text = response if isinstance(response, str) else getattr(response, "text", "")

        return TranscriptionResult(
            text=text.strip(), latency=latency, model=self.model
        )
