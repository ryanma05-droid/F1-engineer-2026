"""F1 25 UDP packet layouts (packet format 2025).

Every layout here is transcribed from docs/spec/f1_25_udp_spec.md (Appendix A of the kickoff prompt,
same content as the EA PDF in docs/spec/). Do not edit layouts from memory or from older game years.

How it works, in plain terms:
- Each C struct becomes a numpy "structured dtype": a named list of fields with exact byte types.
  '<' means little-endian; numpy packs fields with no padding unless asked, which matches the game.
- `np.frombuffer(raw, dtype)` then reads a packet *in place* (no copying), giving us named access
  like `rec["m_lapData"]["m_carPosition"]` -> an array of 22 positions.
- At import we assert every packet dtype's size equals the size in the spec, so a transcription
  mistake fails immediately instead of producing garbage numbers.
"""

from __future__ import annotations

import numpy as np

PACKET_FORMAT = 2025
MAX_CARS = 22  # cs_maxNumCarsInUDPData

# Short aliases so the layouts read like the C structs.
u8, i8, u16, i16, u32, u64 = "u1", "i1", "<u2", "<i2", "<u4", "<u8"
f32, f64 = "<f4", "<f8"


def _dt(fields: list) -> np.dtype:
    return np.dtype(fields)  # align=False by default -> packed, like the game's structs


# --- Header (29 bytes) -------------------------------------------------------------------------

HEADER = _dt([
    ("m_packetFormat", u16),
    ("m_gameYear", u8),
    ("m_gameMajorVersion", u8),
    ("m_gameMinorVersion", u8),
    ("m_packetVersion", u8),
    ("m_packetId", u8),
    ("m_sessionUID", u64),
    ("m_sessionTime", f32),
    ("m_frameIdentifier", u32),
    ("m_overallFrameIdentifier", u32),
    ("m_playerCarIndex", u8),
    ("m_secondaryPlayerCarIndex", u8),
])
HEADER_SIZE = 29

# --- 0 Motion ----------------------------------------------------------------------------------

CAR_MOTION = _dt([
    ("m_worldPositionX", f32), ("m_worldPositionY", f32), ("m_worldPositionZ", f32),
    ("m_worldVelocityX", f32), ("m_worldVelocityY", f32), ("m_worldVelocityZ", f32),
    ("m_worldForwardDirX", i16), ("m_worldForwardDirY", i16), ("m_worldForwardDirZ", i16),
    ("m_worldRightDirX", i16), ("m_worldRightDirY", i16), ("m_worldRightDirZ", i16),
    ("m_gForceLateral", f32), ("m_gForceLongitudinal", f32), ("m_gForceVertical", f32),
    ("m_yaw", f32), ("m_pitch", f32), ("m_roll", f32),
])
PACKET_MOTION = _dt([("m_header", HEADER), ("m_carMotionData", CAR_MOTION, (MAX_CARS,))])

# --- 1 Session ---------------------------------------------------------------------------------

MARSHAL_ZONE = _dt([("m_zoneStart", f32), ("m_zoneFlag", i8)])

WEATHER_FORECAST_SAMPLE = _dt([
    ("m_sessionType", u8),
    ("m_timeOffset", u8),
    ("m_weather", u8),
    ("m_trackTemperature", i8),
    ("m_trackTemperatureChange", i8),
    ("m_airTemperature", i8),
    ("m_airTemperatureChange", i8),
    ("m_rainPercentage", u8),
])

