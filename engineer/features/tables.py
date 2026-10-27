"""Offline feature tables: replay every recording and write Parquet files.

    uv run python -m engineer.features build          # -> data/features/laps.parquet, stints.parquet
    uv run python -m engineer.features query "select * from stints where is_player"

Recordings go through the same Pipeline and LapFeatureBuilder as live play, so the tables hold
exactly what the live engineer would have computed. The tables are rebuilt from scratch each time:
simple, and the recordings are the source of truth.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from engineer.features.lap_features import LapFeatureBuilder, LapFeatures, summarize_stints
from engineer.ingest.recorder import read_recording
from engineer.pipeline import Pipeline

log = logging.getLogger(__name__)


def features_from_recording(path: Path | str) -> list[LapFeatures]:
    pipeline = Pipeline()
    builder = LapFeatureBuilder(pipeline.bus, pipeline.state)
    pipeline.after_packet.append(builder.after_packet)
    rows: list[LapFeatures] = []
    builder.listeners.append(rows.append)
    for t_ns, raw in read_recording(path):
        pipeline.feed(raw, t_ns)
    return rows


def _write(rows: list[dict], path: Path) -> None:
    if rows:
        pq.write_table(pa.Table.from_pylist(rows), path)
    elif path.exists():
        path.unlink()


def build(sessions_dir: Path | str, features_dir: Path | str, progress=print) -> tuple[int, int]:
    """Returns (laps, stints) written."""
    sessions_dir, features_dir = Path(sessions_dir), Path(features_dir)
    features_dir.mkdir(parents=True, exist_ok=True)
    recordings = sorted(sessions_dir.glob("*.zst"))
    laps: list[LapFeatures] = []
    for n, path in enumerate(recordings, start=1):
        got = features_from_recording(path)
        progress(f"[{n}/{len(recordings)}] {path.name}: {len(got)} laps")
        laps.extend(got)
    stints = summarize_stints(laps)
    _write([r.to_row() for r in laps], features_dir / "laps.parquet")
    _write([s.to_row() for s in stints], features_dir / "stints.parquet")
    return len(laps), len(stints)


def query(features_dir: Path | str, sql: str):
    """Run SQL over the tables with DuckDB. Tables are available as `laps` and `stints`, plus
    lookups `tracks(track_id, track)` and `compounds(actual_compound, compound)` for names."""
    import duckdb

    from engineer.ingest.formats import f1_2025 as f

    con = duckdb.connect()
    tracks = pa.table({"track_id": list(f.TRACKS), "track": list(f.TRACKS.values())})
    compounds = pa.table({"actual_compound": list(f.ACTUAL_COMPOUNDS),
                          "compound": list(f.ACTUAL_COMPOUNDS.values())})
    con.register("tracks", tracks)
    con.register("compounds", compounds)
    for name in ("laps", "stints"):
        path = Path(features_dir) / f"{name}.parquet"
        if path.exists():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    return con.sql(sql)
