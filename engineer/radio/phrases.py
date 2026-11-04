"""Turns analysis results into short sentences that sound right when spoken.

Engineers say lap times as "one oh nine one" (1:09.1) and deltas as "two tenths", so we write the
words out ourselves rather than letting the voice read "1:09.162".
"""

from __future__ import annotations

import math

from engineer.radio.variety import pick
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
    """69162 -> 'one oh nine one', 77680 -> 'one seventeen six', 59850 -> 'fifty nine eight',
    100400 -> 'one forty point four' (not 'one forty four', which sounds like 1:44).

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
    elif seconds % 10 == 0:
        # "one forty four" would sound like 1:44 (3 Nov Shanghai, a 1:40.4): "one forty point four".
        return f"{number_words(minutes)} {number_words(seconds)} point {number_words(tenths)}"
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
    gap = say_gap(delta)
    if same:
        change = pick("lap_same", ["same again", "matched it", "right on your last one"])
    elif delta < 0:
        change = pick("lap_quicker", [f"{gap} quicker", f"up {gap}", f"{gap} better"])
    else:
        change = pick("lap_slower", [f"{gap} slower", f"down {gap}", f"{gap} off that"])
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
            sec = f"sector {_ONES[d.biggest_sector]}"
            words = pick("lap_sector_" + verb, [f"{verb} in {sec}",
                                                f"{'found it' if sd < 0 else 'dropped it'} in {sec}",
                                                f"{sec} {'was the good one' if sd < 0 else 'cost you'}"])
            sector = f"{'but ' if against else ''}{words}"
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
    n = f"{number_words(min(laps, 99))} lap{'s' if laps != 1 else ''}"
    return pick("laps_left", [f"about {n} left on these", f"good for about {n} more",
                              f"around {n} left in them"])


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
    head = pick("tyre_even", [f"tyres all even at {pct}", f"all four even, {pct}"]) if even \
        else pick("tyre_worst", [f"tyres: {wheel} worst at {pct}", f"{wheel} is the worst one, {pct}",
                                 f"tyres, {wheel} leading the wear at {pct}"])
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
    care = pick("tyre_care", ["look after it", "manage that one", "go easy on it"])
    return _sentence(f"{wheel} at {say_percent(threshold)}", care, left)


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
    going = pick("tyre_going", ["tyres are going", "they're starting to go",
                                "that's the tyres dropping off"])
    return _sentence(f"pace dropping {say_gap(int(ms_per_lap))} a lap", going)


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

# Plain wording (radio.detail: plain, the default): what to do, not the measurement. The size
# is in words, from how far past its threshold the measurement is (queries.REASON_THRESHOLDS):
# under 2x "slightly", under 4x "a bit", else "a lot".
_ADVICE = {
    "brake_early": ("brake slightly later", "brake a bit later", "brake a lot later"),
    "min_speed": ("carry a touch more speed through the corner",
                  "carry a bit more speed through the corner",
                  "carry a lot more speed through the corner"),
    "throttle_late": ("get on the power a touch earlier", "get on the power a bit earlier",
                      "get on the power much earlier"),
    "exit_speed": ("get it turned a touch earlier for the exit",
                   "get it turned earlier for a better exit",
                   "get it turned much earlier for the exit"),
}
_DRIVING_ORDER = ("brake_early", "min_speed", "exit_speed", "throttle_late")


def _size(kind: str, value: float) -> int:
    from engineer.tools.queries import REASON_THRESHOLDS
    r = value / REASON_THRESHOLDS[kind]
    return 0 if r < 2 else 1 if r < 4 else 2


def advice(reason: tuple[str, float] | None, reasons: dict[str, float] | None = None,
           most: int = 2) -> str:
    """'brake slightly later, then carry a bit more speed through the corner'. The main cause
    first in the order you drive the corner, plus one more cause if there is one."""
    if reason is None:
        return ""
    picked = {reason[0]: reason[1]}
    for k, v in sorted((reasons or {}).items(), key=lambda kv: -kv[1] / _threshold(kv[0])):
        if len(picked) >= most:
            break
        # A slow exit and late throttle go together (the throttle is the cause): one of them.
        if {k, *picked} >= {"exit_speed", "throttle_late"}:
            continue
        picked.setdefault(k, v)
    steps = [_ADVICE[k][_size(k, picked[k])] for k in _DRIVING_ORDER if k in picked]
    return ", then ".join(steps)


def _threshold(kind: str) -> float:
    from engineer.tools.queries import REASON_THRESHOLDS
    return REASON_THRESHOLDS[kind]


def reason_words(reason: tuple[str, float] | None, reasons: dict[str, float] | None = None,
                 detail: str = "plain") -> str:
    """The why of a corner call: plain advice, or the measurement (detail "specific")."""
    if reason is None:
        return ""
    if detail == "specific":
        return _REASONS[reason[0]](reason[1])
    return advice(reason, reasons)


_STREAK = {2: "second lap running", 3: "third lap running", 4: "fourth lap running",
           5: "fifth lap running"}


def corner_better_phrase(corner_name: str, gained_s: float | None = None) -> str:
    """'Turn four better, two tenths found.' / without the amount: 'Turn four better.'"""
    head = pick("corner_better", [f"{corner_name} better", f"better through {corner_name}",
                                  f"{corner_name} sorted"])
    if gained_s is None:
        return _sentence(head)
    return _sentence(head, f"{say_gap(int(round(gained_s * 1000)))} found")


def corner_phrase(delta_s: float, corner_name: str, against: str,
                  reason: tuple[str, float] | None = None, verbosity: str = "normal",
                  again: bool = False, streak: int = 0, detail: str = "plain",
                  reasons: dict[str, float] | None = None, report: bool = False) -> str:
    """against: "ahead", "behind", "best" or "pace" (your best lap on similar tyre wear).
    'Turn three, losing two tenths to the car ahead, braking ten metres earlier.'
    'Turn four, three tenths off your best, minimum speed eight k p h down.'
    'Gaining a tenth on the car ahead through turn nine.' (delta_s < 0)
    again=True (same corner as the last call): 'Turn four again, ...'
    streak=3 (lost time there 3 laps in a row): 'Turn four, third lap running, ...'
    report=True (a corner your driver report says you usually lose time in, first call this
    session): 'Turn four, one of your report corners, ...'"""
    gap = say_gap(int(round(delta_s * 1000)))
    if against == "fastest":                       # coaching: the fastest lap on track
        if delta_s < 0:
            return _sentence(f"{corner_name}, {gap} up on the fastest lap")
        why = "" if verbosity == "terse" else reason_words(reason, reasons, detail)
        head = _sentence(corner_name, f"{gap} off the fastest lap")
        return f"{head} {_sentence(why)}" if why else head
    if delta_s < 0:
        if against in ("best", "pace"):
            return _sentence(f"{corner_name}, {gap} up on your {against}")
        return _sentence(f"gaining {gap} on the car {against} through {corner_name}")
    loss = f"{gap} off your {against}" if against in ("best", "pace") \
        else pick("corner_loss", [f"losing {gap} to the car {against}",
                                  f"{gap} down on the car {against}",
                                  f"giving {gap} to the car {against}"])
    why = "" if verbosity == "terse" else reason_words(reason, reasons, detail)
    if streak >= 2:
        head = f"{corner_name}, {_STREAK.get(streak, 'lap after lap')}"
    elif report:
        head = f"{corner_name}, " + pick("report_corner", ["one of your report corners",
                                                           "the one from your report",
                                                           "your usual one from the report"])
    else:
        head = f"{corner_name} again" if again else corner_name
    if detail == "plain" and why:                  # the advice as its own sentence
        return f"{_sentence(head, loss)} {_sentence(why)}"
    return _sentence(head, loss, why)


def focus_brief_phrase(corners: list[str], habit: str | None) -> str:
    """What the driver report says to work on, once at the start of a session.
    'From your report, the time's in turn four and turn six. Work on the exits: brake a touch
    earlier and get to full throttle sooner.' Either part can be missing."""
    out = []
    if corners:
        where = " and ".join(corners)
        out.append(_sentence(pick("focus_where", [f"from your report, the time's in {where}",
                                                  f"from your report, watch {where}",
                                                  f"your report says {where}"])))
    if habit:
        out.append(_sentence(f"work on {habit}") if corners
                   else _sentence(f"from your report, work on {habit}"))
    return " ".join(out)


