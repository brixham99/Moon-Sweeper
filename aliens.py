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
NAMES = list(VARIANTS)          # the bouncing Spore Hoppers (spawn tables pick from these)
# the Alien Missile Base's homing spores: smaller, hot orange with red-hot tips (never purple)
SEEKER_VARIANT = ((255, 170, 60), (255, 60, 20), (255, 120, 40))
SEEKER_SCALE = 0.7
ALL_VARIANTS = dict(VARIANTS, seeker=SEEKER_VARIANT)
SPHERE_KINDS = ("spore", "seeker")   # drawn with the lit-sphere sprites rather than painted art


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
    stalk, club, body = ALL_VARIANTS[variant]
    k = SEEKER_SCALE if variant == "seeker" else 1.0
    BODY_R, SPIKE_LEN = globals()["BODY_R"] * k, globals()["SPIKE_LEN"] * k
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
            rg.sphere(base + (tip - base) * t, (0.026 + 0.008 * t) * (k ** 0.5), stalk, 0.3)
        rg.sphere(tip + d * 0.02 * k, 0.058 * (k ** 0.5), club, 0.7)
    return rg


def frame_bytes(key):
    """(variant, frame) -> plain bytes, cropped; safe to run in a worker process."""
    variant, frame = key
    surf, (ax, ay) = jm.render(build(variant, frame))
    r = surf.get_bounding_rect()
    crop = surf.subsurface(r).copy()
    return key, pygame.image.tobytes(crop, "RGBA"), crop.get_size(), ax - r.x, ay - r.y


def all_keys():
    return [(v, f) for v in NAMES + ["seeker"] for f in range(SPIN_FRAMES)]


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
    kind = "spore"

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

    def update(self, dt, world, jx, jy, events, jz=0.0):
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


# ---------------------------------------------------------------- level 2 and 3 aliens
# Painted sprites (assets/art/creeper.png, flyer.png: AI-painted from the spore alien as a
# style reference, cut out of a magenta background by tools/cutout.py). Each faces the
# viewer's lower right; it is mirrored when the creature heads the other way.
import os
ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "art")
KINDS = {
    #  kind       file           width px  points  stamina hit  splat colour     reach (tiles)  body (px up)
    "spore":   (None,           None,     100,    8,           None,            0.55,          22),
    "creeper": ("creeper.png",  78,       150,    12,          (200, 110, 40),  0.7,           14),
    "flyer":   ("flyer.png",    104,      250,    15,          (70, 200, 220),  0.75,          16),
    "seeker":  (None,           None,     50,     10,          None,            0.5,           14),
}
CREEPER_SHEET, CREEPER_FRAMES, CREEPER_PAD = "creeper_walk.png", 8, 6
CREEPER_STRIDE = 0.42        # tiles travelled per full scuttle cycle (keeps the feet from sliding)
KIND_NAMES = {"spore": "Spore Hoppers", "creeper": "Rock Creepers", "flyer": "Void Rays", "seeker": "Seeker Spores"}


class PaintedSprites:
    """Loads the painted aliens once and hands out flipped / squashed copies (cached)."""

    def __init__(self):
        self.src, self.cache = {}, {}
        self.pad = 0.0

    def _load(self, kind, frame=0):
        if (kind, frame) not in self.src:
            fn, w = KINDS[kind][0], KINDS[kind][1]
            if kind == "creeper":                 # the scuttle cycle (tools/creeper_frames.py)
                sheet = pygame.image.load(os.path.join(ART, CREEPER_SHEET))
                fw = sheet.get_width() // CREEPER_FRAMES
                k = w / (fw - 2 * CREEPER_PAD)    # same scale as the plain sprite
                for i in range(CREEPER_FRAMES):
                    img = sheet.subsurface((i * fw, 0, fw, sheet.get_height())).copy()
                    if pygame.display.get_surface():
                        img = img.convert_alpha()
                    self.src[(kind, i)] = pygame.transform.smoothscale(
                        img, (round(fw * k), round(img.get_height() * k)))
                self.pad = CREEPER_PAD * k
            else:
                img = pygame.image.load(os.path.join(ART, fn))
                if pygame.display.get_surface():
                    img = img.convert_alpha()
                h = int(img.get_height() * w / img.get_width())
                self.src[(kind, frame)] = pygame.transform.smoothscale(img, (w, h))
        return self.src[(kind, frame)]

    def get(self, kind, flip, sy=1.0, sx=1.0, frame=0):
        """(surface, anchor x, anchor y); the anchor is the ground contact (creeper) or the
        point under the body's centre (flyer)."""
        qy, qx = round(sy * 20) / 20, round(sx * 20) / 20
        frame = frame % CREEPER_FRAMES if kind == "creeper" else 0
        k = (kind, flip, qy, qx, frame)
        hit = self.cache.get(k)
        if hit:
            return hit
        s = self._load(kind, frame)
        if flip:
            s = pygame.transform.flip(s, True, False)
        if qy != 1 or qx != 1:
            s = pygame.transform.smoothscale(s, (max(1, int(s.get_width() * qx)), max(1, int(s.get_height() * qy))))
        w, h = s.get_size()
        out = (s, w / 2, h - (2 + self.pad * qy if kind == "creeper" else h * 0.35))
        self.cache[k] = out
        return out


