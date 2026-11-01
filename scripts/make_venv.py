"""Pick a Python for Tolmach and make the .venv with it. tolmach.cmd runs this on the first start:

    py scripts\\make_venv.py C:\\path\\to\\tolmach\\.venv

Any Python can run this file - a 32-bit or an old one too - so it does not import Tolmach and keeps
to the language of old Pythons (no f-strings). It collects the installed Pythons (the py launcher's
list, the Windows registry, the Python running it), asks each one its version and bitness, prints
every one with "suitable" or why not, and makes the environment with a suitable one. When there is
none, it says what to install. Exit code 0: the environment is made; 1: it is not, the window says
why.
"""
import os
import re
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 12)  # the same as tolmach.tray.startup.MIN_PYTHON
DOWNLOAD_URL = "https://www.python.org/downloads/windows/"
ASK_TIMEOUT_S = 30  # a Store Python may take a while on its first start
# Prints "3 14 0 64": the version and the size of a pointer in bits. Any Python can run it.
PROBE = ('import struct, sys; v = sys.version_info; '
         'sys.stdout.write("%d %d %d %d" % (v[0], v[1], v[2], struct.calcsize("P") * 8))')
_PATH_AT_END = re.compile(r"([A-Za-z]:\\.*?)\s*$")


class Found(object):
    """One Python: where it is and what it said about itself, or why it did not answer."""

    def __init__(self, path, version=None, bits=None, error=None):
        self.path, self.version, self.bits, self.error = path, version, bits, error

    def suitable(self):
        return self.error is None and self.bits == 64 and tuple(self.version[:2]) >= MIN_PYTHON

    def verdict(self):
        if self.error is not None:
            return "no: does not start - " + self.error
        if self.bits != 64:
            return "no: Tolmach needs a 64-bit Python"
        if tuple(self.version[:2]) < MIN_PYTHON:
            return "no: Tolmach needs Python %d.%d or newer" % MIN_PYTHON
        return "suitable"

    def name(self):
        if self.error is not None:
            return os.path.basename(self.path)
        return "Python %s, %d-bit" % (".".join(str(n) for n in self.version), self.bits)


# ---- finding the Pythons ----

def parse_launcher_list(text):
    """`py -0p` prints one Python per line with its path at the end; the old launcher writes
    ` -3.8-64  C:\\...`, the new one ` -V:3.13 *  C:\\...`."""
    paths = []
    for line in text.splitlines():
        match = _PATH_AT_END.search(line)
        if match and match.group(1).lower().endswith(".exe"):
            paths.append(match.group(1))
    return paths


def launcher_pythons():
    try:
        code, out, _ = _run(["py", "-0p"])
    except Exception:
        return []  # no launcher: the registry still knows the python.org installs
    return parse_launcher_list(out) if code == 0 else []


def registry_pythons():
    """Every Python registered the standard way (PEP 514): per user and for all users, 64-bit
    and 32-bit registry views."""
    try:
        import winreg
    except ImportError:
        return []
    places = ((winreg.HKEY_CURRENT_USER, 0),
              (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
              (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY))
    paths = []
    for hive, view in places:
        access = winreg.KEY_READ | view
        for company in _subkeys(hive, "Software\\Python", access):
            if company == "PyLauncher":
                continue
            base = "Software\\Python\\" + company
            for tag in _subkeys(hive, base, access):
                where = base + "\\" + tag + "\\InstallPath"
                exe = _value(hive, where, "ExecutablePath", access)
                folder = _value(hive, where, "", access)
                if not exe and folder:
                    exe = os.path.join(folder, "python.exe")
                if exe:
                    paths.append(exe)
    return paths


def _subkeys(hive, path, access):
    import winreg
    try:
        key = winreg.OpenKey(hive, path, 0, access)
    except OSError:
        return []
    names = []
    try:
        while True:
            names.append(winreg.EnumKey(key, len(names)))
    except OSError:
        pass
    finally:
        winreg.CloseKey(key)
    return names


def _value(hive, path, name, access):
    import winreg
    try:
        key = winreg.OpenKey(hive, path, 0, access)
    except OSError:
        return None
    try:
        value = winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)
    return value if isinstance(value, str) and value else None


