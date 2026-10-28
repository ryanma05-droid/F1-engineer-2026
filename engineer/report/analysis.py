"""Per-recording analysis for the driver report: your runs through each corner vs the quickest AI.

One recording is replayed once (same Pipeline and TraceRecorder as live play), and the result is
cached as JSON in data/reports/cache/, so the daily report only replays recordings it hasn't seen.

For every corner of the track map we keep, per clean lap of yours, a set of numbers measured from
the 5 m distance trace (see CornerRun), and the same numbers as medians over the clean laps of the
quickest AI cars in that session (the reference). The reference is the quickest AI, not the whole
field, so "you vs them" means "you vs the pace that's on offer in that race".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from engineer.bus import events as ev
from engineer.features.lap_features import LapFeatureBuilder
from engineer.ingest.formats import f1_2025 as f
from engineer.ingest.recorder import read_recording
from engineer.pipeline import Pipeline
from engineer.state.track_maps import Corner, TrackMap, TrackMaps, detect_corners
from engineer.state.traces import LapTrace, TraceRecorder
from engineer.tools.queries import BRAKE_ON, FULL_THROTTLE, corner_stats

log = logging.getLogger(__name__)

# Bump when the numbers below change meaning: every cached recording is then analysed again.
ANALYSIS_VERSION = 1
TOP_AI = 5                 # the reference: the quickest AI cars by median clean lap
MIN_AI_CARS = 3            # fewer AI cars with clean laps (time trial, a short practice): skipped
TIME_TRIAL = 18
CARRY_M = 300.0            # how far down the next straight an exit is followed
PICKUP = 0.2               # throttle above this = back on the throttle
TRAIL_BRAKE = 0.05         # brake above this while steering = trail braking
TRAIL_STEER = 0.1
COAST = 0.05               # neither pedal above this = coasting


@dataclass(frozen=True)
class CornerRun:
    """One car's run through one corner, measured from its lap trace. Distances are lap metres."""
    time_s: float           # corner entry to corner exit (the map's points)
    in_s: float             # entry to the map's apex
    out_s: float            # apex to exit
    carry_s: float          # exit to CARRY_M down the road (or the next corner): exit speed's cost
    entry_kph: float        # top speed before braking
    brake_m: float | None   # braking starts (None: a lift, no braking)
    brake_end_m: float | None   # last braking before the slowest point
    min_kph: float
    min_m: float            # where the car was slowest (its own apex)
    pickup_m: float | None  # back on the throttle after the slowest point
    full_m: float | None    # full throttle after the slowest point
    exit_kph: float
    trail_m: float          # metres braking while steering, before the slowest point
    coast_m: float          # metres with neither pedal
    gear: int


def corner_run(tr: LapTrace, c: Corner, next_entry_m: float, lap_m: float) -> CornerRun:
    st = corner_stats(tr, c)            # the shared corner numbers (same as live coaching)
    x, sp, th, br, steer = tr.distance_m, tr.speed_kph, tr.throttle, tr.brake, tr.steer
    a, b = tr.index(c.entry_m), tr.index(c.exit_m)
    lo = max(0, a - 10)
    m = a + int(np.argmin(sp[a:b + 1]))
    bk = np.flatnonzero(br[a:m + 1] > BRAKE_ON)
    pk = np.flatnonzero(th[m:b + 1] > PICKUP)
    full = np.flatnonzero(th[m:b + 1] >= FULL_THROTTLE)
    top_end = a + bk[0] + 1 if bk.size else m + 1
    after = min(c.exit_m + CARRY_M, next_entry_m, lap_m - 5)
    t = tr.time_at
    return CornerRun(
        time_s=st.time_s, in_s=t(c.apex_m) - t(c.entry_m), out_s=t(c.exit_m) - t(c.apex_m),
        carry_s=t(after) - t(c.exit_m) if after > c.exit_m else 0.0,
        entry_kph=float(sp[lo:top_end].max()),
        brake_m=st.brake_m, brake_end_m=float(x[a + bk[-1]]) if bk.size else None,
        min_kph=st.min_speed_kph, min_m=float(x[m]),
        pickup_m=float(x[m + pk[0]]) if pk.size else None,
        full_m=float(x[m + full[0]]) if full.size else None,
        exit_kph=st.exit_speed_kph,
        trail_m=float(np.sum((br[a:m + 1] > TRAIL_BRAKE) & (np.abs(steer[a:m + 1]) > TRAIL_STEER))
                      * 5.0),
        coast_m=float(np.sum((br[a:b + 1] < COAST) & (th[a:b + 1] < COAST)) * 5.0),
        gear=int(tr.gear[m]))


def median_run(runs: list[CornerRun]) -> dict:
    """Field-by-field median (None fields left out of their own median)."""
    out = {}
    for k in CornerRun.__dataclass_fields__:
        vals = [getattr(r, k) for r in runs if getattr(r, k) is not None]
        out[k] = float(np.median(vals)) if vals else None
    return out


def _clean(r) -> bool:
    return r.clean and not r.safety_car and not r.race_start_lap and not r.invalid


