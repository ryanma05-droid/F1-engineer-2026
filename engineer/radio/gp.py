"""GP: Gary's voice with a race engineer's manner learned from real team radio.

Built from 976 broadcast engineer lines on Max Verstappen's car, 2021 and 2023-25 races and
sprints (formuladream.app's archive of broadcast radio; 2022 isn't in it), matched to the race
situation from OpenF1 timing (12 Nov). It's a style, not an impression: the lines below are
written here in that manner, not quotes, and the voice is Gary's Piper voice.

What the radio showed, and how GP uses it:
- Calm and short: median 11 words a line. GP never makes a call longer than a short add-on.
- The driver's name in 44 % of lines ("Okay Max, ..."): GP uses yours (`radio.driver_name`),
  or "mate" when it isn't set, on about a third of calls, never twice within NAME_GAP_S.
- Acknowledge first: "Understood." / "Okay, ..." / "Copy." open a lot of answers.
- The key number said twice: "Gap at 3.8, 3.8." GP echoes the last gap or time in gap calls.
- Box calls doubled and confirmed: "Box and confirm, Max. Box, confirm."
- Bad news without drama ("No dramas", "long way to go"); praise that's specific and
  measured ("very well controlled", "clinical", "textbook", "mega" at the flag).
- Closing laps get a short lift: "Ten laps to go. Ten of your finest, mate."

Like Kevin (engineers.py), GP wraps the call and never changes it: `RadioMessage.text` stays the
call; this only changes what's heard. Seeded per session, so a replay says the same.
"""

from __future__ import annotations

import math
import random
import re

NAME_GAP_S = 25.0            # game seconds between uses of your name
ECHO_GAP_S = 20.0            # ... between number echoes

_NUM = (r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[ -]?)?"
        r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
        r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|a)")
# "one point two seconds", "eight tenths", "a tenth", "three seconds"
_GAP = re.compile(rf"\b({_NUM}(?: point {_NUM})?) (seconds?|tenths?)\b", re.IGNORECASE)

_ANSWER_IN = ["Okay {n},", "Understood.", "Yep.", "Okay,", "Copy, {n}.", "So,"]
_PASS_OUT = ["Nicely done.", "Well controlled.", "Lovely, {n}.", "Good job.", "Very tidy."]
_LOST_IN = ["Okay, no dramas.", "Stay calm, {n}.", "Head down.", "Okay {n}, reset."]
_BATTLE_OUT = ["Come on, {n}.", "Keep it clean.", "Good stuff.", "That's racing."]
_FIVE_OUT = ["{laps} of your finest, {n}. Come on.", "{laps} more, nice and tidy.",
             "{laps} to go, keep doing what you're doing."]
_LAST_OUT = ["Bring it home, {n}.", "Nice and tidy to the flag.", "One more, {n}. Come on."]
_WIN_OUT = ["That was mega. Very well controlled.", "Absolutely clinical, {n}.",
            "Textbook. Well done.", "Very, very well controlled. Well done, {n}.",
            "Metronome, {n}. Brilliant."]
_PODIUM_OUT = ["Good day's work, {n}.", "We can be happy with that, {n}.",
               "Well driven, good points."]
_POINTS_OUT = ["We'll take that, {n}.", "Decent points. Good effort.", "Solid, well done."]
_BAD_OUT = ["Hard luck, {n}. We'll regroup.", "Not our day. Good effort, {n}."]
_BOX_OUT = ["Box, confirm.", "Box and confirm, {n}.", "Confirm, box."]
_CORNER_IN = ["So,", "Okay {n},", "Just a note,"]


def _repeats(extra: str, text: str) -> bool:
    """Does the add-on say what the call already says (its first three words)?"""
    words = re.findall(r"[a-z']+", extra.lower())[:3]
    return len(words) >= 2 and " ".join(words) in " ".join(re.findall(r"[a-z']+", text.lower()))


