"""macOS CoreAudio loopback capture through an installed virtual device."""

from __future__ import annotations

import queue
import time
from contextlib import suppress

import numpy as np

from ..config import TARGET_SAMPLE_RATE


class MacSystemAudioCapture:
    """Capture a BlackHole/Loopback-style input using sounddevice."""

    def __init__(self, device: int | str | None = None) -> None:
        self.device = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=100)
        self._stream = None
        self.timed_out_idle = False
        self.dropped_chunks = 0

    def __enter__(self) -> "MacSystemAudioCapture":
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "macOS capture requires sounddevice and a CoreAudio loopback "
                "device such as BlackHole."
            ) from exc

        def callback(indata, frames, _time, status):
            if status:
                return
            data = np.asarray(indata, dtype=np.float32)
            mono = data.mean(axis=1) if data.ndim == 2 else data
            try:
                self._queue.put_nowait(mono.copy())
            except queue.Full:
                with suppress(queue.Empty):
                    self._queue.get_nowait()
                self.dropped_chunks += 1
                self._queue.put_nowait(mono.copy())

        self._stream = sd.InputStream(
            samplerate=TARGET_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=callback,
            blocksize=800,
        )
        self._stream.start()
        return self

    def __exit__(self, *_args) -> None:
        self.stop()

    def frames(self, timeout: float = 0.2, idle_timeout: float = 25.0):
        idle_since = time.monotonic()
        while self._stream is not None:
            try:
                yield self._queue.get(timeout=timeout)
                idle_since = time.monotonic()
            except queue.Empty:
                if time.monotonic() - idle_since >= idle_timeout:
                    self.timed_out_idle = True
                    return

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    @property
    def backlog(self) -> int:
        return self._queue.qsize()

    def drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
