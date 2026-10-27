"""Answers to push-to-talk questions, from the same query functions the engineer's own calls use,
so an answer always agrees with what the engineer would say unprompted.

Every question is logged to data/ptt/questions.jsonl (time, session, lap, what was heard, the
intent it matched, the answer). Questions that matched nothing are the list of phrasings to add
to engineer/voice/intents.py.

Answers go to the director as topic "answer": high priority and said straight away (you pressed
the button at a moment you chose), and still said in quiet mode.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import numpy as np

from engineer.bus import events as ev
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall
from engineer.tools.queries import lap_delta
from engineer.state.track_maps import turn_name
from engineer.voice.intents import driver_in, match, turn_in

# Damage parts reported by "any damage?" (state key -> spoken name), from 10 %.
DAMAGE_PARTS = {"front_left_wing": "front left wing", "front_right_wing": "front right wing",
                "rear_wing": "rear wing", "floor": "floor", "diffuser": "diffuser",
                "sidepod": "sidepod", "gearbox": "gearbox", "engine": "engine"}
DAMAGE_MIN_PCT = 10

log = logging.getLogger(__name__)

ANSWER_MAX_WAIT_S = 8.0


class QuestionLog:
    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None

    def write(self, entry: dict) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as exc:
            log.warning("could not write the question log %s (%s)", self.path, exc)


class QuestionAnswerer:
    def __init__(self, bus: ev.EventBus, engine, log_path: Path | str | None = None,
                 verbosity: str = "normal", max_answer_delay_s: float = 10.0):
        """engine: the app's Engine (state, director and the analyzers whose numbers we read).
        max_answer_delay_s: if speech recognition took longer than this, the answer would be
        stale (a gap from 40 s ago), so it isn't said; the question is still logged."""
        self.engine = engine
        self.max_answer_delay_s = max_answer_delay_s
        self.state = engine.pipeline.state
        self.director = engine.director
        self.verbosity = verbosity
        self.log = QuestionLog(log_path)
        self.answered: list[tuple[str, str]] = []      # (intent, answer), for tests and display
        self.heard = ""                                 # the question being answered
        bus.subscribe(ev.QuestionAsked, self.on_question)

    # -- entry point -----------------------------------------------------------------------------

    def on_question(self, e: ev.QuestionAsked) -> None:
        intent = match(e.text)
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "session_uid": f"{e.session_uid:016x}", "session_time": round(e.session_time, 3),
                 "lap": self._lap(), "heard": e.text, "intent": intent.name,
                 "audio": e.audio_path, "stt_ms": e.stt_ms}
        if e.stt_ms is not None and e.stt_ms > self.max_answer_delay_s * 1000:
            entry["answer"] = f"(not said: speech recognition took {e.stt_ms / 1000:.0f} s)"
            log.warning("question %r not answered: recognition took %.0f s", e.text,
                        e.stt_ms / 1000)
            self.log.write(entry)
            return
        handler = getattr(self, f"_a_{intent.name}", None)
        self.heard = e.text
        try:
            text = handler() if handler else None
        except Exception:
            log.exception("answering %r failed", e.text)
            text = None
        if text is None and intent.name == "box_now":
            entry["answer"] = "(pending: strategy is working it out)"
        else:
            text = text or phrases._sentence("say again")
            entry["answer"] = text
            self._say(intent.name, text)
        self.log.write(entry)

    def _say(self, intent: str, text: str) -> None:
        self.answered.append((intent, text))
        self.director.submit(RadioCall(topic="answer", text=text, priority=Priority.HIGH,
                                       max_wait_s=ANSWER_MAX_WAIT_S, wait_for_straight=False))

    # -- helpers ---------------------------------------------------------------------------------

    def _player(self) -> int | None:
        p = self.state.player_index
        return None if p is None or p >= len(self.state.cars.position) else p

    def _lap(self) -> int | None:
        p = self._player()
        lap = self.state.cars.lap[p] if p is not None else math.nan
        return None if np.isnan(lap) else int(lap)

    def _laps_to_go(self) -> int | None:
        lap, total = self._lap(), self.state.session.total_laps
        return None if lap is None or not total else max(1, total - lap + 1)

    def _name(self, car: int) -> str | None:
        n = self.state.cars.names[car]
        return n.title() if n else None

    # -- answers (one per intent) ----------------------------------------------------------------

    def _a_radio_check(self) -> str:
        return phrases._sentence("loud and clear")

    def _a_quiet(self) -> str:
        self.director.quiet = True
        return phrases._sentence("copy, quiet. Box calls only")

    def _a_talk(self) -> str:
        self.director.quiet = False
        return phrases._sentence("copy, back to normal")

    def _a_tyres(self) -> str:
        st = self.engine.tyres.status()
        if st is None or st.worst_wheel is None:
            # No lap finished yet: the live wear still says something (27 Oct race, lap 1).
            p = self._player()
            wear = self.state.cars.tyre_wear_pct[p] if p is not None else None
            if wear is None or np.isnan(wear).all():
                return phrases._sentence("no tyre numbers yet, give me a lap")
            w = int(np.nanargmax(wear))
            return phrases.live_tyres_phrase(w, float(wear[w]))
        return phrases.tyre_status_phrase(st, "chatty", laps_left_from_pct=0)

    def _a_plan(self) -> str:
        sa = self.engine.strategy
        if not sa._active():
            return phrases._sentence("no strategy calls in this race")
        res = sa.status()
        if res is None or res.sim.best is None:
            return phrases._sentence("no plan yet, give me a couple of laps")
        return phrases.plan_phrase(res.sim.best, res.sim.alternative(), self.verbosity)

    def _a_box_now(self) -> str | None:
        sa = self.engine.strategy

        def answer(res) -> None:
            if res is None or res.what_if is None or res.what_if.best is None \
                    or res.sim.best is None:
                text = phrases._sentence("can't work that out right now")
            else:
                rejoin = sa._rejoin(False) if res.model is not None else None
                text = phrases.box_now_phrase(res.what_if.best, res.sim.best, rejoin)
            self._say("box_now", text)
            self.log.write({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "intent": "box_now",
                            "answer": text, "lap": self._lap(), "followup": True})

        if not sa.ask_box_now(answer):
            return phrases._sentence("no strategy calls in this race") if not sa._active() \
                else phrases._sentence("too late for that one")
        return None

    def _gap(self, direction: str) -> str:
        c, p = self.state.cars, self._player()
        if p is None or np.isnan(c.position[p]):
            return phrases._sentence("no timing yet")
        if direction == "ahead":
            if c.position[p] == 1:
                return phrases.gap_answer_phrase("ahead", None, None)
            ahead = np.flatnonzero(c.active & (c.position == c.position[p] - 1))
            gap = c.delta_to_car_in_front_ms[p]
            car = int(ahead[0]) if ahead.size else None
        else:
            behind = np.flatnonzero(c.active & (c.position == c.position[p] + 1))
            if not behind.size:
                return phrases.gap_answer_phrase("behind", None, None)
            car = int(behind[0])
            gap = c.delta_to_car_in_front_ms[car]
        if np.isnan(gap):
            return phrases._sentence("no timing yet")
        return phrases.gap_answer_phrase(direction, self._name(car) if car is not None else None,
                                         float(gap))

    def _a_gap_ahead(self) -> str:
        return self._gap("ahead")

    def _a_gap_behind(self) -> str:
        return self._gap("behind")

    def _a_lap(self) -> str:
        p = self._player()
        d = lap_delta(self.engine.history, p) if p is not None else None
        if d is None:
            return phrases._sentence("no lap finished yet")
        return phrases.lap_delta_phrase(d, "chatty")

    # -- where the time is: corners when the map is known, else sectors ---------------------------

    def _a_losing_time(self) -> str:
        return self._where(losing=True)

    def _a_gaining_time(self) -> str:
        return self._where(losing=False)

    def _where(self, losing: bool) -> str:
        corners = self.engine.corners
        deltas = [d for d in corners.last if (d.delta_s > 0) == losing and d.delta_s != 0]
        if deltas:
            d = max(deltas, key=lambda d: abs(d.delta_s))
            return phrases.corner_phrase(d.delta_s, d.corner.name, corners.last_against or "best",
                                         d.reason(), "chatty")
        # No corner comparison yet (a new track's corner map is learned from 2 clean laps):
        # say it by sector instead.
        sectors, against = self._sector_deltas()
        if sectors:
            pick = [x for x in sectors if (x[1] > 0) == losing and x[1] != 0]
            if pick:
                sector, delta = max(pick, key=lambda x: abs(x[1]))
                return phrases.sector_phrase(sector, delta, against)
            who = phrases.against_words(against)
            return phrases._sentence(f"not losing anywhere to {who} last lap" if losing
                                     else f"not gaining anywhere on {who} last lap")
        return phrases._sentence("nothing to compare yet, give me a clean lap")

    def _sector_deltas(self, ref_car: int | None = None) -> tuple[list[tuple[int, int]], str]:
        """[(sector, my ms - reference ms)] for my last lap, against a given car's same lap,
        else the car ahead's same lap, else my best sectors."""
        hist, p = self.engine.history, self._player()
        mine = hist.for_car(p) if p is not None else []
        if not mine:
            return [], ""
        me = mine[-1]
        if me.invalid or me.pit_in_lap or me.pit_out_lap:
            return [], ""
        c = self.state.cars
        cands = []
        if ref_car is not None:
            cands.append((ref_car, self._name(ref_car) or "them"))
        else:
            ahead = np.flatnonzero(c.active & (c.position == c.position[p] - 1))
            if ahead.size:
                cands.append((int(ahead[0]), "ahead"))
        for car, label in cands:
            ref = next((r for r in hist.for_car(car) if r.lap == me.lap), None) \
                or (hist.for_car(car)[-1] if ref_car is not None and hist.for_car(car) else None)
            if ref is not None and not (ref.pit_in_lap or ref.pit_out_lap):
                return self._diff(me.sector_ms, ref.sector_ms), label
        if ref_car is not None:
            return [], ""
        others = [r for r in mine[:-1] if r.clean]
        if not others:
            return [], ""
        best = tuple(min((r.sector_ms[k] for r in others if r.sector_ms[k]), default=None)
                     for k in range(3))
        return self._diff(me.sector_ms, best), "best"

    @staticmethod
    def _diff(mine, ref) -> list[tuple[int, int]]:
        return [(k + 1, a - b) for k, (a, b) in enumerate(zip(mine, ref))
                if a is not None and b is not None]

    def _a_corner(self) -> str:
        """'How was that exit?' = the corner you just came out of, this lap, vs your best lap.
        'How was turn four?' = turn four, this lap if you've driven it, else last lap."""
        corners, traces, p = self.engine.corners, self.engine.traces, self._player()
        tmap = corners.track_map
        if tmap is None or not tmap.corners:
            return phrases._sentence("still learning this track's corners",
                                     "give me two clean laps")
        lap, dist = self._lap(), self.state.cars.lap_distance_m[p]
        n = turn_in(self.heard)
        corner = None
        if n is not None:
            corner = next((c for c in tmap.corners if c.number == n), None)
            if corner is None:
                return phrases._sentence(f"{turn_name(n)} isn't a braking zone on my map")
        elif not np.isnan(dist):
            passed = [c for c in tmap.corners if c.exit_m <= dist]
            corner = passed[-1] if passed else tmap.corners[-1]
        if corner is None or lap is None:
            return phrases._sentence("nothing to compare yet")
        this_lap = not np.isnan(dist) and corner.exit_m <= dist
        trace = traces.trace_for(p, lap if this_lap else lap - 1)
        ref = corners._best_trace(exclude_lap=lap if this_lap else lap - 1)
        if trace is None or ref is None:
            return phrases._sentence("no clean lap to compare with yet")
        from engineer.tools.queries import corner_stats
        from engineer.tools.queries import CornerDelta
        d = CornerDelta(corner, corner_stats(trace, corner), corner_stats(ref, corner))
        if abs(d.delta_s) < 0.05:
            return phrases._sentence(f"{corner.name}, same as your best")
        return phrases.corner_phrase(d.delta_s, corner.name, "best", d.reason(), "chatty")

    # -- other drivers ---------------------------------------------------------------------------

    def _named_driver(self) -> int | None:
        return driver_in(self.heard, self.state.cars.names)

    def _a_compare_driver(self) -> str:
        car, p = self._named_driver(), self._player()
        if car is None or p is None:
            return phrases._sentence("I don't see that driver in this race")
        name = self._name(car) or "them"
        hist = self.engine.history
        mine, theirs = hist.for_car(p), hist.for_car(car)
        if not mine or not theirs:
            return phrases._sentence(f"no clean lap to compare with {name} yet")
        # The latest lap you both drove cleanly (no race start, pit lap, safety car or invalid
        # lap: comparing those says nothing about pace).
        laps = {r.lap: r for r in theirs if r.clean}
        me = next((r for r in reversed(mine) if r.clean and r.lap in laps), None)
        if me is None:
            return phrases._sentence(f"no clean lap to compare with {name} yet")
        them = laps[me.lap]
        lap_delta = me.lap_time_ms - them.lap_time_ms
        gain = loss = None
        tmap = self.engine.corners.track_map
        mt, tt = self.engine.traces.trace_for(p, me.lap), self.engine.traces.trace_for(car, them.lap)
        if tmap is not None and mt is not None and tt is not None and mt.usable and tt.usable:
            from engineer.tools.queries import corner_deltas
            ds = corner_deltas(mt, tt, tmap)
            g = min(ds, key=lambda d: d.delta_s, default=None)
            lo = max(ds, key=lambda d: d.delta_s, default=None)
            gain = (g.corner.name, g.delta_s) if g is not None and g.delta_s <= -0.05 else None
            loss = (lo.corner.name, lo.delta_s) if lo is not None and lo.delta_s >= 0.05 else None
        else:
            secs = self._diff(me.sector_ms, them.sector_ms)
            g = min(secs, key=lambda x: x[1], default=None)
            lo = max(secs, key=lambda x: x[1], default=None)
            gain = (phrases._SECTORS[g[0]], g[1] / 1000) if g and g[1] <= -50 else None
            loss = (phrases._SECTORS[lo[0]], lo[1] / 1000) if lo and lo[1] >= 50 else None
        return phrases.compare_phrase(name, lap_delta, gain, loss)

    def _race_gap_ms(self, car: int) -> float | None:
        c = self.state.cars
        if np.isnan(c.position[car]):
            return None
        return 0.0 if c.position[car] == 1 else float(c.delta_to_leader_ms[car])

    def _a_gap_driver(self) -> str:
        car, p = self._named_driver(), self._player()
        if car is None or p is None:
            return phrases._sentence("I don't see that driver in this race")
        c = self.state.cars
        # Right next to you: the game's own gap to the car in front is the most accurate.
        if c.position[car] == c.position[p] - 1 and not np.isnan(c.delta_to_car_in_front_ms[p]):
            return phrases.gap_answer_phrase("ahead", self._name(car),
                                             float(c.delta_to_car_in_front_ms[p]))
        if c.position[car] == c.position[p] + 1 and not np.isnan(c.delta_to_car_in_front_ms[car]):
            return phrases.gap_answer_phrase("behind", self._name(car),
                                             float(c.delta_to_car_in_front_ms[car]))
        # Further away: the difference of both gaps to the leader.
        mine, theirs = self._race_gap_ms(p), self._race_gap_ms(car)
        if mine is None or theirs is None or np.isnan(mine) or np.isnan(theirs):
            return phrases._sentence("no timing yet")
        direction = "ahead" if c.position[car] < c.position[p] else "behind"
        return phrases.gap_answer_phrase(direction, self._name(car), abs(mine - theirs))

    def _a_gap_leader(self) -> str:
        c, p = self.state.cars, self._player()
        if p is None or np.isnan(c.position[p]):
            return phrases._sentence("no timing yet")
        if c.position[p] == 1:
            second = np.flatnonzero(c.active & (c.position == 2))
            if not second.size:
                return phrases.gap_leader_phrase(None, None)
            j = int(second[0])
            return phrases.gap_leader_phrase(None, float(c.delta_to_car_in_front_ms[j]),
                                             self._name(j))
        leader = np.flatnonzero(c.active & (c.position == 1))
        name = self._name(int(leader[0])) if leader.size else None
        return phrases.gap_leader_phrase(name or "the leader", float(c.delta_to_leader_ms[p]))

    # -- car and conditions ----------------------------------------------------------------------

    def _a_damage(self) -> str:
        p = self._player()
        if p is None:
            return phrases._sentence("no data yet")
        items = []
        for key, name in DAMAGE_PARTS.items():
            v = self.state.cars.damage[key][p]
            if not np.isnan(v) and v >= DAMAGE_MIN_PCT:
                items.append((name, float(v)))
        return phrases.damage_phrase(items)

    def _a_best_lap(self) -> str:
        p = self._player()
        laps = [r for r in self.engine.history.for_car(p) if not r.invalid] if p is not None else []
        if not laps:
            return phrases._sentence("no valid lap yet")
        best = min(laps, key=lambda r: r.lap_time_ms)
        return phrases.best_lap_phrase(best.lap_time_ms, best.lap)

    def _a_weather(self) -> str:
        s = self.state.session
        return phrases.weather_phrase(s.weather, s.track_temp_c, s.air_temp_c)

    def _a_fuel(self) -> str:
        p = self._player()
        margin = self.state.cars.fuel_remaining_laps[p] if p is not None else math.nan
        if np.isnan(margin):
            return phrases._sentence("no fuel reading yet")
        return phrases.fuel_phrase(float(margin))

    def _a_position(self) -> str:
        p = self._player()
        pos = self.state.cars.position[p] if p is not None else math.nan
        if np.isnan(pos):
            return phrases._sentence("no timing yet")
        return phrases.position_phrase(int(pos), self._laps_to_go())

    def _a_laps_left(self) -> str:
        n = self._laps_to_go()
        return phrases.laps_left_phrase(n) if n else phrases._sentence("no timing yet")
