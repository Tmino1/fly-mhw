"""
Offline fixture suite for env/tools.py against the real
configs/weapons/greatsword_tools.yaml — no game, no evdev needed.

    python tests/test_tools.py
"""

import contextlib
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from env.evdev_codes import warn_if_compass_alias  # noqa: E402
from env.moveset_graph import MovesetTracker  # noqa: E402
from env.tools import ToolCall, ToolSet  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


ts = ToolSet.from_config(REPO / "configs/weapons/greatsword_tools.yaml")

# 1. Loads, and every reachable graph move has a tool (validated on load)
check("config loads", ts.n > 0, f"n={ts.n}")
check("tool names", "strong_charged_slash" in ts.tool_names() and "camera" in ts.tool_names())

# 2. Flat index round-trips for every call
check("flat index round-trip", all(ts.index_of(ts.call_at(i)) == i for i in range(ts.n)))

# 3. Factored view
choices = ts.arg_choices("charged_slash")
check("factored args", choices == {"direction": ts.arg_choices("dodge")["direction"],
                                   "level": ["lv0", "lv1", "lv2", "lv3"]}, str(choices))

# 4. Call construction/validation
c = ts.call("strong_charged_slash", direction="forward", level="lv2")
check("valid call", str(c) == "strong_charged_slash(direction=forward, level=lv2)", str(c))
check("dict round-trip", ToolCall.from_dict(c.to_dict()) == c)
for bad, label in [
    (ToolCall.make("charged_slash", direction="forward"), "missing arg"),
    (ToolCall.make("charged_slash", direction="forward", level="lv9"), "bad arg value"),
    (ToolCall.make("move", direction="none", duration="short"), "move has no 'none' direction"),
]:
    try:
        ts.validate_call(bad)
        check(f"rejects {label}", False)
    except ValueError:
        check(f"rejects {label}", True)
try:
    ts.validate_call(ToolCall.make("not_a_tool"))
    check("rejects unknown tool", False)
except KeyError:
    check("rejects unknown tool", True)

# 5. coerce() accepts the three action forms
check("coerce int", ts.coerce(0) == ts.call_at(0))
check("coerce dict", ts.coerce({"tool": "wait", "args": {"duration": "short"}}) == ts.call("wait", duration="short"))

# 6. Mask follows the combo root
tr = MovesetTracker(ts.graph)
mask = ts.mask(tr, 0.0)
allowed = {c.name for c, ok in zip(ts.calls(), mask) if ok}
check("neutral mask", {"charged_slash", "wide_slash", "dodge", "move"} <= allowed
      and "strong_charged_slash" not in allowed, str(sorted(allowed)))
tr.advance_input("y_hold", "release", 0.0, 0.5)
mask = ts.mask(tr, 1.0)
allowed = {c.name for c, ok in zip(ts.calls(), mask) if ok}
check("after charged slash", {"strong_charged_slash", "side_blow_1", "rising_slash_1", "wide_slash"} <= allowed,
      str(sorted(allowed)))
# After a dodge, a Y *tap* is the chart's "Y after dodge" Tackle, pinned
# to lv1 by the edge; a charged tackle is still reachable, but only by
# re-rooting through neutral's Y hold (dodge_out doesn't claim y_hold)
tr = MovesetTracker(ts.graph)
tr.advance_input("a", None, 0.0)
lv1 = tr.move_options("tackle", {"direction": "none", "level": "lv1"}, 0.2)
lv2 = tr.move_options("tackle", {"direction": "none", "level": "lv2"}, 0.2)
check("lv1 tackle after dodge prefers the Y tap", lv1[0].input == "y_tap" and not lv1[0].rerooted,
      str([o.describe() for o in lv1]))
check("lv2 tackle after dodge re-roots via a charge", [o.input for o in lv2] == ["y_hold"] and lv2[0].rerooted,
      str([o.describe() for o in lv2]))

# 7. Compass-alias warning still fires (moved to env/evdev_codes.py)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    warned = warn_if_compass_alias("BTN_NORTH")
check("compass alias warning", warned and "BTN_X" in buf.getvalue())

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
