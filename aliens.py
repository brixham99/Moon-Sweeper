"""Spiky, bouncing aliens for Moon Sweeper.

Each alien is a grey, pitted sphere covered in club-tipped spike proteins.
Sprites are rendered with the same lit-sphere renderer as Jetman (sun in the
east, strict 2:1 isometric view), in a few spike colours, at several spin
angles. Squash and stretch are applied when drawing.
"""
import math
import rover as rv
import random

import numpy as np
import pygame

import jetman as jm
import world as wd

BODY_R = 0.36                 # rig units (Jetman is ~1.9 tall)
SPIKE_LEN = 0.21
SPIN_FRAMES = 12
VARIANTS = {
    #  name       spike stalk        club tip          body tint
    "green":   ((96, 168, 60),   (170, 235, 80),   (150, 156, 146)),
    "crimson": ((150, 30, 36),   (235, 60, 60),    (156, 150, 150)),
    "violet":  ((110, 60, 160),  (200, 120, 255),  (150, 150, 158)),
    "amber":   ((170, 110, 30),  (255, 196, 70),   (156, 154, 146)),
}
NAMES = list(VARIANTS)


def _fib(n, seed):
    r = random.Random(seed)
    pts = []
    g = math.pi * (3 - math.sqrt(5))
    for i in range(n):
        y = 1 - 2 * (i + 0.5) / n
        rad = math.sqrt(1 - y * y)
        a = g * i
        p = np.array([math.cos(a) * rad, math.sin(a) * rad, y])
        p += np.array([r.uniform(-.08, .08) for _ in range(3)])
        pts.append(p / np.linalg.norm(p))
    return pts


def _rot(axis, ang):
    axis = axis / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def build(variant, frame):
    stalk, club, body = VARIANTS[variant]
    R = _rot(np.array([0.35, 0.2, 1.0]), frame / SPIN_FRAMES * math.tau / 3)  # 12 frames = 1/3 turn (pattern repeats)
    c = np.array([0, 0, BODY_R + SPIKE_LEN * 0.5])
    rg = jm.Rig()
    rg.sphere(c, BODY_R, body, 0.25)
    rb = random.Random(5)
    for d in _fib(70, 3):                      # pitted, lumpy surface texture
        d = R @ d
        shade = rb.uniform(0.78, 1.08)
        col = tuple(int(min(255, v * shade)) for v in body)
        rg.sphere(c + d * BODY_R * 0.93, BODY_R * rb.uniform(0.12, 0.2), col, 0.15)
    for d in _fib(26, 11):                     # spike proteins
        d = R @ d
        base = c + d * BODY_R * 0.95
        tip = c + d * (BODY_R + SPIKE_LEN)
        n = 7
        for t in np.linspace(0, 1, n):
            rg.sphere(base + (tip - base) * t, 0.026 + 0.008 * t, stalk, 0.3)
        rg.sphere(tip + d * 0.02, 0.058, club, 0.7)
    return rg


def frame_bytes(key):
    """(variant, frame) -> plain bytes, cropped; safe to run in a worker process."""
    variant, frame = key
    surf, (ax, ay) = jm.render(build(variant, frame))
    r = surf.get_bounding_rect()
    crop = surf.subsurface(r).copy()
    return key, pygame.image.tobytes(crop, "RGBA"), crop.get_size(), ax - r.x, ay - r.y


def all_keys():
    return [(v, f) for v in NAMES for f in range(SPIN_FRAMES)]


class AlienSprites:
    def __init__(self):
        self.base = {}
        self.squashed = {}

    def accept(self, res):
        key, data, size, ax, ay = res
        s = pygame.image.frombytes(data, size, "RGBA")
        if pygame.display.get_surface():
            s = s.convert_alpha()
        self.base[key] = (s, ax, ay)

    def get(self, variant, frame, sx=1.0, sy=1.0):
        """Sprite with squash (sx wider, sy shorter); anchor is the ground contact point."""
        if (variant, frame) not in self.base:
            self.accept(frame_bytes((variant, frame)))
        if abs(sx - 1) < 0.02 and abs(sy - 1) < 0.02:
            return self.base[(variant, frame)]
        qx, qy = round(sx * 20) / 20, round(sy * 20) / 20
        k = (variant, frame, qx, qy)
        hit = self.squashed.get(k)
        if hit:
            return hit
        s, ax, ay = self.base[(variant, frame)]
        w, h = max(1, int(s.get_width() * qx)), max(1, int(s.get_height() * qy))
        out = (pygame.transform.smoothscale(s, (w, h)), ax * qx, ay * qy)
        if len(self.squashed) > 600:
            self.squashed.clear()
        self.squashed[k] = out
        return out


