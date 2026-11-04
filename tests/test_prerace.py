"""The pre-race tyre plan (strategy/prerace.py): race length, the start tyre, the radio and the
app window."""

import asyncio
import io
import json
import time

from engineer.app import build_engine
from engineer.bus import events as ev
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall
from engineer.strategy.prerace import (
    laps_at,
    last_race,
    race_length,
    same_length_rows,
    usual_share,
)
from engineer.strategy.runner import InlineRunner
from engineer.voice.intents import laps_in, match, track_in
from tests.test_strategy import stint_rows


def race(rows, session_type=15):
    for r in rows:
        r["session_type"] = session_type
    return rows


def history_at(track=17, laps=18, session="h1"):
    """A race at `track`: you and an AI car on mediums then hards, wearing ~3 %/lap and ~2 %/lap."""
    half = laps // 2
    rows = []
    for car, player in ((0, True), (1, False)):
        rows += stint_rows(session, car, 1, 17, half - 1, 2, rate=3.0, track=track, player=player)
        rows += stint_rows(session, car, 2, 18, laps - half, half + 1, rate=2.0, track=track,
                           player=player)
    return race(rows)


def engine(history):
    runner = InlineRunner(None)
    runner._history = history
    eng = build_engine(Config(), out=io.StringIO(), priors=WearPriors(), strategy_runner=runner)
    eng.director.idle_after_s = 1.0       # as live in the menus: no packets, say at once
    said = []
    eng.pipeline.bus.subscribe(ev.RadioMessage, lambda e: said.append(e.text))
    return eng, said


# -- race length ------------------------------------------------------------------------------

def test_race_length_from_your_last_race_here_or_your_usual_distance():
    assert usual_share(0, 15) == 0.25 and laps_at(0, 0.25) == 15     # Melbourne 58 x 25 %
    assert laps_at(0, 0.35) == 20 and laps_at(2, 0.35) == 20         # nearest lap (3 Nov races)
    assert laps_at(17, 0.25) == 18 and laps_at(2, 0.25) == 14
    assert usual_share(0, 5) is None                                   # a 5-lap race
    hist = history_at(17, 18) + history_at(0, 6, session="h2")         # left Melbourne on lap 6
    assert last_race(hist) == (0, 6)
    # Left early: snapped to the distance setting (Shanghai, 3 Nov: lap 6 of 14).
    assert race_length(0, hist).laps == 15 and race_length(0, hist).how == "last race here"
    assert race_length(17, hist).laps == 18
    other = race_length(13, hist)                                      # Suzuka: never raced
    assert (other.laps, other.how) == (13, "usual distance")          # 53 x 25 % = 13.25
    # A race seen live this run has the game's exact lap count.
    assert race_length(13, hist, recent=(13, 20)).laps == 20
    assert race_length(13, []) is None
    # Practice laps don't count as races.
    assert last_race(race(stint_rows("p1", laps=4), session_type=1)) is None


# -- the plan ---------------------------------------------------------------------------------

def test_the_start_tyre_and_stops_from_history():
    eng, _ = engine(history_at(17, 18))
    got = []
    assert eng.strategy.ask_prerace(got.append, track_id=17) is None
    plan = got[0]
    assert plan.laps.laps == 18 and plan.laps.how == "last race here"
    assert {s.compound for s in plan.starts} == {17, 18}
    best = plan.best
    assert best.safe and best.decision.call.plan.n_stops >= 1          # two-compound rule
    text = phrases.prerace_phrase(plan)
    assert text.startswith("Austria, eighteen laps, same as your last race here. Start on ")
    assert "box end of lap" in text
    # Laps said override the history.
    got.clear()
    eng.strategy.ask_prerace(got.append, track_id=17, laps=10)
    assert got[0].laps.laps == 10 and got[0].laps.how == "said"
    assert phrases.prerace_phrase(got[0]).startswith("Austria, ten laps. Start on ")


def test_no_history_at_the_track_means_no_plan_and_no_track_means_ask():
    eng, _ = engine(history_at(17, 18))
    got = []
    eng.strategy.ask_prerace(got.append, track_id=13)
    assert got[0].starts == () and got[0].laps.laps == 13
    assert phrases.prerace_phrase(got[0]) == ("No tyre numbers for Suzuka yet, I'll call the "
                                             "plan a couple of laps in.")
    assert eng.strategy.ask_prerace(got.append) == "no_track"
    eng2, _ = engine([])
    got.clear()
    eng2.strategy.ask_prerace(got.append, track_id=17)
    assert got[0].error == "no_laps"


def test_track_and_laps_said_on_the_radio():
    assert match("What tyres should I start on?").name == "prerace_plan"
    assert match("What's the strategy for Suzuka").name == "prerace_plan"
    assert match("plan for twenty two laps").name == "prerace_plan"
    assert match("pre-race strategy").name == "prerace_plan"
    assert match("when do I pit").name == "plan"
    assert track_in("strategy for the race in Spain") == 4
    assert track_in("what's the plan for abu dhabi") == 14
    assert laps_in("strategy for 15 laps at Monza") == 15 and track_in("15 laps at Monza") == 11
    assert laps_in("plan for twenty two laps") == 22 and laps_in("plan for ten laps") == 10
    assert laps_in("what's the plan") is None


