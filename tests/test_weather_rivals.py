"""12 Nov, from the real team radio: weather forecasts, other drivers' penalties, rival
information (analyzers/weather.py, analyzers/rivals.py, tools/rivals.py)."""

import numpy as np

from engineer.analyzers.rivals import RivalsAnalyzer
from engineer.analyzers.weather import WeatherAnalyzer
from engineer.bus import events as ev
from engineer.features.laps import LapHistory
from engineer.ingest.formats import f1_2025 as f
from engineer.radio import phrases
from engineer.radio.director import RadioDirector
from engineer.state.race_state import RaceState
from engineer.tools.rivals import gap_between, next_weather_change
from engineer.voice.intents import match

SESSION = f.PacketId.SESSION


def rig():
    bus = ev.EventBus()
    st = RaceState()
    st.session_uid, st.player_index = 1, 0
    st.session.session_type, st.session.total_laps = 15, 20     # race
    c = st.cars
    c.active[:3] = True
    c.position[:3] = [2, 1, 3]                 # you P2, car 1 ahead, car 2 behind
    c.delta_to_car_in_front_ms[:3] = [1500, 0, 900]
    c.lap[:3] = 6
    c.last_lap_time_ms[:3] = 90000
    c.pit_status[:3] = 0
    c.num_pit_stops[:3] = 0
    c.penalties_s[:3] = 0
    c.names[:3] = ["PLAYER", "LECLERC", "NORRIS"]
    said = []
    d = RadioDirector(bus, st)
    bus.subscribe(ev.RadioMessage, lambda e: said.append(e.text))
    st.session_time = 500.0
    return bus, st, d, said


def later(st, d, s=30.0):
    """Move the clock on, so the call before has been said."""
    st.session_time += s
    d.after_packet(None)


def test_rain_coming_then_soon_then_it_starts():
    bus, st, d, said = rig()
    w = WeatherAnalyzer(bus, st, d)
    st.session.weather = 1
    st.session.forecast = [(15, 5, 1, 10), (15, 10, 3, 60), (15, 15, 3, 70)]
    w.after_packet(SESSION)
    w.after_packet(SESSION)                                   # sent twice a second: said once
    assert said == ["Rain expected in about ten minutes, around lap twelve. Sixty percent chance."]
    later(st, d)
    st.session.forecast = [(15, 5, 3, 65), (15, 10, 3, 70)]
    w.after_packet(SESSION)
    later(st, d)
    assert said[-1] == "Rain about five minutes away. Sixty five percent chance."
    st.session.weather = 3                                     # here
    w.after_packet(SESSION)
    later(st, d)
    assert said[-1].startswith("Rain's starting, ")


def test_forecast_clears_only_once_the_chance_has_really_dropped():
    bus, st, d, said = rig()
    w = WeatherAnalyzer(bus, st, d)
    st.session.weather = 1
    st.session.forecast = [(15, 10, 2, 45)]
    w.after_packet(SESSION)
    assert said and said[0].startswith("Rain expected")
    st.session.forecast = [(15, 10, 2, 35)]                   # hovering: no flip-flop
    w.after_packet(SESSION)
    assert len(said) == 1
    st.session.forecast = [(15, 10, 1, 10)]
    w.after_packet(SESSION)
    later(st, d)
    assert said[-1].startswith("Forecast's cleared")


def test_rain_easing_while_wet_and_other_sessions_ignored():
    bus, st, d, said = rig()
    st.session.weather = 3
    st.session.forecast = [(10, 5, 1, 0), (15, 5, 3, 70), (15, 10, 1, 15)]   # 10 = another session
    change = next_weather_change(st)
    assert change.kind == "dry" and change.minutes == 10
    WeatherAnalyzer(bus, st, d).after_packet(SESSION)
    assert said[0].startswith("Rain easing in about ten minutes, around lap twelve.")


def _laps(bus, car, times, first=2):
    for i, t in enumerate(times):
        bus.publish(ev.LapCompleted(1, 0.0, car_idx=car, lap=first + i, lap_time_ms=t,
                                    sector1_ms=None, sector2_ms=None, sector3_ms=None,
                                    invalid=False, pit_in_lap=False, pit_out_lap=False,
                                    position=None, visual_compound=17, tyre_age_laps=None))


