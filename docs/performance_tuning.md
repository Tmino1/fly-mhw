# Performance tuning: making MHW run smoother

This started as a side question ("can we down-sample the game to run
smoother?") but it's directly useful to the project too: Phase 0 measured
`grim`-based capture at only ~2.4 fps
(see `docs/risks.md`), largely because it was capturing this machine's
*entire* 5360×1440 multi-monitor desktop instead of just the game window.
Lowering the game's own render load and shrinking what gets captured are
the same problem from two angles — this doc covers both.

## Baseline (read from the game's own config, 2026-09-14)

From `graphics_option.ini` in the MHW install directory:

```
Resolution=2560x1440
DisplayName=Display2
DisplayIndex=1
NVIDIA DLSS=Off
ResolutionScaling=High
DirectX12Enable=Off
... (most quality settings at High/Highest)
```

Useful fact hiding in there: **MHW already renders to one 2560×1440
display (`Display2`), not the full virtual desktop.** `grim`'s slow
measurement was capturing far more than the game actually occupies.

## Free win: capture only the game's output

Before touching a single graphics setting — `grim -o <output-name>`
captures a single named output instead of everything. Find MHW's actual
Wayland output name (`hyprctl monitors` lists them) and pass it to
`env/game_interface/capture.capture_frame(geometry=...)`, or use `grim -o`
directly. Cuts captured pixel area roughly in half immediately, no
in-game changes needed.

## In-game settings, ranked by FPS impact when lowered

(Per the [Steam Community graphics guide](https://steamcommunity.com/sharedfiles/filedetails/?id=1850758934).)

| # | Setting | Impact when lowered | Recommendation |
|---|---|---|---|
| 1 | **"Image Quality"** (this is what `ResolutionScaling` in the ini maps to) | Highest — literally an internal-render-resolution slider: Low / Mid / High / Variable (Prioritise Framerate) / Variable (Prioritise Resolution) | **Try this first.** Set to "Variable (Prioritise Framerate)" — it's the actual "down-sample" control and self-adjusting |
| 2 | NVIDIA DLSS | High — up to ~50% in demanding scenes per [Nvidia's own numbers](https://www.nvidia.com/en-us/geforce/news/monster-hunter-world-nvidia-dlss/) | Turn on, Performance or Balanced mode. This is DLSS 1.0 (version-capped, no newer-DLSS mod applied here) but still a real win. Pair with a bit of FidelityFX CAS sharpening (`FidelityFX CAS` toggle, already in the same menu) to counter the softness |
| 3 | Volume Rendering Quality | High | Drop if step 1 alone isn't enough |
| 4 | Foliage Sway, SH Diffuse, LOD Bias/Max LOD Level | Moderate–high | Drop next |
| 5 | Ambient Occlusion | Moderate | |
| 6 | Shadow Quality, Screen Space Reflection | Low–moderate | |
| 7 | Anti-Aliasing (currently TAA+FXAA) | Low | **Leave alone even when trimming elsewhere** — a cleaner, less noisy frame is also better input for the connectome's visual encoder later (project-specific reason on top of the general one) |

Suggested order: (1) → measure → (2) → measure → only then start on 3–5.

## System-level option: `gamescope` (not installed here)

[`gamescope`](https://github.com/ValveSoftware/gamescope) is Valve's
nested Wayland compositor. Confirmed **not installed** on this machine
(`command -v gamescope` found nothing) — this is an opt-in suggestion, not
something already set up.

What it buys, beyond raw performance:

- Decouples internal render resolution from output resolution: `-w/-h`
  sets what the game actually renders at, `-W/-H` sets the output size it
  gets scaled to. Upscale with `-F fsr` or `-F nis`, or a crisp
  `-S integer` scale.
- Hard FPS caps and VRR support.
- **Contains the game to one fixed-size, fixed-position window** — which
  solves the capture-cropping problem a different way than "just grab
  Display2": instead of depending on which output the game happens to be
  on, `grim -g` can target gamescope's known, stable window geometry
  directly. Smoother play and a more robust capture target from one tool.

Would be invoked as a Steam launch option:

```
gamescope -w 1920 -h 1080 -W 2560 -H 1440 -F fsr -- %command%
```

(internal render at 1920×1080, upscaled to 2560×1440 output via FSR —
adjust to taste). Install via the distro package manager before trying
this; not done as part of this pass.

## Measuring the effect

- **General FPS/frametime:** [`mangohud`](https://github.com/flightlicense/MangoHud) is the standard Linux overlay for this — also not installed here, same opt-in caveat as gamescope.
- **What actually matters for this project — capture throughput:** re-run
  the Phase 0 measurement already built into the repo, before and after
  any change:
  ```sh
  python -c "from env.game_interface.capture import measure_capture_rate; print(measure_capture_rate())"
  ```
  Baseline from Phase 0 (whole-desktop capture, default in-game settings):
  **~2.4 fps**. Record new numbers here as changes are tried.

## Open questions

- Whether "Image Quality"/`ResolutionScaling` and DLSS's own internal
  scaling interact or override each other when both are active — untested,
  worth checking empirically rather than assuming they stack.
- Actual FSR/output quality trade-off with gamescope once installed — not
  evaluated yet.
