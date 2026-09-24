# Implementation Plan — Orbit Live Assistant

Four phases. Phase 0 is a measurement exercise that decides whether the rest is worth building. Phase 1 produces the working application. Phase 2 makes it survivable and shippable. Phase 3 is unscheduled.

Each phase ends with something runnable. No phase leaves a half-built layer waiting on the next one.

---

## Phase 0 — Feasibility spike

**Goal:** measure real latency on this machine and this network before investing in a UI. Command line only, throwaway code, lives in `scripts/`.

**Exit criterion:** measured turn-end-to-first-answer-token under the 5.0 s ceiling in the requirements. If it is over, stop and revisit the design.

**Status: built and locally verified. Blocked on an API key for the latency measurement that is the entire point of this phase.**

- [x] 0.1 Scaffold the project
  - `pyproject.toml`, `requirements.txt`, `.gitignore`, `src/orbit/` package
  - Python 3.12.10 installed user-scoped; venv at `.venv`
  - Resolved versions pinned in `requirements.lock.txt`
  - All dependencies installed as prebuilt wheels, no compiler required

- [x] 0.2 Resolve the loopback device
  - Enumerate WASAPI devices, print the full list with host API and channel info
  - Locate the loopback counterpart of the default output device
  - Confirm the matching logic handles the appended-virtual-device layout
  - **Verified:** auto-detected `[10] Speakers (Realtek(R) Audio) [Loopback] (48000 Hz, 2ch)`
  - _Requirements: R1.1_

- [x] 0.3 Prove capture works
  - Capture system audio, downmix and resample to 16 kHz mono
  - **Verified** by `scripts/selftest.py`: 61 chunks, 3.05 s of audio in 3.05 s wall clock,
    real signal captured off the speakers at peak 0.1477, zero dropped chunks
  - No wav written; capture stays in memory per R7
  - _Requirements: R1.3, R1.4_

- [x] 0.4 Turn segmentation on the command line
  - VAD per frame with the IDLE/SPEAKING state machine, pre-roll, and endpoint detection
  - **Verified** by self-test: onset, endpoint, short-blip rejection, and length capping
  - _Requirements: R3.1, R3.2, R3.3_

- [x] 0.5 Cloud transcription — written, not yet exercised
  - **Deviation from the original design, deliberate.** Implemented as one batch
    request per completed utterance rather than a persistent realtime WebSocket
    session. Batch is far simpler, and whether the streaming session is needed is
    exactly what this phase measures. If batch lands inside the latency ceiling,
    the WebSocket complexity buys nothing. Swapping it later touches one class
    (`stt/openai_stt.py`) because everything goes through the `Transcriber`
    protocol.
  - Silence gating confirmed: VAD decides what is sent, so idle audio costs nothing
  - _Requirements: R2.2, R2.5, R3.4_

- [x] 0.6 Streaming answer on the command line
  - Streaming chat completion with rolling in-memory context, tokens to stdout
  - Uses `max_completion_tokens`, and drops per-model unsupported parameters on
    retry, since the model is a CLI flag and o-series models reject `temperature`
  - **Verified** offline: context bookkeeping, bounded eviction, non-question filtering
  - _Requirements: R4.1, R4.2_

- [ ] 0.7 Latency report and go/no-go — **BLOCKED: needs an API key**
  - Run `scripts/set_key.py`, then `scripts/phase0_probe.py` against real questions
  - Instrumentation is in place; `metrics.py` reports median and worst per stage
    plus a verdict against the target table
  - Record the numbers here and make an explicit continue-or-redesign call

- [x] 0.8 Local backend implementation and benchmark harness
  - `stt/local_whisper.py` implements the existing protocol with lazy optional
    dependency loading and actionable model errors
  - Automated tests cover prompt forwarding and model failure behavior
  - A real-time GPU benchmark remains an environment-specific validation step
  - _Requirements: R2.3_

### Phase 0 findings

Three real defects surfaced during verification. None were visible by reading the
code, which is the argument for this phase existing.

1. **Access violation on shutdown (`0xC0000005`).** A blocking `stream.read()`
   never returns when the audio endpoint is idle, so teardown could not join the
   reader thread and freed the PortAudio stream underneath it. Fixed by moving
   capture to PortAudio callback mode and deleting the hand-rolled thread. Would
   have crashed the app every time audio went quiet.
2. **Infinite hang on idle audio.** The Realtek loopback endpoint delivers no
   buffers at all while idle, not silence as assumed. The frame generator spun
   without yielding, so the app would sit in "calibrating" forever with no
   diagnostic. Fixed with a bounded idle timeout that reports a device error.
3. **Notification chimes were billable.** The minimum-utterance guard measured
   total buffer length, which includes 200 ms of pre-roll plus a silence tail, so
   a 0.15 s Teams ping cleared a 0.4 s threshold. Now counts actual
   above-threshold speech frames. Would have fired constantly in a real meeting.

Two platform constraints worth carrying into Phase 1: capture must use callback
mode, and any audio read loop needs an idle bound.

