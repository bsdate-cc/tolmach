from tolmach.client.paste import paste_text


class Env:
    """A fake desktop: a clipboard, held modifier keys and a clock that only sleep() moves."""

    def __init__(self, previous="старое", held_for=0.0):
        self.clip = previous
        self.now = 0.0
        self.held_until = held_for
        self.steps = []
        self.pasted_at = None
        self.fail_set = False
        self.fail_get = False

    def get_clip(self):
        if self.fail_get:
            raise RuntimeError("clipboard is locked")
        return self.clip

    def set_clip(self, text):
        if self.fail_set:
            raise RuntimeError("clipboard is locked")
        self.clip = text
        self.steps.append(("set", text))

    def send_paste(self):
        self.pasted_at = self.now
        self.steps.append(("ctrl+v", self.clip))

    def sleep(self, seconds):
        self.now += seconds

    def run(self, text):
        return paste_text(text, get_clip=self.get_clip, set_clip=self.set_clip,
                          modifiers_down=lambda: self.now < self.held_until,
                          send_paste=self.send_paste, sleep=self.sleep, clock=lambda: self.now)


def test_pastes_and_restores_previous_text():
    env = Env(previous="старое")
    assert env.run("новое") is True
    assert env.steps == [("set", "новое"), ("ctrl+v", "новое"), ("set", "старое")]
    assert env.clip == "старое"


def test_empty_previous_clipboard_is_not_restored():
    env = Env(previous="")
    assert env.run("новое") is True
    assert env.steps == [("set", "новое"), ("ctrl+v", "новое")]
    assert env.clip == "новое"


def test_waits_for_modifiers_to_be_released():
    env = Env(held_for=0.5)
    env.run("новое")
    assert 0.5 <= env.pasted_at < 0.7


def test_gives_up_waiting_after_one_second():
    env = Env(held_for=10.0)
    assert env.run("новое") is True
    assert 1.0 <= env.pasted_at < 1.2


def test_clipboard_failure_reports_false():
    env = Env()
    env.fail_set = True
    assert env.run("новое") is False
    assert env.steps == []


def test_unreadable_clipboard_still_pastes():
    env = Env()
    env.fail_get = True
    assert env.run("новое") is True
    assert env.steps == [("set", "новое"), ("ctrl+v", "новое")]


def test_ctrl_v_is_sent_with_scan_codes(monkeypatch):
    """A remote-desktop client forwards scan codes; a key event with scan code 0 never reaches the remote app."""
    from tolmach.client import paste

    sent = []

    class FakeUser32:
        def MapVirtualKeyW(self, vk, kind):
            assert kind == 0                      # MAPVK_VK_TO_VSC
            return {0x11: 0x1D, 0x56: 0x2F}[vk]

        def keybd_event(self, vk, scan, flags, extra):
            sent.append((vk, scan, flags))

    monkeypatch.setattr(paste, "user32", FakeUser32())
    paste._send_ctrl_v()
    assert sent == [(0x11, 0x1D, 0), (0x56, 0x2F, 0), (0x56, 0x2F, 2), (0x11, 0x1D, 2)]


# --- typing the text as Unicode keystrokes: no clipboard, and it reaches a remote desktop ---

from tolmach.client import paste as paste_module
from tolmach.client.paste import type_text, typed_units


def test_typed_units_are_utf16_code_units():
    assert typed_units("Да") == [0x0414, 0x0430]
    assert typed_units("😀") == [0xD83D, 0xDE00]       # a surrogate pair stays a pair


def test_line_breaks_are_typed_as_spaces():
    """Enter in a chat window sends the message; a dictation must never press it."""
    assert typed_units("раз\nдва\r\nтри") == typed_units("раз два три")


class TypingEnv:
    def __init__(self, held_for=0.0, fail_on_call=None):
        self.now = 0.0
        self.held_until = held_for
        self.sent = []
        self.first_send_at = None
        self.fail_on_call = fail_on_call

    def modifiers_down(self):
        return self.now < self.held_until

    def send_units(self, units):
        if self.first_send_at is None:
            self.first_send_at = self.now
        self.sent.append(list(units))
        return self.fail_on_call != len(self.sent)

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def run(self, text):
        return type_text(text, modifiers_down=self.modifiers_down, send_units=self.send_units,
                         sleep=self.sleep, clock=self.clock)


