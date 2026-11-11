"""What the engineer knows about the others, and the weather (12 Nov: from the real team radio,
the engineer's most common unprompted topics were the weather, other drivers' penalties and
what the cars around you are doing).

Shared by the proactive calls (analyzers/weather.py, analyzers/rivals.py) and the push-to-talk
answers, so a call and an answer never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from engineer.features.laps import LapHistory
from engineer.state.race_state import RaceState

WEATHER_NAMES = {0: "clear", 1: "light cloud", 2: "overcast", 3: "light rain", 4: "heavy rain",
                 5: "a storm"}
RAIN = 3                       # weather codes from here up are rain (spec: 3 light rain)


# -- gaps ----------------------------------------------------------------------------------------

def car_at(state: RaceState, position: float) -> int | None:
    c = state.cars
    hits = np.flatnonzero(c.active & (c.position == position))
    return int(hits[0]) if hits.size else None


def gap_between(state: RaceState, a: int, b: int) -> float | None:
    """Seconds from car a back to car b (positive: b is behind a), from the intervals."""
    c = state.cars
    pa, pb = c.position[a], c.position[b]
    if np.isnan(pa) or np.isnan(pb):
        return None
    sign, lo, hi = (1, pa, pb) if pa < pb else (-1, pb, pa)
    total = 0.0
    for pos in range(int(lo) + 1, int(hi) + 1):
        car = car_at(state, pos)
        if car is None or np.isnan(c.delta_to_car_in_front_ms[car]):
            return None
        total += c.delta_to_car_in_front_ms[car]
    return sign * total / 1000.0


# -- weather -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WeatherChange:
    kind: str                  # "rain" (coming) or "dry" (rain stopping)
    minutes: int               # from now, as the game's forecast gives it
    rain_pct: int
    weather: int               # the forecast weather at that time (0-5)


def raining(state: RaceState) -> bool:
    return (state.session.weather or 0) >= RAIN


def next_weather_change(state: RaceState, rain_pct: int = 40,
                        horizon_min: int = 30) -> WeatherChange | None:
    """The first forecast sample for this session that changes things: rain (rain weather or a
    rain chance of rain_pct+) while it's dry, or dry weather with a low chance while it's wet."""
    s = state.session
    wet_now = raining(state)
    for st, minutes, weather, pct in sorted(s.forecast, key=lambda x: x[1]):
        if st != s.session_type or minutes <= 0 or minutes > horizon_min:
            continue
        wet = weather >= RAIN or pct >= rain_pct
        if wet and not wet_now:
            return WeatherChange("rain", minutes, pct, weather)
        if wet_now and not wet:
            return WeatherChange("dry", minutes, pct, weather)
    return None


def minutes_to_lap(state: RaceState, minutes: int) -> int | None:
    """Roughly which lap you'll be on in that many minutes, from your last lap time."""
    c, p = state.cars, state.player_index
    if p is None or np.isnan(c.lap[p]) or np.isnan(c.last_lap_time_ms[p]) \
            or c.last_lap_time_ms[p] <= 0:
        return None
    lap = int(c.lap[p]) + int(minutes * 60000 // c.last_lap_time_ms[p])
    total = state.session.total_laps or 0
    return lap if not total or lap <= total else None


# -- rivals --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RivalReport:
    car: int
    position: int | None
    gap_s: float | None            # positive: they're behind you
    compound: int | None           # visual compound
    tyre_age: int | None
    stops: int
    wear_pct: float | None         # their worst tyre, None when not shared (online players)
    pace_vs_you_s: float | None    # their recent clean laps minus yours, same laps (+ = slower)
    trend_s: float | None          # how their lap times are changing, s per lap (+ = slowing)
    penalty_s: int


def _clean(history: LapHistory, car: int) -> list:
    return [r for r in history.for_car(car) if r.clean and r.lap_time_ms]


def rival_report(state: RaceState, history: LapHistory, car: int) -> RivalReport:
    c, me = state.cars, state.player_index
    num = lambda v: None if np.isnan(v) else int(v)
    wear = c.tyre_wear_pct[car]
    worst = None if np.all(np.isnan(wear)) else float(np.nanmax(wear))
    theirs = _clean(history, car)
    # Only laps since their last stop count for the trend (new tyres reset it).
    age = num(c.tyre_age_laps[car])
    stint = theirs[-4:]
    if age is not None and theirs:
        stint = [r for r in theirs if r.lap > theirs[-1].lap - age][-4:]
    trend = None
    if len(stint) >= 3:
        xs = np.array([r.lap for r in stint], float)
        ys = np.array([r.lap_time_ms for r in stint], float) / 1000.0
        trend = float(np.polyfit(xs, ys, 1)[0])
    pace = None
    if me is not None and me != car:
        mine = {r.lap: r.lap_time_ms for r in _clean(history, me)}
        common = [(r.lap_time_ms - mine[r.lap]) / 1000.0 for r in theirs if r.lap in mine][-3:]
        pace = float(np.mean(common)) if len(common) >= 2 else None
    return RivalReport(car, num(c.position[car]),
                       gap_between(state, me, car) if me is not None and me != car else None,
                       num(c.visual_compound[car]), age,
                       num(c.num_pit_stops[car]) or 0, worst, pace, trend,
                       num(c.penalties_s[car]) or 0)


def penalised_near(state: RaceState, me: int, positions: int = 2) -> list[tuple[int, int]]:
    """(car, seconds) for the cars within `positions` places of you carrying a time penalty."""
    c = state.cars
    out = []
    if np.isnan(c.position[me]):
        return out
    for car in np.flatnonzero(c.active):
        car = int(car)
        if car == me or np.isnan(c.position[car]) or np.isnan(c.penalties_s[car]):
            continue
        if abs(c.position[car] - c.position[me]) <= positions and c.penalties_s[car] > 0:
            out.append((car, int(c.penalties_s[car])))
    return sorted(out, key=lambda x: c.position[x[0]])

