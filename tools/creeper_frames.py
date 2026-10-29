"""Rock Creeper scuttle cycle: 8 frames built from the painted sprite (assets/art/creeper.png).

Each painted leg is cut out as its own piece (the thin parts of the alpha mask, split into
connected pieces) and swung about its root: a planted stance sweep backwards, then a quick
lifted swing forwards. Alternate legs are half a cycle apart (a woodlouse-like
ripple); the feelers by the head twitch a little. The body itself never changes, so the
frames match exactly apart from the legs (no flicker). The work is done at 4x and filtered
back down. Output: assets/art/creeper_walk.png, 8 frames side by side, each with a 6 px margin.
"""
import math
import os
import sys
import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "assets", "art", "creeper.png")
OUT = os.path.join(HERE, "assets", "art", "creeper_walk.png")
N = 8
S = 4
PAD = 6                      # transparent margin round each frame (aliens.CREEPER_PAD)


def gait(p):
    """Foot position along the stride (-1 back .. +1 forward) and lift (0..1) at phase p."""
    p %= 1.0
    if p < 0.6:                                   # stance: planted, sweeps back as the body moves on
        return 1 - 2 * p / 0.6, 0.0
    q = (p - 0.6) / 0.4                           # swing: up, forward, down
    return -1 + 2 * (0.5 - 0.5 * math.cos(math.pi * q)), math.sin(math.pi * q)


def build(amp_deg=17.0, lift_px=2.2, debug=False):
    im0 = Image.open(SRC).convert("RGBA")
    im = Image.new("RGBA", (im0.width + 2 * PAD, im0.height + 2 * PAD), (0, 0, 0, 0))
    im.paste(im0, (PAD, PAD))                    # room for the legs to swing out
    w, h = im.size
    src = np.asarray(im).astype(np.float32)
    alpha = src[..., 3]
    body = ndimage.binary_opening(alpha > 128, structure=np.ones((7, 7)))
    body = ndimage.binary_fill_holes(ndimage.binary_closing(body, structure=np.ones((3, 3))))
    hard = (alpha > 128) & ~body
    lab, n = ndimage.label(hard)
    soft = (alpha > 8) & ~body
    _, (iy, ix) = ndimage.distance_transform_edt(lab == 0, return_indices=True)
    lab_soft = np.where(soft, lab[iy, ix], 0)                 # anti-aliased edge pixels join their leg
    dist_body = ndimage.distance_transform_edt(~body)
    legs = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if len(xs) < 15 or ys.max() < PAD + 12:               # specks and the hump's top edge
            continue
        dd = dist_body[ys, xs]
        r = np.argmin(dd)
        legs.append(dict(id=i, root=(xs[r] + 0.5, ys[r] - 0.5), n=len(xs), tipd=dd.max()))
    legs.sort(key=lambda L: L["root"][0])
    main = [L for L in legs if L["n"] >= 50]
    for k, L in enumerate(main):
        L["phase"], L["amp"] = (0.0 if k % 2 == 0 else 0.5), 1.0
    for L in legs:
        if L not in main:                                     # feelers and mouthparts
            L["phase"], L["amp"] = 0.25 + 0.3 * (L["root"][1] / h), 0.35
    # premultiplied 4x layers
    def up(a):
        return np.asarray(Image.fromarray(a.astype(np.uint8), "RGBA").resize((w * S, h * S), Image.BICUBIC)).astype(np.float32)
    pm = src.copy(); pm[..., :3] *= pm[..., 3:4] / 255.0
    body_rgba = np.where((lab_soft == 0)[..., None], pm, 0)
    def to_pm(rgba_pm):
        x = rgba_pm.copy()
        a = np.clip(x[..., 3:4], 1e-3, 255)
        x[..., :3] = np.where(x[..., 3:4] > 0, x[..., :3] * 255.0 / a, 0)
        return x
    body_up = up(to_pm(body_rgba)); body_up[..., :3] *= body_up[..., 3:4] / 255.0
    layers = []
    for L in legs:
        lay = np.where((lab_soft == L["id"])[..., None], pm, 0)
        u = up(to_pm(lay)); u[..., :3] *= u[..., 3:4] / 255.0
        layers.append(u)
    yy, xx = np.mgrid[0:h * S, 0:w * S].astype(np.float32)
    frames = []
    for f in range(N):
        acc = np.zeros_like(body_up)
        for L, lay in zip(legs, layers):
            fwd, lift = gait(f / N + L["phase"])
            th = -math.radians(amp_deg) * fwd * L["amp"]        # negative = tip towards the head (screen right)
            rx, ry = L["root"][0] * S, L["root"][1] * S
            ly = lift * lift_px * S * L["amp"]
            c, s = math.cos(th), math.sin(th)
            # inverse map: undo the lift, then rotate back about the root
            X, Y = xx - rx, yy - ry + ly
            sx = c * X + s * Y + rx
            sy = -s * X + c * Y + ry
            moved = np.stack([ndimage.map_coordinates(lay[..., ch], [sy, sx], order=1, cval=0) for ch in range(4)], -1)
            acc = moved + acc * (1 - moved[..., 3:4] / 255.0)
        out = body_up + acc * (1 - body_up[..., 3:4] / 255.0)      # body over the leg roots
        a = np.clip(out[..., 3:4], 1e-3, 255)
        rgb = np.where(out[..., 3:4] > 0.5, out[..., :3] * 255.0 / a, 0)
        img = np.concatenate([np.clip(rgb, 0, 255), np.clip(out[..., 3:4], 0, 255)], -1).astype(np.uint8)
        frames.append(Image.fromarray(img, "RGBA").resize((w, h), Image.LANCZOS))
    sheet = Image.new("RGBA", (w * N, h))
    for i, fr in enumerate(frames):
        sheet.paste(fr, (i * w, 0))
    sheet.save(OUT)
    if debug:
        print([(L["root"], L["n"], L["phase"], L["amp"]) for L in legs])
    return sheet


if __name__ == "__main__":
    s = build(debug="--debug" in sys.argv)
    print("saved", OUT, s.size)
