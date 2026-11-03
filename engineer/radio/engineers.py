"""Who's on the radio: the engineer voices you can pick in the app window (Settings tab).

Each engineer is a Piper voice (downloaded the first time it's picked, ~20-110 MB) plus how fast
it talks, and optionally a personality that rewrites what's said around the facts. Every
personality keeps the call itself word for word ("Box this lap, box this lap. Hards." is always
in there), so even the bad one is safe to race with.

Kevin is the joke: first day on work experience, panics, apologises, hedges everything, cheers
too loudly, and still gets every box call out. His additions are drawn from a seeded random
generator, so a replay says the same thing every time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Engineer:
    key: str
    name: str
    blurb: str                       # one line for the Settings card
    voice: str                       # Piper voice (rhasspy/piper-voices)
    length_scale: float = 1.0        # < 1 talks faster
    personality: str | None = None   # None, or "intern"
    sample: str = "Box this lap, box this lap. Hards. You'll come out behind Sainz."


ENGINEERS = (
    Engineer("northern", "Gary", "Northern English, gruff and steady. The original.",
             "en_GB-northern_english_male-medium"),
    Engineer("scottish", "Alan", "Calm Scottish veteran. Has never once raised his voice.",
             "en_GB-alan-medium"),
    Engineer("english", "Cori", "Clear, crisp English. The polished race-control sound.",
             "en_GB-cori-high"),
    Engineer("southern", "Sophie", "Southern English, a bit lo-fi. Sounds most like real radio.",
             "en_GB-southern_english_female-low"),
    Engineer("american", "Ryan", "American, spotter energy. Quick and to the point.",
             "en_US-ryan-high", length_scale=0.95),
    Engineer("intern", "Kevin", "First day on work experience. Panics, apologises, cheers "
             "far too loudly. Does get the box calls right. Mostly.",
             "en_US-joe-medium", length_scale=0.88, personality="intern",
             sample="Oh! Oh, okay. Box this lap, box this lap. Hards. "
                    "Sorry. Was that loud? That felt loud."),
)
BY_KEY = {e.key: e for e in ENGINEERS}
DEFAULT = "northern"


def engineer(key: str | None) -> Engineer:
    return BY_KEY.get(key or DEFAULT, BY_KEY[DEFAULT])


# -- Kevin ------------------------------------------------------------------------------------

_URGENT_IN = ["Oh! Oh, okay.", "Right. Right right right.", "Um. This one's important.",
              "Okay don't panic. I'm not panicking."]
_URGENT_OUT = ["Sorry. Was that loud? That felt loud.", "I think. No. Definitely. Yes.",
               "Oh god. Okay. Good.", "I wrote it on my hand."]
_GOOD_IN = ["Yes! YES! Sorry. Ahem.", "Oh that was so cool.", "Whoa. Wait, was that us?",
            "Everyone in the garage just stood up.", "I just high-fived a mechanic. He didn't "
            "want to.", "Get in! Is that what you say? Get in!"]
_FINISH_IN = ["Is it over? It's over!", "Okay I'm crying a bit.", "We did it! You did it. I "
              "watched."]
_FINISH_OUT = ["I'm telling my mum.", "Can I put this on my CV?", "Same time next week? Do I "
               "come back next week?"]
_BAD_IN = ["Oh no. Oh no no no.", "Okay, so, that's not ideal.", "Hmm. I didn't see that.",
           "Was that my fault? That wasn't my fault."]
_ANSWER_IN = ["Um, let me check. Okay.", "Great question. Uh.", "Hang on, wrong screen. Okay.",
              "Oh, me? Right."]
_ANSWER_OUT = ["Don't quote me on that.", "I asked someone.", "Is that what you meant?"]
_PLAN_OUT = ["I did that on a spreadsheet. Mostly.", "The computer said that, not me.",
             "Pretty sure that's right."]
_LAP_OUT = ["Is that good? That sounds good.", "I like that number.", "Nice. I think."]
_CORNER_OUT = ["That's what the screen says, anyway.", "I've never driven, to be fair.",
               "You probably knew that."]
_ANY_IN = ["Uh,", "Okay so,", "Sorry, is this on? Okay.", "Hi. It's Kevin.",
           "Sorry, I was eating."]
_ANY_OUT = ["Over. Is it over? Over.", "Sorry.", "Cool cool cool."]

_URGENT = {"box", "safety_car", "box_now", "racecraft_penalty", "racecraft_damage"}
_BAD_WORDS = ("lost", "past you", "got you", "gone by", "slipped", "penalty", "damage")


class Intern:
    """Kevin's radio manner. Wraps the call; never changes it."""

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self.recent: list[str] = []          # not the same line twice in a short while

    def _pick(self, lines: list[str], chance: float = 1.0) -> str:
        if self.rng.random() >= chance:
            return ""
        fresh = [x for x in lines if x not in self.recent] or lines
        line = self.rng.choice(fresh)
        self.recent = (self.recent + [line])[-6:]
        return line

    def __call__(self, topic: str, text: str) -> str:
        low = text.lower()
        if topic in _URGENT:
            before, after = self._pick(_URGENT_IN), self._pick(_URGENT_OUT, 0.7)
        elif topic == "racecraft_finish":
            before, after = self._pick(_FINISH_IN), self._pick(_FINISH_OUT)
        elif topic.startswith("racecraft_"):
            bad = any(w in low for w in _BAD_WORDS)
            before = self._pick(_BAD_IN if bad else _GOOD_IN, 0.8)
            after = self._pick(_ANY_OUT, 0.25)
        elif topic in ("answer", "prerace_plan"):
            before, after = self._pick(_ANSWER_IN, 0.8), self._pick(_ANSWER_OUT, 0.4)
        elif topic in ("strategy_plan", "prerace", "box_shift"):
            before, after = self._pick(_ANY_IN, 0.3), self._pick(_PLAN_OUT, 0.8)
        elif topic == "lap_delta":
            before, after = self._pick(_ANY_IN, 0.2), self._pick(_LAP_OUT, 0.6)
        elif topic in ("corner", "coaching", "coaching_summary"):
            before, after = self._pick(_ANY_IN, 0.3), self._pick(_CORNER_OUT, 0.6)
        else:
            before, after = self._pick(_ANY_IN, 0.4), self._pick(_ANY_OUT, 0.4)
        if before.endswith(","):            # "Okay so, turn four ..."
            text = text[:1].lower() + text[1:]
        return " ".join(p for p in (before, text, after) if p)


def personality(key: str | None):
    """The text wrapper for an engineer: f(topic, text) -> text, or None to say calls as they
    are."""
    e = engineer(key)
    return Intern() if e.personality == "intern" else None