def test_a_penalty_ahead_and_what_it_means():
    bus, st, d, said = rig()
    hist = LapHistory(bus, st)
    RivalsAnalyzer(bus, st, hist, d)
    st.cars.penalties_s[1] = 5                                 # Leclerc ahead, 1.5 s up
    _laps(bus, 0, [90000] * 3)
    assert said == [("Leclerc ahead has a five second penalty. You're one point five seconds "
                     "behind, inside it. Stay there and you're ahead.")]
    _laps(bus, 0, [90000], first=5)
    assert len(said) == 1                                      # once per penalty


def test_struggling_car_ahead_and_one_about_to_pit():
    bus, st, d, said = rig()
    hist = LapHistory(bus, st)
    RivalsAnalyzer(bus, st, hist, d)
    c = st.cars
    c.visual_compound[1], c.tyre_age_laps[1] = 17, 15
    _laps(bus, 1, [89000, 89400, 89800, 90200])                # Leclerc slowing 0.4 s a lap
    _laps(bus, 2, [90100, 90100, 90100, 90100])
    _laps(bus, 0, [89500, 89500, 89500, 89500])
    assert said and said[0].startswith(
        "Leclerc ahead is struggling, losing four tenths a lap on fifteen-lap-old mediums.")
    c.tyre_wear_pct[2] = [48, 50, 40, 41]                      # Norris near the 60 % limit
    later(st, d)
    _laps(bus, 0, [89500], first=6)
    later(st, d)
    assert said[-1] == "Expect Norris behind to pit soon, tyres at fifty percent."
    n = len(said)
    _laps(bus, 0, [89500], first=7)
    later(st, d)
    assert len(said) == n                                      # once per car per stint


def test_gap_between_cars():
    _, st, _, _ = rig()
    assert gap_between(st, 0, 2) == 0.9 and gap_between(st, 0, 1) == -1.5


def test_questions_and_phrases():
    assert match("How's Norris looking?").name == "rival"
    assert match("What tyres is Leclerc on?").name == "rival"
    assert match("Any penalties?").name == "penalties"
    assert match("How many warnings?").name == "penalties"
    assert match("Has Albon pitted?").name == "driver_pitted"
    assert phrases.track_limits_warning_phrase(3).startswith("Track limits, that's three warnings.")
    assert phrases.penalties_answer_phrase(2, 0, [("Leclerc", "ahead", 5)]) == \
        "You've got two track limit warnings, no penalty. Leclerc ahead has five seconds."
    assert phrases.rival_penalty_phrase("Norris", "behind", 5, 0.9).endswith(
        "stay within five seconds and you finish ahead.")
    assert np.isnan(RaceState().cars.corner_cutting_warnings[0])


def test_the_answers_through_the_engine():
    import io

    from engineer.app import build_engine
    from engineer.config import Config
    from engineer.features.priors import WearPriors
    eng = build_engine(Config(data_dir="/nonexistent-test-dir"), out=io.StringIO(),
                       priors=WearPriors())
    st = eng.pipeline.state
    st.session_uid, st.player_index = 1, 0
    st.session.session_type, st.session.total_laps = 15, 20
    st.session.weather, st.session.track_temp_c, st.session.air_temp_c = 1, 30, 22
    st.session.forecast = [(15, 10, 3, 60)]
    c = st.cars
    c.active[:3] = True
    c.position[:3] = [2, 1, 3]
    c.delta_to_car_in_front_ms[:3] = [1500, 0, 900]
    c.lap[:3], c.last_lap_time_ms[:3] = 6, 90000
    c.penalties_s[:3] = [0, 5, 0]
    c.corner_cutting_warnings[0] = 2
    c.names[:3] = ["PLAYER", "LECLERC", "NORRIS"]
    c.visual_compound[1], c.tyre_age_laps[1], c.num_pit_stops[1] = 17, 12, 0
    said = []
    eng.pipeline.bus.subscribe(ev.RadioMessage, lambda e: said.append(e.text)
                               if e.topic == "answer" else None)
    for q in ("Will it rain?", "Any penalties?", "How's Leclerc looking?"):
        st.session_time = (st.session_time or 0) + 30
        eng.pipeline.bus.publish(ev.QuestionAsked(1, st.session_time, text=q, stt_ms=1000))
        eng.director.after_packet(None)
    assert said[0].endswith("Rain expected in about ten minutes, around lap twelve. Sixty "
                            "percent chance.")
    assert said[1] == ("You've got two track limit warnings, no penalty. Leclerc ahead has "
                       "five seconds.")
    assert said[2].startswith("Leclerc, P one, one point five seconds ahead. Twelve-lap-old "
                              "mediums, no stops yet.")
    assert said[2].endswith("Carrying a five second penalty.")
