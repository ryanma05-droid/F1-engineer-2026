"""python -m engineer.features build | query "<sql>" | tyres | corners"""

from __future__ import annotations

import argparse
import sys

from engineer.config import load_config
from engineer.features import tables

TYRES_SQL = """
-- your median tyre wear per lap (%), RL RR FL FR, from stints of 3+ laps
SELECT coalesce(track, track_id::VARCHAR) AS track, coalesce(compound, actual_compound::VARCHAR)
       AS compound, count(*) AS stints, sum(laps)::INT AS laps,
       round(median(wear_rate_rl), 1) AS rl, round(median(wear_rate_rr), 1) AS rr,
       round(median(wear_rate_fl), 1) AS fl, round(median(wear_rate_fr), 1) AS fr
FROM stints LEFT JOIN tracks USING (track_id) LEFT JOIN compounds USING (actual_compound)
WHERE is_player AND laps >= 3
GROUP BY ALL ORDER BY track, compound
"""


CORNERS_SQL = """
-- per corner: your best and median time, vs the median of every other car (clean laps only)
WITH c AS (
  SELECT corners.*, laps.is_player FROM corners
  JOIN laps USING (session_uid, car_idx, lap)
  WHERE laps.clean AND NOT laps.safety_car
)
SELECT coalesce(track, track_id::VARCHAR) AS track, corner, any_value(corner_name) AS name,
       round(min(time_s) FILTER (WHERE is_player), 3) AS your_best,
       round(median(time_s) FILTER (WHERE is_player), 3) AS your_median,
       round(median(time_s) FILTER (WHERE NOT is_player), 3) AS others_median,
       round(median(min_speed_kph) FILTER (WHERE is_player)) AS your_min_kph,
       round(median(min_speed_kph) FILTER (WHERE NOT is_player)) AS others_min_kph
FROM c LEFT JOIN tracks USING (track_id)
GROUP BY ALL ORDER BY track, corner
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.features")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="replay every recording and write the Parquet tables")
    q = sub.add_parser("query", help="run SQL over the tables (laps, stints)")
    q.add_argument("sql")
    sub.add_parser("tyres", help="your wear rate per lap by track and compound")
    sub.add_parser("corners", help="your average time and minimum speed per corner")
    args = ap.parse_args(argv)
    cfg = load_config()
    out_dir = cfg.data_path / "features"

    if args.cmd == "build":
        laps, stints = tables.build(cfg.sessions_path, out_dir, tracks_dir=cfg.tracks_path)
        print(f"Wrote {laps} laps and {stints} stints to {out_dir}")
        return 0
    if not (out_dir / "stints.parquet").exists():
        print("No feature tables yet. Run: uv run python -m engineer.features build")
        return 1
    sql = {"tyres": TYRES_SQL, "corners": CORNERS_SQL}.get(args.cmd) or args.sql
    tables.query(out_dir, sql).show(max_rows=200)
    return 0


if __name__ == "__main__":
    sys.exit(main())
