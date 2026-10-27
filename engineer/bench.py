"""Benchmark: time per packet for the whole engine (parse, race state, events, lap features,
traces, radio analyzers and director), i.e. everything live play does on the main loop.

    python -m engineer.bench data/sessions/<file>.zst
    python -m engineer.bench --synthetic          # no recording needed (uses the test generator)

Packets are loaded into memory first, so decompression and disk speed are not counted. Each
`Pipeline.feed` call is timed individually. Target from the kickoff: average under 1 ms.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from engineer.ingest.formats import f1_2025 as f
from engineer.ingest.recorder import read_recording
import io

from engineer.app import build_engine
from engineer.config import Config
from engineer.features.priors import WearPriors

TARGET_MS = 1.0


def _synthetic():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tests.synth import FakeRace  # noqa: E402  (test helper, only for this mode)

    cars = {i: [90000 + 137 * i + 211 * lap for lap in range(3)] for i in range(20)}
    return list(FakeRace(cars).frames())


def run(records) -> dict:
    import tempfile   # a scratch data folder, so a learned track map isn't saved anywhere real
    cfg = Config(data_dir=tempfile.mkdtemp(prefix="engineer-bench-"))
    pipeline = build_engine(cfg, out=io.StringIO(), priors=WearPriors()).pipeline
    per_type: dict[int, list[int]] = defaultdict(list)
    all_ns = []
    perf = time.perf_counter_ns
    for t_ns, raw in records:
        start = perf()
        pipeline.feed(raw, t_ns)
        dt = perf() - start
        all_ns.append(dt)
        per_type[raw[6] if len(raw) > 6 else -1].append(dt)
    return {"all": all_ns, "per_type": per_type, "pipeline": pipeline}


def _fmt_us(ns: float) -> str:
    return f"{ns / 1000:8.1f} us"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engineer.bench")
    ap.add_argument("file", nargs="?", type=Path, help="recording (.zst)")
    ap.add_argument("--synthetic", action="store_true", help="benchmark a generated 20-car race")
    args = ap.parse_args(argv)
    if not args.file and not args.synthetic:
        ap.error("give a recording file or --synthetic")

    print("Loading packets...", flush=True)
    records = _synthetic() if args.synthetic else list(read_recording(args.file))
    if not records:
        print("No packets.")
        return 1
    res = run(records)
    all_ns = sorted(res["all"])
    mean = statistics.fmean(all_ns)
    p = lambda q: all_ns[min(len(all_ns) - 1, int(q * len(all_ns)))]  # noqa: E731

    print(f"\n{len(all_ns)} packets")
    print(f"  mean {_fmt_us(mean)}   p50 {_fmt_us(p(0.5))}   p99 {_fmt_us(p(0.99))}   "
          f"max {_fmt_us(all_ns[-1])}")
    print("\nBy packet type (mean, count):")
    for pid, times in sorted(res["per_type"].items(), key=lambda kv: -statistics.fmean(kv[1])):
        name = f.PACKETS.get(pid, (f"id {pid}",))[0]
        print(f"  {name:<20} {_fmt_us(statistics.fmean(times))}  {len(times):>8}")
    ok = mean / 1e6 < TARGET_MS
    print(f"\nTarget: mean < {TARGET_MS} ms -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
