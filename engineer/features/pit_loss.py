"""Pit loss measured from the game's own pit-lane timer.

The old estimate (in-lap + out-lap minus the car's normal laps, strategy/model.py
`measure_pit_losses`) also counts everything else on those two laps: a cold-tyre out-lap,
traffic after rejoining, a mistake. Melbourne (2 Nov) came out at 26 s that way.

F1 25 sends, for every car, `m_pitLaneTimerActive` / `m_pitLaneTimeInLaneInMS` /
`m_pitStopTimerInMS`. Checked on the 2 Nov Melbourne race: every stop shows, entry at 5040 m and
exit at 44 m for all 19, ~18.3 s in the lane with ~2.5-3.3 s stationary.

The loss itself is measured over a window: from `MARGIN_M` before the pit entry line to
`MARGIN_M` after the exit line (so braking into the pit lane and getting back up to speed
count), against the same car over the same stretch of track on its own green laps (k laps either
side, no stop, no safety car, not lap 1):

    loss = time through the window on the stop - median time through it on normal laps

Under a safety car the reference is the other cars through the same stretch at about the same
time (they're all slow too).

To be able to measure any window afterwards, every car's session time is kept at every
`BIN_M` of total distance (a few thousand numbers per car per race). Same code live and in
replays (`Pipeline.after_packet`), and in the feature tables (pit_losses.parquet).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from engineer.bus import events as ev
from engineer.ingest.formats import f1_2025 as f
from engineer.state.race_state import N, RaceState

BIN_M = 20.0          # session time kept at every 20 m of each car's total distance
MARGIN_M = 300.0      # window: this far before the pit entry line and after the exit line
REF_LAPS = 4          # the car's own laps either side used for its normal time
MIN_REFS = 2
SC_MATCH_S = 30.0     # SC stops: other cars through the window within this of the stop
LONG_STOP_S = 2.0     # stationary this much over the usual: a repair or a penalty, left out


@dataclass
class PitStop:
    car_idx: int
    lap: int                     # the lap the car pitted at the end of (pit entry)
    entry_m: float               # total distance at the pit entry line
    exit_m: float                # ... at the exit line
    entry_t: float               # session time at the entry line
    lane_s: float                # the game's time in the pit lane
    stop_s: float                # ... stationary in the box
    sc: bool                     # safety car / VSC out at any time from entry to exit
    loss_s: float | None = None  # measured once the window is behind the car


class PitLossMeter:
    def __init__(self, bus: ev.EventBus, state: RaceState):
        self.state = state
        self.stops: list[PitStop] = []
        # Earlier sessions' measured stops, kept for the feature table: one recording can hold
        # several races (29 Oct: Bahrain, Abu Dhabi and Imola under one session UID).
        self.done_rows: list[dict] = []
        self._track: int | None = None
        self._uid: int | None = None
        self._reset()
        bus.subscribe(ev.SessionStarted, self._new_session)
        bus.subscribe(ev.Flashback, lambda e: self._rewind())

    def _new_session(self, e) -> None:
        if self.stops and self._track is not None:
            self.done_rows += self._rows(f"{self._uid or 0:016x}", self._track)
        self._reset()
        self._track, self._uid = e.track_id, e.session_uid

    def _reset(self) -> None:
        self.stops = []
        self.times: list[list[float]] = [[] for _ in range(N)]   # session time per BIN_M
        self._last = [(math.nan, math.nan)] * N                  # (total distance, time)
        self._open: dict[int, dict] = {}                         # car -> stop in progress
        self._last_lane = np.zeros(N)
        self._last_stop = np.zeros(N)
        self.sc_spans: list[list[float]] = []                    # [start, end] session time
        self._sc_on = False

    def _rewind(self) -> None:
        """A flashback: forget what happened after the time we're back at."""
        t = self.state.session_time or 0.0
        for i in range(N):
            tl = self.times[i]
            while tl and tl[-1] > t:
                tl.pop()
            self._last[i] = (math.nan, math.nan)
        self.stops = [s for s in self.stops if s.entry_t <= t]
        self._open = {i: o for i, o in self._open.items() if o["entry_t"] <= t}
        self.sc_spans = [sp for sp in self.sc_spans if sp[0] <= t]

    # -- recording ---------------------------------------------------------------------------

    def after_packet(self, packet_id: int | None) -> None:
        s = self.state
        if packet_id == f.PacketId.SESSION:
            on = s.session.safety_car_status in (1, 2)
            t = s.session_time or 0.0
            if on and not self._sc_on:
                self.sc_spans.append([t, math.inf])
            elif not on and self._sc_on and self.sc_spans:
                self.sc_spans[-1][1] = t
            self._sc_on = on
            return
        if packet_id != f.PacketId.LAP_DATA or not s.session.is_race:
            return
        c, t = s.cars, s.session_time or 0.0
        if t <= 0:
            return
        for i in np.flatnonzero(c.active):
            d = float(c.total_distance_m[i])
            if math.isnan(d):
                continue
            self._advance(i, d, t)
            self._pit_timer(i, d, t)

    def _advance(self, i: int, d: float, t: float) -> None:
        d0, t0 = self._last[i]
        self._last[i] = (d, t)
        tl = self.times[i]
        if math.isnan(d0) or d <= d0 or d < 0:
            if not tl and d >= 0:
                tl.extend([math.nan] * int(d // BIN_M))   # joined mid-session: unknown so far
            return
        k0, k1 = len(tl), int(d // BIN_M)
        for k in range(k0, k1 + 1):
            edge = k * BIN_M
            if edge < d0:                                  # a gap (paused / not sent)
                tl.append(math.nan)
                continue
            tl.append(t0 + (edge - d0) / (d - d0) * (t - t0))

    def _pit_timer(self, i: int, d: float, t: float) -> None:
        c = self.state.cars
        active = c.pit_lane_timer_active[i] == 1
        if active and i not in self._open:
            self._open[i] = {"entry_m": d, "entry_t": t, "lap": int(c.lap[i]),
                             "sc": self._sc_on}
        elif active:
            self._open[i]["sc"] |= self._sc_on
            self._last_lane[i] = c.pit_lane_time_ms[i]
            self._last_stop[i] = c.pit_stop_timer_ms[i]
        elif i in self._open:
            o = self._open.pop(i)
            self.stops.append(PitStop(int(i), o["lap"], o["entry_m"], d, o["entry_t"],
                                      float(self._last_lane[i]) / 1000.0,
                                      float(self._last_stop[i]) / 1000.0, o["sc"]))

    # -- measuring ---------------------------------------------------------------------------

    def _at(self, i: int, d: float) -> float:
        k = d / BIN_M
        lo = int(math.floor(k))
        tl = self.times[i]
        if lo < 0 or lo + 1 >= len(tl):
            return math.nan
        a, b = tl[lo], tl[lo + 1]
        return a + (b - a) * (k - lo)

    def _through(self, i: int, start: float, end: float) -> float:
        return self._at(i, end) - self._at(i, start)

    def _sc_during(self, t0: float, t1: float) -> bool:
        return any(sp[0] < t1 and sp[1] > t0 for sp in self.sc_spans)

    def _pitting(self, i: int, start: float, end: float) -> bool:
        return any(s.car_idx == i and s.entry_m < end and s.exit_m > start for s in self.stops) \
            or (i in self._open and self._open[i]["entry_m"] < end)

    def measure(self) -> list[PitStop]:
        """Every stop with its loss filled in where the data allows."""
        length = self.state.session.track_length_m or 0
        if not length:
            return self.stops
        for st in self.stops:
            if st.loss_s is not None:
                continue
            start, end = st.entry_m - MARGIN_M, st.exit_m + MARGIN_M
            took = self._through(st.car_idx, start, end)
            if math.isnan(took):
                continue
            ref = self._sc_reference(st, start, end, length) if st.sc \
                else self._own_reference(st.car_idx, start, end, length)
            if ref is not None:
                st.loss_s = round(took - ref, 3)
        return self.stops

    def _own_reference(self, i: int, start: float, end: float, length: float) -> float | None:
        refs = []
        for k in range(-REF_LAPS, REF_LAPS + 1):
            if k == 0:
                continue
            a, b = start + k * length, end + k * length
            if a < length or self._pitting(i, a, b):      # lap 1 (standing start) left out
                continue
            t0, t1 = self._at(i, a), self._at(i, b)
            if math.isnan(t0) or math.isnan(t1) or self._sc_during(t0, t1):
                continue
            refs.append(t1 - t0)
        return float(np.median(refs)) if len(refs) >= MIN_REFS else None

    def _sc_reference(self, st: PitStop, start: float, end: float,
                      length: float) -> float | None:
        t_stop = self._at(st.car_idx, start)
        refs = []
        for j in range(N):
            if j == st.car_idx or len(self.times[j]) < 2:
                continue
            best = None
            for k in range(-3, 4):                         # j can be laps ahead or behind
                a = start + k * length
                t0 = self._at(j, a)
                if math.isnan(t0) or abs(t0 - t_stop) > SC_MATCH_S:
                    continue
                b = end + k * length
                if self._pitting(j, a, b):
                    continue
                dt = self._through(j, a, b)
                if not math.isnan(dt) and (best is None or abs(t0 - t_stop) < best[0]):
                    best = (abs(t0 - t_stop), dt)
            if best is not None:
                refs.append(best[1])
        return float(np.median(refs)) if len(refs) >= 3 else None

    def samples(self) -> tuple[list[float], list[float]]:
        """(green losses, safety car losses) in seconds, this session."""
        return samples_from(self.measure())

    def rows(self, session_uid: str, track_id: int | None) -> list[dict]:
        """For data/features/pit_losses.parquet: earlier sessions' stops, then this one's."""
        return self.done_rows + self._rows(session_uid, track_id)

    def _rows(self, session_uid: str, track_id: int | None) -> list[dict]:
        return [{"session_uid": session_uid, "track_id": track_id, **asdict(s)}
                for s in self.measure() if s.loss_s is not None]


def samples_from(stops) -> tuple[list[float], list[float]]:
    """(green, safety car) losses from measured stops (PitStop or pit_losses.parquet rows), less
    the ones held in the box much longer than usual (a wing change, a penalty served)."""
    def get(s, k):
        return s[k] if isinstance(s, dict) else getattr(s, k)
    done = [s for s in stops if get(s, "loss_s") is not None]
    if not done:
        return [], []
    usual = float(np.median([get(s, "stop_s") for s in done]))
    keep = [s for s in done if get(s, "stop_s") <= usual + LONG_STOP_S]
    return ([float(get(s, "loss_s")) for s in keep if not get(s, "sc")],
            [float(get(s, "loss_s")) for s in keep if get(s, "sc")])

