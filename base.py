"""The Alien Missile Base, after the launch site in Ultimate's Lunar Jetman (1983).

An octagonal armoured launchpad with a raised plinth; on it a turret that
slowly turns, carrying a gold launcher arm with two missiles raised at 50
degrees. Six protective field devices (pylons with glowing emitter heads)
stand guard round the pad. Built as a 3D polygon model with the same strict
2:1 isometric projection, light and shading as the rover (see rover.py).

Units: x, y in tiles; z in screen pixels (32 px = 1 tile).
"""
import math
import numpy as np
import pygame

import rover as rv
from rover import ZT, facing

FRAMES = 16                           # turret rotation frames (full turn)

STEEL = (150, 154, 160)
STEEL_D = (96, 100, 108)
DECK = (118, 120, 124)
DARK = (46, 48, 54)
GOLD = (210, 168, 56)
GOLD_D = (150, 116, 36)
WHITE = (226, 228, 230)
RED = (214, 44, 40)
HAZ = (226, 186, 40)
CYAN = (110, 236, 236)
CYAN_D = (40, 150, 160)

PAD_R, PAD_Z = 2.7, 18.0
PLINTH_R, PLINTH_Z = 1.35, 32.0
TURRET_R, TURRET_Z = 0.78, 62.0
PYLON_R, PYLON_H = 3.75, 92.0
PYLONS = [(PYLON_R * math.cos(math.tau * (i + 0.5) / 6), PYLON_R * math.sin(math.tau * (i + 0.5) / 6)) for i in range(6)]
HALF = 4.15                           # square footprint half-size (tiles) for depth sorting


def _ngon(cx, cy, r, n, rot=0.0):
    return [(cx + r * math.cos(rot + i * math.tau / n), cy + r * math.sin(rot + i * math.tau / n)) for i in range(n)]


def prism(m, base, z0, z1, col, spec=0.2, top=None, side=None, bias=0.0, tbias=None):
    """Vertical prism over a convex polygon (list of (x, y)); faces oriented outwards."""
    cx = sum(p[0] for p in base) / len(base); cy = sum(p[1] for p in base) / len(base)
    lo = [(x, y, z0) for x, y in base]; hi = [(x, y, z1) for x, y in base]
    m.face(facing(hi, (0, 0, 1)), top or col, spec, bias if tbias is None else tbias)
    m.face(facing(lo, (0, 0, -1)), col, spec, bias)
    n = len(base)
    for i in range(n):
        a, b = base[i], base[(i + 1) % n]
        q = [lo[i], lo[(i + 1) % n], hi[(i + 1) % n], hi[i]]
        out = ((a[0] + b[0]) / 2 - cx, (a[1] + b[1]) / 2 - cy, 0)
        c = side(i) if side else col
        m.face(facing(q, out), c, spec, bias)


def solid(m, pts8, col, spec=0.3, bias=0.0, cols=None):
    """A hexahedron from 8 model points (0-3 bottom ring, 4-7 top ring)."""
    P = np.array([rv.M(p) for p in pts8]); c = P.mean(0)
    for j, (a, b, cc, d) in enumerate(((4, 5, 6, 7), (0, 3, 2, 1), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (3, 0, 4, 7))):
        q = [pts8[a], pts8[b], pts8[cc], pts8[d]]
        fc = np.mean([rv.M(p) for p in q], axis=0)
        m.face(facing(q, fc - c), (cols[j] if cols else col), spec, bias)


def obox(m, piv, th, el, u0, u1, w, h0, h1, col, spec=0.3, bias=0.0, cols=None):
    """Box in a frame rotated th about z and pitched el about the side axis, pivot piv (x, y, z px).
    u along the arm (tiles), w half-width (tiles), h0..h1 across the arm (tiles)."""
    ca, sa, ce, se = math.cos(th), math.sin(th), math.cos(el), math.sin(el)
    fwd = np.array([ca * ce, sa * ce, se]); side = np.array([-sa, ca, 0.0]); up = np.cross(fwd, side) * -1
    if up[2] < 0: up = -up
    P0 = np.array([piv[0], piv[1], piv[2] / ZT])
    pts = []
    for hh in (h0, h1):
        for uu, ww in ((u0, -w), (u1, -w), (u1, w), (u0, w)):
            p = P0 + fwd * uu + side * ww + up * hh
            pts.append((p[0], p[1], p[2] * ZT))
    solid(m, pts, col, spec, bias, cols)


