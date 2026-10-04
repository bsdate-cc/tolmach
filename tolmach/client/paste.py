"""Put text into the focused field: typed as keystrokes, or pasted through the clipboard."""
from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes
from typing import Callable

import pyperclip

log = logging.getLogger("tolmach")

MODIFIER_WAIT_S = 1.0
SETTLE_S = 0.05
RESTORE_DELAY_S = 0.3
# Typing goes out in small bursts with a breath in between, so that a slow
# receiver (a remote desktop over the network) is not flooded.
TYPE_CHUNK = 40
TYPE_PAUSE_S = 0.01
FOCUS_WAIT_S = 0.5
FOCUS_SETTLE_S = 0.1
# The taskbar and the tray's overflow panel: in front for a moment on the way to our
# menu, and never a place to type into.
SHELL_CLASSES = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                           "TopLevelWindowForOverflowXamlIsland"})

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_V = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x56
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
MAPVK_VK_TO_VSC = 0

user32 = ctypes.windll.user32


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):  # never sent; it gives the union its real size
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


def typed_units(text: str) -> list[int]:
    """UTF-16 code units to type. Line breaks become spaces: Enter in a chat
    window sends the message, and a dictation must never press it."""
    flat = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    data = flat.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def type_text(text: str, *, modifiers_down: Callable[[], bool], send_units: Callable[[list[int]], bool],
              sleep: Callable[[float], None], clock: Callable[[], float]) -> bool:
    """Type the text as Unicode keystrokes. False: the system refused some of them.

    No clipboard is involved, the keyboard layout does not matter, and - unlike
    Ctrl+V - the keystrokes reach an application behind a remote desktop.
    """
    deadline = clock() + MODIFIER_WAIT_S
    while modifiers_down() and clock() < deadline:
        sleep(0.02)
    sleep(SETTLE_S)
    units = typed_units(text)
    for start in range(0, len(units), TYPE_CHUNK):
        if not send_units(units[start:start + TYPE_CHUNK]):
            return False
        sleep(TYPE_PAUSE_S)
    return True


def _send_units(units: list[int]) -> bool:
    events = (_INPUT * (2 * len(units)))()
    for i, unit in enumerate(units):
        for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
            event = events[2 * i + j]
            event.type = INPUT_KEYBOARD
            event.ki = _KEYBDINPUT(0, unit, flags, 0, 0)
    return user32.SendInput(len(events), events, ctypes.sizeof(_INPUT)) == len(events)


def paste_text(text: str, *, get_clip: Callable[[], str], set_clip: Callable[[str], None],
               modifiers_down: Callable[[], bool], send_paste: Callable[[], None],
               sleep: Callable[[float], None], clock: Callable[[], float]) -> bool:
    """Clipboard round trip around Ctrl+V. False: the text never reached the clipboard.

    An empty previous clipboard (nothing there, or not text) is not restored.
    """
    try:
        previous = get_clip()
    except Exception:
        previous = ""
    try:
        set_clip(text)
    except Exception as e:
        log.warning("clipboard is not available: %s", type(e).__name__)
        return False
    deadline = clock() + MODIFIER_WAIT_S
    while modifiers_down() and clock() < deadline:
        sleep(0.02)
    sleep(SETTLE_S)
    send_paste()
    sleep(RESTORE_DELAY_S)
    if previous:
        try:
            set_clip(previous)
        except Exception as e:
            log.warning("could not restore the clipboard: %s", type(e).__name__)
    return True


def _modifiers_down() -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000
               for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def _send_ctrl_v() -> None:
    # With scan codes: a remote-desktop client forwards scan codes, not virtual
    # keys, so a key event with scan code 0 never reaches the remote application.
    ctrl = user32.MapVirtualKeyW(VK_CONTROL, MAPVK_VK_TO_VSC)
    v = user32.MapVirtualKeyW(VK_V, MAPVK_VK_TO_VSC)
    user32.keybd_event(VK_CONTROL, ctrl, 0, 0)
    user32.keybd_event(VK_V, v, 0, 0)
    user32.keybd_event(VK_V, v, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, ctrl, KEYEVENTF_KEYUP, 0)