class Creeper:
    """Level 2: a low armoured crawler. It scuttles over the ground, can't climb the rover or
    boulders (it turns away), and hunts Jetman when he's on or near the ground."""
    kind = "creeper"

    def __init__(self, uid, x, y, seed, speed=1.0):
        self.uid, self.x, self.y, self.z = uid, x, y, 0.0
        self.variant = "creeper"
        self.rng = random.Random(seed)
        self.heading = self.rng.uniform(0, math.tau)
        self.speed = speed
        self.vx = self.vy = self.vz = 0.0
        self.floor, self.hit_cool, self.land_t = 0.0, 0.0, 9.0
        self.spin = self.rng.uniform(0, 10); self.spin_rate = 0.0
        self.turn_t = 0.0
        self.gait = self.rng.random()

    def frame(self):
        return int(self.gait * CREEPER_FRAMES) % CREEPER_FRAMES

    def update(self, dt, world, jx, jy, events, jz=0.0):
        self.hit_cool -= dt
        self.turn_t -= dt
        self.spin += dt * 9 * self.speed
        dx, dy = jx - self.x, jy - self.y
        d = math.hypot(dx, dy)
        hunting = d < 9.0 and jz - world.floor_at(jx, jy) < 90
        if hunting and self.turn_t <= 0:
            want = math.atan2(dy, dx)
            diff = (want - self.heading + math.pi) % math.tau - math.pi
            self.heading += max(-2.5 * dt, min(2.5 * dt, diff))
        elif self.rng.random() < dt * 0.6:
            self.heading += self.rng.uniform(-1.2, 1.2)
        sp = (2.3 if hunting else 1.2) * self.speed
        self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp
        nx, ny = self.x + self.vx * dt, self.y + self.vy * dt
        if world.blocked(nx, ny, 0.0, 0.3):
            self.heading += self.rng.choice((1, -1)) * self.rng.uniform(1.6, 2.6)
            self.turn_t = 0.8
        else:
            self.x, self.y = nx, ny
            self.gait += sp * dt / CREEPER_STRIDE     # legs keep pace with the ground
        self.floor = world.floor_at(self.x, self.y)
        self.z = self.floor

    def flip(self):
        sx_ = self.vx - self.vy            # screen-x direction of travel
        return sx_ < 0

    def squash(self):
        return 1.0, 1.0 + 0.04 * math.sin(self.spin)


class Flyer:
    """Level 3: a ray that glides at altitude, then swoops at Jetman and climbs away."""
    kind = "flyer"

    def __init__(self, uid, x, y, seed, speed=1.0):
        self.uid, self.x, self.y = uid, x, y
        self.variant = "flyer"
        self.rng = random.Random(seed)
        self.heading = self.rng.uniform(0, math.tau)
        self.speed = speed
        self.cruise = self.rng.uniform(*FLYER_CRUISE)       # up where a high-flying Jetman goes
        self.z = self.cruise
        self.vx = self.vy = self.vz = 0.0
        self.floor, self.hit_cool, self.land_t = 0.0, 0.0, 9.0
        self.spin = self.rng.uniform(0, 10); self.spin_rate = 0.0
        self.mode, self.mode_t = "glide", self.rng.uniform(1, 4)

    def update(self, dt, world, jx, jy, events, jz=0.0):
        self.hit_cool -= dt
        self.mode_t -= dt
        self.spin += dt * (7 if self.mode == "swoop" else 4)
        dx, dy = jx - self.x, jy - self.y
        d = math.hypot(dx, dy)
        if self.mode == "glide":
            if self.rng.random() < dt * 0.5:
                self.heading += self.rng.uniform(-1.0, 1.0)
            if d < 11 and self.mode_t <= 0:
                self.mode, self.mode_t = "swoop", 3.0
            sp, tz = 2.4, self.cruise
        elif self.mode == "swoop":
            want = math.atan2(dy, dx)
            diff = (want - self.heading + math.pi) % math.tau - math.pi
            self.heading += max(-2.2 * dt, min(2.2 * dt, diff))
            sp, tz = 4.6, jz + 30
            if self.mode_t <= 0 or self.hit_cool > 0.3:
                self.mode, self.mode_t = "climb", 1.8
        else:
            sp, tz = 3.4, min(300.0, self.cruise + 50)
            if self.mode_t <= 0:
                self.mode, self.mode_t = "glide", self.rng.uniform(1.5, 3.5)
        sp *= self.speed
        self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp
        self.x += self.vx * dt; self.y += self.vy * dt
        self.z += (tz - self.z) * min(1.0, dt * 1.6)
        self.floor = world.floor_at(self.x, self.y)
        self.z = max(self.z, self.floor + 35)

    def flip(self):
        return (self.vx - self.vy) < 0

    def squash(self):
        return 1.0, 0.72 + 0.28 * abs(math.sin(self.spin))          # wing beat


