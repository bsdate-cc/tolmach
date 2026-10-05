"""The tray in two languages."""
import json
import re

from tolmach import VERSION, config, paths

from .test_app import tray  # noqa: F401  (the fixture)


def menu_rows(tray):  # noqa: F811
    return [item.text for item in tray.icon.menu.items if item.visible]


def test_the_tray_menu_and_tooltip_switch_with_the_setting(tray):  # noqa: F811
    tray._ready = True
    assert "Начать диктовку" in menu_rows(tray) and tray.icon.title == f"Толмач {VERSION}"
    assert "Открыть словарь терминов" in menu_rows(tray)
    paths.config_file().write_text(json.dumps({"language": "en"}), encoding="utf-8")
    updates = tray.icon.menu_updates
    tray.refresh()
    rows = menu_rows(tray)
    assert tray.icon.menu_updates == updates + 1
    assert tray.icon.title == f"Tolmach {VERSION}"
    for english in ("Start dictation", "Copy the last text", "Insert the last text", "Microphone",
                    "Overlay position", "Start with Windows", "Open the terms dictionary", "Open the logs",
                    "Exit…"):
        assert english in rows, english
    assert not [row for row in rows if re.search("[А-Яа-яЁё]", row) and "Язык" not in row]


def test_the_language_is_picked_from_the_menu(tray):  # noqa: F811
    tray._ready = True
    rows = list(tray._languages())
    assert [row.text for row in rows] == ["Авто / Auto", "Русский", "English"]
    assert [row.text for row in rows if row.checked] == ["Авто / Auto"]
    tray._pick_language("en")(tray.icon, None)
    assert config.load().config.language == "en"
    assert [row.text for row in tray._languages() if row.checked] == ["English"]
    assert "Start dictation" in menu_rows(tray)          # and the menu follows at once


def test_the_submenus_are_translated_too(tray):  # noqa: F811
    paths.config_file().write_text(json.dumps({"language": "en"}), encoding="utf-8")
    tray._ready = True
    tray.refresh()
    assert [row.text for row in tray._positions()][4] == "Centre"
    assert next(iter(tray._microphones())).text == "Default"
