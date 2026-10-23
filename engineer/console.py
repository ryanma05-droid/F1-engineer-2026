"""Console output shared by live mode and replay: one line per completed player lap.

Live and replay use this same printer on the same events, so a replay prints identical lines.
"""

from __future__ import annotations

import math
import sys
from typing import TextIO

from engineer.bus import events as ev
from engineer.ingest.formats import f1_2025 as f
from engineer.state.race_state import WHEELS, RaceState


def fmt_lap(ms: int | None) -> str:
    if not ms:
        return "--:--.---"
    m, rem = divmod(int(ms), 60000)
    return f"{m}:{rem // 1000:02d}.{rem % 1000:03d}"


def fmt_sector(ms: int | None) -> str:
    if not ms:
        return "--.---"
    if ms >= 60000:
        return fmt_lap(ms)
    return f"{ms // 1000}.{ms % 1000:03d}"


def fmt_compound(visual: int | None, actual: float | None) -> str:
    if visual is None:
        return "Unknown tyre"
    name = f.VISUAL_COMPOUNDS.get(visual, f"Compound {visual}")
    if actual is not None and not math.isnan(actual):
        act = f.ACTUAL_COMPOUNDS.get(int(actual))
        if act and act != name:
            return f"{name} ({act})"
    return name


def fmt_wear(values) -> str:
    parts = []
    for wheel, v in zip(WHEELS, values):
        parts.append(f"{wheel} {'?' if math.isnan(v) else f'{v:.1f}'}%")
    return " ".join(parts)


def lap_line(e: ev.LapCompleted, state: RaceState) -> str:
    c = state.cars
    i = e.car_idx
    flags = []
    if e.invalid:
        flags.append("INVALID")
    if e.pit_in_lap:
        flags.append("IN LAP")
    if e.pit_out_lap:
        flags.append("OUT LAP")
    age = "?" if e.tyre_age_laps is None else str(e.tyre_age_laps)
    return (
        f"Lap {e.lap:>2}  {fmt_lap(e.lap_time_ms)}  "
        f"S1 {fmt_sector(e.sector1_ms)}  S2 {fmt_sector(e.sector2_ms)}  "
        f"S3 {fmt_sector(e.sector3_ms)}  "
        f"{fmt_compound(e.visual_compound, c.actual_compound[i])} age {age}  "
        f"wear {fmt_wear(c.tyre_wear_pct[i])}"
        + (f"  [{', '.join(flags)}]" if flags else "")
    )


class LapPrinter:
    """Subscribes to the bus and prints the player's laps and session changes."""

    def __init__(self, bus: ev.EventBus, state: RaceState, out: TextIO | None = None):
        self.state = state
        self.out = out or sys.stdout
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SessionStarted, self.on_session_started)
        bus.subscribe(ev.SessionEnded, self.on_session_ended)

    def _print(self, line: str) -> None:
        print(line, file=self.out, flush=True)

    def on_session_started(self, e: ev.SessionStarted) -> None:
        s = self.state.session
        laps = f", {s.total_laps} laps" if s.is_race and s.total_laps else ""
        self._print(f"== {s.session_name} at {s.track_name}{laps} (session {e.session_uid:016x})")

    def on_session_ended(self, e: ev.SessionEnded) -> None:
        self._print(f"== Session {e.session_uid:016x} ended")

    def on_lap(self, e: ev.LapCompleted) -> None:
        if e.car_idx != self.state.player_index:
            return
        self._print(lap_line(e, self.state))
