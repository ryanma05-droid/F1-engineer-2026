"""Turns analysis results into short sentences that sound right when spoken.

Engineers say lap times as "one oh nine one" (1:09.1) and deltas as "two tenths", so we write the
words out ourselves rather than letting the voice read "1:09.162".
"""

from __future__ import annotations

from engineer.tools.queries import SAME_LAP_THRESHOLD_MS, LapDelta

VERBOSITY_LEVELS = ("terse", "normal", "chatty")

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    """0..99 as words."""
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])


def say_lap_time(ms: int) -> str:
    """69162 -> 'one oh nine one', 77680 -> 'one seventeen six', 59850 -> 'fifty nine eight'.

    Truncated to the tenth, like a timing screen (1:09.162 reads as 1:09.1)."""
    tenths_total = ms // 100
    minutes, rem = divmod(tenths_total, 600)
    seconds, tenths = divmod(rem, 10)
    if minutes == 0:
        return f"{number_words(seconds)} {number_words(tenths)}"
    if seconds == 0:
        secs = "oh oh"
    elif seconds < 10:
        secs = f"oh {_ONES[seconds]}"
    else:
        secs = number_words(seconds)
    return f"{number_words(minutes)} {secs} {number_words(tenths)}"


def say_gap(ms: int) -> str:
    """Size of a time gap, no direction: 'a tenth', 'three tenths', 'one point two seconds'."""
    ms = abs(ms)
    tenths = (ms + 50) // 100
    if tenths <= 1:
        return "a tenth"
    if tenths < 10:
        return f"{_ONES[tenths]} tenths"
    seconds, t = divmod(tenths, 10)
    if seconds >= 10:
        return f"{number_words(seconds)} seconds"
    if t == 0:
        return "a second" if seconds == 1 else f"{_ONES[seconds]} seconds"
    return f"{_ONES[seconds]} point {_ONES[t]} seconds"


def _sentence(*parts: str) -> str:
    text = ", ".join(p for p in parts if p)
    return text[:1].upper() + text[1:] + "."


def lap_delta_phrase(d: LapDelta, verbosity: str = "normal") -> str:
    lap_time = say_lap_time(d.lap.lap_time_ms)

    if not d.compared:
        label = {
            "invalid": "lap invalid",
            "in_lap": "in lap",
            "out_lap": "out lap",
            "race_start": f"lap {number_words(d.lap.lap)}",
            "safety_car": "safety car lap",
            "no_reference": "",
        }[d.reason_not_compared]
        if verbosity == "terse":
            return _sentence(label or lap_time)
        return _sentence(label, lap_time)

    delta = d.delta_ms
    same = abs(delta) < SAME_LAP_THRESHOLD_MS
    if same:
        change = "same again"
    else:
        change = f"{say_gap(delta)} {'quicker' if delta < 0 else 'slower'}"
    if verbosity == "terse":
        return _sentence(change)

    sector = ""
    if d.biggest_sector is not None:
        sd = d.sector_delta_ms[d.biggest_sector - 1]
        if sd is not None and abs(sd) >= SAME_LAP_THRESHOLD_MS:
            verb = "gained" if sd < 0 else "lost"
            # "but" when the sector goes against the overall result: "same again, but gained in
            # sector two" / "a tenth quicker, but lost in sector three".
            against = same or (sd < 0) != (delta < 0)
            sector = f"{'but ' if against else ''}{verb} in sector {_ONES[d.biggest_sector]}"
    reference = "" if d.reference_is_previous_lap else "on your last clean lap"
    if verbosity == "chatty" and d.reference is not None and d.reference_is_previous_lap:
        reference = f"on lap {number_words(d.reference.lap)}"
    return _sentence(lap_time, f"{change} {reference}".strip(), sector)


# -- tyres (Phase 3) ---------------------------------------------------------------------------

WHEEL_NAMES = ("rear left", "rear right", "front left", "front right")   # RL, RR, FL, FR order
AXLE_NAMES = {(2, 3): "fronts", (0, 1): "rears"}


def say_percent(pct: float) -> str:
    n = int(round(pct))
    return "a hundred percent" if n >= 100 else f"{number_words(max(0, n))} percent"


