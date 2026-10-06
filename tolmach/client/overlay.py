"""Always-on-top text window. Tk lives in its own thread; commands arrive through a queue."""
from __future__ import annotations

import ctypes
import logging
import queue
import threading
import tkinter as tk
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("tolmach")

POLL_MS = 15  # "Слушаю…" is the cue to start speaking: it must not wait for a slow poll
MARGIN = 60
# A 3x3 grid inside the work area of the monitor; the setting client.overlay_position names one cell.
POSITIONS = ("top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right")
MONITOR_DEFAULTTOPRIMARY = 1
BACKGROUND = "#1e1e1e"
CROSS = "✕"  # the multiplication sign that windows use for "close"
CROSS_DIM, CROSS_LIT, CROSS_LIT_BACKGROUND = "#9a9a9a", "#ffffff", "#3c3c3c"

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

user32 = ctypes.windll.user32


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def place(position: str, work: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int]:
    """Top-left corner for a window of `size` at `position` inside the work area
    (left, top, right, bottom). An unknown position means the centre; a window larger
    than the area starts at the area's corner."""
    left, top, right, bottom = work
    width, height = size
    if "left" in position:
        x = left + MARGIN
    elif "right" in position:
        x = right - MARGIN - width
    else:
        x = left + (right - left - width) // 2
    if position.startswith("top"):
        y = top + MARGIN
    elif position.startswith("bottom"):
        y = bottom - MARGIN - height
    else:
        y = top + (bottom - top - height) // 2
    return max(left, min(x, right - width)), max(top, min(y, bottom - height))


def geometry(x: int, y: int) -> str:
    """Tk's spelling of a window position; a monitor left of (or above) the main one has
    negative coordinates, which Tk wants as "+-120"."""
    return f"+{x}+{y}"


def _work_area(hwnd: int) -> tuple[int, int, int, int] | None:
    """Work area (the screen minus the taskbar) of the monitor the window is on; the main
    monitor when there is no such window."""
    try:
        monitor_from_window = user32.MonitorFromWindow
        monitor_from_window.restype = wintypes.HMONITOR
        monitor_from_window.argtypes = [wintypes.HWND, wintypes.DWORD]
        get_monitor_info = user32.GetMonitorInfoW
        get_monitor_info.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
        info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
        if not get_monitor_info(monitor_from_window(hwnd or None, MONITOR_DEFAULTTOPRIMARY), ctypes.byref(info)):
            return None
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom
    except Exception:
        log.exception("could not read the monitor's work area")
        return None


def _foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def _restore_foreground(previous: int) -> None:
    """Give the focus back if the overlay took it from the window the user was working in."""
    if previous and user32.GetForegroundWindow() != previous:
        user32.SetForegroundWindow(previous)


def _make_no_activate(winfo_id: int) -> None:
    """Keep the window from being activated by showing or clicking it, and out of Alt+Tab."""
    hwnd = user32.GetParent(winfo_id) or winfo_id
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


