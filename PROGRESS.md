# Progress

## Phase 0 — foundation

- [x] 1. Project setup (pyproject, .gitignore, config.yaml, CLAUDE.md, docs/architecture.md, spec saved)
- [x] 2. Versioned parser (`engineer/ingest/formats/f1_2025.py`)
- [x] 3. UDP listener
- [x] 4. Recorder + SQLite session index
- [x] 5. Race state
- [x] 6. Event bus + event detection
- [ ] 7. Replay runner
- [ ] 8. CLI lap lines
- [ ] 9. Tests and benchmark

## Open questions

- Lap Data grid position / driver status field order (spec vs community report) — needs a real
  race-start recording.
- GitHub remote not yet created (cloud session could not create repos).
