"""Strategy calls: the race plan, box calls, safety car stops and undercut reminders.

After every lap you complete (from lap `announce_after_lap`), the strategy model is rebuilt with the
latest laps and every plan is simulated again (engineer/strategy). What you hear:

- The plan, once: "Plan is one stop, box lap eleven to thirteen for hards. Two stops is four
  seconds slower." After that only when it changes: a different number of stops or compounds, or
  the stop moving by `replan_laps` or more. A change has to show up on two laps running before
  it's called, so one odd lap can't flip the plan (a planned stop moving up to this lap is called
  at once, with the box call). Box calls are only made for a plan you've heard. After each of
  your stops the plan for the rest of the race is said again.
- Box calls for the plan's next stop: one lap before the pit entry, as you start the lap you'll
  pit at the end of ("Box this lap, box this lap. Hards. You'll come out behind Sainz, about two
  seconds back."), and again half a lap before it ("Box box, end of this lap. Hards.").
- Safety car / VSC: the plan is re-run at once with the cheaper stop. If boxing under it beats the
  plan: "Safety car, box this lap, box this lap. Mediums, it's a cheap stop." If you had a stop
  coming and it doesn't pay: "Safety car, stay out."
- Undercut reminder (information only, the plan isn't changed by it): when the car right behind
  you, within `undercut_gap_s`, pits while you still have a stop to make: "Norris behind has
  pitted, watch the undercut."

Only in races of `min_race_laps` or more, in the dry, on slicks. Wet strategy is not covered yet.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from engineer.bus import events as ev
from engineer.config import Strategy, Tyres
from engineer.features.lap_features import LapFeatureBuilder, LapFeatures
from engineer.ingest.formats import f1_2025 as f
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.state.race_state import RaceState
from engineer.strategy.model import DRY, GameTyreInfo, ModelSettings, TyreSet, game_tyre_info
from engineer.strategy.runner import HISTORY_COLUMNS, StrategyRequest, StrategyResult
from engineer.strategy.sim import Plan, RaceSituation

log = logging.getLogger(__name__)

SC_LATE_IN_LAP = 0.85        # SC out after 85% of the lap: too late for this lap's pit entry
RAIN = 3                     # session weather: 3 light rain and above


@dataclass(frozen=True)
class Rejoin:
    behind_name: str | None      # the car you'd come out behind, if close
    behind_ms: int | None
    ahead_of_name: str | None    # the car you'd come out just ahead of, if close
    positions_lost: int


class StrategyAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, features: LapFeatureBuilder,
                 director: RadioDirector, runner, settings: Strategy | None = None,
                 tyres: Tyres | None = None, verbosity: str = "normal"):
        self.state = state
        self.features = features
        self.director = director
        self.runner = runner
        self.cfg = settings or Strategy()
        self.tyres = tyres or Tyres()
        self.verbosity = verbosity
        self._rows: dict[int, tuple[LapFeatures, dict]] = {}
        self._callbacks: dict[int, object] = {}     # request seq -> push-to-talk answer
        self._seq = 0
        self._reset()
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.PitEntry, self.on_pit_entry)
        bus.subscribe(ev.SessionStarted, lambda e: (self._reset(), self._rows.clear()))
        bus.subscribe(ev.Flashback, self._on_flashback)

    def _reset(self) -> None:
        self.latest: StrategyResult | None = None
        self.announced: Plan | None = None
        self.pending_change: Plan | None = None
        self.box_lap: int | None = None          # committed: "box this lap" said for this lap
        self.half_said = False
        self.sc_status = 0
        self.applied_seq = -1

    def _on_flashback(self, _e) -> None:
        self.latest = None
        self.pending_change = None
        self.box_lap = None
        self.half_said = False
        self.applied_seq = self._seq             # anything still being solved is stale

    # -- what the engineer knows (also for push-to-talk) -----------------------------------------

    def status(self) -> StrategyResult | None:
        return self.latest

    # -- when it applies -------------------------------------------------------------------------

    def _active(self) -> bool:
        s, p = self.state.session, self.state.player_index
        if not self.cfg.enabled or not s.is_race or p is None:
            return False
        if (s.total_laps or 0) < self.cfg.min_race_laps:
            return False
        if s.weather is not None and s.weather >= RAIN:
            return False
        return self.state.cars.visual_compound[p] in DRY

    def _two_compound_rule(self) -> bool:
        rule = self.cfg.two_compound_rule
        if rule != "auto":
            return rule == "on"
        return (self.state.session.total_laps or 0) >= self.cfg.two_compound_min_laps

    # -- requests --------------------------------------------------------------------------------

    def _session_rows(self) -> tuple[dict, ...]:
        rows = []
        live = {}
        for laps in self.features.laps.values():
            for rec in laps:
                hit = self._rows.get(id(rec))
                if hit is None or hit[0] is not rec:
                    full = rec.to_row()
                    hit = (rec, {k: full.get(k) for k in HISTORY_COLUMNS})
                live[id(rec)] = hit
                rows.append(hit[1])
        self._rows = live
        return tuple(rows)

    def _sets(self) -> tuple[dict[int, list[float]], list[TyreSet]]:
        p = self.state.player_index
        raw = self.state.cars.tyre_sets[p]
        if raw is None:
            n = self.cfg.assume_sets_per_compound
            return {c: [0.0] * n for c in DRY}, []
        sets, info = {}, []
        for k, s in enumerate(raw["m_tyreSetData"]):
            c = int(s["m_visualTyreCompound"])
            fitted = bool(s["m_fitted"]) or k == int(raw["m_fittedIdx"])
            info.append(TyreSet(c, float(s["m_wear"]), int(s["m_usableLife"]) or None,
                                int(s["m_lapDeltaTime"]) / 1000.0, fitted))
            if s["m_available"] and not fitted and c in DRY:
                sets.setdefault(c, []).append(float(s["m_wear"]))
        return sets, info

    def ask_box_now(self, callback, compound: int | None = None) -> bool:
        """Push-to-talk "what if I box now?": callback(result) gets the best plan stopping at the
        end of this lap (result.what_if) next to the current plan (result.sim). False if there's
        no strategy in this race. compound: the asked-for tyre ("what if I pit for hards?")."""
        s, c, p = self.state, self.state.cars, self.state.player_index
        if not self._active() or np.isnan(c.lap[p]) or c.pit_status[p] > 0:
            return False
        lap = int(c.lap[p])
        length = s.session.track_length_m or 0
        late = length and not np.isnan(c.lap_distance_m[p]) \
            and c.lap_distance_m[p] > SC_LATE_IN_LAP * length
        box_lap = lap + 1 if late else lap
        if box_lap >= (s.session.total_laps or 0):
            return False
        sc = (box_lap,) if s.session.safety_car_status in (1, 2) else ()
        self._submit("box_now", lap - 1, sc_laps=sc, what_if_box_lap=box_lap, callback=callback,
                     what_if_compound=compound)
        return True

    def _submit(self, reason: str, lap_done: int, sc_laps: tuple[int, ...] = (),
                sc_compare: bool = False, what_if_box_lap: int | None = None,
                what_if_compound: int | None = None,
                callback=None) -> None:
        s, c, p = self.state, self.state.cars, self.state.player_index
        mine = self.features.for_car(p)
        stint = self.features.current_stint(p)
        sets, info = self._sets()
        wear_now = c.tyre_wear_pct[p]
        wear = float(np.nanmax(wear_now)) if not np.isnan(wear_now).all() else 0.0
        compound = int(c.visual_compound[p])
        used = {r.visual_compound for r in mine if r.visual_compound in DRY} | {compound}
        sit = RaceSituation(
            lap_done=lap_done, total_laps=int(s.session.total_laps), compound=compound,
            wear=wear, sets=sets, used=frozenset(used), require_two=self._two_compound_rule(),
            limit_pct=self.tyres.limit_pct, pit_loss_s=self.cfg.pit_loss_s, sc_laps=sc_laps,
            max_stops=self.cfg.max_stops, window_s=self.cfg.window_s,
            over_limit_s_per_pct=self.cfg.over_limit_s_per_pct, min_gain_s=self.cfg.min_gain_s)
        self._seq += 1
        rows = self._session_rows()
        stint_rows = tuple(self._rows[id(r)][1] for r in stint if id(r) in self._rows)
        game: GameTyreInfo | None = game_tyre_info(info) if info else None
        req = StrategyRequest(
            seq=self._seq, reason=reason, track_id=s.session.track_id,
            session_uid=f"{s.session_uid or 0:016x}", session_rows=rows,
            current_stint=stint_rows, game=game, situation=sit,
            settings=ModelSettings(
                fuel_s_per_kg=self.tyres.fuel_s_per_kg, ai_weight=self.cfg.ai_weight,
                game_prior_laps=self.cfg.game_prior_laps, game_deg_sd=self.cfg.game_deg_sd,
                fallback_deg_s_per_pct=self.cfg.fallback_deg_s_per_pct,
                pit_loss_s=self.cfg.pit_loss_s, sc_pit_loss_factor=self.cfg.sc_pit_loss_factor),
            sc_compare=sc_compare, what_if_box_lap=what_if_box_lap,
            what_if_compound=what_if_compound)
        if callback is not None:
            self._callbacks[self._seq] = callback
        self.runner.submit(req)
        self._drain()

    # -- events ----------------------------------------------------------------------------------

    def on_lap(self, e: ev.LapCompleted) -> None:
        p = self.state.player_index
        if e.car_idx != p or not self._active():
            return
        if self.box_lap is not None and e.lap >= self.box_lap and not e.pit_in_lap:
            self.box_lap = None                  # the stop didn't happen: back to planning
        total = self.state.session.total_laps or 0
        if e.lap < self.cfg.announce_after_lap or e.lap >= total:
            return
        # An in-lap ends in the pit lane, often still on the old tyres: plan again on the out-lap.
        if e.pit_in_lap or self.state.cars.pit_status[p] > 0:
            return
        sc_laps = (e.lap + 1,) if self.state.session.safety_car_status in (1, 2) else ()
        self._submit("lap", e.lap, sc_laps=sc_laps)

    def on_pit_entry(self, e: ev.PitEntry) -> None:
        p = self.state.player_index
        if e.car_idx == p:
            self.box_lap = None
            self.half_said = False
            # The plan from here is announced again after the stop ("Plan is no stop, tyres
            # will make the end." confirms the rest of the race).
            self.announced = self.pending_change = None
            return
        self._undercut_check(e.car_idx)

    def after_packet(self, packet_id: int | None) -> None:
        """Hooked into Pipeline.after_packet."""
        self._drain()
        if packet_id == f.PacketId.SESSION:
            self._safety_car_check()
        elif packet_id == f.PacketId.LAP_DATA:
            self._box_check()

    # -- results ---------------------------------------------------------------------------------

    def _drain(self) -> None:
        for res in self.runner.poll():
            callback = self._callbacks.pop(res.seq, None)
            if callback is not None:             # a question: answer it, the plan is untouched
                callback(None if res.error else res)
                continue
            if res.seq <= self.applied_seq or res.error or res.sim.best is None:
                continue
            self.applied_seq = res.seq
            self.latest = res
            if res.reason == "safety_car":
                self._on_sc_result(res)
            else:
                self._on_plan(res)
            self._box_check()

    def _on_plan(self, res: StrategyResult) -> None:
        best = res.sim.best
        if self.box_lap is not None:             # mid box call: the box calls do the talking
            return
        if self.announced is None:
            self.announced, self.pending_change = best, None
            self._say("strategy_plan", phrases.plan_phrase(best, res.sim.alternative(),
                                                           self.verbosity), Priority.LOW)
            return
        if not self._differs(best, self.announced):
            self.pending_change = None
            return
        if (best.next_stop is not None and best.next_stop.lap == res.lap_done + 1
                and best.n_stops == self.announced.n_stops):
            # The planned stop moved up to this lap: the box call itself is the news. (A new
            # stop, or one fewer, always needs a second lap to confirm.)
            self.announced, self.pending_change = best, None
            return
        if self.pending_change is not None and not self._differs(best, self.pending_change, 1):
            self.announced, self.pending_change = best, None
            self._say("strategy_plan", phrases.plan_phrase(best, None, self.verbosity,
                                                           change=True), Priority.LOW)
        else:
            self.pending_change = best           # confirm on the next lap first

    def _differs(self, a: Plan, b: Plan, laps: int | None = None) -> bool:
        if a.n_stops != b.n_stops or [s.compound for s in a.stops] != [s.compound for s in b.stops]:
            return True
        if not a.stops:
            return False
        return abs(a.stops[0].lap - b.stops[0].lap) >= (laps or self.cfg.replan_laps)

    # -- box calls -------------------------------------------------------------------------------

    def _box_check(self) -> None:
        s, c, p = self.state, self.state.cars, self.state.player_index
        if p is None or self.latest is None or np.isnan(c.lap[p]) or not self._active():
            return
        lap, in_pits = int(c.lap[p]), c.pit_status[p] > 0
        length = s.session.track_length_m or 0
        dist = c.lap_distance_m[p]
        best = self.latest.sim.best
        stop = best.next_stop if best else None
        # Box calls only for a plan you've been told about (a change is announced first).
        agreed = self.announced is not None and not self._differs(best, self.announced)
        if (self.box_lap is None and stop is not None and stop.lap == lap and not in_pits
                and self.latest.lap_done == lap - 1 and agreed):
            self.box_lap, self.half_said = lap, False
            if length and dist >= length / 2:    # already past half way: one call only
                self.half_said = True
                self._say("box", phrases.box_half_phrase(stop.compound), Priority.HIGH,
                          max_wait_s=10.0)
            else:
                rejoin = self._rejoin(self._sc_stop(stop))
                self._say("box", phrases.box_phrase(stop.compound, rejoin, self.verbosity),
                          Priority.HIGH)
            return
        if (self.box_lap == lap and not self.half_said and not in_pits and length
                and not math.isnan(dist) and dist >= length / 2):
            self.half_said = True
            compound = stop.compound if stop is not None else None
            if compound is not None:
                self._say("box", phrases.box_half_phrase(compound), Priority.HIGH,
                          max_wait_s=10.0)

    def _sc_stop(self, stop) -> bool:
        return self.latest is not None and stop.lap in self.latest.sc_laps

    def _rejoin(self, sc: bool) -> Rejoin | None:
        s, c, p = self.state, self.state.cars, self.state.player_index
        model = self.latest.model if self.latest else None
        if model is None or np.isnan(c.delta_to_leader_ms[p]):
            return None
        loss_ms = (model.pit_loss_sc if sc else model.pit_loss).value * 1000
        mine = 0.0 if c.position[p] == 1 else c.delta_to_leader_ms[p]
        after = mine + loss_ms
        others = [(0.0 if c.position[j] == 1 else float(c.delta_to_leader_ms[j]), j)
                  for j in np.flatnonzero(c.active) if j != p and c.pit_status[j] == 0
                  and not np.isnan(c.delta_to_leader_ms[j])]
        ahead = [(g, j) for g, j in others if g <= after]
        behind = [(g, j) for g, j in others if g > after]
        lost = sum(1 for g, _ in others if mine < g <= after)
        traffic = self.cfg.rejoin_traffic_s * 1000
        if ahead:
            g, j = max(ahead)
            if after - g <= traffic:
                return Rejoin(_name(c.names[j]), int(after - g), None, lost)
        if behind:
            g, j = min(behind)
            if g - after <= traffic:
                return Rejoin(None, None, _name(c.names[j]), lost)
        return Rejoin(None, None, None, lost)

    # -- safety car ------------------------------------------------------------------------------

    def _safety_car_check(self) -> None:
        s, c, p = self.state, self.state.cars, self.state.player_index
        status = s.session.safety_car_status or 0
        before, self.sc_status = self.sc_status, status
        if status not in (1, 2) or before in (1, 2) or not self._active():
            return
        if np.isnan(c.lap[p]) or c.pit_status[p] > 0:
            return
        lap = int(c.lap[p])
        if lap >= (s.session.total_laps or 0):
            return
        length = s.session.track_length_m or 0
        late = length and not np.isnan(c.lap_distance_m[p]) \
            and c.lap_distance_m[p] > SC_LATE_IN_LAP * length
        sc_lap = lap + 1 if late else lap
        if sc_lap >= (s.session.total_laps or 0):
            return
        self._submit("safety_car", lap - 1, sc_laps=(sc_lap,), sc_compare=True)

    def _on_sc_result(self, res: StrategyResult) -> None:
        c, p = self.state.cars, self.state.player_index
        kind = "virtual safety car" if self.sc_status == 2 else "safety car"
        best, green = res.sim.best, res.green.best if res.green else None
        lap_now = int(c.lap[p])
        stop = best.next_stop
        if stop is not None and stop.lap in (lap_now, lap_now + 1) and (
                green is None or green.next_stop is None or green.next_stop.lap != stop.lap):
            saving = green.cost_s - best.cost_s if green is not None else None
            self.announced, self.pending_change = best, None
            if stop.lap == lap_now:
                # This lap: commits the stop (the half-lap call still follows). Next lap: the
                # usual "box this lap" comes when that lap starts.
                self.box_lap, self.half_said = stop.lap, False
            self._say("box", phrases.safety_car_box_phrase(
                kind, stop.compound, next_lap=stop.lap != lap_now, saving_s=saving,
                verbosity=self.verbosity), Priority.HIGH, wait_for_straight=False)
            return
        if green is not None and green.n_stops > 0 and self.box_lap is None:
            self._say("safety_car", phrases.safety_car_stay_phrase(kind), Priority.NORMAL,
                      wait_for_straight=False)

    # -- undercut --------------------------------------------------------------------------------

    def _undercut_check(self, car: int) -> None:
        s, c, p = self.state, self.state.cars, self.state.player_index
        if not self.cfg.undercut_reminder or p is None or not self._active():
            return
        plan = self.latest.sim.best if self.latest else None
        if plan is None or plan.next_stop is None or c.pit_status[p] > 0:
            return
        if self.box_lap is not None or np.isnan(c.position[p]) or c.lap[p] <= 1:
            return
        if c.position[car] != c.position[p] + 1:
            return
        gap = c.delta_to_car_in_front_ms[car]
        if np.isnan(gap) or gap > self.cfg.undercut_gap_s * 1000:
            return
        self._say("undercut", phrases.undercut_phrase(_name(c.names[car])), Priority.NORMAL)

    # -- output ----------------------------------------------------------------------------------

    def _say(self, topic: str, text: str, priority: int, max_wait_s: float | None = None,
             wait_for_straight: bool = True) -> None:
        if text:
            self.director.submit(RadioCall(
                topic=topic, text=text, priority=priority,
                max_wait_s=self.cfg.max_wait_s if max_wait_s is None else max_wait_s,
                wait_for_straight=wait_for_straight))


def _name(raw: str | None) -> str | None:
    return raw.title() if raw else None
