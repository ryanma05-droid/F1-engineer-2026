"""Your ideal lap: the best you've shown lately, corner by corner, on the tyres you have now.

Not your fastest lap of the race: that's usually on new tyres and low fuel, so later in a stint
it makes everything look slow (4 Nov). Instead:

1. Your last WINDOW (5) clean laps in the current stint (at least MIN_LAPS, 3; a pit stop starts
   again: new tyres reset the pace). Clean = no in/out lap, safety car, invalid lap or race lap 1.
2. Each lap is split into corner segments, from one corner's entry to the next corner's entry
   (the first runs from the line to the first corner), so the segments add up to the lap time.
3. Older laps are moved to "as if driven now": the stint's pace trend (seconds per lap: tyre
   wear slowing you, fuel burn speeding you up) times the laps since, spread over the segments by
   their share of the lap. With a +0.1 s/lap trend, a lap four laps ago counts 0.4 s slower. The
   trend is the median slope between every pair of the stint's clean laps (one messy lap can't
   swing it), between MIN_TREND_S (-0.05) and MAX_TREND_S (+0.25) seconds per lap.
4. Ideal lap = the best corrected time in each segment, added up.

The live engineer (analyzers/corners.py) and the driver report (report/analysis.py, insights.py)
both use these functions, so they agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import numpy as np

WINDOW = 5
MIN_LAPS = 3
# The pace trend is capped: tyre fade in F1 25 is about 0.05-0.15 s/lap (a plain straight line
# over 4 laps with one slow lap in traffic said +0.65 on a real Austria race). Getting quicker is
# capped at about what burning fuel gives: beyond that it's you learning the track (the 5-lap
# Austria fixture: -0.2 s/lap), and older laps shouldn't be credited with it.
TREND_MIN_LAPS = MIN_LAPS
MAX_TREND_S = 0.25
MIN_TREND_S = -0.05


@dataclass(frozen=True)
class Segment:
    name: str              # the corner it belongs to ("turn four"), or "run to turn one"
    number: int | None     # the corner number (None: the run from the line to the first corner)
    start_m: float
    end_m: float | None    # None: to the end of the lap


def segments(track_map) -> list[Segment]:
    cs = sorted(track_map.corners, key=lambda c: c.entry_m)
    if not cs:
        return []
    out = [Segment(f"run to {cs[0].name}", None, 0.0, cs[0].entry_m)]
    for i, c in enumerate(cs):
        out.append(Segment(c.name, c.number, c.entry_m,
                           cs[i + 1].entry_m if i + 1 < len(cs) else None))
    return out


def segment_times(trace, lap_ms: int, segs: list[Segment]) -> list[float]:
    """Seconds in each segment; they add up to the lap time (the last one ends at the line)."""
    marks = [trace.time_at(s.start_m) for s in segs] + [lap_ms / 1000.0]
    marks[0] = 0.0
    return [max(0.0, b - a) for a, b in pairwise(marks)]


@dataclass(frozen=True)
class LapSegments:
    lap: int
    lap_ms: int
    times: list[float]     # per segment, seconds


@dataclass(frozen=True)
class IdealLap:
    ms: int
    laps: tuple[int, ...]          # the laps it was built from
    trend_s: float                 # the stint's pace trend used, seconds per lap
    names: tuple[str, ...]
    numbers: tuple[int | None, ...]
    best: tuple[float, ...]        # corrected best per segment
    best_lap: tuple[int, ...]      # which lap set each one
    last: tuple[float, ...]        # the latest lap's segments
    last_lap: int
    last_ms: int
    spread: tuple[float, ...]      # median minus best per segment (corrected): consistency

    @property
    def gap_ms(self) -> int:
        """The latest lap minus the ideal (positive = slower than your ideal)."""
        return self.last_ms - self.ms

    def losses(self) -> list[tuple[str, int | None, float]]:
        """Where the latest lap lost to the ideal: (name, corner number, seconds), worst first."""
        out = [(n, k, a - b) for n, k, a, b in zip(self.names, self.numbers, self.last, self.best)]
        return sorted(out, key=lambda x: -x[2])


def stint_trend(laps: list[LapSegments]) -> float:
    """Seconds per lap: the median of the slopes between every pair of the stint's clean laps
    (Theil-Sen: a slow lap in traffic or a mistake barely moves it). 0 with fewer than
    TREND_MIN_LAPS laps; limited to MIN_TREND_S..MAX_TREND_S."""
    if len(laps) < TREND_MIN_LAPS:
        return 0.0
    pts = [(lap.lap, lap.lap_ms / 1000.0) for lap in laps]
    slopes = [(yb - ya) / (xb - xa) for i, (xa, ya) in enumerate(pts)
              for xb, yb in pts[i + 1:] if xb != xa]
    if not slopes:
        return 0.0
    return max(MIN_TREND_S, min(MAX_TREND_S, float(np.median(slopes))))


def rolling_ideal(stint: list[LapSegments], names: list[str], numbers: list[int | None],
                  window: int = WINDOW, min_laps: int = MIN_LAPS) -> IdealLap | None:
    """stint: your clean laps in the current stint, oldest first (same segments). The ideal lap
    as of the latest one, None with fewer than min_laps."""
    stint = [lap for lap in stint if len(lap.times) == len(names)]
    recent = stint[-window:]
    if len(recent) < min_laps or not names:
        return None
    trend = stint_trend(stint)
    now = recent[-1].lap
    corrected = []
    for lap in recent:
        extra = trend * (now - lap.lap)                  # "as if driven now"
        total = sum(lap.times) or 1.0
        corrected.append([t + extra * t / total for t in lap.times])
    arr = np.array(corrected)
    best_i = arr.argmin(axis=0)
    best = arr.min(axis=0)
    spread = np.median(arr, axis=0) - best
    last = recent[-1]
    return IdealLap(int(round(best.sum() * 1000)), tuple(lap.lap for lap in recent), trend,
                    tuple(names), tuple(numbers), tuple(float(b) for b in best),
                    tuple(recent[i].lap for i in best_i), tuple(last.times), last.lap,
                    last.lap_ms, tuple(float(s) for s in spread))
