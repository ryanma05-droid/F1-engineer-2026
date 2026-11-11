"""The live lap delta (state/live_delta.py), on generated races: FakeRace drives each lap at an
even speed, so the delta grows evenly through the lap to the lap time difference at the line.
The real race (delta at the line = the real lap difference) is checked in test_golden."""

import io
from types import SimpleNamespace

import pytest

from engineer.app import build_engine
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.state.live_delta import LiveDelta
from engineer.state.track_maps import Corner, TrackMap
from tests.synth import FakeRace

LEN = FakeRace.LAP_LEN


def drive(laps, reference="previous", duration_ms=None, frames=None, **race):
    """Returns (engine, readings): every reading as (lap, ref lap, distance, delta)."""
    cfg = Config(data_dir="/nonexistent-test-dir")
    cfg.overlay.reference = reference
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    got = []
    for t_ns, raw in FakeRace({0: laps}, **race).frames(duration_ms=duration_ms,
                                                       **(frames or {})):
        eng.pipeline.feed(raw, t_ns)
        r = eng.live_delta.current
        if r is not None:
            got.append((r.lap, r.ref_lap, r.distance_m, r.delta_s))
    return eng, got


def at(readings, lap, distance):
    """The reading of `lap` closest to `distance`."""
    return min((r for r in readings if r[0] == lap), key=lambda r: abs(r[2] - distance))


def test_delta_to_the_previous_and_the_best_lap():
    # Both references side by side on one race: "previous" (the engine's) and "best".
    cfg = Config(data_dir="/nonexistent-test-dir")
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    best = LiveDelta(eng.pipeline.state, eng.traces, eng.features, reference="best")
    eng.pipeline.after_packet.append(best.after_packet)
    got, got_best = [], []
    for t_ns, raw in FakeRace({0: [20000, 19500, 21000, 19000, 20000]}).frames():
        eng.pipeline.feed(raw, t_ns)
        for ld, out in ((eng.live_delta, got), (best, got_best)):
            if (r := ld.current) is not None:
                out.append((r.lap, r.ref_lap, r.distance_m, r.delta_s))
    assert min(r[0] for r in got) == 3              # lap 1 (standing start) is no reference
    assert all(r[1] == r[0] - 1 for r in got)
    assert at(got, 3, LEN / 2)[3] == pytest.approx(0.75, abs=0.02)   # evenly through the lap
    assert at(got, 4, LEN / 4)[3] == pytest.approx(-0.5, abs=0.02)
    for lap, real in ((3, 1.5), (4, -2.0), (5, 1.0)):
        assert eng.live_delta.lap_end[lap].delta_s == pytest.approx(real, abs=0.03)
    # Best: lap 4 against lap 2 (19.5 s), lap 5 against lap 4 (19.0 s, the new best).
    assert {r[1] for r in got_best if r[0] == 4} == {2}
    assert best.lap_end[4].delta_s == pytest.approx(-0.5, abs=0.03)
    assert {r[1] for r in got_best if r[0] == 5} == {4}
    with pytest.raises(ValueError):
        LiveDelta(None, None, None, reference="fastest")


def test_no_reading_on_pit_laps_and_invalid_laps_are_no_reference():
    # Pit at the end of lap 3: no reading from the pit entry, nor on the out lap (4). Lap 5 is
    # invalid (track limits). Laps 3, 4 and 5 are no reference: laps 5 and 6 compare with lap 2.
    _, got = drive([20000] * 6, pit_laps={0: 3}, invalid_laps={0: {5}})
    assert 4 not in {r[0] for r in got}
    assert not [r for r in got if r[0] == 3 and r[2] > LEN * 0.95]
    assert {r[1] for r in got if r[0] in (5, 6)} == {2}


def test_flashback_and_the_flag():
    # A flashback 5 s back in lap 3: the delta follows the rewound point; nothing after the flag.
    eng, got = drive([20000, 20000, 21000, 20000], chequered_flag=True, duration_ms=120000,
                     frames={"flashback_at_ms": 55000, "flashback_back_ms": 5000})
    assert eng.live_delta.lap_end[3].delta_s == pytest.approx(1.0, abs=0.03)
    last = [r for r in got if r[0] == 4]
    assert last and last[-1][2] > LEN * 0.95       # the last reading is at the line, not after


def test_corner_splits_with_a_track_map():
    # A stand-in for CornerAnalyzer: two corners. Lap 3 is 1 s slower spread evenly, so each
    # corner's split is its share of the lap.
    corners = SimpleNamespace(track_map=TrackMap(10, LEN, [
        Corner(1, "turn one", 1000.0, 1200.0, 1500.0),
        Corner(2, "turn two", 3000.0, 3200.0, 4000.0)]))
    eng2 = build_engine(Config(data_dir="/nonexistent-test-dir"), out=io.StringIO(),
                        priors=WearPriors())
    ld = LiveDelta(eng2.pipeline.state, eng2.traces, eng2.features, corners)
    eng2.pipeline.after_packet.append(ld.after_packet)
    held = []
    for t_ns, raw in FakeRace({0: [20000, 20000, 21000]}).frames(duration_ms=61000):
        eng2.pipeline.feed(raw, t_ns)
        if ld.current is not None and ld.current.corner is not None:
            held.append(ld.current.corner.corner)
    one, two = ld.splits
    assert (one.corner, two.corner) == (1, 2)
    assert one.gain_s == pytest.approx(0.1, abs=0.02)           # 500 m of 5000 m
    assert two.gain_s == pytest.approx(0.2, abs=0.02)           # 1000 m
    assert two.delta_s == pytest.approx(0.8, abs=0.02)          # running delta at 4000 m
    assert held and set(held) == {1, 2}                          # shown for a moment each
