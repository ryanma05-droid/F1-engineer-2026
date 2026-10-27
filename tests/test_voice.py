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
    ("Talk to me", "talk"),
    ("What's for dinner", "unknown"),
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
    assert got["plan"].startswith("Plan is one stop, box lap ")
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
