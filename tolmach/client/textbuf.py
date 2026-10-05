"""Text of one dictation session: finished phrases plus the current draft."""
from __future__ import annotations

SENTENCE_END = ".!?…"
CLOSERS = "\"'»”)]"


def _unfinished(text: str) -> bool:
    """True when `text` stops in the middle of a sentence."""
    body = text.rstrip(CLOSERS)
    return bool(body) and body[-1] not in SENTENCE_END


def _continued(text: str) -> str:
    """The recogniser starts every piece it is given with a capital. A piece that follows
    an unfinished one continues its sentence, so the capital goes; an abbreviation (USB)
    keeps its, and so does a word in Latin letters - that is a name or a term (GitHub,
    Windows), written the way it is written. A Russian name at the seam is lowered too -
    rarer than a cut in mid-sentence."""
    if text[:1].isupper() and not text[:1].isascii() and not text[1:2].isupper():
        return text[0].lower() + text[1:]
    return text


class TextBuffer:
    def __init__(self) -> None:
        self._phrases: list[str] = []
        self._draft = ""

    def _joined(self, text: str) -> str:
        if self._phrases and _unfinished(self._phrases[-1]):
            return _continued(text)
        return text

    def set_draft(self, text: str) -> None:
        self._draft = text.strip()

    def add_phrase(self, text: str) -> None:
        """A finished phrase replaces the draft it grew from; blank phrases are dropped."""
        text = text.strip()
        if text:
            self._phrases.append(self._joined(text))
        self._draft = ""

    def final(self) -> str:
        """What gets pasted: finished phrases only."""
        return " ".join(self._phrases)

    def display(self) -> str:
        """What the overlay shows: finished phrases and the draft."""
        parts = list(self._phrases)
        if self._draft:
            parts.append(self._joined(self._draft))
        return " ".join(parts)


def tail(text: str, limit: int) -> str:
    """The last `limit` characters, cut at a word boundary and marked with an ellipsis."""
    if len(text) <= limit:
        return text
    start = len(text) - limit
    if text[start - 1] != " ":
        space = text.find(" ", start)
        if space != -1:
            start = space + 1
    return "…" + text[start:]
