import numpy as np

from engineer.bus import events as ev
from engineer.ingest.formats import f1_2025 as f
from engineer.radio.director import Priority, RadioCall, RadioDirector, estimate_duration_s
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
