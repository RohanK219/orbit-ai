"""List WASAPI loopback devices.

Run this first. If it prints nothing useful, audio capture will not work and
there is no point going further.

    python scripts/list_devices.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (sys.path side effect)

import pyaudiowpatch as pyaudio

from orbit.audio.devices import AudioDeviceError, find_default_loopback, list_loopback_devices


def main() -> int:
    with pyaudio.PyAudio() as pa:
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            print("WASAPI is not available. orbit-ai requires Windows.")
            return 1

        default_index = int(wasapi.get("defaultOutputDevice", -1))
        if default_index >= 0:
            info = pa.get_device_info_by_index(default_index)
            print(f"Default output device: {info['name']}")
        print()

        devices = list_loopback_devices(pa)
        if not devices:
            print("No WASAPI loopback devices found.")
            print("Check that a playback device is enabled in Windows Sound settings.")
            return 1

        print("Loopback input devices:")
        for device in devices:
            print(f"  {device}")
        print()

        try:
            chosen = find_default_loopback(pa)
        except AudioDeviceError as exc:
            print(f"Auto-detection failed: {exc}")
            return 1

        print(f"Auto-detected capture device: {chosen}")
        print()
        print("Play some audio, then run:  python scripts/phase0_probe.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
