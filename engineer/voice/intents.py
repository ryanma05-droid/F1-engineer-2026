"""What did the driver ask? Keyword rules over the recognised text.

Rules are tried in order and the first match wins, so more specific questions come first ("what
if I box now" before "when do I box"). Speech recognition writes what it heard, so the rules
allow for the usual mishearings and spellings ("tires", "tyre's", "box" heard as "boxx").

Questions that match nothing are answered "Say again?" and logged with intent "unknown" in
data/ptt/questions.jsonl: that log is how new phrasings get added over time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    name: str
    text: str                    # the normalised text it was matched on


# (intent, patterns). A pattern is a regex searched in the normalised text.
RULES: list[tuple[str, list[str]]] = [
    ("radio_check", [r"\bradio check\b", r"\bcan you hear me\b", r"\bdo you (read|copy)\b"]),
    ("quiet", [r"\b(be )?quiet\b", r"\bstop talking\b", r"\bshut up\b", r"\bsilence\b",
               r"\bleave me alone\b", r"\bno more (calls|talking)\b"]),
    ("talk", [r"\btalk to me\b", r"\bkeep me (posted|updated|informed)\b",
              r"\bback to normal\b", r"\bresume\b", r"\byou can talk\b"]),
    ("box_now", [r"\bwhat if i (box|pit|stop)\b", r"\b(box|pit|stop)(ping)? (now|this lap)\b",
                 r"\bshould i (box|pit|stop)\b", r"\bif i (box|pit|stop) now\b"]),
    ("plan", [r"\bplan\b", r"\bstrategy\b", r"\bwhen (do|should|will) (i|we) (box|pit|stop)\b",
              r"\bwhen('s| is) (the|my|our) (stop|pit ?stop|box)\b", r"\bhow many stops\b",
              r"\bpit window\b", r"\bwhich tyres? next\b"]),
    ("tyres", [r"\btyres?\b", r"\btires?\b", r"\bwear\b", r"\brubber\b", r"\bgrip\b",
               r"\bfronts\b", r"\brears\b"]),
    ("gap_behind", [r"\bgap behind\b", r"\bbehind( me)?\b", r"\bcar behind\b",
                    r"\bwho('s| is) behind\b"]),
    ("gap_ahead", [r"\bgap( ahead| in front| to the car)?\b", r"\bahead\b", r"\bin front\b",
                   r"\bwho('s| is) (ahead|in front)\b", r"\binterval\b"]),
    ("losing_time", [r"\bwhere (am i|i'm|are we) (losing|slow)\b", r"\blosing time\b",
                     r"\bwhich corner\b", r"\bwhere can i (gain|improve|find time)\b",
                     r"\bwhere('s| is) the time\b"]),
    ("lap", [r"\bhow was (that|the|my) lap\b", r"\blast lap\b", r"\blap time\b",
             r"\bhow('s| is) (my|the) pace\b", r"\bpace\b"]),
    ("fuel", [r"\bfuel\b", r"\bgas\b", r"\blift and coast\b"]),
    ("laps_left", [r"\bhow many laps\b", r"\blaps (left|to go|remaining)\b"]),
    ("position", [r"\bposition\b", r"\bwhat place\b", r"\bwhere am i\b", r"\bp ?what\b"]),
]

_FIXES = [
    (r"\btire", "tyre"), (r"\btyre's\b", "tyres"), (r"\bboxx\b", "box"), (r"\bpitt\b", "pit"),
    (r"\bfor the car\b", "to the car"),
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
