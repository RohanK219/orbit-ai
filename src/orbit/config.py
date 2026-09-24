"""Configuration and credential loading.

Design notes:
  - There is no database. Nothing about a live meeting is worth persisting, and
    keeping other people's speech off disk keeps the privacy story simple.
  - The only persisted values are the API key (Windows Credential Manager) and
    user settings (a JSON file, added in Phase 1). Transcripts stay in RAM.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

KEYRING_SERVICE = "orbit-ai"
KEYRING_USERNAME = "openai-api-key"


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------

#: Sample rate we feed to speech-to-text. 16 kHz is the standard STT input
#: rate; sending 48 kHz just costs bandwidth without improving accuracy.
TARGET_SAMPLE_RATE = 16_000

#: Frame size used for voice activity decisions. 20 ms is a good tradeoff:
#: short enough to detect speech onset quickly, long enough for stable RMS.
VAD_FRAME_MS = 20

#: How much audio we pull from WASAPI per read. Smaller means lower latency
#: but more Python wakeups. ~50 ms at the device's native rate.
CAPTURE_CHUNK_MS = 50


@dataclass(slots=True)
class VadSettings:
    """Thresholds for energy-based utterance segmentation.

    These are deliberately tunable from the CLI because the right values depend
    on the meeting platform, output device, and system volume. Phase 0 exists
    partly to find good defaults for real calls.
    """

    #: Seconds of audio used to learn the room/device noise floor at startup.
    calibration_seconds: float = 1.0

    #: A frame counts as speech when its RMS exceeds the noise floor by this
    #: factor. Higher = less sensitive to background hiss and keyboard noise.
    speech_threshold_multiplier: float = 3.5

    #: Absolute floor so a perfectly silent device doesn't make every faint
    #: sound look like speech. RMS is normalised to 0.0-1.0.
    min_speech_rms: float = 0.005

    #: Consecutive speech frames required to open an utterance (debounce).
    speech_frames_to_start: int = 5  # 100 ms

    #: Consecutive silence frames required to close it. This is the single
    #: biggest contributor to perceived latency, so it is worth tuning: too
    #: short and we cut questions in half, too long and answers lag.
    silence_frames_to_end: int = 35  # 700 ms

    #: Minimum seconds of *actual speech* (not buffer length, which also holds
    #: pre-roll and a silence tail) for an utterance to be worth transcribing.
    #: Filters out coughs, clicks, and notification chimes before they cost an
    #: API call.
    min_utterance_seconds: float = 0.4

    #: Hard cap. Someone monologuing should still get transcribed in chunks
    #: rather than buffering forever.
    max_utterance_seconds: float = 30.0


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

@dataclass(slots=True)
class ModelSettings:
    """Which OpenAI models to use.

    Cost note: we deliberately use a transcription model plus a *text* chat
    model rather than the speech-to-speech Realtime API. We only ever want text
    on screen, and audio output tokens are dramatically more expensive. Same
    result, small fraction of the price.
    """

    stt_model: str = field(
        default_factory=lambda: os.getenv("ORBIT_STT_MODEL", "gpt-4o-mini-transcribe")
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("ORBIT_LLM_MODEL", "gpt-4o-mini")
    )

    #: Language hint for STT. Improves accuracy and speed when known.
    #: None lets the model auto-detect.
    stt_language: str | None = "en"

    #: Cap on answer length. Answers are meant to be read at a glance, and
    #: long generations directly increase the time you sit waiting.
    max_answer_tokens: int = 700

    #: How many previous exchanges to keep as context, so follow-up questions
    #: like "now optimise that" still make sense. Lives in RAM only.
    context_turns: int = 6


SYSTEM_PROMPT = """You are a live meeting assistant. The user is in a call and \
you receive a transcript of what the other participants said.

Answer so the user can read your response aloud immediately:
- Lead with the direct answer in one or two sentences. No preamble.
- Then add only the supporting detail that matters.
- For code, give a short working solution in a fenced block, then one line on \
the approach and its complexity.
- If the transcript is small talk, an incomplete sentence, or not a question, \
reply with exactly: (no question detected)
- If the transcript is ambiguous, answer the most likely interpretation rather \
than asking for clarification. The user cannot ask you follow-ups mid-call.

Be accurate. If you are unsure, say so briefly rather than inventing detail."""


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def load_api_key() -> str:
    """Return the OpenAI API key, or raise with actionable guidance.

    Resolution order, most secure first:
      1. Windows Credential Manager (via keyring)
      2. OPENAI_API_KEY environment variable
      3. .env file in the project root
    """
    try:
        import keyring

        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if key:
            return key.strip()
    except Exception:
        # keyring missing or no backend available; fall through to env vars.
        pass

    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key.strip()

    try:
        from dotenv import load_dotenv

        load_dotenv()
        key = os.getenv("OPENAI_API_KEY")
        if key:
            return key.strip()
    except ImportError:
        pass

    raise RuntimeError(
        "No OpenAI API key found.\n"
        "  Recommended: python scripts/set_key.py    (Windows Credential Manager)\n"
        "  Or:          copy .env.example to .env and add your key\n"
        "  Or:          $env:OPENAI_API_KEY = 'sk-...'"
    )
