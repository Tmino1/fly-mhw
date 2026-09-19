"""
Offline fixture suite for demos/keyboard_bindings.py — no running game,
no real input device needed (KeyboardActionReducer.sample() takes an
active_codes override for exactly this purpose).

    python tests/test_keyboard_bindings.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from evdev import ecodes  # noqa: E402

from demos.keyboard_bindings import KeyBinding, KeyboardActionReducer, KeyboardBindings  # noqa: E402

bindings = KeyboardBindings(
    weapon_config="configs/weapons/greatsword.yaml",
    bindings=(
        KeyBinding("move_forward", "KEY_W"),
        KeyBinding("move_backward", "KEY_S"),
        KeyBinding("strafe_left", "KEY_A"),
        KeyBinding("strafe_right", "KEY_D"),
        KeyBinding("attack_1", "BTN_LEFT"),
        KeyBinding("attack_2", "BTN_RIGHT"),
        KeyBinding("dodge", "KEY_SPACE"),
    ),
)
reducer = KeyboardActionReducer(bindings, devices=[])


def code(name: str) -> int:
    return ecodes.ecodes[name]


def check(label, sample, expect_action, expect_collided=None):
    ok = sample.action_name == expect_action
    if expect_collided is not None:
        ok = ok and sample.collided == expect_collided
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {label}: action={sample.action_name!r} collided={sample.collided} "
          f"candidates={sample.candidate_actions}")
    if not ok:
        print(f"        expected action={expect_action!r} collided={expect_collided}")


# 1. Nothing held -> idle, no candidates, no collision
check("nothing held", reducer.sample(active_codes=set()), "idle")

# 2. A single bound key -> that action, no collision
check("forward only", reducer.sample(active_codes={code("KEY_W")}), "move_forward", False)

# 3. An unbound key (e.g. some other key on the keyboard) -> idle
check("unbound key", reducer.sample(active_codes={code("KEY_TAB")}), "idle", False)

# 4. Movement + attack held together -> attack wins (ACTION_PRIORITY)
check(
    "forward + attack_1",
    reducer.sample(active_codes={code("KEY_W"), code("BTN_LEFT")}),
    "attack_1", True,
)

# 5. Two movement keys held together (diagonal) -> declared-priority winner, flagged as a collision
check(
    "forward + strafe_left (diagonal)",
    reducer.sample(active_codes={code("KEY_W"), code("KEY_A")}),
    "move_forward", True,
)

# 6. dodge beats movement but loses to attack_1 (ACTION_PRIORITY order:
#    attack_1, attack_2, dodge, then movement)
check(
    "attack_1 + dodge",
    reducer.sample(active_codes={code("BTN_LEFT"), code("KEY_SPACE")}),
    "attack_1", True,
)
check(
    "dodge + forward",
    reducer.sample(active_codes={code("KEY_SPACE"), code("KEY_W")}),
    "dodge", True,
)

print("\nAll checks ran without raising.")
