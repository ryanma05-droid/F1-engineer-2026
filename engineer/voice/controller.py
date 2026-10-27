"""The push-to-talk button: a button on an Xbox controller, read through XInput.

XInput is how Windows talks to Xbox controllers, and any number of programs can read the same
controller at once, so F1 25 keeps working with it. We only read the buttons (no rumble, no
writes). The game still sees the press too: if LB does something in your F1 25 controls, unbind
it there (Settings > Controls) so asking a question doesn't also trigger that.

Polled every 20 ms from the main loop: one XInputGetState call is a few microseconds.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

# XINPUT_GAMEPAD wButtons bits (Microsoft XInput documentation).
BUTTONS = {
    "DPAD_UP": 0x0001, "DPAD_DOWN": 0x0002, "DPAD_LEFT": 0x0004, "DPAD_RIGHT": 0x0008,
    "START": 0x0010, "MENU": 0x0010, "BACK": 0x0020, "VIEW": 0x0020,
    "LS": 0x0040, "RS": 0x0080, "LB": 0x0100, "RB": 0x0200,
    "A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000,
}
MAX_CONTROLLERS = 4


class _Gamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _Gamepad)]


def _load_xinput():
    if sys.platform != "win32":
        return None
    for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return getattr(ctypes.windll, dll)
        except OSError:
            continue
    return None


class XInputButton:
    """pressed() -> True while the button is held on any connected controller (or the chosen
    one). available is False off Windows or without XInput."""

    def __init__(self, button: str = "LB", controller: int | None = None):
        if button.upper() not in BUTTONS:
            raise ValueError(f"unknown button {button!r}; one of {', '.join(BUTTONS)}")
        self.mask = BUTTONS[button.upper()]
        self.controller = controller
        self._xinput = _load_xinput()
        self._state = _State()

    @property
    def available(self) -> bool:
        return self._xinput is not None

    def connected(self) -> list[int]:
        if self._xinput is None:
            return []
        return [i for i in range(MAX_CONTROLLERS)
                if self._xinput.XInputGetState(i, ctypes.byref(self._state)) == 0]

    def pressed(self) -> bool:
        if self._xinput is None:
            return False
        pads = [self.controller] if self.controller is not None else range(MAX_CONTROLLERS)
        for i in pads:
            if self._xinput.XInputGetState(i, ctypes.byref(self._state)) == 0 \
                    and self._state.Gamepad.wButtons & self.mask:
                return True
        return False


class PushToTalk:
    """Turns button polls into press/release calls. A tap shorter than min_press_s is ignored
    (a brush of the button); holding longer than max_s ends the question anyway."""

    def __init__(self, button, on_press, on_release, on_cancel=None,
                 min_press_s: float = 0.25, max_s: float = 10.0):
        self.button = button
        self.on_press, self.on_release = on_press, on_release
        self.on_cancel = on_cancel or (lambda: None)
        self.min_press_s, self.max_s = min_press_s, max_s
        self.down_at: float | None = None
        self.capped = False

    def poll(self, now: float) -> None:
        held = self.button.pressed()
        if held and self.down_at is None and not self.capped:
            self.down_at = now
            self.on_press()
        elif held and self.down_at is not None and now - self.down_at >= self.max_s:
            self.down_at, self.capped = None, True
            self.on_release()
        elif not held:
            self.capped = False
            if self.down_at is not None:
                short = now - self.down_at < self.min_press_s
                self.down_at = None
                self.on_cancel() if short else self.on_release()