PACKET_SESSION = _dt([
    ("m_header", HEADER),
    ("m_weather", u8),
    ("m_trackTemperature", i8),
    ("m_airTemperature", i8),
    ("m_totalLaps", u8),
    ("m_trackLength", u16),
    ("m_sessionType", u8),
    ("m_trackId", i8),
    ("m_formula", u8),
    ("m_sessionTimeLeft", u16),
    ("m_sessionDuration", u16),
    ("m_pitSpeedLimit", u8),
    ("m_gamePaused", u8),
    ("m_isSpectating", u8),
    ("m_spectatorCarIndex", u8),
    ("m_sliProNativeSupport", u8),
    ("m_numMarshalZones", u8),
    ("m_marshalZones", MARSHAL_ZONE, (21,)),
    ("m_safetyCarStatus", u8),
    ("m_networkGame", u8),
    ("m_numWeatherForecastSamples", u8),
    ("m_weatherForecastSamples", WEATHER_FORECAST_SAMPLE, (64,)),
    ("m_forecastAccuracy", u8),
    ("m_aiDifficulty", u8),
    ("m_seasonLinkIdentifier", u32),
    ("m_weekendLinkIdentifier", u32),
    ("m_sessionLinkIdentifier", u32),
    ("m_pitStopWindowIdealLap", u8),
    ("m_pitStopWindowLatestLap", u8),
    ("m_pitStopRejoinPosition", u8),
    ("m_steeringAssist", u8),
    ("m_brakingAssist", u8),
    ("m_gearboxAssist", u8),
    ("m_pitAssist", u8),
    ("m_pitReleaseAssist", u8),
    ("m_ERSAssist", u8),
    ("m_DRSAssist", u8),
    ("m_dynamicRacingLine", u8),
    ("m_dynamicRacingLineType", u8),
    ("m_gameMode", u8),
    ("m_ruleSet", u8),
    ("m_timeOfDay", u32),
    ("m_sessionLength", u8),
    ("m_speedUnitsLeadPlayer", u8),
    ("m_temperatureUnitsLeadPlayer", u8),
    ("m_speedUnitsSecondaryPlayer", u8),
    ("m_temperatureUnitsSecondaryPlayer", u8),
    ("m_numSafetyCarPeriods", u8),
    ("m_numVirtualSafetyCarPeriods", u8),
    ("m_numRedFlagPeriods", u8),
    ("m_equalCarPerformance", u8),
    ("m_recoveryMode", u8),
    ("m_flashbackLimit", u8),
    ("m_surfaceType", u8),
    ("m_lowFuelMode", u8),
    ("m_raceStarts", u8),
    ("m_tyreTemperature", u8),
    ("m_pitLaneTyreSim", u8),
    ("m_carDamage", u8),
    ("m_carDamageRate", u8),
    ("m_collisions", u8),
    ("m_collisionsOffForFirstLapOnly", u8),
    ("m_mpUnsafePitRelease", u8),
    ("m_mpOffForGriefing", u8),
    ("m_cornerCuttingStringency", u8),
    ("m_parcFermeRules", u8),
    ("m_pitStopExperience", u8),
    ("m_safetyCar", u8),
    ("m_safetyCarExperience", u8),
    ("m_formationLap", u8),
    ("m_formationLapExperience", u8),
    ("m_redFlags", u8),
    ("m_affectsLicenceLevelSolo", u8),
    ("m_affectsLicenceLevelMP", u8),
    ("m_numSessionsInWeekend", u8),
    ("m_weekendStructure", u8, (12,)),
    ("m_sector2LapDistanceStart", f32),
    ("m_sector3LapDistanceStart", f32),
])

# --- 2 Lap Data --------------------------------------------------------------------------------

