import io
import json
import subprocess

import pytest

from engineer.app import build_engine
from engineer.bus import events as ev
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.ingest import share
from engineer.radio import phrases
from engineer.strategy.runner import InlineRunner
from engineer.strategy.sim import Plan, Stop, simulate
from engineer.voice.controller import PushToTalk, XInputButton
from engineer.voice.intents import match
from tests.synth import FakeRace
from tests.test_strategy import SETS, model, situation

# -- intents -----------------------------------------------------------------------------------

@pytest.mark.parametrize("said,intent", [
    ("Radio check", "radio_check"),
    ("Can you hear me?", "radio_check"),
    ("How are the tires?", "tyres"),
    ("How are my tyres looking", "tyres"),
    ("What's the plan?", "plan"),
    ("When do I box?", "plan"),
    ("When should we pit?", "plan"),
    ("What's the strategy", "plan"),
    ("What if I box now?", "box_now"),
    ("Should I pit this lap?", "box_now"),
    ("Gap ahead?", "gap_ahead"),
    ("What's the gap to the car in front", "gap_ahead"),
    ("Who's behind me?", "gap_behind"),
    ("Gap behind", "gap_behind"),
    ("How was that lap?", "lap"),
    ("Where am I losing time?", "losing_time"),
    ("Which corner am I slow in", "losing_time"),
    ("Fuel?", "fuel"),
    ("How's the fuel looking", "fuel"),
    ("What position am I?", "position"),
    ("How many laps left?", "laps_left"),
    ("Quiet please", "quiet"),
    ("Stop talking", "quiet"),
    ("Talk more", "tempo_chatty"),
    ("Can you be more chatty", "tempo_chatty"),
    ("Keep it short", "tempo_terse"),
    ("Talk less please", "tempo_terse"),
    ("Talk to me", "talk"),
    # From the 28-29 Oct Melbourne, Imola and Abu Dhabi races (all were "say again" or wrong):
    ("When do you think I should pit?", "plan"),
    ("When should I pit?", "plan"),
    ("Yeah, but which lap should I pit?", "plan"),
    ("Where should I pit?", "plan"),
    ("Do I have to stop again?", "plan"),
    ("What tyres should I pit for?", "plan"),
    ("What tyres should I be on?", "plan"),
    ("Okay, we're on hards now.", "plan"),
    ("Should I pit this lap?", "box_now"),
    ("You sure we shouldn't pit?", "box_now"),
    ("Box.", "box_now"),
    ("Run the simulation for hards right now.", "box_now"),
    ("What happens if I pit for hards?", "box_now"),
    ("Are we boxing now?", "boxing_now"),
    ("How am I doing through some of these corners?", "losing_time"),
    ("Is there a corner that I'm losing a lot of speed?", "losing_time"),
    ("Am I losing speed in one particular corner?", "losing_time"),
    ("Give me some coaching.", "losing_time"),
    ("Where's Hamilton winning?", "compare_driver"),
    ("Talk to me about tire wear.", "tyres"),
    ("Talk to me more.", "tempo_chatty"),
    ("Give me some chatter.", "tempo_chatty"),
    ("Repeat that.", "repeat"),
    ("How are we looking right now?", "status"),
    ("How does this current tyre hold up to the end of the race?", "tyres_to_end"),
    ("Do I have to stop again with these tyres? Will I get over 45%?", "tyres_to_end"),
    ("How many laps have I done on these tyres?", "tyre_age"),
    ("Who's pitted in front of me?", "who_pitted"),
    ("What if I go a lap later?", "box_shift"),
    ("What about two laps earlier?", "box_shift"),
    ("What if we extend?", "box_shift"),
    ("What's for dinner", "unknown"),
    # From the 27 Oct Abu Dhabi race:
    ("How does that exit?", "corner"),
    ("Where am I gaining on Lando?", "compare_driver"),
    ("Where am I gaining speed to Kimi Antonelli?", "compare_driver"),
    ("Where can I improve pace?", "losing_time"),
    # Likely next ones:
    ("How was turn four?", "corner"),
    ("How was the braking there", "corner"),
    ("Am I quicker than Max?", "compare_driver"),
    ("Gap to Norris?", "gap_driver"),
    ("How far behind the leader am I?", "gap_leader"),
    ("Who's leading?", "gap_leader"),
    ("Where am I gaining time?", "gaining_time"),
    ("Where am I?", "position"),
    ("Any damage?", "damage"),
    ("What's my best lap?", "best_lap"),
    ("Is it going to rain?", "weather"),
    ("What lap is it?", "laps_left"),
    # 2-3 Nov races (the questions you logged, and ones that went to the wrong answer)
    ("Where am I going to come out?", "rejoin"),
    ("Where's my track position?", "true_position"),
    ("We should figure out a way to give accurate track position.", "true_position"),
    ("Are these tyres going to make it?", "tyres_to_end"),
    ("Any tips for managing these tyres? Where am I over exerting them?", "tyre_tips"),
    ("Give me tips on how to speed up.", "speed_tips"),
    ("How are the tyres looking? Relative to the strategy?", "tyres"),
    ("Be definitive with the strategy. What should I do?", "plan"),
    ("Be definitive.", "plan"),
    # Coaching mode and the wording switch
    ("Coaching mode.", "tempo_coaching"),
    ("Switch to coaching.", "tempo_coaching"),
    ("Give me the numbers.", "detail_specific"),
    ("Keep it simple.", "detail_plain"),
    ("How far off the fastest car am I?", "gap_fastest"),
    ("Where's the time?", "gap_fastest"),
])
def test_intents(said, intent):
    assert match(said).name == intent


