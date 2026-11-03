"""Runs the strategy model + simulator: in a worker process live, in-process for replays and tests.

A request carries this session's lap rows and the live race situation; the runner adds the lap
history of past recordings (data/features/laps.parquet, loaded once), builds the model and runs the
simulator. Results come back tagged with the request's sequence number.

Why two runners: live, the asyncio loop must never wait on a computation (CLAUDE.md), so the work
goes to a worker process and the answer arrives a few milliseconds later. Replays and tests run
it in-process, so the same packets always give the same calls at the same game time.
"""

from __future__ import annotations

import logging
import queue
from dataclasses import dataclass, replace
from pathlib import Path

from engineer.features.pit_loss import samples_from
from engineer.strategy.model import (
    GameTyreInfo,
    ModelSettings,
    StrategyModel,
    build_model,
)
from engineer.strategy.prerace import RaceLength, race_length, start_situations
from engineer.strategy.real_pit_loss import REAL_FILE, load_real
from engineer.strategy.sim import Plan, RaceSituation, SimResult, plan_cost, simulate

log = logging.getLogger(__name__)

HISTORY_COLUMNS = ("session_uid", "track_id", "car_idx", "is_player", "lap", "stint", "stint_lap",
                   "lap_time_ms", "clean", "safety_car", "invalid", "pit_in_lap", "pit_out_lap",
                   "race_start_lap", "visual_compound", "fuel_kg", "wear_rl", "wear_rr",
                   "wear_fl", "wear_fr", "session_type")


@dataclass(frozen=True)
class StrategyRequest:
    seq: int
    reason: str                          # "lap", "safety_car", ...
    track_id: int | None
    session_uid: str
    session_rows: tuple                  # this session's lap rows (all cars)
    current_stint: tuple                 # your laps on the fitted set
    game: GameTyreInfo | None
    situation: RaceSituation
    settings: ModelSettings
    sc_compare: bool = False             # also solve without the cheap SC stop
    what_if_box_lap: int | None = None   # also solve with the first stop forced on this lap
    what_if_compound: int | None = None  # ... onto this compound
    hold_lap: int | None = None          # the committed plan's first stop: what it costs now
    hold_compound: int | None = None
    safe_limits: tuple[float, ...] = ()  # also plan with these (lower) wear limits: safe options
    # The pre-race plan (strategy/prerace.py): solve once per start compound. situation.total_laps
    # 0 = not known (the worker works it out from the history).
    starts: tuple[int, ...] = ()
    laps_how: str = "game"               # where situation.total_laps came from
    recent_race: tuple[int, int] | None = None   # (track, laps) of a race seen live this run
    two_compound: tuple[str, int] = ("auto", 10)  # strategy.two_compound_rule, ..._min_laps
    # This session's stops timed by the game's pit lane timer: (green, safety car) losses.
    pit_samples: tuple[tuple[float, ...], tuple[float, ...]] = ((), ())


@dataclass
class StrategyResult:
    seq: int
    reason: str
    lap_done: int
    model: StrategyModel | None
    sim: SimResult
    green: SimResult | None = None       # sc_compare: the same race without the SC discount
    sc_laps: tuple[int, ...] = ()        # laps where a stop was costed as a safety car stop
    what_if: SimResult | None = None     # what_if_box_lap: the best plan stopping on that lap
    error: str | None = None
    situation: RaceSituation | None = None   # what was solved (wear, sets, laps) after the model
    options: tuple = ()                  # candidate plans: each stop count, each next tyre
    hold: Plan | None = None             # hold_lap: the best plan keeping the committed stop
    starts: tuple = ()                   # pre-race: (start compound, StrategyResult), each
    race_length: RaceLength | None = None    # pre-race: the laps planned for, and why


def load_history(features_dir: Path | str | None) -> list[dict]:
    """Past laps (all tracks, all cars) from data/features/laps.parquet; [] if not built."""
    if features_dir is None:
        return []
    path = Path(features_dir) / "laps.parquet"
    if not path.exists():
        return []
    try:
        import pyarrow.parquet as pq
        table = pq.read_table(path)
        cols = [c for c in HISTORY_COLUMNS if c in table.column_names]
        return table.select(cols).to_pylist()
    except Exception as exc:          # a broken table shouldn't stop the engineer
        log.warning("could not read %s (%s); no lap history used", path, exc)
        return []


def solve_prerace(req: StrategyRequest, history: list[dict],
                  pit: tuple[list[dict], dict] = ([], {})) -> StrategyResult:
    """Every start compound from lap 0 (strategy/prerace.py)."""
    sit = req.situation
    length = RaceLength(sit.total_laps, req.laps_how) if sit.total_laps \
        else race_length(req.track_id, history, req.recent_race)
    if length is None:
        return StrategyResult(req.seq, req.reason, 0, None, SimResult(None), error="no_laps")
    rule, min_laps = req.two_compound
    base = replace(sit, total_laps=length.laps,
                   require_two=rule == "on" or (rule == "auto" and length.laps >= min_laps))
    found = []
    for c, s in start_situations(base, req.starts):
        r = solve(replace(req, starts=(), situation=s), history, pit)
        if r.error is None and r.sim.best is not None:
            found.append((c, r))
    best = min((r.sim.best for _, r in found), key=lambda p: p.cost_s, default=None)
    return StrategyResult(req.seq, req.reason, 0, found[0][1].model if found else None,
                          SimResult(best), situation=base, starts=tuple(found),
                          race_length=length)


