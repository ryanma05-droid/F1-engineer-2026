import io
import json
import re

import numpy as np
import pytest

from engineer.app import build_engine
from engineer.config import Config, Corners
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.state.track_maps import Corner, TrackMap, TrackMaps, detect_corners
from engineer.state.traces import BIN_SIZE_M, LapTrace
from engineer.tools.queries import corner_deltas, corner_stats
from tests.synth import FakeRace

LAP = 5000.0
CORNERS = [0.3, 0.7]          # apexes at 1500 m and 3500 m


def run(race, tmp_path, corners: Corners | None = None, verbosity="normal", **frames_kw):
    cfg = Config(data_dir=tmp_path, corners=corners or Corners())
    cfg.radio.verbosity = verbosity
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors())
    frames_kw.setdefault("duration_ms", sum(max(race.lap_times.values(), key=sum)) + 12000)
    for t_ns, raw in race.frames(**frames_kw):
        eng.pipeline.feed(raw, t_ns)
    radio = [line.split("s  ", 1)[1].split("  (held")[0]
             for line in out.getvalue().splitlines() if line.startswith("RADIO")]
    return eng, radio


def trace(speed, brake=None, throttle=None, lap_time=60.0, lap=1):
    n = len(speed)
    x = np.arange(n) * BIN_SIZE_M
    # time from speed: each 5 m section takes 5 / v seconds
    v = np.maximum(np.asarray(speed, float), 1) / 3.6
    t = np.concatenate([[0], np.cumsum(BIN_SIZE_M / v[:-1])])
    return LapTrace(0, lap, x, t, np.asarray(speed, float),
                    np.ones(n) if throttle is None else np.asarray(throttle, float),
                    np.zeros(n) if brake is None else np.asarray(brake, float),
                    np.zeros(n), np.full(n, 7.0), 1.0)


def profile(n=400, apexes=(100, 300), drop=150, kink_at=None, brake_shift=0):
    """Speed 300 with dips; braking in the 20 sections before each apex."""
    speed = np.full(n, 300.0)
    brake = np.zeros(n)
    throttle = np.ones(n)
    for a in apexes:
        for i in range(n):
            d = abs(i - a)
            if d < 30:
                speed[i] = min(speed[i], 300 - drop * (1 - d / 30))
        brake[a - 20 - brake_shift:a] = 0.8
        throttle[a - 20 - brake_shift:a + 10] = 0.0
    if kink_at is not None:
        speed[kink_at - 5:kink_at + 5] -= 8           # a flat-out kink: not a corner
    return speed, brake, throttle


def test_detect_corners_ignores_kinks():
    s, b, t = profile(kink_at=200)
    corners = detect_corners([trace(s, b, t)], track_length_m=400 * BIN_SIZE_M)
    assert [c.name for c in corners] == ["turn one", "turn two"]
    assert corners[0].apex_m == pytest.approx(100 * BIN_SIZE_M, abs=10)
    assert corners[0].entry_m < corners[0].apex_m < corners[0].exit_m <= corners[1].entry_m


def test_corner_delta_finds_early_braking():
    s, b, t = profile()
    ref = trace(s, b, t)
    s2 = s.copy()
    s2[60:96] = s[60:96] - 20                # slower into turn one, same minimum speed
    mine = trace(s2, *profile(brake_shift=6)[1:])
    tmap = TrackMap(10, 2000.0, detect_corners([ref], 2000.0))
    d1, d2 = corner_deltas(mine, ref, tmap)
    assert d1.delta_s > 0.1 and abs(d2.delta_s) < 0.01
    assert d1.reason() == ("brake_early", 30.0)
    st = corner_stats(ref, tmap.corners[0])
    assert st.min_speed_kph == pytest.approx(150, abs=6)


