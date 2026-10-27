"""Lap history: every completed lap per car, built from LapCompleted events.

This is a reader of the event stream, so live play, replays and (later) offline training jobs all
build exactly the same history from the same packets.

A lap is "clean" when it can be used as a reference for comparisons: valid, not an in-lap or
out-lap, not lap 1 of a race (it includes the standing start), and no safety car or VSC during it.
(Real Austria race: a lap behind the safety car made the next lap "twenty seven seconds quicker".)
"""

from __future__ import annotations

from dataclasses import dataclass

from engineer.bus import events as ev
from engineer.state.race_state import RaceState


@dataclass(frozen=True)
class LapRecord:
    car_idx: int
    lap: int
    lap_time_ms: int
    sector_ms: tuple[int | None, int | None, int | None]
    invalid: bool
    pit_in_lap: bool
    pit_out_lap: bool
    race_start_lap: bool        # lap 1 of a race: standing start, never a fair reference
    safety_car: bool            # SC or VSC out at any point of the lap
    session_time: float         # when the lap was completed
    t_ns: int | None            # arrival time of the packet that completed it (for latency)

    @property
    def clean(self) -> bool:
        return not (self.invalid or self.pit_in_lap or self.pit_out_lap or self.race_start_lap
                    or self.safety_car)


class LapHistory:
    """Subscribe with `LapHistory(bus, state)`. Construct it BEFORE analyzers that read it: the bus
    calls handlers in subscription order, so the lap is stored before analyzers look for it."""

    def __init__(self, bus: ev.EventBus, state: RaceState, arrival_ns=None):
        self.state = state
        self._arrival_ns = arrival_ns or (lambda: None)
        self.laps: dict[int, list[LapRecord]] = {}
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SessionStarted, lambda e: self.laps.clear())
        bus.subscribe(ev.Flashback, self.on_flashback)

    def on_lap(self, e: ev.LapCompleted) -> None:
        rec = LapRecord(
            car_idx=e.car_idx, lap=e.lap, lap_time_ms=e.lap_time_ms,
            sector_ms=(e.sector1_ms, e.sector2_ms, e.sector3_ms),
            invalid=e.invalid, pit_in_lap=e.pit_in_lap, pit_out_lap=e.pit_out_lap,
            race_start_lap=self.state.session.is_race and e.lap == 1,
            safety_car=e.safety_car,
            session_time=e.session_time, t_ns=self._arrival_ns(),
        )
        laps = self.laps.setdefault(e.car_idx, [])
        # A lap re-driven after a flashback replaces the earlier version.
        laps[:] = [r for r in laps if r.lap < e.lap]
        laps.append(rec)

    def on_flashback(self, e: ev.Flashback) -> None:
        for car, laps in self.laps.items():
            self.laps[car] = [r for r in laps if r.session_time <= e.to_session_time]

    def for_car(self, car_idx: int) -> list[LapRecord]:
        return self.laps.get(car_idx, [])
