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

        # Either input block has the same shape, so the programs below are
        # backend-agnostic; keyboard_mouse just names virtual axes where
        # the gamepad names ABS_*. See docs/risks.md for why the pad is
        # reference-only on this machine.
        gp = toolset.keyboard_mouse if toolset.input_backend == "keyboard_mouse" else toolset.gamepad
        self.buttons = {k: code_resolver(v) for k, v in gp["buttons"].items()}
        self.lx = code_resolver(gp["left_stick"]["x"])
        self.ly = code_resolver(gp["left_stick"]["y"])
        self.rx = code_resolver(gp["right_stick"]["x"])
        self.ry = code_resolver(gp["right_stick"]["y"])
        # An unbound trigger (keyboard_bindings.yaml still has rt: null)
        # is carried as None rather than failing at construction — only
        # the moves that actually need it should break, and they raise a
        # clear error in _send_input() instead.
        rt_name = gp["right_trigger"]["code"]
        self.rt = code_resolver(rt_name) if rt_name is not None else None
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

    def _require_rt(self, input: str) -> None:
        if self.rt is None:
            raise ValueError(
                f"input primitive {input!r} needs the right trigger, but this "
                "weapon config's right_trigger.code is null — the `rt` role is "
                "still uncalibrated in configs/keyboard_bindings.yaml, so "
                "guard, kick and the RT side blows can't be sent yet. Run "
                "scripts/calibrate_keyboard_bindings.py to bind it."
            )

    def rest(self) -> None:
        """Release everything and recentre both sticks."""
        for code in list(self._held):
            self.pad.release(code)
        self._held.clear()
        if self.rt is not None:
            self.pad.set_axis(self.rt, 0)
        for axis in (self.lx, self.ly, self.rx, self.ry):
            self.pad.set_axis(axis, 0)

    def charge_hold_seconds(self, level: str) -> float:
        t = self.timings
        # lv0 is "no charge at all" — a real tap. It must NOT be raised to
        # min_charge_hold_seconds (that floor exists to make lv1+ holds
        # register), or the uncharged swing drifts toward being a charged
        # one and lv0 stops meaning anything.
        if t["level_seconds"].get(level, 0.0) <= 0.0:
            return t["tap_seconds"]
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
            hold = self.charge_hold_seconds(args["level"])
            if finish == "tackle":
                # A tackle needs the charge to have run ~1s before the B
                # tap registers; at the plain lv1 hold (0.55s) the tap is
                # ignored and the charge just releases as a Charged Slash
                # — silently the wrong move. Measured live 2026-10-02.
                hold = max(hold, t.get("tackle_min_charge_seconds", 1.0))
            self.sleep(hold)
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
            self._require_rt(input)
            self.pad.set_axis(self.rt, self.rt_pressed)
            self.sleep(t["duration_seconds"][args["duration"]])
            self.pad.set_axis(self.rt, 0)
        elif input == "rt_y":
            self._require_rt(input)
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

    def run(self, call: ToolCall, option: Optional[Option] = None) -> ExecResult:
        """Run call. `option` pins which route to use when several reach
        the same move (a graph-relative action picks one explicitly); it
        must be one of the tracker's current options for this move."""
        call = self.toolset.validate_call(call)
        t_start = self.clock()

        if not self.toolset.is_graph_tool(call.name):
            try:
                self._run_timed(call)
            finally:
                self.rest()
            return ExecResult(call, False, None, None, t_start, self.clock())

        options = self.tracker.move_options(call.name, call.arg_dict, t_start)
        if option is not None and option not in options:
            options = []
        if not options:
            return ExecResult(call, True, None, None, t_start, self.clock())

        option = option or options[0]
        try:
            t_commit = self._send_input(option.input, option.finish, call.arg_dict)
        finally:
            self.rest()
        transition = self.tracker.advance(option, t_start, t_commit)
        self._await_animation(transition, t_commit)
        return ExecResult(call, False, option, transition, t_start, self.clock())

    def _await_animation(self, transition: Transition, t_commit: float) -> None:
        """Block until the move's animation has played out.

        A follow-up pressed DURING an attack's animation does not chain —
        it's simply lost, and the combo window only opens once the swing
        finishes (user, 2026-10-02; measured the same day). Without this
        wait, back-to-back run() calls fire the next input mid-animation
        and silently produce a fresh attack instead of the intended
        chain: a Charged Slash followed at 0.2-0.8s produced another
        Charged Slash, while 1.1-2.0s produced the Strong Charged Slash.

        Waiting here rather than in the caller means every driver — the
        env's step(), verify_tools, a future RL loop — gets correct
        chaining for free, and the combo window the tracker already
        models (duration_s + combo_window_s) is the window the next call
        actually lands in.
        """
        node = self.tracker.graph.nodes.get(transition.to)
        if node is None:
            return
        remaining = (t_commit + node.duration_s) - self.clock()
        if remaining > 0:
            self.sleep(remaining)
