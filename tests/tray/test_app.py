import http.client
import logging
import threading
from pathlib import Path

import pytest

from tolmach import paths
from tolmach.tray import app as tray_app
from tolmach.tray import gatewayctl
from tolmach.tray.models import ModelFile

OK = gatewayctl.Health("ok", "gigaam-v3", "", 1)


class FakeIcon:
    run_error = None
    last = None

    def __init__(self, name, image, title, menu=None):
        self.icon, self.menu, self.visible, self.title = image, menu, False, title
        self.menu_updates = 0
        self.stops = 0
        FakeIcon.last = self

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.stops += 1

    def run(self, setup=None):
        if FakeIcon.run_error is not None:
            raise FakeIcon.run_error


class FakeClient:
    start_error = None

    def __init__(self, on_state=None):
        self.state, self.last_text, self.button_found = "idle", "", False
        self.starts = self.stops = 0
        self.hotkeys = {"dictation": "", "insert_last": ""}     # none is ours
        self.ruled_by_button = False

    def button_rules(self):
        return self.ruled_by_button

    def microphones(self, periodic):
        return None

    def insert_last(self, source="menu"):
        self.inserts = getattr(self, "inserts", []) + [source]

    def start(self):
        if FakeClient.start_error is not None:
            raise FakeClient.start_error
        self.starts += 1

    def stop(self):
        self.stops += 1


@pytest.fixture
def tray(monkeypatch):
    FakeIcon.run_error = None
    FakeClient.start_error = None
    monkeypatch.setattr(tray_app.pystray, "Icon", FakeIcon)
    monkeypatch.setattr(tray_app, "ClientApp", FakeClient)
    monkeypatch.setattr(tray_app.keyfile, "read_key", lambda: None)
    # No test may run git against whatever folder it happens to be started in (or its origin).
    monkeypatch.setattr(tray_app.updater, "status", lambda root: tray_app.updater.Update("off"))
    monkeypatch.setattr(tray_app.updater, "disk_version", lambda root: tray_app.VERSION)
    monkeypatch.setattr(tray_app.updater, "requirements_digest", lambda root: "digest-at-start")
    monkeypatch.setattr(tray_app.updater, "libraries_stale", lambda root: False)
    return tray_app.TrayApp(Path("."))


def test_poll_survives_a_probe_that_raises(tray, monkeypatch):
    monkeypatch.setattr(tray_app, "POLL_S", 0.01)
    tray._ready = True
    answers = iter([http.client.BadStatusLine("x"), OK])

    def probe(gateway):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        tray._stopping.set()
        return answer

    monkeypatch.setattr(tray_app.gatewayctl, "probe", probe)
    tray._poll()
    assert tray._health == OK


def test_setup_starts_the_poll_thread_when_the_first_check_raises(tray, monkeypatch):
    started = []

    def probe(gateway):
        raise http.client.BadStatusLine("x")

    monkeypatch.setattr(tray_app.gatewayctl, "probe", probe)
    monkeypatch.setattr(tray_app.gatewayctl, "launch", lambda root: None)
    monkeypatch.setattr(tray_app.TrayApp, "_await_gateway", lambda self, seconds=20.0: None)
    monkeypatch.setattr(tray_app.TrayApp, "_poll", lambda self: started.append(True))
    tray._setup(tray.icon)
    for t in threading.enumerate():
        if t.name == "health":
            t.join(2)
    assert started == [True]
    assert tray._client.starts == 1


def test_run_stops_a_started_client_when_the_icon_loop_raises(tray):
    tray._client.starts = 1
    tray._started = True
    FakeIcon.run_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):   # run() stops the client, then re-raises for main() to log
        tray.run()
    assert tray._client.stops == 1