def test_long_text_is_typed_in_chunks_in_order():
    env = TypingEnv()
    text = "а" * 100
    assert env.run(text) is True
    assert [len(chunk) for chunk in env.sent] == [40, 40, 20]
    assert [unit for chunk in env.sent for unit in chunk] == typed_units(text)


def test_typing_waits_for_the_modifier_keys_to_be_released():
    env = TypingEnv(held_for=0.5)
    assert env.run("привет") is True
    assert 0.5 <= env.first_send_at < 0.7


def test_typing_gives_up_waiting_after_one_second():
    env = TypingEnv(held_for=60.0)
    assert env.run("привет") is True
    assert 1.0 <= env.first_send_at < 1.2


def test_a_refused_chunk_stops_the_typing_and_reports_failure():
    env = TypingEnv(fail_on_call=2)
    assert env.run("а" * 100) is False
    assert len(env.sent) == 2


def test_send_units_presses_and_releases_every_unit(monkeypatch):
    calls = []

    class FakeUser32:
        def SendInput(self, count, array, size):
            calls.append([(array[i].ki.wVk, array[i].ki.wScan, array[i].ki.dwFlags) for i in range(count)])
            return count

    monkeypatch.setattr(paste_module, "user32", FakeUser32())
    assert paste_module._send_units([0x0414, 0x0430]) is True
    assert calls == [[(0, 0x0414, 4), (0, 0x0414, 6), (0, 0x0430, 4), (0, 0x0430, 6)]]


def test_send_units_reports_keystrokes_the_system_did_not_take(monkeypatch):
    class FakeUser32:
        def SendInput(self, count, array, size):
            return 0

    monkeypatch.setattr(paste_module, "user32", FakeUser32())
    assert paste_module._send_units([0x0414]) is False


def test_the_paster_types_by_default_and_never_touches_the_clipboard(monkeypatch):
    typed = []

    def no_clipboard(*args):
        raise AssertionError("typing must not touch the clipboard")

    monkeypatch.setattr(paste_module.pyperclip, "copy", no_clipboard)
    monkeypatch.setattr(paste_module.pyperclip, "paste", no_clipboard)
    monkeypatch.setattr(paste_module, "_modifiers_down", lambda: False)
    monkeypatch.setattr(paste_module, "_send_units", lambda units: typed.extend(units) or True)
    monkeypatch.setattr(paste_module, "_foreground_class", lambda: "Test")
    monkeypatch.setattr(paste_module.time, "sleep", lambda seconds: None)
    assert paste_module.Paster(focus=AlwaysInFront()).paste("Да", "type") is True
    assert typed == [0x0414, 0x0430]


# --- which window gets the text

class AlwaysInFront:
    def aim(self):
        return True


OURS, NOTEPAD, BROWSER, TASKBAR = 1, 10, 20, 30
CLASSES = {OURS: "TolmachSystemTrayIcon", NOTEPAD: "Notepad", BROWSER: "Chrome_WidgetWin_1", TASKBAR: "Shell_TrayWnd"}


class Desktop:
    """Windows of a fake desktop: one in front, some closed, a switch that may be refused."""

    def __init__(self, front):
        self.front = front
        self.closed = set()
        self.refuse = False
        self.activated = []
        self.now = 0.0
        self.focus = paste_module.Focus(
            foreground=lambda: self.front, is_ours=lambda hwnd: hwnd == OURS,
            window_class=lambda hwnd: CLASSES[hwnd], usable=lambda hwnd: hwnd not in self.closed,
            activate=self.activate, sleep=self.sleep, clock=lambda: self.now)

    def activate(self, hwnd):
        self.activated.append(hwnd)
        if not self.refuse:
            self.front = hwnd

    def sleep(self, seconds):
        self.now += seconds


