"""Query tools: analyses with plain, structured answers.

Proactive radio calls and (Phase 6) push-to-talk questions both call these functions, so the
engineer gives the same answer whether it volunteers it or you ask for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from engineer.features.lap_features import (LapFeatureBuilder, Stint, fit_wear_rate,
                                            summarize_stints)
from engineer.features.laps import LapHistory, LapRecord
from engineer.features.priors import WearPriors

SAME_LAP_THRESHOLD_MS = 50     # deltas smaller than this are "the same"


@dataclass(frozen=True)
class LapDelta:
    lap: LapRecord
    reference: LapRecord | None          # None: nothing clean to compare with
    reason_not_compared: str | None      # "invalid", "in_lap", "out_lap", "race_start", "no_reference"
    delta_ms: int | None                 # lap - reference: negative = quicker
    sector_delta_ms: tuple[int | None, int | None, int | None] = (None, None, None)
    biggest_sector: int | None = None    # 1, 2 or 3: where the most time moved (either way)

    @property
    def compared(self) -> bool:
        return self.delta_ms is not None

    @property
    def reference_is_previous_lap(self) -> bool:
        return self.reference is not None and self.reference.lap == self.lap.lap - 1


def _why_not_clean(lap: LapRecord) -> str | None:
    if lap.invalid:
        return "invalid"
    if lap.pit_in_lap:
        return "in_lap"
    if lap.pit_out_lap:
        return "out_lap"
    if lap.race_start_lap:
        return "race_start"
    return None


def lap_delta(history: LapHistory, car_idx: int, lap: int | None = None) -> LapDelta | None:
    """Compare a car's lap (default: its latest) with its previous clean lap.

    Laps that aren't clean are not compared; the result says why, so the caller can call them out.
    Returns None if the car has no completed laps.
    """
    laps = history.for_car(car_idx)
    if not laps:
        return None
    idx = len(laps) - 1 if lap is None else next(
        (i for i, r in enumerate(laps) if r.lap == lap), None)
    if idx is None:
        return None
    this = laps[idx]
    reason = _why_not_clean(this)
    if reason:
        return LapDelta(this, None, reason, None)
    ref = next((r for r in reversed(laps[:idx]) if r.clean), None)
    if ref is None:
        return LapDelta(this, None, "no_reference", None)

    sector_deltas = tuple(
        (a - b) if a is not None and b is not None else None
        for a, b in zip(this.sector_ms, ref.sector_ms)
    )
    known = [(abs(d), n) for n, d in enumerate(sector_deltas, start=1) if d is not None]
    biggest = max(known)[1] if known else None
    return LapDelta(this, ref, None, this.lap_time_ms - ref.lap_time_ms, sector_deltas, biggest)


# -- tyres (Phase 3) ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TyreStatus:
    car_idx: int
    lap: int                             # the last completed lap these numbers are from
    stint: int
    stint_lap: int                       # laps on this set in this stint
    visual_compound: int | None
    wear: np.ndarray                     # (4,) % at the end of the last lap, RL RR FL FR
    wear_rate: np.ndarray                # (4,) % per lap (NaN = not known yet)
    rate_from_history: bool              # True: rate came from past stints (stint too short)
    worst_wheel: int | None              # index into RL RR FL FR
    worst_pct: float | None
    limit_pct: float
    laps_left: int | None                # laps until any tyre reaches limit_pct
    limiting_wheel: int | None           # the wheel that gets there first
    damage: np.ndarray
    blisters: np.ndarray


def tyre_status(features: LapFeatureBuilder, car_idx: int, limit_pct: float = 60.0,
                priors: WearPriors | None = None) -> TyreStatus | None:
    """Tyre wear now, wear rate and laps left on this set. None before the first lap.

    Wear rate is a straight-line fit over this stint's laps. With fewer than 3 laps in the stint,
    the rate from your past stints on the same track and compound is used instead (if any).
    """
    stint = features.current_stint(car_idx)
    if not stint:
        return None
    last = stint[-1]
    wear = np.array(last.wear, dtype=float)
    rate = fit_wear_rate(stint)
    from_history = False
    if priors is not None and len(stint) < 3:
        prior = priors.get(last.track_id, last.actual_compound)
        if prior is not None:
            rate, from_history = prior, True

    worst = last.worst_wheel
    laps_left = limiting = None
    with np.errstate(invalid="ignore", divide="ignore"):
        left = np.where(rate > 0.05, (limit_pct - wear) / rate, np.nan)
    if not np.isnan(left).all():
        limiting = int(np.nanargmin(left))
        laps_left = max(0, int(round(left[limiting])))
    return TyreStatus(
        car_idx=car_idx, lap=last.lap, stint=last.stint, stint_lap=last.stint_lap,
        visual_compound=last.visual_compound, wear=wear, wear_rate=rate,
        rate_from_history=from_history, worst_wheel=worst,
        worst_pct=None if worst is None else float(wear[worst]), limit_pct=limit_pct,
        laps_left=laps_left, limiting_wheel=limiting,
        damage=np.array(last.damage, dtype=float), blisters=np.array(last.blisters, dtype=float))


def pace_trend_ms_per_lap(features: LapFeatureBuilder, car_idx: int, fuel_s_per_kg: float = 0.03,
                          laps: int = 4) -> float | None:
    """How much slower each lap is getting on this set (ms per lap; positive = slower), after
    allowing for fuel burn: a lighter car is naturally quicker, so each lap time is adjusted to
    what it would be with an empty tank. Uses the last `laps` clean, non-safety-car laps of the
    current stint, and needs all of them to be consecutive-ish (from this stint)."""
    pts = [r for r in features.current_stint(car_idx) if r.clean and not r.safety_car][-laps:]
    if len(pts) < laps:
        return None
    x = np.array([r.stint_lap for r in pts], dtype=float)
    y = np.array([r.lap_time_ms - (0.0 if math.isnan(r.fuel_kg) else r.fuel_kg)
                  * fuel_s_per_kg * 1000 for r in pts])
    return float(np.polyfit(x, y, 1)[0])


def stint_summary(features: LapFeatureBuilder, car_idx: int) -> list[Stint]:
    """Every stint this car has done this session, oldest first."""
    return sorted(summarize_stints(features.for_car(car_idx)), key=lambda s: s.stint)