# -- the button --------------------------------------------------------------------------------

class FakeButton:
    def __init__(self):
        self.down = False

    def pressed(self):
        return self.down


def test_push_to_talk_press_release_tap_and_cap():
    b, events = FakeButton(), []
    ptt = PushToTalk(b, lambda: events.append("press"), lambda: events.append("release"),
                     lambda: events.append("cancel"), min_press_s=0.25, max_s=10)
    b.down = True
    ptt.poll(0.0)
    ptt.poll(0.1)
    b.down = False
    ptt.poll(1.0)                       # held 1 s: a question
    b.down = True
    ptt.poll(2.0)
    b.down = False
    ptt.poll(2.1)                       # a 0.1 s brush: ignored
    b.down = True
    ptt.poll(3.0)
    ptt.poll(13.5)                      # held past 10 s: ends anyway
    ptt.poll(14.0)                      # still held: no new question until released
    b.down = False
    ptt.poll(15.0)
    assert events == ["press", "release", "press", "cancel", "press", "release"]


def test_xinput_off_windows_is_simply_unavailable():
    b = XInputButton("LB")
    if not b.available:
        assert b.pressed() is False and b.connected() == []
    with pytest.raises(ValueError):
        XInputButton("Z9")


# -- answers, end to end through the engine ----------------------------------------------------

def run_and_ask(race, questions: dict[float, str], tmp_path=None, duration_ms=None):
    """Feed the race; at each game time (s) in `questions`, ask that question."""
    cfg = Config(data_dir=str(tmp_path) if tmp_path else "/nonexistent-test-dir")
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors(), strategy_runner=InlineRunner(None))
    todo = sorted(questions.items())
    st = eng.pipeline.state
    for t_ns, raw in race.frames(duration_ms=duration_ms):
        eng.pipeline.feed(raw, t_ns)
        while todo and (st.session_time or 0) >= todo[0][0]:
            _, text = todo.pop(0)
            eng.pipeline.bus.publish(ev.QuestionAsked(st.session_uid or 0, st.session_time,
                                                      text=text))
    radio = [line.split("s  ", 1)[1].split("  (held")[0]
             for line in out.getvalue().splitlines() if line.startswith("RADIO")]
    return eng, radio


