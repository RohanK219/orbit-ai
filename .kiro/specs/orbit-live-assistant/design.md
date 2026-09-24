# Design — Orbit Live Assistant

## Technology decisions

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Fast to build; workload is I/O-bound on network, so interpreter speed is not the bottleneck |
| Audio capture | `PyAudioWPatch` (WASAPI loopback) | Only maintained Python path to loopback capture on Windows; free, OS-level API |
| Voice activity detection | `webrtcvad`, Silero as fallback | Cheap enough to run per-frame in the capture thread; gates cloud spend |
| Transcription (cloud) | OpenAI realtime **transcription-only** session over WebSocket | Sub-second partial deltas; avoids audio-output token pricing of speech-to-speech |
| Transcription (local) | `faster-whisper` | Removes per-minute cost and network latency when a GPU is available |
| Answers | OpenAI chat completions, streaming | Token streaming lets text appear while generating |
| UI | PySide6 (Qt 6) | Frameless + translucent + always-on-top is first-class and reliable on Windows; web-based Python wrappers are inconsistent here. LGPLv3, free to distribute |
| Async bridge | `qasync` | Runs asyncio on the Qt event loop so WebSocket work never blocks the UI |
| Code rendering | Pygments to HTML into `QTextBrowser` | Avoids embedding a browser engine |
| Secrets | `keyring` (Windows Credential Manager) | Keeps the API key out of files entirely |
| Packaging | PyInstaller, one-file, windowed | Single `.exe`, no console, no Python prerequisite |

## Architecture

```
┌──────────────────────── Windows ────────────────────────┐
│  Meeting client audio  ──▶  Default output device       │
└────────────────────────────┬────────────────────────────┘
                             │ WASAPI loopback
                             ▼
              ┌──────────────────────────────┐
              │  audio.capture (own thread)  │   blocking reads
              │  → 16 kHz mono PCM frames    │
              │  → bounded ring buffer       │
              └──────────────┬───────────────┘
                             │ per-frame
                             ▼
              ┌──────────────────────────────┐
              │  audio.vad                   │   speech? silence run?
              │  → emits TurnStart/TurnEnd   │
              └──────────────┬───────────────┘
                             │ Qt signal (thread-safe, queued)
                             ▼
              ┌──────────────────────────────┐
              │  core.pipeline  (asyncio on  │
              │  the Qt loop via qasync)     │
              └───┬──────────────────────┬───┘
                  │                      │
                  ▼                      ▼
      ┌────────────────────┐   ┌────────────────────┐
      │ stt.Transcriber    │   │ ai.AnswerClient    │
      │  ├ openai_realtime │   │  streaming chat    │
      │  └ local_whisper   │   │  + ai.context      │
      └─────────┬──────────┘   └─────────┬──────────┘
                │ transcript deltas      │ answer deltas
                └───────────┬────────────┘
                            ▼
              ┌──────────────────────────────┐
              │  ui.overlay (Qt main thread) │
              │  status · question · answer  │
              └──────────────────────────────┘
```

## Concurrency model

This is the highest-risk part of the build. Three subsystems each want to own control flow.

**Resolution**

- **Qt owns the main thread.** `QApplication` runs the event loop. All widget mutation happens here.
- **asyncio runs on that same loop** via `qasync.QEventLoop`. WebSocket sessions and HTTP streaming are coroutines, so they interleave with UI repaints without a second thread.
- **Audio capture gets one dedicated thread.** PyAudio reads block, so they cannot share the event loop. The thread does capture plus VAD, both cheap, and hands results off.
- **Handoff across the boundary** uses Qt signals. Cross-thread emission is queued and thread-safe by design, which makes it the marshalling mechanism rather than manual locking. Where a coroutine must be started from the audio thread, `asyncio.run_coroutine_threadsafe` is used against the stored loop reference.

**Invariants**

- No `await` on the audio thread; no blocking call on the main thread.
- The ring buffer is the only shared mutable state, guarded by a lock and bounded so a slow consumer degrades by dropping old audio rather than exhausting memory.
- Every in-flight model request is cancellable, tracked as a single `asyncio.Task` that a new turn cancels (R4.5).

**Documented fallback.** If `qasync` proves unstable, move the asyncio loop to its own thread and keep the identical signal-based interface to the UI. Only `core/pipeline.py` changes; nothing else is coupled to the choice.

## Modules