# FIELD-ORDER NOTE: the spec lists m_gridPosition BEFORE m_driverStatus, and that is what we
# implement. At least one developer reported that real packets have the two swapped (grid
# positions read as 0 at race start). engineer/state/checks.py warns when the data looks swapped;
# confirm against a real race-start recording before changing anything here.
LAP_DATA = _dt([
    ("m_lastLapTimeInMS", u32),
    ("m_currentLapTimeInMS", u32),
    ("m_sector1TimeMSPart", u16),
    ("m_sector1TimeMinutesPart", u8),
    ("m_sector2TimeMSPart", u16),
    ("m_sector2TimeMinutesPart", u8),
    ("m_deltaToCarInFrontMSPart", u16),
    ("m_deltaToCarInFrontMinutesPart", u8),
    ("m_deltaToRaceLeaderMSPart", u16),
    ("m_deltaToRaceLeaderMinutesPart", u8),
    ("m_lapDistance", f32),
    ("m_totalDistance", f32),
    ("m_safetyCarDelta", f32),
    ("m_carPosition", u8),
    ("m_currentLapNum", u8),
    ("m_pitStatus", u8),
    ("m_numPitStops", u8),
    ("m_sector", u8),
    ("m_currentLapInvalid", u8),
    ("m_penalties", u8),
    ("m_totalWarnings", u8),
    ("m_cornerCuttingWarnings", u8),
    ("m_numUnservedDriveThroughPens", u8),
    ("m_numUnservedStopGoPens", u8),
    ("m_gridPosition", u8),   # see FIELD-ORDER NOTE above
    ("m_driverStatus", u8),   # see FIELD-ORDER NOTE above
    ("m_resultStatus", u8),
    ("m_pitLaneTimerActive", u8),
    ("m_pitLaneTimeInLaneInMS", u16),
    ("m_pitStopTimerInMS", u16),
    ("m_pitStopShouldServePen", u8),
    ("m_speedTrapFastestSpeed", f32),
    ("m_speedTrapFastestLap", u8),
])
PACKET_LAP_DATA = _dt([
    ("m_header", HEADER),
    ("m_lapData", LAP_DATA, (MAX_CARS,)),
    ("m_timeTrialPBCarIdx", u8),
    ("m_timeTrialRivalCarIdx", u8),
])

# --- 3 Event -----------------------------------------------------------------------------------

# The details are a C union: 12 bytes (the largest member, SpeedTrap). We keep them as raw bytes
# in the packet dtype and decode with the member dtype matching the event code (decode_event).
EVENT_DETAILS_SIZE = 12
PACKET_EVENT = _dt([
    ("m_header", HEADER),
    ("m_eventStringCode", "S4"),
    ("m_eventDetails", u8, (EVENT_DETAILS_SIZE,)),
])

EVENT_DETAIL_DTYPES: dict[bytes, np.dtype] = {
    b"FTLP": _dt([("vehicleIdx", u8), ("lapTime", f32)]),
    b"RTMT": _dt([("vehicleIdx", u8), ("reason", u8)]),
    b"DRSD": _dt([("reason", u8)]),
    b"TMPT": _dt([("vehicleIdx", u8)]),
    b"RCWN": _dt([("vehicleIdx", u8)]),
    b"PENA": _dt([("penaltyType", u8), ("infringementType", u8), ("vehicleIdx", u8),
                  ("otherVehicleIdx", u8), ("time", u8), ("lapNum", u8), ("placesGained", u8)]),
    b"SPTP": _dt([("vehicleIdx", u8), ("speed", f32), ("isOverallFastestInSession", u8),
                  ("isDriverFastestInSession", u8), ("fastestVehicleIdxInSession", u8),
                  ("fastestSpeedInSession", f32)]),
    b"STLG": _dt([("numLights", u8)]),
    b"DTSV": _dt([("vehicleIdx", u8)]),
    b"SGSV": _dt([("vehicleIdx", u8), ("stopTime", f32)]),
    b"FLBK": _dt([("flashbackFrameIdentifier", u32), ("flashbackSessionTime", f32)]),
    b"BUTN": _dt([("buttonStatus", u32)]),
    b"OVTK": _dt([("overtakingVehicleIdx", u8), ("beingOvertakenVehicleIdx", u8)]),
    b"SCAR": _dt([("safetyCarType", u8), ("eventType", u8)]),
    b"COLL": _dt([("vehicle1Idx", u8), ("vehicle2Idx", u8)]),
}
# Codes with no details: SSTA, SEND, DRSE, CHQF, LGOT, RDFL.