def race12(**kw):
    base = dict(wear_per_lap=[4, 4, 6, 8], extra_packets=False, tyre_sets=SETS,
                fuel_per_lap=1.0)
    base.update(kw)
    return FakeRace({0: [30000] * 12, 1: [30500] * 12}, **base)


def test_answers_to_each_question(tmp_path):
    race = race12(positions={0: 2, 1: 1}, gap_ahead_ms={0: 1200}, names=["PLAYER", "NORRIS"])
    eng, radio = run_and_ask(race, {
        65.5: "Radio check", 66.5: "How are the tyres?", 70: "What's the plan?",
        75: "Gap ahead?", 78: "Gap behind?", 80: "Position?", 83: "What's for dinner",
        86: "Fuel?",
    }, tmp_path, duration_ms=95000)
    got = dict(eng.answers.answered)
    assert got["radio_check"] == "Loud and clear."
    assert got["tyres"].startswith("Tyres: front right worst at sixteen percent")
    assert got["plan"].startswith("Plan is one stop, box end of lap ")
    assert got["gap_ahead"] == "Norris ahead, one point two seconds."
    assert got["gap_behind"] == "Nobody behind."
    assert got["position"] == "P two, ten laps to go."
    assert got["unknown"] == "Say again."
    assert got["fuel"] == "No fuel reading yet." or got["fuel"].startswith("Fuel is")
    # Every answer is said, at once, as topic "answer".
    for text in got.values():
        assert text in radio
    # Every question is logged, unknown ones too (the list of phrasings to add).
    entries = [json.loads(line) for line in
               (tmp_path / "ptt" / "questions.jsonl").read_text().splitlines()]
    assert [e["intent"] for e in entries] == ["radio_check", "tyres", "plan", "gap_ahead",
                                             "gap_behind", "position", "unknown", "fuel"]
    assert entries[6]["heard"] == "What's for dinner"
    assert entries[0]["session_uid"] == "deadbeef12345678" and entries[0]["lap"] == 3


def test_box_now_what_if():
    eng, radio = run_and_ask(race12(), {95: "What if I box now?"}, duration_ms=100000)
    intent, text = eng.answers.answered[-1]
    assert intent == "box_now"
    assert text.startswith(("Box now costs about", "Box now is the plan",
                            "Box now, it's about the same"))


def test_quiet_mode_keeps_only_box_calls_and_answers():
    eng, radio = run_and_ask(race12(), {10: "Quiet", 200: "Talk to me"})
    quiet = radio[radio.index("Copy, quiet. Box calls only."):
                  radio.index("Copy, back to normal.")]
    assert any(r.startswith("Box this lap") for r in quiet)
    assert not any(r.startswith(("Thirty", "Plan", "Tyres")) for r in quiet)
    after = radio[radio.index("Copy, back to normal."):]
    assert any(r.startswith("Thirty") for r in after)


def test_short_race_has_no_strategy_to_ask_about():
    race = FakeRace({0: [30000] * 5}, extra_packets=False)
    eng, _ = run_and_ask(race, {70: "What's the plan?", 75: "Should I box now?"})
    assert dict(eng.answers.answered) == {"plan": "No strategy calls in this race.",
                                          "box_now": "No strategy calls in this race."}


def test_answer_phrases():
    assert phrases.fuel_phrase(0.4) == "Fuel is fine, plus zero point four laps."
    assert phrases.fuel_phrase(-0.3) == "Fuel is tight, minus zero point three laps, lift and coast."
    assert phrases.position_phrase(1, 1) == "P one, last lap."
    plan = Plan((Stop(14, 18, 0),), 100.0, (14, 16))
    now = Plan((Stop(10, 18, 0),), 103.0)
    assert phrases.box_now_phrase(now, plan) == "Box now costs about three seconds over the plan, hards."


# -- simulator what-if -------------------------------------------------------------------------

