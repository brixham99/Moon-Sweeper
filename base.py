"""The Alien Missile Base, after the launch site in Ultimate's Lunar Jetman (1983).

A grown, not built, launch site: a lumpy carapace pad laced with glowing
veins and ringed by bone tusks; on its hub a seed-pod turret that slowly
turns, with a stalked eye and two claws cradling acid-green crystal missiles
raised at 48 degrees. Six bone spires topped with glowing orbs project a
shimmering force-field dome over the whole site. Built as a 3D polygon model with the same strict
2:1 isometric projection, light and shading as the rover (see rover.py).

Units: x, y in tiles; z in screen pixels (32 px = 1 tile).
"""
import math
import numpy as np
import pygame

import rover as rv
from rover import ZT, facing

FRAMES = 16                           # turret rotation frames (full turn)

# alien palette: dark carapace and pale bone, lit by acid-green veins and a magenta eye
SHELL = (64, 82, 84)
SHELL_D = (40, 54, 58)
SHELL_L = (92, 112, 110)
BONE = (186, 188, 160)
BONE_D = (128, 132, 108)
VEIN = (150, 255, 110)
VEIN_D = (60, 170, 70)
ACID = (220, 255, 80)
MAG = (232, 70, 180)
MAG_D = (140, 30, 110)
ORB = (150, 255, 220)
ORB_D = (50, 190, 170)
DARK = (26, 34, 36)

PAD_R, PAD_Z = 2.7, 18.0
PLINTH_R, PLINTH_Z = 1.35, 32.0
TURRET_R, TURRET_Z = 0.78, 62.0
PYLON_R, PYLON_H = 3.75, 92.0
PYLONS = [(PYLON_R * math.cos(math.tau * (i + 0.5) / 6), PYLON_R * math.sin(math.tau * (i + 0.5) / 6)) for i in range(6)]
HALF = 4.15                           # square footprint half-size (tiles) for depth sorting
FIELD_R, FIELD_H = 4.05, 150.0        # force-field dome: radius (tiles) and height (px)


def _ngon(cx, cy, r, n, rot=0.0):
    return [(cx + r * math.cos(rot + i * math.tau / n), cy + r * math.sin(rot + i * math.tau / n)) for i in range(n)]


def lathe(m, rings, n, col, spec=0.3, bias=0.0, rot=0.0, cols=None, top=None, wob=None, tbias=None):
    """Surface of revolution. rings: [(cx, cy, r, z)] bottom to top (r tiles, z px); the centre may wander
    so stalks can bend. cols(i, k) picks a colour per side i and band k; wob(i, k) scales the radius."""
    R = []
    for k, (cx, cy, r, z) in enumerate(rings):
        R.append([(cx + r * (wob(i, k) if wob else 1) * math.cos(rot + i * math.tau / n),
                   cy + r * (wob(i, k) if wob else 1) * math.sin(rot + i * math.tau / n), z) for i in range(n)])
    for k in range(len(rings) - 1):
        (ax, ay, r0, z0), (bx, by, r1, z1) = rings[k], rings[k + 1]
        dz, dr = (z1 - z0) / ZT, r1 - r0
        for i in range(n):
            q = [R[k][i], R[k][(i + 1) % n], R[k + 1][(i + 1) % n], R[k + 1][i]]
            if r0 < 1e-3: q = [q[0], q[2], q[3]]
            elif r1 < 1e-3: q = q[:3]
            a = rot + (i + 0.5) * math.tau / n
            out = (math.cos(a) * dz, math.sin(a) * dz, -dr)
            c = cols(i, k) if cols else col
            m.face(facing(q, out), c, spec, bias)
    if rings[-1][2] > 1e-3:
        m.face(facing(R[-1], (0, 0, 1)), top or col, spec, bias if tbias is None else tbias)


def solid(m, pts8, col, spec=0.3, bias=0.0, cols=None):
    """A hexahedron from 8 model points (0-3 bottom ring, 4-7 top ring)."""
    P = np.array([rv.M(p) for p in pts8]); c = P.mean(0)
    for j, (a, b, cc, d) in enumerate(((4, 5, 6, 7), (0, 3, 2, 1), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7))):
        q = [pts8[a], pts8[b], pts8[cc], pts8[d]]
        fc = np.mean([rv.M(p) for p in q], axis=0)
        m.face(facing(q, fc - c), (cols[j] if cols else col), spec, bias)


