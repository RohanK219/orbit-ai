# orbit-ai

A live meeting assistant for Windows. It listens to your **system audio** (what the
other participants say, coming out of your speakers), transcribes it, asks an LLM,
and puts the answer on your screen while the call is still going.

It never touches your microphone, so your own voice is not captured or processed.

## How it works

```
Windows system audio (WASAPI loopback)
  -> mono 16 kHz float32
  -> utterance segmentation (detects when the speaker stopped)
  -> transcription        (gpt-4o-mini-transcribe)
  -> streaming answer     (gpt-4o-mini)
  -> your screen
```

Two design decisions worth knowing up front:

**No database.** Nothing about a live meeting is worth keeping thirty seconds after
you have read the answer. Transcripts live in RAM and die with the process. Only the
API key (Windows Credential Manager) and your settings persist.

**Text, never audio out.** We use a transcription model plus a text chat model rather
than the speech-to-speech Realtime API. We only want text on screen, and audio output
tokens cost dramatically more for an identical result.

## Requirements

- Windows (WASAPI loopback is a Windows API; there is no cross-platform path)
- Python 3.10 to 3.13
- An audio output device whose driver supports loopback (nearly all do)
- An OpenAI API key

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Store your API key encrypted per-user in Windows Credential Manager:

```powershell
python scripts/set_key.py
```

Alternatively copy `.env.example` to `.env` and put the key there. `.env` is
gitignored, but Credential Manager is the safer option.

## Running the app

```powershell
python scripts/run_app.py
```

A setup window opens. Paste your API key, pick the audio device, press **Test
audio** while something is playing to confirm capture works, then press **Start
Transcript**. The setup window hides itself and the overlay appears.

The overlay carries four controls and nothing else:

| Control | What it does |
|---|---|
| **Start / Stop** | Begin or end listening |
| **Clear** | Wipe the answer and forget conversation context |
| **Back** | Stop listening and return to the setup window |
| **Copy code** | Appears when an answer contains code |

Global hotkeys work while Chrome, Zoom, or Teams has focus:

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+O` | Show or hide the overlay |
| `Ctrl+Alt+A` | Answer now, without waiting for the silence gap |
| `Ctrl+Alt+C` | Clear |
| `Ctrl+Alt+S` | Start or stop listening |

Verify the UI layer without a display or any API spend:

```powershell
python scripts/selftest_ui.py
```

## Running Phase 0

Confirm audio capture works before anything else:

```powershell
python scripts/list_devices.py
```

Verify the pipeline logic. Makes no API calls, so it costs nothing:

```powershell
python scripts/selftest.py
```

Play some audio before running it, otherwise the live capture check will
correctly report that the device delivered nothing. Loopback endpoints on many
drivers, including Realtek, send no buffers at all while idle rather than sending
silence.

Then start the probe, play some meeting audio, and watch the timings:

```powershell
python scripts/phase0_probe.py
```

Useful flags:

| Flag | Why |
|---|---|
| `--silence-ms 500` | Shorter endpoint wait. The single biggest latency knob |
| `--no-llm` | Transcription only. Isolates STT latency, costs almost nothing |
| `--llm-model gpt-4o` | Better answers, higher cost and latency |
| `--save-audio recordings/` | Dump each detected utterance as WAV to debug segmentation |
| `--vocab "Kubernetes,gRPC,idempotent"` | Bias transcription toward your jargon |
| `--device 12` | Pick a loopback device explicitly |

Ctrl+C prints a latency summary and a cost estimate.

## What Phase 0 is for

It answers one question: **is this fast enough to use in a live conversation?**

The number that matters is *time to first text*, from the other person finishing
their question to words appearing on your screen. You can start reading while the
rest streams in, so total generation time matters much less.

| Time to first text | Meaning |
|---|---|
| under 3s | Comfortable |
| 3 to 5s | Usable, pause is noticeable |
| 5 to 8s | Switch to streaming STT before Phase 1 |
| over 8s | Redesign: streaming STT and/or local Whisper |

If the numbers are bad, we fix the pipeline before investing in a UI. That is the
whole reason this phase has no GUI.

## Roadmap

**Phase 0 — latency probe (current).** CLI only. Proves loopback capture,
segmentation, transcription, and streaming answers, with timing instrumentation.

**Phase 1 — the actual application.** PySide6 always-on-top overlay, frameless and
translucent, streaming answers with syntax-highlighted code, device picker, global
hotkeys, settings persistence, packaged as a single `.exe`.

**Phase 2 and beyond.** Optional local `faster-whisper` backend to remove per-minute
cost, streaming transcription to cut latency, screen/clipboard context so it can see
a shared code snippet as well as hear the question.

## Cost

The only paid component. Everything else, including WASAPI, PyAudioWPatch and
PySide6, is free.

Rough estimate at `gpt-4o-mini-transcribe` plus `gpt-4o-mini`, with silence gated out
so it is not billed: **about $0.20 per meeting hour**, almost entirely transcription.
The probe prints its own estimate. Verify real numbers against your
[OpenAI billing page](https://platform.openai.com/pricing), since rates change.

## Project layout

```
src/orbit/
  config.py           Settings, prompts, credential loading
  metrics.py          Latency and cost accounting
  audio/
    devices.py        WASAPI loopback device discovery
    capture.py        Capture thread, downmix, resample to 16 kHz
    vad.py            Utterance segmentation (energy + hysteresis)
  stt/
    base.py           Transcriber interface (the local/API swap point)
    openai_stt.py     OpenAI transcription
  llm/
    openai_llm.py     Streaming answers, rolling in-memory context
  settings.py         User settings, JSON under %APPDATA%
  app.py              Application wiring, owns both windows
  core/
    worker.py         Pipeline on a worker thread, emits Qt signals
    session.py        Thread lifecycle, re-exposes worker signals
    audiotest.py      Short capture probe for the Test audio button
  ui/
    main_window.py    Setup window and consent notice
    overlay.py        Frameless always-on-top answer panel
    answer_view.py    Streaming renderer with highlighted code
    widgets.py        Status indicator, audio level meter
    hotkeys.py        Global hotkeys via Win32 RegisterHotKey
    theme.py          Palette and stylesheet
scripts/
  run_app.py          Launch the application
  list_devices.py     Verify audio capture works
  selftest.py         Offline pipeline checks, no API calls
  selftest_ui.py      Offline UI checks, headless, no API calls
  set_key.py          Store API key in Credential Manager
  phase0_probe.py     The end-to-end latency probe
```

## Known platform quirks

Both of these were found by the self-test rather than in a live call, and both are
handled in the code:

**Idle loopback endpoints deliver nothing.** Not silence, no buffers at all. Any
read loop needs a bounded idle timeout or it will appear to hang forever. See
`SystemAudioCapture.frames(idle_timeout=...)`.

**Capture must use PortAudio callback mode, not blocking reads.** With a blocking
`stream.read()` on an idle device the read never returns, so shutdown cannot join
the reader thread and ends up freeing the stream underneath it. That crashes the
process with an access violation (`0xC0000005`). Callback mode makes teardown
deterministic.

## A note on use

This is a meeting assistant. It deliberately contains no features for hiding itself
from screen sharing, masking its process, or evading proctoring software.

Be aware that capturing and transmitting other participants' speech to a third-party
API has legal weight in GDPR jurisdictions and US two-party-consent states. The
zero-storage design helps, but consent is still your responsibility.
