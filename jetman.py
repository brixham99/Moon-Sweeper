"""Jetman, built as a small 3D rig and rendered to sprites.

Each body part is a sphere or a capsule (a run of spheres). Parts are drawn as
lit sphere impostors into a z-buffer at 2x size and smooth-scaled down, so the
eight facings are true views of the same figure (the sun is in the east, so a
mirrored sprite would be lit from the wrong side). Frames are built lazily and
cached.
"""
import math
import numpy as np
import pygame

SS = 2                       # supersampling
S = 64                       # sprite pixels per rig unit (about 1.9 units tall)
# Camera basis for 2:1 isometric (30 degree elevation): right, up, towards viewer.
R = np.array([0.7071, -0.7071, 0.0])
U = np.array([-0.3536, -0.3536, 0.866])
V = np.array([0.6124, 0.6124, 0.5])
L = np.array([0.62, -0.62, 0.48]); L /= np.linalg.norm(L)
LS = np.array([L @ R, L @ U, L @ V])          # light in screen space
H = LS + np.array([0, 0, 1.0]); H /= np.linalg.norm(H)   # Blinn half-vector

WHITE = (232, 232, 228)
SUIT2 = (205, 206, 204)
GREY = (120, 122, 126)
DARK = (54, 56, 60)
GOLD = (240, 186, 70)
RED = (205, 46, 40)
GREEN = (80, 220, 110)
METAL = (170, 174, 180)


class Rig:
    def __init__(self):
        self.prims = []      # (centre world xyz, radius, colour, shininess)

    def sphere(self, c, r, col, spec=0.25):
        self.prims.append((np.asarray(c, float), r, col, spec))

    def capsule(self, a, b, r0, col, spec=0.25, r1=None):
        a, b = np.asarray(a, float), np.asarray(b, float)
        r1 = r0 if r1 is None else r1
        n = max(2, int(np.linalg.norm(b - a) / (min(r0, r1) * 0.09)) + 1)
        for t in np.linspace(0, 1, n):
            self.sphere(a + (b - a) * t, r0 + (r1 - r0) * t, col, spec)


