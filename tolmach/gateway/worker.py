"""One recognition thread for the whole gateway: finals in order, then the freshest draft.

A single thread on purpose: the engine already uses `threads` cores per decode,
and two decodes at once would only slow each other down.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Job:
    session: str
    item: int  # phrase number inside the session, from 1
    final: bool
    samples: np.ndarray
    deliver: Callable[["Job", str], None]  # runs on the worker thread
    # A phrase of an uploaded file: runs only when no dictation needs the engine,
    # so a ten-minute file cannot hold up a live session.
    background: bool = False
    # The recognizer of another model, for a file that asked for one; None - the main model.
    recognize: Callable[[np.ndarray], str] | None = None


class Worker:
    def __init__(self, recognize: Callable[[np.ndarray], str]) -> None:
        self._recognize = recognize
        self._finals: deque[Job] = deque()
        self._drafts: dict[str, Job] = {}  # session -> its one pending draft
        self._background: deque[Job] = deque()
        self._closed: dict[str, int] = {}  # session -> highest item that has a final
        self._cv = threading.Condition()
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="recognizer", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, job: Job) -> None:
        with self._cv:
            if job.background:
                self._background.append(job)
            elif job.final:
                self._finals.append(job)
                self._closed[job.session] = max(job.item, self._closed.get(job.session, 0))
                pending = self._drafts.get(job.session)
                if pending is not None and pending.item <= job.item:
                    del self._drafts[job.session]
            elif job.item <= self._closed.get(job.session, 0):
                return
            else:
                self._drafts[job.session] = job
            self._cv.notify()

    def forget(self, session: str) -> None:
        """Drop everything queued for a session that went away."""
        with self._cv:
            self._finals = deque(j for j in self._finals if j.session != session)
            self._background = deque(j for j in self._background if j.session != session)
            self._drafts.pop(session, None)
            self._closed.pop(session, None)

    def stop(self) -> None:
        with self._cv:
            self._stopping = True
            self._cv.notify()
        self._thread.join(timeout=10)

    def _next(self) -> Job | None:
        with self._cv:
            while not self._stopping:
                if self._finals:
                    return self._finals.popleft()
                if self._drafts:
                    return self._drafts.pop(next(iter(self._drafts)))
                if self._background:
                    return self._background.popleft()
                self._cv.wait()
            return None

    def _stale(self, job: Job) -> bool:
        with self._cv:
            return not job.final and job.item <= self._closed.get(job.session, 0)

    def _run(self) -> None:
        while (job := self._next()) is not None:
            self._do(job)
            # Not held while the thread waits for the next one: a job holds what recognizes it,
            # and a model beside the main one must leave memory when it is let go of.
            del job

    def _do(self, job: Job) -> None:
        try:
            text = (job.recognize or self._recognize)(job.samples)
        except Exception:
            log.exception("recognition failed")  # no text and no audio in the log
            text = ""
        if self._stale(job):
            return  # the phrase closed while this draft was being computed
        try:
            job.deliver(job, text)
        except Exception:
            log.exception("delivering a result failed")
