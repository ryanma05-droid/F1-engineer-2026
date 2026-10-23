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
- [x] 9. Tests (61 passing) and benchmark
- [ ] Golden test with a real recording (harness ready in `tests/test_golden.py`, needs a fixture)
- [ ] Acceptance run in the real game on Windows

## Verified so far (synthetic data, Linux)

- Every dtype size matches the spec; round-trip tests catch any one-byte field shift.
- End-to-end over real UDP: `engineer run` received 32,888 packets sent at 10x speed with no loss,
  printed correct lap lines, recorded the session, shut down cleanly on Ctrl+C; replaying the
  recording printed identical lines.
- Benchmark (`python -m engineer.bench --synthetic`, 20 cars, ~91k packets): mean 32 us, p50 16 us,
  p99 147 us, max 1.8 ms per packet. Target (mean < 1 ms) met. Lap Data is the most expensive packet
  (~93 us) because of event detection.

## Open questions / to confirm with a real recording

- **Lap Data field order** (`m_gridPosition` vs `m_driverStatus`): spec order implemented. In a race
  with 6+ cars, the log says either "spec order confirmed" or a warning that they look swapped.
- **Last-lap time timing**: the detector waits for `m_lastLapTimeInMS` to change after the lap number
  rolls over (up to 2 s). Confirm on real data that lap lines appear within a frame or two of the line.
- **Sector 3** is computed as lap - S1 - S2. Compare with Session History's S3 on a real recording.
- **Flashbacks**: Phase 0 drops lap/sector baselines on FLBK so rewound laps aren't reported. It does
  not roll back other state; values refresh within one packet cycle. Revisit for Phase 4 traces.
- **Windows**: developed and tested on Linux. First Windows run should confirm the UDP port binds
  and Ctrl+C stops cleanly.
- GitHub remote: the cloud session could not create the repo; it needs to be created by the owner.

## Phase 1 notes

- Analyzers subscribe to `LapCompleted` on `pipeline.bus`; lap delta needs the previous valid,
  non-pit lap of the player, which the event already flags (`invalid`, `pit_in_lap`, `pit_out_lap`).
- TTS must run in a separate process (`engineer/workers/`), fed through a queue from the radio director.