class GPStyle:
    """GP's radio manner. Wraps the call; never changes it."""

    def __init__(self, driver_name: str | None = None, seed: int = 0):
        self.name = (driver_name or "").strip() or None
        self._start(seed)

    def _start(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.recent: dict[int, list[str]] = {}
        self.last_name = -math.inf
        self.last_echo = -math.inf

    def new_session(self, session_uid: int | None) -> None:
        self._start(session_uid or 0)

    # -- helpers --------------------------------------------------------------------------------

    def _pick(self, lines: list[str]) -> str:
        """No line again until most of its list has been used."""
        key = id(lines)
        recent = self.recent.setdefault(key, [])
        fresh = [x for x in lines if x not in recent] or lines
        line = self.rng.choice(fresh)
        self.recent[key] = (recent + [line])[-max(1, len(lines) - 1):]
        return line

    def _fill(self, line: str, t: float | None, **kw) -> str:
        """Put your name (or "mate") in, only if it hasn't been used just now."""
        if "{n}" in line:
            use = t is None or t - self.last_name >= NAME_GAP_S
            if use and t is not None:
                self.last_name = t
            who = (self.name or "mate") if use else ""
            line = line.replace("{n}", who)
            line = re.sub(r"\s*,\s*([.!?])", r"\1", line)        # "Lovely, ." -> "Lovely."
            line = re.sub(r",\s*,", ",", line).replace(" ,", ",").strip()
            line = re.sub(r"^Okay\s+,", "Okay,", line)
        return line.format(**kw) if kw else line

    def _echo(self, text: str, t: float | None) -> str:
        """Say the last gap again: 'Norris ahead, one point two seconds. One point two.'"""
        if t is not None and t - self.last_echo < ECHO_GAP_S:
            return ""
        hits = _GAP.findall(text)
        if not hits:
            return ""
        num, unit = hits[-1]
        if t is not None:
            self.last_echo = t
        # "One point two." / "Eight tenths." / "Two seconds." (a bare "Two." sounds odd)
        said = num if " point " in num and unit.startswith("second") else f"{num} {unit}"
        return said[:1].upper() + said[1:] + "."

    # -- the manner ------------------------------------------------------------------------------

    def __call__(self, topic: str, text: str, t: float | None = None) -> str:
        low = text.lower()
        before = after = echo = ""
        r = self.rng.random()
        if topic == "box" and low.startswith("box this lap"):
            after = self._fill(self._pick(_BOX_OUT), t)
        elif topic == "answer":
            if r < 0.5:
                before = self._fill(self._pick(_ANSWER_IN), t)
            if re.search(r"\b(gap|ahead|behind)\b", low):
                after = echo = self._echo(text, t)
        elif topic == "racecraft_finish":
            if low.startswith("p one") or "won it" in low:
                after = self._fill(self._pick(_WIN_OUT), t)
            elif "podium" in low:
                after = self._fill(self._pick(_PODIUM_OUT), t)
            elif "tough" in low or "not our day" in low or "learn" in low:
                after = self._fill(self._pick(_BAD_OUT), t)
            else:
                after = self._fill(self._pick(_POINTS_OUT), t)
        elif topic == "racecraft_countdown":
            m = re.match(r"(\w+) laps to go", low)
            if m and r < 0.8:
                after = self._fill(self._pick(_FIVE_OUT), t, laps=m.group(1).capitalize())
            elif low.startswith("last lap") and r < 0.6:
                after = self._fill(self._pick(_LAST_OUT), t)
        elif topic == "racecraft_pass":
            bad = any(w in low for w in ("lost", "got by", "is through", "past,", "is by",
                                         "got through", "way past"))
            if bad and r < 0.5:
                before = self._fill(self._pick(_LOST_IN), t)
            elif not bad and r < 0.3:
                after = self._fill(self._pick(_PASS_OUT), t)
        elif topic in ("racecraft_battle", "racecraft_push", "racecraft_defend",
                       "racecraft_attack"):
            echo = self._echo(text, t) if topic != "racecraft_battle" else ""
            if echo:
                after = echo
            elif r < 0.35:
                after = self._fill(self._pick(_BATTLE_OUT), t)
        elif topic in ("corner", "coaching") and r < 0.3:
            before = self._fill(self._pick(_CORNER_IN), t)
        if after and after != echo and _repeats(after, text):
            after = ""                                    # "Bring it home. Bring it home."
        if before.endswith(","):                          # "So, turn four ..."
            text = text[:1].lower() + text[1:]
        return " ".join(p for p in (before, text, after) if p)
