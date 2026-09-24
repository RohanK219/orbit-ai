"""Latency and cost accounting.

Phase 0 exists to answer one question: is this fast enough to use in a live
conversation? Everything here serves that.

The headline number is :attr:`Measurement.time_to_first_text` - seconds between
the other person finishing their question and useful text appearing on your
screen. That, not total generation time, is what decides whether the tool feels
usable, because you can start reading while the rest streams in.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .config import VAD_FRAME_MS, VadSettings

# Indicative USD rates for cost estimation only. These move, and vary by region
# and account. Verify against https://platform.openai.com/pricing before relying
# on any figure this module prints.
_STT_USD_PER_MINUTE = {
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-transcribe": 0.006,
    "whisper-1": 0.006,
}
_LLM_USD_PER_MTOK = {
    # (input, output)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
_CHARS_PER_TOKEN = 4  # Rough English approximation, adequate for estimates.


@dataclass(slots=True)
class Measurement:
    """Timings for one question/answer cycle. All values in seconds."""

    speech_duration: float
    endpoint_delay: float
    """Silence we waited through before declaring the question finished. This is
    a fixed cost set by ``VadSettings.silence_frames_to_end``, not a measurement."""

    stt_latency: float
    llm_first_token_latency: float | None
    llm_total_latency: float
    transcript_chars: int = 0
    answer_chars: int = 0

    @property
    def time_to_first_text(self) -> float | None:
        """Endpoint detection to first visible token. The number that matters."""
        if self.llm_first_token_latency is None:
            return None
        return self.stt_latency + self.llm_first_token_latency

    @property
    def perceived_latency(self) -> float | None:
        """From the speaker actually stopping to first visible token.

        Includes the endpoint delay, so this is what the user experiences as
        dead air.
        """
        first = self.time_to_first_text
        return None if first is None else self.endpoint_delay + first

    @property
    def time_to_complete(self) -> float:
        return self.endpoint_delay + self.stt_latency + self.llm_total_latency


@dataclass
class MetricsCollector:
    """Aggregates measurements across a probe session."""

    stt_model: str = ""
    llm_model: str = ""
    samples: list[Measurement] = field(default_factory=list)

    def add(self, measurement: Measurement) -> None:
        self.samples.append(measurement)

    # -- cost ---------------------------------------------------------------

    @property
    def audio_minutes(self) -> float:
        return sum(m.speech_duration for m in self.samples) / 60.0

    def estimated_cost_usd(self) -> float:
        stt_rate = _STT_USD_PER_MINUTE.get(self.stt_model, 0.003)
        cost = self.audio_minutes * stt_rate

        in_rate, out_rate = _LLM_USD_PER_MTOK.get(self.llm_model, (0.15, 0.60))
        in_tokens = sum(m.transcript_chars for m in self.samples) / _CHARS_PER_TOKEN
        out_tokens = sum(m.answer_chars for m in self.samples) / _CHARS_PER_TOKEN
        cost += (in_tokens / 1_000_000) * in_rate
        cost += (out_tokens / 1_000_000) * out_rate
        return cost

    def projected_hourly_cost_usd(self, speech_ratio: float = 0.55) -> float:
        """Extrapolate to an hour of meeting.

        Args:
            speech_ratio: Fraction of a real meeting that is actual speech.
                Silence is gated out before transcription, so it is not billed.
        """
        if not self.samples:
            return 0.0
        stt_rate = _STT_USD_PER_MINUTE.get(self.stt_model, 0.003)
        stt_hourly = 60.0 * speech_ratio * stt_rate

        per_question = self.estimated_cost_usd() - (self.audio_minutes * stt_rate)
        per_question = per_question / len(self.samples)
        return stt_hourly + per_question * 15  # ~15 questions/hour

    # -- reporting ----------------------------------------------------------

    def summary(self) -> str:
        if not self.samples:
            return "No utterances captured, so there is nothing to measure.\n"

        def stats(values: list[float | None]) -> str:
            usable = [v for v in values if v is not None]
            if not usable:
                return "n/a"
            median = statistics.median(usable)
            worst = max(usable)
            return f"median {median:5.2f}s   worst {worst:5.2f}s"

        lines = [
            "",
            "=" * 66,
            f"  LATENCY SUMMARY over {len(self.samples)} utterance(s)",
            "=" * 66,
            f"  Endpoint wait (fixed)  {stats([m.endpoint_delay for m in self.samples])}",
            f"  Transcription          {stats([m.stt_latency for m in self.samples])}",
            f"  LLM first token        {stats([m.llm_first_token_latency for m in self.samples])}",
            f"  LLM full answer        {stats([m.llm_total_latency for m in self.samples])}",
            "-" * 66,
            f"  TIME TO FIRST TEXT     {stats([m.perceived_latency for m in self.samples])}",
            f"  Time to full answer    {stats([m.time_to_complete for m in self.samples])}",
            "=" * 66,
        ]

        median_perceived = statistics.median(
            [m.perceived_latency for m in self.samples if m.perceived_latency is not None]
            or [0.0]
        )
        if median_perceived <= 3.0:
            verdict = "Good. Comfortable for live conversation."
        elif median_perceived <= 5.0:
            verdict = "Usable. The pause is noticeable but workable."
        elif median_perceived <= 8.0:
            verdict = "Marginal. Worth switching to streaming STT before Phase 1."
        else:
            verdict = "Too slow. Redesign needed: streaming STT and/or local Whisper."
        lines.append(f"  Verdict: {verdict}")

        lines += [
            "",
            f"  Audio transcribed      {self.audio_minutes * 60:.1f}s",
            f"  Estimated spend        ${self.estimated_cost_usd():.4f}",
            f"  Projected per meeting  ${self.projected_hourly_cost_usd():.2f}/hour",
            "  (cost figures are estimates; check your OpenAI billing page)",
            "",
        ]
        return "\n".join(lines)


def endpoint_delay_seconds(settings: VadSettings) -> float:
    """The fixed dead air spent confirming the speaker stopped."""
    return settings.silence_frames_to_end * VAD_FRAME_MS / 1000.0
