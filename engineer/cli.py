"""Command line: `python -m engineer run` (live) and `python -m engineer sessions` (list recordings)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from engineer.config import load_config
from engineer.console import LapPrinter
from engineer.ingest.formats import f1_2025 as f
from engineer.ingest.listener import UdpListener
from engineer.ingest.recorder import Recorder, list_sessions
from engineer.pipeline import Pipeline

log = logging.getLogger("engineer")

STATUS_INTERVAL_S = 10


async def _run(config_path: Path | None, record: bool | None) -> None:
    cfg = load_config(config_path)
    if record is not None:
        cfg.record = record

    pipeline = Pipeline()
    LapPrinter(pipeline.bus, pipeline.state)
    listener = UdpListener(cfg.listen.host, cfg.listen.port,
                           forward=[(t.host, t.port) for t in cfg.forward])
    recorder = Recorder(cfg.sessions_path) if cfg.record else None
    if recorder:
        listener.subscribe(recorder)       # recorder first: raw bytes are saved even if parsing fails
    listener.subscribe(pipeline.feed)

    await listener.start()
    print(f"Listening on {cfg.listen.host}:{cfg.listen.port}. "
          f"In F1 25: Settings > Telemetry Settings > UDP Telemetry On, port {cfg.listen.port}, "
          f"format 2025, send rate 60Hz. Ctrl+C to stop.")
    if recorder:
        print(f"Recording sessions to {cfg.sessions_path}")
    try:
        last = 0
        while True:
            await asyncio.sleep(STATUS_INTERVAL_S)
            if listener.packets_received == last:
                log.info("No packets in the last %ds. Is UDP telemetry on in the game?",
                         STATUS_INTERVAL_S)
            last = listener.packets_received
    finally:
        listener.close()
        if recorder:
            recorder.close()
        print(f"Stopped. {listener.packets_received} packets received, "
              f"{pipeline.bad_packets} rejected, {pipeline.unknown_packets} unknown id.")


def _sessions(config_path: Path | None) -> None:
    cfg = load_config(config_path)
    rows = list_sessions(cfg.sessions_path)
    if not rows:
        print(f"No recorded sessions in {cfg.sessions_path}")
        return
    for r in rows:
        start = datetime.fromtimestamp(r["start_time_ns"] / 1e9).strftime("%Y-%m-%d %H:%M")
        track = f.TRACKS.get(r["track_id"], "?") if r["track_id"] is not None else "?"
        stype = f.SESSION_TYPES.get(r["session_type"], "?") if r["session_type"] is not None else "?"
        print(f"{start}  {track:<22} {stype:<18} {r['packets'] or 0:>8} packets  {r['file_path']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer", description="F1 25 race engineer")
    ap.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="listen live and print lap lines")
    run.add_argument("--no-record", dest="record", action="store_false", default=None,
                     help="don't record this session")
    sub.add_parser("sessions", help="list recorded sessions")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    if args.command == "run":
        try:
            asyncio.run(_run(args.config, args.record))
        except KeyboardInterrupt:
            pass
        except OSError as exc:
            print(f"Could not open the UDP port: {exc}. Is another app (or another copy of this "
                  f"one) already using it?", file=sys.stderr)
            return 1
    elif args.command == "sessions":
        _sessions(args.config)
    return 0
