"""What the lap delta overlay shows, worked out without any window code (so it's tested here).

The engine sends a small dict per reading (`message`); the overlay process turns it into what to
draw (`view`): the delta as text ("+0.23", red when slower, green when quicker, white within
two hundredths), the bar (-1..+1 of `bar_s`), "vs L4", and the latest corner split ("T3 +0.14").
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SLOWER = "#ff4d4d"
QUICKER = "#3ddc84"
EVEN = "#f2f2f2"
EVEN_S = 0.02                 # within this of the reference: white


def message(reading) -> dict | None:
    """A DeltaReading (state/live_delta.py) as a small picklable dict, None = nothing to show."""
    if reading is None or not math.isfinite(reading.delta_s):
        return None
    c = reading.corner
    return {"delta": round(float(reading.delta_s), 3), "lap": reading.lap,
            "ref": reading.ref_lap,
            "corner": None if c is None else {"turn": c.corner, "gain": round(float(c.gain_s), 3)}}


def delta_text(d: float) -> str:
    """+0.23 / -0.23 / 0.00; one decimal from 10 s, capped at 99.9."""
    a = min(abs(d), 99.9)
    body = f"{a:.2f}" if a < 9.995 else f"{a:.1f}"
    if body.strip("0.") == "":
        return "0.00"
    return ("+" if d > 0 else "-") + body


def colour(d: float) -> str:
    return EVEN if abs(d) < EVEN_S else SLOWER if d > 0 else QUICKER


@dataclass(frozen=True)
class View:
    text: str                 # "+0.23"
    colour: str
    bar: float                # -1 (quicker, full) .. +1 (slower, full)
    ref: str                  # "vs L4"
    corner: str               # "T3 +0.14", or ""
    corner_colour: str


def view(msg: dict | None, bar_s: float = 1.0) -> View | None:
    if msg is None:
        return None
    d = float(msg["delta"])
    c = msg.get("corner")
    corner = f"T{c['turn']} {delta_text(c['gain'])}" if c else ""
    return View(delta_text(d), colour(d), max(-1.0, min(1.0, d / bar_s)) if bar_s > 0 else 0.0,
                f"vs L{msg['ref']}", corner, colour(c["gain"]) if c else EVEN)


SAMPLE = {"delta": 0.23, "lap": 5, "ref": 4, "corner": {"turn": 3, "gain": 0.14}}  # move mode


def place(x: int | None, y: int | None, w: int, h: int,
          screen: tuple[int, int, int, int], primary_w: int) -> tuple[int, int]:
    """Where the window goes: the saved spot if it's still on a screen (a monitor may have been
    unplugged since), else top centre of the main monitor. screen = (left, top, width, height)
    of all monitors together; the main monitor starts at (0, 0)."""
    left, top, sw, sh = screen
    if x is None or y is None or x < left or y < top or x + w > left + sw or y + h > top + sh:
        return (primary_w - w) // 2, 80
    return x, y
