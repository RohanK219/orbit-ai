# orbit-ai — Project Plan

Live meeting assistant for Windows. Captures system audio during an online meeting,
transcribes what other participants say, sends it to the OpenAI API, and displays the
answer on an always-on-top overlay.

Status: **planning**. Nothing implemented yet.

---

## 1. What it does

You run orbit-ai alongside a meeting (Google Meet, Zoom, Teams). It listens to
**system audio only** — the audio coming out of your speakers — so it hears the other
participants and not your own microphone. That audio is transcribed live, the question is
sent to the OpenAI API, and the answer streams onto a small overlay panel you can glance at.

Runs continuously for the duration of the call. No manual trigger needed per question.

Capturing loopback rather than the mic is deliberate: it filters your own voice out for
free, so only the other side gets processed.

## 2. Scope boundary

This is built as a general live meeting assistant. It will **not** include an evasion
layer: no hiding the window from screen share, no masking the process, no defeating
proctoring software. Those features exist only to deceive and carry real blowback risk.

The engine is the same either way — the useful applications are sales calls, customer
support, client technical calls, live note-taking, and accessibility captioning.

## 3. Decisions

| Area | Decision | Status |
|---|---|---|
| Platform | Windows desktop | Decided |
| Backend | Python | Decided |
| Audio capture | WASAPI loopback via PyAudioWPatch | Decided |
| Transcription | Audio → text only. No speech-to-speech, no TTS | Decided |
| LLM | OpenAI API (`gpt-4o-mini` default) | Decided |
| Database | **None.** Nothing persisted to disk | Decided |
| Frontend | PySide6 (Qt) | Proposed, awaiting confirmation |
| Local transcription fallback | `faster-whisper`, GPU permitting | Open — depends on hardware |

### Why no database

No accounts, no history, no cross-session state. The transcript is worthless once the
answer has been read. Zero storage also lowers legal exposure, since other people's
speech never touches disk.

Three things still need to survive a restart, none of which is a database:

- **API key** → Windows Credential Manager via `keyring`. Not a plaintext file, not a
  committed `.env`.
- **Settings** (audio device, hotkeys, model, overlay position and opacity) → single JSON
  file in `%APPDATA%\orbit-ai\`.
- **Rolling conversation context** → in-memory `deque` holding the last few minutes so
  follow-ups like "now optimize that" still make sense. Discarded on exit.

### Why PySide6

The hardest UI requirement is a frameless, translucent, always-on-top overlay. Qt supports
that combination natively and reliably on Windows. The web-based Python wrappers
(pywebview, Flet) have inconsistent support for exactly that combination.

It is also pure Python, so one process and one language, with no IPC layer between UI and
backend. Code blocks render by piping Pygments HTML into a `QTextBrowser`. PyInstaller
packaging to a single `.exe` is well-trodden.

Runner-up is **Flet** (Flutter under the hood, pure Python) if the Qt result feels too
utilitarian.

### Note on the Python choice

Python is not fast at execution; it is fast to build in. That still makes it correct here,
because the bottleneck is network round-trips to OpenAI, not CPU. The process spends most
of its time waiting either way.

## 4. Architecture

```
Windows system audio
  → WASAPI loopback capture, 16kHz mono PCM        (dedicated thread)
  → ring buffer + voice activity detection          (has the question ended?)
  → OpenAI realtime transcription over WebSocket    (streaming partial deltas)
  → committed question + rolling context
  → gpt-4o-mini streaming completion
  → PySide6 overlay renders tokens as they arrive
