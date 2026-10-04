"""One version number for the whole program."""
import re
from pathlib import Path

from tolmach import VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_three_numbers():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)


def test_the_changelog_starts_with_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}$", changelog, re.MULTILINE)
    assert newest is not None, "CHANGELOG.md has no '## X.Y.Z — YYYY-MM-DD' heading"
    assert newest.group(1) == VERSION


def test_the_startup_window_names_the_version():
    from tolmach.tray import startup

    assert startup.banner() == f"Tolmach {VERSION}"
