"""Headless verification of Phase 1.4 / Phase 2.1 resilience behaviour.

No API calls, no cost, no audio hardware. Covers:
  1. The bounded retry/backoff helper (`orbit.core.retry`)
  2. OpenAI error classification (retryable vs. offline vs. permanent)
  3. Answer stream cancellation and mid-stream reconnect
  4. Transcriber retry-then-succeed and retry-then-give-up
  5. Worker-level error message shaping (offline vs. generic)

Run:
    python scripts/selftest_resilience.py
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import _bootstrap  # noqa: F401,E402  (sys.path side effect)

PASS = "  PASS  "
FAIL = "  FAIL  "
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{PASS if condition else FAIL} {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        _failures.append(name)


def _make_response(status_code: int):
    import httpx

    request = httpx.Request("POST", "https://api.openai.com/v1/x")
    return httpx.Response(status_code, request=request)


def _connection_error():
    import httpx
    from openai import APIConnectionError

    request = httpx.Request("POST", "https://api.openai.com/v1/x")
    return APIConnectionError(request=request)


def _timeout_error():
    import httpx
    from openai import APITimeoutError

    request = httpx.Request("POST", "https://api.openai.com/v1/x")
    return APITimeoutError(request=request)


def _rate_limit_error():
    from openai import RateLimitError

    return RateLimitError("rate limited", response=_make_response(429), body=None)


def _server_error():
    from openai import InternalServerError

    return InternalServerError("boom", response=_make_response(500), body=None)


def _bad_request_error():
    from openai import BadRequestError

    return BadRequestError("bad request", response=_make_response(400), body=None)


def _auth_error():
    from openai import AuthenticationError

    return AuthenticationError("nope", response=_make_response(401), body=None)


def test_retry_helper() -> None:
    print("\n-- retry/backoff helper --")
    from orbit.core.retry import RetryCancelled, call_with_retry

    # Succeeds immediately, no retries needed.
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return "fine"

    result = call_with_retry(ok, attempts=3, sleep=lambda _: None)
    check("first-try success needs one call", calls["n"] == 1 and result == "fine")

    # Fails twice with a transient error, then succeeds.
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _connection_error()
        return "recovered"

    slept: list[float] = []
    result = call_with_retry(flaky, attempts=5, sleep=slept.append)
    check("recovers within attempt budget", result == "recovered", f"{calls['n']} calls")
    check("backoff slept between failures", len(slept) == 2, str(slept))
    check("backoff delays increase", slept[1] >= slept[0], str(slept))

    # Exhausts the attempt budget and raises the last error.
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise _server_error()

    try:
        call_with_retry(always_fails, attempts=3, sleep=lambda _: None)
        check("exhausted retries raise", False, "no exception raised")
    except Exception as exc:  # noqa: BLE001
        check("exhausted retries raise", type(exc).__name__ == "InternalServerError")
        check("exhausted retries used full budget", calls["n"] == 3, str(calls["n"]))

    # A non-retryable error propagates on the first failure, no retry spent.
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise _bad_request_error()

    try:
        call_with_retry(bad_request, attempts=5, sleep=lambda _: None)
        check("non-retryable propagates immediately", False, "no exception raised")
    except Exception as exc:  # noqa: BLE001
        check(
            "non-retryable propagates immediately",
            type(exc).__name__ == "BadRequestError" and calls["n"] == 1,
            f"{calls['n']} calls",
        )

    # should_stop aborts the loop between attempts instead of exhausting it.
    calls = {"n": 0}
    stop_after_first = {"stop": False}

    def flaky_but_interrupted():
        calls["n"] += 1
        stop_after_first["stop"] = True
        raise _connection_error()

    try:
        call_with_retry(
            flaky_but_interrupted,
            attempts=5,
            sleep=lambda _: None,
            should_stop=lambda: stop_after_first["stop"],
        )
        check("should_stop cancels the retry loop", False, "no exception raised")
    except RetryCancelled:
        check(
            "should_stop cancels the retry loop",
            calls["n"] == 1,
            f"{calls['n']} calls, stopped before exhausting budget",
        )


def test_error_classification() -> None:
    print("\n-- OpenAI error classification --")
    from orbit.core.retry import is_offline_error, is_retryable_openai_error

    check("connection error is retryable", is_retryable_openai_error(_connection_error()))
    check("timeout is retryable", is_retryable_openai_error(_timeout_error()))
    check("rate limit is retryable", is_retryable_openai_error(_rate_limit_error()))
    check("5xx is retryable", is_retryable_openai_error(_server_error()))
    check("bad request is not retryable", not is_retryable_openai_error(_bad_request_error()))
    check("auth failure is not retryable", not is_retryable_openai_error(_auth_error()))

    check("connection error looks offline", is_offline_error(_connection_error()))
    check("timeout looks offline", is_offline_error(_timeout_error()))
    check("rate limit is not 'offline'", not is_offline_error(_rate_limit_error()))
    check("5xx is not 'offline'", not is_offline_error(_server_error()))


class _FakeChunk:
    def __init__(self, text: str | None) -> None:
        delta = type("Delta", (), {"content": text})()
        choice = type("Choice", (), {"delta": delta})()
        self.choices = [choice] if text is not None else []


class _FakeStream:
    """A minimal stand-in for the SDK's streaming response object.

    ``fail_before_first`` raises before any chunk is yielded (simulating a
    connection drop before content ever arrives). ``fail_after`` raises after
    that many chunks have been yielded (simulating a drop mid-answer, once
    something is already on screen).
    """

    def __init__(
        self,
        pieces: list[str],
        *,
        fail_before_first: Exception | None = None,
        fail_after: int | None = None,
        fail_exc: Exception | None = None,
    ) -> None:
        self._pieces = list(pieces)
        self._fail_before_first = fail_before_first
        self._fail_after = fail_after
        self._fail_exc = fail_exc
        self.closed = False

    def __iter__(self):
        if self._fail_before_first is not None:
            exc, self._fail_before_first = self._fail_before_first, None
            raise exc
        for i, piece in enumerate(self._pieces):
            yield _FakeChunk(piece)
            if self._fail_after is not None and i == self._fail_after - 1:
                raise self._fail_exc

    def close(self) -> None:
        self.closed = True


def test_answer_generation_cancel() -> None:
    print("\n-- answer stream cancellation --")
    from orbit.llm.openai_llm import AnswerGeneration

    stream = _FakeStream(["Hello", " ", "world", "!", " more", " than", " enough"])
    completed: list[str] = []
    gen = AnswerGeneration(stream, completed.append)

    collected = ""
    for i, delta in enumerate(gen):
        collected += delta
        if i == 1:  # cancel partway through
            gen.cancel()
            break

    check("cancelled flag set", gen.cancelled)
    check("underlying stream closed", stream.closed)
    check("partial text kept, not discarded", collected == "Hello ", repr(collected))
    check("on_complete NOT called for a cancelled answer", completed == [], str(completed))

    # Calling cancel() again, or after the generation is done, must be safe.
    gen.cancel()
    check("cancel is idempotent", gen.cancelled)


def test_answer_generation_reconnect() -> None:
    print("\n-- answer stream reconnect --")
    from orbit.core.retry import RetryCancelled
    from orbit.llm.openai_llm import AnswerGeneration

    # First stream dies before yielding anything; reopen hands back a good one.
    broken = _FakeStream([], fail_before_first=_connection_error())
    good = _FakeStream(["Recovered", " answer"])
    opened = {"n": 0}

    def reopen():
        opened["n"] += 1
        return good

    completed: list[str] = []
    gen = AnswerGeneration(broken, completed.append, reopen=reopen)
    text = "".join(gen)

    check("reconnected after a pre-first-token failure", text == "Recovered answer", repr(text))
    check("reopen invoked exactly once", opened["n"] == 1, str(opened["n"]))
    check("on_complete fires for a completed answer", completed == ["Recovered answer"])

    # A failure AFTER the first token must NOT be retried: the answer is
    # already partially on screen, so resending the whole request would
    # duplicate/contradict it.
    partial_then_broken = _FakeStream(
        ["Some "], fail_after=1, fail_exc=_connection_error()
    )
    never_reopen_calls = {"n": 0}

    def never_reopen():
        never_reopen_calls["n"] += 1
        return _FakeStream(["should not be used"])

    gen2 = AnswerGeneration(partial_then_broken, lambda _t: None, reopen=never_reopen)
    try:
        list(gen2)
        check("failure after first token propagates", False, "no exception raised")
    except Exception as exc:  # noqa: BLE001
        check(
            "failure after first token propagates",
            type(exc).__name__ == "APIConnectionError" and never_reopen_calls["n"] == 0,
            f"reopen calls={never_reopen_calls['n']}",
        )
    check("partial text preserved before the failure", gen2.text == "Some ", repr(gen2.text))

    # A non-retryable error must not attempt to reopen, even before any token
    # has arrived: retrying only makes sense for transient network problems.
    class _Boom(Exception):
        pass

    boom_stream = _FakeStream([], fail_before_first=_Boom("nope"))
    reopen_calls = {"n": 0}

    def reopen_boom():
        reopen_calls["n"] += 1
        return good

    gen3 = AnswerGeneration(boom_stream, lambda _t: None, reopen=reopen_boom)
    try:
        list(gen3)
        check("non-retryable stream error propagates", False)
    except _Boom:
        check("non-retryable stream error propagates", reopen_calls["n"] == 0)


class _FakeTranscriptions:
    def __init__(self, fail_times: int, exc_factory, text: str = "hello there") -> None:
        self.fail_times = fail_times
        self.exc_factory = exc_factory
        self.text = text
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc_factory()
        return self.text


class _FakeAudio:
    def __init__(self, transcriptions) -> None:
        self.transcriptions = transcriptions


class _FakeClient:
    def __init__(self, transcriptions) -> None:
        self.audio = _FakeAudio(transcriptions)


def test_transcriber_retry() -> None:
    print("\n-- transcriber retry --")
    import numpy as np

    from orbit.stt.openai_stt import OpenAITranscriber

    audio = np.zeros(1600, dtype=np.float32)

    # Recovers within the attempt budget.
    transcriptions = _FakeTranscriptions(fail_times=2, exc_factory=_connection_error)
    client = _FakeClient(transcriptions)
    transcriber = OpenAITranscriber(client, retry_attempts=3)
    # Patch out real sleeping so the test is instant.
    import orbit.core.retry as retry_mod

    original_sleep = retry_mod.time.sleep
    retry_mod.time.sleep = lambda _s: None
    try:
        result = transcriber.transcribe(audio)
    finally:
        retry_mod.time.sleep = original_sleep

    check("transcription recovers after transient failures", result.text == "hello there")
    check("used exactly the failing + succeeding calls", transcriptions.calls == 3, str(transcriptions.calls))

    # Gives up cleanly once the attempt budget is exhausted.
    transcriptions2 = _FakeTranscriptions(fail_times=99, exc_factory=_server_error)
    client2 = _FakeClient(transcriptions2)
    transcriber2 = OpenAITranscriber(client2, retry_attempts=2)
    retry_mod.time.sleep = lambda _s: None
    try:
        try:
            transcriber2.transcribe(audio)
            check("exhausted transcription retries raise", False)
        except Exception as exc:  # noqa: BLE001
            check(
                "exhausted transcription retries raise",
                type(exc).__name__ == "InternalServerError" and transcriptions2.calls == 2,
                f"{transcriptions2.calls} calls",
            )
    finally:
        retry_mod.time.sleep = original_sleep

    # A stop request in flight aborts the retry loop rather than exhausting it.
    from orbit.core.retry import RetryCancelled

    transcriptions3 = _FakeTranscriptions(fail_times=99, exc_factory=_connection_error)
    client3 = _FakeClient(transcriptions3)

    transcriber3 = OpenAITranscriber(client3, retry_attempts=5, should_stop=lambda: True)
    retry_mod.time.sleep = lambda _s: None
    try:
        try:
            transcriber3.transcribe(audio)
            check("stop mid-retry cancels transcription", False)
        except RetryCancelled:
            check("stop mid-retry cancels transcription", transcriptions3.calls == 1, str(transcriptions3.calls))
    finally:
        retry_mod.time.sleep = original_sleep


def test_worker_error_messages() -> None:
    print("\n-- worker error messages --")
    from orbit.core.worker import _describe_error, _offline_message, _short_error

    offline = _describe_error(_connection_error())
    check("offline error gets a network-specific message", "internet connection" in offline.lower(), offline[:60])

    generic = _describe_error(_bad_request_error())
    check(
        "non-offline error keeps the short generic message",
        generic == _short_error(_bad_request_error()),
        generic,
    )

    check("offline message helper mentions retrying context", "OpenAI" in _offline_message(_connection_error()))


def test_worker_cancellation_wiring() -> None:
    print("\n-- worker cancellation wiring --")
    from orbit.core.worker import PipelineWorker
    from orbit.settings import Settings

    worker = PipelineWorker(Settings())
    check("not interrupted initially", not worker._interrupted())
    worker.request_force_answer()
    check("force-answer marks interrupted (cancels in-flight answer)", worker._interrupted())
    worker._force_answer.clear()
    check("clearing force flag restores normal state", not worker._interrupted())
    worker.request_stop()
    check("stop marks interrupted", worker._interrupted())


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    print("orbit-ai Phase 1.4 / 2.1 resilience self-test (offscreen, no API calls)")

    test_retry_helper()
    test_error_classification()
    test_answer_generation_cancel()
    test_answer_generation_reconnect()
    test_transcriber_retry()
    test_worker_error_messages()
    test_worker_cancellation_wiring()

    app.processEvents()

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