def focus_level_phrase(corner_name: str) -> str:
    """A report corner level with the fastest lap on track, said once."""
    return " ".join([_sentence(corner_name, "level with the fastest lap now"),
                     _sentence(pick("focus_level", ["that was a report corner, nice",
                                                    "that's a report corner, keep it there",
                                                    "report corner sorted, keep that"]))])


def coaching_phrase(lap_ms: int | None, gap_ms: int, who: str,
                    items: list[tuple[str, int, tuple[str, float] | None, dict, int]],
                    better: str | None = None, detail: str = "plain") -> str:
    """Coaching mode, after a lap: the lap, the gap to the fastest lap on track (who: the car
    that set it) and where it is. items: (corner, loss ms, main cause, all causes, streak),
    biggest first, at most two.
    'One twenty three four, seven tenths off Verstappen. Most of it's turn two, three tenths.
    Brake slightly later, then carry a bit more speed through the corner. Turn six, get on the
    power a bit earlier.'
    Quicker than everyone (gap_ms < 0): 'One oh nine one, fastest lap on track. Albon still
    quicker through turn four. Brake slightly later.'"""
    out = []
    if better:
        out.append(_sentence(f"better into {better}",
                             pick("coach_keep", ["keep doing that", "that's the way",
                                                 "more of that"])))
    lap = say_lap_time(lap_ms) if lap_ms else ""
    fastest = gap_ms < 0
    if fastest:
        out.append(_sentence(lap, "fastest lap on track"))
    elif gap_ms <= 50:
        out.append(_sentence(lap, f"matching {who}"))
    else:
        out.append(_sentence(lap, f"{say_gap(gap_ms)} off {who}"))
    if not items:
        if not fastest and gap_ms > 50:
            out.append(_sentence("spread around the lap, no one corner"))
        return " ".join(out)
    for i, (name, loss_ms, reason, reasons, streak) in enumerate(items[:2]):
        if detail == "specific":
            why = _REASONS[reason[0]](reason[1]) if reason else ""
        else:
            why = advice(reason, reasons, most=2 if i == 0 else 1)
        if i == 0:
            if fastest:
                where = f"{who} still quicker through {name}"
            elif streak >= 2:
                where = f"still {name}, {say_gap(loss_ms)}"
            else:
                where = pick("coach_most", [f"most of it's {name}, {say_gap(loss_ms)}",
                                            f"the big one is {name}, {say_gap(loss_ms)}",
                                            f"{name} is where it is, {say_gap(loss_ms)}"])
            out.append(_sentence(where))
            if why:
                out.append(_sentence(why))
        else:
            out.append(_sentence(name, why or say_gap(loss_ms)))
    return " ".join(out)


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