# --- 4 Participants ----------------------------------------------------------------------------

LIVERY_COLOUR = _dt([("red", u8), ("green", u8), ("blue", u8)])
PARTICIPANT = _dt([
    ("m_aiControlled", u8),
    ("m_driverId", u8),
    ("m_networkId", u8),
    ("m_teamId", u8),
    ("m_myTeam", u8),
    ("m_raceNumber", u8),
    ("m_nationality", u8),
    ("m_name", "S32"),
    ("m_yourTelemetry", u8),
    ("m_showOnlineNames", u8),
    ("m_techLevel", u16),
    ("m_platform", u8),
    ("m_numColours", u8),
    ("m_liveryColours", LIVERY_COLOUR, (4,)),
])
PACKET_PARTICIPANTS = _dt([
    ("m_header", HEADER),
    ("m_numActiveCars", u8),
    ("m_participants", PARTICIPANT, (MAX_CARS,)),
])

# --- 5 Car Setups (not used in Phase 0) --------------------------------------------------------

CAR_SETUP = _dt([
    ("m_frontWing", u8), ("m_rearWing", u8),
    ("m_onThrottle", u8), ("m_offThrottle", u8),
    ("m_frontCamber", f32), ("m_rearCamber", f32),
    ("m_frontToe", f32), ("m_rearToe", f32),
    ("m_frontSuspension", u8), ("m_rearSuspension", u8),
    ("m_frontAntiRollBar", u8), ("m_rearAntiRollBar", u8),
    ("m_frontSuspensionHeight", u8), ("m_rearSuspensionHeight", u8),
    ("m_brakePressure", u8), ("m_brakeBias", u8), ("m_engineBraking", u8),
    ("m_rearLeftTyrePressure", f32), ("m_rearRightTyrePressure", f32),
    ("m_frontLeftTyrePressure", f32), ("m_frontRightTyrePressure", f32),
    ("m_ballast", u8),
    ("m_fuelLoad", f32),
])
PACKET_CAR_SETUPS = _dt([
    ("m_header", HEADER),
    ("m_carSetups", CAR_SETUP, (MAX_CARS,)),
    ("m_nextFrontWingValue", f32),
])

# --- 6 Car Telemetry ---------------------------------------------------------------------------

CAR_TELEMETRY = _dt([
    ("m_speed", u16),
    ("m_throttle", f32),
    ("m_steer", f32),
    ("m_brake", f32),
    ("m_clutch", u8),
    ("m_gear", i8),
    ("m_engineRPM", u16),
    ("m_drs", u8),
    ("m_revLightsPercent", u8),
    ("m_revLightsBitValue", u16),
    ("m_brakesTemperature", u16, (4,)),
    ("m_tyresSurfaceTemperature", u8, (4,)),
    ("m_tyresInnerTemperature", u8, (4,)),
    ("m_engineTemperature", u16),
    ("m_tyresPressure", f32, (4,)),
    ("m_surfaceType", u8, (4,)),
])
PACKET_CAR_TELEMETRY = _dt([
    ("m_header", HEADER),
    ("m_carTelemetryData", CAR_TELEMETRY, (MAX_CARS,)),
    ("m_mfdPanelIndex", u8),
    ("m_mfdPanelIndexSecondaryPlayer", u8),
    ("m_suggestedGear", i8),
])

# --- 7 Car Status ------------------------------------------------------------------------------

