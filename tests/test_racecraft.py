import io

from engineer.app import build_engine
from engineer.bus import events as ev
from engineer.config import Config
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall
from engineer.strategy.runner import InlineRunner
from tests.synth import FakeRace


def run(race, events=None, duration_ms=None):
    """Feed the race; events: {game time s: (code, details)} published as GameEvents."""
    cfg = Config(data_dir="/nonexistent-test-dir")
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors(), strategy_runner=InlineRunner(None))
    todo = sorted((events or {}).items())
    st = eng.pipeline.state
    for t_ns, raw in race.frames(duration_ms=duration_ms):
        eng.pipeline.feed(raw, t_ns)
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
