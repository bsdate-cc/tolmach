"""The terms dictionary: where the recognised text sounds like a term, the term is written its own way.

The recogniser hears Russian. A developer's "гитхаб" comes out as "Githab", "GetHub" or
"гитхаб" - right in sound, wrong in letters. A term is a written form and the ways it is
said; a run of words that sounds like one of those ways is replaced by the written form.

Nothing is invented: only what was recognised can be replaced, and when in doubt the text
is left alone. Words in Russian letters are ordinary speech until they sound exactly like
a term, word for word; a near miss counts only where the recogniser itself wrote Latin
letters - its own sign that the word is not a Russian one.

Standard library only: the gateway imports this before the model is loaded.
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# ---- how a word sounds ---------------------------------------------------------------------------

# The recogniser writes small numbers as digits; they were said as words.
_DIGITS = {"0": "ноль", "1": "один", "2": "два", "3": "три", "4": "четыре", "5": "пять",
           "6": "шесть", "7": "семь", "8": "восемь", "9": "девять", "10": "десять"}
# Latin, read the way a Russian speaker says an English word. Pairs first, then single letters.
_PAIRS = (("tch", "ч"), ("sch", "ск"), ("wh", "в"), ("th", "т"), ("sh", "ш"), ("ch", "ч"), ("ck", "к"),
          ("ph", "ф"), ("ee", "и"), ("oo", "у"), ("qu", "кв"), ("ay", "ей"), ("ey", "ей"))
_LATIN = dict(zip("abcdefghijklmnopqrstuvwxyz",
                  ("а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м", "н", "о", "п", "к", "р",
                   "с", "т", "у", "в", "в", "кс", "и", "з")))
# Russian letters to sounds, one character a sound. The back vowels are one sound, "a": an
# unstressed "о" is said "а", and an English "u" is read either way ("hub", "pull"). Nothing
# else is blurred - "бетон" must not sound like "питон", nor "GET" like "git".
_SOUNDS = {"а": "a", "о": "a", "у": "a", "ю": "a", "я": "a", "ё": "a",
           "и": "i", "ы": "i", "й": "i", "е": "e", "э": "e",
           "б": "b", "п": "p", "д": "d", "т": "t", "г": "g", "к": "k", "в": "v", "ф": "f",
           "з": "z", "с": "s", "ж": "Z", "ш": "S", "щ": "S", "ч": "C", "ц": "c",
           "х": "x", "л": "l", "м": "m", "н": "n", "р": "r", "ь": "", "ъ": ""}
_VOICELESS = {"b": "p", "d": "t", "g": "k", "v": "f", "z": "s", "Z": "S"}
_MUTE = frozenset("ptkfsSCcx")
_VOWELS = frozenset("aie")
_PARTS = re.compile(r"[^\W_]+")
# "GitHub" is Git and Hub: without the break its "tH" would be read as the pair "th".
_HUMP = re.compile(r"(?<=[a-zа-яё])(?=[A-ZА-ЯЁ])")


def _as_russian(part: str) -> str:
    if part in _DIGITS:
        return _DIGITS[part]
    for latin, russian in _PAIRS:
        part = part.replace(latin, russian)
    return "".join(_LATIN.get(ch, ch) for ch in part)


def _letters(word: str) -> str:
    """The word in Russian letters, lower case: Latin is read the Russian way."""
    return "".join(_as_russian(part) for part in _PARTS.findall(_HUMP.sub(" ", word).lower()))


def _joined(run: list[str], more) -> list[str]:
    """The run with more sounds after it. A consonant said twice in a row is one sound; a
    vowel is a syllable and is never dropped."""
    for sound in more:
        if not run or run[-1] != sound or sound in _VOWELS:
            run.append(sound)
    return run


def _said(letters: str) -> tuple[str, ...]:
    """What a word in Russian letters sounds like."""
    out: list[str] = []
    for ch in letters:
        sound = _SOUNDS.get(ch, "")
        if sound and not (ch == "й" and out and out[-1] == "i"):    # "ий" is one long "и"
            out.append(sound)
    # the end of a word and a voiceless consonant after it take the voice away: "гид" is said "гит"
    for i in reversed(range(len(out))):
        if out[i] in _VOICELESS and (i + 1 == len(out) or out[i + 1] in _MUTE):
            out[i] = _VOICELESS[out[i]]
    return tuple(_joined([], out))


@lru_cache(maxsize=50_000)
def sounds(word: str) -> tuple[str, ...]:
    """The sounds of one word."""
    return _said(_letters(word))


def key(text: str) -> str:
    """How a word or a run of words sounds, as one string."""
    out: list[str] = []
    for word in text.split():
        _joined(out, sounds(word))
    return "".join(out)


# ---- the dictionary ------------------------------------------------------------------------------

MIN_SOUNDS = 3
_COMMENT = re.compile(r"(?:^|\s)#")


@dataclass(frozen=True)
class Term:
    written: str
    said: tuple[str, ...]      # every way it is said; the written form itself comes first


def parse(text: str) -> tuple[list[Term], list[int]]:
    """Terms and the numbers of the lines that are not understood.

    One term per line: `GitHub`, or `main = мэйн, мейн` when it is said in a way its letters
    do not show. `#` at the start of a line or after a space starts a comment (so `C#` is a
    term). The written form counts as a way it is said. A way shorter than three sounds is
    not a way: too many ordinary words sound like it."""
    terms: list[Term] = []
    bad: list[int] = []
    for number, line in enumerate(text.splitlines(), 1):
        line = _COMMENT.split(line, 1)[0].strip()
        if not line:
            continue
        written, _, rest = line.partition("=")
        written = written.strip()
        ways = (written, *(part.strip() for part in rest.split(",")))
        said = tuple(dict.fromkeys(way for way in ways if len(key(way)) >= MIN_SOUNDS))
        if not written or not said:
            bad.append(number)
            continue
        terms.append(Term(written, said))
    return terms, bad


# ---- matching ------------------------------------------------------------------------------------

# A Russian case ending on a term, in letters: "в гитхабе", "докером", "на серверах".
_ENDINGS = ("ами", "ом", "ам", "ах", "ов", "а", "у", "е", "ы", "и")
_ENDING_SOUNDS = 3              # the longest of them, said
_SHORT = 5                      # a way of fewer sounds is taken only from Latin letters
_EDGE_BEFORE = re.compile(r"^[\W_]+")
_EDGE_AFTER = re.compile(r"[\W_]+$")
_LATIN_LETTER = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")
_WORD = re.compile(r"\S+")


def _slack(length: int) -> int:
    """How far a run in Latin letters may be from a term in sound: nothing for a short term,
    more for a long one."""
    return 0 if length < 6 else 1 if length < 9 else 2 if length < 13 else 3


@lru_cache(maxsize=50_000)
def _stems(word: str) -> tuple[tuple[str, ...], ...]:
    """The sounds of the word without its case ending, for every ending it may carry."""
    letters = _letters(word)
    stems = (_said(letters[:-len(ending)]) for ending in _ENDINGS if letters.endswith(ending))
    # an ending is taken off a long word only: "pipe" is not "pip" with an ending
    return tuple(stem for stem in stems if len(stem) >= _SHORT)


@dataclass(frozen=True)
class _Way:
    written: str
    target: str     # the key of one way the term is said
    words: int
    slack: int
    order: int      # the term's place in the dictionary: of two that sound the same the earlier wins


class Table:
    """Terms made ready for matching, every way a term is said filed twice: under its key,
    for words in Russian letters, and under each length of key that could still be it, for
    words in Latin letters."""

    def __init__(self, terms: list[Term] | tuple[Term, ...] = ()) -> None:
        self.exact: dict[str, list[_Way]] = defaultdict(list)
        self.by_length: dict[int, list[_Way]] = defaultdict(list)
        self.span = 0           # the longest run of words worth looking at
        for order, term in enumerate(terms):
            ways: dict[str, int] = {}       # "гит хаб" and "гитхаб" sound the same: the way of more words stands
            for said in term.said:
                if target := key(said):
                    ways[target] = max(ways.get(target, 0), len(said.split()))
            for target, words in ways.items():
                way = _Way(term.written, target, words, _slack(len(target)), order)
                self.span = max(self.span, way.words + 1)
                if len(target) >= _SHORT:       # "git" and "гид" sound the same
                    self.exact[target].append(way)
                for length in range(len(target) - way.slack, len(target) + way.slack + _ENDING_SOUNDS + 1):
                    self.by_length[length].append(way)

    def __bool__(self) -> bool:
        return self.span > 0


def _within(a: str, b: str, limit: int) -> int:
    """The edit distance between two keys if it is at most `limit`, else limit + 1."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        low, high = max(1, i - limit), min(len(b), i + limit)
        current = [limit + 1] * (len(b) + 1)
        if low == 1:
            current[0] = i
        best = limit + 1
        for j in range(low, high + 1):
            value = min(previous[j - 1] + (a[i - 1] != b[j - 1]), previous[j] + 1, current[j - 1] + 1)
            current[j] = value
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous = current
    return min(previous[len(b)], limit + 1)


