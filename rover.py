"""The Moon Rover, after the buggy on Ultimate's 1983 Lunar Jetman cover.

A faceted, armoured, wedge-nosed buggy on six fat balloon tyres, built as a
real 3D polygon model and projected with the game's own strict 2:1 isometric
transform, so its edges run along the tile axes. It points along world +x
(screen south-east). Flat-shaded with the same light (from the east, above).

Units: x, y in tiles; z in screen pixels (32 px of height = 1 tile, for
normals, shading and depth only).
"""
import math
import os
import numpy as np
import pygame

ZT = 32.0                            # px of height per tile
LIGHT = np.array([0.62, -0.62, 0.48]); LIGHT /= np.linalg.norm(LIGHT)
VIEW = np.array([1.0, 1.0, 1.0]) / math.sqrt(3)      # towards the viewer (metric)
SS = 2

BODY = (214, 216, 210)                 # off-white pressurised cabin
BODY_D = (176, 178, 174)
SEAM = (120, 124, 126)
UNDER = (52, 56, 62)
GLASS = (22, 26, 34)
GLOW = (96, 72, 38)                  # warm cabin light behind the glass
GOLDW = (122, 96, 40)                  # gold-tinted side windows
GOLD = (196, 152, 58)                  # gold foil
RED = (206, 40, 38)
LAMP = (246, 250, 255)
GRILLE = (40, 44, 50)
TYRE = (40, 40, 44)
TYRE_L = (62, 62, 66)
MESH = (128, 132, 138)
MESH_D = (52, 54, 58)
HUB = (180, 184, 190)
ARM = (70, 74, 82)
AMBER = (255, 176, 60)

HALF_L, HALF_W = 2.55, 2.56          # footprint half-sizes in tiles (x, y)
# cabin cross-sections from the back to the nose:
# (x, half-width low, half-width top, z bottom, z top)
BODY_W = 0.86
RINGS = [
    (-2.45, 0.78, 0.70, 46, 140),
    (-2.32, BODY_W, 0.80, 40, 150),
    (1.55, BODY_W, 0.80, 40, 150),       # roof front edge
    (2.28, BODY_W, 0.76, 40, 106),       # raked wraparound windscreen
    (2.45, 0.80, 0.72, 44, 98),          # slightly tapered nose
]
WHEEL_X = (-1.6, 0.0, 1.6)
SY = 2.4                             # cabin stretched across world y
WHEEL_Y = BODY_W * SY + 0.2                       # centre line of the tyres, either side
WHEEL_R = 40.0                       # px
WHEEL_HW = 0.23                      # half tyre width, tiles


def M(p):
    """Model point (x tiles, y tiles, z px) -> metric (all tiles)."""
    return np.array([p[0], p[1], p[2] / ZT])


class Model:
    def __init__(self):
        self.faces = []
        self.sy = SY                 # y scale applied to body parts              # (pts (n,3) model units, colour, spec)

    def face(self, pts, col, spec=0.15, bias=0.0):
        p = np.array(pts, float); p[:, 1] *= self.sy
        self.faces.append((p, col, spec, bias))

    def decal(self, quad, s0, s1, t0, t1, col, spec=0.3, lift=0.012):
        """A coloured patch inside a quad face (bilinear s along edge 0-1, t along 0-3)."""
        q = np.array(quad, float)
        def at(s, t):
            a = q[0] + (q[1] - q[0]) * s; b = q[3] + (q[2] - q[3]) * s
            return a + (b - a) * t
        pts = [at(s0, t0), at(s1, t0), at(s1, t1), at(s0, t1)]
        n = normal(pts)
        c = np.mean([M(p) for p in pts], axis=0)
        if n @ (c - np.array([c[0], 0.0, 75.0 / ZT])) < 0:     # face outwards from the hull's spine
            pts = pts[::-1]; n = -n
        off = np.array([n[0], n[1], n[2] * ZT]) * lift
        self.face([p + off for p in pts], col, spec, 0.3)

    def box(self, lo, hi, col, spec=0.15, bias=0.0):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for a, b, c, d in ((4, 5, 6, 7), (0, 3, 2, 1), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7)):
            self.face([v[a], v[b], v[c], v[d]], col, spec, bias)


