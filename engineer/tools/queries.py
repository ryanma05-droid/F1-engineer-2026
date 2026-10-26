"""Query tools: analyses with plain, structured answers.

Proactive radio calls and (Phase 6) push-to-talk questions both call these functions, so the
engineer gives the same answer whether it volunteers it or you ask for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from engineer.features.laps import LapHistory, LapRecord

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
