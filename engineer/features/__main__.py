"""python -m engineer.features build | query "<sql>" | tyres"""

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.features")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="replay every recording and write the Parquet tables")
    q = sub.add_parser("query", help="run SQL over the tables (laps, stints)")
    q.add_argument("sql")
    sub.add_parser("tyres", help="your wear rate per lap by track and compound")
    args = ap.parse_args(argv)
    cfg = load_config()
    out_dir = cfg.data_path / "features"

    if args.cmd == "build":
        laps, stints = tables.build(cfg.sessions_path, out_dir)
        print(f"Wrote {laps} laps and {stints} stints to {out_dir}")
        return 0
    if not (out_dir / "stints.parquet").exists():
        print("No feature tables yet. Run: uv run python -m engineer.features build")
        return 1
    tables.query(out_dir, TYRES_SQL if args.cmd == "tyres" else args.sql).show(max_rows=200)
    return 0


if __name__ == "__main__":
    sys.exit(main())