def normal(pts):
    p = [M(q) for q in pts]
    n = np.cross(p[1] - p[0], p[2] - p[0])
    if np.linalg.norm(n) < 1e-9 and len(p) > 3:
        n = np.cross(p[2] - p[0], p[3] - p[0])
    return n / (np.linalg.norm(n) + 1e-12)


def ring_pts(r):
    x, wl, wt, zb, zt = r
    c = 0.16                                     # chamfer, tiles
    cz = 10.0                                    # chamfer, px
    # anticlockwise seen from the nose (+x): -y is left
    return [(x, -wl + c, zb), (x, wl - c, zb), (x, wl, zb + cz), (x, wt, zt - cz),
            (x, wt - c, zt), (x, -wt + c, zt), (x, -wt, zt - cz), (x, -wl, zb + cz)]


def facing(pts, out):
    """Reverse a polygon if needed so its normal points along `out` (model units)."""
    n = normal(pts)
    return pts if n @ np.asarray(out, float) >= 0 else pts[::-1]


def flank(side, s, z):
    """Point on the cabin's tall flank: s along x (0 back..1 roof front), z px."""
    x0, x1 = RINGS[1][0], RINGS[2][0]
    zb, zt = 40 + 10, 150 - 10
    t = (z - zb) / (zt - zb)
    y = BODY_W + (0.80 - BODY_W) * t + 0.013
    return (x0 + (x1 - x0) * s, side * y, z)


def disc(m, c, u, v, r, col, out, spec=0.4, n=12, rz=None):
    """Circle centred c spanned by unit dirs u, v (u in tiles, v in tiles or px via rz)."""
    rz = r * ZT if rz is None else rz
    pts = [tuple(np.array(c) + np.array(u) * math.cos(i * math.tau / n) * r + np.array(v) * math.sin(i * math.tau / n) * rz)
           for i in range(n)]
    m.face(facing(pts, out), col, spec, 1.5)


# ESA emblem: the genuine logo (assets/esa_logo.png, transparent outside the disc), painted onto the
# visible (+y) cabin flank after the model is drawn, through the flank's exact 2:1 isometric affine.
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "esa_logo.png")
LOGO_S, LOGO_Z, LOGO_R = 0.41, 115.0, 24.0        # centre along the flank (0..1), height (px), radius (px)


def _logo_affine(ax, ay):
    """Sprite-pixel position of emblem coords (u, w), each -1..1 (u along the body, w up): q = o + u*a + w*b."""
    x0, x1 = RINGS[1][0], RINGS[2][0]
    Uc = (x0 + (x1 - x0) * LOGO_S) * ZT
    def scr(u, w):
        p = flank(1, ((Uc + u * LOGO_R) / ZT - x0) / (x1 - x0), LOGO_Z + w * LOGO_R)
        q = iso((p[0], p[1] * SY, p[2]))
        return np.array([q[0] + ax, q[1] + ay])
    o = scr(0, 0)
    return o, scr(1, 0) - o, scr(0, 1) - o


def _logo_light():
    """How the sun lights the flank, applied lightly so the logo sits in the scene but keeps its colours."""
    pts = [flank(1, 0.3, 80), flank(1, 0.6, 80), flank(1, 0.6, 130)]
    pts = [(p[0], p[1] * SY, p[2]) for p in pts]
    n = normal(pts)
    if n[1] < 0: n = -n
    k = shade((255, 255, 255), n, 0.0)[0] / 255.0
    return 1.0 - 0.2 * (1.0 - k)


