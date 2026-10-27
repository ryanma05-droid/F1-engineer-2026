"""Replay a recorded session through the same pipeline as live.

    python -m engineer.ingest.replay data/sessions/<file>.zst --speed max
    python -m engineer.ingest.replay <file> --speed 1           # real time
    python -m engineer.ingest.replay <file> --speed 4           # 4x
    python -m engineer.ingest.replay <file> --udp               # resend to 127.0.0.1:20777
    python -m engineer.ingest.replay <file> --voice             # real time, with the radio voice
    python -m engineer.ingest.replay <file> --questions         # ask the logged push-to-talk
                                                                # questions again, at the same time

In-process (default), packets go straight into `Pipeline.feed`, exactly as the listener would call
it, and the lap lines are printed. With --udp, packets are sent over UDP instead, so a separately
running `python -m engineer run` (or SimHub) receives them as if the game were sending.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import time
from pathlib import Path
from typing import Iterable

from engineer.app import Engine, build_engine
from engineer.bus import events as ev
from engineer.config import Config, load_config
from engineer.ingest.recorder import read_recording

log = logging.getLogger(__name__)


def paced(records: Iterable[tuple[int, bytes]], speed: float | None
          ) -> Iterable[tuple[int, bytes]]:
    """Yield records, sleeping to reproduce the original timing divided by `speed`.
    speed=None means as fast as possible."""
    start_wall = None
    t0 = None
    for t_ns, raw in records:
        if speed is not None:
            if t0 is None:
                t0, start_wall = t_ns, time.perf_counter()
            due = start_wall + (t_ns - t0) / 1e9 / speed
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        yield t_ns, raw


def load_questions(log_path: Path) -> list[dict]:
    """Push-to-talk questions from data/ptt/questions.jsonl (follow-up lines skipped)."""
    if not log_path.exists():
        return []
    out = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            q = json.loads(line)
        except ValueError:
            continue
        if q.get("heard") and q.get("session_uid") and q.get("session_time") is not None:
            out.append(q)
    return out


def replay(path: Path | str, speed: float | None = None, out=None,
           cfg: Config | None = None, engine: Engine | None = None,
           questions: list[dict] | None = None) -> Engine:
    """Replay in-process through the same engine as live. Returns the engine for inspection.

    questions: logged push-to-talk questions; each is asked again when the replay reaches the
    session time it was asked at (only those from this recording's session)."""
    engine = engine or build_engine(cfg or load_config(), out=out)
    feed = engine.pipeline.feed
    state, bus = engine.pipeline.state, engine.pipeline.bus
    todo = sorted(questions or [], key=lambda q: q["session_time"])
    for t_ns, raw in paced(read_recording(path), speed):
        feed(raw, t_ns)
        while todo and state.session_uid is not None:
            uid = f"{state.session_uid:016x}"
            mine = [q for q in todo if q["session_uid"] == uid]
            if not mine or (state.session_time or 0) < mine[0]["session_time"]:
                break
            q = mine[0]
            todo.remove(q)
            bus.publish(ev.QuestionAsked(state.session_uid, state.session_time or 0.0,
                                         text=q["heard"], audio_path=q.get("audio")))
    return engine


def replay_udp(path: Path | str, host: str, port: int, speed: float | None) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = 0
    try:
        for _t, raw in paced(read_recording(path), speed):
            sock.sendto(raw, (host, port))
            n += 1
    finally:
        sock.close()
    return n


def _parse_speed(text: str) -> float | None:
    if text.lower() == "max":
        return None
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("speed must be > 0 or 'max'")
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.ingest.replay",
                                 description="Replay a recorded F1 25 session.")
    ap.add_argument("file", type=Path, help="recording (.zst) from data/sessions/")
    ap.add_argument("--speed", type=_parse_speed, default=1.0,
                    help="playback speed: 1 = real time (default), 4 = 4x, max = no waiting")
    ap.add_argument("--udp", action="store_true",
                    help="resend packets over UDP instead of replaying in-process")
    ap.add_argument("--host", default="127.0.0.1", help="UDP target host (with --udp)")
    ap.add_argument("--port", type=int, default=20777, help="UDP target port (with --udp)")
    ap.add_argument("--voice", action="store_true",
                    help="speak the radio calls (use with --speed 1 to hear them in real time)")
    ap.add_argument("--verbosity", choices=["terse", "normal", "chatty"],
                    help="override radio.verbosity from config.yaml")
    ap.add_argument("--questions", action="store_true",
                    help="ask the push-to-talk questions logged for this session again")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not args.file.exists():
        print(f"No such recording: {args.file}", file=sys.stderr)
        return 2
    try:
        if args.udp:
            if args.speed is None:
                log.warning("--speed max over UDP can overflow the receiver's buffer and drop "
                            "packets; use a finite speed for faithful results")
            n = replay_udp(args.file, args.host, args.port, args.speed)
            print(f"Sent {n} packets to {args.host}:{args.port}")
        else:
            cfg = load_config()
            if args.verbosity:
                cfg.radio.verbosity = args.verbosity
            questions = None
            if args.questions:
                questions = load_questions(cfg.ptt_path / "questions.jsonl")
                cfg.voice.log = False            # don't log the replayed questions twice
            engine = build_engine(cfg)
            audio = None
            if args.voice:
                from engineer.cli import start_voice
                if args.speed is None:
                    log.warning("--voice with --speed max: calls will pile up; use --speed 1")
                audio = start_voice(cfg, engine)
            try:
                p = replay(args.file, args.speed, engine=engine, questions=questions).pipeline
            finally:
                if audio:
                    audio.close(timeout_s=30)
            print(f"Replayed {p.packets} packets "
                  f"({p.unknown_packets} unknown id, {p.bad_packets} rejected)")
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
