{
  description = "fly-mhw: teaching an agent to hunt in Monster Hunter World from pixels";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # Phase 0/1 deps. Phase 2+ (model/training work) will add torch,
        # numpy, etc. here once that starts — same list requirements.txt
        # tracks for non-Nix machines.
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          evdev  # virtual gamepad via /dev/uinput — nixpkgs builds this
                 # properly against the running kernel's headers, unlike
                 # the evdev-binary workaround requirements.txt needs for
                 # a plain venv on this machine (see docs/risks.md).
          pillow # decode PNG frames from grim
          pyyaml # configs/*.yaml (env/config_loader.py)
          pycryptodome # scripts/patch_arena_quest.py's Blowfish decrypt/re-encrypt
        ]);

        # `nix run .#<name> -- <args>` for every script in scripts/, instead
        # of `nix develop` + `python scripts/<name>.py <args>`. Each script
        # resolves its own imports (env.game_interface, ...) relative to
        # __file__, so pointing python straight at the copy of the script
        # inside `self` (the flake's own source) works with no extra
        # PYTHONPATH wiring — same as running it from a `nix develop` shell
        # at the repo root.
        mkScriptApp = scriptRelPath: {
          type = "app";
          # PYTHONUNBUFFERED=1: without it, Python fully buffers stdout
          # whenever it's not a TTY (piped, redirected, `nix run ... | tee
          # log`, etc.) — confirmed live: output was silently lost with a
          # bare `timeout N nix run .#verify-state-read > log` until this
          # was added, not just a testing artifact.
          program = "${pkgs.writeShellScript (builtins.baseNameOf scriptRelPath) ''
            export PYTHONUNBUFFERED=1
            exec ${pythonEnv}/bin/python ${self}/${scriptRelPath} "$@"
          ''}";
        };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.grim        # screen capture (env/game_interface/capture.py)
            pkgs.slurp       # interactive region selection, for finding
                              # capture geometry (see docs/performance_tuning.md)
            pkgs.wf-recorder # continuous capture, if grim proves too slow
            pkgs.gamescope   # optional performance/capture-window tool —
                              # see docs/performance_tuning.md; not required
                              # for Phase 0 itself
          ];

          shellHook = ''
            echo "fly-mhw dev shell — $(python3 --version)"
            echo "Try: python scripts/verify_state_read.py"
            echo "     python scripts/verify_input_injection.py"
          '';
        };

        apps = {
          verify-state-read = mkScriptApp "scripts/verify_state_read.py";
          verify-input-injection = mkScriptApp "scripts/verify_input_injection.py";
          list-windows = mkScriptApp "scripts/list_windows.py";
          run-dummy-policy = mkScriptApp "scripts/run_dummy_policy.py";
          # run_dummy_policy.py requires --monster/--state-path (--tools
          # defaults to the Great Sword tools config) — e.g.:
          #   nix run .#run-dummy-policy -- \
          #     --monster configs/monsters/great_jagras.yaml \
          #     --state-path "$HOME/.local/share/Steam/steamapps/common/Monster Hunter World/fly_mhw_state.json" \
          #     --policy idle

          calibrate-keyboard-bindings = mkScriptApp "scripts/calibrate_keyboard_bindings.py";
          # needs this user in the "input" group to read /dev/input/eventN
          # (see docs/risks.md) — e.g.:
          #   nix run .#calibrate-keyboard-bindings

          record-hunt = mkScriptApp "scripts/record_hunt.py";
          # nix run .#record-hunt   — zero args needed, defaults to this
          # project's one pilot pair + this machine's MHW install (see
          # scripts/record_hunt.py's own defaults for overrides). Same
          # "input" group prerequisite as calibrate-keyboard-bindings above.

          # Tool-based action space (docs/architecture.md):
          verify-tools = mkScriptApp "scripts/verify_tools.py";
          # MHW focused, weapon drawn — runs each move's shortest combo
          # path from neutral; --chain a,b,c for a specific sequence.
          audit-tool-labels = mkScriptApp "scripts/audit_tool_labels.py";
          # checks recorded demos' labels + the moveset graph's declared
          # and candidate edges against the game's own lmtIDs.
          relabel-demos = mkScriptApp "scripts/relabel_demos.py";
          # re-labels recorded v2 episodes from their raw events after a
          # graph/timing/bindings change.

          patch-arena-quest = mkScriptApp "scripts/patch_arena_quest.py";
          # Side project, not part of the ML pipeline — see that script's
          # own module docstring and docs/architecture.md's "Starting a
          # quest programmatically" entry. Requires --source/--output/
          # --quest-id, e.g.:
          #   nix run .#patch-arena-quest -- \
          #     --source /path/to/some/mod's/questData_XXXXX.mib \
          #     --output /path/to/questData_90099.mib \
          #     --quest-id 90099
        };
      });
}
