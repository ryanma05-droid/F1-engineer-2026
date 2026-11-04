"""'Restart race' keeps the session UID (real Imola race, 29 Oct): it must count as a new session."""

from engineer.bus import events as ev
from engineer.ingest.recorder import read_recording
from engineer.pipeline import Pipeline

FIXTURE = "tests/fixtures/austria_race_5laps.zst"


def test_restarted_session_with_the_same_uid_starts_again():
    p = Pipeline()
    seen = []
    for E in (ev.SessionStarted, ev.SessionEnded, ev.Flashback):
        p.bus.subscribe(E, lambda e, E=E: seen.append(E.__name__))
    packets = list(read_recording(FIXTURE))
    for t, raw in packets + packets:              # the same race twice: clock back to 0, same UID
        p.feed(raw, t)
    starts = seen.count("SessionStarted")
    assert starts == 2, seen
    # The fixture's real flashback (back ~10 s on lap 3) stays a flashback, twice.
    assert seen.count("Flashback") == 2
    assert seen.count("SessionEnded") >= 1


def test_results_screen_packets_with_the_flt_max_clock_are_skipped():
    """3 Nov online lobby: after a session ends the game keeps sending with session time = FLT_MAX
    for half an hour; a jump from that back to 0 isn't a restart, and nothing waits on it."""
    import numpy as np

    from tests.synth import PacketFactory
    p = Pipeline()
    starts = []
    p.bus.subscribe(ev.SessionStarted, lambda e: starts.append(e.session_time))
    pf = PacketFactory()
    pf.session_time = 95.0
    p.feed(pf.session(session_type=9), 1)
    pf.session_time = float(np.finfo(np.float32).max)
    for _ in range(5):
        p.feed(pf.session(session_type=9), 2)
    assert p.after_end_packets == 5 and p.state.session_time == 95.0
    pf.session_time = 0.5                                    # the next session's menu
    p.feed(pf.session(session_type=9), 3)
    assert len(starts) == 2                                  # a new session, not FLT_MAX noise


def test_hidden_online_players_are_named_by_team():
    from engineer.ingest.formats import f1_2025 as f
    from tests.synth import PacketFactory
    p = Pipeline()
    pf = PacketFactory(player=2)
    a = pf.blank(f.PacketId.PARTICIPANTS)
    r = a[0]
    r["m_numActiveCars"] = 4
    for i, (name, team, ai, num) in enumerate([("Player", 1, 0, 16), ("HAMILTON", 1, 1, 44),
                                               ("spusplash", 7, 0, 31), ("Player", 9, 0, 27)]):
        q = r["m_participants"][i]
        q["m_name"], q["m_teamId"], q["m_aiControlled"], q["m_raceNumber"] = \
            name.encode(), team, ai, num
    p.feed(a.tobytes(), 1)
    assert p.state.cars.names[:4] == ["FERRARI", "HAMILTON", "spusplash", "SAUBER"]
    r["m_participants"][3]["m_teamId"] = 1                   # two hidden Ferraris
    p.feed(a.tobytes(), 2)
    assert p.state.cars.names[0] == "FERRARI 16" and p.state.cars.names[3] == "FERRARI 27"
