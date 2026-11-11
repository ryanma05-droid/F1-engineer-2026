"""Your preferences for the race (13 Nov: "more ability for the user to input words into the
strategy").

Said on the radio ("finish on softs", "max fifty percent", "one stop only", "no hards", "I want
to go long", "box on lap twelve") and kept until you change them or say "back to your plan".
Every plan the strategy works out follows them (strategy/sim.py: avoid, final_compound,
min/max stops, first_stop_range, the wear limit). The engineer reads each change back with
what it costs against its own plan, and pushes back (still doing it) when it's risky.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from engineer.strategy.sim import RaceSituation

# "Go long" / "box early": how far from the engineer's own first stop.
LONG_LAPS = 3
EARLY_LAPS = 2


@dataclass
class DriverPrefs:
    final_compound: int | None = None
    avoid: frozenset[int] = field(default_factory=frozenset)
    stops: tuple[int, int] | None = None          # (min, max) stops from here
    first_stop: tuple[int, int] | None = None     # first stop between these laps
    next_compound: int | None = None              # the tyre at the next stop (an accepted offer)
    limit_pct: float | None = None                # your wear limit for this race
    said: list[str] = field(default_factory=list)  # what you asked for, for the read-back

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.final_compound, self.stops, self.first_stop,
                                           self.next_compound, self.limit_pct)) \
            or bool(self.avoid)

    def apply(self, sit: RaceSituation) -> RaceSituation:
        """The situation with your rules on it."""
        out = replace(sit, avoid=frozenset(self.avoid), final_compound=self.final_compound)
        if self.limit_pct is not None:
            out = replace(out, limit_pct=self.limit_pct)
        if self.stops is not None:
            out = replace(out, min_stops=self.stops[0], max_stops=min(self.stops[1],
                                                                       sit.max_stops))
        if self.first_stop is not None:
            lo, hi = self.first_stop
            out = replace(out, first_stop_range=(max(lo, sit.lap_done + 1), hi))
        if self.next_compound is not None:
            out = replace(out, first_stop_compound=self.next_compound)
        return out

    def clear(self) -> None:
        self.__init__()
