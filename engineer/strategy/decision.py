"""Turning simulated plans into one confident call: which plan, how safe it is, and the gamble.

The simulator (sim.py) finds the cheapest plan for each number of stops and for each tyre you
could fit at the next stop. This picks the one to call:

- Every plan is checked stint by stint: the worst tyre's wear at the end of each stint, from the
  wear rate the model learned, plus an allowance for how sure that rate is (a rate measured over
  two laps is less certain than one over ten). That's the plan's tightest stint.
- A plan is *safe* when even with that allowance every stint ends at least `margin_pct` under the
  wear limit. The call is the fastest safe plan (fewer stops unless more save `min_gain_s`).
- A quicker plan that isn't safe is the *gamble*: called out with what it's worth and how close
  to the limit it runs ("Softs are the gamble: about three seconds quicker, but sixty one percent
  at the flag, you'd have to look after them"). If it's quicker by `gamble_take_s` or more, it
  becomes the call itself, still labelled as tight, with the safe plan named as the fallback.
- If nothing is safe, the fastest plan is the call, labelled tight.

Nothing here is a fixed number for a tyre: rates come from the model (your laps, history, the
game), and the allowance shrinks as evidence builds up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from engineer.strategy.model import StrategyModel
from engineer.strategy.sim import Plan, RaceSituation


@dataclass(frozen=True)
class DecisionSettings:
    margin_pct: float = 5.0          # a safe plan ends every stint this far under the limit
    rate_sd_one_lap: float = 0.3     # wear rate uncertainty with one lap of evidence (relative)
    gamble_min_s: float = 1.0        # a riskier plan quicker by less than this isn't worth naming
    gamble_take_s: float = 5.0       # ... quicker by this much and it becomes the call
    min_gain_s: float = 3.0          # an extra stop must save this much


@dataclass(frozen=True)
class Stint:
    compound: int
    start_lap: int                   # last lap before the stint (lap_done or the stop lap)
    end_lap: int                     # the lap it ends at (a stop, or the flag)
    end_wear: float                  # worst wheel at the end, at the expected rate
    risk_wear: float                 # ... with the uncertainty allowance (one sd)

    @property
    def laps(self) -> int:
        return self.end_lap - self.start_lap


@dataclass(frozen=True)
class Assessed:
    plan: Plan
    stints: tuple[Stint, ...]
    safe: bool

    @property
    def tightest(self) -> Stint:
        return max(self.stints, key=lambda s: s.risk_wear)

    @property
    def last(self) -> Stint:
        return self.stints[-1]


@dataclass(frozen=True)
class Decision:
    call: Assessed                   # what the engineer tells you to do
    gamble: Assessed | None = None   # a quicker, riskier plan worth knowing about
    safe_alternative: Assessed | None = None   # when the call is itself the gamble
    gain_s: float = 0.0              # how much quicker the gamble is (or the call, when taken)

    @property
    def taken_gamble(self) -> bool:
        return not self.call.safe


def _rate(model: StrategyModel, compound: int) -> tuple[float, float]:
    cm = model.compounds.get(compound)
    if cm is None:
        return math.nan, 0.0
    return cm.wear_rate.value, cm.wear_rate.weight


def assess(plan: Plan, sit: RaceSituation, model: StrategyModel,
           cfg: DecisionSettings, limit_pct: float) -> Assessed:
    stints = []
    start, wear0, comp = sit.lap_done, sit.wear, sit.compound
    for end_lap, next_comp, next_wear in [(s.lap, s.compound, s.set_wear) for s in plan.stops] \
            + [(sit.total_laps, None, 0.0)]:
        rate, weight = _rate(model, comp)
        laps = end_lap - start
        if math.isnan(rate):
            end = risk = math.inf
        else:
            end = wear0 + rate * laps
            sd = cfg.rate_sd_one_lap / math.sqrt(max(weight, 1.0))
            risk = end + rate * laps * sd
        stints.append(Stint(comp, start, end_lap, end, risk))
        start, wear0, comp = end_lap, next_wear, next_comp
    safe = all(s.risk_wear <= limit_pct - cfg.margin_pct for s in stints)
    return Assessed(plan, tuple(stints), safe)


def _prefer_fewer_stops(plans: list[Assessed], min_gain_s: float) -> Assessed:
    fastest = min(plans, key=lambda a: a.plan.cost_s)
    return min((a for a in plans if a.plan.cost_s <= fastest.plan.cost_s + min_gain_s),
               key=lambda a: (a.plan.n_stops, a.plan.cost_s))


def decide(candidates: list[Plan], sit: RaceSituation, model: StrategyModel,
           cfg: DecisionSettings, limit_pct: float) -> Decision | None:
    seen, plans = set(), []
    for p in candidates:
        if p is None or p.key() in seen:
            continue
        seen.add(p.key())
        plans.append(assess(p, sit, model, cfg, limit_pct))
    if not plans:
        return None
    safe = [a for a in plans if a.safe]
    fastest = _prefer_fewer_stops(plans, cfg.min_gain_s)
    if not safe:
        return Decision(fastest)
    call = _prefer_fewer_stops(safe, cfg.min_gain_s)
    risky = [a for a in plans if not a.safe]
    if not risky:
        return Decision(call)
    gamble = min(risky, key=lambda a: a.plan.cost_s)
    gain = call.plan.cost_s - gamble.plan.cost_s
    if gain >= cfg.gamble_take_s:
        return Decision(gamble, safe_alternative=call, gain_s=gain)
    if gain >= cfg.gamble_min_s:
        return Decision(call, gamble=gamble, gain_s=gain)
    return Decision(call)


def wear_at(sit: RaceSituation, model: StrategyModel, lap: int) -> float:
    """Expected worst-wheel wear on the current tyres at the end of `lap`."""
    rate, _ = _rate(model, sit.compound)
    return sit.wear + rate * (lap - sit.lap_done) if not math.isnan(rate) else math.nan