def test_phrases():
    assert phrases.corner_phrase(0.21, "turn three", "ahead", ("brake_early", 10)) == \
        "Turn three, losing two tenths to the car ahead, braking ten metres earlier."
    assert phrases.corner_phrase(0.3, "turn four", "best", ("min_speed", 8.2)) == \
        "Turn four, three tenths off your best, minimum speed eight k p h down."
    assert phrases.corner_phrase(0.3, "turn four", "best", ("min_speed", 8), "terse") == \
        "Turn four, three tenths off your best."
    assert phrases.corner_phrase(-0.12, "turn nine", "ahead") == \
        "Gaining a tenth on the car ahead through turn nine."
    assert phrases.corner_phrase(0.5, "turn one", "behind", ("throttle_late", 42)) == \
        "Turn one, losing five tenths to the car behind, full throttle forty metres later."
    assert phrases.corner_phrase(0.4, "turn four", "best", None, again=True) == \
        "Turn four again, four tenths off your best."


def test_traces_match_lap_times(tmp_path):
    race = FakeRace({0: [30000, 31000, 32000]}, corners=CORNERS, extra_packets=False)
    eng, _ = run(race, tmp_path)
    for lap, ms in ((2, 31000), (3, 32000)):
        tr = eng.traces.trace_for(0, lap)
        assert tr.usable
        assert tr.time_s[int(4900 // BIN_SIZE_M)] == pytest.approx(ms / 1000 * 0.98, abs=0.05)


def test_learns_and_saves_corner_map_then_coaches_vs_best(tmp_path):
    race = FakeRace({0: [30000, 30000, 30000, 33000]}, corners=CORNERS, extra_packets=False)
    eng, radio = run(race, tmp_path)
    saved = json.loads((tmp_path / "tracks" / "spa.json").read_text())   # FakeRace track 10
    assert [c["name"] for c in saved["corners"]] == ["turn one", "turn two"]
    # FakeRace slows a lap evenly everywhere, so which corner loses most depends on its length.
    [call] = [c for c in radio if c.startswith("Turn")]
    assert re.fullmatch(r"Turn (one|two), \w+ tenths off your pace\.", call)


def test_coaches_vs_car_ahead_within_gap(tmp_path):
    # Player (car 0) is P2 behind car 1; lap 4 is much slower in the second half.
    race = FakeRace({0: [31000, 31000, 31000, 34000], 1: [30500] * 4}, corners=CORNERS,
                    positions={0: 2, 1: 1}, extra_packets=False)
    eng, radio = run(race, tmp_path)
    [call] = [c for c in radio if c.startswith("Turn")]
    assert re.fullmatch(r"Turn (one|two), losing \w+ tenths to the car ahead\.", call)
    assert eng.corners.last_against == "ahead"


def test_existing_map_file_is_used(tmp_path):
    tmap = TrackMap(10, LAP, [Corner(5, "turn five", 1300.0, 1500.0, 1700.0)])
    TrackMaps(tmp_path / "tracks").save(tmap)
    race = FakeRace({0: [30000, 30000, 33000]}, corners=CORNERS, extra_packets=False)
    _eng, radio = run(race, tmp_path)
    assert [c for c in radio if c.startswith("Turn")] == \
        ["Turn five, two tenths off your pace."]


def test_corner_calls_can_be_switched_off(tmp_path):
    race = FakeRace({0: [30000, 30000, 30000, 33000]}, corners=CORNERS, extra_packets=False)
    _eng, radio = run(race, tmp_path, Corners(enabled=False))
    assert not [c for c in radio if c.startswith("Turn")]


def test_a_corner_lost_on_consecutive_laps_gets_priority_and_says_so(tmp_path):
    from engineer.radio.director import Priority

    eng, _ = run(FakeRace({0: [30000]}, corners=CORNERS, extra_packets=False), tmp_path,
                 duration_ms=1000)
    ca = eng.corners
    speed, brake, throttle = profile()
    ref = trace(speed, brake, throttle)
    ca.track_map = TrackMap(10, 2000.0, detect_corners([ref], 2000.0))
    said = []
    ca.director.submit = lambda call: said.append((call.text, call.priority))

    def lap(n, speed_drop_second=0.0):
        sp = speed.copy()
        sp[80:120] -= 20                            # turn one slower every lap (the habit)
        sp[280:320] -= speed_drop_second            # a one-off bigger loss in turn two
        ca._compare(trace(sp, brake, throttle, lap=n), ref, "best")

    lap(2)
    assert said[-1][1] == Priority.LOW and "running" not in said[-1][0]
    lap(3)
    text, prio = said[-1]
    assert prio == Priority.NORMAL and "second lap running" in text
    # Lap 4: a one-off bigger loss in the second corner doesn't steal the call from the habit.
    lap(4, speed_drop_second=60.0)
    text, prio = said[-1]
    assert prio == Priority.NORMAL and text.startswith("Turn one, third lap running")
    assert ca.streak(1) == 3 and ca.streak(2) == 1


def test_streak_phrase():
    assert phrases.corner_phrase(0.2, "turn four", "best", ("brake_early", 10.0), streak=3) == \
        "Turn four, third lap running, two tenths off your best, braking ten metres earlier."
    assert phrases.corner_phrase(0.2, "turn four", "ahead", streak=7) == \
        "Turn four, lap after lap, losing two tenths to the car ahead."


def _habit_rig(tmp_path):
    """A corner analyzer with a two-corner map, a reference lap, and a list of what it says."""
    eng, _ = run(FakeRace({0: [30000]}, corners=CORNERS, extra_packets=False), tmp_path,
                 duration_ms=1000)
    ca = eng.corners
    speed, brake, throttle = profile()
    ref = trace(speed, brake, throttle)
    ca.track_map = TrackMap(10, 2000.0, detect_corners([ref], 2000.0))
    said = []
    ca.director.submit = lambda call: said.append(call.text)

    def lap(n, drop_one=0.0, drop_two=0.0, against="pace"):
        sp = speed.copy()
        sp[80:120] -= drop_one
        sp[280:320] -= drop_two
        before = len(said)
        ca._compare(trace(sp, brake, throttle, lap=n), ref, against)
        return said[-1] if len(said) > before else None
    return ca, lap


def test_long_habit_backs_off_to_every_other_lap_unless_worse(tmp_path):
    ca, lap = _habit_rig(tmp_path)
    calls = [lap(n, drop_one=20) for n in range(2, 9)]
    # Called laps 2, 3, 4 (third lap running), then every other lap: 6, 8.
    assert [c is not None for c in calls] == [True, True, True, False, True, False, True]
    assert calls[2].startswith("Turn one, third lap running")
    # Getting worse is always said.
    assert lap(9, drop_one=60) is not None


def test_better_only_after_a_habit_and_with_your_own_times(tmp_path):
    ca, lap = _habit_rig(tmp_path)
    lap(2, drop_two=20)                       # a one-off loss in turn two...
    assert lap(3) is None                     # ...then fine: no "better" (it's just noise)
    lap(4, drop_one=20)
    assert "second lap running" in lap(5, drop_one=20)
    text = lap(6)                             # the habit is fixed: said once
    assert text.startswith("Turn one better, ") and text.endswith(" found.")
    assert lap(7) is None


def test_no_coaching_vs_your_pace_in_a_fight_but_vs_the_car_ahead_yes(tmp_path):
    ca, lap = _habit_rig(tmp_path)
    ca._in_a_fight = lambda: True
    assert lap(2, drop_one=20) is None
    assert lap(3, drop_one=20, against="ahead").startswith("Turn one, second lap running")


def test_your_pace_is_a_lap_on_similar_tyre_wear(tmp_path):
    # Front right wears 3 % a lap; lap 2 (fresh tyres) is the fastest lap of all.
    race = FakeRace({0: [31000, 29000, 30000, 30000, 30200, 30400]}, corners=CORNERS,
                    wear_per_lap=[1, 1, 2, 3], extra_packets=False)
    eng, _ = run(race, tmp_path)
    ca = eng.corners
    # Lap 6 is at ~18 % wear: lap 2 (~6 %) is outside the 8 % window, so laps 4-5 set the pace.
    assert ca.reference_trace(6).lap == 4
    # Lap 3 (~9 %) is close enough to lap 2 (~6 %): the fast lap is the reference.
    assert ca.reference_trace(3).lap == 2


def test_detect_corners_with_laps_of_slightly_different_length():
    # The restarted Abu Dhabi race (29 Oct): traces a bin apart in length broke the report.
    s, b, t = profile(n=402)
    s2, b2, t2 = profile(n=400)
    corners = detect_corners([trace(s, b, t), trace(s2, b2, t2)],
                             track_length_m=400 * BIN_SIZE_M)
    assert [c.name for c in corners] == ["turn one", "turn two"]
