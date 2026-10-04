import queue
import threading
import time

import pytest

from tolmach.client import events as ev
from tolmach.client.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse_hotkey, Hotkey, WM_HOTKEY, WM_QUIT


def test_default_hotkey():
    assert parse_hotkey("shift+win+q") == (MOD_SHIFT | MOD_WIN, 0x51)


def test_case_spaces_and_function_keys():
    assert parse_hotkey("Ctrl + Alt + F9") == (MOD_CONTROL | MOD_ALT, 0x78)


def test_named_keys_and_digits():
    assert parse_hotkey("ctrl+space") == (MOD_CONTROL, 0x20)
    assert parse_hotkey("control+win+1") == (MOD_CONTROL | MOD_WIN, 0x31)


@pytest.mark.parametrize("text", ["q", "shift+", "+q", "hyper+q", "shift+ю", "shift+f25", "shift+qq", ""])
def test_rejects_unusable_hotkeys(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_malformed_hotkey():
    """Malformed hotkey: start() returns, registered is False, stop() does not raise."""
    events = []
    hk = Hotkey(events.append, "invalid+hotkey+string+with+too+many+parts")
    hk.start()
    assert hk.registered is False
    hk.stop()  # Should not raise


def test_registrar_fails(monkeypatch):
    """RegisterHotKey fails: start() returns, registered is False, nothing is posted."""
    events = []

    class FakeUser32:
        def RegisterHotKey(self, hwnd, id, mods, vk):
            return False  # Simulate failure

        def UnregisterHotKey(self, hwnd, id):
            return True

        def PostThreadMessageW(self, tid, msg, wparam, lparam):
            return True

        def GetMessageW(self, msg, hwnd, min_msg, max_msg):
            return 0  # No more messages

        def PeekMessageW(self, msg, hwnd, min_msg, max_msg, flags):
            return 0

    monkeypatch.setattr("tolmach.client.hotkey.user32", FakeUser32())

    hk = Hotkey(events.append, "shift+alt+q")
    hk.start()
    assert hk.registered is False
    assert events == []
    hk.stop()


def test_hotkey_posts_toggle(monkeypatch):
    """WM_HOTKEY message posts Toggle('hotkey'), stop() ends thread."""
    events = []
    msg_queue = queue.Queue()

    class FakeUser32:
        def RegisterHotKey(self, hwnd, id, mods, vk):
            return True  # Success

        def UnregisterHotKey(self, hwnd, id):
            return True

        def PostThreadMessageW(self, tid, msg, wparam, lparam):
            msg_queue.put((msg, wparam, lparam))
            return True

        def GetMessageW(self, msg_ptr, hwnd, min_msg, max_msg):
            try:
                msg_tuple = msg_queue.get(timeout=2.0)
                if msg_tuple[0] == WM_QUIT:
                    return 0  # Exit loop
                # Populate msg_ptr with message data
                # msg_ptr is a ctypes reference; access the underlying object
                msg_obj = msg_ptr._obj
                msg_obj.message = msg_tuple[0]
                msg_obj.wParam = msg_tuple[1]
                msg_obj.lParam = msg_tuple[2]
                return 1  # Message received
            except queue.Empty:
                return 0

        def PeekMessageW(self, msg, hwnd, min_msg, max_msg, flags):
            return 0  # No message peeked

    monkeypatch.setattr("tolmach.client.hotkey.user32", FakeUser32())

    hk = Hotkey(events.append, "shift+alt+q")
    hk.start()
    assert hk.registered is True

    # Simulate hotkey press by queuing a WM_HOTKEY message
    msg_queue.put((WM_HOTKEY, 1, 0))

    # Give the thread time to process
    threading.Event().wait(0.1)

    # Verify Toggle event was posted
    assert len(events) == 1
    assert isinstance(events[0], ev.Toggle)
    assert events[0].source == "hotkey"

    # Stop should cleanly end the thread
    hk.stop()
    assert hk.registered is False


# --- a hotkey can post any event, and an empty one is simply off


def test_a_hotkey_posts_the_event_it_was_given(monkeypatch):
    events = []
    msg_queue = queue.Queue()

    class FakeUser32:
        def RegisterHotKey(self, hwnd, id, mods, vk):
            return True

        def UnregisterHotKey(self, hwnd, id):
            return True

        def PostThreadMessageW(self, tid, msg, wparam, lparam):
            msg_queue.put((msg, wparam, lparam))
            return True

        def GetMessageW(self, msg_ptr, hwnd, min_msg, max_msg):
            try:
                message, wparam, lparam = msg_queue.get(timeout=2.0)
            except queue.Empty:
                return 0
            if message == WM_QUIT:
                return 0
            msg_ptr._obj.message, msg_ptr._obj.wParam, msg_ptr._obj.lParam = message, wparam, lparam
            return 1

        def PeekMessageW(self, msg, hwnd, min_msg, max_msg, flags):
            return 0

    monkeypatch.setattr("tolmach.client.hotkey.user32", FakeUser32())
    hk = Hotkey(events.append, "shift+win+z", make_event=lambda: ev.InsertLast("hotkey"))
    hk.start()
    msg_queue.put((WM_HOTKEY, 1, 0))
    deadline = time.monotonic() + 2
    while not events and time.monotonic() < deadline:
        time.sleep(0.01)
    hk.stop()
    assert events == [ev.InsertLast("hotkey")]


def test_an_empty_hotkey_is_off_without_a_complaint(caplog):
    events = []
    with caplog.at_level("WARNING", logger="tolmach"):
        hk = Hotkey(events.append, "")
        hk.start()
        hk.stop()
    assert hk.registered is False
    assert caplog.records == []