def test_exit_then_run_stops_the_client_once(tray, monkeypatch):
    tray._started = True
    monkeypatch.setattr(tray_app.ctypes, "windll", type("W", (), {"user32": type(
        "U", (), {"MessageBoxW": staticmethod(lambda *a: tray_app.IDNO)})})(), raising=False)
    monkeypatch.setattr(tray_app.gatewayctl, "stop", lambda *a: True)
    tray._exit(tray.icon, None)
    tray.run()
    assert tray._client.stops == 1


def test_run_does_not_stop_a_client_that_never_started(tray):
    tray.run()
    assert tray._client.stops == 0


def _status_rows(tray):
    return [item.text for item in tray.icon.menu.items if item.visible and not item.enabled]


def test_menu_change_leaves_a_file_that_is_not_json_untouched(tray, monkeypatch):
    applied = []
    monkeypatch.setattr(tray_app.autostart, "apply", lambda *a: applied.append(a))
    path = tray_app.paths.config_file()
    path.write_bytes(b'{"client": {"microphone": "USB",}}')
    tray._toggle_mute(tray.icon, None)
    tray._pick_microphone("Other")(tray.icon, None)
    tray._toggle_autostart(tray.icon, None)
    assert path.read_bytes() == b'{"client": {"microphone": "USB",}}'
    assert applied == []
    assert not path.with_name("config.json.bak").exists()


def test_menu_change_backs_up_a_file_with_a_bad_field_then_saves(tray):
    path = tray_app.paths.config_file()
    path.with_name("config.json.bak").write_text("older copy", encoding="utf-8")
    old = '{"client": {"microphone": "USB", "min_recording_s": -1}, "extra": 1}'
    path.write_text(old, encoding="utf-8")
    tray._toggle_mute(tray.icon, None)
    assert path.with_name("config.json.bak").read_text(encoding="utf-8") == old
    loaded = tray_app.config.load()
    assert loaded.problems == []
    assert loaded.config.client.microphone == "USB"
    assert loaded.config.client.mute_other_apps is False


def test_clean_file_is_saved_without_a_backup(tray):
    path = tray_app.paths.config_file()
    path.write_text('{"client": {"microphone": "USB"}}', encoding="utf-8")
    tray._toggle_mute(tray.icon, None)
    assert not path.with_name("config.json.bak").exists()
    assert tray_app.config.load().config.client.mute_other_apps is False


def test_menu_shows_a_config_error_line_while_the_file_has_problems(tray):
    tray._ready = True
    path = tray_app.paths.config_file()
    assert tray_app.CONFIG_ERROR not in _status_rows(tray)
    path.write_text("{", encoding="utf-8")
    tray.refresh()
    assert tray_app.CONFIG_ERROR in _status_rows(tray)
    path.write_text("{}", encoding="utf-8")
    tray.refresh()
    assert tray_app.CONFIG_ERROR not in _status_rows(tray)
    assert tray_app.CONFIG_ERROR == "Настройки: ошибка в config.json — см. журнал"


def test_refresh_rebuilds_the_menu_only_when_something_shown_changed(tray):
    tray._ready = True
    tray.refresh()
    tray.refresh()
    tray.refresh()
    assert tray.icon.menu_updates == 1
    tray._client.last_text = "готово"
    tray.refresh()
    assert tray.icon.menu_updates == 2
    tray._health = OK
    tray.refresh()
    tray.refresh()
    assert tray.icon.menu_updates == 3
    tray_app.paths.config_file().write_text('{"client": {"mute_other_apps": false}}', encoding="utf-8")
    tray.refresh()
    assert tray.icon.menu_updates == 4


def test_refresh_holds_a_lock(tray):
    tray._ready = True
    inside = []

    def update_menu():
        inside.append(tray._refresh_lock.locked())

    tray.icon.update_menu = update_menu
    tray.refresh()
    assert inside == [True]


def test_the_icon_tooltip_names_the_version(tray):
    from tolmach import VERSION

    assert tray.icon.title == f"Толмач {VERSION}"


