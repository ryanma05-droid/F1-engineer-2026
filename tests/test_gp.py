"""GP (radio/gp.py): Gary's voice with a manner learned from real team radio."""

import re

from engineer.radio.engineers import BY_KEY, personality
from engineer.radio.gp import GPStyle

CALLS = [
    (10, "box", "Box this lap, box this lap. Hards. You'll come out behind Sainz."),
    (40, "answer", "Norris ahead, one point two seconds."),
    (70, "racecraft_push", "Push now, Russell is two seconds back and coming. Keep them out of DRS."),
    (120, "racecraft_pass", "Great move on Norris, P three."),
    (200, "racecraft_pass", "Lost P three to Leclerc. Stay with Leclerc."),
    (300, "racecraft_countdown", "Five laps to go, P three. Leclerc ahead, eight tenths."),
    (400, "racecraft_countdown", "Last lap, P two. Bring it home."),
    (500, "racecraft_finish", "P one! You've won it! Brilliant drive."),
    (520, "corner", "Turn four, losing two tenths to the car ahead. Brake slightly later."),
    (560, "answer", "Plan is one stop, box end of lap nine for softs."),
]


def run(name=None, seed=7):
    g = GPStyle(name)
    g.new_session(seed)
    return [g(topic, text, t) for t, topic, text in CALLS]


def test_gp_is_an_engineer_with_garys_voice():
    gp = BY_KEY["gp"]
    assert gp.name == "GP" and gp.voice == BY_KEY["northern"].voice
    assert isinstance(personality("gp"), GPStyle) and personality("gp", "Ryan").name == "Ryan"


def test_every_call_is_kept_word_for_word_and_replays_repeat():
    out = run()
    assert out == run()                                      # seeded per session
    for (_, _, text), said in zip(CALLS, out):
        assert text[1:] in said and text[0].lower() in said.lower()
    assert sum(s != t for (_, _, t), s in zip(CALLS, out)) >= 5   # it does have a manner


def test_the_habits_from_the_radio():
    out = run()
    assert out[0].endswith(("Box, confirm.", "Box and confirm, mate.", "Confirm, box."))
    assert out[1].endswith("One point two.")                 # the number twice
    assert out[2].endswith("Two seconds.")                   # not a bare "Two."
    assert out[7].startswith("P one! You've won it! Brilliant drive. ")   # praise added
    assert out[6].count("ring it home") == 1                 # no doubling up
    # your name instead of "mate", never twice within 25 s
    named = run("Ryan")
    assert any("Ryan" in s for s in named) and not any("mate" in s for s in named)
    g = GPStyle("Ryan")
    g.new_session(1)
    said = [g("box", "Box this lap, box this lap. Hards.", t) for t in (0.0, 1.0, 2.0)]
    assert sum("Ryan" in s for s in said) <= 1


def test_no_stray_punctuation_when_the_name_is_held_back():
    g = GPStyle(None)
    g.new_session(3)
    for i in range(60):
        s = g("racecraft_pass", "Great move on Norris, P three.", float(i))
        assert not re.search(r",\s*[.!?]|\s,|,,", s), s
