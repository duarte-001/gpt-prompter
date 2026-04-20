from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "assets"
    src_png = assets / "Designer.png"

    if not src_png.exists():
        raise FileNotFoundError(f"Missing source icon: {src_png}")

    # Designer.png already has the desired glyph and background; we just need
    # correct safe-area padding and proper ICO sizes for Windows.
    base = Image.open(src_png).convert("RGBA")
    # Trim transparent borders if any (usually none), then contain.
    bbox = base.getbbox()
    if bbox:
        base = base.crop(bbox)

    # Build a clean brand background (squircle + subtle radial gradient).
    size = 1024
    bg = Image.new("RGBA", (size, size), (15, 23, 42, 255))  # #0F172A
    # Cheap radial gradient: draw on small canvas then upscale.
    gsz = 256
    grad = Image.new("RGBA", (gsz, gsz), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(gsz // 2, 0, -1):
        # center highlight toward #111C33
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

    # Place the Designer mark with generous padding (Spotify-like weight).
    art_target = int(size * 0.62)
    art = ImageOps.contain(base, (art_target, art_target), method=Image.Resampling.LANCZOS)
    x = (size - art.width) // 2
    y = (size - art.height) // 2
    out = bg_masked.copy()
    out.alpha_composite(art, (x, y))

    master_out = assets / "icon_master_1024.png"
    out.save(master_out)

    # Streamlit favicon: keep it small to avoid the default Streamlit logo flash.
    # 128x128 loads quickly but still looks sharp in the tab.
    out.resize((128, 128), Image.Resampling.LANCZOS).save(assets / "icon.png")

    # Windows exe/shortcut/taskbar: multi-resolution ICO.
    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    ico_path = assets / "icon.ico"
    # Pillow's automatic downscale can look soft at 16–32px, so we pre-render and sharpen each size.
    frames: list[Image.Image] = []
    for w, h in ico_sizes:
        im_sz = out.resize((w, h), Image.Resampling.LANCZOS)
        if w <= 64:
            im_sz = im_sz.filter(ImageFilter.UnsharpMask(radius=1.2, percent=190, threshold=2))
        if w <= 32:
            im_sz = ImageEnhance.Contrast(im_sz).enhance(1.08)
        frames.append(im_sz)
    frames[0].save(ico_path, format="ICO", append_images=frames[1:])

    # Verify frames/sizes (best-effort; Pillow reports size per frame).
    ico = Image.open(ico_path)
    sizes = ico.info.get("sizes", [])
    print("Wrote:", master_out)
    print("Overwrote:", assets / "icon.png")
    print("Wrote:", ico_path)
    print("ICO sizes:", sizes)


if __name__ == "__main__":
    main()

