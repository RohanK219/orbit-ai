"""Platform-neutral system-audio capture selection.

Windows uses the existing PyAudioWPatch WASAPI implementation. macOS requires
an installed CoreAudio loopback device such as BlackHole; the optional
sounddevice backend reads that selected virtual device.
"""

from __future__ import annotations

import platform


def ensure_supported_platform() -> None:
    if platform.system() not in {"Windows", "Darwin"}:
        raise RuntimeError("orbit-ai supports Windows and macOS only.")


def mac_audio_device_hint() -> str:
    return (
        "On macOS, install a CoreAudio loopback device such as BlackHole and "
        "select it as the input source before starting."
    )
