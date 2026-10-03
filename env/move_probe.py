"""
Working out which animation a move actually produced, from the ids the
game logged while it ran.

Kept separate from scripts/probe_moveset.py (which drives the live game)
so the attribution rule — the part that's easy to get subtly wrong — is
offline-testable. See tests/test_move_probe.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Animations that are never the move being probed: the character standing
# still, running because a direction is held (SCS/TCS need forward), or
# the charge-hold pose while the button is down. Measured live
# 2026-10-02; extend as more are identified rather than guessing ranges.
IDLE_IDS = frozenset({49153})
NOISE_IDS = frozenset({
    49402,   # running (a direction key held)
    49276,   # Great Sword charge-hold / uncharged release pose
})


@dataclass
class ProbeResult:
    move: str
    path: list[str]                       # the moves run to reach it
    lmt_id: Optional[int]                 # the animation attributed to the move
    sequence: list[int] = field(default_factory=list)
    full_animation_s: Optional[float] = None
    fired: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "move": self.move, "path": self.path, "lmt_id": self.lmt_id,
            "sequence": self.sequence, "full_animation_s": self.full_animation_s,
            "fired": self.fired, "note": self.note,
        }


def attribute(
    events: list[tuple[float, int]],
    t_commit: float,
    idle_ids: frozenset = IDLE_IDS,
    noise_ids: frozenset = NOISE_IDS,
) -> tuple[Optional[int], Optional[float], list[int]]:
    """Pick the move's animation id out of everything logged after it
    committed, and measure how long it ran.

    Takes the LAST non-idle, non-noise id rather than the first. A move
    is often preceded by wind-up frames (the True Charged Slash logged
    49412 and 49398 before 49341) and, when a direction is held, by the
    run animation — so the first id is routinely the wrong one, while the
    last before the character settles is the move itself.

    Duration is that id's start until the next event, which is the return
    to idle when nothing else was pressed. Returns (id, seconds, full
    sequence); id is None when the move never fired.
    """
    after = [(t, i) for t, i in events if t >= t_commit]
    sequence = [i for _, i in after]
    real = [(t, i) for t, i in after if i not in idle_ids and i not in noise_ids]
    if not real:
        return None, None, sequence

    t_start, lmt_id = real[-1]
    duration = None
    for t, _ in after:
        if t > t_start:
            duration = t - t_start
            break
    return lmt_id, duration, sequence


def conflicts(results: list[ProbeResult]) -> dict[int, list[str]]:
    """Animation ids claimed by more than one move.

    A real signal, not a nuisance: it's how the phantom "Overhead Smash"
    was caught (it shared the Charged Slash's id because it WAS one), and
    how a mis-recorded id would be caught before it corrupts labelling.
    Either the graph has a duplicate move, or a probe silently failed and
    attributed the previous move's animation.
    """
    by_id: dict[int, list[str]] = {}
    for r in results:
        if r.lmt_id is not None:
            by_id.setdefault(r.lmt_id, []).append(r.move)
    return {k: v for k, v in by_id.items() if len(v) > 1}
