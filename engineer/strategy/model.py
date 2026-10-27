"""The strategy model: what each tyre compound will do over a stint, and what a pit stop costs.

Everything here is LEARNED from laps, and every number gets better as more races are recorded:

    lap time  =  base pace  +  compound offset  +  deg x wear  (+ deg2 x wear^2)  +  fuel

- wear rate (% per lap, worst wheel) per compound: your current stint, your past stints at this
  track, the AI's stints scaled by how much harder you are on tyres than the AI (learned from
  every race you've recorded), and the game's own tyre life figures for compounds you haven't run.
- deg (seconds per % of wear) per compound: a fit of your fuel-corrected lap times against wear
  over your clean laps (each stint gets its own baseline, so only the trend within a stint counts),
  plus the game's own estimate from the Tyre Sets packet, each weighed by how precise it is (your
  fit by its standard error, the game's as good to +-`game_deg_sd`): a few noisy laps barely move
  it, a long clean stint moves it a lot. A curve (wear^2) is added once your laps
  cover enough wear to show the drop-off. The AI's lap times are NOT used here: on real data they
  barely slow down as their tyres wear (the game drives them differently), while yours and the
  game's estimate agree (Austria, mediums: 0.053 and 0.050 s per %).
- compound offsets (how much faster softs are than mediums, ...): the same car's pace on two
  compounds in one race, plus the game's estimate.
- pit loss: in-lap + out-lap minus the car's normal laps around them, for every stop in your
  recordings and in the current race (the AI stops count too). Separately under safety car.

How the sources are mixed: each source is an estimate with a weight in "laps of evidence". Your
own laps count 1 each, the AI field counts like one driver's laps times `ai_weight`, the game's
estimate counts `game_prior_laps` (deg is weighed by precision instead, see above).
The model takes the weighted average, so with little data the game's numbers matter, and as your
laps pile up they take over. Nothing about tyre behaviour is fixed in the code; the only fallbacks
(config `strategy:`) are used when there is no data and no game estimate at all.

Inputs are plain lap rows (dicts with the columns of data/features/laps.parquet): the history
table and this session's `LapFeatures.to_row()`, so live and offline use one code path.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

from engineer.features.lap_features import WHEEL_SUFFIXES, fit_wear_rate

DRY = (16, 17, 18)                      # visual compounds: soft, medium, hard
COMPOUND_NAMES = {16: "softs", 17: "mediums", 18: "hards", 7: "inters", 8: "wets"}
MIN_STINT_LAPS_FOR_DEG = 5              # shorter stints are mostly "finding the pace" (5-lap races)
MIN_QUADRATIC_LAPS = 15
MIN_QUADRATIC_WEAR = 40.0               # the curve is only fitted once laps reach this wear
OUTLIER_MADS = 3.0


@dataclass(frozen=True)
class Estimate:
    value: float
    weight: float                       # laps of evidence
    sources: tuple[str, ...] = ()

    def describe(self) -> str:
        return ", ".join(self.sources)


def combine(parts: list[tuple[float | None, float, str]]) -> Estimate | None:
    """Weighted average of (value, weight, source) estimates, ignoring unknown or weightless ones."""
    ok = [(v, w, s) for v, w, s in parts
          if v is not None and not math.isnan(v) and w > 0]
    if not ok:
        return None
    total = sum(w for _, w, _ in ok)
    return Estimate(sum(v * w for v, w, _ in ok) / total, total, tuple(s for _, _, s in ok))


@dataclass(frozen=True)
class CompoundModel:
    compound: int                       # visual compound
    wear_rate: Estimate                 # % per lap, worst wheel
    deg: Estimate                       # seconds per % of wear
    deg2: float                         # seconds per %^2 (0 until the data shows a curve)
    offset: Estimate                    # seconds per lap vs the reference compound

    def lap_cost(self, wear: np.ndarray) -> np.ndarray:
        """Seconds per lap from the tyre alone, at these end-of-lap wear levels."""
        return self.offset.value + self.deg.value * wear + self.deg2 * wear ** 2


@dataclass(frozen=True)
class StrategyModel:
    compounds: dict[int, CompoundModel]
    pit_loss: Estimate                  # seconds, green flag
    pit_loss_sc: Estimate               # seconds, under safety car / VSC
    wear_ratio: Estimate | None         # your wear rate / the AI's (how hard you are on tyres)
    notes: tuple[str, ...] = field(default=())


# -- game estimates from the Tyre Sets packet --------------------------------------------------

@dataclass(frozen=True)
class TyreSet:
    compound: int                       # visual
    wear: float                         # %
    usable_life: int | None = None      # game's recommended max laps for the compound
    lap_delta_s: float | None = None    # game's lap time vs the fitted set
    fitted: bool = False


@dataclass(frozen=True)
class GameTyreInfo:
    """What the game itself thinks, read from the player's Tyre Sets packet."""
    deg: dict[int, float]               # s per % wear (fitted compound only)
    offsets: dict[int, float]           # s per lap vs the reference compound
    usable_life: dict[int, int]         # laps


