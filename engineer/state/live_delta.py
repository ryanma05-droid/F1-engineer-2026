"""Live lap delta: how far up or down you are on a reference lap, at every point of the lap
(14 Nov: the first step of the lap delta overlay).

Every Lap Data packet (60 a second) for your car:
- distance into the lap and the current lap time, both from the game;
- the reference lap's time at the same distance, from its trace (state/traces.py: lap time at
  the start of each 5 m section, interpolated between samples, so a few ms accurate);
- delta = now - reference: + slower, - quicker. At the line it equals the lap time difference.

The reference (`reference`): "previous" = your latest fair lap (normally the one just driven),
"best" = your fastest fair lap this session. Fair = a usable trace (90 % of sections sampled)
and not invalid (track limits), not an in or out lap, not the standing-start lap, not under the
safety car. No fair lap yet (first lap, out lap) = no reading. It's chosen when the lap starts
and kept for the whole lap, so the number never jumps mid-lap.

Corner splits: with a track map (corners.track_map), at each corner's exit the delta there and
what the corner itself gained or lost (delta at exit - delta at entry) are kept in
`DeltaReading.corner` for `CORNER_HOLD_S`.

No reading in the pit lane, nor for the rest of a lap that went through it, nor once you've
finished (`m_resultStatus` 3+: at the flag the lap number doesn't move on, the distance does). After a flashback
the delta simply follows the rewound distance and time; corners after the rewound point are
measured again. A reader of RaceState like TraceRecorder: never writes state, works the same in
replays (no wall clock: game time only).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from engineer.state.race_state import RaceState
from engineer.state.traces import TraceRecorder

REFERENCES = ("previous", "best")
CORNER_HOLD_S = 3.0          # a corner split stays in the reading this long (game time)
REWIND_M = 30.0              # distance going back more than this within a lap = a flashback
FLAG_M = 200.0               # ... unless from the last FLAG_M of the lap to the first: the flag


@dataclass(frozen=True)
class CornerSplit:
    corner: int               # turn number
    name: str                 # "turn three"
    delta_s: float            # the running delta at the corner's exit
    gain_s: float             # what this corner cost (+) or gained (-) against the reference
    at: float                 # session time it was measured


@dataclass(frozen=True)
class Reference:
    """A reference lap as time against distance, pinned at both ends: (0 m, 0 s) and (the
    line, the lap time). The trace's sections before its first sample and after its last one
    hold a copied value (np.interp clamps), which put the delta at the line 20-40 ms out."""
    lap: int
    lap_time_s: float
    distance_m: np.ndarray
    time_s: np.ndarray

    @classmethod
    def from_trace(cls, tr, lap_time_s: float, track_length_m: float) -> Reference:
        d, t = tr.distance_m, tr.time_s
        rising = np.flatnonzero(np.diff(t) > 0)
        first, last = (rising[0], rising[-1] + 1) if len(rising) else (0, len(t) - 1)
        keep = (d > 0) & (d < track_length_m) & (np.arange(len(d)) >= first) \
            & (np.arange(len(d)) <= last) & (t > 0) & (t < lap_time_s)
        return cls(tr.lap, lap_time_s,
                   np.concatenate(([0.0], d[keep], [track_length_m])),
                   np.concatenate(([0.0], t[keep], [lap_time_s])))

    def time_at(self, distance_m: float) -> float:
        return float(np.interp(distance_m, self.distance_m, self.time_s))


@dataclass(frozen=True)
class DeltaReading:
    lap: int                  # the lap being driven
    ref_lap: int              # the lap it's compared with
    distance_m: float
    delta_s: float            # + slower than the reference, - quicker
    session_time: float
    corner: CornerSplit | None = None    # the latest corner split, for CORNER_HOLD_S


class LiveDelta:
    def __init__(self, state: RaceState, traces: TraceRecorder, features, corners=None,
                 reference: str = "previous"):
        """features: LapFeatureBuilder (which laps were fair); corners: CornerAnalyzer (its
        track_map, for corner splits), optional."""
        if reference not in REFERENCES:
            raise ValueError(f"unknown reference {reference!r}; one of {', '.join(REFERENCES)}")
        self.state, self.traces, self.features, self.corners = state, traces, features, corners
        self.reference = reference
        self.reset()

    def reset(self) -> None:
        self.current: DeltaReading | None = None
        self.splits: list[CornerSplit] = []    # this lap's corner splits, in order
        self.lap_end: dict[int, DeltaReading] = {}   # each lap's furthest reading (at the line)
        self._lap: int | None = None
        self._ref: Reference | None = None
        self._pit_lap: int | None = None
        self._finished_lap: int | None = None
        self._last_d = math.nan
        self._entry: dict[int, float] = {}     # corner -> delta at its entry, this lap

    def set_reference(self, reference: str) -> None:
        if reference not in REFERENCES:
            raise ValueError(f"unknown reference {reference!r}")
        self.reference = reference
        self._ref = None                        # chosen again on the next packet

    # -- choosing the reference ------------------------------------------------------------------

    def _fair_laps(self, me: int, before: int) -> list[tuple[int, int]]:
        """(lap, lap time ms) of your fair laps before `before` that have a usable trace."""
        feats = {f.lap: f for f in self.features.for_car(me)}
        out = []
        for lap, tr in self.traces.player_laps.items():
            f = feats.get(lap)
            if lap >= before or f is None or not tr.usable:
                continue
            if f.invalid or f.pit_in_lap or f.pit_out_lap or f.race_start_lap or f.safety_car:
                continue
            out.append((lap, f.lap_time_ms))
        return out

    def _choose(self, me: int, lap: int) -> Reference | None:
        fair = self._fair_laps(me, lap)
        length = self.traces.track_length_m or self.state.session.track_length_m
        if not fair or not length:
            return None
        if self.reference == "best":
            pick, ms = min(fair, key=lambda x: (x[1], -x[0]))
        else:
            pick, ms = max(fair)
        return Reference.from_trace(self.traces.player_laps[pick], ms / 1000.0, float(length))

    # -- each packet -----------------------------------------------------------------------------

    def after_packet(self, packet_id: int | None) -> None:
        """Hooked into Pipeline.after_packet, after TraceRecorder and LapFeatureBuilder."""
        from engineer.ingest.formats.f1_2025 import PacketId
        if packet_id == PacketId.LAP_DATA:
            self.update()

    def update(self) -> None:
        s, c, me = self.state, self.state.cars, self.state.player_index
        if me is None:
            self.current = None
            return
        lap, d, t_ms = c.lap[me], c.lap_distance_m[me], c.current_lap_time_ms[me]
        if np.isnan(lap) or np.isnan(d) or np.isnan(t_ms) or d < 0:
            self.current = None
            return
        if c.result_status[me] >= 3:                           # finished, out, or retired
            self.current = None
            return
        lap = int(lap)
        if c.pit_status[me] > 0 or c.pit_lane_timer_active[me] == 1:
            self._pit_lap = lap
        if lap != self._lap:
            self._lap, self._ref = lap, None
            self._entry, self.splits = {}, []
            self._last_d = math.nan
        elif self._finished_lap == lap:
            self.current = None
            return
        elif d < self._last_d - REWIND_M and d < FLAG_M and self._near_line(self._last_d):
            # Back to the start of the same lap: the chequered flag (the lap number doesn't move
            # on, and the result status stays "active" for a few packets more). Splits kept.
            self._finished_lap, self.current = lap, None
            return
        elif d < self._last_d - REWIND_M:                       # flashback within the lap
            self._entry = {k: v for k, v in self._entry.items() if self._corner_entry(k) < d}
            self.splits = [sp for sp in self.splits if self._corner_exit(sp.corner) < d]
        self._last_d = d
        if lap == self._pit_lap:
            self.current = None
            return
        if self._ref is None:
            self._ref = self._choose(me, lap)
            if self._ref is None:
                self.current = None
                return
        now = s.session_time or 0.0
        delta = float(t_ms) / 1000.0 - self._ref.time_at(d)
        self._corner_splits(d, delta, now)
        last = self.splits[-1] if self.splits else None
        self.current = DeltaReading(
            lap=lap, ref_lap=self._ref.lap, distance_m=float(d), delta_s=delta,
            session_time=now,
            corner=last if last is not None and now - last.at <= CORNER_HOLD_S else None)
        end = self.lap_end.get(lap)
        if end is None or d >= end.distance_m:
            self.lap_end[lap] = self.current

    def _near_line(self, d: float) -> bool:
        length = self.traces.track_length_m or self.state.session.track_length_m
        return bool(length) and d > length - FLAG_M

    # -- corners ---------------------------------------------------------------------------------

    def _map(self):
        return getattr(self.corners, "track_map", None) if self.corners is not None else None

    def _corner_entry(self, number: int) -> float:
        m = self._map()
        return next((cn.entry_m for cn in m.corners if cn.number == number), math.inf) \
            if m else math.inf

    def _corner_exit(self, number: int) -> float:
        m = self._map()
        return next((cn.exit_m for cn in m.corners if cn.number == number), math.inf) \
            if m else math.inf

    def _corner_splits(self, d: float, delta: float, now: float) -> None:
        m = self._map()
        if m is None:
            return
        done = {sp.corner for sp in self.splits}
        for cn in m.corners:
            if cn.number in done:
                continue
            if cn.number not in self._entry:
                if cn.entry_m <= d < cn.exit_m:
                    self._entry[cn.number] = delta
                continue
            if d >= cn.exit_m:
                self.splits.append(CornerSplit(cn.number, cn.name, delta,
                                               delta - self._entry[cn.number], now))
