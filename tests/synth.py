"""Helpers that build synthetic F1 25 packets from the real dtypes, for tests."""

from __future__ import annotations

import numpy as np

from engineer.ingest.formats import f1_2025 as f

SESSION_UID = 0xDEADBEEF12345678
PLAYER = 0


class PacketFactory:
    """Builds packets with a consistent header (session UID, rising frame ids/session time)."""

    def __init__(self, session_uid: int = SESSION_UID, player: int = PLAYER):
        self.session_uid = session_uid
        self.player = player
        self.frame = 0
        self.session_time = 0.0

    def blank(self, packet_id: int) -> np.ndarray:
        """A zeroed 1-element record array for packet_id with the header filled in."""
        _name, dtype, _size = f.PACKETS[packet_id]
        arr = np.zeros(1, dtype)
        h = arr[0]["m_header"]
        h["m_packetFormat"] = 2025
        h["m_gameYear"] = 25
        h["m_gameMajorVersion"] = 1
        h["m_gameMinorVersion"] = 0
        h["m_packetVersion"] = 1
        h["m_packetId"] = packet_id
        h["m_sessionUID"] = self.session_uid
        h["m_sessionTime"] = self.session_time
        h["m_frameIdentifier"] = self.frame
        h["m_overallFrameIdentifier"] = self.frame
        h["m_playerCarIndex"] = self.player
        h["m_secondaryPlayerCarIndex"] = 255
        return arr

    def tick(self, dt: float = 1 / 60):
        self.frame += 1
        self.session_time += dt

    # Convenience builders -----------------------------------------------------------------

    def session(self, track_id=7, session_type=15, total_laps=5, track_length=5891, **kw) -> bytes:
        a = self.blank(f.PacketId.SESSION)
        r = a[0]
        r["m_trackId"] = track_id
        r["m_sessionType"] = session_type
        r["m_totalLaps"] = total_laps
        r["m_trackLength"] = track_length
        for k, v in kw.items():
            r[k] = v
        return a.tobytes()

    def participants(self, num_active=2, telemetry_public=None, names=None) -> bytes:
        a = self.blank(f.PacketId.PARTICIPANTS)
        r = a[0]
        r["m_numActiveCars"] = num_active
        p = r["m_participants"]
        for i in range(num_active):
            p[i]["m_yourTelemetry"] = 1 if telemetry_public is None else telemetry_public[i]
            p[i]["m_name"] = (names[i] if names else f"DRIVER {i}").encode()
            p[i]["m_aiControlled"] = 0 if i == self.player else 1
        return a.tobytes()

    def lap_data(self, cars: dict[int, dict]) -> bytes:
        """cars: {car_index: {field_without_m_prefix: value}}. Cars not listed are result_status 0."""
        a = self.blank(f.PacketId.LAP_DATA)
        ld = a[0]["m_lapData"]
        a[0]["m_timeTrialPBCarIdx"] = 255
        a[0]["m_timeTrialRivalCarIdx"] = 255
        for idx, fields in cars.items():
            ld[idx]["m_resultStatus"] = 2
            for k, v in fields.items():
                ld[idx]["m_" + k] = v
        return a.tobytes()

    def car_status(self, cars: dict[int, dict]) -> bytes:
        a = self.blank(f.PacketId.CAR_STATUS)
        cs = a[0]["m_carStatusData"]
        for idx, fields in cars.items():
            for k, v in fields.items():
                cs[idx]["m_" + k] = v
        return a.tobytes()

    def car_damage(self, cars: dict[int, dict]) -> bytes:
        a = self.blank(f.PacketId.CAR_DAMAGE)
        cd = a[0]["m_carDamageData"]
        for idx, fields in cars.items():
            for k, v in fields.items():
                cd[idx]["m_" + k] = v
        return a.tobytes()

    def car_telemetry(self, cars: dict[int, dict]) -> bytes:
        a = self.blank(f.PacketId.CAR_TELEMETRY)
        a[0]["m_mfdPanelIndex"] = 255
        ct = a[0]["m_carTelemetryData"]
        for idx, fields in cars.items():
            for k, v in fields.items():
                ct[idx]["m_" + k] = v
        return a.tobytes()

    def event(self, code: str, **details) -> bytes:
        a = self.blank(f.PacketId.EVENT)
        a[0]["m_eventStringCode"] = code.encode("ascii")
        dtype = f.EVENT_DETAIL_DTYPES.get(code.encode("ascii"))
        if dtype is not None:
            d = np.zeros(1, dtype)
            for k, v in details.items():
                d[0][k] = v
            buf = np.zeros(f.EVENT_DETAILS_SIZE, np.uint8)
            buf[: dtype.itemsize] = np.frombuffer(d.tobytes(), np.uint8)
            a[0]["m_eventDetails"] = buf
        return a.tobytes()


def split_ms(ms: int) -> tuple[int, int]:
    """Sector times come as (ms part, minutes part)."""
    return ms % 60000, ms // 60000