---

## Phase 1 — Working application

**Goal:** the app you actually run during a meeting. Real code in `src/orbit/`, replacing the spike.

**Exit criterion:** used successfully in a live call, start to finish, without touching a terminal.

**Status: built. Verified headlessly via `scripts/selftest_ui.py` (all checks pass).
Not yet exercised in a real meeting, because that needs an API key.**

- [x] 1.1 Configuration and secrets
  - `settings.py`: dataclass with JSON persistence under `%APPDATA%\orbit-ai\`
  - Atomic writes via temp file and replace, so an interrupted save cannot
    truncate the settings file
  - API key via `keyring` into Windows Credential Manager, never into the JSON
  - Corrupt, missing, and forward-version settings files all fall back cleanly
  - Values clamped on load and save rather than trusted
  - Key redaction in `_short_error` for anything heading to the UI or a log
  - _Requirements: R6.1, R6.2, R6.5, R6.6_

- [x] 1.2 Audio subsystem — completed in Phase 0
  - `audio/devices.py`, `audio/capture.py`, `audio/vad.py`
  - Callback-mode capture, bounded queue with oldest-frame eviction, idle timeout
  - _Requirements: R1.1, R1.2, R1.5, R1.6, R3.1, R3.2, R3.3, R3.6_

- [x] 1.3 Transcription behind one interface
  - `stt/base.py` protocol, `stt/openai_stt.py` implementation
  - **Deviation:** batch per utterance, not `openai_realtime.py`. Carried over
    from Phase 0 and unchanged, since the measurement that would justify the
    streaming session has not been run yet.
  - Backend is selected per session rather than hot-swapped mid-session
  - _Requirements: R2.1, R2.2, R2.4, R2.6_

- [x] 1.4 Answer layer
  - **Deviation:** one module, `llm/openai_llm.py`, rather than three. Streaming,
    rolling context deque, and prompt all fit comfortably in one file; splitting
    them would be structure without benefit at this size.
  - Uses `max_completion_tokens` and drops per-model unsupported parameters on
    retry, so switching to an o-series model does not fail with an opaque 400
  - Cancelling an in-flight answer is now implemented: `AnswerGeneration.cancel()`
    closes the underlying stream immediately. The worker checks for a stop or a
    forced new turn on every token and cancels rather than letting the stream
    keep running unread; a cancelled answer is left off the record (no context
    commit, no metrics) instead of the backlog-discard workaround this used to
    rely on.
  - _Requirements: R4.1, R4.2, R4.3, R4.4_

- [x] 1.5 Pipeline orchestration
  - **Deviation from design: no `qasync`, no asyncio.** The transcription and
    answer layers are synchronous, and a worker thread is the correct home for
    blocking calls. Adopting an event loop would have meant rewriting verified
    code for no gain, since there is one audio stream and one request at a time.
  - `core/worker.py` runs the pipeline; `core/session.py` owns the thread
  - State machine: idle, calibrating, listening, transcribing, thinking,
    answering, error
  - Worker exceptions convert to an error state and leave the UI alive
  - Stop is checked inside the streaming loop so it responds mid-answer
  - _Requirements: R9.4, R9.5_

- [x] 1.6 Overlay window
  - Frameless, always-on-top, `Qt.Tool` so it stays out of the taskbar and
    alt-tab list
  - Draggable by header, resizable via size grip, geometry persisted
  - Configurable opacity and font size
  - _Requirements: R5.1, R5.2, R5.6_

- [x] 1.7 Answer view
  - Timer-coalesced rendering at ~11 Hz rather than per token, so Pygments does
    not re-run on every delta
  - Unterminated code fences render as code, which is the normal state while a
    code answer streams
  - Auto-scroll yields the moment the user scrolls up, resumes at the bottom
  - Copy button for the last code block
  - _Requirements: R5.5, R5.7_

- [x] 1.8 Status and question display
  - Status indicator with a pulse while work is in progress, so a slow answer
    cannot be mistaken for a frozen app
  - **Added beyond spec:** live audio level meter. A wrong device and a silent
    room are otherwise indistinguishable, which is the most confusing failure
    in the product.
  - Transcribed question shown separately from the answer
  - _Requirements: R5.3, R5.4, R2.5, R11.3_

- [x] 1.9 Settings
  - **Deviation:** settings live in the setup window rather than a separate
    dialog. That was the point of the two-window design: configuration up front,
    so the overlay stays at four controls.
  - Device picker with refresh, models, language, vocabulary hints, silence
    threshold, opacity, font size, spend limit
  - Test audio button proves capture works before a meeting depends on it
  - _Requirements: R6.3, R6.4, R2.4, R3.6, R8.2_

- [x] 1.10 Global hotkeys
  - Win32 `RegisterHotKey` via ctypes, dispatched through `nativeEvent`
  - Chosen over `pynput` to avoid both an extra dependency and a low-level
    keyboard hook, which antivirus software flags as keylogging
  - `MOD_NOREPEAT` prevents a held key queueing a burst of API calls
  - Conflicts reported, and failure is never fatal since every action also has
    a button
  - _Requirements: R3.5, R4.6, R11.2_

- [x] 1.11 First-run notice
  - One-time disclosure covering third-party transmission and jurisdictional
    consent, acknowledged into settings
  - _Requirements: R11.1_

- [ ] 1.12 End-to-end validation — **BLOCKED: needs an API key**
  - Headless verification passes: settings, hotkey parsing, markdown
    segmentation, answer rendering, window construction, signal paths
  - Still unverified: a real meeting, real latency, and streaming smoothness
    under actual token rates
  - _Requirements: R7.1, R7.2, R7.3, R7.4, R9.5_

### Phase 1 notes

The cost-control requirement (R11.3, session spend estimate) is implemented in
the worker and shown in the overlay footer, ahead of its Phase 2 slot, because
the metering already existed from Phase 0 and surfacing it was nearly free.

`PySide6-Essentials` is used rather than full `PySide6`. It carries QtCore,
QtGui and QtWidgets and omits WebEngine, 3D and Charts, which cuts roughly two
thirds of the wheel size. That difference carries straight into the packaged
executable, which matters given the app is meant to be portable across machines.

---

## Phase 2 — Hardening and distribution

**Goal:** it recovers from problems on its own and installs like a normal program.

- [x] 2.1 Resilience
  - Bounded exponential retry/backoff for transcription and answer requests
  - Pre-token stream reconnect, cancellation, and offline-specific errors
  - Capture callback/device errors are surfaced without terminating the UI
  - Local backend selection provides the offline fallback
  - _Requirements: R9.1, R9.2, R9.3, R1.5_

- [x] 2.2 Cost metering
  - Session spend estimate in the overlay, rates sourced from config
  - Audio minutes and token counters
  - User-defined cap with warning and pause
  - _Requirements: R8.1, R8.3, R8.4, R8.5_

- [x] 2.3 Local transcription backend
  - `stt/local_whisper.py` against the existing protocol
  - Model size selection and graceful dependency/model errors
  - _Requirements: R2.1, R2.3_

- [x] 2.4 Test suite
  - Unit tests: VAD state machine, context trimming, cost arithmetic, config fallbacks
  - `FakeTranscriber` and `FakeAnswerClient` driving pipeline state and cancellation tests
  - No network or audio hardware required to run the suite
  - Content-free logging assertions
  - _Requirements: R7.4_

- [~] 2.5 Packaging — spec, script, lightweight validation, and guide written; target build verification remains
  - `packaging/orbit-ai.spec`: one-file, windowed (no console), collects the
    PyAudioWPatch native PortAudio DLL and forces in keyring's Windows backend
    submodules, which PyInstaller does not pick up from imports alone. Excludes
    unused heavy Qt modules. UPX disabled because it trips antivirus.
  - `packaging/entry.py`: a top-level frozen entry point rather than freezing a
    package `__main__`, which resolves more reliably once frozen.
  - `scripts/build_exe.ps1`: single documented build command.
  - `scripts/validate_packaging.py`: fast preflight check for package markers,
    optional dependency groups, and spec exclusions. Phase 3 modules are collected
    while optional OCR/local-STT/macOS backends remain excluded from the Windows exe.
  - **Environment note:** PyInstaller analysis is slow on this machine because
    corporate antivirus scans every file access during the build. A trivial
    hello-world onefile built successfully (exit 0), confirming the toolchain
    works; the real build is just slow, not broken. Build here, run elsewhere.
  - Not yet verifiable here: cold-start time and no-Python launch, which are
    properties of the *target* machine.
  - _Requirements: R10.1, R10.2, R10.3, R10.4_

- [x] 2.6 Documentation
  - `DISTRIBUTION.md`: build, move the single exe, first-run SmartScreen/AV
    note, one-time setup, hotkeys, what to validate on the run machine, and
    troubleshooting. README already covers setup, hotkeys, cost, and non-goals.

---

## Phase 3 — Implemented (opt-in)

Additive against the Phase 1 interfaces; all features are opt-in and preserve the
default zero-storage behavior.

- [x] Multi-language question translation
- [x] Approximate participant labels for mono loopback turns
- [x] Optional OCR module for screenshots and code images
- [x] Local domain knowledge retrieval and prompt injection
- [x] Explicit Markdown/JSON session export
- [x] macOS CoreAudio loopback adapter for BlackHole-style devices

---

## Sequencing notes

Phase 0 is genuinely disposable. Do not try to write it well; write it to produce numbers. The interfaces in the design document only start to matter in Phase 1.

The riskiest task in the whole plan is 1.5, the concurrency integration. If `qasync` fights back, take the documented fallback in the design rather than spending days on it: move asyncio to its own thread and keep the identical signal interface.

Task 0.2 is the most common place a project like this stalls silently, because opening the wrong device yields clean silence rather than an error. Confirm a non-zero RMS level in 0.3 before moving on.
