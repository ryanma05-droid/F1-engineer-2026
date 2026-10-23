"""The live model of the race. Only `RaceState.apply()` writes to it; everything else reads.

Layout, in plain terms:
- `session`: one-per-session facts (track, session type, laps, weather, pit window, safety car).
- `cars`: per-car numpy arrays with one slot per car index (22). Car indices never change during a
  session, so `cars.position[5]` is always the same driver. Numbers are stored as float arrays so
  that "unknown" can be NaN instead of a misleading 0.
- `player`: a convenience view of the player's car (fuel, ERS, per-wheel wear/temps, damage).

Unknown, never zero:
- Cars whose Lap Data `m_resultStatus` is 0 (invalid) or 1 (inactive) hold no real data; their
  values are NaN and `cars.active[i]` is False.
- When another player has "Your Telemetry" set to restricted (Participants `m_yourTelemetry` == 0),
  the game sends zeros for the fields in the spec's "Restricted data" list. We store those as NaN.
  The player's own car is always visible.

Wheel arrays (shape (22, 4)) are ordered RL, RR, FL, FR, as in the game.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from engineer.ingest.formats import f1_2025 as f
from engineer.state import checks

log = logging.getLogger(__name__)

N = f.MAX_CARS
WHEELS = ("RL", "RR", "FL", "FR")


def _nan(shape=N) -> np.ndarray:
    return np.full(shape, np.nan)


@dataclass
class SessionInfo:
    track_id: int | None = None
    session_type: int | None = None
    total_laps: int | None = None
    track_length_m: int | None = None
    weather: int | None = None
    track_temp_c: int | None = None
    air_temp_c: int | None = None
    pit_speed_limit_kph: int | None = None
    safety_car_status: int | None = None   # 0 none, 1 full, 2 virtual, 3 formation lap
    pit_window_ideal_lap: int | None = None
    pit_window_latest_lap: int | None = None
    pit_rejoin_position: int | None = None
    sector2_start_m: float | None = None
    sector3_start_m: float | None = None
    network_game: bool | None = None
    formula: int | None = None

    @property
    def track_name(self) -> str:
        if self.track_id is None:
            return "Unknown track"
        return f.TRACKS.get(self.track_id, f"Track {self.track_id}")

    @property
    def session_name(self) -> str:
        if self.session_type is None:
            return "Unknown session"
        return f.SESSION_TYPES.get(self.session_type, f"Session {self.session_type}")

    @property
    def is_race(self) -> bool:
        return self.session_type in f.RACE_SESSION_TYPES


class Cars:
    """Per-car arrays. Index = car index from the game (0..21)."""

    def __init__(self):
        # Participants
        self.names: list[str | None] = [None] * N
        self.ai_controlled = _nan()
        self.team_id = _nan()
        self.race_number = _nan()
        # Telemetry visibility: True public, False restricted. Unknown until Participants arrives,
        # so we start as False (restricted) and fill restricted fields with NaN until told otherwise.
        self.telemetry_public = np.zeros(N, dtype=bool)
        self.num_active_cars: int | None = None

        # Lap Data
        self.active = np.zeros(N, dtype=bool)
        self.result_status = _nan()
        self.position = _nan()
        self.lap = _nan()                  # current lap number
        self.lap_distance_m = _nan()
        self.total_distance_m = _nan()
        self.current_lap_time_ms = _nan()
        self.last_lap_time_ms = _nan()
        self.sector = _nan()               # 0, 1, 2
        self.sector1_ms = _nan()           # current lap's sector 1 (0 until set)
        self.sector2_ms = _nan()
        self.current_lap_invalid = _nan()
        self.pit_status = _nan()           # 0 none, 1 pitting, 2 in pit area
        self.num_pit_stops = _nan()
        self.penalties_s = _nan()
        self.grid_position = _nan()
        self.driver_status = _nan()
        self.delta_to_car_in_front_ms = _nan()
        self.delta_to_leader_ms = _nan()

        # Car Status (not restricted)
        self.actual_compound = _nan()
        self.visual_compound = _nan()
        self.tyre_age_laps = _nan()
        self.fia_flag = _nan()
        # Car Status (restricted -> NaN when hidden)
        self.fuel_in_tank = _nan()
        self.fuel_capacity = _nan()
        self.fuel_remaining_laps = _nan()
        self.fuel_mix = _nan()
        self.front_brake_bias = _nan()
        self.ers_store_energy_j = _nan()
        self.ers_deploy_mode = _nan()
        self.ers_deployed_this_lap_j = _nan()
        self.ers_harvested_mguk_j = _nan()
        self.ers_harvested_mguh_j = _nan()
        self.engine_power_ice_w = _nan()
        self.engine_power_mguk_w = _nan()

        # Car Telemetry (not restricted)
        self.speed_kph = _nan()
        self.throttle = _nan()
        self.brake = _nan()
        self.steer = _nan()
        self.gear = _nan()
        self.drs = _nan()
        self.brake_temp_c = _nan((N, 4))
        self.tyre_surface_temp_c = _nan((N, 4))
        self.tyre_inner_temp_c = _nan((N, 4))
        self.tyre_pressure_psi = _nan((N, 4))

        # Car Damage. Tyre blisters are not in the restricted list; everything else here is.
        self.tyre_blisters_pct = _nan((N, 4))
        self.tyre_wear_pct = _nan((N, 4))
        self.tyre_damage_pct = _nan((N, 4))
        self.brake_damage_pct = _nan((N, 4))
        self.damage: dict[str, np.ndarray] = {name: _nan() for name in _DAMAGE_FIELDS}

        # Per-car packets kept whole (copies, small): latest Session History and Tyre Sets.
        self.session_history: list[np.void | None] = [None] * N
        self.tyre_sets: list[np.void | None] = [None] * N


# Car Damage scalar fields, all restricted. state key -> packet field
_DAMAGE_FIELDS = {
    "front_left_wing": "m_frontLeftWingDamage",
    "front_right_wing": "m_frontRightWingDamage",
    "rear_wing": "m_rearWingDamage",
    "floor": "m_floorDamage",
    "diffuser": "m_diffuserDamage",
    "sidepod": "m_sidepodDamage",
    "drs_fault": "m_drsFault",
    "ers_fault": "m_ersFault",
    "gearbox": "m_gearBoxDamage",
    "engine": "m_engineDamage",
    "engine_mguh_wear": "m_engineMGUHWear",
    "engine_es_wear": "m_engineESWear",
    "engine_ce_wear": "m_engineCEWear",
    "engine_ice_wear": "m_engineICEWear",
    "engine_mguk_wear": "m_engineMGUKWear",
    "engine_tc_wear": "m_engineTCWear",
    "engine_blown": "m_engineBlown",
    "engine_seized": "m_engineSeized",
}
# ersFault, engineBlown and engineSeized are not in the spec's restricted list; the rest are.
_DAMAGE_UNRESTRICTED = {"ers_fault", "engine_blown", "engine_seized"}

# Car Status restricted fields: state attribute -> packet field
_STATUS_RESTRICTED = {
    "fuel_in_tank": "m_fuelInTank",
    "fuel_capacity": "m_fuelCapacity",
    "fuel_remaining_laps": "m_fuelRemainingLaps",
    "fuel_mix": "m_fuelMix",
    "front_brake_bias": "m_frontBrakeBias",
    "ers_store_energy_j": "m_ersStoreEnergy",
    "ers_deploy_mode": "m_ersDeployMode",
    "ers_deployed_this_lap_j": "m_ersDeployedThisLap",
    "ers_harvested_mguk_j": "m_ersHarvestedThisLapMGUK",
    "ers_harvested_mguh_j": "m_ersHarvestedThisLapMGUH",
    "engine_power_ice_w": "m_enginePowerICE",
    "engine_power_mguk_w": "m_enginePowerMGUK",
}


@dataclass
class PlayerView:
    """Snapshot of the player's car, read from the per-car arrays."""

    index: int
    name: str | None
    position: float
    lap: float
    fuel_in_tank: float
    fuel_remaining_laps: float
    ers_store_energy_j: float
    ers_deploy_mode: float
    visual_compound: float
    actual_compound: float
    tyre_age_laps: float
    tyre_wear_pct: np.ndarray        # RL, RR, FL, FR
    tyre_damage_pct: np.ndarray
    tyre_blisters_pct: np.ndarray
    tyre_surface_temp_c: np.ndarray
    tyre_inner_temp_c: np.ndarray
    brake_temp_c: np.ndarray
    tyre_pressure_psi: np.ndarray
    damage: dict[str, float]


