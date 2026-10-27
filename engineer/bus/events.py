"""In-process publish/subscribe event bus and the event types.

Detectors publish events when race state changes in a meaningful way (a lap finished, a car
entered the pits). Readers subscribe by event type. Handlers run synchronously on the main loop,
so they must be quick; anything heavy belongs in a worker process (later phases).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, TypeVar

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    session_uid: int
    session_time: float          # game session time (seconds) of the packet that triggered it


@dataclass(frozen=True)
class SessionStarted(Event):
    track_id: int | None
    session_type: int | None


@dataclass(frozen=True)
class SessionEnded(Event):
    reason: str                  # "SEND" (game said so) or "new_session" (UID changed)


@dataclass(frozen=True)
class LapCompleted(Event):
    car_idx: int
    lap: int                     # the lap number that was just completed
    lap_time_ms: int
    sector1_ms: int | None
    sector2_ms: int | None
    sector3_ms: int | None
    invalid: bool                # lap was flagged invalid at any point
    pit_in_lap: bool             # car entered the pit lane during this lap
    pit_out_lap: bool            # car started this lap in the pit lane
    position: int | None
    visual_compound: int | None
    tyre_age_laps: int | None
    safety_car: bool = False     # safety car or VSC out at any point of the lap


@dataclass(frozen=True)
class SectorCompleted(Event):
    car_idx: int
    lap: int
    sector: int                  # 1, 2 or 3
    sector_time_ms: int | None


@dataclass(frozen=True)
class PitEntry(Event):
    car_idx: int
    lap: int


@dataclass(frozen=True)
class PitExit(Event):
    car_idx: int
    lap: int


@dataclass(frozen=True)
class Flashback(Event):
    """The player used a flashback: state built from frames after this point is stale."""
    to_frame: int
    to_session_time: float


E = TypeVar("E", bound=Event)
Handler = Callable[[Event], None]


class EventBus:
    def __init__(self):
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self._all: list[Handler] = []
        self.handler_errors = 0

    def subscribe(self, event_type: type[E] | None, handler: Callable[[E], None]) -> None:
        """Subscribe to one event type, or to every event with event_type=None."""
        if event_type is None:
            self._all.append(handler)
        else:
            self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in (*self._handlers.get(type(event), ()), *self._all):
            try:
                handler(event)
            except Exception:
                self.handler_errors += 1
                if self.handler_errors <= 10:
                    log.exception("event handler %r failed on %r", handler, event)


# -- radio (Phase 1) ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RadioMessage(Event):
    """The director decided to say this now. The audio process speaks it; the console prints it."""
    topic: str
    text: str
    priority: int
    queued_at: float             # session time the call was first requested
    origin_t_ns: int | None      # arrival time of the packet that triggered it (latency checks)


@dataclass(frozen=True)
class RadioDropped(Event):
    """A call expired before a good moment to say it came up."""
    topic: str
    text: str
    reason: str                  # "expired" or "cooldown"
