# CLAUDE.md — race-engineer

Personal AI race engineer for EA SPORTS F1 25 (Windows, Python 3.12, asyncio). Listens to UDP
telemetry, keeps a live race model, and (Phase 1+) speaks like a race engineer. The owner is new to
this kind of project: explain decisions briefly in plain language, keep code readable over clever.

Read `docs/architecture.md` (full design) and `PROGRESS.md` (what's done / open questions) first.

## Non-negotiable rules

- **Packet layouts come only from `docs/spec/f1_25_udp_spec.md` / the PDF in `docs/spec/`.** Never from
  memory or older game years. Every dtype's size is asserted against the spec at import.
- **Record everything, replay anything.** Live and replay both go through `Pipeline.feed(raw, t_ns)`.
  Never add a code path that only works live.
- **Only the parser and `RaceState.apply()` write state.** Nothing else decodes bytes.
- **Never block the asyncio loop.** Heavy work (TTS, strategy sims, speech recognition) goes in worker
  processes.
- Wheel arrays: **RL, RR, FL, FR**. Unknown = NaN/None, never 0 (restricted telemetry, inactive cars).
- Don't build later-phase features early, but don't design them out.

## Phase plan

0 foundation (done, awaiting real-game acceptance) · 1 radio director + Piper TTS + lap delta · 2 features/feature tables ·
3 tyre coaching · 4 traces + corner coaching · 5 strategy sim · 6 push-to-talk.

## Commands

```
uv sync
uv run pytest
uv run python -m engineer run
uv run python -m engineer.ingest.replay <file.zst> --speed max
uv run python -m engineer.bench <file.zst>
```

## Known open questions

- Lap Data `m_gridPosition` / `m_driverStatus` order: spec order implemented; community report says
  they're swapped in real packets. `engineer/state/checks.py` warns at race start; confirm against a
  real recording and update `PROGRESS.md`.
- Development happens in a Linux cloud container; the target runtime is Windows. Keep paths and
  sockets cross-platform.