@dataclass
class FlashbackInfo:
    count: int = 0
    last_frame: int | None = None
    last_session_time: float | None = None


class RaceState:
    def __init__(self):
        self.reset(session_uid=None)

    def reset(self, session_uid: int | None) -> None:
        self.session_uid = session_uid
        self.player_index: int | None = None
        self.session_time: float | None = None
        self.frame: int | None = None
        self.session = SessionInfo()
        self.cars = Cars()
        self.lap_positions = np.zeros((0, N), dtype=np.uint8)  # [lap][car] position at lap start
        self.flashbacks = FlashbackInfo()
        self.checks = checks.Checks()
        self.packets_applied = 0

    # -- reading -------------------------------------------------------------------------------

    @property
    def player(self) -> PlayerView | None:
        i = self.player_index
        if i is None or i >= N:
            return None
        c = self.cars
        return PlayerView(
            index=i,
            name=c.names[i],
            position=c.position[i],
            lap=c.lap[i],
            fuel_in_tank=c.fuel_in_tank[i],
            fuel_remaining_laps=c.fuel_remaining_laps[i],
            ers_store_energy_j=c.ers_store_energy_j[i],
            ers_deploy_mode=c.ers_deploy_mode[i],
            visual_compound=c.visual_compound[i],
            actual_compound=c.actual_compound[i],
            tyre_age_laps=c.tyre_age_laps[i],
            tyre_wear_pct=c.tyre_wear_pct[i].copy(),
            tyre_damage_pct=c.tyre_damage_pct[i].copy(),
            tyre_blisters_pct=c.tyre_blisters_pct[i].copy(),
            tyre_surface_temp_c=c.tyre_surface_temp_c[i].copy(),
            tyre_inner_temp_c=c.tyre_inner_temp_c[i].copy(),
            brake_temp_c=c.brake_temp_c[i].copy(),
            tyre_pressure_psi=c.tyre_pressure_psi[i].copy(),
            damage={k: float(v[i]) for k, v in c.damage.items()},
        )

    # -- writing -------------------------------------------------------------------------------

    def apply(self, header: np.void, packet_id: int, rec: np.void | None) -> bool:
        """Update state from one parsed packet. Returns True if the session changed (state reset)."""
        uid = int(header["m_sessionUID"])
        if uid == 0:
            return False  # menu packets carry no session
        new_session = uid != self.session_uid
        if new_session:
            self.reset(uid)
        self.player_index = int(header["m_playerCarIndex"])
        self.session_time = float(header["m_sessionTime"])
        self.frame = int(header["m_frameIdentifier"])
        self.packets_applied += 1
        if rec is not None:
            handler = _HANDLERS.get(packet_id)
            if handler is not None:
                handler(self, rec)
        return new_session

    def _restricted_mask(self) -> np.ndarray:
        """True where a car's restricted fields must be treated as unknown."""
        hidden = ~self.cars.telemetry_public
        if self.player_index is not None and self.player_index < N:
            hidden[self.player_index] = False
        return hidden

    def _on_session(self, r: np.void) -> None:
        s = self.session
        s.track_id = int(r["m_trackId"])
        s.session_type = int(r["m_sessionType"])
        s.total_laps = int(r["m_totalLaps"])
        s.track_length_m = int(r["m_trackLength"])
        s.weather = int(r["m_weather"])
        s.track_temp_c = int(r["m_trackTemperature"])
        s.air_temp_c = int(r["m_airTemperature"])
        s.pit_speed_limit_kph = int(r["m_pitSpeedLimit"])
        s.safety_car_status = int(r["m_safetyCarStatus"])
        s.pit_window_ideal_lap = int(r["m_pitStopWindowIdealLap"])
        s.pit_window_latest_lap = int(r["m_pitStopWindowLatestLap"])
        s.pit_rejoin_position = int(r["m_pitStopRejoinPosition"])
        s.sector2_start_m = float(r["m_sector2LapDistanceStart"])
        s.sector3_start_m = float(r["m_sector3LapDistanceStart"])
        s.network_game = bool(r["m_networkGame"])
        s.formula = int(r["m_formula"])

    def _on_participants(self, r: np.void) -> None:
        c = self.cars
        p = r["m_participants"]
        c.num_active_cars = int(r["m_numActiveCars"])
        c.telemetry_public = p["m_yourTelemetry"] == 1
        c.ai_controlled = p["m_aiControlled"].astype(float)
        c.team_id = p["m_teamId"].astype(float)
        c.race_number = p["m_raceNumber"].astype(float)
        c.names = [f.decode_name(bytes(n)) or None for n in p["m_name"]]

    def _on_lap_data(self, r: np.void) -> None:
        c = self.cars
        ld = r["m_lapData"]
        status = ld["m_resultStatus"]
        active = status >= 2   # 0 invalid, 1 inactive -> no valid data
        c.active = active
        c.result_status = status.astype(float)

        def put(attr: str, values: np.ndarray) -> None:
            setattr(c, attr, np.where(active, values, np.nan))

        put("position", ld["m_carPosition"])
        put("lap", ld["m_currentLapNum"])
        put("lap_distance_m", ld["m_lapDistance"])
        put("total_distance_m", ld["m_totalDistance"])
        put("current_lap_time_ms", ld["m_currentLapTimeInMS"])
        put("last_lap_time_ms", ld["m_lastLapTimeInMS"])
        put("sector", ld["m_sector"])
        put("sector1_ms", ld["m_sector1TimeMSPart"] + ld["m_sector1TimeMinutesPart"] * 60000.0)
        put("sector2_ms", ld["m_sector2TimeMSPart"] + ld["m_sector2TimeMinutesPart"] * 60000.0)
        put("current_lap_invalid", ld["m_currentLapInvalid"])
        put("pit_status", ld["m_pitStatus"])
        put("num_pit_stops", ld["m_numPitStops"])
        put("penalties_s", ld["m_penalties"])
        put("grid_position", ld["m_gridPosition"])
        put("driver_status", ld["m_driverStatus"])
        put("delta_to_car_in_front_ms",
            ld["m_deltaToCarInFrontMSPart"] + ld["m_deltaToCarInFrontMinutesPart"] * 60000.0)
        put("delta_to_leader_ms",
            ld["m_deltaToRaceLeaderMSPart"] + ld["m_deltaToRaceLeaderMinutesPart"] * 60000.0)
        checks.check_grid_order(self)

    def _on_car_status(self, r: np.void) -> None:
        c = self.cars
        cs = r["m_carStatusData"]
        c.actual_compound = cs["m_actualTyreCompound"].astype(float)
        c.visual_compound = cs["m_visualTyreCompound"].astype(float)
        c.tyre_age_laps = cs["m_tyresAgeLaps"].astype(float)
        c.fia_flag = cs["m_vehicleFiaFlags"].astype(float)
        hidden = self._restricted_mask()
        for attr, fld in _STATUS_RESTRICTED.items():
            setattr(c, attr, np.where(hidden, np.nan, cs[fld].astype(float)))

    def _on_car_telemetry(self, r: np.void) -> None:
        c = self.cars
        t = r["m_carTelemetryData"]
        c.speed_kph = t["m_speed"].astype(float)
        c.throttle = t["m_throttle"].astype(float)
        c.brake = t["m_brake"].astype(float)
        c.steer = t["m_steer"].astype(float)
        c.gear = t["m_gear"].astype(float)
        c.drs = t["m_drs"].astype(float)
        c.brake_temp_c = t["m_brakesTemperature"].astype(float)
        c.tyre_surface_temp_c = t["m_tyresSurfaceTemperature"].astype(float)
        c.tyre_inner_temp_c = t["m_tyresInnerTemperature"].astype(float)
        c.tyre_pressure_psi = t["m_tyresPressure"].astype(float)

    def _on_car_damage(self, r: np.void) -> None:
        c = self.cars
        d = r["m_carDamageData"]
        hidden = self._restricted_mask()
        hidden_w = hidden[:, None]
        c.tyre_blisters_pct = d["m_tyreBlisters"].astype(float)
        c.tyre_wear_pct = np.where(hidden_w, np.nan, d["m_tyresWear"].astype(float))
        c.tyre_damage_pct = np.where(hidden_w, np.nan, d["m_tyresDamage"].astype(float))
        c.brake_damage_pct = np.where(hidden_w, np.nan, d["m_brakesDamage"].astype(float))
        for key, fld in _DAMAGE_FIELDS.items():
            vals = d[fld].astype(float)
            c.damage[key] = vals if key in _DAMAGE_UNRESTRICTED else np.where(hidden, np.nan, vals)

    def _on_session_history(self, r: np.void) -> None:
        idx = int(r["m_carIdx"])
        if idx < N:
            self.cars.session_history[idx] = r.copy()

    def _on_tyre_sets(self, r: np.void) -> None:
        idx = int(r["m_carIdx"])
        if idx < N:
            # Tyre Sets is entirely restricted: keep it only when visible.
            self.cars.tyre_sets[idx] = None if self._restricted_mask()[idx] else r.copy()

    def _on_lap_positions(self, r: np.void) -> None:
        start = int(r["m_lapStart"])
        n = int(r["m_numLaps"])
        rows = r["m_positionForVehicleIdx"][: max(0, min(n, f.LAP_POSITIONS_MAX_LAPS))]
        need = start + len(rows)
        if self.lap_positions.shape[0] < need:
            grown = np.zeros((need, N), dtype=np.uint8)
            grown[: self.lap_positions.shape[0]] = self.lap_positions
            self.lap_positions = grown
        self.lap_positions[start:need] = rows

    def _on_event(self, r: np.void) -> None:
        code, details = f.decode_event(r)
        if code == "FLBK":
            self.flashbacks.count += 1
            self.flashbacks.last_frame = details["flashbackFrameIdentifier"]
            self.flashbacks.last_session_time = details["flashbackSessionTime"]


_HANDLERS = {
    f.PacketId.SESSION: RaceState._on_session,
    f.PacketId.PARTICIPANTS: RaceState._on_participants,
    f.PacketId.LAP_DATA: RaceState._on_lap_data,
    f.PacketId.CAR_STATUS: RaceState._on_car_status,
    f.PacketId.CAR_TELEMETRY: RaceState._on_car_telemetry,
    f.PacketId.CAR_DAMAGE: RaceState._on_car_damage,
    f.PacketId.SESSION_HISTORY: RaceState._on_session_history,
    f.PacketId.TYRE_SETS: RaceState._on_tyre_sets,
    f.PacketId.LAP_POSITIONS: RaceState._on_lap_positions,
    f.PacketId.EVENT: RaceState._on_event,
}
