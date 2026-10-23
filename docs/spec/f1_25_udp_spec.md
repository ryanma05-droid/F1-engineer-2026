<!-- Copied verbatim from Appendix A of the project kickoff prompt. Source: EA "Data Output from F1 25 Game" v3 (Data_Output_from_F1_25_v3.pdf in this folder). Parser field layouts must come ONLY from this file / the PDF. -->

# F1 25 UDP specification (packet format 2025)

Source: EA "Data Output from F1 25 Game". All values little-endian. All structures packed, no
padding. Maximum 22 cars in every array (check `m_numActiveCars` and each car's `m_resultStatus`).
Wheel arrays are always ordered 0 = Rear Left, 1 = Rear Right, 2 = Front Left, 3 = Front Right.
Vehicle indices never change during a session. The game also supports UDP formats 2024 and 2023;
we only implement 2025.

Types: uint8, int8, uint16, int16, uint32, float (32-bit), double (64-bit), uint64, char.

### Header (29 bytes, on every packet)

```c
struct PacketHeader {
    uint16 m_packetFormat;            // 2025
    uint8  m_gameYear;                // last two digits, e.g. 25
    uint8  m_gameMajorVersion;        // "X.00"
    uint8  m_gameMinorVersion;        // "1.XX"
    uint8  m_packetVersion;           // version of this packet type, starts at 1
    uint8  m_packetId;                // see table below
    uint64 m_sessionUID;              // unique identifier for the session
    float  m_sessionTime;             // session timestamp
    uint32 m_frameIdentifier;         // frame the data was retrieved on
    uint32 m_overallFrameIdentifier;  // doesn't go back after flashbacks
    uint8  m_playerCarIndex;          // index of player's car in the array
    uint8  m_secondaryPlayerCarIndex; // splitscreen, 255 if none
};
```

### Packet IDs, sizes and rates

| ID | Packet | Size (bytes) | Rate |
| --- | --- | --- | --- |
| 0 | Motion | 1349 | menu rate; only while player is in control |
| 1 | Session | 753 | 2 per second |
| 2 | Lap Data | 1285 | menu rate |
| 3 | Event | 45 | when the event occurs |
| 4 | Participants | 1284 | every 5 seconds |
| 5 | Car Setups | 1133 | 2 per second |
| 6 | Car Telemetry | 1352 | menu rate |
| 7 | Car Status | 1239 | menu rate |
| 8 | Final Classification | 1042 | once at end of race |
| 9 | Lobby Info | 954 | 2 per second in lobby |
| 10 | Car Damage | 1041 | 10 per second |
| 11 | Session History | 1460 | 20 per second, cycling through cars |
| 12 | Tyre Sets | 231 | 20 per second, cycling through cars |
| 13 | Motion Ex | 273 | menu rate |
| 14 | Time Trial | 101 | 1 per second, Time Trial mode only |
| 15 | Lap Positions | 1131 | 1 per second |

Packets sent at "menu rate" on a frame always arrive together on that frame. The first frame
(session time 0.000) carries Session, Participants, Car Setups, Lap Data, Motion, Car Telemetry,
Car Status, Car Damage and Motion Ex to initialise state.

### 0 Motion

```c
struct CarMotionData {
    float m_worldPositionX;       // metres
    float m_worldPositionY;
    float m_worldPositionZ;
    float m_worldVelocityX;       // metres/s
    float m_worldVelocityY;
    float m_worldVelocityZ;
    int16 m_worldForwardDirX;     // normalised: divide by 32767.0
    int16 m_worldForwardDirY;
    int16 m_worldForwardDirZ;
    int16 m_worldRightDirX;
    int16 m_worldRightDirY;
    int16 m_worldRightDirZ;
    float m_gForceLateral;
    float m_gForceLongitudinal;
    float m_gForceVertical;
    float m_yaw;                  // radians
    float m_pitch;
    float m_roll;
};
struct PacketMotionData {
    PacketHeader  m_header;
    CarMotionData m_carMotionData[22];
};
```

### 1 Session

```c
struct MarshalZone {
    float m_zoneStart;  // fraction (0..1) of the lap where the zone starts
    int8  m_zoneFlag;   // -1 invalid/unknown, 0 none, 1 green, 2 blue, 3 yellow
};
struct WeatherForecastSample {
    uint8 m_sessionType;            // see Session types
    uint8 m_timeOffset;             // minutes ahead
    uint8 m_weather;                // 0 clear, 1 light cloud, 2 overcast, 3 light rain, 4 heavy rain, 5 storm
    int8  m_trackTemperature;       // Celsius
    int8  m_trackTemperatureChange; // 0 up, 1 down, 2 no change
    int8  m_airTemperature;         // Celsius
    int8  m_airTemperatureChange;   // 0 up, 1 down, 2 no change
    uint8 m_rainPercentage;         // 0-100
};
struct PacketSessionData {
    PacketHeader m_header;
    uint8  m_weather;                 // as above
    int8   m_trackTemperature;
    int8   m_airTemperature;
    uint8  m_totalLaps;
    uint16 m_trackLength;             // metres
    uint8  m_sessionType;             // see Session types
    int8   m_trackId;                 // -1 unknown, see Track IDs
    uint8  m_formula;                 // 0 F1 Modern, 1 F1 Classic, 2 F2, 3 F1 Generic, 4 Beta, 6 Esports, 8 F1 World, 9 F1 Elimination
    uint16 m_sessionTimeLeft;         // seconds
    uint16 m_sessionDuration;         // seconds
    uint8  m_pitSpeedLimit;           // km/h
    uint8  m_gamePaused;              // network game only
    uint8  m_isSpectating;
    uint8  m_spectatorCarIndex;
    uint8  m_sliProNativeSupport;
    uint8  m_numMarshalZones;
    MarshalZone m_marshalZones[21];
    uint8  m_safetyCarStatus;         // 0 none, 1 full, 2 virtual, 3 formation lap
    uint8  m_networkGame;             // 0 offline, 1 online
    uint8  m_numWeatherForecastSamples;
    WeatherForecastSample m_weatherForecastSamples[64];
    uint8  m_forecastAccuracy;        // 0 perfect, 1 approximate
    uint8  m_aiDifficulty;            // 0-110
    uint32 m_seasonLinkIdentifier;
    uint32 m_weekendLinkIdentifier;
    uint32 m_sessionLinkIdentifier;
    uint8  m_pitStopWindowIdealLap;   // player
    uint8  m_pitStopWindowLatestLap;  // player
    uint8  m_pitStopRejoinPosition;   // player
    uint8  m_steeringAssist;          // 0 off, 1 on
    uint8  m_brakingAssist;           // 0 off, 1 low, 2 medium, 3 high
    uint8  m_gearboxAssist;           // 1 manual, 2 manual + suggested, 3 auto
    uint8  m_pitAssist;
    uint8  m_pitReleaseAssist;
    uint8  m_ERSAssist;
    uint8  m_DRSAssist;
    uint8  m_dynamicRacingLine;       // 0 off, 1 corners only, 2 full
    uint8  m_dynamicRacingLineType;   // 0 2D, 1 3D
    uint8  m_gameMode;                // see Game mode IDs
    uint8  m_ruleSet;                 // see Ruleset IDs
    uint32 m_timeOfDay;               // minutes since midnight
    uint8  m_sessionLength;           // 0 none, 2 very short, 3 short, 4 medium, 5 medium long, 6 long, 7 full
    uint8  m_speedUnitsLeadPlayer;    // 0 MPH, 1 KPH
    uint8  m_temperatureUnitsLeadPlayer;      // 0 Celsius, 1 Fahrenheit
    uint8  m_speedUnitsSecondaryPlayer;
    uint8  m_temperatureUnitsSecondaryPlayer;
    uint8  m_numSafetyCarPeriods;
    uint8  m_numVirtualSafetyCarPeriods;
    uint8  m_numRedFlagPeriods;
    uint8  m_equalCarPerformance;     // 0 off, 1 on
    uint8  m_recoveryMode;            // 0 none, 1 flashbacks, 2 auto-recovery
    uint8  m_flashbackLimit;          // 0 low, 1 medium, 2 high, 3 unlimited
    uint8  m_surfaceType;             // 0 simplified, 1 realistic
    uint8  m_lowFuelMode;             // 0 easy, 1 hard
    uint8  m_raceStarts;              // 0 manual, 1 assisted
    uint8  m_tyreTemperature;         // 0 surface only, 1 surface + carcass
    uint8  m_pitLaneTyreSim;          // 0 on, 1 off
    uint8  m_carDamage;               // 0 off, 1 reduced, 2 standard, 3 simulation
    uint8  m_carDamageRate;           // 0 reduced, 1 standard, 2 simulation
    uint8  m_collisions;              // 0 off, 1 player-to-player off, 2 on
    uint8  m_collisionsOffForFirstLapOnly;
    uint8  m_mpUnsafePitRelease;      // 0 on, 1 off
    uint8  m_mpOffForGriefing;
    uint8  m_cornerCuttingStringency; // 0 regular, 1 strict
    uint8  m_parcFermeRules;
    uint8  m_pitStopExperience;       // 0 automatic, 1 broadcast, 2 immersive
    uint8  m_safetyCar;               // 0 off, 1 reduced, 2 standard, 3 increased
    uint8  m_safetyCarExperience;     // 0 broadcast, 1 immersive
    uint8  m_formationLap;
    uint8  m_formationLapExperience;
    uint8  m_redFlags;                // 0 off, 1 reduced, 2 standard, 3 increased
    uint8  m_affectsLicenceLevelSolo;
    uint8  m_affectsLicenceLevelMP;
    uint8  m_numSessionsInWeekend;
    uint8  m_weekendStructure[12];    // session types
    float  m_sector2LapDistanceStart; // metres
    float  m_sector3LapDistanceStart; // metres
};
```

### 2 Lap Data

```c
struct LapData {
    uint32 m_lastLapTimeInMS;
    uint32 m_currentLapTimeInMS;
    uint16 m_sector1TimeMSPart;
    uint8  m_sector1TimeMinutesPart;
    uint16 m_sector2TimeMSPart;
    uint8  m_sector2TimeMinutesPart;
    uint16 m_deltaToCarInFrontMSPart;
    uint8  m_deltaToCarInFrontMinutesPart;
    uint16 m_deltaToRaceLeaderMSPart;
    uint8  m_deltaToRaceLeaderMinutesPart;
    float  m_lapDistance;             // metres; negative before crossing the line
    float  m_totalDistance;           // metres; negative before crossing the line
    float  m_safetyCarDelta;          // seconds
    uint8  m_carPosition;
    uint8  m_currentLapNum;
    uint8  m_pitStatus;               // 0 none, 1 pitting, 2 in pit area
    uint8  m_numPitStops;
    uint8  m_sector;                  // 0, 1, 2
    uint8  m_currentLapInvalid;       // 0 valid, 1 invalid
    uint8  m_penalties;               // seconds to be added
    uint8  m_totalWarnings;
    uint8  m_cornerCuttingWarnings;
    uint8  m_numUnservedDriveThroughPens;
    uint8  m_numUnservedStopGoPens;
    uint8  m_gridPosition;            // see field-order note in step 2
    uint8  m_driverStatus;            // 0 in garage, 1 flying lap, 2 in lap, 3 out lap, 4 on track
    uint8  m_resultStatus;            // 0 invalid, 1 inactive, 2 active, 3 finished, 4 DNF, 5 DSQ, 6 not classified, 7 retired
    uint8  m_pitLaneTimerActive;
    uint16 m_pitLaneTimeInLaneInMS;
    uint16 m_pitStopTimerInMS;
    uint8  m_pitStopShouldServePen;
    float  m_speedTrapFastestSpeed;   // km/h
    uint8  m_speedTrapFastestLap;     // 255 = not set
};
struct PacketLapData {
    PacketHeader m_header;
    LapData m_lapData[22];
    uint8   m_timeTrialPBCarIdx;      // 255 if invalid
    uint8   m_timeTrialRivalCarIdx;   // 255 if invalid
};
```

### 3 Event

The details are a union: interpret only the struct matching the event code.

```c
union EventDataDetails {
    struct { uint8 vehicleIdx; float lapTime; } FastestLap;          // lapTime in seconds
    struct { uint8 vehicleIdx; uint8 reason; } Retirement;           // 0 invalid, 1 retired, 2 finished, 3 terminal damage, 4 inactive, 5 not enough laps, 6 black flagged, 7 red flagged, 8 mechanical failure, 9 session skipped, 10 session simulated
    struct { uint8 reason; } DRSDisabled;                             // 0 wet track, 1 safety car, 2 red flag, 3 min lap not reached
    struct { uint8 vehicleIdx; } TeamMateInPits;
    struct { uint8 vehicleIdx; } RaceWinner;
    struct { uint8 penaltyType; uint8 infringementType; uint8 vehicleIdx; uint8 otherVehicleIdx;
             uint8 time; uint8 lapNum; uint8 placesGained; } Penalty;
    struct { uint8 vehicleIdx; float speed; uint8 isOverallFastestInSession;
             uint8 isDriverFastestInSession; uint8 fastestVehicleIdxInSession;
             float fastestSpeedInSession; } SpeedTrap;                // km/h
    struct { uint8 numLights; } StartLights;
    struct { uint8 vehicleIdx; } DriveThroughPenaltyServed;
    struct { uint8 vehicleIdx; float stopTime; } StopGoPenaltyServed; // seconds
    struct { uint32 flashbackFrameIdentifier; float flashbackSessionTime; } Flashback;
    struct { uint32 buttonStatus; } Buttons;                          // bit flags, see Button flags
    struct { uint8 overtakingVehicleIdx; uint8 beingOvertakenVehicleIdx; } Overtake;
    struct { uint8 safetyCarType; uint8 eventType; } SafetyCar;       // type: 0 none, 1 full, 2 virtual, 3 formation lap; event: 0 deployed, 1 returning, 2 returned, 3 resume race
    struct { uint8 vehicle1Idx; uint8 vehicle2Idx; } Collision;
};
struct PacketEventData {
    PacketHeader     m_header;
    uint8            m_eventStringCode[4];
    EventDataDetails m_eventDetails;
};
```

Event codes: SSTA session started, SEND session ended, FTLP fastest lap, RTMT retirement,
DRSE DRS enabled, DRSD DRS disabled, TMPT team mate in pits, CHQF chequered flag, RCWN race winner,
PENA penalty issued, SPTP speed trap, STLG start lights, LGOT lights out, DTSV drive through served,
SGSV stop go served, FLBK flashback, BUTN button status, RDFL red flag, OVTK overtake,
SCAR safety car, COLL collision.

Flashbacks matter: on FLBK, state built from later frames must be rolled back or marked stale.

### 4 Participants

```c
struct LiveryColour { uint8 red; uint8 green; uint8 blue; };
struct ParticipantData {
    uint8  m_aiControlled;      // 1 AI, 0 human
    uint8  m_driverId;          // 255 if network human
    uint8  m_networkId;
    uint8  m_teamId;
    uint8  m_myTeam;            // 1 = My Team
    uint8  m_raceNumber;
    uint8  m_nationality;
    char   m_name[32];          // UTF-8, null terminated, may be truncated with U+2026
    uint8  m_yourTelemetry;     // 0 restricted, 1 public
    uint8  m_showOnlineNames;   // 0 off, 1 on
    uint16 m_techLevel;         // F1 World tech level
    uint8  m_platform;          // 1 Steam, 3 PlayStation, 4 Xbox, 6 Origin, 255 unknown
    uint8  m_numColours;
    LiveryColour m_liveryColours[4];
};
struct PacketParticipantsData {
    PacketHeader    m_header;
    uint8           m_numActiveCars;
    ParticipantData m_participants[22];
};
```

### 5 Car Setups (not needed in Phase 0; other players' setups are always blank online)

```c
struct CarSetupData {
    uint8 m_frontWing; uint8 m_rearWing;
    uint8 m_onThrottle; uint8 m_offThrottle;            // differential %
    float m_frontCamber; float m_rearCamber;
    float m_frontToe; float m_rearToe;
    uint8 m_frontSuspension; uint8 m_rearSuspension;
    uint8 m_frontAntiRollBar; uint8 m_rearAntiRollBar;
    uint8 m_frontSuspensionHeight; uint8 m_rearSuspensionHeight;
    uint8 m_brakePressure; uint8 m_brakeBias; uint8 m_engineBraking;  // %
    float m_rearLeftTyrePressure; float m_rearRightTyrePressure;      // PSI
    float m_frontLeftTyrePressure; float m_frontRightTyrePressure;
    uint8 m_ballast;
    float m_fuelLoad;
};
struct PacketCarSetupData {
    PacketHeader m_header;
    CarSetupData m_carSetups[22];
    float        m_nextFrontWingValue;  // after next pit stop, player only
};
```

### 6 Car Telemetry

```c
struct CarTelemetryData {
    uint16 m_speed;                       // km/h
    float  m_throttle;                    // 0.0-1.0
    float  m_steer;                       // -1.0 full left to 1.0 full right
    float  m_brake;                       // 0.0-1.0
    uint8  m_clutch;                      // 0-100
    int8   m_gear;                        // 1-8, N=0, R=-1
    uint16 m_engineRPM;
    uint8  m_drs;                         // 0 off, 1 on
    uint8  m_revLightsPercent;
    uint16 m_revLightsBitValue;
    uint16 m_brakesTemperature[4];        // Celsius
    uint8  m_tyresSurfaceTemperature[4];  // Celsius
    uint8  m_tyresInnerTemperature[4];    // Celsius
    uint16 m_engineTemperature;           // Celsius
    float  m_tyresPressure[4];            // PSI
    uint8  m_surfaceType[4];              // see Surface types
};
struct PacketCarTelemetryData {
    PacketHeader     m_header;
    CarTelemetryData m_carTelemetryData[22];
    uint8 m_mfdPanelIndex;                // 255 closed; race: 0 setup, 1 pits, 2 damage, 3 engine, 4 temperatures
    uint8 m_mfdPanelIndexSecondaryPlayer;
    int8  m_suggestedGear;                // 1-8, 0 if none
};
```

### 7 Car Status

```c
struct CarStatusData {
    uint8  m_tractionControl;        // 0 off, 1 medium, 2 full
    uint8  m_antiLockBrakes;         // 0 off, 1 on
    uint8  m_fuelMix;                // 0 lean, 1 standard, 2 rich, 3 max
    uint8  m_frontBrakeBias;         // %
    uint8  m_pitLimiterStatus;       // 0 off, 1 on
    float  m_fuelInTank;             // fuel mass
    float  m_fuelCapacity;
    float  m_fuelRemainingLaps;      // value on MFD
    uint16 m_maxRPM;
    uint16 m_idleRPM;
    uint8  m_maxGears;
    uint8  m_drsAllowed;             // 0 not allowed, 1 allowed
    uint16 m_drsActivationDistance;  // 0 not available, else metres until available
    uint8  m_actualTyreCompound;     // F1 Modern: 16 C5, 17 C4, 18 C3, 19 C2, 20 C1, 21 C0, 22 C6, 7 inter, 8 wet; F1 Classic: 9 dry, 10 wet; F2: 11 super soft, 12 soft, 13 medium, 14 hard, 15 wet
    uint8  m_visualTyreCompound;     // F1: 16 soft, 17 medium, 18 hard, 7 inter, 8 wet; F2 '20: 15 wet, 19 super soft, 20 soft, 21 medium, 22 hard
    uint8  m_tyresAgeLaps;
    int8   m_vehicleFiaFlags;        // -1 unknown, 0 none, 1 green, 2 blue, 3 yellow
    float  m_enginePowerICE;         // W
    float  m_enginePowerMGUK;        // W
    float  m_ersStoreEnergy;         // J
    uint8  m_ersDeployMode;          // 0 none, 1 medium, 2 hotlap, 3 overtake
    float  m_ersHarvestedThisLapMGUK;
    float  m_ersHarvestedThisLapMGUH;
    float  m_ersDeployedThisLap;
    uint8  m_networkPaused;
};
struct PacketCarStatusData {
    PacketHeader  m_header;
    CarStatusData m_carStatusData[22];
};
```

### 8 Final Classification

```c
struct FinalClassificationData {
    uint8  m_position;
    uint8  m_numLaps;
    uint8  m_gridPosition;
    uint8  m_points;
    uint8  m_numPitStops;
    uint8  m_resultStatus;           // as Lap Data
    uint8  m_resultReason;           // as Retirement reason
    uint32 m_bestLapTimeInMS;
    double m_totalRaceTime;          // seconds, without penalties
    uint8  m_penaltiesTime;          // seconds
    uint8  m_numPenalties;
    uint8  m_numTyreStints;
    uint8  m_tyreStintsActual[8];
    uint8  m_tyreStintsVisual[8];
    uint8  m_tyreStintsEndLaps[8];
};
struct PacketFinalClassificationData {
    PacketHeader m_header;
    uint8 m_numCars;
    FinalClassificationData m_classificationData[22];
};
```

### 9 Lobby Info (not needed)

```c
struct LobbyInfoData {
    uint8  m_aiControlled; uint8 m_teamId; uint8 m_nationality; uint8 m_platform;
    char   m_name[32];
    uint8  m_carNumber; uint8 m_yourTelemetry; uint8 m_showOnlineNames;
    uint16 m_techLevel;
    uint8  m_readyStatus;            // 0 not ready, 1 ready, 2 spectating
};
struct PacketLobbyInfoData {
    PacketHeader  m_header;
    uint8         m_numPlayers;
    LobbyInfoData m_lobbyPlayers[22];
};
```

### 10 Car Damage

```c
struct CarDamageData {
    float m_tyresWear[4];            // %
    uint8 m_tyresDamage[4];          // %
    uint8 m_brakesDamage[4];         // %
    uint8 m_tyreBlisters[4];         // % (new in F1 25)
    uint8 m_frontLeftWingDamage;
    uint8 m_frontRightWingDamage;
    uint8 m_rearWingDamage;
    uint8 m_floorDamage;
    uint8 m_diffuserDamage;
    uint8 m_sidepodDamage;
    uint8 m_drsFault;                // 0 OK, 1 fault
    uint8 m_ersFault;
    uint8 m_gearBoxDamage;
    uint8 m_engineDamage;
    uint8 m_engineMGUHWear;
    uint8 m_engineESWear;
    uint8 m_engineCEWear;
    uint8 m_engineICEWear;
    uint8 m_engineMGUKWear;
    uint8 m_engineTCWear;
    uint8 m_engineBlown;             // 0 OK, 1 fault
    uint8 m_engineSeized;
};
struct PacketCarDamageData {
    PacketHeader  m_header;
    CarDamageData m_carDamageData[22];
};
```

### 11 Session History (one car per packet, cycling)

```c
struct LapHistoryData {
    uint32 m_lapTimeInMS;
    uint16 m_sector1TimeMSPart;
    uint8  m_sector1TimeMinutesPart;
    uint16 m_sector2TimeMSPart;
    uint8  m_sector2TimeMinutesPart;
    uint16 m_sector3TimeMSPart;
    uint8  m_sector3TimeMinutesPart;
    uint8  m_lapValidBitFlags;       // 0x01 lap, 0x02 S1, 0x04 S2, 0x08 S3 valid
};
struct TyreStintHistoryData {
    uint8 m_endLap;                  // 255 = current tyre
    uint8 m_tyreActualCompound;
    uint8 m_tyreVisualCompound;
};
struct PacketSessionHistoryData {
    PacketHeader m_header;
    uint8 m_carIdx;
    uint8 m_numLaps;                 // includes current partial lap
    uint8 m_numTyreStints;
    uint8 m_bestLapTimeLapNum;
    uint8 m_bestSector1LapNum;
    uint8 m_bestSector2LapNum;
    uint8 m_bestSector3LapNum;
    LapHistoryData       m_lapHistoryData[100];
    TyreStintHistoryData m_tyreStintsHistoryData[8];
};
```

After Final Classification, a bulk update of all cars' session histories is sent.

### 12 Tyre Sets (one car per packet, cycling)

```c
struct TyreSetData {
    uint8 m_actualTyreCompound;
    uint8 m_visualTyreCompound;
    uint8 m_wear;                    // %
    uint8 m_available;
    uint8 m_recommendedSession;      // session type
    uint8 m_lifeSpan;                // laps left in this set
    uint8 m_usableLife;              // max recommended laps for this compound
    int16 m_lapDeltaTime;            // ms compared to fitted set
    uint8 m_fitted;
};
struct PacketTyreSetsData {
    PacketHeader m_header;
    uint8        m_carIdx;
    TyreSetData  m_tyreSetData[20];  // 13 dry + 7 wet
    uint8        m_fittedIdx;
};
```

### 13 Motion Ex (player car only)

```c
struct PacketMotionExData {
    PacketHeader m_header;
    float m_suspensionPosition[4];      // all wheel arrays RL, RR, FL, FR
    float m_suspensionVelocity[4];
    float m_suspensionAcceleration[4];
    float m_wheelSpeed[4];
    float m_wheelSlipRatio[4];
    float m_wheelSlipAngle[4];
    float m_wheelLatForce[4];
    float m_wheelLongForce[4];
    float m_heightOfCOGAboveGround;
    float m_localVelocityX;             // metres/s
    float m_localVelocityY;
    float m_localVelocityZ;
    float m_angularVelocityX;           // radians/s
    float m_angularVelocityY;
    float m_angularVelocityZ;
    float m_angularAccelerationX;       // radians/s/s
    float m_angularAccelerationY;
    float m_angularAccelerationZ;
    float m_frontWheelsAngle;           // radians
    float m_wheelVertForce[4];
    float m_frontAeroHeight;
    float m_rearAeroHeight;
    float m_frontRollAngle;
    float m_rearRollAngle;
    float m_chassisYaw;                 // radians, relative to direction of motion
    float m_chassisPitch;               // radians, relative to direction of motion
    float m_wheelCamber[4];             // radians
    float m_wheelCamberGain[4];         // radians
};
```

### 14 Time Trial (Time Trial mode only)

```c
struct TimeTrialDataSet {
    uint8  m_carIdx;
    uint8  m_teamId;
    uint32 m_lapTimeInMS;
    uint32 m_sector1TimeInMS;
    uint32 m_sector2TimeInMS;
    uint32 m_sector3TimeInMS;
    uint8  m_tractionControl;        // 0 off, 1 on
    uint8  m_gearboxAssist;
    uint8  m_antiLockBrakes;
    uint8  m_equalCarPerformance;    // 0 realistic, 1 equal
    uint8  m_customSetup;
    uint8  m_valid;
};
struct PacketTimeTrialData {
    PacketHeader     m_header;
    TimeTrialDataSet m_playerSessionBestDataSet;
    TimeTrialDataSet m_personalBestDataSet;
    TimeTrialDataSet m_rivalDataSet;
};
```

### 15 Lap Positions

At most 50 laps per packet; races over 50 laps send two packets with different `m_lapStart`.

```c
struct PacketLapPositionsData {
    PacketHeader m_header;
    uint8 m_numLaps;
    uint8 m_lapStart;                          // 0-indexed lap where data starts
    uint8 m_positionForVehicleIdx[50][22];     // position at start of each lap, 0 = no record
};
```

### Restricted data ("Your Telemetry" setting)

For another player with Your Telemetry set to restricted, these are sent as zero (your own car is
always visible):
- Car Status: fuelInTank, fuelCapacity, fuelMix, fuelRemainingLaps, frontBrakeBias,
  ersDeployMode, ersStoreEnergy, ersDeployedThisLap, ersHarvestedThisLapMGUK,
  ersHarvestedThisLapMGUH, enginePowerICE, enginePowerMGUK.
- Car Damage: all wing, floor, diffuser and sidepod damage, engineDamage, gearBoxDamage,
  tyresWear, tyresDamage, brakesDamage (all four wheels), drsFault, and all engine wear fields.
- Tyre Sets: all data.

### Appendix tables we need

Track IDs: 0 Melbourne, 2 Shanghai, 3 Sakhir (Bahrain), 4 Catalunya, 5 Monaco, 6 Montreal,
7 Silverstone, 9 Hungaroring, 10 Spa, 11 Monza, 12 Singapore, 13 Suzuka, 14 Abu Dhabi, 15 Texas,
16 Brazil, 17 Austria, 19 Mexico, 20 Baku (Azerbaijan), 26 Zandvoort, 27 Imola, 29 Jeddah,
30 Miami, 31 Las Vegas, 32 Losail, 39 Silverstone (Reverse), 40 Austria (Reverse),
41 Zandvoort (Reverse).

Session types: 0 Unknown, 1 Practice 1, 2 Practice 2, 3 Practice 3, 4 Short Practice,
5 Qualifying 1, 6 Qualifying 2, 7 Qualifying 3, 8 Short Qualifying, 9 One-Shot Qualifying,
10 Sprint Shootout 1, 11 Sprint Shootout 2, 12 Sprint Shootout 3, 13 Short Sprint Shootout,
14 One-Shot Sprint Shootout, 15 Race, 16 Race 2, 17 Race 3, 18 Time Trial.

Game modes: 4 Grand Prix '23, 5 Time Trial, 6 Splitscreen, 7 Online Custom, 15 Online Weekly
Event, 17 Story Mode (Braking Point), 27 My Team Career '25, 28 Driver Career '25,
29 Career '25 Online, 30 Challenge Career '25, 75 Story Mode (APXGP), 127 Benchmark.

Rulesets: 0 Practice & Qualifying, 1 Race, 2 Time Trial, 12 Elimination.

Surface types: 0 Tarmac, 1 Rumble strip, 2 Concrete, 3 Rock, 4 Gravel, 5 Mud, 6 Sand, 7 Grass,
8 Water, 9 Cobblestone, 10 Metal, 11 Ridged.

Button flags (Event BUTN, `buttonStatus`): 0x00000001 Cross/A, 0x00000002 Triangle/Y,
0x00000004 Circle/B, 0x00000008 Square/X, 0x00000010 D-pad Left, 0x00000020 D-pad Right,
0x00000040 D-pad Up, 0x00000080 D-pad Down, 0x00000100 Options/Menu, 0x00000200 L1/LB,
0x00000400 R1/RB, 0x00000800 L2/LT, 0x00001000 R2/RT, 0x00002000 Left Stick Click,
0x00004000 Right Stick Click, 0x00008000 Right Stick Left, 0x00010000 Right Stick Right,
0x00020000 Right Stick Up, 0x00040000 Right Stick Down, 0x00080000 Special,
0x00100000 to 0x80000000 UDP Action 1 to 12 (one bit each, in order). UDP Action buttons can be
mapped to wheel buttons in the game and will be used for push-to-talk in Phase 6.

Team, driver, nationality, penalty and infringement ID tables are in the PDF in `docs/spec/`;
they are not needed for Phase 0. Driver names come from the Participants packet.

### Game configuration

UDP is set in Settings > Telemetry Settings, or in
`Documents\My Games\<game folder>\hardwaresettings\hardware_settings_config.xml`:
`<udp enabled="false" broadcast="false" ip="127.0.0.1" port="20777" sendRate="20" format="2025"
yourTelemetry="restricted" onlineNames="off" />`. The default send rate is 20; we want 60.
Changes made in the game while it runs overwrite manual edits to the file.
