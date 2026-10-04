"""The tray's side of updates: one menu line, a click, a hand-over to a new process."""
import pytest

from tolmach import VERSION
from tolmach.i18n import Text
from tolmach.tray import app as tray_app
from tolmach.tray import updater

from .test_app import tray  # noqa: F401  (the fixture)

BEHIND = updater.Update("behind", "9.9.9", commit="c" * 40)
SAME_LIBRARIES = updater.Pulled("a" * 40, "b" * 40, False)
NEW_LIBRARIES = updater.Pulled("a" * 40, "b" * 40, True)


def rows(tray):  # noqa: F811
    return [item.text for item in tray.icon.menu.items if item.visible]


@pytest.fixture
def acts(tray, monkeypatch):  # noqa: F811
    """Everything an update touches, recorded instead of done."""
    log = {"asked": [], "said": [], "pulled": [], "checks": 0, "gateway_stops": 0, "gateway_launches": 0,
           "quiet": 0, "console": 0, "answer": True, "pull": SAME_LIBRARIES, "gateway_stops_ok": True,
           "spawn_error": None, "status": updater.Update("none")}

    def pull(root, expected):
        log["pulled"].append(expected)
        if isinstance(log["pull"], Exception):
            raise log["pull"]
        return log["pull"]

    def status(root):
        log["checks"] += 1
        return log["status"]

    def stop(*args):
        log["gateway_stops"] += 1
        return log["gateway_stops_ok"]

    def spawn(kind):
        def run(root):
            if log["spawn_error"] is not None:
                raise log["spawn_error"]
            log[kind] += 1
        return run

    monkeypatch.setattr(tray_app.updater, "pull", pull)
    monkeypatch.setattr(tray_app.updater, "status", status)
    monkeypatch.setattr(tray_app.gatewayctl, "stop", stop)
    monkeypatch.setattr(tray_app.gatewayctl, "launch", lambda root: log.__setitem__("gateway_launches", log["gateway_launches"] + 1))
    monkeypatch.setattr(tray_app.relaunch, "spawn", spawn("quiet"))
    monkeypatch.setattr(tray_app.relaunch, "spawn_console", spawn("console"))
    monkeypatch.setattr(tray, "_ask", lambda text: log["asked"].append(text) or log["answer"])
    monkeypatch.setattr(tray, "_say", lambda text: log["said"].append(text))
    tray._started = True
    return log


def handed_over(tray, log, how):  # noqa: F811
    """The tray gave way to exactly one successor of the given kind and stopped itself."""
    other = "console" if how == "quiet" else "quiet"
    return (log[how], log[other], tray._client.stops, tray.icon.stops) == (1, 0, 1, 1)


def still_running(tray, log):  # noqa: F811
    return (log["quiet"], log["console"], tray._client.stops, tray.icon.stops) == (0, 0, 0, 0)


# --- the menu


def test_no_update_line_when_there_is_nothing_to_do(tray):  # noqa: F811
    assert not [row for row in rows(tray) if "Обновить" in row or "Перезапустить:" in row]
    assert "Проверить обновления" not in rows(tray)          # updates are off until origin has been seen


def test_an_update_on_origin_shows_one_line_with_its_version(tray):  # noqa: F811
    tray._update = BEHIND
    assert "Обновить до 9.9.9…" in rows(tray)
    assert "Проверить обновления" in rows(tray)


def test_newer_code_on_disk_shows_a_restart_line(tray):  # noqa: F811
    tray._on_disk = "9.9.9"
    assert "Перезапустить: на диске версия 9.9.9" in rows(tray)


def test_a_developers_clone_is_told_why_it_gets_nothing(tray):  # noqa: F811
    tray._update = updater.Update("off", note="ветка feature — обновления выключены")
    assert "Обновления: ветка feature — обновления выключены" in rows(tray)


def test_the_menu_is_rebuilt_when_an_update_appears(tray):  # noqa: F811
    tray._ready = True
    tray.refresh()
    before = tray.icon.menu_updates
    tray._update = BEHIND
    tray.refresh()
    assert tray.icon.menu_updates == before + 1


def test_the_running_version_is_what_the_disk_is_compared_with(tray):  # noqa: F811
    assert tray._on_disk == VERSION


# --- taking an update


def test_the_update_is_taken_after_a_yes_and_a_new_tray_takes_over(tray, acts):  # noqa: F811
    tray._update = BEHIND
    tray._apply_update(tray.icon, None)
    assert len(acts["asked"]) == 1 and "9.9.9" in acts["asked"][0]
    assert acts["pulled"] == [BEHIND]                         # exactly what the user agreed to
    assert acts["gateway_stops"] == 1
    assert handed_over(tray, acts, "quiet")


