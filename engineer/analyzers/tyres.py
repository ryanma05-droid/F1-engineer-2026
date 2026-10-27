"""Tyre coaching: wear status, wear warnings, temperatures, pace drop-off and damage.

All tyre calls are LOW priority (a lap delta always goes first) and wait for a straight. At most
one wear call per lap: damage beats a wear warning, which beats the routine status call.

When each call happens (player's car only):
- Status: every `status_every_laps` laps of a stint ("front right worst at nineteen percent, about
  four percent a lap"). Once the worst tyre passes `laps_left_from_pct`, it says laps left instead.
- Wear warning: the first lap the worst tyre passes each of `wear_warnings_pct`.
- Damage / blisters: a wheel's blistering, or tyre damage beyond its wear, is 10+ points higher
  than last called.
- Temperatures: a sector where a wheel (or both wheels of an axle) spent `temp_min_fraction` of
  the sector outside the carcass window. Never on race lap 1, in/out laps, or under SC/VSC. The
  same axle and direction isn't called again for 3 laps.
- Drop-off: fuel-corrected pace over the last 4 clean laps fading by more than
  `dropoff_ms_per_lap`, on this lap AND the lap before (one mistake lap can't trigger it);
  not repeated within 3 laps.
"""

from __future__ import annotations

import math

from engineer.bus import events as ev
from engineer.config import Tyres
from engineer.features.lap_features import LapFeatureBuilder, LapFeatures
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.state.race_state import RaceState
from engineer.tools.queries import TyreStatus, pace_trend_ms_per_lap, tyre_status

DAMAGE_STEP_PCT = 10.0
REPEAT_LAPS = 3
AXLES = ((2, 3), (0, 1))          # fronts, rears (indices into RL RR FL FR)


class TyreAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, features: LapFeatureBuilder,
                 director: RadioDirector, settings: Tyres | None = None,
                 priors: WearPriors | None = None, verbosity: str = "normal"):
        self.state = state
        self.features = features
        self.director = director
        self.cfg = settings or Tyres()
        self.priors = priors
        self.verbosity = verbosity
        self._reset()
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SectorCompleted, self.on_sector)
        bus.subscribe(ev.SessionStarted, lambda e: self._reset())
        bus.subscribe(ev.Flashback, lambda e: self._reset())

    def _reset(self) -> None:
        self.warned: dict[int, set[float]] = {}            # stint -> thresholds already called
        self.damage_called = [0.0] * 4
        self.blisters_called = [0.0] * 4
        self.temp_called: dict[tuple[str, tuple[int, ...]], int] = {}   # (kind, wheels) -> lap
        self.temp_called_lap: dict[tuple[int, int], bool] = {}          # (lap, axle) -> said
        self.dropoff_lap = -math.inf

    def status(self, car_idx: int | None = None) -> TyreStatus | None:
        """The numbers behind the calls (also what push-to-talk will answer from)."""
        car = self.state.player_index if car_idx is None else car_idx
        return tyre_status(self.features, car, self.cfg.limit_pct, self.priors)

    # -- laps ----------------------------------------------------------------------------------

    def on_lap(self, e: ev.LapCompleted) -> None:
        if not self.cfg.enabled or e.car_idx != self.state.player_index:
            return
        laps = self.features.for_car(e.car_idx)
        if not laps or laps[-1].lap != e.lap:
            return
        lap = laps[-1]
        st = self.status(e.car_idx)
        if st is None:
            return
        text, topic, prio = self._wear_call(st, lap)
        if text:
            self._submit(topic, text, prio, e)
        else:
            self._dropoff_call(lap, e)
        if self.cfg.temp_calls and self._temps_count(lap):
            self._temp_call(lap, sector=3, e=e)

    def _wear_call(self, st: TyreStatus, lap: LapFeatures) -> tuple[str, str, int]:
        cfg, v = self.cfg, self.verbosity
        # Damage and blisters first: they can change the race.
        # In F1 25 tyre "damage" equals wear in normal running (real Austria data), so only
        # damage BEYOND wear counts (e.g. a lock-up flat spot or a puncture).
        extra_damage = st.damage - st.wear
        for kind, values, called in (("damage", extra_damage, self.damage_called),
                                     ("blisters", st.blisters, self.blisters_called)):
            for w in range(4):
                pct = values[w]
                if not math.isnan(pct) and pct >= called[w] + DAMAGE_STEP_PCT:
                    called[w] = pct - pct % DAMAGE_STEP_PCT
                    shown = st.damage[w] if kind == "damage" else pct
                    return (phrases.tyre_damage_phrase(w, shown, kind), "tyre_damage",
                            Priority.NORMAL)
        if st.worst_pct is None:
            return "", "", 0
        # Wear warnings: the highest threshold newly passed this stint.
        done = self.warned.setdefault(st.stint, set())
        passed = [t for t in cfg.wear_warnings_pct if st.worst_pct >= t and t not in done]
        if passed:
            done.update(t for t in cfg.wear_warnings_pct if st.worst_pct >= t)
            return (phrases.tyre_warning_phrase(st, max(passed), v, cfg.laps_left_from_pct),
                    "tyre_warning", Priority.LOW)
        if st.stint_lap % cfg.status_every_laps == 0:
            return (phrases.tyre_status_phrase(st, v, cfg.laps_left_from_pct),
                    "tyre_status", Priority.LOW)
        return "", "", 0

    def _dropoff_call(self, lap: LapFeatures, e: ev.LapCompleted) -> None:
        if lap.lap - self.dropoff_lap < REPEAT_LAPS:
            return
        # Confirmed on two laps running, so one mistake lap can't trigger it.
        limit = self.cfg.dropoff_ms_per_lap
        trend = pace_trend_ms_per_lap(self.features, e.car_idx, self.cfg.fuel_s_per_kg)
        before = pace_trend_ms_per_lap(self.features, e.car_idx, self.cfg.fuel_s_per_kg,
                                       skip_latest=1)
        if trend is not None and before is not None and trend > limit and before > limit:
            self.dropoff_lap = lap.lap
            self._submit("tyre_dropoff", phrases.tyre_dropoff_phrase(trend, self.verbosity),
                         Priority.LOW, e)

    # -- temperatures --------------------------------------------------------------------------

    def _temps_count(self, lap: LapFeatures) -> bool:
        """Laps where temperatures say something about the driving, not the situation."""
        return lap.clean and not lap.safety_car and lap.visual_compound not in (7, 8)

    def on_sector(self, e: ev.SectorCompleted) -> None:
        # Sector 3 is handled with the lap (we need the lap's flags, which arrive just after).
        if (not self.cfg.enabled or not self.cfg.temp_calls or e.sector == 3
                or e.car_idx != self.state.player_index):
            return
        s, c, i = self.state, self.state.cars, e.car_idx
        if (c.driver_status[i] in (2, 3) or c.pit_status[i] > 0 or c.current_lap_invalid[i] == 1
                or (s.session.is_race and e.lap == 1) or s.session.safety_car_status in (1, 2)
                or c.visual_compound[i] in (7, 8)):
            return
        temps = self.features.temps.temps_for(i, e.lap)
        if temps is None or temps.safety_car:
            return
        self._temp_call_from(temps, e.lap, e.sector, e)

    def _temp_call(self, lap: LapFeatures, sector: int, e) -> None:
        if lap.temps is not None:
            self._temp_call_from(lap.temps, lap.lap, sector, e)

    def _temp_call_from(self, temps, lap: int, sector: int, e) -> None:
        k = sector - 1
        if temps.samples[k] < 10:
            return
        frac = self.cfg.temp_min_fraction
        for axle_i, axle in enumerate(AXLES):
            if self.temp_called_lap.get((lap, axle_i)):
                continue
            for kind, values in (("hot", temps.hot_frac[k]), ("cold", temps.cold_frac[k])):
                wheels = [w for w in axle if values[w] >= frac]
                if not wheels:
                    continue
                key = (kind, axle)
                if lap - self.temp_called.get(key, -math.inf) < REPEAT_LAPS:
                    continue
                self.temp_called[key] = lap
                self.temp_called_lap[(lap, axle_i)] = True
                self._submit("tyre_temp", phrases.tyre_temp_phrase(kind, wheels, sector),
                             Priority.LOW, e, max_wait_s=min(self.cfg.max_wait_s, 10.0))
                return          # one temperature call at a time

    # -- output --------------------------------------------------------------------------------

    def _submit(self, topic: str, text: str, priority: int, e, max_wait_s: float | None = None):
        if not text:
            return
        self.director.submit(RadioCall(
            topic=topic, text=text, priority=priority,
            max_wait_s=self.cfg.max_wait_s if max_wait_s is None else max_wait_s))
