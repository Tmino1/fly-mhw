"""
Keyboard + mouse input backend — the one that actually reaches MHW on this
machine. Replaces the virtual gamepad (input_injector.py) for driving the
game; see docs/risks.md's open entry for the full evidence that the pad
never arrives (pressure-vessel's static /dev/input snapshot, uinput
devices having no hidraw node, and MHW ignoring the pad even once Proton
registers it as a genuine XInput device).

Confirmed live 2026-10-02: a plain uinput keyboard moved the character in
all four directions (correctly antiparallel, axes perpendicular), and a
uinput mouse's BTN_LEFT held 2.5s produced a real charged slash
(player.action.lmt_id 62 -> 49254 fsm 85 charging -> 49306 fsm 65 on
release). The path is uinput -> libinput -> Hyprland -> XWayland -> MHW,
which is why it works where the gamepad doesn't: it's ordinary key/button
input to the focused window, not a HID device the game has to enumerate.

Deliberately exposes the SAME press(code)/release(code)/set_axis(code,
value) surface as VirtualGamepad, so env/tool_executor.py drives either
backend unchanged. The stick axes have no keyboard equivalent, so they're
emulated:
  - the left stick becomes WASD key holds (recomputed whenever either
    axis changes, so diagonals work and a direction change releases the
    keys it no longer needs),
  - the right stick becomes relative mouse motion, emitted continuously
    by a background thread for as long as the axis is non-zero — a
    one-shot REL event can't produce a sustained turn the way holding a
    stick does.

TWO GOTCHAS, both confirmed the hard way (see docs/risks.md):
  - MHW ignores injected input entirely unless its window is focused.
  - A freshly created uinput device needs ~1.5s before the compositor
    routes its events; open_keyboard_mouse() waits that out for you.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from evdev import UInput, ecodes as e

from ..evdev_codes import VIRTUAL_CODES

# Below this (out of 32767) a stick axis counts as centred. Matches the
# gamepad `directions` table, whose smallest non-zero component is 23170.
_AXIS_DEADZONE = 8000

# How fast a fully-deflected right stick turns the camera. The demo side
# measures real camera motion in mouse counts
# (configs/keyboard_bindings.yaml's mouse.counts_small/counts_large), but
# those are still placeholders (calibrated: false), so this is a
# placeholder too — tune it once the calibration sweep has run.
_CAMERA_COUNTS_PER_SECOND = 900.0
_CAMERA_TICK_SECONDS = 1.0 / 120


class VirtualKeyboardMouse:
    """One logical device pair (a keyboard and a mouse), presented with
    VirtualGamepad's interface.

    `move_keys` maps the four movement roles to evdev key codes, in the
    order (forward, back, left, right) — i.e. the game's W/S/A/D.
    """

    def __init__(
        self,
        move_keys: dict[str, int],
        extra_keys: list[int] = (),
        rt_key: int | None = None,
        name_prefix: str = "fly-mhw virtual",
    ):
        self.move_keys = move_keys
        # The key VAXIS_RT holds (this user's guard: BTN_EXTRA, identified
        # live 2026-10-02 by its hold-steady-then-return animation).
        self.rt_key = rt_key
        key_codes = sorted({*move_keys.values(), *extra_keys, *([rt_key] if rt_key else [])})
        # Mouse buttons must live on the device that also reports REL
        # motion, or libinput won't treat them as mouse buttons. Side
        # buttons included: this user's guard is one of them (BTN_SIDE /
        # BTN_EXTRA, captured live from their Logitech PRO X).
        self._mouse_btns = [
            c for c in key_codes
            if c in (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA)
        ]
        self._kbd_keys = [c for c in key_codes if c not in self._mouse_btns]

        self._kbd = UInput({e.EV_KEY: self._kbd_keys}, name=f"{name_prefix} keyboard")
        self._mouse = UInput(
            {e.EV_KEY: self._mouse_btns or [e.BTN_LEFT], e.EV_REL: [e.REL_X, e.REL_Y]},
            name=f"{name_prefix} mouse",
        )

        self._move_vec = {"x": 0, "y": 0}
        self._move_held: set[int] = set()
        self._cam = {"x": 0, "y": 0}
        self._cam_lock = threading.Lock()
        self._stop = threading.Event()
        self._cam_thread = threading.Thread(target=self._camera_loop, daemon=True)
        self._cam_thread.start()

    # --- device routing ---------------------------------------------------

    def _dev_for(self, code: int) -> UInput:
        return self._mouse if code in self._mouse_btns else self._kbd

    def _key(self, code: int, value: int) -> None:
        dev = self._dev_for(code)
        dev.write(e.EV_KEY, code, value)
        dev.syn()

    # --- VirtualGamepad-compatible surface ---------------------------------

    def press(self, code: int) -> None:
        self._key(code, 1)

    def release(self, code: int) -> None:
        self._key(code, 0)

    def tap(self, code: int, hold_seconds: float = 0.1) -> None:
        self.press(code)
        time.sleep(hold_seconds)
        self.release(code)

    def set_axis(self, code: int, value: int) -> None:
        if code == VIRTUAL_CODES["VAXIS_MOVE_X"]:
            self._move_vec["x"] = value
            self._apply_move()
        elif code == VIRTUAL_CODES["VAXIS_MOVE_Y"]:
            self._move_vec["y"] = value
            self._apply_move()
        elif code == VIRTUAL_CODES["VAXIS_CAM_X"]:
            with self._cam_lock:
                self._cam["x"] = value
        elif code == VIRTUAL_CODES["VAXIS_CAM_Y"]:
            with self._cam_lock:
                self._cam["y"] = value
        elif code == VIRTUAL_CODES["VAXIS_RT"]:
            if self.rt_key is None:
                raise ValueError(
                    "config drives VAXIS_RT but no right_trigger.key is set — "
                    "the `rt` role is unbound, so guard/kick can't be sent."
                )
            self._key(self.rt_key, 1 if value else 0)
        else:
            raise ValueError(
                f"set_axis got code {code!r}, which isn't one of this backend's "
                f"virtual axes {sorted(VIRTUAL_CODES)}. A keyboard/mouse config "
                "must name virtual axes, not ABS_* gamepad axes."
            )

    # --- left stick -> WASD -------------------------------------------------

    def _apply_move(self) -> None:
        x, y = self._move_vec["x"], self._move_vec["y"]
        want: set[int] = set()
        # Forward is stick UP, i.e. negative y — the same sign convention
        # the gamepad `directions` table uses (verified live 2026-09-14).
        if y <= -_AXIS_DEADZONE:
            want.add(self.move_keys["forward"])
        elif y >= _AXIS_DEADZONE:
            want.add(self.move_keys["back"])
        if x <= -_AXIS_DEADZONE:
            want.add(self.move_keys["left"])
        elif x >= _AXIS_DEADZONE:
            want.add(self.move_keys["right"])

        for code in self._move_held - want:
            self._key(code, 0)
        for code in want - self._move_held:
            self._key(code, 1)
        self._move_held = want

    # --- right stick -> mouse motion ---------------------------------------

    def _camera_loop(self) -> None:
        per_tick = _CAMERA_COUNTS_PER_SECOND * _CAMERA_TICK_SECONDS
        carry_x = carry_y = 0.0
        while not self._stop.is_set():
            with self._cam_lock:
                cx, cy = self._cam["x"], self._cam["y"]
            if cx or cy:
                carry_x += per_tick * (cx / 32767.0)
                carry_y += per_tick * (cy / 32767.0)
                dx, dy = int(carry_x), int(carry_y)
                carry_x -= dx
                carry_y -= dy
                if dx:
                    self._mouse.write(e.EV_REL, e.REL_X, dx)
                if dy:
                    self._mouse.write(e.EV_REL, e.REL_Y, dy)
                if dx or dy:
                    self._mouse.syn()
            else:
                carry_x = carry_y = 0.0
            self._stop.wait(_CAMERA_TICK_SECONDS)

    # --- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self._stop.set()
        self._cam_thread.join(timeout=1.0)
        for code in list(self._move_held):
            self._key(code, 0)
        self._move_held.clear()
        self._kbd.close()
        self._mouse.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# A new uinput device isn't routed by the compositor immediately — without
# this wait the first press of a session is silently dropped (seen live).
SETTLE_SECONDS = 1.5


@contextmanager
def open_keyboard_mouse(move_keys: dict[str, int], extra_keys: list[int] = ()):
    kbm = VirtualKeyboardMouse(move_keys, extra_keys)
    try:
        time.sleep(SETTLE_SECONDS)
        yield kbm
    finally:
        kbm.close()


def make_backend(toolset):
    """Build the input device a tools config asks for, already settled and
    ready to send. Callers get VirtualGamepad or VirtualKeyboardMouse —
    both satisfy the press/release/set_axis surface ToolExecutor needs, so
    nothing downstream has to care which one it is."""
    from ..evdev_codes import resolve_code

    if toolset.input_backend != "keyboard_mouse":
        from .input_injector import VirtualGamepad

        return VirtualGamepad()

    km = toolset.keyboard_mouse
    move_keys = {role: resolve_code(name) for role, name in km["move_keys"].items()}
    extra = [resolve_code(n) for n in km["buttons"].values()]
    rt_name = km.get("right_trigger", {}).get("key")
    rt_key = resolve_code(rt_name) if rt_name else None
    kbm = VirtualKeyboardMouse(move_keys, extra, rt_key=rt_key)
    time.sleep(SETTLE_SECONDS)
    return kbm


@contextmanager
def open_backend(toolset):
    dev = make_backend(toolset)
    try:
        yield dev
    finally:
        dev.close()
