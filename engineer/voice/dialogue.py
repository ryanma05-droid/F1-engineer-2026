"""Reading your preferences out of what you said (13 Nov: strategy as a dialogue).

`parse_prefs` turns "finish on softs", "max fifty percent", "one stop only", "no more stops",
"no hards", "I want to go long", "box early", "let's box on lap twelve" into a change for
strategy/prefs.DriverPrefs, plus the words to read back ("finishing on softs").
"""

from __future__ import annotations

import re

from engineer.radio.phrases import number_words

COMPOUNDS = {"soft": 16, "softs": 16, "medium": 17, "mediums": 17, "hard": 18, "hards": 18}
NAMES = {16: "softs", 17: "mediums", 18: "hards"}
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19, "a": 1}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
NUM = (r"(\d+|(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[ -](?:one|two|three"
       r"|four|five|six|seven|eight|nine))?)|one|two|three|four|five|six|seven|eight|nine|ten"
       r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)")


def number(word: str) -> int | None:
    word = word.strip().lower()
    if word.isdigit():
        return int(word)
    parts = re.split(r"[ -]", word)
    if parts[0] in _TENS:
        return _TENS[parts[0]] + (_ONES.get(parts[1], 0) if len(parts) > 1 else 0)
    return _ONES.get(word)


def _compound(word: str) -> int | None:
    return COMPOUNDS.get(word.lower().rstrip("s") + "s") if word else None


def parse_prefs(text: str, lap: int, plan_stop_lap: int | None, stops_made: int,
                total_laps: int) -> tuple[dict, list[str]] | None:
    """(change, read-back words) or None when nothing in it is a preference.
    lap: the lap being driven; plan_stop_lap: the engineer's own first stop (for "go long")."""
    t = text.lower().replace("tire", "tyre")
    change, said = {}, []
    m = re.search(r"\b(?:finish|end|last stint|to the flag|final stint)\w* on (?:the )?"
                  r"(softs?|mediums?|hards?)\b", t)
    if m:
        c = _compound(m.group(1))
        change["final_compound"] = c
        said.append(f"finishing on {NAMES[c]}")
    m = re.search(r"\b(?:max|maximum|limit|no more than|up to|don't go (?:past|over|above)|"
                  r"(?:run|take) (?:them|it|these|the tyres) to)\b[^0-9a-z]*(?:of |at |is )?"
                  + NUM + r"\s*(?:%|percent)?", t) \
        or re.search(NUM + r"\s*(?:%|percent)\s*(?:max|maximum|limit|tops)\b", t)
    if m:
        pct = number(m.group(1))
        if pct is not None and 20 <= pct <= 95:
            change["limit_pct"] = float(pct)
            said.append(f"tyres to {number_words(pct)} percent")
    m = re.search(r"\bno more stops\b", t)
    n = 0 if m else None
    if n is None:
        m = re.search(r"\b(one|two|three|1|2|3)[ -]?stop(?:per)?\b", t)
        if m and re.search(r"\b(only|make it|do a|let's|lets|i want|go for|switch to|we'll|"
                           r"i'd like|change to|try)\b", t):
            n = max(0, number(m.group(1)) - stops_made)
    if n is not None:
        change["stops"] = (n, n)
        said.append("no more stops" if n == 0 else
                    f"{number_words(n)} more stop{'s' if n > 1 else ''}")
    m = re.findall(r"\b(?:no|avoid(?: the)?|not the|don't (?:want|give me|put me on)(?: the)?|"
                   r"skip(?: the)?|without(?: the)?) (softs?|mediums?|hards?)\b", t)
    if m:
        avoid = frozenset(c for c in (_compound(x) for x in m) if c is not None)
        change["avoid"] = avoid
        said.append("no " + " or ".join(NAMES[c] for c in sorted(avoid)))
    m = re.search(r"\b(?:box|pit|stop|come in)\w* (?:on |at |end of )?lap " + NUM, t)
    if m and re.search(r"\b(i want|i'll|let's|lets|i'm going to|we'll|i'd like|plan to)\b", t):
        target = number(m.group(1))
        if target is not None and lap <= target < total_laps:
            change["first_stop"] = (target, target)
            said.append(f"boxing lap {number_words(target)}")
    elif re.search(r"\b(?:go|going|stay out|run|stretch)(?:ing)? (?:a bit )?long(?:er)?\b|"
                   r"\bextend (?:the |this )?stint\b", t):
        base = plan_stop_lap or lap
        change["first_stop"] = (min(base + 3, total_laps - 1), total_laps - 1)
        said.append("going long")
    elif re.search(r"\b(?:box|pit|stop|come in)(?:ing)? (?:early|earlier|short|sooner)\b|"
                   r"\bgo(?:ing)? short\b", t):
        base = plan_stop_lap or (lap + 4)
        change["first_stop"] = (lap, max(lap, base - 2))
        said.append("boxing early")
    return (change, said) if change else None