def apply_logo(surf, ax, ay, scale=1):
    """Warp the logo onto the flank of `surf` (drawn at `scale` x sprite size, before its downsample).
    Premultiplied texture, prefiltered to about the sampling rate, and NSUB x NSUB supersampled per pixel
    then box-averaged, so the disc rim and the white shapes come out soft with no fringe."""
    NSUB = 4
    tex = pygame.image.load(LOGO_PATH)
    tex = pygame.image.frombytes(pygame.image.tobytes(tex, "RGBA"), tex.get_size(), "RGBA")
    o, a, b = _logo_affine(ax / scale, ay / scale); o, a, b = o * scale, a * scale, b * scale
    n = int(min(tex.get_width(), 2 * max(np.hypot(*a), np.hypot(*b)) * NSUB))   # texels ~ subsamples
    tex = pygame.transform.smoothscale(tex, (n, n))                             # area-filtered minify
    T = np.dstack([pygame.surfarray.array3d(tex), pygame.surfarray.array_alpha(tex)]).astype(np.float32)
    T[..., :3] *= T[..., 3:4] / 255.0                                            # premultiply
    corners = [o + su * a + sw * b for su in (-1, 1) for sw in (-1, 1)]
    X0, Y0 = np.floor(np.min(corners, 0)).astype(int) - 1; X1, Y1 = np.ceil(np.max(corners, 0)).astype(int) + 2
    X0, Y0 = max(X0, 0), max(Y0, 0); X1, Y1 = min(X1, surf.get_width()), min(Y1, surf.get_height())
    inv = np.linalg.inv(np.array([[a[0], b[0]], [a[1], b[1]]]))
    c = np.zeros((X1 - X0, Y1 - Y0, 4), np.float32)
    for sy_ in range(NSUB):
        for sx_ in range(NSUB):
            xs, ys = np.meshgrid(np.arange(X0, X1) + (sx_ + 0.5) / NSUB, np.arange(Y0, Y1) + (sy_ + 0.5) / NSUB,
                                 indexing="ij")
            dx, dy = xs - o[0], ys - o[1]
            u = inv[0, 0] * dx + inv[0, 1] * dy; w = inv[1, 0] * dx + inv[1, 1] * dy
            tx = (u + 1) / 2 * n - 0.5; ty = (1 - w) / 2 * n - 0.5
            i0 = np.floor(tx).astype(int); j0 = np.floor(ty).astype(int)
            fx = (tx - i0)[..., None]; fy = (ty - j0)[..., None]
            def g(i, j):
                ok = ((i >= 0) & (i < n) & (j >= 0) & (j < n))[..., None]
                return T[np.clip(i, 0, n - 1), np.clip(j, 0, n - 1)] * ok
            c += (g(i0, j0) * (1 - fx) * (1 - fy) + g(i0 + 1, j0) * fx * (1 - fy) +
                  g(i0, j0 + 1) * (1 - fx) * fy + g(i0 + 1, j0 + 1) * fx * fy)
    c /= NSUB * NSUB
    px = pygame.surfarray.pixels3d(surf); pa = pygame.surfarray.pixels_alpha(surf)
    cover = pa[X0:X1, Y0:Y1, None] / 255.0                                      # only onto the cabin itself
    al = c[..., 3:4] / 255.0 * cover
    pre = c[..., :3] * cover * _logo_light()                                    # premultiplied, lit
    dst = px[X0:X1, Y0:Y1].astype(np.float32)
    px[X0:X1, Y0:Y1] = (dst * (1 - al) + pre).round().clip(0, 255).astype(np.uint8)
    del px, pa


