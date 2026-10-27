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


def test_safety_car_lap_is_not_a_reference():
    import io

    from engineer.app import build_engine
    from engineer.config import Config
    from engineer.features.priors import WearPriors
    from tests.synth import FakeRace

    class SCRace(FakeRace):
        """Safety car out during lap 3 (60-90 s) of a 30 s-a-lap race."""
        def frames(self, **kw):
            for t_ns, raw in super().frames(**kw):
                yield t_ns, raw
                if 60.0 <= self.pf.session_time < 90.0 and self.pf.frame % 30 == 0:
                    yield t_ns, self.pf.session(track_id=self.track_id, session_type=15,
                                                total_laps=5, m_safetyCarStatus=1)

    out = io.StringIO()
    eng = build_engine(Config(data_dir="/nonexistent-test-dir"), out=out, priors=WearPriors())
    race = SCRace({0: [30000, 30000, 45000, 30500, 30400]}, extra_packets=False)
    for t_ns, raw in race.frames():
        eng.pipeline.feed(raw, t_ns)
    laps = eng.history.for_car(0)
    assert [r.safety_car for r in laps] == [False, False, True, False, False]
    assert lap_delta(eng.history, 0, 3).reason_not_compared == "safety_car"
    assert lap_delta(eng.history, 0, 4).reference.lap == 2     # not the SC lap
    assert "Safety car lap, forty five zero." in out.getvalue()
