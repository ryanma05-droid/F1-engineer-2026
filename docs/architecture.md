# Architecture

## Goal

A Python app on Windows that listens to F1 25 UDP telemetry, keeps a live model of the race and
speaks to the driver like a race engineer: lap deltas, corner-by-corner coaching against nearby cars,
tyre degradation coaching, pit strategy simulation, and later push-to-talk questions.

## Design principles

1. **Record everything, replay anything.** Every raw UDP packet is saved with its arrival time
   (`time.time_ns()`). A recorded session replays through exactly the same code path as live play:
   the replay runner feeds the same `Pipeline.feed(raw, t_ns)` the listener does.
2. **One race state, many readers.** Only the parser + `RaceState.apply()` write state. Analyzers,
   the CLI and tools read `RaceState`; nothing else decodes packet bytes.
3. **One feature code path.** Feature functions (Phase 2+) take race state / traces and are the same
   functions used offline to build training tables.
4. **Announcing and answering share one API.** Analyses are exposed as query tools
   (`engineer/tools/queries.py`) used by both proactive radio calls and push-to-talk.
5. **Never block the hot path.** The asyncio loop only ingests, parses, updates state and dispatches
   events. Strategy simulation, TTS and speech recognition run in worker processes (later phases).

## Pipeline

```
                         ┌──────────► forward to SimHub etc. (unchanged bytes)
F1 25 ──UDP──► Listener ─┼──────────► Recorder ──► data/sessions/<uid>.zst + index.sqlite
               (t_ns)    │
                         └──► Pipeline.feed(raw, t_ns)
                                 │
                                 ▼
                         formats.dispatch()  (reads m_packetFormat → f1_2025 / f1_2026)
                                 │  numpy record view, zero-copy
                                 ▼
                           RaceState.apply()  ──► detectors ──► EventBus
                                                                 │
                         analyzers (Phase 1+) ◄──────────────────┘
                                 │
                           radio director ──► audio process (TTS)

Offline: session store ──► replay ──► feature jobs ──► model registry ──► analyzers load models
```

## Modules (Phase 0)

| Module | Role |
| --- | --- |
| `engineer/ingest/formats/f1_2025.py` | numpy structured dtypes for every 2025 packet, size-checked against the spec at import. `parse(raw)` returns `(header, packet_id, record)` where `record` is a zero-copy view. |
| `engineer/ingest/formats/__init__.py` | `dispatch(raw)`: reads the 2-byte `m_packetFormat` and routes to the matching module. Unknown formats raise `UnsupportedFormat`; unknown packet IDs return `record=None`. |
| `engineer/ingest/formats/f1_2026.py` | Stub; raises a helpful error. |
| `engineer/ingest/listener.py` | asyncio datagram endpoint; stamps arrival time; fans raw bytes to subscribers; optional forwarding. |
| `engineer/ingest/recorder.py` | One zstd stream per session UID: records of `u64 t_ns, u16 len, bytes`. SQLite index of sessions. `read_recording()` iterates a file. |
| `engineer/ingest/replay.py` | Replays a recording at real-time or max speed, in-process or re-sent over UDP. |
| `engineer/pipeline.py` | Glue: parse → state → events. The single entry point for live and replay. |
| `engineer/state/race_state.py` | Session info, per-car numpy arrays (22 cars), player extras. Restricted telemetry stored as NaN/None. |
| `engineer/state/traces.py` | Placeholder for 5 m distance-binned traces (Phase 4). |
| `engineer/bus/events.py` | In-process publish/subscribe; event dataclasses. |
| `engineer/state/detectors.py` | Turns state changes into events (session start/end, lap, sector, pit entry/exit). |
| `engineer/cli.py` | `python -m engineer run` / `sessions`. |
| `engineer/app.py` | `build_engine()`: wires pipeline, lap history, director, analyzers and printers; used by live and replay. |

## Modules (Phase 1)

| Module | Role |
| --- | --- |
| `engineer/features/laps.py` | `LapHistory`: completed laps per car from `LapCompleted`; marks clean laps. |
| `engineer/tools/queries.py` | `lap_delta()`: structured comparison with the previous clean lap. Shared by radio calls and (Phase 6) push-to-talk. |
| `engineer/radio/phrases.py` | Engineer-style wording ("one oh nine one", "two tenths quicker"); verbosity levels. |
| `engineer/radio/director.py` | `RadioDirector`: priority, expiry, cooldowns, quiet zones; publishes `RadioMessage`. Game-time only, so replay is deterministic. |
| `engineer/analyzers/lap_delta.py` | On the player's `LapCompleted`, submits the lap delta call. |
| `engineer/radio/tts.py` | Audio process (spawned): Piper + radio effect, pyttsx3 fallback, latency readout. |
| `engineer/radio/effects.py` | Squelch beeps, 300-3400 Hz band-limit, light compression and static. |
| `engineer/workers/pool.py` | `start_worker()`: spawn-based worker processes with in/out queues. |

