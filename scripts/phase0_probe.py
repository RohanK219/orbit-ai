"""Phase 0: end-to-end latency probe. No GUI.

Proves every risky part of orbit-ai in one process:

    system audio (WASAPI loopback)
      -> mono 16 kHz
      -> utterance segmentation
      -> transcription
      -> streaming answer
      -> stdout, with timings

The point is measurement, not usability. Run it against a real call (or a
YouTube video of someone asking technical questions), read the summary, and
decide whether the Phase 1 overlay is worth building on these numbers.

Usage:
    python scripts/phase0_probe.py
    python scripts/phase0_probe.py --silence-ms 500 --llm-model gpt-4o
    python scripts/phase0_probe.py --no-llm          # isolate STT latency
    python scripts/phase0_probe.py --save-audio recordings/   # debug the VAD

Press Ctrl+C to stop and print the summary.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path side effect)

import pyaudiowpatch as pyaudio
from openai import OpenAI, OpenAIError

from orbit.audio.capture import SystemAudioCapture
from orbit.audio.devices import AudioDeviceError, resolve_device
from orbit.audio.vad import Utterance, UtteranceSegmenter
from orbit.config import (
    TARGET_SAMPLE_RATE,
    VAD_FRAME_MS,
    ModelSettings,
    VadSettings,
    load_api_key,
)
from orbit.llm.openai_llm import AnswerGenerator
from orbit.metrics import Measurement, MetricsCollector, endpoint_delay_seconds
from orbit.stt.openai_stt import OpenAITranscriber, encode_wav


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

class Style:
    """Minimal ANSI styling, disabled automatically when unsupported."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def bold(self, t: str) -> str:
        return self._wrap("1", t)

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)

    def green(self, t: str) -> str:
        return self._wrap("32", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)


def _enable_ansi() -> bool:
    """Turn on virtual terminal processing so ANSI codes render on Windows."""
    if not sys.stdout.isatty():
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        # 0x0001 ENABLE_PROCESSED_OUTPUT | 0x0002 WRAP_AT_EOL | 0x0004 VT
        return bool(kernel32.SetConsoleMode(handle, 7))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="orbit-ai Phase 0 latency probe",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--device", type=int, default=None,
        help="Loopback device index (default: auto-detect). See list_devices.py",
    )
    p.add_argument(
        "--silence-ms", type=int, default=700,
        help="Silence before a question is considered finished. The main latency knob",
    )
    p.add_argument(
        "--threshold", type=float, default=3.5,
        help="Speech threshold as a multiple of the measured noise floor",
    )
    p.add_argument(
        "--min-utterance", type=float, default=0.4,
        help="Ignore speech shorter than this many seconds",
    )
    p.add_argument("--stt-model", default=ModelSettings().stt_model)
    p.add_argument("--llm-model", default=ModelSettings().llm_model)
    p.add_argument(
        "--language", default="en",
        help="Language hint for transcription; 'auto' to let the model detect",
    )
    p.add_argument(
        "--vocab", default=None,
        help="Comma-separated jargon to bias transcription (names, tech terms)",
    )
    p.add_argument(
        "--no-llm", action="store_true",
        help="Transcribe only. Isolates STT latency and costs almost nothing",
    )
    p.add_argument(
        "--no-drain", action="store_true",
        help="Keep audio buffered during answers instead of discarding the backlog",
    )
    p.add_argument(
        "--save-audio", type=Path, default=None,
        help="Directory to write each utterance as WAV, for debugging segmentation",
    )
    p.add_argument("--no-color", action="store_true")
    return p


