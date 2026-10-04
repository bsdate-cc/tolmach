import queue
import threading

import numpy as np
import pytest

from tolmach.gateway.worker import Job, Worker

AUDIO = np.zeros(1600, dtype=np.float32)


class Gate:
    """A recognize() that blocks until released, so tests can queue jobs behind it."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def __call__(self, samples):
        self.calls.append(len(samples))
        self.entered.set()
        assert self.release.wait(5)
        return f"len{len(samples)}"


@pytest.fixture
def delivered():
    return queue.Queue()


@pytest.fixture
def make_job(delivered):
    def make(session, item, final, n=1600):
        return Job(session, item, final, np.zeros(n, dtype=np.float32),
                   lambda job, text: delivered.put((job.session, job.item, job.final, text)))
    return make


def drain(q, count):
    return [q.get(timeout=5) for _ in range(count)]


def test_finals_keep_their_order_and_beat_drafts(delivered, make_job):
    gate = Gate()
    w = Worker(gate)
    w.start()
    w.submit(make_job("c", 1, False, n=1000))      # occupies the worker
    assert gate.entered.wait(5)
    w.submit(make_job("b", 1, False, n=2000))      # a draft, queued first
    w.submit(make_job("a", 2, True, n=3000))
    w.submit(make_job("b", 2, True, n=4000))
    gate.release.set()
    got = drain(delivered, 3)
    w.stop()
    # c's draft was already running and nothing closed its phrase; then both
    # finals in the order they came. b's queued draft was dropped by b's final.
    assert got == [("c", 1, False, "len1000"), ("a", 2, True, "len3000"), ("b", 2, True, "len4000")]
    assert delivered.empty()


def test_newer_draft_replaces_the_pending_one(delivered, make_job):
    gate = Gate()
    w = Worker(gate)
    w.start()
    w.submit(make_job("a", 1, True, n=1000))
    assert gate.entered.wait(5)
    w.submit(make_job("a", 2, False, n=2000))
    w.submit(make_job("a", 2, False, n=3000))
    gate.release.set()
    got = drain(delivered, 2)
    w.stop()
    assert got == [("a", 1, True, "len1000"), ("a", 2, False, "len3000")]
    assert gate.calls == [1000, 3000]


def test_draft_of_a_closed_phrase_is_refused(delivered, make_job):
    w = Worker(lambda s: "x")
    w.start()
    w.submit(make_job("a", 1, True))
    assert drain(delivered, 1) == [("a", 1, True, "x")]
    w.submit(make_job("a", 1, False))
    w.submit(make_job("a", 2, False))
    assert drain(delivered, 1) == [("a", 2, False, "x")]
    w.stop()
    assert delivered.empty()


def test_draft_result_is_dropped_when_its_phrase_closed_meanwhile(delivered, make_job):
    gate = Gate()
    w = Worker(gate)
    w.start()
    w.submit(make_job("a", 1, False, n=1000))
    assert gate.entered.wait(5)                     # the draft is being recognized
    w.submit(make_job("a", 1, True, n=2000))        # the phrase closes meanwhile
    gate.release.set()
    got = drain(delivered, 1)
    w.stop()
    assert got == [("a", 1, True, "len2000")]
    assert delivered.empty()


def test_forget_drops_everything_queued_for_a_session(delivered, make_job):
    gate = Gate()
    w = Worker(gate)
    w.start()
    w.submit(make_job("busy", 1, True, n=1000))
    assert gate.entered.wait(5)
    w.submit(make_job("gone", 1, True))
    w.submit(make_job("gone", 2, False))
    w.submit(make_job("kept", 1, True, n=2000))
    w.forget("gone")
    gate.release.set()
    got = drain(delivered, 2)
    w.stop()
    assert got == [("busy", 1, True, "len1000"), ("kept", 1, True, "len2000")]
    assert delivered.empty()


def test_failed_recognition_still_delivers_a_final(delivered, make_job):
    def boom(samples):
        raise RuntimeError("engine fell over")

    w = Worker(boom)
    w.start()
    w.submit(make_job("a", 1, True))
    w.submit(make_job("a", 2, True))
    assert drain(delivered, 2) == [("a", 1, True, ""), ("a", 2, True, "")]
    w.stop()


def test_a_deliver_that_raises_does_not_kill_the_worker(delivered, make_job):
    w = Worker(lambda s: "x")
    w.start()

    def bad(job, text):
        raise RuntimeError("socket is gone")

    w.submit(Job("a", 1, True, AUDIO, bad))
    w.submit(make_job("b", 1, True))
    assert drain(delivered, 1) == [("b", 1, True, "x")]
    w.stop()


def test_stop_joins_an_idle_worker():
    w = Worker(lambda s: "x")
    w.start()
    w.stop()
    assert not w._thread.is_alive()


def test_background_jobs_wait_for_live_work(delivered, make_job):
    gate = Gate()
    w = Worker(gate)
    w.start()
    w.submit(make_job("busy", 1, True, n=1000))
    assert gate.entered.wait(5)

    def note(job, text):
        delivered.put((job.session, job.item, job.final, text))

    w.submit(Job("file", 1, True, np.zeros(2000, dtype=np.float32), note, background=True))
    w.submit(Job("file", 2, True, np.zeros(3000, dtype=np.float32), note, background=True))
    w.submit(make_job("talk", 1, False, n=4000))
    w.submit(make_job("live", 1, True, n=5000))
    gate.release.set()
    got = drain(delivered, 5)
    w.stop()
    # a file's phrases run only when no dictation needs the engine
    assert got == [("busy", 1, True, "len1000"), ("live", 1, True, "len5000"), ("talk", 1, False, "len4000"),
                   ("file", 1, True, "len2000"), ("file", 2, True, "len3000")]


def test_forget_drops_background_jobs_too(delivered, make_job):
    gate = Gate()
    w = Worker(gate)
    w.start()
    w.submit(make_job("busy", 1, True, n=1000))
    assert gate.entered.wait(5)
    w.submit(Job("file", 1, True, AUDIO, lambda job, text: delivered.put("the file ran"), background=True))
    w.forget("file")
    gate.release.set()
    assert drain(delivered, 1) == [("busy", 1, True, "len1000")]
    w.stop()
    assert delivered.empty()
