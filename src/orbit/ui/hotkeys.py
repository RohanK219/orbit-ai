"""System-wide hotkeys via the Win32 ``RegisterHotKey`` API.

Qt's own ``QShortcut`` only fires when the application has keyboard focus, which
is never the case here: focus lives in Chrome, Zoom, or Teams while the user is
in a meeting. So the hotkeys have to be registered with the OS.

Implemented with ``ctypes`` against ``user32`` rather than a package like
``pynput`` for two reasons: it needs no extra dependency, and it does not install
a low-level keyboard hook, which antivirus software routinely flags as
keylogging behaviour.

Registration failure is never fatal. A hotkey already claimed by another
application is reported so the user can rebind it, and the app remains fully
usable through its own buttons.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIER_NAMES: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}

#: Named keys that are not a single character.
_NAMED_KEYS: dict[str, int] = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "\\": 0xDC,
}


class MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class HotkeyError(ValueError):
    """Raised when a hotkey string cannot be parsed."""


@dataclass(slots=True)
class Binding:
    identifier: int
    name: str
    spec: str
    callback: Callable[[], None]


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Turn ``"ctrl+alt+o"`` into Win32 ``(modifiers, virtual_key)``.

    Raises:
        HotkeyError: if the string has no key, or an unrecognised key name.
    """
    tokens = [part.strip().lower() for part in spec.split("+") if part.strip()]
    if not tokens:
        raise HotkeyError("Hotkey is empty")

    modifiers = 0
    key_token: str | None = None

    for token in tokens:
        if token in _MODIFIER_NAMES:
            modifiers |= _MODIFIER_NAMES[token]
        elif key_token is None:
            key_token = token
        else:
            raise HotkeyError(f"More than one non-modifier key in {spec!r}")

    if key_token is None:
        raise HotkeyError(f"No key given in {spec!r}, only modifiers")

    if len(key_token) == 1 and (key_token.isalnum()):
        virtual_key = ord(key_token.upper())
    elif key_token in _NAMED_KEYS:
        virtual_key = _NAMED_KEYS[key_token]
    elif key_token.startswith("f") and key_token[1:].isdigit():
        number = int(key_token[1:])
        if not 1 <= number <= 24:
            raise HotkeyError(f"Unknown function key {key_token!r}")
        virtual_key = 0x70 + number - 1
    else:
        raise HotkeyError(f"Unrecognised key {key_token!r}")

    # MOD_NOREPEAT stops auto-repeat firing the action continuously while the
    # key is held, which would otherwise queue a burst of API calls.
    return modifiers | MOD_NOREPEAT, virtual_key


class HotkeyManager:
    """Registers global hotkeys against a window handle."""

    def __init__(self) -> None:
        self._bindings: dict[int, Binding] = {}
        self._next_id = 0xB000  # arbitrary base unlikely to collide
        self._hwnd: int | None = None
        self._user32 = ctypes.windll.user32 if sys.platform == "win32" else None

    @property
    def available(self) -> bool:
        return self._user32 is not None

    def attach(self, hwnd: int) -> None:
        """Bind to a window handle. Re-registers anything already added."""
        self._hwnd = int(hwnd)
        existing = list(self._bindings.values())
        self._bindings.clear()
        for binding in existing:
            self.register(binding.name, binding.spec, binding.callback)

    def register(self, name: str, spec: str, callback: Callable[[], None]) -> str | None:
        """Register one hotkey.

        Returns:
            ``None`` on success, or a human-readable reason on failure.
        """
        if self._user32 is None:
            return "Global hotkeys are only supported on Windows"
        if self._hwnd is None:
            return "No window handle available yet"

        try:
            modifiers, virtual_key = parse_hotkey(spec)
        except HotkeyError as exc:
            return str(exc)

        identifier = self._next_id
        self._next_id += 1

        if not self._user32.RegisterHotKey(
            wintypes.HWND(self._hwnd), identifier, modifiers, virtual_key
        ):
            # Almost always because another application already owns it.
            return f"{spec} is already in use by another application"

        self._bindings[identifier] = Binding(identifier, name, spec, callback)
        return None

    def unregister_all(self) -> None:
        if self._user32 is None or self._hwnd is None:
            self._bindings.clear()
            return
        for identifier in list(self._bindings):
            try:
                self._user32.UnregisterHotKey(wintypes.HWND(self._hwnd), identifier)
            except Exception:
                pass
        self._bindings.clear()

    def handle_native_event(self, message: object) -> bool:
        """Dispatch a native Windows message. True if it was one of ours.

        Call from a widget's ``nativeEvent``.
        """
        if self._user32 is None:
            return False
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
        except (TypeError, ValueError):
            return False

        if msg.message != WM_HOTKEY:
            return False

        binding = self._bindings.get(int(msg.wParam))
        if binding is None:
            return False

        try:
            binding.callback()
        except Exception:
            # A failing action must not propagate into Qt's event dispatch.
            pass
        return True