# ---------------------------------------------------------------- behaviour
GRAV = 240.0          # px/s^2: floaty low-gravity hops
BOUNCE = (150.0, 230.0)
HOP_SPEED = (1.0, 2.0)
CHASE_RANGE = 7.0


class Alien:
    def __init__(self, uid, x, y, variant, rng_seed):
        self.uid = uid
        self.x, self.y, self.z = x, y, 0.0
        self.variant = variant
        self.rng = random.Random(rng_seed)
        self.heading = self.rng.uniform(0, math.tau)
        sp = self.rng.uniform(*HOP_SPEED)
        self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp
        self.vz = self.rng.uniform(*BOUNCE) * self.rng.uniform(0.2, 1.0)
        self.z = self.rng.uniform(0, 40)
        self.spin = self.rng.uniform(0, SPIN_FRAMES)
        self.spin_rate = self.rng.uniform(3, 7) * self.rng.choice((1, -1))
        self.land_t = 9.0
        self.floor = 0.0
        self.hit_cool = 0.0

    def update(self, dt, world, jx, jy, events):
        self.spin += self.spin_rate * dt
        self.land_t += dt
        self.hit_cool -= dt
        nx, ny = self.x + self.vx * dt, self.y + self.vy * dt
        rx, ry = world.rover
        if world.blocked(nx, self.y, self.z, 0.28):
            self.vx = -self.vx * 0.9; nx = self.x
            if rv.in_footprint(rx, ry, self.x, self.y, 0.4):          # bounce clear of the rover's flank
                self.vx += math.copysign(0.6, self.x - rx)
        if world.blocked(self.x, ny, self.z, 0.28):
            self.vy = -self.vy * 0.9; ny = self.y
            if rv.in_footprint(rx, ry, self.x, self.y, 0.4):
                self.vy += math.copysign(0.6, self.y - ry)
        self.x, self.y = nx, ny
        self.vz -= GRAV * dt
        self.z += self.vz * dt
        fl = world.floor_at(self.x, self.y)
        self.floor = fl
        if self.z <= fl and self.vz < 0:
            self.z = fl
            self.land_t = 0.0
            events.append(("bounce", self))
            self.vz = self.rng.uniform(*BOUNCE)
            # pick a new hop: wander, or drift towards Jetman when he is close
            dx, dy = jx - self.x, jy - self.y
            d = math.hypot(dx, dy)
            self.heading += self.rng.uniform(-1.1, 1.1)
            if d < CHASE_RANGE:
                want = math.atan2(dy, dx)
                diff = (want - self.heading + math.pi) % math.tau - math.pi
                self.heading += diff * 0.6
            sp = self.rng.uniform(*HOP_SPEED) * (1.25 if d < CHASE_RANGE else 1.0)
            self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp

    def squash(self):
        """(sx, sy): a quick squash on landing, a stretch when moving fast vertically."""
        t = self.land_t
        if t < 0.22:
            k = math.sin(t / 0.22 * math.pi) * (1 - t / 0.22 * 0.4)
            return 1 + 0.32 * k, 1 - 0.36 * k
        st = min(0.14, abs(self.vz) / 1600)
        return 1 - st * 0.6, 1 + st

    def lift(self):
        """Draw a squashed alien sitting slightly into the ground so it 'gives'."""
        return 0.0


def chunk_aliens(ci, cj):
    """Deterministic spawn list for a chunk: (uid, x, y, variant, seed)."""
    r = wd.chunk_rng(ci, cj, 21)
    out = []
    for i in range(r.choice((0, 0, 1, 1, 1, 2, 2))):
        out.append(((ci, cj, i), ci * wd.CHUNK + r.uniform(1, wd.CHUNK - 1), cj * wd.CHUNK + r.uniform(1, wd.CHUNK - 1),
                    r.choice(NAMES), r.randrange(1 << 30)))
    return out
