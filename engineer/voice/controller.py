"""The push-to-talk button: LB on an Xbox controller (XInput) or L1 on a PlayStation controller
(the winmm joystick API).

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


# winmm joystick API (Windows multimedia): how Windows sees DirectInput game controllers such as
# the PlayStation DualShock 4 and DualSense. Their buttons arrive in this order (1-based):
# Square, Cross, Circle, Triangle, L1, R1, L2, R2, Share/Create, Options, L3, R3, PS, Touchpad.
PS_BUTTONS = {"SQUARE": 1, "CROSS": 2, "CIRCLE": 3, "TRIANGLE": 4, "L1": 5, "R1": 6, "L2": 7,
              "R2": 8, "SHARE": 9, "CREATE": 9, "OPTIONS": 10, "L3": 11, "R3": 12}
# PlayStation button -> the Xbox button in the same place, for when Steam Input (or DS4Windows)
# presents the PlayStation controller as an Xbox one.
PS_TO_XBOX = {"L1": "LB", "R1": "RB", "CROSS": "A", "CIRCLE": "B", "SQUARE": "X",
              "TRIANGLE": "Y", "OPTIONS": "START", "SHARE": "BACK", "CREATE": "BACK",
              "L3": "LS", "R3": "RS"}
JOY_RETURNBUTTONS = 0x80
MAX_JOYSTICKS = 16
RESCAN_S = 2.0


class _JoyInfoEx(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint),
                ("dwXpos", ctypes.c_uint), ("dwYpos", ctypes.c_uint), ("dwZpos", ctypes.c_uint),
                ("dwRpos", ctypes.c_uint), ("dwUpos", ctypes.c_uint), ("dwVpos", ctypes.c_uint),
                ("dwButtons", ctypes.c_uint), ("dwButtonNumber", ctypes.c_uint),
                ("dwPOV", ctypes.c_uint), ("dwReserved1", ctypes.c_uint),
                ("dwReserved2", ctypes.c_uint)]


class PlayStationButton:
    """A button on a PlayStation controller (DualShock 4 / DualSense, USB or Bluetooth), read
    through the winmm joystick API; also accepts the matching Xbox button, in case Steam Input
    shows the controller as an Xbox one. Reading is shared, like XInput: the game keeps working.

    Not yet tried on a real DualSense: if L1 isn't picked up, `voice.ps_button_number` sets the
    button number (python -m engineer.voice.setup shows which number lights up)."""

    def __init__(self, button: str = "L1", number: int | None = None):
        name = button.upper()
        if number is None and name not in PS_BUTTONS:
            raise ValueError(f"unknown button {button!r}; one of {', '.join(PS_BUTTONS)}")
        self.mask = 1 << ((number or PS_BUTTONS[name]) - 1)
        self.xbox = XInputButton(PS_TO_XBOX[name]) if name in PS_TO_XBOX else None
        self._winmm = None
        if sys.platform == "win32":
            try:
                self._winmm = ctypes.windll.winmm
            except OSError:
                self._winmm = None
        self._info = _JoyInfoEx()
        self._info.dwSize = ctypes.sizeof(_JoyInfoEx)
        self._info.dwFlags = JOY_RETURNBUTTONS
        self._ids: list[int] = []
        self._scanned = -RESCAN_S

    @property
    def available(self) -> bool:
        return self._winmm is not None

    def _read(self, i: int) -> int | None:
        if self._winmm.joyGetPosEx(i, ctypes.byref(self._info)) != 0:
            return None
        return self._info.dwButtons

    def connected(self) -> list[int]:
        if self._winmm is None:
            return []
        return [i for i in range(MAX_JOYSTICKS) if self._read(i) is not None]

    def pressed(self, now: float | None = None) -> bool:
        if self.xbox is not None and self.xbox.pressed():
            return True
        if self._winmm is None:
            return False
        import time
        now = time.monotonic() if now is None else now
        # Asking about an empty joystick slot is slow, so the connected ones are looked up again
        # only every RESCAN_S; in between just those are read.
        if now - self._scanned >= RESCAN_S:
            self._ids, self._scanned = self.connected(), now
        return any((b := self._read(i)) is not None and b & self.mask for i in self._ids)


def _button_name(controller_type: str, button: str | None) -> str:
    """The configured button if it exists on this kind of controller, else LB / L1 (an "LB" left
    in a settings file doesn't apply to a PlayStation controller)."""
    names = PS_BUTTONS if controller_type == "playstation" else BUTTONS
    if button and button.upper() in names:
        return button.upper()
    return "L1" if controller_type == "playstation" else "LB"


def make_button(controller_type: str, button: str | None = None,
                controller: int | None = None, ps_number: int | None = None):
    """The push-to-talk button for an Xbox or PlayStation controller (LB / L1 by default)."""
    name = _button_name(controller_type, button)
    if controller_type == "playstation":
        return PlayStationButton(name, ps_number)
    return XInputButton(name, controller)


def button_label(controller_type: str, button: str | None = None) -> str:
    return _button_name(controller_type, button)


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
