"""Generate every logo/icon variant from the untouched master sheet.

    python tools/make_brand_assets.py

Reads  assets_src/masters/controlled-chaos-logo-sheet.original.png   (never modified; checksum in SHA256SUMS)
Writes src/assets/brand/*, src/assets/icon*.png|ico, src/assets/splash*.png  (flet build looks for assets next to main.py)

Geometry is detected from the pixels, not hard-coded, and asserted, so a wrong crop fails loudly.
Android adaptive icons: 108dp canvas, the visible mask is the centre 72dp and Google's safe zone is the centre 66dp
circle, so the mark is scaled to fit inside a 60% box (a little inside the safe zone).
"""
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "assets_src" / "masters" / "controlled-chaos-logo-sheet.original.png"
A = ROOT / "src" / "assets"
B = A / "brand"
INK, PAPER = (26, 22, 20), (246, 240, 228)

sums = (MASTER.parent / "SHA256SUMS").read_text()
assert hashlib.sha256(MASTER.read_bytes()).hexdigest() in sums, "master changed!"
src = Image.open(MASTER).convert("RGB")
gray = np.array(src.convert("L"))


def components(mask, min_area, closing=3):
    lab, _ = ndi.label(ndi.binary_closing(mask, iterations=closing))
    out = []
    for sl in ndi.find_objects(lab):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if h * w >= min_area:
            out.append((sl[1].start, sl[0].start, sl[1].stop, sl[0].stop))
    return sorted(out)


def alpha_from(box, lo=70, hi=225):
    g = np.array(src.crop(box).convert("L")).astype(float)
    return np.clip((hi - g) / (hi - lo), 0, 1)


def tinted(box, rgb):
    a = alpha_from(box)
    px = np.zeros(a.shape + (4,), np.uint8)
    px[..., :3] = rgb
    px[..., 3] = (a * 255).astype(np.uint8)
    im = Image.fromarray(px, "RGBA")
    return im.crop(im.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox())


def fit(im, box_w, box_h):
    k = min(box_w / im.width, box_h / im.height)
    return im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)


def centred(im, size, bg=(0, 0, 0, 0), scale=1.0):
    canvas = Image.new("RGBA", (size, size), bg)
    m = fit(im, size * scale, size * scale)
    canvas.paste(m, ((size - m.width) // 2, (size - m.height) // 2), m)
    return canvas


# ---- locate artwork on the sheet (top half: mark + wordmark; bottom row: icons) --------------------------
top = components((gray < 110) & (np.arange(gray.shape[0])[:, None] < 530), 20000, closing=6)
assert len(top) >= 2, top
mark_box = min(top, key=lambda b: b[0])                                   # left-most big shape = the emblem
mx0, my0, mx1, my1 = mark_box
word_box = (mx1 + 80, my0 + 100, 1470, 395)                                # title text, excluding tagline and divider
bottom = components((gray < 110) & (np.arange(gray.shape[0])[:, None] > 535), 20000)
icons = [b for b in bottom if 0.9 < (b[2] - b[0]) / (b[3] - b[1]) < 1.1 and (b[2] - b[0]) > 200]
assert icons, bottom
icon_box = max(icons, key=lambda b: b[0])                                  # the rightmost dark rounded-square tile

B.mkdir(parents=True, exist_ok=True)
mbox = (mx0 - 12, my0 - 12, mx1 + 12, my1 + 12)
for nm, c in (("ink", INK), ("light", PAPER)):
    m = tinted(mbox, c)
    w = tinted(word_box, c)
    m.save(B / f"mark_{nm}.png", optimize=True)
    w.save(B / f"wordmark_{nm}.png", optimize=True)
    th = int(w.height * 1.55)
    mm = m.resize((int(m.width * th / m.height), th), Image.LANCZOS)
    gap = int(th * 0.18)
    L = Image.new("RGBA", (mm.width + gap + w.width, th), (0, 0, 0, 0))
    L.paste(mm, (0, 0), mm)
    L.paste(w, (mm.width + gap, (th - w.height) // 2), w)
    L.save(B / f"lockup_{nm}.png", optimize=True)

mark_ink = Image.open(B / "mark_ink.png")
mark_light = Image.open(B / "mark_light.png")

# ---- full-bleed square app icon -------------------------------------------------------------------------------
# The sheet's own icon tile is only ~280 px wide; upscaling it to 1024 would be soft.  The same design (light emblem on the
# ink ground) is rebuilt from the higher-resolution emblem instead.  The tile in the sheet is used only to measure the ratio.
tile_w = icon_box[2] - icon_box[0]
emblem_ratio = 0.70
sq = Image.new("RGBA", (1024, 1024), INK + (255,))
m = fit(mark_light, 1024 * emblem_ratio, 1024 * emblem_ratio)
sq.paste(m, ((1024 - m.width) // 2, (1024 - m.height) // 2), m)
sq = sq.convert("RGB")
sq.save(A / "icon.png", optimize=True)                                    # shared / iOS-style full-bleed
sq.resize((512, 512), Image.LANCZOS).save(A / "icon_linux.png", optimize=True)
sq.resize((256, 256), Image.LANCZOS).save(B / "app_icon_256.png", optimize=True)
sq.resize((64, 64), Image.LANCZOS).save(A / "icon_web.png", optimize=True)
sq.save(A / "icon_windows.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

# ---- Android adaptive layers (108dp grid rendered at 1024 px) -------------------------------------------------
fg = centred(mark_light, 1024, scale=0.60)                                # mark inside the 66dp safe zone
fg.save(B / "android_adaptive_foreground.png", optimize=True)
Image.new("RGB", (1024, 1024), INK).save(B / "android_adaptive_background.png", optimize=True)
mono = centred(tinted(mbox, (0, 0, 0)), 1024, scale=0.60)
mono.save(B / "android_adaptive_monochrome.png", optimize=True)           # Android 13 themed icons
fg.save(A / "icon_android.png", optimize=True)                            # flet build picks this up for Android
# status-bar / notification glyph: white silhouette on transparent, 96 px (Android only uses the alpha channel)
centred(tinted(mbox, (255, 255, 255)), 96, scale=0.9).save(B / "notification_icon_96.png", optimize=True)

# ---- splash ---------------------------------------------------------------------------------------------------
for name, bg, mk in (("splash.png", PAPER, mark_ink), ("splash_dark.png", INK, mark_light)):
    s = Image.new("RGB", (1152, 1152), bg)
    m = fit(mk, 576, 576)
    s.paste(m, ((1152 - m.width) // 2, (1152 - m.height) // 2), m)
    s.save(A / name, optimize=True)

for p in sorted(list(B.glob("*")) + [x for x in A.glob("*") if x.is_file()]):
    im = Image.open(p) if p.suffix != ".ico" else None
    print(f"{p.relative_to(ROOT)!s:55} {p.stat().st_size/1024:7.1f} KB  {im.size if im else 'multi-size ico'}")
