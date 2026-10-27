"""Turns race-state changes into events: session start/end, lap and sector completed, pit entry/exit.

How lap detection works: after each Lap Data update we compare every car's current lap number with
the value from the previous Lap Data packet. When it goes up, the previous lap is complete. We
capture that lap's sector 1/2 times *before* the game resets them for the new lap, and remember
whether the lap was ever flagged invalid or touched the pit lane.

In real F1 25 data `m_lastLapTimeInMS` updates on the same frame as the lap number, but a
completed lap is still held as "pending" until the last-lap time changes (or 2 s of session time
pass) in case it ever lags. Sector completions are held the same way until the sector time is
non-zero.

The final lap of a race is different: at the chequered flag the game does NOT advance the lap
number. Instead `m_lastLapTimeInMS` changes while the lap number stays put (and the result status
turns Finished on the next frame). So a last-lap time change on the same lap also completes a lap,
numbered as the current lap. Confirmed on a real 5-lap race at Austria.

SessionStarted is published on the first Session packet of a new session UID, so it always
carries the track and session type. SessionEnded comes from the SEND event, or from the UID changing.

Flashbacks: after an FLBK event (or if a lap number goes backwards) we drop the baselines and any
pending laps, so a rewound lap is never reported as completed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from engineer.bus import events as ev
from engineer.ingest.formats import f1_2025 as f
from engineer.state.race_state import N, RaceState

log = logging.getLogger(__name__)

PENDING_TIMEOUT_S = 2.0


def _int_or_none(x: float) -> int | None:
    return None if x is None or np.isnan(x) else int(x)


@dataclass
class _PendingLap:
    lap: int
    old_last_lap_ms: float
    sector1_ms: float
    sector2_ms: float
    invalid: bool
    pit_in_lap: bool
    pit_out_lap: bool
    since: float
    safety_car: bool = False


@dataclass
class _PendingSector:
    lap: int
    sector: int
    since: float


@dataclass
class _Baseline:
    lap: np.ndarray = field(default_factory=lambda: np.full(N, np.nan))
    sector: np.ndarray = field(default_factory=lambda: np.full(N, np.nan))
    pit_status: np.ndarray = field(default_factory=lambda: np.full(N, np.nan))
    last_lap_ms: np.ndarray = field(default_factory=lambda: np.full(N, np.nan))
    sector1_ms: np.ndarray = field(default_factory=lambda: np.full(N, np.nan))
    sector2_ms: np.ndarray = field(default_factory=lambda: np.full(N, np.nan))
    invalid_latch: np.ndarray = field(default_factory=lambda: np.zeros(N, dtype=bool))
    sc_latch: np.ndarray = field(default_factory=lambda: np.zeros(N, dtype=bool))
    pit_during_lap: np.ndarray = field(default_factory=lambda: np.zeros(N, dtype=bool))
    pit_at_lap_start: np.ndarray = field(default_factory=lambda: np.zeros(N, dtype=bool))


class EventDetector:
    def __init__(self, bus: ev.EventBus):
        self.bus = bus
        self._reset_tracking()
        self._started_uid: int | None = None
        self._ended_uid: int | None = None

    def _reset_tracking(self) -> None:
        self.base = _Baseline()
        self.pending_laps: dict[int, _PendingLap] = {}
        self.pending_sectors: dict[int, _PendingSector] = {}

    # -- entry points called by the pipeline --------------------------------------------------

    def on_new_session(self, state: RaceState, old_uid: int | None, t: float) -> None:
        if old_uid is not None and self._ended_uid != old_uid:
            self.bus.publish(ev.SessionEnded(old_uid, t, reason="new_session"))
            self._ended_uid = old_uid
        self._reset_tracking()

    def process(self, state: RaceState, packet_id: int, rec: np.void | None) -> None:
        if rec is None or state.session_uid is None:
            return
        if packet_id == f.PacketId.EVENT:
            self._on_event(state, rec)
        elif packet_id == f.PacketId.LAP_DATA:
            self._on_lap_data(state)
        elif packet_id == f.PacketId.SESSION:
            self._start(state)  # first Session packet of a session: track and type now known

    # -- internals ---------------------------------------------------------------------------

    def _start(self, state: RaceState) -> None:
        if self._started_uid == state.session_uid:
            return
        self._started_uid = state.session_uid
        self.bus.publish(ev.SessionStarted(state.session_uid, state.session_time or 0.0,
                                           track_id=state.session.track_id,
                                           session_type=state.session.session_type))

    def _on_event(self, state: RaceState, rec: np.void) -> None:
        code, details = f.decode_event(rec)
        t = state.session_time or 0.0
        if code == "SEND":
            if self._ended_uid != state.session_uid:
                self._ended_uid = state.session_uid
                self.bus.publish(ev.SessionEnded(state.session_uid, t, reason="SEND"))
        elif code == "FLBK":
            self._reset_tracking()
            self.bus.publish(ev.Flashback(state.session_uid, t,
                                          to_frame=details["flashbackFrameIdentifier"],
                                          to_session_time=details["flashbackSessionTime"]))

    def _on_lap_data(self, state: RaceState) -> None:
        c = state.cars
        b = self.base
        t = state.session_time or 0.0
        uid = state.session_uid

        known = c.active & ~np.isnan(b.lap) & ~np.isnan(c.lap)
        lap_up = known & (c.lap > b.lap)
        lap_back = known & (c.lap < b.lap)
        same_lap = known & (c.lap == b.lap)
        pit_now = c.pit_status > 0  # NaN compares False
        pit_before = b.pit_status > 0

        # Pit entry / exit
        for i in np.flatnonzero(known & pit_now & ~pit_before):
            self.bus.publish(ev.PitEntry(uid, t, car_idx=int(i), lap=int(c.lap[i])))
        for i in np.flatnonzero(known & ~pit_now & pit_before):
            self.bus.publish(ev.PitExit(uid, t, car_idx=int(i), lap=int(c.lap[i])))

        # Lap completions: snapshot the finished lap before baselines move on.
        for i in np.flatnonzero(lap_up):
            self.pending_sectors.pop(int(i), None)
            self.pending_laps[int(i)] = _PendingLap(
                lap=int(b.lap[i]),
                old_last_lap_ms=b.last_lap_ms[i],
                sector1_ms=b.sector1_ms[i],
                sector2_ms=b.sector2_ms[i],
                invalid=bool(b.invalid_latch[i]),  # this packet's flag belongs to the new lap
                pit_in_lap=bool(b.pit_during_lap[i] or (pit_now[i] and not pit_before[i])),
                pit_out_lap=bool(b.pit_at_lap_start[i]),
                since=t,
                safety_car=bool(b.sc_latch[i]),
            )
            b.invalid_latch[i] = False
            b.sc_latch[i] = False
            b.pit_during_lap[i] = False
            b.pit_at_lap_start[i] = pit_now[i]

        # Finish line at the chequered flag: lap number stays, last-lap time changes.
        # Skipped for cars with a lap already pending, so a lagging update can't count twice.
        last_changed = (c.last_lap_time_ms > 0) & (c.last_lap_time_ms != b.last_lap_ms)
        for i in np.flatnonzero(same_lap & last_changed & ~np.isnan(b.last_lap_ms)):
            if int(i) in self.pending_laps:
                continue
            self.pending_sectors.pop(int(i), None)
            self.pending_laps[int(i)] = _PendingLap(
                lap=int(c.lap[i]),
                old_last_lap_ms=b.last_lap_ms[i],
                sector1_ms=b.sector1_ms[i],
                sector2_ms=b.sector2_ms[i],
                invalid=bool(b.invalid_latch[i]),
                pit_in_lap=bool(b.pit_during_lap[i] or (pit_now[i] and not pit_before[i])),
                pit_out_lap=bool(b.pit_at_lap_start[i]),
                since=t,
                safety_car=bool(b.sc_latch[i]),
            )
            b.invalid_latch[i] = False
            b.sc_latch[i] = False

        # Sector changes within a lap: 0 -> 1 means S1 done, 1 -> 2 means S2 done.
        sec_up = same_lap & (c.sector > b.sector)
        for i in np.flatnonzero(sec_up):
            self.pending_sectors[int(i)] = _PendingSector(lap=int(c.lap[i]),
                                                          sector=int(c.sector[i]), since=t)

        # Laps going backwards (flashback / restart without an FLBK event): re-baseline quietly.
        for i in np.flatnonzero(lap_back):
            self.pending_laps.pop(int(i), None)
            self.pending_sectors.pop(int(i), None)
            b.invalid_latch[i] = False
            b.sc_latch[i] = False
            b.pit_during_lap[i] = False
            b.pit_at_lap_start[i] = pit_now[i]

        # Latches for the lap in progress (after rollover handling, so they apply to the new lap).
        b.invalid_latch |= c.active & (c.current_lap_invalid == 1)
        if state.session.safety_car_status in (1, 2):      # full SC or VSC
            b.sc_latch |= c.active
        # In-lap = the car ENTERED the pit lane during the lap. An out-lap that starts in the
        # pit lane (box after the line) is not also an in-lap.
        b.pit_during_lap |= c.active & pit_now & ~pit_before
        new = c.active & np.isnan(b.lap)
        b.pit_at_lap_start[new] = pit_now[new]

        self._flush_pending(state, t)

        # Move baselines forward (inactive cars are already NaN in state).
        b.lap[:] = c.lap
        b.sector[:] = c.sector
        b.pit_status[:] = c.pit_status
        b.last_lap_ms[:] = c.last_lap_time_ms
        b.sector1_ms[:] = c.sector1_ms
        b.sector2_ms[:] = c.sector2_ms

    def _flush_pending(self, state: RaceState, t: float) -> None:
        c = state.cars
        uid = state.session_uid
        for i, p in list(self.pending_laps.items()):
            last = c.last_lap_time_ms[i]
            updated = not np.isnan(last) and last > 0 and (
                np.isnan(p.old_last_lap_ms) or last != p.old_last_lap_ms)
            timed_out = t - p.since >= PENDING_TIMEOUT_S
            if not (updated or timed_out):
                continue
            del self.pending_laps[i]
            if np.isnan(last) or last <= 0:
                log.warning("car %d lap %d: no last lap time; skipping LapCompleted", i, p.lap)
                continue
            # Not updated after the timeout: the new lap time equals the old one to the ms.
            lap_ms = int(last)
            s1 = _int_or_none(p.sector1_ms) or None
            s2 = _int_or_none(p.sector2_ms) or None
            s3 = lap_ms - s1 - s2 if s1 and s2 and lap_ms > s1 + s2 else None
            self.bus.publish(ev.SectorCompleted(uid, t, car_idx=i, lap=p.lap, sector=3,
                                                sector_time_ms=s3))
            self.bus.publish(ev.LapCompleted(
                uid, t, car_idx=i, lap=p.lap, lap_time_ms=lap_ms,
                sector1_ms=s1, sector2_ms=s2, sector3_ms=s3,
                invalid=p.invalid, pit_in_lap=p.pit_in_lap, pit_out_lap=p.pit_out_lap,
                position=_int_or_none(c.position[i]),
                visual_compound=_int_or_none(c.visual_compound[i]),
                tyre_age_laps=_int_or_none(c.tyre_age_laps[i]),
                safety_car=p.safety_car,
            ))
        for i, p in list(self.pending_sectors.items()):
            ms = c.sector1_ms[i] if p.sector == 1 else c.sector2_ms[i]
            ready = not np.isnan(ms) and ms > 0
            if not ready and t - p.since < PENDING_TIMEOUT_S:
                continue
            del self.pending_sectors[i]
            self.bus.publish(ev.SectorCompleted(uid, t, car_idx=i, lap=p.lap, sector=p.sector,
                                                sector_time_ms=int(ms) if ready else None))