def test_a_no_leaves_everything_as_it_is(tray, acts):  # noqa: F811
    tray._update = BEHIND
    acts["answer"] = False
    tray._apply_update(tray.icon, None)
    assert acts["pulled"] == [] and still_running(tray, acts)


def test_a_pull_that_fails_is_said_and_origin_is_asked_again(tray, acts):  # noqa: F811
    tray._update = BEHIND
    acts["pull"] = updater.UpdateError("на origin уже версия 10.0.0 — нажмите «Обновить» ещё раз")
    acts["status"] = updater.Update("behind", "10.0.0")
    tray._apply_update(tray.icon, None)
    assert acts["said"] == ["на origin уже версия 10.0.0 — нажмите «Обновить» ещё раз"]
    assert still_running(tray, acts)
    assert tray._update == updater.Update("behind", "10.0.0")  # the menu line is fresh for the next click


def test_changed_libraries_are_installed_by_the_launcher_window_not_by_the_tray(tray, acts):  # noqa: F811
    # pip needs the tray and the gateway gone (they hold the files it replaces) and a window
    # that shows what went wrong: that is the startup console, not a process under pythonw.
    tray._update = BEHIND
    acts["pull"] = NEW_LIBRARIES
    tray._apply_update(tray.icon, None)
    assert handed_over(tray, acts, "console")
    assert acts["gateway_stops"] == 0                          # the window stops it, right before pip


# --- newer code on disk


def test_newer_code_on_disk_restarts_without_asking_or_pulling(tray, acts):  # noqa: F811
    tray._on_disk = "9.9.9"
    tray._apply_update(tray.icon, None)
    assert acts["asked"] == [] and acts["pulled"] == []
    assert handed_over(tray, acts, "quiet")


def test_newer_code_with_other_libraries_goes_through_the_launcher_window(tray, acts, monkeypatch):  # noqa: F811
    tray._on_disk = "9.9.9"
    monkeypatch.setattr(tray_app.updater, "requirements_digest", lambda root: "digest-after-the-merge")
    tray._apply_update(tray.icon, None)
    assert handed_over(tray, acts, "console")


def test_libraries_left_behind_by_a_failed_install_get_their_own_menu_line(tray, acts):  # noqa: F811
    tray._stale = True
    assert "Поставить библиотеки: requirements.txt изменился" in rows(tray)
    tray._apply_update(tray.icon, None)
    assert handed_over(tray, acts, "console")


def test_a_click_with_nothing_to_do_does_nothing(tray, acts):  # noqa: F811
    tray._apply_update(tray.icon, None)
    assert still_running(tray, acts) and acts["gateway_stops"] == 0


# --- a hand-over that cannot be made leaves the tray working


def test_a_gateway_that_will_not_stop_cancels_the_restart(tray, acts):  # noqa: F811
    tray._on_disk = "9.9.9"
    acts["gateway_stops_ok"] = False
    tray._apply_update(tray.icon, None)
    assert still_running(tray, acts)
    assert len(acts["said"]) == 1 and "Шлюз" in acts["said"][0]


def test_a_successor_that_cannot_be_started_puts_the_gateway_back(tray, acts):  # noqa: F811
    tray._on_disk = "9.9.9"
    acts["spawn_error"] = OSError("no such file")
    tray._apply_update(tray.icon, None)
    assert (tray._client.stops, tray.icon.stops) == (0, 0)
    assert acts["gateway_launches"] == 1
    assert len(acts["said"]) == 1


def test_a_launcher_window_that_cannot_be_opened_changes_nothing(tray, acts, monkeypatch):  # noqa: F811
    tray._on_disk = "9.9.9"
    monkeypatch.setattr(tray_app.updater, "requirements_digest", lambda root: "digest-after-the-merge")
    acts["spawn_error"] = OSError("no such file")
    tray._apply_update(tray.icon, None)
    assert (tray._client.stops, tray.icon.stops, acts["gateway_stops"]) == (0, 0, 0)
    assert len(acts["said"]) == 1


# --- asking origin


def test_a_check_asks_origin_and_remembers_the_answer(tray, acts):  # noqa: F811
    acts["status"] = BEHIND
    tray._check_update()
    assert tray._update == BEHIND


