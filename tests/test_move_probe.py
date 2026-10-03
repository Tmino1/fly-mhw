"""
Offline fixture suite for env/move_probe.py — the rule that decides which
animation a probed move actually produced. No game, no evdev.

    python tests/test_move_probe.py

Every fixture here is a real sequence observed live on 2026-10-02; the
attribution rule exists because the obvious one (take the first id) gets
all three of the interesting cases wrong.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from env.move_probe import ProbeResult, attribute, conflicts  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures += 1


def ev(pairs):
    return [(t, i) for t, i in pairs]


# 1. A plain Charged Slash: charge-hold pose, then the swing, then idle.
#    The charge-hold (49276) is noise — taking the FIRST id would report it.
lmt, dur, seq = attribute(ev([(0.14, 49276), (0.90, 49304), (3.46, 49153)]), 0.0)
check("charged slash attributed past the charge-hold pose", lmt == 49304, str(lmt))
check("duration is the swing, not the whole sequence", abs(dur - 2.56) < 0.01, str(dur))

# 2. True Charged Slash: two wind-up frames precede the move itself.
#    Observed live as 49412 -> 49398 -> 49341.
lmt, dur, seq = attribute(ev([(0.1, 49412), (0.3, 49398), (0.5, 49341), (3.9, 49153)]), 0.0)
check("TCS attributed past its wind-up frames", lmt == 49341, str(lmt))

# 3. Forward held for the SCS makes the character run first; the run
#    animation must not be mistaken for the move.
lmt, dur, seq = attribute(ev([(0.2, 49402), (1.0, 49307), (4.2, 49153)]), 0.0)
check("run animation ignored when a direction is held", lmt == 49307, str(lmt))

# 4. Events from before the move committed belong to the previous move.
lmt, _, _ = attribute(ev([(0.1, 49304), (2.0, 49153), (3.0, 49307), (6.0, 49153)]), 2.5)
check("events before the commit are excluded", lmt == 49307, str(lmt))

# 5. A move that never fired: nothing but idle.
lmt, dur, seq = attribute(ev([(0.5, 49153)]), 0.0)
check("no animation -> no id", lmt is None and dur is None, f"{lmt} {dur}")

# 6. Nothing logged at all (the game ignored the input entirely).
lmt, dur, seq = attribute([], 0.0)
check("empty log -> no id", lmt is None and seq == [], f"{lmt} {seq}")

# 7. Duplicate ids across moves — how the phantom "Overhead Smash" was
#    caught, and how a mis-recorded id gets caught before it reaches
#    labelling (a Side Blow id was once recorded as the SCS's).
dupes = conflicts([
    ProbeResult("charged_slash", [], 49304),
    ProbeResult("overhead_smash", [], 49304),
    ProbeResult("wide_slash", [], 49258),
])
check("duplicate animation ids are reported", dupes == {49304: ["charged_slash", "overhead_smash"]},
      str(dupes))
check("distinct ids are not flagged",
      conflicts([ProbeResult("a", [], 1), ProbeResult("b", [], 2)]) == {})

# 8. A move that never fired must not collide with every other failure.
check("unfired moves are excluded from conflicts",
      conflicts([ProbeResult("a", [], None), ProbeResult("b", [], None)]) == {})

print(f"\n{failures} failure(s).")
sys.exit(1 if failures else 0)
