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
from engineer.voice.intents import match

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
            return phrases._sentence("no tyre numbers yet, give me a lap")
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

    def _a_losing_time(self) -> str:
        corners = self.engine.corners
        losses = [d for d in corners.last if d.delta_s > 0]
        if not losses:
            return phrases._sentence("nothing to compare yet, give me a clean lap")
        d = max(losses, key=lambda d: d.delta_s)
        return phrases.corner_phrase(d.delta_s, d.corner.name, corners.last_against or "best",
                                     d.reason(), "chatty")

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