def correct(text: str, table: Table) -> str:
    """The text with every run of words that sounds like a term replaced by the term."""
    spans = [m.span() for m in _WORD.finditer(text)]
    if not table or not spans:
        return text
    words = [text[a:b] for a, b in spans]
    before = [m.group() if (m := _EDGE_BEFORE.match(w)) else "" for w in words]
    after = [m.group() if (m := _EDGE_AFTER.search(w)) else "" for w in words]
    cores = [w[len(b):len(w) - len(a)] if len(b) + len(a) < len(w) else "" for w, b, a in zip(words, before, after)]
    # a word with a digit in it ("python3", "h264") is a name of its own: it is left as it is
    heard = [() if _DIGIT.search(core) and not core.isdigit() else sounds(core) for core in cores]
    latin = [bool(_LATIN_LETTER.search(core)) for core in cores]
    # "в", "и", "а": a word of one Russian letter beside Latin letters is not a piece of the term
    small = [len(core) == 1 and not has_latin and not core.isdigit() for core, has_latin in zip(cores, latin)]

    found = []      # the closest first; of equals the longer term, the shorter run of words, the earlier line
    for start in range(len(words)):
        run: list[str] = []
        has_latin = False
        for count in range(1, min(table.span, len(words) - start) + 1):
            last = start + count - 1
            if not heard[last] or (count > 1 and (after[last - 1] or before[last])):
                break       # a term is said in one breath: no punctuation inside it, nothing without a sound
            base = len(run)                 # where the last word of the run begins
            _joined(run, heard[last])
            has_latin = has_latin or latin[last]
            # the run as it is, and without the case ending of its last word
            forms = ["".join(run)] + ["".join(_joined(run[:base], stem)) for stem in _stems(cores[last])]
            if not has_latin:
                # Russian letters are ordinary speech until they sound exactly like the term, word for word
                for form in forms:
                    for way in table.exact.get(form, ()):
                        if count <= way.words:
                            found.append((0.0, -len(way.target), count, way.order, start, last + 1, way.written))
                continue
            if small[start] or small[last]:
                continue
            for way in table.by_length.get(len(run), ()):
                if count > way.words + 1:   # a term may be heard in one piece more than it has words ("Local Host")
                    continue
                distance = min(_within(form, way.target, way.slack) for form in forms)
                if distance <= way.slack:
                    found.append((distance / len(way.target), -len(way.target), count, way.order, start, last + 1,
                                  way.written))

    taken: set[int] = set()
    pieces = []                             # (from, to, replacement) in the text, the best matches first
    for _, _, _, _, start, end, written in sorted(found):
        if taken.isdisjoint(range(start, end)):
            taken.update(range(start, end))
            lead = "" if written.startswith(before[start]) else before[start]
            pieces.append((spans[start][0], spans[end - 1][1], lead + written + after[end - 1]))
    for a, b, piece in sorted(pieces, reverse=True):       # from the end, so earlier positions stay true
        text = text[:a] + piece + text[b:]
    return text


