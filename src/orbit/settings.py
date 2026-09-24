"""User settings: one JSON file, no database.

This holds the small amount of state that genuinely should survive a restart:
which audio device to capture, which models to call, VAD tuning, and where the
user left the overlay window. Conversation content is never included, per R8.

The API key is deliberately *not* here. It lives in Windows Credential Manager
so that this file stays safe to read, copy, and paste into a bug report.

Settings are stored in user-friendly units (milliseconds, points, 0-100 opacity)
and converted into the internal representations that :mod:`orbit.config` uses.
That keeps the settings UI readable without leaking frame counts into it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import (
    KEYRING_SERVICE,
    KEYRING_USERNAME,
    VAD_FRAME_MS,
    ModelSettings,
    VadSettings,
)

APP_DIR_NAME = "orbit-ai"
SETTINGS_FILENAME = "settings.json"

DEFAULT_HOTKEYS: dict[str, str] = {
    "toggle_overlay": "ctrl+alt+o",
    "force_answer": "ctrl+alt+a",
    "clear": "ctrl+alt+c",
    "stop_capture": "ctrl+alt+s",
}


def settings_dir() -> Path:
    """Directory for user settings, created on demand."""
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / APP_DIR_NAME


def settings_path() -> Path:
    return settings_dir() / SETTINGS_FILENAME


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Settings:
    """Everything the user can configure."""

    # -- audio ---------------------------------------------------------------
    #: Loopback device index, or None to auto-detect the default output's twin.
    audio_device_index: int | None = None

    # -- models --------------------------------------------------------------
    stt_model: str = "gpt-4o-mini-transcribe"
    llm_model: str = "gpt-4o-mini"
    #: Language hint for transcription. "auto" lets the model detect it.
    language: str = "en"
    max_answer_tokens: int = 700
    context_turns: int = 6
    #: Extra vocabulary to bias transcription toward, comma separated.
    vocabulary_hint: str = ""

    # -- segmentation --------------------------------------------------------
    #: Silence before a question is considered finished. The dominant latency
    #: knob in the whole application.
    silence_ms: int = 700
    speech_threshold_multiplier: float = 3.5
    min_speech_seconds: float = 0.4
    max_utterance_seconds: float = 30.0

    # -- overlay appearance --------------------------------------------------
    #: Window opacity as a percentage. Full transparency looks good in
    #: screenshots and hurts real readability, so the default stays high.
    opacity_percent: int = 94
    font_size: int = 15
    overlay_x: int | None = None
    overlay_y: int | None = None
    overlay_width: int = 430
    overlay_height: int = 640

    # -- behaviour -----------------------------------------------------------
    #: By default, audio captured while an answer was generating is KEPT and
    #: shown as the next turn, so a follow-up asked mid-answer is not lost.
    #: Enable this only to discard a large stale backlog left by a long answer;
    #: it trades away follow-up capture for staying current.
    drop_stale_audio: bool = False
    #: Stop making paid calls once a session passes this spend. 0 disables.
    session_cost_limit_usd: float = 0.0

    # -- one-time state ------------------------------------------------------
    consent_acknowledged: bool = False

    hotkeys: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_HOTKEYS)
    )

    # ------------------------------------------------------------------
    # Derived views onto the internal config types
    # ------------------------------------------------------------------

    def to_vad_settings(self) -> VadSettings:
        return VadSettings(
            speech_threshold_multiplier=self.speech_threshold_multiplier,
            silence_frames_to_end=max(1, int(self.silence_ms) // VAD_FRAME_MS),
            min_utterance_seconds=self.min_speech_seconds,
            max_utterance_seconds=self.max_utterance_seconds,
        )

    def to_model_settings(self) -> ModelSettings:
        return ModelSettings(
            stt_model=self.stt_model,
            llm_model=self.llm_model,
            stt_language=None if self.language.lower() == "auto" else self.language,
            max_answer_tokens=self.max_answer_tokens,
            context_turns=self.context_turns,
        )

    @property
    def opacity(self) -> float:
        """Opacity as a 0.0-1.0 fraction for Qt."""
        return _clamp(self.opacity_percent / 100.0, 0.3, 1.0)

    @property
    def transcription_prompt(self) -> str | None:
        hint = self.vocabulary_hint.strip()
        return hint.replace(",", ", ") if hint else None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> "Settings":
        """Read settings from disk, falling back to defaults on any problem.

        A corrupt or hand-edited settings file must never prevent the
        application from starting, so every failure mode here degrades to
        defaults rather than raising.
        """
        path = settings_path()
        if not path.exists():
            return cls()

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()

        if not isinstance(raw, dict):
            return cls()

        known = {f.name for f in fields(cls)}
        # Drop unknown keys so a settings file from a newer version does not
        # crash an older build.
        filtered = {k: v for k, v in raw.items() if k in known}

        try:
            instance = cls(**filtered)
        except TypeError:
            return cls()

        instance._sanitise()
        return instance

    def save(self) -> None:
        """Write settings to disk atomically.

        Written to a temporary file and moved into place so an interrupted write
        cannot leave a truncated settings file behind.
        """
        self._sanitise()
        directory = settings_dir()
        directory.mkdir(parents=True, exist_ok=True)

        payload = asdict(self)
        # Defensive: nothing resembling a credential belongs in this file.
        payload.pop("api_key", None)

        target = settings_path()
        temp = target.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        temp.replace(target)

    def _sanitise(self) -> None:
        """Clamp values into workable ranges after load or before save."""
        self.silence_ms = int(_clamp(self.silence_ms, 200, 3000))
        self.speech_threshold_multiplier = _clamp(
            self.speech_threshold_multiplier, 1.2, 20.0
        )
        self.min_speech_seconds = _clamp(self.min_speech_seconds, 0.1, 5.0)
        self.max_utterance_seconds = _clamp(self.max_utterance_seconds, 5.0, 120.0)
        self.opacity_percent = int(_clamp(self.opacity_percent, 30, 100))
        self.font_size = int(_clamp(self.font_size, 9, 32))
        self.overlay_width = int(_clamp(self.overlay_width, 300, 2000))
        self.overlay_height = int(_clamp(self.overlay_height, 240, 2000))
        self.max_answer_tokens = int(_clamp(self.max_answer_tokens, 64, 4000))
        self.context_turns = int(_clamp(self.context_turns, 0, 30))
        self.session_cost_limit_usd = max(0.0, float(self.session_cost_limit_usd))

        if not isinstance(self.hotkeys, dict):
            self.hotkeys = dict(DEFAULT_HOTKEYS)
        else:
            merged = dict(DEFAULT_HOTKEYS)
            merged.update(
                {
                    k: v
                    for k, v in self.hotkeys.items()
                    if k in DEFAULT_HOTKEYS and isinstance(v, str) and v.strip()
                }
            )
            self.hotkeys = merged


# ----------------------------------------------------------------------
# Credential helpers
#
# Kept here so the UI has a single import for "where do settings live",
# even though the key itself is stored by the OS rather than in our file.
# ----------------------------------------------------------------------

def save_api_key(key: str) -> None:
    """Store the API key in Windows Credential Manager."""
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.strip())


def delete_api_key() -> None:
    """Remove the stored API key. Safe to call when none is stored."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        pass


def has_api_key() -> bool:
    try:
        import keyring

        return bool(keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME))
    except Exception:
        return False


def mask_key(key: str) -> str:
    """Render a key for display without revealing it."""
    key = key.strip()
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}{'*' * 8}{key[-4:]}"
