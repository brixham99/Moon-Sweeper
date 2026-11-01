"""Cut the level 4-6 painted aliens out of their magenta backgrounds (assets/art_src/*.jpg -> assets/art/*.png).

    python3 tools/cut_aliens.py

The key colour is measured from the border of each painting (the generator's magenta
varies). Alpha comes from the colour distance to it; edge and see-through pixels
(the beetle's wings) are un-mixed from the background, and anything still pink or
purple afterwards is pulled back to warm amber/grey, so no magenta fringe survives.
"""
import os
import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = {"worm": 60, "leech": 56, "beetle": 100}     # name -> sprite width in px


def cut(name, width):
    src = os.path.join(ROOT, "assets", "art_src", name + ".jpg")
    im = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    border = np.concatenate([im[:8].reshape(-1, 3), im[-8:].reshape(-1, 3), im[:, :8].reshape(-1, 3), im[:, -8:].reshape(-1, 3)])
    bg = np.median(border, 0)
    d = np.sqrt(((im - bg) ** 2).sum(-1))
    a = np.clip((d - 38) / 90, 0, 1)
    a = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))
                   .filter(ImageFilter.GaussianBlur(0.6))).astype(np.float32) / 255
    # un-mix: pixel = a * colour + (1 - a) * bg
    k = np.maximum(a, 0.15)[..., None]
    col = np.clip((im - (1 - k) * bg) / k, 0, 255)
    col = np.where(a[..., None] > 0.98, im, col)
    # anything left pinkish/purple (red and blue both well above green): warm it towards amber
    r, g, b = col[..., 0], col[..., 1], col[..., 2]
    pink = np.clip(np.minimum(r, b) - g - 10, 0, None)
    col[..., 2] = b - pink * 0.9
    col[..., 1] = g + pink * 0.35
    a[a < 0.04] = 0
    rgba = np.dstack([np.clip(col, 0, 255), a * 255]).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")
    img = img.crop(img.getbbox())
    h = round(img.height * width / img.width)
    out = os.path.join(ROOT, "assets", "art", name + ".png")
    img.resize((width, h), Image.LANCZOS).save(out)
    print(out, width, h, "key", bg.astype(int))


if __name__ == "__main__":
    for n, w in JOBS.items():
        cut(n, w)
