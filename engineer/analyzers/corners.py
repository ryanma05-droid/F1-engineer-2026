"""Corner coaching: after each of your laps, the corner where you lost the most time, and why.

Who you're compared with (`corners.nearby_gap_s`, default 2 s):
1. the car directly ahead, if it's within 2 s and its lap was clean (same lap number: it crossed
   the line just before you, so its lap is complete)
2. otherwise the car directly behind, if within 2 s: its lap finishes a moment after yours, so the
   call waits for it (up to 5 s)
3. otherwise your own best clean lap this session

Only clean laps are coached and used as references (no race lap 1, in/out laps, safety car,
invalid laps). One call per lap at most, and only if the loss is at least `min_loss_s`. Gains are
called only on chatty verbosity.

Habits first: a corner that has lost time on `focus_after_laps` (2) coached laps in a row is
called ahead of a bigger one-off loss elsewhere, at NORMAL priority instead of LOW (so it isn't
crowded out), and said as "Turn four, third lap running, ...". One bad lap is a mistake; the
same corner every lap is the thing to work on.

The corner map comes from data/tracks/<track>.json; with no file, it's learned from your first two
clean laps and saved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from engineer.bus import events as ev
from engineer.config import Corners
from engineer.features.laps import LapHistory, LapRecord
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.radio.phrases import corner_phrase
from engineer.state.race_state import RaceState
from engineer.state.track_maps import TrackMap, TrackMaps, detect_corners
from engineer.state.traces import LapTrace, TraceRecorder
from engineer.tools.queries import REASON_THRESHOLDS, CornerDelta, corner_deltas

log = logging.getLogger(__name__)

WAIT_FOR_CAR_BEHIND_S = 5.0
LEARN_FROM_LAPS = 2


@dataclass
class _Pending:
    lap: int
    trace: LapTrace
    behind_car: int
    deadline: float


class CornerAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, history: LapHistory,
                 traces: TraceRecorder, maps: TrackMaps, director: RadioDirector,
                 settings: Corners | None = None, verbosity: str = "normal"):
        self.state = state
        self.history = history
        self.traces = traces
        self.maps = maps
        self.director = director
        self.cfg = settings or Corners()
        self.verbosity = verbosity
        self.track_map: TrackMap | None = None
        self.pending: _Pending | None = None
        self.last: list[CornerDelta] = []          # last comparison (for push-to-talk later)
        self.last_against: str | None = None
        self.called_on: dict[int, int] = {}        # corner number -> lap it was last called
        self.compared_laps: list[int] = []         # your laps that were coached, in order
        self.losing_laps: dict[int, set[int]] = {}  # corner number -> laps it lost min_loss_s
        self.lap_reasons: dict[tuple[int, int], set[str]] = {}   # (corner, lap) -> causes
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SessionStarted, self._on_session_started)
        bus.subscribe(ev.Flashback, lambda e: setattr(self, "pending", None))

    def _on_session_started(self, e: ev.SessionStarted) -> None:
        self.pending = None
        self.last = []
        self.called_on = {}
        self.compared_laps = []
        self.losing_laps = {}
        self.lap_reasons = {}
        self.track_map = self.maps.load(e.track_id) if e.track_id is not None else None
        if self.track_map:
            log.info("corner map: %d corners from %s", len(self.track_map.corners),
                     self.maps.path(e.track_id))

    # -- helpers ---------------------------------------------------------------------------------

    def _clean_trace(self, car: int, lap: int) -> LapTrace | None:
        rec = next((r for r in self.history.for_car(car) if r.lap == lap), None)
        if rec is None or not rec.clean:
            return None
        tr = self.traces.trace_for(car, lap)
        return tr if tr is not None and tr.usable else None

    def _best_trace(self, exclude_lap: int) -> LapTrace | None:
        me = self.state.player_index
        laps: list[LapRecord] = sorted((r for r in self.history.for_car(me)
                                        if r.clean and r.lap != exclude_lap),
                                       key=lambda r: r.lap_time_ms)
        for r in laps:
            tr = self._clean_trace(me, r.lap)
            if tr is not None:
                return tr
        return None

    def _car_at(self, position: float) -> int | None:
        hits = np.flatnonzero(self.state.cars.active & (self.state.cars.position == position))
        return int(hits[0]) if hits.size else None

    def _learn_map(self) -> None:
        s = self.state
        if s.session.track_id is None or not s.session.track_length_m:
            return
        me = s.player_index
        laps = [tr for r in self.history.for_car(me) if r.clean
                and (tr := self._clean_trace(me, r.lap)) is not None]
        if len(laps) < LEARN_FROM_LAPS:
            return
        corners = detect_corners(laps, float(s.session.track_length_m))
        if not corners:
            return
        self.track_map = TrackMap(s.session.track_id, float(s.session.track_length_m), corners)
        path = self.maps.save(self.track_map)
        log.info("learned %d corners at %s%s", len(corners), s.session.track_name,
                 f"; saved {path}" if path else "")

    # -- laps ------------------------------------------------------------------------------------

    def on_lap(self, e: ev.LapCompleted) -> None:
        if not self.cfg.enabled:
            return
        me = self.state.player_index
        if self.pending and e.car_idx == self.pending.behind_car and e.lap == self.pending.lap:
            p, self.pending = self.pending, None
            ref = self._clean_trace(e.car_idx, e.lap)
            self._compare(p.trace, ref, "behind") if ref else self._compare_best(p.trace)
            return
        if e.car_idx != me:
            return
        self.pending = None
        trace = self._clean_trace(me, e.lap)
        if trace is None:
            return
        if self.track_map is None:
            self._learn_map()
            if self.track_map is None:
                return
        gap_ms = self.cfg.nearby_gap_s * 1000
        c = self.state.cars
        pos = c.position[me]
        ahead = self._car_at(pos - 1)
        if ahead is not None and c.delta_to_car_in_front_ms[me] <= gap_ms:
            ref = self._clean_trace(ahead, e.lap)
            if ref is not None:
                self._compare(trace, ref, "ahead")
                return
        behind = self._car_at(pos + 1)
        if behind is not None and c.delta_to_car_in_front_ms[behind] <= gap_ms:
            self.pending = _Pending(e.lap, trace, behind,
                                    (self.state.session_time or 0) + WAIT_FOR_CAR_BEHIND_S)
            return
        self._compare_best(trace)

    def after_packet(self, _packet_id) -> None:
        """Give up waiting for the car behind after a few seconds."""
        if self.pending and (self.state.session_time or 0) > self.pending.deadline:
            p, self.pending = self.pending, None
            self._compare_best(p.trace)

    def _compare_best(self, trace: LapTrace) -> None:
        ref = self._best_trace(exclude_lap=trace.lap)
        if ref is not None:
            self._compare(trace, ref, "best")

    def _compare(self, trace: LapTrace, ref: LapTrace, against: str) -> None:
        if self.track_map is None:
            return
        deltas = corner_deltas(trace, ref, self.track_map)
        self.last, self.last_against = deltas, against
        if not deltas:
            return
        losses = self._record(trace.lap, deltas)
        best = min(deltas, key=lambda d: d.delta_s)
        priority = Priority.LOW
        if losses:
            # A corner that keeps costing time (focus_after_laps laps running) beats a bigger
            # one-off loss, and is said with more priority: fixing a habit is how you improve.
            # The longest streak first, then the biggest loss.
            pick = max(losses, key=lambda d: (self.streak(d.corner.number), d.delta_s))
            n, streak = pick.corner.number, self.streak(pick.corner.number)
            if streak < self.cfg.focus_after_laps:
                pick, streak = max(losses, key=lambda d: d.delta_s), 0
                n = pick.corner.number
            else:
                priority = Priority.NORMAL
            again = not streak and n in self.called_on and trace.lap - self.called_on[n] <= 3
            self.called_on[n] = trace.lap
            reason = self._habit_reason(pick, trace.lap, streak) if streak else pick.reason()
            text = corner_phrase(pick.delta_s, pick.corner.name, against, reason,
                                 self.verbosity, again=again, streak=streak)
        elif self.verbosity == "chatty" and best.delta_s <= -self.cfg.min_loss_s:
            text = corner_phrase(best.delta_s, best.corner.name, against)
        else:
            return
        self.director.submit(RadioCall(topic="corner", text=text, priority=priority,
                                       max_wait_s=self.cfg.max_wait_s))

    def _record(self, lap: int, deltas: list[CornerDelta]) -> list[CornerDelta]:
        """Remember which corners lost time on this lap; returns those losses."""
        # A lap driven again after a flashback replaces the old one.
        self.compared_laps = [x for x in self.compared_laps if x < lap] + [lap]
        losses = [d for d in deltas if d.delta_s >= self.cfg.min_loss_s]
        for laps in self.losing_laps.values():
            laps.discard(lap)
        for d in losses:
            self.losing_laps.setdefault(d.corner.number, set()).add(lap)
            self.lap_reasons[(d.corner.number, lap)] = set(d.reasons())
        return losses

    def _habit_reason(self, d: CornerDelta, lap: int, streak: int) -> tuple[str, float] | None:
        """During a streak, the cause that keeps coming back (most laps of the streak) rather
        than whichever was biggest this lap: one clear thing to work on, not a new one each lap.
        Real 18-lap Austria race: turn four lost time 11 laps running, and the biggest cause
        changed almost every lap (exit, braking, throttle, minimum speed)."""
        now = d.reasons()
        if not now:
            return None
        laps = self.compared_laps[-streak:]
        counts = {k: sum(k in self.lap_reasons.get((d.corner.number, x), ()) for x in laps)
                  for k in now}
        kind = max(now, key=lambda k: (counts[k], now[k] / REASON_THRESHOLDS[k]))
        return kind, now[kind]

    def streak(self, corner_number: int) -> int:
        """How many coached laps in a row (up to the latest) this corner lost min_loss_s.
        Laps that weren't coached (in-laps, safety car, ...) neither count nor break it."""
        laps = self.losing_laps.get(corner_number, set())
        n = 0
        for lap in reversed(self.compared_laps):
            if lap not in laps:
                break
            n += 1
        return n