def test_asking_before_the_race_gets_the_pre_race_plan():
    eng, said = engine(history_at(17, 18))
    bus = eng.pipeline.bus
    bus.publish(ev.QuestionAsked(0, 0.0, text="What tyres should I start on for Austria?"))
    assert said and said[-1].startswith("Austria, eighteen laps") and "Start on " in said[-1]
    # A plain "what's the plan?" in the menus too, for the track you last drove.
    eng.strategy.last_track = 17
    bus.publish(ev.QuestionAsked(0, 0.0, text="What's the plan?"))
    assert said[-1].startswith("Austria, eighteen laps")
    bus.publish(ev.QuestionAsked(0, 0.0, text="Strategy for 12 laps"))
    assert said[-1].startswith("Austria, twelve laps.")
    eng.strategy.last_track = None
    bus.publish(ev.QuestionAsked(0, 0.0, text="What tyres should I start on?"))
    assert said[-1] == "Which track, say strategy for, then the track."
    # The answers are logged as answered, not "say again".
    assert all(t != "Say again." for _, t in eng.answers.answered)


def test_said_by_itself_when_qualifying_ends():
    eng, said = engine(history_at(17, 18))
    bus = eng.pipeline.bus
    bus.publish(ev.SessionStarted(7, 0.0, track_id=17, session_type=9))
    bus.publish(ev.SessionEnded(7, 600.0, reason="SEND"))
    eng.director.release_now()
    assert any(t.startswith("Austria, eighteen laps") for t in said)
    n = len(said)
    bus.publish(ev.SessionEnded(7, 601.0, reason="new_session"))      # once per session
    eng.director.release_now()
    assert len(said) == n
    # Not after a race or practice.
    bus.publish(ev.SessionStarted(8, 0.0, track_id=17, session_type=15))
    bus.publish(ev.SessionEnded(8, 900.0, reason="SEND"))
    eng.director.release_now()
    assert len(said) == n


def test_director_speaks_at_once_when_no_packets_come():
    eng, said = engine([])
    d = eng.director
    d.idle_after_s = None
    d.speaking_until = 1e9                         # would hold anything queued
    d.submit(RadioCall(topic="answer", text="One.", priority=Priority.HIGH))
    assert said == []                              # replays/tests: queued as before
    d.idle_after_s = 1.0                           # live: nothing has come for a while
    d.submit(RadioCall(topic="answer", text="Two.", priority=Priority.HIGH))
    assert said == ["Two."]
    d._last_packet = time.monotonic()              # packets flowing again: queued
    d.submit(RadioCall(topic="answer", text="Three.", priority=Priority.HIGH))
    assert said == ["Two."]


def test_app_window_plans_and_shows_the_stints():
    from engineer.ui.app import AppWindow
    eng, _ = engine(history_at(17, 18))
    eng.strategy.last_track = 17
    win = AppWindow(Config(), eng)
    snap = win.snapshot()["prerace"]
    assert snap["plan"] is None and snap["defaults"]["track_id"] == 17 and not snap["racing"]
    r = asyncio.run(win._api_prerace({"track_id": 17, "laps": 16}))
    assert r.status == 200
    body = json.loads(r.body)
    plan = body["prerace"]["plan"]
    assert plan["laps"] == 16 and plan["track"] == "Austria" and plan["starts"][0]["best"]
    st = plan["starts"][0]["stints"]
    assert st[0]["from"] == 0 and st[-1]["to"] == 16 and all(x["wear"] for x in st)
    assert body["prerace"]["defaults"] == {"track_id": 17, "laps": 16, "how": "said"}
    assert asyncio.run(win._api_prerace({"track_id": 999})).status == 400
    assert asyncio.run(win._api_prerace({"laps": 0})).status == 400


def test_five_lap_races_dont_count_for_longer_ones():
    """3 Nov: tyres wear about half as fast in the game's 5-lap races."""
    hist = history_at(0, 15) + history_at(0, 5, session="short")
    assert race_length(0, hist).laps == 15                       # not "5, like last time"
    assert race_length(0, history_at(0, 5)) is None or race_length(0, history_at(0, 5)).how \
        == "usual distance"
    assert race_length(13, [], recent=(0, 5)) is None
    longer = same_length_rows(hist, 15)
    assert {r["session_uid"] for r in longer} == {"h1"}
    assert {r["session_uid"] for r in same_length_rows(hist, 5)} == {"short"}
    practice = race(stint_rows("p1", track=0, laps=4), session_type=1)
    assert len(same_length_rows(hist + practice, 15)) == len(longer) + len(practice)


def test_prerace_plan_after_each_qualifying_in_one_lobby():
    """3 Nov: one online lobby kept the same session UID for three qualifying sessions."""
    eng, said = engine(history_at(17, 18) + history_at(2, 18, session="h2"))
    bus = eng.pipeline.bus
    for track in (17, 2, 15):                                     # Austria, Shanghai, Texas
        bus.publish(ev.SessionStarted(7, 0.0, track_id=track, session_type=9))
        bus.publish(ev.SessionEnded(7, 100.0, reason="SEND"))
    eng.director.release_now()
    assert any(t.startswith("Austria") for t in said) and any(t.startswith("Shanghai") for t in said)
    assert any(t.startswith("No tyre numbers for Texas") for t in said)
