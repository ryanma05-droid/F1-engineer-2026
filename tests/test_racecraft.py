import io

from engineer.app import build_engine
from engineer.bus import events as ev
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall
from engineer.strategy.runner import InlineRunner
from tests.synth import FakeRace


def run(race, events=None, duration_ms=None, moves=None):
    """Feed the race; events: {game time s: (code, details)} published as GameEvents;
    moves: {game time s: positions} changes the running order from then on."""
    cfg = Config(data_dir="/nonexistent-test-dir")
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors(), strategy_runner=InlineRunner(None))
    todo = sorted((events or {}).items())
    order = sorted((moves or {}).items())
    st = eng.pipeline.state
    for t_ns, raw in race.frames(duration_ms=duration_ms):
        eng.pipeline.feed(raw, t_ns)
        while order and (st.session_time or 0) >= order[0][0]:
            race.positions = order.pop(0)[1]
        while todo and (st.session_time or 0) >= todo[0][0]:
            _, (code, details) = todo.pop(0)
            eng.pipeline.bus.publish(ev.GameEvent(st.session_uid, st.session_time,
                                                  code=code, details=details))
    radio = [line.split("s  ", 1)[1].split("  (held")[0]
             for line in out.getvalue().splitlines() if line.startswith("RADIO")]
    return eng, radio


def ovtk(a, b):
    return ("OVTK", {"overtakingVehicleIdx": a, "beingOvertakenVehicleIdx": b})


def test_start_finish_and_countdown():
    # Player starts from the front (grid slot 0), runs P3 all race (positions are static). The
    # last lap must differ from the one before: at the flag the lap number doesn't change, only
    # the last-lap time does.
    race = FakeRace({0: [30000] * 7 + [30300], 1: [29900] * 8, 2: [29800] * 8},
                    positions={0: 3, 1: 2, 2: 1}, names=["PLAYER", "NORRIS", "PIASTRI"],
                    extra_packets=False, chequered_flag=True)
    _, radio = run(race, duration_ms=250000)
    assert "Lost two places at the start. Long way to go, we'll get them back!" in radio
    assert "Three laps to go, P three." not in radio            # only five and one
    assert any(r.startswith("Last lap, P three. Bring it home!") for r in radio)
    assert "Chequered flag, P three. Podium. Great drive." in radio


def test_a_pass_is_called_once_it_sticks_and_not_if_swapped_back():
    race = FakeRace({0: [30000] * 4, 1: [30500] * 4}, positions={0: 1, 1: 2},
                    names=["PLAYER", "NORRIS"], extra_packets=False)
    _, radio = run(race, {35: ovtk(0, 1)}, duration_ms=60000)
    assert "Great move on Norris! P one." in radio
    # Reported as Norris passing us, but we're still ahead 4 s later: no call.
    _, radio = run(race, {35: ovtk(1, 0)}, duration_ms=60000)
    assert not any("Norris" in r for r in radio)
    # Lap 1 swaps aren't called one by one (the start summary covers them).
    _, radio = run(race, {10: ovtk(0, 1)}, duration_ms=30000)
    assert not any("move" in r for r in radio)


def test_penalty_fastest_lap_and_safety_car_in():
    race = FakeRace({0: [30000] * 5, 1: [30500] * 5}, extra_packets=False)
    pena = ("PENA", {"penaltyType": 5, "infringementType": 27, "vehicleIdx": 0,
                     "otherVehicleIdx": 255, "time": 255, "lapNum": 2, "placesGained": 0})
    scar = ("SCAR", {"safetyCarType": 1, "eventType": 1})
    _, radio = run(race, {
        35: pena,
        62: ("FTLP", {"vehicleIdx": 1, "lapTime": 30.5}),
        95: ("FTLP", {"vehicleIdx": 0, "lapTime": 30.0}),       # taken from someone else
        100: scar, 100.5: scar,                                 # sent twice: said once
    }, duration_ms=110000)
    assert "Warning for track limits, that's the first." in radio
    assert "Fastest lap of the race, thirty zero." in radio
    assert radio.count("Safety car in this lap, get the tyres ready.") == 1


