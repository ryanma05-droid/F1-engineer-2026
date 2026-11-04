"""Corner coaching: after each of your laps, the corner where you lost the most time, and why.

Who you're compared with (`corners.nearby_gap_s`, default 2 s):
1. the car directly ahead, if it's within 2 s and its lap was clean (same lap number: it crossed
   the line just before you, so its lap is complete)
2. otherwise the car directly behind, if within 2 s: its lap finishes a moment after yours, so the
   call waits for it (up to 5 s)
3. otherwise your own pace: the fastest of your clean laps on the same compound with similar
   tyre wear (within `ref_wear_window_pct`), so worn tyres aren't blamed on a corner. ("Off your
   pace" in the call.)

Only clean laps are coached and used as references (no race lap 1, in/out laps, safety car,
invalid laps). One call per lap at most, and only if the loss is at least `min_loss_s`. Gains are
called only on chatty verbosity.

Habits first: a corner that has lost time on `focus_after_laps` (2) coached laps in a row is
called ahead of a bigger one-off loss elsewhere, at NORMAL priority instead of LOW (so it isn't
crowded out), and said as "Turn four, third lap running, ...". One bad lap is a mistake; the
same corner every lap is the thing to work on.

Closing the loop: when a habit corner you were told about stops losing time (your own time there
is at least `min_loss_s` quicker than on the lap it was called), you hear it once: "Turn four
better, two tenths found." (or "Turn four better." in front of that lap's other call).
After `back_off_after` (3) calls on the same corner in a streak, it's called only every other
lap unless it gets worse, so a long habit isn't repeated every single lap.

In a fight (a car within `battle_gap_s`, 1 s, ahead or behind) only the comparison with the car
ahead is said (where you lose to it is how you plan a pass); coaching against your own pace stays
quiet: you're busy, and racing lines and dirty air make it unfair. The laps still count for
streaks. (On the 5-lap Austria race a car was within 0.5 s behind on every lap: the calls vs the
car ahead still come.)

Coaching mode (tempo "coaching"): every clean lap is compared with the fastest clean lap any
other car has done this session (`bench`, kept as it's beaten), and one call says the lap, the
gap and the one or two corners it comes from, with what to do about them ("Most of it's turn
two, three tenths. Brake slightly later, then carry a bit more speed through the corner.").
Quicker than everyone? "Fastest lap on track", then where that car is still quicker (Austria
fixture: the player set the fastest lap from lap 2). Habits work as above: a corner that keeps
losing comes first ("Still turn two"), and "Better into turn two, keep doing that" when it's
fixed. In a fight it isn't silenced (you chose coaching; Melbourne, 2 Nov, had a car within a
second on nine of fifteen laps), just cut to one corner.

From your driver report (report/focus.py, data/reports/focus.json): the corners where you
usually lose the most at this track come straight after a live habit, from the first lap (one
lap there is the same old corner, not a one-off), at NORMAL priority, said the first time as
"Turn four, one of your report corners, ...", with the cause your report habit points at when
it shows this lap. Once per session the engineer says what you're working on (after your first
lap: "From your report, the time's in turn four and turn six. Work on the exits ..."), and once
per corner when a report corner is level with the fastest lap on track ("Turn four, level with
the fastest lap now. That was a report corner."). In coaching mode the report corners are
ordered right after the habits too.

Wording (radio.detail): "plain" says what to do ("brake slightly later"), "specific" says the
measurement ("braking fifteen metres earlier").

The corner map comes from data/tracks/<track>.json; with no file, it's learned from your first two
clean laps and saved.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from engineer.analyzers.coaching import CoachCorner, CoachLap
from engineer.bus import events as ev
from engineer.config import Corners
from engineer.features.lap_features import LapFeatureBuilder
from engineer.features.laps import LapHistory, LapRecord
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.radio.phrases import (
    coaching_phrase,
    corner_better_phrase,
    corner_phrase,
    focus_brief_phrase,
    focus_level_phrase,
)
from engineer.report.focus import CoachingFocus, FocusCorner
from engineer.state.race_state import RaceState
from engineer.state.traces import LapTrace, TraceRecorder
from engineer.state.track_maps import TrackMap, TrackMaps, detect_corners
from engineer.tools.queries import REASON_THRESHOLDS, CornerDelta, corner_deltas

log = logging.getLogger(__name__)

WAIT_FOR_CAR_BEHIND_S = 5.0
LEARN_FROM_LAPS = 2


@dataclass
class _Bench:
    """The fastest clean lap on track this session (coaching mode compares with it)."""
    car: int
    lap: int
    lap_time_ms: int
    trace: LapTrace


@dataclass
class _Pending:
    lap: int
    trace: LapTrace
    behind_car: int
    deadline: float


class CornerAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, history: LapHistory,
                 traces: TraceRecorder, maps: TrackMaps, director: RadioDirector,
                 settings: Corners | None = None, verbosity: str = "normal",
                 features: LapFeatureBuilder | None = None, detail: str = "plain",
                 focus: CoachingFocus | None = None):
        self.state = state
        self.history = history
        self.traces = traces
        self.maps = maps
        self.director = director
        self.cfg = settings or Corners()
        self.verbosity = verbosity
        self.detail = detail                       # "plain" or "specific" causes
        self.coaching = False                      # tempo "coaching" (Engine.set_tempo)
        self.bench: _Bench | None = None
        self.coach_laps: list[CoachLap] = []       # every clean lap vs the bench (coaching.py)
        self._bench_todo: list[tuple[int, int, int]] = []   # (car, lap, ms): trace not ready
        self.features = features                   # tyre wear per lap (for the reference lap)
        self.track_map: TrackMap | None = None
        self.pending: _Pending | None = None
        self.last: list[CornerDelta] = []          # last comparison (for push-to-talk later)
        self.last_against: str | None = None
        self.called_on: dict[int, int] = {}        # corner number -> lap it was last called
        self.compared_laps: list[int] = []         # your laps that were coached, in order
        self.losing_laps: dict[int, set[int]] = {}  # corner number -> laps it lost min_loss_s
        self.lap_reasons: dict[tuple[int, int], set[str]] = {}   # (corner, lap) -> causes
        # corner -> [(lap, loss called, your corner time that lap, streak when called)]
        self.calls: dict[int, list[tuple[int, float, float, int]]] = {}
        self.praised: dict[int, int] = {}          # corner -> lap "better" was said
        self.focus = focus                         # from the driver report (report/focus.py)
        self.focus_corners: dict[int, FocusCorner] = {}   # this track's report corners
        self.briefed = False                       # "from your report, ..." said this session
        self.report_said: set[int] = set()         # report corners named as such this session
        self.level_said: set[int] = set()          # "level with the fastest lap now" said
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SessionStarted, self._on_session_started)
        bus.subscribe(ev.Flashback, lambda e: setattr(self, "pending", None))

    def _on_session_started(self, e: ev.SessionStarted) -> None:
        self.pending = None
        self.bench = None
        self._bench_todo = []
        self.coach_laps = []
        self.last = []
        self.called_on = {}
        self.compared_laps = []
        self.losing_laps = {}
        self.lap_reasons = {}
        self.calls = {}
        self.praised = {}
        self.track_map = self.maps.load(e.track_id) if e.track_id is not None else None
        self.focus_corners = {c.number: c for c in self.focus.for_track(e.track_id)} \
            if self.focus is not None else {}
        self.briefed = False
        self.report_said = set()
        self.level_said = set()
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

    def reference_trace(self, lap: int) -> LapTrace | None:
        """Your pace for this lap: the fastest clean lap on the same compound with tyre wear
        within ref_wear_window_pct of this lap's. Without tyre data: your best clean lap."""
        if self.features is None:
            return self._best_trace(exclude_lap=lap)
        me = self.state.player_index
        recs = {r.lap: r for r in self.features.for_car(me)}
        this = recs.get(lap)
        if this is None:
            return self._best_trace(exclude_lap=lap)
        worst = _worst(this)
        window = self.cfg.ref_wear_window_pct
        laps = sorted((r for r in self.history.for_car(me) if r.clean and r.lap != lap),
                      key=lambda r: r.lap_time_ms)
        for r in laps:
            f = recs.get(r.lap)
            if f is None or f.visual_compound != this.visual_compound:
                continue
            if not (math.isnan(worst) or math.isnan(_worst(f))) and abs(_worst(f) - worst) > window:
                continue
            tr = self._clean_trace(me, r.lap)
            if tr is not None:
                return tr
        return None

    def _in_a_fight(self) -> bool:
        c, me = self.state.cars, self.state.player_index
        gap = self.cfg.battle_gap_s * 1000
        if c.position[me] > 1 and c.delta_to_car_in_front_ms[me] <= gap:
            return True
        behind = self._car_at(c.position[me] + 1)
        return behind is not None and c.delta_to_car_in_front_ms[behind] <= gap

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

    def _update_bench(self, e: ev.LapCompleted) -> None:
        """Keep the fastest clean lap on track. A lap's trace can finish a moment after its
        LapCompleted, so faster laps wait in _bench_todo until their trace is there."""
        if e.car_idx == self.state.player_index:
            return                                 # the reference is the fastest OTHER car
        rec = next((r for r in self.history.for_car(e.car_idx) if r.lap == e.lap), None)
        if rec is not None and rec.clean and rec.lap_time_ms \
                and (self.bench is None or rec.lap_time_ms < self.bench.lap_time_ms):
            self._bench_todo.append((e.car_idx, e.lap, rec.lap_time_ms))
        keep = []
        for car, lap, ms in self._bench_todo:
            if self.bench is not None and ms >= self.bench.lap_time_ms:
                continue
            tr = self._clean_trace(car, lap)
            if tr is not None:
                self.bench = _Bench(car, lap, ms, tr)
            elif lap >= self._last_lap(car) - 1:  # give it until the car's next lap
                keep.append((car, lap, ms))
        self._bench_todo = keep

    def _last_lap(self, car: int) -> int:
        laps = self.history.for_car(car)
        return laps[-1].lap if laps else 0

    def on_lap(self, e: ev.LapCompleted) -> None:
        if not self.cfg.enabled:
            return
        self._update_bench(e)
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
        deltas = self._log_coach_lap(trace, e)
        self._brief()
        if deltas is not None:
            self._level_check(deltas)
        if self.coaching and deltas is not None:
            self._coach(e, deltas)
            return
        if not self.state.session.is_race:
            # Practice and qualifying: the car "ahead" is ahead on lap time, often on a slow lap
            # (3 Nov one-shot qualifying: "losing one point nine seconds to the car ahead"
            # through one corner). Against the fastest lap of the session, else your own.
            if self.bench is not None and self.bench.car != me:
                self._compare(trace, self.bench.trace, "fastest")
            else:
                self._compare_best(trace)
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
        ref = self.reference_trace(trace.lap)
        if ref is not None:
            self._compare(trace, ref, "best" if self.features is None else "pace")

    def _compare(self, trace: LapTrace, ref: LapTrace, against: str) -> None:
        if self.track_map is None:
            return
        deltas = corner_deltas(trace, ref, self.track_map)
        self.last, self.last_against = deltas, against
        if not deltas:
            return
        losses = self._record(trace.lap, deltas)
        if against != "ahead" and self._in_a_fight():
            return                                 # streaks still counted, nothing said
        losses = [d for d in losses if not self._backed_off(d, trace.lap)]
        best = min(deltas, key=lambda d: d.delta_s)
        priority = Priority.LOW
        habits = [d for d in losses if self.streak(d.corner.number) >= self.cfg.focus_after_laps]
        found = self._improved(deltas, trace.lap)
        better = None
        if found is not None:
            d, gained = found
            self.praised[d.corner.number] = trace.lap
            better = (d, gained)
        if habits:
            # A corner that keeps costing time beats a bigger one-off loss, and is said with
            # more priority: fixing a habit is how you improve. Longest streak, then biggest.
            pick = max(habits, key=lambda d: (self.streak(d.corner.number), d.delta_s))
            text = self._loss_call(pick, trace.lap, against, self.streak(pick.corner.number))
            priority = Priority.NORMAL
        elif report := [d for d in losses if d.corner.number in self.focus_corners]:
            # A report corner: your usual weak spot here, so not a one-off even on lap one.
            pick = max(report, key=lambda d: d.delta_s)
            text = self._loss_call(pick, trace.lap, against, 0)
            priority = Priority.NORMAL
        elif losses:
            pick = max(losses, key=lambda d: d.delta_s)
            text = self._loss_call(pick, trace.lap, against, 0)
        elif better is not None:
            text = corner_better_phrase(better[0].corner.name, better[1])
            better = None
        elif self.verbosity == "chatty" and best.delta_s <= -self.cfg.min_loss_s:
            text = corner_phrase(best.delta_s, best.corner.name, against)
        else:
            return
        if better is not None:                     # both: the good news first, briefly
            text = corner_better_phrase(better[0].corner.name) + " " + text
        self.director.submit(RadioCall(topic="corner", text=text, priority=priority,
                                       max_wait_s=self.cfg.max_wait_s))

    def _log_coach_lap(self, trace: LapTrace, e: ev.LapCompleted) -> list[CornerDelta] | None:
        """Every clean lap of yours against the fastest lap on track, in any tempo: the app
        window's Coaching card and the end-of-session summary (coaching.py) read coach_laps."""
        if self.bench is None or self.track_map is None:
            return None
        deltas = corner_deltas(trace, self.bench.trace, self.track_map)
        rec = next((r for r in self.history.for_car(self.state.player_index)
                    if r.lap == e.lap), None)
        lap_ms = rec.lap_time_ms if rec is not None else 0
        lap = CoachLap(e.lap, lap_ms, lap_ms - self.bench.lap_time_ms if lap_ms else 0,
                       self._name(self.bench.car),
                       [CoachCorner(d.corner.number, d.corner.name, round(d.delta_s, 3),
                                    d.reasons()) for d in deltas])
        # A lap driven again after a flashback replaces the old one.
        self.coach_laps = [x for x in self.coach_laps if x.lap < e.lap] + [lap]
        return deltas

    def _coach(self, e: ev.LapCompleted, deltas: list[CornerDelta]) -> None:
        """Coaching mode: this lap against the fastest lap on track."""
        bench, me = self.bench, self.state.player_index
        rec = next((r for r in self.history.for_car(me) if r.lap == e.lap), None)
        lap_ms = rec.lap_time_ms if rec is not None else None
        self.last, self.last_against = deltas, "fastest"
        if not deltas:
            return
        losses = self._record(e.lap, deltas)
        most = 1 if self._in_a_fight() else 2
        found = self._improved(deltas, e.lap)
        better = None
        if found is not None:
            self.praised[found[0].corner.number] = e.lap
            better = found[0].corner.name
        # Habits first (longest streak), then the biggest losses.
        order = sorted(losses, key=lambda d: (-min(self.streak(d.corner.number),
                                                   self.cfg.focus_after_laps),
                                              d.corner.number not in self.focus_corners,
                                              -d.delta_s))
        items = []
        for d in order[:most]:
            n = d.corner.number
            streak = self.streak(n)
            streak = streak if streak >= self.cfg.focus_after_laps else 0
            self.called_on[n] = e.lap
            self.calls.setdefault(n, []).append((e.lap, d.delta_s, d.mine.time_s, streak))
            reason = self._habit_reason(d, e.lap, streak) if streak else self._report_reason(d)
            items.append((d.corner.name, int(round(d.delta_s * 1000)), reason, d.reasons(),
                          streak))
        gap_ms = (lap_ms - bench.lap_time_ms) if lap_ms else 0
        text = coaching_phrase(lap_ms, gap_ms, self._name(bench.car), items, better, self.detail)
        # Its own topic: once a lap, and not held back by the corner calls' cooldown.
        self.director.submit(RadioCall(topic="coaching", text=text, priority=Priority.NORMAL,
                                       max_wait_s=self.cfg.max_wait_s))

    def _name(self, car: int) -> str:
        n = self.state.cars.names[car] if car < len(self.state.cars.names) else None
        return n.title() if n else "the fastest car"

    def _loss_call(self, d: CornerDelta, lap: int, against: str, streak: int) -> str:
        n = d.corner.number
        again = not streak and n in self.called_on and lap - self.called_on[n] <= 3
        self.called_on[n] = lap
        self.calls.setdefault(n, []).append((lap, d.delta_s, d.mine.time_s, streak))
        reason = self._habit_reason(d, lap, streak) if streak else self._report_reason(d)
        report = not streak and n in self.focus_corners and n not in self.report_said
        if report:
            self.report_said.add(n)
        return corner_phrase(d.delta_s, d.corner.name, against, reason, self.verbosity,
                             again=again and not report, streak=streak, detail=self.detail,
                             reasons=d.reasons(), report=report)

    # -- the driver report -----------------------------------------------------------------------

    def _report_reason(self, d: CornerDelta) -> tuple[str, float] | None:
        """For a report corner, the cause its report habit points at if it shows this lap
        (one thing to work on, the same one the report names); otherwise this lap's biggest."""
        fc = self.focus_corners.get(d.corner.number)
        now = d.reasons()
        hits = [k for k in (fc.reasons if fc else ()) if k in now]
        if hits:
            k = max(hits, key=lambda k: now[k] / REASON_THRESHOLDS[k])
            return k, now[k]
        return d.reason()

    def _corner_name(self, number: int) -> str | None:
        c = next((c for c in self.track_map.corners if c.number == number), None) \
            if self.track_map else None
        return c.name if c else None

    def focus_brief(self) -> str | None:
        """What the report says to work on here: the report corners (worst first) and the habit
        behind them (their most common one, else your top habit overall). None: no report."""
        if self.focus is None or self.focus.empty:
            return None
        names = [n for c in self.focus_corners.values() if (n := self._corner_name(c.number))]
        themes = [c.theme for c in self.focus_corners.values() if c.theme]
        key = max(set(themes), key=themes.count) if themes else None
        habit = next((h for h in self.focus.habits if h["key"] == key), None) if key else None
        if habit is None and self.focus.habits:
            habit = self.focus.habits[0]
        if habit is None and not names:
            return None
        from engineer.report.focus import RADIO_HABIT
        words = RADIO_HABIT.get(habit["key"], habit.get("radio")) if habit else None
        return focus_brief_phrase(names[:2], words)

    def report_card(self) -> dict | None:
        """The Live tab: the report corners at this track and the habit, for the Coaching card."""
        if self.focus is None or self.focus.empty:
            return None
        corners = [{"name": self._corner_name(c.number) or f"T{c.number}",
                    "loss_s": c.loss_s, "laps": c.laps,
                    "level": c.number in self.level_said}
                   for c in self.focus_corners.values()]
        habit = self.focus.habits[0]["title"] if self.focus.habits else None
        return {"corners": corners, "habit": habit, "said": self.briefed}

    def _brief(self) -> None:
        """Once a session, after your first coached lap: what you're working on."""
        if self.briefed or self.focus is None:
            return
        self.briefed = True
        text = self.focus_brief()
        if text:
            self.director.submit(RadioCall(topic="coaching_focus", text=text,
                                           priority=Priority.NORMAL,
                                           max_wait_s=self.cfg.max_wait_s))

    def _level_check(self, deltas: list[CornerDelta]) -> None:
        """A report corner level with (or quicker than) the fastest lap on track: said once."""
        for d in deltas:
            n = d.corner.number
            if n in self.focus_corners and n not in self.level_said and d.delta_s <= 0.02:
                self.level_said.add(n)
                self.director.submit(RadioCall(topic="coaching_focus",
                                               text=focus_level_phrase(d.corner.name),
                                               priority=Priority.LOW,
                                               max_wait_s=self.cfg.max_wait_s))
                return

    def _backed_off(self, d: CornerDelta, lap: int) -> bool:
        """Once a corner has been called back_off_after times in its current streak, it's said
        only every other lap (skipped if it was said on the previous coached lap), unless it's
        getting worse (at least min_loss_s more than when last called)."""
        n = d.corner.number
        streak_laps = self.compared_laps[-self.streak(n):-1] if self.streak(n) > 1 else []
        called = {c[0] for c in self.calls.get(n, [])}
        if sum(x in called for x in streak_laps) < self.cfg.back_off_after:
            return False
        if streak_laps[-1] not in called:
            return False                           # skipped last lap: say it this lap
        last_loss = self.calls[n][-1][1]
        return d.delta_s < last_loss + self.cfg.min_loss_s

    def _improved(self, deltas: list[CornerDelta], lap: int) -> tuple[CornerDelta, float] | None:
        """A habit corner (called on a streak) in the last 3 coached laps that no longer loses
        time, where your own time through it is at least min_loss_s quicker than on the lap it
        was called. Your own times, not the deltas: the reference can change between laps
        (another car ahead). Only habits: after a one-off bad corner the next lap is usually
        better anyway, so "better" would be noise. Said once per call."""
        recent = [x for x in self.compared_laps if x < lap][-3:]
        out = []
        for d in deltas:
            n = d.corner.number
            calls = [c for c in self.calls.get(n, []) if c[0] in recent and c[3] > 0]
            if not calls or d.delta_s >= self.cfg.min_loss_s:
                continue
            called_lap, _loss, called_time, _streak = calls[-1]
            if self.praised.get(n, -1) >= called_lap:
                continue
            gained = called_time - d.mine.time_s
            if gained >= self.cfg.min_loss_s:
                out.append((d, gained))
        return max(out, key=lambda x: x[1]) if out else None

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


def _worst(rec) -> float:
    w = [v for v in rec.wear if not math.isnan(v)]
    return max(w) if w else math.nan
