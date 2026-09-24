"""Offline self-test. Verifies the pipeline logic without spending API credit.

Covers the parts most likely to be silently wrong:
  1. Resampling 48 kHz stereo -> 16 kHz mono, including sample-count accuracy
  2. Utterance segmentation against synthetic speech-then-silence audio
  3. WAV encoding produces a valid, correctly-sized container
  4. Rolling context bookkeeping in the answer generator
  5. Live loopback capture actually delivers samples from the sound card

Run:
    python scripts/selftest.py
"""

from __future__ import annotations

import time

import _bootstrap  # noqa: F401  (sys.path side effect)

import numpy as np
import pyaudiowpatch as pyaudio

from orbit.audio.capture import SystemAudioCapture, _Resampler
from orbit.audio.devices import find_default_loopback
from orbit.audio.vad import UtteranceSegmenter
from orbit.config import TARGET_SAMPLE_RATE, VAD_FRAME_MS, VadSettings
from orbit.stt.openai_stt import encode_wav

PASS = "  PASS  "
FAIL = "  FAIL  "
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{PASS if condition else FAIL} {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        _failures.append(name)


def tone(freq: float, seconds: float, rate: int, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---------------------------------------------------------------------------

def test_resampler() -> None:
    print("\n-- resampling --")
    r = _Resampler(48_000, TARGET_SAMPLE_RATE)
    one_second = tone(440.0, 1.0, 48_000)
    out = r.process(one_second)

    # Streaming resamplers hold a little audio in their filter delay line, so
    # allow a small shortfall rather than demanding exactly 16000 samples.
    check(
        "48kHz -> 16kHz sample count",
        15_000 <= out.size <= 16_100,
        f"{one_second.size} in -> {out.size} out",
    )
    check("output is float32", out.dtype == np.float32, str(out.dtype))
    check(
        "amplitude preserved",
        0.15 < float(np.abs(out).max()) < 0.45,
        f"peak {float(np.abs(out).max()):.3f}",
    )

    passthrough = _Resampler(16_000, 16_000)
    same = passthrough.process(one_second[:16_000])
    check("same-rate is a passthrough", same.size == 16_000, f"{same.size} samples")


def test_segmenter() -> None:
    print("\n-- utterance segmentation --")
    settings = VadSettings(
        calibration_seconds=0.5,
        silence_frames_to_end=15,  # 300 ms, keeps the test quick
        min_utterance_seconds=0.3,
    )
    seg = UtteranceSegmenter(settings)
    rng = np.random.default_rng(1234)

    def quiet(seconds: float) -> np.ndarray:
        return (rng.standard_normal(int(seconds * TARGET_SAMPLE_RATE)) * 0.0005).astype(
            np.float32
        )

    # Calibrate on near-silence.
    emitted = seg.push(quiet(0.6))
    check("no utterance during calibration", emitted == [], f"{len(emitted)} emitted")
    check("calibration completes", not seg.is_calibrating)
    check("threshold above noise floor", seg.threshold > 0.0, f"{seg.threshold:.5f}")

    # 1.2s of "speech", then enough silence to close the utterance.
    speech = tone(300.0, 1.2, TARGET_SAMPLE_RATE, amplitude=0.25)
    emitted = seg.push(speech)
    check("no premature emit while speaking", emitted == [])
    check("segmenter is in speech state", seg.in_speech)

    emitted = seg.push(quiet(0.6))
    check("utterance emitted after silence", len(emitted) == 1, f"{len(emitted)} emitted")

    if emitted:
        utt = emitted[0]
        check(
            "duration approximately correct",
            1.0 <= utt.duration <= 1.8,
            f"{utt.duration:.2f}s for 1.2s of speech",
        )
        check("not length-capped", not utt.truncated)
        check("timestamps ordered", utt.ended_at > utt.started_at)
        pcm = utt.to_int16()
        check("int16 conversion", pcm.dtype == np.int16, str(pcm.dtype))
        check(
            "int16 within range",
            int(np.abs(pcm).max()) <= 32767,
            f"peak {int(np.abs(pcm).max())}",
        )

    # A very short blip must be rejected as noise, not sent to the API.
    seg2 = UtteranceSegmenter(settings)
    seg2.push(quiet(0.6))
    blip = tone(300.0, 0.15, TARGET_SAMPLE_RATE, amplitude=0.25)
    emitted = seg2.push(blip) + seg2.push(quiet(0.6))
    check("short blip discarded", emitted == [], f"{len(emitted)} emitted")

    # Continuous speech past the cap must still emit.
    capped = VadSettings(
        calibration_seconds=0.5, max_utterance_seconds=1.0, min_utterance_seconds=0.3
    )
    seg3 = UtteranceSegmenter(capped)
    seg3.push(quiet(0.6))
    emitted = seg3.push(tone(300.0, 2.5, TARGET_SAMPLE_RATE, amplitude=0.25))
    check("long speech is length-capped", len(emitted) >= 1, f"{len(emitted)} emitted")
    if emitted:
        check("cap flag set", emitted[0].truncated)


def test_wav_encoding() -> None:
    print("\n-- wav encoding --")
    samples = (tone(440.0, 0.5, TARGET_SAMPLE_RATE) * 32767).astype(np.int16)
    data = encode_wav(samples, TARGET_SAMPLE_RATE)

    check("RIFF header", data[:4] == b"RIFF", data[:4].decode("latin1"))
    check("WAVE format", data[8:12] == b"WAVE", data[8:12].decode("latin1"))
    expected = 44 + samples.nbytes  # 44-byte canonical header
    check("size matches payload", len(data) == expected, f"{len(data)} vs {expected}")

    import io
    import wave

    with wave.open(io.BytesIO(data), "rb") as wav:
        check("mono", wav.getnchannels() == 1, f"{wav.getnchannels()}ch")
        check("16-bit", wav.getsampwidth() == 2, f"{wav.getsampwidth() * 8}-bit")
        check("16 kHz", wav.getframerate() == TARGET_SAMPLE_RATE, str(wav.getframerate()))
        check("frame count", wav.getnframes() == samples.size, str(wav.getnframes()))


def test_context_bookkeeping() -> None:
    print("\n-- rolling context --")
    from orbit.config import ModelSettings
    from orbit.llm.openai_llm import NO_QUESTION_MARKER, AnswerGenerator

    gen = AnswerGenerator.__new__(AnswerGenerator)  # no client needed
    from collections import deque

    gen.settings = ModelSettings(context_turns=2)
    gen._context = deque(maxlen=4)
    gen._pending_question = None

    gen._pending_question = "what is a mutex?"
    gen._commit_turn("A mutual exclusion lock.")
    check("real answer stored", gen.context_size == 2, f"{gen.context_size} messages")

    gen._pending_question = "hello how are you"
    gen._commit_turn(NO_QUESTION_MARKER)
    check(
        "non-question excluded from context",
        gen.context_size == 2,
        f"{gen.context_size} messages",
    )

    for i in range(5):
        gen._pending_question = f"q{i}"
        gen._commit_turn(f"a{i}")
    check("context bounded by maxlen", gen.context_size == 4, f"{gen.context_size}")

    gen.reset_context()
    check("reset clears context", gen.context_size == 0)


def test_live_capture() -> None:
    print("\n-- live loopback capture --")
    with pyaudio.PyAudio() as pa:
        device = find_default_loopback(pa)
        print(f"         device: {device}")

        with SystemAudioCapture(pa, device) as cap:
            collected: list[np.ndarray] = []
            started = time.monotonic()
            deadline = started + 3.0
            # Bounded idle wait so a driver that delivers nothing while idle
            # ends the test with a diagnostic instead of hanging forever.
            for chunk in cap.frames(timeout=0.25, idle_timeout=4.0):
                collected.append(chunk)
                if time.monotonic() > deadline:
                    break
            elapsed = time.monotonic() - started

            check(
                "no callback errors",
                cap.callback_error is None,
                cap.callback_error or "clean",
            )

            if cap.timed_out_idle:
                check(
                    "capture delivered audio",
                    False,
                    "device delivered no buffers while idle; play audio and retry",
                )
                return

            total = sum(c.size for c in collected)
            seconds = total / TARGET_SAMPLE_RATE
            check(
                "capture delivered audio",
                total > 0,
                f"{len(collected)} chunks, {seconds:.2f}s",
            )
            check(
                "roughly real-time",
                abs(seconds - elapsed) < 1.5,
                f"{seconds:.2f}s audio in {elapsed:.2f}s wall clock",
            )
            check("no dropped chunks", cap.dropped_chunks == 0, str(cap.dropped_chunks))
            if collected:
                sample = np.concatenate(collected)
                check("mono float32", sample.ndim == 1 and sample.dtype == np.float32)
                peak = float(np.abs(sample).max())
                check("samples in valid range", peak <= 1.0, f"peak {peak:.4f}")
                if peak < 1e-6:
                    print(
                        "         note: captured silence. That is expected if "
                        "nothing was playing. Play audio to test with real signal."
                    )
                else:
                    print(f"         live signal detected, peak {peak:.4f}")

    print("         teardown completed without crashing")


def main() -> int:
    print("orbit-ai Phase 0 self-test (no API calls, no cost)")
    print(f"frame size: {VAD_FRAME_MS} ms, target rate: {TARGET_SAMPLE_RATE} Hz")

    test_resampler()
    test_segmenter()
    test_wav_encoding()
    test_context_bookkeeping()
    test_live_capture()

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
