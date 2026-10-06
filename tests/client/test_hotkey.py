import ctypes
import queue
import threading
import time
from ctypes import wintypes

import pytest

from tolmach.client import events as ev
from tolmach.client.hotkey import (MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, parse_hotkey, Hotkey,
                                   WM_HOTKEY, WM_QUIT)


@pytest.fixture(autouse=True)
def no_hotkey_outlives_its_test(monkeypatch):
    """Every hotkey a test starts is stopped when the test is over, passed or not - and before
    the fake of Windows is taken away: a thread left running would go on with the real one."""
    started = []
    start = Hotkey.start

    def remembered(self):
        started.append(self)
        start(self)

    monkeypatch.setattr(Hotkey, "start", remembered)
    yield
    for hotkey in started:
        hotkey.stop()
    assert [hotkey for hotkey in started if hotkey._thread.is_alive()] == []


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

        def SetTimer(self, hwnd, id, ms, proc):
            return 0  # no timer in this fake

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

        def SetTimer(self, hwnd, id, ms, proc):
            return 0  # no timer in this fake

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


WM_TIMER = 0x0113
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
MODIFIER_KEYS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
VK_ESCAPE, VK_A = 0x1B, 0x41


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


# --- a key without modifiers, taken only for a while: Esc cancels a dictation and is an ordinary key otherwise


class QueueUser32:
    """RegisterHotKey always succeeds unless `taken`; thread messages go through a queue."""

    def __init__(self, taken=False):
        self.taken = taken
        self.down = False           # whether the key is physically held down
        self.modifiers_down = set() # virtual keys of the modifiers held down
        self.asked = set()          # the keys whose state was asked for
        self.ticking = False        # whether a timer is running
        self.messages = queue.Queue()
        self.calls = []
        self.hook_proc = None       # the keyboard hook that is in, if any
        self.hook_calls = []        # "in" / "out", in the order they came
        self.hook_refused = False
        self.passed_on = 0          # key events the hook did not keep

    def RegisterHotKey(self, hwnd, id, mods, vk):
        self.calls.append(("register", mods, vk))
        return not self.taken

    def UnregisterHotKey(self, hwnd, id):
        self.calls.append(("unregister",))
        return True

    def PostThreadMessageW(self, tid, msg, wparam, lparam):
        self.messages.put((msg, wparam, lparam))
        return True

    def GetMessageW(self, msg_ptr, hwnd, min_msg, max_msg):
        try:
            message, wparam, lparam = self.messages.get(timeout=2.0)
        except queue.Empty:
            return 0
        if message == WM_QUIT:
            return 0
        if message == WM_TIMER:
            self._tick()
        msg_ptr._obj.message, msg_ptr._obj.wParam, msg_ptr._obj.lParam = message, wparam, lparam
        return 1

    def PeekMessageW(self, msg, hwnd, min_msg, max_msg, flags):
        return 0

    def SetTimer(self, hwnd, id, ms, proc):
        self.ticking = True
        self._tick()
        return 1

    def KillTimer(self, hwnd, id):
        self.ticking = False
        return True

    def _tick(self):
        """The next WM_TIMER a few milliseconds from now, for as long as the timer lives."""
        def fire():
            if self.ticking:
                self.messages.put((WM_TIMER, 1, 0))

        threading.Timer(0.005, fire).start()

    def GetAsyncKeyState(self, vk):
        self.asked.add(vk)
        if vk in MODIFIER_KEYS:
            return 0x8000 if vk in self.modifiers_down else 0
        return 0x8000 if self.down else 0

    def SetWindowsHookExW(self, kind, proc, module, thread):
        if self.hook_refused:
            return 0
        self.hook_proc = proc
        self.hook_calls.append("in")
        return 77

    def UnhookWindowsHookEx(self, handle):
        self.hook_proc = None
        self.hook_calls.append("out")
        return 1

    def CallNextHookEx(self, handle, code, wparam, lparam):
        self.passed_on += 1
        return 0

    def key(self, vk, down=True):
        """A key event as the keyboard hook gets it; True when the hook kept it to itself."""
        event = KBDLLHOOKSTRUCT(vkCode=vk)
        return self.hook_proc(0, 0x0100 if down else 0x0101, ctypes.addressof(event)) == 1

    def press(self):
        self.messages.put((WM_HOTKEY, 1, 0))


