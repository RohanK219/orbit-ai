"""Streaming answer generation with in-memory conversation context.

Streaming is not a nicety here, it is the main latency mitigation. A complete
answer to a coding question can take ten seconds or more to generate, but the
first tokens arrive in well under a second, so text appears on screen almost
immediately and keeps filling in while you read.

Context is a bounded deque in RAM. It exists so follow-ups ("now make it
O(n)", "what about empty input") resolve correctly. It is never written to disk
and dies with the process.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Iterator

from openai import OpenAI

from ..config import SYSTEM_PROMPT, ModelSettings

#: Sentinel the model returns for audio that was not actually a question.
#: Filtering these keeps the display quiet during small talk.
NO_QUESTION_MARKER = "(no question detected)"


class AnswerGeneration:
    """A single in-flight answer.

    Iterate to consume text deltas as they arrive, then read the timing fields::

        gen = generator.answer("what is a deadlock?")
        for delta in gen:
            print(delta, end="")
        print(gen.first_token_latency)
    """

    def __init__(self, stream, on_complete) -> None:
        self._stream = stream
        self._on_complete = on_complete
        self.text = ""
        self.first_token_latency: float | None = None
        self.total_latency: float = 0.0
        self._started = time.monotonic()

    def __iter__(self) -> Iterator[str]:
        try:
            for event in self._stream:
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                piece = getattr(delta, "content", None)
                if not piece:
                    continue
                if self.first_token_latency is None:
                    self.first_token_latency = time.monotonic() - self._started
                self.text += piece
                yield piece
        finally:
            self.total_latency = time.monotonic() - self._started
            self._on_complete(self.text)

    @property
    def is_question(self) -> bool:
        """False when the model judged the audio to contain no real question."""
        return NO_QUESTION_MARKER not in self.text.strip().lower()


class AnswerGenerator:
    """Turns transcribed questions into streamed answers."""

    def __init__(self, client: OpenAI, settings: ModelSettings | None = None) -> None:
        self._client = client
        self.settings = settings or ModelSettings()
        # Two messages per turn (question + answer).
        self._context: deque[dict[str, str]] = deque(
            maxlen=self.settings.context_turns * 2
        )
        self._pending_question: str | None = None

    def reset_context(self) -> None:
        """Forget the conversation so far. Bound to a hotkey in Phase 1."""
        self._context.clear()
        self._pending_question = None

    @property
    def context_size(self) -> int:
        return len(self._context)

    def answer(self, question: str) -> AnswerGeneration:
        """Start streaming an answer to ``question``."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._context,
            {"role": "user", "content": question},
        ]

        stream = self._create_stream(messages)
        self._pending_question = question
        return AnswerGeneration(stream, self._commit_turn)

    def _create_stream(self, messages: list[dict[str, str]]):
        """Open a streaming completion, tolerating per-model parameter support.

        The model is a user-facing setting, so it may be a reasoning model that
        rejects ``temperature`` or requires ``max_completion_tokens``. Rather
        than fail with an opaque 400, drop whichever parameter the API objects
        to and retry. ``max_tokens`` is deprecated in favour of
        ``max_completion_tokens``, so we lead with the current name.
        """
        from openai import BadRequestError

        optional: dict[str, object] = {
            "max_completion_tokens": self.settings.max_answer_tokens,
            "temperature": 0.2,  # Low: we want accuracy, not variety.
        }

        while True:
            try:
                return self._client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=messages,
                    stream=True,
                    **optional,
                )
            except BadRequestError as exc:
                offending = self._unsupported_param(exc, optional)
                if offending is None:
                    raise
                optional.pop(offending)

    @staticmethod
    def _unsupported_param(
        error: Exception, candidates: dict[str, object]
    ) -> str | None:
        """Return the candidate parameter the API rejected, if identifiable."""
        message = str(error).lower()
        if "unsupported" not in message and "not supported" not in message:
            return None
        for name in candidates:
            if name in message:
                return name
        return None

    def _commit_turn(self, answer_text: str) -> None:
        question = self._pending_question
        self._pending_question = None
        if not question or not answer_text:
            return
        # Don't pollute context with non-questions.
        if NO_QUESTION_MARKER in answer_text.strip().lower():
            return
        self._context.append({"role": "user", "content": question})
        self._context.append({"role": "assistant", "content": answer_text})