def test_sim_forced_first_stop():
    m = model()
    free = simulate(situation(), m).best
    forced = simulate(situation(first_stop_lap=4), m).best
    assert forced.stops[0].lap == 4 and forced.cost_s >= free.cost_s


# -- uploads carry the session's questions -----------------------------------------------------

def test_upload_includes_the_questions_of_that_session(tmp_path):
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    run = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
    run("init", "--bare", str(remote), cwd=tmp_path)
    run("init", "-b", "main", str(repo), cwd=tmp_path)
    run("remote", "add", "origin", str(remote), cwd=repo)
    rec = tmp_path / "20261027T010920Z_cdb2205fbdb7a7db.zst"
    rec.write_bytes(b"\x01" * 1000)
    log = tmp_path / "questions.jsonl"
    log.write_text(json.dumps({"session_uid": "cdb2205fbdb7a7db", "heard": "gap ahead"}) + "\n"
                   + json.dumps({"session_uid": "0000000000000001", "heard": "other"}) + "\n")
    branch = share.upload(rec, tmp_path / "share", "origin", repo=repo,
                          progress=lambda m: None, questions_log=log)
    files = run("ls-tree", "-r", "--name-only", branch, cwd=remote).stdout.decode().split()
    q = "data/share/20261027T010920Z_cdb2205fbdb7a7db.zst.questions.jsonl"
    assert q in files
    body = run("show", f"{branch}:{q}", cwd=remote).stdout.decode()
    assert "gap ahead" in body and "other" not in body
    assert not (tmp_path / f"{rec.name}.questions.jsonl").exists()


def test_replay_asks_logged_questions_again(tmp_path):
    from engineer.ingest.recorder import Recorder
    from engineer.ingest.replay import replay

    race = race12()
    rec = Recorder(tmp_path / "sessions")
    for t_ns, raw in race.frames(duration_ms=70000):
        rec(raw, t_ns)
    rec.close()
    path = next((tmp_path / "sessions").glob("*.zst"))
    qs = [{"session_uid": "deadbeef12345678", "session_time": 66.0, "heard": "radio check"},
          {"session_uid": "0000000000000001", "session_time": 10.0, "heard": "fuel"}]
    cfg = Config(data_dir=str(tmp_path / "d"))
    cfg.voice.log = False
    from engineer.app import build_engine as be
    eng = be(cfg, out=io.StringIO(), priors=WearPriors(), strategy_runner=InlineRunner(None))
    replay(path, engine=eng, questions=qs)
    assert eng.answers.answered == [("radio_check", "Loud and clear.")]


def test_speech_process_fails_cleanly_without_a_microphone():
    from engineer.voice.speech import SpeechProcess, SpeechSettings
    sp = SpeechProcess(SpeechSettings(backend="null"))
    try:
        ok = sp.start(wait_ready_s=60)
        assert ok or sp.error            # no hang, and a readable reason if it can't start
    finally:
        sp.close()


def test_speech_model_falls_back_to_cpu_when_the_gpu_fails(monkeypatch):
    import ctranslate2

    from engineer.voice import speech
    tried = []

    def fake_open(settings, device):
        tried.append(device)
        if device == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return object(), f"{settings.model} on {device}"

    monkeypatch.setattr(speech, "_open_model", fake_open)
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    _, desc = speech.load_model(speech.SpeechSettings(model="small.en"))
    assert tried == ["cuda", "cpu"] and desc == "small.en on cpu"
    tried.clear()
    speech.load_model(speech.SpeechSettings(model="small.en", device="cpu"))
    assert tried == ["cpu"]


