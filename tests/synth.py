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
