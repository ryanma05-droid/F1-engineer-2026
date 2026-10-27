"""What your past stints say about tyre wear, read from the offline stint table.

For each (track, actual compound) we keep the median wear rate per wheel over your own stints of
3+ laps. The live engineer uses it while a new stint is too short to measure (see
tools/queries.tyre_status). No table yet (nothing built) simply means no history.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MIN_STINT_LAPS = 3


class WearPriors:
    def __init__(self, rates: dict[tuple[int, int], np.ndarray] | None = None):
        self.rates = rates or {}

    def get(self, track_id: int | None, actual_compound: int | None) -> np.ndarray | None:
        r = self.rates.get((track_id, actual_compound))
        return None if r is None else r.copy()

    def __len__(self) -> int:
        return len(self.rates)

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "WearPriors":
        groups: dict[tuple[int, int], list[list[float]]] = {}
        for r in rows:
            if not r.get("is_player") or (r.get("laps") or 0) < MIN_STINT_LAPS:
                continue
            if r.get("track_id") is None or r.get("actual_compound") is None:
                continue
            rate = [r.get(f"wear_rate_{w}") for w in ("rl", "rr", "fl", "fr")]
            groups.setdefault((r["track_id"], r["actual_compound"]), []).append(
                [np.nan if v is None else v for v in rate])
        rates = {}
        for key, vals in groups.items():
            arr = np.array(vals, dtype=float)
            if not np.isnan(arr).all():
                rates[key] = np.nanmedian(arr, axis=0)
        return cls(rates)

    @classmethod
    def load(cls, features_dir: Path | str) -> "WearPriors":
        path = Path(features_dir) / "stints.parquet"
        if not path.exists():
            return cls()
        try:
            import pyarrow.parquet as pq
            return cls.from_rows(pq.read_table(path).to_pylist())
        except Exception as exc:   # a broken table shouldn't stop the engineer
            log.warning("could not read %s (%s); no tyre history used", path, exc)
            return cls()
