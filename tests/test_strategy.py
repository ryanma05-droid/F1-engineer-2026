import io
import math

import numpy as np
import pytest

from engineer.app import build_engine
from engineer.config import Config, Strategy
from engineer.features.priors import WearPriors
from engineer.radio import phrases
from engineer.strategy.model import (CompoundModel, Estimate, GameTyreInfo, ModelSettings,
                                     StrategyModel, TyreSet, build_model, combine, fit_deg,
                                     fit_offsets, game_tyre_info, measure_pit_losses)
from engineer.strategy.runner import InlineRunner
from engineer.strategy.sim import Plan, RaceSituation, Stop, simulate
from tests.synth import FakeRace


# -- synthetic lap rows (the columns of data/features/laps.parquet) ----------------------------

def stint_rows(session="s1", car=0, stint=1, compound=17, laps=10, first_lap=2, rate=4.0,
               deg=0.05, base=70.0, fuel0=30.0, fuel_per_lap=1.5, player=True, track=17,
               offset=0.0, start_wear=0.0, extra_ms=None, noise_ms=0.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(laps):
        lap = first_lap + k
        wear = start_wear + rate * (k + 1)
        fuel = fuel0 - fuel_per_lap * k
        t = base + offset + deg * wear + 0.03 * fuel
        ms = int(round(t * 1000 + rng.normal(0, noise_ms))) + (extra_ms or {}).get(lap, 0)
        rows.append(dict(session_uid=session, track_id=track, car_idx=car, is_player=player,
                         lap=lap, stint=stint, stint_lap=k + 1, lap_time_ms=ms, clean=True,
                         safety_car=False, invalid=False, pit_in_lap=False, pit_out_lap=False,
                         race_start_lap=False, visual_compound=compound, fuel_kg=fuel,
                         wear_rl=wear * 0.5, wear_rr=wear * 0.6, wear_fl=wear * 0.8,
                         wear_fr=wear))
    return rows


def test_combine_weights_and_ignores_unknowns():
    est = combine([(1.0, 1, "a"), (4.0, 2, "b"), (None, 5, "c"), (math.nan, 5, "d"),
                   (9.0, 0, "e")])
    assert est.value == pytest.approx(3.0) and est.weight == 3 and est.sources == ("a", "b")
    assert combine([(None, 1, "x")]) is None


def test_deg_fit_recovers_trend_and_drops_a_mistake_lap():
    rows = stint_rows(laps=12, deg=0.06, extra_ms={6: 3000})       # one lap with a big mistake
    deg, deg2, n, se = fit_deg(rows, fuel_s_per_kg=0.03)
    assert deg == pytest.approx(0.06, abs=0.002) and deg2 == 0 and n == 11 and se < 0.001


def test_deg_fit_needs_real_stints():
    # 5-lap races: 4 clean laps of "finding the pace" don't count.
    assert math.isnan(fit_deg(stint_rows(laps=4), 0.03)[0])


def test_quadratic_only_when_the_laps_show_a_curve():
    rows = []
    for k in range(18):
        wear = 3.0 * (k + 1)
        t = 70 + 0.03 * wear + 0.0008 * wear ** 2
        rows.append(dict(session_uid="s", car_idx=0, stint=1, lap=k + 2, stint_lap=k + 1,
                         lap_time_ms=int(t * 1000), clean=True, safety_car=False, fuel_kg=0.0,
                         visual_compound=17, wear_rl=wear, wear_rr=wear, wear_fl=wear,
                         wear_fr=wear, is_player=True))
    deg, deg2, n, _ = fit_deg(rows, 0.03, quadratic=True)
    assert deg2 == pytest.approx(0.0008, abs=1e-4) and deg == pytest.approx(0.03, abs=0.005)


def test_your_laps_take_over_from_the_game_as_they_pile_up():
    # Real laps scatter by a few tenths: a short stint says little, many long ones say a lot.
    game = GameTyreInfo(deg={17: 0.05}, offsets={}, usable_life={})
    few = stint_rows(laps=6, deg=0.09, noise_ms=400)
    many = few + [r for n in range(2, 7) for r in stint_rows(session=f"s{n}", laps=15, deg=0.09,
                                                             noise_ms=400, seed=n)]
    d_few = build_model(few, few, [], game, ModelSettings()).compounds[17].deg
    d_many = build_model(many, many, [], game, ModelSettings()).compounds[17].deg
    assert 0.05 < d_few.value < d_many.value and d_few.weight < d_many.weight / 3
    assert d_many.value == pytest.approx(0.09, abs=0.01)
    assert "game" in d_many.sources


def test_wear_rate_current_stint_history_and_ai_scaled_by_your_wear_ratio():
    # Another track: you wear 1.5x the AI on mediums. This track: only the AI has run hards.
    other = (stint_rows(track=3, rate=6.0, laps=8)
             + stint_rows(track=3, car=5, player=False, rate=4.0, laps=8))
    here = (stint_rows(track=17, car=5, player=False, compound=18, rate=2.0, laps=10)
            + stint_rows(track=17, compound=17, rate=5.0, laps=8, session="old"))
    current = stint_rows(track=17, compound=17, rate=6.0, laps=5, session="now")
    rows_all = other + here + current
    track = here + current
    m = build_model(track, rows_all, current, None, ModelSettings())
    assert m.wear_ratio.value == pytest.approx(1.5, rel=0.05)
    assert m.compounds[18].wear_rate.value == pytest.approx(3.0, rel=0.05)    # 2.0 x 1.5
    # Mediums: 7 laps of history at 5.0 and 4 laps of this stint at 6.0.
    assert m.compounds[17].wear_rate.value == pytest.approx((5 * 7 + 6 * 4) / 11, rel=0.02)
    # Softs: nothing at all, and no game data: can't be planned with.
    assert 16 not in m.compounds


def test_game_tyre_life_fills_a_compound_you_have_no_data_for():
    current = stint_rows(compound=16, rate=8.0, laps=5)
    game = GameTyreInfo(deg={}, offsets={}, usable_life={16: 10, 17: 15, 18: 20})
    m = build_model(current, current, current, game, ModelSettings())
    assert m.compounds[17].wear_rate.value == pytest.approx(8.0 * 10 / 15, rel=0.02)
    assert m.compounds[18].wear_rate.value == pytest.approx(4.0, rel=0.02)


def test_offsets_from_the_same_car_on_two_compounds():
    rows = (stint_rows(compound=17, laps=8) +
            stint_rows(compound=16, stint=2, first_lap=10, laps=8, offset=-0.5, fuel0=18))
    off = fit_offsets(rows, {16: 0.05, 17: 0.05}, 0.03)
    assert off[16][0] == pytest.approx(-0.5, abs=0.01) and off[16][1] == 8


def _pit(rows, lap, in_extra, out_extra, sc=False):
    for r in rows:
        if r["lap"] == lap:
            r.update(pit_in_lap=True, clean=False, safety_car=sc)
            r["lap_time_ms"] += in_extra
        if r["lap"] == lap + 1:
            r.update(pit_out_lap=True, clean=False, safety_car=sc)
            r["lap_time_ms"] += out_extra
    return rows


def test_pit_loss_measured_green_and_under_safety_car():
    green_stop = _pit(stint_rows(laps=12, deg=0, fuel_per_lap=0), 6, 8000, 13000)
    g, sc = measure_pit_losses(green_stop)
    assert g == [pytest.approx(21.0, abs=0.01)] and sc == []
    # Safety car: three cars stay out at 100 s laps, one pits (in 104 s, out 113 s).
    rows = []
    for car in range(4):
        rs = stint_rows(car=car, laps=6, deg=0, fuel_per_lap=0, base=100, fuel0=0)
        for r in rs:
            r["safety_car"] = True
            r["clean"] = False
        rows += rs
    _pit([r for r in rows if r["car_idx"] == 3], 4, 4000, 13000, sc=True)
    g, sc = measure_pit_losses(rows)
    assert g == [] and sc == [pytest.approx(17.0, abs=0.01)]


def test_pit_loss_default_until_measured_then_measured():
    rows = stint_rows(laps=12)
    m = build_model(rows, rows, [], None, ModelSettings(pit_loss_s=22.0))
    assert m.pit_loss.value == 22.0 and m.pit_loss_sc.value == 11.0
    stop = _pit(stint_rows(laps=12, deg=0, fuel_per_lap=0), 6, 6000, 12000)
    m = build_model(stop * 1, stop, [], None, ModelSettings(pit_loss_s=22.0))
    assert m.pit_loss.value == pytest.approx((18 + 22) / 2)


def test_game_tyre_info_from_the_tyre_sets_packet():
    # Numbers from the real 18-lap Austria race, lap 10 (the new hard set is no longer available,
    # but still carries the game's estimate; the used one at 4 % isn't a fair comparison).
    sets = [TyreSet(17, 0, 19, -1.005), TyreSet(18, 4, 24, -0.483), TyreSet(16, 0, 13, -1.448),
            TyreSet(18, 0, 24, -0.561), TyreSet(17, 20, 19, 0.0, fitted=True)]
    g = game_tyre_info(sets)
    assert g.deg[17] == pytest.approx(0.05025)
    assert g.offsets[16] == pytest.approx(-0.443) and g.offsets[17] == 0
    assert g.offsets[18] == pytest.approx(0.444)          # same as the game said on lap 1
    assert g.usable_life == {17: 19, 18: 24, 16: 13}


# -- simulator ---------------------------------------------------------------------------------

def model(rates=None, offsets=None, deg=0.05, pit=20.0, sc=10.0):
    rates = rates or {16: 6.0, 17: 4.0, 18: 3.0}
    offsets = offsets or {16: -0.5, 17: 0.0, 18: 0.4}
    comps = {c: CompoundModel(c, Estimate(rates[c], 10), Estimate(deg, 10), 0.0,
                              Estimate(offsets[c], 10)) for c in rates}
    return StrategyModel(comps, Estimate(pit, 1), Estimate(sc, 1), None)


def situation(**kw):
    # A steep cost past the limit, so these tests can check plans stay inside it.
    base = dict(lap_done=2, total_laps=30, compound=17, wear=8.0,
                sets={16: [0.0, 0.0], 17: [0.0], 18: [0.0]}, used=frozenset({17}),
                require_two=True, limit_pct=60.0, pit_loss_s=20.0, over_limit_s_per_pct=1000.0)
    base.update(kw)
    return RaceSituation(**base)


def _wear_ok(sit, m, plan):
    """Every stint of the plan stays inside the wear limit."""
    start, wear0, comp = sit.lap_done, sit.wear, sit.compound
    for stop in list(plan.stops) + [Stop(sit.total_laps, None, 0)]:
        end_wear = wear0 + m.compounds[comp].wear_rate.value * (stop.lap - start)
        if end_wear > sit.limit_pct + 1e-9:
            return False
        start, wear0, comp = stop.lap, stop.set_wear, stop.compound
    return True


def test_sim_finds_a_legal_plan_and_a_window():
    m = model()
    sit = situation()
    res = simulate(sit, m)
    best = res.best
    assert best.n_stops >= 1 and _wear_ok(sit, m, best)
    assert {s.compound for s in best.stops} - {17}           # a second compound is used
    lo, hi = best.window
    assert lo <= best.stops[0].lap <= hi
    alt = res.alternative()
    assert alt is not None and alt.n_stops != best.n_stops and alt.cost_s >= best.cost_s


def test_sim_brute_force_agrees_on_one_stop_plans():
    m, sit = model(), situation(total_laps=20, max_stops=1)
    res = simulate(sit, m)
    best = None
    for lap in range(sit.lap_done + 1, sit.total_laps):
        for c in (16, 18):
            plan = Plan((Stop(lap, c, 0.0),), 0.0)
            if not _wear_ok(sit, m, plan):
                continue
            cost = sit.pit_loss_s
            for k in range(1, lap - sit.lap_done + 1):
                cost += m.compounds[17].lap_cost(sit.wear + 4.0 * k)
            for k in range(1, sit.total_laps - lap + 1):
                cost += m.compounds[c].lap_cost(m.compounds[c].wear_rate.value * k)
            if best is None or cost < best[0] - 1e-9:
                best = (cost, lap, c)
    assert res.by_stops[1].cost_s == pytest.approx(best[0])
    assert (res.by_stops[1].stops[0].lap, res.by_stops[1].stops[0].compound) == best[1:]


def test_sim_no_stop_when_the_rule_is_met_and_tyres_last():
    sit = situation(total_laps=10, used=frozenset({16, 17}))
    assert simulate(sit, model()).best.stops == ()
    # The same race with the two-compound rule: one stop.
    assert simulate(situation(total_laps=10), model()).best.n_stops == 1


def test_sim_wear_limit_forces_extra_stops():
    sit = situation(total_laps=60, sets={16: [0.0] * 3, 17: [0.0] * 3, 18: [0.0] * 3},
                    max_stops=5)
    best = simulate(sit, model(rates={16: 8.0, 17: 6.0, 18: 5.0})).best
    assert best.n_stops >= 3 and _wear_ok(sit, model(rates={16: 8.0, 17: 6.0, 18: 5.0}), best)


def test_sim_a_little_over_the_limit_at_the_flag_beats_a_stop():
    # Real 18-lap Austria race: mediums at ~4 % a lap from lap 3 end around 60-62 %.
    m = model(rates={16: 6.0, 17: 4.1, 18: 3.0})
    sit = situation(lap_done=3, total_laps=18, wear=0.0, used=frozenset({17, 18}),
                    over_limit_s_per_pct=1.0)
    assert simulate(sit, m).best.stops == ()
    # Far over the limit: stopping wins.
    assert simulate(situation(lap_done=3, total_laps=30, wear=0.0, used=frozenset({17, 18}),
                              over_limit_s_per_pct=1.0), m).best.n_stops == 1


def test_sim_only_uses_sets_you_have():
    sit = situation(sets={18: [0.0]})
    best = simulate(sit, model()).best
    assert [s.compound for s in best.stops] == [18]


def test_sim_safety_car_makes_an_earlier_stop_pay():
    m = model(rates={16: 6.0, 17: 3.0, 18: 2.5})
    green = simulate(situation(total_laps=25), m).best
    sc_lap = green.stops[0].lap - 3
    sc = simulate(situation(total_laps=25, sc_pit_loss_s=8.0, sc_laps=(sc_lap,)), m).best
    assert sc.stops[0].lap == sc_lap and sc.cost_s < green.cost_s


# -- phrases ------------------------------------------------------------------------------------

def test_strategy_phrases():
    one = Plan((Stop(11, 18, 0),), 100.0, (11, 13))
    two = Plan((Stop(8, 17, 0), Stop(19, 18, 0)), 104.2, (7, 9))
    assert phrases.plan_phrase(one, two) == ("Plan is one stop, box lap eleven to thirteen for "
                                             "hards. Two stops is four point two seconds slower.")
    assert phrases.plan_phrase(two) == \
        "Plan is two stops: lap seven to nine for mediums, then lap nineteen for hards."
    assert phrases.plan_phrase(Plan((), 50.0)) == "Plan is no stop, tyres will make the end."
    assert phrases.plan_phrase(Plan((Stop(9, 17, 0),), 1.0, (9, 9)), change=True) == \
        "Plan change: one stop, box lap nine for mediums."
    assert phrases.plan_phrase(one, two, "terse") == \
        "Plan is one stop, box lap eleven to thirteen for hards."
    assert phrases.box_half_phrase(18) == "Box box, end of this lap. Hards."
    assert phrases.safety_car_box_phrase("safety car", 17) == \
        "Safety car, box this lap, box this lap. Mediums, it's a cheap stop."
    assert phrases.safety_car_stay_phrase("virtual safety car") == \
        "Virtual safety car, stay out. Stopping now doesn't pay."
    assert phrases.undercut_phrase("Norris") == "Norris behind has pitted, watch the undercut."


# -- the analyzer, end to end through the packets ----------------------------------------------

SETS = [(16, 0, 8, 0), (16, 0, 8, 0), (17, 0, 12, 400), (18, 0, 16, 800)]


def run(race, strategy: Strategy | None = None, **frames_kw):
    cfg = Config(data_dir="/nonexistent-test-dir", strategy=strategy or Strategy())
    out = io.StringIO()
    eng = build_engine(cfg, out=out, priors=WearPriors(), strategy_runner=InlineRunner(None))
    for t_ns, raw in race.frames(**frames_kw):
        eng.pipeline.feed(raw, t_ns)
    radio = [(float(line.split()[1].rstrip("s")), line.split("s  ", 1)[1].split("  (held")[0])
             for line in out.getvalue().splitlines() if line.startswith("RADIO")]
    return eng, radio


def strategy_calls(radio):
    return [(t, r) for t, r in radio if r.startswith(("Plan", "Box", "Safety", "Virtual"))
            or "undercut" in r]


def test_plan_then_box_calls_one_lap_and_half_a_lap_before_the_stop():
    # 12 laps of 30 s; the front right wears 8 % a lap, so the softs can't make the end.
    race = FakeRace({0: [30000] * 12}, wear_per_lap=[4, 4, 6, 8], extra_packets=False,
                    tyre_sets=SETS, fuel_per_lap=1.0)
    eng, radio = run(race)
    calls = strategy_calls(radio)
    t0, first = calls[0]
    assert 60 < t0 < 70 and first.startswith("Plan is one stop, box lap ")
    assert "for mediums" in first or "for hards" in first
    box = [(t, r) for t, r in calls if r.startswith("Box this lap")]
    half = [(t, r) for t, r in calls if r.startswith("Box box")]
    # "Box this lap" as the stop lap starts, "box box" half way round the same lap.
    stop_lap = int(box[0][0] // 30) + 1
    assert box[0][0] - 30 * (stop_lap - 1) < 5
    assert 30 * (stop_lap - 1) + 15 <= half[0][0] < 30 * stop_lap


def test_no_more_calls_after_the_planned_stop():
    probe, _ = run(FakeRace({0: [30000] * 12}, wear_per_lap=[4, 4, 6, 8], extra_packets=False,
                            tyre_sets=SETS, fuel_per_lap=1.0), duration_ms=100000)
    stop = probe.strategy.latest.sim.best.stops[0]
    race = FakeRace({0: [30000] * 12}, wear_per_lap=[4, 4, 6, 8], extra_packets=False,
                    tyre_sets=SETS, fuel_per_lap=1.0, pit_laps={0: stop.lap},
                    compound_after_pit=stop.compound)
    eng, radio = run(race)
    calls = [r for _, r in strategy_calls(radio)]
    assert sum(r.startswith("Box this lap") for r in calls) == 1
    assert sum(r.startswith("Box box") for r in calls) == 1
    assert not any(r.startswith("Plan change") for r in calls)
    assert eng.strategy.latest.sim.best.stops == ()


def test_undercut_reminder_when_the_car_behind_pits():
    race = FakeRace({0: [30000] * 12, 1: [30100] * 12}, wear_per_lap=[4, 4, 6, 8],
                    extra_packets=False, tyre_sets=SETS, fuel_per_lap=1.0, pit_laps={1: 3},
                    gap_ahead_ms={1: 1500}, names=["PLAYER", "NORRIS"])
    _, radio = run(race, duration_ms=100000)
    assert any(r == "Norris behind has pitted, watch the undercut." for _, r in radio)
    _, radio = run(race, Strategy(undercut_reminder=False), duration_ms=100000)
    assert not any("undercut" in r for _, r in radio)


def test_safety_car_box_now():
    # Probe the green plan, then bring a safety car out early in the lap two before the stop.
    probe, _ = run(FakeRace({0: [30000] * 14}, wear_per_lap=[3, 3, 4, 5], extra_packets=False,
                            tyre_sets=SETS, fuel_per_lap=1.0), duration_ms=100000)
    green_lap = probe.strategy.latest.sim.best.stops[0].lap
    sc_lap = green_lap - 2
    start = (sc_lap - 1) * 30000 + 3000
    race = FakeRace({0: [30000] * 14}, wear_per_lap=[3, 3, 4, 5], extra_packets=False,
                    tyre_sets=SETS, fuel_per_lap=1.0, safety_car_ms=(start, start + 60000))
    _, radio = run(race, duration_ms=start + 20000)
    sc = [(t, r) for t, r in radio if r.startswith("Safety car")]
    assert sc and sc[0][1].startswith("Safety car, box this lap, box this lap.")
    assert start / 1000 <= sc[0][0] < start / 1000 + 2


def test_short_races_and_disabled_strategy_stay_quiet():
    race = FakeRace({0: [30000] * 5}, wear_per_lap=[4, 4, 6, 8], extra_packets=False,
                    tyre_sets=SETS)
    _, radio = run(race)
    assert strategy_calls(radio) == []
    race = FakeRace({0: [30000] * 12}, wear_per_lap=[4, 4, 6, 8], extra_packets=False,
                    tyre_sets=SETS)
    _, radio = run(race, Strategy(enabled=False))
    assert strategy_calls(radio) == []


def test_worker_runner_gives_the_same_answer_as_inline():
    import time

    from engineer.strategy.runner import StrategyRequest, WorkerRunner
    rows = tuple(stint_rows(laps=8, rate=5.0))
    req = StrategyRequest(seq=1, reason="lap", track_id=17, session_uid="s1", session_rows=rows,
                          current_stint=rows, game=None,
                          situation=situation(lap_done=9, total_laps=25, wear=40.0),
                          settings=ModelSettings())
    inline = InlineRunner(None)
    inline.submit(req)
    want = inline.poll()[0]
    worker = WorkerRunner(None)
    try:
        worker.submit(req)
        got = []
        deadline = time.monotonic() + 30
        while not got and time.monotonic() < deadline:
            got = worker.poll()
            time.sleep(0.05)
    finally:
        worker.close()
    assert got and got[0].seq == 1 and got[0].sim.best == want.sim.best


def test_what_if_box_now_onto_a_given_compound():
    """Push-to-talk "what happens if I pit for hards?" (29 Oct Imola)."""
    m = model()
    hards = simulate(situation(first_stop_lap=5, first_stop_compound=18), m).best
    softs = simulate(situation(first_stop_lap=5, first_stop_compound=16), m).best
    assert (hards.stops[0].lap, hards.stops[0].compound) == (5, 18)
    assert (softs.stops[0].lap, softs.stops[0].compound) == (5, 16)
    none_left = simulate(situation(first_stop_lap=5, first_stop_compound=18,
                                   sets={16: [0.0], 17: [0.0]}), m)
    assert none_left.best is None
