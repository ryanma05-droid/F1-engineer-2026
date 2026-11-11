"""Gear coaching: a gear lower than the other car at the apex, at a similar speed, is a cause of
a loss, but only when you change gear yourself (manual or manual + suggested)."""

from engineer.radio import phrases
from engineer.report.focus import RADIO_HABIT, REASON_FOR_THEME
from engineer.report.insights import THEMES, CornerAgg
from engineer.state.race_state import SessionInfo
from engineer.state.track_maps import TrackMap, detect_corners
from engineer.tools.queries import corner_deltas
from tests.test_corners import profile, trace


def _geared(speed, brake, throttle, apex_gear, lap=1):
    tr = trace(speed, brake, throttle, lap=lap)
    tr.gear[:] = 7.0
    tr.gear[60:140] = float(apex_gear)                # around turn one's apex (section 100)
    return tr


def test_a_lower_gear_at_a_similar_speed_is_a_cause_on_manual_only():
    s, b, t = profile()
    ref = _geared(s, b, t, 3)
    tmap = TrackMap(10, 2000.0, detect_corners([ref], 2000.0))
    s2 = s.copy()
    s2[100:140] -= 2                                    # same apex speed, slower out
    mine = _geared(s2, b, t, 2)
    d1 = corner_deltas(mine, ref, tmap)[0]
    assert d1.mine.apex_gear == 2 and d1.ref.apex_gear == 3
    assert d1.reasons().get("gear_low") == 1.0
    assert "gear_low" not in corner_deltas(mine, ref, tmap, gears=False)[0].reasons()
    # Much slower at the apex: the low gear just follows the speed.
    s3 = s.copy()
    s3[90:110] -= 20
    assert "gear_low" not in corner_deltas(_geared(s3, b, t, 2), ref, tmap)[0].reasons()
    # A higher gear is never called.
    assert "gear_low" not in corner_deltas(_geared(s2, b, t, 4), ref, tmap)[0].reasons()


def test_gearbox_setting_from_the_session():
    assert SessionInfo(gearbox_assist=1).manual_gears and SessionInfo(gearbox_assist=2).manual_gears
    assert not SessionInfo(gearbox_assist=3).manual_gears and not SessionInfo().manual_gears


def test_gear_wording():
    assert phrases.corner_phrase(0.2, "turn six", "pace", ("gear_low", 1.0)) == \
        "Turn six, two tenths off your pace. Take the apex a gear higher."
    assert phrases.corner_phrase(0.2, "turn six", "pace", ("gear_low", 1.0),
                                 detail="specific") == \
        "Turn six, two tenths off your pace, a gear lower at the apex."
    assert phrases.advice(("brake_early", 12), {"brake_early": 12, "gear_low": 1}) == \
        "brake slightly later, then take the apex a gear higher"


def test_report_habit_and_its_live_cause():
    def corner(n, gear, min_kph, out_s):
        return CornerAgg("Shanghai", 2, n, laps=10, weight=10, ref_time=4.0, ref_min_kph=90,
                         d={"time_s": out_s, "gear": gear, "min_kph": min_kph, "out_s": out_s,
                            "carry_s": 0.0})
    cs = [corner(6, -0.6, -1.0, 0.3), corner(2, -1.0, -12.0, 0.2), corner(9, 0.5, 0.0, 0.1)]
    text, costs = THEMES["low_gear"](cs)
    assert [c.number for c, _ in costs] == [6] and costs[0][1] == 0.3
    assert text[0] == "A gear too low at the apex"
    assert REASON_FOR_THEME["low_gear"] == ("gear_low",) and "gear higher" in RADIO_HABIT["low_gear"]
    assert THEMES["low_gear"]([corner(9, 0.5, 0.0, 0.1)])[0] is None


def test_automatic_gears_in_the_real_recording(entry):
    """The fixture race was driven on the automatic gearbox: your apex gears aren't recorded as
    a choice, so the report has no gear habit."""
    assert entry["manual_gears"] is False
    assert all(r["gear"] is None for c in entry["corners"] for r in c["mine"])
    assert all(c["ref"].get("gear") for c in entry["corners"])

