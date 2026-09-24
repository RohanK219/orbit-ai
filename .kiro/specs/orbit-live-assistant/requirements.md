# Requirements — Orbit Live Assistant

## Overview

Orbit is a Windows desktop application that runs alongside an online meeting (Google Meet, Zoom, Teams). It captures **system audio only**, transcribes speech live, sends the detected question to an LLM, and streams the answer onto an always-on-top overlay window. Conversation data is never written to disk.

The user reads the answer off the overlay themselves. The app produces no audio and never joins the meeting as a participant.

## Target user

A single person running the app on their own Windows laptop during calls where they need fast technical recall: client calls, support calls, sales engineering, or accessibility support.

## Scope

**In scope for v1**

- WASAPI loopback capture of system output
- Live speech-to-text with a swappable backend (OpenAI API or local Whisper)
- Automatic turn segmentation plus a manual trigger
- Streaming LLM answers with rolling conversation context
- Frameless, translucent, always-on-top overlay with syntax-highlighted code
- Settings UI, global hotkeys, secure API key storage
- Live cost and latency metering
- Single-file `.exe` distribution

**Explicit non-goals**

- No stealth, process masking, screen-share hiding, or proctoring evasion of any kind
- No text-to-speech or audio output
- No microphone capture
- No database, no accounts, no cloud sync
- No transcript export or session history in v1
- Windows only in v1; macOS and Linux are deferred

## Requirements

### R1 — System audio capture

**User story:** As a user, I want the app to hear what the other participants say, without picking up my own microphone, so that only the other side is processed.

1. WHEN the app starts THEN it SHALL enumerate available WASAPI loopback devices and identify the loopback counterpart of the current default output device.
2. WHEN the user selects an audio device in settings THEN the app SHALL persist that choice and use it on subsequent launches.
3. WHEN capture is active THEN the app SHALL read system output audio and downmix it to 16 kHz mono 16-bit PCM.
4. The app SHALL NOT open, request, or read from any microphone or other input device.
5. WHEN the selected audio device is removed or changed by the OS THEN the app SHALL surface a clear error state and offer to re-select a device without crashing.
6. WHEN capture is active THEN audio frames SHALL be held in a bounded in-memory ring buffer that discards the oldest frames on overflow rather than growing without limit.

### R2 — Live transcription

**User story:** As a user, I want speech converted to text quickly, so that an answer can be produced while the topic is still relevant.

1. The app SHALL define a single transcription interface with at least two interchangeable implementations: a cloud backend and a local backend.
2. WHEN the cloud backend is active THEN the app SHALL stream audio to a transcription-only realtime session over WebSocket and consume incremental transcript deltas.
3. WHEN the local backend is active THEN the app SHALL transcribe buffered audio on-device with no network call.
4. WHEN the user changes the transcription backend in settings THEN the app SHALL apply the change without requiring a restart.
5. WHEN partial transcript text is received THEN the app SHALL display it as provisional text distinct from finalized text.
6. The app SHALL NOT use a speech-to-speech session type, because audio output tokens are billed at a substantially higher rate than text.

### R3 — Turn segmentation

**User story:** As a user, I want the app to work out when the speaker has finished asking, so that I do not have to trigger it manually every time.

1. WHEN voice activity is detected followed by continuous silence exceeding a configurable threshold (default 800 ms) THEN the app SHALL treat the preceding speech as a completed turn.
2. WHEN a turn's speech duration is below a configurable minimum (default 1.2 s) THEN the app SHALL discard it as noise or filler and SHALL NOT call the LLM.
3. WHEN a turn exceeds a configurable maximum duration (default 30 s) THEN the app SHALL force a commit and begin a new turn.
4. WHEN no voice activity is present THEN the app SHALL NOT transmit audio to any cloud backend.
5. WHEN the user presses the ask-now hotkey THEN the app SHALL immediately commit whatever audio is buffered and request an answer, regardless of silence state.
6. The app SHALL expose the silence, minimum, and maximum thresholds as user-editable settings.

### R4 — Answer generation

**User story:** As a user, I want a short, readable answer I can speak aloud, so that I am not reading a wall of text on a live call.

1. WHEN a turn is committed THEN the app SHALL send the transcript plus rolling context to the configured chat model and stream the response.
2. WHEN response tokens arrive THEN the app SHALL render them incrementally rather than waiting for completion.
3. The app SHALL maintain a rolling in-memory context of the most recent turns (default 10) so that follow-up questions referring to earlier discussion resolve correctly.
4. The system prompt SHALL instruct the model to lead with a direct answer, stay brief enough to read aloud, and place code in fenced blocks with a language tag.
5. WHEN a new turn is committed while a previous answer is still streaming THEN the app SHALL cancel the in-flight request and start the new one.
6. WHEN the user presses the clear hotkey THEN the app SHALL discard the rolling context and clear the overlay.

