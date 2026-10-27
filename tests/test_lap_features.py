import io

import numpy as np
import pytest

from engineer.app import build_engine
from engineer.config import Config
from engineer.features import tables
from engineer.features.lap_features import LapFeatures, fit_wear_rate, summarize_stints
from engineer.features.priors import WearPriors
from engineer.ingest.recorder import Recorder
from tests.synth import FakeRace


def run(race, **frames_kw):
    cfg = Config(data_dir="/nonexistent-test-dir")
    eng = build_engine(cfg, out=io.StringIO(), priors=WearPriors())
    for t_ns, raw in race.frames(**frames_kw):
        eng.pipeline.feed(raw, t_ns)
    return eng


def test_feature_record_per_lap_with_stints_split_at_pit_stop():
    race = FakeRace({0: [30000] * 5, 1: [30500] * 5}, pit_laps={0: 2},
                    wear_per_lap=[2, 2, 3, 4], compound_after_pit=17, extra_packets=False)
    eng = run(race, duration_ms=160000)
    laps = eng.features.for_car(0)
    assert [r.lap for r in laps] == [1, 2, 3, 4, 5]
    assert [r.stint for r in laps] == [1, 1, 2, 2, 2]
    assert [r.stint_lap for r in laps] == [1, 2, 1, 2, 3]
    assert laps[1].pit_in_lap and laps[2].pit_out_lap and not laps[2].pit_in_lap
    assert laps[2].visual_compound == 17 and laps[0].visual_compound == 16
    assert laps[0].race_start_lap and not laps[0].clean and laps[3].clean
    # Wear at the end of each lap: front right 4 % a lap, reset by the stop
    assert laps[0].wear[3] == pytest.approx(4, abs=1)
    assert laps[4].wear[3] == pytest.approx(12, abs=1)
    assert laps[3].inner_avg == pytest.approx((90, 90, 90, 90))
    assert laps[3].hot_frac == (0, 0, 0, 0) and laps[3].temps.samples.min() > 0
    # Other cars get records too
    assert len(eng.features.for_car(1)) == 5 and not eng.features.for_car(1)[0].is_player

    stints = summarize_stints(laps)
    assert [(s.stint, s.laps, s.visual_compound) for s in stints] == [(1, 2, 16), (2, 3, 17)]
    assert stints[1].wear_rate[3] == pytest.approx(4, abs=0.6)


def _lap(stint_lap, wear):
    return LapFeatures(1, 10, 15, 0, True, None, stint_lap, 1, stint_lap, 90000, None, None, None,
                       False, False, False, False, True, False, None, 16, 18, 0,
                       tuple(wear), (0,) * 4, (0,) * 4, (90,) * 4, (90,) * 4, (90,) * 4,
                       (0,) * 4, (0,) * 4, 10.0, 30, 20, 0, 0.0)


def test_fit_wear_rate():
    laps = [_lap(n, (n * 1.0, n * 2.0, n * 3.0, np.nan)) for n in range(1, 6)]
    rate = fit_wear_rate(laps)
    assert rate[:3] == pytest.approx([1, 2, 3])
    assert np.isnan(rate[3])
    assert np.isnan(fit_wear_rate(laps[:1])).all()     # one lap: no rate yet


def test_flashback_keeps_earlier_sectors_of_the_lap():
    race = FakeRace({0: [30000] * 3}, extra_packets=False)
    # Rewind 5 s in the middle of lap 2's last sector.
    eng = run(race, flashback_at_ms=30000 + 27000, flashback_back_ms=5000)
    lap2 = eng.features.for_car(0)[1]
    assert lap2.lap == 2 and lap2.temps.samples.min() > 0


def test_offline_tables_and_priors(tmp_path):
    race = FakeRace({0: [30000] * 5, 1: [30500] * 5}, wear_per_lap=[2, 2, 3, 4],
                    extra_packets=False)
    rec = Recorder(tmp_path / "sessions")
    for t_ns, raw in race.frames(duration_ms=160000):
        rec(raw, t_ns)
    rec.close()
    n_laps, n_stints = tables.build(tmp_path / "sessions", tmp_path / "features",
                                    progress=lambda _m: None)
    assert (n_laps, n_stints) == (10, 2)
    rows = tables.query(tmp_path / "features",
                        "select lap, wear_fr, inner_avg_rl from laps where is_player order by lap"
                        ).fetchall()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5] and rows[2][2] == pytest.approx(90)
    priors = WearPriors.load(tmp_path / "features")
    assert priors.get(10, 18)[3] == pytest.approx(4, abs=0.6)    # FakeRace track 10, C3
    assert priors.get(10, 99) is None
    assert len(WearPriors.load(tmp_path / "nothing")) == 0
    [(n_sections,)] = tables.query(tmp_path / "features",
                                   "select count(*) from traces where car_idx = 0 and lap = 3"
                                   ).fetchall()
    assert n_sections == 1001              # 5000 m lap in 5 m sections (+ the line)
