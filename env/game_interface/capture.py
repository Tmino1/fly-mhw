"""
Phase 0 screen-capture backend, Hyprland/wlroots-specific.

grim is a screenshot CLI for wlroots compositors; each call here shells out
to it and decodes the image it writes to stdout. This is simple and
known-good on this machine (Hyprland/Wayland — mss and other X11 capture
libraries do not work here at all), but the per-call subprocess spawn is a
real throughput ceiling. Phase 0 only needs "capture works, and measure the
rate/latency it gets" (per the roadmap) — if that measurement says this is
too slow for the eventual real-time training loop (Phase 4), the upgrade
path is a PipeWire ScreenCast-portal-based capture that streams frames
without a spawn-per-frame; not built here.

JPEG, not PNG: measured live, 2026-09-19, at this project's actual capture
geometry (2003,33 3324x1374) — grim's PNG encode alone averaged 280ms/frame
(worse in a sustained run: 3.47 fps), almost certainly the dominant reason
the whole pipeline topped out below its documented 5fps target, since a
single capture could already exceed the 200ms step budget. Switching to
JPEG (quality 85): grim capture throughput 3.47 -> 33.8 fps (10x, sustained
10-frame measurement), PIL decode 46ms -> 12ms/frame (4x), file size 1.7MB
-> 0.6MB (2.8x smaller). The eventual pixel input to the brain is a 32x32
greyscale downsample (see brain/connectome_brain.py's preprocess_image) —
JPEG's compression artifacts are complete noise relative to that, so there
is no real accuracy cost, only upside.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from PIL import Image


class GrimCaptureError(RuntimeError):
    """grim failed or produced no usable output."""


_DEFAULT_JPEG_QUALITY = 85


@dataclass
class CaptureResult:
    image: Image.Image
    captured_at: float
    latency_seconds: float
    # Raw encoded bytes exactly as grim produced them — lets a caller that
    # only wants to persist the frame (demos/recorder.py) write straight to
    # disk instead of re-encoding the already-decoded `image`, which would
    # otherwise be a second, wasted encode pass on top of grim's own.
    raw_bytes: bytes
    raw_format: str  # "jpeg" or "png", matches capture_frame()'s `fmt` arg


def capture_frame(
    geometry: Optional[str] = None,
    fmt: str = "jpeg",
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
) -> CaptureResult:
    """Capture one frame via grim.

    geometry: optional "X,Y WxH" string (grim -g syntax) to capture a
    region instead of the whole screen/output — useful once the MHW
    window's position/size is known, to avoid capturing/decoding pixels we
    don't need. Use `slurp` interactively to find a region's geometry
    string.

    fmt: "jpeg" (default) or "png". JPEG is ~10x faster to capture and ~4x
    faster to decode than PNG at this project's capture geometry (measured
    live, 2026-09-19 — see this module's docstring) with no real downstream
    accuracy cost, since the brain only ever sees a 32x32 greyscale
    downsample of it. "png" stays available for lossless capture if ever
    needed (e.g. debugging a capture artifact).
    """
    cmd = ["grim"]
    if fmt == "jpeg":
        cmd += ["-t", "jpeg", "-q", str(jpeg_quality)]
    elif fmt != "png":
        raise ValueError(f"fmt must be 'jpeg' or 'png', got {fmt!r}")
    if geometry:
        cmd += ["-g", geometry]
    cmd += ["-"]  # write to stdout

    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise GrimCaptureError("grim not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise GrimCaptureError(f"grim exited {exc.returncode}: {exc.stderr!r}") from exc
    end = time.monotonic()

    try:
        image = Image.open(io.BytesIO(proc.stdout))
        image.load()
    except Exception as exc:
        raise GrimCaptureError("failed to decode grim output as an image") from exc

    return CaptureResult(
        image=image, captured_at=end, latency_seconds=end - start,
        raw_bytes=proc.stdout, raw_format=fmt,
    )



# UNVERIFIED (2026-09-18): written with MHW not running, so the exact
# Hyprland window `class` it reports under Proton is unknown — these are
# best-guess patterns (the Steam app id, and the literal name), matched
# case-insensitively as substrings against both `class` and `title`. Same
# "guess, flag it, verify live" pattern as the rest of this project —
# once the game is running, run `scripts/list_windows.py` to see what it
# actually reports, and narrow/fix these patterns if they didn't match.
_MHW_CLASS_PATTERNS: Sequence[str] = ("monsterhunterworld", "steam_app_582010")
_MHW_TITLE_PATTERNS: Sequence[str] = ("monster hunter world",)


def find_window_geometry(
    class_patterns: Sequence[str] = _MHW_CLASS_PATTERNS,
    title_patterns: Sequence[str] = _MHW_TITLE_PATTERNS,
) -> Optional[str]:
    """Query Hyprland for a mapped window matching class_patterns or
    title_patterns (case-insensitive substring match against `hyprctl
    clients -j`'s `class`/`title` fields) and return a grim -g compatible
    "X,Y WxH" geometry string for it, or None if hyprctl is unavailable,
    fails, or nothing matches (e.g. the game isn't running).

    Call this ONCE and reuse the returned string across many
    capture_frame()/measure_capture_rate() calls — this shells out to
    hyprctl and does not belong in a per-frame hot path. If it returns
    None, capture_frame(geometry=None) still works — it just falls back
    to capturing the whole multi-monitor desktop, the original Phase 0
    behavior.
    """
    if shutil.which("hyprctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["hyprctl", "clients", "-j"], capture_output=True, check=True, text=True
        )
        clients = json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None

    for client in clients:
        if not client.get("mapped"):
            continue
        cls = (client.get("class") or "").lower()
        title = (client.get("title") or "").lower()
        if any(p in cls for p in class_patterns) or any(p in title for p in title_patterns):
            x, y = client["at"]
            w, h = client["size"]
            return f"{x},{y} {w}x{h}"
    return None


def measure_capture_rate(num_frames: int = 20, geometry: Optional[str] = None, fmt: str = "jpeg") -> float:
    """Capture num_frames frames back-to-back and return frames/second.

    This is the Phase 0 "measure achievable rate/latency" check from the
    roadmap — run it and record the result in docs/risks.md. Pass
    fmt="png" to reproduce the original (much slower) baseline measurement
    for comparison.
    """
    start = time.monotonic()
    for _ in range(num_frames):
        capture_frame(geometry=geometry, fmt=fmt)
    elapsed = time.monotonic() - start
    return num_frames / elapsed if elapsed > 0 else float("inf")
