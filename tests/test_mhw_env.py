"""
Offline check of env/mhw_env.py's tool-based step loop: a fake gamepad,
stubbed screen capture and a stubbed Lua bridge — no game, no uinput.
If python-evdev isn't installed (e.g. on macOS), a minimal stand-in
module is used just so env.game_interface.input_injector imports.

    python tests/test_mhw_env.py
"""

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    import evdev  # noqa: F401
except ImportError:
    fake = types.ModuleType("evdev")
    fake_ecodes = types.ModuleType("evdev.ecodes")
    fake_ecodes.__getattr__ = lambda name: name  # codes stay symbolic names
    fake.ecodes = fake_ecodes
    fake.AbsInfo = lambda **kw: kw
    fake.UInput = object
    sys.modules["evdev"] = fake
    sys.modules["evdev.ecodes"] = fake_ecodes

import env.mhw_env as mhw_env  # noqa: E402
from env.game_interface.capture import CaptureResult  # noqa: E402
from env.game_interface.lua_bridge import GameState  # noqa: E402
from env.mhw_env import MHWEnv, RelativeAction  # noqa: E402

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

    def close(self):
        pass


def state(quest_id=1):
    return GameState(raw={
        "player": {"health_current": 100, "health_max": 100, "action": {"lmt_id": 7, "fsm": 0}},
        "monsters": [{"id": 7, "health_current": 1000, "health_max": 1000}],
        "quest": {"id": quest_id, "state": 2},
    }, read_at=0.0, file_age_seconds=0.1)


mhw_env.capture_frame = lambda geometry=None: CaptureResult(image="IMG", latency_seconds=0.0, captured_at=0.0)
mhw_env.time.sleep = lambda s: None  # executor + env sleeps are instant

env = MHWEnv(REPO / "configs/weapons/greatsword_tools.yaml", REPO / "configs/monsters/great_jagras.yaml",
             "/nonexistent/state.json", gamepad=FakePad())
reads = iter([state(quest_id=-1)])  # idle once (reset's stale-quest drain), then a live quest
env.lua_bridge.read = lambda: next(reads, None) or state()
env.executor.sleep = lambda s: None

obs, info = env.reset()
check("reset returns the image", obs == "IMG")
check("reset info has a mask", len(info["action_mask"]) == env.toolset.n)
check("reset moveset is neutral", info["moveset_next"]["from"] == "neutral")

_, _, _, _, info = env.step(env.toolset.call("charged_slash", direction="forward", level="lv1"))
check("step runs a graph tool", info["tool_name"] == "charged_slash" and info["moveset"]["move"] == "charged_slash"
      and not info["invalid_call"], str(info["moveset"]))
check("player lmtID lands in reward_debug only", info["reward_debug"]["player_action"] == {"lmt_id": 7, "fsm": 0})

avail = info["moveset_next"]["available"]
i = next(k for k, d in enumerate(avail) if d.startswith("y_hold/release"))
_, _, _, _, info = env.step(RelativeAction(i, {"direction": "none", "level": "lv2"}))
check("relative action follows the chain", info["moveset"]["move"] == "strong_charged_slash", str(info["moveset"]))

_, _, _, _, info = env.step(env.toolset.call("overhead_smash", direction="none"))
check("masked call is a no-op", info["invalid_call"] and info["moveset"]["from"] == "strong_charged_slash")

idx = env.toolset.index_of(env.toolset.call("wait", duration="short"))
_, _, _, _, info = env.step(idx)
check("flat index action", info["tool_name"] == "wait" and info["moveset"]["move"] is None)

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
