# data/features

Feature tables (Parquet), rebuilt from the recordings in data/sessions/. Not committed.

    uv run python -m engineer.features build     # laps.parquet, stints.parquet
    uv run python -m engineer.features tyres     # your wear per lap by track and compound