def _frame(th, el):
    ca, sa, ce, se = math.cos(th), math.sin(th), math.cos(el), math.sin(el)
    fwd = np.array([ca * ce, sa * ce, se]); side = np.array([-sa, ca, 0.0]); up = np.cross(side, fwd)
    return fwd, side, up


def crystal(m, P0, fwd, side, up, length, r, col, col_d, tip, n=6, bias=0.4):
    """An elongated hexagonal crystal (the alien missile) along fwd from P0 (tiles, z in tiles)."""
    def mp(p): return (p[0], p[1], p[2] * ZT)
    secs = [(-0.15, 0.01), (0.1, r), (length * 0.7, r * 0.95), (length, 0.01)]
    for (ua, ra), (ub, rb) in zip(secs, secs[1:]):
        for i in range(n):
            a0, a1 = i * math.tau / n, (i + 1) * math.tau / n
            A0 = P0 + fwd * ua + (side * math.cos(a0) + up * math.sin(a0)) * ra
            A1 = P0 + fwd * ua + (side * math.cos(a1) + up * math.sin(a1)) * ra
            B1 = P0 + fwd * ub + (side * math.cos(a1) + up * math.sin(a1)) * rb
            B0 = P0 + fwd * ub + (side * math.cos(a0) + up * math.sin(a0)) * rb
            q = [mp(A0), mp(A1), mp(B1), mp(B0)]
            if ra < 0.02: q = [q[0], q[2], q[3]]
            elif rb < 0.02: q = q[:3]
            am = (a0 + a1) / 2
            out = side * math.cos(am) + up * math.sin(am) + fwd * (0.3 if ub > length * 0.8 else -0.3 if ua < 0 else 0)
            c = tip if ua >= length * 0.7 else (col if i % 2 else col_d)
            m.face(facing(q, out), c, 0.9, bias)


def pylon(m, px, py, seed=0):
    """A grown field spire: a ribbed root bulb, a bent bone stalk leaning in, thorns and a glowing orb."""
    rnd = np.random.default_rng(seed)
    d = math.hypot(px, py); ix, iy = -px / d, -py / d          # leans towards the pad
    lean = 0.28 + rnd.uniform(0, 0.12)
    lathe(m, [(px, py, 0.46, 0), (px, py, 0.5, 4), (px, py, 0.36, 12), (px, py, 0.18, 18)], 7, SHELL, 0.3,
          rot=seed, cols=lambda i, k: SHELL_L if i % 2 else SHELL_D)
    rings = []
    for k in range(7):
        t = k / 6
        bend = lean * t * t
        r = 0.17 * (1 - 0.55 * t) * (1.25 if k in (2, 4) else 1.0)
        rings.append((px + ix * bend, py + iy * bend, r, 16 + t * (PYLON_H - 30)))
    lathe(m, rings, 6, BONE, 0.35, rot=seed * 0.7, cols=lambda i, k: BONE if (i + k) % 2 else BONE_D)
    tx, ty = px + ix * lean, py + iy * lean
    ztop = PYLON_H - 14
    # three curling thorns cradling the orb
    for j in range(3):
        a = seed + j * math.tau / 3
        ox, oy = math.cos(a), math.sin(a)
        b = (tx + ox * 0.05, ty + oy * 0.05, ztop - 6)
        tip = (tx + ox * 0.3, ty + oy * 0.3, ztop + 14)
        w = 0.05
        q1 = [(b[0] - oy * w, b[1] + ox * w, b[2]), (b[0] + oy * w, b[1] - ox * w, b[2]), tip]
        m.face(facing(q1, (ox, oy, 0.2)), BONE, 0.4, 0.2)
        m.face(facing(q1, (-ox, -oy, -0.2)), BONE_D, 0.4, 0.2)
    # the orb: a faceted sphere glowing field green
    zc = ztop + 8; rr = 0.24
    sph = [(tx, ty, rr * math.sin(a), zc + rr * ZT * -math.cos(a)) for a in np.linspace(0, math.pi, 6)]
    lathe(m, [(x, y, max(0.0, r), z) for x, y, r, z in sph], 8, ORB, 0.9, bias=0.3,
          cols=lambda i, k: ORB if k >= 2 else ORB_D)