def build(facing, pose="idle", phase=0.0, lean=0.0, recoil=0.0):
    """facing: 0..7 (0 = world +x, i.e. screen south-east, counting towards +y)."""
    h = facing * math.pi / 4
    f = np.array([math.cos(h), math.sin(h), 0.0])
    s = np.array([-math.sin(h), math.cos(h), 0.0])   # Jetman's right-hand side... sign picked so gun is on the right
    s = -s
    z = np.array([0, 0, 1.0])
    pivot = np.array([0, 0, 0.95])
    ca, sa = math.cos(lean), math.sin(lean)

    def P(fw, sd, up, body=True):
        p = f * fw + s * sd + z * up
        if body and lean:
            q = p - pivot
            qf, qz = q @ f, q @ z
            p = pivot + s * (q @ s) + f * (qf * ca + qz * sa) + z * (qz * ca - qf * sa)
        return p

    rg = Rig()
    # ---- legs
    if pose == "walk":
        sw = math.sin(phase * math.tau)
        lift_l = max(0.0, math.cos(phase * math.tau)) * 0.14
        lift_r = max(0.0, -math.cos(phase * math.tau)) * 0.14
        legs = ((-0.12, sw * 0.28, lift_l), (0.12, -sw * 0.28, lift_r))
    elif pose == "fly":
        legs = ((-0.12, -0.12, 0.10), (0.12, 0.05, 0.02))
    else:
        legs = ((-0.12, 0.02, 0.0), (0.12, -0.02, 0.0))
    for sd, fw, lift in legs:
        hip = P(0, sd, 0.92)
        foot = P(fw - (0.1 if pose == "fly" else 0), sd * 1.1, 0.12 + lift)
        knee = (hip + foot) / 2 + f * 0.07 + z * 0.02
        rg.capsule(hip, knee, 0.125, SUIT2)
        rg.capsule(knee, foot, 0.12, SUIT2, r1=0.1)
        rg.sphere(knee + f * 0.05, 0.11, WHITE)
        rg.capsule(foot - f * 0.02 + z * 0.0, foot + f * 0.13 - z * 0.04, 0.085, GREY, 0.3)
        rg.sphere(foot + z * 0.04, 0.095, METAL, 0.3)
    # ---- torso
    rg.capsule(P(0, 0, 0.98), P(0.01, 0, 1.3), 0.235, WHITE, 0.3, r1=0.255)
    rg.capsule(P(0.0, -0.14, 0.93), P(0.0, 0.14, 0.93), 0.13, GREY, 0.35)        # belt
    rg.sphere(P(0.12, 0, 0.93), 0.07, METAL, 0.6)                              # buckle
    rg.capsule(P(0.13, -0.12, 1.27), P(0.13, 0.12, 1.27), 0.1, SUIT2)            # chest unit
    rg.sphere(P(0.215, -0.07, 1.28), 0.035, RED, 0.9)
    rg.sphere(P(0.215, 0.0, 1.28), 0.035, GREEN, 0.9)
    rg.sphere(P(0.215, 0.07, 1.28), 0.035, (90, 160, 240), 0.9)
    # ---- jetpack
    rg.capsule(P(-0.3, 0, 1.0), P(-0.3, 0, 1.38), 0.22, METAL, 0.45)
    for sd in (-0.15, 0.15):
        rg.capsule(P(-0.44, sd, 0.92), P(-0.44, sd, 1.42), 0.12, (215, 216, 218), 0.6)
        rg.sphere(P(-0.44, sd, 1.48), 0.095, RED, 0.7)
        rg.capsule(P(-0.44, sd, 0.88), P(-0.44, sd, 0.76), 0.075, DARK, 0.5, r1=0.09)
    # ---- helmet
    rg.capsule(P(0, 0, 1.42), P(0, 0, 1.46), 0.18, GREY, 0.4)                  # neck ring
    rg.sphere(P(-0.01, 0, 1.64), 0.235, WHITE, 0.5)
    rg.sphere(P(0.075, 0, 1.62), 0.195, GOLD, 1.0)                              # visor
    rg.sphere(P(-0.02, 0.17, 1.66), 0.05, GREY, 0.4)                           # ear pods
    rg.sphere(P(-0.02, -0.17, 1.66), 0.05, GREY, 0.4)
    rg.capsule(P(-0.08, 0.13, 1.8), P(-0.12, 0.15, 1.98), 0.012, DARK)          # antenna
    rg.sphere(P(-0.12, 0.15, 1.99), 0.025, RED, 0.9)
    # ---- arms: right holds the laser forward, left swings
    back = recoil * 0.12
    sh_r = P(0, 0.3, 1.32)
    hand_r = P(0.36 - back, 0.2, 1.2)
    el_r = (sh_r + hand_r) / 2 - z * 0.08 + s * 0.05
    rg.sphere(sh_r, 0.13, WHITE, 0.3)
    rg.capsule(sh_r, el_r, 0.085, SUIT2)
    rg.capsule(el_r, hand_r, 0.08, SUIT2)
    rg.sphere(hand_r, 0.085, GREY, 0.3)
    g0, g1 = hand_r + f * 0.0 + z * 0.06, hand_r + f * 0.48 + z * 0.06
    rg.capsule(g0 - f * 0.1, g0 + f * 0.24, 0.09, DARK, 0.5)
    rg.capsule(g0 + f * 0.05 + z * 0.0, g0 + f * 0.2, 0.095, (150, 40, 36), 0.6)                  # gun body
    rg.capsule(g0 + f * 0.1, g1, 0.05, METAL, 0.8)                             # barrel
    rg.sphere(g1, 0.05, (230, 90, 70), 0.9)                                    # emitter
    rg.capsule(g0 + z * 0.08, g0 + z * 0.08 + f * 0.14, 0.03, METAL, 0.8)      # sight
    sh_l = P(0, -0.3, 1.32)
    if pose == "walk":
        swing = -math.sin(phase * math.tau) * 0.22
        hand_l = P(swing, -0.3, 0.98)
    elif pose == "fly":
        hand_l = P(0.18, -0.3, 1.1)
    else:
        hand_l = P(0.04, -0.3, 0.98)
    el_l = (sh_l + hand_l) / 2 - s * 0.03
    rg.sphere(sh_l, 0.13, WHITE, 0.3)
    rg.capsule(sh_l, el_l, 0.085, SUIT2)
    rg.capsule(el_l, hand_l, 0.08, SUIT2)
    rg.sphere(hand_l, 0.085, GREY, 0.3)
    nozzles = [P(-0.44, sd, 0.72) for sd in (-0.15, 0.15)]
    return rg, nozzles, g1 + f * 0.06, f


