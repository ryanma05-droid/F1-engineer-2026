import numpy as np

from engineer.bus import events as ev
from engineer.ingest.formats import f1_2025 as f
from engineer.radio.director import (
    Priority,
    RadioCall,
    RadioDirector,
    estimate_duration_s,
)
from engineer.state.race_state import RaceState

TEL = f.PacketId.CAR_TELEMETRY


class Rig:
    """A director with a hand-driven race state: player is car 0."""

    def __init__(self, cooldowns=None):
        self.bus = ev.EventBus()
        self.state = RaceState()
        self.state.session_uid = 1
        self.state.player_index = 0
        self.state.session.session_type = 15
        self.state.session.track_length_m = 1000
        self.d = RadioDirector(self.bus, self.state, cooldowns_s=cooldowns)
        self.said, self.dropped = [], []
        self.bus.subscribe(ev.RadioMessage, self.said.append)
        self.bus.subscribe(ev.RadioDropped, self.dropped.append)
        self.drive(t=0.0, dist=0.0)

    def drive(self, t, dist, throttle=1.0, brake=0.0, steer=0.0, speed=250.0, lap=2, pit=0):
        c = self.state.cars
        self.state.session_time = t
        c.throttle = np.full(22, throttle)
        c.brake = np.full(22, brake)
        c.steer = np.full(22, steer)
        c.speed_kph = np.full(22, speed)
        c.lap_distance_m = np.full(22, dist)
        c.lap = np.full(22, float(lap))
        c.pit_status = np.full(22, float(pit))
        c.driver_status = np.full(22, 4.0)
        self.d.after_packet(TEL)

    def texts(self):
        return [m.text for m in self.said]


def test_speaks_immediately_on_a_straight():
    r = Rig()
    r.d.submit(RadioCall("lap_delta", "One oh nine one."))
    assert r.texts() == ["One oh nine one."]


def test_held_while_braking_then_released():
    r = Rig()
    r.drive(t=1.0, dist=100, brake=0.8, throttle=0.0)
    r.d.submit(RadioCall("lap_delta", "Hello."))
    assert r.said == []
    r.drive(t=1.5, dist=120, steer=0.5, throttle=0.6)     # mid-corner
    assert r.said == []
    r.drive(t=2.0, dist=150)                               # back on the throttle, straight
    assert r.texts() == ["Hello."]
    assert r.said[0].queued_at == 1.0 and r.said[0].session_time == 2.0


def test_expires_if_no_straight_comes():
    r = Rig()
    r.drive(t=1.0, dist=100, brake=1.0, throttle=0.0)
    r.d.submit(RadioCall("lap_delta", "Too late.", max_wait_s=3.0))
    r.drive(t=4.5, dist=200, brake=1.0, throttle=0.0)
    assert r.said == [] and [x.reason for x in r.dropped] == ["expired"]


def test_learned_corner_ahead_holds_call():
    r = Rig()
    # Lap 2: driver brakes hard in the 400-500 m section, flat out elsewhere.
    t = 0.0
    for dist in range(0, 1000, 10):
        t += 0.15
        busy = 400 <= dist < 500
        r.drive(t=t, dist=dist, brake=1.0 if busy else 0.0, throttle=0.0 if busy else 1.0)
    # Lap 3, at 350 m, flat out: the corner is ~1 s ahead, so a 4 s message should wait.
    r.drive(t=t + 10, dist=350, lap=3)
    r.d.submit(RadioCall("lap_delta", "One oh nine one, two tenths quicker, gained in sector one."))
    assert r.said == []
    r.drive(t=t + 12, dist=520, lap=3)                     # past the corner
    assert len(r.said) == 1


def test_standing_start_not_learned_as_corner():
    r = Rig()
    for i, dist in enumerate(range(0, 100, 5)):
        r.drive(t=i * 0.2, dist=dist, throttle=0.3, speed=80, lap=1)   # race lap 1 launch
    r.drive(t=100, dist=5, lap=2)
    r.d.submit(RadioCall("lap_delta", "One oh nine one, two tenths quicker, gained in sector one."))
    assert len(r.said) == 1


