"""Distance traces: every car's lap sampled into 5 m sections, so laps and cars can be compared
corner by corner.

For each car and lap we keep, per 5 m section along the lap (`m_lapDistance`):
- the lap time when the car reached the start of the section (interpolated between samples, so
  it's accurate to a few milliseconds even though samples come 1/60 s apart)
- speed, throttle, brake, steering and gear (the last sample in the section)

It's a reader of RaceState, updated after each Lap Data / Car Telemetry packet (all 22 cars at
once with numpy), never by the parser. At 300 km/h a 60 Hz feed gives a sample every ~1.4 m, so
5 m sections lose nothing; at lower send rates the gaps are filled by interpolation.

Lap boundaries work like the tyre temperatures: when a car's lap number changes, its finished lap
is kept; at the chequered flag the lap number doesn't change, so the lap in progress is the
finished one. After a flashback, each car's sections from the point it rewound to are cleared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from engineer.state.race_state import N, RaceState

BIN_SIZE_M = 5.0
KEEP_LAPS_OTHER_CARS = 3          # the player's laps are all kept (for best-lap comparisons)
MIN_COVERAGE = 0.9                # a usable lap trace has samples in 90% of its sections


@dataclass(frozen=True)
class LapTrace:
    car_idx: int
    lap: int
    distance_m: np.ndarray        # (B,) start of each section
    time_s: np.ndarray            # (B,) lap time at the start of each section
    speed_kph: np.ndarray
    throttle: np.ndarray
    brake: np.ndarray
    steer: np.ndarray
    gear: np.ndarray
    coverage: float               # share of sections that had a real sample

    @property
    def usable(self) -> bool:
        return self.coverage >= MIN_COVERAGE

    def time_at(self, distance_m: float) -> float:
        return float(np.interp(distance_m, self.distance_m, self.time_s))

    def index(self, distance_m: float) -> int:
        return int(min(len(self.distance_m) - 1, max(0, distance_m // BIN_SIZE_M)))


def _fill(values: np.ndarray, have: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Fill missing sections by linear interpolation from the ones we have."""
    if have.all() or not have.any():
        return values.copy()
    return np.interp(x, x[have], values[have])


