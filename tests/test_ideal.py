"""The rolling ideal lap (tools/ideal.py): your last 5 clean laps this stint, corner segments,
older laps corrected for the stint's pace trend. Not your fastest lap of the race (4 Nov)."""

from datetime import UTC, datetime

import pytest

from engineer.radio import phrases
from engineer.report.insights import ideal_gaps
from engineer.state.track_maps import TrackMap, detect_corners
from engineer.tools.ideal import (
    LapSegments,
    rolling_ideal,
    segment_times,
    segments,
    stint_trend,
)
from engineer.voice.intents import match
from tests.synth import FakeRace
from tests.test_corners import CORNERS, profile, run, trace

NAMES, NUMBERS = ["run to turn one", "turn one", "turn two"], [None, 1, 2]


def lap(n, a, b, c):
    return LapSegments(n, int(round((a + b + c) * 1000)), [a, b, c])


def test_segments_add_up_to_the_lap():
    s, b, t = profile()
    tr = trace(s, b, t)
    tmap = TrackMap(10, 2000.0, detect_corners([tr], 2000.0))
    segs = segments(tmap)
    assert [x.name for x in segs] == ["run to turn one", "turn one", "turn two"]
    lap_ms = int(round(tr.time_s[-1] * 1000)) + 20
    times = segment_times(tr, lap_ms, segs)
    assert sum(times) == pytest.approx(lap_ms / 1000, abs=1e-6) and min(times) > 0


def test_best_corners_of_the_last_five_laps_on_todays_tyres():
    # Laps slow by 0.3 s each (tyres fading). Lap 1 has the quickest raw turn one, but four laps
    # of fade later it isn't: the trend moves it to "as if driven now".
    laps = [lap(1, 10.0, 20.0, 30.0), lap(2, 10.1, 20.1, 30.1), lap(3, 10.2, 20.2, 30.2),
            lap(4, 10.3, 20.3, 30.3), lap(5, 10.4, 20.4, 30.4), lap(6, 10.5, 20.1, 30.5)]
    assert stint_trend(laps) == pytest.approx(0.25, abs=0.06)
    i = rolling_ideal(laps, NAMES, NUMBERS)
    assert i.laps == (2, 3, 4, 5, 6)                       # the last five
    assert i.best_lap[2] != 2                              # lap 2's raw best isn't today's
    assert i.best_lap[1] == 6                              # lap 6's turn one is a real gain
    assert i.last_lap == 6 and i.gap_ms > 0
    # Without the correction, the fastest lap of the race would make lap 6 look 1.1 s off.
    assert i.gap_ms < 600
    assert i.losses()[0][0] in ("turn two", "run to turn one")
    assert rolling_ideal(laps[:2], NAMES, NUMBERS) is None  # three laps needed


def test_live_ideal_lap_and_the_radio(tmp_path):
    race = FakeRace({0: [30000, 30100, 30200, 30300, 30400, 30500, 30600]}, corners=CORNERS,
                    extra_packets=False)
    eng, _ = run(race, tmp_path)
    i = eng.corners.ideal
    assert i is not None and len(i.laps) == 5 and 1 not in i.laps   # race lap 1 isn't clean
    assert abs(i.gap_ms) < 60                              # evenly fading laps: right on it
    card = eng.corners.ideal_card()
    assert card["ms"] == i.ms and card["laps"] == list(i.laps)
    for q in ("What's my ideal lap?", "theoretical best", "best possible lap time"):
        assert match(q).name == "ideal_lap", q
    assert match("what's my best lap").name == "best_lap"
    assert eng.answers._a_ideal_lap().startswith("Ideal lap ")


def test_getting_quicker_is_credited_only_as_fuel():
    """Laps 0.2 s quicker each time is you learning the track: older laps aren't moved by more
    than fuel burn (0.05 s/lap)."""
    laps = [lap(n, 10.0 - 0.03 * n, 20.0 - 0.07 * n, 30.0 - 0.1 * n) for n in range(2, 7)]
    assert stint_trend(laps) == -0.05


def test_phrases():
    assert phrases.ideal_phrase(300, "turn four") == "Three tenths off your ideal, most of it turn four."
    assert phrases.ideal_answer_phrase(89400, 5, 300, "turn four") == (
        "Ideal lap one twenty nine four, from your last five laps. Three tenths off it on that lap, "
        "most of it turn four.")
    assert phrases.ideal_answer_phrase(89400, 3, 20, None).endswith("That lap was right on it.")


def test_report_uses_the_rolling_ideal_not_the_session_best():
    # Every segment fades by the same share each lap (0.2 %, 0.12 s/lap): a steady driver.
    laps = [{"lap": n, "stint": 1, "lap_ms": int(round(60 * (1 + 0.002 * n) * 1000)),
             "times": [x * (1 + 0.002 * n) for x in (10.0, 20.0, 30.0)]} for n in range(2, 9)]
    entry = {"recorded_at": "2026-11-03T00:00:00+00:00", "track_id": 2,
             "my_segments": {"names": NAMES, "numbers": NUMBERS, "laps": laps}}
    gap, corners = ideal_gaps([entry], datetime(2026, 11, 4, tzinfo=UTC))
    assert gap < 0.05                                      # steady fade = consistent
    assert set(corners) == {(2, 1), (2, 2)} and all(v < 0.05 for v in corners.values())
