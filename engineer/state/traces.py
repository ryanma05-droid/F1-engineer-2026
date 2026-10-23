"""Distance traces (Phase 4 placeholder).

Plan: for each car and lap, sample telemetry (speed, throttle, brake, gear, steer, time) into 5 m
bins along the lap (`m_lapDistance`), so laps and cars can be compared corner by corner. Bins are
filled by a reader of RaceState after each Car Telemetry / Lap Data update, never by the parser.
Nothing here is used in Phase 0.
"""

BIN_SIZE_M = 5.0
