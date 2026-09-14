{
  description = "fly-mhw: teaching a fruit-fly brain connectome to hunt in Monster Hunter World";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # Phase 0 deps only. Phase 2+ (brain/training work) will add
        # torch, numpy, pyyaml, etc. here once that starts — same list
        # requirements.txt tracks for non-Nix machines.
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          evdev  # virtual gamepad via /dev/uinput — nixpkgs builds this
                 # properly against the running kernel's headers, unlike
                 # the evdev-binary workaround requirements.txt needs for
                 # a plain venv on this machine (see docs/risks.md).
          pillow # decode PNG frames from grim
        ]);
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
      });
}
