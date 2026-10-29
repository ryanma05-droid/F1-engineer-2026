"""Race simulator: tries every pit plan and adds up its race time.

A plan is a list of stops: "box at the end of lap 11 for hards, then lap 24 for mediums". Its cost
is the time the tyres cost lap by lap (the strategy model's offset + deg x wear for every lap left)
plus the pit loss of each stop. Base pace and fuel are the same for every plan, so they drop out.

Rules a plan must follow:
- only tyre sets you still have (the game's Tyre Sets packet; the freshest set of a compound first)
- in a dry race, at least two different compounds (when the two-compound rule applies)
- the wear limit (`tyres.limit_pct`) is soft: every lap that ends past it costs
  `over_limit_s_per_pct` seconds per % over. Finishing the race 2 % over costs a few seconds (not a
  22 s stop: on the real 18-lap Austria race the mediums ended at 56 % with the model projecting
  ~60 %); running 10 % over for five laps costs far more than stopping.

How it's searched (dynamic programming, so even a 70-lap race takes milliseconds): working
backwards from the flag, for every lap and every combination of sets used so far, keep the cheapest
way to finish. Every plan is covered; nothing is sampled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from engineer.strategy.model import DRY, CompoundModel, StrategyModel


@dataclass(frozen=True)
class Stop:
    lap: int                     # box at the end of this lap
    compound: int                # visual compound fitted
    set_wear: float              # wear of the set fitted (0 = new)


@dataclass(frozen=True)
class Plan:
    stops: tuple[Stop, ...]
    cost_s: float                # tyre time + pit losses for the rest of the race
    window: tuple[int, int] | None = None   # first stop: laps within window_s of the best

    @property
    def n_stops(self) -> int:
        return len(self.stops)

    @property
    def next_stop(self) -> Stop | None:
        return self.stops[0] if self.stops else None

    def key(self) -> tuple:
        """What makes two plans "the same plan" for the radio: stops, compounds, first lap."""
        return (self.n_stops, tuple(s.compound for s in self.stops),
                self.stops[0].lap if self.stops else None)


@dataclass(frozen=True)
class RaceSituation:
    lap_done: int                # last completed lap
    total_laps: int
    compound: int                # fitted now (visual)
    wear: float                  # worst wheel, % (end of the last lap)
    sets: dict[int, list[float]] # compound -> wear of each set still available (not the fitted)
    used: frozenset[int]         # dry compounds already run this race (including the fitted)
    require_two: bool
    limit_pct: float
    pit_loss_s: float
    sc_pit_loss_s: float | None = None   # cheaper stop on these laps (safety car out)
    sc_laps: tuple[int, ...] = ()
    max_stops: int = 3
    window_s: float = 1.0
    over_limit_s_per_pct: float = 1.0
    min_gain_s: float = 3.0      # an extra stop must save at least this much to be the plan
    first_stop_lap: int | None = None   # "what if I box now?": force the first stop on this lap
    first_stop_compound: int | None = None   # ... and onto this compound ("pit for hards")


@dataclass
class SimResult:
    best: Plan | None
    by_stops: dict[int, Plan] = field(default_factory=dict)

    def alternative(self) -> Plan | None:
        """The best plan with a different number of stops than the best one."""
        if self.best is None:
            return None
        others = [p for k, p in self.by_stops.items() if k != self.best.n_stops]
        return min(others, key=lambda p: p.cost_s) if others else None


def stint_costs(cm: CompoundModel, start_wear: float, laps: int, limit: float,
                over_s_per_pct: float) -> np.ndarray:
    """Cumulative tyre cost of 0..laps laps on a set: C[n] = cost of the first n laps."""
    k = np.arange(1, laps + 1)
    wear = start_wear + cm.wear_rate.value * k
    per_lap = cm.lap_cost(wear) + over_s_per_pct * np.clip(wear - limit, 0.0, None)
    return np.concatenate([[0.0], np.cumsum(per_lap)])


def simulate(sit: RaceSituation, model: StrategyModel) -> SimResult:
    R = sit.total_laps - sit.lap_done           # laps still to drive
    if R <= 0 or sit.compound not in model.compounds:
        return SimResult(None)
    comps = [c for c in DRY if c in model.compounds and sit.sets.get(c)]
    sets = {c: sorted(sit.sets[c]) for c in comps}
    curves: dict[tuple[int, int], np.ndarray] = {}     # (compound, set index) -> costs

    def curve(c: int, i: int) -> np.ndarray:
        if (c, i) not in curves:
            curves[(c, i)] = stint_costs(model.compounds[c], sets[c][i], R, sit.limit_pct,
                                         sit.over_limit_s_per_pct)
        return curves[(c, i)]

    cur = stint_costs(model.compounds[sit.compound], sit.wear, R, sit.limit_pct,
                      sit.over_limit_s_per_pct)
    pit = np.full(R + 1, sit.pit_loss_s)
    if sit.sc_pit_loss_s is not None:
        for lap in sit.sc_laps:
            q = lap - sit.lap_done
            if 1 <= q < R:
                pit[q] = sit.sc_pit_loss_s
    # cost matrix helper: M[p, q] = C[q - p] for q > p
    p_idx, q_idx = np.meshgrid(np.arange(R + 1), np.arange(R + 1), indexing="ij")
    diff = q_idx - p_idx

    def best_from(C: np.ndarray, G: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        M = np.where(diff > 0, C[np.clip(diff, 0, R)] + G[None, :], math.inf)
        arg = np.argmin(M, axis=1)
        return M[np.arange(R + 1), arg], arg

    def rule_ok(counts: tuple[int, ...]) -> bool:
        if not sit.require_two:
            return True
        used = set(sit.used) | {c for c, n in zip(comps, counts) if n > 0}
        return len(used & set(DRY)) >= 2

    def solve(k: int):
        V: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
        Gs: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

        def G(counts: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
            """Cost to go from the end of lap q (relative) when stopping there, and which set."""
            if counts in Gs:
                return Gs[counts]
            g = np.full(R + 1, math.inf)
            choice = np.full(R + 1, -1)
            if sum(counts) == k:
                if rule_ok(counts):
                    g[R] = 0.0
            else:
                for ci, c in enumerate(comps):
                    if counts[ci] >= len(sets[c]):
                        continue
                    nxt = counts[:ci] + (counts[ci] + 1,) + counts[ci + 1:]
                    v, _ = Vs(nxt, ci)
                    cand = pit + v
                    cand[R] = math.inf
                    cand[0] = math.inf
                    better = cand < g
                    g[better], choice[better] = cand[better], ci
            Gs[counts] = (g, choice)
            return Gs[counts]

        def Vs(counts: tuple[int, ...], ci: int) -> tuple[np.ndarray, np.ndarray]:
            key = (counts, ci)
            if key not in V:
                c = comps[ci]
                V[key] = best_from(curve(c, counts[ci] - 1), G(counts)[0])
            return V[key]

        zero = (0,) * len(comps)
        g0 = G(zero)[0]
        forced = None
        if sit.first_stop_compound is not None:
            # Only the asked-for compound may follow the first stop.
            if sit.first_stop_compound not in comps or not sets[sit.first_stop_compound]:
                return None
            forced = comps.index(sit.first_stop_compound)
            first = tuple(int(i == forced) for i in range(len(comps)))
            g0 = pit + Vs(first, forced)[0]
            g0[0] = g0[R] = math.inf
        if sit.first_stop_lap is not None:
            # Only the forced lap may end the first stint (no stop at all isn't allowed either).
            keep = np.full(R + 1, math.inf)
            q = sit.first_stop_lap - sit.lap_done
            if 1 <= q < R:
                keep[q] = 0.0
            g0 = g0 + keep
        root_v, root_arg = best_from(cur, g0)
        total = root_v[0]
        if not math.isfinite(total):
            return None
        # Walk the choices forward to read the plan.
        stops, counts, q = [], zero, int(root_arg[0])
        while q < R:
            ci = forced if (forced is not None and counts == zero) else int(G(counts)[1][q])
            counts = counts[:ci] + (counts[ci] + 1,) + counts[ci + 1:]
            c = comps[ci]
            stops.append(Stop(sit.lap_done + q, c, sets[c][counts[ci] - 1]))
            q = int(Vs(counts, ci)[1][q])
        window = None
        if stops:
            # First stop laps whose best continuation (same first compound) is close to the best.
            ci = comps.index(stops[0].compound)
            first = tuple(int(i == ci) for i in range(len(comps)))
            v, _ = Vs(first, ci)
            tot = cur[: R + 1] + pit + v
            tot[0] = tot[R] = math.inf
            ok = tot <= total + sit.window_s + 1e-9
            q0 = stops[0].lap - sit.lap_done
            lo = hi = q0
            while lo - 1 >= 1 and ok[lo - 1]:
                lo -= 1
            while hi + 1 < R and ok[hi + 1]:
                hi += 1
            window = (sit.lap_done + lo, sit.lap_done + hi)
        return Plan(tuple(stops), float(total), window)

    by_stops = {k: p for k in range(0, sit.max_stops + 1) if (p := solve(k)) is not None}
    if not by_stops:
        return SimResult(None)
    # Fewer stops wins unless more stops save at least min_gain_s: a stop that gains a second on
    # paper isn't worth the risk (traffic, a slow stop, the model being a bit off).
    if sit.first_stop_lap is not None:          # a what-if: simply the cheapest way to do it
        return SimResult(min(by_stops.values(), key=lambda p: p.cost_s), by_stops)
    fastest = min(by_stops.values(), key=lambda p: p.cost_s)
    best = min((p for p in by_stops.values() if p.cost_s <= fastest.cost_s + sit.min_gain_s),
               key=lambda p: (p.n_stops, p.cost_s))
    return SimResult(best, by_stops)