def build(frame=0, parts=("pad", "turret", "pylons")):
    m = rv.Model(); m.sy = 1.0
    th = frame * math.tau / FRAMES
    if "pad" in parts:
        # a low grown carapace: a lumpy many-lobed disc rather than a machined octagon
        lob = lambda i, k: 1.0 + 0.035 * math.sin(i * 7 * math.tau / 28 + 0.6) + 0.02 * math.sin(i * 3 * math.tau / 28)
        lathe(m, [(0, 0, PAD_R + 0.12, 0), (0, 0, PAD_R, PAD_Z * 0.6), (0, 0, PAD_R - 0.2, PAD_Z)], 28, SHELL, 0.25,
              cols=lambda i, k: (SHELL_D if i % 3 == 0 else SHELL) if k == 0 else SHELL_L, top=SHELL, wob=lob, tbias=-9.0)
        # glowing veins: curved spokes that branch towards the rim, uneven like roots
        for j in range(7):
            a0 = j * math.tau / 7 + (0.25 if j % 2 else -0.1)
            curl = 0.5 if j % 3 else -0.35
            pts = []
            for s in np.linspace(0.0, 1.0, 9):
                r = PLINTH_R + 0.05 + s * (PAD_R - 0.45 - PLINTH_R)
                a = a0 + curl * s * s
                w = 0.07 * (1 - 0.6 * s) + 0.02
                pts.append((r, a, w))
            for (r0, a0_, w0), (r1, a1_, w1) in zip(pts, pts[1:]):
                p = lambda r, a, o: (r * math.cos(a) - math.sin(a) * o, r * math.sin(a) + math.cos(a) * o, PAD_Z + 0.5)
                q = [p(r0, a0_, -w0), p(r1, a1_, -w1), p(r1, a1_, w1), p(r0, a0_, w0)]
                m.face(facing(q, (0, 0, 1)), VEIN, 0.4, -3.5)
            # a node of light where each vein ends
            r, a, _ = pts[-1]
            ex, ey = r * math.cos(a), r * math.sin(a)
            lathe(m, [(ex, ey, 0.13, PAD_Z), (ex, ey, 0.1, PAD_Z + 3), (ex, ey, 0.0, PAD_Z + 5)], 6, ACID, 0.9, bias=0.4)
        # five curved ribs (tusks) rising round the rim at uneven spacing
        for j, a in enumerate((0.3, 1.45, 2.5, 3.9, 5.1)):
            ca, sa = math.cos(a), math.sin(a)
            prev = None
            for s in np.linspace(0, 1, 6):
                r = PAD_R + 0.35 - s * 0.75
                z = 2 + s * (24 + 6 * (j % 2))
                w = 0.2 * (1 - s) + 0.03
                ring = [(ca * r - sa * -w, sa * r + ca * -w), (ca * r - sa * w, sa * r + ca * w)]
                cur = [(x, y, z - 3 * (1 - s)) for x, y in ring] + [(ca * (r + 0.12 * (1 - s)), sa * (r + 0.12 * (1 - s)), z + 3)]
                if prev:
                    for u, v in ((0, 2), (2, 1), (1, 0)):
                        q = [prev[u], prev[v], cur[v], cur[u]]
                        mid = np.mean(q, axis=0)
                        out = (mid[0] - ca * (r - 0.3), mid[1] - sa * (r - 0.3), (mid[2] - z) / ZT + 0.2)
                        m.face(facing(q, out), BONE if u != 1 else BONE_D, 0.4, 0.1)
                prev = cur
        # raised organic hub: a bulb with a dark socket for the launcher
        lathe(m, [(0, 0, PLINTH_R, PAD_Z), (0, 0, PLINTH_R + 0.1, PAD_Z + 6), (0, 0, PLINTH_R - 0.1, PLINTH_Z - 3),
                  (0, 0, 1.0, PLINTH_Z)], 14, SHELL_L, 0.35, top=DARK,
              cols=lambda i, k: MAG_D if (k == 1 and i % 2) else (SHELL_L if i % 2 else SHELL))
    if "turret" in parts:
        n0 = len(m.faces)
        # the turning pod: an onion-shaped seed head with magenta gills
        lathe(m, [(0, 0, TURRET_R * 0.7, PLINTH_Z), (0, 0, TURRET_R, PLINTH_Z + 10), (0, 0, TURRET_R * 0.9, TURRET_Z - 10),
                  (0, 0, TURRET_R * 0.5, TURRET_Z), (0, 0, 0.12, TURRET_Z + 4)], 10, SHELL, 0.5, rot=th, bias=0.2,
              cols=lambda i, k: (MAG if i % 2 else SHELL_D) if k == 1 else (SHELL_L if i % 2 else SHELL), top=VEIN)
        ca, sa = math.cos(th), math.sin(th)
        # a stalked eye at the back, watching the sky
        sx, sy = -ca * 0.5, -sa * 0.5
        lathe(m, [(sx, sy, 0.08, TURRET_Z - 4), (sx - ca * 0.1, sy - sa * 0.1, 0.06, TURRET_Z + 16),
                  (sx - ca * 0.05, sy - sa * 0.05, 0.05, TURRET_Z + 26)], 5, BONE_D, 0.3, bias=0.5)
        ez = TURRET_Z + 34; ex, ey = sx - ca * 0.05, sy - sa * 0.05
        sph = [(ex, ey, 0.2 * math.sin(a), ez - 0.2 * ZT * math.cos(a)) for a in np.linspace(0, math.pi, 6)]
        lathe(m, sph, 8, MAG, 0.9, bias=0.6, rot=th,
              cols=lambda i, k: DARK if (k in (2, 3) and i in (0, 7)) else (MAG if k >= 2 else MAG_D))
        # launcher: a pair of curved claws cradling two acid crystals pitched up
        el = math.radians(48)
        fwd, side, up = _frame(th, el)
        piv = np.array([ca * 0.15, sa * 0.15, (TURRET_Z + 14) / ZT])
        for s in (-1, 1):
            prev = None
            for t in np.linspace(0, 1, 5):
                p = piv + side * s * (0.42 - 0.12 * math.sin(t * math.pi)) + fwd * (t * 1.1 - 0.4) - up * (0.25 * math.sin(t * math.pi))
                w = 0.08 * (1 - 0.6 * t) + 0.02
                cur = [p + up * w, p - up * w * 0.5 + side * s * w, p - up * w * 0.5 - side * s * w]
                if prev is not None:
                    for u, v in ((0, 1), (1, 2), (2, 0)):
                        q = [prev[u], prev[v], cur[v], cur[u]]
                        mid = np.mean(q, axis=0)
                        out = mid - (p - fwd * 0.1)
                        m.face(facing([(x, y, z * ZT) for x, y, z in q], (out[0], out[1], out[2])), BONE if u == 0 else BONE_D, 0.4, 0.45)
                prev = cur
        for off in (-0.2, 0.2):
            crystal(m, piv + side * off + up * 0.05, fwd, side, up, 1.7, 0.2, VEIN, VEIN_D, ACID)
        m.faces[n0:] = [(p, c, sp, b + 2.0) for p, c, sp, b in m.faces[n0:]]     # all above the pod top
    if "pylons" in parts:
        for i, (px, py) in enumerate(PYLONS):
            pylon(m, px, py, i)
    return m


