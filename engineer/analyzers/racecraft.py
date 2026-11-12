"""Race craft calls: the human side of the radio. Battles, passes, milestones, penalties.

Races only. Each call is said once per situation, with its own rules so it isn't chatter:

- Start: after race lap 1, places gained or lost from the grid ("Great start, up eight places").
- Passes: the game reports every position swap (472 in the 18-lap Austria race, six between you
  and one car in 12 s of lap 1), so a pass is only called once it has stuck for `pass_hold_s`
  (4 s), from lap 2, not under the safety car or when the other car is in the pits. Several
  in one go are said together. A lost place is said the same way, with encouragement, but
  waits `lost_hold_s` (8 s): you often fight back at the next corner. Taking a place back
  within `fight_window_s` (20 s) is praised as a fight ("Good fight, Hamilton couldn't make it
  stick"), not called as a lost place followed by a pass (Shanghai, 3 Nov). Places lost
  around your pit stop (pit entry to the end of the out lap) aren't called, and a lost place
  re-sent after a flashback isn't said twice.
- Defending: the car behind is inside `defend_gap_s` (1 s) and closer than on the previous lap.
  Again only after it drops back past `clear_gap_s` (2 s), which gets "Good defending".
- Attacking: the car ahead is within `attack_gap_s` (4 s), you've closed at least
  `min_closing_s` (0.15 s) a lap over the last two laps, and at that rate you'd catch it before
  the flag ("realistic"). Then "DRS range on Norris" the first time you get within 1 s.
- Long battles: the same car within `battle_gap_s` (2 s, 12 Nov; was 1) ahead or behind at the line for
  `battle_laps` (3) laps running gets encouragement ("Russell has been on you for four laps and
  still hasn't got by. Brilliant work, stay calm."), again every `battle_repeat_laps` (3) while
  it lasts, at most BATTLE_MAX_CALLS per car; "back and forth" wording once you've swapped places
  twice. Not on a lap that already had a defend/attack/DRS/pass call about it.
- Battle updates (12 Nov): every lap a car is within `battle_gap_s` (plus `chatty_extra_gap_s` in
  chatty), the gap and which way it went: "Antonelli behind, eight tenths, you're holding him.
  Great job!" (topic racecraft_gap, LOW). Not on a lap with another race craft call.
- Stops shaken out (12 Nov): once every running car has stopped as often as you, "Stops all
  done, P six, and that's real. Up two on the cycle!" (topic racecraft_cycle).
- Your time penalty (12 Nov): with the penalty call, and every lap after, the gap you need to the
  car behind ("Antonelli one second behind, you need three seconds on him. Push!"), once "clear"
  when nobody is within it (topic racecraft_penalty_gap).
- Chatty (12 Nov): a car within two places going into the pits (topic racecraft_rival_pit).
- Push (12 Nov): closing in on a battle, the car ahead or behind inside `push_gap_s` (3 s) but
  outside DRS range (`drs_gap_s`), the gap down `push_closing_s` (0.2 s) on the last lap:
  "Push now! Norris is one point four ahead and you're catching. Big lap, get into DRS." /
  "Push now, Russell is one point six back and coming. Keep them out of DRS." Once per car per
  PUSH_REPEAT_LAPS, not right after another race craft call, not in Quiet.
- Countdown: five laps to go (with the gap ahead) and the last lap.
- Finish: the flag, your position and places gained from the grid.
- Fastest lap of the race: when you take it from someone else (not every time you improve it).
- Penalties and warnings for you (from the game's PENA event).
- Safety car / VSC coming in ("get the tyres ready").
- Damage after contact: wing, floor, diffuser or sidepod damage up 10 % or more.

Not in quiet mode (only box calls, safety car box calls and answers come through then).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from engineer.bus import events as ev
from engineer.config import RaceCraft
from engineer.ingest.formats import f1_2025 as f
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.state.race_state import RaceState

log = logging.getLogger(__name__)

DAMAGE_PARTS = {"front_left_wing": "front left wing", "front_right_wing": "front right wing",
                "rear_wing": "rear wing", "floor": "floor", "diffuser": "diffuser",
                "sidepod": "sidepod"}
DAMAGE_STEP_PCT = 10
CONTACT_CHECK_S = 2.0
COUNTDOWN_LAPS = 5
ATTACK_REPEAT_LAPS = 4
SC_IN_REPEAT_S = 30.0
PASS_GROUP_S = 8.0
TRACK_LIMITS = {7, 27, 28, 29}   # infringement IDs (spec): corner cutting, track limits
BATTLE_MAX_CALLS = 4         # encouragement per car per race
PUSH_REPEAT_LAPS = 4         # "push now" again for the same car after this many laps
BATTLE_QUIET_S = 15.0        # not right after another race craft call
RETIRED = {4, 5, 6, 7}       # result status (spec): DNF, disqualified, not classified, retired
PENALTY_CLOSE_S = 1.0        # a car just outside your penalty (within this more) is still named
RIVAL_PIT_PLACES = 2         # chatty: cars this many places ahead/behind pitting


LOST_REPEAT_S = 60.0         # the same lost place isn't said again this soon (flashbacks)
EXCHANGE_S = 5.0             # a car re-passing you this soon after YOUR move is your overtake
                             # still going on (Austria lap 2, Piastri), not a fight you defended


@dataclass
class _Swap:
    other: int
    gained: bool
    due: float                  # session time to check it stuck
    at: float = 0.0             # session time of the swap


class RaceCraftAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, director: RadioDirector,
                 settings: RaceCraft | None = None):
        self.state = state
        self.director = director
        self.cfg = settings or RaceCraft()
        self._reset()
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.GameEvent, self.on_game_event)
        bus.subscribe(ev.SectorCompleted, self._on_sector)
        bus.subscribe(ev.SessionStarted, lambda e: self._reset())
        bus.subscribe(ev.Flashback, lambda e: self._on_flashback())
        bus.subscribe(ev.PitEntry, self._on_pit_entry)
        bus.subscribe(ev.RadioMessage, self._on_radio)
        bus.subscribe(ev.RadioDropped, self._on_dropped)

    def _reset(self) -> None:
        self.swaps: list[_Swap] = []
        self.ahead_gaps: list[tuple[int, int, float]] = []   # (lap, car ahead, gap ms)
        self.behind_gaps: dict[int, float] = {}              # car behind -> gap at its last lap
        self.defending: int | None = None                    # car we warned about
        self.attack_said: dict[int, int] = {}                # car -> lap "catching" was said
        self.drs_said: set[int] = set()
        self.fastest_holder: int | None = None
        self.damage_said: dict[str, float] = {}
        self.damage_lap: int | None = None     # your lap when damage was first called
        self.contact_due: float | None = None
        self.finished = False
        self.sc_in_said = -math.inf
        self.last_pass_call = -math.inf
        self.passes_said = 0
        self.passed_by: dict[int, float] = {}               # car -> when it last passed you
        self.passed: dict[int, float] = {}                  # car -> when you last passed it
        self.lost_said: dict[int, float] = {}               # car -> when "lost to" was said
        self.lost_pending: dict[str, list[int]] = {}        # lost call text -> its cars
        self.lost_calls = 0                                  # wording rotation
        self.fights_said = 0
        self.pit_laps = 0                                    # in lap + out lap: no lost calls
        self.track_warnings = 0                              # your track-limit warnings
        self.push_said: dict[tuple[int, str], int] = {}      # (car, ahead/behind) -> your lap
        self.battles: dict[int, tuple[int, int]] = {}        # car -> (first lap, last lap) close
        self.battle_swaps: dict[int, list[float]] = {}       # car -> when places swapped
        self.battle_said: dict[int, int] = {}                # car -> lap encouragement was said
        self.battle_calls: dict[int, int] = {}               # car -> how many times
        # Battle calls not on the radio yet: text -> (car, said lap and count before). A
        # flashback or an expired wait puts them back (Brazil, 7 Nov: two lost to flashbacks).
        self.battle_pending: dict[str, tuple[int, int | None, int]] = {}
        self.gap_seen: dict[int, tuple[str, float, int]] = {}   # car -> (side, gap ms, your lap)
        self.battle_said_lap: int | None = None              # your lap ended by a battle call
        self.cycle_said: set[int] = set()                    # your stop counts already called
        self.last_position: int | None = None                # at your last line crossing
        self.position_before_stop: int | None = None
        self.penalty_clear_said = False
        self.penalty_said_at = -math.inf                     # the penalty call with its gap

    def _on_flashback(self) -> None:
        self.swaps.clear()
        self.contact_due = None
        for text in list(self.battle_pending):           # the director dropped them unsaid
            self._battle_unsaid(text)

    def _battle_unsaid(self, text: str) -> None:
        car, said, calls = self.battle_pending.pop(text)
        if said is None:
            self.battle_said.pop(car, None)
        else:
            self.battle_said[car] = said
        self.battle_calls[car] = calls

    def _on_dropped(self, e: ev.RadioDropped) -> None:
        if e.topic == "racecraft_battle" and e.text in self.battle_pending:
            self._battle_unsaid(e.text)

    def _on_radio(self, e: ev.RadioMessage) -> None:
        # A lost place counts as said once it's actually on the radio: a flashback can drop
        # the call unsaid, and the game then sends the pass again (Melbourne, 2 Nov, last lap).
        for car in self.lost_pending.pop(e.text, []) if e.topic == "racecraft_pass" else []:
            self.lost_said[car] = e.session_time
        if e.topic == "racecraft_battle":
            self.battle_pending.pop(e.text, None)

    def _on_pit_entry(self, e: ev.PitEntry) -> None:
        if e.car_idx != self.state.player_index:
            self._rival_pit(e.car_idx)
            return
        if e.car_idx == self.state.player_index:
            self.position_before_stop = self.last_position
            # Cars going by while you stop aren't overtakes (Melbourne, 2 Nov: ten "lost"
            # places around the stop). Covers the in lap and the out lap.
            self.pit_laps = 2
            self.swaps = [x for x in self.swaps if x.gained]

    # -- helpers ---------------------------------------------------------------------------------

    def _active(self) -> bool:
        s = self.state
        return self.cfg.enabled and s.session.is_race and s.player_index is not None

    def _name(self, car: int | None) -> str | None:
        if car is None or car >= len(self.state.cars.names):
            return None
        n = self.state.cars.names[car]
        return n.title() if n else None

    def _car_at(self, position: float) -> int | None:
        c = self.state.cars
        hits = np.flatnonzero(c.active & (c.position == position))
        return int(hits[0]) if hits.size else None

    def _under_sc(self) -> bool:
        return self.state.session.safety_car_status in (1, 2)

    def _say(self, topic: str, text: str | None, priority: int = Priority.NORMAL,
             max_wait_s: float = 10.0, wait_for_straight: bool = True,
             must_say: bool = False, excited: bool | None = None) -> None:
        if text:
            self.director.submit(RadioCall(topic=topic, text=text, priority=priority,
                                           max_wait_s=max_wait_s,
                                           wait_for_straight=wait_for_straight,
                                           must_say=must_say, excited=excited))

    # -- laps ------------------------------------------------------------------------------------

    def on_lap(self, e: ev.LapCompleted) -> None:
        if not self._active():
            return
        s, c, me = self.state, self.state.cars, self.state.player_index
        if e.car_idx != me:
            self._behind_lap(e)
            return
        if self.pit_laps:
            self.pit_laps -= 1
        total = s.session.total_laps or 0
        if e.lap == 1 and self.cfg.start:
            grid = c.grid_position[me]
            if not np.isnan(grid) and e.position is not None:
                gained = int(grid) + 1 - e.position      # grid position is zero-based
                if gained != 0:
                    self._say("racecraft_start", phrases.start_phrase(gained))
        if e.position is not None and not e.pit_in_lap:
            self.last_position = e.position
        if total and e.lap >= total:
            self._finish(e)
            return
        self._cycle(e)
        if self._under_sc() or e.safety_car:
            self.ahead_gaps.clear()                       # gaps behind the SC mean nothing
            self.gap_seen.clear()
            return
        self._attack(e)
        if self._battle(e):
            self.battle_said_lap = e.lap
        self._penalty_lap()
        if self.cfg.countdown and total:
            to_go = total - e.lap
            if to_go in (COUNTDOWN_LAPS, 1):
                ahead = self._car_at(c.position[me] - 1)
                gap = c.delta_to_car_in_front_ms[me]
                text = phrases.laps_to_go_phrase(to_go, int(c.position[me]), self._name(ahead),
                                                 None if np.isnan(gap) else float(gap))
                text += self._penalty_reminder(me)
                self._say("racecraft_countdown", text,
                    Priority.NORMAL if to_go == 1 else Priority.LOW, max_wait_s=30.0,
                    must_say=to_go == 1)        # Jeddah, 9 Nov: "Last lap" was dropped

    def _close_cars(self, limit_ms: float) -> dict[int, tuple[str, float]]:
        """The cars just ahead and just behind within limit_ms: car -> (side, gap ms)."""
        c, me = self.state.cars, self.state.player_index
        near: dict[int, tuple[str, float]] = {}
        ahead = self._car_at(c.position[me] - 1)
        gap = c.delta_to_car_in_front_ms[me]
        if ahead is not None and not np.isnan(gap) and 0 < gap <= limit_ms \
                and not c.pit_status[ahead] > 0:
            near[ahead] = ("ahead", float(gap))
        behind = self._car_at(c.position[me] + 1)
        if behind is not None and not c.pit_status[behind] > 0:
            g = c.delta_to_car_in_front_ms[behind]
            if not np.isnan(g) and 0 < g <= limit_ms:
                near[behind] = ("behind", float(g))
        return near

    def _racecraft_busy(self, t: float) -> bool:
        """Another race craft call said in the last BATTLE_QUIET_S, or one waiting to be said."""
        recent = max((v for k, v in self.director.last_said.items()
                      if k.startswith("racecraft_") and k != "racecraft_countdown"),
                     default=-math.inf)
        return t - recent < BATTLE_QUIET_S or any(
            q.topic.startswith("racecraft_") and q.topic != "racecraft_countdown"
            for q in self.director.queue)

    def _battle(self, e: ev.LapCompleted) -> bool:
        """Encouragement in a battle that goes on for laps (see the module notes). True if a
        call was made."""
        if not self.cfg.battles:
            return False
        if e.pit_in_lap or e.pit_out_lap or self.pit_laps or e.lap < 2:
            self.battles.clear()
            return False
        near = {car: side for car, (side, _) in
                self._close_cars(self.cfg.battle_gap_s * 1000).items()}
        # A streak continues from the previous lap (or the same lap re-driven after a flashback).
        streaks = {}
        for car in near:
            first, last = self.battles.get(car, (e.lap, e.lap))
            streaks[car] = (first if last >= e.lap - 1 else e.lap, e.lap)
        self.battles = streaks
        due = []
        for car, (first, _) in streaks.items():
            laps = e.lap - first + 1
            said = self.battle_said.get(car)
            if laps < self.cfg.battle_laps or self.battle_calls.get(car, 0) >= BATTLE_MAX_CALLS:
                continue
            if said is not None and e.lap - said < self.cfg.battle_repeat_laps:
                continue
            due.append((laps, near[car] == "ahead", car))
        if not due:
            return False
        t = self.state.session_time or 0.0
        recent = max((self.director.last_said.get(k, -math.inf) for k in
                      ("racecraft_defend", "racecraft_attack", "racecraft_pass")),
                     default=-math.inf)
        if t - recent < BATTLE_QUIET_S:
            return False                                     # next lap, if it's still on
        laps, _, car = max(due)
        before = (car, self.battle_said.get(car), self.battle_calls.get(car, 0))
        self.battle_said[car] = e.lap
        self.battle_calls[car] = self.battle_calls.get(car, 0) + 1
        # Swaps in this battle only: since about a lap before it started.
        since = t - (laps + 1) * e.lap_time_ms / 1000.0
        swaps = sum(x >= since for x in self.battle_swaps.get(car, []))
        text = phrases.battle_phrase(self._name(car), near[car], laps, swaps)
        self.battle_pending[text] = before
        self._say("racecraft_battle", text, Priority.LOW, max_wait_s=15.0)
        return True

    def _on_sector(self, e: ev.SectorCompleted) -> None:
        if e.car_idx == self.state.player_index and e.sector == 1 and self._active():
            self._gap_update(e.lap)

    def _gap_update(self, lap: int) -> None:
        """In a battle, every lap at the end of sector one: the gap and which way it went since
        the last one (12 Nov). Not at the line, where the lap time and the other lap calls
        queue (the first try there lost most of them to the wait)."""
        cfg, c, me = self.cfg, self.state.cars, self.state.player_index
        if not (cfg.battles and cfg.battle_updates) or self.pit_laps or c.pit_status[me] > 0 \
                or lap < 3 or self._under_sc():
            self.gap_seen.clear()
            return
        extra = cfg.chatty_extra_gap_s if self.director.tempo == "chatty" else 0.0
        close = self._close_cars((cfg.battle_gap_s + extra) * 1000)
        prev, self.gap_seen = self.gap_seen, {car: (side, gap, lap)
                                              for car, (side, gap) in close.items()}
        if not close or self.battle_said_lap == lap - 1 \
                or self._racecraft_busy(self.state.session_time or 0.0):
            return
        car, (side, gap) = min(close.items(), key=lambda kv: kv[1][1])
        p = prev.get(car)
        change = gap - p[1] if p is not None and p[0] == side and p[2] == lap - 1 else None
        self._say("racecraft_gap", phrases.battle_gap_phrase(self._name(car), side, gap, change),
                  Priority.LOW, max_wait_s=20.0)

    def _stops(self, car: int) -> int:
        n = self.state.cars.num_pit_stops[car]
        return 0 if np.isnan(n) else int(n)

    def _cycle(self, e: ev.LapCompleted) -> None:
        """Once every running car has stopped as often as you: where that leaves you (12 Nov;
        Miami: 18 cars stopped on laps 7-8 and you went P19 -> P6 without a word)."""
        s, c, me = self.state, self.state.cars, self.state.player_index
        mine = self._stops(me)
        if not self.cfg.cycle or mine < 1 or mine in self.cycle_said or e.pit_in_lap \
                or c.pit_status[me] > 0 or (s.session.weather or 0) >= 3 or e.position is None:
            return
        others = [int(j) for j in np.flatnonzero(c.active) if j != me
                  and c.result_status[j] not in RETIRED]
        if any(c.pit_status[j] > 0 or self._stops(j) < mine for j in others):
            return
        self.cycle_said.add(mine)
        before = self.position_before_stop
        gained = before - e.position if before is not None else None
        self._say("racecraft_cycle", phrases.cycle_phrase(e.position, gained), Priority.NORMAL,
                  max_wait_s=20.0, excited=gained is not None and gained > 0)

    def _penalty_cars(self, seconds: float) -> list[tuple[int, float]]:
        """The cars behind within your penalty (plus PENALTY_CLOSE_S): (car, gap ms), nearest
        first."""
        c, me = self.state.cars, self.state.player_index
        out, total, pos = [], 0.0, c.position[me]
        limit = (seconds + PENALTY_CLOSE_S) * 1000
        while not np.isnan(pos):
            pos += 1
            car = self._car_at(pos)
            if car is None or np.isnan(c.delta_to_car_in_front_ms[car]):
                break
            total += float(c.delta_to_car_in_front_ms[car])
            if total > limit:
                break
            if not c.pit_status[car] > 0:
                out.append((car, total))
        return out

    def _penalty_gap_text(self, seconds: float) -> str | None:
        cars = self._penalty_cars(seconds)
        inside = [x for x in cars if x[1] < seconds * 1000]
        if not cars:
            if self.penalty_clear_said:
                return None
            self.penalty_clear_said = True
            return phrases.penalty_clear_phrase(int(seconds))
        self.penalty_clear_said = False
        car, gap = cars[0]
        return phrases.penalty_gap_phrase(self._name(car), gap, int(seconds), len(inside))

    def _penalty_lap(self) -> None:
        """Your time penalty, every lap until it's served: the gap you need (12 Nov; Miami:
        three seconds on lap 13 with Antonelli one second behind, and nothing said about it)."""
        c, me = self.state.cars, self.state.player_index
        sec = c.penalties_s[me]
        if not (self.cfg.penalties and self.cfg.penalty_gap) or np.isnan(sec) or sec <= 0:
            self.penalty_clear_said = False
            return
        if (self.state.session_time or 0.0) - self.penalty_said_at < BATTLE_QUIET_S * 2:
            return                                  # just said with the penalty itself
        text = self._penalty_gap_text(float(sec))
        if text:
            self._say("racecraft_penalty_gap", text, Priority.NORMAL, max_wait_s=20.0)

    def _rival_pit(self, car: int) -> None:
        """Chatty: a car within two places going into the pits."""
        c, me = self.state.cars, self.state.player_index
        if not self._active() or not self.cfg.rival_pits or self.director.tempo != "chatty" \
                or self._under_sc() or c.pit_status[me] > 0:
            return
        lap = c.lap[me]
        if np.isnan(lap) or lap < 2 or np.isnan(c.position[car]) or np.isnan(c.position[me]):
            return
        diff = int(c.position[car] - c.position[me])
        # The car right behind pitting while you still have a stop is the strategy's undercut
        # call; not twice.
        if diff == 0 or abs(diff) > RIVAL_PIT_PLACES or (diff == 1 and self._stops(me) == 0):
            return
        self._say("racecraft_rival_pit", phrases.rival_pit_phrase(
            self._name(car), "ahead" if diff < 0 else "behind", abs(diff),
            self._stops(car) + 1), Priority.LOW, max_wait_s=15.0)

    def _penalty_reminder(self, me: int) -> str:
        """For the countdown: a time penalty on the car just ahead or behind (12 Nov)."""
        from engineer.tools.rivals import car_at, gap_between
        c = self.state.cars
        for side, off in (("ahead", -1), ("behind", 1)):
            car = car_at(self.state, c.position[me] + off)
            if car is not None and not np.isnan(c.penalties_s[car]) and c.penalties_s[car] > 0:
                return " " + phrases.penalty_reminder_phrase(
                    self._name(car), side, int(c.penalties_s[car]),
                    gap_between(self.state, me, car))
        return ""

    def _finish(self, e: ev.LapCompleted) -> None:
        if self.finished or not self.cfg.finish or e.position is None:
            return
        self.finished = True
        grid = self.state.cars.grid_position[self.state.player_index]
        gained = None if np.isnan(grid) else int(grid) + 1 - e.position
        self._say("racecraft_finish", phrases.finish_phrase(e.position, gained), Priority.HIGH,
                  max_wait_s=20.0, wait_for_straight=False)

    def _attack(self, e: ev.LapCompleted) -> None:
        if not self.cfg.battles:
            return
        s, c, me = self.state, self.state.cars, self.state.player_index
        ahead = self._car_at(c.position[me] - 1)
        gap = c.delta_to_car_in_front_ms[me]
        if ahead is None or np.isnan(gap) or e.pit_in_lap or e.pit_out_lap:
            self.ahead_gaps.clear()
            return
        # A lap re-driven after a flashback replaces its earlier gap (Brazil, 7 Nov: one lap
        # three times made the closing rate divide by zero laps).
        kept = [x for x in self.ahead_gaps if x[1] == ahead and x[0] < e.lap]
        prev_gap = kept[-1][2] if kept and kept[-1][0] == e.lap - 1 else None
        self.ahead_gaps = kept[-2:] + [(e.lap, ahead, gap)]
        self._push(ahead, "ahead", gap, prev_gap, e.lap)
        name = self._name(ahead)
        if gap <= 1000:
            if ahead not in self.drs_said and len(self.ahead_gaps) >= 2:
                self.drs_said.add(ahead)
                self._say("racecraft_attack", phrases.drs_range_phrase(name))
            return
        if len(self.ahead_gaps) < 3 or gap > self.cfg.attack_gap_s * 1000:
            return
        first_lap, _, first_gap = self.ahead_gaps[0]
        closing = (first_gap - gap) / (e.lap - first_lap)      # ms per lap, over two laps
        if closing < self.cfg.min_closing_s * 1000:
            return
        # Closing on both of the last two laps, not one big jump (a mistake ahead, a pit stop).
        if not all(b[2] < a[2] for a, b in zip(self.ahead_gaps, self.ahead_gaps[1:])):
            return
        laps_left = (s.session.total_laps or 0) - e.lap
        laps_to_catch = math.ceil((gap - 1000) / closing)       # to DRS range
        if laps_to_catch > laps_left:
            return                                              # not realistic: stay quiet
        if e.lap - self.attack_said.get(ahead, -99) < ATTACK_REPEAT_LAPS:
            return
        self.attack_said[ahead] = e.lap
        self._say("racecraft_attack", phrases.attack_phrase(name, gap, closing, laps_to_catch),
                  Priority.LOW, max_wait_s=20.0)

    def _behind_lap(self, e: ev.LapCompleted) -> None:
        """The car right behind finished a lap: its gap to you, and whether it's closing."""
        if not self.cfg.battles or self._under_sc() or e.safety_car:
            self.behind_gaps.pop(e.car_idx, None)
            return
        c, me = self.state.cars, self.state.player_index
        if c.position[e.car_idx] != c.position[me] + 1 or c.pit_status[me] > 0:
            self.behind_gaps.pop(e.car_idx, None)
            if self.defending == e.car_idx:
                self.defending = None
            return
        gap = float(c.delta_to_car_in_front_ms[e.car_idx])
        if np.isnan(gap):
            return
        prev = self.behind_gaps.get(e.car_idx)
        self.behind_gaps[e.car_idx] = gap
        name = self._name(e.car_idx)
        if self.defending == e.car_idx:
            if gap > self.cfg.clear_gap_s * 1000:
                self.defending = None
                self._say("racecraft_defend", phrases.breathing_room_phrase(name, gap),
                          Priority.LOW, max_wait_s=15.0)
            return
        mine = self.state.cars.lap[me]
        if not np.isnan(mine):
            self._push(e.car_idx, "behind", gap, prev, int(mine) - 1)
        if prev is not None and gap <= self.cfg.defend_gap_s * 1000 and gap < prev - 50 \
                and e.lap >= 2:
            self.defending = e.car_idx
            self._say("racecraft_defend", phrases.defend_phrase(name, gap))

    def _push(self, car: int, side: str, gap: float, prev: float | None, lap: int) -> None:
        """"Push now" closing in on a battle (see the module notes)."""
        cfg = self.cfg
        if not cfg.push or prev is None or lap < 2 or self.pit_laps or self._under_sc():
            return
        if not (cfg.drs_gap_s * 1000 < gap <= cfg.push_gap_s * 1000):
            return                          # too far, or already in DRS (defend/DRS calls)
        if prev - gap < cfg.push_closing_s * 1000 or self.state.cars.pit_status[car] > 0:
            return
        last = self.push_said.get((car, side))
        if last is not None and lap - last < PUSH_REPEAT_LAPS:
            return
        t = self.state.session_time or 0.0
        recent = max(self.director.last_said.get(k, -math.inf) for k in
                     ("racecraft_defend", "racecraft_attack", "racecraft_pass", "racecraft_push"))
        if t - recent < BATTLE_QUIET_S:
            return
        self.push_said[(car, side)] = lap
        name = self._name(car)
        text = phrases.push_attack_phrase(name, gap) if side == "ahead" \
            else phrases.push_defend_phrase(name, gap)
        self._say("racecraft_push", text, Priority.NORMAL, max_wait_s=12.0)

    # -- game events -----------------------------------------------------------------------------

    def on_game_event(self, e: ev.GameEvent) -> None:
        if not self._active():
            return
        me, c, d = self.state.player_index, self.state.cars, e.details
        lap = c.lap[me]
        if e.code == "OVTK" and self.cfg.passes:
            a, b = d["overtakingVehicleIdx"], d["beingOvertakenVehicleIdx"]
            if me not in (a, b) or np.isnan(lap) or lap < 2 or self._under_sc():
                return
            other = b if a == me else a
            if c.pit_status[other] > 0 or c.pit_status[me] > 0:
                return
            self.battle_swaps.setdefault(other, []).append(e.session_time)
            gained = a == me
            if gained:
                self.passed[other] = e.session_time
            else:
                if self.pit_laps:
                    return
                if e.session_time - self.passed.get(other, -math.inf) > EXCHANGE_S:
                    self.passed_by[other] = e.session_time
            hold = self.cfg.pass_hold_s if gained else self.cfg.lost_hold_s
            self.swaps = [x for x in self.swaps if x.other != other]
            self.swaps.append(_Swap(other, gained, e.session_time + hold, e.session_time))
        elif e.code == "FTLP" and self.cfg.fastest_lap:
            holder, before = d["vehicleIdx"], self.fastest_holder
            self.fastest_holder = holder
            if holder == me and before is not None and before != me and not np.isnan(lap) \
                    and lap >= 3:
                self._say("racecraft_fastest", phrases.fastest_lap_phrase(d["lapTime"]),
                          Priority.LOW, max_wait_s=15.0)
        elif e.code == "PENA" and self.cfg.penalties and d["vehicleIdx"] == me:
            if d["penaltyType"] == 5 and d["infringementType"] in TRACK_LIMITS:
                # Counted (12 Nov, from the real radio: "track limit strike, turn two").
                self.track_warnings += 1
                text = phrases.track_limits_warning_phrase(self.track_warnings)
            else:
                text = phrases.penalty_phrase(d["penaltyType"], d["infringementType"], d["time"])
                # A time penalty: what it means, at once (the per-lap update follows).
                if text and d["penaltyType"] == 4 and self.cfg.penalty_gap \
                        and 0 < d["time"] < 255:
                    self.penalty_clear_said = False
                    self.penalty_said_at = e.session_time
                    more = self._penalty_gap_text(float(d["time"]))
                    text += " " + more if more else ""
            self._say("racecraft_penalty", text, max_wait_s=15.0)
        elif e.code == "SCAR" and self.cfg.safety_car and d["eventType"] == 1:
            # The game can send the same SCAR twice (18-lap Austria race: both at 321.6 s).
            if e.session_time - self.sc_in_said < SC_IN_REPEAT_S:
                return
            self.sc_in_said = e.session_time
            kind = "virtual safety car" if d["safetyCarType"] == 2 else "safety car"
            self._say("safety_car", phrases.safety_car_in_phrase(kind), Priority.HIGH,
                      wait_for_straight=False)
        elif e.code == "COLL" and self.cfg.damage and me in (d["vehicle1Idx"], d["vehicle2Idx"]):
            self.contact_due = e.session_time + CONTACT_CHECK_S

    def after_packet(self, packet_id: int | None) -> None:
        """Hooked into Pipeline.after_packet: settle passes once they've stuck, check damage
        a moment after contact."""
        if packet_id != f.PacketId.LAP_DATA or not self._active():
            return
        t = self.state.session_time or 0.0
        if self.swaps and t >= min(x.due for x in self.swaps):
            self._settle_swaps(t)
        if self.contact_due is not None and t >= self.contact_due:
            self.contact_due = None
            self._contact_damage()

    def _settle_swaps(self, t: float) -> None:
        """Say the passes that have stuck. Passes close together become one call: while the
        last pass call is less than PASS_GROUP_S old, they wait and are said together (lap 2 of
        the 5-lap Austria race had five position changes in 20 s)."""
        if t - self.last_pass_call < PASS_GROUP_S:
            return
        c, me = self.state.cars, self.state.player_index
        due = [x for x in self.swaps if t >= x.due]
        self.swaps = [x for x in self.swaps if t < x.due]
        if self._under_sc() or c.pit_status[me] > 0:
            return
        # Still the way round it was reported? (Swapped back = no call.) A car that has gone
        # into the pit lane wasn't passed on track: a pit stop isn't a move (Baku, 7 Nov: five
        # cars pitted as you crossed the line and Antonelli's stop became "Good move, P two").
        on_track = [x for x in due if not c.pit_status[x.other] > 0]
        gained = [x for x in on_track if x.gained and c.position[me] < c.position[x.other]]
        lost = [x for x in on_track if not x.gained and c.position[me] > c.position[x.other]]
        pos = int(c.position[me])
        # Retaken from a car that passed you moments before: that's a fight, not a pass.
        fights = [x for x in gained
                  if x.at - self.passed_by.get(x.other, -math.inf) <= self.cfg.fight_window_s]
        if fights:
            x = fights[-1]
            heard = x.at - self.lost_said.get(x.other, -math.inf) <= self.cfg.fight_window_s
            self.last_pass_call = t
            self._say("racecraft_pass", phrases.fight_back_phrase(
                self._name(x.other), pos, heard, self.fights_said), max_wait_s=8.0)
            self.fights_said += 1
            for f_ in fights:
                self.passed_by.pop(f_.other, None)
        elif len(gained) > len(lost):
            self.last_pass_call = t
            self._say("racecraft_pass", phrases.pass_phrase(
                [self._name(x.other) for x in gained], pos, self.passes_said), max_wait_s=8.0)
            self.passes_said += 1
        elif lost and not gained:
            # Already said (the game re-sends a pass after a flashback): not again.
            lost = [x for x in lost if t - self.lost_said.get(x.other, -math.inf) > LOST_REPEAT_S]
            if not lost:
                return
            self.last_pass_call = t
            names = [self._name(x.other) for x in lost]
            text = phrases.lost_place_phrase(names[0], pos, self.lost_calls) if len(lost) == 1 \
                else phrases.lost_places_phrase(len(lost), pos)
            self.lost_pending[text] = [x.other for x in lost]
            self._say("racecraft_pass", text, max_wait_s=8.0)
            self.lost_calls += 1

    def _contact_damage(self) -> None:
        dmg, me = self.state.cars.damage, self.state.player_index
        parts = []
        for key, name in DAMAGE_PARTS.items():
            v = dmg[key][me]
            if np.isnan(v):
                continue
            if v >= self.damage_said.get(key, 0.0) + DAMAGE_STEP_PCT:
                self.damage_said[key] = v - v % DAMAGE_STEP_PCT
                parts.append((name, float(v)))
        if parts:
            if self.damage_lap is None and not np.isnan(self.state.cars.lap[me]):
                self.damage_lap = int(self.state.cars.lap[me])
            self._say("racecraft_damage", phrases.contact_damage_phrase(parts))