def say_laps_left(laps: int) -> str:
    if laps <= 0:
        return "they're at the limit"
    return f"about {number_words(min(laps, 99))} lap{'s' if laps != 1 else ''} left on these"


def say_wear_rate(rate: float) -> str:
    if rate < 0.75:
        return "under a percent a lap"
    return f"about {number_words(int(round(rate)))} percent a lap"


def _laps_left_part(st, from_pct: float) -> str:
    if st.laps_left is None or st.worst_pct is None or st.worst_pct < from_pct:
        return ""
    return say_laps_left(st.laps_left)


def tyre_status_phrase(st, verbosity: str = "normal", laps_left_from_pct: float = 25.0) -> str:
    """'Tyres: front right worst at nineteen percent, about four percent a lap.'
    Once the worst tyre passes laps_left_from_pct: '..., about ten laps left on these.'"""
    if st.worst_wheel is None:
        return ""
    wheel, pct = WHEEL_NAMES[st.worst_wheel], say_percent(st.worst_pct)
    left = _laps_left_part(st, laps_left_from_pct)
    rate = st.wear_rate[st.worst_wheel]
    rate_part = "" if rate != rate else say_wear_rate(rate)       # NaN check
    even = float(st.wear.max() - st.wear.min()) < 1.0              # all four the same
    if verbosity == "terse":
        return _sentence(f"{'all four' if even else wheel} {pct}", left)
    head = f"tyres all even at {pct}" if even else f"tyres: {wheel} worst at {pct}"
    if verbosity == "chatty":
        return _sentence(head, rate_part, left)
    return _sentence(head, left or rate_part)


def tyre_warning_phrase(st, threshold: float, verbosity: str = "normal",
                        laps_left_from_pct: float = 25.0) -> str:
    """'Front right at fifty percent, look after it, about six laps left on these.'"""
    wheel = WHEEL_NAMES[st.worst_wheel]
    left = _laps_left_part(st, laps_left_from_pct)
    if verbosity == "terse":
        return _sentence(f"{wheel} {say_percent(threshold)}", left)
    return _sentence(f"{wheel} at {say_percent(threshold)}", "look after it", left)


def tyre_temp_phrase(kind: str, wheels: list[int], sector: int) -> str:
    """'Rears running hot in sector three.' / 'Front left is cold in sector one.'"""
    name = AXLE_NAMES.get(tuple(sorted(wheels))) if len(wheels) == 2 else None
    if name is None:
        name = " and ".join(WHEEL_NAMES[w] for w in wheels)
    verb = "running hot" if kind == "hot" else "cold"
    plural = len(wheels) > 1
    if kind == "cold":
        verb = f"{'are' if plural else 'is'} cold"
    return _sentence(f"{name} {verb} in sector {_ONES[sector]}")


def tyre_dropoff_phrase(ms_per_lap: float, verbosity: str = "normal") -> str:
    """'Pace dropping two tenths a lap, tyres are going.'"""
    if verbosity == "terse":
        return _sentence(f"pace dropping {say_gap(int(ms_per_lap))} a lap")
    return _sentence(f"pace dropping {say_gap(int(ms_per_lap))} a lap", "tyres are going")


def tyre_damage_phrase(wheel: int, pct: float, kind: str = "damage") -> str:
    """'Front left tyre damage, twenty percent.' / 'Blistering on the rear right, fifteen percent.'"""
    if kind == "blisters":
        return _sentence(f"blistering on the {WHEEL_NAMES[wheel]}", say_percent(pct))
    return _sentence(f"{WHEEL_NAMES[wheel]} tyre damage", say_percent(pct))


# -- corners (Phase 4) -------------------------------------------------------------------------

def say_metres(m: float) -> str:
    n = int(round(m / 5.0) * 5)
    return "over a hundred metres" if n >= 100 else f"{number_words(max(5, n))} metres"


def say_kph(kph: float) -> str:
    return f"{number_words(min(99, max(1, int(round(kph)))))} k p h"


