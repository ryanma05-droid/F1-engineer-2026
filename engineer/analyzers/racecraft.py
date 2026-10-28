"""Race craft calls: the human side of the radio. Battles, passes, milestones, penalties.

Races only. Each call is said once per situation, with its own rules so it isn't chatter:

- Start: after race lap 1, places gained or lost from the grid ("Great start, up eight places").
- Passes: the game reports every position swap (472 in the 18-lap Austria race, six between you
  and one car in 12 s of lap 1), so a pass is only called once it has stuck for `pass_hold_s`
  (4 s), from lap 2, not under the safety car or when the other car is in the pits. Several
  in one go are said together. A lost place is said the same way, with encouragement.
- Defending: the car behind is inside `defend_gap_s` (1 s) and closer than on the previous lap.
  Again only after it drops back past `clear_gap_s` (2 s), which gets "Good defending".
- Attacking: the car ahead is within `attack_gap_s` (4 s), you've closed at least
  `min_closing_s` (0.15 s) a lap over the last two laps, and at that rate you'd catch it before
  the flag ("realistic"). Then "DRS range on Norris" the first time you get within 1 s.
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


@dataclass
class _Swap:
    other: int
    gained: bool
    due: float                  # session time to check it stuck


class RaceCraftAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, director: RadioDirector,
                 settings: RaceCraft | None = None):
        self.state = state
        self.director = director
        self.cfg = settings or RaceCraft()
        self._reset()
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.GameEvent, self.on_game_event)
        bus.subscribe(ev.SessionStarted, lambda e: self._reset())
        bus.subscribe(ev.Flashback, lambda e: self._on_flashback())

    def _reset(self) -> None:
        self.swaps: list[_Swap] = []
        self.ahead_gaps: list[tuple[int, int, float]] = []   # (lap, car ahead, gap ms)
        self.behind_gaps: dict[int, float] = {}              # car behind -> gap at its last lap
        self.defending: int | None = None                    # car we warned about
        self.attack_said: dict[int, int] = {}                # car -> lap "catching" was said
        self.drs_said: set[int] = set()
        self.fastest_holder: int | None = None
        self.damage_said: dict[str, float] = {}
        self.contact_due: float | None = None
        self.finished = False
        self.sc_in_said = -math.inf
        self.last_pass_call = -math.inf
        self.passes_said = 0

    def _on_flashback(self) -> None:
        self.swaps.clear()
        self.contact_due = None

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
             max_wait_s: float = 10.0, wait_for_straight: bool = True) -> None:
        if text:
            self.director.submit(RadioCall(topic=topic, text=text, priority=priority,
                                           max_wait_s=max_wait_s,
                                           wait_for_straight=wait_for_straight))

    # -- laps ------------------------------------------------------------------------------------

    def on_lap(self, e: ev.LapCompleted) -> None:
        if not self._active():
            return
        s, c, me = self.state, self.state.cars, self.state.player_index
        if e.car_idx != me:
            self._behind_lap(e)
            return
        total = s.session.total_laps or 0
        if e.lap == 1 and self.cfg.start:
            grid = c.grid_position[me]
            if not np.isnan(grid) and e.position is not None:
                gained = int(grid) + 1 - e.position      # grid position is zero-based
                if gained != 0:
                    self._say("racecraft_start", phrases.start_phrase(gained))
        if total and e.lap >= total:
            self._finish(e)
            return
        if self._under_sc() or e.safety_car:
            self.ahead_gaps.clear()                       # gaps behind the SC mean nothing
            return
        self._attack(e)
        if self.cfg.countdown and total:
            to_go = total - e.lap
            if to_go in (COUNTDOWN_LAPS, 1):
                ahead = self._car_at(c.position[me] - 1)
                gap = c.delta_to_car_in_front_ms[me]
                self._say("racecraft_countdown", phrases.laps_to_go_phrase(
                    to_go, int(c.position[me]), self._name(ahead),
                    None if np.isnan(gap) else float(gap)),
                    Priority.NORMAL if to_go == 1 else Priority.LOW, max_wait_s=30.0)

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
        self.ahead_gaps = [x for x in self.ahead_gaps if x[1] == ahead][-2:] + [(e.lap, ahead, gap)]
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
        if prev is not None and gap <= self.cfg.defend_gap_s * 1000 and gap < prev - 50 \
                and e.lap >= 2:
            self.defending = e.car_idx
            self._say("racecraft_defend", phrases.defend_phrase(name, gap))

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
            self.swaps = [x for x in self.swaps if x.other != other]
            self.swaps.append(_Swap(other, a == me, e.session_time + self.cfg.pass_hold_s))
        elif e.code == "FTLP" and self.cfg.fastest_lap:
            holder, before = d["vehicleIdx"], self.fastest_holder
            self.fastest_holder = holder
            if holder == me and before is not None and before != me and not np.isnan(lap) \
                    and lap >= 3:
                self._say("racecraft_fastest", phrases.fastest_lap_phrase(d["lapTime"]),
                          Priority.LOW, max_wait_s=15.0)
        elif e.code == "PENA" and self.cfg.penalties and d["vehicleIdx"] == me:
            self._say("racecraft_penalty", phrases.penalty_phrase(
                d["penaltyType"], d["infringementType"], d["time"]), max_wait_s=15.0)
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
        # Still the way round it was reported? (Swapped back = no call.)
        gained = [x for x in due if x.gained and c.position[me] < c.position[x.other]]
        lost = [x for x in due if not x.gained and c.position[me] > c.position[x.other]]
        pos = int(c.position[me])
        if len(gained) > len(lost):
            self.last_pass_call = t
            self._say("racecraft_pass", phrases.pass_phrase(
                [self._name(x.other) for x in gained], pos, self.passes_said), max_wait_s=8.0)
            self.passes_said += 1
        elif lost and not gained:
            self.last_pass_call = t
            self._say("racecraft_pass", phrases.lost_place_phrase(self._name(lost[-1].other),
                                                                  pos), max_wait_s=8.0)

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
            self._say("racecraft_damage", phrases.contact_damage_phrase(parts))
