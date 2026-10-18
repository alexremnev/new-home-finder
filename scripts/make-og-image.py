"""Letterbox the square logo onto a 1200x630 (1.91:1) canvas.

Facebook crops whatever it is given to 1.91:1 for the feed card. Given a
square, that crop takes the bottom off -- which on this mark is the
"HOME FINDER" line. Supplying 1.91:1 directly leaves nothing to crop.

The filler either side is not a flat bar: it continues the logo's own radial
vignette outward, sampled from the logo's background pixels by radius, so the
wider frame reads as one image rather than a square with wings.
"""
import numpy as np
from PIL import Image, ImageFilter

SRC = "/Users/alexey.remnev/Downloads/photo.jpg"
OUT = "og-1200x630.jpg"
W, H = 1200, 630
LOGO = 600                      # drawn height, leaving 15px top and bottom
FEATHER = 36                    # px of alpha ramp, well clear of the artwork

src = Image.open(SRC).convert("RGB")
a = np.asarray(src, dtype=np.float64)
sh, sw, _ = a.shape
cy, cx = (sh - 1) / 2, (sw - 1) / 2

# Background = teal: green and blue both clearly above red. That excludes the
# navy lettering (dark everywhere) and the orange roof (red dominant).
r, g, b = a[..., 0], a[..., 1], a[..., 2]
bg = (g > r + 12) & (b > r + 12)

yy, xx = np.mgrid[0:sh, 0:sw]
rad = np.hypot(yy - cy, xx - cx)

# Mean background colour per 1px radius ring, then smoothed. Rings that the
# artwork covers entirely have no samples; fill them by interpolation so the
# curve stays continuous.
nbin = int(rad.max()) + 1
idx = rad.astype(int)
curve = np.zeros((nbin, 3))
for c in range(3):
    tot = np.bincount(idx[bg], weights=a[..., c][bg], minlength=nbin)
    cnt = np.bincount(idx[bg], minlength=nbin)
    have = cnt > 0
    vals = np.divide(tot, np.maximum(cnt, 1))
    curve[:, c] = np.interp(np.arange(nbin), np.flatnonzero(have), vals[have])
k = 41
pad = np.pad(curve, ((k // 2, k // 2), (0, 0)), mode="edge")
curve = np.stack([np.convolve(pad[:, c], np.ones(k) / k, "valid") for c in range(3)], 1)

# Paint the canvas from that curve, in logo-pixel space so the falloff
# continues at the same rate past the logo's own edge.
scale = LOGO / sh
ox, oy = (W - LOGO) / 2, (H - LOGO) / 2
cyy, cxx = np.mgrid[0:H, 0:W]
lr = np.hypot((cyy - (oy + LOGO / 2)) / scale, (cxx - (ox + LOGO / 2)) / scale)
canvas = curve[np.clip(lr.astype(int), 0, nbin - 1)]

# The logo's texture is fine-grained noise; without it the filler looks flat
# and plasticky beside the real thing. Measure it as the high-frequency part
# only -- the residual against the radial curve also carries the vignette and
# the texture's own low-frequency mottling, which overstates the grain by
# about three times and gives a filler visibly noisier than the logo.
hi = a - np.asarray(src.filter(ImageFilter.GaussianBlur(4)), dtype=np.float64)
grain = float(np.std(hi[bg]))
rng = np.random.default_rng(7)
# 1.35x because the jpeg quantiser takes roughly a quarter of it back off;
# the factor is calibrated against the measured grain in the saved file.
canvas += rng.normal(0, grain * 1.35, canvas.shape)
out = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")

# Feather so the pasted square has no visible border where its vignette meets
# the painted one.
logo = src.resize((LOGO, LOGO), Image.LANCZOS)
mask = Image.new("L", (LOGO, LOGO), 255)
m = np.asarray(mask).copy()
ramp = (np.arange(FEATHER) / FEATHER * 255).astype(np.uint8)
m[:, :FEATHER] = np.minimum(m[:, :FEATHER], ramp)
m[:, -FEATHER:] = np.minimum(m[:, -FEATHER:], ramp[::-1])
m[:FEATHER, :] = np.minimum(m[:FEATHER, :], ramp[:, None])
m[-FEATHER:, :] = np.minimum(m[-FEATHER:, :], ramp[::-1, None])
mask = Image.fromarray(m, "L").filter(ImageFilter.GaussianBlur(6))

out.paste(logo, (int(ox), int(oy)), mask)
out.save(OUT, "JPEG", quality=92, optimize=True, progressive=True)
print("wrote", OUT, out.size, "grain", round(grain, 2))
