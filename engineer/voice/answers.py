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
import re
import time
from collections import deque
from pathlib import Path

import numpy as np

from engineer.bus import events as ev
from engineer.radio import phrases
from engineer.radio.director import ANSWER_HOLD_S, Priority, RadioCall
from engineer.state.track_maps import turn_name
from engineer.strategy.decision import wear_at
from engineer.tools.queries import lap_delta
from engineer.voice.feedback import FeedbackLog
from engineer.voice.intents import (
    driver_in,
    lap_in,
    laps_in,
    log_text,
    match,
    sector_in,
    track_in,
    turn_in,
)

# Damage parts reported by "any damage?" (state key -> spoken name), from 10 %.
DAMAGE_PARTS = {"front_left_wing": "front left wing", "front_right_wing": "front right wing",
                "rear_wing": "rear wing", "floor": "floor", "diffuser": "diffuser",
                "sidepod": "sidepod", "gearbox": "gearbox", "engine": "engine"}
DAMAGE_MIN_PCT = 10

log = logging.getLogger(__name__)

ANSWER_MAX_WAIT_S = 15.0     # an answer is still worth hearing this late (it goes ahead of
                             # everything but box and safety car calls)
LOG_LAST_WITHIN_S = 180.0    # "log that" means the question asked up to this long before
LOG_NEXT_WITHIN_S = 30.0     # "log the following question" alone: the next question, this soon


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
                 verbosity: str = "normal", max_answer_delay_s: float = 10.0,
                 feedback: FeedbackLog | None = None, detail: str = "plain"):
        """engine: the app's Engine (state, director and the analyzers whose numbers we read).
        max_answer_delay_s: if speech recognition took longer than this, the answer would be
        stale (a gap from 40 s ago), so it isn't said; the question is still logged.
        feedback: where "log that" puts flagged questions (data/feedback/), None = nowhere."""
        self.engine = engine
        self.max_answer_delay_s = max_answer_delay_s
        self.state = engine.pipeline.state
        self.director = engine.director
        self.verbosity = verbosity
        self.detail = detail                            # corner causes: "plain" or "specific"
        self.log = QuestionLog(log_path)
        self.answered: list[tuple[str, str]] = []      # (intent, answer), for tests and display
        self.heard = ""                                 # the question being answered
        self.last_radio: str | None = None              # for "repeat that"
        self.last_strategy: str | None = None           # for "repeat the strategy call"
        self.feedback = feedback or FeedbackLog(None)
        self.recent_radio: deque[str] = deque(maxlen=3)  # kept with a flagged question
        self._prev: dict | None = None                  # the last question, for "log that"
        self._last_driver: int | None = None            # the driver last named, for "his lap"
        self._log_next_until: float | None = None       # "log the following question" alone
        bus.subscribe(ev.QuestionAsked, self.on_question)
        bus.subscribe(ev.RadioMessage, self._on_radio)
        bus.subscribe(ev.SessionEnded, self._on_session_ended)

    def _on_radio(self, e: ev.RadioMessage) -> None:
        if e.text != phrases._sentence("say again"):
            self.last_radio = e.text
        if e.topic != "answer":
            self.recent_radio.append(e.text)
        if e.topic in ("strategy_plan", "box", "safety_car"):
            self.last_strategy = e.text

    # -- entry point -----------------------------------------------------------------------------

    def on_question(self, e: ev.QuestionAsked) -> None:
        # Nothing else on the radio until this is answered (radio/director.py).
        self.director.listen(ANSWER_HOLD_S)
        intent = match(e.text)
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "session_uid": f"{e.session_uid:016x}", "session_time": round(e.session_time, 3),
                 "lap": self._lap(), "heard": e.text, "intent": intent.name,
                 "audio": e.audio_path, "stt_ms": e.stt_ms}
        # Flagging a question for the feedback log isn't time-critical, so it goes before the
        # recognition-delay check.
        if intent.name == "log_question" or self._logging_next():
            self._log_question(e, entry, intent.name == "log_question")
            return
        if e.stt_ms is not None and e.stt_ms > self.max_answer_delay_s * 1000:
            entry["answer"] = f"(not said: speech recognition took {e.stt_ms / 1000:.0f} s)"
            log.warning("question %r not answered: recognition took %.0f s", e.text,
                        e.stt_ms / 1000)
            self.log.write(entry)
            self.director.stop_listening()
            return
        handler = getattr(self, f"_a_{intent.name}", None)
        self.heard = e.text
        failed = False
        try:
            text = handler() if handler else None
        except Exception:
            log.exception("answering %r failed", e.text)
            text, failed = None, True
        # None from a handler = the strategy worker is on it and the answer follows (box_now,
        # box_shift, the pre-race plan).
        if text is None and handler is not None and not failed:
            entry["answer"] = "(pending: strategy is working it out)"
        else:
            text = text or phrases._sentence("say again")
            entry["answer"] = text
            self._say(intent.name, text)
        self.log.write(entry)
        self._prev = {"heard": e.text, "intent": intent.name, "answer": entry.get("answer"),
                      "at": time.monotonic()}

    def _say(self, intent: str, text: str) -> None:
        self.answered.append((intent, text))
        if self._prev and self._prev["intent"] == intent \
                and str(self._prev["answer"]).startswith("(pending"):
            self._prev["answer"] = text          # a strategy answer that came a moment later
        self.director.submit(RadioCall(topic="answer", text=text, priority=Priority.HIGH,
                                       max_wait_s=ANSWER_MAX_WAIT_S, wait_for_straight=False))

    # -- the feedback log ("log that") ------------------------------------------------------------

    def _logging_next(self) -> bool:
        if self._log_next_until is None:
            return False
        if time.monotonic() > self._log_next_until:
            self._log_next_until = None
            return False
        return True

    def _log_question(self, e: ev.QuestionAsked, entry: dict, command: bool) -> None:
        """Put a question in the feedback log: the one said with the command ("log the following
        question: ..."), the one just asked ("log that"), or, if neither, the next one."""
        self._log_next_until = None
        said = log_text(e.text) if command else e.text
        prev = self._prev if self._prev and time.monotonic() - self._prev["at"] \
            <= LOG_LAST_WITHIN_S else None
        flagged = {"time": entry["time"], "session_uid": entry["session_uid"],
                   "session_time": entry["session_time"], "lap": self._lap(),
                   "total_laps": int(self.state.session.total_laps or 0) or None,
                   "track": self.state.session.track_name,
                   "session": self.state.session.session_name, "said": e.text}
        if said:
            flagged.update(question=said, source="said" if command else "next question",
                           intent_then=match(said).name)
            reply = "copy, logged"
        elif prev is not None:
            flagged.update(question=prev["heard"], source="last question",
                           answer=prev["answer"], intent_then=prev["intent"])
            reply = "copy, logged that one"
        else:
            self._log_next_until = time.monotonic() + LOG_NEXT_WITHIN_S
            entry["answer"] = text = phrases._sentence("go ahead, what's the question")
            self._say("log_question", text)
            self.log.write(entry)
            return
        try:
            flagged["context"] = self._context()
        except Exception:                                # never lose the question over context
            log.exception("could not snapshot the race for the feedback log")
        entry["logged"] = flagged["question"]
        entry["context"] = flagged.get("context")
        entry["feedback_id"] = self.feedback.add(flagged)
        entry["answer"] = text = phrases._sentence(reply)
        self._say("log_question", text)
        self.log.write(entry)
        self._prev = None                                # "log that" twice doesn't log it twice

    def _context(self) -> dict:
        """The race when a question was flagged: enough to tell afterwards whether the data to
        answer it was there."""
        c, p = self.state.cars, self._player()
        ctx: dict = {"recent_radio": list(self.recent_radio),
                     "safety_car": self.state.session.safety_car_status in (1, 2)}
        if p is None:
            return ctx

        def num(v):
            return None if v is None or np.isnan(v) else v

        pos = num(c.position[p])
        ctx["position"] = int(pos) if pos else None
        ctx["cars"] = int(np.sum(c.active)) or None
        comp = num(c.visual_compound[p])
        ctx["tyre"] = phrases.say_compound(int(comp)) if comp is not None else None
        age = num(c.tyre_age_laps[p])
        ctx["tyre_age"] = int(age) if age is not None else None
        wear = c.tyre_wear_pct[p]
        ctx["wear_pct"] = None if np.isnan(wear).all() else round(float(np.nanmax(wear)))
        gap = num(c.delta_to_car_in_front_ms[p])
        if pos and pos > 1 and gap is not None:
            ctx["gap_ahead_s"] = round(gap / 1000, 1)
            ahead = np.flatnonzero(c.active & (c.position == pos - 1))
            ctx["ahead"] = self._name(int(ahead[0])) if ahead.size else None
        if pos:
            behind = np.flatnonzero(c.active & (c.position == pos + 1))
            if behind.size and num(c.delta_to_car_in_front_ms[behind[0]]) is not None:
                ctx["gap_behind_s"] = round(c.delta_to_car_in_front_ms[behind[0]] / 1000, 1)
                ctx["behind"] = self._name(int(behind[0]))
        plan = self.engine.strategy.committed() if self.engine.strategy else None
        if plan is not None:
            stop = plan.next_stop
            ctx["plan"] = (f"box end of lap {stop.lap} for {phrases.say_compound(stop.compound)}"
                           if stop else "no more stops")
        return ctx

    def _on_session_ended(self, e: ev.SessionEnded) -> None:
        """The race is over: its flagged questions get the result, and feedback-log.md is
        rebuilt (off the loop) so the list is ready when you get out of the car."""
        uid = f"{e.session_uid:016x}"
        result: dict = {"ended": e.reason}
        p = self._player()
        if self.state.session_uid == e.session_uid and p is not None:
            pos = self.state.cars.position[p]
            result["position"] = None if np.isnan(pos) else int(pos)
            result["laps"] = self._lap()
        if self.feedback.finish(uid, result):
            self.feedback.write_markdown_in_background()

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
        self.engine.set_tempo(self.engine.default_tempo)
        return phrases._sentence("copy, back to normal")

    def _a_tempo_chatty(self) -> str:
        self.engine.set_tempo("chatty")
        return phrases._sentence("copy, I'll keep you posted on everything")

    def _a_detail_specific(self) -> str:
        self.engine.set_detail("specific")
        return phrases._sentence("copy, I'll give you the numbers")

    def _a_detail_plain(self) -> str:
        self.engine.set_detail("plain")
        return phrases._sentence("copy, keeping it simple")

    def _a_tempo_coaching(self) -> str:
        self.engine.set_tempo("coaching")
        return phrases._sentence("copy, coaching. I'll tell you where the time is against the "
                                 "fastest lap each lap")

    def _a_gap_fastest(self) -> str:
        """"How far off the fastest car?" / "Where's the time?": your last lap against the
        fastest lap on track, and the corner it comes from most."""
        corners, p = self.engine.corners, self._player()
        bench = corners.bench
        if bench is None or p is None:
            return phrases._sentence("no clean laps on track to compare with yet")
        mine = [r for r in self.engine.history.for_car(p) if r.clean and r.lap_time_ms]
        if not mine:
            return phrases._sentence("give me a clean lap first")
        if bench.car == p:
            return phrases._sentence("you've got the fastest lap on track")
        best = min(r.lap_time_ms for r in mine)
        who = f"{corners._name(bench.car)}'s best"
        text = phrases._sentence(f"your best is {phrases.say_gap(best - bench.lap_time_ms)} off "
                                 f"{who}")
        if corners.last_against == "fastest":
            losing = sorted((d for d in corners.last if d.delta_s > 0), key=lambda d: -d.delta_s)
            if losing:
                d = losing[0]
                text += " " + phrases.corner_phrase(d.delta_s, d.corner.name, "fastest",
                                                    d.reason(), "chatty", detail=self.detail,
                                                    reasons=d.reasons())
        return text

    def _a_tempo_terse(self) -> str:
        self.engine.set_tempo("terse")
        return phrases._sentence("copy, essentials only")

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

    def _a_prerace_plan(self) -> str | None:
        """Before the race: the start tyre and the stops (strategy/prerace.py), for the track
        and laps said, else the ones remembered. Once the lights are out: the race plan."""
        sa = self.engine.strategy
        if sa.racing():
            return self._a_plan()

        def answer(plan) -> None:
            if plan.error == "no_laps":
                text = phrases._sentence("how many laps is the race",
                                         "say strategy for, then the laps")
            elif plan.error:
                text = phrases._sentence("can't work that out right now")
            else:
                text = phrases.prerace_phrase(plan, self.verbosity, sa.tyres.limit_pct,
                                              sa.cfg.margin_pct)
                self.last_strategy = text
            self._say("prerace_plan", text)

        why = sa.ask_prerace(answer, track_in(self.heard), laps_in(self.heard))
        if why == "no_track":
            return phrases._sentence("which track", "say strategy for, then the track")
        return None

    def _a_plan(self) -> str | None:
        sa = self.engine.strategy
        if not sa.racing() and self.engine.strategy.cfg.enabled:
            # Before the lights go out (menus, car setup, the grid): the pre-race plan.
            return self._a_prerace_plan()
        if self._lap() is None or not self.state.session.total_laps:
            # Before the start the game hasn't sent the race yet (28 Oct Melbourne: asked on the
            # grid, answered "no strategy calls in this race", which wasn't true).
            return phrases._sentence("no plan yet, I'll have one a couple of laps in")
        if not sa._active():
            return phrases._sentence("no strategy calls in this race")
        if sa.decision is None:
            p = self._player()
            if p is not None and (self.state.cars.pit_status[p] > 0 or sa._stops_made()):
                # Suzuka, 9 Nov: asked on the in-lap, after the stop was called, and got
                # "no plan yet, give me a couple of laps" on lap eleven.
                return phrases._sentence("working out the rest of the race after the stop",
                                         "give me a moment")
            return phrases._sentence("no plan yet, give me a couple of laps")
        # The committed call, said the same way as on the radio.
        return phrases.call_phrase(sa.decision, int(self.state.session.total_laps or 0),
                                   self.verbosity, after_stop=bool(sa._stops_made()),
                                   ahead=sa._ahead(), limit_pct=sa.tyres.limit_pct,
                                   margin_pct=sa.cfg.margin_pct)

    def _a_box_now(self) -> str | None:
        sa = self.engine.strategy

        compound = _compound_in(self.heard)

        def answer(res) -> None:
            if compound is not None and res is not None and res.sim.best is not None \
                    and (res.what_if is None or res.what_if.best is None):
                text = phrases._sentence(f"no {phrases.say_compound(compound)} left to fit")
            elif res is None or res.what_if is None or res.what_if.best is None \
                    or res.sim.best is None:
                text = phrases._sentence("can't work that out right now")
            else:
                rejoin = sa._rejoin(False) if res.model is not None else None
                text = phrases.box_now_phrase(res.what_if.best, res.sim.best, rejoin)
            self._say("box_now", text)
            # session_uid: so the follow-up travels with the recording's upload (28 Oct: box-now
            # answers were missing from the uploaded logs).
            self.log.write({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "intent": "box_now",
                            "session_uid": f"{self.state.session_uid or 0:016x}",
                            "session_time": round(self.state.session_time or 0.0, 3),
                            "answer": text, "lap": self._lap(), "followup": True})

        if not sa.ask_box_now(answer, compound):
            return phrases._sentence("no strategy calls in this race") if not sa._active() \
                else phrases._sentence("too late for that one")
        return None

    def _a_repeat(self) -> str:
        # 2 Nov: "Repeat the call and strategy" got the last radio call (a pass), not the plan.
        if re.search(r"\b(strategy|plan|pit|box|call)\b", self.heard.lower()) \
                and self.last_strategy:
            return self.last_strategy
        return self.last_radio or phrases._sentence("nothing to repeat yet")

    def _a_rejoin(self) -> str:
        """"Where am I going to come out?": if you boxed this lap (3 Nov, logged)."""
        sa, c, p = self.engine.strategy, self.state.cars, self._player()
        if p is None or np.isnan(c.position[p]):
            return phrases._sentence("no timing yet")
        r = sa._rejoin(self.state.session.safety_car_status in (1, 2)) \
            if sa.latest is not None else None
        if r is None:
            return phrases._sentence("can't work that out yet, give me a lap")
        before = sa.position_before_pit if c.pit_status[p] > 0 else None
        pos = (before or int(c.position[p])) + r.positions_lost
        return phrases.rejoin_answer_phrase(pos, r)

    def _a_true_position(self) -> str:
        """"Where's my track position?": where you are once the stops shake out (2 Nov, logged:
        "give accurate track position"). In a dry race every car has to stop at least once, so
        a car ahead that hasn't stopped will lose a stop's time; if you still have a stop to
        make, cars behind that have made theirs gain it."""
        c, p, sa = self.state.cars, self._player(), self.engine.strategy
        if p is None or np.isnan(c.position[p]) or np.isnan(c.num_pit_stops[p]):
            return phrases._sentence("no timing yet")
        pos = int(c.position[p])
        model = sa.latest.model if sa.latest is not None else None
        loss = (model.pit_loss.value if model is not None else sa.cfg.pit_loss_s) * 1000

        def t(j):
            return 0.0 if c.position[j] == 1 else float(c.delta_to_leader_ms[j])
        if np.isnan(t(p)):
            return phrases.position_phrase(pos, self._laps_to_go())
        plan = sa.committed()
        mine_done = int(c.num_pit_stops[p])
        mine_left = len(plan.stops) if plan is not None else (0 if mine_done else 1)
        to_stop, passing = [], []
        for j in np.flatnonzero(c.active):
            if j == p or c.pit_status[j] > 0 or np.isnan(c.num_pit_stops[j]) or np.isnan(t(j)):
                continue
            done = int(c.num_pit_stops[j])
            if c.position[j] < pos and done == 0 and mine_done >= 1 and t(j) + loss > t(p):
                to_stop.append(int(j))               # still has to stop, will drop behind
            elif c.position[j] > pos and mine_left and done > mine_done \
                    and t(j) < t(p) + loss:
                passing.append(int(j))               # has stopped; you'll drop behind them
        real = pos - len(to_stop) + len(passing)
        return phrases.true_position_phrase(pos, real, [self._name(j) for j in to_stop],
                                            [self._name(j) for j in passing])

    def _a_tyre_tips(self) -> str:
        """"Any tips for managing the tyres? Where am I over-working them?" (2 Nov, logged):
        the tyre that limits you, the wear you can afford to reach the stop or the flag, where
        it runs hottest, and what to change for that axle."""
        st, p = self.engine.tyres.status(), self._player()
        if st is None or st.worst_wheel is None or p is None:
            return phrases._sentence("no tyre numbers yet, give me a lap")
        w = st.limiting_wheel if st.limiting_wheel is not None else st.worst_wheel
        rate = float(st.wear_rate[w])
        plan = self.engine.strategy.committed()
        lap = self._lap()
        target = None
        if lap is not None:
            stop = plan.next_stop if plan is not None else None
            end = stop.lap if stop is not None else self.state.session.total_laps
            if end and end >= lap:
                laps = end - lap + 1
                target = ((st.limit_pct - float(st.wear[w])) / laps, stop.lap if stop else None)
        hot = None
        laps_f = self.engine.features.for_car(p)
        temps = laps_f[-1].temps if laps_f else None
        if temps is not None:
            k = int(np.argmax([temps.hot_frac[i][w] for i in range(3)]))
            if temps.hot_frac[k][w] >= 0.3:
                hot = k + 1
        return phrases.tyre_tips_phrase(w, None if np.isnan(rate) else rate, target, hot)

    def _a_ideal_lap(self) -> str:
        """"What's my ideal lap?": the rolling ideal from your last five clean laps this stint,
        and how far the latest lap was off it (tools/ideal.py)."""
        i = self.engine.corners.ideal
        if i is None:
            return phrases._sentence("not enough clean laps this stint yet, give me three")
        name, _n, loss = i.losses()[0]
        return phrases.ideal_answer_phrase(i.ms, len(i.laps), i.gap_ms,
                                           name if loss >= 0.05 else None)

    def _a_report_focus(self) -> str:
        """"What should I work on?": the driver report's corners here and the habit behind them
        (the same words as the briefing at the start of a session)."""
        text = self.engine.corners.focus_brief()
        return text or phrases._sentence("no driver report yet, build one from the report tab")

    def _a_speed_tips(self) -> str:
        """"Give me tips on how to speed up" (3 Nov, logged): the two corners costing the most
        on the last lap and why, from the same comparison as the corner calls."""
        corners = self.engine.corners
        losing = sorted((d for d in corners.last if d.delta_s > 0), key=lambda d: -d.delta_s)
        if not losing:
            return self._where(True)
        against = corners.last_against or "best"
        first = phrases.corner_phrase(losing[0].delta_s, losing[0].corner.name, against,
                                      losing[0].reason(), "chatty", detail=self.detail,
                                      reasons=losing[0].reasons())
        if len(losing) < 2:
            return first
        second = phrases.corner_phrase(losing[1].delta_s, losing[1].corner.name, against,
                                       losing[1].reason(), "chatty", detail=self.detail,
                                       reasons=losing[1].reasons())
        return f"{first} Then {second[0].lower()}{second[1:]}"

    def _a_status(self) -> str:
        """"How are we looking?": position, the gap ahead, the tyres and the next stop."""
        parts = []
        p = self._player()
        pos = self.state.cars.position[p] if p is not None else math.nan
        if np.isnan(pos):
            return phrases._sentence("no timing yet")
        parts.append(phrases.position_phrase(int(pos), self._laps_to_go()))
        if pos > 1:
            parts.append(self._gap("ahead"))
        st = self.engine.tyres.status()
        if st is not None and st.worst_wheel is not None:
            parts.append(phrases._sentence(
                f"tyres at {phrases.say_percent(st.worst_pct)}",
                phrases.say_laps_left(st.laps_left) if st.laps_left is not None else ""))
        plan = self.engine.strategy.committed()
        if plan is not None:
            stop = plan.next_stop
            parts.append(phrases._sentence(
                f"box end of lap {phrases.number_words(stop.lap)} for "
                f"{phrases.say_compound(stop.compound)}" if stop else "no stop planned"))
        return " ".join(parts)

    def _a_boxing_now(self) -> str:
        """"Are we boxing now?": yes / not yet (and when) / no stop planned."""
        plan, lap = self.engine.strategy.committed(), self._lap()
        if plan is None or lap is None:
            return self._a_plan()
        stop = plan.next_stop
        if stop is None:
            return phrases._sentence("no, no stop planned, the tyres make the end")
        if stop.lap == lap:
            return phrases._sentence(f"yes, box this lap for {phrases.say_compound(stop.compound)}")
        if lap < stop.lap:
            return phrases._sentence(f"not yet, box end of lap {phrases.number_words(stop.lap)}",
                                     phrases.say_compound(stop.compound))
        return phrases._sentence(f"we're past the stop, box as soon as you can for "
                                 f"{phrases.say_compound(stop.compound)}")

    def _a_box_shift(self) -> str | None:
        """"What if I go a lap later / two laps earlier?" against the committed stop."""
        sa, plan = self.engine.strategy, self.engine.strategy.committed()
        if plan is None or plan.next_stop is None:
            return phrases._sentence("no stop planned to move")
        shift = _shift_in(self.heard)
        stop = plan.next_stop
        lap = stop.lap + shift
        limit = sa.tyres.limit_pct
        # 3 Nov: "Should I pit later for hards?" -> that tyre, not the planned one.
        compound = _compound_in(self.heard) or stop.compound

        def answer(res) -> None:
            if res is None or res.what_if is None or res.what_if.best is None:
                text = phrases._sentence("can't work that out right now")
            else:
                keep = res.hold or res.sim.best
                wear = wear_at(res.situation, res.model, lap) if res.situation else None
                text = phrases.shift_phrase(shift, res.what_if.best.cost_s - keep.cost_s, wear,
                                            limit)
                if compound != stop.compound:
                    text = phrases._sentence(f"on {phrases.say_compound(compound)}") + " " + text
            self._say_followup("box_shift", text)

        if not sa.ask_what_if(answer, lap, compound):
            return phrases._sentence("too late for that one")
        return None

    def _a_tyres_to_end(self) -> str:
        """"Will these tyres make the end?" / "Will I get over 45 percent?": the worst tyre's
        wear at the flag at the current rate, against the limit (or the number you said)."""
        st, to_go = self.engine.tyres.status(), self._laps_to_go()
        if st is None or st.worst_wheel is None or to_go is None:
            return phrases._sentence("no tyre numbers yet, give me a lap")
        rate = st.wear_rate[st.worst_wheel] if st.limiting_wheel is None \
            else st.wear_rate[st.limiting_wheel]
        if np.isnan(rate):
            return phrases._sentence("no wear rate yet, give me another lap")
        wheel = st.limiting_wheel if st.limiting_wheel is not None else st.worst_wheel
        at_flag = float(st.wear[wheel] + rate * to_go)
        asked = _percent_in(self.heard)
        limit = asked if asked is not None else st.limit_pct
        where = phrases.WHEEL_NAMES[wheel]
        finish = f"{where} finishes at about {phrases.say_percent(at_flag)}, " \
                 f"{phrases.number_words(min(to_go, 99))} laps to go"
        if at_flag <= limit:
            verdict = f"stays under {phrases.say_percent(limit)}" if asked is not None \
                else "they'll make the end"
        else:
            laps_ok = max(0, int((limit - st.wear[wheel]) / rate)) if rate > 0 else to_go
            verdict = f"over {phrases.say_percent(limit)} in about " \
                      f"{phrases.number_words(min(laps_ok, 99))} laps"
        return phrases._sentence(finish, verdict)

    def _a_tyre_age(self) -> str:
        p = self._player()
        age = self.state.cars.tyre_age_laps[p] if p is not None else math.nan
        if np.isnan(age):
            return phrases._sentence("no tyre numbers yet")
        comp = self.state.cars.visual_compound[p]
        name = phrases.say_compound(int(comp)) if not np.isnan(comp) else "these"
        n = int(age)
        return phrases._sentence(f"{phrases.number_words(min(n, 99))} lap{'s' if n != 1 else ''} "
                                 f"on these {name}" if name != "these" else
                                 f"{phrases.number_words(min(n, 99))} laps on these")

    def _a_who_pitted(self) -> str:
        """"Who's pitted in front of me?": the cars just ahead and behind, and how many stops
        they've made against yours."""
        c, p = self.state.cars, self._player()
        if p is None or np.isnan(c.position[p]) or np.isnan(c.num_pit_stops[p]):
            return phrases._sentence("no timing yet")
        mine, pos = int(c.num_pit_stops[p]), int(c.position[p])
        bits = []
        for d, word in ((-1, "ahead"), (1, "behind")):
            idx = np.flatnonzero(c.active & (c.position == pos + d))
            if not idx.size or np.isnan(c.num_pit_stops[idx[0]]):
                continue
            n, name = int(c.num_pit_stops[idx[0]]), self._name(int(idx[0])) or f"car {word}"
            if c.pit_status[idx[0]] > 0:
                bits.append(f"{name} {word} is in the pits now")
            elif n == mine:
                bits.append(f"{name} {word} has stopped the same as you")
            else:
                more = "more" if n > mine else "fewer"
                diff = abs(n - mine)
                bits.append(f"{name} {word} has {phrases.number_words(diff)} {more} "
                            f"stop{'s' if diff != 1 else ''} than you")
        return phrases._sentence(*bits) if bits else phrases._sentence("nobody near you")

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
        if car is not None:
            self._last_driver = car
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
                                         d.reason(), "chatty", detail=self.detail,
                                         reasons=d.reasons())
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
        ref_lap = lap if this_lap else lap - 1
        ref = corners.reference_trace(ref_lap) or corners._best_trace(exclude_lap=ref_lap)
        if trace is None or ref is None:
            return phrases._sentence("no clean lap to compare with yet")
        from engineer.tools.queries import CornerDelta, corner_stats
        d = CornerDelta(corner, corner_stats(trace, corner), corner_stats(ref, corner))
        if abs(d.delta_s) < 0.05:
            return phrases._sentence(f"{corner.name}, right on your pace")
        return phrases.corner_phrase(d.delta_s, corner.name, "pace", d.reason(), "chatty",
                                     detail=self.detail, reasons=d.reasons())

    # -- other drivers ---------------------------------------------------------------------------

    def _named_driver(self) -> int | None:
        car = driver_in(self.heard, self.state.cars.names)
        if car is not None:
            self._last_driver = car
            return car
        # "What was his last lap relative to mine?" (9 Nov, after "Verstappen behind ..."): the
        # driver named last, in a question or an answer.
        if re.search(r"\b(his|her|their|him|them|he|she|they)\b", self.heard.lower()):
            return self._last_driver
        return None

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
            ds = corner_deltas(mt, tt, tmap, self.state.session.manual_gears)
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
        text = phrases.damage_phrase(items)
        # 3 Nov: "How should I be thinking about this damage?" -> what it costs, and whether to
        # stop for it.
        rc, sa = self.engine.racecraft, self.engine.strategy
        if items and rc is not None and rc.damage_lap is not None:
            laps = [r for r in self.engine.history.for_car(p) if r.clean and r.lap_time_ms]
            before = [r.lap_time_ms for r in laps if r.lap < rc.damage_lap][-4:]
            after = [r.lap_time_ms for r in laps if r.lap > rc.damage_lap][-3:]
            if not after:
                return text + " " + phrases._sentence("give me a clean lap to see what it costs")
            if not before:
                # Damaged before any clean lap (Jeddah, 9 Nov: lap one, "Should I fix the
                # wing?" asked four times got "give me a clean lap" every time). Nothing of
                # yours to compare with, so compare with the quickest cars on the same laps.
                return text + " " + self._damage_vs_field(p, laps, rc.damage_lap)
            loss_s = (float(np.median(after)) - float(np.median(before))) / 1000
            pit_s = sa.latest.model.pit_loss.value if sa.latest and sa.latest.model \
                else sa.cfg.pit_loss_s
            stop = sa.committed().next_stop if sa.committed() is not None else None
            text += " " + phrases.damage_cost_phrase(loss_s, self._laps_to_go() or 0, pit_s,
                                                     stop.lap if stop else None)
        return text

    def _damage_vs_field(self, p: int, laps: list, damage_lap: int) -> str:
        """Damage from before your first clean lap: your clean laps since vs the three quickest
        other cars on the same laps. Only an upper bound (it's driving too), so it's said as
        one: on the leaders' pace = not costing much."""
        mine, field = [], []
        for r in [r for r in laps if r.lap > damage_lap][-3:]:
            others = sorted(o.lap_time_ms for car in range(len(self.state.cars.names))
                            if car != p for o in self.engine.history.for_car(car)
                            if o.lap == r.lap and o.clean and o.lap_time_ms)
            if len(others) >= 3:
                mine.append(r.lap_time_ms)
                field.append(float(np.median(others[:3])))
        if not mine:
            return phrases._sentence("give me a clean lap to see what it costs")
        gap_s = (float(np.median(mine)) - float(np.median(field))) / 1000
        return phrases.damage_vs_field_phrase(gap_s)

    # -- 3 Nov (20-lap races): questions that got "Say again" or the wrong answer --------------

    def _a_fastest_overall(self) -> str:
        """"What's the fastest lap right now?": the session's fastest lap, by anyone."""
        p, hist = self._player(), self.engine.history
        best = None
        for car in range(len(self.state.cars.names)):
            for r in hist.for_car(car):
                if r.lap_time_ms and not r.invalid and (best is None or r.lap_time_ms < best[1].lap_time_ms):
                    best = (car, r)
        if best is None:
            return phrases._sentence("no valid laps yet")
        car, r = best
        if car == p:
            return phrases._sentence(f"you have it, {phrases.say_lap_time(r.lap_time_ms)}",
                                     f"lap {phrases.number_words(r.lap)}")
        text = phrases._sentence(f"fastest lap is {self._name(car) or 'another car'}, "
                                 f"{phrases.say_lap_time(r.lap_time_ms)}",
                                 f"lap {phrases.number_words(r.lap)}")
        mine = [x.lap_time_ms for x in hist.for_car(p) if x.lap_time_ms and not x.invalid] \
            if p is not None else []
        if mine:
            text += " " + phrases._sentence(f"your best is {phrases.say_gap(min(mine) - r.lap_time_ms)} "
                                            f"off that")
        return text

    def _a_what_if_lap(self) -> str | None:
        """"If I box at lap 10 for hards, how am I looking?": the best plan stopping at the end
        of that lap (onto that tyre), against the committed plan."""
        sa = self.engine.strategy
        lap, compound = lap_in(self.heard), _compound_in(self.heard)
        now = self._lap()
        if lap is None or now is None:
            return phrases._sentence("which lap")
        if lap < now:
            return phrases._sentence(f"lap {phrases.number_words(lap)} has gone")

        def answer(res) -> None:
            if res is None or res.what_if is None or res.what_if.best is None \
                    or res.sim.best is None:
                text = phrases._sentence(f"can't make lap {phrases.number_words(lap)} work"
                                         + (f" on {phrases.say_compound(compound)}" if compound else ""))
            else:
                keep = res.hold or res.sim.best
                wear = wear_at(res.situation, res.model, lap) if res.situation else None
                text = phrases.what_if_phrase(lap, res.what_if.best, keep, wear,
                                              sa.tyres.limit_pct, res.model,
                                              int(self.state.session.total_laps or 0))
            self._say_followup("what_if_lap", text)

        if not sa.ask_what_if(answer, lap, compound):
            return phrases._sentence("no strategy calls in this race") if not sa._active() \
                else phrases._sentence("can't work that out right now")
        return None

    def _a_why_not(self) -> str | None:
        """"Why not hards?" / "Should I go hards instead?": that tyre at the planned stop,
        against the plan: the time it costs and how worn it would finish."""
        sa = self.engine.strategy
        compound = _compound_in(self.heard)
        plan = sa.committed()
        if compound is None:
            return phrases._sentence("which tyre")
        if plan is None:
            return phrases._sentence("no plan yet, give me a couple of laps")
        stop = plan.next_stop
        if stop is None:
            return phrases._sentence("no more stops planned, these go to the end")
        if stop.compound == compound:
            return phrases._sentence(f"{phrases.say_compound(compound)} are the plan, lap "
                                     f"{phrases.number_words(stop.lap)}")
        lead = self._clear_gap_ms()

        def answer(res) -> None:
            if res is None or res.what_if is None or res.what_if.best is None:
                text = phrases._sentence(f"no {phrases.say_compound(compound)} left to fit")
            else:
                text = phrases.why_not_phrase(compound, stop, res.what_if.best, res.hold or res.sim.best,
                                              res.model, int(self.state.session.total_laps or 0),
                                              sa.tyres.limit_pct, sa.cfg.margin_pct, lead)
            self._say_followup("why_not", text)

        if not sa.ask_what_if(answer, stop.lap, compound):
            return phrases._sentence("can't work that out right now")
        return None

    def _clear_gap_ms(self) -> float | None:
        """The smaller of your gaps to the cars ahead and behind (None if unknown): the time a
        slower plan can give away without changing a place."""
        c, p = self.state.cars, self._player()
        if p is None or np.isnan(c.position[p]):
            return None
        gaps = []
        behind = np.flatnonzero(c.active & (c.position == c.position[p] + 1))
        if behind.size and not np.isnan(c.delta_to_car_in_front_ms[behind[0]]):
            gaps.append(float(c.delta_to_car_in_front_ms[behind[0]]))
        return min(gaps) if gaps else None

    def _a_compound_to_end(self) -> str:
        """"Will hards make it to the end?": a new set of that tyre from the end of this lap."""
        sa, compound = self.engine.strategy, _compound_in(self.heard)
        res, now, total = sa.latest, self._lap(), self.state.session.total_laps
        if compound is None:
            return self._a_tyres_to_end()
        cm = res.model.compounds.get(compound) if res is not None and res.model else None
        if cm is None or now is None or not total:
            return phrases._sentence(f"no wear numbers for the {phrases.say_compound(compound)} yet")
        laps = max(0, int(total) - now)
        return phrases.new_set_phrase(compound, laps, cm.wear_rate.value, sa.tyres.limit_pct)

    def _a_stints_end(self) -> str:
        """"What will they be by the end?" / "...at the end of the second stint?": each stint of
        the plan and how worn it comes off."""
        sa = self.engine.strategy
        if sa.decision is None:
            return self._a_tyres_to_end()
        return phrases.stints_phrase(sa.decision.call.stints, int(self.state.session.total_laps or 0))

    def _a_driver_pitted(self) -> str:
        """"Has Albon pitted?": stops, when, and the tyres now."""
        car = self._named_driver()
        if car is None:
            return phrases._sentence("I don't see that driver in this race")
        c, name = self.state.cars, self._name(car) or "they"
        if c.pit_status[car] > 0:
            return phrases._sentence(f"{name} is in the pits now")
        n = 0 if np.isnan(c.num_pit_stops[car]) else int(c.num_pit_stops[car])
        comp = c.visual_compound[car]
        tyre = phrases.say_compound(int(comp)) if not np.isnan(comp) else None
        age = None if np.isnan(c.tyre_age_laps[car]) else int(c.tyre_age_laps[car])
        if n == 0:
            return phrases._sentence(f"{name} hasn't stopped",
                                     f"on {tyre}" + (f", {phrases.number_words(min(age, 99))} laps old"
                                                     if age is not None else "") if tyre else "")
        laps = [r.lap for r in self.engine.history.for_car(car) if r.pit_in_lap]
        when = f"on lap {phrases.number_words(laps[-1])}" if laps else ""
        times = "once" if n == 1 else f"{phrases.number_words(n)} times"
        return phrases._sentence(f"{name} has pitted {times}", when,
                                 f"now on {tyre}" if tyre else "")

    def _a_catch(self) -> str:
        """"Can I catch the leader?": the gap, the pace difference over the last laps, and the
        laps left."""
        c, p, hist = self.state.cars, self._player(), self.engine.history
        if p is None or np.isnan(c.position[p]):
            return phrases._sentence("no timing yet")
        car = self._named_driver()
        if car is None:
            want = 1 if re.search(r"\bleader\b", self.heard.lower()) else int(c.position[p]) - 1
            idx = np.flatnonzero(c.active & (c.position == want))
            car = int(idx[0]) if idx.size else None
        if car is None or car == p:
            return phrases._sentence("you're leading, nobody to catch")
        if c.position[car] == c.position[p] - 1 and not np.isnan(c.delta_to_car_in_front_ms[p]):
            gap_ms = float(c.delta_to_car_in_front_ms[p])     # right ahead: the game's own gap
        else:
            gap, mine = self._race_gap_ms(car), self._race_gap_ms(p)
            if gap is None or mine is None or np.isnan(gap) or np.isnan(mine):
                return phrases._sentence("no timing yet")
            gap_ms = mine - gap
        pace = []
        for who in (p, car):
            laps = [r.lap_time_ms for r in hist.for_car(who) if r.clean and r.lap_time_ms][-3:]
            pace.append(float(np.median(laps)) if laps else math.nan)
        to_go = self._laps_to_go() or 0
        return phrases.catch_phrase(self._name(car) or "them", gap_ms, pace[1] - pace[0], to_go)

    def _a_sector(self) -> str:
        """"How did sector one look relative to Lewis?" / "sector two to my best?"."""
        n, car = sector_in(self.heard), self._named_driver()
        if n is None:
            return phrases._sentence("which sector")
        if car is not None:
            deltas, against = self._sector_deltas_vs(car)
        else:
            deltas, against = self._sector_deltas_best()
        d = next((ms for k, ms in deltas if k == n), None)
        if d is None:
            return phrases._sentence(f"no clean sector {phrases.number_words(n)} to compare yet")
        return phrases.sector_phrase(n, d, against)

    def _sector_deltas_vs(self, car: int) -> tuple[list[tuple[int, int]], str]:
        """Against another car: the latest lap you both drove cleanly (your very last lap may
        have been invalid or a pit lap: 3 Nov, "sector one relative to Lewis")."""
        hist, p = self.engine.history, self._player()
        theirs = {r.lap: r for r in hist.for_car(car) if r.clean}
        me = next((r for r in reversed(hist.for_car(p) if p is not None else [])
                   if r.clean and r.lap in theirs), None)
        if me is None:
            return [], ""
        return self._diff(me.sector_ms, theirs[me.lap].sector_ms), self._name(car) or "them"

    def _sector_deltas_best(self) -> tuple[list[tuple[int, int]], str]:
        hist, p = self.engine.history, self._player()
        mine = hist.for_car(p) if p is not None else []
        if len(mine) < 2:
            return [], ""
        me = mine[-1]
        others = [r for r in mine[:-1] if r.clean]
        best = tuple(min((r.sector_ms[k] for r in others if r.sector_ms[k]), default=None)
                     for k in range(3))
        return self._diff(me.sector_ms, best), "best"

    def _a_last_corners(self) -> str:
        """"How was that last couple turns?": the last two corners of your last lap."""
        corners = self.engine.corners
        if not corners.last:
            return phrases._sentence("no corner numbers for that lap yet")
        ds = sorted(corners.last, key=lambda d: d.corner.number)[-2:]
        bits = []
        for d in ds:
            gap = phrases.say_gap(int(round(d.delta_s * 1000)))
            ref = phrases.against_words(corners.last_against or "best")
            if abs(d.delta_s) < 0.05:
                bits.append(f"{d.corner.name} matching {ref}")
            elif d.delta_s > 0:
                bits.append(f"{d.corner.name} {gap} slower than {ref}")
            else:
                bits.append(f"{d.corner.name} {gap} quicker than {ref}")
        return phrases._sentence(*bits)

    def _a_clarify(self) -> str:
        """"You say off or up?": plainly, whether the last call meant quicker or slower."""
        last = (self.last_radio or "").lower()
        if re.search(r"\b(up on|gaining|quicker|faster|better|found|sorted)\b", last):
            return phrases._sentence("up", "you were quicker there")
        if re.search(r"\b(off|down|losing|slower|giving|lost)\b", last):
            return phrases._sentence("off", "you were slower there")
        return phrases._sentence("say again")

    def _say_followup(self, intent: str, text: str) -> None:
        """A strategy answer that came a moment after the question: said, and logged (it
        travels with the recording's upload)."""
        self._say(intent, text)
        self.log.write({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "intent": intent,
                        "session_uid": f"{self.state.session_uid or 0:016x}",
                        "session_time": round(self.state.session_time or 0.0, 3),
                        "answer": text, "lap": self._lap(), "followup": True})

    def _a_best_lap(self) -> str:
        p = self._player()
        laps = [r for r in self.engine.history.for_car(p) if not r.invalid] if p is not None else []
        if not laps:
            return phrases._sentence("no valid lap yet")
        best = min(laps, key=lambda r: r.lap_time_ms)
        return phrases.best_lap_phrase(best.lap_time_ms, best.lap)

    def _a_weather(self) -> str:
        """Now, and (12 Nov) what the forecast says is coming: rain, or rain easing."""
        from engineer.tools.rivals import minutes_to_lap, next_weather_change
        s = self.state.session
        now = phrases.weather_phrase(s.weather, s.track_temp_c, s.air_temp_c)
        if not s.forecast:
            return now
        w = self.engine.weather.cfg if self.engine.weather is not None else None
        change = next_weather_change(self.state, w.rain_pct if w else 40,
                                     w.horizon_min if w else 30)
        lap = minutes_to_lap(self.state, change.minutes) if change and s.is_race else None
        return phrases.weather_forecast_answer(now, change, lap)

    def _rival_car(self) -> int | None:
        """The car a rival question is about: a named driver, else "ahead" / "behind"."""
        from engineer.tools.rivals import car_at
        car = self._named_driver()
        if car is not None:
            return car
        c, p = self.state.cars, self._player()
        if p is None or np.isnan(c.position[p]):
            return None
        low = self.heard.lower()
        if re.search(r"\b(behind|back)\b", low):
            return car_at(self.state, c.position[p] + 1)
        if re.search(r"\b(ahead|in front)\b", low):
            return car_at(self.state, c.position[p] - 1)
        return None

    def _a_rival(self) -> str:
        """"How's Norris looking?": position, gap, tyres, stops, pace vs you, will they stop."""
        from engineer.tools.rivals import rival_report
        car = self._rival_car()
        if car is None:
            return phrases._sentence("which car")
        self._last_driver = car
        r = rival_report(self.state, self.engine.history, car)
        return phrases.rival_answer_phrase(self._name(car), r, self.engine.tyres.cfg.limit_pct)

    def _a_penalties(self) -> str:
        """"Any penalties?": yours and your track-limit warnings, then the cars near you (or the
        one named)."""
        from engineer.tools.rivals import penalised_near
        c, p = self.state.cars, self._player()
        if p is None:
            return phrases._sentence("no timing yet")
        named = self._named_driver()
        if named is not None:
            sec = 0 if np.isnan(c.penalties_s[named]) else int(c.penalties_s[named])
            who = self._name(named) or "they"
            return phrases._sentence(f"{who} has a {phrases.number_words(sec)} second penalty"
                                     if sec else f"{who} has no penalty")
        rc = self.engine.racecraft
        warn = rc.track_warnings if rc is not None else 0
        if not np.isnan(c.corner_cutting_warnings[p]):
            warn = max(warn, int(c.corner_cutting_warnings[p]))
        mine = 0 if np.isnan(c.penalties_s[p]) else int(c.penalties_s[p])
        others = [(self._name(car) or "a car", "ahead" if c.position[car] < c.position[p]
                   else "behind", sec) for car, sec in penalised_near(self.state, p, 3)]
        return phrases.penalties_answer_phrase(warn, mine, others)

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

    # -- 9 Nov additions ----------------------------------------------------------------------

    def _gap_between(self, a: int, b: int) -> float | None:
        """Seconds from car a back to car b (positive: b is behind), from the intervals."""
        c = self.state.cars
        pa, pb = c.position[a], c.position[b]
        if np.isnan(pa) or np.isnan(pb):
            return None
        sign, lo, hi = (1, pa, pb) if pa < pb else (-1, pb, pa)
        total = 0.0
        for pos in range(int(lo) + 1, int(hi) + 1):
            car = np.flatnonzero(c.active & (c.position == pos))
            if not car.size or np.isnan(c.delta_to_car_in_front_ms[car[0]]):
                return None
            total += c.delta_to_car_in_front_ms[car[0]]
        return sign * total / 1000.0

    def _a_teammate(self) -> str:
        """"Where's my teammate?": their position, the gap and their tyres."""
        c, p = self.state.cars, self._player()
        if p is None or np.isnan(c.team_id[p]):
            return phrases._sentence("no timing yet")
        mates = [i for i in np.flatnonzero(c.active & (c.team_id == c.team_id[p])) if i != p]
        if not mates:
            return phrases._sentence("I don't see your teammate in this race")
        mate = int(mates[0])
        self._last_driver = mate
        name = self._name(mate) or "your teammate"
        pos = c.position[mate]
        if np.isnan(pos):
            return phrases._sentence(f"no timing for {name} yet")
        gap = self._gap_between(p, mate)
        where = phrases.say_position(int(pos))
        if gap is not None:
            side = "behind" if gap > 0 else "ahead"
            where += f", {phrases.say_gap(int(abs(gap) * 1000))} {side}"
        comp = c.visual_compound[mate]
        tyre = f", on {phrases.say_compound(int(comp))}" if not np.isnan(comp) else ""
        return phrases._sentence(f"{name} is {where}{tyre}")

    def _a_hype(self) -> str:
        """"A little hype in the chat, Gary?" (9 Nov): a lift, built on how the race is going."""
        c, p = self.state.cars, self._player()
        pos = None if p is None or np.isnan(c.position[p]) else int(c.position[p])
        grid = None if p is None or np.isnan(c.grid_position[p]) else int(c.grid_position[p]) + 1
        return phrases.hype_phrase(pos, grid, self._laps_to_go())

    def _a_pit_stop_time(self) -> str:
        """"How long did the pit stop take?" (9 Nov): the game's pit lane and stationary times
        for your last stop, and the other cars' typical stationary time this race."""
        p = self._player()
        meter = getattr(self.engine.strategy, "pit_meter", None)
        if p is None or meter is None:
            return phrases._sentence("no stop timed yet")
        mine = [st for st in meter.stops if st.car_idx == p]
        if not mine:
            return phrases._sentence("you haven't stopped yet")
        st = mine[-1]
        others = [o.stop_s for o in meter.stops if o.car_idx != p and o.stop_s > 0 and not o.sc]
        typical = float(np.median(others)) if len(others) >= 3 else None
        return phrases.pit_stop_time_phrase(st.lane_s, st.stop_s, typical)

    def _a_laps_left(self) -> str:
        n = self._laps_to_go()
        return phrases.laps_left_phrase(n) if n else phrases._sentence("no timing yet")


_COMPOUNDS_SAID = {"soft": 16, "softs": 16, "medium": 17, "mediums": 17, "hard": 18, "hards": 18}


def _compound_in(text: str) -> int | None:
    """'what happens if I pit for hards' -> 18 (visual compound)."""
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in _COMPOUNDS_SAID:
            return _COMPOUNDS_SAID[w]
    return None


def _percent_in(text: str) -> float | None:
    """'will I get over 45%' -> 45 (a number as digits, as speech recognition writes it)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)?", text)
    return float(m.group(1)) if m else None


_SHIFT_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def _shift_in(text: str) -> int:
    """'a lap later' -> +1, 'two laps earlier' -> -2 (default one lap)."""
    t = text.lower()
    m = re.search(r"\b(a|one|two|three|four|\d)\s+laps?\s+(later|longer|earlier|sooner|early|late)",
                  t)
    n = _SHIFT_WORDS.get(m.group(1), 1) if m else 1
    later = bool(re.search(r"\b(later|longer|late|extend|stay out)\b", t))
    return n if later else -n
