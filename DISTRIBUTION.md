# Running orbit-ai on another machine

orbit-ai is built on one Windows machine and run on another. The build produces a
single `orbit-ai.exe` that needs **no Python, no virtual environment, and no setup**
on the machine that runs it. This is deliberate: the build machine can be locked
down, and the run machine only ever sees one file.

---

## 1. Build the exe (on the build machine)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

The build performs a fast packaging preflight first. To run that check without
starting PyInstaller:

```powershell
.\.venv\Scripts\python.exe scripts\validate_packaging.py
```

This produces:

```
dist\orbit-ai.exe
```

Expect roughly 80-120 MB. It is large because it carries the Python runtime and the
Qt UI libraries inside it, which is exactly what lets it run with nothing installed
on the other side.

## 2. Move it to the run machine

Copy `dist\orbit-ai.exe` across by whatever means you normally use: USB drive, your
own file transfer, a personal cloud drive. It is a single file.

Nothing else needs to come with it. No `src` folder, no `.venv`, no Python.

## 3. First launch

Double-click `orbit-ai.exe`. The setup window opens.

### The SmartScreen warning is expected

The first time an unsigned exe runs on a machine, Windows shows:

> Windows protected your PC

This is not a sign anything is wrong. It appears for any program without a paid code
signing certificate. Click **More info**, then **Run anyway**. It only asks once per
machine.

Some antivirus tools may also flag it briefly. This is because the app is packed into
one file, a technique legitimate and malicious software both use, so scanners are
cautious. If your antivirus quarantines it, restore it from quarantine or add an
exclusion for the file.

## 4. Set it up (one time)

In the setup window:

1. **OpenAI API key** — paste your key and press **Save**. It is validated against
   OpenAI immediately, and stored in Windows Credential Manager on that machine, not
   in any file.
2. **Audio source** — leave it on Automatic, or pick your speakers explicitly.
3. **Test audio** — play any audio (a YouTube video works) and press this. The level
   meter should move and it should report a detected peak. If it reads silence, the
   selected device is not where your sound is playing; pick a different one.

## 5. Use it in a meeting

1. Join your meeting as normal.
2. Press **Start Transcript**. The setup window hides and the floating overlay
   appears, staying on top of other windows.
3. When someone asks a question, it is transcribed and answered on the overlay. The
   conversation builds up as a scrollable feed, so follow-up questions stack below
   earlier ones and you can scroll back.

Global hotkeys work even while the meeting app has focus:

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+O` | Show / hide the overlay |
| `Ctrl+Alt+A` | Answer now, without waiting for the pause |
| `Ctrl+Alt+C` | Clear the conversation |
| `Ctrl+Alt+S` | Start / stop listening |

---

## What to check on the run machine

These could not be verified on the build machine, so confirm them on first real use:

- **Transcription accuracy** on real speech, including any technical vocabulary. Use
  the Vocabulary hints field for names and jargon it mishears.
- **Latency** — the footer shows time-to-first-text per answer. Under about 5 seconds
  is comfortable. If it is slow, lower the **End of question** setting toward 500 ms.
- **Cost** — the footer shows an estimated running total. Set a **Session spend limit**
  if you want a hard stop.
- **Overlay stacking** — confirm it stays above your specific meeting app, including in
  full-screen.

## Cost reminder

Only the OpenAI API costs money. At the default models, budget roughly $0.20 per hour
of meeting audio. Verify against your own OpenAI billing page, since rates change.

## Privacy reminder

While listening, audio from your speakers, which includes other participants, is sent
to OpenAI for transcription. Nothing is written to disk. Recording or processing other
people's speech carries legal obligations in some regions; make sure you are entitled
to do it.

---

## Troubleshooting

**The window opens then closes immediately.**
Usually a missing bundled component. Rebuild with `scripts\build_exe.ps1` and confirm
the build reported success. If it persists, build a console variant to see the error:
edit `packaging\orbit-ai.spec`, set `console=True`, rebuild, and run from a terminal to
read the traceback.

**"Could not find or load the Qt platform plugin windows".**
The Qt platform plugin was not bundled. The spec collects it, so this points to a
broken build; rebuild from a clean state (the script removes old output first).

**Test audio always reports silence.**
Loopback devices report nothing while idle on many drivers. Make sure audio is actually
playing when you press Test audio. If it still reads silent, pick a specific device
from the dropdown rather than Automatic.

**No answers appear during a meeting.**
Check the status indicator in the overlay header. If it never leaves "listening", the
audio is not being captured; use Test audio to confirm the device. If it reaches
"thinking" then shows an error, the API key or network is the issue.