def game_tyre_info(sets: list[TyreSet], ref: int = 17) -> GameTyreInfo:
    deg, offsets, life = {}, {}, {}
    for s in sets:
        if s.usable_life:
            life.setdefault(s.compound, s.usable_life)
    fitted = next((s for s in sets if s.fitted), None)
    if fitted is not None and fitted.wear >= 5:
        same = [s for s in sets if s.compound == fitted.compound and not s.fitted
                and s.lap_delta_s is not None and s.wear < fitted.wear - 4]
        if same:
            fresh = min(same, key=lambda s: s.wear)
            d = -fresh.lap_delta_s / (fitted.wear - fresh.wear)
            if d > 0:
                deg[fitted.compound] = d
    # Offsets between compounds from their new sets (all deltas are vs the same fitted set). Sets
    # you can't use any more still carry the game's estimate, so they count too.
    fresh = {}
    for s in sets:
        if s.lap_delta_s is None or s.fitted or s.wear > 0 or s.compound not in DRY:
            continue
        if s.compound not in fresh or s.wear < fresh[s.compound].wear:
            fresh[s.compound] = s
    if ref in fresh:
        for c, s in fresh.items():
            offsets[c] = s.lap_delta_s - fresh[ref].lap_delta_s
    return GameTyreInfo(deg, offsets, life)


# -- helpers over lap rows ---------------------------------------------------------------------

def _wear(r: dict) -> tuple:
    return tuple(math.nan if r.get(f"wear_{w}") is None else float(r[f"wear_{w}"])
                 for w in WHEEL_SUFFIXES)


def _worst(r: dict) -> float:
    w = [v for v in _wear(r) if not math.isnan(v)]
    return max(w) if w else math.nan


def _green(r: dict) -> bool:
    """A lap that shows the tyre's real pace: clean (no pit, lap 1, invalid) and no safety car."""
    return (bool(r.get("clean")) and not r.get("safety_car") and r.get("fuel_kg") is not None
            and r.get("visual_compound") in DRY and not math.isnan(_worst(r)))


def _stints(rows: list[dict]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["session_uid"], r["car_idx"], r["stint"])].append(r)
    for g in groups.values():
        g.sort(key=lambda r: r["lap"])
    return groups


def stint_wear_rate(laps: list[dict]) -> float:
    """Worst-wheel wear per lap of one stint, with the same fit the tyre calls use."""
    ns = [SimpleNamespace(pit_in_lap=bool(r.get("pit_in_lap")), stint_lap=r["stint_lap"],
                          wear=_wear(r)) for r in laps]
    rate = fit_wear_rate(ns, last_n=len(ns))
    return float(np.nanmax(rate)) if not np.isnan(rate).all() else math.nan


def _wear_rates(rows: list[dict]) -> dict[tuple[bool, int], list[tuple[float, int]]]:
    """(is_player, compound) -> [(rate, laps)] over stints with a measurable rate."""
    out: dict[tuple[bool, int], list[tuple[float, int]]] = defaultdict(list)
    for laps in _stints(rows).values():
        c = laps[0].get("visual_compound")
        if c not in DRY:
            continue
        on_set = [r for r in laps if not r.get("pit_in_lap")]
        if len(on_set) < 2:
            continue
        rate = stint_wear_rate(laps)
        if not math.isnan(rate) and rate >= 0:
            out[(bool(laps[0].get("is_player")), c)].append((rate, len(on_set) - 1))
    return out


def _weighted(vals: list[tuple[float, int]]) -> tuple[float, int]:
    n = sum(w for _, w in vals)
    return (sum(v * w for v, w in vals) / n, n) if n else (math.nan, 0)


