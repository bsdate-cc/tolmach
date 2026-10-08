"""The models beside the main one.

Such a model is in memory only while somebody needs it: it is loaded when it is first asked
for and let go of when nobody has asked for a while - a model is hundreds of megabytes. One at
a time: a request for another one waits until the one in memory is free, and takes its place.
"""
from __future__ import annotations

import gc
import threading
import time
from typing import Callable

import numpy as np

from tolmach.config import ModelConfig

Recognize = Callable[[np.ndarray], str]


class UnknownModel(Exception):
    """The settings name no such model."""


class NotInstalled(Exception):
    """The settings name the model, but its files are not on this computer."""


class ModelFailed(Exception):
    """The model is there and could not be loaded; the message says why."""


class Extras:
    """`load(model)` gives the recognizer of a model - seconds of work; `installed(model)` says
    whether its files are there. `idle_s` is how long a model that nobody uses stays in memory."""

    def __init__(self, models: list[ModelConfig], load: Callable[[ModelConfig], Recognize], idle_s: float, *,
                 installed: Callable[[ModelConfig], bool], clock: Callable[[], float] = time.monotonic) -> None:
        self._models = {model.name: model for model in models}
        self._load, self._installed, self._idle_s, self._clock = load, installed, idle_s, clock
        self._cv = threading.Condition()
        self._name: str | None = None           # the model in memory, or the one being loaded
        self._recognize: Recognize | None = None
        self._users = 0                         # how many requests are using it right now
        self._free_since = 0.0

    def known(self, name: str) -> bool:
        return name in self._models

    def loaded(self) -> str | None:
        """The name of the model in memory, if there is one."""
        with self._cv:
            return self._name if self._recognize is not None else None

    def loading(self) -> str | None:
        """The name of the model that is being loaded right now, if one is."""
        with self._cv:
            return self._name if self._recognize is None else None

    def told(self) -> list[dict]:
        """Every model, for whoever asks which ones there are."""
        now = self.loaded()
        return [{"id": name, "language": model.language, "installed": self._installed(model), "loaded": name == now}
                for name, model in self._models.items()]

    def acquire(self, name: str) -> Recognize:
        """The recognizer of a model, loaded first if it has to be; the caller says release()
        when it is done. Waits while the model is being loaded for somebody else, or while
        another model is in use. UnknownModel, NotInstalled or ModelFailed if it cannot be had."""
        model = self._models.get(name)
        if model is None:
            raise UnknownModel(name)
        if not self._installed(model):
            raise NotInstalled(name)
        with self._cv:
            while True:
                if self._name == name and self._recognize is not None:
                    self._users += 1
                    return self._recognize
                if self._name is None or (self._name != name and self._recognize is not None and self._users == 0):
                    break                   # nothing in the way: this request loads it
                self._cv.wait()
            self._drop()
            self._name = name
        try:
            recognize = self._load(model)       # outside the lock: it takes seconds
        except Exception as e:
            with self._cv:
                self._name = None
                self._cv.notify_all()
            raise ModelFailed(str(e)) from e
        with self._cv:
            self._recognize, self._users = recognize, 1
            self._cv.notify_all()
            return recognize

    def release(self, name: str) -> None:
        with self._cv:
            self._users -= 1
            self._free_since = self._clock()
            self._cv.notify_all()

    def sweep(self) -> str | None:
        """Let go of the model in memory if nobody has used it for long enough; its name if so."""
        with self._cv:
            if self._recognize is None or self._users > 0 or self._clock() - self._free_since < self._idle_s:
                return None
            name = self._name
            self._drop()
            self._cv.notify_all()
        gc.collect()        # the memory of the model goes back now, not at some later collection
        return name

    def _drop(self) -> None:
        self._name = self._recognize = None