def missile(m, piv, th, el, off, n=8):
    """A slim missile along the arm: white body, red nose cone, dark fins."""
    ca, sa, ce, se = math.cos(th), math.sin(th), math.cos(el), math.sin(el)
    fwd = np.array([ca * ce, sa * ce, se]); side = np.array([-sa, ca, 0.0]); up = np.cross(side, fwd)
    P0 = np.array([piv[0], piv[1], piv[2] / ZT]) + side * off + up * 0.2
    r = 0.17
    def ring(u, rr):
        return [P0 + fwd * u + (side * math.cos(i * math.tau / n) + up * math.sin(i * math.tau / n)) * rr for i in range(n)]
    def mp(p): return (p[0], p[1], p[2] * ZT)
    secs = [(-0.75, r * 0.85, DARK), (-0.65, r, WHITE), (1.05, r, WHITE), (1.3, r * 0.7, RED), (1.55, 0.01, RED)]
    centre = P0 + fwd * 0.3
    for (ua, ra, _), (ub, rb, cb) in zip(secs, secs[1:]):
        A, B = ring(ua, ra), ring(ub, rb)
        for i in range(n):
            q = [mp(A[i]), mp(A[(i + 1) % n]), mp(B[(i + 1) % n]), mp(B[i])]
            mid = (A[i] + B[(i + 1) % n]) / 2
            out = mid - (P0 + fwd * ((ua + ub) / 2))
            col = cb if not (cb == WHITE and i % 4 == 0 and 0.2 < (ua + ub) / 2 < 0.4) else RED
            m.face(facing(q, out), col, 0.5, 0.4)
    m.face(facing([mp(p) for p in ring(-0.75, r * 0.85)], -fwd), DARK, 0.2, 0.4)
    for k in (0, 2):                                   # fins
        d = side if k == 0 else up
        a = P0 + fwd * -0.7; b = P0 + fwd * -0.2
        q = [mp(a + d * r), mp(b + d * r), mp(a + d * (r + 0.2))]
        m.face(facing(q, np.cross(fwd, d)), RED, 0.3, 0.5)
        q = [mp(a - d * r), mp(b - d * r), mp(a - d * (r + 0.2))]
        m.face(facing(q, np.cross(fwd, d)), RED, 0.3, 0.5)


def pylon(m, px, py):
    """A protective field device: footing, tapered mast, collar and a glowing emitter head."""
    prism(m, _ngon(px, py, 0.42, 6), 0, 10, STEEL_D, 0.2)
    lo = _ngon(px, py, 0.17, 6); hi = _ngon(px, py, 0.11, 6)
    for i in range(6):
        q = [(lo[i][0], lo[i][1], 10), (lo[(i + 1) % 6][0], lo[(i + 1) % 6][1], 10),
             (hi[(i + 1) % 6][0], hi[(i + 1) % 6][1], PYLON_H - 22), (hi[i][0], hi[i][1], PYLON_H - 22)]
        mx = (lo[i][0] + lo[(i + 1) % 6][0]) / 2 - px; my = (lo[i][1] + lo[(i + 1) % 6][1]) / 2 - py
        m.face(facing(q, (mx, my, 0)), STEEL if i % 2 else STEEL_D, 0.4)
    prism(m, _ngon(px, py, 0.26, 6), PYLON_H - 26, PYLON_H - 20, GOLD, 0.6, bias=0.2)
    # emitter: faceted crystal (double pyramid) in field cyan
    eq = _ngon(px, py, 0.24, 6)
    zc = PYLON_H - 8
    for i in range(6):
        a, b = eq[i], eq[(i + 1) % 6]
        for zt, out_z in ((PYLON_H + 6, 1), (PYLON_H - 20, -1)):
            q = [(a[0], a[1], zc), (b[0], b[1], zc), (px, py, zt)]
            mx = (a[0] + b[0]) / 2 - px; my = (a[1] + b[1]) / 2 - py
            m.face(facing(q, (mx, my, out_z * 0.6)), CYAN if (i % 2 or out_z > 0) else CYAN_D, 0.9, 0.3)


