"""Driver report: analysis of a real recording, insights, the page, and the daily trigger."""

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engineer.config import Config
from engineer.report import daily
from engineer.report.analysis import ANALYSIS_VERSION, AnalysisCache
from engineer.report.insights import build_report, recency_weight
from engineer.report.render import render_html
from engineer.state.track_maps import TrackMaps

FIXTURE = Path(__file__).parent / "fixtures" / "austria_race_5laps.zst"
AUSTRIA_MAP = Path(__file__).parent.parent / "data" / "tracks" / "austria.json"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("data")
    (d / "sessions").mkdir()
    (d / "tracks").mkdir()
    shutil.copy(FIXTURE, d / "sessions" / "20261020T180000Z_0c69b56cedd41e45.zst")
    shutil.copy(AUSTRIA_MAP, d / "tracks")
    return d


@pytest.fixture(scope="module")
def entry(data_dir) -> dict:
    cache = AnalysisCache(data_dir / "reports" / "cache", TrackMaps(data_dir / "tracks"))
    rec = next((data_dir / "sessions").glob("*.zst"))
    return cache.get(rec, progress=lambda m: None)


def test_analysis_of_a_real_race(entry):
    assert entry["version"] == ANALYSIS_VERSION and "skipped" not in entry
    assert entry["track"] == "Austria"
    assert len(entry["my_laps"]) == 4                      # laps 2-5 (lap 1 is the start)
    assert entry["pace_rank"] == 1                         # quickest car in that race
    assert [c["number"] for c in entry["corners"]] == [1, 3, 4, 6, 7, 9, 10]
    t3 = next(c for c in entry["corners"] if c["number"] == 3)
    assert len(t3["mine"]) == 4 and t3["ref_runs"] >= 10
    assert t3["ref"]["min_kph"] < 80                       # the hairpin
    # Austria T3 is where you gain most on the AI (checked by hand on the traces)
    d = sum(r["time_s"] for r in t3["mine"]) / 4 - t3["ref"]["time_s"]
    assert d < -0.2


def test_cache_is_reused_until_the_recording_changes(data_dir, entry):
    cache = AnalysisCache(data_dir / "reports" / "cache", TrackMaps(data_dir / "tracks"))
    rec = next((data_dir / "sessions").glob("*.zst"))
    assert cache.cached(rec) == entry
    path = data_dir / "reports" / "cache" / f"{rec.name}.json"
    stale = dict(entry, version=ANALYSIS_VERSION - 1)
    path.write_text(json.dumps(stale))
    assert cache.cached(rec) is None
    path.write_text(json.dumps(entry))


def test_report_finds_strengths_and_ranks_themes(entry):
    now = datetime.fromisoformat(entry["recorded_at"]) + timedelta(days=1)
    r = build_report([entry], now)
    assert [t.track for t in r.tracks] == ["Austria"]
    assert r.tracks[0].pct < 0 and r.overall_pct < 0
    assert r.types[0].name == "Slow corners onto a straight"     # T3: your best corner type
    assert all(a.cost_s >= b.cost_s for a, b in zip(r.themes, r.themes[1:]))
    assert any("Slow corners onto a straight" in s for s in r.strengths)
    html = render_html(r)
    for part in ("Where your lap time lives", "Austria", "What costs you time", "Corner by corner"):
        assert part in html


def test_newer_sessions_count_more():
    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    assert recency_weight("2026-11-30T00:00:00+00:00", now) == 1.0
    assert recency_weight("2026-10-31T00:00:00+00:00", now) == pytest.approx(0.5)


def test_skipped_recordings_dont_break_the_report():
    r = build_report([{"file": "x.zst", "recorded_at": "2026-10-27T00:00:00+00:00",
                       "skipped": "no laps of yours"}])
    assert not r.sessions and r.skipped
    assert "Not used: x.zst" in render_html(r)


def test_daily_report_only_when_due(data_dir, entry, monkeypatch):
    cfg = Config(data_dir=data_dir)
    monkeypatch.setattr(daily, "html_to_pdf", lambda html, pdf: False)   # no browser in tests
    assert daily.due(cfg)                                # a recording, no report yet
    path = daily.generate(cfg, progress=lambda m: None)
    assert path.suffix == ".html" and path.exists()
    assert not daily.due(cfg)                            # built today, nothing new
    state = daily.load_state(cfg)
    state["date"] = "2000-01-01"                         # yesterday's report...
    daily._state_path(cfg).write_text(json.dumps(state))
    assert not daily.due(cfg)                            # ...but no new sessions since
    shutil.copy(FIXTURE, data_dir / "sessions" / "20261021T180000Z_0c69b56cedd41e46.zst")
    assert daily.due(cfg)
    cfg.report.daily = False
    assert not daily.start_in_background(cfg)            # turned off in config.yaml