Quiet zones: the director holds a call while the player is braking, steering or off throttle, and
checks a per-session map (50 m sections, learned from the player's previous laps, excluding race
lap 1, in-laps and out-laps) so a call doesn't start just before a braking zone.

## Modules (Phases 2 and 3)

| Module | Role |
| --- | --- |
| `engineer/features/lap_features.py` | `LapFeatureBuilder`: one `LapFeatures` record per car per lap (times, flags, compound, age, wear/damage/blisters, carcass and surface temps per sector, fuel, conditions). `TempAccumulator` collects temps during the lap. Stints, `fit_wear_rate()`, `summarize_stints()`. |
| `engineer/features/tables.py` | Offline: replays every recording through the same builder and writes `data/features/laps.parquet` and `stints.parquet`; DuckDB queries. CLI: `python -m engineer.features build / query / tyres`. |
| `engineer/features/priors.py` | `WearPriors`: your median wear rate per (track, compound) from the stint table; used while a stint is under 3 laps. |
| `engineer/tools/queries.py` | `tyre_status()` (wear, rate, laps left), `pace_trend_ms_per_lap()` (fuel corrected), `stint_summary()`. |
| `engineer/analyzers/tyres.py` | `TyreAnalyzer`: tyre status, wear warnings, temperatures, drop-off, damage/blisters radio calls. |

Stints: a new set is a lower tyre age or a different compound. An in-lap never starts a stint
(where the box is before the line it already ends on the new tyres); drive-throughs aren't stints.

Tyre temperatures are judged on the carcass (inner) temperature: the surface swings by ~100 C
within a corner on real data, the carcass moves smoothly. "Hot" or "cold" = outside the window
(`tyres.temp_window_c`, slicks only) for 60%+ of a sector.

## Modules (Phase 4)

| Module | Role |
| --- | --- |
| `engineer/state/traces.py` | `TraceRecorder`: every car's lap in 5 m sections (lap time at each section start, interpolated; speed, throttle, brake, steer, gear). Flashback-aware. `LapTrace`. |
| `engineer/state/track_maps.py` | `detect_corners()` from clean laps (speed dips of 15+ km/h; kinks ignored); `TrackMaps` loads/saves `data/tracks/<track>.json` (editable; Austria shipped with official turn numbers). |
| `engineer/tools/queries.py` | `corner_stats()`, `corner_deltas()`: corner time, braking point, minimum speed, full-throttle point, exit speed; `CornerDelta.reason()`. |
| `engineer/analyzers/corners.py` | `CornerAnalyzer`: one call per lap, biggest corner loss vs the car ahead/behind (within 2 s) or your best lap. |

A corner runs from where braking starts (20 m margin) to 100 m after you're back on full
throttle, capped at the next corner. Loss causes are checked against thresholds (braking 10 m
earlier, minimum speed 4 km/h down, full throttle 15 m later, exit 4 km/h down) and the biggest
relative to its threshold is named. Offline, `features build` also writes `traces.parquet` and
`corners.parquet`.

## Data conventions

- Wheel arrays are ordered **RL, RR, FL, FR** everywhere (as in the game).
- Times inside state are milliseconds (int) unless the name says `_s`.
- Unknown values are `NaN` (floats) or `None` (scalars), never zero. This covers fields restricted by
  another player's "Your Telemetry: Restricted" setting (spec "Restricted data" list) and cars whose
  `m_resultStatus` is 0 (invalid) or 1 (inactive).
- Flashbacks (`FLBK` event): state from frames after the flashback point is stale. Phase 0 marks the
  event, resets lap-tracking baselines for every car so no false LapCompleted fires, and publishes a
  `Flashback` event so later analyzers can discard data.

## Recording format

A `.zst` file is one zstd frame stream. Decompressed it is a sequence of records:

```
uint64 arrival_time_ns  (little-endian)
uint16 length
bytes  raw packet (length bytes)
```

Files are named `<UTC start time>_<sessionUID hex>.zst`. `data/sessions/index.sqlite` has a
`sessions` table: `session_uid, start_time_ns, track_id, session_type, file_path, packets, bytes`.
Packets that arrive with session UID 0 (menus) are not recorded.

## Phase plan

| Phase | Scope |
| --- | --- |
| 0 | Foundation: parser, listener, recorder, race state, events, replay, CLI, tests, benchmark. |
| 1 | Radio director (priority, expiry, quiet zones, cooldowns, verbosity), Piper TTS audio process, lap delta analyzer. |
| 2 | Lap feature records, stints, offline feature tables (Parquet/DuckDB). (built) |
| 3 | Tyre coaching: wear status, laps left, warnings, temperatures, drop-off. (built) |
| 4 | Distance traces (5 m bins), track maps, corner-by-corner coaching vs nearby cars. (built) |
| 5 | Strategy simulation (pit timing, compound) in worker processes. |
| 6 | Push-to-talk: faster-whisper, intents, query tools. |

(Phases 2–6 ordering is indicative; the kickoff prompt fixes only Phase 0 and Phase 1.)
