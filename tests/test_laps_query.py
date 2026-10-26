from engineer.bus import events as ev
from engineer.features.laps import LapHistory
from engineer.state.race_state import RaceState
from engineer.tools.queries import lap_delta


def make(session_type=15):
    bus = ev.EventBus()
    state = RaceState()
    state.session.session_type = session_type
    return bus, LapHistory(bus, state)


def done(bus, lap, ms, sectors=(20000, 30000, None), t=None, **flags):
    s3 = ms - sectors[0] - sectors[1]
    bus.publish(ev.LapCompleted(1, t if t is not None else lap * 70.0, car_idx=0, lap=lap,
                                lap_time_ms=ms, sector1_ms=sectors[0], sector2_ms=sectors[1],
                                sector3_ms=s3, invalid=flags.get("invalid", False),
                                pit_in_lap=flags.get("pit_in", False),
                                pit_out_lap=flags.get("pit_out", False), position=1,
                                visual_compound=16, tyre_age_laps=lap - 1))


def test_race_lap_one_not_compared_and_not_a_reference():
    bus, h = make()
    done(bus, 1, 77680)
    assert lap_delta(h, 0).reason_not_compared == "race_start"
    done(bus, 2, 69622)
    assert lap_delta(h, 0).reason_not_compared == "no_reference"
    done(bus, 3, 69599, (20100, 29900, None))
    d = lap_delta(h, 0)
    assert d.delta_ms == -23 and d.reference.lap == 2 and d.reference_is_previous_lap


def test_quali_lap_one_can_be_reference():
    bus, h = make(session_type=5)
    done(bus, 1, 80000)
    done(bus, 2, 79000)
    assert lap_delta(h, 0).delta_ms == -1000


def test_skips_unclean_reference_and_biggest_sector():
    bus, h = make()
    done(bus, 1, 77000)
    done(bus, 2, 70000, (20000, 30000, None))
    done(bus, 3, 75000, invalid=True)
    assert lap_delta(h, 0).reason_not_compared == "invalid"
    done(bus, 4, 69500, (20100, 29200, None))
    d = lap_delta(h, 0)
    assert d.reference.lap == 2 and not d.reference_is_previous_lap
    assert d.delta_ms == -500
    assert d.sector_delta_ms == (100, -800, 200)
    assert d.biggest_sector == 2


def test_pit_laps_called_out():
    bus, h = make()
    done(bus, 1, 77000)
    done(bus, 2, 90000, pit_in=True)
    assert lap_delta(h, 0).reason_not_compared == "in_lap"
    done(bus, 3, 95000, pit_out=True)
    assert lap_delta(h, 0).reason_not_compared == "out_lap"


def test_flashback_drops_rewound_laps():
    bus, h = make()
    done(bus, 1, 77000, t=77.0)
    done(bus, 2, 70000, t=147.0)
    bus.publish(ev.Flashback(1, 150.0, to_frame=1, to_session_time=140.0))
    assert [r.lap for r in h.for_car(0)] == [1]
    done(bus, 2, 69900, t=146.0)
    assert [r.lap_time_ms for r in h.for_car(0)] == [77000, 69900]
