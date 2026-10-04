import ast
import re
from pathlib import PureWindowsPath

from tolmach.tray.autostart import VALUE, command

PYTHONW = r"C:\projects\tolmach\.venv\Scripts\pythonw.exe"


def literal_in(cmd: str) -> str:
    """The Python string literal handed to sys.path.insert inside the -c payload."""
    return re.search(r"sys\.path\.insert\(0, ('(?:[^'\\]|\\.)*')\)", cmd).group(1)


def test_value_name():
    assert VALUE == "Tolmach"


def test_command_runs_the_tray_under_pythonw_with_the_repo_on_the_path():
    root = PureWindowsPath(r"C:\projects\tolmach")
    cmd = command(root, PYTHONW)
    assert cmd.startswith(f'"{PYTHONW}" -c "')
    assert cmd.endswith('from tolmach.tray.__main__ import main; main()"')
    assert ast.literal_eval(literal_in(cmd)) == str(root)


def test_path_with_an_apostrophe_survives():
    root = PureWindowsPath(r"C:\Users\O'Brien\tolmach")
    cmd = command(root, PYTHONW)
    assert ast.literal_eval(literal_in(cmd)) == str(root)
    payload = cmd[len(f'"{PYTHONW}" -c "'):-1]
    assert '"' not in payload   # a double quote would end the -c "..." payload
