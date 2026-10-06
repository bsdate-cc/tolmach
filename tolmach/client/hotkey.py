"""Global hotkeys through RegisterHotKey, each in its own message-loop thread.

The hotkeys that are held all the time get no keyboard hook: a toggle needs only the press,
and a hook would make every keystroke in the system wait for this process. The one key that
is ours only for a while - Esc, while a dictation can be cancelled - does get a hook for that
while: a program that takes the whole keyboard when it is in front (a remote-desktop client)
never lets a registered hotkey through. The hook looks at one thing, whether the key is that
key, and keeps nothing.
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
WM_HOTKEY, WM_TIMER, WM_QUIT = 0x0312, 0x0113, 0x0012
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_ARM = 0x8000 + 1  # WM_APP + 1, to the thread of a hotkey: wParam says take the key (1) or let it go (0)
WH_KEYBOARD_LL, HC_ACTION = 13, 0
PM_NOREMOVE = 0
HOTKEY_ID = 1
RELEASE_WAIT_S = 2.0  # how long a key that is being given back is waited for to come up
TICK_MS = 20  # how often a key is looked at while it is pressed, being given back or hooked
HOOK_EVERY_TICKS = 12  # the hook goes in anew this often: about four times a second

_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
_MOD_KEYS = {MOD_SHIFT: (0x10,), MOD_CONTROL: (0x11,), MOD_ALT: (0x12,), MOD_WIN: (0x5B, 0x5C)}
_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "pause": 0x13,
         "insert": 0x2D, "home": 0x24, "end": 0x23}

user32 = ctypes.windll.user32
_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
# Handles and pointers are wider than the int ctypes assumes.
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = _LRESULT
_module_handle = ctypes.windll.kernel32.GetModuleHandleW
_module_handle.argtypes = [wintypes.LPCWSTR]
_module_handle.restype = wintypes.HMODULE


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


def parse_hotkey(text: str, bare: bool = False) -> tuple[int, int]:
    """'shift+win+q' -> (modifier flags, virtual key code). A key without modifiers
    ('esc') is accepted only when `bare` says so."""
    parts = [part.strip().lower() for part in text.split("+")]
    if not all(parts) or (len(parts) < 2 and not bare):
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


def label(text: str) -> str:
    """A hotkey the way keys are written for a person: 'shift+win+q' -> 'Shift+Win+Q'."""
    return "+".join(part.strip().capitalize() for part in text.split("+") if part.strip())


def _toggle() -> ev.Toggle:
    return ev.Toggle("hotkey", time.monotonic())


class Hotkey:
    """Posts an event on every press: Toggle('hotkey') unless told otherwise. A taken or
    malformed hotkey is logged, not fatal; an empty one is simply off.

    `held=False` makes a key that is ours only between arm(True) and arm(False) and belongs
    to the other programs the rest of the time; `bare` lets it be a key without modifiers.
    Together: Esc, while there is a dictation to cancel. While it is ours, such a key is
    caught by a keyboard hook as well as registered, and kept from the program in front."""

    def __init__(self, post: Callable, hotkey: str, make_event: Callable[[], object] = _toggle, *,
                 bare: bool = False, held: bool = True):
        self._post = post
        self._text = hotkey.strip()
        self._make_event = make_event
        self._bare = bare
        self._held = held
        self._mods = self._vk = 0
        self._looping = False
        self._complained = False
        self._pressed = False  # the key has not been seen up since its press was passed on
        self._timer = 0
        self._ticks = 0
        # A key that is ours for a while:
        self._ours = False  # between arm(True) and the moment it is given back
        self._leaving: float | None = None  # given back as soon as it comes up - or at this time
        self._hook = None
        self._hook_proc = _HOOKPROC(self._on_key)  # kept alive for as long as Windows may call it
        self._hook_complained = False
        self._kept = False  # the press of the key was kept from the program in front: so is its release
        self._thread_id = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self.registered = False

    @property
    def text(self) -> str:
        """The hotkey as the settings spell it; "" for one that is switched off."""
        return self._text

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        # Only a running message loop can be ended: a taken, malformed or empty hotkey let
        # its thread go already, and there is nobody to post to.
        if self._looping and self._thread_id is not None:
            result = user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            if not result:
                log.warning("failed to post quit message to hotkey thread")
        self._thread.join(2.0)

    def arm(self, on: bool) -> None:
        """Take the key (True) or give it back to the other programs (False). Safe from any
        thread, and when nothing changes; a key that is off or unusable stays that way, and
        so does a hotkey that is held all the time."""
        if not self._held and self._looping and self._thread_id is not None:
            if not user32.PostThreadMessageW(self._thread_id, WM_ARM, 1 if on else 0, 0):
                log.warning("failed to reach the hotkey thread")

    # -- everything below runs on the hotkey's own thread

    def _register(self) -> bool:
        if not self.registered:
            if user32.RegisterHotKey(None, HOTKEY_ID, self._mods | MOD_NOREPEAT, self._vk):
                self.registered = True
            elif not self._complained:
                self._complained = True  # once, not at every dictation
                log.warning("hotkey %s is taken by another program", self._text)
        return self.registered

    def _unregister(self) -> None:
        if self.registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False

    def _key_is_up(self) -> bool:
        # A press the hook kept never reached Windows: asked about the key, Windows would
        # say "up" all the while it is held. Only the hook knows of such a press.
        return not self._kept and not user32.GetAsyncKeyState(self._vk) & 0x8000

    def _tick_on(self) -> bool:
        if not self._timer:
            self._timer = user32.SetTimer(None, 0, TICK_MS, None)
        return bool(self._timer)

    def _on_press(self) -> None:
        """One press, one event - until the key comes up. Windows keeps a held hotkey from
        repeating by itself (MOD_NOREPEAT), but was seen to forget that the key is held when
        another hotkey is registered or released meanwhile - and one of ours comes and goes
        with every dictation. So the key is watched here: a timer looks at it until it is up."""
        if self._pressed:
            return
        # Without a timer the key would never be seen to come up: then every press counts.
        self._pressed = self._tick_on()
        self._post(self._make_event())

    def _on_tick(self) -> None:
        self._ticks += 1
        up = self._key_is_up()
        if self._pressed and up:
            self._pressed = False
        if self._leaving is not None and (up or time.monotonic() >= self._leaving):
            self._give_back()
        if self._hook is not None and self._ticks % HOOK_EVERY_TICKS == 0:
            # Hooks are asked in the order of the latest first, and a program that takes the
            # keyboard puts its own in when it comes to the front: ours goes in anew, before it.
            self._hook_out()
            self._hook_in()
        if not self._pressed and self._leaving is None and self._hook is None and self._timer:
            user32.KillTimer(None, self._timer)
            self._timer = 0

    def _take(self) -> None:
        self._ours, self._leaving = True, None
        self._register()  # taken by another program? The hook does not depend on that.
        if self._hook is None:
            self._hook_in()
        if self._hook is not None:
            self._tick_on()

    def _let_go(self) -> None:
        """Give the key back - once it is up. Let go of while still held down, it would start
        repeating into the program in front: the very key that was meant for us."""
        if not self._ours:
            return
        if self._key_is_up() or not self._tick_on():
            self._give_back()
        else:
            self._leaving = time.monotonic() + RELEASE_WAIT_S

    def _give_back(self) -> None:
        self._ours, self._leaving, self._kept = False, None, False
        self._hook_out()
        self._unregister()

    def _hook_in(self) -> None:
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, _module_handle(None), 0) or None
        if self._hook is None and not self._hook_complained:
            self._hook_complained = True  # once: the registered hotkey still works where it can
            log.warning("the keyboard hook could not be put in")

    def _hook_out(self) -> None:
        if self._hook is not None:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _modifiers_fit(self) -> bool:
        """Exactly the modifiers of the hotkey are down, as a registered hotkey wants them."""
        for flag, keys in _MOD_KEYS.items():
            down = any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in keys)
            if down != bool(self._mods & flag):
                return False
        return True

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        """The keyboard hook: every key of the system passes here while the key is ours.
        Ours is kept - press, repeats and release; anything else goes on untouched, and so
        does everything if this code fails: the keyboard must never hang on our mistake."""
        try:
            if code == HC_ACTION:
                key = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if key.vkCode == self._vk:
                    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if self._kept:
                            return 1  # the key repeats while it is held: still ours, still one press
                        if self._modifiers_fit():
                            self._on_press()
                            self._kept = True
                            return 1
                    elif self._kept:
                        self._kept = False
                        return 1
        except Exception:
            log.exception("the keyboard hook failed")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        try:
            if not self._text:
                return
            try:
                self._mods, self._vk = parse_hotkey(self._text, self._bare)
            except ValueError as e:
                log.warning("hotkey is not usable: %s", e)
                return
            # Asking for a message gives the thread its queue: stop() and arm() post to it.
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
            if self._held and not self._register():
                return
            self._looping = True
        finally:
            self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self._on_press()
                elif msg.message == WM_TIMER:
                    self._on_tick()
                elif msg.message == WM_ARM:
                    if msg.wParam:
                        self._take()
                    else:
                        self._let_go()
        finally:
            self._looping = False
            self._hook_out()
            self._unregister()
