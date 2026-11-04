"""The pre-race tyre plan: which tyre to start on, and the stops that go with it, worked out
before the race (during car and tyre setup).

The game sends nothing useful in the pre-race menus (real recordings: only button presses, no
track, laps or tyre sets until about seven seconds before lights out), so the plan is built from
what the engineer already knows:

- the track: the one you name ("strategy for Suzuka"), the app window's pick, or the session you
  just drove (qualifying at the same weekend);
- the race length: the one you name ("fifteen laps"), else your last race at this track, else your
  usual race distance (your last race as a share of its full distance: 25 %, 35 %, 50 %, 100 %)
  applied to this track's full distance, rounded to the nearest lap like the game (Melbourne 58
  laps at 25 % = 15, at 35 % = 20);
- the tyres: wear rates and pace from your history at this track (the same model as the race
  calls, engineer/strategy/model.py), and the game's tyre sets when the last session was here.

Every dry compound you could start on is simulated from lap 0 with the same sim and safe-call
rules as the race (decision.py); the recommendation is the quickest safe start. On the grid (the
race session has started but the lights haven't gone out) the real laps and sets are used.

Nothing here knows how tyres behave: no history at a track means no plan ("I'll call it after
lap two"), never a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from engineer.ingest.formats import f1_2025 as f
from engineer.strategy.model import DRY

# Full race distance per track (laps). Facts about the calendar, not about tyres: used only to
# turn "your usual race distance" into laps at another track.
FULL_RACE_LAPS = {
    0: 58, 2: 56, 3: 57, 4: 66, 5: 78, 6: 70, 7: 52, 9: 70, 10: 44, 11: 53, 12: 62, 13: 53,
    14: 58, 15: 56, 16: 71, 17: 71, 19: 71, 20: 51, 26: 72, 27: 63, 29: 50, 30: 57, 31: 50,
    32: 57, 39: 52, 40: 71, 41: 72,
}
DISTANCES = (0.25, 0.35, 0.5, 1.0)     # the game's race distance settings (besides 5 laps)
SHORT_RACE = 5


@dataclass(frozen=True)
class RaceLength:
    laps: int
    how: str          # "game" | "said" | "last race here" | "usual distance"


def last_race(history: list[dict]) -> tuple[int, int] | None:
    """(track_id, laps) of the newest race in the history (rows are in recording order). The
    laps are the most any car completed, so a race you left early reads short."""
    best: dict[str, list] = {}
    order: list[str] = []
    for r in history:
        if r.get("session_type") not in f.RACE_SESSION_TYPES or r.get("lap") is None:
            continue
        uid = r.get("session_uid")
        if uid not in best:
            best[uid] = [r.get("track_id"), 0]
            order.append(uid)
        best[uid][1] = max(best[uid][1], int(r["lap"]))
    for uid in reversed(order):
        track, laps = best[uid]
        if track is not None and laps > SHORT_RACE:     # 5-lap races say nothing about yours
            return int(track), laps
    return None


def last_race_at(history: list[dict], track_id: int) -> int | None:
    """Laps of your newest race at this track (5-lap races left out), None if none."""
    laps: dict[str, int] = {}
    for r in history:
        if r.get("track_id") != track_id or r.get("session_type") not in f.RACE_SESSION_TYPES \
                or r.get("lap") is None:
            continue
        uid = r.get("session_uid")
        laps[uid] = max(laps.get(uid, 0), int(r["lap"]))
    longer = [uid for uid in laps if laps[uid] > SHORT_RACE]
    return laps[longer[-1]] if longer else None


def usual_share(track_id: int, laps: int) -> float | None:
    """Your race distance setting, from one race: laps over the full distance, snapped to the
    nearest setting. None for 5-lap races and unknown tracks."""
    full = FULL_RACE_LAPS.get(track_id)
    if not full or laps <= SHORT_RACE:
        return None
    share = laps / full
    return min(DISTANCES, key=lambda d: abs(d - share))


def laps_at(track_id: int, share: float) -> int | None:
    full = FULL_RACE_LAPS.get(track_id)
    # Rounded to the nearest lap, halves up, like the game: Melbourne 58 laps at 25 % = 15, at
    # 35 % = 20 (20.3; 3 Nov), Shanghai 56 at 35 % = 20 (19.6).
    return math.floor(full * share + 0.5) if full else None


def race_length(track_id: int, history: list[dict],
                recent: tuple[int, int] | None = None) -> RaceLength | None:
    """The laps to plan for when nobody said: your last race here, else your usual distance.
    recent: (track_id, laps) of a race seen live this run (the game's lap count, exact).
    From the history the laps are the most any car completed, so a race you left early reads
    short (Shanghai, 3 Nov: left on lap 6 of 14): they're snapped to the distance setting."""
    if recent is not None and recent[1] <= SHORT_RACE:
        recent = None
    if recent is not None and recent[0] == track_id:
        return RaceLength(recent[1], "last race here")
    here = last_race_at(history, track_id)
    if here:
        share = usual_share(track_id, here)
        return RaceLength(laps_at(track_id, share) if share else here, "last race here")
    ref = recent or last_race(history)
    if ref is None:
        return None
    share = usual_share(*ref)
    n = laps_at(track_id, share) if share else None
    return RaceLength(n, "usual distance") if n else None


def start_situations(sit, starts: tuple[int, ...]):
    """One race situation per start compound: lap 0 on the freshest set of it."""
    out = []
    for c in starts:
        left = sorted(sit.sets.get(c, []))
        if c not in DRY or not left:
            continue
        sets = dict(sit.sets)
        sets[c] = left[1:]
        out.append((c, replace(sit, lap_done=0, compound=c, wear=float(left[0]), sets=sets,
                               used=frozenset({c}))))
    return out


@dataclass(frozen=True)
class PreRaceStart:
    compound: int                 # the tyre you start on
    decision: object              # strategy.decision.Decision: the call for this start
    situation: object             # the RaceSituation it was solved from (lap 0)

    @property
    def cost_s(self) -> float:
        return self.decision.call.plan.cost_s

    @property
    def safe(self) -> bool:
        """Safe on the tyres, or tight but quick enough that the race call would take it too
        (decision.py's gamble_take_s)."""
        return self.decision.call.safe or self.decision.taken_gamble


@dataclass(frozen=True)
class PreRacePlan:
    track_id: int
    laps: RaceLength | None
    starts: tuple[PreRaceStart, ...]   # the recommendation first
    grid: bool = False                 # worked out on the grid: the tyre is already fitted
    error: str | None = None           # "no_laps": no race length known; "failed"

    @property
    def best(self) -> PreRaceStart | None:
        return self.starts[0] if self.starts else None

    @property
    def gamble(self) -> PreRaceStart | None:
        """A quicker start the recommendation passed over because it's tight on the tyres."""
        b = self.best
        quicker = [s for s in self.starts[1:] if s.cost_s < b.cost_s] if b else []
        return min(quicker, key=lambda s: s.cost_s) if quicker else None


def order_starts(starts: list[PreRaceStart]) -> list[PreRaceStart]:
    """The recommendation first: the quickest start whose call is safe (decision.py's rules,
    stint by stint), then the rest by time."""
    safe = [s for s in starts if s.safe]
    if not safe:
        return sorted(starts, key=lambda s: s.cost_s)
    best = min(safe, key=lambda s: s.cost_s)
    return [best] + sorted((s for s in starts if s is not best), key=lambda s: s.cost_s)


def short_race(laps: int) -> bool:
    """The game's 5-lap race setting. Tyres wear about half as fast there (3 Nov, Melbourne softs:
    you 3.3 vs 6.2 %/lap, the AI 2.3 vs 4.3 in a 15-lap race; Brazil's 5-lap race the same), while
    25 % and 35 % races wear alike, so wear is never mixed across the two."""
    return laps <= SHORT_RACE


def same_length_rows(rows: list[dict], race_laps: int,
                     this_session: str | None = None) -> list[dict]:
    """Lap rows usable for a race of race_laps: other races only if they're the same kind (5-lap
    or a proper distance); practice and qualifying rows stay, and so does the race being run
    (this_session: its laps so far would look like a short race early on)."""
    want = short_race(race_laps)
    most: dict[tuple, int] = {}
    for r in rows:
        if r.get("session_type") in f.RACE_SESSION_TYPES and r.get("lap") is not None:
            key = (r.get("session_uid"), r.get("track_id"))
            most[key] = max(most.get(key, 0), int(r["lap"]))
    return [r for r in rows if r.get("session_type") not in f.RACE_SESSION_TYPES
            or r.get("session_uid") == this_session
            or short_race(most.get((r.get("session_uid"), r.get("track_id")), 0)) == want]

