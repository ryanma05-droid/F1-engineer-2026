"""Lap delta analyzer: when the player finishes a lap, ask the director to say how it compared.

Invalid laps, in-laps, out-laps and race lap 1 are called out instead of compared (see
tools/queries.lap_delta for the rules).

A flashback to just before the line re-drives the lap (Brazil, 7 Nov: a spin at turn 1, rewound to
0.4 s before the line, three times on one lap). The lap time already said isn't said again when
the re-driven lap is within SAME_LAP_MS of it: it's the same news.
"""

from __future__ import annotations

from engineer.bus import events as ev
from engineer.features.laps import LapHistory
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.radio.phrases import lap_delta_phrase
from engineer.state.race_state import RaceState
from engineer.tools.queries import lap_delta

TOPIC = "lap_delta"
SAME_LAP_MS = 100


class LapDeltaAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, history: LapHistory,
                 director: RadioDirector, verbosity: str = "normal", max_wait_s: float = 3.0):
        self.state = state
        self.history = history
        self.director = director
        self.verbosity = verbosity
        self.max_wait_s = max_wait_s
        self.said: dict[int, int] = {}                  # lap -> lap time said on the radio
        self._queued: dict[str, tuple[int, int]] = {}   # call text -> (lap, lap time)
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.RadioMessage, self._on_said)
        bus.subscribe(ev.SessionStarted, lambda e: (self.said.clear(), self._queued.clear()))

    def _on_said(self, e: ev.RadioMessage) -> None:
        if e.topic == TOPIC and e.text in self._queued:
            lap, ms = self._queued.pop(e.text)
            self.said[lap] = ms

    def on_lap(self, e: ev.LapCompleted) -> None:
        if e.car_idx != self.state.player_index:
            return
        d = lap_delta(self.history, e.car_idx, e.lap)
        if d is None:
            return
        before = self.said.get(e.lap)
        if before is not None and abs(e.lap_time_ms - before) < SAME_LAP_MS:
            return
        text = lap_delta_phrase(d, self.verbosity)
        self._queued[text] = (e.lap, e.lap_time_ms)
        self.director.submit(RadioCall(
            topic=TOPIC,
            text=text,
            priority=Priority.NORMAL,
            max_wait_s=self.max_wait_s,
            origin_t_ns=d.lap.t_ns,
        ))