def build():
    m = Model()
    rings = [ring_pts(r) for r in RINGS]
    n = len(rings[0])
    sf = {}
    for i in range(len(rings) - 1):
        a, b = rings[i], rings[i + 1]
        for k in range(n):
            q = [a[k], a[(k + 1) % n], b[(k + 1) % n], b[k]]
            col = UNDER if k == 0 else (BODY_D if k in (1, 7) else BODY)
            if i == 2 and k in (3, 4, 5):
                col = GLASS
            m.face(q, col, 0.05 if k == 0 else (0.6 if col == GLASS else 0.3))
            sf[(i, k)] = q
    m.face(list(reversed(rings[0])), BODY_D, 0.2)
    m.face(rings[-1], BODY_D, 0.2)
    # warm glow behind the windscreen, and wraparound glass down the corners
    m.decal(sf[(2, 4)], 0.3, 0.7, 0.45, 0.85, GLOW, 0.2)
    for k in (2, 6):
        q = sf[(2, k)]; quad = [q[0], q[3], q[2], q[1]]
        m.decal(quad, 0.0, 0.92, 0.55, 1.0, GLASS, 0.6)
    for side in (-1, 1):
        out = (0, side, 0)
        # two small trapezoid side windows near the front, gold tinted
        for (sa, sb, sc, sd) in ((0.76, 0.86, 0.84, 0.80), (0.88, 0.99, 0.99, 0.92)):
            pass
        w1 = [flank(side, 0.74, 110), flank(side, 0.84, 110), flank(side, 0.84, 132), flank(side, 0.79, 132)]
        w2 = [flank(side, 0.87, 104), flank(side, 0.985, 104), flank(side, 0.985, 132), flank(side, 0.91, 132)]
        for w in (w1, w2):
            m.face(facing(w, out), GOLDW, 0.6, 0.6)
        # panel seams
        for sx in ((0.18, 0.42, 0.66) if side < 0 else (0.18, 0.66)):
            q = [flank(side, sx, 52), flank(side, sx + 0.01, 52), flank(side, sx + 0.006, 138), flank(side, sx, 138)]
            m.face(facing(q, out), SEAM, 0.1, 0.6)
        q = [flank(side, 0.0, 66), flank(side, 1.0, 66), flank(side, 1.0, 68), flank(side, 0.0, 68)]
        m.face(facing(q, out), SEAM, 0.1, 0.6)
        # small red circular marker
        c = np.array(flank(side, 0.66, 122)); c[1] += side * 0.004
        disc(m, c, (1, 0, 0), (0, 0, 1), 0.09, RED, out, 0.5, 12)
        # hatch outline at the back
        q = [flank(side, 0.04, 74), flank(side, 0.14, 74), flank(side, 0.14, 128), flank(side, 0.04, 128)]
        m.face(facing(q, out), BODY_D, 0.2, 0.6)
    # nose: gold-foil lower panel, grille band, round headlamps, marker lights
    xn = 2.475
    q = [(xn, -0.5, 50), (xn, 0.5, 50), (xn, 0.42, 76), (xn, -0.42, 76)]
    m.face(facing(q, (1, 0, 0)), GOLD, 0.9, 0.6)
    m.box((2.44, -0.66, 80), (2.50, 0.66, 92), GRILLE, 0.3, 0.6)
    m.sy = 1.0
    for y in (-0.6, -0.36, 0.36, 0.6):
        disc(m, (2.53, y * SY, 86), (0, 1, 0), (0, 0, 1), 0.09, LAMP, (1, 0, 0), 1.0, 12)
    m.sy = SY
    for y in (-0.56, 0.56):
        m.box((2.44, y - 0.04, 54), (2.50, y + 0.04, 60), AMBER, 0.8, 1.5)
    m.box((2.40, -0.3, 40), (2.52, 0.3, 50), BODY_D, 0.2)                  # bumper
    # roof: rails, flat dish and two tall aerials
    for y in (-0.62, 0.62):
        m.box((-2.1, y - 0.03, 150), (1.3, y + 0.03, 156), BODY_D, 0.4)
    m.sy = 1.0
    m.box((-0.25, -0.08, 150), (0.05, 0.08, 160), BODY_D, 0.3)
    dish = [(-0.1 + math.cos(i * math.tau / 16) * 0.85, math.sin(i * math.tau / 16) * 0.85, 162) for i in range(16)]
    m.face(facing(dish, (0, 0, 1)), (226, 228, 224), 0.5)
    m.face(facing(dish, (0, 0, -1)), BODY_D, 0.2)
    m.box((0.6, -1.2, 150), (0.64, -1.16, 236), MESH, 0.5)
    m.box((0.9, -1.5, 150), (0.93, -1.47, 196), MESH, 0.5)
    m.sy = SY
    # exposed chassis underneath
    m.box((-2.2, -0.62, 22), (2.3, 0.62, 40), UNDER, 0.1)
    m.sy = 1.0
    BW = BODY_W * SY
    for wx in WHEEL_X:
        for s in (-1, 1):
            yin, yout = s * (BW - 0.05), s * (WHEEL_Y - WHEEL_HW + 0.02)
            m.box((wx - 0.08, min(yin, yout), WHEEL_R - 5), (wx + 0.08, max(yin, yout), WHEEL_R + 5), ARM, 0.4)
            tyre(m, wx, s * WHEEL_Y, s)
            # mudguard fender over the wheel
            y0, y1 = sorted((s * (BW - 0.02), s * (WHEEL_Y + WHEEL_HW + 0.04)))
            m.box((wx - 0.62, y0, 84), (wx + 0.66, y1, 89), BODY_D, 0.4)
            m.box((wx + 0.62, y0, 70), (wx + 0.70, y1, 89), BODY_D, 0.4)
    return m


