"""The main-process side of the lap delta overlay: starts the window process and feeds it.

`OverlayProcess.send(reading)` never blocks: it puts a small dict on a queue at most `RATE_HZ`
times a second (always at once when the reading appears or disappears, or a corner split comes),
which is plenty for a number that changes 10 times a second. The window's position, when you lock
it after moving, comes back as a "placed" message and is saved with `on_placed` (cli: into
config.local.yaml). Display only: wall-clock throttling here never touches the race model.
"""

from __future__ import annotations

import logging
import queue as queue_mod
import time

from engineer.overlay.view import message
from engineer.workers.pool import start_worker

log = logging.getLogger(__name__)

RATE_HZ = 15
ALIVE_EVERY_S = 2.0


def settings_of(ov) -> dict:
    """The window's settings from config.Overlay."""
    return {"x": ov.x, "y": ov.y, "scale": ov.scale, "opacity": ov.opacity, "bar_s": ov.bar_s}


class OverlayProcess:
    def __init__(self, settings: dict, on_placed=None, clock=time.monotonic):
        self.settings = dict(settings)
        self.on_placed = on_placed            # called with {"x", "y", "scale"}
        self.clock = clock
        self._proc = self._inbox = self._outbox = None
        self._sent_at = -1e9
        self._last: dict | None = None
        self._alive_at = 0.0

    @property
    def running(self) -> bool:
        return self._proc is not None

    def start(self, locked: bool = True, wait_ready_s: float = 10.0) -> bool:
        self._proc, self._inbox, self._outbox = start_worker(
            _main, {**self.settings, "locked": locked})
        try:
            ok = self._outbox.get(timeout=wait_ready_s) == ("ready",)
        except queue_mod.Empty:
            ok = False
        if not ok:
            log.warning("the lap delta overlay didn't start (no window system, or no Tk?)")
            self.close()
        return ok

    def send(self, reading) -> None:
        """A DeltaReading or None (nothing to show)."""
        if self._proc is None:
            return
        msg = message(reading)
        now = self.clock()
        appears = (msg is None) != (self._last is None)
        corner = msg is not None and msg["corner"] != (self._last or {}).get("corner")
        if not appears and not corner and (msg == self._last or now - self._sent_at < 1 / RATE_HZ):
            return
        self._sent_at, self._last = now, msg
        self._inbox.put({"reading": msg})
        self.poll()

    def set_locked(self, locked: bool, save: bool = False) -> None:
        """save: also report the spot back (a "placed" message), as double-clicking does."""
        if self._inbox is not None:
            self._inbox.put({"locked": locked, "save": save})

    def update_settings(self, **changes) -> None:
        self.settings.update(changes)
        if self._inbox is not None:
            self._inbox.put({"settings": changes})

    def poll(self) -> None:
        """Pick up messages from the window (its position after a move; closed)."""
        if self._outbox is None:
            return
        try:
            while True:
                kind, *rest = self._outbox.get_nowait()
                if kind == "placed":
                    self.settings.update(rest[0])
                    if self.on_placed is not None:
                        self.on_placed(rest[0])
                elif kind == "closed":
                    self._proc = None
                    return
        except queue_mod.Empty:
            pass
        now = self.clock()
        if now - self._alive_at >= ALIVE_EVERY_S:
            self._alive_at = now
            if self._proc is not None and not self._proc.is_alive():
                log.warning("the lap delta overlay closed")
                self._proc = None

    def close(self, timeout_s: float = 3.0) -> None:
        if self._proc is None:
            return
        self._inbox.put(None)
        self._proc.join(timeout_s)
        self.poll()
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
        self._proc = None


def _main(inbox, outbox, settings: dict) -> None:
    from engineer.overlay.window import overlay_main
    overlay_main(inbox, outbox, settings)


class OverlayFeed:
    """Hooked into Pipeline.after_packet: sends the live delta after each Lap Data packet."""

    def __init__(self, live_delta, overlay: OverlayProcess):
        from engineer.ingest.formats.f1_2025 import PacketId
        self.live_delta, self.overlay, self._lap_data = live_delta, overlay, PacketId.LAP_DATA

    def after_packet(self, packet_id) -> None:
        if packet_id == self._lap_data:
            self.overlay.send(self.live_delta.current)


def save_placed(config_path=None):
    """on_placed for the app: the position goes into config.local.yaml."""
    def save(pos: dict) -> None:
        from engineer.config import save_local
        try:
            save_local({"overlay": {"x": int(pos["x"]), "y": int(pos["y"]),
                                    "scale": float(pos["scale"])}}, config_path)
        except OSError as exc:
            log.warning("could not save the overlay position: %s", exc)
    return save