_REASONS = {
    "brake_early": lambda v: f"braking {say_metres(v)} earlier",
    "min_speed": lambda v: f"minimum speed {say_kph(v)} down",
    "throttle_late": lambda v: f"full throttle {say_metres(v)} later",
    "exit_speed": lambda v: f"exit speed {say_kph(v)} down",
}


def corner_phrase(delta_s: float, corner_name: str, against: str,
                  reason: tuple[str, float] | None = None, verbosity: str = "normal",
                  again: bool = False) -> str:
    """against: "ahead", "behind" or "best".
    'Turn three, losing two tenths to the car ahead, braking ten metres earlier.'
    'Turn four, three tenths off your best, minimum speed eight k p h down.'
    'Gaining a tenth on the car ahead through turn nine.' (delta_s < 0)
    again=True (same corner as the last call): 'Turn four again, ...'"""
    gap = say_gap(int(round(delta_s * 1000)))
    if delta_s < 0:
        if against == "best":
            return _sentence(f"{corner_name}, {gap} up on your best")
        return _sentence(f"gaining {gap} on the car {against} through {corner_name}")
    loss = f"{gap} off your best" if against == "best" else f"losing {gap} to the car {against}"
    why = "" if reason is None or verbosity == "terse" else _REASONS[reason[0]](reason[1])
    return _sentence(f"{corner_name} again" if again else corner_name, loss, why)


# -- strategy (Phase 5) ------------------------------------------------------------------------

COMPOUND_WORDS = {16: "softs", 17: "mediums", 18: "hards", 7: "inters", 8: "wets"}
_STOP_WORDS = {0: "no stop", 1: "one stop", 2: "two stops", 3: "three stops", 4: "four stops",
               5: "five stops"}


def say_compound(c: int) -> str:
    return COMPOUND_WORDS.get(c, "new tyres")


def _laps_range(window: tuple[int, int] | None, lap: int) -> str:
    lo, hi = window or (lap, lap)
    if lo == hi:
        return f"lap {number_words(lo)}"
    return f"lap {number_words(lo)} to {number_words(hi)}"


def plan_phrase(plan, alternative=None, verbosity: str = "normal", change: bool = False) -> str:
    """plan / alternative: strategy.sim.Plan.
    'Plan is one stop, box lap eleven to thirteen for hards. Two stops is four seconds slower.'
    'Plan is two stops: lap eight for mediums, then lap nineteen for hards.'
    'Plan is no stop, tyres will make the end.'
    change=True: 'Plan change: one stop, box lap nine to ten for mediums.'"""
    stops = plan.stops
    if not stops:
        body = "no stop, tyres will make the end"
    elif len(stops) == 1:
        body = (f"one stop, box {_laps_range(plan.window, stops[0].lap)} "
                f"for {say_compound(stops[0].compound)}")
    else:
        first = f"{_laps_range(plan.window, stops[0].lap)} for {say_compound(stops[0].compound)}"
        rest = [f"lap {number_words(s.lap)} for {say_compound(s.compound)}" for s in stops[1:]]
        body = f"{_STOP_WORDS[len(stops)]}: {first}, then " + ", then ".join(rest)
    head = f"plan change: {body}" if change else f"plan is {body}"
    alt = ""
    if alternative is not None and verbosity != "terse":
        diff_ms = int(round((alternative.cost_s - plan.cost_s) * 1000))
        name = _STOP_WORDS[alternative.n_stops]
        name = name[0].upper() + name[1:]
        alt = (f"{name} is about the same" if diff_ms < 500
               else f"{name} is {say_gap(diff_ms)} slower")
    return _sentence(head) + (f" {_sentence(alt)}" if alt else "")


def rejoin_phrase(rejoin) -> str:
    """rejoin: analyzers.strategy.Rejoin.
    "You'll come out behind Sainz, about two seconds back." / "You'll come out in clear air." """
    if rejoin is None:
        return ""
    if rejoin.behind_name is not None:
        return _sentence(f"you'll come out behind {rejoin.behind_name}, "
                         f"about {say_gap(rejoin.behind_ms)} back")
    if rejoin.ahead_of_name is not None:
        return _sentence(f"you'll come out just ahead of {rejoin.ahead_of_name}")
    return _sentence("you'll come out in clear air")