def tyre(m, cx, cy, side, N=24):
    """Big wheel: chunky dark tread, wire-mesh fan-spoke faces, bright hub."""
    R = WHEEL_R
    prof = [(-WHEEL_HW, 0.86), (-WHEEL_HW * 0.6, 1.0), (WHEEL_HW * 0.6, 1.0), (WHEEL_HW, 0.86)]
    rings = []
    for j, (dy, k) in enumerate(prof):
        ring = []
        for i in range(N):
            a = i * math.tau / N
            rr = R * k * (0.92 if (i % 2 and 0 < j < 3) else 1.0)
            ring.append((cx + math.cos(a) * rr / ZT, cy + dy, R + math.sin(a) * rr))
        rings.append(ring)
    for j in range(3):
        for i in range(N):
            a, b = rings[j], rings[j + 1]
            q = [a[i], a[(i + 1) % N], b[(i + 1) % N], b[i]]
            col = TYRE_L if (j == 1 and i % 2 == 0) else TYRE
            m.face(q[::-1], col, 0.08)
    for j, sgn in ((0, -1), (3, 1)):
        wall = rings[j][::-1] if sgn > 0 else rings[j]
        m.face(wall, TYRE, 0.05)
        yy = rings[j][0][1] + sgn * 0.01
        # fan of curved spokes (alternating light/dark mesh sectors)
        S = 16
        for i in range(S):
            a0, a1 = i * math.tau / S, (i + 1) * math.tau / S
            r0, r1 = 0.2 * R, 0.8 * R
            tw = 0.12                                      # curve the spokes
            p = [(cx + math.cos(a0) * r0 / ZT, yy, R + math.sin(a0) * r0),
                 (cx + math.cos(a0 + tw) * r1 / ZT, yy, R + math.sin(a0 + tw) * r1),
                 (cx + math.cos(a1 + tw) * r1 / ZT, yy, R + math.sin(a1 + tw) * r1),
                 (cx + math.cos(a1) * r0 / ZT, yy, R + math.sin(a1) * r0)]
            m.face(facing(p, (0, sgn, 0)), MESH if i % 2 else MESH_D, 0.5)
        cap = [(cx + math.cos(i * math.tau / 8) * R * 0.2 / ZT, yy + sgn * 0.01, R + math.sin(i * math.tau / 8) * R * 0.2) for i in range(8)]
        m.face(facing(cap, (0, sgn, 0)), HUB, 0.8)


def iso(p):
    return ((p[0] - p[1]) * 32.0, (p[0] + p[1]) * 16.0 - p[2])


def shade(col, n, spec):
    lam = max(0.0, float(n @ LIGHT))
    h = LIGHT + VIEW; h /= np.linalg.norm(h)
    sp = spec * max(0.0, float(n @ h)) ** 24 * 200
    k = 0.52 + 0.7 * lam + 0.08 * max(0.0, float(n[2]))
    return tuple(int(min(255, c * k + sp)) for c in col)


