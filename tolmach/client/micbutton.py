"""USB microphone button over HID: every press toggles dictation.

The button also mutes the microphone in hardware and the report carries no
state, so a press is all we learn; the controller's floor check
keeps the program and the microphone in step.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import hid

from tolmach.client import events as ev

log = logging.getLogger("tolmach")

USAGE_PAGE_CONSUMER = 0x0C
RETRY_S = 3.0
READ_TIMEOUT_MS = 500


def is_press(report, mask: int) -> bool:
    """A press is a report whose LAST byte has a mask bit set.

    That covers both shapes hidapi may hand over: [0x80] and [0x00, 0x80]
    (with a report-id byte). All-zero reports are releases, an empty one is
    a read timeout.
    """
    return bool(report) and bool(report[-1] & mask)


def find_device(devices: list[dict]) -> dict | None:
    """The Consumer Control interface among the HID interfaces of the microphone."""
    for device in devices:
        if device.get("usage_page") == USAGE_PAGE_CONSUMER:
            return device
    return None


def find_path(devices: list[dict]) -> bytes | None:
    device = find_device(devices)
    return None if device is None else device["path"]


class MicButton:
    """Posts Toggle('button') on every press; searches again every 3 s while the device is absent.

    When the device shows up after having been away, the controller is told with
    ButtonConnected: what it believed about the microphone may no longer hold."""

    def __init__(self, post: Callable, vid: str, pid: str, mask: int):
        self._post = post
        self._vid = int(vid, 16)
        self._pid = int(pid, 16)
        self._mask = mask
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="micbutton", daemon=True)
        self.found = False
        self.product = ""  # USB product name while found: it names the microphone to record from
        self._absent = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(2.0)

    def _open(self):
        try:
            info = find_device(hid.enumerate(self._vid, self._pid))
            if info is None:
                return None
            device = hid.device()
            device.open_path(info["path"])
            self.product = info.get("product_string") or ""
            return device
        except (OSError, ValueError) as e:
            log.debug("microphone button is not available: %s", e)
            return None

    def _run(self) -> None:
        # Always pause before the next attempt: a device that opens but fails
        # every read would otherwise be reopened in a busy loop.
        while not self._stopping.is_set():
            try:
                self._serve()
            except Exception:
                log.exception("microphone button failed; retrying")
            self._stopping.wait(RETRY_S)

    def _serve(self) -> None:
        """Open the button and read it until it is lost or the button is stopped."""
        device = self._open()
        if device is None:
            self._absent = True
            return
        self.found = True
        log.info("microphone button found")
        try:
            if self._absent:
                self._absent = False
                self._post(ev.ButtonConnected())
            while not self._stopping.is_set():
                report = device.read(8, READ_TIMEOUT_MS)
                if is_press(report, self._mask):
                    self._post(ev.Toggle("button", time.monotonic()))
        except (OSError, ValueError) as e:
            log.warning("microphone button lost: %s", e)
            self._absent = True
        finally:
            self.found = False
            self.product = ""
            try:
                device.close()
            except Exception:
                pass
