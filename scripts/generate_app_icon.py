"""
Minimalist trading-style app icon: dark rounded tile, dual-tone green S-curve arrow,
simple vertical bars (candle hint + subtle A read). No text, glow, or blur effects.

Writes assets/icon.png (RGBA, transparent outside rounded tile) and assets/icon.ico.

Run from repo root:  python scripts/generate_app_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
import ico_export  # noqa: E402

SIZE = 1024
INSET = 44
RADIUS = int(SIZE * 0.185)

# Flat navy / charcoal (no gradient per spec)
BG = (17, 24, 39, 255)  # #111827
# Optional hairline — very subtle, still flat
BORDER = (51, 65, 85, 90)

# Dual flat greens (not glossy): outer stroke darker, inner lighter
GREEN_DARK = (22, 101, 52, 255)  # #166534
GREEN_LIGHT = (74, 222, 128, 255)  # #4ade80

# Candle bodies: slightly muted green-gray for contrast on dark
CANDLE = (34, 197, 94, 255)  # #22c55e
CANDLE_DIM = (21, 128, 61, 255)  # #15803d


def _bezier_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    n: int,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = i / (n - 1)
        mt = 1.0 - t
        a = mt * mt * mt
        b = 3.0 * mt * mt * t
        c = 3.0 * mt * t * t
        d = t * t * t
        x = a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0]
        y = a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]
        pts.append((x, y))
    return pts


def _arrow_points(ix0: float, iy0: float, iw: float, ih: float) -> list[tuple[float, float]]:
    """Thin upward S-curve: low-left → high-right (control points tuned for small-size read)."""
    p0 = (ix0 + iw * 0.10, iy0 + ih * 0.78)
    p1 = (ix0 + iw * 0.42, iy0 + ih * 0.88)  # first belly of S
    p2 = (ix0 + iw * 0.38, iy0 + ih * 0.38)  # sweep up
    p3 = (ix0 + iw * 0.88, iy0 + ih * 0.14)  # exit top-right
    return _bezier_cubic(p0, p1, p2, p3, 56)


def _draw_polyline_stroke(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    color: tuple[int, int, int, int],
    width: int,
) -> None:
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=color, width=width)


def _draw_arrow_dual_tone(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]]) -> None:
    """Two solid strokes, same path — reads as two flat greens at small sizes."""
    w_outer = max(10, int(SIZE * 0.012))
    w_inner = max(5, int(SIZE * 0.0065))
    _draw_polyline_stroke(draw, pts, GREEN_DARK, w_outer)
    _draw_polyline_stroke(draw, pts, GREEN_LIGHT, w_inner)


def _draw_candles(
    draw: ImageDraw.ImageDraw,
    ix0: float,
    iy0: float,
    iw: float,
    ih: float,
) -> None:
    """
    Three simple vertical bars; middle taller (A peak); right bar shifted right
    (separated) so the cluster hints at an 'A' without text.
    """
    base_y = iy0 + ih * 0.84
    bw = max(14, int(iw * 0.055))  # body width — survives 16px icon

    # Left leg (shorter)
    h1 = ih * 0.14
    x1 = ix0 + iw * 0.26
    y1t = base_y - h1
    draw.rectangle([x1, y1t, x1 + bw, base_y], fill=CANDLE_DIM)

    # Center peak (taller)
    h2 = ih * 0.22
    x2 = ix0 + iw * 0.455 - bw * 0.5
    y2t = base_y - h2
    draw.rectangle([x2, y2t, x2 + bw, base_y], fill=CANDLE)

    # Right leg — separated gap from center cluster
    h3 = ih * 0.15
    x3 = ix0 + iw * 0.72
    y3t = base_y - h3
    draw.rectangle([x3, y3t, x3 + bw, base_y], fill=CANDLE_DIM)

    # Minimal wicks (1px sharp lines) for candlestick cue — high contrast
    wick = max(1, int(SIZE * 0.0025))
    cx1, cx2, cx3 = x1 + bw // 2, x2 + bw // 2, x3 + bw // 2
    draw.line([(cx1, y1t - ih * 0.02), (cx1, y1t)], fill=CANDLE_DIM, width=wick)
    draw.line([(cx2, y2t - ih * 0.028), (cx2, y2t)], fill=CANDLE, width=wick)
    draw.line([(cx3, y3t - ih * 0.022), (cx3, y3t)], fill=CANDLE_DIM, width=wick)


def render_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    x0, y0 = INSET, INSET
    x1, y1 = SIZE - INSET - 1, SIZE - INSET - 1
    iw_f = float(x1 - x0)
    ih_f = float(y1 - y0)

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=RADIUS, fill=BG)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=RADIUS, outline=BORDER, width=1)

    pts = _arrow_points(float(x0), float(y0), iw_f, ih_f)
    _draw_candles(draw, float(x0), float(y0), iw_f, ih_f)
    _draw_arrow_dual_tone(draw, pts)

    return img


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    png_path = root / "assets" / "icon.png"
    ico_path = root / "assets" / "icon.ico"
    im = render_icon()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(png_path, format="PNG", optimize=True)
    ico_export.save_windows_ico(im, ico_path)
    print(f"Wrote {png_path} and {ico_path}")


if __name__ == "__main__":
    main()
