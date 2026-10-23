# race-engineer

A personal AI race engineer for EA SPORTS F1 25 on Windows. It listens to the game's UDP telemetry,
keeps a live model of the race, and (from Phase 1) talks to you over the radio.

Status: **Phase 0 (foundation)**. See `PROGRESS.md`, `CLAUDE.md` and `docs/architecture.md`.

## Setup

```
uv sync
```

(Without uv: `python -m venv .venv`, activate it, then `pip install -e . pytest`.)

## Game settings

Settings > Telemetry Settings: UDP Telemetry **On**, Broadcast Mode Off, IP Address `127.0.0.1`,
UDP Port `20777`, UDP Send Rate **60Hz**, UDP Format **2025**.

## Run

```
uv run python -m engineer run                                  # live: prints one line per lap
uv run python -m engineer.ingest.replay <file.zst> --speed max # replay a recording
uv run python -m engineer sessions                             # list recorded sessions
uv run pytest                                                  # tests
uv run python -m engineer.bench <file.zst>                     # parse + state benchmark
```