def _window_class(hwnd: int) -> str:
    try:
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buffer, 128)
        return buffer.value or "?"
    except Exception:
        return "?"


def _foreground_class() -> str:
    """Window class of what receives the paste: enough to tell 'went to the wrong
    window' in the log, and unlike the title it says nothing about the content."""
    return _window_class(user32.GetForegroundWindow())


def _is_ours(hwnd: int) -> bool:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def _usable(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd))


class Focus:
    """Which window gets the text.

    Normally the one in front. But a click on our tray menu ("Остановить диктовку")
    leaves the icon's hidden window in front, and text typed there goes nowhere. So
    the window the user works in is remembered while we run (note(), on every tick),
    and brought back to the front before the text is put in (aim()).
    """

    def __init__(self, *, foreground: Callable[[], int], is_ours: Callable[[int], bool],
                 window_class: Callable[[int], str], usable: Callable[[int], bool],
                 activate: Callable[[int], object], sleep: Callable[[float], None],
                 clock: Callable[[], float]):
        self._foreground = foreground
        self._is_ours = is_ours
        self._window_class = window_class
        self._usable = usable
        self._activate = activate
        self._sleep = sleep
        self._clock = clock
        self._last = 0

    def _takes_text(self, hwnd: int) -> bool:
        return bool(hwnd) and not self._is_ours(hwnd) and self._window_class(hwnd) not in SHELL_CLASSES

    def note(self) -> None:
        hwnd = self._foreground()
        if self._takes_text(hwnd):
            self._last = hwnd

    def target(self) -> int:
        """The window the text would go to right now - the one in front, or the remembered
        one - without switching anything; 0 when there is none."""
        hwnd = self._foreground()
        if self._takes_text(hwnd):
            return hwnd
        return self._last if self._last and self._usable(self._last) else 0

    def aim(self) -> bool:
        """True when a window that can take the text is in front (after a switch, if one was needed)."""
        if self._takes_text(self._foreground()):
            return True
        target = self._last
        if not target or not self._usable(target):
            return False
        self._activate(target)
        deadline = self._clock() + FOCUS_WAIT_S
        while self._foreground() != target:
            if self._clock() >= deadline:
                return False
            self._sleep(0.01)
        self._sleep(FOCUS_SETTLE_S)  # the window puts the caret back into its field
        return True


def _desktop_focus() -> Focus:
    return Focus(foreground=lambda: user32.GetForegroundWindow() or 0, is_ours=_is_ours,
                 window_class=_window_class, usable=_usable, activate=user32.SetForegroundWindow,
                 sleep=time.sleep, clock=time.monotonic)


def copy_text(text: str) -> bool:
    try:
        pyperclip.copy(text)
        return True
    except Exception as e:
        log.warning("clipboard is not available: %s", type(e).__name__)
        return False


class Paster:
    """The controller's `paster` port."""

    def __init__(self, focus: Focus | None = None):
        self._focus = focus if focus is not None else _desktop_focus()

    def note_foreground(self) -> None:
        """Called on every tick: remember the window the user is working in."""
        self._focus.note()

    def target_window(self) -> int:
        """The window a dictation would be typed into now (0: none); the overlay shows up on its monitor."""
        return self._focus.target()

    def paste(self, text: str, mode: str = "type") -> bool:
        if not self._focus.aim():
            log.warning("no window to put the text into")
            return False
        if mode == "paste":
            log.info("pasting into a window of class %s", _foreground_class())
            return paste_text(text, get_clip=pyperclip.paste, set_clip=pyperclip.copy,
                              modifiers_down=_modifiers_down, send_paste=_send_ctrl_v,
                              sleep=time.sleep, clock=time.monotonic)
        log.info("typing into a window of class %s", _foreground_class())
        ok = type_text(text, modifiers_down=_modifiers_down, send_units=_send_units,
                       sleep=time.sleep, clock=time.monotonic)
        if not ok:
            log.warning("the system did not take all the keystrokes")
        return ok
