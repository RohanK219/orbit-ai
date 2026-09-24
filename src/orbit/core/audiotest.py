"""Short capture-only probe used by the setup window's "Test audio" button.

Answers the single most confusing failure in the product before a meeting rather
than during one: the selected device is wrong, or the meeting audio is routed
somewhere else, and the app hears nothing. Without this the user finds out by
watching an empty overlay while someone waits for an answer.

Deliberately does not touch the network, so it is free to run.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal, Slot

from ..audio.capture import SystemAudioCapture
from ..audio.devices import AudioDeviceError, resolve_device


class AudioTestWorker(QObject):
    """Captures for a few seconds and reports the peak level observed."""

    level_changed = Signal(float)
    #: ``(peak_level, message)``. An empty message means success.
    completed = Signal(float, str)
    finished = Signal()

    def __init__(self, device_index: int | None, duration: float = 3.0) -> None:
        super().__init__()
        self._device_index = device_index
        self._duration = duration

    @Slot()
    def run(self) -> None:
        try:
            peak, message = self._probe()
            self.completed.emit(peak, message)
        except AudioDeviceError as exc:
            self.completed.emit(0.0, str(exc))
        except Exception as exc:
            self.completed.emit(0.0, f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()

    def _probe(self) -> tuple[float, str]:
        import numpy as np
        import pyaudiowpatch as pyaudio

        with pyaudio.PyAudio() as pa:
            device = resolve_device(pa, self._device_index)

            with SystemAudioCapture(pa, device) as capture:
                peak = 0.0
                deadline = time.monotonic() + self._duration

                for chunk in capture.frames(
                    timeout=0.2, idle_timeout=self._duration + 1.0
                ):
                    if chunk.size:
                        level = float(np.abs(chunk).max())
                        peak = max(peak, level)
                        self.level_changed.emit(min(1.0, level**0.5 * 2.2))
                    if time.monotonic() >= deadline:
                        break

                if capture.timed_out_idle or peak <= 1e-6:
                    return peak, (
                        f"No audio detected on {device.name}.\n\n"
                        "Loopback devices report nothing while idle on many "
                        "drivers, so play something audible and test again. If "
                        "it still reads silent, pick a different device."
                    )

                if capture.callback_error:
                    return peak, f"Capture error: {capture.callback_error}"

                return peak, ""