def _stops_body(plan, verbosity: str, after_stop: bool) -> str:
    """'one stop, box end of lap nine for softs' / 'no more stops'. One lap, never a range,
    at any tempo ("Be definitive", 2 Nov: a range sounds like the call isn't made)."""
    stops = plan.stops
    if not stops:
        return "no more stops" if after_stop else "no stop"
    first = stops[0]
    if len(stops) == 1:
        return (f"one {'more ' if after_stop else ''}stop, box end of lap "
                f"{number_words(first.lap)} for {say_compound(first.compound)}")
    rest = ", then ".join(f"lap {number_words(s.lap)} for {say_compound(s.compound)}"
                          for s in stops[1:])
    count = _STOP_WORDS[len(stops)]
    if after_stop:
        count = count.replace(" stops", " more stops")
    return (f"{count}: box lap "
            f"{number_words(first.lap)} for {say_compound(first.compound)}, then {rest}")


def say_track(track_id: int | None) -> str:
    """'Sakhir (Bahrain)' -> 'Bahrain', 'Baku (Azerbaijan)' -> 'Baku', 'Austria (Reverse)' ->
    'Austria reverse'."""
    from engineer.ingest.formats.f1_2025 import TRACKS
    name = TRACKS.get(track_id, "this track") if track_id is not None else "this track"
    if "(" not in name:
        return name
    head, inner = name.split(" (", 1)
    inner = inner.rstrip(")")
    if inner == "Reverse":
        return f"{head} reverse"
    return inner if head == "Sakhir" else head


_LAPS_HOW = {"last race here": "same as your last race here",
             "usual distance": "your usual race distance"}


def prerace_phrase(pre, verbosity: str = "normal", limit_pct: float = 60.0,
                   margin_pct: float = 5.0) -> str:
    """The pre-race plan (strategy.prerace.PreRacePlan):
    'Melbourne, fifteen laps, same as your last race here. Start on mediums, one stop, box end of
    lap eight for hards. Starting on softs is two seconds slower.'
    On the grid (the tyres are fitted): 'On these mediums: one stop, box end of lap eight for
    hards.'"""
    track = say_track(pre.track_id)
    best = pre.best
    if best is None:
        return _sentence(f"no tyre numbers for {track} yet",
                         "I'll call the plan a couple of laps in")
    plan, total = best.decision.call.plan, pre.laps.laps if pre.laps else 0
    body = _stops_body(plan, verbosity, False)
    if not plan.stops:
        body += f", about {say_percent(best.decision.call.last.end_wear)} at the flag"
    tight = best.decision.taken_gamble
    note = "tight on the tyres, look after them" if tight else ""
    if pre.grid:
        return _sentence(f"on these {say_compound(best.compound)}: {body}", note)
    out = [_sentence(f"{track}, {number_words(total)} laps",
                     _LAPS_HOW.get(pre.laps.how if pre.laps else "", "")),
           _sentence(f"start on {say_compound(best.compound)}", body, note)]
    if verbosity != "terse":
        gamble = pre.gamble
        if gamble is not None:
            gain = int((best.cost_s - gamble.cost_s) * 1000)
            out.append(_sentence(f"starting on {say_compound(gamble.compound)} is "
                                 f"{say_gap(gain)} quicker", "but tight on the tyres"))
        elif len(pre.starts) > 1:
            nxt = pre.starts[1]
            gap = int((nxt.cost_s - best.cost_s) * 1000)
            out.append(_sentence(f"starting on {say_compound(nxt.compound)} is "
                                 + ("about the same" if gap < 500 else f"{say_gap(gap)} slower")))
    return " ".join(out)


def _where_wear(stint, total_laps: int) -> str:
    """'at the flag' / 'by lap nineteen'."""
    return "at the flag" if stint.end_lap >= total_laps else f"by lap {number_words(stint.end_lap)}"


def _option_name(option, call) -> str:
    """How a plan differs from the call, in a word: 'softs' / 'one stop' / 'boxing lap nine'."""
    a, b = option.plan.next_stop, call.plan.next_stop
    if option.plan.n_stops != call.plan.n_stops:
        stops = _STOP_WORDS[option.plan.n_stops]
        return f"{stops} for {say_compound(a.compound)}" if a is not None else stops
    if a is not None and (b is None or a.compound != b.compound):
        return say_compound(a.compound)
    return f"boxing lap {number_words(a.lap)}" if a is not None else "staying out"