def test_an_origin_that_did_not_answer_does_not_erase_what_is_known(tray, acts):  # noqa: F811
    tray._update = BEHIND
    acts["status"] = updater.Update("unknown")
    tray._check_update()
    assert tray._update == BEHIND
    assert "Обновить до 9.9.9…" in rows(tray)


def test_a_check_that_blows_up_is_logged_and_keeps_the_last_answer(tray, monkeypatch, caplog):  # noqa: F811
    def boom(root):
        raise RuntimeError("git exploded")

    tray._update = BEHIND
    monkeypatch.setattr(tray_app.updater, "status", boom)
    with caplog.at_level("ERROR", logger="tolmach"):
        tray._check_update()
    assert tray._update == BEHIND
    assert any(record.exc_info for record in caplog.records)


def test_a_check_from_the_menu_that_finds_nothing_says_when_it_looked(tray, acts, monkeypatch):  # noqa: F811
    tray._update = updater.Update("none")
    monkeypatch.setattr(tray_app.time, "strftime", lambda fmt: "12:05")
    tray._check_update_now(tray.icon, None)
    assert "Обновлений нет — проверено в 12:05" in rows(tray)


def test_the_time_in_the_check_line_is_that_of_the_check_it_reports(tray, acts, monkeypatch):  # noqa: F811
    tray._update = updater.Update("none")
    monkeypatch.setattr(tray_app.time, "strftime", lambda fmt: "12:05")
    tray._check_update_now(tray.icon, None)             # by hand: nothing new
    acts["status"] = updater.Update("unknown")
    tray._check_update()                                # the six-hour check gets no answer
    assert "Обновлений нет — проверено в 12:05" in rows(tray)
    assert not [row for row in rows(tray) if row.startswith("Нет связи")]


def test_a_check_does_not_run_into_an_update_that_is_being_taken(tray, acts):  # noqa: F811
    import threading

    tray._update = BEHIND
    seen_during_pull = []

    def pull(root, expected):
        other = threading.Thread(target=lambda: seen_during_pull.append(tray._check_update()))
        other.start()
        other.join()
        return SAME_LIBRARIES

    tray_app.updater.pull = pull
    tray._apply_update(tray.icon, None)
    assert seen_during_pull == [None] and acts["checks"] == 0   # the check stepped aside


def test_a_check_from_the_menu_that_got_no_answer_says_so_and_not_that_all_is_current(tray, acts, monkeypatch):  # noqa: F811
    tray._update = updater.Update("none")
    acts["status"] = updater.Update("unknown")
    monkeypatch.setattr(tray_app.time, "strftime", lambda fmt: "12:05")
    tray._check_update_now(tray.icon, None)
    assert "Нет связи с origin — проверено в 12:05" in rows(tray)
    assert not [row for row in rows(tray) if row.startswith("Обновлений нет")]


def test_the_six_hour_look_at_origin_does_not_hold_the_poll(tray, monkeypatch):  # noqa: F811
    import threading

    from .test_app import OK

    monkeypatch.setattr(tray_app, "POLL_S", 0.001)
    monkeypatch.setattr(tray_app, "UPDATE_EVERY", 1)
    tray._ready = True
    asked_on, polls = [], []
    origin_answers = threading.Event()

    def status(root):
        asked_on.append(threading.current_thread().name)
        origin_answers.wait(5)                      # an origin that takes its time
        return updater.Update("none")

    def probe(gateway):
        polls.append(1)
        if len(polls) >= 3:
            tray._stopping.set()
        return OK

    monkeypatch.setattr(tray_app.updater, "status", status)
    monkeypatch.setattr(tray_app.gatewayctl, "probe", probe)
    poll = threading.Thread(target=tray._poll, name="health")
    poll.start()
    poll.join(3)
    still_polling = poll.is_alive()
    origin_answers.set()
    poll.join(3)
    assert not still_polling and len(polls) >= 3    # the gateway was watched while origin was silent
    assert asked_on and "health" not in asked_on


def test_a_pull_that_fails_goes_to_the_log_in_english(tray, acts, caplog):  # noqa: F811
    # The user reads the reason in the language of the menu; the log is always English.
    tray._update = BEHIND
    acts["pull"] = updater.UpdateError(Text("git merge не выполнен: {reason}", reason="exit 1"))
    with caplog.at_level("WARNING", logger="tolmach"):
        tray._apply_update(tray.icon, None)
    assert acts["said"] == ["git merge не выполнен: exit 1"]
    assert any("git merge failed: exit 1" in record.getMessage() for record in caplog.records)
