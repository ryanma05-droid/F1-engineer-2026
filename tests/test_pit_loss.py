"""Pit loss from the game's pit lane timer (features/pit_loss.py) and real F1 figures
(strategy/real_pit_loss.py)."""

import json

import numpy as np

from engineer.bus.events import EventBus
from engineer.features.pit_loss import PitLossMeter, samples_from
from engineer.ingest.formats import f1_2025 as f
from engineer.state.race_state import RaceState
from engineer.strategy.model import ModelSettings, build_model
from engineer.strategy.real_pit_loss import fetch, load_real, race_loss, save, track_of
from tests.test_strategy import stint_rows

LAP_M, SPEED = 1000.0, 50.0            # 20 s laps


def drive(stops: dict[int, tuple[int, float]], laps=10, cars=3, sc_lap=None, dt=0.05):
    """Cars at 50 m/s; stops: car -> (lap, seconds stationary). The pit lane runs from 950 m to
    50 m of the next lap at 20 m/s (5 s instead of 2 s at speed)."""
    state, bus = RaceState(), EventBus()
    state.session.session_type, state.session.track_length_m = 15, int(LAP_M)
    state.session_uid = 1
    m = PitLossMeter(bus, state)
    c = state.cars
    c.active[:cars] = True
    dist = np.zeros(cars)
    held = np.zeros(cars)
    lane_t = np.zeros(cars)
    t = 0.0
    while dist.min() < laps * LAP_M:
        t += dt
        state.session_time = t
        for i in range(cars):
            lap = int(dist[i] // LAP_M) + 1
            stop = stops.get(i)
            in_lane = stop is not None and (stop[0] - 1) * LAP_M + 950 <= dist[i] \
                < stop[0] * LAP_M + 50
            if in_lane:
                lane_t[i] += dt
                box = stop[0] * LAP_M
                if dist[i] >= box and held[i] < stop[1]:
                    held[i] += dt                       # stationary in the box
                else:
                    dist[i] += 20.0 * dt
            else:
                dist[i] += SPEED * dt
            c.total_distance_m[i] = dist[i]
            c.lap[i] = lap
            c.pit_lane_timer_active[i] = 1.0 if in_lane else 0.0
            c.pit_lane_time_ms[i] = lane_t[i] * 1000 if in_lane else 0.0
            c.pit_stop_timer_ms[i] = held[i] * 1000 if in_lane else 0.0
        if sc_lap is not None:
            sc = (sc_lap - 1) * LAP_M - 200 <= dist[0] < sc_lap * LAP_M + 300
            state.session.safety_car_status = 1 if sc else 0
            m.after_packet(f.PacketId.SESSION)
        m.after_packet(f.PacketId.LAP_DATA)
    return m


def test_the_timer_measures_the_stop_against_the_cars_own_laps():
    m = drive({0: (5, 3.0), 1: (6, 2.5)})
    stops = m.measure()
    assert [(s.car_idx, s.lap) for s in stops] == [(0, 5), (1, 6)]
    s0 = stops[0]
    assert abs(s0.lane_s - 8.0) < 0.2 and abs(s0.stop_s - 3.0) < 0.1 and not s0.sc
    # 100 m at 20 m/s (5 s) + 3 s stationary, instead of 2 s at racing speed: 6 s lost.
    assert abs(s0.loss_s - 6.0) < 0.2
    assert abs(stops[1].loss_s - 5.5) < 0.2
    green, sc = m.samples()
    assert len(green) == 2 and sc == []


def test_a_long_stop_is_left_out_and_rows_round_trip():
    m = drive({0: (5, 3.0), 1: (6, 2.5), 2: (4, 9.0)})          # car 2: a wing change
    green, _ = m.samples()
    assert len(green) == 2 and max(green) < 7
    rows = m.rows("abc", 0)
    assert len(rows) == 3 and rows[0]["session_uid"] == "abc" and rows[0]["track_id"] == 0
    assert samples_from(rows) == (green, [])


def test_safety_car_stop_against_the_other_cars():
    m = drive({0: (5, 3.0)}, cars=5, sc_lap=5)
    s = m.measure()[0]
    assert s.sc and s.loss_s is not None
    green, sc = m.samples()
    assert green == [] and len(sc) == 1


def test_flashback_forgets_the_future():
    m = drive({0: (5, 3.0)})
    m.state.session_time = 60.0                                  # back before the stop
    m._rewind()
    assert m.stops == [] and all(not tl or tl[-1] <= 60.0 for tl in m.times)


def test_model_prefers_the_timer_then_real_f1_then_the_default():
    rows = stint_rows(track=0)
    cfg = ModelSettings(pit_loss_s=22.0)
    base = build_model(rows, rows, [], None, cfg)
    assert base.pit_loss.value == 22.0
    real = build_model(rows, rows, [], None, cfg, real_pit_s=19.0)
    assert abs(real.pit_loss.value - (19.0 * 3 + 22.0) / 4) < 1e-6
    timed = build_model(rows, rows, [], None, cfg, timer=([18.9] * 19, []), real_pit_s=21.0)
    assert abs(timed.pit_loss.value - (18.9 * 19 + 21.0 * 3 + 22.0) / 23) < 1e-6
    assert any("pit lane timer" in s for s in timed.pit_loss.sources)


def test_real_f1_from_openf1_rows(tmp_path):
    laps = []
    for drv in (1, 44):
        for n in range(1, 21):
            laps.append({"driver_number": drv, "lap_number": n, "lap_duration": 80.0,
                         "is_pit_out_lap": False})
    # Driver 1 stops end of lap 10: in-lap 85 s, out-lap 96 s -> 21 s lost.
    laps[9]["lap_duration"], laps[10]["lap_duration"] = 85.0, 96.0
    laps[10]["is_pit_out_lap"] = True
    assert race_loss(laps) == (21.0, 1)
    assert track_of({"circuit_short_name": "Melbourne"}) == 0
    assert track_of({"circuit_short_name": "Spielberg", "country_name": "Austria"}) == 17
    assert track_of({"circuit_short_name": "Catalunya", "country_name": "Spain"}) == 4

    def get(url):
        if "sessions" in url:
            return [{"session_key": 9, "circuit_short_name": "Melbourne", "location": "Melbourne"}]
        return laps
    table = fetch(get, years=(2024,), pause_s=0, progress=lambda m: None)
    assert table == {0: {"loss_s": 21.0, "races": 1, "stops": 1}}
    path = tmp_path / "real_pit_loss.json"
    save(table, path)
    assert load_real(path) == {0: 21.0}
    assert json.loads(path.read_text(encoding="utf-8"))["source"].startswith("OpenF1")
    assert load_real(tmp_path / "missing.json") == {}


def test_several_races_in_one_recording_keep_their_stops():
    """29 Oct: Bahrain, Abu Dhabi and Imola in one recording under one session UID."""
    from types import SimpleNamespace
    m = drive({0: (5, 3.0), 1: (6, 2.5)})
    m._track, m._uid = 14, 7
    m._new_session(SimpleNamespace(track_id=27, session_uid=7))
    assert m.stops == [] and len(m.done_rows) == 2
    assert {r["track_id"] for r in m.done_rows} == {14}
    assert m.rows("x", 27) == m.done_rows                    # nothing timed at Imola yet