def test_priority_and_no_talking_over():
    r = Rig()
    r.drive(t=1.0, dist=100, brake=1.0, throttle=0.0)
    r.d.submit(RadioCall("a", "Low one.", priority=Priority.LOW))
    r.d.submit(RadioCall("b", "High one.", priority=Priority.HIGH))
    r.drive(t=1.5, dist=150)
    assert r.texts() == ["High one."]
    r.drive(t=1.6, dist=160)                               # still speaking the first call
    assert r.texts() == ["High one."]
    r.drive(t=1.5 + estimate_duration_s("High one.") + 0.01, dist=200)
    assert r.texts() == ["High one.", "Low one."]


def test_cooldown():
    r = Rig(cooldowns={"lap_delta": 5.0})
    r.d.submit(RadioCall("lap_delta", "First."))
    r.drive(t=3.0, dist=300)
    r.d.submit(RadioCall("lap_delta", "Second."))
    assert r.texts() == ["First."] and r.dropped[0].reason == "cooldown"


def test_pit_lane_never_busy():
    r = Rig()
    r.drive(t=1.0, dist=100, throttle=0.2, steer=0.4, pit=1, speed=80)
    r.d.submit(RadioCall("lap_delta", "In lap."))
    assert r.texts() == ["In lap."]


def test_flashback_clears_queue():
    r = Rig()
    r.drive(t=10.0, dist=100, brake=1.0, throttle=0.0)
    r.d.submit(RadioCall("lap_delta", "Rewound."))
    r.bus.publish(ev.Flashback(1, 10.0, to_frame=1, to_session_time=5.0))
    r.drive(t=5.1, dist=50)
    assert r.said == []


def test_your_answer_goes_ahead_of_every_other_waiting_call():
    """3 Nov Texas: an answer expired behind a tyre warning queued earlier at the same priority.
    4 Nov: answers go first, before box calls too."""
    from engineer.bus import events as ev
    from engineer.radio.director import Priority, RadioCall, RadioDirector
    from engineer.state.race_state import RaceState
    bus, state = ev.EventBus(), RaceState()
    state.session_time = 100.0
    d = RadioDirector(bus, state)
    said = []
    bus.subscribe(ev.RadioMessage, lambda e: said.append(e.topic))
    d.speaking_until = 105.0                                  # something is being said
    d.submit(RadioCall(topic="tyre_warning", text="Front left at forty percent.",
                       priority=Priority.HIGH, wait_for_straight=False, max_wait_s=30))
    state.session_time = 101.0
    d.submit(RadioCall(topic="answer", text="They'd make it.", priority=Priority.HIGH,
                       wait_for_straight=False, max_wait_s=30))
    state.session_time = 102.0
    d.submit(RadioCall(topic="box", text="Box this lap, box this lap.", priority=Priority.HIGH,
                       wait_for_straight=False, max_wait_s=30))
    for t in (106.0, 110.0, 114.0):
        state.session_time = t
        d.after_packet(None)
    assert said == ["answer", "box", "tyre_warning"]


def test_on_the_mic_nothing_else_is_said_until_the_answer():
    """4 Nov: you ask on the main straight, the lap is crossed, and the lap calls queue up. They
    wait (without expiring) until your answer has been said, then follow it."""
    r = Rig()
    d = r.d
    r.drive(t=10.0, dist=100)
    d.listen()                                                    # button down
    d.submit(RadioCall("lap_delta", "One thirty two point one.", max_wait_s=3.0))
    d.submit(RadioCall("box", "Box this lap.", priority=Priority.HIGH, wait_for_straight=False))
    for t in (11.0, 13.0, 16.0):                                  # longer than their 3 s wait
        r.drive(t=t, dist=100 + t)
    assert r.said == [] and not r.dropped
    d.listen(8.0)                                                 # question heard
    d.submit(RadioCall("answer", "Hards make it to the end.", priority=Priority.HIGH,
                       wait_for_straight=False, max_wait_s=15.0))
    assert [m.topic for m in r.said] == ["answer"]
    for t in (17.0, 18.5, 19.0, 20.0, 21.0, 22.0):
        r.drive(t=t, dist=100 + t)
    assert [m.topic for m in r.said] == ["answer", "box", "lap_delta"] and not r.dropped


