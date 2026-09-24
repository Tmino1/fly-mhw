"""
Regression check for scripts/audit_tool_labels.py's candidate-edge
verdicts, on a synthetic "expert demo" where the game really does chain
Wide Slash -> hold Y into a Strong Charged Slash (a `candidates:` edge the
tracker ignores, so those holds get labelled as re-rooted charged_slash).

Caught during development: building the lmtID catalog from ALL labels let
those very re-rooted labels claim SCS's lmtID, flipping the verdict to
"contradicted". The catalog must come from trusted labels only.

    python tests/test_audit_tool_labels.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from demos.keyboard_bindings import KeyboardBindings  # noqa: E402
from demos.labeling import label_episode  # noqa: E402
from env.tools import ToolSet  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


T = 1000.0
with tempfile.TemporaryDirectory() as tmp:
    ep = Path(tmp) / "ep"
    ep.mkdir()
    (ep / "frames.jsonl").write_text("".join(
        json.dumps({"step": i, "t": T + 0.2 * i, "frame": f"f{i}.png"}) + "\n" for i in range(250)))
    events, lmt = [], []

    def press(role, a, b):
        events.extend([{"type": "key", "t": T + a, "role": role, "down": True},
                       {"type": "key", "t": T + b, "role": role, "down": False}])

    t = 0.0
    for _ in range(3):  # declared chain: charged (lmt 11) -> strong charged (lmt 22)
        press("y", t, t + 0.5); lmt.append((t + 0.6, 11))
        press("y", t + 1.0, t + 2.0); lmt.append((t + 2.1, 22))
        t += 8
    for _ in range(3):  # candidate: wide slash (33) -> hold Y, and the game plays SCS (22)
        press("b", t, t + 0.1); lmt.append((t + 0.2, 33))
        press("y", t + 0.8, t + 1.6); lmt.append((t + 1.7, 22))
        t += 8
    (ep / "input_events.jsonl").write_text("".join(
        json.dumps(e) + "\n" for e in sorted(events, key=lambda e: e["t"])))
    (ep / "lmt_events.jsonl").write_text("".join(
        json.dumps({"lmt_id": l, "t_arrival": T + a}) + "\n" for a, l in lmt))

    ts = ToolSet.from_config(REPO / "configs/weapons/greatsword_tools.yaml")
    label_episode(ep, ts, KeyboardBindings.from_config(REPO / "configs/keyboard_bindings.yaml").mouse, T, T + 50)

    out = subprocess.run([sys.executable, str(REPO / "scripts/audit_tool_labels.py"), str(ep)],
                         capture_output=True, text=True, cwd=REPO).stdout
    line = next((l for l in out.splitlines() if "wide_slash --y_hold--> charge_2" in l), "")
    check("candidate supported by the game's lmtIDs", "SUPPORTED" in line, line.strip())
    check("undeclared transition surfaced", "wide_slash -> strong_charged_slash" in out)
    check("no cross-chain phantom transitions", "strong_charged_slash -> wide_slash" not in out)

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