class Overlay:
    """The controller's `overlay` port. Every public method is thread-safe and returns at once.

    The window shows up on the monitor of the window that gets the text (`target_window`
    says which; 0 means the main monitor), at the position set with set_position().

    show() is for what is in progress and can be cancelled: it carries a cross, and a click
    on the cross calls `on_cancel` (on the overlay's thread - it must only pass the word on).
    message() is a notice without a cross that goes away by itself - back to what is in
    progress when the caller says there is something, hidden otherwise."""

    def __init__(self, target_window: Callable[[], int] = lambda: 0,
                 on_cancel: Callable[[], None] = lambda: None) -> None:
        self._target_window = target_window
        self._on_cancel = on_cancel
        self._position = "center"
        self._queue: queue.Queue = queue.Queue()
        self._hide_job = None
        self._root = None
        self._label = None
        self._cross = None
        self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
        self._thread.start()

    def show(self, text: str) -> None:
        self._queue.put(("show", text, 0.0, None))

    def message(self, text: str, seconds: float, back_to: str | None = None) -> None:
        """Show `text` for `seconds`; then, unless something else was shown or hidden meanwhile,
        show `back_to` again as show() would - or hide, when there is nothing to go back to."""
        self._queue.put(("message", text, seconds, back_to))

    def hide(self) -> None:
        self._queue.put(("hide", "", 0.0, None))

    def set_position(self, position: str) -> None:
        """One of POSITIONS, from the settings; takes effect the next time something is shown."""
        if position in POSITIONS:
            self._position = position

    def close(self) -> None:
        self._queue.put(("close", "", 0.0, None))
        self._thread.join(2.0)

    def _run(self) -> None:
        try:
            previous = _foreground_window()
            self._root = tk.Tk()
            self._root.withdraw()
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            self._root.attributes("-alpha", 0.85)
            self._root.configure(bg=BACKGROUND)
            self._label = tk.Label(self._root, text="", font=("Segoe UI", 14), fg="#ffffff", bg=BACKGROUND,
                                   wraplength=500, justify="left", padx=16, pady=12)
            self._label.grid(row=0, column=0)
            self._cross = tk.Label(self._root, text=CROSS, font=("Segoe UI", 11), fg=CROSS_DIM, bg=BACKGROUND,
                                   padx=10, pady=6, cursor="hand2")
            self._cross.grid(row=0, column=1, sticky="ne")
            self._cross.bind("<Button-1>", self._clicked)
            self._cross.bind("<Enter>", lambda event: self._light(True))
            self._cross.bind("<Leave>", lambda event: self._light(False))
            self._root.update_idletasks()
            _make_no_activate(self._root.winfo_id())
            _restore_foreground(previous)
            self._poll()
            self._root.mainloop()
        except Exception:
            log.exception("overlay thread failed")
        finally:
            # Tk objects must die on the thread that made them, or the process aborts at exit.
            root, self._root, self._label, self._cross = self._root, None, None, None
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass

    def _clicked(self, event=None) -> None:
        try:
            self._on_cancel()
        except Exception:
            log.exception("the cross of the overlay could not pass the cancel on")

    def _light(self, on: bool) -> None:
        if self._cross is not None:
            self._cross.config(fg=CROSS_LIT if on else CROSS_DIM, bg=CROSS_LIT_BACKGROUND if on else BACKGROUND)

    def _poll(self) -> None:
        try:
            while True:
                command, text, seconds, back_to = self._queue.get_nowait()
                try:
                    if self._apply(command, text, seconds, back_to):
                        return  # closed: do not reschedule
                except Exception:
                    log.exception("overlay command %s failed", command)
        except queue.Empty:
            pass
        self._root.after(POLL_MS, self._poll)

    def _apply(self, command: str, text: str, seconds: float, back_to: str | None = None) -> bool:
        if command == "close":
            self._root.destroy()
            return True
        if self._hide_job is not None:
            self._root.after_cancel(self._hide_job)
            self._hide_job = None
        if command == "hide":
            self._root.withdraw()
            self._light(False)
            return False
        self._label.config(text=text)
        if command == "show":
            self._cross.grid()
        else:
            self._cross.grid_remove()
            self._light(False)  # it may have gone from under the pointer: not lit when it comes back
        # Placed first, shown second: a window shown at its old spot and then moved is a flicker.
        self._root.update_idletasks()
        work = _work_area(self._target_window()) or (0, 0, self._root.winfo_screenwidth(),
                                                     self._root.winfo_screenheight())
        spot = self._place(work, None)
        self._root.deiconify()
        self._root.update_idletasks()
        self._place(work, spot)  # in case the hidden window had not learned its new size yet
        if command == "message":
            self._hide_job = self._root.after(int(seconds * 1000), lambda: self._after_message(back_to))
        return False

    def _place(self, work: tuple[int, int, int, int], current: str | None) -> str:
        spot = geometry(*place(self._position, work, (self._root.winfo_reqwidth(), self._root.winfo_reqheight())))
        if spot != current:
            self._root.geometry(spot)
        return spot

    def _after_message(self, back_to: str | None) -> None:
        self._hide_job = None
        if back_to is None:
            self._root.withdraw()
        else:
            self._apply("show", back_to, 0.0)
