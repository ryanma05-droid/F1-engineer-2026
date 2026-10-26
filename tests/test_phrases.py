import pytest

from engineer.features.laps import LapRecord
from engineer.radio.phrases import lap_delta_phrase, say_gap, say_lap_time
from engineer.tools.queries import LapDelta


@pytest.mark.parametrize("ms,words", [
    (69162, "one oh nine one"),
    (69126, "one oh nine one"),
    (77680, "one seventeen six"),
    (59850, "fifty nine eight"),
    (59999, "fifty nine nine"),
    (60000, "one oh oh zero"),
    (125300, "two oh five three"),
])
def test_say_lap_time(ms, words):
    assert say_lap_time(ms) == words


@pytest.mark.parametrize("ms,words", [
    (60, "a tenth"), (149, "a tenth"), (230, "two tenths"), (-460, "five tenths"),
    (1000, "a second"), (1320, "one point three seconds"), (2000, "two seconds"),
    (12400, "twelve seconds"),
])
def test_say_gap(ms, words):
    assert say_gap(ms) == words


def lap(n, ms, sectors=(20000, 30000, 19000), **flags):
    return LapRecord(0, n, ms, sectors, flags.get("invalid", False), flags.get("pit_in", False),
                     flags.get("pit_out", False), flags.get("race_start", False), 0.0, None)


def test_compared_phrase_levels():
    this = lap(3, 69400, (20000, 29800, 19600))
    ref = lap(2, 69600, (20300, 29800, 19500))
    d = LapDelta(this, ref, None, -200, (-300, 0, 100), 1)
    assert lap_delta_phrase(d, "terse") == "Two tenths quicker."
    assert lap_delta_phrase(d, "normal") == "One oh nine four, two tenths quicker, gained in sector one."
    assert lap_delta_phrase(d, "chatty") == \
        "One oh nine four, two tenths quicker on lap two, gained in sector one."


def test_reference_not_previous_lap_is_said():
    d = LapDelta(lap(5, 70000), lap(3, 69500), None, 500, (500, 0, 0), 1)
    assert lap_delta_phrase(d) == "One ten zero, five tenths slower on your last clean lap, lost in sector one."


def test_same_again():
    d = LapDelta(lap(3, 69420), lap(2, 69400), None, 20, (10, 5, 5), 1)
    assert lap_delta_phrase(d) == "One oh nine four, same again."


@pytest.mark.parametrize("reason,normal,terse", [
    ("invalid", "Lap invalid, one oh nine four.", "Lap invalid."),
    ("in_lap", "In lap, one oh nine four.", "In lap."),
    ("out_lap", "Out lap, one oh nine four.", "Out lap."),
    ("race_start", "Lap three, one oh nine four.", "Lap three."),
    ("no_reference", "One oh nine four.", "One oh nine four."),
])
def test_not_compared(reason, normal, terse):
    d = LapDelta(lap(3, 69400), None, reason, None)
    assert lap_delta_phrase(d, "normal") == normal
    assert lap_delta_phrase(d, "terse") == terse