def render(rg, extra=()):
    W, Hh = 190 * SS, 190 * SS
    ox, oy = W / 2, Hh * 0.82          # where the feet (rig origin) land
    zbuf = np.full((W, Hh), -1e9)
    col = np.zeros((W, Hh, 3))
    for c, r, rgb, spec in rg.prims:
        cx = ox + (c @ R) * S * SS
        cy = oy - (c @ U) * S * SS
        cz = (c @ V) * S * SS
        rp = r * S * SS
        x0, x1 = int(max(0, cx - rp)), int(min(W, cx + rp + 2))
        y0, y1 = int(max(0, cy - rp)), int(min(Hh, cy + rp + 2))
        if x0 >= x1 or y0 >= y1:
            continue
        xs = (np.arange(x0, x1) + 0.5 - cx)[:, None] / rp
        ys = (np.arange(y0, y1) + 0.5 - cy)[None, :] / rp
        d2 = xs * xs + ys * ys
        m = d2 < 1
        nz = np.sqrt(np.clip(1 - d2, 0, 1))
        depth = cz + nz * rp
        zb = zbuf[x0:x1, y0:y1]
        m &= depth > zb
        if not m.any():
            continue
        nr, nu = xs, -ys
        lam = np.clip(nr * LS[0] + nu * LS[1] + nz * LS[2], 0, 1)
        hs = np.clip(nr * H[0] + nu * H[1] + nz * H[2], 0, 1)
        base = np.array(rgb, float)
        shade = 0.2 + 0.95 * lam
        # cool bounce light from the grey ground on downward faces
        bounce = np.clip(-nu, 0, 1) * 0.12
        out = base[None, None, :] * (shade + bounce)[..., None]
        sp = spec * hs ** (8 + 40 * spec) * 255
        if rgb == GOLD:                       # visor: dark mirror with sky reflection
            # dark gold mirror: black sky above, bright grey regolith reflected below
            env = 0.18 + 0.75 * np.clip(-nu * 0.9 + 0.15, 0, 1) ** 1.5
            out = base[None, None, :] * env[..., None] + base[None, None, :] * 0.35 * lam[..., None]
            out = out + (hs ** 60 * 255 * 1.4)[..., None]
        else:
            out = out + sp[..., None]
        zb[m] = depth[m]
        col[x0:x1, y0:y1][m] = out[m]
    alpha = (zbuf > -1e8)
    img = np.clip(col, 0, 255).astype(np.uint8)
    surf = pygame.Surface((W, Hh), pygame.SRCALPHA)
    pygame.surfarray.blit_array(surf, img)
    a = pygame.surfarray.pixels_alpha(surf); a[:] = alpha * 255; del a
    # dark outline for punch
    msk = pygame.mask.from_surface(surf)
    ol = msk.outline()
    big = pygame.Surface((W, Hh), pygame.SRCALPHA)
    if len(ol) > 2:
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            pygame.draw.lines(big, (18, 18, 22, 255), True, [(x + dx / 2, y + dy / 2) for x, y in ol], 3)
    big.blit(surf, (0, 0))
    small = pygame.transform.smoothscale(big, (W // SS, Hh // SS))
    return small, (ox / SS, oy / SS)


def project(p):
    return (p @ R) * S, -(p @ U) * S


class JetmanSprites:
    def __init__(self):
        self.cache = {}

    def get(self, facing, pose="idle", frame=0, lean=0, recoil=0, convert=True):
        key = (facing, pose, frame, lean, recoil)
        hit = self.cache.get(key)
        if hit:
            return hit
        phase = frame / 8.0
        rg, noz, muz, f = build(facing, pose, phase, lean * 0.18, recoil)
        surf, (ax, ay) = render(rg)
        if convert and pygame.display.get_surface():
            surf = surf.convert_alpha()
        nozzles = [project(n) for n in noz]
        mz = project(muz)
        res = (surf, ax, ay, nozzles, mz, (muz @ V) > 0.0, f)
        if convert or key not in self.cache:
            self.cache[key] = res
        return res


def frame_bytes(key):
    """Build one frame in a worker process; returns plain bytes so it can be sent back."""
    facing, pose, frame, lean, recoil = key
    rg, noz, muz, f = build(facing, pose, frame / 8.0, lean * 0.18, recoil)
    surf, (ax, ay) = render(rg)
    return (key, pygame.image.tobytes(surf, "RGBA"), surf.get_size(), ax, ay,
            [project(n) for n in noz], project(muz), bool((muz @ V) > 0.0), f)


def all_keys():
    keys = []
    for pose, frames in (("idle", [0]), ("walk", range(8)), ("fly", [0])):
        for fr in frames:
            for fc in range(8):
                for lean in ((0, 1, 2, 3, -1) if pose == "fly" else (0,)):
                    for rc in (0, 1):
                        keys.append((fc, pose, fr, lean, rc))
    return keys


def accept(cache, res):
    key, data, size, ax, ay, noz, mz, flag, f = res
    surf = pygame.image.frombytes(data, size, "RGBA")
    if pygame.display.get_surface():
        surf = surf.convert_alpha()
    cache[key] = (surf, ax, ay, noz, mz, flag, f)