def save_utterance(directory: Path, index: int, utterance: Utterance) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"utterance_{index:03d}.wav"
    path.write_bytes(encode_wav(utterance.to_int16(), TARGET_SAMPLE_RATE))
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = build_parser().parse_args()
    st = Style(enabled=not args.no_color and _enable_ansi())

    try:
        api_key = load_api_key()
    except RuntimeError as exc:
        print(st.red("Missing credentials"))
        print(exc)
        return 1

    vad_settings = VadSettings(
        speech_threshold_multiplier=args.threshold,
        silence_frames_to_end=max(1, args.silence_ms // VAD_FRAME_MS),
        min_utterance_seconds=args.min_utterance,
    )
    model_settings = ModelSettings(
        stt_model=args.stt_model,
        llm_model=args.llm_model,
        stt_language=None if args.language == "auto" else args.language,
    )

    client = OpenAI(api_key=api_key)
    transcriber = OpenAITranscriber(
        client, model=model_settings.stt_model, language=model_settings.stt_language
    )
    generator = AnswerGenerator(client, model_settings)
    metrics = MetricsCollector(
        stt_model=model_settings.stt_model,
        llm_model="" if args.no_llm else model_settings.llm_model,
    )
    fixed_endpoint_delay = endpoint_delay_seconds(vad_settings)
    vocab_prompt = args.vocab.replace(",", ", ") if args.vocab else None

    with pyaudio.PyAudio() as pa:
        try:
            device = resolve_device(pa, args.device)
        except AudioDeviceError as exc:
            print(st.red("Audio device error"))
            print(exc)
            return 1

        print(st.bold("orbit-ai Phase 0 probe"))
        print(f"  Capture   {device}")
        print(f"  STT       {model_settings.stt_model}")
        print(f"  LLM       {'(disabled)' if args.no_llm else model_settings.llm_model}")
        print(
            f"  Endpoint  {args.silence_ms} ms silence "
            f"({fixed_endpoint_delay:.2f}s fixed cost per question)"
        )
        print()

        segmenter = UtteranceSegmenter(vad_settings)
        counter = 0

        try:
            with SystemAudioCapture(pa, device) as capture:
                print(
                    st.dim(
                        f"Calibrating noise floor for "
                        f"{vad_settings.calibration_seconds:.0f}s - keep audio quiet..."
                    )
                )
                calibrated = False

                for chunk in capture.frames(idle_timeout=20.0):
                    utterances = segmenter.push(chunk)

                    if not calibrated and not segmenter.is_calibrating:
                        calibrated = True
                        print(
                            st.green("Listening.")
                            + st.dim(
                                f" threshold RMS {segmenter.threshold:.5f}. "
                                "Play meeting audio. Ctrl+C to stop."
                            )
                        )
                        print()

                    for utterance in utterances:
                        counter += 1
                        handled = handle_utterance(
                            counter=counter,
                            utterance=utterance,
                            transcriber=transcriber,
                            generator=None if args.no_llm else generator,
                            metrics=metrics,
                            endpoint_delay=fixed_endpoint_delay,
                            vocab_prompt=vocab_prompt,
                            style=st,
                        )
                        if args.save_audio:
                            path = save_utterance(args.save_audio, counter, utterance)
                            print(st.dim(f"    audio saved to {path}"))
                        if handled and not args.no_drain:
                            dropped = capture.drain()
                            if dropped:
                                print(
                                    st.dim(
                                        f"    discarded {dropped * 50} ms of audio "
                                        "buffered while answering"
                                    )
                                )
                        print()

                if capture.timed_out_idle:
                    print(st.yellow("No audio received from the capture device."))
                    print(
                        "Loopback devices deliver nothing while the endpoint is idle "
                        "on some drivers.\nStart playing audio through "
                        f"{device.name!r} first, then run this again."
                    )

        except KeyboardInterrupt:
            print()
            print(st.dim("Stopping..."))
            tail = segmenter.flush()
            if tail is not None:
                print(st.dim(f"  (discarded {tail.duration:.1f}s of trailing speech)"))

    print(metrics.summary())
    return 0


def handle_utterance(
    *,
    counter: int,
    utterance: Utterance,
    transcriber: OpenAITranscriber,
    generator: AnswerGenerator | None,
    metrics: MetricsCollector,
    endpoint_delay: float,
    vocab_prompt: str | None,
    style: Style,
) -> bool:
    """Transcribe and answer one utterance. Returns True if it was a real question."""
    st = style
    label = f"[{counter:02d}]"
    flag = " (length-capped)" if utterance.truncated else ""
    print(
        st.dim(f"{label} speech {utterance.duration:.1f}s{flag} -> transcribing...")
    )

    try:
        result = transcriber.transcribe(utterance.audio, prompt=vocab_prompt)
    except OpenAIError as exc:
        print(st.red(f"    transcription failed: {exc}"))
        return False

    if not result.text:
        print(st.dim(f"    no speech recognised ({result.latency:.2f}s)"))
        return False

    print(st.cyan(f"    heard ({result.latency:.2f}s): ") + result.text)

    if generator is None:
        metrics.add(
            Measurement(
                speech_duration=utterance.duration,
                endpoint_delay=endpoint_delay,
                stt_latency=result.latency,
                llm_first_token_latency=None,
                llm_total_latency=0.0,
                transcript_chars=len(result.text),
            )
        )
        return True

    print(st.bold("    answer: "), end="", flush=True)
    generation = generator.answer(result.text)
    try:
        for delta in generation:
            # Indent continuation lines so multi-line answers stay readable.
            sys.stdout.write(delta.replace("\n", "\n            "))
            sys.stdout.flush()
    except OpenAIError as exc:
        print()
        print(st.red(f"    answer failed: {exc}"))
        return False
    print()

    if not generation.is_question:
        print(st.dim("    (not a question - skipped)"))
        return False

    ttft = generation.first_token_latency
    perceived = endpoint_delay + result.latency + (ttft or 0.0)
    ttft_text = f"{ttft:.2f}s" if ttft is not None else "n/a"
    print(
        st.dim(
            f"    timings: endpoint {endpoint_delay:.2f}s | "
            f"stt {result.latency:.2f}s | "
            f"first token {ttft_text} | "
            f"full answer {generation.total_latency:.2f}s | "
        )
        + st.yellow(f"visible after {perceived:.2f}s")
    )

    metrics.add(
        Measurement(
            speech_duration=utterance.duration,
            endpoint_delay=endpoint_delay,
            stt_latency=result.latency,
            llm_first_token_latency=ttft,
            llm_total_latency=generation.total_latency,
            transcript_chars=len(result.text),
            answer_chars=len(generation.text),
        )
    )
    return True


if __name__ == "__main__":
    raise SystemExit(main())
