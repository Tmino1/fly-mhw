"""
Shared YAML-config loading for env/action_space.py and env/reward.py.

Deliberately tiny: load the file, assert its declared schema matches what
the caller expects, hand back the raw dict. No game-live dependency —
fully unit-testable offline (see docs/architecture.md's Design
Principles: monster/weapon knowledge lives in config, not code — this is
the one place that boundary is enforced).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path, expected_schema: str) -> dict[str, Any]:
    path = Path(path)
    with path.open("r") as f:
        data = yaml.safe_load(f)

    schema = data.get("schema") if isinstance(data, dict) else None
    if schema != expected_schema:
        raise ValueError(
            f"{path}: expected schema {expected_schema!r}, got {schema!r} — "
            "wrong config type, or the file predates a schema bump."
        )
    return data