class TraceRecorder:
    def __init__(self, state: RaceState):
        self.state = state
        self.track_length_m: float | None = None
        self.n_bins = 0
        self.player_laps: dict[int, LapTrace] = {}
        self.other_laps: dict[tuple[int, int], LapTrace] = {}
        self._rewound = False
        self.cur_lap = np.full(N, np.nan)

    # -- lifecycle ----------------------------------------------------------------------------

    def reset(self) -> None:
        self.track_length_m = None
        self.n_bins = 0
        self.player_laps.clear()
        self.other_laps.clear()
        self.cur_lap = np.full(N, np.nan)
        self._rewound = False

    def rewind(self) -> None:
        """After a flashback. Finished laps from the discarded future are simply overwritten when
        the lap is driven again (same car and lap number)."""
        self._rewound = True

    def _alloc(self, length_m: float) -> None:
        self.track_length_m = length_m
        self.n_bins = int(math.ceil(length_m / BIN_SIZE_M)) + 1
        shape = (N, self.n_bins)
        self.first_d = np.full(shape, np.nan)       # lap distance of the first sample in a section
        self.first_t = np.full(shape, np.nan)       # lap time of that sample
        self.speed = np.full(shape, np.nan)
        self.throttle = np.full(shape, np.nan)
        self.brake = np.full(shape, np.nan)
        self.steer = np.full(shape, np.nan)
        self.gear = np.full(shape, np.nan)

    def _arrays(self):
        return (self.first_d, self.first_t, self.speed, self.throttle, self.brake, self.steer,
                self.gear)

    def _clear(self, rows, from_bin: int = 0) -> None:
        for arr in self._arrays():
            arr[rows, from_bin:] = np.nan

    # -- updates ------------------------------------------------------------------------------

    def after_packet(self, packet_id: int | None) -> None:
        """Hooked into Pipeline.after_packet."""
        from engineer.ingest.formats.f1_2025 import PacketId
        if packet_id == PacketId.LAP_DATA:
            self.on_lap_data()
        elif packet_id == PacketId.CAR_TELEMETRY:
            self.on_telemetry()

    def on_lap_data(self) -> None:
        s, c = self.state, self.state.cars
        if self.track_length_m is None:
            if not s.session.track_length_m:
                return
            self._alloc(float(s.session.track_length_m))
        known = c.active & ~np.isnan(c.lap)
        if self._rewound:
            self._rewound = False
            same = known & (c.lap == self.cur_lap) & ~np.isnan(c.lap_distance_m)
            for i in np.flatnonzero(same):
                b = int(max(0.0, c.lap_distance_m[i]) // BIN_SIZE_M)
                self._clear(i, b)
            back = known & (c.lap < self.cur_lap)
            self._clear(back)
            self.cur_lap[back] = c.lap[back]
        changed = known & (c.lap != self.cur_lap)
        for i in np.flatnonzero(changed & ~np.isnan(self.cur_lap)):
            self._finish(int(i), int(self.cur_lap[i]))
        self._clear(changed)
        self.cur_lap[known] = c.lap[known]

    def on_telemetry(self) -> None:
        if self.track_length_m is None:
            return
        c = self.state.cars
        d = c.lap_distance_m
        ok = (c.active & ~np.isnan(self.cur_lap) & ~np.isnan(d) & (d >= 0)
              & ~np.isnan(c.current_lap_time_ms))
        if not ok.any():
            return
        idx = np.flatnonzero(ok)
        b = (d[idx] // BIN_SIZE_M).astype(int).clip(0, self.n_bins - 1)
        first = np.isnan(self.first_d[idx, b])
        fi, fb = idx[first], b[first]
        self.first_d[fi, fb] = d[fi]
        self.first_t[fi, fb] = c.current_lap_time_ms[fi] / 1000.0
        self.speed[idx, b] = c.speed_kph[idx]
        self.throttle[idx, b] = c.throttle[idx]
        self.brake[idx, b] = c.brake[idx]
        self.steer[idx, b] = c.steer[idx]
        self.gear[idx, b] = c.gear[idx]

    # -- reading ------------------------------------------------------------------------------

    def _snapshot(self, i: int, lap: int) -> LapTrace | None:
        if self.track_length_m is None:
            return None
        x = np.arange(self.n_bins) * BIN_SIZE_M
        have = ~np.isnan(self.first_d[i])
        if have.sum() < 2:
            return None
        order = np.argsort(self.first_d[i][have])
        d_s, t_s = self.first_d[i][have][order], self.first_t[i][have][order]
        time_s = np.interp(x, d_s, t_s)
        hs = ~np.isnan(self.speed[i])
        return LapTrace(
            car_idx=i, lap=lap, distance_m=x, time_s=time_s,
            speed_kph=_fill(self.speed[i], hs, x), throttle=_fill(self.throttle[i], hs, x),
            brake=_fill(self.brake[i], hs, x), steer=_fill(self.steer[i], hs, x),
            gear=_fill(self.gear[i], hs, x), coverage=float(have.mean()))

    def _finish(self, i: int, lap: int) -> None:
        tr = self._snapshot(i, lap)
        if tr is None:
            return
        if i == self.state.player_index:
            self.player_laps[lap] = tr
        else:
            self.other_laps[(i, lap)] = tr
            self.other_laps.pop((i, lap - KEEP_LAPS_OTHER_CARS), None)

    def trace_for(self, car: int, lap: int) -> LapTrace | None:
        """The trace of a car's lap: the finished one, or the lap in progress if it's that lap
        (at the chequered flag the lap number never moves on)."""
        if car == self.state.player_index and lap in self.player_laps:
            return self.player_laps[lap]
        if (car, lap) in self.other_laps:
            return self.other_laps[(car, lap)]
        if self.cur_lap[car] == lap:
            return self._snapshot(car, lap)
        return None
