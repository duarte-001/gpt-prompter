from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

# Every size the Windows shell may request across common DPI scales (100 %–250 %).
# Largest first — first frame is the shell's preferred pick on modern Windows.
ICO_SIZES = (256, 192, 128, 96, 72, 64, 48, 40, 32, 24, 20, 16)

# Streamlit / browser favicon — 256 px keeps tabs sharp on 2× displays
# while still being small enough to load fast.
FAVICON_SIZE = 256


def _render_frame(master: Image.Image, side: int) -> Image.Image:
    """Downscale *master* to *side*×*side*, sharpening small frames."""
    frame = master.resize((side, side), Image.Resampling.LANCZOS)
    if side <= 72:
        frame = frame.filter(
            ImageFilter.UnsharpMask(radius=1.0, percent=200, threshold=2)
        )
    if side <= 32:
        frame = ImageEnhance.Contrast(frame).enhance(1.10)
    return frame


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "assets"
    src_png = assets / "Designer.png"

    if not src_png.exists():
        raise FileNotFoundError(f"Missing source icon: {src_png}")

    base = Image.open(src_png).convert("RGBA")
    bbox = base.getbbox()
    if bbox:
        base = base.crop(bbox)

    # Build a clean brand background (squircle + subtle radial gradient).
    size = 1024
    bg = Image.new("RGBA", (size, size), (15, 23, 42, 255))  # #0F172A
    gsz = 256
    grad = Image.new("RGBA", (gsz, gsz), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(gsz // 2, 0, -1):
        t = i / (gsz / 2)
        col = (
            int(17 + (15 - 17) * t),
            int(28 + (23 - 28) * t),
            int(51 + (42 - 51) * t),
            int(60 * (1 - t)),
        )
        gd.ellipse([gsz / 2 - i, gsz / 2 - i, gsz / 2 + i, gsz / 2 + i], fill=col)
    grad = grad.resize((size, size), Image.Resampling.LANCZOS)
    bg.alpha_composite(grad)

    # Squircle mask
    mask_draw = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask_draw)
    radius = int(size * 0.22)
    d.rounded_rectangle([0, 0, size, size], radius=radius, fill=255)
    mask = mask_draw.filter(ImageFilter.GaussianBlur(radius=1.2))

    bg_masked = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_masked.paste(bg, (0, 0), mask)

    art_target = int(size * 0.62)
    art = ImageOps.contain(base, (art_target, art_target), method=Image.Resampling.LANCZOS)
    x = (size - art.width) // 2
    y = (size - art.height) // 2
    out = bg_masked.copy()
    out.alpha_composite(art, (x, y))

    # --- Master 1024 ---
    master_out = assets / "icon_master_1024.png"
    out.save(master_out)

    # --- Streamlit / browser favicon (256 px) ---
    favicon = _render_frame(out, FAVICON_SIZE)
    favicon.save(assets / "icon.png")

    # --- Windows multi-resolution ICO ---
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

