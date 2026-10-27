"""Track maps: where the corners are, by lap distance, per track.

A corner here is a place where you brake or lift enough to lose real speed. Flat-out kinks don't
count (Austria has 10 official turns, but T2, T5 and T8 are flat, so 7 corners).

Each corner has:
- entry: where braking starts (the speed peak before the corner), minus a small margin
- apex: the slowest point
- exit: 100 m after you're back on full throttle (or the next corner's entry, if sooner)

Corners are found automatically from your own clean laps: the median speed over your laps,
smoothed, then every dip of at least MIN_DROP_KPH. The first session at a track learns the map
and saves it as data/tracks/<track>.json. If that file exists it's used as-is, so you can edit
it: rename corners to match the official turn numbers, or delete one that isn't a real corner.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from engineer.ingest.formats import f1_2025 as f
from engineer.state.traces import BIN_SIZE_M, LapTrace

log = logging.getLogger(__name__)

MIN_DROP_KPH = 15.0          # a dip smaller than this is a flat-out kink, not a corner
ENTRY_MARGIN_M = 20.0
EXIT_AFTER_FULL_THROTTLE_M = 100.0
FULL_THROTTLE = 0.95
SMOOTH_BINS = 5              # 25 m moving average before looking for dips
PEAK_TOLERANCE_KPH = 2.0


@dataclass(frozen=True)
class Corner:
    number: int                  # 1, 2, ... in lap order (official turn number if renamed)
    name: str                    # what the engineer says: "turn three"
    entry_m: float
    apex_m: float
    exit_m: float


@dataclass
class TrackMap:
    track_id: int
    track_length_m: float
    corners: list[Corner]
    source: str = "learned"      # "learned" or "file"

    def to_json(self) -> str:
        return json.dumps({"track_id": self.track_id, "track": f.TRACKS.get(self.track_id),
                           "track_length_m": self.track_length_m,
                           "corners": [asdict(c) for c in self.corners]}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "TrackMap":
        d = json.loads(text)
        return cls(d["track_id"], d["track_length_m"],
                   [Corner(**c) for c in d["corners"]], source="file")


_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen", "twenty"]


def turn_name(n: int) -> str:
    return f"turn {_WORDS[n]}" if n < len(_WORDS) else f"turn {n}"


def _smooth(x: np.ndarray, k: int = SMOOTH_BINS) -> np.ndarray:
    pad = np.pad(x, (k // 2, k // 2), mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid")


def detect_corners(laps: list[LapTrace], track_length_m: float) -> list[Corner]:
    """Find the corners in a set of clean laps of the same track."""
    if not laps:
        return []
    speed = _smooth(np.median(np.array([t.speed_kph for t in laps]), axis=0))
    throttle = np.median(np.array([t.throttle for t in laps]), axis=0)
    x = laps[0].distance_m
    last = int(track_length_m // BIN_SIZE_M)        # sections past the line are padding
    speed, throttle, x = speed[:last], throttle[:last], x[:last]

    # Walk the local minima of speed in lap order. A minimum is a new corner when the car got at
    # least MIN_DROP_KPH faster since the previous corner's apex and then lost it again. A
    # minimum without that recovery is part of the same corner (the apex moves if it's slower).
    # Entry is the last point within PEAK_TOLERANCE_KPH of the top speed before the dip, i.e.
    # where braking starts (speeds come in whole km/h, so the top is often a flat run).
    merged: list[tuple[int, int]] = []
    for i in range(1, len(speed) - 1):
        if not (speed[i] <= speed[i - 1] and speed[i] < speed[i + 1]):
            continue
        prev_apex = merged[-1][1] if merged else 0
        window = speed[prev_apex:i]
        if window.size == 0:
            continue
        peak = float(window.max())
        if merged and peak - speed[prev_apex] < MIN_DROP_KPH:
            if speed[i] < speed[prev_apex]:
                merged[-1] = (merged[-1][0], i)
            continue
        if peak - speed[i] >= MIN_DROP_KPH:
            start = prev_apex + int(np.flatnonzero(window >= peak - PEAK_TOLERANCE_KPH)[-1])
            merged.append((start, i))

    entries = [float(max(0.0, x[start] - ENTRY_MARGIN_M)) for start, _ in merged]
    corners = []
    for n, (start, apex) in enumerate(merged, start=1):
        full = apex
        while full < len(speed) - 1 and throttle[full] < FULL_THROTTLE:
            full += 1
        exit_m = float(x[min(len(x) - 1, full + int(EXIT_AFTER_FULL_THROTTLE_M // BIN_SIZE_M))])
        if n < len(merged):
            exit_m = min(exit_m, entries[n])         # never run into the next corner
        corners.append(Corner(number=n, name=turn_name(n), entry_m=entries[n - 1],
                              apex_m=float(x[apex]), exit_m=exit_m))
    return corners


def _slug(track_id: int) -> str:
    name = f.TRACKS.get(track_id, f"track_{track_id}")
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class TrackMaps:
    """Loads and saves track maps in data/tracks/."""

    def __init__(self, tracks_dir: Path | str | None):
        self.dir = Path(tracks_dir) if tracks_dir else None

    def path(self, track_id: int) -> Path | None:
        return None if self.dir is None else self.dir / f"{_slug(track_id)}.json"

    def load(self, track_id: int) -> TrackMap | None:
        p = self.path(track_id)
        if p is None or not p.exists():
            return None
        try:
            return TrackMap.from_json(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("could not read track map %s (%s); learning a new one", p, exc)
            return None

    def save(self, tmap: TrackMap) -> Path | None:
        p = self.path(tmap.track_id)
        if p is None:
            return None
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(tmap.to_json(), encoding="utf-8")
            return p
        except OSError as exc:
            log.warning("could not save track map %s (%s)", p, exc)
            return None
