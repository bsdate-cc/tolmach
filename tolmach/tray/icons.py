"""Tray icons: one image per glance state."""
from __future__ import annotations

from PIL import Image, ImageDraw

SIZE = 64
GREY = (128, 128, 128, 255)
RED = (220, 50, 47, 255)
YELLOW = (240, 190, 40, 255)

IDLE, RECORDING, FINISHING, DOWN, ERROR = "idle", "recording", "finishing", "down", "error"

_HEAD = (22, 4, 42, 38)
_CRADLE = (13, 14, 51, 48)


def _microphone(draw: ImageDraw.ImageDraw, colour, filled: bool) -> None:
    if filled:
        draw.rounded_rectangle(_HEAD, radius=10, fill=colour)
    else:
        draw.rounded_rectangle(_HEAD, radius=10, outline=colour, width=4)
    draw.arc(_CRADLE, start=0, end=180, fill=colour, width=5)
    draw.line((32, 48, 32, 57), fill=colour, width=5)
    draw.line((21, 58, 43, 58), fill=colour, width=5)


def image(state: str) -> Image.Image:
    """A microphone - a plain disc is what other tray programs use, and two discs
    side by side cannot be told apart. Grey idle, red recording, yellow finishing.
    Outline only: no gateway - a SHAPE on purpose, 'nothing is running' must not
    read as one more colour of 'something is running'. Red cross over it: error."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if state == RECORDING:
        _microphone(draw, RED, filled=True)
    elif state == FINISHING:
        _microphone(draw, YELLOW, filled=True)
    elif state == DOWN:
        _microphone(draw, GREY, filled=False)
    elif state == ERROR:
        _microphone(draw, GREY, filled=True)
        draw.line((10, 10, SIZE - 10, SIZE - 10), fill=RED, width=9)
        draw.line((10, SIZE - 10, SIZE - 10, 10), fill=RED, width=9)
    else:
        _microphone(draw, GREY, filled=True)
    return img