def test_text_goes_to_the_window_in_front():
    desk = Desktop(front=NOTEPAD)
    assert desk.focus.aim() is True
    assert desk.activated == []


def test_after_a_click_on_our_tray_menu_the_text_goes_to_the_window_used_before():
    desk = Desktop(front=NOTEPAD)
    desk.focus.note()
    desk.front = OURS                 # the menu left the icon's hidden window in front
    desk.focus.note()                 # ticks go on while the menu is open
    assert desk.focus.aim() is True
    assert desk.activated == [NOTEPAD] and desk.front == NOTEPAD


def test_the_latest_window_is_the_one_remembered():
    desk = Desktop(front=NOTEPAD)
    desk.focus.note()
    desk.front = BROWSER
    desk.focus.note()
    desk.front = OURS
    assert desk.focus.aim() is True
    assert desk.front == BROWSER


def test_the_taskbar_is_never_remembered_as_the_window_to_type_into():
    desk = Desktop(front=NOTEPAD)
    desk.focus.note()
    desk.front = TASKBAR              # the click on the tray icon passes through the taskbar
    desk.focus.note()
    desk.front = OURS
    assert desk.focus.aim() is True
    assert desk.front == NOTEPAD


def test_with_the_taskbar_in_front_the_remembered_window_is_tried():
    # Windows may refuse this switch (the taskbar belongs to another process); then nothing is typed.
    desk = Desktop(front=NOTEPAD)
    desk.focus.note()
    desk.front = TASKBAR
    assert desk.focus.aim() is True
    assert desk.front == NOTEPAD


def test_with_no_window_remembered_there_is_nowhere_to_type():
    desk = Desktop(front=OURS)
    desk.focus.note()
    assert desk.focus.aim() is False
    assert desk.activated == []


def test_a_window_closed_in_the_meantime_is_not_typed_into():
    desk = Desktop(front=NOTEPAD)
    desk.focus.note()
    desk.front = OURS
    desk.closed.add(NOTEPAD)
    assert desk.focus.aim() is False
    assert desk.activated == []


def test_a_refused_switch_is_reported_after_a_short_wait():
    desk = Desktop(front=NOTEPAD)
    desk.focus.note()
    desk.front = OURS
    desk.refuse = True
    assert desk.focus.aim() is False
    assert paste_module.FOCUS_WAIT_S <= desk.now < paste_module.FOCUS_WAIT_S + 0.2


def test_no_window_in_front_at_all_counts_as_ours():
    desk = Desktop(front=NOTEPAD)
    desk.focus.note()
    desk.front = 0
    desk.focus.note()
    assert desk.focus.aim() is True
    assert desk.front == NOTEPAD


def test_the_paster_types_nothing_when_there_is_no_window_for_the_text(monkeypatch, caplog):
    typed = []

    class NowhereToType:
        def aim(self):
            return False

    monkeypatch.setattr(paste_module, "_send_units", lambda units: typed.extend(units) or True)
    with caplog.at_level("WARNING", logger="tolmach"):
        assert paste_module.Paster(focus=NowhereToType()).paste("Да", "type") is False
        assert paste_module.Paster(focus=NowhereToType()).paste("Да", "paste") is False
    assert typed == []
    assert "no window" in caplog.text


# --- the window the text would go to right now (the overlay shows up on its monitor)

def test_the_target_is_the_window_in_front():
    desk = Desktop(front=NOTEPAD)
    assert desk.focus.target() == NOTEPAD


def test_the_target_behind_our_own_window_is_the_one_remembered():
    desk = Desktop(front=BROWSER)
    desk.focus.note()
    desk.front = OURS
    assert desk.focus.target() == BROWSER
    assert desk.activated == []                      # asking does not switch windows


def test_there_is_no_target_when_nothing_usable_is_remembered():
    desk = Desktop(front=OURS)
    assert desk.focus.target() == 0
    desk.front = NOTEPAD
    desk.focus.note()
    desk.front = TASKBAR
    desk.closed.add(NOTEPAD)
    assert desk.focus.target() == 0