FLYER_CRUISE = (150.0, 255.0)   # px; was 110-190, which let Jetman cruise over them at ~205


# ---------------------------------------------------------------- the base's homing spores
SEEK_RANGE = 36.0               # tiles from the base: it starts launching when Jetman is this close
SEEK_CHARGE = 1.0               # s of glow at the dome's top before each launch (a fair warning)
GUARD_R = (1.4, 3.0)            # tiles: guard spores circle over the dome, above the force field...
GUARD_Z = (175.0, 235.0)        # ...at about the height you need to drop the bomb from
HUNT_RANGE = 13.0               # tiles: a guard peels off after Jetman when he comes this close
SPORE_FLOOR = 90.0              # px: the lowest a spore will drift above the ground (a walking Jetman is clear)
MAX_HUNTERS = 2                 # never more than two chasing him at once; the rest stay on guard
HUNT_LIFE = 7.0                 # s of chasing before a spore fizzles out on its own


def seeker_tuning(level):
    """(launch interval s, most alive at once, speed tiles/s, turn rad/s) for a level.
    Jetman flies at 6.5 tiles/s and walks at 3, so a spore can always be outrun in the air."""
    n = level - 1
    return (max(1.8, 2.8 - 0.25 * n), min(5, 2 + (n + 1) // 2), min(3.4, 2.4 + 0.2 * n), min(2.0, 1.4 + 0.1 * n))


class Seeker:
    """Fired from the top of the force field. It rises out and circles the base as a guard;
    when Jetman comes close it peels off and drifts after his chest, turning only slowly,
    so you can dodge it, outrun it or shoot it. It glows and warbles so you know it's there."""
    kind = "seeker"
    variant = "seeker"
    hunting = 0                  # how many are chasing him right now (the campaign counts them each frame)

    def __init__(self, uid, bx, by, z, ang, level, rng_seed=0, armed=False):
        self.uid, self.bx, self.by = uid, bx, by
        self.x, self.y, self.z = bx, by, z
        _, _, self.speed, self.turn = seeker_tuning(level)
        self.rng = random.Random(rng_seed)
        self.orbit = ang                                  # angle round the base it heads for
        self.orbit_dir = self.rng.choice((1, -1))
        self.guard_z = self.rng.uniform(*GUARD_Z)
        self.guard_r = self.rng.uniform(*GUARD_R)
        self.heading = ang
        self.mode = "rise"
        self.vx = self.vy = 0.0
        self.vz = 150.0
        self.age, self.hunt_t = 0.0, 0.0
        self.spin, self.spin_rate = 0.0, 8.0
        self.floor, self.hit_cool, self.land_t = 0.0, 0.0, 9.0
        self.chirp_t = 0.3
        self.life = 1e9
        self.last_j, self.jv = None, [0.0, 0.0]
        self.calm_t = 0.0                                 # s before a regrouped guard will hunt again
        if armed:                                         # already on guard when the level starts
            self.mode, self.age = "guard", 2.0
            self.x, self.y = bx + math.cos(ang) * self.guard_r, by + math.sin(ang) * self.guard_r
            self.z, self.vz = self.guard_z, 0.0
            self.heading = ang + self.orbit_dir * math.pi / 2

    def steer_to(self, tx, ty, dt, turn):
        want = math.atan2(ty - self.y, tx - self.x)
        diff = (want - self.heading + math.pi) % math.tau - math.pi
        self.heading += max(-turn * dt, min(turn * dt, diff))

    def update(self, dt, world, jx, jy, events, jz=0.0):
        self.age += dt
        self.hit_cool -= dt
        self.spin += self.spin_rate * dt
        if self.age >= self.life:
            events.append(("fizzle", self))
            return
        dj = math.hypot(jx - self.x, jy - self.y)
        if self.last_j is not None and dt > 0:            # watch how he's moving, to head him off a little
            k = min(1.0, dt * 4)
            self.jv[0] += ((jx - self.last_j[0]) / dt - self.jv[0]) * k
            self.jv[1] += ((jy - self.last_j[1]) / dt - self.jv[1]) * k
        self.last_j = (jx, jy)
        if self.mode == "rise":
            self.vz = 150.0
            if self.age > 0.45:
                self.mode = "guard"
        if self.mode == "guard":
            self.orbit += self.orbit_dir * 0.55 * dt
            tx, ty = self.bx + math.cos(self.orbit) * self.guard_r, self.by + math.sin(self.orbit) * self.guard_r
            self.steer_to(tx, ty, dt, 3.0)
            sp = self.speed * min(1.0, math.hypot(tx - self.x, ty - self.y) / 1.5 + 0.35)
            tz = self.guard_z + 10 * math.sin(self.age * 1.7)
            self.calm_t -= dt
            if dj < HUNT_RANGE and self.age > 1.2 and self.calm_t <= 0 and Seeker.hunting < MAX_HUNTERS:
                self.mode, self.life = "hunt", self.age + HUNT_LIFE
                Seeker.hunting += 1
        elif self.mode == "hunt":
            lead = min(1.0, dj / (self.speed + 6.5)) * 0.6
            self.steer_to(jx + self.jv[0] * lead, jy + self.jv[1] * lead, dt, self.turn)
            sp = self.speed
            tz = jz + 36                                         # aim the body at his chest
        else:
            sp, tz = self.speed * 0.5, None
        self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp
        self.x += self.vx * dt; self.y += self.vy * dt
        if tz is not None:
            self.vz += (max(-70.0, min(70.0, (tz - self.z) * 1.6)) - self.vz) * min(1.0, dt * 3)
        self.z += self.vz * dt
        self.floor = world.floor_at(self.x, self.y)
        low = SPORE_FLOOR if self.mode != "rise" else 8.0
        if self.z < self.floor + low:                    # spores float: they won't come down to the ground,
            self.z = self.floor + low                    # so landing is a way to duck under one
            self.vz = max(self.vz, 0.0)
        self.chirp_t -= dt
        if self.chirp_t <= 0:
            self.chirp_t = 0.45 if self.mode == "hunt" else 0.9      # warbles faster when it's after you
            events.append(("chirp", self))

    def squash(self):
        p = 1.0 + 0.06 * math.sin(self.age * 14)                    # a nervous throb
        return p, p


LEVEL_MIX = {1: [("spore", 1.0)], 2: [("creeper", 0.75), ("spore", 0.25)], 3: [("flyer", 0.6), ("creeper", 0.4)]}


def level_kinds(level):
    if level in LEVEL_MIX:
        return LEVEL_MIX[level]
    return [("spore", 0.3), ("creeper", 0.35), ("flyer", 0.35)]


def level_new_kind(level):
    """The alien type that is new on this level (for the level card)."""
    return {1: "spore", 2: "creeper", 3: "flyer"}.get(level)


def chunk_aliens_level(ci, cj, level):
    """Deterministic spawn list for a chunk on a level: (uid, x, y, kind, variant, seed)."""
    r = wd.chunk_rng(ci, cj, 21 + 97 * level)
    mix = level_kinds(level)
    out = []
    for i in range(r.choice((0, 0, 1, 1, 1, 2, 2))):
        u = r.random(); acc = 0.0; kind = mix[-1][0]
        for k, w in mix:
            acc += w
            if u < acc:
                kind = k; break
        out.append(((level, ci, cj, i), ci * wd.CHUNK + r.uniform(1, wd.CHUNK - 1), cj * wd.CHUNK + r.uniform(1, wd.CHUNK - 1),
                    kind, r.choice(NAMES), r.randrange(1 << 30)))
    return out


def make_alien(uid, x, y, kind, variant, seed, level):
    speed = 1.0 + 0.15 * max(0, level - 3)
    if kind == "creeper":
        return Creeper(uid, x, y, seed, speed)
    if kind == "flyer":
        return Flyer(uid, x, y, seed, speed)
    return Alien(uid, x, y, variant, seed)
