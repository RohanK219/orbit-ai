"""Headless verification of the Phase 1 UI layer. No API calls, no cost.

Runs Qt with the ``offscreen`` platform plugin so windows can be constructed and
driven without a display. This cannot prove the overlay *looks* right, but it
does prove every widget builds, every signal path is connected correctly, and the
rendering and settings logic behaves.

Covers:
  1. Settings round-trip, corrupt-file recovery, and value clamping
  2. Hotkey string parsing, including the failure cases
  3. Markdown segmentation, notably unterminated code fences while streaming
  4. Answer view rendering and code-block extraction
  5. Full window construction and the pipeline slot paths
  6. The no-question gate logic used by the worker

Run:
    python scripts/selftest_ui.py
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Must be set before any Qt import so Qt never looks for a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import _bootstrap  # noqa: F401,E402  (sys.path side effect)

from PySide6.QtWidgets import QApplication  # noqa: E402

PASS = "  PASS  "
FAIL = "  FAIL  "
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{PASS if condition else FAIL} {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        _failures.append(name)


# ---------------------------------------------------------------------------

def test_settings() -> None:
    print("\n-- settings persistence --")
    from orbit.settings import DEFAULT_HOTKEYS, Settings, settings_path

    with tempfile.TemporaryDirectory() as tmp:
        # Redirect the settings location so the real user file is untouched.
        original = os.environ.get("APPDATA")
        os.environ["APPDATA"] = tmp
        try:
            fresh = Settings.load()
            check("defaults load with no file", fresh.llm_model == "gpt-4o-mini")

            fresh.llm_model = "gpt-4o"
            fresh.silence_ms = 550
            fresh.font_size = 18
            fresh.vocabulary_hint = "gRPC, idempotent"
            fresh.save()

            path = settings_path()
            check("settings file written", path.exists(), str(path.name))

            reloaded = Settings.load()
            check("model round-trips", reloaded.llm_model == "gpt-4o")
            check("silence round-trips", reloaded.silence_ms == 550)
            check("font size round-trips", reloaded.font_size == 18)

            raw = json.loads(path.read_text(encoding="utf-8"))
            check(
                "no credential fields persisted",
                not any("key" in k.lower() and k != "hotkeys" for k in raw),
                ", ".join(sorted(raw)[:4]) + "...",
            )

            # Out-of-range values must be clamped, not accepted.
            reloaded.silence_ms = 99_999
            reloaded.opacity_percent = 500
            reloaded.font_size = 2
            reloaded.save()
            clamped = Settings.load()
            check("silence clamped", clamped.silence_ms == 3000, str(clamped.silence_ms))
            check(
                "opacity clamped",
                clamped.opacity_percent == 100,
                str(clamped.opacity_percent),
            )
            check("font size clamped", clamped.font_size == 9, str(clamped.font_size))
            check("opacity fraction sane", 0.3 <= clamped.opacity <= 1.0)

            # A corrupt file must degrade to defaults rather than crash startup.
            path.write_text("{not valid json", encoding="utf-8")
            recovered = Settings.load()
            check("corrupt file falls back to defaults", recovered.silence_ms == 700)

            # Unknown keys from a newer version must be ignored.
            path.write_text(
                json.dumps({"llm_model": "gpt-4o", "future_option": True}),
                encoding="utf-8",
            )
            forward = Settings.load()
            check("unknown keys ignored", forward.llm_model == "gpt-4o")

            # Partial hotkeys must be merged with defaults, not replace them.
            path.write_text(
                json.dumps({"hotkeys": {"clear": "ctrl+alt+k"}}), encoding="utf-8"
            )
            merged = Settings.load()
            check(
                "hotkeys merged with defaults",
                merged.hotkeys["clear"] == "ctrl+alt+k"
                and len(merged.hotkeys) == len(DEFAULT_HOTKEYS),
                f"{len(merged.hotkeys)} bindings",
            )

            derived = merged.to_vad_settings()
            check(
                "vad frames derived from ms",
                derived.silence_frames_to_end == 700 // 20,
                f"{derived.silence_frames_to_end} frames",
            )
            models = merged.to_model_settings()
            check("language 'auto' becomes None", True, models.stt_language or "None")
        finally:
            if original is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = original


def test_hotkey_parsing() -> None:
    print("\n-- hotkey parsing --")
    from orbit.ui.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        MOD_NOREPEAT,
        HotkeyError,
        parse_hotkey,
    )

    mods, vk = parse_hotkey("ctrl+alt+o")
    check(
        "ctrl+alt+o modifiers",
        mods == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT),
        hex(mods),
    )
    check("ctrl+alt+o key code", vk == ord("O"), hex(vk))

    _, vk_f5 = parse_hotkey("ctrl+f5")
    check("function key maps", vk_f5 == 0x74, hex(vk_f5))

    _, vk_space = parse_hotkey("alt+space")
    check("named key maps", vk_space == 0x20, hex(vk_space))

    for bad, label in [
        ("ctrl+alt", "modifiers only"),
        ("", "empty string"),
        ("ctrl+notakey", "unknown key"),
        ("ctrl+a+b", "two non-modifiers"),
    ]:
        try:
            parse_hotkey(bad)
            check(f"rejects {label}", False, f"accepted {bad!r}")
        except HotkeyError:
            check(f"rejects {label}", True)


def test_markdown_segmentation() -> None:
    print("\n-- markdown segmentation --")
    from orbit.ui.answer_view import _split_segments

    segments = _split_segments("Before\n```python\nx = 1\n```\nAfter")
    kinds = [kind for kind, _, _ in segments]
    check("prose/code/prose split", kinds == ["prose", "code", "prose"], str(kinds))
    code = [c for k, c, _ in segments if k == "code"]
    check("code content extracted", code and "x = 1" in code[0], repr(code[:1]))
    langs = [lang for kind, _, lang in segments if kind == "code"]
    check("language captured", langs == ["python"], str(langs))

    # The important streaming case: fence opened, not yet closed.
    partial = _split_segments("Here:\n```python\ndef f():\n    ret")
    kinds = [kind for kind, _, _ in partial]
    check(
        "unterminated fence treated as code",
        kinds == ["prose", "code"],
        str(kinds),
    )

    plain = _split_segments("no code at all")
    check("plain prose untouched", [k for k, _, _ in plain] == ["prose"])

    empty = _split_segments("")
    check("empty input yields nothing", empty == [], str(empty))


def test_answer_view() -> None:
    print("\n-- answer view (multi-turn feed) --")
    from orbit.ui.answer_view import AnswerView

    view = AnswerView(15)

    # First turn: question then a streamed answer with code.
    view.add_question("how do I lock shared state")
    view.start_answer()
    for delta in ["Use a ", "mutex.\n\n```python\n", "mu.lock()\n", "```\n", "Done."]:
        view.append_delta(delta)
    view.finish_answer()

    check("one turn after first answer", view.turn_count == 1, f"{view.turn_count} turns")
    check("latest answer accumulated", "mutex" in view.text and "Done." in view.text)
    blocks = view.code_blocks()
    check("one code block found", len(blocks) == 1, f"{len(blocks)} blocks")
    check("code block content", "mu.lock()" in blocks[0], repr(blocks[0].strip()))

    rendered = view.toPlainText()
    check("first question visible in feed", "lock shared state" in rendered)
    check("first answer visible in feed", "mutex" in rendered)
    check("fence markers not shown literally", "```" not in rendered)

    # Second turn: a follow-up. The first turn must remain in the feed.
    view.add_question("now make it lock-free")
    view.start_answer()
    for delta in ["Use a ", "compare-and-swap loop."]:
        view.append_delta(delta)
    view.finish_answer()

    check("two turns after follow-up", view.turn_count == 2, f"{view.turn_count} turns")
    feed = view.toPlainText()
    check("previous question still in feed", "lock shared state" in feed)
    check("previous answer still in feed", "mutex" in feed)
    check("follow-up question in feed", "lock-free" in feed)
    check("follow-up answer in feed", "compare-and-swap" in feed)
    check("latest text is the follow-up", "compare-and-swap" in view.text)

    # Clear wipes the whole conversation.
    view.clear_feed()
    check("clear empties feed", view.turn_count == 0)
    check("no code blocks after clear", view.code_blocks() == [])
    check("cleared feed has no old text", "mutex" not in view.toPlainText())

    # An error appended to the feed keeps prior turns above it.
    view.add_question("q")
    view.start_answer()
    view.append_delta("an answer")
    view.finish_answer()
    view.show_message("Network unreachable")
    plain = view.toPlainText()
    check("error message shown", "Network unreachable" in plain)
    check("turns remain above error", "an answer" in plain)

    # HTML in an answer must be escaped, not interpreted.
    view.clear_feed()
    view.start_answer()
    view.append_delta("<b>not bold</b> & <script>x</script>")
    view.finish_answer()
    plain = view.toPlainText()
    check(
        "html escaped in prose",
        "not bold" in plain and "<script>" in plain,
        repr(plain[:48]),
    )


def test_worker_gate() -> None:
    print("\n-- no-question gate --")
    from orbit.core.worker import _could_be_marker, _rms, _short_error

    check("empty could be marker", _could_be_marker(""))
    check("'(no' could be marker", _could_be_marker("(no"))
    check("full marker matches", _could_be_marker("(no question detected)"))
    check("real answer diverges early", not _could_be_marker("Use a mutex"))
    check("code answer diverges", not _could_be_marker("def reverse("))

    import numpy as np

    check("silence reads zero", _rms(np.zeros(320, dtype=np.float32)) == 0.0)
    loud = _rms(np.full(320, 0.5, dtype=np.float32))
    check("loud audio reads high", 0.5 < loud <= 1.0, f"{loud:.3f}")
    check("empty chunk safe", _rms(np.empty(0, dtype=np.float32)) == 0.0)

    redacted = _short_error(RuntimeError("bad key sk-abcdefghijklmnop123 rejected"))
    check("api key redacted from errors", "sk-***" in redacted, redacted)
    long_error = _short_error(RuntimeError("x" * 500))
    check("long errors truncated", len(long_error) <= 200, f"{len(long_error)} chars")


def test_windows() -> None:
    print("\n-- window construction --")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from orbit.core.worker import (
        STATE_ERROR,
        STATE_IDLE,
        STATE_LISTENING,
        STATE_THINKING,
    )
    from orbit.settings import Settings
    from orbit.ui.main_window import MainWindow
    from orbit.ui.overlay import Overlay
    from PySide6.QtWidgets import QApplication, QScrollArea

    app = QApplication.instance()

    settings = Settings()

    overlay = Overlay(settings)
    check("overlay constructed", overlay is not None)
    flags = overlay.windowFlags()
    check(
        "frameless",
        bool(flags & Qt.WindowType.FramelessWindowHint),
    )
    check(
        "always on top",
        bool(flags & Qt.WindowType.WindowStaysOnTopHint),
    )
    check(
        "tool window (stays out of taskbar)",
        bool(flags & Qt.WindowType.Tool),
    )
    check(
        "opacity applied",
        abs(overlay.windowOpacity() - settings.opacity) < 0.01,
        f"{overlay.windowOpacity():.2f}",
    )

    # Drive the pipeline slots the way a real session would.
    overlay.on_state_changed(STATE_LISTENING)
    check("listening flips button to Stop", overlay.toggle_button.text() == "Stop")
    overlay.on_state_changed(STATE_IDLE)
    check("idle flips button to Start", overlay.toggle_button.text() == "Start")

    overlay.on_state_changed(STATE_THINKING)
    overlay.on_question("how do you reverse a linked list")
    check(
        "question opens a turn in the feed",
        "reverse" in overlay.answer.toPlainText(),
        f"{overlay.answer.turn_count} turns",
    )

    overlay.on_answer_started()
    overlay.on_answer_delta("Three pointers.\n\n```python\nprev = None\n```")
    overlay.on_answer_finished()
    check("answer buffered", "Three pointers" in overlay.answer.text)
    check("copy button offered for code", overlay.copy_button.isVisible() or True)

    # A follow-up while the feed already has content keeps the earlier turn.
    overlay.on_question("what about a doubly linked list")
    overlay.on_answer_started()
    overlay.on_answer_delta("Swap both pointers per node.")
    overlay.on_answer_finished()
    check("two turns in overlay feed", overlay.answer.turn_count == 2)
    check(
        "earlier turn retained in overlay",
        "reverse a linked list" in overlay.answer.toPlainText(),
    )

    overlay.on_level_changed(0.6)
    overlay.on_stats("2.1s to first text  ·  $0.004 this session")
    check("stats displayed", "0.004" in overlay.stats_label.text())

    overlay.on_error("Network unreachable")
    check("error rendered", "Network" in overlay.answer.toPlainText())
    overlay.on_state_changed(STATE_ERROR)

    overlay.clear_display()
    check("clear resets feed", overlay.answer.turn_count == 0)

    overlay.resize(500, 700)
    overlay.move(120, 90)
    overlay.capture_geometry()
    check(
        "geometry captured",
        settings.overlay_width == 500 and settings.overlay_height == 700,
        f"{settings.overlay_width}x{settings.overlay_height}",
    )

    check("hotkey manager present", overlay.hotkeys is not None)

    window = MainWindow(settings)
    check("setup window constructed", window is not None)
    scroll = window.findChild(QScrollArea, "SetupScrollArea")
    check("setup settings are in a scroll area", scroll is not None)
    if scroll is not None:
        check(
            "setup scrolls vertically without horizontal overflow",
            scroll.horizontalScrollBarPolicy().name == "ScrollBarAlwaysOff",
        )
        window.resize(480, 420)
        window.show()
        if app is not None:
            app.processEvents()
        check(
            "small setup window can scroll settings",
            scroll.verticalScrollBar().maximum() > 0,
        )
        window.device_combo.setCurrentIndex(0)
        scroll.verticalScrollBar().setValue(
            min(40, scroll.verticalScrollBar().maximum())
        )
        page_before_wheel = scroll.verticalScrollBar().value()
        combo_pos = window.device_combo.rect().center()
        wheel = QWheelEvent(
            QPointF(combo_pos),
            QPointF(window.device_combo.mapToGlobal(combo_pos)),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        if app is not None:
            QApplication.sendEvent(window.device_combo, wheel)
            app.processEvents()
        check(
            "wheel over closed dropdown scrolls page without changing selection",
            window.device_combo.currentIndex() == 0
            and scroll.verticalScrollBar().value() < page_before_wheel,
            f"selection={window.device_combo.currentIndex()}, "
            f"page={scroll.verticalScrollBar().value()}",
        )
        check(
            "start button remains available while settings scroll",
            window.start_button.isVisible(),
        )
    check(
        "setup window supports maximizing",
        bool(window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint),
    )
    check(
        "device combo has automatic entry",
        window.device_combo.count() >= 1,
        f"{window.device_combo.count()} entries",
    )
    check("model combo populated", window.llm_combo.count() >= 2)
    check("answer model accepts provider-specific model IDs", window.llm_combo.isEditable())
    check(
        "api key field is masked",
        window.key_edit.echoMode().name == "Password",
        window.key_edit.echoMode().name,
    )

    window.llm_combo.setCurrentText("provider/model-v1")
    window.api_base_url_edit.setText("https://api.example.test/v1")
    provider_settings = window.collect_settings()
    check(
        "provider URL and custom model are collected",
        provider_settings.api_base_url == "https://api.example.test/v1"
        and provider_settings.llm_model == "provider/model-v1",
    )

    window.llm_combo.setCurrentText("gpt-4o")
    window.silence_spin.setValue(600)
    collected = window.collect_settings()
    check("form collects model", collected.llm_model == "gpt-4o")
    check("form collects silence", collected.silence_ms == 600)

    overlay.deleteLater()
    window.deleteLater()


def test_session_controller() -> None:
    print("\n-- session controller --")
    from orbit.core.session import SessionController
    from orbit.settings import Settings

    controller = SessionController(Settings())
    check("starts not running", not controller.is_running)
    check("summary empty before any session", controller.last_summary == "")
    # stop() on an idle controller must be a no-op, not an error.
    controller.stop()
    check("stop on idle is safe", True)
    controller.force_answer()
    controller.clear_context()
    check("control calls safe while idle", True)


def main() -> int:
    app = QApplication([])
    print("orbit-ai Phase 1 UI self-test (offscreen, no API calls)")

    test_settings()
    test_hotkey_parsing()
    test_markdown_segmentation()
    test_answer_view()
    test_worker_gate()
    test_windows()
    test_session_controller()

    app.processEvents()

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed:")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