def test_a_late_answer_is_not_said(tmp_path):
    # Real race, 27 Oct: recognition on a GPU busy with the game took 41 s.
    eng, radio = run_and_ask(race12(), {}, tmp_path, duration_ms=70000)
    st = eng.pipeline.state
    eng.pipeline.bus.publish(ev.QuestionAsked(st.session_uid, st.session_time,
                                              text="Gap behind.", stt_ms=41317.0))
    eng.pipeline.bus.publish(ev.QuestionAsked(st.session_uid, st.session_time,
                                              text="Radio check.", stt_ms=184.0))
    assert eng.answers.answered == [("radio_check", "Loud and clear.")]
    entries = [json.loads(line) for line in
               (tmp_path / "ptt" / "questions.jsonl").read_text().splitlines()]
    assert entries[0]["answer"].startswith("(not said: speech recognition took 41 s)")


def test_driver_and_turn_from_text():
    from engineer.voice.intents import driver_in, turn_in
    names = ["PLAYER", "NORRIS", "ANTONELLI", None]
    assert driver_in("Where am I gaining on Lando?", names) == 1
    assert driver_in("gaining speed to Kimi Antonelli", names) == 2
    assert driver_in("gap to Verstappen", names) is None
    assert turn_in("How was turn four?") == 4 and turn_in("turn 12") == 12
    assert turn_in("how was that exit") is None


def test_new_answers_from_the_abu_dhabi_race(tmp_path):
    race = race12(positions={0: 2, 1: 1}, gap_ahead_ms={0: 1200}, names=["PLAYER", "NORRIS"])
    eng, _ = run_and_ask(race, {
        20: "How are the tyres?",              # lap 1: live wear, not "give me a lap"
        25: "Where am I gaining on Lando?",    # lap 1: no clean lap to compare
        70: "How does that exit?",             # no corner map on a flat-out test track
        75: "Gap to Norris?", 80: "Where am I gaining on Lando?",
        85: "Where can I improve pace?", 86: "Any damage?", 87: "What's my best lap?",
        88: "Who's leading?", 89: "Is it going to rain?",
    }, tmp_path, duration_ms=95000)
    got = eng.answers.answered
    assert got[0] == ("tyres", "Front right at five percent, too early for a wear rate.")
    assert got[1] == ("compare_driver", "No clean lap to compare with Norris yet.")
    assert got[2] == ("corner", "Still learning this track's corners, give me two clean laps.")
    assert got[3] == ("gap_driver", "Norris ahead, one point two seconds.")
    assert got[4][0] == "compare_driver" and got[4][1].startswith("Last lap, ")
    assert "Norris" in got[4][1]
    # Quicker than Norris (ahead) everywhere in the test race.
    assert got[5] == ("losing_time", "Not losing anywhere to the car ahead last lap.")
    assert got[6] == ("damage", "No damage, car looks fine.")
    assert got[7][0] == "best_lap" and got[7][1].startswith("Your best is thirty")
    assert got[8][0] == "gap_leader" and got[8][1].startswith("Norris leads")
    assert got[9] == ("weather", "Clear, track zero degrees, air zero.")


def test_welcome_is_said_once_ever(tmp_path):
    from engineer.cli import welcome
    from engineer.intro import welcome_text

    class FakeAudio:
        backend = "piper"

        def __init__(self):
            self.said = []

        def say(self, text, origin_t_ns=None):
            self.said.append(text)

    cfg = Config(data_dir=str(tmp_path))
    silent = FakeAudio()
    silent.backend = "null"
    welcome(cfg, silent, push_to_talk=True)
    assert silent.said == []                              # no voice: kept for later
    a = FakeAudio()
    welcome(cfg, a, push_to_talk=True)
    assert a.said == [welcome_text("LB")] and "Hold L B any time" in a.said[0]
    b = FakeAudio()
    welcome(cfg, b, push_to_talk=True)                    # second start: never again
    assert b.said == []
    assert "Hold" not in welcome_text(None)               # console players: no push-to-talk


