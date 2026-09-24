"""
Runs a ToolCall on the virtual gamepad (env/game_interface/input_injector.py).

Graph tools (moves): ask the tracker which option reaches the requested
move from the current combo root, send that option's input primitive with
the call's args, then advance the tracker. A move no option reaches is
masked — it's a logged no-op (`invalid`), never a best-guess input
(user decision, 2026-09-24: mask, don't auto-route or send anyway).

Timed tools (move/wait/camera) just drive the sticks for a duration and
never touch the combo root.

Every program ends with all buttons released, RT at rest and both sticks
recentred — in a `finally`, so an exception mid-charge can't leave Y held
down in the game.

The pad only needs press/release/set_axis, and sleep/clock are injectable,
so tests/test_tool_executor.py runs this against a FakePad with no evdev.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .evdev_codes import resolve_code
from .moveset_graph import MovesetTracker, Option, Transition
from .tools import ToolCall, ToolSet


@dataclass
class ExecResult:
    call: ToolCall
    invalid: bool
    option: Optional[Option]
    transition: Optional[Transition]  # None for timed tools and invalid calls
    t_start: float
    t_end: float

    @property
    def duration_s(self) -> float:
        return self.t_end - self.t_start


class ToolExecutor:
    def __init__(
        self,
        pad: Any,
        toolset: ToolSet,
        tracker: MovesetTracker,
        code_resolver: Callable[[str], Any] = resolve_code,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.pad = pad
        self.toolset = toolset
        self.tracker = tracker
        self.clock = clock
        self.sleep = sleep

        gp = toolset.gamepad
        self.buttons = {k: code_resolver(v) for k, v in gp["buttons"].items()}
        self.lx = code_resolver(gp["left_stick"]["x"])
        self.ly = code_resolver(gp["left_stick"]["y"])
        self.rx = code_resolver(gp["right_stick"]["x"])
        self.ry = code_resolver(gp["right_stick"]["y"])
        self.rt = code_resolver(gp["right_trigger"]["code"])
        self.rt_pressed = gp["right_trigger"]["pressed"]
        self.timings = toolset.timings
        self._held: set = set()

    # --- primitives -----------------------------------------------------------

    def _press(self, button: str) -> None:
        code = self.buttons[button]
        self.pad.press(code)
        self._held.add(code)

    def _release(self, button: str) -> None:
        code = self.buttons[button]
        self.pad.release(code)
        self._held.discard(code)

    def _tap(self, button: str, seconds: float) -> None:
        self._press(button)
        self.sleep(seconds)
        self._release(button)

    def _left_stick(self, direction: str) -> None:
        x, y = self.toolset.directions[direction]
        self.pad.set_axis(self.lx, x)
        self.pad.set_axis(self.ly, y)

    def _aim(self, direction: str) -> None:
        self._left_stick(direction)
        if direction != "none":
            self.sleep(self.timings["stick_settle_seconds"])

    def _rest(self) -> None:
        for code in list(self._held):
            self.pad.release(code)
        self._held.clear()
        self.pad.set_axis(self.rt, 0)
        for axis in (self.lx, self.ly, self.rx, self.ry):
            self.pad.set_axis(axis, 0)

    def charge_hold_seconds(self, level: str) -> float:
        t = self.timings
        return max(t["level_seconds"][level] + t["level_hold_margin_seconds"], t["min_charge_hold_seconds"])

    # --- programs ---------------------------------------------------------

    def _send_input(self, input: str, finish: Optional[str], args: dict[str, Any]) -> float:
        """Send one input primitive; return the time the move commits (the
        Y release / tackle press for a charge, else when the input ends)."""
        t = self.timings
        direction = args.get("direction", "none")
        if input == "y_tap":
            self._aim(direction)
            self._tap("Y", t["tap_seconds"])
        elif input == "y_hold":
            self._aim(direction)
            self._press("Y")
            self.sleep(self.charge_hold_seconds(args["level"]))
            if finish == "tackle":
                commit = self.clock()
                self._tap("B", t["tackle_tap_seconds"])
                self._release("Y")
                return commit
            self._release("Y")
        elif input == "b":
            self._aim(direction)
            self._tap("B", t["tap_seconds"])
        elif input == "yb":
            self._aim(direction)
            self._press("Y")
            self._press("B")
            self.sleep(t["yb_hold_seconds"])
            self._release("B")
            self._release("Y")
        elif input == "rt_hold":
            self.pad.set_axis(self.rt, self.rt_pressed)
            self.sleep(t["duration_seconds"][args["duration"]])
            self.pad.set_axis(self.rt, 0)
        elif input == "rt_y":
            self._aim(direction)
            self.pad.set_axis(self.rt, self.rt_pressed)
            self.sleep(t["rt_before_y_seconds"])
            self._tap("Y", t["tap_seconds"])
            self.pad.set_axis(self.rt, 0)
        elif input == "a":
            self._aim(direction)
            self._tap("A", t["dodge_tap_seconds"])
        elif input == "x":
            self._tap("X", t["tap_seconds"])
        else:
            raise ValueError(f"unknown input primitive {input!r}")
        return self.clock()

    def _run_timed(self, call: ToolCall) -> None:
        t = self.timings
        args = call.arg_dict
        if call.name == "wait":
            self.sleep(t["duration_seconds"][args["duration"]])
        elif call.name == "move":
            self._left_stick(args["direction"])
            self.sleep(t["duration_seconds"][args["duration"]])
        elif call.name == "camera":
            value = t["camera_stick_value"] * (-1 if args["yaw"] == "left" else 1)
            self.pad.set_axis(self.rx, value)
            self.sleep(t["camera_amount_seconds"][args["amount"]])
        else:
            raise ValueError(f"no program for timed tool {call.name!r}")

    def run(self, call: ToolCall) -> ExecResult:
        call = self.toolset.validate_call(call)
        t_start = self.clock()

        if not self.toolset.is_graph_tool(call.name):
            try:
                self._run_timed(call)
            finally:
                self._rest()
            return ExecResult(call, False, None, None, t_start, self.clock())

        options = self.tracker.move_options(call.name, call.arg_dict, t_start)
        if not options:
            return ExecResult(call, True, None, None, t_start, self.clock())

        option = options[0]
        try:
            t_commit = self._send_input(option.input, option.finish, call.arg_dict)
        finally:
            self._rest()
        transition = self.tracker.advance(option, t_start, t_commit)
        return ExecResult(call, False, option, transition, t_start, self.clock())
