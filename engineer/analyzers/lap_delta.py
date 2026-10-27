"""Lap delta analyzer: when the player finishes a lap, ask the director to say how it compared.

Invalid laps, in-laps, out-laps and race lap 1 are called out instead of compared (see
tools/queries.lap_delta for the rules).
"""

from __future__ import annotations

from engineer.bus import events as ev
from engineer.features.laps import LapHistory
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.radio.phrases import lap_delta_phrase
from engineer.state.race_state import RaceState
from engineer.tools.queries import lap_delta

TOPIC = "lap_delta"


class LapDeltaAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, history: LapHistory,
                 director: RadioDirector, verbosity: str = "normal", max_wait_s: float = 3.0):
        self.state = state
        self.history = history
        self.director = director
        self.verbosity = verbosity
        self.max_wait_s = max_wait_s
        bus.subscribe(ev.LapCompleted, self.on_lap)

    def on_lap(self, e: ev.LapCompleted) -> None:
        if e.car_idx != self.state.player_index:
            return
        d = lap_delta(self.history, e.car_idx, e.lap)
        if d is None:
            return
        self.director.submit(RadioCall(
            topic=TOPIC,
            text=lap_delta_phrase(d, self.verbosity),
            priority=Priority.NORMAL,
            max_wait_s=self.max_wait_s,
            origin_t_ns=d.lap.t_ns,
        ))
