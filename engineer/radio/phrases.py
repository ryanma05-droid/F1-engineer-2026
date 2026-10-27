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