def test_phrases():
    assert phrases.start_phrase(8) == "Great start, up eight places!"
    assert phrases.pass_phrase(["Norris", "Piastri"], 5) == "Two cars in one go! P five."
    assert phrases.lost_place_phrase("Leclerc", 4) == "Lost P three to Leclerc. Stay with Leclerc, you'll get another go!"
    assert phrases.defend_phrase("Leclerc", 800) == \
        "Leclerc behind, eight tenths and closing, DRS range. Defend!"
    assert phrases.attack_phrase("Norris", 2100, 300, 4) == (
        "Norris ahead, two point one seconds. Catching three tenths a lap, on Norris in about "
        "four laps. Keep pushing!")
    assert phrases.drs_range_phrase("Norris") == "DRS range on Norris. Go get Norris!"
    assert phrases.finish_phrase(1, 3) == "P one! You've won it! Brilliant drive."
    assert phrases.finish_phrase(9, -2) == "Chequered flag, P nine. Tough one, we'll learn from it."
    assert phrases.penalty_phrase(4, 3, 5) == "Five second time penalty, for a collision."
    assert phrases.penalty_phrase(10, 25, 255) is None          # lap invalidated: lap call says it


def test_important_calls_are_said_when_the_session_ends():
    race = FakeRace({0: [30000] * 2}, extra_packets=False)
    eng, _ = run(race, duration_ms=5000)
    d = eng.director
    said = []
    eng.pipeline.bus.subscribe(ev.RadioMessage, lambda e: said.append(e.text))
    d.speaking_until = 1e9                                       # busy: everything queues
    d.submit(RadioCall("racecraft_finish", "Chequered flag.", Priority.HIGH, max_wait_s=20,
                       wait_for_straight=False))
    d.submit(RadioCall("corner", "Turn four.", Priority.LOW, max_wait_s=20))
    eng.pipeline.bus.publish(ev.SessionEnded(0, 5.0, reason="SEND"))
    assert said == ["Chequered flag."]


def _duel():
    return FakeRace({0: [30000] * 4, 1: [30500] * 4}, positions={0: 1, 1: 2},
                    names=["PLAYER", "NORRIS"], extra_packets=False)


def test_a_lost_place_waits_longer_and_a_quick_fight_back_is_praised():
    # Norris gets by and stays ahead: called, but only after lost_hold_s (8 s), not 4 s.
    eng, radio = run(_duel(), {35: ovtk(1, 0)}, duration_ms=60000,
                     moves={35: {0: 2, 1: 1}})
    assert "Lost P one to Norris. Stay with Norris, you'll get another go!" in radio
    # Gone for 3 s, then back past before the lost call: a good defence, not a lost place.
    _, radio = run(_duel(), {35: ovtk(1, 0), 38: ovtk(0, 1)}, duration_ms=60000,
                   moves={35: {0: 2, 1: 1}, 38: {0: 1, 1: 2}})
    assert "Good fight, Norris couldn't make it stick, P one!" in radio
    assert not any(r.startswith("Lost") for r in radio)
    # Lost (and told), then straight back past: "great response", not a plain pass.
    _, radio = run(_duel(), {35: ovtk(1, 0), 47: ovtk(0, 1)}, duration_ms=65000,
                   moves={35: {0: 2, 1: 1}, 47: {0: 1, 1: 2}})
    assert "Lost P one to Norris. Stay with Norris, you'll get another go!" in radio
    assert "Straight back past Norris, P one. Great response!" in radio


def test_lost_place_wording():
    assert phrases.lost_place_phrase("Hamilton", 6, 1) == \
        "Hamilton got by, P six. Stay close, the move back is on!"
    assert phrases.lost_places_phrase(3, 9) == "Lost three places, P nine. Heads down, keep at it!"
    assert phrases.fight_back_phrase("Hamilton", 5, False, 1) == \
        "Nicely defended, back ahead of Hamilton, P five!"