### R5 — Overlay display

**User story:** As a user, I want a compact panel I can glance at without it taking over my screen.

1. The overlay SHALL be frameless, translucent with configurable opacity, and stay above other windows including full-screen meeting clients.
2. The overlay SHALL be draggable by its header and SHALL persist its last position and size.
3. The overlay SHALL show a status indicator distinguishing at minimum: idle, listening, transcribing, thinking, error.
4. The overlay SHALL show the transcribed question so the user can confirm it was heard correctly.
5. WHEN an answer contains a fenced code block THEN the overlay SHALL render it with syntax highlighting and a monospace font.
6. The overlay SHALL support user-adjustable font size, and SHALL be readable at a glance at the default size.
7. The overlay SHALL be scrollable and SHALL auto-scroll to follow streaming output unless the user has scrolled up manually.

### R6 — Configuration and secrets

**User story:** As a user, I want to enter my API key once and have it stored safely.

1. WHEN the user enters an API key THEN the app SHALL store it in Windows Credential Manager via the OS keyring, and SHALL NOT write it to any file in the project or config directory.
2. The app SHALL store non-secret settings as a single JSON file under `%APPDATA%\orbit-ai\`.
3. WHEN the app starts with no stored key and the cloud backend is selected THEN it SHALL prompt for a key before attempting capture.
4. WHEN the stored key is rejected by the provider THEN the app SHALL surface an authentication error and reopen the key entry field.
5. The app SHALL NOT log API keys, and SHALL redact them if they appear in error payloads.
6. WHEN the settings file is missing or malformed THEN the app SHALL fall back to defaults and continue rather than failing to start.

### R7 — Privacy and zero persistence

**User story:** As a user, I want other people's speech to leave no trace on my machine.

1. The app SHALL NOT write captured audio to disk at any point.
2. The app SHALL NOT write transcripts or model responses to disk at any point.
3. WHEN the app exits THEN all conversation state SHALL be discarded with the process.
4. Diagnostic logs SHALL contain only timing, state transitions, and error information, never transcript or answer content.
5. WHEN the user enables a debug logging mode THEN the app SHALL warn explicitly that content may then appear in logs, and SHALL default this mode to off.

### R8 — Cost control

**User story:** As a user on a budget, I want to see and cap what I am spending.

1. The overlay SHALL display a running estimate of the current session's spend.
2. The app SHALL let the user select the transcription and chat models from a configured list, defaulting to the cheapest viable option.
3. WHEN the session spend estimate crosses a user-defined limit THEN the app SHALL warn and offer to pause capture.
4. The app SHALL count and expose audio minutes transmitted and tokens consumed for the session.
5. Silence-gated capture per R3.4 SHALL be on by default.

### R9 — Resilience

**User story:** As a user mid-call, I need the app to recover on its own rather than needing a restart.

1. WHEN the transcription WebSocket disconnects THEN the app SHALL reconnect with exponential backoff while showing a reconnecting state, preserving buffered audio where possible.
2. WHEN a model request fails or is rate-limited THEN the app SHALL retry with backoff and show the failure in the overlay rather than silently doing nothing.
3. WHEN network access is unavailable entirely THEN the app SHALL report it clearly and, if a local backend is configured, offer to switch to it.
4. An unhandled exception in the audio, transcription, or model layer SHALL NOT terminate the UI process.
5. The UI SHALL remain responsive at all times; no network or audio call SHALL block the Qt event loop.

### R10 — Packaging

**User story:** As a user, I want to launch this like a normal program.

1. The app SHALL build to a distributable Windows executable that runs without a preinstalled Python.
2. The app SHALL launch with no console window visible.
3. The app SHALL start and be ready to capture in under 5 seconds on a typical machine.
4. The build SHALL be reproducible from a single documented command.

### R11 — Consent and disclosure

**User story:** As someone recording other people's speech through a third-party API, I want the app to help me stay on the right side of this.

1. WHEN the app runs for the first time THEN it SHALL display a one-time notice explaining that participant audio is transmitted to a third-party API and that consent requirements vary by jurisdiction.
2. The app SHALL provide a visible, single-action way to stop capture immediately.
3. WHEN capture is active THEN the overlay status SHALL make that unambiguous, so the user is never unsure whether audio is being processed.

## Performance targets

| Measure | Target | Go/no-go ceiling |
|---|---|---|
| Turn end to first transcript token | < 1.0 s | 2.5 s |
| Turn end to first answer token | < 2.5 s | 5.0 s |
| Turn end to complete short answer | < 5.0 s | 10.0 s |
| Overlay frame responsiveness | no visible stall | — |
| Idle CPU | < 5% | 15% |

Phase 0 exists to measure these on the user's actual machine and network. If the go/no-go ceilings cannot be met, the design is revisited before any UI work begins.
