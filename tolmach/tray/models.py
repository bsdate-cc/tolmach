"""The model files: what is needed, where it comes from, how it is fetched.

The files are not in the repository. The launcher offers to download them when they
are missing, the way it offers to install missing libraries. Each file is pinned to
a size and a SHA-256: whatever the server sends, only the expected bytes are put in
place. Standard library only - this runs before the libraries are checked.

    python -m tolmach.tray.models        download what is missing
"""
from __future__ import annotations

import hashlib
import http.client
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

# GigaAM v3 (SberDevices, MIT licence), the e2e_rnnt variant - with punctuation and text
# normalisation - exported to ONNX for sherpa-onnx. The repository is a community
# conversion; the address names one revision of it, and the checksums below are of the
# files this program was developed and tested with.
GIGAAM_REVISION = "6888903da215c7735f51101d939f3bfa679fb2b8"
_GIGAAM = f"https://huggingface.co/Smirnov75/GigaAM-v3-sherpa-onnx/resolve/{GIGAAM_REVISION}/"
_SILERO = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"

CHUNK = 1 << 20
TIMEOUT_S = 60.0
PLACE_ATTEMPTS = 10


class ModelFile(NamedTuple):
    path: str     # relative to the models folder, as the default settings name it
    url: str
    size: int
    sha256: str


FILES = (
    ModelFile("silero_vad.onnx", _SILERO, 643854,
              "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"),
    ModelFile("gigaam-v3/gigaam_v3_e2e_rnnt_encoder_int8.onnx", _GIGAAM + "gigaam_v3_e2e_rnnt_encoder_int8.onnx",
              318995997, "2cac62d0c270bd128f898f2be1a2d34780d524a6e9483888ebac7b00f97410f1"),
    ModelFile("gigaam-v3/gigaam_v3_e2e_rnnt_decoder.onnx", _GIGAAM + "gigaam_v3_e2e_rnnt_decoder.onnx",
              4600058, "781971998e6a355d6a714f6932a30eab295e7ba0d14fd7e0f78c83b87e811860"),
    ModelFile("gigaam-v3/gigaam_v3_e2e_rnnt_joint.onnx", _GIGAAM + "gigaam_v3_e2e_rnnt_joint.onnx",
              2712896, "602ff7017a93311aad34df1437c8d7f49911353c13d6eae7a6ee7b041339465c"),
    ModelFile("gigaam-v3/gigaam_v3_e2e_rnnt_tokens.txt", _GIGAAM + "gigaam_v3_e2e_rnnt_tokens.txt",
              13353, "7ddf22514c42c531358182c81446a8159771e9921019f09ae743ea622d40221d"),
)
TOTAL_MB = round(sum(file.size for file in FILES) / 1e6)


class DownloadError(Exception):
    """A file could not be fetched or is not the expected one; the message is for the console."""


def among(named: Iterable[Path], models_dir: Path, files: tuple[ModelFile, ...] | None = None) -> tuple[ModelFile, ...]:
    """The files of the list that these paths name: what this program knows where to get.
    A model of the user's own is named by other paths and is never in it."""
    asked = {Path(path) for path in named}
    return tuple(file for file in (FILES if files is None else files) if Path(models_dir) / file.path in asked)


def missing(models_dir: Path, files: tuple[ModelFile, ...] | None = None) -> list[ModelFile]:
    """Files that are not there, or not of the expected size (a copy or a download cut short)."""
    absent = []
    for file in FILES if files is None else files:
        target = Path(models_dir) / file.path
        try:
            ok = target.stat().st_size == file.size
        except OSError:
            ok = False
        if not ok:
            absent.append(file)
    return absent


def _clear_leftovers(target: Path) -> None:
    """Unfinished files of earlier downloads of this file (a window closed in the middle).
    One that another download is writing right now is held open and stays."""
    try:
        siblings = list(target.parent.iterdir())
    except OSError:
        return
    for path in siblings:
        if path.name.startswith(target.name + ".") and path.name.endswith(".part"):
            try:
                path.unlink()
            except OSError:
                pass


def _put_in_place(part: Path, target: Path) -> None:
    """os.replace, with patience: Windows refuses while something else has the place for a
    moment - another download putting the same file there, or an antivirus looking at it."""
    for attempt in range(PLACE_ATTEMPTS):
        try:
            os.replace(part, target)
            return
        except PermissionError:
            if attempt == PLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.2)


def fetch(file: ModelFile, models_dir: Path, say: Callable[[str], None]) -> None:
    """Download one file next to its place, check it, then move it in. Raises DownloadError;
    nothing unverified is left behind. The unfinished file has a name of its own: two
    downloads of one file (two launcher windows) never write into each other."""
    target = Path(models_dir) / file.path
    name = target.name
    part = None
    digest = hashlib.sha256()
    received = 0
    megabytes = max(file.size / 1e6, 0.1)
    request = urllib.request.Request(file.url, headers={"User-Agent": "tolmach"})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _clear_leftovers(target)
        handle, part_name = tempfile.mkstemp(prefix=name + ".", suffix=".part", dir=target.parent)
        part = Path(part_name)
        with os.fdopen(handle, "wb") as out, urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            next_mark = 10
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                out.write(block)
                digest.update(block)
                received += len(block)
                percent = min(100, received * 100 // file.size) if file.size else 100
                if percent >= next_mark:
                    say(f"  {name}: {percent}% ({received / 1e6:.0f} of {megabytes:.0f} MB)")
                    next_mark = percent - percent % 10 + 10
        if received != file.size:
            raise DownloadError(f"{name}: got {received} bytes, expected {file.size}")
        if digest.hexdigest() != file.sha256:
            raise DownloadError(f"{name}: the checksum does not match - this is not the expected file")
        _put_in_place(part, target)
    except urllib.error.HTTPError as e:
        raise DownloadError(f"{name}: the server answered {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise DownloadError(f"{name}: {reason}") from e
    except http.client.HTTPException as e:        # the connection was cut in the middle of an answer
        raise DownloadError(f"{name}: the connection broke ({type(e).__name__})") from e
    finally:
        if part is not None:
            try:
                part.unlink()
            except OSError:
                pass
        if target.parent != Path(models_dir):
            try:
                target.parent.rmdir()  # a subfolder made for this file and left empty; never the models folder
            except OSError:
                pass


def ensure(models_dir: Path, say: Callable[[str], None], files: tuple[ModelFile, ...] | None = None) -> bool:
    """Download every missing file. False as soon as one fails; what was fetched stays."""
    for file in missing(models_dir, files):
        size = f"{file.size / 1e6:.0f} MB" if file.size >= 1e6 else f"{max(1, file.size // 1000)} KB"
        say(f"  downloading {Path(file.path).name} ({size})")
        try:
            fetch(file, models_dir, say)
        except DownloadError as e:
            say(f"  FAILED: {e}")
            return False
    return True


def main() -> int:
    from tolmach import paths

    folder = paths.models_dir()
    print(f"Models folder: {folder}", flush=True)
    if not missing(folder):
        print("All model files are in place.", flush=True)
        return 0
    return 0 if ensure(folder, lambda line: print(line, flush=True)) else 1


if __name__ == "__main__":
    sys.exit(main())