def find_pythons():
    """The paths of the installed Pythons, each once, in the order they were found. A registration
    left behind by a removed Python points to nothing and is skipped."""
    paths = launcher_pythons() + registry_pythons()
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):  # not a venv: a Python of its own
        paths.append(sys.executable)
    seen, unique = set(), []
    for path in paths:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen and os.path.lexists(path):  # lexists: Store Pythons are reparse points
            seen.add(key)
            unique.append(path)
    return unique


def ask_python(path):
    """Run the Python and ask its version and bitness."""
    try:
        code, out, err = _run([path, "-E", "-c", PROBE])
    except subprocess.TimeoutExpired:
        return Found(path, error="did not answer in %d s" % ASK_TIMEOUT_S)
    except OSError as e:
        return Found(path, error=e.strerror or e.__class__.__name__)
    if code != 0:
        lines = [line for line in err.splitlines() if line.strip()]
        return Found(path, error=lines[-1].strip() if lines else "exit code %d" % code)
    try:
        major, minor, micro, bits = (int(part) for part in out.split())
    except ValueError:
        answer = out.strip()[:60]
        return Found(path, error=("unexpected answer: " + answer) if answer else "not a Python")
    return Found(path, (major, minor, micro), bits)


def _run(args):
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    try:
        out, err = process.communicate(timeout=ASK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    return process.returncode, _text(out), _text(err)


def _text(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("mbcs", "replace")


# ---- choosing and making ----

def choose(found):
    """The suitable ones, oldest first: the libraries are pinned, and an older Python is the
    likelier to have them ready-built."""
    return sorted((f for f in found if f.suitable()), key=lambda f: tuple(f.version))


def make_venv(python, target):
    """`python -m venv target`; a folder it started and could not finish is removed, so that the
    next run starts clean. True when the environment's python.exe is there."""
    existed = os.path.exists(target)
    code = subprocess.call([python, "-m", "venv", target])
    if code == 0 and os.path.isfile(os.path.join(target, "Scripts", "python.exe")):
        return True
    if not existed:
        shutil.rmtree(target, ignore_errors=True)
    return False


def report(found):
    lines = ["Pythons on this computer (Tolmach needs a 64-bit Python %d.%d or newer):" % MIN_PYTHON]
    for f in found:
        lines.append("  %-24s %s" % (f.name(), f.verdict()))
        lines.append("      " + f.path)
    if not found:
        lines.append("  none found")
    return lines


def what_to_install(found):
    return ["",
            ("None of them will do." if found else "No Python found."),
            "What to do:",
            "  1. Open %s and download" % DOWNLOAD_URL,
            "     \"Windows installer (64-bit)\" of Python %d.%d or newer." % MIN_PYTHON,
            "  2. Install it. The Pythons you have can stay, a 32-bit one too.",
            "  3. Run tolmach.cmd again: it finds the new Python by itself."]


def main(argv=None, find=find_pythons, ask=ask_python, make=make_venv, say=None):
    args = sys.argv[1:] if argv is None else argv
    say = say or _say
    if len(args) != 1:
        say("usage: make_venv.py <folder of the environment>")
        return 1
    target = args[0]
    found = [ask(path) for path in find()]
    for line in report(found):
        say(line)
    suitable = choose(found)
    if not suitable:
        for line in what_to_install(found):
            say(line)
        return 1
    for f in suitable:
        say("")
        say("Making %s with %s ..." % (target, f.name()))
        if make(f.path, target):
            return 0
        say("Could not make it with %s." % f.path)
    say("")
    say("The environment could not be made with any suitable Python (the errors are above).")
    say("What to do: repair that Python (run its installer again and choose Repair) or install")
    say("a 64-bit Python %d.%d or newer from %s, then run tolmach.cmd again." % (MIN_PYTHON + (DOWNLOAD_URL,)))
    return 1


def _say(text):
    """Print what the console can show: a path with letters outside its code page must not stop
    the run."""
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    sys.stdout.write(text.encode(encoding, "replace").decode(encoding) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
