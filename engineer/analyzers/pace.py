"""Race pace: save or push, measured (13 Nov: "controlling race pace should be accounted for";
console-only drivers "can't ask in real time", so the engineer volunteers more).

Each lap you finish (races, from lap 3, not under the safety car, not on an in/out lap):
- The target: the planned stop (or the flag) with every tyre `strategy.margin_pct` under the
  wear limit (yours if you set one). From your tyre wear and its rate (analyzers/tyres.py),
  `target()` works out how much a lap you'd have to save to make it, or how much is in hand.
- Manage: you'd need to save `manage_min_save_pct` (0.3 %) a lap or more: "Manage the tyres,
  save about one percent a lap to make lap twelve." Every `manage_every_laps` (3; 2 in console
  mode) while it holds. NORMAL priority: it decides whether the plan works.
- Push (console mode only; with push-to-talk you ask "can I push?"): `push_margin_pct` (12 %)
  in hand at the stop or the flag: "You've got about twelve percent in hand on these. You can
  push." Every 4 laps.
- "I'm managing" / "I'm pushing" (push-to-talk): the wear rate before, and two laps later what
  it changed ("managing's working, wear down from three to two percent a lap"). Measured, never
  assumed: the effect of managing is whatever your laps show.
- Console mode also gets the gaps ahead and behind every `status_every_laps` (3) laps, with
  which way they're going.
"""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np

from engineer.bus import events as ev
from engineer.config import Pace
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.state.race_state import RaceState
from engineer.tools.rivals import car_at, gap_between

PUSH_EVERY_LAPS = 4
CONSOLE_WAIT_S = 45.0
MODE_LAPS = 2


class PaceAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, features, tyres, strategy,
                 director: RadioDirector, settings: Pace | None = None,
                 console: bool = False):
        self.state, self.features, self.tyres, self.strategy = state, features, tyres, strategy
        self.director = director
        self.cfg = settings or Pace()
        self.console = console
        self._reset()
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SessionStarted, lambda e: self._reset())

    def _reset(self) -> None:
        self.manage_said = -99
        self.push_said = -99
        self.status_said = -99
        self.mode: tuple[str, int, float] | None = None    # (manage|push, from lap, rate then)
        self.gaps: dict[str, list[tuple[int, int, float]]] = {"ahead": [], "behind": []}

    # -- the numbers -----------------------------------------------------------------------------

    def target(self) -> dict | None:
        """{save, margin, to_lap, flag, laps} for the worst tyre against the plan: save > 0 is
        what you'd have to save a lap (%), margin what's in hand at the stop or flag (%)."""
        st = self.tyres.status() if self.tyres is not None else None
        sa = self.strategy
        total = self.state.session.total_laps or 0
        if st is None or st.worst_pct is None or not total or np.isnan(st.wear_rate).all():
            return None
        plan = sa.announced if sa is not None else None
        stop = plan.next_stop if plan is not None else None
        end = stop.lap if stop is not None else total
        laps = end - st.lap
        if laps <= 0:
            return None
        limit = sa._limit() if sa is not None else st.limit_pct
        target = limit - (sa.cfg.margin_pct if sa is not None else 5.0)
        proj = st.wear + np.nan_to_num(st.wear_rate, nan=0.0) * laps
        i = int(np.nanargmax(proj))
        rate, wear = float(st.wear_rate[i]), float(st.wear[i])
        if math.isnan(rate):
            return None
        need = (target - wear) / laps
        return {"save": rate - need, "margin": target - float(proj[i]), "to_lap": end,
                "flag": stop is None, "laps": laps, "rate": rate}

    def _lap_rates(self, since_lap: int | None = None) -> list[float]:
        """Worst-tyre wear gained each lap on the current set."""
        p = self.state.player_index
        laps = [f for f in self.features.for_car(p)] if p is not None else []
        if not laps:
            return []
        stint = laps[-1].stint
        laps = [f for f in laps if f.stint == stint and not f.pit_out_lap]
        worst = [(f.lap, max(w for w in f.wear if w is not None and not math.isnan(w)))
                 for f in laps if any(w is not None and not math.isnan(w) for w in f.wear)]
        out = [b - a for (la, a), (lb, b) in pairwise(worst)
               if lb == la + 1 and (since_lap is None or lb > since_lap)]
        return out

    # -- push-to-talk ----------------------------------------------------------------------------

    def declare(self, mode: str) -> None:
        """'I'm managing' / 'I'm pushing': remember the rate now, report in MODE_LAPS laps."""
        rates = self._lap_rates()[-3:]
        p = self.state.player_index
        lap = self.features.for_car(p)[-1].lap if p is not None and self.features.for_car(p) \
            else 0
        self.mode = (mode, lap, float(np.mean(rates)) if rates else math.nan)

    # -- each lap --------------------------------------------------------------------------------

    def _say(self, topic: str, text: str, priority: int = Priority.LOW) -> None:
        # Console players can't ask again, so their calls wait longer for a gap in the radio.
        self.director.submit(RadioCall(topic=topic, text=text, priority=priority,
                                       max_wait_s=CONSOLE_WAIT_S if self.console else 20.0))

    def on_lap(self, e: ev.LapCompleted) -> None:
        s, me = self.state, self.state.player_index
        if not self.cfg.enabled or me is None or e.car_idx != me or not s.session.is_race:
            return
        self._track_gaps(e.lap)
        if self.mode is not None and e.lap >= self.mode[1] + MODE_LAPS:
            mode, start, before = self.mode
            after = self._lap_rates(since_lap=start)
            self.mode = None
            if after and not math.isnan(before):
                self._say("pace", phrases.pace_mode_result_phrase(mode, before,
                                                                  float(np.mean(after))),
                          Priority.NORMAL)
                return
        if e.lap < 3 or e.safety_car or e.pit_in_lap or e.pit_out_lap \
                or s.session.safety_car_status in (1, 2):
            return
        t = self.target()
        if t is not None and t["laps"] >= 2:
            every = 2 if self.console else self.cfg.manage_every_laps
            if t["save"] >= self.cfg.manage_min_save_pct and e.lap - self.manage_said >= every:
                self.manage_said = e.lap
                self._say("pace", phrases.manage_signal_phrase(t["save"], t["to_lap"], t["flag"]),
                          Priority.NORMAL)
                return
            if self.console and t["margin"] >= self.cfg.push_margin_pct and t["laps"] >= 3 \
                    and e.lap - self.push_said >= PUSH_EVERY_LAPS:
                self.push_said = e.lap
                self._say("pace", phrases.push_signal_phrase(t["margin"]))
                return
        if self.console and e.lap - self.status_said >= self.cfg.status_every_laps:
            text = self._status()
            if text:
                self.status_said = e.lap
                self._say("console_status", text)

    def _track_gaps(self, lap: int) -> None:
        s, c, me = self.state, self.state.cars, self.state.player_index
        if np.isnan(c.position[me]):
            return
        for side, off in (("ahead", -1), ("behind", 1)):
            car = car_at(s, c.position[me] + off)
            gap = gap_between(s, me, car) if car is not None else None
            hist = [x for x in self.gaps[side] if x[1] == car][-3:] if car is not None else []
            self.gaps[side] = hist + ([(lap, car, abs(gap))] if gap is not None else [])

    def _status(self) -> str:
        """Console mode: the cars ahead and behind, and which way the gaps are going."""
        out = {}
        for side in ("ahead", "behind"):
            h = self.gaps[side]
            if not h or h[-1][2] > 10.0:
                continue
            lap, car, gap = h[-1]
            trend = (gap - h[0][2]) / (lap - h[0][0]) if len(h) >= 2 and lap > h[0][0] else None
            n = self.state.cars.names[car]
            out[side] = ((n.title() if n else "the car"), gap, trend)
        return phrases.status_phrase(out.get("ahead"), out.get("behind")) if out else ""