def test_a_car_going_into_the_pits_is_not_a_pass():
    # Baku, 7 Nov: five cars pitted as you crossed the line and Antonelli's stop was called
    # "Good move on Antonelli, P two."
    race = FakeRace({0: [30000] * 4, 1: [29500] * 4}, positions={0: 2, 1: 1},
                    names=["PLAYER", "ANTONELLI"], extra_packets=False, pit_laps={1: 2})
    # Reported on the pit entry road (not yet in the pit lane), in the pit lane 4 s later.
    _, radio = run(race, {56.0: ovtk(0, 1)}, moves={56.0: {0: 1, 1: 2}}, duration_ms=75000)
    assert not any("Antonelli" in r for r in radio)
    # The same swap on track is a pass.
    race = FakeRace({0: [30000] * 4, 1: [29500] * 4}, positions={0: 2, 1: 1},
                    names=["PLAYER", "ANTONELLI"], extra_packets=False)
    _, radio = run(race, {56.0: ovtk(0, 1)}, moves={56.0: {0: 1, 1: 2}}, duration_ms=75000)
    assert any("Antonelli! P one" in r for r in radio)


def test_a_lap_re_driven_after_a_flashback_is_not_announced_twice():
    # Brazil, 7 Nov: a spin at turn 1, rewound to just before the line, three times on one lap;
    # the lap time was said each time.
    race = FakeRace({0: [30000, 30200, 30100, 30300]}, extra_packets=False)
    cfg = Config(data_dir="/nonexistent-test-dir")
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors(), strategy_runner=InlineRunner(None))
    laps, said = [], []
    eng.pipeline.bus.subscribe(ev.LapCompleted, lambda e: laps.append(e.lap) if e.car_idx == 0
                               else None)
    eng.pipeline.bus.subscribe(ev.RadioMessage, lambda e: said.append(e.text)
                               if e.topic == "lap_delta" else None)
    # Lap 2 ends at 60.2 s: back from 64 s to 59 s, so lap 2 is completed again.
    for t_ns, raw in race.frames(duration_ms=95000, flashback_at_ms=64000,
                                 flashback_back_ms=5000):
        eng.pipeline.feed(raw, t_ns)
    assert laps.count(2) == 2                        # the game really did complete it twice
    assert [s.lower().startswith("thirty two") for s in said].count(True) == 1, said


def test_a_long_battle_gets_encouragement():
    # 7 Nov: "more encouragement when it seems like there's a battle that is consistent".
    # Behind Norris, eight tenths, lap after lap (no closing, so no "catching" call).
    race = FakeRace({0: [30000] * 8, 1: [30000] * 8}, positions={0: 2, 1: 1},
                    names=["PLAYER", "NORRIS"], extra_packets=False, gap_ahead_ms={0: 800})
    _, radio = run(race, duration_ms=215000)          # battle laps 2-7: calls after 4 and 7
    battle = [r for r in radio if "gearbox" in r]
    assert battle and battle[0].startswith("Three laps on Norris's gearbox."), radio
    assert 2 <= sum("Norris" in r and "laps" in r for r in radio) <= 4   # repeats, not every lap
    # Norris all over you instead.
    race = FakeRace({0: [30000] * 6, 1: [30000] * 6}, positions={0: 1, 1: 2},
                    names=["PLAYER", "NORRIS"], extra_packets=False, gap_ahead_ms={1: 700})
    _, radio = run(race, duration_ms=185000)
    assert "Norris has been on you for three laps and still hasn't got by. Brilliant work, " \
           "stay calm!" in radio, radio


def test_battle_phrases():
    assert phrases.battle_phrase("Russell", "behind", 4) == \
        "Russell has been on you for four laps and still hasn't got by. Brilliant work, stay calm!"
    assert phrases.battle_phrase("Russell", "ahead", 3, swaps=3) == \
        "Proper scrap with Russell, three laps of it. Loving this, keep it clean!"


def test_a_battle_call_lost_to_a_flashback_is_made_again():
    # Brazil, 7 Nov: the call waited for a straight, a flashback cleared it, and the re-driven
    # lap thought it had been said.
    race = FakeRace({0: [30000] * 6, 1: [30000] * 6}, positions={0: 1, 1: 2},
                    names=["PLAYER", "NORRIS"], extra_packets=False, gap_ahead_ms={1: 700})
    eng, _ = run(race, duration_ms=100000)           # laps 2 and 3 close: not yet a battle
    rc = eng.racecraft
    rc.battle_said[1], rc.battle_calls[1] = 4, 1
    rc.battle_pending["Norris has been on you."] = (1, None, 0)
    eng.pipeline.bus.publish(ev.Flashback(0, 100.0, to_frame=0, to_session_time=95.0))
    assert 1 not in rc.battle_said and rc.battle_calls[1] == 0 and not rc.battle_pending