CAR_STATUS = _dt([
    ("m_tractionControl", u8),
    ("m_antiLockBrakes", u8),
    ("m_fuelMix", u8),
    ("m_frontBrakeBias", u8),
    ("m_pitLimiterStatus", u8),
    ("m_fuelInTank", f32),
    ("m_fuelCapacity", f32),
    ("m_fuelRemainingLaps", f32),
    ("m_maxRPM", u16),
    ("m_idleRPM", u16),
    ("m_maxGears", u8),
    ("m_drsAllowed", u8),
    ("m_drsActivationDistance", u16),
    ("m_actualTyreCompound", u8),
    ("m_visualTyreCompound", u8),
    ("m_tyresAgeLaps", u8),
    ("m_vehicleFiaFlags", i8),
    ("m_enginePowerICE", f32),
    ("m_enginePowerMGUK", f32),
    ("m_ersStoreEnergy", f32),
    ("m_ersDeployMode", u8),
    ("m_ersHarvestedThisLapMGUK", f32),
    ("m_ersHarvestedThisLapMGUH", f32),
    ("m_ersDeployedThisLap", f32),
    ("m_networkPaused", u8),
])
PACKET_CAR_STATUS = _dt([("m_header", HEADER), ("m_carStatusData", CAR_STATUS, (MAX_CARS,))])

# --- 8 Final Classification --------------------------------------------------------------------

FINAL_CLASSIFICATION = _dt([
    ("m_position", u8),
    ("m_numLaps", u8),
    ("m_gridPosition", u8),
    ("m_points", u8),
    ("m_numPitStops", u8),
    ("m_resultStatus", u8),
    ("m_resultReason", u8),
    ("m_bestLapTimeInMS", u32),
    ("m_totalRaceTime", f64),
    ("m_penaltiesTime", u8),
    ("m_numPenalties", u8),
    ("m_numTyreStints", u8),
    ("m_tyreStintsActual", u8, (8,)),
    ("m_tyreStintsVisual", u8, (8,)),
    ("m_tyreStintsEndLaps", u8, (8,)),
])
PACKET_FINAL_CLASSIFICATION = _dt([
    ("m_header", HEADER),
    ("m_numCars", u8),
    ("m_classificationData", FINAL_CLASSIFICATION, (MAX_CARS,)),
])

# --- 9 Lobby Info (not used) -------------------------------------------------------------------

LOBBY_INFO = _dt([
    ("m_aiControlled", u8), ("m_teamId", u8), ("m_nationality", u8), ("m_platform", u8),
    ("m_name", "S32"),
    ("m_carNumber", u8), ("m_yourTelemetry", u8), ("m_showOnlineNames", u8),
    ("m_techLevel", u16),
    ("m_readyStatus", u8),
])
PACKET_LOBBY_INFO = _dt([
    ("m_header", HEADER),
    ("m_numPlayers", u8),
    ("m_lobbyPlayers", LOBBY_INFO, (MAX_CARS,)),
])

# --- 10 Car Damage -----------------------------------------------------------------------------

CAR_DAMAGE = _dt([
    ("m_tyresWear", f32, (4,)),
    ("m_tyresDamage", u8, (4,)),
    ("m_brakesDamage", u8, (4,)),
    ("m_tyreBlisters", u8, (4,)),
    ("m_frontLeftWingDamage", u8),
    ("m_frontRightWingDamage", u8),
    ("m_rearWingDamage", u8),
    ("m_floorDamage", u8),
    ("m_diffuserDamage", u8),
    ("m_sidepodDamage", u8),
    ("m_drsFault", u8),
    ("m_ersFault", u8),
    ("m_gearBoxDamage", u8),
    ("m_engineDamage", u8),
    ("m_engineMGUHWear", u8),
    ("m_engineESWear", u8),
    ("m_engineCEWear", u8),
    ("m_engineICEWear", u8),
    ("m_engineMGUKWear", u8),
    ("m_engineTCWear", u8),
    ("m_engineBlown", u8),
    ("m_engineSeized", u8),
])
PACKET_CAR_DAMAGE = _dt([("m_header", HEADER), ("m_carDamageData", CAR_DAMAGE, (MAX_CARS,))])

# --- 11 Session History ------------------------------------------------------------------------

