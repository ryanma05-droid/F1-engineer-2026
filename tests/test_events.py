from engineer.bus import events as ev
from engineer.pipeline import Pipeline
from tests.synth import FakeRace


def run(race: FakeRace, **kw):
    race.extra = False  # Motion/History filler packets don't affect these events; skip for speed
    p = Pipeline()
    got = []
    p.bus.subscribe(None, got.append)
    for t_ns, raw in race.frames(**kw):
        p.feed(raw, t_ns)
    return p, got


def of(got, kind, car=None):
    return [e for e in got if isinstance(e, kind) and (car is None or e.car_idx == car)]


def test_session_started_once_with_track():
    _, got = run(FakeRace({0: [30000, 29000]}))
    started = of(got, ev.SessionStarted)
    assert len(started) == 1
    assert started[0].track_id == 10 and started[0].session_type == 15


def test_lap_completed_with_times_and_sectors():
    _, got = run(FakeRace({0: [30000, 29500, 31000], 1: [31000, 30500, 30000]}))
    laps = of(got, ev.LapCompleted, car=0)
    assert [(e.lap, e.lap_time_ms) for e in laps] == [(1, 30000), (2, 29500), (3, 31000)]
    e = laps[1]
    assert (e.sector1_ms, e.sector2_ms, e.sector3_ms) == (8850, 11800, 8850)
    assert e.visual_compound == 16
    assert [(e.lap, e.lap_time_ms) for e in of(got, ev.LapCompleted, car=1)] == \
        [(1, 31000), (2, 30500), (3, 30000)]


def test_sector_events_in_order():
    _, got = run(FakeRace({0: [30000, 30000]}))
    secs = [(e.lap, e.sector) for e in of(got, ev.SectorCompleted, car=0)]
    assert secs[:6] == [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
    s1 = of(got, ev.SectorCompleted, car=0)[0]
    assert s1.sector_time_ms == 9000


def test_invalid_lap_flagged_and_not_carried_over():
    _, got = run(FakeRace({0: [30000, 30000, 30000]}, invalid_laps={0: {2}}))
    assert [e.invalid for e in of(got, ev.LapCompleted, car=0)] == [False, True, False]


def test_late_last_lap_time_still_reports_correct_time():
    _, got = run(FakeRace({0: [30000, 29000, 28000]}, last_lap_delay_frames=3))
    assert [e.lap_time_ms for e in of(got, ev.LapCompleted, car=0)] == [30000, 29000, 28000]


def test_pit_entry_exit_and_in_out_laps():
    _, got = run(FakeRace({0: [30000, 30000, 30000]}, pit_laps={0: 1}))
    assert len(of(got, ev.PitEntry, car=0)) == 1
    assert len(of(got, ev.PitExit, car=0)) == 1
    laps = of(got, ev.LapCompleted, car=0)
    assert laps[0].pit_in_lap and not laps[0].pit_out_lap
    assert laps[1].pit_out_lap
    assert not laps[2].pit_in_lap and not laps[2].pit_out_lap


def test_flashback_does_not_double_count_laps():
    # Flash back 20 s from 35 s into the session, i.e. from lap 2 into lap 1.
    race = FakeRace({0: [30000, 30000, 30000]})
    _, got = run(race, flashback_at_ms=35000, duration_ms=95000)
    assert len(of(got, ev.Flashback)) == 1
    laps = of(got, ev.LapCompleted, car=0)
    # Lap 1 is completed twice (before and after the rewind), but never lap 2 early or twice.
    assert [e.lap for e in laps] == [1, 1, 2]


def test_session_end_on_new_uid():
    p = Pipeline()
    got = []
    p.bus.subscribe(None, got.append)
    for t, raw in FakeRace({0: [30000]}, session_uid=1).frames(duration_ms=2000):
        p.feed(raw, t)
    for t, raw in FakeRace({0: [30000]}, session_uid=2).frames(duration_ms=2000):
        p.feed(raw, t)
    ended = of(got, ev.SessionEnded)
    assert len(ended) == 1 and ended[0].session_uid == 1 and ended[0].reason == "new_session"
    assert [e.session_uid for e in of(got, ev.SessionStarted)] == [1, 2]