class FakeRace:
    """Generates a plausible packet stream for a short session, frame by frame at 60 Hz.

    Each car drives laps of `lap_times_ms[car][lap-1]`; sectors split the lap 30/40/30 by time.
    Options let tests inject the awkward cases: an invalid lap, a pit stop, a flashback, and the
    last-lap time arriving a few packets late.
    """

    LAP_LEN = 5000.0

    def __init__(self, lap_times_ms: dict[int, list[int]], session_type=15, track_id=10,
                 invalid_laps: dict[int, set[int]] | None = None,
                 pit_laps: dict[int, int] | None = None,
                 last_lap_delay_frames: int = 0, extra_packets: bool = True,
                 session_uid: int = SESSION_UID, telemetry_public: list[int] | None = None):
        self.pf = PacketFactory(session_uid=session_uid)
        self.lap_times = lap_times_ms
        self.session_type = session_type
        self.track_id = track_id
        self.invalid_laps = invalid_laps or {}
        self.pit_laps = pit_laps or {}           # car -> lap on which it pits (end of that lap)
        self.delay = last_lap_delay_frames
        self.extra = extra_packets
        self.telemetry_public = telemetry_public
        self.t0_ns = 1_700_000_000_000_000_000

    def _car_at(self, car: int, t_ms: float) -> dict:
        laps = self.lap_times[car]
        elapsed = 0.0
        last = 0
        for n, lt in enumerate(laps, start=1):
            if t_ms < elapsed + lt:
                into = t_ms - elapsed
                frac = into / lt
                s1, s2 = round(lt * 0.3), round(lt * 0.4)
                sector = 0 if into < s1 else (1 if into < s1 + s2 else 2)
                pit = 0
                if self.pit_laps.get(car) == n and frac > 0.95:
                    pit = 1
                if self.pit_laps.get(car) == n - 1 and frac < 0.05:
                    pit = 2 if frac < 0.02 else 1
                return dict(lap=n, into=into, frac=frac, sector=sector,
                            s1=s1 if sector >= 1 else 0, s2=s2 if sector >= 2 else 0,
                            last=last, last_changed_at=elapsed, pit=pit,
                            invalid=1 if n in self.invalid_laps.get(car, set()) and frac > 0.5 else 0)
            elapsed += lt
            last = lt
        return dict(lap=len(laps) + 1, into=0, frac=0, sector=0, s1=0, s2=0, last=last,
                    last_changed_at=elapsed, pit=0, invalid=0)

    def frames(self, duration_ms: float | None = None, flashback_at_ms: float | None = None,
               flashback_back_ms: float = 20000):
        """Yield (t_ns, raw) packets. If flashback_at_ms is given, at that moment the session
        jumps back flashback_back_ms (an FLBK event is sent first)."""
        pf = self.pf
        cars = sorted(self.lap_times)
        if duration_ms is None:
            duration_ms = min(sum(v) for v in self.lap_times.values()) + 3000
        frame = 0
        wall_ms = 0.0
        game_ms = 0.0
        did_flashback = False
        step = 1000 / 60
        while wall_ms <= duration_ms:
            t_ns = self.t0_ns + int(wall_ms * 1e6)
            pf.session_time = game_ms / 1000
            pf.frame = frame
            if flashback_at_ms is not None and not did_flashback and game_ms >= flashback_at_ms:
                did_flashback = True
                target = game_ms - flashback_back_ms
                yield t_ns, pf.event("FLBK", flashbackFrameIdentifier=int(target / step),
                                     flashbackSessionTime=target / 1000)
                game_ms = target
                pf.session_time = game_ms / 1000
            if frame % 30 == 0:
                yield t_ns, pf.session(track_id=self.track_id, session_type=self.session_type,
                                       total_laps=max(len(v) for v in self.lap_times.values()))
            if frame % 300 == 0:
                yield t_ns, pf.participants(num_active=len(cars),
                                            telemetry_public=self.telemetry_public)
            lap = {}
            for car in cars:
                st = self._car_at(car, game_ms)
                last = st["last"]
                if self.delay and game_ms - st["last_changed_at"] < self.delay * step:
                    # simulate the game updating last-lap time a few frames late
                    prev = self.lap_times[car][st["lap"] - 3] if st["lap"] >= 3 else 0
                    last = prev
                lap[car] = {
                    "currentLapNum": st["lap"], "lastLapTimeInMS": last,
                    "currentLapTimeInMS": int(st["into"]), "lapDistance": st["frac"] * self.LAP_LEN,
                    "totalDistance": (st["lap"] - 1 + st["frac"]) * self.LAP_LEN,
                    "sector": st["sector"],
                    "sector1TimeMSPart": split_ms(st["s1"])[0],
                    "sector1TimeMinutesPart": split_ms(st["s1"])[1],
                    "sector2TimeMSPart": split_ms(st["s2"])[0],
                    "sector2TimeMinutesPart": split_ms(st["s2"])[1],
                    "pitStatus": st["pit"], "currentLapInvalid": st["invalid"],
                    "carPosition": car + 1, "gridPosition": car + 1, "driverStatus": 4,
                }
            yield t_ns, pf.lap_data(lap)
            yield t_ns, pf.car_telemetry({c: {"speed": 250, "throttle": 1.0,
                                              "tyresSurfaceTemperature": [90, 91, 92, 93]}
                                          for c in cars})
            yield t_ns, pf.car_status({c: {"visualTyreCompound": 16, "actualTyreCompound": 18,
                                           "tyresAgeLaps": lap[c]["currentLapNum"] - 1,
                                           "fuelInTank": 50.0} for c in cars})
            if frame % 6 == 0:
                yield t_ns, pf.car_damage({c: {"tyresWear": [5.5, 6.0, 4.0, 4.5]} for c in cars})
            if self.extra:
                yield t_ns, pf.blank(f.PacketId.MOTION).tobytes()
                yield t_ns, pf.blank(f.PacketId.MOTION_EX).tobytes()
                if frame % 3 == 0:
                    a = pf.blank(f.PacketId.SESSION_HISTORY)
                    a[0]["m_carIdx"] = cars[(frame // 3) % len(cars)]
                    yield t_ns, a.tobytes()
            frame += 1
            wall_ms += step
            game_ms += step