def load_pit_history(features_dir: Path | str | None) -> tuple[list[dict], dict[int, float]]:
    """Past timed stops (data/features/pit_losses.parquet) and the real F1 pit loss per track
    (data/features/real_pit_loss.json); empty when not there."""
    if features_dir is None:
        return [], {}
    stops: list[dict] = []
    path = Path(features_dir) / "pit_losses.parquet"
    if path.exists():
        try:
            import pyarrow.parquet as pq
            stops = pq.read_table(path).to_pylist()
        except Exception as exc:
            log.warning("could not read %s (%s)", path, exc)
    return stops, load_real(Path(features_dir) / REAL_FILE)


def _timer(req: StrategyRequest, pit: tuple[list[dict], dict]) -> tuple[list[float], list[float]]:
    past = [r for r in pit[0] if r.get("track_id") == req.track_id
            and r.get("session_uid") != req.session_uid]
    green, sc = samples_from(past)
    return green + list(req.pit_samples[0]), sc + list(req.pit_samples[1])


def solve(req: StrategyRequest, history: list[dict],
          pit: tuple[list[dict], dict] = ([], {})) -> StrategyResult:
    try:
        if req.starts:
            return solve_prerace(req, history, pit)
        # This session might also be in the history table (a replay of a built recording).
        past = [r for r in history if r.get("session_uid") != req.session_uid]
        all_rows = past + list(req.session_rows)
        track_rows = [r for r in all_rows if r.get("track_id") == req.track_id]
        model = build_model(track_rows, all_rows, list(req.current_stint), req.game, req.settings,
                            timer=_timer(req, pit), real_pit_s=pit[1].get(req.track_id))
        sit = replace(req.situation, pit_loss_s=model.pit_loss.value,
                      sc_pit_loss_s=model.pit_loss_sc.value if req.situation.sc_laps else None)
        sim = simulate(sit, model)
        green = simulate(replace(sit, sc_laps=(), sc_pit_loss_s=None), model) \
            if req.sc_compare else None
        what_if = simulate(replace(sit, first_stop_lap=req.what_if_box_lap,
                                   first_stop_compound=req.what_if_compound), model) \
            if req.what_if_box_lap is not None else None
        # Candidates for the call (decision.py): the best plan for each number of stops, and the
        # best plan for each tyre you could fit at the next stop.
        options = list(sim.by_stops.values())
        if req.what_if_box_lap is None:
            for c, left in sorted(sit.sets.items()):
                if left:
                    options.append(simulate(replace(sit, first_stop_compound=c), model).best)
        # Careful versions (stopping before the tyres get near the limit), priced at the real
        # limit, so there's a safe plan to compare a tight one with.
        for lim in req.safe_limits:
            careful = replace(sit, limit_pct=lim)
            found = list(simulate(careful, model).by_stops.values())
            if req.what_if_box_lap is None:
                for c, left in sorted(sit.sets.items()):
                    if left:
                        found.append(simulate(replace(careful, first_stop_compound=c), model).best)
            for p in found:
                if p is not None:
                    options.append(replace(p, cost_s=plan_cost(p, sit, model)))
        hold = None
        if req.hold_lap is not None and req.hold_lap > sit.lap_done:
            hold = simulate(replace(sit, first_stop_lap=req.hold_lap,
                                    first_stop_compound=req.hold_compound), model).best
        return StrategyResult(req.seq, req.reason, sit.lap_done, model, sim, green,
                              sc_laps=sit.sc_laps, what_if=what_if, situation=sit,
                              options=tuple(p for p in options if p is not None), hold=hold)
    except Exception as exc:
        log.exception("strategy solve failed")
        return StrategyResult(req.seq, req.reason, req.situation.lap_done, None, SimResult(None),
                              error=repr(exc))


class InlineRunner:
    """Solves on the spot (replays, tests): the result is ready when submit() returns."""

    def __init__(self, features_dir: Path | str | None):
        self.features_dir = features_dir
        self._history: list[dict] | None = None
        self._pit: tuple[list[dict], dict] | None = None
        self._done: list[StrategyResult] = []

    @property
    def pit(self) -> tuple[list[dict], dict]:
        if self._pit is None:
            self._pit = load_pit_history(self.features_dir)
        return self._pit

    @property
    def history(self) -> list[dict]:
        if self._history is None:
            self._history = load_history(self.features_dir)
        return self._history

    def submit(self, req: StrategyRequest) -> None:
        self._done.append(solve(req, self.history, self.pit))

    def poll(self) -> list[StrategyResult]:
        out, self._done = self._done, []
        return out

    def close(self) -> None:
        pass


def worker_main(inbox, outbox, features_dir) -> None:
    """Worker process: load the history once, then answer requests until told to stop (None)."""
    history = load_history(features_dir)
    pit = load_pit_history(features_dir)
    while True:
        req = inbox.get()
        if req is None:
            return
        outbox.put(solve(req, history, pit))


class WorkerRunner:
    """Solves in a separate process (live play); results are picked up by poll()."""

    def __init__(self, features_dir: Path | str | None):
        from engineer.workers.pool import start_worker
        self.proc, self.inbox, self.outbox = start_worker(
            worker_main, None if features_dir is None else str(features_dir))

    def submit(self, req: StrategyRequest) -> None:
        self.inbox.put(req)

    def poll(self) -> list[StrategyResult]:
        out = []
        while True:
            try:
                out.append(self.outbox.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        try:
            self.inbox.put(None)
            self.proc.join(timeout=2)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.terminate()