```

Transcription is behind a swappable interface with two implementations, **API** and
**local**, selected by a setting. This avoids lock-in and lets both be measured in Phase 0.

### Implementation notes

- WASAPI loopback devices appear as duplicate virtual **input** devices at the end of the
  device list. Enumerate and match the loopback twin of the default output device rather
  than opening the speaker directly. This trips up most first attempts.
  See [PyAudioWPatch](https://pypi.org/project/PyAudioWPatch/).
- OpenAI provides a dedicated realtime transcription session type over WebSocket, built
  for server-side audio pipelines, streaming partial deltas at sub-second latency instead
  of waiting for a complete file. See the
  [realtime transcription guide](https://developers.openai.com/api/docs/guides/realtime-transcription/)
  and the [Whisper migration notes](https://developers.openai.com/cookbook/examples/migrating_from_whisper_to_gpt_transcribe).
- [WhisperScribe-Pro](https://github.com/JunzheLin/WhisperScribe-Pro) is a working
  reference doing loopback plus live transcription, so the architecture is proven rather
  than theoretical.

### UI layout

Roughly 420×650, docked to one side, always on top:

- Thin top strip: status dot (idle / listening / thinking), audio device, gear, close
- Dim two-line band showing the transcribed question, so you can confirm it heard correctly
- Main panel: the answer streaming in token by token, large readable type, highlighted code
- Global hotkeys for show/hide, ask-now, and clear

## 5. Risks

### Latency is the thing that decides whether this works

| Stage | Realistic |
|---|---|
| Audio buffer + detecting the question ended | 0.5–1.5s |
| Streaming transcription | 0.3–1s |
| LLM first token | 0.5–2s |
| Full answer for a code problem | 3–15s |

Expect **4 to 8 seconds** before anything useful appears, longer for code. In live
conversation that pause is noticeable. Mitigations: stream tokens so text appears as it
generates, show a short answer first with detail following, keep a rolling transcript so
earlier context can be pulled in.

Phase 0 exists to measure this on real hardware before any UI is built.

### Concurrency is the real engineering difficulty

Three things each want to own control flow: Qt needs the main thread, the WebSocket needs
an asyncio loop, and audio capture needs a blocking read thread. Getting this wrong
produces a frozen UI or dropped audio. `qasync` bridges Qt and asyncio cleanly. The UI is
simple; the concurrency is not.

### Consent

The app captures and transmits other people's speech to a third-party API. In GDPR
countries and US two-party-consent states that carries legal weight. The zero-storage
design mitigates it substantially. A disclosure setting is worth adding.

## 6. Cost

Everything except the OpenAI API is free.

| Component | Cost |
|---|---|
| WASAPI loopback (Windows OS API) | Free — built into the OS, no key or account |
| PyAudioWPatch | Free, open source |
| PySide6 | Free under LGPLv3 |
| Python, VAD, Pygments, keyring | Free |
| **OpenAI API** | **The only line item** |

PySide6 ships under LGPLv3 alongside a commercial option that is not needed here. LGPL
permits closed-source distribution and charging money.
See [PySide6 on PyPI](https://pypi.org/project/PySide6) and
[Qt's licensing docs](https://doc.qt.io/qtforpython-6/commercial/index.html).

### Estimated OpenAI spend

One hour of meeting using `gpt-4o-mini-transcribe` plus `gpt-4o-mini` for answers:

- Transcription at roughly $0.003/min, gated by voice detection so silence is not
  billed → **$0.10 to $0.18 per hour**
- Answers, 15 questions at ~500 tokens in and ~600 out → **under one cent**

So **~$0.20 per meeting hour**, almost entirely transcription. Twenty hours a month is
about **$4**.

These are estimates from third-party pricing trackers. Verify against
[OpenAI's pricing page](https://platform.openai.com/pricing) with the actual account,
since rates move and vary by region.

### Cost trap to avoid

Two ways to use OpenAI's realtime stack differ by roughly 10–50× in price.

- **Wrong:** full speech-to-speech Realtime API, streaming audio in and audio out.
  [Measured pricing puts the top realtime model near $32 in / $64 out per million audio
  tokens](https://hackernoon.com/openai-realtime-api-pricing-in-2026-real-world-data-from-4000-measured-sessions).
  Audio tokens accumulate fast on a continuous hour-long stream.
- **Right:** transcription-only session for text, then a separate cheap text completion.
  No audio output is ever requested, because the output is text on screen.

Easy to get wrong when following realtime API tutorials, and it only shows up on the bill.

### The near-free path

Running transcription locally with `faster-whisper` drops the bill to LLM calls alone,
roughly a cent per hour, and removes a network round-trip from the latency budget.

Requires a GPU. A `small` or `medium` Whisper model on a modern NVIDIA card keeps up with
real time; CPU-only will not.

## 7. Phases

### Phase 0 — Prove the pipeline (~1 day)

Command-line script, no UI. Capture loopback, transcribe, print with timestamps.

The only goal is measuring real latency on the target machine and network. If it lands
near 4 seconds the app is viable; if it lands at 12, redesign before investing in a UI.
Also the point at which local vs API transcription gets measured head to head.

### Phase 1 — Working application

The full end-to-end app:

- Always-on-top overlay with streaming answers
- Audio device picker
- Global hotkeys
- Secure API key storage
- Settings persistence
- Packaged single-file `.exe`

### Phase 2+

Larger scale ideas, once there is something running to build on.

## 8. Open questions

1. Confirm **PySide6**, or evaluate Flet first.
2. **GPU model** — decides whether local `faster-whisper` is viable, which is the
   difference between paying per hour indefinitely and paying almost nothing.
   Run: `Get-CimInstance Win32_VideoController | Select-Object Name`
3. Rough **budget ceiling** for API spend, which decides `gpt-4o-mini` vs `gpt-4o`
   as the default.