def fit_deg(rows: list[dict], fuel_s_per_kg: float,
            quadratic: bool = False) -> tuple[float, float, int, float]:
    """(deg s/%, deg2 s/%^2, laps used, standard error of deg) from green laps, each stint with
    its own baseline. Mistake laps (residual beyond 3 MADs) are dropped and the fit repeated.

    The standard error says how sure the fit is: a few noisy laps over a small wear range give a
    large one, a long clean stint a small one. build_model weighs the fit by it."""
    nan = (math.nan, 0.0, 0, math.inf)
    xs, ys, gs = [], [], []
    for key, laps in _stints(rows).items():
        g = [r for r in laps if _green(r)]
        if len(g) < MIN_STINT_LAPS_FOR_DEG:
            continue
        for r in g:
            xs.append(_worst(r))
            ys.append(r["lap_time_ms"] / 1000.0 - fuel_s_per_kg * r["fuel_kg"])
            gs.append(key)
    if len(xs) < MIN_STINT_LAPS_FOR_DEG:
        return nan
    x, y = np.array(xs), np.array(ys)
    groups = {k: i for i, k in enumerate(dict.fromkeys(gs))}
    gi = np.array([groups[k] for k in gs])
    use_q = quadratic and len(x) >= MIN_QUADRATIC_LAPS and x.max() >= MIN_QUADRATIC_WEAR
    cols = [x, x ** 2] if use_q else [x]
    keep = np.ones(len(x), dtype=bool)
    for _ in range(3):
        X = np.column_stack([_demean_all(c, gi, keep) for c in cols])
        Y = _demean_all(y, gi, keep)
        if not np.any(X[keep]):
            return nan
        coef, *_ = np.linalg.lstsq(X[keep], Y[keep], rcond=None)
        resid = Y - X @ coef
        mad = np.median(np.abs(resid[keep])) * 1.4826
        new = np.abs(resid) <= max(OUTLIER_MADS * mad, 1e-9)
        if (new == keep).all():
            break
        keep = new
    n = int(keep.sum())
    dof = n - len(groups) - len(cols)
    if dof < 1:
        return nan
    s2 = float(resid[keep] @ resid[keep]) / dof
    cov = s2 * np.linalg.pinv(X[keep].T @ X[keep])
    se = math.sqrt(max(cov[0, 0], 1e-12))
    b, b2 = float(coef[0]), float(coef[1]) if use_q else 0.0
    if b2 < 0:                       # only an accelerating drop-off is believable; refit linear
        return fit_deg(rows, fuel_s_per_kg, quadratic=False)
    return b, b2, n, se


