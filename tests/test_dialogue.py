"""13 Nov: strategy as a dialogue. Your preferences steer the plan (read back with what they
cost, pushback when risky), offers you can say yes / no to, "why?", "are you sure?", race pace,
and console-only mode."""

import io
from dataclasses import replace

from engineer.app import build_engine
from engineer.bus import events as ev
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.strategy.runner import InlineRunner
from engineer.strategy.sim import RaceSituation, simulate
from engineer.voice.dialogue import parse_prefs
from tests.synth import FakeRace
from tests.test_strategy import SETS, model

RACE = {"wear_per_lap": [4, 4, 6, 8], "extra_packets": False, "tyre_sets": SETS, "fuel_per_lap": 1.0}


def drive(questions=None, duration_ms=150000, cfg=None):
    """A 12-lap race of 30 s laps (the softs can't make the end); questions: {game time:
    text}. Returns (engine, radio lines, answers)."""
    cfg = cfg or Config(data_dir="/nonexistent-test-dir")
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors(), strategy_runner=InlineRunner(None))
    todo = sorted((questions or {}).items())
    st = eng.pipeline.state
    for t_ns, raw in FakeRace({0: [30000] * 12}, **RACE).frames(duration_ms=duration_ms):
        eng.pipeline.feed(raw, t_ns)
        while todo and (st.session_time or 0) >= todo[0][0]:
            _, q = todo.pop(0)
            eng.pipeline.bus.publish(ev.QuestionAsked(st.session_uid, st.session_time, text=q,
                                                      stt_ms=900))
    radio = [line.split("s  ", 1)[1].split("  (held")[0]
             for line in out.getvalue().splitlines() if line.startswith("RADIO")]
    return eng, radio, eng.answers.answered


def test_the_simulator_follows_the_rules():
    m = model()
    sit = RaceSituation(lap_done=3, total_laps=20, compound=16, wear=12.0,
                        sets={16: [0.0], 17: [0.0], 18: [0.0]}, used=frozenset({16}),
                        require_two=False, limit_pct=60.0, pit_loss_s=20.0)
    free = simulate(sit, m).best
    assert free is not None
    plan = simulate(replace(sit, final_compound=18), m).best
    assert plan.stops and plan.stops[-1].compound == 18
    plan = simulate(replace(sit, avoid=frozenset({17, 18})), m).best
    assert all(s.compound == 16 for s in plan.stops)
    plan = simulate(replace(sit, min_stops=2, max_stops=2), m).best
    assert plan.n_stops == 2
    plan = simulate(replace(sit, first_stop_range=(12, 14)), m).best
    assert plan.stops and 12 <= plan.stops[0].lap <= 14


def test_parse_prefs():
    assert parse_prefs("I want to finish on hards", 5, 8, 0, 12)[0] == {"final_compound": 18}
    assert parse_prefs("max fifty five percent", 5, 8, 0, 12)[0] == {"limit_pct": 55.0}
    assert parse_prefs("make it a two stop", 5, 8, 1, 12)[0] == {"stops": (1, 1)}
    assert parse_prefs("no hards", 5, 8, 0, 12)[0] == {"avoid": frozenset({18})}
    assert parse_prefs("I want to go long", 5, 8, 0, 12)[0] == {"first_stop": (11, 11)}
    assert parse_prefs("what if I box on lap ten", 5, 8, 0, 12) is None


def test_a_preference_is_read_back_with_what_it_costs_and_the_box_calls_follow_it():
    eng, radio, answers = drive({100.0: "I want to finish on hards"})
    got = [t for i, t in answers if i == "prefs"]
    assert got and got[0].startswith("Copy, finishing on hards. ") and "our plan" in got[0], got
    plan = eng.strategy.announced
    assert plan.stops and plan.stops[-1].compound == 18
    box = [r for r in radio if r.startswith("Box this lap")]
    assert box and "hards" in box[0].lower(), radio


def test_risky_preferences_get_pushback_with_the_time_and_back_to_the_plan_clears_them():
    eng, _radio, answers = drive({70.0: "I want to box on lap ten", 100.0: "back to your plan"})
    risky = [t for i, t in answers if i == "prefs"]
    assert risky and risky[0].startswith("Copy, boxing lap ten.") and "past our limit" in risky[0] \
        and "than our plan" in risky[0], risky
    back = [t for i, t in answers if i == "prefs_clear"]
    assert back and back[0].startswith("Copy, back to our plan.") and not eng.strategy.prefs.active
    _, _, answers = drive({70.0: "run them to 85 percent"})
    said = next(t for i, t in answers if i == "prefs")
    assert "Eighty five is past our sixty percent limit" in said, said


def test_no_more_stops_when_the_rules_need_one():
    _, _, answers = drive({70.0: "no more stops"})
    said = next(t for i, t in answers if i == "prefs")
    assert said == ("Can't make a plan with no more stops, the rules need a second compound, so "
                    "one more stop at least. Staying with the current plan."), said


def test_why_sure_and_pace_answers():
    _eng, _radio, answers = drive({100.0: "Why?", 103.0: "Are you sure?",
                                 106.0: "How much do I need to save?", 109.0: "Can I push?"})
    said = dict(answers)
    assert said["why"].startswith("It's the quickest")
    assert said["sure"].split(",")[0] in ("Yes", "Fairly", "No")
    assert said["pace_save"].startswith(("You need to save about", "You're fine to"))
    assert said["pace_push"].startswith(("Yes, about", "Not really"))


def test_an_offer_can_be_taken():
    eng, _radio, _answers = drive(duration_ms=75000)
    sa = eng.strategy
    if sa.offer is None:                    # no worthwhile gamble in this race: make one
        dec = sa.decision
        sa.offer, sa.offer_at = dec.call.plan, eng.pipeline.state.session_time
    eng.pipeline.bus.publish(ev.QuestionAsked(1, eng.pipeline.state.session_time, text="Yes",
                                              stt_ms=900))
    assert any(i == "accept" and t.startswith("Copy, going for it.") for i, t in
               eng.answers.answered), eng.answers.answered
    assert sa.offer is None and sa.prefs.stops is not None


def test_console_only_volunteers_alternatives_status_and_manage_calls():
    cfg = Config(data_dir="/nonexistent-test-dir")
    cfg.voice.enabled = False                  # no push-to-talk: console only by default
    eng, radio, _ = drive({70.0: "I want to box on lap ten"}, cfg=cfg)   # too long: manage
    assert eng.strategy.console and eng.pace.console
    assert any(r.startswith("Option: ") for r in radio), radio
    assert any(r.startswith("Manage the tyres, save about") for r in radio), radio
    # With push-to-talk there are no volunteered alternatives.
    _, radio2, _ = drive()
    assert not any(r.startswith("Option: ") for r in radio2)
