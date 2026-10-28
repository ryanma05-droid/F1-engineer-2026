"""A one-time welcome from the engineer: said the first time you start it, never again.

"This is your race engineer. I'll call lap times, tyres, strategy and corners, and keep you
posted in battles. Hold L B any time to ask me something, like how are the tyres. Good luck out
there."

Why at start-up and not on the grid: on real races the race session starts only ~6 s before
lights out (the start lights begin straight away, and the game's clock stays at 0 until lights
out), so a 13 s welcome would talk over the start. At start-up you're still in the menus.

Remembered in data/once.json once it has been handed to a working voice (also across updates);
with no working voice yet it waits for a run where the voice works. Replays and
tests never say it: it's spoken by `engineer run` directly, not through the race pipeline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

WELCOME = "welcome"


class OnceStore:
    """Remembers things that should only ever happen once (a small JSON file)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def done(self, key: str) -> bool:
        return bool(self._load().get(key))

    def mark(self, key: str) -> None:
        data = self._load()
        data[key] = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("could not save %s (%s)", self.path, exc)


def welcome_text(button: str | None) -> str:
    """button: the push-to-talk button if push-to-talk is working, else None."""
    parts = ["This is your race engineer.",
             "I'll call lap times, tyres, strategy and corners, and keep you posted in battles."]
    if button:
        # Spelled out so the voice says "L B", not "pounds".
        parts.append(f"Hold {' '.join(button)} any time to ask me something, "
                     f"like how are the tyres.")
    parts.append("Good luck out there.")
    return " ".join(parts)
