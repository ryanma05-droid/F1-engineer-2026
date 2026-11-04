"""Coaching focus: what the driver report found, handed to the live engineer.

The report (insights.py) combines every recording: per corner, how much time you lose to the
quickest AI there, and the habits ("themes") behind it. Live coaching (analyzers/corners.py) only
sees this session, so on lap two it can't tell your usual weak corner from a one-off mistake.
Each time the report is built it also writes data/reports/focus.json:

- per track: up to FOCUS_CORNERS corners where you usually lose the most (at least
  FOCUS_MIN_LOSS_S per lap, with at least FOCUS_MIN_LAPS laps of evidence), each with the habit it
  belongs to (the costliest theme that picked that corner) and the live cause that habit shows up
  as (REASON_FOR_THEME);
- your top habits overall, with a short radio version of the advice.

Live, those corners are coached ahead of one-off losses from the first lap, the engineer says
what you're working on at the start of a session, and says it once when a report corner is level
with the fastest car on track. Nothing here is fixed per track: it's the report's numbers, which
follow your newest sessions.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from engineer.report.insights import THEMES, Report

log = logging.getLogger(__name__)

FOCUS_CORNERS = 3
FOCUS_MIN_LOSS_S = 0.05
FOCUS_MIN_LAPS = 4
FOCUS_HABITS = 2
FILE_NAME = "focus.json"

# The live cause (tools/queries.py CornerDelta.reasons) each report habit shows up as. None: no
# single live cause (coasting has no live measurement yet).
REASON_FOR_THEME = {
    "exit_giveback": ("throttle_late", "exit_speed"),
    "slow_throttle": ("throttle_late",),
    "early_braking": ("brake_early",),
    "over_slowing": ("min_speed",),
    "coasting": (),
    "straight_exits": ("exit_speed",),
}

# Said on the radio: short, an instruction, not the report's paragraph.
RADIO_HABIT = {
    "exit_giveback": "the exits: brake a touch earlier and get to full throttle sooner",
    "slow_throttle": "the throttle: squeeze it to full quicker once the car is straight",
    "early_braking": "braking: move the braking point a few metres later",
    "over_slowing": "apex speed: come off the brake earlier and carry more speed",
    "coasting": "the pedals: straight from brake to throttle, no coasting",
    "straight_exits": "exits onto the straights: a clean, early exit",
}


@dataclass(frozen=True)
class FocusCorner:
    number: int
    loss_s: float                 # usual loss per lap to the quickest AI (report)
    laps: int
    theme: str | None             # the habit it belongs to (key in THEMES)
    reasons: tuple[str, ...]      # live causes that habit shows up as


@dataclass
class CoachingFocus:
    generated: str | None = None
    corners: dict[int, list[FocusCorner]] = field(default_factory=dict)   # track_id -> worst first
    habits: list[dict] = field(default_factory=list)   # {"key", "title", "cost_s", "radio"}

    def for_track(self, track_id: int | None) -> list[FocusCorner]:
        return list(self.corners.get(track_id, [])) if track_id is not None else []

    @property
    def empty(self) -> bool:
        return not self.corners and not self.habits

    def to_json(self) -> dict:
        return {"generated": self.generated,
                "tracks": {str(t): [c.__dict__ | {"reasons": list(c.reasons)} for c in cs]
                           for t, cs in self.corners.items()},
                "habits": self.habits}

    @classmethod
    def from_json(cls, d: dict) -> CoachingFocus:
        return cls(d.get("generated"),
                   {int(t): [FocusCorner(c["number"], c["loss_s"], c["laps"], c.get("theme"),
                                         tuple(c.get("reasons", ()))) for c in cs]
                    for t, cs in d.get("tracks", {}).items()},
                   list(d.get("habits", [])))


def focus_from_report(report: Report) -> CoachingFocus:
    # Which habit picked each corner: the costliest theme that selected it.
    theme_of: dict[tuple[int, int], str] = {}
    for t in report.themes:                                   # costliest first
        _text, costs = THEMES[t.key](report.corners)
        for c, cost in costs:
            if cost > 0:
                theme_of.setdefault((c.track_id, c.number), t.key)
    by_track: dict[int, list[FocusCorner]] = {}
    for c in report.corners:
        loss = c.dv("time_s")
        if loss < FOCUS_MIN_LOSS_S or c.laps < FOCUS_MIN_LAPS:
            continue
        theme = theme_of.get((c.track_id, c.number))
        by_track.setdefault(c.track_id, []).append(FocusCorner(
            c.number, round(loss, 3), c.laps, theme, REASON_FOR_THEME.get(theme, ())))
    corners = {t: sorted(cs, key=lambda c: -c.loss_s)[:FOCUS_CORNERS]
               for t, cs in by_track.items()}
    habits = [{"key": t.key, "title": t.title, "cost_s": round(t.cost_s, 3),
               "radio": RADIO_HABIT.get(t.key, t.title.lower())}
              for t in report.themes[:FOCUS_HABITS]]
    return CoachingFocus(report.generated.isoformat(), corners, habits)


def save_focus(focus: CoachingFocus, reports_dir: Path) -> Path:
    path = Path(reports_dir) / FILE_NAME
    path.write_text(json.dumps(focus.to_json(), indent=1), encoding="utf-8")
    return path


def load_focus(reports_dir: Path) -> CoachingFocus | None:
    """None when no report has been built yet (or the file is unreadable). The test suite
    switches it off (RACE_ENGINEER_REPORT_FOCUS=0) so a local report can't change its calls."""
    if os.environ.get("RACE_ENGINEER_REPORT_FOCUS") == "0":
        return None
    path = Path(reports_dir) / FILE_NAME
    try:
        return CoachingFocus.from_json(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("could not read the coaching focus %s: %s", path, exc)
        return None
