"""Test-wide settings.

Radio wording variety (engineer/radio/variety.py) is off for the suite, so expected texts are
the fixed wording; tests/test_variety.py turns it on to test it."""

import os

os.environ["RACE_ENGINEER_VARIETY"] = "0"
os.environ["RACE_ENGINEER_REPORT_FOCUS"] = "0"     # report/focus.py: no local report

import shutil
from pathlib import Path

import pytest

from engineer.radio.variety import VARIETY


@pytest.fixture(autouse=True)
def _variety_off():
    VARIETY.enabled, VARIETY.live, VARIETY.path, VARIETY.recent = False, False, None, {}
    yield
    VARIETY.enabled, VARIETY.live, VARIETY.path, VARIETY.recent = False, False, None, {}


# -- the real Austria race, analysed once for the whole run ------------------------------------
# (test_report, test_focus and test_gears all need it; it takes ~5 s, so not once per file)

REAL_RACE = Path(__file__).parent / "fixtures" / "austria_race_5laps.zst"
AUSTRIA_MAP = Path(__file__).parent.parent / "data" / "tracks" / "austria.json"


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    """A data folder holding the real race and the Austria map. Shared: don't add to it (copy
    it first, as test_daily_report_only_when_due does)."""
    d = tmp_path_factory.mktemp("data")
    (d / "sessions").mkdir()
    (d / "tracks").mkdir()
    shutil.copy(REAL_RACE, d / "sessions" / "20261020T180000Z_0c69b56cedd41e45.zst")
    shutil.copy(AUSTRIA_MAP, d / "tracks")
    return d


@pytest.fixture(scope="session")
def entry(data_dir) -> dict:
    from engineer.report.analysis import AnalysisCache
    from engineer.state.track_maps import TrackMaps
    cache = AnalysisCache(data_dir / "reports" / "cache", TrackMaps(data_dir / "tracks"))
    rec = next((data_dir / "sessions").glob("*.zst"))
    return cache.get(rec, progress=lambda m: None)


# -- quick runs ---------------------------------------------------------------------------------
# `uv run pytest` runs everything (and so does the release build). While working, `uv run pytest
# -m "not slow"` skips these: whole-race replays that take 3-25 s each and repeat ground the quick
# tests already cover at a smaller scale. The golden test is never in this list: it's the check
# that the real race still gives the same lap lines and radio calls.
SLOW = {
    "test_variety.py::test_replays_repeat_and_races_differ_but_keep_the_facts",
    "test_lap_features.py::test_add_recordings_appends_and_replaces_a_session",
    "test_report.py::test_each_session_in_a_recording_is_its_own_entry",
    "test_coaching.py::test_summary_said_at_the_flag_in_coaching_mode_and_saved",
    "test_racecraft.py::test_a_long_battle_gets_encouragement",
    "test_racecraft.py::test_start_finish_and_countdown",
    "test_racecraft.py::test_a_lost_place_waits_longer_and_a_quick_fight_back_is_praised",
    "test_voice.py::test_quiet_mode_keeps_only_box_calls_and_answers",
    "test_strategy.py::test_plan_then_box_calls_one_lap_and_half_a_lap_before_the_stop",
    "test_strategy.py::test_no_more_calls_after_the_planned_stop",
    "test_strategy.py::test_short_races_and_disabled_strategy_stay_quiet",
    "test_strategy.py::test_safety_car_box_now",
    "test_strategy.py::test_undercut_reminder_when_the_car_behind_pits",
    "test_tyres.py::test_status_every_three_laps_then_laps_left_and_warning",
    "test_tyres.py::test_pace_dropoff",
    "test_tyres.py::test_one_slow_lap_does_not_trigger_dropoff",
    "test_focus.py::test_focus_from_a_real_report_and_back",
    "test_ideal.py::test_live_ideal_lap_and_the_radio",
    "test_pipeline_restart.py::test_restarted_session_with_the_same_uid_starts_again",
    "test_replay.py::test_replay_prints_identical_lines_to_live",
}


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: whole-race replays; skip with -m 'not slow'")


def pytest_collection_modifyitems(config, items):
    found = set()
    for item in items:
        key = f"{Path(str(item.fspath)).name}::{item.originalname or item.name}"
        if key in SLOW:
            item.add_marker(pytest.mark.slow)
            found.add(key)
    missing = SLOW - found
    if missing and len(items) > 100:          # a full run: catch a renamed or deleted test
        raise pytest.UsageError(f"tests/conftest.py SLOW lists tests that don't exist: {missing}")
