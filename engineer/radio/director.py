"""Radio director: decides what the engineer says, and when.

Analyzers don't talk directly; they hand the director a RadioCall. The director:

- orders calls by priority (then oldest first). A call never cuts off one already being spoken.
- drops a call that has waited longer than its `max_wait_s` (a lap delta said 10 s late is noise).
- applies per-topic cooldowns, so the same kind of call can't repeat too often.
- holds calls while the driver is busy (braking, turning, lifting) and releases them on straights.
  See QuietZones below.

Everything runs on GAME time (the packet's session time), never the PC clock, and how long a
sentence takes to say is estimated from its length rather than measured from the audio. That makes
the director's decisions depend only on the packets, so replaying a recording produces exactly the
same calls at the same moments as the live session did.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from engineer.bus import events as ev
from engineer.ingest.formats import f1_2025 as f
from engineer.state.race_state import RaceState

log = logging.getLogger(__name__)


class Priority:
    LOW = 1
    NORMAL = 2
    HIGH = 3


@dataclass
class RadioCall:
    topic: str
    text: str
    priority: int = Priority.NORMAL
    max_wait_s: float = 3.0           # drop if not said within this much game time
    wait_for_straight: bool = True    # False for urgent calls that can't wait for a straight
    origin_t_ns: int | None = None    # arrival time of the triggering packet (latency readout)
    queued_at: float = field(default=0.0, init=False)


# Speech-length estimate: Piper voices speak roughly 2.8 words a second; plus the squelch beeps.
WORDS_PER_SECOND = 2.8
RADIO_OVERHEAD_S = 0.5


def estimate_duration_s(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND + RADIO_OVERHEAD_S


class QuietZones:
    """Knows when the driver is busy, so the engineer talks on the straights.

    Two signals:
    1. Right now: braking, steering, or not near full throttle means a corner or braking zone.
    2. Just ahead: a map of the lap in 50 m sections, learned from the driver's own previous laps
       this session, records how often each section was "busy". Before speaking we check the
       sections the car will cover in the next couple of seconds, so a message doesn't start just
       before a braking zone. On the first lap the map is empty and only signal 1 is used.
    In the pit lane the driver is never considered busy.

    The map doesn't learn from laps that don't look like normal driving: race lap 1 (the standing
    start made the first sections look like corners on real Austria data), in-laps and out-laps.
    """

    BIN_M = 50.0
    LOOKAHEAD_CAP_S = 2.0      # only need the first ~2 s of a message to be on a straight
    BUSY_FRACTION = 0.5        # a section is busy if the driver was busy there >= half the time
    MIN_SAMPLES = 3

    # "Busy right now" thresholds
    BRAKE = 0.05
    THROTTLE = 0.8
    STEER = 0.2

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.busy: dict[int, int] = {}
        self.total: dict[int, int] = {}

    def _player(self, state: RaceState):
        i = state.player_index
        if i is None or i >= len(state.cars.active):
            return None
        return i

    def busy_now(self, state: RaceState) -> bool:
        i = self._player(state)
        if i is None:
            return False
        c = state.cars
        throttle, brake, steer = c.throttle[i], c.brake[i], c.steer[i]
        if math.isnan(throttle) or math.isnan(brake) or math.isnan(steer):
            return False   # no telemetry yet: don't block forever
        return brake > self.BRAKE or throttle < self.THROTTLE or abs(steer) > self.STEER

    def observe(self, state: RaceState) -> None:
        """Called on each Car Telemetry packet: add the player's current section to the map."""
        i = self._player(state)
        if i is None:
            return
        c = state.cars
        d = c.lap_distance_m[i]
        if math.isnan(d) or d < 0 or c.pit_status[i] > 0:
            return
        if state.session.is_race and c.lap[i] == 1:
            return
        if c.driver_status[i] in (2, 3):   # in lap, out lap
            return
        b = int(d // self.BIN_M)
        self.total[b] = self.total.get(b, 0) + 1
        if self.busy_now(state):
            self.busy[b] = self.busy.get(b, 0) + 1

    def _section_busy(self, b: int) -> bool:
        n = self.total.get(b, 0)
        return n >= self.MIN_SAMPLES and self.busy.get(b, 0) / n >= self.BUSY_FRACTION

    def busy_ahead(self, state: RaceState, seconds: float) -> bool:
        i = self._player(state)
        if i is None:
            return False
        c = state.cars
        d, speed_kph = c.lap_distance_m[i], c.speed_kph[i]
        if math.isnan(d) or math.isnan(speed_kph) or d < 0:
            return False
        ahead_m = speed_kph / 3.6 * min(seconds, self.LOOKAHEAD_CAP_S)
        lap_len = state.session.track_length_m or 0
        first = int(d // self.BIN_M)
        last = int((d + ahead_m) // self.BIN_M)
        for b in range(first, last + 1):
            if lap_len and b * self.BIN_M >= lap_len:      # wrap past the line
                b = int((b * self.BIN_M - lap_len) // self.BIN_M)
            if self._section_busy(b):
                return True
        return False

    def driver_busy(self, state: RaceState, speech_s: float) -> bool:
        i = self._player(state)
        if i is not None and state.cars.pit_status[i] > 0:
            return False
        return self.busy_now(state) or self.busy_ahead(state, speech_s)


# Said even in quiet mode ("Quiet" on push-to-talk): box calls, safety car calls, and answers to
# your own questions.
QUIET_TOPICS = {"box", "safety_car", "answer"}


class RadioDirector:
    def __init__(self, bus: ev.EventBus, state: RaceState,
                 cooldowns_s: dict[str, float] | None = None):
        self.bus = bus
        self.quiet = False
        self.state = state
        self.cooldowns_s = cooldowns_s or {}
        self.zones = QuietZones()
        self.queue: list[RadioCall] = []
        self.speaking_until = -math.inf
        self.last_said: dict[str, float] = {}
        bus.subscribe(ev.SessionStarted, self._on_session_started)
        bus.subscribe(ev.Flashback, self._on_flashback)

    # -- inputs --------------------------------------------------------------------------------

    def submit(self, call: RadioCall) -> None:
        if self.quiet and call.topic not in QUIET_TOPICS:
            self._drop(call, "quiet")
            return
        t = self._now()
        cooldown = self.cooldowns_s.get(call.topic, 0.0)
        last = self.last_said.get(call.topic)
        if last is not None and t - last < cooldown:
            self._drop(call, "cooldown")
            return
        call.queued_at = t
        self.queue.append(call)
        self.after_packet(None)   # try straight away: no need to wait for the next packet

    def after_packet(self, packet_id: int | None) -> None:
        """Hooked into Pipeline.after_packet: learn the quiet-zone map and maybe speak."""
        if packet_id == f.PacketId.CAR_TELEMETRY:
            self.zones.observe(self.state)
        if not self.queue:
            return
        t = self._now()
        for call in [c for c in self.queue if t - c.queued_at > c.max_wait_s]:
            self.queue.remove(call)
            self._drop(call, "expired")
        if not self.queue or t < self.speaking_until:
            return
        call = max(self.queue, key=lambda c: (c.priority, -c.queued_at))
        duration = estimate_duration_s(call.text)
        if call.wait_for_straight and self.zones.driver_busy(self.state, duration):
            return
        self.queue.remove(call)
        self.speaking_until = t + duration
        self.last_said[call.topic] = t
        self.bus.publish(ev.RadioMessage(
            self.state.session_uid or 0, t, topic=call.topic, text=call.text,
            priority=call.priority, queued_at=call.queued_at, origin_t_ns=call.origin_t_ns))

    # -- internals -----------------------------------------------------------------------------

    def _now(self) -> float:
        return self.state.session_time or 0.0

    def _drop(self, call: RadioCall, reason: str) -> None:
        log.debug("radio call dropped (%s): %s", reason, call.text)
        self.bus.publish(ev.RadioDropped(self.state.session_uid or 0, self._now(),
                                         topic=call.topic, text=call.text, reason=reason))

    def _on_session_started(self, _e) -> None:
        self.queue.clear()
        self.zones.reset()
        self.speaking_until = -math.inf
        self.last_said.clear()

    def _on_flashback(self, _e) -> None:
        # Time jumped backwards: anything queued belongs to the rewound future.
        self.queue.clear()
        self.speaking_until = -math.inf
        self.last_said.clear()