def test_the_overlay_position_is_picked_from_the_menu(tray):
    tray._pick_position("bottom")(tray.icon, None)
    assert tray_app.config.load().config.client.overlay_position == "bottom"


def test_the_position_menu_offers_all_nine_places_in_russian_and_marks_the_current_one(tray):
    from tolmach.client import overlay

    rows = list(tray._positions())
    assert [row.text for row in rows] == [tray_app.POSITION_NAMES[p] for p in overlay.POSITIONS]
    assert len({row.text for row in rows}) == 9
    assert [row.text for row in rows if row.checked] == ["Посередине"]


def test_what_starts_a_dictation_is_picked_from_the_menu(tray):
    assert [row.text for row in tray._controls()] == [
        "Авто: кнопка, если она есть у микрофона", "Кнопка микрофона", "Горячая клавиша"]
    assert [row.text for row in tray._controls() if row.checked] == ["Авто: кнопка, если она есть у микрофона"]
    tray._ready = True
    tray._pick_control("hotkey")(tray.icon, None)
    assert tray_app.config.load().config.client.control == "hotkey"
    assert [row.text for row in tray._controls() if row.checked] == ["Горячая клавиша"]


def test_what_starts_a_dictation_cannot_be_changed_while_one_is_being_recorded(tray):
    assert [row.enabled for row in tray._controls()] == [True, True, True]
    tray._client.state = "recording"
    assert [row.enabled for row in tray._controls()] == [False, False, False]
    tray._client.state = "finishing"
    assert [row.enabled for row in tray._controls()] == [True, True, True]


def test_refresh_rebuilds_the_menu_when_what_starts_a_dictation_changes(tray):
    tray._ready = True
    tray.refresh()
    before = tray.icon.menu_updates
    tray_app.paths.config_file().write_text('{"client": {"control": "button"}}', encoding="utf-8")
    tray.refresh()
    assert tray.icon.menu_updates == before + 1


def test_refresh_rebuilds_the_menu_when_the_overlay_position_changes(tray):
    tray._ready = True
    tray.refresh()
    before = tray.icon.menu_updates
    tray_app.paths.config_file().write_text('{"client": {"overlay_position": "top"}}', encoding="utf-8")
    tray.refresh()
    assert tray.icon.menu_updates == before + 1


def test_the_microphone_list_comes_from_the_client_when_it_has_a_new_one(tray, monkeypatch):
    monkeypatch.setattr(tray_app.gatewayctl, "probe", lambda gateway: OK)
    tray._ready = True
    asked = []

    def microphones(periodic):
        asked.append(periodic)
        return ["Microphone (Usb Audio Device)"] if len(asked) == 1 else None

    tray._client.microphones = microphones
    tray._check(devices=True)
    assert tray._devices == ["Microphone (Usb Audio Device)"]
    updates = tray.icon.menu_updates
    tray._check()
    assert asked == [True, False]
    assert tray._devices == ["Microphone (Usb Audio Device)"]     # no news: the list stays
    assert tray.icon.menu_updates == updates


def test_the_menu_offers_both_copying_and_inserting_the_last_text(tray):
    labels = [item.text for item in tray.icon.menu.items]
    assert labels.index("Вставить последний текст") == labels.index("Скопировать последний текст") + 1
    insert = next(item for item in tray.icon.menu.items if item.text == "Вставить последний текст")
    assert insert.enabled is False                    # nothing was dictated yet
    tray._client.last_text = "готово"
    assert insert.enabled is True
    tray._insert_last(tray.icon, None)
    assert tray._client.inserts == ["menu"]


def test_the_terms_dictionary_is_created_with_the_starter_set_and_opened(tray, monkeypatch):
    opened = []
    monkeypatch.setattr(tray_app.subprocess, "Popen", lambda args, **kwargs: opened.append(args))
    tray._open_terms(tray.icon, None)
    assert "GitHub" in paths.terms_file().read_text(encoding="utf-8")
    assert opened == [["notepad.exe", str(paths.terms_file())]]
    paths.terms_file().write_text("Docker\n", encoding="utf-8")
    tray._open_terms(tray.icon, None)
    assert paths.terms_file().read_text(encoding="utf-8") == "Docker\n"       # an existing dictionary is the user's