def box_phrase(compound: int, rejoin=None, verbosity: str = "normal") -> str:
    """One lap before the pit entry: 'Box this lap, box this lap. Hards. You'll come out ...'"""
    text = f"Box this lap, box this lap. {_sentence(say_compound(compound))}"
    extra = "" if verbosity == "terse" else rejoin_phrase(rejoin)
    return f"{text} {extra}".strip()


def box_half_phrase(compound: int) -> str:
    """Half a lap before the pit entry: 'Box box, end of this lap. Hards.'"""
    return f"Box box, end of this lap. {_sentence(say_compound(compound))}"


def safety_car_box_phrase(kind: str, compound: int, next_lap: bool = False,
                          saving_s: float | None = None, verbosity: str = "normal") -> str:
    """kind: 'safety car' or 'virtual safety car'.
    'Safety car, box this lap, box this lap. Mediums, it's a cheap stop.'"""
    when = "box next lap" if next_lap else "box this lap, box this lap"
    why = "it's a cheap stop"
    if verbosity == "chatty" and saving_s is not None and saving_s >= 1:
        why = f"it's a cheap stop, saves about {say_gap(int(saving_s * 1000))}"
    return f"{_sentence(kind + ', ' + when)} {_sentence(say_compound(compound), why)}"


def safety_car_stay_phrase(kind: str) -> str:
    """'Safety car, stay out. Stopping now doesn't pay.'"""
    return _sentence(f"{kind}, stay out") + " " + _sentence("stopping now doesn't pay")


def undercut_phrase(name: str | None) -> str:
    """'Norris behind has pitted, watch the undercut.'"""
    who = f"{name} behind" if name else "the car behind"
    return _sentence(f"{who} has pitted, watch the undercut")


# -- push-to-talk answers (Phase 6) ------------------------------------------------------------

def say_position(pos: int) -> str:
    return f"P {number_words(pos)}"


def gap_answer_phrase(direction: str, name: str | None, gap_ms: float | None) -> str:
    """direction 'ahead' or 'behind'. 'Norris ahead, one point two seconds.'
    'Nobody ahead, you're leading.' / 'Nobody behind.'"""
    if gap_ms is None:
        return _sentence("you're leading") if direction == "ahead" else _sentence("nobody behind")
    who = name or f"the car {direction}"
    return _sentence(f"{who} {direction}", say_gap(int(gap_ms)))


def fuel_phrase(laps_margin: float) -> str:
    """m_fuelRemainingLaps: laps of fuel beyond the finish (the MFD number).
    'Fuel is fine, plus zero point four laps.' / 'Fuel is tight, minus zero point three laps,
    lift and coast.'"""
    tenths = int(round(abs(laps_margin) * 10))
    whole, t = divmod(tenths, 10)
    amount = (f"{number_words(whole)} point {number_words(t)}" if whole < 100 else "lots of")
    if laps_margin >= 0:
        return _sentence("fuel is fine", f"plus {amount} laps")
    return _sentence("fuel is tight", f"minus {amount} laps", "lift and coast")


def position_phrase(pos: int, laps_to_go: int | None) -> str:
    """'P three, eight laps to go.' / 'P one, last lap.'"""
    if laps_to_go is None:
        return _sentence(say_position(pos))
    if laps_to_go <= 1:
        return _sentence(say_position(pos), "last lap")
    return _sentence(say_position(pos), f"{number_words(min(laps_to_go, 99))} laps to go")


def laps_left_phrase(laps_to_go: int) -> str:
    if laps_to_go <= 1:
        return _sentence("last lap")
    return _sentence(f"{number_words(min(laps_to_go, 99))} laps to go")


def box_now_phrase(what_if, plan, rejoin=None) -> str:
    """what_if / plan: strategy.sim.Plan. 'Box now: costs about three seconds over the plan.
    You'll come out behind Sainz, about a second back.' / 'Box now is the plan. ...'"""
    diff_ms = int(round((what_if.cost_s - plan.cost_s) * 1000))
    if plan.next_stop is not None and what_if.next_stop is not None \
            and plan.next_stop.lap == what_if.next_stop.lap:
        head = "box now is the plan"
    elif diff_ms < 500:
        head = "box now, it's about the same as the plan"
    else:
        head = f"box now costs about {say_gap(diff_ms)} over the plan"
    tyre = f"{say_compound(what_if.next_stop.compound)}" if what_if.next_stop else ""
    return " ".join(x for x in (_sentence(head, tyre), rejoin_phrase(rejoin)) if x)


