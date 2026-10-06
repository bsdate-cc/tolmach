import logging
import threading
import time

from tolmach.client import overlay as ov


class FakeUser32:
    def __init__(self, foreground=100, parent=200, style=0x10):
        self.foreground = foreground
        self.parent = parent
        self.style = style
        self.set_foreground_calls = []
        self.style_writes = []

    def GetForegroundWindow(self):
        return self.foreground

    def SetForegroundWindow(self, hwnd):
        self.set_foreground_calls.append(hwnd)
        return 1

    def GetParent(self, hwnd):
        return self.parent

    def GetWindowLongW(self, hwnd, index):
        assert index == -20
        return self.style

    def SetWindowLongW(self, hwnd, index, value):
        assert index == -20
        self.style_writes.append((hwnd, value))
        self.style = value
        return 0


def test_restore_foreground_only_when_changed_and_previous_known(monkeypatch):
    fake = FakeUser32(foreground=100)
    monkeypatch.setattr(ov, "user32", fake)
    ov._restore_foreground(100)  # unchanged
    ov._restore_foreground(0)    # nothing remembered
    assert fake.set_foreground_calls == []
    fake.foreground = 999        # overlay took the focus
    ov._restore_foreground(100)
    assert fake.set_foreground_calls == [100]


def test_foreground_window_returns_handle(monkeypatch):
    monkeypatch.setattr(ov, "user32", FakeUser32(foreground=42))
    assert ov._foreground_window() == 42


def test_no_activate_style_is_or_ed_into_the_top_level_window(monkeypatch):
    fake = FakeUser32(parent=200, style=0x10)
    monkeypatch.setattr(ov, "user32", fake)
    ov._make_no_activate(555)
    assert fake.style_writes == [(200, 0x10 | 0x08000000 | 0x00000080)]


def test_no_activate_falls_back_to_winfo_id_when_no_parent(monkeypatch):
    fake = FakeUser32(parent=0)
    monkeypatch.setattr(ov, "user32", fake)
    ov._make_no_activate(555)
    assert fake.style_writes[0][0] == 555


class FakeLabel:
    made = []

    def __init__(self, *a, **k):
        self.text = k.get("text", "")
        self.bound = {}
        FakeLabel.made.append(self)

    def grid(self, **k):
        pass

    def grid_remove(self):
        pass

    def bind(self, sequence, handler):
        self.bound[sequence] = handler

    def config(self, **k):
        pass


class FakeTkModule:
    """Just enough of tkinter for Overlay._run; mainloop runs scheduled callbacks until destroy()."""

    Label = FakeLabel

    def __init__(self, events):
        self.events = events
        module = self

        class Tk:
            def __init__(self):
                events.append("Tk()")
                self.after_jobs = []
                self.destroyed = False

            def withdraw(self): events.append("withdraw")
            def overrideredirect(self, flag): pass
            def attributes(self, *a): pass
            def configure(self, **k): pass
            def update_idletasks(self): events.append("update_idletasks")
            def winfo_id(self): return 555
            def after_cancel(self, job): pass

            def after(self, ms, fn):
                self.after_jobs.append(fn)
                return fn

            def mainloop(self):
                while not self.destroyed:
                    jobs, self.after_jobs = self.after_jobs, []
                    for job in jobs:
                        job()
                    time.sleep(0.001)

            def destroy(self):
                events.append(("destroy", threading.current_thread().name))
                self.destroyed = True

        self.Tk = Tk


def test_run_sets_up_window_then_cleans_up_on_its_own_thread(monkeypatch):
    events = []
    monkeypatch.setattr(ov, "tk", FakeTkModule(events))
    fake = FakeUser32(foreground=100, parent=200)
    monkeypatch.setattr(ov, "user32", fake)
    monkeypatch.setattr(ov, "_restore_foreground", lambda previous: events.append(("restore", previous)))
    monkeypatch.setattr(ov, "_make_no_activate", lambda hwnd: events.append(("no_activate", hwnd)))
    overlay = ov.Overlay()
    overlay.show("hello")
    overlay.close()
    assert not overlay._thread.is_alive()
    assert overlay._root is None and overlay._label is None
    assert ("destroy", "overlay") in events
    assert events.index("update_idletasks") < events.index(("no_activate", 555)) < events.index(("restore", 100))
    assert events.index("Tk()") > -1


def test_tk_failure_is_logged_and_does_not_escape_the_thread(monkeypatch, caplog):
    class BrokenTk:
        Label = FakeLabel

        @staticmethod
        def Tk():
            raise RuntimeError("no display")

    monkeypatch.setattr(ov, "tk", BrokenTk)
    monkeypatch.setattr(ov, "user32", FakeUser32())
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args))
    with caplog.at_level(logging.ERROR, logger="tolmach"):
        overlay = ov.Overlay()
        overlay._thread.join(2.0)
    assert not overlay._thread.is_alive()
    assert errors == []
    assert any("overlay" in r.getMessage() for r in caplog.records)
    overlay.close()  # must not hang or raise


# --- the cross: a click on it asks to cancel what is in progress


def overlay_with_a_cross(monkeypatch, on_cancel):
    FakeLabel.made = []
    monkeypatch.setattr(ov, "tk", FakeTkModule([]))
    monkeypatch.setattr(ov, "user32", FakeUser32())
    overlay = ov.Overlay(on_cancel=on_cancel)
    overlay.show("Слушаю…")
    deadline = time.monotonic() + 2
    while len(FakeLabel.made) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    crosses = [label for label in FakeLabel.made if label.text == ov.CROSS]
    assert len(crosses) == 1
    return overlay, crosses[0]


def test_a_click_on_the_cross_asks_to_cancel(monkeypatch):
    asked = []
    overlay, cross = overlay_with_a_cross(monkeypatch, lambda: asked.append("cancel"))
    cross.bound["<Button-1>"](None)
    overlay.close()
    assert asked == ["cancel"]


def test_a_cancel_that_fails_is_logged_and_the_window_lives_on(monkeypatch, caplog):
    def broken():
        raise RuntimeError("the queue is gone")

    overlay, cross = overlay_with_a_cross(monkeypatch, broken)
    with caplog.at_level(logging.ERROR, logger="tolmach"):
        cross.bound["<Button-1>"](None)
    assert overlay._thread.is_alive()
    assert any("cross" in r.getMessage() for r in caplog.records)
    overlay.close()
    assert not overlay._thread.is_alive()
