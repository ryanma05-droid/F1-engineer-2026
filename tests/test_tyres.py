import io

import numpy as np
import pytest

from engineer.app import build_engine
from engineer.config import Config, Tyres
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.tools.queries import TyreStatus, tyre_status
from tests.synth import FakeRace


def run(race, tyres: Tyres | None = None, priors=None, **frames_kw):
    cfg = Config(data_dir="/nonexistent-test-dir", tyres=tyres or Tyres())
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=priors or WearPriors())
    for t_ns, raw in race.frames(**frames_kw):
        eng.pipeline.feed(raw, t_ns)
    radio = [line.split("s  ", 1)[1].split("  (held")[0]
             for line in out.getvalue().splitlines() if line.startswith("RADIO")]
    return eng, radio


def tyre_calls(radio):
    return [r for r in radio if not r.startswith((
        "Lap ", "Thirty", "Lap invalid", "Plan ", "Five laps to go", "Last lap", "P one",
        "Chequered", "Great start", "Lost ", "DRS range"))]


def _status(**kw):
    base = dict(car_idx=0, lap=6, stint=1, stint_lap=6, visual_compound=16,
                wear=np.array([20.0, 21.0, 28.0, 30.0]), wear_rate=np.array([3.3, 3.5, 4.6, 5.0]),
                rate_from_history=False, worst_wheel=3, worst_pct=30.0, limit_pct=60.0,
                laps_left=6, limiting_wheel=3, damage=np.zeros(4), blisters=np.zeros(4))
    base.update(kw)
    return TyreStatus(**base)


def test_phrases():
    st = _status()
    assert phrases.tyre_status_phrase(st) == \
        "Tyres: front right worst at thirty percent, about six laps left on these."
    early = _status(worst_pct=11.0, wear=np.array([8.0, 7, 10, 11]), wear_rate=np.full(4, 4.0))
    assert phrases.tyre_status_phrase(early) == \
        "Tyres: front right worst at eleven percent, about four percent a lap."
    assert phrases.tyre_status_phrase(st, "terse") == \
        "Front right thirty percent, about six laps left on these."
    assert phrases.tyre_warning_phrase(_status(worst_pct=51.0, laps_left=1), 50) == \
        "Front right at fifty percent, look after it, about one lap left on these."
    assert phrases.tyre_temp_phrase("hot", [0, 1], 3) == "Rears running hot in sector three."
    assert phrases.tyre_temp_phrase("cold", [2], 1) == "Front left is cold in sector one."
    assert phrases.tyre_dropoff_phrase(230) == "Pace dropping two tenths a lap, tyres are going."
    assert phrases.tyre_damage_phrase(2, 23) == "Front left tyre damage, twenty three percent."
    assert phrases.say_laps_left(0) == "they're at the limit"
    even = _status(wear=np.full(4, 5.0), worst_wheel=0, worst_pct=5.0, wear_rate=np.full(4, 2.0))
    assert phrases.tyre_status_phrase(even) == \
        "Tyres all even at five percent, about two percent a lap."


def test_status_every_three_laps_then_laps_left_and_warning():
    # Front right wears 5 % a lap: 15 % after lap 3, 30 % after lap 6, 40 % after lap 8.
    race = FakeRace({0: [30000] * 9}, wear_per_lap=[3, 3, 4, 5], extra_packets=False)
    eng, radio = run(race, duration_ms=9 * 30000 + 8000)
    calls = tyre_calls(radio)
    # Wear comes in whole percent and a few frames either side of the line, so the 3-lap fit
    # is a little noisy: five or six percent a lap.
    assert calls[0].startswith("Tyres: front right worst at fifteen percent, about ")
    assert calls[1:] == [
        "Tyres: front right worst at thirty percent, about six laps left on these.",
        "Front right at forty percent, look after it, about four laps left on these.",
        "Tyres: front right worst at forty five percent, about three laps left on these.",
    ]
    st = eng.tyres.status()
    assert st.laps_left == 3 and st.limiting_wheel == 3
    assert st.wear_rate[3] == pytest.approx(5, abs=0.3)


def test_laps_left_threshold_is_configurable():
    race = FakeRace({0: [30000] * 3}, wear_per_lap=[3, 3, 4, 5], extra_packets=False)
    _eng, radio = run(race, Tyres(laps_left_from_pct=10), duration_ms=3 * 30000 + 8000)
    [call] = tyre_calls(radio)
    assert call.startswith("Tyres: front right worst at fifteen percent, about ")
    assert call.endswith("laps left on these.")


def test_history_used_while_stint_is_short():
    race = FakeRace({0: [30000] * 2}, wear_per_lap=[3, 3, 4, 5], extra_packets=False)
    priors = WearPriors({(10, 18): np.array([1.0, 1.0, 2.0, 2.5])})
    eng, _radio = run(race, priors=priors)
    st = eng.tyres.status()
    assert st.stint_lap == 2 and st.rate_from_history
    assert st.wear_rate[3] == 2.5
    assert tyre_status(eng.features, 0).rate_from_history is False   # no priors given


def test_hot_rears_in_sector_two_called_once():
    def temp(car, lap, sector):
        return [125, 125, 95, 95] if lap in (3, 4) and sector == 1 else [90, 90, 90, 90]
    race = FakeRace({0: [30000] * 5}, inner_temp=temp, extra_packets=False)
    _eng, radio = run(race)
    assert tyre_calls(radio).count("Rears running hot in sector two.") == 1


def test_cold_tyres_on_race_lap_one_are_not_called():
    race = FakeRace({0: [30000] * 2}, extra_packets=False,
                    inner_temp=lambda car, lap, sector: [60] * 4 if lap == 1 else [90] * 4)
    _eng, radio = run(race)
    assert not [r for r in radio if "cold" in r]


def test_pace_dropoff():
    race = FakeRace({0: [30000, 30000, 30300, 30600, 30900, 31200, 31500]}, extra_packets=False)
    _eng, radio = run(race, duration_ms=230000)
    assert "Pace dropping three tenths a lap, tyres are going." in radio


def test_one_slow_lap_does_not_trigger_dropoff():
    # Lap 6 has a big mistake; the laps after it are back on pace.
    race = FakeRace({0: [30000, 30000, 30000, 30000, 30000, 31900, 30000, 30000]},
                    extra_packets=False)
    _eng, radio = run(race)
    assert not [r for r in radio if "Pace dropping" in r]


def test_tyre_calls_can_be_switched_off():
    race = FakeRace({0: [30000] * 4}, wear_per_lap=[3, 3, 4, 5], extra_packets=False)
    _eng, radio = run(race, Tyres(enabled=False))
    assert tyre_calls(radio) == []
