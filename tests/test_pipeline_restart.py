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
