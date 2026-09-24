"""Bounded retry with exponential backoff for OpenAI network calls.

Transcription and answer requests both cross the network, and both should
survive a transient blip (a dropped connection, a rate limit, a 5xx) without
surfacing an error to a person who is mid-meeting. Retrying is deliberately
bounded: a hung network should degrade to a clear error rather than a worker
thread that silently retries forever and never reports back.
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

#: Attempts, not retries: 3 means one initial try plus two retries.
DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.5
DEFAULT_MAX_DELAY = 4.0


def is_retryable_openai_error(exc: Exception) -> bool:
    """True for transient OpenAI SDK errors worth retrying.

    Deliberately excludes anything the network cannot fix on its own:
    bad requests, auth failures, and missing resources all need the caller (or
    the user) to change something, so retrying just wastes the retry budget.
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
        return True
    # Some 5xx responses surface as the base APIStatusError rather than one of
    # the named subclasses above, depending on SDK version and endpoint.
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


def is_offline_error(exc: Exception) -> bool:
    """True when ``exc`` looks like "no network", as opposed to an API error.

    Used to give a specific "you appear to be offline" message rather than a
    generic API failure, since the fix (check your connection) is different.
    """
    from openai import APIConnectionError, APITimeoutError

    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    # APIConnectionError.__cause__ typically holds the underlying httpx/socket
    # error, but be tolerant of a raw OSError escaping the SDK too.
    return isinstance(exc, OSError)


class RetryCancelled(Exception):
    """Raised when ``should_stop`` aborts a retry loop before any success.

    Distinct from the underlying transient error so callers can tell "gave up
    because the user stopped" apart from "gave up because retries ran out".
    """

    def __init__(self, last_error: Exception | None = None) -> None:
        super().__init__("retry loop cancelled")
        self.last_error = last_error


def call_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    should_retry: Callable[[Exception], bool] = is_retryable_openai_error,
    should_stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` with bounded exponential backoff plus jitter.

    Args:
        attempts: Total attempts including the first, not additional retries.
        should_retry: Predicate deciding whether an exception is transient.
            Anything it rejects propagates immediately.
        should_stop: Optional check consulted between attempts (never before
            the first) so a user-requested stop aborts the retry loop instead
            of running out its full backoff first. Raises :class:`RetryCancelled`.
        sleep: Injectable for tests.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below when exhausted
            last_exc = exc
            if not should_retry(exc):
                raise
            if attempt == attempts - 1:
                break
            if should_stop is not None and should_stop():
                raise RetryCancelled(exc) from exc
            delay = min(max_delay, base_delay * (2**attempt))
            delay += random.uniform(0, delay * 0.25)
            sleep(delay)

    assert last_exc is not None
    raise last_exc
