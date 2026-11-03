"""The coaching session: every clean lap of yours against the fastest lap on track, kept so the
app window's Coaching card can show it live and the session can be summed up at the end.

The laps are logged by CornerAnalyzer (it has the traces and the fastest lap, `bench`) in every
tempo, so the card works whatever the engineer is saying. The spoken summary is coaching mode
only:

    "Session done. Best lap two tenths off Piastri, down from nine tenths on your first. Turn two
    sorted, three tenths found. Still to work on: turn seven, get on the power earlier."

When it's said: in a race on your last lap (the game stops sending right after the flag, so it
goes with the finish call); in practice and qualifying when the session ends.

Fixed and open corners (`summarize`):
- fixed: lost at least `min_loss_s` on two or more of the first half of the laps, and neither of
  the last two laps lost that much. "Found" is the drop in the average loss there.
- open: still losing `min_loss_s` on average over the last three laps; the biggest is named,
  with the cause that came up most.

Live play saves each summary to data/coaching/ (`save_dir`, set by cli; replays don't write),
and the driver report lists the latest ones.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from engineer.bus import events as ev
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.state.race_state import RaceState

log = logging.getLogger(__name__)

MIN_LAPS = 3                 # fewer coached laps than this: nothing to sum up
SHOW_LAPS = 6                # the card's trend per corner


@dataclass
class CoachCorner:
    number: int
    name: str
    delta_s: float                        # + = slower than the fastest lap there
    reasons: dict[str, float] = field(default_factory=dict)


@dataclass
class CoachLap:
    lap: int
    lap_ms: int
    gap_ms: int                           # to the fastest lap (negative: you're quickest)
    bench_name: str
    corners: list[CoachCorner]


@dataclass
class Summary:
    laps: int
    bench_name: str
    first_gap_ms: int
    best_gap_ms: int
    fixed: list[dict]                     # {"name", "found_s"}
    open: list[dict]                      # {"name", "loss_s", "reason": (kind, value) | None}


def summarize(laps: list[CoachLap], min_loss_s: float = 0.1) -> Summary | None:
    if len(laps) < MIN_LAPS:
        return None
    series: dict[int, list[CoachCorner]] = {}
    for lap in laps:
        for c in lap.corners:
            series.setdefault(c.number, []).append(c)
    half = max(2, len(laps) // 2)
    fixed, open_ = [], []
    for runs in series.values():
        early, late = runs[:half], runs[-2:]
        name = runs[-1].name
        lost_early = [r.delta_s for r in early if r.delta_s >= min_loss_s]
        if len(lost_early) >= 2 and all(r.delta_s < min_loss_s for r in late) \
                and len(runs) > half:
            found = sum(r.delta_s for r in early) / len(early) - sum(r.delta_s for r in late) / len(late)
            if found >= min_loss_s:
                fixed.append({"name": name, "found_s": round(found, 2)})
                continue
        last3 = runs[-3:]
        avg = sum(r.delta_s for r in last3) / len(last3)
        if avg >= min_loss_s:
            kinds = Counter(k for r in last3 for k in r.reasons)
            reason = None
            if kinds:
                kind = kinds.most_common(1)[0][0]
                vals = [r.reasons[kind] for r in last3 if kind in r.reasons]
                reason = (kind, sum(vals) / len(vals))
            open_.append({"name": name, "loss_s": round(avg, 2), "reason": reason})
    fixed.sort(key=lambda x: -x["found_s"])
    open_.sort(key=lambda x: -x["loss_s"])
    first, best = _gaps(laps)
    return Summary(len(laps), laps[-1].bench_name, first, best, fixed, open_)


def _gaps(laps: list[CoachLap]) -> tuple[int, int]:
    """Your first and best lap against the fastest lap at the END (it gets beaten during the
    session: Melbourne, 2 Nov, your 1:22.9 was quickest on lap 9, Russell's 1:22.2 came on 11)."""
    bench_ms = laps[-1].lap_ms - laps[-1].gap_ms
    timed = [lap.lap_ms for lap in laps if lap.lap_ms]
    if not timed:
        return laps[0].gap_ms, min(lap.gap_ms for lap in laps)
    return timed[0] - bench_ms, min(timed) - bench_ms


def _streak(laps: list[CoachLap], number: int, min_loss_s: float) -> int:
    n = 0
    for lap in reversed(laps):
        c = next((c for c in lap.corners if c.number == number), None)
        if c is None or c.delta_s < min_loss_s:
            break
        n += 1
    return n


def card(laps: list[CoachLap], focus_after: int = 2, min_loss_s: float = 0.1) -> dict:
    """The Live tab's Coaching card: the gap to the fastest lap, and each corner's loss on your
    last lap with its trend over the last few laps. Focus = a corner losing on a streak."""
    if not laps:
        return {"laps": 0}
    last = laps[-1]
    trend: dict[int, list[float]] = {}
    for lap in laps[-SHOW_LAPS:]:
        for c in lap.corners:
            trend.setdefault(c.number, []).append(round(c.delta_s, 3))
    s = summarize(laps, min_loss_s)
    fixed = {f["name"] for f in s.fixed} if s else set()
    # Focus: at most two corners, losing on a streak, biggest loss on the last lap first.
    streaking = [c for c in last.corners if _streak(laps, c.number, min_loss_s) >= focus_after]
    focus = {c.number for c in sorted(streaking, key=lambda c: -c.delta_s)[:2]}
    corners = []
    for c in last.corners:
        advice = phrases.advice(_main(c.reasons), c.reasons) if c.delta_s >= min_loss_s else ""
        corners.append({"name": c.name, "delta_s": round(c.delta_s, 3),
                        "trend": trend.get(c.number, []), "focus": c.number in focus,
                        "fixed": c.name in fixed, "advice": advice})
    first, best = _gaps(laps)
    return {"laps": len(laps), "bench": last.bench_name, "lap": last.lap,
            "lap_ms": last.lap_ms, "gap_ms": last.gap_ms, "best_gap_ms": best,
            "first_gap_ms": first, "corners": corners}


def _main(reasons: dict[str, float]) -> tuple[str, float] | None:
    from engineer.tools.queries import REASON_THRESHOLDS
    if not reasons:
        return None
    kind = max(reasons, key=lambda k: reasons[k] / REASON_THRESHOLDS[k])
    return kind, reasons[kind]


class CoachingSummary:
    """Says and saves the summary at the end of the session. Wired in build_engine after the
    race-craft analyzer, so in a race the finish call is said first."""

    def __init__(self, bus: ev.EventBus, state: RaceState, corners, director: RadioDirector):
        self.state, self.corners, self.director = state, corners, director
        self.save_dir: Path | None = None             # live only (cli); replays don't write
        self.done = False
        self.last: Summary | None = None
        bus.subscribe(ev.LapCompleted, self._on_lap)
        bus.subscribe(ev.SessionEnded, self._on_session_ended)
        bus.subscribe(ev.SessionStarted, lambda e: setattr(self, "done", False))

    def _on_lap(self, e: ev.LapCompleted) -> None:
        s = self.state.session
        if e.car_idx == self.state.player_index and s.is_race and s.total_laps \
                and e.lap >= s.total_laps:
            self._finish(at_end=False)

    def _on_session_ended(self, e: ev.SessionEnded) -> None:
        self._finish(at_end=True)

    def _finish(self, at_end: bool) -> None:
        if self.done:
            return
        summary = summarize(self.corners.coach_laps, self.corners.cfg.min_loss_s)
        if summary is None:
            return
        self.done, self.last = True, summary
        self._save(summary)
        if not self.corners.coaching:
            return
        self.director.submit(RadioCall(topic="coaching_summary", text=summary_phrase(summary),
                                       priority=Priority.HIGH, max_wait_s=30.0,
                                       wait_for_straight=False))
        if at_end:
            self.director.release_now()          # no more packets will come to release it

    def _save(self, summary: Summary) -> None:
        if self.save_dir is None:
            return
        s = self.state.session
        data = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "track": s.track_name,
                "session": s.session_name, **asdict(summary)}
        path = self.save_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"

        def write():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, indent=1), encoding="utf-8")
            except OSError as exc:
                log.warning("could not save the coaching summary %s (%s)", path, exc)
        threading.Thread(target=write, daemon=True).start()


