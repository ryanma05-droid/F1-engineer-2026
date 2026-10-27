"""Offline feature tables: replay every recording and write Parquet files.

    uv run python -m engineer.features build   # -> data/features/laps, stints, traces, corners
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

import numpy as np

from engineer.bus import events as ev
from engineer.features.lap_features import LapFeatureBuilder, LapFeatures, summarize_stints
from engineer.ingest.recorder import read_recording
from engineer.pipeline import Pipeline
from engineer.state.track_maps import TrackMaps
from engineer.state.traces import LapTrace, TraceRecorder
from engineer.tools.queries import corner_stats

log = logging.getLogger(__name__)


def features_from_recording(path: Path | str) -> list[LapFeatures]:
    return _replay(path)[0]


def _replay(path: Path | str) -> tuple[list[LapFeatures], list[LapTrace], dict]:
    """Replay one recording; returns lap features, lap traces and session info."""
    pipeline = Pipeline()
    builder = LapFeatureBuilder(pipeline.bus, pipeline.state)
    pipeline.after_packet.append(builder.after_packet)
    traces = TraceRecorder(pipeline.state)
    pipeline.bus.subscribe(ev.SessionStarted, lambda e: traces.reset())
    pipeline.bus.subscribe(ev.Flashback, lambda e: traces.rewind())
    pipeline.after_packet.append(traces.after_packet)
    rows: list[LapFeatures] = []
    builder.listeners.append(rows.append)
    lap_traces: dict[tuple[int, int], LapTrace] = {}

    def on_lap(e: ev.LapCompleted) -> None:
        tr = traces.trace_for(e.car_idx, e.lap)
        if tr is not None and tr.usable:
            lap_traces[(e.car_idx, e.lap)] = tr      # a re-driven lap replaces the old one
    pipeline.bus.subscribe(ev.LapCompleted, on_lap)
    for t_ns, raw in read_recording(path):
        pipeline.feed(raw, t_ns)
    s = pipeline.state
    info = {"session_uid": f"{s.session_uid or 0:016x}", "track_id": s.session.track_id}
    return rows, list(lap_traces.values()), info


def _trace_rows(tr: LapTrace, info: dict) -> dict:
    """One trace as columns (one row per 5 m section)."""
    n = len(tr.distance_m)
    return {"session_uid": [info["session_uid"]] * n, "car_idx": np.full(n, tr.car_idx),
            "lap": np.full(n, tr.lap), "distance_m": tr.distance_m, "time_s": tr.time_s,
            "speed_kph": tr.speed_kph, "throttle": tr.throttle, "brake": tr.brake,
            "steer": tr.steer, "gear": tr.gear}


def _write(rows: list[dict], path: Path) -> None:
    if rows:
        pq.write_table(pa.Table.from_pylist(rows), path)
    elif path.exists():
        path.unlink()


def build(sessions_dir: Path | str, features_dir: Path | str, progress=print,
          tracks_dir: Path | str | None = None) -> tuple[int, int]:
    """Writes laps, stints, traces and (where a track map exists) corners tables.
    Returns (laps, stints) written."""
    sessions_dir, features_dir = Path(sessions_dir), Path(features_dir)
    features_dir.mkdir(parents=True, exist_ok=True)
    maps = TrackMaps(tracks_dir)
    recordings = sorted(sessions_dir.glob("*.zst"))
    laps: list[LapFeatures] = []
    corner_rows: list[dict] = []
    trace_path = features_dir / "traces.parquet"
    writer = None
    for n, path in enumerate(recordings, start=1):
        got, lap_traces, info = _replay(path)
        progress(f"[{n}/{len(recordings)}] {path.name}: {len(got)} laps")
        laps.extend(got)
        tmap = maps.load(info["track_id"]) if info["track_id"] is not None else None
        for tr in lap_traces:
            table = pa.table(_trace_rows(tr, info))
            if writer is None:
                writer = pq.ParquetWriter(trace_path, table.schema)
            writer.write_table(table)
            for c in (tmap.corners if tmap else []):
                st = corner_stats(tr, c)
                corner_rows.append({"session_uid": info["session_uid"],
                                    "track_id": info["track_id"], "car_idx": tr.car_idx,
                                    "lap": tr.lap, "corner": c.number, "corner_name": c.name,
                                    "time_s": st.time_s, "brake_m": st.brake_m,
                                    "min_speed_kph": st.min_speed_kph,
                                    "full_throttle_m": st.full_throttle_m,
                                    "exit_speed_kph": st.exit_speed_kph})
    if writer is not None:
        writer.close()
    elif trace_path.exists():
        trace_path.unlink()
    stints = summarize_stints(laps)
    _write([r.to_row() for r in laps], features_dir / "laps.parquet")
    _write([s.to_row() for s in stints], features_dir / "stints.parquet")
    _write(corner_rows, features_dir / "corners.parquet")
    return len(laps), len(stints)


def query(features_dir: Path | str, sql: str):
    """Run SQL over the tables with DuckDB. Tables: `laps`, `stints`, `traces`, `corners`, plus
    lookups `tracks(track_id, track)` and `compounds(actual_compound, compound)` for names."""
    import duckdb

    from engineer.ingest.formats import f1_2025 as f

    con = duckdb.connect()
    tracks = pa.table({"track_id": list(f.TRACKS), "track": list(f.TRACKS.values())})
    compounds = pa.table({"actual_compound": list(f.ACTUAL_COMPOUNDS),
                          "compound": list(f.ACTUAL_COMPOUNDS.values())})
    con.register("tracks", tracks)
    con.register("compounds", compounds)
    for name in ("laps", "stints", "traces", "corners"):
        path = Path(features_dir) / f"{name}.parquet"
        if path.exists():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    return con.sql(sql)