def test_the_hold_ends_by_itself_and_on_a_short_press():
    r = Rig()
    d = r.d
    r.drive(t=10.0, dist=100)
    d.listen(8.0)                                                 # heard, answer never comes
    d.submit(RadioCall("lap_delta", "Lap.", max_wait_s=3.0))
    r.drive(t=17.0, dist=170)
    assert r.said == []
    r.drive(t=18.5, dist=185)                                     # 8 s up: released
    assert [m.topic for m in r.said] == ["lap_delta"]
    d.listen()
    d.stop_listening()                                            # short press / nothing heard
    d.submit(RadioCall("tyre_status", "Tyres fine."))
    r.drive(t=20.0, dist=300)
    assert [m.topic for m in r.said] == ["lap_delta", "tyre_status"]


def test_a_box_call_is_said_mid_corner_rather_than_dropped():
    # Interlagos, 7 Nov: half way round is the infield, no straight for 10 s, and the half-lap
    # "Box box" was dropped.
    r = Rig()
    r.drive(t=1.0, dist=100, brake=0.6, throttle=0.0, steer=0.4)
    r.d.submit(RadioCall("box", "Box box, end of this lap.", Priority.HIGH, max_wait_s=10.0,
                         must_say=True))
    r.d.submit(RadioCall("lap_delta", "One oh nine one.", max_wait_s=10.0))
    for k in range(1, 10):
        r.drive(t=1.0 + k, dist=100 + k * 30, brake=0.6, throttle=0.0, steer=0.4)
    assert r.said == []                                    # still waiting for a straight
    r.drive(t=11.5, dist=500, brake=0.6, throttle=0.0, steer=0.4)
    assert r.texts() == ["Box box, end of this lap."]      # overdue: said in the corner
    assert [d.text for d in r.dropped] == ["One oh nine one."]


def test_a_must_say_call_is_dropped_after_its_cap():
    r = Rig()
    r.d.speaking_until = 100.0                             # a very long call still going
    r.d.submit(RadioCall("box", "Box box.", Priority.HIGH, max_wait_s=2.0, must_say=True))
    r.drive(t=5.0, dist=100)
    assert r.dropped == []                                 # past its wait, under the cap
    r.drive(t=6.5, dist=150)
    assert [d.text for d in r.dropped] == ["Box box."] and r.said == []


def test_your_lap_time_is_said_when_the_session_ends():
    # One-shot qualifying ends the moment the lap is done (Baku, 7 Nov).
    r = Rig()
    r.drive(t=1.0, dist=100, brake=1.0, throttle=0.0)
    r.d.submit(RadioCall("lap_delta", "One forty three three."))
    r.d.submit(RadioCall("tyre_status", "Fronts cold.", Priority.LOW))
    r.bus.publish(ev.SessionEnded(1, 1.1, reason="SEND"))
    assert r.texts() == ["One forty three three."]


def test_urgent_race_craft_goes_before_a_corner_tip():
    # Jeddah, 9 Nov: a 13 s corner tip queued first took the gap; "Last lap" and a fight call
    # expired behind it.
    r = Rig()
    r.drive(t=1.0, dist=100, brake=1.0, throttle=0.0)
    r.d.submit(RadioCall("corner", "Turn six, a long tip.", max_wait_s=20))
    r.d.submit(RadioCall("racecraft_countdown", "Last lap, P two.", max_wait_s=30))
    r.drive(t=2.0, dist=200)
    assert r.texts() == ["Last lap, P two."]


def test_quiet_tempo_leaves_out_battle_push_but_keeps_the_undercut_push():
    r = Rig()
    r.d.tempo = "terse"
    r.d.submit(RadioCall("racecraft_push", "Push now! Norris ahead."))
    r.d.submit(RadioCall("undercut_push", "Push now, big out lap."))
    assert r.texts() == ["Push now, big out lap."]