def test_microphone_that_stops_sending_is_noticed(monkeypatch):
    """27-29 Oct: every question came back as 0.32 s (just the pre-roll): the microphone stream
    had stopped delivering audio. The speech process now notices and reopens it."""
    import numpy as np

    from engineer.voice import speech

    class FakeStream:
        def __init__(self, **kw):
            self.callback, self.active = kw["callback"], False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            pass

    class FakeSd:
        opened = 0

        def InputStream(self, **kw):
            FakeSd.opened += 1
            self.stream = FakeStream(**kw)
            return self.stream

        def _terminate(self):
            pass

        def _initialize(self):
            pass

    sd = FakeSd()
    monkeypatch.setattr(speech, "resolve_input_device", lambda spec: (None, "Headset"))
    mic = speech._Mic(sd, None)
    mic.open()
    block = np.zeros((512, 1), np.float32)
    for _ in range(20):
        sd.stream.callback(block, 512, None, None)
    assert not mic.stalled()
    mic.start_recording()
    audio, new = mic.stop_recording()          # nothing arrived while "held"
    assert new == 0 and audio.size == 10 * 512  # only the pre-roll
    mic.last_block -= speech.STALL_S + 0.5
    assert mic.stalled()
    mic.reopen()
    assert FakeSd.opened == 2 and not mic.stalled() and not mic.pre
    mic.start_recording()
    sd.stream.callback(block, 512, None, None)
    assert mic.stop_recording()[1] == 1



def test_percent_and_compound_in_a_question():
    from engineer.voice.answers import _compound_in, _percent_in
    assert _percent_in("Will I get over 45%?") == 45
    assert _percent_in("How are the tyres?") is None
    assert _compound_in("What happens if I pit for hards?") == 18
    assert _compound_in("Box now?") is None




def test_shift_in_a_question():
    from engineer.voice.answers import _shift_in
    assert _shift_in("What if I go a lap later?") == 1
    assert _shift_in("What about two laps earlier?") == -2
    assert _shift_in("What if we extend?") == 1


def test_answers_to_the_questions_logged_on_2_3_nov(tmp_path):
    race = race12(positions={0: 2, 1: 1}, gap_ahead_ms={0: 1200}, names=["PLAYER", "NORRIS"])
    eng, radio = run_and_ask(race, {
        70: "Where am I going to come out?", 74: "Where's my track position?",
        78: "Any tips for managing the tyres?", 82: "Give me tips on how to speed up.",
        86: "Repeat the strategy call.",
    }, tmp_path, duration_ms=95000)
    got = dict(eng.answers.answered)
    assert got["rejoin"].startswith("Box now and you come out P ")
    # Nobody has stopped yet and neither have you: the order is real.
    assert got["true_position"].startswith("P two, and that's real")
    assert " is your limiting tyre" in got["tyre_tips"]
    assert got["speed_tips"] != "Say again."
    assert got["repeat"].startswith("Plan is one stop")       # the strategy call, not the last
    assert "Say again." not in got.values()


def test_true_position_phrase():
    assert phrases.true_position_phrase(7, 5, ["Norris", "Leclerc"], []) == (
        "P seven on the road. Norris and Leclerc ahead still have to stop. About P five once it "
        "all shakes out.")
    assert phrases.true_position_phrase(7, 8, [], ["Sainz"]) == (
        "P seven on the road. After your stop Sainz will be ahead. About P eight once it all "
        "shakes out.")