# --- the menu says what to press: the hotkeys that are in force stand next to what they do


def shown_rows(tray):
    tray._ready = True
    tray.refresh()
    return [item.text for item in tray.icon.menu.items if item.visible]


def test_the_menu_names_the_hotkeys_that_are_in_force(tray):
    tray._client.hotkeys = {"dictation": "shift+win+q", "insert_last": "shift+win+z"}
    rows = shown_rows(tray)
    assert "Начать диктовку\tShift+Win+Q" in rows
    assert "Вставить последний текст\tShift+Win+Z" in rows
    tray._client.state = "recording"
    assert "Остановить диктовку\tShift+Win+Q" in shown_rows(tray)


def test_a_hotkey_that_is_off_or_taken_is_not_named(tray):
    rows = shown_rows(tray)
    assert "Начать диктовку" in rows and "Вставить последний текст" in rows


def test_when_the_button_rules_the_menu_says_so_instead_of_the_hotkey(tray):
    tray._client.hotkeys = {"dictation": "shift+win+q", "insert_last": "shift+win+z"}
    tray._client.ruled_by_button = True
    rows = shown_rows(tray)
    assert "Начать диктовку\tКнопка микрофона" in rows
    assert "Вставить последний текст\tShift+Win+Z" in rows    # that one works whatever starts a dictation


def test_the_menu_is_rebuilt_when_the_button_begins_to_rule_or_a_hotkey_changes(tray):
    shown_rows(tray)
    before = tray.icon.menu_updates
    tray._client.ruled_by_button = True
    tray.refresh()
    assert tray.icon.menu_updates == before + 1
    tray._client.hotkeys = {"dictation": "shift+win+q", "insert_last": ""}
    tray.refresh()
    assert tray.icon.menu_updates == before + 2


def test_what_starts_a_dictation_is_offered_only_when_there_is_a_button(tray):
    assert "Управление диктовкой" not in shown_rows(tray)     # no button: the hotkey it is, nothing to choose
    tray._client.button_found = True
    assert "Управление диктовкой" in shown_rows(tray)


def test_set_to_the_button_with_no_button_the_way_back_stays_in_the_menu(tray):
    tray_app.paths.config_file().write_text('{"client": {"control": "button"}}', encoding="utf-8")
    assert "Управление диктовкой" in shown_rows(tray)


# --- the English model

SMALL = (ModelFile("parakeet-unified-en/encoder.int8.onnx", "https://example.invalid/encoder", 300, "0" * 64),
         ModelFile("parakeet-unified-en/tokens.txt", "https://example.invalid/tokens", 100, "0" * 64))


@pytest.fixture
def english(tray, monkeypatch):
    """The tray with its icon up. The English model is two small files here, and what downloads
    them is the test."""
    monkeypatch.setattr(tray_app.models, "ENGLISH", SMALL)
    tray._ready = True
    return tray


def _english_row(tray):
    return next(item for item in tray.icon.menu.items if "модель" in item.text or "model" in item.text)


def test_the_menu_offers_to_download_the_english_model_and_says_how_large_it_is(english):
    row = _english_row(english)
    assert row.text == "Английская модель: скачать (663 МБ)" and row.enabled and row.visible


