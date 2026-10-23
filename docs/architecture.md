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
| `engineer/cli.py` | `python -m engineer run` and the lap-line printer shared by live and replay. |

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
| 2 | Feature functions and offline feature tables (Parquet/DuckDB). |
| 3 | Tyre degradation coaching and models. |
| 4 | Distance traces (5 m bins), track maps, corner-by-corner coaching vs nearby cars. |
| 5 | Strategy simulation (pit timing, compound) in worker processes. |
| 6 | Push-to-talk: faster-whisper, intents, query tools. |

(Phases 2–6 ordering is indicative; the kickoff prompt fixes only Phase 0 and Phase 1.)
