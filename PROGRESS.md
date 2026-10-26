# Progress

## Phase 0 — foundation

- [x] 1. Project setup (pyproject, .gitignore, config.yaml, CLAUDE.md, docs/architecture.md, spec saved)
- [x] 2. Versioned parser (`engineer/ingest/formats/f1_2025.py`): all 16 packet dtypes, sizes asserted
- [x] 3. UDP listener (arrival timestamps, fan-out, forwarding on a separate socket)
- [x] 4. Recorder (zstd raw streams per session UID) + SQLite session index
- [x] 5. Race state (session, 22-car arrays, player view, restricted data and inactive cars as NaN)
- [x] 6. Event bus + detection (SessionStarted/Ended, LapCompleted, SectorCompleted, PitEntry/Exit, Flashback)
- [x] 7. Replay runner (in-process at 1x / Nx / max, or `--udp`)
- [x] 8. CLI lap lines (`python -m engineer run`, `python -m engineer sessions`)
- [x] 9. Tests (63 passing, including the golden test) and benchmark
- [x] Golden test with a real recording (`tests/fixtures/austria_race_5laps.zst`)
- [x] Acceptance run in the real game on Windows

**Phase 0 accepted.**

## Real-game results (Austria, 5-lap race, 20 cars, Windows, 26 Oct 2026)

- `engineer run` on Windows: port bound, 114,689 packets, 0 rejected, clean Ctrl+C.
- Replay of the recording prints identical lap lines to the live run.
- Every lap of all 20 cars (5 each, no duplicates) matches the game's own Session History lap times.
  The player's 5 lap times, S1 and S2 match the game exactly; lap 5 matches the game's
  fastest-lap event (1:09.126).
- A flashback on lap 3 was handled: the lap was reported once, with the right time.
- Benchmark on the real recording: mean 31 us, p50 14 us, p99 139 us, max 3.0 ms per packet.

## Questions settled by the real recording

- **Lap Data field order**: spec order is CORRECT (not swapped). `m_gridPosition` is zero-based in
  practice (pole = 0), which likely explains the community "grid reads 0" report.
- **Final race lap**: the game does NOT advance `m_currentLapNum` at the chequered flag; the
  last-lap time changes on the same lap and result status becomes Finished a frame later. The
  detector now completes a lap on that signal. (Live run printed only 4 of 5 laps before this fix.)
- **Last-lap time timing**: updates on the same frame as the lap number, so lap lines are instant.
- **Restricted telemetry**: the player's own `m_yourTelemetry` can be 0 (restricted); the player car
  is still treated as visible, as the spec says.

## Still open

- **Sector 3 is 1-2 ms off the game's value.** We compute S3 = lap - S1 - S2 the moment the lap ends;
  the game rounds each sector separately (e.g. ours 21.660, game's 21.659). The game's exact S3
  arrives in Session History up to ~1.1 s later. Fine for radio calls; revisit if exact S3 matters.
- **Final Classification** was not in this recording (the session was stopped before the results
  screen), so packet 8 is untested on real data. Not needed in Phase 0.
- **Flashbacks** only reset lap/sector tracking; fuller rollback can wait until Phase 4 traces.

## Phase 1 notes

- Analyzers subscribe to `LapCompleted` on `pipeline.bus`; lap delta needs the previous valid,
  non-pit lap of the player, which the event already flags (`invalid`, `pit_in_lap`, `pit_out_lap`).
- TTS must run in a separate process (`engineer/workers/`), fed through a queue from the radio director.
