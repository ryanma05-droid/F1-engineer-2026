"""Who's on the radio: the engineer voices you can pick in the app window (Settings tab).

Each engineer is a Piper voice (downloaded the first time it's picked, ~20-110 MB) plus how fast
it talks, and optionally a personality that rewrites what's said around the facts. Every
personality keeps the call itself word for word ("Box this lap, box this lap. Hards." is always
in there), so even the bad one is safe to race with.

Kevin is the joke: first day on work experience, panics, apologises, hedges everything, cheers
too loudly, and still gets every box call out. His additions are drawn from a seeded random
generator, so a replay says the same thing every time. How often he jokes and how he avoids
repeating himself: see the Kevin section below.
"""

from __future__ import annotations

import json
import logging
import math
import random
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


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
#
# Kevin's jokes wore thin when they repeated (7 Nov: "he did say the same jokes a bit too much").
# So:
# - big pools of lines, and a line is never said twice in a session (an empty pool means he stays
#   quiet, he doesn't repeat);
# - lines said in recent races are remembered (data/radio/kevin.json, live only, LineMemory) and
#   kept back, so the next race doesn't open with the same gags;
# - a joke on fewer calls: one line per call (before OR after it, not both), and at least
#   QUIRK_GAP_S of game time between jokes. Box calls, the safety car and penalties always get
#   his panic (that's the character), still never the same line twice;
# - a fresh shuffle each session (live: random; replays: the session UID, so a replay repeats).

QUIRK_GAP_S = 45.0           # game seconds between jokes on ordinary calls
MEMORY_KEEP = 80             # lines remembered across races

_URGENT_IN = [
    "Oh! Oh, okay.", "Right. Right right right.", "Um. This one's important.",
    "Okay don't panic. I'm not panicking.", "Oh, this is the big one.",
    "Everyone's looking at me. Okay.", "They said I could do this one.",
    "Deep breath. Okay.", "I've been practising this in the mirror.",
    "Wait, is it now? It's now.", "Right, listen carefully. Mostly to me.",
    "I've got my serious voice on.", "Okay, okay, okay.",
    "Someone just tapped my shoulder very hard.", "Ahem. Official message.",
]
_URGENT_OUT = [
    "Sorry. Was that loud? That felt loud.", "I think. No. Definitely. Yes.",
    "Oh god. Okay. Good.", "I wrote it on my hand.", "Did I say that right?",
    "Nailed it. I think I nailed it.", "My hands are shaking a bit.",
    "That's the most important thing I've ever said.", "Please do it, they'll blame me.",
    "I'm going to sit down now.",
]
_GOOD_IN = [
    "Yes! YES! Sorry. Ahem.", "Oh that was so cool.", "Whoa. Wait, was that us?",
    "Everyone in the garage just stood up.", "I just high-fived a mechanic. He didn't want to.",
    "Get in! Is that what you say? Get in!", "I spilled my tea. Worth it.",
    "Okay I screamed a bit. Into my sleeve.", "That's going in my diary.",
    "The team boss just nodded at me. I think.", "Did you see that? You did that.",
    "Oh, I've got goosebumps.", "I knocked over a whole stack of tyres. Unrelated.",
    "My headset fell off. Amazing though.", "I'm doing a little dance. Nobody's watching.",
    "Someone said 'mega'. I'm saying it too. Mega.",
]
_BAD_IN = [
    "Oh no. Oh no no no.", "Okay, so, that's not ideal.", "Hmm. I didn't see that.",
    "Was that my fault? That wasn't my fault.", "Ooh. Ouch.", "Okay, deep breaths. For both of us.",
    "That's alright. It's alright, isn't it?", "Nobody panic. I'm panicking a bit.",
    "I'll pretend I didn't see that.", "Hmm. Someone just sighed very loudly behind me.",
    "Right. Plan B. What's plan B?", "That one stung a bit.",
]
_FINISH_IN = [
    "Is it over? It's over!", "Okay I'm crying a bit.", "We did it! You did it. I watched.",
    "That's it? That's the whole race?", "Flag! I saw the flag!",
    "Oh wow. Okay. Wow.", "Everyone's taking their headsets off. So am I. Not yet.",
]
_FINISH_OUT = [
    "I'm telling my mum.", "Can I put this on my CV?",
    "Same time next week? Do I come back next week?", "Can I keep the headset?",
    "Best day of work experience ever. Only day, but still.", "I'm framing the timing sheet.",
    "Someone said I wasn't terrible. Big day.",
]
_ANSWER_IN = [
    "Um, let me check. Okay.", "Great question. Uh.", "Hang on, wrong screen. Okay.",
    "Oh, me? Right.", "Ooh, I know this one.", "Let me ask the clever ones. Okay.",
    "One sec, it's a big spreadsheet.", "Right, I'm looking. I'm looking.",
    "That's a good one.",
]
_ANSWER_OUT = [
    "Don't quote me on that.", "I asked someone.", "Is that what you meant?",
    "Ninety percent sure. Eighty.", "That's what the screen says.", "Hope that helps!",
    "Any other questions? Please not right now.",
]
_PLAN_OUT = [
    "I did that on a spreadsheet. Mostly.", "The computer said that, not me.",
    "Pretty sure that's right.", "The strategy guy nodded, so.",
    "I double checked it. Single checked it.", "That's the plan. I wrote it down this time.",
    "I'd trust it. I'm not allowed to change it anyway.",
]
_LAP_OUT = [
    "Is that good? That sounds good.", "I like that number.", "Nice. I think.",
    "Writing that one down.", "That's a lot of numbers to do in a row.",
    "Faster than my bus in, anyway.", "I've started a little graph.",
]
_CORNER_OUT = [
    "That's what the screen says, anyway.", "I've never driven, to be fair.",
    "You probably knew that.", "The data man told me to say that.",
    "I'm just reading it out.", "I'd be in the gravel, honestly.",
    "No pressure. Some pressure.",
]
_ANY_IN = [
    "Uh,", "Okay so,", "Sorry, is this on? Okay.", "Hi. It's Kevin.", "Sorry, I was eating.",
    "Right, um,", "Hello again.", "Me again.", "Okay, quick one.",
]
_ANY_OUT = [
    "Over. Is it over? Over.", "Sorry.", "Cool cool cool.", "Kevin out.",
    "That's all. For now.", "Back to my biscuit.",
]

