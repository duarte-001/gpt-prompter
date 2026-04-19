"""
Trim **outer** white margins only: white that touches the image edge (canvas
background). White **inside** the icon (e.g. candles on blue) stays — it is not
connected to the edge through white pixels.

Writes assets/icon.png + assets/icon.ico.

Run from repo root:  python scripts/crop_app_icon.py [optional/path/to/source.png]
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
import ico_export  # noqa: E402

# Near-white treated as “background” for flood from edges only
WHITE_THRESH = 245


def _outer_white_mask(rgb: np.ndarray) -> np.ndarray:
    """
    True where pixel is part of the **outer** white region (4-connected to an
    edge pixel that is near-white). Inner white enclosed by the icon is False.
    """
    h, w = rgb.shape[:2]
    white = np.all(rgb[:, :, :3] >= WHITE_THRESH, axis=2)
    visited = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()

    def try_push(y: int, x: int) -> None:
        if 0 <= y < h and 0 <= x < w and white[y, x] and not visited[y, x]:
            visited[y, x] = True
            q.append((y, x))

    for x in range(w):
        try_push(0, x)
        try_push(h - 1, x)
    for y in range(h):
        try_push(y, 0)
        try_push(y, w - 1)

    while q:
        y, x = q.popleft()
        if y > 0:
            try_push(y - 1, x)
        if y + 1 < h:
            try_push(y + 1, x)
        if x > 0:
            try_push(y, x - 1)
        if x + 1 < w:
            try_push(y, x + 1)
    return visited


def _content_bbox(rgb: np.ndarray) -> tuple[int, int, int, int]:
    outer = _outer_white_mask(rgb)
    content = ~outer
    if not np.any(content):
        return 0, 0, rgb.shape[1] - 1, rgb.shape[0] - 1
    ys, xs = np.where(content)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def crop_outer_white(im: Image.Image, *, pad: int = 8) -> Image.Image:
    rgb = np.array(im.convert("RGB"))
    x0, y0, x1, y1 = _content_bbox(rgb)
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(im.width - 1, x1 + pad)
    y1 = min(im.height - 1, y1 + pad)
    return im.crop((x0, y0, x1 + 1, y1 + 1))


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "assets" / "icon.png"
    dst_png = root / "assets" / "icon.png"
    dst_ico = root / "assets" / "icon.ico"
    if not src.exists():
        print(f"Source not found: {src}", file=sys.stderr)
        sys.exit(1)

    with Image.open(src) as im0:
        im0.load()
        base = im0.convert("RGBA")

    cropped = crop_outer_white(base, pad=8)
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(dst_png, format="PNG", optimize=True)
    ico_export.save_windows_ico(cropped, dst_ico)
    print(f"Wrote {dst_png} ({cropped.size}) and {dst_ico}")


if __name__ == "__main__":
    main()
