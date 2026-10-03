"""
Offline checks for demos/keyboard_bindings.py (v2: keys -> input roles).
No evdev or input device needed — only config loading is exercised; the
event-to-tool-call logic that used to live here (v1's
KeyboardActionReducer) is now demos/tool_segmenter.py, covered by
tests/test_tool_segmenter.py.

    python tests/test_keyboard_bindings.py
"""

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from demos.keyboard_bindings import ROLES, KeyboardBindings  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


b = KeyboardBindings.from_config(REPO / "configs/keyboard_bindings.yaml")
check("loads the repo's bindings", b.roles["y"] == "BTN_LEFT" and b.roles["x"] == "KEY_E", str(b.roles))
check("every role present", set(b.roles) == set(ROLES))
# rt (guard) bound 2026-10-02 (BTN_EXTRA, identified live by its
# hold-steady-then-return-on-release animation); rb (sprint) bound
# 2026-10-03 (left shift, reported by the user). lt (slinger) is the
# last one still unbound — see docs/architecture.md's clutch
# claw / slinger expansion entry.
check("uncalibrated roles reported", b.unbound_roles() == ["lt"], str(b.unbound_roles()))
check("tools config reference", b.tools_config == "configs/weapons/greatsword_tools.yaml")
check("mouse thresholds", b.mouse.get("counts_small") and b.mouse.get("counts_large"))


def load(text):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(text)
    return KeyboardBindings.from_config(f.name)


header = 'schema: "fly-mhw/keyboard_bindings/v2"\ntools_config: "x"\n'
for text, label in [
    (header + "roles: {y: BTN_LEFT, b: BTN_LEFT}\n", "same key on two roles"),
    (header + "roles: {attack_1: BTN_LEFT}\n", "unknown role"),
    ('schema: "fly-mhw/keyboard_bindings/v1"\nweapon_config: "x"\nbindings: []\n', "v1 file"),
]:
    try:
        load(text)
        check(f"rejects {label}", False)
    except ValueError:
        check(f"rejects {label}", True)

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