def build(frame=0, parts=("pad", "turret", "pylons")):
    m = rv.Model(); m.sy = 1.0
    th = frame * math.tau / FRAMES
    if "pad" in parts:
        oct8 = _ngon(0, 0, PAD_R, 8, math.pi / 8)
        prism(m, oct8, 0, PAD_Z, STEEL_D, 0.2, top=DECK, side=lambda i: STEEL_D if i % 2 else STEEL, tbias=-9.0)
        # gold trim band round the pad edge
        tb = _ngon(0, 0, PAD_R + 0.03, 8, math.pi / 8)
        for i in range(8):
            a, b = tb[i], tb[(i + 1) % 8]
            q = [(a[0], a[1], PAD_Z - 7), (b[0], b[1], PAD_Z - 7), (b[0], b[1], PAD_Z - 3), (a[0], a[1], PAD_Z - 3)]
            m.face(facing(q, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0)), GOLD, 0.7, 0.1)
        # hazard chevrons on the deck: alternate gold/dark trapezoids round the rim
        ring_o = _ngon(0, 0, PAD_R - 0.15, 32); ring_i = _ngon(0, 0, PAD_R - 0.5, 32)
        for i in range(32):
            a, b = ring_o[i], ring_o[(i + 1) % 32]; c, d = ring_i[(i + 1) % 32], ring_i[i]
            q = [(a[0], a[1], PAD_Z + 0.5), (b[0], b[1], PAD_Z + 0.5), (c[0], c[1], PAD_Z + 0.5), (d[0], d[1], PAD_Z + 0.5)]
            m.face(facing(q, (0, 0, 1)), HAZ if i % 2 else DARK, 0.3, -3.5)
        # four access ramps / steps
        for k in range(4):
            a = k * math.pi / 2
            ca, sa = math.cos(a), math.sin(a)
            r0, r1, w = PAD_R - 0.1, PAD_R + 0.75, 0.42
            base = [(ca * r0 - sa * -w, sa * r0 + ca * -w), (ca * r1 - sa * -w, sa * r1 + ca * -w),
                    (ca * r1 - sa * w, sa * r1 + ca * w), (ca * r0 - sa * w, sa * r0 + ca * w)]
            pts = [(x, y, 0) for x, y in base] + [(base[0][0], base[0][1], PAD_Z), (base[1][0], base[1][1], 2),
                                                  (base[2][0], base[2][1], 2), (base[3][0], base[3][1], PAD_Z)]
            solid(m, pts, STEEL, 0.25, -0.2)
        # corner landing lights
        for x, y in _ngon(0, 0, PAD_R - 0.28, 8, math.pi / 8):
            prism(m, _ngon(x, y, 0.09, 6), PAD_Z, PAD_Z + 4, RED, 1.0, bias=0.4)
        # raised plinth with a dark slot ring
        prism(m, _ngon(0, 0, PLINTH_R, 8, math.pi / 8), PAD_Z, PLINTH_Z, STEEL, 0.3, top=STEEL_D,
              side=lambda i: STEEL if i % 2 else STEEL_D)
        prism(m, _ngon(0, 0, 0.95, 16), PLINTH_Z, PLINTH_Z + 2, DARK, 0.1, bias=0.1)
    if "turret" in parts:
        # turning drum
        prism(m, _ngon(0, 0, TURRET_R, 12, th), PLINTH_Z + 2, TURRET_Z - 10, GOLD_D, 0.4, top=GOLD,
              side=lambda i: GOLD if i % 2 else GOLD_D, bias=0.2, tbias=1.2)
        prism(m, _ngon(0, 0, TURRET_R * 0.72, 12, th), TURRET_Z - 10, TURRET_Z, GOLD, 0.6, bias=1.25, tbias=1.4)
        ca, sa = math.cos(th), math.sin(th)
        n0 = len(m.faces)
        # cab/radar block at the back of the drum
        obox(m, (-ca * 0.35, -sa * 0.35, TURRET_Z), th, 0.0, -0.3, 0.25, 0.32, 0.0, 0.55, STEEL, 0.4, 0.35)
        # radar dish on a short mast
        obox(m, (-ca * 0.45, -sa * 0.45, TURRET_Z + 17), th, 0.0, -0.05, 0.05, 0.04, 0.0, 0.5, DARK, 0.2, 0.4)
        dish_c = (-ca * 0.45, -sa * 0.45, TURRET_Z + 34)
        dpts = [(dish_c[0] + (-sa) * math.cos(i * math.tau / 10) * 0.32, dish_c[1] + ca * math.cos(i * math.tau / 10) * 0.32,
                 dish_c[2] + math.sin(i * math.tau / 10) * 0.32 * ZT) for i in range(10)]
        m.face(facing(dpts, (-ca, -sa, 0)), WHITE, 0.6, 0.5)
        m.face(facing(dpts, (ca, sa, 0)), STEEL_D, 0.2, 0.45)
        # launcher: two cheek plates and a gold rail arm pitched up, two missiles
        el = math.radians(48)
        piv = (ca * 0.15, sa * 0.15, TURRET_Z + 22)
        for s in (-1, 1):
            obox(m, (piv[0] - sa * s * 0.42, piv[1] + ca * s * 0.42, TURRET_Z), th, 0.0, -0.35, 0.35, 0.06, 0.0, 0.95, GOLD_D, 0.5, 0.35)
        obox(m, piv, th, el, -0.8, 1.2, 0.44, -0.08, 0.06, GOLD, 0.7, 0.5)
        obox(m, piv, th, el, -0.2, 0.2, 0.46, -0.16, 0.0, STEEL_D, 0.3, 0.45)
        for off in (-0.25, 0.25):
            missile(m, piv, th, el, off)
        m.faces[n0:] = [(p, c, sp, b + 2.0) for p, c, sp, b in m.faces[n0:]]     # all above the drum top
    if "pylons" in parts:
        for px, py in PYLONS:
            pylon(m, px, py)
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
        m = rv.Model(); m.sy = 1.0; pylon(m, px, py)
        hulls.append(_hull(proj(m)))
    allp = np.array([p for h in hulls for p in h])
    x0, y0 = allp.min(0) - 12; x1, y1 = allp.max(0) + 12
    W, H = int(x1 - x0), int(y1 - y0)
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    for h in hulls:
        pygame.draw.polygon(s, (0, 0, 0, 120), [(x - x0, y - y0) for x, y in h])
    s = pygame.transform.smoothscale(pygame.transform.smoothscale(s, (max(1, W // 6), max(1, H // 6))), (W, H))
    return s, -x0, -y0


class BaseSprites:
    def __init__(self):
        self.frames = {}
        self.shadow = None

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

    def ready(self):
        return len(self.frames) == FRAMES

    def get(self, t):
        return self.frames[int(t * 1.6) % FRAMES]      # ~10 s per turn


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
