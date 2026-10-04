import pytest

from tolmach.client.micbutton import MicButton, find_device, find_path, is_press

MASK = 0x80


def test_is_press_single_byte_report():
    assert is_press([0x80], MASK) is True


def test_is_press_report_with_id_byte():
    assert is_press([0x00, 0x80], MASK) is True
    assert is_press(b"\x00\x80", MASK) is True


def test_is_press_release_report():
    assert is_press([0x00, 0x00], MASK) is False
    assert is_press([0x00], MASK) is False


def test_is_press_empty_report_from_a_read_timeout():
    assert is_press([], MASK) is False


def test_is_press_ignores_other_buttons():
    assert is_press([0x00, 0x01], MASK) is False   # volume down
    assert is_press([0x00, 0x40], MASK) is False   # speaker mute
    assert is_press([0x80, 0x00], MASK) is False   # the bit must be in the last byte


def test_is_press_with_another_bit_alongside():
    assert is_press([0x00, 0x81], MASK) is True


def test_find_path_picks_the_consumer_control_interface():
    devices = [
        {"path": b"audio-control", "usage_page": 0xFF00, "usage": 1},
        {"path": b"consumer", "usage_page": 0x0C, "usage": 1},
    ]
    assert find_path(devices) == b"consumer"


def test_find_path_without_a_match():
    assert find_path([]) is None
    assert find_path([{"path": b"x", "usage_page": 0x01, "usage": 6}]) is None


def test_bad_ids_raise_value_error():
    with pytest.raises(ValueError):
        MicButton(lambda event: None, "zzzz", "2008", MASK)


# --- the read loop, with a faked hid module ---

import threading
import time
from types import SimpleNamespace

from tolmach.client import micbutton


class FakeDevice:
    opens = 0

    def __init__(self, read):
        self._read = read

    def open_path(self, path):
        FakeDevice.opens += 1

    def read(self, size, timeout_ms):
        return self._read()

    def close(self):
        pass


def fake_hid(read):
    FakeDevice.opens = 0
    return SimpleNamespace(enumerate=lambda vid, pid: [{"path": b"consumer", "usage_page": 0x0C}],
                           device=lambda: FakeDevice(read))


def test_a_device_that_fails_every_read_is_reopened_only_after_the_retry_interval(monkeypatch):
    def broken():
        raise OSError("read error")

    monkeypatch.setattr(micbutton, "hid", fake_hid(broken))
    monkeypatch.setattr(micbutton, "RETRY_S", 0.05)
    button = MicButton(lambda event: None, "1B3F", "2008", MASK)
    button.start()
    time.sleep(0.3)
    button.stop()
    assert 1 <= FakeDevice.opens <= 10


def test_an_unexpected_error_is_logged_and_the_button_keeps_working(monkeypatch, caplog):
    posts = []

    def press():
        time.sleep(0.01)
        return [0x80]

    def post(event):
        posts.append(event)
        if len(posts) == 1:
            raise RuntimeError("queue is gone")

    monkeypatch.setattr(micbutton, "hid", fake_hid(press))
    monkeypatch.setattr(micbutton, "RETRY_S", 0.05)
    button = MicButton(post, "1B3F", "2008", MASK)
    with caplog.at_level("ERROR", logger="tolmach"):
        button.start()
        deadline = time.monotonic() + 2
        while len(posts) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        alive = button._thread.is_alive()
        button.stop()
    assert alive and len(posts) >= 3
    assert any(r.exc_info for r in caplog.records)


def test_find_device_returns_the_consumer_control_interface_with_its_product_name():
    devices = [{"path": b"vendor", "usage_page": 0xFF00, "product_string": "Usb Audio Device"},
               {"path": b"consumer", "usage_page": 0x0C, "product_string": "Usb Audio Device"}]
    assert find_device(devices)["path"] == b"consumer"
    assert find_device([]) is None


def test_the_button_exposes_its_product_name_while_it_is_found(monkeypatch):
    def idle():
        time.sleep(0.01)
        return []

    fake = fake_hid(idle)
    fake.enumerate = lambda vid, pid: [{"path": b"consumer", "usage_page": 0x0C, "product_string": "Usb Audio Device"}]
    monkeypatch.setattr(micbutton, "hid", fake)
    monkeypatch.setattr(micbutton, "RETRY_S", 0.05)
    button = MicButton(lambda event: None, "1B3F", "2008", MASK)
    assert button.product == ""
    button.start()
    for _ in range(100):
        if button.found:
            break
        time.sleep(0.01)
    assert button.found and button.product == "Usb Audio Device"
    button.stop()
    assert not button.found and button.product == ""


# --- what the button tells the controller besides the presses

from tolmach.client import events as ev


def run_button(monkeypatch, fake, until, seconds=2.0):
    posts = []
    monkeypatch.setattr(micbutton, "hid", fake)
    monkeypatch.setattr(micbutton, "RETRY_S", 0.02)
    button = MicButton(posts.append, "1B3F", "2008", MASK)
    button.start()
    deadline = time.monotonic() + seconds
    while not until(posts) and time.monotonic() < deadline:
        time.sleep(0.01)
    button.stop()
    return posts


def test_a_press_carries_the_moment_it_happened(monkeypatch):
    def press():
        time.sleep(0.01)
        return [0x80]

    before = time.monotonic()
    posts = run_button(monkeypatch, fake_hid(press), lambda posts: len(posts) >= 1)
    assert posts[0].source == "button"
    assert before <= posts[0].at <= time.monotonic()


def test_a_button_that_is_there_from_the_start_is_not_reported_as_plugged_in(monkeypatch):
    def press():
        time.sleep(0.01)
        return [0x80]

    posts = run_button(monkeypatch, fake_hid(press), lambda posts: len(posts) >= 2)
    assert all(isinstance(event, ev.Toggle) for event in posts)


def test_a_button_that_appears_later_is_reported_as_plugged_in(monkeypatch):
    def idle():
        time.sleep(0.01)
        return []

    fake = fake_hid(idle)
    looks = []

    def enumerate_devices(vid, pid):
        looks.append(1)
        return [] if len(looks) < 3 else [{"path": b"consumer", "usage_page": 0x0C}]

    fake.enumerate = enumerate_devices
    posts = run_button(monkeypatch, fake, lambda posts: len(posts) >= 1)
    assert posts == [ev.ButtonConnected()]


def test_a_button_that_comes_back_after_being_lost_is_reported_as_plugged_in(monkeypatch):
    reads = []

    def read():
        reads.append(1)
        time.sleep(0.01)
        if len(reads) == 1:
            raise OSError("read error")      # unplugged
        return []

    posts = run_button(monkeypatch, fake_hid(read), lambda posts: len(posts) >= 1)
    assert posts == [ev.ButtonConnected()]
