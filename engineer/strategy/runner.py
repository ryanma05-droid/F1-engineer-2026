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

from engineer.strategy.model import (GameTyreInfo, ModelSettings, StrategyModel, build_model)
from engineer.strategy.sim import RaceSituation, SimResult, simulate

log = logging.getLogger(__name__)

HISTORY_COLUMNS = ("session_uid", "track_id", "car_idx", "is_player", "lap", "stint", "stint_lap",
                   "lap_time_ms", "clean", "safety_car", "invalid", "pit_in_lap", "pit_out_lap",
                   "race_start_lap", "visual_compound", "fuel_kg", "wear_rl", "wear_rr",
                   "wear_fl", "wear_fr")


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


def solve(req: StrategyRequest, history: list[dict]) -> StrategyResult:
    try:
        # This session might also be in the history table (a replay of a built recording).
        past = [r for r in history if r.get("session_uid") != req.session_uid]
        all_rows = past + list(req.session_rows)
        track_rows = [r for r in all_rows if r.get("track_id") == req.track_id]
        model = build_model(track_rows, all_rows, list(req.current_stint), req.game, req.settings)
        sit = replace(req.situation, pit_loss_s=model.pit_loss.value,
                      sc_pit_loss_s=model.pit_loss_sc.value if req.situation.sc_laps else None)
        sim = simulate(sit, model)
        green = simulate(replace(sit, sc_laps=(), sc_pit_loss_s=None), model) \
            if req.sc_compare else None
        what_if = simulate(replace(sit, first_stop_lap=req.what_if_box_lap), model) \
            if req.what_if_box_lap is not None else None
        return StrategyResult(req.seq, req.reason, sit.lap_done, model, sim, green,
                              sc_laps=sit.sc_laps, what_if=what_if)
    except Exception as exc:
        log.exception("strategy solve failed")
        return StrategyResult(req.seq, req.reason, req.situation.lap_done, None, SimResult(None),
                              error=repr(exc))


class InlineRunner:
    """Solves on the spot (replays, tests): the result is ready when submit() returns."""

    def __init__(self, features_dir: Path | str | None):
        self.features_dir = features_dir
        self._history: list[dict] | None = None
        self._done: list[StrategyResult] = []

    @property
    def history(self) -> list[dict]:
        if self._history is None:
            self._history = load_history(self.features_dir)
        return self._history

    def submit(self, req: StrategyRequest) -> None:
        self._done.append(solve(req, self.history))

    def poll(self) -> list[StrategyResult]:
        out, self._done = self._done, []
        return out

    def close(self) -> None:
        pass


def worker_main(inbox, outbox, features_dir) -> None:
    """Worker process: load the history once, then answer requests until told to stop (None)."""
    history = load_history(features_dir)
    while True:
        req = inbox.get()
        if req is None:
            return
        outbox.put(solve(req, history))


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