```
src/orbit/
├─ __main__.py             entry point, wires loop + window
├─ config.py               settings dataclass, JSON load/save, keyring access
├─ audio/
│  ├─ devices.py           enumerate + match loopback twin of default output
│  ├─ capture.py           capture thread, resample/downmix, ring buffer
│  └─ vad.py               frame-level speech detection, turn state machine
├─ stt/
│  ├─ base.py              Transcriber protocol
│  ├─ openai_realtime.py   transcription-only WebSocket session
│  └─ local_whisper.py     faster-whisper backend
├─ ai/
│  ├─ client.py            streaming chat completions, cancellable
│  ├─ context.py           rolling deque of turns
│  └─ prompts.py           system prompt + context assembly
├─ core/
│  ├─ pipeline.py          orchestration, state machine, task lifecycle
│  ├─ events.py            signal definitions and event dataclasses
│  └─ hotkeys.py           global hotkey registration
├─ ui/
│  ├─ overlay.py           frameless always-on-top main window
│  ├─ answer_view.py       streaming markdown + Pygments code blocks
│  ├─ settings_dialog.py   device, backend, model, thresholds, key, opacity
│  ├─ status_pill.py       state indicator + spend meter
│  └─ theme.py             QSS
└─ util/
   ├─ logging.py           content-free structured logging
   └─ metrics.py           latency stopwatch, token/cost accounting
```

## Key designs

### Loopback device resolution

Loopback devices appear as *duplicate virtual input devices appended to the end of the device list*, not as the output device itself. Opening the speaker directly yields silence, which is the most common failure in first implementations.

Resolution order: look up the default output device, find its loopback counterpart among WASAPI devices, fall back to the first available loopback device, and finally surface a clear error if none exists. The chosen device is shown in settings so the user can override.

### Turn state machine (`audio/vad.py`)

```
IDLE ──speech frame──▶ SPEAKING ──silence ≥ 800ms──▶ evaluate
                          │
                          └──duration ≥ 30s──▶ force commit ──▶ SPEAKING
evaluate: duration < 1.2s ─▶ discard, back to IDLE
          otherwise       ─▶ emit TurnEnd, back to IDLE
```

The manual ask-now hotkey injects a forced `TurnEnd` from any state.

### Transcriber interface (`stt/base.py`)

```python
class Transcriber(Protocol):
    async def start(self) -> None: ...
    async def feed(self, pcm: bytes) -> None: ...
    async def commit(self) -> None: ...          # force end-of-turn
    async def stop(self) -> None: ...
    # yields (text, is_final) as transcription progresses
    def deltas(self) -> AsyncIterator[tuple[str, bool]]: ...
```

Both backends satisfy this, so `pipeline.py` never branches on which is active. This is what keeps the local/cloud decision reversible and lets Phase 0 benchmark both against the same harness.

### Prompt design (`ai/prompts.py`)

The system prompt targets the read-aloud constraint directly: answer first in one or two sentences, then detail only if warranted; no preamble; code in fenced blocks with a language tag; if the question is ambiguous, answer the most likely reading rather than asking for clarification, since the model gets no second turn.

Context is assembled as the last N turns of question/answer pairs from `ai/context.py`, trimmed by token budget rather than turn count alone so a long code paste cannot crowd out the current question.

### Cost accounting (`util/metrics.py`)

Audio seconds transmitted and tokens in/out are tracked per session against a configured price table. Rates live in config rather than code, because provider pricing moves and hardcoding it guarantees the meter drifts wrong. The meter is an estimate and labelled as such in the UI.

### Error handling

| Failure | Behaviour |
|---|---|
| No API key | Block capture, open key field (R6.3) |
| Key rejected | Auth error state, reopen key field |
| WebSocket drop | Backoff reconnect, `reconnecting` status, retain buffer |
| Rate limited | Backoff retry, show status, do not queue unboundedly |
| No network | Error state, offer local backend switch if configured |
| Audio device lost | Error state, re-enumerate, offer re-select |
| Model request error | Show in overlay, keep session alive |
| Unhandled exception in worker | Logged, converted to error state, UI survives (R9.4) |

## Privacy implementation

Zero persistence is enforced structurally, not by convention: no module in `audio/`, `stt/`, or `ai/` imports file-writing APIs, audio lives only in the bounded in-memory buffer, and `util/logging.py` accepts only structured fields (state, duration, error type) with no free-text content parameter. Debug content logging, if built at all, is a separately gated build-time flag rather than a runtime setting that could be left on.

## Testing strategy

- **Unit, no I/O:** VAD turn state machine against synthetic frame sequences; context trimming and token budgeting; cost arithmetic; settings load with missing and malformed files.
- **Fakes:** a `FakeTranscriber` and `FakeAnswerClient` satisfying the protocols, so `pipeline.py` state transitions and cancellation are testable without network or audio hardware.
- **Manual, hardware-dependent:** loopback device resolution, real latency measurement, overlay stacking above full-screen meeting clients.

Latency measurement is instrumented in code from Phase 0 onward rather than bolted on, since the performance targets in the requirements are the project's main risk.

## Deferred

Multi-language transcription and translation, speaker diarization, screen/OCR input for questions shared as images, session export, macOS support, and a plugin surface for domain-specific knowledge. Each is additive against the interfaces above and none requires reworking the pipeline.