def frame_bytes(key):
    i = int(key.split(":")[1])
    surf, ax, ay = rv.render_model(build(i))
    return (key, pygame.image.tobytes(surf, "RGBA"), surf.get_size(), ax, ay)


def all_keys():
    return [f"base:{i}" for i in range(FRAMES)]


def _hull(pts):
    P = sorted(set(map(tuple, np.round(pts, 1))))
    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    for p in reversed(P):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0: hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def shadow_sprite():
    """Ground shadow cast west by the eastern sun: one hull per part, so pylons cast their own."""
    groups = [build(0, ("pad", "turret"))] + []
    hulls = []
    def proj(m):
        out = []
        for f in m.faces:
            for p in f[0]:
                z = p[2] / ZT
                out.append(rv.iso((p[0] - rv.LIGHT[0] / rv.LIGHT[2] * z * 0.55, p[1] - rv.LIGHT[1] / rv.LIGHT[2] * z * 0.55, 0.0)))
        return np.array(out)
    hulls.append(_hull(proj(groups[0])))
    for px, py in PYLONS:
        m = rv.Model(); m.sy = 1.0; pylon(m, px, py, PYLONS.index((px, py)))
        hulls.append(_hull(proj(m)))
    allp = np.array([p for h in hulls for p in h])
    x0, y0 = allp.min(0) - 12; x1, y1 = allp.max(0) + 12
    W, H = int(x1 - x0), int(y1 - y0)
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    for h in hulls:
        pygame.draw.polygon(s, (0, 0, 0, 120), [(x - x0, y - y0) for x, y in h])
    s = pygame.transform.smoothscale(pygame.transform.smoothscale(s, (max(1, W // 6), max(1, H // 6))), (W, H))
    return s, -x0, -y0


# ------------------------------------------------------------ force field
FIELD_FRAMES = 24                     # one shimmer cycle (~2 s)
FIELD_COL = np.array([70.0, 255.0, 190.0])


def field_e(bx, by, x, y, z, rad=0.0, zpad=0.0):
    """< 1 inside the dome (optionally inflated by rad tiles and zpad px), > 1 outside."""
    return ((x - bx) ** 2 + (y - by) ** 2) / (FIELD_R + rad) ** 2 + (max(0.0, z) / (FIELD_H + zpad)) ** 2


def _hexline(u, v, size):
    """0 at the centre of a hex cell, 1 on its edges (u, v in tiles)."""
    q = (u * 2 / 3) / size
    r = (-u / 3 + v / math.sqrt(3)) / size
    # cube round
    x, z = q, r; y = -x - z
    rx, ry, rz = np.round(x), np.round(y), np.round(z)
    dx, dy, dz = abs(rx - x), abs(ry - y), abs(rz - z)
    m1 = (dx > dy) & (dx > dz); m2 = (~m1) & (dy > dz)
    rx = np.where(m1, -ry - rz, rx); ry = np.where(m2, -rx - rz, ry); rz = -rx - ry
    cu = size * 1.5 * rx; cv = size * math.sqrt(3) * (rz + rx / 2)
    du, dv = u - cu, v - cv
    # hex (flat-top) distance normalised to 1 at the edge
    au, av = np.abs(du), np.abs(dv)
    return np.maximum(av / (size * math.sqrt(3) / 2), (au * math.sqrt(3) / 2 + av / 2) / (size * math.sqrt(3) / 2))


def field_frames():
    """The dome as additive RGB layers (black = no light): (back, front) per frame, anchored on the
    base origin. Ray-cast per pixel through the strict 2:1 projection: screen (sx, sy) sees the points
    x - y = sx/32, x + y = (sy + z)/16, so each pixel hits the ellipsoid at two heights."""
    R, H = FIELD_R, FIELD_H
    x0, x1 = -R * 32 * math.sqrt(2) - 4, R * 32 * math.sqrt(2) + 4
    y0, y1 = -H - 6, R * 16 * math.sqrt(2) + 4
    W_, H_ = int(x1 - x0), int(y1 - y0)
    sx, sy = np.meshgrid(np.arange(W_) + x0 + 0.5, np.arange(H_) + y0 + 0.5)
    a, c = sx / 32.0, sy / 16.0
    A = 1 / (512 * R * R) + 1 / (H * H)
    B = c / (16 * R * R)
    C = (a * a + c * c) / (2 * R * R) - 1
    disc = B * B - 4 * A * C
    hit = disc > 0
    sq = np.sqrt(np.where(hit, disc, 0))
    out = []
    layers = []
    for zr in ((-B - sq) / (2 * A), (-B + sq) / (2 * A)):         # back, front
        ok = hit & (zr >= 0)
        b = c + zr / 16
        X, Y = (a + b) / 2, (b - a) / 2
        Z = zr / ZT
        nrm = np.stack([X / R ** 2, Y / R ** 2, Z / (H / ZT) ** 2])
        nrm /= np.linalg.norm(nrm, axis=0) + 1e-9
        ndv = np.abs((nrm * rv.VIEW[:, None, None]).sum(0))
        az = np.arctan2(Y, X)
        el = np.arcsin(np.clip(zr / H, 0, 1))
        hexd = _hexline(az * R, el * R * 1.3, 0.42)
        layers.append((ok, ndv, az, el, hexd, zr))
    for f in range(FIELD_FRAMES):
        t = f / FIELD_FRAMES
        pulse = 0.85 + 0.15 * math.sin(t * math.tau)
        pair = []
        for li, (ok, ndv, az, el, hexd, zr) in enumerate(layers):
            rim = (1 - ndv) ** 3
            edge = np.clip((hexd - 0.82) / 0.18, 0, 1) ** 2
            # bands of energy rolling up the dome, and a shimmer that drifts round it
            band = np.clip(np.sin(el * 9 - t * math.tau * 2) * 0.5 + 0.5, 0, 1) ** 6
            shim = 0.5 + 0.5 * np.sin(az * 5 + el * 7 + t * math.tau)
            foot = np.clip(1 - zr / 10, 0, 1)                         # bright seam where it meets the ground
            k = (0.04 + 0.5 * rim + edge * (0.15 + 0.2 * shim) + band * 0.16 + foot * 0.6) * pulse
            k *= 0.55 if li == 0 else 1.0
            k = np.where(ok, k, 0)
            rgb = np.clip(k[..., None] * FIELD_COL, 0, 255).astype(np.uint8)
            surf = pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(), (W_, H_), "RGB").copy()
            pair.append(surf)
        out.append((pair[0], pair[1], -x0, -y0))
    return out


def ripple_frames(n=12, size=90):
    """A bright hex-flecked splash where something strikes the field (additive RGB)."""
    out = []
    yy, xx = np.mgrid[-size:size, -size:size] + 0.5
    rr = np.hypot(xx, yy * 1.6) / size
    hexd = _hexline(xx / 14.0, yy / 9.0, 0.5)
    for i in range(n):
        t = (i + 1) / n
        ring = np.exp(-((rr - t * 0.9) / 0.09) ** 2) * (1 - t)
        core = np.exp(-(rr / (0.25 + 0.3 * t)) ** 2) * (1 - t) ** 2
        k = ring * (0.9 + 0.8 * (hexd > 0.8)) + core * 1.3
        rgb = np.clip(k[..., None] * np.array([150.0, 255.0, 210.0]), 0, 255).astype(np.uint8)
        out.append(pygame.image.frombuffer(np.ascontiguousarray(rgb).tobytes(), (2 * size, 2 * size), "RGB").copy())
    return out


class BaseSprites:
    def __init__(self):
        self.frames = {}
        self.shadow = None
        self.field = None
        self.ripples = None

    def accept(self, res):
        key, data, size, ax, ay = res
        s = pygame.image.frombytes(data, size, "RGBA")
        if pygame.display.get_surface():
            s = s.convert_alpha()
        self.frames[int(key.split(":")[1])] = (s, ax, ay)
        if self.shadow is None:
            sh, sx, sy = shadow_sprite()
            if pygame.display.get_surface():
                sh = sh.convert_alpha()
            self.shadow = (sh, sx, sy)
        if self.field is None:
            conv = (lambda x: x.convert()) if pygame.display.get_surface() else (lambda x: x)
            self.field = [(conv(b), conv(f), ax, ay) for b, f, ax, ay in field_frames()]
            self.ripples = [conv(r) for r in ripple_frames()]

    def ready(self):
        return len(self.frames) == FRAMES

    def get(self, t):
        return self.frames[int(t * 1.6) % FRAMES]      # ~10 s per turn

    def get_field(self, t):
        return self.field[int(t * 12) % FIELD_FRAMES]


# ------------------------------------------------------------ physical shape
def height_at(bx, by, x, y, rad=0.0):
    u, v = x - bx, y - by
    if abs(u) > HALF + rad or abs(v) > HALF + rad:
        return 0.0
    r = math.hypot(u, v)
    h = 0.0
    if r <= PAD_R + rad:
        h = PAD_Z
    if r <= PLINTH_R + rad:
        h = PLINTH_Z
    if r <= TURRET_R + rad:
        h = TURRET_Z
    for px, py in PYLONS:
        if (u - px) ** 2 + (v - py) ** 2 <= (0.3 + rad) ** 2:
            h = max(h, PYLON_H)
    return h


def in_footprint(bx, by, x, y, rad=0.0):
    return abs(x - bx) < HALF + rad and abs(y - by) < HALF + rad


def relation(bx, by, x, y, r=0.3):
    if x - r >= bx + HALF or y - r >= by + HALF:
        return 1
    if x + r <= bx - HALF or y + r <= by - HALF:
        return -1
    return 0
