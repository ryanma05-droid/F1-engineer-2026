"""What did the driver ask? Keyword rules over the recognised text.

Rules are tried in order and the first match wins, so more specific questions come first ("what
if I box now" before "when do I box"). Speech recognition writes what it heard, so the rules
allow for the usual mishearings and spellings ("tires", "tyre's", "box" heard as "boxx").

Drivers can be named ("where am I gaining on Lando?"): `driver_in` finds the car from the game's
surnames or a first name. Questions that match nothing are answered "Say again?" and logged with intent "unknown" in
data/ptt/questions.jsonl: that log is how new phrasings get added over time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    name: str
    text: str                    # the normalised text it was matched on


# Drivers can be named by first name ("gaining on Lando?"); the game only sends surnames.
FIRST_NAMES = {
    "max": "verstappen", "yuki": "tsunoda", "lando": "norris", "oscar": "piastri",
    "charles": "leclerc", "lewis": "hamilton", "george": "russell", "kimi": "antonelli",
    "andrea": "antonelli", "fernando": "alonso", "lance": "stroll", "pierre": "gasly",
    "franco": "colapinto", "jack": "doohan", "esteban": "ocon", "ollie": "bearman",
    "oliver": "bearman", "alex": "albon", "alexander": "albon", "carlos": "sainz",
    "liam": "lawson", "isack": "hadjar", "nico": "hulkenberg", "gabriel": "bortoleto",
    "valtteri": "bottas", "sergio": "perez", "checo": "perez", "arvid": "lindblad",
}
SURNAMES = set(FIRST_NAMES.values()) | {"kimi antonelli"}
_DRIVER = r"\b(" + "|".join(sorted(set(FIRST_NAMES) | SURNAMES, key=len, reverse=True)) + r")\b"
_TURN = (r"\bturn (\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
         r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b")

# (intent, patterns). A pattern is a regex searched in the normalised text. Order matters: the
# first intent with a matching pattern wins, so specific questions come before general ones.
RULES: list[tuple[str, list[str]]] = [
    ("radio_check", [r"\bradio check\b", r"\bcan you hear me\b", r"\bdo you (read|copy)\b"]),
    ("quiet", [r"\b(be )?quiet\b", r"\bstop talking\b", r"\bshut up\b", r"\bsilence\b",
               r"\bleave me alone\b", r"\bno more (calls|talking)\b"]),
    ("tempo_chatty", [r"\b(talk|say) more\b", r"\bmore (chatty|talk|info|information|detail)\b",
                      r"\bbe (more )?chatty\b", r"\bchattier\b", r"\bmore often\b"]),
    ("tempo_terse", [r"\b(talk|say) less\b", r"\bless (chatty|talk|talking|info|detail)\b",
                     r"\bkeep it short\b", r"\bshort(er)? calls\b", r"\bless often\b",
                     r"\bessentials only\b", r"\btone it down\b"]),
    ("talk", [r"\btalk to me\b", r"\bkeep me (posted|updated|informed)\b",
              r"\bback to normal\b", r"\bresume\b", r"\byou can talk\b"]),
    ("box_now", [r"\bwhat if i (box|pit|stop)\b", r"\b(box|pit|stop)(ping)? (now|this lap)\b",
                 r"\bshould i (box|pit|stop)\b", r"\bif i (box|pit|stop) now\b"]),
    ("plan", [r"\bplan\b", r"\bstrategy\b", r"\bwhen (do|should|will) (i|we) (box|pit|stop)\b",
              r"\bwhen('s| is) (the|my|our) (stop|pit ?stop|box)\b", r"\bhow many stops\b",
              r"\bpit window\b", r"\bwhich tyres? next\b"]),
    # A named driver: "where am I gaining on Lando?", "gap to Norris?" (27 Oct race)
    ("gap_driver", [_DRIVER + r".*\b(gap|how far|interval)\b",
                    r"\b(gap|how far|interval)\b.*" + _DRIVER]),
    ("compare_driver", [_DRIVER + r".*\b(gain|gaining|losing|lose|quicker|faster|slower|pace|"
                        r"compare|catching|time|speed)\b",
                        r"\b(gain|gaining|losing|lose|quicker|faster|slower|pace|compare|"
                        r"catching|time|speed|versus|vs|against)\b.*" + _DRIVER]),
    # A corner: "how was that exit?" (heard as "how does that exit", 27 Oct race), "turn four?"
    ("corner", [r"\bhow (was|did|does|is|'s) (that|the|my) (exit|corner|turn|entry|braking|"
                r"apex|chicane|hairpin|line)\b",
                r"\b(that|the last) (exit|corner|entry|braking zone|chicane|hairpin)\b",
                r"\bhow (was|did|does|is) " + _TURN[2:], _TURN]),
    ("gaining_time", [r"\bwhere (am i|i'm|are we) (gaining|quicker|faster|good)\b",
                      r"\bwhere('s| is) my (strength|best)\b", r"\bgaining time\b"]),
    ("losing_time", [r"\bwhere (am i|i'm|are we) (losing|slow)\b", r"\blosing time\b",
                     r"\bwhich corner\b", r"\bwhere can i (gain|improve|find time|be quicker)\b",
                     r"\bwhere('s| is) the time\b", r"\bimprove (my )?pace\b"]),
    ("damage", [r"\bdamage\b", r"\bwing\b", r"\bis the car (ok|okay|alright|all right)\b"]),
    ("tyres", [r"\btyres?\b", r"\bwear\b", r"\brubber\b", r"\bgrip\b",
               r"\bfronts\b", r"\brears\b"]),
    ("best_lap", [r"\b(best|fastest|quickest) lap\b", r"\bpersonal best\b"]),
    ("gap_leader", [r"\b(gap|how far|interval)( back)? (to|behind|from|off) (the )?lead(er)?\b",
                    r"\bwho('s| is) (leading|in the lead|p ?one)\b"]),
    ("gap_behind", [r"\bgap behind\b", r"\bbehind( me)?\b", r"\bcar behind\b",
                    r"\bwho('s| is) behind\b"]),
    ("gap_ahead", [r"\bgap( ahead| in front| to the car)?\b", r"\bahead\b", r"\bin front\b",
                   r"\bwho('s| is) (ahead|in front)\b", r"\binterval\b"]),
    ("lap", [r"\bhow was (that|the|my) lap\b", r"\blast lap\b", r"\blap time\b",
             r"\bhow('s| is) (my|the) pace\b", r"\bpace\b", r"\bdelta\b"]),
    ("fuel", [r"\bfuel\b", r"\bgas\b", r"\blift and coast\b"]),
    ("weather", [r"\bweather\b", r"\brain(ing)?\b", r"\btrack temp", r"\btemperature\b"]),
    ("laps_left", [r"\bhow many laps\b", r"\blaps (left|to go|remaining)\b", r"\bwhat lap\b"]),
    ("position", [r"\bposition\b", r"\bwhat place\b", r"\bp ?what\b",
                  r"\bwhere am i( running| placed| in the race)?$"]),
]

_FIXES = [
    (r"\btire", "tyre"), (r"\btyre's\b", "tyres"), (r"\bboxx\b", "box"), (r"\bpitt\b", "pit"),
    (r"\bfor the car\b", "to the car"), (r"\bp1\b", "p one"),
]


def normalise(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    for pat, rep in _FIXES:
        t = re.sub(pat, rep, t)
    return re.sub(r"\s+", " ", t).strip()


def match(text: str) -> Intent:
    t = normalise(text)
    for name, patterns in RULES:
        if any(re.search(p, t) for p in patterns):
            return Intent(name, t)
    return Intent("unknown", t)


def driver_in(text: str, names: list[str | None]) -> int | None:
    """The car index of a driver named in the text: a surname as the game sends it, or a first
    name from FIRST_NAMES. None if no driver on the grid is named."""
    t = normalise(text)
    words = set(t.split())
    wanted = {FIRST_NAMES[w] for w in words if w in FIRST_NAMES} | words
    for i, n in enumerate(names):
        if n and any(part.lower() in wanted for part in n.split()):
            return i
    return None


_NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}


def turn_in(text: str) -> int | None:
    """'how was turn four' -> 4."""
    m = re.search(_TURN, normalise(text))
    if not m:
        return None
    w = m.group(1)
    return int(w) if w.isdigit() else _NUMBERS[w]
