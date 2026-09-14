# Architecture

Full design rationale, prior art, and the phased roadmap live in the
planning document this project was scoped from:
`~/.claude/plans/do-you-know-about-valiant-wreath.md` (not part of this
repo — copy the relevant sections in here as they firm up, so the repo is
self-contained once Phase 0/1 settle).

This file tracks the concrete technical decisions made so far, which are
this machine/environment's answers to the roadmap's open questions.

## Phase 0 decisions

| Concern | Decision | Why |
|---|---|---|
| Screen capture | `grim` (wlroots screenshot CLI), shelled out per frame | This machine runs Hyprland/Wayland, not X11 — `mss` and other X11-based capture libraries don't work here. `grim` is already installed and works on any wlroots compositor. Per-process-spawn overhead is a known limitation — see `capture.py`'s docstring. |
| Structured state IPC | Lua script writes a JSON snapshot file; Python polls it | Simplest thing likely to work without confirming LuaEngine's full API surface first. Websocket support is hinted at in LuaEngine's own docs and would be a strictly better upgrade — revisit once the real API is inspected (see `docs/modding_setup.md`). |
| Input injection | Virtual Xbox 360 gamepad via `/dev/uinput` (`python-evdev`) | Wine/Proton's controller input goes through SDL with an evdev fallback, so a uinput-created virtual pad is picked up the same way a physical controller would be. `/dev/uinput` already has an ACL entry for this user on this machine — no extra setup needed. |
| Python environment | Plain `venv` + `pip`, not the Nix-managed system Python | System `python3` here is a Nix per-user profile interpreter with no `pip`; `python3 -m venv` does produce a working `pip` inside the venv. |

## Not yet decided (later phases)

- Full connectome scale vs. a scoped-down subset (Phase 2 — needs measured
  parameter count / forward-pass latency first).
- Exact reward-shaping weights for Great Jagras (Phase 1, once
  `configs/monsters/great_jagras.yaml` is written).
- Great Sword action-space granularity (Phase 1, once
  `configs/weapons/greatsword.yaml` is written).
