"""Per-lap feature records and stints: the tidy table every later model learns from.

One `LapFeatures` record per car per completed lap: times, flags, tyre compound and age, wear,
damage and blisters per wheel at the end of the lap, tyre temperatures during the lap, fuel, track
conditions. A *stint* is a run of laps on one set of tyres.

The same code runs live (the tyre analyzer reads these records) and offline (`python -m
engineer.features build` replays every recording through it and writes Parquet tables), so a
model trained offline sees exactly the numbers the live engineer sees.

Tyre temperatures
-----------------
We collect temperatures per car, per sector and per wheel while the lap is driven
(`TempAccumulator`). We judge hot/cold on the INNER (carcass) temperature: on real Austria data the
surface temperature swings from ~55 to ~150 C within a single corner, while the carcass moves
smoothly (78-96 C over a 5-lap stint on Softs). "Too hot" means more than `min_fraction` of a
sector's samples above the window, so one hot corner doesn't count.

Unknown, never zero: wear/damage hidden by "Your Telemetry" stay NaN, as in the race state.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from engineer.bus import events as ev
from engineer.ingest.formats import f1_2025 as f
from engineer.state.race_state import N, WHEELS, RaceState

WHEEL_SUFFIXES = tuple(w.lower() for w in WHEELS)       # rl, rr, fl, fr
INTER, WET = 7, 8                                       # visual compounds
WEAR_FIT_LAPS = 8                                       # wear rate = slope over the last 8 laps


def _nan4() -> tuple[float, float, float, float]:
    return (math.nan,) * 4


def _t4(a) -> tuple[float, float, float, float]:
    return tuple(float(x) for x in a)


@dataclass(frozen=True)
class LapTemps:
    """Tyre temperatures over one lap of one car. Sector arrays are (3 sectors, 4 wheels)."""
    inner_avg: np.ndarray            # (3, 4) mean carcass temp per sector
    hot_frac: np.ndarray             # (3, 4) share of samples above the window
    cold_frac: np.ndarray            # (3, 4) share of samples below the window
    surface_avg: np.ndarray          # (4,) over the lap
    surface_max: np.ndarray          # (4,)
    samples: np.ndarray              # (3,) samples per sector
    safety_car: bool                 # SC or VSC out at any point of the lap

    def lap_avg(self, arr: np.ndarray) -> np.ndarray:
        """Lap-wide average of a (3, 4) per-sector array, weighted by samples per sector."""
        n = self.samples
        if n.sum() == 0:
            return np.full(4, np.nan)
        return (arr * n[:, None]).sum(axis=0) / n.sum()


class TempAccumulator:
    """Collects tyre temperatures for every car's lap in progress (numpy, all 22 cars at once).

    The lap a sample belongs to comes from Lap Data. When a car's lap number changes, its finished
    lap is kept in `finished` so the LapCompleted handler can still find it (and at the chequered
    flag the lap number doesn't change, so the lap in progress IS the finished one).
    """

    def __init__(self, window_c: tuple[float, float] = (80.0, 110.0)):
        self.window_c = window_c
        self.reset()

    def reset(self) -> None:
        self.cur_lap = np.full(N, np.nan)
        self.inner_sum = np.zeros((N, 3, 4))
        self.surf_sum = np.zeros((N, 3, 4))
        self.surf_max = np.full((N, 4), np.nan)
        self.hot = np.zeros((N, 3, 4))
        self.cold = np.zeros((N, 3, 4))
        self.count = np.zeros((N, 3))
        self.sc = np.zeros(N, dtype=bool)
        self.finished: dict[tuple[int, int], LapTemps] = {}
        self._rewound = False

    def rewind(self) -> None:
        """After a flashback: on the next Lap Data, drop each car's samples from the sector it
        rewound to onwards (they were driven in the discarded future), keeping earlier sectors."""
        self._rewound = True

    def _clear(self, rows: np.ndarray) -> None:
        self.inner_sum[rows] = 0
        self.surf_sum[rows] = 0
        self.surf_max[rows] = np.nan
        self.hot[rows] = 0
        self.cold[rows] = 0
        self.count[rows] = 0
        self.sc[rows] = False

    def _snapshot(self, i: int) -> LapTemps:
        n = self.count[i]
        safe = np.where(n > 0, n, np.nan)[:, None]
        surf_lap = self.surf_sum[i].sum(axis=0) / n.sum() if n.sum() else np.full(4, np.nan)
        return LapTemps(
            inner_avg=self.inner_sum[i] / safe, hot_frac=self.hot[i] / safe,
            cold_frac=self.cold[i] / safe, surface_avg=surf_lap,
            surface_max=self.surf_max[i].copy(), samples=n.copy(), safety_car=bool(self.sc[i]))

    def on_lap_data(self, state: RaceState) -> None:
        c = state.cars
        known = c.active & ~np.isnan(c.lap)
        if self._rewound:
            self._rewound = False
            same = known & (c.lap == self.cur_lap) & ~np.isnan(c.sector)
            for i in np.flatnonzero(same):
                k = int(c.sector[i])
                for arr in (self.inner_sum, self.surf_sum, self.hot, self.cold):
                    arr[i, k:] = 0
                self.count[i, k:] = 0
            # A car rewound into an earlier lap starts that lap's collection over.
            back = known & (c.lap < self.cur_lap)
            self._clear(back)
            self.cur_lap[back] = c.lap[back]
        changed = known & (c.lap != self.cur_lap)
        for i in np.flatnonzero(changed & ~np.isnan(self.cur_lap)):
            self.finished[(int(i), int(self.cur_lap[i]))] = self._snapshot(int(i))
            # Keep only the last couple of laps per car.
            self.finished.pop((int(i), int(self.cur_lap[i]) - 3), None)
        self._clear(changed)
        self.cur_lap[known] = c.lap[known]

    def on_telemetry(self, state: RaceState) -> None:
        c = state.cars
        ok = c.active & ~np.isnan(self.cur_lap) & ~np.isnan(c.sector)
        if not ok.any():
            return
        idx = np.flatnonzero(ok)
        sec = c.sector[idx].astype(int).clip(0, 2)
        inner = c.tyre_inner_temp_c[idx]
        surf = c.tyre_surface_temp_c[idx]
        lo, hi = self.window_c
        slick = ~np.isin(c.visual_compound[idx], (INTER, WET))[:, None]
        self.inner_sum[idx, sec] += inner
        self.surf_sum[idx, sec] += surf
        self.surf_max[idx] = np.fmax(self.surf_max[idx], surf)
        # Only slicks are judged hot/cold: inters and wets work in a different window.
        self.hot[idx, sec] += (inner > hi) & slick
        self.cold[idx, sec] += (inner < lo) & slick
        self.count[idx, sec] += 1
        if state.session.safety_car_status in (1, 2):
            self.sc[idx] = True

    def temps_for(self, car: int, lap: int) -> LapTemps | None:
        if self.cur_lap[car] == lap:
            return self._snapshot(car)
        return self.finished.get((car, lap))


@dataclass(frozen=True)
class LapFeatures:
    session_uid: int
    track_id: int | None
    session_type: int | None
    car_idx: int
    is_player: bool
    driver: str | None
    lap: int
    stint: int                       # 1, 2, ... per car
    stint_lap: int                   # laps completed in this stint, including this one
    lap_time_ms: int
    sector1_ms: int | None
    sector2_ms: int | None
    sector3_ms: int | None
    invalid: bool
    pit_in_lap: bool
    pit_out_lap: bool
    race_start_lap: bool
    clean: bool
    safety_car: bool
    position: int | None
    visual_compound: int | None
    actual_compound: int | None
    tyre_age_laps: int | None
    wear: tuple                      # per wheel RL, RR, FL, FR, at the end of the lap (%)
    damage: tuple
    blisters: tuple
    inner_avg: tuple                 # carcass temperature, lap average
    surface_avg: tuple
    surface_max: tuple
    hot_frac: tuple                  # share of the lap above / below the temperature window
    cold_frac: tuple
    fuel_kg: float                   # in the tank at the end of the lap
    track_temp_c: int | None
    air_temp_c: int | None
    weather: int | None
    session_time: float
    temps: LapTemps | None = field(default=None, compare=False, repr=False)  # per-sector detail

    @property
    def worst_wheel(self) -> int | None:
        w = np.array(self.wear)
        return None if np.isnan(w).all() else int(np.nanargmax(w))

    def to_row(self) -> dict:
        """Flat dict for a table row: per-wheel tuples become wear_rl, wear_rr, ... columns."""
        row = asdict(self)
        row.pop("temps")
        for key in ("wear", "damage", "blisters", "inner_avg", "surface_avg", "surface_max",
                    "hot_frac", "cold_frac"):
            for suffix, v in zip(WHEEL_SUFFIXES, row.pop(key)):
                row[f"{key}_{suffix}"] = None if math.isnan(v) else float(v)
        row["session_uid"] = f"{self.session_uid:016x}"
        if isinstance(row["fuel_kg"], float) and math.isnan(row["fuel_kg"]):
            row["fuel_kg"] = None
        return row


def _opt_int(x: float) -> int | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else int(x)


class LapFeatureBuilder:
    """Builds a LapFeatures record for every car's completed lap.

    Wiring: construct after LapHistory (it reads nothing from it, but radio analyzers that read
    both expect the lap stored in both) and add `after_packet` to `Pipeline.after_packet`.
    `listeners` are called with each new record (the offline table builder uses this).
    """

    def __init__(self, bus: ev.EventBus, state: RaceState,
                 temp_window_c: tuple[float, float] = (80.0, 110.0)):
        self.state = state
        self.temps = TempAccumulator(temp_window_c)
        self.laps: dict[int, list[LapFeatures]] = {}
        self.listeners: list = []
        bus.subscribe(ev.LapCompleted, self.on_lap)
        bus.subscribe(ev.SessionStarted, self._on_session_started)
        bus.subscribe(ev.Flashback, self._on_flashback)

    def after_packet(self, packet_id: int | None) -> None:
        if packet_id == f.PacketId.LAP_DATA:
            self.temps.on_lap_data(self.state)
        elif packet_id == f.PacketId.CAR_TELEMETRY:
            self.temps.on_telemetry(self.state)

    def _on_session_started(self, _e) -> None:
        self.laps.clear()
        self.temps.reset()

    def _on_flashback(self, e: ev.Flashback) -> None:
        for car, laps in self.laps.items():
            self.laps[car] = [r for r in laps if r.session_time <= e.to_session_time]
        self.temps.rewind()

    def for_car(self, car_idx: int) -> list[LapFeatures]:
        return self.laps.get(car_idx, [])

    def current_stint(self, car_idx: int) -> list[LapFeatures]:
        laps = self.for_car(car_idx)
        if not laps:
            return []
        return [r for r in laps if r.stint == laps[-1].stint]

    @staticmethod
    def _new_stint(laps: list[LapFeatures], compound: int | None, age: int | None,
                   pit_in_lap: bool) -> bool:
        """Does this lap start a new set of tyres?

        A fresh set shows up as a different compound or a lower tyre age. Where the pit box is
        before the finish line, the in-lap already ends on the new tyres, so an in-lap never
        starts a stint: the lap after it does, compared with the lap before the in-lap. A pit
        stop where neither changed (a drive-through) is not a new stint. If the tyre age is
        unknown, any pit stop counts.
        """
        if not laps:
            return True
        prev = laps[-1]
        if pit_in_lap:
            return False
        if prev.pit_in_lap:
            ref = laps[-2] if len(laps) >= 2 else None
            if ref is None or age is None or ref.tyre_age_laps is None:
                return True
        else:
            ref = prev
        return compound != ref.visual_compound or (
            age is not None and ref.tyre_age_laps is not None and age < ref.tyre_age_laps)

    def on_lap(self, e: ev.LapCompleted) -> None:
        s, c, i = self.state, self.state.cars, e.car_idx
        laps = self.laps.setdefault(i, [])
        laps[:] = [r for r in laps if r.lap < e.lap]          # re-driven after a flashback
        prev = laps[-1] if laps else None

        compound = _opt_int(c.visual_compound[i])
        age = e.tyre_age_laps
        new_stint = self._new_stint(laps, compound, age, e.pit_in_lap)
        stint = 1 if prev is None else prev.stint + (1 if new_stint else 0)
        stint_lap = 1 if new_stint else prev.stint_lap + 1

        temps = self.temps.temps_for(i, e.lap)
        race_start = s.session.is_race and e.lap == 1
        sc = e.safety_car or bool(temps and temps.safety_car)
        rec = LapFeatures(
            session_uid=s.session_uid or 0, track_id=s.session.track_id,
            session_type=s.session.session_type, car_idx=i, is_player=i == s.player_index,
            driver=c.names[i], lap=e.lap, stint=stint, stint_lap=stint_lap,
            lap_time_ms=e.lap_time_ms, sector1_ms=e.sector1_ms, sector2_ms=e.sector2_ms,
            sector3_ms=e.sector3_ms, invalid=e.invalid, pit_in_lap=e.pit_in_lap,
            pit_out_lap=e.pit_out_lap, race_start_lap=race_start,
            clean=not (e.invalid or e.pit_in_lap or e.pit_out_lap or race_start),
            safety_car=sc, position=e.position, visual_compound=compound,
            actual_compound=_opt_int(c.actual_compound[i]), tyre_age_laps=age,
            wear=_t4(c.tyre_wear_pct[i]), damage=_t4(c.tyre_damage_pct[i]),
            blisters=_t4(c.tyre_blisters_pct[i]),
            inner_avg=_t4(temps.lap_avg(temps.inner_avg)) if temps else _nan4(),
            surface_avg=_t4(temps.surface_avg) if temps else _nan4(),
            surface_max=_t4(temps.surface_max) if temps else _nan4(),
            hot_frac=_t4(temps.lap_avg(temps.hot_frac)) if temps else _nan4(),
            cold_frac=_t4(temps.lap_avg(temps.cold_frac)) if temps else _nan4(),
            fuel_kg=float(c.fuel_in_tank[i]), track_temp_c=s.session.track_temp_c,
            air_temp_c=s.session.air_temp_c, weather=s.session.weather,
            session_time=e.session_time, temps=temps,
        )
        laps.append(rec)
        for fn in self.listeners:
            fn(rec)


# -- stints and wear rates (pure functions, used live and offline) -----------------------------

def fit_wear_rate(laps: list[LapFeatures], last_n: int = WEAR_FIT_LAPS) -> np.ndarray:
    """Wear per lap for each wheel (% per lap): the slope of a straight line through the end-of-lap
    wear of the last `last_n` laps of a stint. NaN for a wheel with fewer than 2 known points."""
    # In-laps are left out: where the box is before the line they end on the new tyres.
    pts = [r for r in laps if not r.pit_in_lap][-last_n:]
    rate = np.full(4, np.nan)
    if len(pts) < 2:
        return rate
    x = np.array([r.stint_lap for r in pts], dtype=float)
    wear = np.array([r.wear for r in pts], dtype=float)        # (laps, 4)
    for w in range(4):
        ok = ~np.isnan(wear[:, w])
        if ok.sum() >= 2 and np.ptp(x[ok]) > 0:
            rate[w] = np.polyfit(x[ok], wear[ok, w], 1)[0]
    return rate


@dataclass(frozen=True)
class Stint:
    session_uid: int
    track_id: int | None
    car_idx: int
    is_player: bool
    stint: int
    visual_compound: int | None
    actual_compound: int | None
    first_lap: int
    last_lap: int
    laps: int
    start_age: int | None
    wear_end: tuple
    wear_rate: tuple                 # % per lap, per wheel
    avg_clean_lap_ms: float | None

    def to_row(self) -> dict:
        row = asdict(self)
        for key in ("wear_end", "wear_rate"):
            for suffix, v in zip(WHEEL_SUFFIXES, row.pop(key)):
                row[f"{key}_{suffix}"] = None if math.isnan(v) else float(v)
        row["session_uid"] = f"{self.session_uid:016x}"
        return row


def summarize_stints(laps: list[LapFeatures]) -> list[Stint]:
    """Group lap records (any cars, any sessions) into stints."""
    groups: dict[tuple[int, int, int], list[LapFeatures]] = {}
    for r in laps:
        groups.setdefault((r.session_uid, r.car_idx, r.stint), []).append(r)
    out = []
    for (uid, car, stint), rs in groups.items():
        rs = sorted(rs, key=lambda r: r.lap)
        clean = [r.lap_time_ms for r in rs if r.clean and not r.safety_car]
        on_set = [r for r in rs if not r.pit_in_lap] or rs
        out.append(Stint(
            session_uid=uid, track_id=rs[0].track_id, car_idx=car, is_player=rs[0].is_player,
            stint=stint, visual_compound=rs[0].visual_compound,
            actual_compound=rs[0].actual_compound, first_lap=rs[0].lap, last_lap=rs[-1].lap,
            laps=len(rs), start_age=rs[0].tyre_age_laps, wear_end=on_set[-1].wear,
            wear_rate=_t4(fit_wear_rate(rs, last_n=len(rs))),
            avg_clean_lap_ms=float(np.mean(clean)) if clean else None))
    return out
