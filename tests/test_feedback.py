"""The feedback log: questions flagged on the radio ("log that") with the race at that moment,
written up when the race ends, dismissable from the app window."""

import json

import pytest

from engineer.bus import events as ev
from engineer.voice.feedback import FeedbackLog, matches_now, render_markdown
from engineer.voice.intents import log_text, match
from tests.test_voice import race12, run_and_ask


@pytest.mark.parametrize("said, question", [
    ("Log the following question: what tyres is Norris on?", "what tyres is Norris on?"),
    ("log question how much faster are the mediums", "how much faster are the mediums"),
    ("Note that the box call came too late.", "the box call came too late."),
    ("Log that.", ""), ("Lock that", ""), ("Flag this one please", ""),
    ("Add that to the feedback log", ""), ("Log the following question.", ""),
])
def test_log_commands(said, question):
    assert match(said).name == "log_question"
    assert log_text(said) == question


@pytest.mark.parametrize("said", ["How are the tyres?", "What lap should I log?",
                                  "Lock in the plan?", "Gap ahead?"])
def test_ordinary_questions_are_not_logged(said):
    assert match(said).name != "log_question"


def _race():
    return race12(positions={0: 2, 1: 1}, gap_ahead_ms={0: 1200}, names=["PLAYER", "NORRIS"])


def test_log_that_flags_the_last_question_with_the_race(tmp_path):
    eng, radio = run_and_ask(_race(), {
        66: "What's for dinner", 68: "Log that.",                    # the one just asked
        72: "Log the following question: what tyres is Norris on?",  # said with the command
        75: "Log the following question.", 76: "Is Norris pitting?",  # the next one
        80: "Log that",                                               # nothing left to log
    }, tmp_path, duration_ms=95000)
    got = eng.answers.answered
    assert ("log_question", "Copy, logged that one.") in got
    assert ("log_question", "Copy, logged.") in got
    assert [t for i, t in got if i == "log_question"].count("Go ahead, what's the question.") == 2
    assert not any(t.startswith("Norris") for _, t in got)    # the logged ones aren't answered

    fb = eng.answers.feedback
    items = fb.entries()
    assert [e["question"] for e in items] == ["What's for dinner", "what tyres is Norris on?",
                                             "Is Norris pitting?"]
    first = items[0]
    assert first["source"] == "last question" and first["answer"] == "Say again."
    assert first["intent_then"] == "unknown" and first["lap"] == 3
    assert first["track"] and first["session_uid"] == "deadbeef12345678"
    ctx = first["context"]
    assert ctx["position"] == 2 and ctx["gap_ahead_s"] == 1.2 and ctx["ahead"] == "Norris"
    assert ctx["tyre"] and ctx["wear_pct"] is not None
    # Also in the question log, so it travels with the recording upload.
    q = [json.loads(line) for line in
         (tmp_path / "ptt" / "questions.jsonl").read_text().splitlines()]
    assert [e["logged"] for e in q if e.get("logged")] == [e["question"] for e in items]

    # The race ends: its entries get the result and the readable list is written.
    eng.pipeline.bus.publish(ev.SessionEnded(0xdeadbeef12345678, 95.0, reason="SEND"))
    assert all(e["result"]["position"] == 2 for e in fb.entries())
    md = fb.write_markdown().read_text()
    assert "**3 open**" in md and "finished P2" in md
    assert "Engineer answered: \"Say again.\"" in md and "1.2 s to Norris" in md


def test_dismiss_and_matches_now(tmp_path):
    fb = FeedbackLog(tmp_path)
    a = fb.add({"question": "what tyres is Norris on", "session_uid": "1", "lap": 4,
                "intent_then": "tyres"})
    b = fb.add({"question": "how are the tyres", "session_uid": "1", "lap": 5,
                "intent_then": "unknown"})
    assert a != b
    assert matches_now("how are the tyres", "unknown") == "tyres"     # newly handled
    assert matches_now("what tyres is Norris on", "tyres") is None    # still the wrong answer
    assert matches_now("blah blah blah") is None
    assert fb.dismiss(a) and not fb.dismiss("nope")
    assert [e["id"] for e in fb.open_entries()] == [b]
    md = render_markdown(fb.entries())
    assert "now goes to `tyres`" in md and "## Dismissed" in md


def test_nothing_written_when_off(tmp_path):
    fb = FeedbackLog(None)
    fb.add({"question": "anything at all"})
    assert fb.entries() == [] and fb.write_markdown() is None
