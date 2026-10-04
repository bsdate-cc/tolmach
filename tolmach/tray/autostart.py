"""Start with Windows: one value under HKCU\\...\\Run, per user, no admin rights.

A Run entry sets neither environment nor working directory, so the command
bootstraps sys.path inline and runs under pythonw (no console window).
config.json's `autostart` is the authority; this key is its projection.
"""
from __future__ import annotations

from pathlib import Path

_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Tolmach"
LEGACY_VALUE = "SpeechKit"  # the program's former name: its entry would start a module that is gone


def _pyliteral(s: str) -> str:
    """A single-quoted Python string literal, always: repr() would switch to double
    quotes for a path with an apostrophe, and a double quote ends the -c payload."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def command(root: Path, pythonw: str) -> str:
    payload = (f"import sys; sys.path.insert(0, {_pyliteral(str(root))}); "
               "from tolmach.tray.__main__ import main; main()")
    return f'"{pythonw}" -c "{payload}"'


def enabled(value_name: str = VALUE) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as key:
            winreg.QueryValueEx(key, value_name)
        return True
    except OSError:
        return False


def enable(root: Path, pythonw: str, value_name: str = VALUE) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command(root, pythonw))


def disable(value_name: str = VALUE) -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except OSError:
        pass


def apply(want: bool, root: Path, pythonw: str, value_name: str = VALUE) -> None:
    """Make the registry match the setting. Rewrites the command when enabled, so a moved clone heals."""
    if want:
        enable(root, pythonw, value_name)
    else:
        disable(value_name)
    if value_name == VALUE:
        disable(LEGACY_VALUE)