_URGENT = {"box", "safety_car", "box_now", "racecraft_penalty", "racecraft_damage"}
_BAD_WORDS = ("lost", "past you", "got you", "gone by", "slipped", "penalty", "damage",
              "got by", "is through", "got through", "is by", "way past", " past, ")


class LineMemory:
    """Kevin's lines from recent races (live only), so the next race isn't the same gags."""

    def __init__(self):
        self.recent: list[str] = []
        self.path: Path | None = None
        self.live = False                  # fresh random shuffle each session

    def load(self, path: Path) -> None:
        self.path, self.live = Path(path), True
        try:
            self.recent = [str(x) for x in json.loads(
                self.path.read_text(encoding="utf-8")).get("recent", [])][-MEMORY_KEEP:]
        except (OSError, ValueError, AttributeError):
            self.recent = []

    def add(self, line: str) -> None:
        self.recent = ([x for x in self.recent if x != line] + [line])[-MEMORY_KEEP:]

    def save_in_background(self) -> None:
        if self.path is None:
            return
        data, path = {"recent": list(self.recent)}, self.path

        def write():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(path)
            except OSError as exc:
                log.warning("could not save %s (%s)", path, exc)
        threading.Thread(target=write, daemon=True).start()


KEVIN_MEMORY = LineMemory()


class Intern:
    """Kevin's radio manner. Wraps the call; never changes it."""

    def __init__(self, seed: int = 0, memory: LineMemory | None = None):
        self.memory = memory if memory is not None else KEVIN_MEMORY
        self._start(seed)

    def _start(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.used: set[str] = set()            # never twice in a session
        self.last_quirk = -math.inf

    def new_session(self, session_uid: int | None) -> None:
        """Live: a new random shuffle each session. Replay: from the session UID (repeatable)."""
        self._start(random.SystemRandom().getrandbits(64) if self.memory.live
                    else (session_uid or 0))

    def _pick(self, lines: list[str]) -> str:
        fresh = [x for x in lines if x not in self.used]
        if not fresh:
            return ""                          # all said this session: quiet, not a repeat
        rested = [x for x in fresh if x not in self.memory.recent]
        # Lines from recent races only once the rested ones run low (each pool keeps a few).
        pool = rested if len(rested) >= max(2, len(lines) // 4) else fresh
        line = self.rng.choice(pool)
        self.used.add(line)
        self.memory.add(line)
        return line

    def _one(self, before: list[str] | None, after: list[str] | None,
             before_share: float = 0.5) -> tuple[str, str]:
        """One line: before the call or after it."""
        if before and (not after or self.rng.random() < before_share):
            return self._pick(before), ""
        return "", self._pick(after) if after else ""

    def __call__(self, topic: str, text: str, t: float | None = None) -> str:
        """t: game time (None: no spacing between jokes, e.g. the Settings sample)."""
        low = text.lower()
        before = after = ""
        if topic in _URGENT:
            before = self._pick(_URGENT_IN)
            if self.rng.random() < 0.25:
                after = self._pick(_URGENT_OUT)
        elif topic == "racecraft_finish":
            before, after = self._pick(_FINISH_IN), self._pick(_FINISH_OUT)
        else:
            chance = 0.2
            if topic.startswith("racecraft_"):
                bad = any(w in low for w in _BAD_WORDS)
                pools, chance = ((_BAD_IN if bad else _GOOD_IN), _ANY_OUT, 0.85), 0.6
            elif topic in ("answer", "prerace_plan"):
                pools, chance = (_ANSWER_IN, _ANSWER_OUT, 0.5), 0.4
            elif topic in ("strategy_plan", "prerace", "box_shift"):
                pools, chance = (None, _PLAN_OUT, 0.0), 0.4
            elif topic == "lap_delta":
                pools = (_ANY_IN, _LAP_OUT, 0.3)
            elif topic in ("corner", "coaching", "coaching_summary"):
                pools, chance = (_ANY_IN, _CORNER_OUT, 0.3), 0.25
            else:
                pools = (_ANY_IN, _ANY_OUT, 0.5)
            spaced = t is None or t - self.last_quirk >= QUIRK_GAP_S
            if spaced and self.rng.random() < chance:
                before, after = self._one(*pools)
        if (before or after) and t is not None and topic not in _URGENT:
            self.last_quirk = t
        if before.endswith(","):            # "Okay so, turn four ..."
            text = text[:1].lower() + text[1:]
        return " ".join(p for p in (before, text, after) if p)


def personality(key: str | None):
    """The text wrapper for an engineer: f(topic, text, t) -> text, or None to say calls as
    they are."""
    e = engineer(key)
    return Intern() if e.personality == "intern" else None
