"""Coaching mode's session log: the Live tab card, the end-of-session summary, the report."""

import json
from datetime import datetime, timezone

from engineer.analyzers.coaching import (CoachCorner, CoachLap, card, load_summaries,
                                         summarize, summary_phrase)
from engineer.report.insights import build_report
from engineer.report.render import render_html
from tests.test_corners import CORNERS, run
from tests.synth import FakeRace


def _laps():
    """Eight laps vs Piastri: turn two loses three tenths for four laps then stops (fixed);
    turn seven keeps losing (open, late on the throttle); turn three is fine."""
    laps = []
    for i in range(8):
        two = 0.3 if i < 4 else 0.0
        laps.append(CoachLap(3 + i, 84000 - 50 * i, 800 - 50 * i, "Piastri", [
            CoachCorner(2, "turn two", two, {"brake_early": 15} if two else {}),
            CoachCorner(3, "turn three", 0.02),
            CoachCorner(7, "turn seven", 0.25, {"throttle_late": 40})]))
    return laps


def test_summary_finds_fixed_and_open_corners():
    s = summarize(_laps())
    assert s.laps == 8 and s.bench_name == "Piastri"
    assert s.first_gap_ms == 800 and s.best_gap_ms == 450
    assert s.fixed == [{"name": "turn two", "found_s": 0.3}]
    assert [o["name"] for o in s.open] == ["turn seven"]
    assert summary_phrase(s) == (
        "Session done. Best lap five tenths off Piastri, down from eight tenths on your first. "
        "Turn two sorted, three tenths found. Still to work on: turn seven, get on the power a "
        "bit earlier.")
    assert summarize(_laps()[:2]) is None                 # too few laps to sum up


def test_card_marks_focus_and_fixed():
    c = card(_laps())
    rows = {r["name"]: r for r in c["corners"]}
    assert c["bench"] == "Piastri" and c["laps"] == 8 and c["gap_ms"] == 450
    assert rows["turn seven"]["focus"] and rows["turn seven"]["advice"] == \
        "get on the power a bit earlier"
    assert rows["turn two"]["fixed"] and not rows["turn two"]["focus"]
    assert len(rows["turn seven"]["trend"]) == 6
    assert card([]) == {"laps": 0}


def test_gaps_are_against_the_fastest_lap_at_the_end():
    # Lap 9 was quickest on track when set; a faster lap came later. The summary measures your
    # best against the final fastest lap, not the one at the time (Melbourne, 2 Nov).
    laps = [CoachLap(8, 84000, 750, "Piastri", []), CoachLap(9, 82900, -350, "Piastri", []),
            CoachLap(10, 83500, 1300, "Russell", [])]
    s = summarize(laps)
    assert s.best_gap_ms == 700 and s.first_gap_ms == 1800 and s.bench_name == "Russell"
    assert summary_phrase(s).startswith("Session done. Best lap seven tenths off Russell")


def test_summary_said_at_the_flag_in_coaching_mode_and_saved(tmp_path):
    race = FakeRace({0: [31000] * 5 + [31300], 1: [30000] * 6}, corners=CORNERS,
                    positions={0: 2, 1: 1},
                    gap_ahead_ms={0: 5000}, names=["PLAYER", "NORRIS"], extra_packets=False,
                    chequered_flag=True)
    eng, radio = run(race, tmp_path, verbosity="coaching", duration_ms=260000)
    said = [r for r in radio if r.startswith("Session done.")]
    assert said and "off Norris" in said[0]
    assert len(eng.corners.coach_laps) >= 3
    # Not in other tempos (the card still has the laps).
    eng2, radio2 = run(race, tmp_path / "n", verbosity="normal", duration_ms=260000)
    assert not any(r.startswith("Session done.") for r in radio2) and eng2.corners.coach_laps


def test_saved_summaries_go_into_the_report(tmp_path):
    folder = tmp_path / "coaching"
    folder.mkdir()
    s = summarize(_laps())
    data = {"date": "2026-11-03T20:00:00", "track": "Melbourne", "session": "Race",
            **s.__dict__}
    (folder / "20261103-200000.json").write_text(json.dumps(data))
    loaded = load_summaries(folder)
    assert loaded[0]["track"] == "Melbourne"
    report = build_report([], datetime.now(timezone.utc))
    report.coaching = loaded
    html = render_html(report)
    assert "Coaching sessions" in html and "turn two" in html and "turn seven" in html