def wait_for(condition, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert condition()


def cancel_key(events, text="esc"):
    return Hotkey(events.append, text, lambda: ev.Cancel("hotkey"), bare=True, held=False)


def test_a_key_may_come_without_modifiers_only_where_that_is_asked_for():
    assert parse_hotkey("esc", bare=True) == (0, 0x1B)
    assert parse_hotkey("ctrl+esc", bare=True) == (MOD_CONTROL, 0x1B)
    with pytest.raises(ValueError):
        parse_hotkey("esc")
    with pytest.raises(ValueError):
        parse_hotkey("", bare=True)
    with pytest.raises(ValueError):
        parse_hotkey("ю", bare=True)


def test_a_key_that_is_not_held_is_taken_only_while_armed(monkeypatch):
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    events = []
    hk = cancel_key(events)
    hk.start()
    assert hk.registered is False and fake.calls == []        # Esc still belongs to the other programs
    hk.arm(True)
    wait_for(lambda: hk.registered)
    assert fake.calls == [("register", MOD_NOREPEAT, 0x1B)]
    fake.press()
    wait_for(lambda: events)
    assert events == [ev.Cancel("hotkey")]
    hk.arm(False)
    wait_for(lambda: not hk.registered)
    assert fake.calls[-1] == ("unregister",)
    hk.stop()


def test_arming_twice_takes_the_key_once_and_letting_go_twice_is_harmless(monkeypatch):
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    hk = cancel_key([])
    hk.start()
    hk.arm(True)
    hk.arm(True)
    hk.arm(False)
    hk.arm(False)
    hk.arm(True)
    wait_for(lambda: len(fake.calls) == 3)
    assert fake.calls == [("register", MOD_NOREPEAT, 0x1B), ("unregister",), ("register", MOD_NOREPEAT, 0x1B)]
    hk.stop()
    assert hk.registered is False
    assert fake.calls[-1] == ("unregister",)                  # a stop gives an armed key back


def test_a_key_another_program_holds_is_complained_about_once(monkeypatch, caplog):
    fake = QueueUser32(taken=True)
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    events = []
    hk = cancel_key(events)
    hk.start()
    with caplog.at_level("WARNING", logger="tolmach"):
        for _ in range(3):                                    # three dictations
            hk.arm(True)
            hk.arm(False)
        wait_for(lambda: len(fake.calls) == 3)
        hk.stop()
    assert hk.registered is False and events == []
    assert [r.getMessage() for r in caplog.records] == ["hotkey esc is taken by another program"]


def test_a_key_that_cannot_be_read_or_is_switched_off_is_never_taken(monkeypatch, caplog):
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    with caplog.at_level("WARNING", logger="tolmach"):
        for text in ("", "escape key"):
            hk = cancel_key([], text)
            hk.start()
            hk.arm(True)
            hk.arm(False)
            hk.stop()
            assert hk.registered is False
    assert fake.calls == []
    assert len(caplog.records) == 1 and "hotkey is not usable" in caplog.records[0].getMessage()


def test_a_key_still_held_down_is_given_back_only_when_it_comes_up(monkeypatch):
    # Given back while it is down, Esc would start repeating into the program in front.
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    hk = cancel_key([])
    hk.start()
    hk.arm(True)
    wait_for(lambda: hk.registered)
    fake.down = True
    hk.arm(False)
    time.sleep(0.1)
    assert hk.registered is True
    assert VK_ESCAPE in fake.asked
    fake.down = False
    wait_for(lambda: not hk.registered)
    hk.stop()


def test_a_key_that_never_comes_up_is_given_back_all_the_same(monkeypatch):
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    monkeypatch.setattr("tolmach.client.hotkey.RELEASE_WAIT_S", 0.05)
    hk = cancel_key([])
    hk.start()
    hk.arm(True)
    wait_for(lambda: hk.registered)
    fake.down = True                                          # a stuck key, or a report that lies
    hk.arm(False)
    wait_for(lambda: not hk.registered)
    hk.stop()


# --- a key held down is one press, however many times Windows reports it


def test_a_hotkey_held_down_counts_once_until_it_comes_up(monkeypatch):
    # Windows does not repeat a held hotkey by itself (MOD_NOREPEAT) - but it was seen to
    # forget that the key is held when another hotkey is registered or released meanwhile,
    # and ours come and go with every dictation: the held key is then reported again and again.
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    events = []
    hk = Hotkey(events.append, "shift+win+q")
    hk.start()
    fake.down = True
    for _ in range(5):                                        # the key repeats while it is held
        fake.press()
        time.sleep(0.03)
    assert len(events) == 1
    fake.down = False                                         # released
    time.sleep(0.05)
    fake.down = True                                          # and pressed again
    fake.press()
    wait_for(lambda: len(events) == 2)
    hk.stop()


def test_a_tap_too_short_to_be_seen_down_does_not_block_the_next_one(monkeypatch):
    fake = QueueUser32()                                      # the key is never seen down
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    events = []
    hk = Hotkey(events.append, "shift+win+q")
    hk.start()
    fake.press()
    wait_for(lambda: len(events) == 1)
    time.sleep(0.05)
    fake.press()
    wait_for(lambda: len(events) == 2)
    hk.stop()


def test_without_a_timer_every_press_still_counts(monkeypatch):
    # No timer, no way to learn that the key came up: better a repeat than a hotkey gone dead.
    fake = QueueUser32()
    fake.SetTimer = lambda hwnd, id, ms, proc: 0
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    events = []
    hk = Hotkey(events.append, "shift+win+q")
    hk.start()
    fake.down = True
    fake.press()
    fake.press()
    wait_for(lambda: len(events) == 2)
    hk.stop()


# --- a program that takes the whole keyboard when it is in front (a remote-desktop client) never lets
# --- a registered hotkey through: while the key is ours, a keyboard hook catches it as well


def armed(monkeypatch, events, fake=None, text="esc"):
    fake = fake or QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    hk = cancel_key(events, text)
    hk.start()
    hk.arm(True)
    wait_for(lambda: fake.hook_proc is not None)
    return hk, fake


def test_while_the_key_is_ours_a_keyboard_hook_catches_it_and_keeps_it(monkeypatch):
    events = []
    hk, fake = armed(monkeypatch, events)
    assert fake.key(VK_ESCAPE) is True                        # kept: the program in front never sees it
    assert events == [ev.Cancel("hotkey")]
    assert fake.key(VK_ESCAPE, down=False) is True            # and neither its release
    assert fake.key(VK_A) is False and fake.key(VK_A, down=False) is False
    assert fake.passed_on == 2                                # every other key goes on as it came
    assert events == [ev.Cancel("hotkey")]
    hk.stop()


def test_the_hook_is_there_only_while_the_key_is_ours(monkeypatch):
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    hk = cancel_key([])
    hk.start()
    time.sleep(0.05)
    assert fake.hook_calls == []
    hk.arm(True)
    wait_for(lambda: fake.hook_proc is not None)
    hk.arm(False)
    wait_for(lambda: fake.hook_proc is None)
    count = len(fake.hook_calls)
    time.sleep(0.15)
    assert len(fake.hook_calls) == count and fake.hook_calls[-1] == "out"
    assert fake.ticking is False                              # and nothing keeps ticking
    hk.stop()


def test_the_hook_is_put_in_anew_again_and_again(monkeypatch):
    # A program that puts its own hook in later stands before ours: ours goes in afresh, to the front.
    hk, fake = armed(monkeypatch, [])
    wait_for(lambda: fake.hook_calls.count("in") >= 3)
    assert fake.hook_calls[:5] == ["in", "out", "in", "out", "in"]
    hk.stop()
    assert fake.hook_calls[-1] == "out"


def test_a_key_held_down_through_the_hook_is_one_press(monkeypatch):
    # A press the hook keeps never reaches Windows: asked about the key, Windows says "up" all along.
    events = []
    hk, fake = armed(monkeypatch, events)
    for _ in range(4):                                        # the key repeats while it is held
        assert fake.key(VK_ESCAPE) is True
        time.sleep(0.03)
    assert events == [ev.Cancel("hotkey")]
    assert fake.key(VK_ESCAPE, down=False) is True
    time.sleep(0.03)
    assert fake.key(VK_ESCAPE) is True                        # pressed again
    assert events == [ev.Cancel("hotkey")] * 2
    hk.stop()


def test_with_other_modifiers_down_it_is_another_key(monkeypatch):
    events = []
    hk, fake = armed(monkeypatch, events)
    fake.modifiers_down = {VK_CONTROL, VK_SHIFT}              # Ctrl+Shift+Esc: the task manager
    assert fake.key(VK_ESCAPE) is False
    assert fake.key(VK_ESCAPE, down=False) is False
    assert events == []
    hk.stop()


def test_a_key_with_modifiers_wants_exactly_those(monkeypatch):
    events = []
    hk, fake = armed(monkeypatch, events, text="ctrl+esc")
    assert fake.key(VK_ESCAPE) is False                       # bare
    fake.modifiers_down = {VK_CONTROL, VK_MENU}
    assert fake.key(VK_ESCAPE) is False                       # one too many
    fake.modifiers_down = {VK_CONTROL}
    assert fake.key(VK_ESCAPE) is True
    assert events == [ev.Cancel("hotkey")]
    hk.stop()


def test_a_kept_key_stays_kept_when_its_modifier_is_let_go_first(monkeypatch):
    # Ctrl+Esc, and Ctrl comes up while Esc is still down: its repeats and its release are ours all the same.
    events = []
    hk, fake = armed(monkeypatch, events, text="ctrl+esc")
    fake.modifiers_down = {VK_CONTROL}
    assert fake.key(VK_ESCAPE) is True
    fake.modifiers_down = set()
    assert fake.key(VK_ESCAPE) is True
    assert fake.key(VK_ESCAPE, down=False) is True
    assert events == [ev.Cancel("hotkey")]
    hk.stop()


def test_the_release_of_a_press_that_was_not_kept_is_not_kept(monkeypatch):
    # Esc went down before the key became ours: the program in front got the press and gets the release.
    hk, fake = armed(monkeypatch, [])
    assert fake.key(VK_ESCAPE, down=False) is False
    hk.stop()


def test_the_hook_stays_until_a_key_it_keeps_comes_up(monkeypatch):
    events = []
    hk, fake = armed(monkeypatch, events)
    assert fake.key(VK_ESCAPE) is True                        # down, and kept: Windows knows nothing of it
    hk.arm(False)
    time.sleep(0.1)
    assert fake.hook_proc is not None and fake.key(VK_ESCAPE) is True     # a repeat: still kept, no leak
    assert fake.key(VK_ESCAPE, down=False) is True            # up
    wait_for(lambda: fake.hook_proc is None and not hk.registered)
    assert events == [ev.Cancel("hotkey")]
    hk.stop()


def test_a_kept_key_that_never_comes_up_is_given_back_all_the_same(monkeypatch):
    monkeypatch.setattr("tolmach.client.hotkey.RELEASE_WAIT_S", 0.05)
    hk, fake = armed(monkeypatch, [])
    assert fake.key(VK_ESCAPE) is True
    hk.arm(False)
    wait_for(lambda: fake.hook_proc is None and not hk.registered)
    hk.arm(True)                                              # and the next time it is ours it works anew
    wait_for(lambda: fake.hook_proc is not None)
    assert fake.key(VK_ESCAPE, down=False) is False           # that late release is not ours any more
    hk.stop()


def test_a_key_another_program_registered_is_still_caught_by_the_hook(monkeypatch):
    events = []
    hk, fake = armed(monkeypatch, events, QueueUser32(taken=True))
    assert hk.registered is False
    assert fake.key(VK_ESCAPE) is True
    assert fake.key(VK_ESCAPE, down=False) is True
    assert events == [ev.Cancel("hotkey")]
    hk.arm(False)
    wait_for(lambda: fake.hook_proc is None)
    hk.stop()


def test_a_hook_that_cannot_be_put_in_is_complained_about_once(monkeypatch, caplog):
    fake = QueueUser32()
    fake.hook_refused = True
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    events = []
    hk = cancel_key(events)
    hk.start()
    with caplog.at_level("WARNING", logger="tolmach"):
        for _ in range(3):
            hk.arm(True)
            wait_for(lambda: hk.registered)
            hk.arm(False)
            wait_for(lambda: not hk.registered)
        hk.arm(True)
        wait_for(lambda: hk.registered)
        fake.press()                                          # the registered hotkey still works
        wait_for(lambda: events)
        hk.stop()
    assert [r.getMessage() for r in caplog.records] == ["the keyboard hook could not be put in"]


def test_a_failure_inside_the_hook_lets_the_key_go_on(monkeypatch, caplog):
    def broken(event):
        raise RuntimeError("the queue is gone")

    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    hk = Hotkey(broken, "esc", lambda: ev.Cancel("hotkey"), bare=True, held=False)
    hk.start()
    hk.arm(True)
    wait_for(lambda: fake.hook_proc is not None)
    with caplog.at_level("ERROR", logger="tolmach"):
        assert fake.key(VK_ESCAPE) is False
    assert fake.passed_on == 1
    assert any("keyboard hook" in r.getMessage() for r in caplog.records)
    hk.stop()


def test_a_hotkey_held_all_the_time_never_gets_a_hook_and_is_never_given_back(monkeypatch):
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    hk = Hotkey([].append, "shift+win+q")
    hk.start()
    hk.arm(True)
    hk.arm(False)
    time.sleep(0.1)
    assert hk.registered is True
    assert fake.hook_calls == [] and fake.calls == [("register", MOD_SHIFT | MOD_WIN | MOD_NOREPEAT, 0x51)]
    hk.stop()


def test_a_hotkey_a_test_forgot_to_stop_is_stopped_for_it(monkeypatch):
    fake = QueueUser32()
    monkeypatch.setattr("tolmach.client.hotkey.user32", fake)
    hk = cancel_key([])
    hk.start()
    hk.arm(True)
    wait_for(lambda: fake.hook_proc is not None)
    # no hk.stop(): the fixture above stops it, and says so if it could not


# --- a hotkey the way it is shown to a person


def test_a_hotkey_is_shown_the_way_keys_are_written():
    from tolmach.client.hotkey import label

    assert label("shift+win+q") == "Shift+Win+Q"
    assert label(" ctrl + alt + f9 ") == "Ctrl+Alt+F9"
    assert label("esc") == "Esc"
    assert label("") == ""
