"""Real F1 pit loss per track, from the OpenF1 data service, as a starting value.

    python -m engineer.strategy.real_pit_loss            # fetch (about a minute), save, show
    python -m engineer.strategy.real_pit_loss --show     # what's saved

The engineer's own measurement (the game's pit lane timer, features/pit_loss.py) is the real
answer; this is only for tracks where no timed stop has been recorded yet, so a new track
doesn't start from the flat `strategy.pit_loss_s` default. In the model it counts like three
measured stops (strategy/model.py REAL_F1_WEIGHT), so the game's own stops take over quickly.

How: every real Grand Prix of `YEARS` (OpenF1 /sessions, /laps), each stop's in-lap + out-lap
minus twice the driver's median green lap around it; the race's median, then the median over the
years. Real F1 isn't the game (stop times, out-laps), which is why it's only a starting value.

Saved to data/features/real_pit_loss.json; the app fetches it in the background when it's
missing (cli), and failing (offline) just means the default stays.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://api.openf1.org/v1"
YEARS = (2023, 2024, 2025)
REAL_FILE = "real_pit_loss.json"
PAUSE_S = 0.4                  # between requests: OpenF1's free tier is rate limited
MIN_LOSS_S, MAX_LOSS_S = 8.0, 45.0   # outside this: a penalty, a repair, a red flag

# OpenF1 circuit / location names -> game track id.
TRACK_NAMES = {
    0: ("melbourne", "albert park"), 2: ("shanghai",), 3: ("sakhir", "bahrain"),
    4: ("catalunya", "barcelona"), 5: ("monte carlo", "monaco"), 6: ("montreal",),
    7: ("silverstone",), 9: ("hungaroring", "budapest"), 10: ("spa",), 11: ("monza",),
    12: ("singapore", "marina bay"), 13: ("suzuka",), 14: ("yas marina", "abu dhabi"),
    15: ("austin",), 16: ("interlagos", "sao paulo", "são paulo"), 17: ("spielberg",),
    19: ("mexico",), 20: ("baku",), 26: ("zandvoort",), 27: ("imola",), 29: ("jeddah",),
    30: ("miami",), 31: ("las vegas",), 32: ("lusail", "losail", "qatar"),
}


def track_of(session: dict) -> int | None:
    names = " ".join(str(session.get(k) or "") for k in
                     ("circuit_short_name", "location", "country_name")).lower()
    for track, words in TRACK_NAMES.items():
        if any(w in names for w in words):
            return track
    return None


def race_loss(laps: list[dict]) -> tuple[float, int] | None:
    """(median pit loss, stops) for one race from OpenF1 lap rows, None without stops."""
    by: dict[int, dict[int, dict]] = {}
    for r in laps:
        if r.get("driver_number") is not None and r.get("lap_number") is not None:
            by.setdefault(r["driver_number"], {})[int(r["lap_number"])] = r
    losses = []
    for rows in by.values():
        pit_laps = {n for n, r in rows.items() if r.get("is_pit_out_lap")}
        pit_laps |= {n - 1 for n in pit_laps}
        for out_n, out in rows.items():
            if not out.get("is_pit_out_lap") or out_n <= 2:
                continue
            inl = rows.get(out_n - 1)
            if inl is None or not inl.get("lap_duration") or not out.get("lap_duration"):
                continue
            near = [rows[n]["lap_duration"] for n in range(out_n - 6, out_n + 6)
                    if n in rows and n not in pit_laps and n > 1 and rows[n].get("lap_duration")]
            if len(near) < 3:
                continue
            loss = inl["lap_duration"] + out["lap_duration"] - 2 * statistics.median(near)
            if MIN_LOSS_S <= loss <= MAX_LOSS_S:
                losses.append(loss)
    return (statistics.median(losses), len(losses)) if losses else None


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "race-engineer"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(get=_get, years=YEARS, pause_s: float = PAUSE_S, progress=print) -> dict:
    """{track id: {"loss_s", "races", "stops"}} from OpenF1."""
    per_track: dict[int, list[tuple[float, int]]] = {}
    for year in years:
        sessions = get(f"{API}/sessions?year={year}&session_name=Race")
        for s in sessions:
            track = track_of(s)
            if track is None or s.get("session_key") is None:
                continue
            time.sleep(pause_s)
            try:
                got = race_loss(get(f"{API}/laps?session_key={s['session_key']}"))
            except Exception as exc:          # one race failing shouldn't stop the rest
                progress(f"  {year} {s.get('location')}: skipped ({exc})")
                continue
            if got is not None:
                per_track.setdefault(track, []).append(got)
                progress(f"  {year} {s.get('location')}: {got[0]:.1f} s from {got[1]} stops")
    return {t: {"loss_s": round(statistics.median(v for v, _ in races), 2),
                "races": len(races), "stops": sum(n for _, n in races)}
            for t, races in per_track.items()}


def save(table: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"source": "OpenF1, real Grands Prix " + ", ".join(map(str, YEARS)),
            "fetched": time.strftime("%Y-%m-%d"), "tracks": {str(k): v for k, v in table.items()}}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_real(path: Path) -> dict[int, float]:
    """{track id: real pit loss s}; {} when not fetched."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {int(k): float(v["loss_s"]) for k, v in data.get("tracks", {}).items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def fetch_in_background(features_dir: Path) -> None:
    """Live: fetch once when the file isn't there yet (used from the next run on)."""
    path = Path(features_dir) / REAL_FILE
    if path.exists():
        return

    def run():
        try:
            table = fetch(progress=lambda m: log.debug("%s", m))
            if table:
                save(table, path)
                log.info("real F1 pit losses saved for %d tracks", len(table))
        except Exception as exc:              # offline: the default stays
            log.info("real F1 pit losses not fetched (%s)", exc)
    threading.Thread(target=run, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    from engineer.config import load_config
    from engineer.radio.phrases import say_track
    cfg = load_config()
    ap = argparse.ArgumentParser(prog="python -m engineer.strategy.real_pit_loss")
    ap.add_argument("--show", action="store_true", help="show what's saved, don't fetch")
    args = ap.parse_args(argv)
    path = cfg.features_path / REAL_FILE
    if not args.show:
        print(f"Fetching real F1 races {YEARS[0]}-{YEARS[-1]} from OpenF1 (about a minute)...")
        table = fetch()
        if not table:
            print("Nothing fetched.")
            return 1
        save(table, path)
        print(f"Saved {path}")
    real = load_real(path)
    if not real:
        print("No real F1 pit losses saved yet.")
        return 1
    for track, loss in sorted(real.items(), key=lambda kv: say_track(kv[0])):
        print(f"  {say_track(track):20s} {loss:5.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
