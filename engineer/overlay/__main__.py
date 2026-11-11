"""Try the lap delta overlay without the game.

    uv run python -m engineer.overlay --demo              # a made-up lap, starts in move mode
    uv run python -m engineer.overlay --replay <file.zst> # a recording at real speed
    uv run python -m engineer.overlay --demo --locked     # as it looks in a race

Move mode: drag it, scroll to resize, double-click to lock. The spot is saved to
config.local.yaml and used from then on. Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import io
import logging
import math
import sys
import time
from pathlib import Path

from engineer.config import load_config
from engineer.overlay.process import (
    OverlayFeed,
    OverlayProcess,
    save_placed,
    settings_of,
)
from engineer.state.live_delta import CornerSplit, DeltaReading


def demo(ov: OverlayProcess, seconds: float) -> None:
    """A 30 s lap that drifts up and down, a corner split every 6 s, a gap with nothing (like the
    pit lane) at the end of each lap."""
    start = time.monotonic()
    split = None
    while ov.running and time.monotonic() - start < seconds:
        t = time.monotonic() - start
        lap_t = t % 34.0
        if lap_t > 30.0:
            ov.send(None)
        else:
            d = 0.35 * math.sin(lap_t / 4.0) + 0.1 * math.sin(lap_t * 1.7) - lap_t * 0.01
            turn = int(lap_t // 6)
            if lap_t % 6 < 0.05 and turn and (split is None or split.corner != turn):
                split = CornerSplit(turn, f"turn {turn}", d, 0.12 * math.sin(turn * 2.1), t)
            corner = split if split is not None and t - split.at < 3.0 else None
            ov.send(DeltaReading(lap=int(t // 34) + 3, ref_lap=int(t // 34) + 2,
                                 distance_m=lap_t * 150, delta_s=d, session_time=t,
                                 corner=corner))
        ov.poll()
        time.sleep(1 / 60)


def replay_file(ov: OverlayProcess, path: Path, speed: float) -> None:
    from engineer.app import build_engine
    from engineer.ingest.replay import replay
    cfg = load_config()
    cfg.voice.log = False
    engine = build_engine(cfg, out=io.StringIO())          # the window only, no console lines
    engine.pipeline.after_packet.append(OverlayFeed(engine.live_delta, ov).after_packet)
    replay(path, speed, engine=engine)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.overlay",
                                 description="Try the lap delta overlay.")
    ap.add_argument("--demo", action="store_true", help="a made-up lap (default)")
    ap.add_argument("--replay", type=Path, help="a recording (.zst) at real speed")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed (default 1)")
    ap.add_argument("--locked", action="store_true", help="start locked (as in a race)")
    ap.add_argument("--seconds", type=float, default=300.0, help="how long the demo runs")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    ov = OverlayProcess(settings_of(cfg.overlay), on_placed=lambda pos: (
        save_placed()(pos), print(f"Locked at {pos['x']}, {pos['y']} (size {pos['scale']}); "
                                  "saved to config.local.yaml.")))
    if not ov.start(locked=args.locked):
        print("The overlay couldn't open a window here.", file=sys.stderr)
        return 1
    if not args.locked:
        print("Move mode: drag it, scroll to resize, double-click to lock.")
    try:
        if args.replay:
            if not args.replay.exists():
                print(f"No such recording: {args.replay}", file=sys.stderr)
                return 2
            replay_file(ov, args.replay, args.speed)
        else:
            demo(ov, args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        ov.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
