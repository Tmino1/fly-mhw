"""
Phase 0 input-injection backend: a virtual Xbox 360 gamepad via
/dev/uinput (python-evdev), so Wine/Proton's SDL controller layer picks it
up the same way it would a physical pad.

STATUS: creates a device and can send presses; NOT yet confirmed that MHW
actually reacts to it — Steam Input may intercept/remap first (see
docs/modding_setup.md's Steam Input note). Run
scripts/verify_input_injection.py with the game focused to check.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from evdev import AbsInfo, UInput, ecodes as e

# A conservative Xbox-360-shaped capability set. Buttons/axes not needed by
# the Great Sword action space (Phase 1) are still included here since this
# class is meant to stay weapon-agnostic — weapon configs pick which of
# these they actually use.
_BUTTONS = [
    e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST,
    e.BTN_TL, e.BTN_TR, e.BTN_SELECT, e.BTN_START,
    e.BTN_THUMBL, e.BTN_THUMBR, e.BTN_MODE,
]

_AXIS_RANGE = AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)
_TRIGGER_RANGE = AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)
_HAT_RANGE = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)

_CAPABILITIES = {
    e.EV_KEY: _BUTTONS,
    e.EV_ABS: [
        (e.ABS_X, _AXIS_RANGE),
        (e.ABS_Y, _AXIS_RANGE),
        (e.ABS_RX, _AXIS_RANGE),
        (e.ABS_RY, _AXIS_RANGE),
        (e.ABS_Z, _TRIGGER_RANGE),
        (e.ABS_RZ, _TRIGGER_RANGE),
        (e.ABS_HAT0X, _HAT_RANGE),
        (e.ABS_HAT0Y, _HAT_RANGE),
    ],
}


class VirtualGamepad:
    def __init__(self, name: str = "fly-mhw virtual pad"):
        self._ui = UInput(_CAPABILITIES, name=name, vendor=0x045E, product=0x028E)

    def close(self):
        self._ui.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def press(self, button: int):
        self._ui.write(e.EV_KEY, button, 1)
        self._ui.syn()

    def release(self, button: int):
        self._ui.write(e.EV_KEY, button, 0)
        self._ui.syn()

    def tap(self, button: int, hold_seconds: float = 0.1):
        self.press(button)
        time.sleep(hold_seconds)
        self.release(button)

    def set_axis(self, axis: int, value: int):
        self._ui.write(e.EV_ABS, axis, value)
        self._ui.syn()


@contextmanager
def open_gamepad(name: str = "fly-mhw virtual pad"):
    pad = VirtualGamepad(name=name)
    try:
        yield pad
    finally:
        pad.close()
