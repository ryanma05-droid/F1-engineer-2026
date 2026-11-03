"""Wording variety (radio/variety.py): familiar calls, not the same words every race."""

import io
import json
import os
import re
import time
from collections import Counter

from engineer.app import build_engine
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.ingest.recorder import read_recording
from engineer.radio import phrases
from engineer.radio.variety import VARIETY, Variety

FIXTURE = "tests/fixtures/austria_race_5laps.zst"


def calls(cfg=None):
    out = io.StringIO()
    eng = build_engine(cfg or Config(), out=out, priors=WearPriors())
    for t, raw in read_recording(FIXTURE):
        eng.pipeline.feed(raw, t)
    return [re.sub(r"\s+\(held.*", "", line[16:]) for line in out.getvalue().splitlines()
            if line.startswith("RADIO")]


def facts(text):
    """What must survive any wording: positions, numbers said as words, box, tyres."""
    text = re.sub(r"\b(in one go|in one move|one more|last one|good one|at once)\b", "", text,
                  flags=re.I)
    words = re.findall(r"\bP \w+|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
                       r"twelve|thirteen|fourteen|fifteen|twenty|thirty|forty|fifty|sixty|"
                       r"seventy|eighty|ninety|oh)\b|\bbox\b|\b(?:softs|mediums|hards)\b|"
                       r"\b(?:Verstappen|Sainz|Piastri|Hamilton|Norris|Antonelli)\b",
                       text, re.I)
    return [w.lower() for w in words]


def test_off_is_the_old_rotation():
    v = Variety()
    v.enabled = False
    assert [v.pick("k", ["a", "b", "c"], n) for n in range(4)] == ["a", "b", "c", "a"]


def test_on_never_repeats_the_last_two():
    v = Variety()
    v.enabled = True
    seq = [v.pick("k", ["a", "b", "c", "d"]) for _ in range(40)]
    assert all(x not in seq[i - 2:i] for i, x in enumerate(seq) if i >= 2)
    assert len(set(seq)) == 4
    assert {v.pick("two", ["a", "b"]) for _ in range(6)} == {"a", "b"}


def test_replays_repeat_and_races_differ_but_keep_the_facts():
    VARIETY.enabled = False
    plain = calls(Config())
    cfg = Config()
    VARIETY.enabled = True
    os.environ["RACE_ENGINEER_VARIETY"] = "1"
    try:
        a, b = calls(cfg), calls(cfg)
        assert a == b                                   # replay: seeded from the session
        VARIETY.live = True                             # live: every race its own seed
        races = [calls(cfg) for _ in range(3)]
    finally:
        os.environ["RACE_ENGINEER_VARIETY"] = "0"
    for r in [a] + races:
        assert len(r) == len(plain)
        for said, base in zip(r, plain):
            # Every fact of the fixed wording is still said (a wording may add the name of
            # the car passed, which "Good job, that's one more" leaves out).
            missing = Counter(facts(base)) - Counter(facts(said))
            assert not missing, (said, base)
    differ = sum(len({r[i] for r in races}) > 1 for i in range(len(plain)))
    assert differ >= 8
    # Remembered across races: the start call is never the same two races running.
    starts = [r[2] for r in races]
    assert starts[0] != starts[1] != starts[2]


def test_safety_critical_words_stay():
    VARIETY.enabled = True
    for _ in range(12):
        assert phrases.box_phrase(18).startswith("Box this lap, box this lap. ")
        assert "hards" in phrases.box_phrase(18).lower()
        h = phrases.box_half_phrase(17)
        assert "box box" in h.lower() and "mediums" in h.lower()
        assert phrases.safety_car_box_phrase("safety car", 17).startswith(
            "Safety car, box this lap, box this lap.")


def test_memory_is_saved_and_loaded(tmp_path):
    v = Variety()
    v.enabled = True
    v.load(tmp_path / "radio" / "variety.json")
    v.pick("start_up", ["a", "b", "c"])
    v.save_in_background()
    path = tmp_path / "radio" / "variety.json"
    for _ in range(50):
        if path.exists():
            break
        time.sleep(0.02)
    saved = json.loads(path.read_text(encoding="utf-8"))
    w = Variety()
    w.load(path)
    assert w.recent == saved["recent"] and w.live and len(w.recent["start_up"]) == 1