def recorded_at(path: Path) -> datetime:
    """When the recording started: from its name (20261027T010920Z_...), else the file time."""
    m = re.match(r"(\d{8}T\d{6}Z)", path.name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _replay(path: Path):
    pipeline = Pipeline()
    builder = LapFeatureBuilder(pipeline.bus, pipeline.state)
    pipeline.after_packet.append(builder.after_packet)
    traces = TraceRecorder(pipeline.state)
    pipeline.bus.subscribe(ev.SessionStarted, lambda e: traces.reset())
    pipeline.bus.subscribe(ev.Flashback, lambda e: traces.rewind())
    pipeline.after_packet.append(traces.after_packet)
    rows = []
    builder.listeners.append(rows.append)
    lap_traces: dict[tuple[int, int], LapTrace] = {}

    def on_lap(e: ev.LapCompleted) -> None:
        tr = traces.trace_for(e.car_idx, e.lap)
        if tr is not None and tr.usable:
            lap_traces[(e.car_idx, e.lap)] = tr
    pipeline.bus.subscribe(ev.LapCompleted, on_lap)
    for t_ns, raw in read_recording(path):
        pipeline.feed(raw, t_ns)
    return rows, lap_traces, pipeline.state


def analyze_recording(path: Path | str, maps: TrackMaps) -> dict:
    """The report's view of one recording. {"skipped": reason} when there's nothing to compare
    (no laps of yours, a time trial, too few AI cars)."""
    path = Path(path)
    rows, lap_traces, state = _replay(path)
    base = {"file": path.name, "version": ANALYSIS_VERSION,
            "recorded_at": recorded_at(path).isoformat()}
    mine = [r for r in rows if r.is_player]
    if not mine:
        return base | {"skipped": "no laps of yours"}
    track_id, stype = mine[0].track_id, mine[0].session_type
    if stype == TIME_TRIAL:
        return base | {"skipped": "time trial"}
    player = mine[0].car_idx
    clean = {(r.car_idx, r.lap): r for r in rows if _clean(r)}
    my_laps = [r.lap_time_ms for r in mine if _clean(r)]
    pace: dict[int, list[int]] = {}
    for (car, _), r in clean.items():
        if car != player:
            pace.setdefault(car, []).append(r.lap_time_ms)
    if not my_laps:
        return base | {"skipped": "no clean laps of yours"}
    if len(pace) < MIN_AI_CARS:
        return base | {"skipped": "too few AI cars to compare with"}
    fast = sorted(pace, key=lambda c: np.median(pace[c]))[:TOP_AI]
    good = [tr for key, tr in lap_traces.items() if key in clean]

    tmap = maps.load(track_id)
    if tmap is None:
        # Same as live: learn the map from clean laps and save it, so later sessions line up.
        lap_m = float(np.median([tr.distance_m[-1] for tr in good]))
        tmap = TrackMap(track_id, lap_m, detect_corners(good, lap_m))
        if tmap.corners:
            maps.save(tmap)
    corners, lap_m = tmap.corners, tmap.track_length_m
    my_median = float(np.median(my_laps))
    out = base | {
        "session_uid": f"{state.session_uid or 0:016x}",
        "track_id": track_id, "track": f.TRACKS.get(track_id, f"Track {track_id}"),
        "session_type": stype, "session": f.SESSION_TYPES.get(stype, "Session"),
        "lap_m": lap_m, "map": [asdict(c) for c in corners],
        "my_laps": my_laps, "my_median_ms": my_median,
        "top_ai_median_ms": float(np.median([np.median(pace[c]) for c in fast])),
        "field_median_ms": float(np.median([np.median(v) for v in pace.values()])),
        "pace_rank": 1 + sum(np.median(v) < my_median for v in pace.values()),
        "cars": len(pace) + 1, "corners": []}
    for i, c in enumerate(corners):
        nxt = corners[i + 1].entry_m if i + 1 < len(corners) else lap_m + corners[0].entry_m
        me = [corner_run(tr, c, nxt, lap_m) for tr in good if tr.car_idx == player]
        ref = [corner_run(tr, c, nxt, lap_m) for tr in good if tr.car_idx in fast]
        if not me or not ref:
            continue
        out["corners"].append({"number": c.number, "apex_m": c.apex_m,
                               "straight_after_m": nxt - c.exit_m,
                               "mine": [asdict(r) for r in me], "ref": median_run(ref),
                               "ref_runs": len(ref)})
    return out


def _plain(o):
    """numpy numbers -> plain Python for JSON."""
    return o.item() if hasattr(o, "item") else str(o)


class AnalysisCache:
    """data/reports/cache/<recording>.json; an entry is reused while the recording, the analysis
    version and the track map are unchanged."""

    def __init__(self, cache_dir: Path, maps: TrackMaps):
        self.dir, self.maps = Path(cache_dir), maps

    def _path(self, rec: Path) -> Path:
        return self.dir / f"{rec.name}.json"

    def _fresh(self, entry: dict, rec: Path) -> bool:
        if entry.get("version") != ANALYSIS_VERSION or entry.get("size") != rec.stat().st_size:
            return False
        if "track_id" in entry:
            tmap = self.maps.load(entry["track_id"])
            if tmap is not None and [asdict(c) for c in tmap.corners] != entry.get("map"):
                return False                # the track map was edited: measure again
        return True

    def cached(self, rec: Path) -> dict | None:
        p = self._path(rec)
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return entry if self._fresh(entry, rec) else None

    def get(self, rec: Path, progress=print) -> dict:
        entry = self.cached(rec)
        if entry is not None:
            return entry
        progress(f"Analysing {rec.name} ...")
        try:
            entry = analyze_recording(rec, self.maps)
        except Exception as exc:                 # one broken recording mustn't stop the report
            log.warning("could not analyse %s (%s)", rec.name, exc)
            entry = {"file": rec.name, "version": ANALYSIS_VERSION,
                     "recorded_at": recorded_at(rec).isoformat(), "skipped": f"error: {exc}"}
        entry["size"] = rec.stat().st_size
        text = json.dumps(entry, default=_plain)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(rec).write_text(text, encoding="utf-8")
        return json.loads(text)          # the same plain values a cached read gives