def call_phrase(decision, total_laps: int, verbosity: str = "normal",
                change_reason: str | None = None, previous=None, copy_compound: int | None = None,
                after_stop: bool = False, ahead: tuple[str, int] | None = None,
                limit_pct: float = 60.0, margin_pct: float = 5.0) -> str:
    """The strategy call (strategy.decision.Decision), said with confidence:
    'Plan is one stop, box end of lap nine for softs.'
    'Plan is no more stops, these need to last eleven laps, about fifty percent at the flag.'
    + the gamble: 'Softs are the gamble: about three seconds quicker, enough to catch Norris, but
      sixty one percent at the flag, you'd have to look after them.'
    or a taken gamble: 'Mediums will be tight, fifty seven percent at the flag, look after them.
      The safe call is two stops, nine seconds slower.'
    change_reason / previous (the plan it replaces): 'Change: box lap eight instead of nine,
      tyres wearing faster than expected.'
    copy_compound: just pitted: 'Copy, softs.' first. ahead: (name, gap ms) of the car ahead.
    A plan is "tight" when the expected wear runs within margin_pct of the limit; when only the
    allowance for a not-yet-sure wear rate makes it unsafe, it's "early" instead."""
    call = decision.call

    def tight(stint) -> bool:
        return stint.end_wear > limit_pct - margin_pct
    plan = call.plan
    out = []
    if copy_compound is not None:
        out.append(_sentence(f"copy, {say_compound(copy_compound)}"))
    reason = change_reason or ""
    same_shape = (previous is not None and plan.next_stop is not None
                  and previous.next_stop is not None and previous.n_stops == plan.n_stops
                  and [s.compound for s in previous.stops] == [s.compound for s in plan.stops])
    if previous is not None and same_shape:
        out.append(_sentence(f"change, box lap {number_words(plan.next_stop.lap)} instead of "
                             f"{number_words(previous.next_stop.lap)}", reason))
    else:
        body = _stops_body(plan, verbosity, after_stop)
        note = ""
        if not plan.stops:
            last = call.last
            body += (f", these need to last {number_words(min(last.laps, 99))} "
                     f"lap{'s' if last.laps != 1 else ''}, about {say_percent(last.end_wear)} "
                     f"at the flag")
            if decision.taken_gamble:
                note = "that's tight, look after them" if tight(last) \
                    else "should be fine but keep an eye on them"
        head = f"plan change: {body}" if previous is not None else \
            pick("plan_head", [f"plan is {body}", f"strategy is {body}", f"we're going {body}"])
        out.append(_sentence(head, reason, note))
    if decision.taken_gamble:
        t = call.tightest
        if plan.stops and t.end_wear < 1000:
            if tight(t):
                out.append(_sentence(f"{say_compound(t.compound)} will be tight, about "
                                     f"{say_percent(t.end_wear)} {_where_wear(t, total_laps)}",
                                     "look after them"))
            else:
                out.append(_sentence(f"{say_compound(t.compound)} should make it, about "
                                     f"{say_percent(t.end_wear)} {_where_wear(t, total_laps)}",
                                     "but it's early, keep an eye on them"))
        safe = decision.safe_alternative
        if safe is not None and verbosity != "terse" and decision.gain_s < 15:
            out.append(_sentence(f"the safe call is {_option_name(safe, call)}, "
                                 f"{say_gap(int(decision.gain_s * 1000))} slower"))
    elif decision.gamble is not None:
        g, t = decision.gamble, decision.gamble.tightest
        gain_ms = int(decision.gain_s * 1000)
        name = _option_name(g, call)
        name = name[:1].upper() + name[1:]
        if verbosity == "terse":
            out.append(_sentence(f"{name} is the gamble, {say_gap(gain_ms)} quicker but tight"))
        else:
            target = ""
            if ahead is not None:
                who, gap = ahead
                target = (f"enough to catch {who}" if gain_ms >= gap
                          else f"{who} is {say_gap(gap)} ahead")
            risk = (f"but {say_compound(t.compound)} at about {say_percent(t.end_wear)} "
                    f"{_where_wear(t, total_laps)}", "you'd have to look after them") if tight(t) \
                else (f"but there's not much data on the {say_compound(t.compound)} yet",)
            rest = _sentence(f"about {say_gap(gain_ms)} quicker", target, *risk)
            verb = "are" if name.endswith("s") else "is"
            out.append(f"{name} {verb} the gamble: {rest[:1].lower()}{rest[1:]}")
    return " ".join(out)


def shift_phrase(laps: int, diff_s: float, wear_pct: float | None, limit_pct: float) -> str:
    """'A lap later costs about four tenths, tyres at fifty one percent when you box.'"""
    when = f"{'a' if abs(laps) == 1 else number_words(abs(laps))} lap{'s' if abs(laps) != 1 else ''} " \
           f"{'later' if laps > 0 else 'earlier'}"
    diff_ms = int(round(diff_s * 1000))
    if abs(diff_ms) < 300:
        head = f"{when} is about the same"
    elif diff_ms > 0:
        head = f"{when} costs about {say_gap(diff_ms)}"
    else:
        head = f"{when} saves about {say_gap(diff_ms)}"
    tyre = ""
    if wear_pct is not None and not math.isnan(wear_pct):
        tyre = f"tyres at {say_percent(wear_pct)} when you box"
        if wear_pct > limit_pct:
            tyre += ", that's past the limit"
    return _sentence(head, tyre)


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


def rejoin_answer_phrase(position: int, rejoin) -> str:
    """'Box now and you'd come out P eleven. You'll come out behind Sainz, about two seconds
    back.'"""
    return f"{_sentence(f'box now and you come out {say_position(position)}')} " \
           f"{rejoin_phrase(rejoin)}".strip()


