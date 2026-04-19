"""
Build a Windows-friendly multi-size .ico from a square RGBA master.

Uses LANCZOS downsampling (smoother taskbar / title bar than BOX). Includes
sizes Windows 10/11 commonly requests (16–256 + 20/40/72 for shell DPI).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# Largest first — first frame is a common shell preference
ICO_SIZES = (256, 192, 128, 96, 72, 64, 48, 40, 32, 24, 20, 16)
# Upscale small masters so downscales to 256 are less jaggy
MASTER_MIN = 512


def _to_square_rgba(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    w, h = rgba.size
    if w == h:
        return rgba
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    return rgba.crop((left, top, left + s, top + s))


def save_windows_ico(im: Image.Image, ico_path: Path) -> None:
    master = _to_square_rgba(im)
    m = max(master.size)
    if m < MASTER_MIN:
        scale = MASTER_MIN / m
        nw = max(1, int(round(master.width * scale)))
        nh = max(1, int(round(master.height * scale)))
        master = master.resize((nw, nh), Image.Resampling.LANCZOS)

    images: list[Image.Image] = []
    for side in ICO_SIZES:
        images.append(master.resize((side, side), Image.Resampling.LANCZOS))

    ico_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        ico_path,
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
        append_images=images[1:],
    )