LAP_HISTORY = _dt([
    ("m_lapTimeInMS", u32),
    ("m_sector1TimeMSPart", u16),
    ("m_sector1TimeMinutesPart", u8),
    ("m_sector2TimeMSPart", u16),
    ("m_sector2TimeMinutesPart", u8),
    ("m_sector3TimeMSPart", u16),
    ("m_sector3TimeMinutesPart", u8),
    ("m_lapValidBitFlags", u8),
])
TYRE_STINT_HISTORY = _dt([
    ("m_endLap", u8),
    ("m_tyreActualCompound", u8),
    ("m_tyreVisualCompound", u8),
])
PACKET_SESSION_HISTORY = _dt([
    ("m_header", HEADER),
    ("m_carIdx", u8),
    ("m_numLaps", u8),
    ("m_numTyreStints", u8),
    ("m_bestLapTimeLapNum", u8),
    ("m_bestSector1LapNum", u8),
    ("m_bestSector2LapNum", u8),
    ("m_bestSector3LapNum", u8),
    ("m_lapHistoryData", LAP_HISTORY, (100,)),
    ("m_tyreStintsHistoryData", TYRE_STINT_HISTORY, (8,)),
])

# --- 12 Tyre Sets ------------------------------------------------------------------------------

TYRE_SET = _dt([
    ("m_actualTyreCompound", u8),
    ("m_visualTyreCompound", u8),
    ("m_wear", u8),
    ("m_available", u8),
    ("m_recommendedSession", u8),
    ("m_lifeSpan", u8),
    ("m_usableLife", u8),
    ("m_lapDeltaTime", i16),
    ("m_fitted", u8),
])
PACKET_TYRE_SETS = _dt([
    ("m_header", HEADER),
    ("m_carIdx", u8),
    ("m_tyreSetData", TYRE_SET, (20,)),
    ("m_fittedIdx", u8),
])

# --- 13 Motion Ex (player car only) ------------------------------------------------------------

PACKET_MOTION_EX = _dt([
    ("m_header", HEADER),
    ("m_suspensionPosition", f32, (4,)),
    ("m_suspensionVelocity", f32, (4,)),
    ("m_suspensionAcceleration", f32, (4,)),
    ("m_wheelSpeed", f32, (4,)),
    ("m_wheelSlipRatio", f32, (4,)),
    ("m_wheelSlipAngle", f32, (4,)),
    ("m_wheelLatForce", f32, (4,)),
    ("m_wheelLongForce", f32, (4,)),
    ("m_heightOfCOGAboveGround", f32),
    ("m_localVelocityX", f32),
    ("m_localVelocityY", f32),
    ("m_localVelocityZ", f32),
    ("m_angularVelocityX", f32),
    ("m_angularVelocityY", f32),
    ("m_angularVelocityZ", f32),
    ("m_angularAccelerationX", f32),
    ("m_angularAccelerationY", f32),
    ("m_angularAccelerationZ", f32),
    ("m_frontWheelsAngle", f32),
    ("m_wheelVertForce", f32, (4,)),
    ("m_frontAeroHeight", f32),
    ("m_rearAeroHeight", f32),
    ("m_frontRollAngle", f32),
    ("m_rearRollAngle", f32),
    ("m_chassisYaw", f32),
    ("m_chassisPitch", f32),
    ("m_wheelCamber", f32, (4,)),
    ("m_wheelCamberGain", f32, (4,)),
])

# --- 14 Time Trial -----------------------------------------------------------------------------

TIME_TRIAL_DATA_SET = _dt([
    ("m_carIdx", u8),
    ("m_teamId", u8),
    ("m_lapTimeInMS", u32),
    ("m_sector1TimeInMS", u32),
    ("m_sector2TimeInMS", u32),
    ("m_sector3TimeInMS", u32),
    ("m_tractionControl", u8),
    ("m_gearboxAssist", u8),
    ("m_antiLockBrakes", u8),
    ("m_equalCarPerformance", u8),
    ("m_customSetup", u8),
    ("m_valid", u8),
])
PACKET_TIME_TRIAL = _dt([
    ("m_header", HEADER),
    ("m_playerSessionBestDataSet", TIME_TRIAL_DATA_SET),
    ("m_personalBestDataSet", TIME_TRIAL_DATA_SET),
    ("m_rivalDataSet", TIME_TRIAL_DATA_SET),
])

