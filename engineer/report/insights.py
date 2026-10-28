"""Turns the cached per-recording analyses into the driver report: track ranking, corner types,
best and worst corners, and the habits ("themes") that cost you time.

Everything is learned from the recordings, nothing is fixed per track: every session adds evidence,
and newer sessions count for more (weight halves every HALF_LIFE_DAYS), so the picture follows you
as you improve. Each number carries how much evidence is behind it.

All deltas are "you minus the quickest AI" through the same corner in the same session:
negative time = you're quicker; positive distance = you do it further down the road (later).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

HALF_LIFE_DAYS = 30.0
SLOW_KPH, FAST_KPH = 120.0, 180.0     # corner type by the reference's minimum speed
STRAIGHT_M = 400.0                    # a corner "onto a straight" has this much road before the next
MIN_THEME_COST_S = 0.03               # themes below this (per lap) aren't worth a card


def confidence(laps: float) -> str:
    return "early read" if laps < 4 else "building" if laps < 12 else "solid"


@dataclass
class CornerAgg:
    """One corner of one track, combined over every session there."""
    track: str
    track_id: int
    number: int
    laps: int = 0
    weight: float = 0.0
    ref_time: float = 0.0
    ref_min_kph: float = 0.0
    straight_after_m: float = 0.0
    d: dict = field(default_factory=dict)        # weighted mean of (mine - ref) per field
    ramp_me: float | None = None                 # metres from first throttle to full throttle
    ramp_ref: float | None = None
    spread_s: float = 0.0                        # your median minus your best through it
    brake_spread_m: float | None = None          # lap-to-lap spread of your braking point
    sessions: list = field(default_factory=list)  # (recorded_at, delta_time)

    @property
    def label(self) -> str:
        return f"{self.track} T{self.number}"

    @property
    def kind(self) -> str:
        return "slow" if self.ref_min_kph < SLOW_KPH else \
            "medium" if self.ref_min_kph < FAST_KPH else "fast"

    @property
    def onto_straight(self) -> bool:
        return self.straight_after_m >= STRAIGHT_M

    @property
    def type_name(self) -> str:
        k = {"slow": "Slow corners", "medium": "Medium-speed corners",
             "fast": "Fast corners"}[self.kind]
        return f"{k} onto a straight" if self.onto_straight else f"{k} into the next corner"

    @property
    def short_type(self) -> str:
        return f"{self.kind.capitalize()}, {'onto a straight' if self.onto_straight else 'into a corner'}"

    @property
    def pct(self) -> float:
        return 100.0 * self.d["time_s"] / self.ref_time if self.ref_time else 0.0

    def dv(self, k: str) -> float:
        v = self.d.get(k)
        return 0.0 if v is None or math.isnan(v) else v


DELTA_FIELDS = ("time_s", "in_s", "out_s", "carry_s", "entry_kph", "brake_m", "brake_end_m",
                "min_kph", "min_m", "pickup_m", "full_m", "exit_kph", "trail_m", "coast_m")


def _median(vals):
    vals = [v for v in vals if v is not None]
    return float(np.median(vals)) if vals else None


def recency_weight(recorded_at: str, now: datetime) -> float:
    age = (now - datetime.fromisoformat(recorded_at)).total_seconds() / 86400
    return 0.5 ** (max(0.0, age) / HALF_LIFE_DAYS)


def _ramp(run: dict) -> float | None:
    if run.get("full_m") is None or run.get("pickup_m") is None:
        return None
    return run["full_m"] - run["pickup_m"]


def combine_corners(entries: list[dict], now: datetime) -> list[CornerAgg]:
    """Per corner: weighted means over sessions (weight = your laps there x recency)."""
    acc: dict[tuple, dict] = {}
    for e in entries:
        w_s = recency_weight(e["recorded_at"], now)
        for c in e["corners"]:
            mine, ref = c["mine"], c["ref"]
            med = {k: _median([r[k] for r in mine]) for k in DELTA_FIELDS}
            w = w_s * len(mine)
            a = acc.setdefault((e["track_id"], c["number"]), {
                "track": e["track"], "w": 0.0, "laps": 0, "sum": {}, "wsum": {},
                "ref_time": 0.0, "ref_min": 0.0, "straight": 0.0, "ramp_me": [], "ramp_ref": [],
                "spread": 0.0, "bspread": [], "sessions": []})
            a["w"] += w
            a["laps"] += len(mine)
            for k in DELTA_FIELDS:
                if med[k] is not None and ref.get(k) is not None:
                    a["sum"][k] = a["sum"].get(k, 0.0) + w * (med[k] - ref[k])
                    a["wsum"][k] = a["wsum"].get(k, 0.0) + w
            a["ref_time"] += w * ref["time_s"]
            a["ref_min"] += w * ref["min_kph"]
            a["straight"] += w * c["straight_after_m"]
            times = [r["time_s"] for r in mine]
            a["spread"] += w * (float(np.median(times)) - min(times))
            ramps = [x for x in (_ramp(r) for r in mine) if x is not None]
            if ramps and _ramp(ref) is not None:
                a["ramp_me"].append((w, float(np.median(ramps))))
                a["ramp_ref"].append((w, _ramp(ref)))
            brakes = [r["brake_m"] for r in mine if r["brake_m"] is not None]
            if len(brakes) >= 3:
                a["bspread"].append((w, float(np.std(brakes))))
            a["sessions"].append((e["recorded_at"], med["time_s"] - ref["time_s"]))
    out = []
    for (tid, n), a in acc.items():
        if a["w"] <= 0:
            continue
        wmean = lambda pairs: sum(w * v for w, v in pairs) / sum(w for w, _ in pairs) \
            if pairs else None
        out.append(CornerAgg(
            track=a["track"], track_id=tid, number=n, laps=a["laps"], weight=a["w"],
            ref_time=a["ref_time"] / a["w"], ref_min_kph=a["ref_min"] / a["w"],
            straight_after_m=a["straight"] / a["w"],
            d={k: a["sum"][k] / a["wsum"][k] for k in a["sum"]},
            ramp_me=wmean(a["ramp_me"]), ramp_ref=wmean(a["ramp_ref"]),
            spread_s=a["spread"] / a["w"], brake_spread_m=wmean(a["bspread"]),
            sessions=sorted(a["sessions"])))
    return out


# -- tracks -------------------------------------------------------------------------------------

@dataclass
class TrackPace:
    track: str
    track_id: int
    sessions: int
    laps: int
    pct: float                  # your median clean lap vs the quickest AI's, % (negative = quicker)
    gap_s: float
    rank: float                 # your pace rank in the field (median over sessions)
    cars: int
    corner_gap_s: float         # the part of gap_s found in the measured corners
    best_lap_ms: int

    @property
    def confidence(self) -> str:
        return confidence(self.laps)


def track_pace(entries: list[dict], corners: list[CornerAgg], now: datetime) -> list[TrackPace]:
    by: dict[int, list[dict]] = {}
    for e in entries:
        by.setdefault(e["track_id"], []).append(e)
    out = []
    for tid, es in by.items():
        ws = [recency_weight(e["recorded_at"], now) * len(e["my_laps"]) for e in es]
        pct = sum(w * 100 * (e["my_median_ms"] - e["top_ai_median_ms"]) / e["top_ai_median_ms"]
                  for w, e in zip(ws, es)) / sum(ws)
        gap = sum(w * (e["my_median_ms"] - e["top_ai_median_ms"]) / 1000
                  for w, e in zip(ws, es)) / sum(ws)
        out.append(TrackPace(
            track=es[0]["track"], track_id=tid, sessions=len(es),
            laps=sum(len(e["my_laps"]) for e in es), pct=pct, gap_s=gap,
            rank=float(np.median([e["pace_rank"] for e in es])), cars=max(e["cars"] for e in es),
            corner_gap_s=sum(c.dv("time_s") for c in corners if c.track_id == tid),
            best_lap_ms=min(min(e["my_laps"]) for e in es)))
    return sorted(out, key=lambda t: t.pct)


# -- corner types -------------------------------------------------------------------------------

@dataclass
class TypeScore:
    name: str
    corners: int
    laps: int
    pct: float          # your time vs the quickest AI through these corners, %
    relative: float     # vs your average corner (negative = a strength)
    examples: list


def corner_types(corners: list[CornerAgg]) -> list[TypeScore]:
    if not corners:
        return []
    overall = sum(c.weight * c.pct for c in corners) / sum(c.weight for c in corners)
    groups: dict[str, list[CornerAgg]] = {}
    for c in corners:
        groups.setdefault(c.type_name, []).append(c)
    out = []
    for name, cs in groups.items():
        pct = sum(c.weight * c.pct for c in cs) / sum(c.weight for c in cs)
        out.append(TypeScore(name, len(cs), sum(c.laps for c in cs), pct, pct - overall,
                             [c.label for c in sorted(cs, key=lambda c: c.pct)]))
    return sorted(out, key=lambda t: t.relative)


# -- themes: why you lose time ------------------------------------------------------------------

@dataclass
class Theme:
    key: str
    title: str
    cost_s: float               # estimated time per lap (averaged over your tracks)
    corners: list               # (label, cost) worst first
    detail: str                 # what the numbers say
    advice: str
    laps: int
    trend: list = field(default_factory=list)   # (recorded_at, cost) per session

    @property
    def confidence(self) -> str:
        return confidence(self.laps)


def _per_lap(selected: list[tuple[CornerAgg, float]], corners: list[CornerAgg]) -> float:
    """Sum the costs per track (they add up over a lap), then average the tracks by laps."""
    laps = {}
    for c in corners:
        laps[c.track_id] = max(laps.get(c.track_id, 0), c.laps)
    total = sum(laps.values())
    if not total:
        return 0.0
    per_track: dict[int, float] = {}
    for c, cost in selected:
        per_track[c.track_id] = per_track.get(c.track_id, 0.0) + cost
    return sum(per_track.get(t, 0.0) * n for t, n in laps.items()) / total


def _avg(cs: list[CornerAgg], fn) -> float:
    vals = [fn(c) for c in cs]
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else 0.0


def _m(v: float) -> str:
    return f"{abs(v):.0f} m"


def _theme_exit_giveback(cs):
    sel = [c for c in cs if c.kind != "slow" and c.dv("in_s") <= -0.02 and c.dv("out_s") >= 0.02]
    costs = [(c, c.dv("out_s") + max(0.0, c.dv("carry_s"))) for c in sel]
    if not sel:
        return None, costs
    brake, release = _avg(sel, lambda c: c.dv("brake_m")), _avg(sel, lambda c: c.dv("brake_end_m"))
    trail, apex = _avg(sel, lambda c: c.dv("trail_m")), _avg(sel, lambda c: c.dv("min_m"))
    full = _avg(sel, lambda c: c.d.get("full_m"))
    bits = [f"you gain {abs(_avg(sel, lambda c: c.dv('in_s'))):.2f} s per corner on the way in "
            f"but give back {_avg(sel, lambda c: c.dv('out_s')):.2f} s from the apex to the exit"]
    if brake > 2:
        bits.append(f"you brake {_m(brake)} later than the quickest AI")
    if release < -2:
        bits.append(f"come off the brake {_m(release)} sooner")
    if trail < -2:
        bits.append(f"brake {_m(trail)} less while turning in")
    if apex > 5:
        bits.append(f"your slowest point is {_m(apex)} later")
    if full > 5:
        bits.append(f"full throttle comes {_m(full)} later")
    return ("Winning the entry, losing the exit",
            "In medium and fast corners " + ", ".join(bits) + ".",
            "Brake a touch earlier and keep light brake pressure as you turn in so the car "
            "rotates; be slowest earlier and get to full throttle sooner. Hairpins are the "
            "exception: there your late apex works."), costs


def _theme_slow_throttle(cs):
    sel = [c for c in cs if c.ramp_me is not None and c.ramp_ref
           and c.ramp_me - c.ramp_ref >= 20 and c.ramp_me >= 1.4 * c.ramp_ref]
    costs = [(c, max(0.0, c.dv("out_s")) + max(0.0, c.dv("carry_s"))) for c in sel]
    if not sel:
        return None, costs
    return ("Slow squeeze to full throttle",
            f"Once you start applying the throttle you take {_avg(sel, lambda c: c.ramp_me):.0f} m "
            f"to reach full, the quickest AI {_avg(sel, lambda c: c.ramp_ref):.0f} m. This is the "
            "build-up, not lifting: it's the time between first touch and flat out.",
            "Commit sooner once the car is pointing straight: a quicker, smooth squeeze rather "
            "than a long gradual one. Usually fades as a track gets familiar."), costs


def _theme_early_braking(cs):
    sel = [c for c in cs if c.dv("brake_m") <= -8 and c.dv("in_s") >= 0.02]
    costs = [(c, c.dv("in_s")) for c in sel]
    if not sel:
        return None, costs
    return ("Braking too early",
            f"You start braking {_m(_avg(sel, lambda c: c.dv('brake_m')))} before the quickest "
            f"AI and lose {_avg(sel, lambda c: c.dv('in_s')):.2f} s on the way to the apex.",
            "Move the braking point a few metres later each lap until the corner stops getting "
            "quicker."), costs


def _theme_over_slowing(cs):
    sel = [c for c in cs if c.dv("min_kph") <= -4 and c.dv("time_s") >= 0.02]
    costs = [(c, c.dv("time_s")) for c in sel]
    if not sel:
        return None, costs
    return ("Over-slowing the apex",
            f"Your minimum speed is {abs(_avg(sel, lambda c: c.dv('min_kph'))):.0f} kph lower "
            "than the quickest AI's.",
            "Release the brake earlier and trust more speed to the apex."), costs


def _theme_coasting(cs):
    sel = [c for c in cs if c.dv("coast_m") >= 15 and c.dv("time_s") >= 0.02]
    costs = [(c, c.dv("time_s")) for c in sel]
    if not sel:
        return None, costs
    return ("Coasting between the pedals",
            f"You spend {_avg(sel, lambda c: c.dv('coast_m')):.0f} m more with neither pedal "
            "pressed.",
            "Go straight from brake to throttle: a gap between them is time with the car "
            "doing nothing."), costs


def _theme_straight_exits(cs):
    sel = [c for c in cs if c.onto_straight and c.dv("exit_kph") <= -3 and c.dv("carry_s") >= 0.02]
    costs = [(c, c.dv("carry_s")) for c in sel]
    if not sel:
        return None, costs
    return ("Exit speed onto straights",
            f"You leave these corners {abs(_avg(sel, lambda c: c.dv('exit_kph'))):.0f} kph slower "
            "and keep losing time down the straight after them.",
            "Sacrifice a little entry speed for a clean, early exit: on a straight, exit speed "
            "pays all the way down."), costs


THEMES = {"exit_giveback": _theme_exit_giveback, "slow_throttle": _theme_slow_throttle,
          "early_braking": _theme_early_braking, "over_slowing": _theme_over_slowing,
          "coasting": _theme_coasting, "straight_exits": _theme_straight_exits}


def theme_costs(corners: list[CornerAgg]) -> dict[str, float]:
    """Each theme's cost per lap (0 when it doesn't show): used for trends and 'since last time'."""
    return {k: _per_lap(fn(corners)[1], corners) for k, fn in THEMES.items()}


def themes(corners: list[CornerAgg], entries: list[dict], now: datetime) -> list[Theme]:
    out = []
    for key, fn in THEMES.items():
        text, costs = fn(corners)
        if text is None:
            continue
        cost = _per_lap(costs, corners)
        if cost < MIN_THEME_COST_S:
            continue
        title, detail, advice = text
        trend = []
        for e in sorted(entries, key=lambda e: e["recorded_at"]):
            one = combine_corners([e], now)
            trend.append((e["recorded_at"], _per_lap(fn(one)[1], one)))
        out.append(Theme(key, title, cost, sorted([(c.label, v) for c, v in costs],
                                                  key=lambda x: -x[1]),
                         detail, advice, sum(c.laps for c, _ in costs) // max(1, len(costs)),
                         trend))
    return sorted(out, key=lambda t: -t.cost_s)


# -- strengths ----------------------------------------------------------------------------------

def strengths(corners: list[CornerAgg], types: list[TypeScore]) -> list[str]:
    out = []
    for t in types:
        if t.relative <= -0.5 and t.pct < 0:
            out.append(f"{t.name}: {abs(t.pct):.1f}% quicker than the quickest AI "
                       f"(best: {', '.join(t.examples[:2])}).")
    entry = [c for c in corners if c.dv("in_s") < 0]
    if corners and len(entry) >= 0.7 * len(corners):
        out.append(f"Corner entry: quicker than the quickest AI from braking to apex in "
                   f"{len(entry)} of {len(corners)} corners.")
    spreads = [c.brake_spread_m for c in corners if c.brake_spread_m is not None]
    if spreads and np.median(spreads) <= 6:
        out.append(f"Consistent braking: your braking point varies only about "
                   f"{np.median(spreads):.0f} m from lap to lap.")
    return out


# -- the whole report ---------------------------------------------------------------------------

@dataclass
class Report:
    generated: datetime
    sessions: list[dict]
    skipped: list[dict]
    tracks: list[TrackPace]
    types: list[TypeScore]
    corners: list[CornerAgg]
    themes: list[Theme]
    strengths: list[str]
    overall_pct: float
    ideal_gap_s: float          # per lap: your median vs your best through every corner
    previous: dict | None = None

    @property
    def laps(self) -> int:
        return sum(len(e["my_laps"]) for e in self.sessions)

    def summary(self) -> dict:
        """What the next report compares against ("since last report")."""
        return {"generated": self.generated.isoformat(), "files": [e["file"] for e in self.sessions],
                "overall_pct": self.overall_pct, "themes": theme_costs(self.corners),
                "tracks": {t.track: t.pct for t in self.tracks}}


def build_report(entries: list[dict], now: datetime | None = None,
                 previous: dict | None = None) -> Report:
    now = now or datetime.now(timezone.utc)
    used = sorted([e for e in entries if "skipped" not in e and e.get("corners")],
                  key=lambda e: e["recorded_at"])
    skipped = [e for e in entries if e not in used]
    corners = combine_corners(used, now)
    tracks = track_pace(used, corners, now)
    types = corner_types(corners)
    ws = [t.laps for t in tracks]
    overall = sum(t.pct * w for t, w in zip(tracks, ws)) / sum(ws) if ws else 0.0
    laps = {}
    for c in corners:
        laps[c.track_id] = max(laps.get(c.track_id, 0), c.laps)
    ideal = (sum(sum(c.spread_s for c in corners if c.track_id == t) * n for t, n in laps.items())
             / sum(laps.values())) if laps else 0.0
    return Report(now, used, skipped, tracks, types, corners, themes(corners, used, now),
                  strengths(corners, types), overall, ideal, previous)