def test_answers_to_the_questions_from_the_3_nov_races(tmp_path):
    """The 20-lap lobby races: questions that got "Say again" or the wrong answer."""
    race = race12(positions={0: 2, 1: 1}, gap_ahead_ms={0: 1200}, names=["PLAYER", "NORRIS"])
    eng, radio = run_and_ask(race, {
        66: "What's the fastest lap right now?", 68: "Has Norris pitted?",
        70: "Can I catch the leader?", 72: "If I box at lap 8 for hards, how am I looking?",
        74: "Why not hards?", 76: "Will hards make it to the end?",
        78: "What will they be by the end?", 98: "What was the sector two differential to my best?",
        82: "You say off or up?", 84: "Yeah, but should I pit later for hards?",
    }, tmp_path, duration_ms=100000)
    got = dict(eng.answers.answered)
    assert got["fastest_overall"].startswith("You have it, ")           # 30.0 s laps vs 30.5
    assert got["driver_pitted"].startswith("Norris hasn't stopped")
    assert got["catch"].startswith("Norris is one point two seconds ahead.")
    assert got["what_if_lap"].startswith(("Boxing lap eight for hards", "Lap eight for hards"))
    assert got["why_not"].startswith("Hards at lap ") or got["why_not"].startswith("Hards are the plan")
    assert got["compound_to_end"].startswith("A new set of hards from the end of this lap")
    assert got["stints_end"].startswith("On the plan: these ")
    assert got["sector"].startswith("Sector two")
    assert got["box_shift"].startswith(("On hards.", "A lap later"))
    assert "Say again." not in [v for k, v in got.items() if k != "clarify"]
    # The strategy follow-ups are logged with the session, like box_now's.
    import json
    log = [json.loads(x) for x in (tmp_path / "ptt" / "questions.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert {x["intent"] for x in log if x.get("followup")} >= {"what_if_lap", "why_not"}


def test_new_answer_phrases():
    from types import SimpleNamespace as N
    assert phrases.catch_phrase("Verstappen", 6900, 300, 8) == (
        "Verstappen is six point nine seconds ahead. You're three tenths a lap quicker, about "
        "twenty three laps to catch them, only eight to go. Not on pace alone.")
    assert phrases.catch_phrase("Norris", 1200, 400, 8).endswith(
        "on them in about three laps, eight to go.")
    assert phrases.new_set_phrase(18, 15, 2.5, 60) == (
        "A new set of hards from the end of this lap, about thirty eight percent at the flag "
        "after fifteen laps, they'd make it.")
    st = [N(compound=16, start_lap=0, end_lap=12, end_wear=40.0),
          N(compound=17, start_lap=12, end_lap=20, end_wear=55.0)]
    assert phrases.stints_phrase(st, 20) == (
        "On the plan: these softs come off at lap twelve at about forty percent, then the "
        "mediums finish at about fifty five percent.")
    assert phrases.damage_cost_phrase(0.4, 10, 19.0, None) == (
        "It's costing about four tenths a lap, about four seconds to the flag, less than a stop "
        "costs, stay out.")
    assert phrases.damage_cost_phrase(0.4, 10, 19.0, 12).endswith(
        "Your stop on lap twelve fixes it, no extra stop.")
    assert phrases.damage_cost_phrase(0.05, 10, 19.0, None).startswith("It isn't costing")


def test_7_nov_phrasings():
    from engineer.voice.intents import match
    assert match("What am I pitting?").name == "plan"       # heard for "When am I pitting?"
    assert match("When am I pitting?").name == "plan"
    assert match("Has Kimmy pitted?").name == "driver_pitted"


def test_9_nov_phrasings():
    from engineer.voice.intents import match
    cases = {"What do you think about hards?": "why_not",
             "How much slower would hards be?": "why_not",
             "Run a strategy simulation for hards.": "why_not",
             "What was his last lap relative to mine?": "compare_driver",
             "Where's my teammate?": "teammate",
             "Let's get a little hype in the chat here. Gary?": "hype",
             "How long did the pit stop take?": "pit_stop_time",
             "What's the pit strat?": "plan",
             "How's this tyre? We're looking for the rest of the race.": "tyres_to_end"}
    for text, intent in cases.items():
        assert match(text).name == intent, text


def test_9_nov_phrases():
    assert phrases.pit_stop_time_phrase(21.3, 2.41, 2.6) == (
        "Two point four seconds stationary, twenty one seconds in the pit lane. Two tenths "
        "quicker than the others' typical stop.")
    assert phrases.damage_vs_field_phrase(0.1).endswith("so it isn't costing much, I'd leave it.")
    assert "six tenths a lap off the quickest cars" in phrases.damage_vs_field_phrase(0.6)
    assert phrases.hype_phrase(1, 4, None).startswith("You're leading this thing!")