def _closing(race, car, gaps_by_lap, duration_ms):
    """Feed the race with car's gap to the car in front set per lap (ms, from gaps_by_lap)."""
    cfg = Config(data_dir="/nonexistent-test-dir")
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors(), strategy_runner=InlineRunner(None))
    for t_ns, raw in race.frames(duration_ms=duration_ms):
        lap = int((eng.pipeline.state.session_time or 0) // 30) + 1
        race.gap_ahead_ms[car] = gaps_by_lap.get(lap, race.gap_ahead_ms.get(car, 0))
        eng.pipeline.feed(raw, t_ns)
    return [line.split("s  ", 1)[1].split("  (held")[0]
            for line in out.getvalue().splitlines() if line.startswith("RADIO")]


def test_push_now_closing_on_the_car_ahead_and_the_car_behind():
    # 12 Nov: "general hype up when I am close to a battle".
    race = FakeRace({0: [30000] * 6, 1: [30000] * 6}, positions={0: 2, 1: 1},
                    names=["PLAYER", "NORRIS"], extra_packets=False, gap_ahead_ms={0: 2600})
    radio = _closing(race, 0, {2: 2300, 3: 1900, 4: 1500, 5: 1100}, 150000)
    push = [r for r in radio if r.startswith("Push now!")]
    assert push == ["Push now! Norris is one point nine seconds ahead and you're catching. "
                    "Big lap, get into DRS."], radio          # once, not every lap
    # Russell all over you instead: closing from behind, still outside DRS range.
    race = FakeRace({0: [30000] * 6, 1: [30000] * 6}, positions={0: 1, 1: 2},
                    names=["PLAYER", "RUSSELL"], extra_packets=False, gap_ahead_ms={1: 2500})
    radio = _closing(race, 1, {3: 2100, 4: 1700}, 150000)
    assert any(r.startswith("Push now, Russell is") and "Keep them out of DRS." in r
               for r in radio), radio
    # Steady gaps: no push.
    race = FakeRace({0: [30000] * 6, 1: [30000] * 6}, positions={0: 2, 1: 1},
                    names=["PLAYER", "NORRIS"], extra_packets=False, gap_ahead_ms={0: 1500})
    assert not any(r.startswith("Push") for r in _closing(race, 0, {}, 150000))


def test_push_phrases():
    assert phrases.push_defend_phrase(None, 1600).startswith("Push now, the car behind is")
    assert phrases.undercut_result_phrase("Norris", False, 800) == \
        "Norris stayed in front, eight tenths ahead. Fresher tyres, keep pushing!"
    assert phrases.undercut_push_out_phrase("Norris") == \
        "Push now, big out lap. Norris hasn't stopped yet, the undercut's on."


# -- 12 Nov: battles within 2 s, gap updates, stops shaken out, your penalty, energy ------------

def _rig(tempo="normal"):
    import numpy as np
    eng = build_engine(Config(data_dir="/nonexistent-test-dir"), out=io.StringIO(),
                       priors=WearPriors(), strategy_runner=InlineRunner(None))
    eng.set_tempo(tempo)
    st = eng.pipeline.state
    st.session_uid, st.player_index, st.session_time = 1, 0, 500.0
    st.session.session_type, st.session.total_laps, st.session.weather = 15, 14, 0
    st.session.safety_car_status = 0
    c = st.cars
    c.active[:4] = True
    c.names[:4] = ["PLAYER", "NORRIS", "ANTONELLI", "SAINZ"]
    c.position[:4] = [2, 1, 3, 4]
    c.delta_to_car_in_front_ms[:4] = [2500, 0, 800, 1500]
    c.pit_status[:4] = 0
    c.num_pit_stops[:4] = 0
    c.lap[:4] = 6
    c.result_status[:4] = 2
    c.penalties_s[:4] = 0
    said = []
    eng.pipeline.bus.subscribe(ev.RadioMessage, lambda e: said.append((e.topic, e.text,
                                                                      e.excited)))
    return eng, st, c, said, np


def _release(eng, st, dt=30.0):
    st.session_time += dt
    eng.director.after_packet(None)


def test_battle_gap_updates_every_lap_at_the_end_of_sector_one():
    eng, st, c, said, _ = _rig()
    rc = eng.racecraft
    assert eng.racecraft.cfg.battle_gap_s == 2.0          # a battle is within two seconds now
    rc._gap_update(5)
    _release(eng, st)
    assert said[-1] == ("racecraft_gap", ("Antonelli behind, eight tenths, you're holding them. "
                                          "Great job, keep it up!"), True)
    c.delta_to_car_in_front_ms[2] = 500                    # closing by three tenths
    rc._gap_update(6)
    _release(eng, st)
    assert said[-1][1].startswith("Antonelli behind, five tenths, closing by three tenths.")
    # Norris ahead is 2.5 s away: outside a battle, but chatty talks about him too.
    c.delta_to_car_in_front_ms[2] = 3000
    n = len(said)
    rc._gap_update(7)
    _release(eng, st)
    assert len(said) == n
    eng.set_tempo("chatty")
    rc._gap_update(8)
    _release(eng, st)
    assert said[-1][1].startswith("Norris ahead, two point five seconds.")


def test_stops_shaken_out_and_your_penalty_gap():
    eng, st, c, said, _ = _rig()
    rc = eng.racecraft
    rc.position_before_stop = 4
    c.num_pit_stops[0] = 1
    lap = ev.LapCompleted(1, st.session_time, car_idx=0, lap=9, lap_time_ms=90000,
                          sector1_ms=None, sector2_ms=None, sector3_ms=None, invalid=False,
                          pit_in_lap=False, pit_out_lap=False, position=2, visual_compound=None,
                          tyre_age_laps=None)
    rc._cycle(lap)                                         # Norris and co haven't stopped
    assert not rc.cycle_said
    c.num_pit_stops[1:4] = 1
    rc._cycle(lap)
    _release(eng, st)
    assert said[-1] == ("racecraft_cycle", ("Stops all done for everyone. P two, up two places "
                                            "on the cycle. Brilliant work!"), True)
    # A three second penalty with Antonelli eight tenths behind and Sainz 2.3 s behind.
    c.penalties_s[0] = 3
    rc._penalty_lap()
    _release(eng, st)
    assert said[-1][1] == ("Antonelli eight tenths behind, you need three seconds on them, two "
                           "cars inside it. Push, push!")
    c.delta_to_car_in_front_ms[2] = 4500
    rc._penalty_lap()
    _release(eng, st)
    assert said[-1][1] == ("Nobody within your three seconds behind. The penalty costs you "
                           "nothing, keep it clean!")
    n = len(said)
    rc._penalty_lap()                                      # "clear" only once
    _release(eng, st)
    assert len(said) == n


def test_chatty_hears_cars_around_you_pitting():
    eng, st, c, said, _ = _rig("chatty")
    c.position[3], c.position[2] = 3, 4                    # Sainz right behind ...
    c.num_pit_stops[0] = 1                                 # ... and you've stopped
    eng.pipeline.bus.publish(ev.PitEntry(1, st.session_time, car_idx=3, lap=6))
    _release(eng, st)
    assert said[-1][1] == "Sainz, just behind, is in the pits."
    eng.set_tempo("normal")
    n = len(said)
    eng.pipeline.bus.publish(ev.PitEntry(1, st.session_time, car_idx=1, lap=6))
    _release(eng, st)
    assert len(said) == n


def test_moments_that_matter_are_excited_for_every_engineer():
    from engineer.radio.gp import GPStyle
    eng, st, _c, said, _ = _rig()
    eng.director.submit(RadioCall("racecraft_pass", "Great move on Norris! P one."))
    _release(eng, st)
    eng.director.submit(RadioCall("lap_delta", "One thirty."))
    _release(eng, st)
    eng.director.submit(RadioCall("racecraft_cycle", "Stops all done.", excited=False))
    _release(eng, st)
    assert [x[2] for x in said] == [True, False, False]
    # GP echoes the gap in a battle update, like his other battle calls.
    gp = GPStyle("Ryan")
    out = gp("racecraft_gap", "Antonelli behind, eight tenths, you're holding them. Great job!",
             100.0)
    assert out.endswith("Eight tenths.")
