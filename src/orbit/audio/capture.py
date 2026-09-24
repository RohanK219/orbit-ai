"""Background system-audio capture.

Produces a stream of mono float32 frames at :data:`~orbit.config.TARGET_SAMPLE_RATE`
regardless of what the sound card natively runs at.

Uses PortAudio's **callback mode** rather than a thread doing blocking reads.
That is a correctness requirement, not a style preference. With a blocking
``stream.read()``, an idle WASAPI endpoint delivers no buffers at all, so the
read never returns; shutdown then has to abandon the thread and free the stream
underneath it, which crashes the process with an access violation. In callback
mode PortAudio owns the thread and guarantees the callback is no longer running
once ``stop_stream()`` returns, so teardown is deterministic.

The callback must stay cheap and must never block. Downmix and resample of a
50 ms buffer is a handful of microseconds of numpy, which is fine; anything
slower belongs on the consumer side. If the consumer falls behind we drop the
oldest audio rather than stalling the callback, because stalling it causes
buffer overruns and audible gaps.
"""

from __future__ import annotations

import queue
import time

import numpy as np
import pyaudiowpatch as pyaudio

from ..config import CAPTURE_CHUNK_MS, TARGET_SAMPLE_RATE
from .devices import LoopbackDevice

_INT16_FULL_SCALE = 32768.0


class _Resampler:
    """Streaming resampler with a stateless fallback.

    ``soxr.ResampleStream`` carries filter state across chunks, which avoids the
    clicks you get from resampling each buffer independently. If that class is
    unavailable we fall back to the stateless call, which is still correct but
    slightly less clean at chunk boundaries.
    """

    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._stream = None
        self._soxr = None

        if in_rate == out_rate:
            return

        import soxr

        self._soxr = soxr
        try:
            self._stream = soxr.ResampleStream(
                in_rate, out_rate, 1, dtype="float32", quality="QQ"
            )
        except Exception:
            self._stream = None

    def process(self, mono: np.ndarray) -> np.ndarray:
        if self.in_rate == self.out_rate:
            return mono
        if self._stream is not None:
            return self._stream.resample_chunk(mono)
        return self._soxr.resample(mono, self.in_rate, self.out_rate)


class SystemAudioCapture:
    """Captures system audio from a WASAPI loopback device.

    Use as a context manager::

        with SystemAudioCapture(pa, device) as cap:
            for chunk in cap.frames():
                ...
    """

    def __init__(
        self,
        pa: pyaudio.PyAudio,
        device: LoopbackDevice,
        max_queued_chunks: int = 200,
    ) -> None:
        self._pa = pa
        self.device = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queued_chunks)
        self._stream = None
        self._resampler = _Resampler(device.sample_rate, TARGET_SAMPLE_RATE)
        self._closing = False

        #: Count of chunks discarded because the consumer fell behind. Non-zero
        #: here means the pipeline is too slow and audio is being lost.
        self.dropped_chunks = 0

        #: Set when :meth:`frames` gave up waiting for a device that never
        #: delivered any audio. Callers should treat this as a device error.
        self.timed_out_idle = False

        #: Set if the callback ever raised. Surfaces silent capture failures.
        self.callback_error: str | None = None

        self._frames_per_buffer = max(
            256, int(device.sample_rate * CAPTURE_CHUNK_MS / 1000)
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._closing = False
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.device.channels,
            rate=self.device.sample_rate,
            frames_per_buffer=self._frames_per_buffer,
            input=True,
            input_device_index=self.device.index,
            stream_callback=self._callback,
        )

    def stop(self) -> None:
        """Tear down the stream.

        Order matters: ``stop_stream()`` before ``close()``. PortAudio only
        guarantees the callback has finished once the stream is stopped, and
        closing while it may still run is what causes access violations.
        """
        self._closing = True
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            if stream.is_active():
                stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def __enter__(self) -> "SystemAudioCapture":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    @property
    def is_running(self) -> bool:
        stream = self._stream
        if stream is None:
            return False
        try:
            return bool(stream.is_active())
        except Exception:
            return False

    # -- PortAudio callback ------------------------------------------------

    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ANN001
        """Called on PortAudio's thread. Must be fast and must not raise."""
        if self._closing:
            return (None, pyaudio.paComplete)
        try:
            self._enqueue(in_data)
        except Exception as exc:  # never let an exception cross into C code
            self.callback_error = f"{type(exc).__name__}: {exc}"
        return (None, pyaudio.paContinue)

    def _enqueue(self, raw: bytes) -> None:
        samples = np.frombuffer(raw, dtype=np.int16)
        if samples.size == 0:
            return

        # Downmix to mono. Loopback of stereo speakers gives interleaved frames;
        # averaging preserves speech better than dropping a channel.
        channels = self.device.channels
        if channels > 1:
            usable = (samples.size // channels) * channels
            if usable == 0:
                return
            samples = samples[:usable].reshape(-1, channels).mean(axis=1)

        mono = np.ascontiguousarray(samples, dtype=np.float32) / _INT16_FULL_SCALE
        resampled = self._resampler.process(mono)
        if resampled.size == 0:
            return

        try:
            self._queue.put_nowait(resampled)
        except queue.Full:
            # Drop the oldest chunk to make room. Never block the callback.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(resampled)
            except (queue.Empty, queue.Full):  # pragma: no cover - benign race
                pass
            self.dropped_chunks += 1

    # -- consumer API ------------------------------------------------------

    def frames(self, timeout: float = 0.5, idle_timeout: float | None = 10.0):
        """Yield mono float32 chunks at the target sample rate.

        Args:
            timeout: How long each internal queue wait blocks for.
            idle_timeout: Stop iterating if the device delivers nothing at all
                for this many seconds. ``None`` waits forever.

        The idle timeout matters: some drivers, including the Realtek endpoint
        this was developed against, deliver no buffers whatsoever while nothing
        is playing rather than delivering silence. Without a bound this
        generator would spin without ever yielding, so a caller's own break
        condition would never be evaluated and the app would appear to hang.
        Ending iteration lets the caller report a clear "no audio from device"
        error instead.
        """
        last_data = time.monotonic()
        while not self._closing:
            try:
                chunk = self._queue.get(timeout=timeout)
            except queue.Empty:
                if (
                    idle_timeout is not None
                    and time.monotonic() - last_data > idle_timeout
                ):
                    self.timed_out_idle = True
                    return
                continue
            last_data = time.monotonic()
            yield chunk

    def drain(self) -> int:
        """Discard buffered audio and return how many chunks were dropped.

        Call after finishing an answer. Audio kept arriving while we were waiting
        on the API, and replaying that backlog would mean answering questions the
        conversation has already moved past.
        """
        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
                discarded += 1
            except queue.Empty:
                return discarded

    @property
    def backlog(self) -> int:
        """Chunks currently waiting to be consumed."""
        return self._queue.qsize()
