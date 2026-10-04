"""Test doubles for the controller's ports and a rig that wires them."""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from tolmach.client import events as ev
from tolmach.client.controller import Controller, Ports
from tolmach.config import ClientConfig


def tone(amplitude: int = 3000, n: int = 1600) -> np.ndarray:
    """100 ms of a 440 Hz sine: about -24 dBFS at the default amplitude."""
    return (amplitude * np.sin(2 * np.pi * 440 * np.arange(n) / 16000)).astype(np.int16)


class FakeRecorder:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.fail_stop = False
        self.button_mic = False

    def on_button_microphone(self, microphone):
        return self.button_mic

    def start(self, session, microphone):
        if self.fail:
            raise ev.RecorderError("no such device")
        self.calls.append(("start", session, microphone))

    def stop(self):
        self.calls.append(("stop",))
        if self.fail_stop:
            raise RuntimeError("device vanished")


class FakeStream:
    def __init__(self, session):
        self.session = session
        self.sent = 0
        self.committed = False
        self.closed = False

    def send(self, samples):
        self.sent += int(samples.size)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class FakeStreams:
    def __init__(self):
        self.opened = []
        self.fail = False

    def open(self, session):
        if self.fail:
            raise ev.StreamError("connection refused")
        stream = FakeStream(session)
        self.opened.append(stream)
        return stream


class FakeOverlay:
    def __init__(self):
        self.calls = []

    def show(self, text):
        self.calls.append(("show", text))

    def message(self, text, seconds):
        self.calls.append(("message", text))

    def hide(self):
        self.calls.append(("hide",))

    @property
    def last(self):
        return self.calls[-1]

    def messages(self):
        return [call[1] for call in self.calls if call[0] == "message"]


class FakePaster:
    def __init__(self):
        self.pasted = []
        self.modes = []
        self.ok = True

    def paste(self, text, mode):
        self.pasted.append(text)
        self.modes.append(mode)
        return self.ok


class FakeMuter:
    def __init__(self):
        self.calls = []

    def mute(self):
        self.calls.append("mute")

    def unmute(self):
        self.calls.append("unmute")


class Rig:
    """A controller wired to fakes, with a hand-driven clock."""

    def __init__(self, **client_overrides):
        self.now = 100.0
        self.cfg = replace(ClientConfig(), **client_overrides)
        self.recorder = FakeRecorder()
        self.streams = FakeStreams()
        self.overlay = FakeOverlay()
        self.paster = FakePaster()
        self.muter = FakeMuter()
        self.saved = []
        self.states = []
        self.controller = Controller(Ports(
            load_config=lambda: self.cfg,
            recorder=self.recorder,
            streams=self.streams,
            overlay=self.overlay,
            paster=self.paster,
            muter=self.muter,
            clock=lambda: self.now,
            save_wav=lambda samples: self.saved.append(int(samples.size)),
            on_state=self.states.append,
        ))

    def send(self, *events):
        for event in events:
            assert self.controller.handle(event)

    def toggle(self):
        self.send(ev.Toggle("button"))

    def speak(self, session, seconds=1.0, amplitude=3000):
        """Feed `seconds` of audio as 100 ms chunks."""
        for _ in range(round(seconds * 10)):
            self.send(ev.AudioChunk(session, tone(amplitude)))

    def dictate(self, session, *phrases):
        """One full recording: start, a second of speech, phrases, stop."""
        self.toggle()
        self.speak(session)
        self.send(*[ev.Phrase(session, text) for text in phrases])
        self.toggle()
