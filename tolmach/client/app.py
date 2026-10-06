"""Wires the dictation client: one queue, the controller thread, producers and ports."""
from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from tolmach import config, paths
from tolmach.client import events as ev
from tolmach.client import mutesound, recorder
from tolmach.client.controller import CANCELLABLE, Controller, Ports, button_rules
from tolmach.client.devicewatch import DeviceWatch
from tolmach.client.hotkey import Hotkey
from tolmach.client.micbutton import MicButton
from tolmach.client.overlay import Overlay
from tolmach.client.paste import Paster
from tolmach.client.recorder import Recorder
from tolmach.client.stream import StreamFactory

log = logging.getLogger("tolmach")

TICK_S = 0.5


def save_failed_wav(samples: np.ndarray) -> Path:
    """Keep a recording the gateway never finished."""
    stem = time.strftime("%Y%m%d-%H%M%S")
    number = 1
    while True:
        path = paths.failed_dir() / (stem + ("" if number == 1 else f"-{number}") + ".wav")
        try:
            f = open(path, "xb")  # exclusive: never overwrite an earlier recording
            break
        except FileExistsError:
            number += 1
    with f, wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.astype("<i2").tobytes())
    log.warning("unfinished recording saved to %s", path)
    return path


def load_client_config() -> config.ClientConfig:
    """Client settings, re-read before every recording; problems reach the log once per file change."""
    return config.load_reported().config.client


class NullMuter:
    def mute(self) -> None:
        pass

    def unmute(self) -> None:
        pass


class ClientApp:
    def __init__(self, on_state: Callable[[str], None] = lambda state: None):
        self._on_state = on_state
        self._events: queue.Queue = queue.Queue()
        post = self._events.put
        self._paster = Paster()
        self.overlay = Overlay(target_window=self._paster.target_window,
                               on_cancel=lambda: post(ev.Cancel("overlay")))
        self._watch = DeviceWatch()
        self._recorder = Recorder(post, list_is_fresh=lambda: self._watch.active and not self._watch.pending())
        self.controller = Controller(Ports(
            load_config=self._load_config,
            recorder=self._recorder,
            streams=StreamFactory(post),
            overlay=self.overlay,
            paster=self._paster,
            muter=mutesound.Muter(),
            clock=time.monotonic,
            save_wav=save_failed_wav,
            on_state=self._state_changed,
        ), self._events)
        client_cfg = config.load().config.client
        self.overlay.set_position(client_cfg.overlay_position)
        self._hotkey = Hotkey(post, client_cfg.hotkey)
        self._insert_hotkey = Hotkey(post, client_cfg.insert_last_hotkey, lambda: ev.InsertLast("hotkey"))
        self._cancel_hotkey = Hotkey(post, client_cfg.cancel_hotkey, lambda: ev.Cancel("hotkey"),
                                     bare=True, held=False)
        try:
            self._button = MicButton(post, client_cfg.button.vid, client_cfg.button.pid, client_cfg.button.mask)
        except ValueError as e:
            log.warning("config: client.button is not usable: %s", e)
            self._button = None
        self._stopping = threading.Event()
        self._controller_thread = threading.Thread(target=self.controller.run, name="controller", daemon=True)
        self._ticker_thread = threading.Thread(target=self._tick, name="ticker", daemon=True)

    def _load_config(self) -> config.ClientConfig:
        # Pressing the button on one microphone and being recorded from another gives
        # "nothing recognised": with no microphone chosen, the button's own one is used.
        self._recorder.prefer(self._button.product if self.button_found else "")
        cfg = load_client_config()
        self.overlay.set_position(cfg.overlay_position)
        return cfg

    def _state_changed(self, state: str) -> None:
        # The key that cancels is ours only while there is something to cancel; the rest of
        # the time Esc belongs to the other programs.
        self._cancel_hotkey.arm(state in CANCELLABLE)
        self._on_state(state)

    @property
    def state(self) -> str:
        return self.controller.state

    @property
    def last_text(self) -> str:
        return self.controller.last_text

    @property
    def hotkey_registered(self) -> bool:
        return self._hotkey.registered

    @property
    def hotkeys(self) -> dict[str, str]:
        """The hotkeys that are ours now, by what they do; "" for one that is off or taken."""
        return {"dictation": self._hotkey.text if self._hotkey.registered else "",
                "insert_last": self._insert_hotkey.text if self._insert_hotkey.registered else ""}

    def button_rules(self) -> bool:
        """Whether the microphone button - not the hotkey or the menu item - would start a
        dictation now."""
        cfg = self._load_config()
        return button_rules(cfg.control, self._recorder.on_button_microphone(cfg.microphone))

    @property
    def button_found(self) -> bool:
        return self._button is not None and self._button.found

    def microphones(self, periodic: bool) -> list[str] | None:
        """Names of the input devices if the list has just been rebuilt, else None. It is
        rebuilt when Windows reported a change; without notifications, on every `periodic` call."""
        return self._watch.refresh(periodic, recorder.refresh_devices)

    def start(self) -> None:
        mutesound.restore()
        self._watch.start()
        self._controller_thread.start()
        self._ticker_thread.start()
        self._hotkey.start()
        self._insert_hotkey.start()
        self._cancel_hotkey.start()
        if self._button is not None:
            self._button.start()

    def insert_last(self, source: str = "menu") -> None:
        self._events.put(ev.InsertLast(source))

    def toggle(self, source: str = "menu") -> None:
        self._events.put(ev.Toggle(source, time.monotonic()))

    def stop(self) -> None:
        self._stopping.set()
        self._hotkey.stop()
        self._insert_hotkey.stop()
        self._cancel_hotkey.stop()
        if self._button is not None:
            self._button.stop()
        self._events.put(ev.Quit())
        self._controller_thread.join(5.0)
        self._watch.stop()
        self.overlay.close()

    def _tick(self) -> None:
        while not self._stopping.wait(TICK_S):
            self._events.put(ev.Tick())
            try:
                self._paster.note_foreground()
            except Exception:
                log.exception("could not look at the foreground window")
