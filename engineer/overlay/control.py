"""Turning the lap delta overlay on and off, and moving it, from the app window (Settings).

`OverlayControl` owns the window process for `engineer run`: `set_enabled` starts or stops it
(off the asyncio loop: starting waits for the window to report ready, stopping joins the
process), `move` unlocks it so you can drag it, `lock` (or double-clicking the overlay) locks it
where it is and saves the spot. It's also what the engine feeds (`send`, via OverlayFeed), so
the feed is wired once and simply does nothing while the overlay is off.

States: off, starting, on (locked), moving, failed (with `error`). Windows only (`available`):
the overlay sits over the game on this PC.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from engineer.overlay.process import (
    OverlayFeed,
    OverlayProcess,
    save_placed,
    settings_of,
)

log = logging.getLogger(__name__)


class OverlayControl:
    def __init__(self, cfg, engine, config_path=None, process_factory=OverlayProcess,
                 platform: str = sys.platform):
        self.cfg, self.config_path = cfg, config_path
        self.factory = process_factory
        self.available = platform == "win32"
        self.process: OverlayProcess | None = None
        self.state = "off"
        self.error: str | None = None
        self._lock = asyncio.Lock()
        self._save = save_placed(config_path)
        engine.pipeline.after_packet.append(OverlayFeed(engine.live_delta, self).after_packet)

    def status(self) -> dict:
        return {"available": self.available, "enabled": self.cfg.overlay.enabled,
                "state": self.state, "error": self.error}

    # -- switching -------------------------------------------------------------------------------

    async def set_enabled(self, on: bool) -> None:
        self.cfg.overlay.enabled = on
        async with self._lock:
            if on and self.process is None:
                await self._start(locked=True)
            elif not on and self.process is not None:
                proc, self.process = self.process, None
                await asyncio.to_thread(proc.close)
                self.state, self.error = "off", None

    async def _start(self, locked: bool) -> None:
        if not self.available:
            self.state, self.error = "failed", "the overlay is Windows only"
            return
        self.state, self.error = "starting", None
        proc = self.factory(settings_of(self.cfg.overlay), on_placed=self._placed)
        ok = await asyncio.to_thread(proc.start, locked)
        if not ok:
            self.state, self.error = "failed", "the overlay window didn't open"
            return
        if not self.cfg.overlay.enabled:          # switched off while it was starting
            await asyncio.to_thread(proc.close)
            self.state = "off"
            return
        self.process = proc
        self.state = "on" if locked else "moving"

    async def move(self) -> None:
        """Unlock it: drag it, scroll to resize, then double-click it or press Lock."""
        if self.process is None:
            await self.set_enabled(True)
        if self.process is not None:
            self.process.set_locked(False)
            self.state = "moving"

    def lock(self) -> None:
        """Lock it where it is and save the spot (same as double-clicking the overlay)."""
        if self.process is not None:
            self.process.set_locked(True, save=True)

    def _placed(self, pos: dict) -> None:
        ov = self.cfg.overlay
        ov.x, ov.y, ov.scale = int(pos["x"]), int(pos["y"]), float(pos["scale"])
        self._save(pos)
        if self.process is not None:
            self.state = "on"

    # -- feeding ---------------------------------------------------------------------------------

    def send(self, reading) -> None:
        if self.process is not None:
            self.process.send(reading)

    def poll(self) -> None:
        if self.process is None:
            return
        self.process.poll()
        if not self.process.running:              # the window closed or crashed
            self.process = None
            self.state, self.error = "failed", "the overlay window closed"

    def close(self) -> None:
        if self.process is not None:
            self.process.close()
            self.process = None
