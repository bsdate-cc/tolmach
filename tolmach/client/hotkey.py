"""Global hotkey through RegisterHotKey in its own message-loop thread.

No low-level keyboard hook: a toggle needs only the press, and a hook would
make every keystroke in the system wait for this process.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable

from tolmach.client import events as ev

log = logging.getLogger("tolmach")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
PM_NOREMOVE = 0
HOTKEY_ID = 1

_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "pause": 0x13,
         "insert": 0x2D, "home": 0x24, "end": 0x23}

user32 = ctypes.windll.user32


def parse_hotkey(text: str) -> tuple[int, int]:
    """'shift+win+q' -> (modifier flags, virtual key code)."""
    parts = [part.strip().lower() for part in text.split("+")]
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"a hotkey needs modifiers and a key: {text!r}")
    mods = 0
    for name in parts[:-1]:
        if name not in _MODS:
            raise ValueError(f"unknown modifier {name!r} in {text!r}")
        mods |= _MODS[name]
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return mods, ord(key.upper())
    if key in _KEYS:
        return mods, _KEYS[key]
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return mods, 0x70 + int(key[1:]) - 1
    raise ValueError(f"unknown key {key!r} in {text!r}")


def _toggle() -> ev.Toggle:
    return ev.Toggle("hotkey", time.monotonic())


class Hotkey:
    """Posts an event on every press: Toggle('hotkey') unless told otherwise. A taken or
    malformed hotkey is logged, not fatal; an empty one is simply off."""

    def __init__(self, post: Callable, hotkey: str, make_event: Callable[[], object] = _toggle):
        self._post = post
        self._text = hotkey.strip()
        self._make_event = make_event
        self._thread_id = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self.registered = False

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        # Only a registered hotkey has a message loop to end: a taken, malformed or empty
        # one let its thread go already, and there is nobody to post to.
        if self.registered and self._thread_id is not None:
            result = user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            if not result:
                log.warning("failed to post quit message to hotkey thread")
        self._thread.join(2.0)

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not self._text:
            self._ready.set()
            return
        try:
            mods, vk = parse_hotkey(self._text)
        except ValueError as e:
            log.warning("hotkey is not usable: %s", e)
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
            self._ready.set()
            return
        if not user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            log.warning("hotkey %s is taken by another program", self._text)
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
            self._ready.set()
            return
        self.registered = True
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self._post(self._make_event())
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False
