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
    assert "Lost two places at the start, long way to go." in radio
    assert "Three laps to go, P three." not in radio            # only five and one
    assert any(r.startswith("Last lap, P three. Bring it home.") for r in radio)
    assert "Chequered flag, P three. Podium. Great drive." in radio


def test_a_pass_is_called_once_it_sticks_and_not_if_swapped_back():
    race = FakeRace({0: [30000] * 4, 1: [30500] * 4}, positions={0: 1, 1: 2},
                    names=["PLAYER", "NORRIS"], extra_packets=False)
    _, radio = run(race, {35: ovtk(0, 1)}, duration_ms=60000)
    assert "Great move on Norris, P one." in radio
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
    assert "Warning for track limits." in radio
    assert "Fastest lap of the race, thirty zero." in radio
    assert radio.count("Safety car in this lap, get the tyres ready.") == 1


def test_phrases():
    assert phrases.start_phrase(8) == "Great start, up eight places."
    assert phrases.pass_phrase(["Norris", "Piastri"], 5) == "Two cars in one go, P five."
    assert phrases.lost_place_phrase("Leclerc", 4) == "Lost P three to Leclerc. Stay with Leclerc."
    assert phrases.defend_phrase("Leclerc", 800) == \
        "Leclerc behind, eight tenths and closing, DRS range. Defend."
    assert phrases.attack_phrase("Norris", 2100, 300, 4) == (
        "Norris ahead, two point one seconds. Catching three tenths a lap, on Norris in about "
        "four laps. Keep pushing.")
    assert phrases.drs_range_phrase("Norris") == "DRS range on Norris. Go get Norris."
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
    assert "Lost P one to Norris. Stay with Norris." in radio
    # Gone for 3 s, then back past before the lost call: a good defence, not a lost place.
    _, radio = run(_duel(), {35: ovtk(1, 0), 38: ovtk(0, 1)}, duration_ms=60000,
                   moves={35: {0: 2, 1: 1}, 38: {0: 1, 1: 2}})
    assert "Good fight, Norris couldn't make it stick, P one." in radio
    assert not any(r.startswith("Lost") for r in radio)
    # Lost (and told), then straight back past: "great response", not a plain pass.
    _, radio = run(_duel(), {35: ovtk(1, 0), 47: ovtk(0, 1)}, duration_ms=65000,
                   moves={35: {0: 2, 1: 1}, 47: {0: 1, 1: 2}})
    assert "Lost P one to Norris. Stay with Norris." in radio
    assert "Straight back past Norris, P one. Great response." in radio


def test_lost_place_wording():
    assert phrases.lost_place_phrase("Hamilton", 6, 1) == \
        "Hamilton got by, P six. Stay close, the move back is on."
    assert phrases.lost_places_phrase(3, 9) == "Lost three places, P nine. Heads down, keep at it."
    assert phrases.fight_back_phrase("Hamilton", 5, False, 1) == \
        "Nicely defended, back ahead of Hamilton, P five."


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
    assert any("Antonelli, P one" in r for r in radio)


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
    race = FakeRace({0: [30000] * 11, 1: [30000] * 11}, positions={0: 2, 1: 1},
                    names=["PLAYER", "NORRIS"], extra_packets=False, gap_ahead_ms={0: 800})
    _, radio = run(race, duration_ms=335000)
    battle = [r for r in radio if "gearbox" in r]
    assert battle and battle[0].startswith("Three laps on Norris's gearbox."), radio
    assert 2 <= sum("Norris" in r and "laps" in r for r in radio) <= 4   # repeats, not every lap
    # Norris all over you instead.
    race = FakeRace({0: [30000] * 6, 1: [30000] * 6}, positions={0: 1, 1: 2},
                    names=["PLAYER", "NORRIS"], extra_packets=False, gap_ahead_ms={1: 700})
    _, radio = run(race, duration_ms=185000)
    assert "Norris has been on you for three laps and still hasn't got by. Brilliant work, " \
           "stay calm." in radio, radio


def test_battle_phrases():
    assert phrases.battle_phrase("Russell", "behind", 4) == \
        "Russell has been on you for four laps and still hasn't got by. Brilliant work, stay calm."
    assert phrases.battle_phrase("Russell", "ahead", 3, swaps=3) == \
        "Proper scrap with Russell, three laps of it. Loving this, keep it clean."


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