# --- 15 Lap Positions --------------------------------------------------------------------------

LAP_POSITIONS_MAX_LAPS = 50
PACKET_LAP_POSITIONS = _dt([
    ("m_header", HEADER),
    ("m_numLaps", u8),
    ("m_lapStart", u8),
    # [50][22]: position of each vehicle at the start of each lap, 0 = no record
    ("m_positionForVehicleIdx", u8, (LAP_POSITIONS_MAX_LAPS, MAX_CARS)),
])

# --- Packet table ------------------------------------------------------------------------------

class PacketId:
    MOTION = 0
    SESSION = 1
    LAP_DATA = 2
    EVENT = 3
    PARTICIPANTS = 4
    CAR_SETUPS = 5
    CAR_TELEMETRY = 6
    CAR_STATUS = 7
    FINAL_CLASSIFICATION = 8
    LOBBY_INFO = 9
    CAR_DAMAGE = 10
    SESSION_HISTORY = 11
    TYRE_SETS = 12
    MOTION_EX = 13
    TIME_TRIAL = 14
    LAP_POSITIONS = 15


# packet id -> (name, dtype, size stated in the spec)
PACKETS: dict[int, tuple[str, np.dtype, int]] = {
    PacketId.MOTION: ("Motion", PACKET_MOTION, 1349),
    PacketId.SESSION: ("Session", PACKET_SESSION, 753),
    PacketId.LAP_DATA: ("LapData", PACKET_LAP_DATA, 1285),
    PacketId.EVENT: ("Event", PACKET_EVENT, 45),
    PacketId.PARTICIPANTS: ("Participants", PACKET_PARTICIPANTS, 1284),
    PacketId.CAR_SETUPS: ("CarSetups", PACKET_CAR_SETUPS, 1133),
    PacketId.CAR_TELEMETRY: ("CarTelemetry", PACKET_CAR_TELEMETRY, 1352),
    PacketId.CAR_STATUS: ("CarStatus", PACKET_CAR_STATUS, 1239),
    PacketId.FINAL_CLASSIFICATION: ("FinalClassification", PACKET_FINAL_CLASSIFICATION, 1042),
    PacketId.LOBBY_INFO: ("LobbyInfo", PACKET_LOBBY_INFO, 954),
    PacketId.CAR_DAMAGE: ("CarDamage", PACKET_CAR_DAMAGE, 1041),
    PacketId.SESSION_HISTORY: ("SessionHistory", PACKET_SESSION_HISTORY, 1460),
    PacketId.TYRE_SETS: ("TyreSets", PACKET_TYRE_SETS, 231),
    PacketId.MOTION_EX: ("MotionEx", PACKET_MOTION_EX, 273),
    PacketId.TIME_TRIAL: ("TimeTrial", PACKET_TIME_TRIAL, 101),
    PacketId.LAP_POSITIONS: ("LapPositions", PACKET_LAP_POSITIONS, 1131),
}

# Size assertions: fail at import if any layout disagrees with the spec.
assert HEADER.itemsize == HEADER_SIZE, f"Header is {HEADER.itemsize} bytes, spec says 29"
for _pid, (_name, _dtype, _size) in PACKETS.items():
    assert _dtype.itemsize == _size, (
        f"Packet {_pid} {_name}: dtype is {_dtype.itemsize} bytes, spec says {_size}"
    )
for _code, _dtype in EVENT_DETAIL_DTYPES.items():
    assert _dtype.itemsize <= EVENT_DETAILS_SIZE, f"Event {_code!r} details too large"

# --- Parsing -----------------------------------------------------------------------------------


