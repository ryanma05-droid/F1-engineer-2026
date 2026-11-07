"""Wording variety: the same call said a few different ways, race to race.

A real engineer has a handful of ways to say each thing ("great start, up eight places" /
"brilliant launch, eight places gained"): familiar, never word-for-word the same every race.
Each call that varies lists its wordings in phrases.py and asks `VARIETY.pick(key, options)`.
The facts (positions, gaps, laps, tyres) are in every wording; only the words around them change.

- A wording used recently for that call isn't picked again (`avoid_count`: every wording of a
  short list is used before one comes back, longer lists pick from the two least recent), and
  the memory carries
  over between races (data/radio/variety.json, live only), so the next race doesn't open with
  the same start call as the last.
- Live, each session gets a fresh random seed. Replays seed from the session UID, so the same
  recording always gives the same calls.
- Off (`enabled = False`): the old fixed wording, in rotation order (n). The test suite runs this
  way (tests/conftest.py) so expected texts stay exact; tests/test_variety.py checks it on.

Box calls keep "box" in every wording, and safety-critical words ("box this lap, box this lap")
never vary.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
from pathlib import Path

log = logging.getLogger(__name__)

KEEP = 12            # wordings remembered per call


def avoid_count(n_options: int) -> int:
    """How many of a call's latest wordings are kept back (7 Nov: the stock lines came round too
    often). Up to 3 options: all but one, so each is used once before any comes back; more:
    all but two, a choice of the two least recent."""
    return n_options - 1 if n_options <= 3 else n_options - 2


class Variety:
    def __init__(self):
        self.enabled = os.environ.get("RACE_ENGINEER_VARIETY", "1") != "0"
        self.rng = random.Random(0)
        self.recent: dict[str, list[int]] = {}
        self.path: Path | None = None              # live only: memory across races
        self.live = False                          # fresh random seed each session

    # -- choosing ---------------------------------------------------------------------------------

    def pick(self, key: str, options: list, n: int = 0):
        """One of `options` for this call. Off: options[n % len] (the old rotation)."""
        if not options:
            raise ValueError("no options")
        if not self.enabled or len(options) == 1:
            return options[n % len(options)]
        recent = self.recent.get(key, [])
        avoid = set(recent[-avoid_count(len(options)):])
        idx = self.rng.choice([i for i in range(len(options)) if i not in avoid])
        self.recent[key] = (recent + [idx])[-KEEP:]
        return options[idx]

    # -- sessions ---------------------------------------------------------------------------------

    def new_session(self, session_uid: int | None) -> None:
        """Live: a new random seed (every race differs). Replay: the session UID (repeatable)."""
        seed = random.SystemRandom().getrandbits(64) if self.live else (session_uid or 0)
        self.rng = random.Random(seed)
        if not self.live:
            self.recent = {}

    def load(self, path: Path) -> None:
        self.path, self.live = Path(path), True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.recent = {k: [int(i) for i in v][-KEEP:] for k, v in data.get("recent", {}).items()}
        except (OSError, ValueError, AttributeError):
            self.recent = {}

    def save_in_background(self) -> None:
        if self.path is None:
            return
        data, path = {"recent": dict(self.recent)}, self.path

        def write():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(path)
            except OSError as exc:
                log.warning("could not save %s (%s)", path, exc)
        threading.Thread(target=write, daemon=True).start()


VARIETY = Variety()


def pick(key: str, options: list, n: int = 0):
    return VARIETY.pick(key, options, n)
