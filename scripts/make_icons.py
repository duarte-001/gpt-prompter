"""
Build app icons: original glyph from Designer.png, plus a dark slate outer contour
(#0F172A), on a transparent background; tight crop; scale to 1024 master, ICO, PNG.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# Every size the Windows shell may request.
ICO_SIZES = (256, 192, 128, 96, 72, 64, 48, 40, 32, 24, 20, 16)

FAVICON_SIZE = 256

# Brand slate = former background colour, now the contour *around* the symbol.
OUTLINE_SLATE = (15, 23, 42, 255)  # #0F172A

# Thickness of the dark contour in pixels, before the final 1024 scale (relative to
# the padded working canvas — tune for elegance on taskbar 16-32px).
OUTLINE_PX = 20

# Extra margin around the tight glyph bbox (fraction of max(bbox w, h)).
PADDING_FRAC = 0.10

# Final 1024 tile: the artwork (glyph + outline) is scaled to fit this fraction
# of the short side, centered on transparent.
FILL_FRACTION = 0.88


def _glyph_mask_bright_cyan(src: Image.Image) -> np.ndarray:
    """
    Pixels in the source that belong to the cyan glyph. Designer.png uses
    a dark slate-like background; the mark is (g+b)-R high.
    """
    arr = np.array(src.convert("RGBA"))
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    bright = g.astype(int) + b.astype(int) - r.astype(int)
    return (bright > 200).astype(np.uint8) * 255


def _thicken_l(mask: Image.Image, k: int) -> Image.Image:
    """Dilate a binary/gray mask; k = radius in px (diameter 2k+1)."""
    d = 2 * k + 1
    if d < 3:
        return mask
    return mask.filter(ImageFilter.MaxFilter(size=d))


def _padded_bbox(mask: Image.Image) -> tuple[int, int, int, int] | None:
    bbox = mask.getbbox()
    if not bbox:
        return None
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    p = int(max(8, PADDING_FRAC * max(w, h)))
    mw, mh = mask.size
    return (
        max(0, x0 - p),
        max(0, y0 - p),
        min(mw, x1 + p),
        min(mh, y1 + p),
    )


def _build_layered_rgba(
    src_rgba: Image.Image,
    mask: Image.Image,
) -> Image.Image:
    """
    Padded region: slate from dilated mask, original pixels on top from src where mask
    (original glyph) is. Outline = visual ring where dilate > 0 and glyph is not drawn.
    """
    pad = OUTLINE_PX + 6
    w, h = mask.size
    canvas = (w + 2 * pad, h + 2 * pad)

    mask_p = Image.new("L", canvas, 0)
    mask_p.paste(mask, (pad, pad))

    dil = _thicken_l(mask_p, OUTLINE_PX)
    # Slate underlay (full “halo” shape).
    slate = Image.new("RGBA", canvas, (0, 0, 0, 0))
    s = np.array(dil)
    slate_arr = np.array(slate)
    o = np.array(OUTLINE_SLATE, dtype=np.uint8)
    slate_arr[:, :, 0] = o[0]
    slate_arr[:, :, 1] = o[1]
    slate_arr[:, :, 2] = o[2]
    slate_arr[:, :, 3] = s
    slate = Image.fromarray(slate_arr).convert("RGBA")

    # Original glyph: keep source RGB, alpha from mask only (strips background in the crop).
    g = src_rgba.copy()
    if g.size != mask.size:
        g = g.resize(mask.size, Image.Resampling.LANCZOS)
    g.putalpha(mask)
    g_p = Image.new("RGBA", canvas, (0, 0, 0, 0))
    g_p.paste(g, (pad, pad))

    out = Image.alpha_composite(slate, g_p)
    a = out.split()[3]
    bb = a.getbbox()
    if not bb:
        return out
    return out.crop(bb)


def _mask_from_src(src: Image.Image) -> Image.Image:
    m = _glyph_mask_bright_cyan(src)
    return Image.fromarray(m).convert("L")


def _render_frame(master: Image.Image, side: int) -> Image.Image:
    frame = master.resize((side, side), Image.Resampling.LANCZOS)
    if side <= 72:
        frame = frame.filter(
            ImageFilter.UnsharpMask(radius=1.0, percent=220, threshold=2)
        )
    if side <= 32:
        frame = ImageEnhance.Contrast(frame).enhance(1.12)
    return frame


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "assets"
    src_path = assets / "Designer.png"

    if not src_path.exists():
        raise FileNotFoundError(f"Missing source icon: {src_path}")

    full = Image.open(src_path).convert("RGBA")
    m_full = _mask_from_src(full)

    pb = _padded_bbox(m_full)
    if not pb:
        raise ValueError("Could not detect glyph in Designer.png")
    m_crop = m_full.crop(pb)
    src_crop = full.crop(pb)
    # Slight edge softening so the contour + RGB blend stay sharp but not jagged.
    m_crop = m_crop.filter(ImageFilter.GaussianBlur(radius=0.55))

    layered = _build_layered_rgba(src_crop, m_crop)
    # Fit inside 1024 with transparent margins (~6% on each short side at 0.88).
    target = max(1, int(1024 * FILL_FRACTION))
    fit = ImageOps.contain(
        layered, (target, target), method=Image.Resampling.LANCZOS
    )
    out = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    x = (1024 - fit.width) // 2
    y = (1024 - fit.height) // 2
    out.paste(fit, (x, y), fit)

    master_out = assets / "icon_master_1024.png"
    out.save(master_out)
    _render_frame(out, FAVICON_SIZE).save(assets / "icon.png")

    ico_path = assets / "icon.ico"
    frames = [_render_frame(out, s) for s in ICO_SIZES]
    frames[0].save(
        ico_path,
        format="ICO",
        sizes=[(f.width, f.height) for f in frames],
        append_images=frames[1:],
    )

    ico = Image.open(ico_path)
    reported = sorted(ico.info.get("sizes", []))
    print("Wrote:", master_out)
    print("Overwrote:", assets / "icon.png", f"({FAVICON_SIZE}×{FAVICON_SIZE})")
    print("Wrote:", ico_path)
    print("ICO sizes:", reported)


if __name__ == "__main__":
    main()
