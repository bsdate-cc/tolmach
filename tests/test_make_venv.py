"""scripts/make_venv.py: tolmach.cmd's first start - which Python makes .venv, and what the window
says when none will do."""
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

from tolmach.tray import startup

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_venv.py"


def _load():
    spec = importlib.util.spec_from_file_location("make_venv", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mv = _load()
Found = mv.Found

PY314 = r"C:\Users\u\AppData\Local\Programs\Python\Python314\python.exe"
PY313_32 = r"C:\Program Files (x86)\Python313-32\python.exe"
PY312 = r"C:\Users\u\AppData\Local\Programs\Python\Python312\python.exe"
PY311 = r"C:\Python311\python.exe"


def run(found, made=None):
    """main() over scripted Pythons; returns (exit code, printed lines, the Pythons make was given)."""
    by_path = {f.path: f for f in found}
    lines, tried = [], []

    def make(path, target):
        tried.append(path)
        return made is None or path in made

    code = mv.main([r"C:\t\.venv"], find=lambda: list(by_path), ask=by_path.__getitem__,
                   make=make, say=lines.append)
    return code, lines, tried


def test_the_minimum_is_the_one_the_startup_check_holds():
    assert mv.MIN_PYTHON == startup.MIN_PYTHON


def test_the_launcher_list_is_read_in_the_new_and_the_old_format():
    text = (" -V:3.14 *        " + PY314 + "\r\n"
            " -V:3.13-32       " + PY313_32 + "\r\n"
            " -3.8-64          C:\\Python38\\python.exe\r\n"
            "\r\n"
            "No installed Pythons found!\r\n")
    assert mv.parse_launcher_list(text) == [PY314, PY313_32, r"C:\Python38\python.exe"]


def test_only_a_64_bit_python_3_12_or_newer_will_do():
    assert Found(PY312, (3, 12, 10), 64).suitable()
    assert Found(PY314, (3, 14, 0), 64).suitable()
    assert Found(PY313_32, (3, 13, 5), 32).verdict() == "no: Tolmach needs a 64-bit Python"
    assert Found(PY311, (3, 11, 9), 64).verdict() == "no: Tolmach needs Python 3.12 or newer"
    broken = Found(PY311, error="The system cannot find the file specified")
    assert not broken.suitable()
    assert broken.verdict() == "no: does not start - The system cannot find the file specified"


def test_every_python_is_listed_with_its_verdict_and_the_oldest_suitable_one_makes_the_venv():
    code, lines, tried = run([Found(PY314, (3, 14, 0), 64), Found(PY313_32, (3, 13, 5), 32),
                              Found(PY312, (3, 12, 10), 64), Found(PY311, (3, 11, 9), 64)])
    assert code == 0
    assert tried == [PY312]
    rows = [" ".join(line.split()) for line in lines]
    assert "Python 3.14.0, 64-bit suitable" in rows
    assert "Python 3.13.5, 32-bit no: Tolmach needs a 64-bit Python" in rows
    assert "Python 3.11.9, 64-bit no: Tolmach needs Python 3.12 or newer" in rows
    for path in (PY314, PY313_32, PY312, PY311):
        assert "      " + path in lines
    assert lines[-1] == r"Making C:\t\.venv with Python 3.12.10, 64-bit ..."


def test_a_32_bit_python_alone_gets_steps_to_install_a_64_bit_one_and_may_stay():
    # the user's case: a 32-bit Python is kept for other work
    code, lines, tried = run([Found(PY313_32, (3, 13, 5), 32)])
    assert code == 1 and tried == []
    text = "\n".join(lines)
    assert "None of them will do." in text
    assert mv.DOWNLOAD_URL in text
    assert "\"Windows installer (64-bit)\" of Python 3.12 or newer" in text
    assert "The Pythons you have can stay, a 32-bit one too." in text
    assert "Run tolmach.cmd again" in text


def test_no_python_at_all_says_so():
    code, lines, _ = run([])
    assert code == 1
    assert "  none found" in lines and "No Python found." in lines


def test_when_the_venv_fails_the_next_suitable_python_is_tried():
    found = [Found(PY312, (3, 12, 10), 64), Found(PY314, (3, 14, 0), 64)]
    code, lines, tried = run(found, made={PY314})
    assert code == 0 and tried == [PY312, PY314]
    assert "Could not make it with " + PY312 + "." in lines


def test_when_no_suitable_python_makes_the_venv_the_window_says_what_to_do():
    code, lines, tried = run([Found(PY312, (3, 12, 10), 64)], made=set())
    assert code == 1 and tried == [PY312]
    assert any("Repair" in line for line in lines)


def test_a_live_python_answers_its_version_and_bitness():
    found = mv.ask_python(sys.executable)
    assert found.error is None
    assert found.version == tuple(sys.version_info[:3])
    assert found.bits == (64 if sys.maxsize > 2**32 else 32)


def test_a_python_that_does_not_start_is_reported_not_raised(tmp_path):
    missing = mv.ask_python(str(tmp_path / "python.exe"))
    assert missing.error and not missing.suitable()
    not_python = tmp_path / "fake.exe"
    not_python.write_bytes(b"MZ not really")
    assert mv.ask_python(str(not_python)).error


def test_a_removed_python_left_in_the_registry_is_skipped(monkeypatch, tmp_path):
    real = tmp_path / "python.exe"
    real.write_bytes(b"")
    monkeypatch.setattr(mv, "launcher_pythons", lambda: [str(real), str(real).upper()])
    monkeypatch.setattr(mv, "registry_pythons", lambda: [str(tmp_path / "gone" / "python.exe")])
    monkeypatch.setattr(mv.sys, "base_prefix", "elsewhere")  # as if run from a venv
    assert mv.find_pythons() == [str(real)]


def test_the_script_runs_on_its_own_and_keeps_to_old_python_syntax():
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 7))
    assert not any(isinstance(node, ast.JoinedStr) for node in ast.walk(tree))  # no f-strings
    assert "import tolmach" not in source and "from tolmach" not in source
    out = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True)
    assert out.returncode == 1 and "usage:" in out.stdout


def test_the_launcher_hands_the_first_start_to_make_venv():
    cmd = (ROOT / "tolmach.cmd").read_text(encoding="ascii")
    assert "py -3.12" not in cmd
    assert r'"%ROOT%scripts\make_venv.py" "%ROOT%.venv"' in cmd
    assert "call :make_venv py" in cmd and "call :make_venv python" in cmd