def test_the_english_model_is_downloaded_from_the_menu_and_the_row_follows(english, monkeypatch):
    seen = []

    def ensure(folder, say, files, tick):
        assert folder == paths.models_dir() and files == SMALL
        seen.append((_english_row(english).text, _english_row(english).enabled))     # begun, and nothing has come yet
        tick(100)
        seen.append(_english_row(english).text)
        tick(300)
        seen.append(_english_row(english).text)                                         # all of it has come, not yet in place
        for file in files:
            path = folder / file.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * file.size)
        return True

    monkeypatch.setattr(tray_app.models, "ensure", ensure)
    english.refresh()
    before = english.icon.menu_updates
    english._fetch_english(english.icon, None)
    assert seen == [("Английская модель: скачивается, 0 %", False), "Английская модель: скачивается, 25 %",
                    "Английская модель: скачивается, 99 %"]
    row = _english_row(english)
    assert row.text == "Английская модель: установлена" and not row.enabled
    # The menu is built anew when the download begins and when it ends - not at every percent: the row is
    # read by the look the tray takes every few seconds, and a menu that is open is not torn down under the hand.
    assert english.icon.menu_updates == before + 2


def test_a_download_that_failed_is_said_in_the_row_and_can_be_tried_again(english, monkeypatch, caplog):
    monkeypatch.setattr(tray_app.models, "ensure",
                        lambda folder, say, files, tick: say("  FAILED: tokens.txt: the server answered 404") or False)
    with caplog.at_level(logging.INFO, logger="tolmach"):
        english._fetch_english(english.icon, None)
    row = _english_row(english)
    assert row.text == "Английская модель: не скачалась — повторить" and row.enabled
    assert "the English model: FAILED: tokens.txt: the server answered 404" in caplog.text


def test_a_download_that_blows_up_does_not_leave_the_row_downloading(english, monkeypatch, caplog):
    def ensure(folder, say, files, tick):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(tray_app.models, "ensure", ensure)
    with caplog.at_level(logging.ERROR, logger="tolmach"):
        english._fetch_english(english.icon, None)
    assert _english_row(english).text == "Английская модель: не скачалась — повторить" and "the disk is full" in caplog.text


def test_a_second_click_while_the_model_is_being_downloaded_starts_nothing(english, monkeypatch):
    calls = []

    def ensure(folder, say, files, tick):
        calls.append(1)
        english._fetch_english(english.icon, None)           # the click again, in the middle of the first
        return False

    monkeypatch.setattr(tray_app.models, "ensure", ensure)
    english._fetch_english(english.icon, None)
    assert calls == [1]


def test_settings_without_the_english_model_have_no_row_for_it(english):
    paths.config_file().write_text('{"gateway": {"extra_models": []}}', encoding="utf-8")
    english.refresh()
    assert not _english_row(english).visible


def test_the_row_of_the_english_model_follows_the_language(english):
    paths.config_file().write_text('{"language": "en"}', encoding="utf-8")
    english.refresh()
    assert _english_row(english).text == "English model: download (663 MB)"


def test_whatever_goes_wrong_before_the_download_begins_does_not_leave_the_row_downloading(english, monkeypatch, caplog):
    def no_folder():
        raise PermissionError("the folder of the models cannot be made")

    folder = paths.models_dir()
    monkeypatch.setattr(tray_app.paths, "models_dir", no_folder)
    with caplog.at_level(logging.ERROR, logger="tolmach"):
        english._fetch_english(english.icon, None)
    monkeypatch.setattr(tray_app.paths, "models_dir", lambda: folder)
    row = _english_row(english)
    assert row.text == "Английская модель: не скачалась — повторить" and row.enabled and "cannot be made" in caplog.text


def test_a_tray_that_starts_clears_away_the_half_of_a_download_it_was_closed_in_the_middle_of(monkeypatch, request):
    monkeypatch.setattr(tray_app.models, "ENGLISH", SMALL)
    folder = paths.models_dir() / "parakeet-unified-en"
    folder.mkdir(parents=True)
    half, whole, other = folder / "encoder.int8.onnx.k3j2h1.part", folder / "tokens.txt", folder / "notes.part"
    for path in (half, whole, other):
        path.write_bytes(b"x" * 100)
    request.getfixturevalue("tray")                                      # the tray starts
    assert not half.exists() and whole.exists() and other.exists()       # only what a download of these files left
