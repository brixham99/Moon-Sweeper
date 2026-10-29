"""Cut painted sprites out of their magenta background (assets/art/*.png)."""
import sys
import numpy as np
from PIL import Image, ImageFilter

def cut(src, dst, width):
    im = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    mag = np.minimum(r, b) - g                      # how magenta a pixel is
    a = np.clip(1 - (mag - 60) / 120, 0, 1)         # soft key
    a = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))).astype(np.float32) / 255
    # despill: pull magenta out of the edge pixels
    m = np.minimum(r, b)
    spill = np.clip(m - g, 0, None) * (1 - a)
    im[..., 0] -= spill; im[..., 2] -= spill
    rgba = np.dstack([np.clip(im, 0, 255), a * 255]).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")
    img = img.crop(img.getbbox())
    h = int(img.height * width / img.width)
    img.resize((width, h), Image.LANCZOS).save(dst)
    print(dst, width, h)

if __name__ == "__main__":
    cut(sys.argv[1], sys.argv[2], int(sys.argv[3]))
