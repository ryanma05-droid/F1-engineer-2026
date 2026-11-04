"""Coaching from the driver report (report/focus.py): the report's corners and habits handed to
live coaching, the briefing, the radio question and the Live tab."""

import io
from datetime import datetime, timedelta

from engineer.app import build_engine
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.radio.director import Priority
from engineer.report.focus import (
    CoachingFocus,
    FocusCorner,
    focus_from_report,
    load_focus,
    save_focus,
)
from engineer.report.insights import build_report
from engineer.state.track_maps import TrackMap, detect_corners
from tests.test_corners import profile, trace
from tests.test_report import data_dir, entry  # noqa: F401  (fixtures)

FOCUS = CoachingFocus("2026-11-04T00:00:00+00:00",
                      {10: [FocusCorner(2, 0.18, 12, "early_braking", ("brake_early",))]},
                      [{"key": "early_braking", "title": "Braking too early", "cost_s": 0.2,
                        "radio": "braking"}])


def test_focus_from_a_real_report_and_back(entry, tmp_path):  # noqa: F811
    r = build_report([entry], datetime.fromisoformat(entry["recorded_at"]) + timedelta(days=1))
    f = focus_from_report(r)
    for c in f.for_track(17):
        agg = next(a for a in r.corners if a.track_id == 17 and a.number == c.number)
        assert c.loss_s >= 0.05 and abs(agg.dv("time_s") - c.loss_s) < 0.001
    assert len(f.for_track(17)) <= 3
    assert [h["key"] for h in f.habits] == [t.key for t in r.themes[:2]]
    path = save_focus(f, tmp_path)
    assert path.name == "focus.json"
    assert load_focus(tmp_path) is None                      # switched off for the suite
    import os
    os.environ["RACE_ENGINEER_REPORT_FOCUS"] = "1"
    try:
        back = load_focus(tmp_path)
    finally:
        os.environ["RACE_ENGINEER_REPORT_FOCUS"] = "0"
    assert back.for_track(17) == f.for_track(17) and back.habits == f.habits


def _rig(tmp_path, focus=FOCUS):
    from engineer.bus import events as ev
    eng = build_engine(Config(data_dir=tmp_path), out=io.StringIO(), priors=WearPriors())
    ca = eng.corners
    ca.focus = focus
    eng.pipeline.state.player_index = 0
    eng.pipeline.bus.publish(ev.SessionStarted(1, 0.0, track_id=10, session_type=15))
    speed, brake, throttle = profile()
    ref = trace(speed, brake, throttle)
    ca.track_map = TrackMap(10, 2000.0, detect_corners([ref], 2000.0))
    said = []
    ca.director.submit = lambda call: said.append((call.topic, call.text, call.priority))

    def lap(n, drop_one=0.0, drop_two=0.0, brake_two=0):
        sp = speed.copy()
        sp[80:120] -= drop_one
        sp[280:320] -= drop_two
        _s, br, th = profile(brake_shift=0)
        if brake_two:
            br, th = br.copy(), th.copy()
            br[300 - 20 - brake_two:300] = 0.8
            th[300 - 20 - brake_two:310] = 0.0
            sp[300 - 30 - brake_two:300 - 20] -= 25
        ca._compare(trace(sp, br, th, lap=n), ref, "pace")
    return ca, lap, said


def test_a_report_corner_beats_a_bigger_one_off_loss_from_the_first_lap(tmp_path):
    _ca, lap, said = _rig(tmp_path)
    lap(2, drop_one=60, drop_two=20, brake_two=8)        # turn one loses more this lap
    _topic, text, prio = said[-1]
    assert text.startswith("Turn two, one of your report corners,") and prio == Priority.NORMAL
    assert text.split(". ", 1)[1].startswith("Brake")    # the report habit's cause first
    lap(3, drop_one=60, drop_two=20, brake_two=8)        # named as such once a session
    assert "report" not in said[-1][1]
    # Without a report: the bigger loss, at LOW, as before.
    _ca2, lap2, said2 = _rig(tmp_path / "b", focus=None)
    lap2(2, drop_one=60, drop_two=20, brake_two=8)
    assert said2[-1][1].startswith("Turn one") and said2[-1][2] == Priority.LOW


def test_briefing_once_a_session_and_the_radio_question(tmp_path):
    ca, _lap, said = _rig(tmp_path)
    assert ca.focus_brief() == ("From your report, the time's in turn two. Work on braking: "
                                "move the braking point a few metres later.")
    ca._brief()
    ca._brief()
    assert [s for s in said if s[0] == "coaching_focus"] == [
        ("coaching_focus", ca.focus_brief(), Priority.NORMAL)]
    from engineer.voice.intents import match
    for q in ("What should I work on?", "What's my focus?", "What does my report say?",
              "Where do I usually lose time?"):
        assert match(q).name == "report_focus", q
    assert ca.report_card()["corners"][0]["name"] == "turn two"
    ca.focus = None
    assert ca.focus_brief() is None and ca.report_card() is None


def test_level_with_the_fastest_lap_said_once(tmp_path):
    from engineer.tools.queries import corner_deltas
    ca, _lap, said = _rig(tmp_path)
    speed, brake, throttle = profile()
    t = trace(speed, brake, throttle)
    deltas = corner_deltas(t, t, ca.track_map)              # level everywhere
    ca._level_check(deltas)
    ca._level_check(deltas)
    level = [s for s in said if "level with the fastest lap" in s[1]]
    text = "Turn two, level with the fastest lap now. That was a report corner, nice."
    assert level == [("coaching_focus", text, Priority.LOW)]


def test_phrases():
    assert phrases.focus_brief_phrase(["turn four", "turn six"], "the exits") == \
        "From your report, the time's in turn four and turn six. Work on the exits."
    assert phrases.focus_brief_phrase([], "braking") == "From your report, work on braking."
    assert phrases.corner_phrase(0.2, "turn four", "pace", ("brake_early", 12), report=True) == \
        "Turn four, one of your report corners, two tenths off your pace. Brake slightly later."