def _demean_all(v: np.ndarray, g: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Demean every point using the group means of the kept points (so outliers are scored)."""
    sums = np.bincount(g[keep], weights=v[keep], minlength=g.max() + 1)
    cnt = np.bincount(g[keep], minlength=g.max() + 1)
    return v - (sums / np.where(cnt > 0, cnt, 1))[g]


def fit_offsets(rows: list[dict], deg: dict[int, float], fuel_s_per_kg: float,
                ref: int = 17) -> dict[int, tuple[float, float]]:
    """compound -> (seconds vs ref, laps): the same car in the same race on two compounds,
    each lap corrected for fuel and wear. Laps weigh by the smaller of the two samples."""
    by_car: dict[tuple, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        c = r.get("visual_compound")
        if not _green(r) or c not in deg:
            continue
        y = r["lap_time_ms"] / 1000.0 - fuel_s_per_kg * r["fuel_kg"] - deg[c] * _worst(r)
        by_car[(r["session_uid"], r["car_idx"])][c].append(y)
    diffs: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for per in by_car.values():
        if ref not in per:
            continue
        base = float(np.median(per[ref]))
        for c, ys in per.items():
            if c != ref:
                diffs[c].append((float(np.median(ys)) - base, min(len(ys), len(per[ref]))))
    return {c: _weighted(v) for c, v in diffs.items()}


def measure_pit_losses(rows: list[dict]) -> tuple[list[float], list[float]]:
    """(green losses, safety car losses) in seconds, one per pit stop found in the rows.

    loss = in-lap + out-lap - what the car would have done on those two laps:
    - green: the median of its own green laps within 4 laps either side
    - safety car: the median lap of the cars that didn't pit, on the same two lap numbers
    """
    green, sc = [], []
    sessions: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sessions[r["session_uid"]].append(r)
    for srows in sessions.values():
        by_car: dict[int, dict[int, dict]] = defaultdict(dict)
        for r in srows:
            by_car[r["car_idx"]][r["lap"]] = r
        pitting = defaultdict(set)              # lap -> cars on an in/out lap
        for car, laps in by_car.items():
            for lap, r in laps.items():
                if r.get("pit_in_lap") or r.get("pit_out_lap"):
                    pitting[lap].add(car)
        for car, laps in by_car.items():
            for lap, r in laps.items():
                out = laps.get(lap + 1)
                if not r.get("pit_in_lap") or out is None or not out.get("pit_out_lap"):
                    continue
                if r.get("race_start_lap"):
                    continue
                actual = (r["lap_time_ms"] + out["lap_time_ms"]) / 1000.0
                if r.get("safety_car") or out.get("safety_car"):
                    ref = []
                    for l in (lap, lap + 1):
                        others = [o["lap_time_ms"] for c, o2 in by_car.items()
                                  if c not in pitting[l] and (o := o2.get(l)) is not None
                                  and not o.get("invalid")]
                        if len(others) >= 3:
                            ref.append(float(np.median(others)) / 1000.0)
                    if len(ref) == 2:
                        sc.append(actual - sum(ref))
                else:
                    near = [laps[l]["lap_time_ms"] / 1000.0 for l in range(lap - 4, lap + 6)
                            if l in laps and l not in (lap, lap + 1) and _green(laps[l])]
                    if len(near) >= 2:
                        green.append(actual - 2 * float(np.median(near)))
    return green, sc


# -- building the model ------------------------------------------------------------------------

@dataclass
class ModelSettings:
    fuel_s_per_kg: float = 0.03
    ai_weight: float = 0.5
    game_prior_laps: float = 5.0
    game_deg_sd: float = 0.02
    fallback_deg_s_per_pct: float = 0.05
    pit_loss_s: float = 22.0
    sc_pit_loss_factor: float = 0.5
    ref_compound: int = 17


def build_model(track_rows: list[dict], all_rows: list[dict], current_stint: list[dict],
                game: GameTyreInfo | None, cfg: ModelSettings) -> StrategyModel:
    """track_rows: every lap at this track (history + this session, all cars).
    all_rows: every lap at every track (for how hard you are on tyres compared with the AI).
    current_stint: your laps on the current set (the freshest wear evidence)."""
    game = game or GameTyreInfo({}, {}, {})
    aw, gw = cfg.ai_weight, cfg.game_prior_laps
    notes = []

    # How much faster you wear tyres than the AI: every compound at every track where both ran.
    ratio_parts = []
    by_track: dict[object, list[dict]] = defaultdict(list)
    for r in all_rows:
        by_track[r.get("track_id")].append(r)
    for rows in by_track.values():
        rates = _wear_rates(rows)
        for c in DRY:
            mine, ai = _weighted(rates.get((True, c), [])), _weighted(rates.get((False, c), []))
            if mine[1] and ai[1] and ai[0] > 0:
                ratio_parts.append((mine[0] / ai[0], min(mine[1], ai[1]), "your vs AI wear"))
    wear_ratio = combine(ratio_parts)
    ratio = wear_ratio.value if wear_ratio else 1.0

    # Wear rates per compound.
    rates = _wear_rates(track_rows)
    live_rate = stint_wear_rate(current_stint) if len(current_stint) >= 2 else math.nan
    live_c = current_stint[-1].get("visual_compound") if current_stint else None
    live_key = (current_stint[-1]["session_uid"], current_stint[-1]["stint"]) \
        if current_stint else None
    wear: dict[int, Estimate] = {}
    for c in DRY:
        # Your past stints (the current stint is counted separately, below).
        past = rates.get((True, c), [])
        if live_key is not None and c == live_c:
            past = _without_current(track_rows, c, live_key)
        mine, n_mine = _weighted(past)
        ai_stints = rates.get((False, c), [])
        ai, n_ai = _weighted(ai_stints)
        # The whole AI field counts like one driver's laps (times ai_weight): 20 cars running
        # the same compound tell us about the compound, not 20 times as much about your wear.
        ai_eff = n_ai / len(ai_stints) * aw if ai_stints else 0.0
        parts = [(mine, n_mine, f"your stints ({n_mine} laps)"),
                 (ai * ratio if n_ai else None, ai_eff, f"AI x{ratio:.1f} ({n_ai} laps)")]
        if c == live_c and not math.isnan(live_rate):
            parts.append((live_rate, len(current_stint) - 1, "this stint"))
        est = combine(parts)
        if est is not None:
            wear[c] = est
    # Compounds with no data: scale from one with data by the game's tyre life figures.
    for c in DRY:
        if c in wear or c not in game.usable_life:
            continue
        refs = [(k, e) for k, e in wear.items() if k in game.usable_life]
        if refs:
            k, e = max(refs, key=lambda ke: ke[1].weight)
            wear[c] = Estimate(e.value * game.usable_life[k] / game.usable_life[c], gw,
                               (f"game tyre life vs {COMPOUND_NAMES[k]}",))

    # Deg per compound.
    deg: dict[int, Estimate] = {}
    deg2: dict[int, float] = {}
    # Weighed by precision (1 / standard error^2): the game's figure counts as an estimate good to
    # +-game_deg_sd, your fit as good as its own standard error.
    mine_rows = [r for r in track_rows if r.get("is_player")]
    game_any = float(np.mean(list(game.deg.values()))) if game.deg else None
    for c in DRY:
        mrows = [r for r in mine_rows if r.get("visual_compound") == c]
        b_m, b2_m, n_m, se_m = fit_deg(mrows, cfg.fuel_s_per_kg, quadratic=True)
        if c in game.deg:
            game_part = (game.deg[c], 1 / cfg.game_deg_sd ** 2, "game")
        else:                       # the game's figure for another compound, as a weaker hint
            game_part = (game_any, 1 / (2 * cfg.game_deg_sd) ** 2, "game (other compound)")
        est = combine([(b_m, 1 / se_m ** 2, f"your laps ({n_m})"), game_part])
        if est is not None:
            deg[c] = Estimate(max(0.0, est.value), est.weight, est.sources)
            deg2[c] = b2_m if n_m >= MIN_QUADRATIC_LAPS else 0.0
    for c in DRY:
        if c not in deg:
            known = [e.value for e in deg.values()]
            if known:
                deg[c] = Estimate(float(np.mean(known)), 0.0, ("other compounds",))
            else:
                deg[c] = Estimate(cfg.fallback_deg_s_per_pct, 0.0, ("no data yet: default",))
                notes.append("no tyre pace data yet")
            deg2[c] = 0.0

    # Compound offsets.
    ref = cfg.ref_compound
    fitted = fit_offsets(track_rows, {c: e.value for c, e in deg.items()}, cfg.fuel_s_per_kg, ref)
    offsets: dict[int, Estimate] = {}
    for c in DRY:
        if c == ref:
            offsets[c] = Estimate(0.0, math.inf, ("reference",))
            continue
        v, n = fitted.get(c, (math.nan, 0))
        est = combine([(v, n, f"laps on both ({n})"), (game.offsets.get(c), gw, "game")])
        offsets[c] = est or Estimate(0.0, 0.0, ("not measured yet",))

    # Pit loss.
    g_losses, sc_losses = measure_pit_losses(track_rows)
    g = combine([(float(np.median(g_losses)) if g_losses else None, len(g_losses),
                  f"{len(g_losses)} stops measured"), (cfg.pit_loss_s, 1.0, "default")])
    sc_measured = float(np.median(sc_losses)) if sc_losses else None
    sc = combine([(sc_measured, len(sc_losses), f"{len(sc_losses)} SC stops measured"),
                  (g.value * cfg.sc_pit_loss_factor, 1.0, "share of green loss")])

    compounds = {c: CompoundModel(c, wear[c], deg[c], deg2[c], offsets[c])
                 for c in DRY if c in wear}
    return StrategyModel(compounds, g, sc, wear_ratio, tuple(notes))


def _without_current(track_rows: list[dict], c: int, live_key: tuple) -> list[tuple[float, int]]:
    rows = [r for r in track_rows if r.get("is_player")
            and (r["session_uid"], r["stint"]) != live_key]
    return _wear_rates(rows).get((True, c), [])