def render_model(m, post=None):
    faces = []
    for pts, col, spec, bias in m.faces:
        n = normal(pts)
        if n @ VIEW <= 1e-4:
            continue                                  # back face
        c = np.mean([M(p) for p in pts], axis=0)
        faces.append((c @ VIEW + bias, pts, shade(col, n, spec)))
    faces.sort(key=lambda f: f[0])
    allp = np.array([iso(p) for f in m.faces for p in f[0]])
    x0, y0 = allp.min(0) - 6; x1, y1 = allp.max(0) + 6
    W, H = int(x1 - x0) + 1, int(y1 - y0) + 1
    big = pygame.Surface((W * SS, H * SS), pygame.SRCALPHA)
    for _, pts, col in faces:
        poly = [((iso(p)[0] - x0) * SS, (iso(p)[1] - y0) * SS) for p in pts]
        pygame.draw.polygon(big, col, poly)
        pygame.draw.polygon(big, col, poly, 1)       # close hairline seams
    if post:
        post(big, -x0 * SS, -y0 * SS, SS)            # decals painted at the supersampled stage
    # dark outline for punch, as on the other sprites
    ol = pygame.mask.from_surface(big).outline()
    out = pygame.Surface(big.get_size(), pygame.SRCALPHA)
    if len(ol) > 2:
        pygame.draw.lines(out, (18, 18, 22, 255), True, ol, 4)
    out.blit(big, (0, 0))
    small = pygame.transform.smoothscale(out, (W, H))
    return small, -x0, -y0                           # anchor: model origin on the ground


def frame_bytes(key="rover"):
    surf, ax, ay = render_model(build(), post=apply_logo)
    return (key, pygame.image.tobytes(surf, "RGBA"), surf.get_size(), ax, ay)


def shadow_sprite():
    """Ground shadow cast west by the eastern sun (a separate ground-level layer)."""
    m = build()
    pts = []
    for f in m.faces:
        for p in f[0]:
            z = p[2] / ZT
            gx, gy = p[0] - LIGHT[0] / LIGHT[2] * z * 0.55, p[1] - LIGHT[1] / LIGHT[2] * z * 0.55
            pts.append(iso((gx, gy, 0.0)))
    pts = np.array(pts)
    # convex hull (monotone chain)
    P = sorted(set(map(tuple, np.round(pts, 1))))
    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    for p in reversed(P):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0: hi.pop()
        hi.append(p)
    hull = lo[:-1] + hi[:-1]
    x0, y0 = pts.min(0) - 12; x1, y1 = pts.max(0) + 12
    W, H = int(x1 - x0), int(y1 - y0)
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.polygon(s, (0, 0, 0, 120), [(x - x0, y - y0) for x, y in hull])
    s = pygame.transform.smoothscale(pygame.transform.smoothscale(s, (max(1, W // 6), max(1, H // 6))), (W, H))
    return s, -x0, -y0


def make_sprite(res):
    _, data, size, ax, ay = res
    s = pygame.image.frombytes(data, size, "RGBA")
    sh, sx, sy = shadow_sprite()
    if pygame.display.get_surface():
        s = s.convert_alpha(); sh = sh.convert_alpha()
    return s, ax, ay, sh, sx, sy


# ------------------------------------------------------------ physical shape
def _top_at(x):
    """Hull top (px) at model x."""
    for (xa, _, _, _, za), (xb, _, _, _, zb) in zip(RINGS, RINGS[1:]):
        if xa <= x <= xb:
            return za + (zb - za) * (x - xa) / (xb - xa)
    return 0.0


ROOF_Z = 150.0


def height_at(rx, ry, x, y, rad=0.0):
    """Height (px) of the rover's solid top under (x, y), 0 if clear of it. The whole footprint is one
    solid block: wheel-arch height out at the sides, the hull profile over the body, and a generous
    flat roof (stretched by rad at its edges) so Jetman settles on top reliably."""
    u, v = x - rx, y - ry
    if abs(u) > HALF_L + rad or abs(v) > HALF_W + rad:
        return 0.0
    if RINGS[1][0] - rad <= u <= RINGS[2][0] + rad:
        return ROOF_Z                                 # the roof block, side to side over the wheels too
    return max(89.0, _top_at(max(RINGS[0][0], min(RINGS[-1][0], u))))


def in_footprint(rx, ry, x, y, rad=0.0):
    return abs(x - rx) < HALF_L + rad and abs(y - ry) < HALF_W + rad


def relation(rx, ry, x, y, r=0.3):
    """+1 if a point object is in front of the rover, -1 if behind, 0 if unclear.
    Iso rule for boxes: whoever is entirely further along +x or +y is in front."""
    if x - r >= rx + HALF_L or y - r >= ry + HALF_W:
        return 1
    if x + r <= rx - HALF_L or y + r <= ry - HALF_W:
        return -1
    return 0
