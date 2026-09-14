"""
Turns a monster config (configs/monsters/*.yaml) plus consecutive
env.game_interface.lua_bridge.GameState reads into a scalar reward and
episode-boundary signal. No monster-specific knowledge lives here —
swapping monsters is swapping the YAML path, per docs/architecture.md's
Design Principles.

state_reader.lua wraps each JSON section (player/monsters/quest) in its
own pcall, so a section can come back as {"error": "..."} instead of the
documented shape. Every read here treats a missing/error-shaped/
zero-max-HP section as "skip this term" rather than crashing — a Phase 1
env should never raise just because one field was momentarily bad.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config_loader import load_config
from .game_interface.lua_bridge import GameState

MONSTER_CONFIG_SCHEMA = "fly-mhw/monster_config/v1"


@dataclass
class StepOutcome:
    reward: float
    terminated: bool
    truncated: bool
    reason: str  # "" | "monster_defeated" | "player_cart" | "quest_ended_unknown" | "max_steps"
    monster_hp_fraction: Optional[float]
    player_hp_fraction: Optional[float]
    quest_state_raw: Any  # passthrough for logging only, never branched on


def _hp_fraction(entity: Optional[dict]) -> Optional[float]:
    """entity is a player or monster dict with flat health_current/
    health_max fields (state_reader.lua's real schema — same field names
    for both). Returns None if entity is missing, error-shaped, or
    health_max is 0/absent (avoids divide-by-zero)."""
    if not isinstance(entity, dict) or "error" in entity:
        return None
    hp_max = entity.get("health_max")
    hp_current = entity.get("health_current")
    if not hp_max or hp_current is None:
        return None
    return hp_current / hp_max


class RewardModel:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.identification_strategy = config["identification"]["strategy"]
        reward = config["reward"]
        self.monster_hp_lost_weight = reward["monster_hp_lost_weight"]
        self.player_hp_lost_weight = reward["player_hp_lost_weight"]
        self.step_penalty = reward["step_penalty"]
        terminal = reward["terminal"]
        self.monster_defeated_bonus = terminal["monster_defeated_bonus"]
        self.player_cart_penalty = terminal["player_cart_penalty"]
        self.quest_ended_unknown_reward = terminal["quest_ended_unknown_reward"]
        # HP is a float read from game memory — real "death" may never land
        # on exactly 0.0, so "was the monster basically dead" needs a small
        # tolerance rather than an exact-zero check. Default kept here (not
        # just relying on the YAML key existing) so older configs don't break.
        self.near_zero_hp_threshold = terminal.get("near_zero_hp_threshold", 0.01)
        self.max_episode_steps = config["episode_boundaries"]["max_episode_steps"]

    @classmethod
    def from_config(cls, path: str | Path) -> "RewardModel":
        return cls(load_config(path, MONSTER_CONFIG_SCHEMA))

    def select_monster(self, state: Optional[GameState]) -> Optional[dict]:
        if state is None:
            return None
        monsters = state.raw.get("monsters")
        if self.identification_strategy != "first_monster":
            raise NotImplementedError(
                f"unknown identification.strategy {self.identification_strategy!r}"
            )
        if not isinstance(monsters, list) or not monsters:
            return None
        return monsters[0]

    def step(
        self,
        prev_state: Optional[GameState],
        curr_state: GameState,
        step_index: int,
    ) -> StepOutcome:
        player_prev = prev_state.raw.get("player") if prev_state else None
        player_curr = curr_state.raw.get("player")

        monster_prev = self.select_monster(prev_state)
        monster_curr = self.select_monster(curr_state)

        player_prev_frac = _hp_fraction(player_prev)
        player_curr_frac = _hp_fraction(player_curr)
        monster_prev_frac = _hp_fraction(monster_prev)
        monster_curr_frac = _hp_fraction(monster_curr)

        reward = self.step_penalty

        if monster_prev_frac is not None and monster_curr_frac is not None:
            reward += self.monster_hp_lost_weight * max(0.0, monster_prev_frac - monster_curr_frac)

        if player_prev_frac is not None and player_curr_frac is not None:
            reward += self.player_hp_lost_weight * max(0.0, player_prev_frac - player_curr_frac)

        terminated = False
        truncated = False
        reason = ""

        # 1. player fainted/carted
        if isinstance(player_curr, dict) and "error" not in player_curr:
            if player_curr.get("health_current", 1) <= 0:
                terminated = True
                reason = "player_cart"
                reward += self.player_cart_penalty

        # 2. monster list emptied out of a real (in-progress) quest
        if not terminated:
            prev_quest = prev_state.raw.get("quest") if prev_state else None
            prev_quest_id = prev_quest.get("id") if isinstance(prev_quest, dict) else None
            prev_had_monster = isinstance(prev_state.raw.get("monsters") if prev_state else None, list) and bool(
                prev_state.raw.get("monsters") if prev_state else []
            )
            curr_monsters_empty = curr_state.raw.get("monsters") == []

            if prev_had_monster and curr_monsters_empty and prev_quest_id is not None and prev_quest_id >= 0:
                terminated = True
                # Best-effort win/loss distinction from HP alone: if the
                # monster's last known HP fraction was already at/near 0
                # just before it disappeared, call it a defeat. Otherwise
                # we can't tell (captured/fled/quest abandoned) — bucket
                # as unknown rather than guess. See
                # configs/monsters/*.yaml's episode_boundaries.notes.
                if monster_prev_frac is not None and monster_prev_frac <= self.near_zero_hp_threshold:
                    reason = "monster_defeated"
                    reward += self.monster_defeated_bonus
                else:
                    reason = "quest_ended_unknown"
                    reward += self.quest_ended_unknown_reward

        # 3. step budget exhausted
        if not terminated and step_index + 1 >= self.max_episode_steps:
            truncated = True
            reason = "max_steps"

        quest_curr = curr_state.raw.get("quest")
        quest_state_raw = quest_curr.get("state") if isinstance(quest_curr, dict) else None

        return StepOutcome(
            reward=reward,
            terminated=terminated,
            truncated=truncated,
            reason=reason,
            monster_hp_fraction=monster_curr_frac,
            player_hp_fraction=player_curr_frac,
            quest_state_raw=quest_state_raw,
        )