def summary_phrase(s: Summary) -> str:
    """'Session done. Best lap two tenths off Piastri, down from nine tenths on your first.
    Turn two sorted, three tenths found. Still to work on: turn seven, get on the power a bit
    earlier.'"""
    out = ["Session done."]
    if s.best_gap_ms < 0:
        best = "you had the fastest lap on track"
    else:
        best = f"best lap {phrases.say_gap(s.best_gap_ms)} off {s.bench_name}"
    if s.first_gap_ms - s.best_gap_ms >= 100 and s.best_gap_ms >= 0:
        best += f", down from {phrases.say_gap(s.first_gap_ms)} on your first"
    out.append(phrases._sentence(best))
    for f in s.fixed[:2]:
        out.append(phrases._sentence(f"{f['name']} sorted",
                                     f"{phrases.say_gap(int(f['found_s'] * 1000))} found"))
    if s.open:
        o = s.open[0]
        why = phrases.advice(tuple(o["reason"]) if o["reason"] else None, None, most=1)
        out.append(phrases._sentence(f"still to work on: {o['name']}", why))
    elif not s.fixed:
        out.append(phrases._sentence("no one corner stands out, it's spread around the lap"))
    return " ".join(out)


def load_summaries(folder: Path, most: int = 5) -> list[dict]:
    """The latest saved summaries, newest first (for the driver report)."""
    out = []
    for p in sorted(folder.glob("*.json"), reverse=True)[:most]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out
