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

0 foundation (accepted) · 1 radio director + Piper TTS + lap delta (done; voice works on Windows) ·
2 features/feature tables (done) · 3 tyre coaching (done; checked on an 18-lap race) ·
4 traces + corner coaching (built) · 5 strategy sim · 6 push-to-talk.

## Commands

```
uv sync
uv run pytest
uv run python -m engineer run
uv run python -m engineer.ingest.replay <file.zst> --speed max
uv run python -m engineer.bench <file.zst>
uv run python -m engineer.radio.setup_voice --test   # download + hear the Piper voice
uv run python -m engineer.features build             # recordings -> data/features/*.parquet
uv run python -m engineer.features tyres             # your wear per lap by track/compound
uv run python -m engineer.features corners           # your time per corner vs the AI
uv run python -m engineer.features query "select ..."   # SQL over laps/stints/traces/corners
uv run python -m engineer.ingest.share               # split the newest recording to send it
```

## Radio rules (Phase 1)

- Analyzers never speak directly: they submit a `RadioCall` to the `RadioDirector`.
- The director runs on game time and estimates speech length from text, so replays produce the
  same calls. Keep it that way: no wall-clock time or audio feedback in director decisions.
- Wiring lives in `engineer/app.py:build_engine` (shared by live and replay). Subscription order
  matters: LapHistory before analyzers.
- The golden test also checks `tests/fixtures/*.calls.txt`; update it deliberately (and check each
  call by hand) when phrasing changes.

## Features and tyres (Phases 2-3)

- `LapFeatureBuilder` is the one place lap features are computed, live and offline. Offline tables
  are rebuilt from the recordings (`features build`); never compute features a second way.
- `build_engine(cfg, out, priors=None)` loads tyre history from `data/features/stints.parquet`;
  tests pass `priors=WearPriors()` so they don't depend on the local tables.
- Tyre settings live in `config.yaml` `tyres:` (limit 60%, laps left from 25%, status every 3 laps).

## Corners (Phase 4)

- Traces come from `TraceRecorder` (wired in `build_engine`); compare laps only through
  `corner_deltas()` so live calls, tables and push-to-talk agree.
- `data/tracks/<track>.json` is committed and hand-editable (official turn numbers). Without a
  file the map is learned from the first 2 clean laps and saved. The golden test uses
  `data/tracks/austria.json`.

## Real-data facts (confirmed on tests/fixtures/austria_race_5laps.zst)

- Lap Data spec field order is correct; `m_gridPosition` is zero-based (pole = 0).
- At the chequered flag the lap number does NOT advance; the last-lap time changes on the same lap.
- `m_lastLapTimeInMS` updates on the same frame as the lap number.
- Computed S3 can differ from the game's by 1-2 ms (separate rounding).
- Tyre wear and tyre damage come as whole percents, and tyre damage equals wear in normal running.
- Surface temps swing ~55-150 C within a lap; carcass (inner) temps move smoothly (78-96 C, Softs).
- The recording has a real flashback on lap 3 (to 185.0 s at 194.6 s).
- Telemetry comes at 60 Hz for all 22 cars, AI speed/throttle/brake included.
- Austria (4323 m) has 7 braking corners: T1 T3 T4 T6 T7 T9 T10 (T2 T5 T8 are flat).
- The golden test (`tests/test_golden.py`) replays the fixture; keep it passing.

## Environment

Development happens in a Linux cloud container; the target runtime is Windows. Keep paths and
sockets cross-platform.
