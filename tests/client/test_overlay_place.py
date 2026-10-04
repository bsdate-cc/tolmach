"""Where the overlay goes: the monitor of the window that gets the text, at the chosen position."""
from tolmach.client import overlay as ov

PRIMARY = (0, 0, 2560, 1400)   # work area of the main monitor: the taskbar takes the bottom 40 px
LEFT = (-2498, 0, 0, 1440)     # a second monitor standing to the left of it
SIZE = (500, 100)


def test_center_is_the_middle_of_the_work_area():
    assert ov.place("center", PRIMARY, SIZE) == (1030, 650)


def test_top_and_bottom_keep_a_margin_and_stay_centred_across():
    assert ov.place("top", PRIMARY, SIZE) == (1030, ov.MARGIN)
    assert ov.place("bottom", PRIMARY, SIZE) == (1030, 1400 - ov.MARGIN - 100)


def test_left_and_right_keep_a_margin_and_stay_centred_down():
    assert ov.place("left", PRIMARY, SIZE) == (ov.MARGIN, 650)
    assert ov.place("right", PRIMARY, SIZE) == (2560 - ov.MARGIN - 500, 650)


def test_corners():
    assert ov.place("top-left", PRIMARY, SIZE) == (ov.MARGIN, ov.MARGIN)
    assert ov.place("top-right", PRIMARY, SIZE) == (2560 - ov.MARGIN - 500, ov.MARGIN)
    assert ov.place("bottom-left", PRIMARY, SIZE) == (ov.MARGIN, 1400 - ov.MARGIN - 100)
    assert ov.place("bottom-right", PRIMARY, SIZE) == (2560 - ov.MARGIN - 500, 1400 - ov.MARGIN - 100)


def test_a_monitor_left_of_the_main_one_has_negative_coordinates():
    assert ov.place("center", LEFT, SIZE) == (-1499, 670)
    assert ov.place("top-left", LEFT, SIZE) == (-2498 + ov.MARGIN, ov.MARGIN)


def test_every_named_position_is_inside_the_work_area():
    assert len(ov.POSITIONS) == 9 and "center" in ov.POSITIONS
    for work in (PRIMARY, LEFT):
        left, top, right, bottom = work
        for position in ov.POSITIONS:
            x, y = ov.place(position, work, SIZE)
            assert left <= x and x + SIZE[0] <= right, position
            assert top <= y and y + SIZE[1] <= bottom, position


def test_a_window_larger_than_the_work_area_starts_at_its_corner():
    assert ov.place("bottom-right", (100, 50, 400, 250), (500, 400)) == (100, 50)


def test_an_unknown_position_means_center():
    assert ov.place("somewhere", PRIMARY, SIZE) == ov.place("center", PRIMARY, SIZE)


def test_tk_geometry_spells_a_negative_offset_with_plus_minus():
    assert ov.geometry(-1499, 670) == "+-1499+670"
    assert ov.geometry(60, 60) == "+60+60"


# --- the overlay itself, on a faked Tk root


class FakeRoot:
    def __init__(self):
        self.calls = []

    def after_cancel(self, job):
        pass

    def after(self, ms, fn):
        return "job"

    def withdraw(self):
        self.calls.append("withdraw")

    def deiconify(self):
        self.calls.append("deiconify")

    def update_idletasks(self):
        self.calls.append("update")

    def winfo_reqwidth(self):
        return SIZE[0]

    def winfo_reqheight(self):
        return SIZE[1]

    def winfo_screenwidth(self):
        return 2560

    def winfo_screenheight(self):
        return 1440

    def geometry(self, spec):
        self.calls.append(("geometry", spec))


class FakeLabel:
    def config(self, **kwargs):
        pass


class NoTk:
    @staticmethod
    def Tk():
        raise RuntimeError("no Tk in this test")


def overlay_on(monkeypatch, work_areas, target=0):
    """An Overlay whose own thread never opened a window, driven by hand on a fake root."""
    monkeypatch.setattr(ov, "tk", NoTk)
    asked = []

    def work_area(hwnd):
        asked.append(hwnd)
        return work_areas.get(hwnd)

    monkeypatch.setattr(ov, "_work_area", work_area)
    overlay = ov.Overlay(target_window=lambda: target)
    overlay._thread.join(2.0)
    overlay._root, overlay._label = FakeRoot(), FakeLabel()
    return overlay, asked


def test_the_overlay_appears_on_the_monitor_of_the_window_that_gets_the_text(monkeypatch):
    overlay, asked = overlay_on(monkeypatch, {77: LEFT}, target=77)
    overlay._apply("show", "Слушаю…", 0.0)
    assert asked == [77]
    assert ("geometry", "+-1499+670") in overlay._root.calls


def test_the_window_is_placed_before_it_is_shown(monkeypatch):
    overlay, _ = overlay_on(monkeypatch, {0: PRIMARY})
    overlay._apply("show", "Слушаю…", 0.0)
    calls = overlay._root.calls
    assert calls.index(("geometry", "+1030+650")) < calls.index("deiconify")


def test_the_position_follows_the_setting(monkeypatch):
    overlay, _ = overlay_on(monkeypatch, {0: PRIMARY})
    overlay.set_position("top-right")
    overlay._apply("message", "Микрофон не найден", 3.0)
    assert ("geometry", f"+{2560 - ov.MARGIN - 500}+{ov.MARGIN}") in overlay._root.calls


def test_an_unknown_position_setting_is_ignored(monkeypatch):
    overlay, _ = overlay_on(monkeypatch, {0: PRIMARY})
    overlay.set_position("bottom")
    overlay.set_position("nowhere")
    overlay._apply("show", "Слушаю…", 0.0)
    assert ("geometry", f"+1030+{1400 - ov.MARGIN - 100}") in overlay._root.calls


def test_without_a_known_work_area_the_main_screen_is_used(monkeypatch):
    overlay, _ = overlay_on(monkeypatch, {})
    overlay._apply("show", "Слушаю…", 0.0)
    assert ("geometry", "+1030+670") in overlay._root.calls


def test_a_size_learnt_only_after_showing_moves_the_window_once_more(monkeypatch):
    overlay, _ = overlay_on(monkeypatch, {0: PRIMARY})
    root = overlay._root
    root.winfo_reqwidth = lambda: 500 if "deiconify" in root.calls else 300   # stale while hidden
    overlay._apply("show", "Слушаю…", 0.0)
    spots = [call[1] for call in root.calls if call[0] == "geometry"]
    assert spots == ["+1130+650", "+1030+650"]


def test_a_window_already_in_place_is_not_moved_again(monkeypatch):
    overlay, _ = overlay_on(monkeypatch, {0: PRIMARY})
    overlay._apply("show", "Слушаю…", 0.0)
    assert [call for call in overlay._root.calls if call[0] == "geometry"] == [("geometry", "+1030+650")]
