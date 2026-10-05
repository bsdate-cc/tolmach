"""Two languages for what the user reads: Russian is the source, English is looked up."""
import ast
import json
import re
from pathlib import Path

from tolmach import config, i18n, paths
from tolmach.i18n import Text, _

PACKAGE = Path(__file__).resolve().parents[1] / "tolmach"


def test_russian_is_shown_as_written():
    i18n.set_language("ru")
    assert _("Слушаю…") == "Слушаю…"


def test_english_is_looked_up():
    i18n.set_language("en")
    assert _("Слушаю…") == "Listening…"


def test_a_text_without_a_translation_is_shown_as_it_is():
    i18n.set_language("en")
    assert _("Привет, мир") == "Привет, мир"


def test_auto_follows_the_language_of_windows(monkeypatch):
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    i18n.set_language("auto")
    assert i18n.language() == "en"
    monkeypatch.setattr(i18n, "system_language", lambda: "ru")
    i18n.set_language("auto")
    assert i18n.language() == "ru"
    i18n.set_language("klingon")                      # anything unknown is "auto"
    assert i18n.language() == "ru"


def test_a_message_with_values_follows_the_language_of_the_moment_it_is_shown():
    note = Text("ветка {branch} — обновления выключены", branch="feature")
    i18n.set_language("ru")
    assert str(note) == "ветка feature — обновления выключены"
    i18n.set_language("en")
    assert str(note) == "branch feature — updates are off"
    assert note == Text("ветка {branch} — обновления выключены", branch="feature")


def marked_texts() -> set[str]:
    """Every string literal handed to _(), N_() or Text() anywhere in the program."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            first = node.args[0]
            if name in ("_", "N_", "Text") and isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def test_everything_the_program_shows_has_an_english_text():
    missing = marked_texts() - set(i18n.EN)
    assert not missing, f"no English for: {sorted(missing)}"


def test_no_english_text_is_left_without_a_use():
    unused = set(i18n.EN) - marked_texts()
    assert not unused, f"translated but never shown: {sorted(unused)}"


def test_the_values_in_a_text_are_the_same_in_both_languages():
    for russian, english in i18n.EN.items():
        assert set(re.findall(r"{(\w+)}", russian)) == set(re.findall(r"{(\w+)}", english)), russian


def test_no_code_shows_a_russian_literal_past_the_catalogue():
    """A Cyrillic string literal outside _()/N_()/Text() would stay Russian in an English menu.
    Allowed: the catalogue itself, comments and docstrings (not literals handed to anything),
    and the two places that are about characters, not words."""
    # the launcher takes "д"/"да" for yes; terms.py holds the sounds of Russian letters
    allowed_files = {"i18n.py", "textbuf.py", "startup.py", "terms.py"}
    cyrillic = re.compile("[А-Яа-яЁё]")
    marked = marked_texts()
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name in allowed_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.body and isinstance(node.body[0], ast.Expr)
                      and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and cyrillic.search(node.value)
                    and id(node) not in docstrings and node.value not in marked):
                offenders.append(f"{path.name}:{node.lineno}: {node.value[:40]!r}")
    assert not offenders, offenders


# --- the settings


def test_the_language_follows_windows_unless_chosen():
    assert config.load().config.language == "auto"
    paths.config_file().write_text(json.dumps({"language": "en"}), encoding="utf-8")
    loaded = config.load()
    assert loaded.problems == [] and loaded.config.language == "en"


def test_an_unknown_language_keeps_the_default():
    paths.config_file().write_text(json.dumps({"language": "de"}), encoding="utf-8")
    loaded = config.load()
    assert loaded.config.language == "auto"
    assert [p.where for p in loaded.problems] == ["language"]


# --- the overlay, the gateway line, the tray


def test_the_overlay_speaks_english_when_asked():
    from tests.client.fakes import Rig

    i18n.set_language("en")
    rig = Rig()
    rig.toggle()
    assert rig.overlay.last == ("show", "Listening…")
    rig.speak(1, seconds=0.5, amplitude=2)             # the microphone is muted
    assert rig.overlay.last == ("message", "The microphone is off — press the button again")


def test_the_gateway_line_is_translated_with_its_values():
    from tolmach.tray import gatewayctl

    health = gatewayctl.Health("ok", "gigaam-v3", "", 1)
    i18n.set_language("ru")
    assert gatewayctl.status_line(health) == "Шлюз: работает (gigaam-v3)"
    i18n.set_language("en")
    assert gatewayctl.status_line(health) == "Gateway: running (gigaam-v3)"
    assert gatewayctl.status_line(None) == "Gateway: not answering"


def test_the_update_line_is_translated_with_its_values():
    from tolmach.tray import updater

    behind = updater.Update("behind", "9.9.9")
    i18n.set_language("en")
    assert updater.menu_label(behind, running="0.6.0", on_disk="0.6.0") == "Update to 9.9.9…"
    assert updater.menu_label(updater.Update("none"), running="0.6.0", on_disk="0.7.0") == "Restart: version 0.7.0 is on disk"


def test_an_update_error_is_worded_in_the_language_of_the_moment_it_is_shown():
    from tolmach.tray import updater

    error = updater.UpdateError(Text("git merge не выполнен: {reason}", reason="exit 1"))
    i18n.set_language("ru")
    assert str(error) == "git merge не выполнен: exit 1"
    i18n.set_language("en")
    assert str(error) == "git merge failed: exit 1"


def test_auto_means_russian_when_windows_or_its_regional_format_is_russian():
    # A Russian speaker on an English Windows is common among the people who run this
    # (measured on the author's machine: UI 0x409, regional format ru-RU).
    assert i18n.pick_language(ui=0x0419, regional=0x0419) == "ru"
    assert i18n.pick_language(ui=0x0409, regional=0x0419) == "ru"
    assert i18n.pick_language(ui=0x0419, regional=0x0409) == "ru"
    assert i18n.pick_language(ui=0x0409, regional=0x0409) == "en"
    assert i18n.pick_language(ui=0x0407, regional=0x0407) == "en"
    assert i18n.pick_language(ui=0x0819, regional=0x0409) == "ru"      # Russian (Moldova): same primary language


def test_the_log_gets_a_message_in_english_whatever_the_language():
    i18n.set_language("ru")
    assert i18n.english(Text("git merge не выполнен: {reason}", reason="exit 1")) == "git merge failed: exit 1"
    assert i18n.english("Слушаю…") == "Listening…"
    assert i18n.english("no translation for this") == "no translation for this"
