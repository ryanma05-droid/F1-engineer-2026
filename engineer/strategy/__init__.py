"""Race strategy (Phase 5): when to pit and which tyres to fit.

- `model.py`  learns the numbers from your laps: tyre wear per lap, how much each % of wear costs,
              how much faster each compound is, and how long a pit stop takes.
- `sim.py`    tries every plan (stop laps x compounds) and adds up the race time of each.
- `runner.py` runs both, in a worker process when live and in-process for replays and tests.

The analyzer that turns plans into radio calls is `engineer/analyzers/strategy.py`.
"""
