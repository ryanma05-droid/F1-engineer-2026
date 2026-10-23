import math

import numpy as np

from engineer.ingest import formats
from engineer.state.race_state import RaceState
from tests.synth import PacketFactory


def apply(state, raw):
    _m, header, pid, rec = formats.dispatch(raw)
    return state.apply(header, pid, rec)


def test_session_info():
    s = RaceState()
    pf = PacketFactory()
    apply(s, pf.session(track_id=10, session_type=15, total_laps=44, m_safetyCarStatus=2,
                        m_pitStopWindowIdealLap=18))
    assert s.session.track_name == "Spa"
    assert s.session.session_name == "Race"
    assert s.session.total_laps == 44
    assert s.session.safety_car_status == 2
    assert s.session.pit_window_ideal_lap == 18


def test_inactive_cars_are_nan():
    s = RaceState()
    pf = PacketFactory()
    apply(s, pf.lap_data({0: {"carPosition": 1, "currentLapNum": 3}, 1: {"carPosition": 2}}))
    assert s.cars.active[0] and s.cars.active[1] and not s.cars.active[2]
    assert s.cars.position[0] == 1
    assert math.isnan(s.cars.position[2])


def test_restricted_telemetry_is_unknown_not_zero():
    s = RaceState()
    pf = PacketFactory(player=0)
    # car 0 = player (always visible), car 1 public, car 2 restricted
    apply(s, pf.participants(num_active=3, telemetry_public=[0, 1, 0]))
    apply(s, pf.car_status({0: {"fuelInTank": 50.0, "visualTyreCompound": 16},
                            1: {"fuelInTank": 40.0, "visualTyreCompound": 17},
                            2: {"fuelInTank": 0.0, "visualTyreCompound": 18}}))
    apply(s, pf.car_damage({0: {"tyresWear": [1, 2, 3, 4]},
                            1: {"tyresWear": [5, 6, 7, 8]},
                            2: {"tyresWear": [0, 0, 0, 0], "tyreBlisters": [1, 0, 0, 0]}}))
    c = s.cars
    assert c.fuel_in_tank[0] == 50.0
    assert c.fuel_in_tank[1] == 40.0
    assert math.isnan(c.fuel_in_tank[2])
    assert c.visual_compound[2] == 18           # compound is not restricted
    assert np.isnan(c.tyre_wear_pct[2]).all()
    assert c.tyre_blisters_pct[2, 0] == 1        # blisters are not restricted
    np.testing.assert_array_equal(c.tyre_wear_pct[1], [5, 6, 7, 8])
    assert math.isnan(c.damage["floor"][2])
    assert s.player.fuel_in_tank == 50.0
    np.testing.assert_array_equal(s.player.tyre_wear_pct, [1, 2, 3, 4])  # RL, RR, FL, FR


def test_restricted_before_participants_known():
    s = RaceState()
    pf = PacketFactory(player=0)
    apply(s, pf.car_status({0: {"fuelInTank": 50.0}, 1: {"fuelInTank": 40.0}}))
    assert s.cars.fuel_in_tank[0] == 50.0
    assert math.isnan(s.cars.fuel_in_tank[1])


def test_new_session_uid_resets_state():
    s = RaceState()
    apply(s, PacketFactory(session_uid=1).session(track_id=10))
    assert apply(s, PacketFactory(session_uid=2).lap_data({0: {}})) is True
    assert s.session.track_id is None
    assert s.session_uid == 2


def test_menu_packets_ignored():
    s = RaceState()
    assert apply(s, PacketFactory(session_uid=0).session()) is False
    assert s.session_uid is None


def test_grid_order_check_spec_and_swapped():
    s = RaceState()
    pf = PacketFactory()
    apply(s, pf.session(session_type=15))
    apply(s, pf.lap_data({i: {"gridPosition": i + 1, "driverStatus": 4} for i in range(10)}))
    assert s.checks.grid_order == "spec"

    s = RaceState()
    apply(s, pf.session(session_type=15))
    apply(s, pf.lap_data({i: {"gridPosition": 4, "driverStatus": i + 1} for i in range(10)}))
    assert s.checks.grid_order == "swapped"


def test_flashback_recorded():
    s = RaceState()
    pf = PacketFactory()
    apply(s, pf.event("FLBK", flashbackFrameIdentifier=500, flashbackSessionTime=12.5))
    assert s.flashbacks.count == 1 and s.flashbacks.last_frame == 500
