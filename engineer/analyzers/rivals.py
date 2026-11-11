"""What the cars around you are doing (12 Nov, from the real team radio: other drivers'
penalties, 20 lines; rival tyres and pace, 13; rival pit predictions, 10).

Checked each time you finish a lap, in races, from lap 3, not under the safety car:
- Penalties: a car within `penalty_positions` (2) places of you picks up a time penalty: "Leclerc
  ahead has a five second penalty. You're three point one behind, inside it." / "Norris behind
  has a five second penalty. Even if they get by, stay within five seconds ..." (once per car
  and penalty total). The countdown ("five laps to go", "last lap") repeats it (racecraft).
- Struggling: the car just ahead or behind, within `near_gap_s` (5 s), whose lap times on this
  set of tyres are rising by `pace_drop_s` (0.3 s) a lap or more (and slower than you, when it's
  the car ahead): "Norris ahead is struggling, losing three tenths a lap on fifteen-lap-old
  mediums." Once per car per stint.
- Pit soon: the same cars, worst tyre within `pit_soon_margin_pct` (15) of your tyre limit
  (tyres.limit_pct, 60): "Expect Norris ahead to pit soon, tyres at forty eight percent." Once
  per car per stint. Online players' tyre wear isn't shared: no call for them.
One call a lap at most (penalties first). LOW priority, so not in Quiet.
"""

from __future__ import annotations

import numpy as np

from engineer.bus import events as ev
from engineer.config import Rivals
from engineer.features.laps import LapHistory
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.state.race_state import RaceState
from engineer.tools.rivals import car_at, gap_between, penalised_near, rival_report


class RivalsAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, history: LapHistory,
                 director: RadioDirector, settings: Rivals | None = None,
                 limit_pct: float = 60.0):
        self.state, self.history, self.director = state, history, director
        self.cfg = settings or Rivals()
        self.limit_pct = limit_pct
        self._reset()
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SessionStarted, lambda e: self._reset())

    def _reset(self) -> None:
        self.penalty_said: dict[int, int] = {}            # car -> penalty seconds called
        self.struggling_said: set[tuple[int, int]] = set()   # (car, stops)
        self.pit_soon_said: set[tuple[int, int]] = set()

    def _name(self, car: int) -> str | None:
        n = self.state.cars.names[car] if car < len(self.state.cars.names) else None
        return n.title() if n else None

    def _say(self, topic: str, text: str, priority: int = Priority.LOW) -> None:
        self.director.submit(RadioCall(topic=topic, text=text, priority=priority,
                                       max_wait_s=20.0))

    def on_lap(self, e: ev.LapCompleted) -> None:
        s, c, me = self.state, self.state.cars, self.state.player_index
        if not self.cfg.enabled or e.car_idx != me or me is None or not s.session.is_race:
            return
        if e.lap < 3 or e.safety_car or s.session.safety_car_status in (1, 2) \
                or np.isnan(c.position[me]):
            return
        if self.cfg.penalties and self._penalties(me):
            return
        laps_left = (s.session.total_laps or 0) - e.lap
        for side, offset in (("ahead", -1), ("behind", 1)):
            car = car_at(s, c.position[me] + offset)
            if car is None or c.pit_status[car] > 0:
                continue
            gap = gap_between(s, me, car)
            if gap is None or abs(gap) > self.cfg.near_gap_s:
                continue
            r = rival_report(s, self.history, car)
            key = (car, r.stops)
            slower = side == "behind" or r.pace_vs_you_s is None or r.pace_vs_you_s > 0
            if r.trend_s is not None and r.trend_s >= self.cfg.pace_drop_s and slower \
                    and key not in self.struggling_said:
                self.struggling_said.add(key)
                comp = phrases.say_compound(r.compound) if r.compound is not None else None
                self._say("rivals", phrases.rival_struggling_phrase(
                    self._name(car), side, r.trend_s, r.tyre_age, comp))
                return
            if r.wear_pct is not None and laps_left > 2 \
                    and r.wear_pct >= self.limit_pct - self.cfg.pit_soon_margin_pct \
                    and key not in self.pit_soon_said:
                self.pit_soon_said.add(key)
                self._say("rivals", phrases.rival_pit_soon_phrase(self._name(car), side,
                                                                  r.wear_pct))
                return

    def _penalties(self, me: int) -> bool:
        """A new time penalty on a car near you: say it (NORMAL: it changes the race)."""
        c = self.state.cars
        for car, seconds in penalised_near(self.state, me, self.cfg.penalty_positions):
            if self.penalty_said.get(car, 0) >= seconds:
                continue
            self.penalty_said[car] = seconds
            side = "ahead" if c.position[car] < c.position[me] else "behind"
            self._say("rival_penalty", phrases.rival_penalty_phrase(
                self._name(car), side, seconds, gap_between(self.state, me, car)),
                Priority.NORMAL)
            return True
        return False