def _names(names: list[str | None]) -> str:
    names = [n or "a car" for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def true_position_phrase(on_road: int, real: int, to_stop: list, passing: list) -> str:
    """'P seven on the road. Norris and Leclerc ahead still have to stop: about P five once
    they do.' / 'P seven, and that's real: the cars around you have stopped the same.'"""
    if real == on_road and not to_stop and not passing:
        return _sentence(f"{say_position(on_road)}, and that's real",
                         "the cars around you have stopped the same")
    bits = [_sentence(f"{say_position(on_road)} on the road")]
    if to_stop:
        verb = "still has" if len(to_stop) == 1 else "still have"
        bits.append(_sentence(f"{_names(to_stop)} ahead {verb} to stop"))
    if passing:
        bits.append(_sentence(f"after your stop {_names(passing)} will be ahead"))
    bits.append(_sentence(f"about {say_position(real)} once it all shakes out"))
    return " ".join(bits)


_AXLE_TIPS = {
    "front": "brake a touch earlier and straighter, less steering lock mid-corner, and let the "
             "car rotate before you pick up the throttle",
    "rear": "be smoother on the throttle out of the slow corners, short-shift, and don't let the "
            "rear step out",
}


def tyre_tips_phrase(wheel: int, rate: float | None, target, hot_sector: int | None) -> str:
    """'Front left is your limiting tyre, using five percent a lap. You can afford four to make
    lap nine. It runs hottest in sector two. Brake a touch earlier ...'
    target: (affordable % a lap, stop lap or None for the flag) or None."""
    name = WHEEL_NAMES[wheel]
    bits = [_sentence(f"{name} is your limiting tyre" +
                      (f", using {say_percent(rate)} a lap" if rate is not None else ""))]
    if target is not None and rate is not None:
        can, lap = target
        where = f"lap {number_words(lap)}" if lap else "the flag"
        if can >= rate:
            bits.append(_sentence(f"you're fine to {where} at that rate"))
        else:
            bits.append(_sentence(f"you can afford {say_percent(max(can, 0))} a lap to make {where}"))
    if hot_sector is not None:
        bits.append(_sentence(f"it runs hottest in sector {number_words(hot_sector)}"))
    axle = "front" if wheel >= 2 else "rear"
    bits.append(_sentence(_AXLE_TIPS[axle]))
    return " ".join(bits)


def box_phrase(compound: int, rejoin=None, verbosity: str = "normal") -> str:
    """One lap before the pit entry: 'Box this lap, box this lap. Hards. You'll come out ...'"""
    tyre = pick("box_tyre", [say_compound(compound), f"{say_compound(compound)} are ready",
                             f"going onto {say_compound(compound)}"])
    text = f"Box this lap, box this lap. {_sentence(tyre)}"
    extra = "" if verbosity == "terse" else rejoin_phrase(rejoin)
    return f"{text} {extra}".strip()


def box_half_phrase(compound: int) -> str:
    """Half a lap before the pit entry: 'Box box, end of this lap. Hards.'"""
    head = pick("box_half", ["Box box, end of this lap.", "Box box, box box.",
                             "Pit this lap, box box."])
    return f"{head} {_sentence(say_compound(compound))}"


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
    return _sentence(f"{kind}, stay out") + " " + _sentence(pick("sc_stay", [
        "stopping now doesn't pay", "no gain stopping now", "we lose out if we stop now"]))


def undercut_phrase(name: str | None) -> str:
    """'Norris behind has pitted, watch the undercut.'"""
    who = f"{name} behind" if name else "the car behind"
    return _sentence(f"{who} has pitted", pick("undercut", [
        "watch the undercut", "they're trying the undercut", "push now, undercut is on"]))


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
    if against in ("best", "pace"):
        return f"your {against}"
    if against == "fastest":
        return "the fastest lap"
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


# -- race craft (battles, passes, milestones) --------------------------------------------------

def _places(n: int) -> str:
    return "a place" if n == 1 else f"{number_words(min(n, 99))} places"


def start_phrase(gained: int) -> str:
    """After race lap 1. 'Great start, up eight places.' / 'Lost two places at the start, long
    way to go.'"""
    if gained > 0:
        p = _places(gained)
        return _sentence(pick("start_up", [f"great start, up {p}", f"brilliant launch, up {p}",
                                           f"up {p} off the line, great start"]))
    p = _places(-gained)
    return _sentence(*pick("start_down", [(f"lost {p} at the start", "long way to go"),
                                          (f"dropped {p} off the line", "plenty of time"),
                                          (f"lost {p} at the start", "stay calm, long race")]))


_PASS_WORDS = ("great move", "nice pass", "lovely move", "good job, that's one more")
# With variety on: every wording names the car passed.
_PASS_WORDS_VARIED = ("great move", "nice pass", "lovely move", "beautiful move", "clean pass",
                      "superb move", "good move")


def pass_phrase(names: list[str | None], position: int, n: int = 0) -> str:
    """'Great move on Norris, P three.' / 'Two cars in one go, P five.'
    n: how many pass calls so far; the wording rotates (in order, so replays stay the same)."""
    if len(names) == 1:
        words = pick("pass", list(_PASS_WORDS_VARIED if _variety_on() else _PASS_WORDS), n)
        if words.startswith("good job"):
            return _sentence(words, say_position(position))
        who = f" on {names[0]}" if names[0] else ""
        return _sentence(f"{words}{who}", say_position(position))
    many = number_words(len(names))
    return _sentence(pick("pass_many", [f"{many} cars in one go", f"{many} at once, brilliant",
                                        f"that's {many} cars in one move"]), say_position(position))


def lost_place_phrase(name: str | None, position: int, n: int = 0) -> str:
    """'Lost P three to Leclerc. Stay with Leclerc.' The wording rotates with n (how many lost
    places were called), in order, so replays stay the same."""
    if not name:
        return _sentence(f"lost {say_position(position - 1)}", "stay with them")
    lost = say_position(position - 1)
    now = say_position(position)
    opts = [f"{_sentence(f'lost {lost} to {name}')} {_sentence(f'stay with {name}')}",
            f"{_sentence(f'{name} got by, {now}')} {_sentence('stay close, the move back is on')}",
            f"{_sentence(f'{name} is through, {now}')} {_sentence('keep the pressure on')}",
            f"{_sentence(f'{name} past, {now}')} {_sentence(f'stick with {name}, chances will come')}"]
    return pick("lost_place", opts if _variety_on() else opts[:3], n)


def lost_places_phrase(count: int, position: int) -> str:
    """'Lost two places, P eight. Heads down, plenty of race left.'"""
    return f"{_sentence(f'lost {number_words(count)} places, {say_position(position)}')} " \
           f"{_sentence(pick('lost_places', ['heads down, keep at it', 'reset, plenty of race left', 'shake it off, keep going']))}"


def fight_back_phrase(name: str | None, position: int, heard_lost: bool, n: int = 0) -> str:
    """Retaking a place moments after losing it. heard_lost: the lost place was already called
    ('Straight back past Hamilton, P five. Great response.'); otherwise the place was only gone
    briefly and it's a good defence ('Good fight, Hamilton couldn't make it stick, P five.')."""
    who = name or "them"
    pos = say_position(position)
    if heard_lost:
        return pick("fight_back", [
            f"{_sentence(f'straight back past {who}, {pos}')} Great response.",
            f"{_sentence(f'got it back from {who}, {pos}')} That's how you answer.",
            f"{_sentence(f'back ahead of {who}, {pos}')} Good fight."], n)
    return pick("fight_held", [_sentence(f"good fight, {who} couldn't make it stick, {pos}"),
                               _sentence(f"nicely defended, back ahead of {who}, {pos}"),
                               _sentence(f"great racing with {who}, you kept the place, {pos}")], n)


def defend_phrase(name: str | None, gap_ms: float) -> str:
    """'Leclerc behind, eight tenths and closing, DRS range. Defend.'"""
    who = name or "car"
    drs = ", DRS range" if gap_ms <= 1000 else ""
    return (f"{_sentence(f'{who} behind, {say_gap(int(gap_ms))} and closing{drs}')} "
            + pick("defend", ["Defend.", "Watch your mirrors.", "Cover the inside."]))


def breathing_room_phrase(name: str | None, gap_ms: float) -> str:
    """'Leclerc dropping back, two seconds now. Good defending.'"""
    who = name or "car behind"
    return (f"{_sentence(f'{who} dropping back, {say_gap(int(gap_ms))} now')} "
            + pick("breathing", ["Good defending.", "Nicely done.", "That's the gap opening up."]))


def attack_phrase(name: str | None, gap_ms: float, closing_ms: float, laps_to_catch: int) -> str:
    """'Norris ahead, two point one. Catching three tenths a lap, on Norris in about seven
    laps. Keep pushing.'"""
    who = name or "car ahead"
    on = f"on {name}" if name else "on them"
    laps = "next lap" if laps_to_catch <= 1 else f"in about {number_words(min(laps_to_catch, 99))} laps"
    return (f"{_sentence(f'{who} ahead, {say_gap(int(gap_ms))}')} "
            f"{_sentence(f'catching {say_gap(int(closing_ms))} a lap', f'{on} {laps}')} "
            + pick("attack", ["Keep pushing.", "Keep it coming.", "Reel them in."]))


def drs_range_phrase(name: str | None) -> str:
    """'DRS range on Norris. Go get Norris.' (names, not pronouns)"""
    if not name:
        return f"{_sentence('DRS range on the car ahead')} Go get it."
    return f"{_sentence(f'DRS range on {name}')} " + _sentence(pick("drs", [
        f"go get {name}", f"have a look at {name}", f"use it on {name}"]))


def laps_to_go_phrase(laps: int, position: int, ahead: str | None, gap_ms: float | None) -> str:
    """'Five laps to go, P four. Norris ahead, one point two.' / 'Last lap, P four. Bring it
    home.'"""
    head = "last lap" if laps <= 1 else f"{number_words(laps)} laps to go"
    first = _sentence(head, say_position(position))
    if laps <= 1:
        return f"{first} " + pick("last_lap", ["Bring it home.", "Nice and clean to the flag.",
                                               "One more, bring it home."])
    if ahead and gap_ms is not None and gap_ms < 5000:
        return f"{first} {_sentence(f'{ahead} ahead, {say_gap(int(gap_ms))}')}"
    return first


def finish_phrase(position: int, gained_from_grid: int | None) -> str:
    """'P one! You've won it! Brilliant drive.' / 'Chequered flag, P three. Up six from the grid,
    great drive.' / 'Chequered flag, P twelve. Tough one, we'll learn from it.'"""
    if position == 1:
        return pick("finish_win", ["P one! You've won it! Brilliant drive.",
                                   "P one! Get in there! What a drive.",
                                   "You've won it! P one! Fantastic job."])
    first = _sentence(pick("flag", ["chequered flag", "that's the flag"]), say_position(position))
    if gained_from_grid is not None and gained_from_grid > 0:
        up = f"up {_places(gained_from_grid)} from the grid"
        return f"{first} {_sentence(up, pick('finish_up', ['great drive', 'really good work', 'well driven']))}"
    if position <= 3:
        return f"{first} " + pick("finish_podium", ["Podium. Great drive.",
                                                    "On the podium. Great job.",
                                                    "That's a podium. Well done."])
    if gained_from_grid is not None and gained_from_grid < 0:
        return f"{first} " + pick("finish_down", ["Tough one, we'll learn from it.",
                                                  "Not our day, we'll come back stronger.",
                                                  "Hard race, we'll look at it."])
    return f"{first} " + pick("finish_ok", ["Good job.", "Solid work.", "Good race."])


def fastest_lap_phrase(lap_time_s: float) -> str:
    """'Fastest lap of the race, one oh nine one.'"""
    return _sentence(pick("fastest", ["fastest lap of the race", "that's the fastest lap",
                                      "purple, fastest lap of the race"]),
                     say_lap_time(int(lap_time_s * 1000)))


# Penalty and infringement IDs from the F1 25 data spec (Data_Output_from_F1_25_v3.pdf).
PENALTY_TYPES = {0: "drive through penalty", 1: "stop go penalty", 2: "grid penalty",
                 4: "time penalty", 5: "warning", 6: "disqualified", 17: "black flag timer"}
INFRINGEMENTS = {
    3: "a collision", 4: "a collision", 5: "not giving the place back",
    6: "not giving the places back", 7: "cutting a corner", 8: "an illegal overtake",
    9: "an illegal overtake", 11: "ignoring blue flags", 12: "ignoring yellow flags",
    17: "pit lane speeding", 21: "multiple warnings", 22: "approaching disqualification",
    27: "track limits", 28: "track limits", 29: "track limits", 34: "a jump start",
    36: "overtaking under the safety car", 37: "safety car pace", 38: "VSC pace",
    52: "gaining time illegally",
}


def penalty_phrase(penalty_type: int, infringement: int, seconds: int | None) -> str | None:
    """'Warning for track limits.' / 'Five second time penalty, for a collision.' None for the
    lap-invalidated notices (the lap call already says it) and other admin."""
    kind = PENALTY_TYPES.get(penalty_type)
    if kind is None:
        return None
    why = INFRINGEMENTS.get(infringement)
    if penalty_type == 4 and seconds and seconds < 255:
        kind = f"{number_words(seconds)} second time penalty"
    if why is None:
        return _sentence(kind)
    return _sentence(f"{kind}, for {why}" if penalty_type != 5 else f"warning for {why}")


def safety_car_in_phrase(kind: str) -> str:
    """SCAR 'returning': 'Safety car in this lap, get the tyres ready.' / 'VSC ending, get
    ready.'"""
    if kind == "virtual safety car":
        return "VSC ending, get ready."
    return _sentence("safety car in this lap", pick("sc_in", [
        "get the tyres ready", "get some heat in the tyres", "be ready for the restart"]))


def contact_damage_phrase(parts: list[tuple[str, float]]) -> str:
    """After contact: 'Front left wing damage, twenty percent.'"""
    return _sentence(*[f"{name} damage, {say_percent(pct)}" for name, pct in parts])


def _variety_on() -> bool:
    """Longer wording lists only with variety on; off keeps the old rotation exactly."""
    from engineer.radio.variety import VARIETY
    return VARIETY.enabled


# -- 3 Nov questions: what-ifs on a lap and tyre, why not a tyre, catching, damage -------------

def _stint_end(model, plan, total_laps: int, idx: int = 0) -> float | None:
    """Expected wear when the stint started by plan.stops[idx] ends (next stop or the flag)."""
    if model is None or len(plan.stops) <= idx:
        return None
    s = plan.stops[idx]
    cm = model.compounds.get(s.compound)
    if cm is None:
        return None
    end = plan.stops[idx + 1].lap if len(plan.stops) > idx + 1 else total_laps
    return s.set_wear + cm.wear_rate.value * (end - s.lap)


def what_if_phrase(lap: int, plan, keep, wear_by_lap: float | None, limit_pct: float, model,
                   total_laps: int) -> str:
    """'Boxing lap ten for hards costs about two seconds over the plan. These at about forty
    percent by then, the hards finish at about thirty five percent.'"""
    s = plan.next_stop
    what = f"lap {number_words(lap)} for {say_compound(s.compound)}" if s else \
        f"lap {number_words(lap)}"
    diff_ms = int(round((plan.cost_s - keep.cost_s) * 1000))
    k = keep.next_stop
    if k is not None and s is not None and k.lap == s.lap and k.compound == s.compound:
        head = f"{what} is the plan"
    elif abs(diff_ms) < 500:
        head = f"boxing {what} is about the same as the plan"
    elif diff_ms > 0:
        head = f"boxing {what} costs about {say_gap(diff_ms)} over the plan"
    else:
        head = f"boxing {what} saves about {say_gap(diff_ms)} on the plan"
    out = [_sentence(head)]
    if len(plan.stops) > 1:
        out.append(_sentence("then " + ", then ".join(
            f"lap {number_words(x.lap)} for {say_compound(x.compound)}" for x in plan.stops[1:])))
    bits = []
    if wear_by_lap is not None and not math.isnan(wear_by_lap):
        bits.append(f"these at about {say_percent(wear_by_lap)} by then"
                    + (", past the limit" if wear_by_lap > limit_pct else ""))
    end = _stint_end(model, plan, total_laps)
    if end is not None and s is not None:
        where = "finish" if len(plan.stops) == 1 else "come off"
        bits.append(f"the {say_compound(s.compound)} {where} at about {say_percent(end)}")
    if bits:
        out.append(_sentence(*bits))
    return " ".join(out)


def why_not_phrase(compound: int, stop, plan, keep, model, total_laps: int, limit_pct: float,
                   margin_pct: float, clear_gap_ms: float | None = None) -> str:
    """'Hards at lap twelve instead of softs: about seven seconds slower, finishing at about
    thirty five percent. The softs are quicker but tight, about fifty nine percent. With six
    seconds in hand behind you, you can afford the hards.'"""
    diff_ms = int(round((plan.cost_s - keep.cost_s) * 1000))
    name, planned = say_compound(compound), say_compound(stop.compound)
    mine, theirs = _stint_end(model, plan, total_laps), _stint_end(model, keep, total_laps)
    if abs(diff_ms) < 500:
        speed = "about the same time"
    elif diff_ms > 0:
        speed = f"about {say_gap(diff_ms)} slower"
    else:
        speed = f"about {say_gap(diff_ms)} quicker"
    head = f"{name} at lap {number_words(stop.lap)} instead of {planned}: {speed}"
    if mine is None:
        wear = ""
    elif len(plan.stops) > 1:            # another stop after them: they don't go to the flag
        wear = f"coming off at lap {number_words(plan.stops[1].lap)} at about {say_percent(mine)}"
    else:
        wear = f"finishing at about {say_percent(mine)}"
    out = [_sentence(head, wear)]
    tight = theirs is not None and theirs > limit_pct - margin_pct
    if tight and (mine is None or mine < theirs):
        out.append(_sentence(f"the {planned} are quicker but tight, about {say_percent(theirs)}"))
    if diff_ms > 0 and clear_gap_ms is not None:
        if clear_gap_ms > diff_ms + 1000:
            out.append(_sentence(f"with {say_gap(int(clear_gap_ms))} in hand behind you, you can "
                                 f"afford the {name}"))
        else:
            out.append(_sentence(f"the car behind is only {say_gap(int(clear_gap_ms))} back"))
    return " ".join(out)


def new_set_phrase(compound: int, laps: int, rate: float, limit_pct: float) -> str:
    """'A new set of hards from the end of this lap: about forty percent at the flag, they'd
    make it.'"""
    end = rate * laps
    verdict = "they'd make it" if end <= limit_pct else "past the limit, they wouldn't make it"
    return _sentence(f"a new set of {say_compound(compound)} from the end of this lap",
                     f"about {say_percent(end)} at the flag after {number_words(min(laps, 99))} "
                     f"laps", verdict)


def stints_phrase(stints, total_laps: int) -> str:
    """The plan's stints: 'On the plan: these softs come off at lap twelve at about forty
    percent, then the mediums finish at about fifty five percent.'"""
    bits = []
    for i, st in enumerate(stints):
        last = st.end_lap >= total_laps
        verb = "finish" if last else f"come off at lap {number_words(st.end_lap)}"
        who = f"these {say_compound(st.compound)}" if i == 0 else f"then the {say_compound(st.compound)}"
        if st.end_wear >= 1000:
            continue
        bits.append(f"{who} {verb} at about {say_percent(st.end_wear)}")
    return _sentence("on the plan: " + ", ".join(bits)) if bits else _sentence("no plan yet")


def catch_phrase(name: str, gap_ms: float, gain_ms_per_lap: float, laps_to_go: int) -> str:
    """'Verstappen is six point nine seconds ahead. You're three tenths a lap quicker: about
    twenty three laps to catch them, eight to go. Not on pace alone.'"""
    first = _sentence(f"{name} is {say_gap(int(gap_ms))} ahead")
    if math.isnan(gain_ms_per_lap):
        return f"{first} {_sentence('no pace to compare yet')}"
    if gain_ms_per_lap < 50:
        return f"{first} {_sentence('you are not quicker right now', 'not on pace alone')}"
    laps = max(1, math.ceil(gap_ms / gain_ms_per_lap))
    pace = f"you're {say_gap(int(gain_ms_per_lap))} a lap quicker"
    if laps <= laps_to_go:
        return f"{first} {_sentence(pace, f'on them in about {number_words(min(laps, 99))} laps', f'{number_words(min(laps_to_go, 99))} to go')}"
    return f"{first} {_sentence(pace, f'about {number_words(min(laps, 99))} laps to catch them', f'only {number_words(min(laps_to_go, 99))} to go')} Not on pace alone."


def damage_cost_phrase(loss_s: float, laps_to_go: int, pit_loss_s: float,
                       stop_lap: int | None) -> str:
    """'It's costing about four tenths a lap. Your stop on lap twelve fixes it, no extra stop.'"""
    if loss_s < 0.1:
        return _sentence("it isn't costing you anything I can see", "stay out")
    per = f"it's costing about {say_gap(int(loss_s * 1000))} a lap"
    if stop_lap is not None:
        return _sentence(per) + " " + _sentence(f"your stop on lap {number_words(stop_lap)} "
                                                f"fixes it", "no extra stop")
    total = loss_s * laps_to_go
    if total < pit_loss_s:
        return _sentence(per, f"about {say_gap(int(total * 1000))} to the flag",
                         "less than a stop costs, stay out")
    return _sentence(per, f"about {say_gap(int(total * 1000))} to the flag",
                     "more than a stop costs, box for a new wing")
