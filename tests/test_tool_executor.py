"""
Offline fixture suite for env/tool_executor.py — a FakePad records every
primitive, codes stay symbolic names, and sleep/clock are fake, so this
runs instantly with no evdev or gamepad.

    python tests/test_tool_executor.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from env.moveset_graph import MovesetTracker  # noqa: E402
from env.tool_executor import ToolExecutor  # noqa: E402
from env.tools import ToolSet  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


class FakePad:
    def __init__(self):
        self.log = []

    def press(self, code):
        self.log.append(("press", code))

    def release(self, code):
        self.log.append(("release", code))

    def set_axis(self, code, value):
        self.log.append(("axis", code, value))


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.log_sleep.append(s)
        self.t += s


ts = ToolSet.from_config(REPO / "configs/weapons/greatsword_tools.yaml")


def make():
    pad, clock = FakePad(), FakeClock()
    clock.log_sleep = []
    ex = ToolExecutor(pad, ts, MovesetTracker(ts.graph), code_resolver=lambda n: n,
                      clock=clock, sleep=clock.sleep)
    return ex, pad, clock


def buttons(pad):
    return [e for e in pad.log if e[0] in ("press", "release")]


def rest_ok(pad):
    """The last writes must leave RT and all four sticks at 0."""
    finals = {}
    for e in pad.log:
        if e[0] == "axis":
            finals[e[1]] = e[2]
    return all(finals.get(a, 0) == 0 for a in ("ABS_X", "ABS_Y", "ABS_RX", "ABS_RY", "ABS_RZ"))


# 1. wide_slash from neutral resolves to a B tap
ex, pad, _ = make()
r = ex.run(ts.call("wide_slash", direction="forward"))
check("wide_slash from neutral -> B", buttons(pad) == [("press", "BTN_B"), ("release", "BTN_B")]
      and r.option.input == "b", str(pad.log))
check("aim set before the button", pad.log.index(("axis", "ABS_Y", -32767)) < pad.log.index(("press", "BTN_B")))
check("rest after", rest_ok(pad))

# 2. The charge chain: each strong/true charged slash is a Y hold, and the
#    tracker follows along
ex, pad, clock = make()
ex.run(ts.call("charged_slash", direction="none", level="lv1"))
ex.run(ts.call("strong_charged_slash", direction="none", level="lv2"))
r = ex.run(ts.call("true_charged_slash", direction="none", level="lv3"))
check("charge chain reaches TCS", r.transition.move == "true_charged_slash" and r.transition.depth == 3,
      str(r.transition.to_dict()))
check("charge holds use level_seconds", ex.charge_hold_seconds("lv3") in clock.log_sleep
      and ex.charge_hold_seconds("lv2") in clock.log_sleep, str(clock.log_sleep))

# 3. Tackle-during-hold: Y down, B tapped while Y is still held, then Y up
ex, pad, _ = make()
r = ex.run(ts.call("tackle", direction="none", level="lv2"))
check("tackle is Y-hold + B", buttons(pad) == [("press", "BTN_Y"), ("press", "BTN_B"),
                                              ("release", "BTN_B"), ("release", "BTN_Y")], str(buttons(pad)))
check("tackle via charge_1", r.transition.via == "charge_1" and r.transition.to == "tackle_1")

# 4. yb presses both together
ex, pad, _ = make()
ex.run(ts.call("rising_slash", direction="none"))
check("rising_slash is Y+B", buttons(pad)[:2] == [("press", "BTN_Y"), ("press", "BTN_B")], str(buttons(pad)))

# 5. A masked move is a no-op + invalid
ex, pad, _ = make()
r = ex.run(ts.call("strong_wide_slash", direction="none"))
check("masked move is a no-op", r.invalid and pad.log == [] and ex.tracker.current == "neutral")

# 6. Y tap after a Charged Slash is side_blow_1; overhead_smash is masked there
ex, pad, _ = make()
ex.run(ts.call("charged_slash", direction="none", level="lv1"))
check("overhead_smash masked after charged_slash",
      ex.run(ts.call("overhead_smash", direction="none")).invalid)
check("side_blow_1 after charged_slash",
      ex.run(ts.call("side_blow_1", direction="none")).transition.move == "side_blow_1")

# 7. Timed tools never touch the combo root
ex, pad, _ = make()
ex.run(ts.call("charged_slash", direction="none", level="lv1"))
ex.run(ts.call("move", direction="left", duration="short"))
ex.run(ts.call("camera", yaw="right", amount="small"))
check("timed tools keep the root", ex.tracker.current == "charged_slash")
check("camera drives the right stick", ("axis", "ABS_RX", 32767) in pad.log)
check("rest after timed", rest_ok(pad))

# 8. Guard / kick use RT on the trigger axis
ex, pad, _ = make()
ex.run(ts.call("guard", duration="short"))
r = ex.run(ts.call("kick", direction="none"))
check("guard then kick", r.transition.root_before == "guard" and r.transition.move == "kick")
check("RT pressed", ("axis", "ABS_RZ", 255) in pad.log and rest_ok(pad))

# 9. Buttons are released and sticks recentred even if a sleep raises mid-charge
pad, clock = FakePad(), FakeClock()
clock.log_sleep = []


def boom(_s):
    raise KeyboardInterrupt


ex = ToolExecutor(pad, ts, MovesetTracker(ts.graph), code_resolver=lambda n: n, clock=clock, sleep=boom)
try:
    ex.run(ts.call("charged_slash", direction="none", level="lv3"))  # raises during the Y hold
except KeyboardInterrupt:
    pass
check("Y released after an interrupt", buttons(pad) == [("press", "BTN_Y"), ("release", "BTN_Y")]
      and rest_ok(pad), str(pad.log))

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
