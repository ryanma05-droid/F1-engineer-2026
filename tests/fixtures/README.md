# tests/fixtures

Real F1 25 recordings for the golden test (`tests/test_golden.py`).

1. Record a session with `python -m engineer run` (it lands in `data/sessions/`).
2. Copy the `.zst` here with a short name, e.g. `spa_race_3laps.zst`.
3. Create `spa_race_3laps.expected.txt` with the lap lines, checked against the in-game lap times:
   `python -m engineer.ingest.replay tests/fixtures/spa_race_3laps.zst --speed max > tmp.txt`,
   compare each lap time and sector with the game's timing screen, then keep the `Lap ...` lines.
4. `git add -f tests/fixtures/spa_race_3laps.zst tests/fixtures/spa_race_3laps.expected.txt`