class PacketSizeError(ValueError):
    """A known packet id arrived with a length that doesn't match the spec."""


def parse_header(raw: bytes) -> np.void:
    return np.frombuffer(raw, HEADER, count=1)[0]


def parse(raw: bytes, header: np.void | None = None) -> tuple[np.void, int, np.void | None]:
    """Parse one packet.

    Returns (header, packet_id, record). `record` is a zero-copy numpy view of the whole packet
    (header included), or None for packet ids this format doesn't know (passed through untouched).
    Raises PacketSizeError when a known packet has the wrong length.
    """
    if header is None:
        header = parse_header(raw)
    packet_id = int(header["m_packetId"])
    entry = PACKETS.get(packet_id)
    if entry is None:
        return header, packet_id, None
    name, dtype, size = entry
    if len(raw) != size:
        raise PacketSizeError(f"{name} packet is {len(raw)} bytes, expected {size}")
    return header, packet_id, np.frombuffer(raw, dtype, count=1)[0]


def decode_event(record: np.void) -> tuple[str, dict]:
    """Decode an Event packet's union. Returns (code, details) where details is {} for codes
    with no payload."""
    code_b = bytes(record["m_eventStringCode"])
    code = code_b.decode("ascii", errors="replace")
    dtype = EVENT_DETAIL_DTYPES.get(code_b)
    if dtype is None:
        return code, {}
    raw = record["m_eventDetails"].tobytes()[: dtype.itemsize]
    rec = np.frombuffer(raw, dtype, count=1)[0]
    return code, {name: rec[name].item() for name in dtype.names}


def decode_name(raw_name: bytes) -> str:
    """Participant names are UTF-8, null-terminated (numpy 'S' strips trailing nulls)."""
    return raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


# --- Lookup tables (from the spec appendix) ----------------------------------------------------

TRACKS = {
    0: "Melbourne", 2: "Shanghai", 3: "Sakhir (Bahrain)", 4: "Catalunya", 5: "Monaco",
    6: "Montreal", 7: "Silverstone", 9: "Hungaroring", 10: "Spa", 11: "Monza", 12: "Singapore",
    13: "Suzuka", 14: "Abu Dhabi", 15: "Texas", 16: "Brazil", 17: "Austria", 19: "Mexico",
    20: "Baku (Azerbaijan)", 26: "Zandvoort", 27: "Imola", 29: "Jeddah", 30: "Miami",
    31: "Las Vegas", 32: "Losail", 39: "Silverstone (Reverse)", 40: "Austria (Reverse)",
    41: "Zandvoort (Reverse)",
}

SESSION_TYPES = {
    0: "Unknown", 1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short Practice",
    5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3", 8: "Short Qualifying",
    9: "One-Shot Qualifying", 10: "Sprint Shootout 1", 11: "Sprint Shootout 2",
    12: "Sprint Shootout 3", 13: "Short Sprint Shootout", 14: "One-Shot Sprint Shootout",
    15: "Race", 16: "Race 2", 17: "Race 3", 18: "Time Trial",
}
RACE_SESSION_TYPES = {15, 16, 17}

# m_visualTyreCompound
VISUAL_COMPOUNDS = {
    16: "Soft", 17: "Medium", 18: "Hard", 7: "Inter", 8: "Wet",
    # F2 '20
    15: "Wet", 19: "Super Soft", 20: "Soft", 21: "Medium", 22: "Hard",
}
# m_actualTyreCompound
ACTUAL_COMPOUNDS = {
    16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6", 7: "Inter", 8: "Wet",
    9: "Classic Dry", 10: "Classic Wet",
    11: "F2 Super Soft", 12: "F2 Soft", 13: "F2 Medium", 14: "F2 Hard", 15: "F2 Wet",
}

RESULT_STATUS = {0: "Invalid", 1: "Inactive", 2: "Active", 3: "Finished", 4: "DNF", 5: "DSQ",
                 6: "Not classified", 7: "Retired"}