# ---- the file ------------------------------------------------------------------------------------

STARTER = Path(__file__).with_name("terms_starter.txt")
RETRY = 5.0         # seconds before a file that could not be read is tried again


def ensure_file(path: Path) -> None:
    """Create the dictionary with the starter set of terms if there is none yet."""
    if not path.exists():
        path.write_text(STARTER.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")


class Dictionary:
    """The terms file, read again whenever it changes. No file, or an empty one, means no
    corrections. apply() never raises and never puts the text into the log."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._stamp: tuple[int, int] | None | bool = False    # False: never looked
        self._failed: tuple[int, int] | None = None           # the stamp of a file that could not be read
        self._retry_at = 0.0
        self._table = Table()

    def _look(self) -> tuple[int, int] | None:
        try:
            st = self._path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _refresh(self) -> None:
        stamp = self._look()
        if stamp == self._stamp:
            return
        if stamp is None:
            self._stamp = stamp
            self._table = Table()
            log.info("terms: no dictionary file, nothing is corrected")
            return
        if stamp == self._failed and time.monotonic() < self._retry_at:
            return
        try:
            terms, bad = parse(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as e:
            # The terms read before stay in force. An editor or an antivirus may only be holding
            # the file for a moment, so it is tried again - quietly, the log has it once.
            if stamp != self._failed:
                log.warning("terms: %s cannot be read (%s)", self._path.name, type(e).__name__)
            self._failed, self._retry_at = stamp, time.monotonic() + RETRY
            return
        self._stamp, self._failed = stamp, None
        self._table = Table(terms)
        log.info("terms: %d loaded from %s", len(terms), self._path.name)
        for number in bad:
            log.warning("terms: line %d of %s is not understood", number, self._path.name)

    def apply(self, text: str) -> str:
        if not text:
            return text
        try:
            self._refresh()
            return correct(text, self._table)
        except Exception:
            log.exception("terms: the correction failed")   # no text in the log
            return text