_SECTORS = {1: "sector one", 2: "sector two", 3: "sector three"}


def against_words(against: str) -> str:
    """'ahead' / 'behind' / 'best' / a driver name -> 'the car ahead' / 'your best' / 'Norris'."""
    if against in ("ahead", "behind"):
        return f"the car {against}"
    if against == "best":
        return "your best"
    return against


def sector_phrase(sector: int, delta_ms: int, against: str) -> str:
    """'Sector two, losing four tenths to the car ahead.' / 'Sector one, gaining a tenth on
    your best.'"""
    who = against_words(against)
    if delta_ms >= 0:
        return _sentence(_SECTORS[sector], f"losing {say_gap(delta_ms)} to {who}")
    return _sentence(_SECTORS[sector], f"gaining {say_gap(delta_ms)} on {who}")


def compare_phrase(name: str, lap_delta_ms: int | None, gain=None, loss=None) -> str:
    """gain / loss: (where, seconds) or None.
    'Last lap, three tenths quicker than Norris. Gaining most in turn three, a tenth. Losing
    most in turn four, two tenths.'"""
    parts = []
    if lap_delta_ms is not None:
        if abs(lap_delta_ms) < 50:
            parts.append(_sentence(f"last lap, same pace as {name}"))
        else:
            word = "quicker" if lap_delta_ms < 0 else "slower"
            parts.append(_sentence(f"last lap, {say_gap(lap_delta_ms)} {word} than {name}"))
    if gain is not None:
        parts.append(_sentence(f"gaining most in {gain[0]}", say_gap(int(abs(gain[1]) * 1000))))
    if loss is not None:
        parts.append(_sentence(f"losing most in {loss[0]}", say_gap(int(abs(loss[1]) * 1000))))
    return " ".join(parts)


def damage_phrase(items: list[tuple[str, float]]) -> str:
    """'No damage.' / 'Front left wing twenty percent, floor ten percent.'"""
    if not items:
        return _sentence("no damage, car looks fine")
    return _sentence(*[f"{name} {say_percent(pct)}" for name, pct in items])


def best_lap_phrase(lap_time_ms: int, lap: int) -> str:
    """'Your best is one thirty five, lap two.'"""
    return _sentence(f"your best is {say_lap_time(lap_time_ms)}", f"lap {number_words(lap)}")


def gap_leader_phrase(leader: str | None, gap_ms: float | None, second: str | None = None) -> str:
    """You lead: "You're leading, Norris one point two seconds behind."
    Else: 'Verstappen leads, eight point two seconds ahead of you.'"""
    if leader is None:
        if second is None or gap_ms is None:
            return _sentence("you're leading")
        return _sentence("you're leading", f"{second} {say_gap(int(gap_ms))} behind")
    if gap_ms is None:
        return _sentence(f"{leader} leads")
    return _sentence(f"{leader} leads", f"{say_gap(int(gap_ms))} ahead of you")


_WEATHER = {0: "clear", 1: "light cloud", 2: "overcast", 3: "light rain", 4: "heavy rain",
            5: "storm"}


def weather_phrase(weather: int | None, track_c: int | None, air_c: int | None) -> str:
    """'Overcast, track thirty one degrees, air twenty four.' (current conditions)"""
    parts = [_WEATHER.get(weather, "no weather reading")]
    if track_c is not None:
        parts.append(f"track {number_words(max(0, min(track_c, 99)))} degrees")
    if air_c is not None:
        parts.append(f"air {number_words(max(0, min(air_c, 99)))}")
    return _sentence(*parts)


def live_tyres_phrase(wheel: int, pct: float) -> str:
    """Before a lap is done: 'Front right at five percent, too early for a wear rate.'"""
    return _sentence(f"{WHEEL_NAMES[wheel]} at {say_percent(pct)}", "too early for a wear rate")
