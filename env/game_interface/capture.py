"""
Phase 0 screen-capture backend, Hyprland/wlroots-specific.

grim is a screenshot CLI for wlroots compositors; each call here shells out
to it and decodes the PNG it writes to stdout. This is simple and
known-good on this machine (Hyprland/Wayland — mss and other X11 capture
libraries do not work here at all), but the per-call subprocess spawn is a
real throughput ceiling. Phase 0 only needs "capture works, and measure the
rate/latency it gets" (per the roadmap) — if that measurement says this is
too slow for the eventual real-time training loop (Phase 4), the upgrade
path is a PipeWire ScreenCast-portal-based capture that streams frames
without a spawn-per-frame; not built here.
"""

from __future__ import annotations

import io
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from PIL import Image


class GrimCaptureError(RuntimeError):
    """grim failed or produced no usable output."""


@dataclass
class CaptureResult:
    image: Image.Image
    captured_at: float
    latency_seconds: float


def capture_frame(geometry: Optional[str] = None) -> CaptureResult:
    """Capture one frame via grim.

    geometry: optional "X,Y WxH" string (grim -g syntax) to capture a
    region instead of the whole screen/output — useful once the MHW
    window's position/size is known, to avoid capturing/decoding pixels we
    don't need. Use `slurp` interactively to find a region's geometry
    string.
    """
    cmd = ["grim"]
    if geometry:
        cmd += ["-g", geometry]
    cmd += ["-"]  # write PNG to stdout

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

    return CaptureResult(image=image, captured_at=end, latency_seconds=end - start)


def measure_capture_rate(num_frames: int = 20, geometry: Optional[str] = None) -> float:
    """Capture num_frames frames back-to-back and return frames/second.

    This is the Phase 0 "measure achievable rate/latency" check from the
    roadmap — run it and record the result in docs/risks.md.
    """
    start = time.monotonic()
    for _ in range(num_frames):
        capture_frame(geometry=geometry)
    elapsed = time.monotonic() - start
    return num_frames / elapsed if elapsed > 0 else float("inf")
