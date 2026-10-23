"""The single path every packet takes, live or replayed: parse -> race state -> events.

    pipeline = Pipeline()
    pipeline.feed(raw_bytes, arrival_time_ns)

The listener and the replay runner both call `feed`, which is what makes a replay behave exactly
like the live session it came from.
"""

from __future__ import annotations

import logging

from engineer.bus.events import EventBus
from engineer.ingest import formats
from engineer.ingest.formats.f1_2025 import PacketSizeError
from engineer.state.detectors import EventDetector
from engineer.state.race_state import RaceState

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, bus: EventBus | None = None):
        self.bus = bus or EventBus()
        self.state = RaceState()
        self.detector = EventDetector(self.bus)
        self.packets = 0
        self.unknown_packets = 0
        self.bad_packets = 0
        self.last_arrival_ns: int | None = None
        self._warned_formats: set[int] = set()

    def feed(self, raw: bytes, t_ns: int) -> None:
        self.packets += 1
        self.last_arrival_ns = t_ns
        try:
            _module, header, packet_id, rec = formats.dispatch(raw)
        except (formats.UnsupportedFormat, NotImplementedError) as exc:
            fmt = formats.packet_format(raw) if len(raw) >= 2 else -1
            if fmt not in self._warned_formats:  # say it once, not 60 times a second
                self._warned_formats.add(fmt)
                log.error("%s", exc)
            self.bad_packets += 1
            return
        except PacketSizeError as exc:
            self.bad_packets += 1
            if self.bad_packets <= 10:
                log.warning("%s", exc)
            return
        if rec is None:
            self.unknown_packets += 1

        old_uid = self.state.session_uid
        if self.state.apply(header, packet_id, rec):
            self.detector.on_new_session(self.state, old_uid, self.state.session_time or 0.0)
        self.detector.process(self.state, packet_id, rec)
