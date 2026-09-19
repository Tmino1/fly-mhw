"""
Records a human-played hunt as demo data for eventual imitation learning
(Phase 3 of the roadmap), in the v2 tool-call format. This never touches
a VirtualGamepad — you play, this only observes:
  - frames: captured every step_period, with live game state and the
    reward-debug fields (frames.jsonl + frame_NNNNNN.png),
  - your input: every keyboard/mouse event with its kernel timestamp
    (input_events.jsonl, via demos/input_events.InputEventStream) — raw,
    so it can be re-labelled later (scripts/relabel_demos.py),
  - the game's own animation id (player lmtID) on every change
    (lmt_events.jsonl) — offline label auditing only.
At the end of each episode demos/labeling.py turns the events into
tool_calls.jsonl: move-named tool calls, each with the moveset-graph
state it happened in.

Optional exception, off the actual demo-data path: an auto-skip for the
real post-hunt "return to camp" wait (confirmed live to exist —
quest.state=3, quest.id unchanged, well after a kill). Two earlier
approaches failed before this one: a BTN_SOUTH gamepad tap did nothing
(turned out to be a keyboard/mouse-driven UI, not a gamepad one —
confirmed via a screenshot showing a "Tab" key icon), and UI automation
(SharpPluginLoader's F9 menu, click a button) would have needed a new
absolute-position pointer-click injection mechanism and been fragile to
screen/layout changes. The actual fix, if skip_flag_path is given:
create that file the first time quest.state==3 is seen, remove it
otherwise. lua_scripts/state_reader.lua watches for that file and, while
it exists, replicates SharpPluginLoader's own open-source "Quest End
Skip" example plugin's technique directly via a memory write
(Quest.QuestEndTimer.SetToEnd(), i.e. Timer.Time = Timer.MaxTime — read
from github.com/Fexty12573/SharpPluginLoader's actual source, not
guessed) — no SharpPluginLoader install, no input injection, needed at
all for this. This file is never touched during actual recording, only
between episodes, and is created/removed here — not visible to
the input event stream, so it can't leak into a label. The flag
file only exists while a recording session is actively asking for it,
so normal untracked play never sees this at all (explicit requirement).

Reuses env/reward.py's RewardModel directly for episode-boundary
detection and reward-debug fields (it's fully standalone — no MHWEnv/
gamepad dependency) and env/game_interface/lua_bridge.py's quest_id()
for quest-start detection (the same function env/mhw_env.py imports) —
neither is reimplemented here. See docs/architecture.md's Design
Principles: the reward computed is stored for offline analysis only,
never shown live or used to steer recording.

Causal ordering: every frame records the wall-clock time just before
its capture; demos/dataset.py pairs each tool call with the latest frame
captured at or before the call started — the same observation_t ->
action_t pairing MHWEnv.step() implies.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from env.game_interface.capture import GrimCaptureError, capture_frame
from env.game_interface.lua_bridge import GameState, LuaBridge, StateReadError, quest_id
from env.reward import RewardModel
from env.tools import ToolSet

from .input_events import InputEventStream
from .labeling import file_sha256, label_episode

# The confirmed-real (2026-09-19) post-hunt quest.state value seen while
# quest.id is still non-idle, well after a kill — the "return to camp"
# wait screen. See docs/risks.md's quest.state notes.
_POST_HUNT_WAIT_STATE = 3


@dataclass
class EpisodeSummary:
    episode_id: str
    frame_count: int
    duration_seconds: float
    termination_reason: str
    quest_id: Any
    distinct_quest_states: list = field(default_factory=list)
    labels: dict[str, Any] = field(default_factory=dict)  # demos/labeling.label_episode()'s summary


class DemoRecorder:
    def __init__(
        self,
        reward_model: RewardModel,
        lua_bridge: LuaBridge,
        events: InputEventStream,
        toolset: ToolSet,
        tools_config_path: str | Path,
        bindings_path: str | Path,
        mouse_cfg: dict[str, Any],
        capture_geometry: Optional[str],
        step_period_seconds: float,
        output_dir: str | Path,
        reset_timeout_seconds: float = 60.0,
        skip_flag_path: Optional[str | Path] = None,
    ):
        self.reward_model = reward_model
        self.lua_bridge = lua_bridge
        self.events = events
        self.toolset = toolset
        self.tools_config_path = Path(tools_config_path)
        self.bindings_path = Path(bindings_path)
        self.mouse_cfg = mouse_cfg
        self.capture_geometry = capture_geometry
        self.reset_timeout_seconds = reset_timeout_seconds
        self.step_period_seconds = step_period_seconds
        self.output_dir = Path(output_dir)
        # See module docstring: only used to auto-skip the post-hunt
        # "return to camp" wait, never during actual recording, never
        # visible to the input event stream. Must match
        # lua_scripts/state_reader.lua's SKIP_QUEST_END_FLAG_PATH once
        # resolved to an absolute path (that script writes relative to
        # the game's own working directory — see docs/modding_setup.md
        # on where that lands under Proton).
        self.skip_flag_path = Path(skip_flag_path) if skip_flag_path else None

    def _wait_until(self, predicate, timeout_seconds: float, phase_name: str, on_poll=None) -> GameState:
        # Identical pattern to env/mhw_env.py's MHWEnv._wait_until — kept
        # as a second small copy rather than a shared import, since
        # MHWEnv's version isn't currently a free function/staticmethod
        # (it's bound to self for its own gamepad/step-count state, which
        # this class doesn't have). Both call the same underlying
        # quest_id() helper, so behavior stays identical even though the
        # loop itself is duplicated.
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                state = self.lua_bridge.read()
                if on_poll:
                    on_poll(state)
                if predicate(state):
                    return state
            except StateReadError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"DemoRecorder timed out in phase {phase_name!r} after "
                    f"{timeout_seconds}s waiting on lua_bridge state"
                )
            time.sleep(0.5)

    def _update_skip_flag(self, state: Optional[GameState]) -> None:
        """Create skip_flag_path while quest.state==3, remove it otherwise
        — state_reader.lua polls for this file's existence and performs
        the actual memory write while it's present. Idempotent (touching
        an already-existing file, or removing an already-absent one, is a
        cheap no-op) so this can safely be called every tick."""
        if self.skip_flag_path is None or state is None:
            return
        quest = state.raw.get("quest")
        q_state = quest.get("state") if isinstance(quest, dict) else None
        if q_state == _POST_HUNT_WAIT_STATE:
            self.skip_flag_path.touch(exist_ok=True)
        else:
            self.skip_flag_path.unlink(missing_ok=True)

    def wait_for_quest_start(self, timeout_seconds: float = 60.0) -> GameState:
        """Drain a stale in-progress quest back to idle first (in case one
        was already running — e.g. the recorder was (re)started mid-hunt),
        then block until a human accepts a fresh quest (quest id
        transitions from -1/None to a real id).

        Each phase gets its OWN full timeout_seconds budget, not a shared
        one. Confirmed live 2026-09-19 why that matters: with a single
        shared deadline (this class's original behavior, copied from
        MHWEnv.reset() — a bug there too, not fixed there since a single
        Phase-1 acceptance-test run is less likely to hit it), Phase A
        patiently draining an already-in-progress ~410s hunt ate most of a
        600s budget, leaving Phase B only ~169s to catch the next
        quest-accept — nowhere near enough patience for a real session
        with normal pauses between hunts.
        """
        try:
            initial = self.lua_bridge.read()
        except StateReadError:
            initial = None
        if quest_id(initial) is not None and quest_id(initial) != -1:
            self._wait_until(
                lambda s: quest_id(s) == -1, timeout_seconds, "waiting_for_idle",
                on_poll=self._update_skip_flag,
            )

        return self._wait_until(
            lambda s: quest_id(s) not in (None, -1), timeout_seconds, "waiting_for_quest_start"
        )

    def record_episode(self, episode_id: Optional[str] = None) -> EpisodeSummary:
        start_state = self.wait_for_quest_start(timeout_seconds=self.reset_timeout_seconds)
        qid = quest_id(start_state)
        episode_id = episode_id or f"{time.strftime('%Y%m%dT%H%M%S')}_{qid}"
        episode_dir = self.output_dir / episode_id
        episode_dir.mkdir(parents=True, exist_ok=True)

        self.events.take()  # drop anything from between hunts
        started_at = time.time()
        prev_state: Optional[GameState] = start_state
        step = 0
        termination_reason = ""
        distinct_quest_states: list = []

        with (episode_dir / "frames.jsonl").open("w") as frames_f, \
                (episode_dir / "input_events.jsonl").open("w") as events_f, \
                (episode_dir / "lmt_events.jsonl").open("w") as lmt_f:

            def flush_events():
                keys, mouse, lmt = self.events.take()
                for k in keys:
                    events_f.write(json.dumps({"type": "key", "t": k.t, "role": k.role, "down": k.down}) + "\n")
                for m in mouse:
                    events_f.write(json.dumps({"type": "mouse", "t": m.t, "dx": m.dx, "dy": m.dy}) + "\n")
                for rec in lmt:
                    lmt_f.write(json.dumps(rec) + "\n")
                events_f.flush()
                lmt_f.flush()

            while True:
                t_frame = time.time()
                try:
                    capture = capture_frame(geometry=self.capture_geometry)
                except GrimCaptureError as exc:
                    termination_reason = f"capture_error: {exc}"
                    break

                try:
                    curr_state = self.lua_bridge.read()
                except StateReadError as exc:
                    termination_reason = f"state_read_error: {exc}"
                    break

                # Bug found live 2026-09-19: this call used to live only in
                # wait_for_quest_start()'s idle-drain phase, which only
                # runs AFTER the episode ends — but the episode can't end
                # until RewardModel sees the ENTIRE raw monster list go
                # empty (curr_monsters_empty = monsters == []), which only
                # happens once you're fully back at the hub and the whole
                # map's wildlife unloads too. That's the exact same wait
                # this was supposed to skip — circular, so it never fired.
                # Belongs in the active recording loop instead, where it
                # can actually run while quest.state==3 is still showing.
                self._update_skip_flag(curr_state)

                outcome = self.reward_model.step(prev_state, curr_state, step)

                # Writes grim's raw encoded bytes directly — capture.image
                # is only decoded for callers that need pixels immediately
                # (e.g. live inference); re-encoding it here would be a
                # second, wasted encode pass on top of grim's own. See
                # env/game_interface/capture.py's module docstring for the
                # PNG->JPEG throughput numbers this depends on.
                frame_ext = "jpg" if capture.raw_format == "jpeg" else capture.raw_format
                frame_path = episode_dir / f"frame_{step:06d}.{frame_ext}"
                frame_path.write_bytes(capture.raw_bytes)
                frame_name = frame_path.name

                if outcome.quest_state_raw not in distinct_quest_states:
                    distinct_quest_states.append(outcome.quest_state_raw)

                player = curr_state.raw.get("player")
                action = player.get("action") if isinstance(player, dict) else None
                frames_f.write(json.dumps({
                    "step": step,
                    "t": t_frame,
                    "frame": frame_name,
                    "capture_latency_seconds": capture.latency_seconds,
                    "state_age_seconds": curr_state.file_age_seconds,
                    "quest_id": quest_id(curr_state),
                    "termination_reason": outcome.reason,
                    # Same reward_debug shape MHWEnv._build_info() uses —
                    # offline-analysis-only, never fed back into anything
                    # that steers recording. See module docstring.
                    "reward_debug": {
                        "reward": outcome.reward,
                        "quest_state_raw": outcome.quest_state_raw,
                        "monster_hp_fraction": outcome.monster_hp_fraction,
                        "player_hp_fraction": outcome.player_hp_fraction,
                        "monster_id": outcome.monster_id,
                        "monster_count": outcome.monster_count,
                        "player_action": action,
                    },
                }) + "\n")
                frames_f.flush()
                flush_events()

                prev_state = curr_state
                step += 1

                if outcome.terminated or outcome.truncated:
                    termination_reason = outcome.reason
                    break

                time.sleep(self.step_period_seconds)

            flush_events()

        ended_at = time.time()
        labels = label_episode(episode_dir, self.toolset, self.mouse_cfg, t_begin=started_at, t_end=ended_at)
        summary = EpisodeSummary(
            episode_id=episode_id,
            frame_count=step,
            duration_seconds=ended_at - started_at,
            termination_reason=termination_reason,
            quest_id=qid,
            distinct_quest_states=distinct_quest_states,
            labels=labels,
        )

        meta = {
            "schema": "fly-mhw/demo_episode/v2",
            "episode_id": episode_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "frame_count": summary.frame_count,
            "termination_reason": summary.termination_reason,
            "quest_id": summary.quest_id,
            "distinct_quest_states_seen": summary.distinct_quest_states,
            "step_period_seconds": self.step_period_seconds,
            "capture_geometry": self.capture_geometry,
            # Which tool space / moveset graph / bindings these labels were
            # made against — scripts/relabel_demos.py rewrites the labels
            # (and these) if any of them change later.
            "tools_config": str(self.tools_config_path),
            "tools_config_sha256": file_sha256(self.tools_config_path),
            "moveset_config": str(self.toolset.moveset_path),
            "moveset_config_sha256": file_sha256(self.toolset.moveset_path),
            "keyboard_bindings": str(self.bindings_path),
            "keyboard_bindings_sha256": file_sha256(self.bindings_path),
            "labels": labels,
        }
        (episode_dir / "episode_meta.json").write_text(json.dumps(meta, indent=2))

        return summary
