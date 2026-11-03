"""Wording variety: the same call said a few different ways, race to race.

A real engineer has a handful of ways to say each thing ("great start, up eight places" /
"brilliant launch, eight places gained"): familiar, never word-for-word the same every race.
Each call that varies lists its wordings in phrases.py and asks `VARIETY.pick(key, options)`.
The facts (positions, gaps, laps, tyres) are in every wording; only the words around them change.

- A wording used recently for that call isn't picked again (`AVOID`), and the memory carries
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

AVOID = 2            # don't repeat any of the last 2 wordings of a call (fewer for short lists)
KEEP = 4             # wordings remembered per call


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
        avoid = set(recent[-min(AVOID, len(options) - 1):])
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
