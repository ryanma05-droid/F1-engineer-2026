"""Data sanity checks that need a real recording to settle.

Lap Data field order (grid position vs driver status)
------------------------------------------------------
The spec lists `m_gridPosition` before `m_driverStatus`. A community report says real packets have
them swapped. In a race, every active car has a distinct grid slot 1..N, so we look at the first
Lap Data packet of a race with several active cars:

- our `grid_position` values are exactly 1..N  -> spec order confirmed
- our `driver_status` values are exactly 1..N  -> the fields look swapped (warn loudly)
- neither                                      -> inconclusive (try again on the next packet)

The result is kept in `state.checks.grid_order` and printed by the CLI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

MIN_CARS_FOR_GRID_CHECK = 6  # driver status only goes 0..4, so 6+ distinct values can't be status


@dataclass
class Checks:
    grid_order: str | None = None   # None (not checked yet), "spec", "swapped"


def _is_one_to_n(values: np.ndarray) -> bool:
    if values.size == 0 or np.isnan(values).any():
        return False
    return np.array_equal(np.sort(values), np.arange(1, values.size + 1))


def check_grid_order(state) -> None:
    if state.checks.grid_order is not None or not state.session.is_race:
        return
    cars = state.cars
    active = cars.active
    if active.sum() < MIN_CARS_FOR_GRID_CHECK:
        return
    grid = cars.grid_position[active]
    status = cars.driver_status[active]
    if _is_one_to_n(grid):
        state.checks.grid_order = "spec"
        log.info("Lap Data field order check: grid positions look correct (spec order confirmed).")
    elif _is_one_to_n(status):
        state.checks.grid_order = "swapped"
        log.warning(
            "Lap Data field order check: m_gridPosition and m_driverStatus look SWAPPED in real "
            "packets (driver_status holds 1..%d). Swap them in engineer/ingest/formats/f1_2025.py "
            "LAP_DATA and update PROGRESS.md.", int(active.sum()))
