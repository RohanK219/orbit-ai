"""WASAPI loopback device discovery.

Why this module is fiddly enough to deserve its own file:

WASAPI loopback lets you record what is *playing out* of an output device. That
is exactly what we want, because it captures the other meeting participants
while naturally excluding your own microphone.

The catch that trips up most first attempts: you cannot open the speaker device
directly for input. PyAudioWPatch exposes a separate *virtual input device* that
mirrors each output device, usually named "<Speaker Name> [Loopback]", appended
at the end of the device list. So the job here is to find the loopback twin of
whichever device Windows is currently playing through.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyaudiowpatch as pyaudio


class AudioDeviceError(RuntimeError):
    """Raised when no usable loopback device can be found."""


@dataclass(slots=True)
class LoopbackDevice:
    """A resolved WASAPI loopback input device."""

    index: int
    name: str
    sample_rate: int
    channels: int

    def __str__(self) -> str:
        return (
            f"[{self.index}] {self.name} "
            f"({self.sample_rate} Hz, {self.channels}ch)"
        )


def _to_loopback_device(info: dict) -> LoopbackDevice:
    return LoopbackDevice(
        index=int(info["index"]),
        name=str(info["name"]),
        sample_rate=int(info["defaultSampleRate"]),
        channels=int(info["maxInputChannels"]),
    )


def list_loopback_devices(pa: pyaudio.PyAudio) -> list[LoopbackDevice]:
    """Return every WASAPI loopback input device on the system."""
    devices: list[LoopbackDevice] = []
    for info in pa.get_loopback_device_info_generator():
        try:
            devices.append(_to_loopback_device(info))
        except (KeyError, ValueError, TypeError):
            # Skip malformed entries rather than failing the whole enumeration.
            continue
    return devices


def find_default_loopback(pa: pyaudio.PyAudio) -> LoopbackDevice:
    """Resolve the loopback device for the current default output device.

    Raises:
        AudioDeviceError: if WASAPI is unavailable or no loopback twin exists.
    """
    try:
        wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as exc:  # pragma: no cover - platform dependent
        raise AudioDeviceError(
            "WASAPI host API not available. orbit-ai requires Windows."
        ) from exc

    default_output_index = wasapi_info.get("defaultOutputDevice", -1)
    if default_output_index is None or int(default_output_index) < 0:
        raise AudioDeviceError(
            "Windows reports no default audio output device. Check that a "
            "playback device is enabled in Sound settings."
        )

    default_output = pa.get_device_info_by_index(int(default_output_index))

    # Some driver setups already expose the default output as a loopback device.
    if default_output.get("isLoopbackDevice", False):
        return _to_loopback_device(default_output)

    target_name = str(default_output["name"])
    candidates = list_loopback_devices(pa)

    # The loopback twin is normally "<output name> [Loopback]", so a substring
    # match against the output device name identifies it.
    for device in candidates:
        if target_name in device.name:
            return device

    if candidates:
        # Fall back to the first available loopback device. Better to capture
        # something and let the user correct it than to hard fail.
        return candidates[0]

    raise AudioDeviceError(
        f"No WASAPI loopback device found for output device {target_name!r}.\n"
        "This usually means the audio driver does not support loopback. Try a "
        "different output device, or update the audio driver."
    )


def resolve_device(pa: pyaudio.PyAudio, index: int | None = None) -> LoopbackDevice:
    """Return the loopback device to record from.

    Args:
        index: Explicit device index to use. When ``None``, auto-detect the
            loopback twin of the default output device.
    """
    if index is None:
        return find_default_loopback(pa)

    info = pa.get_device_info_by_index(index)
    if int(info.get("maxInputChannels", 0)) < 1:
        raise AudioDeviceError(
            f"Device [{index}] {info.get('name')!r} has no input channels, so it "
            "cannot be recorded from. Loopback devices are input devices; run "
            "`python scripts/list_devices.py` to see valid choices."
        )
    return _to_loopback_device(info)
