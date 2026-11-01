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
    targetable = harmful = True

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
    for i in range(r.choice((0, 0, 1, 1, 1, 2, 2) if level < 4 else (0, 1, 1, 1, 2, 2, 2, 3))):
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
    "spore":   (None,           None,     100,    10,          None,            0.55,          22),
    "creeper": ("creeper.png",  78,       150,    12,          (200, 110, 40),  0.7,           14),
    "flyer":   ("flyer.png",    104,      250,    15,          (70, 200, 220),  0.75,          16),
    "seeker":  (None,           None,     50,     12,          None,            0.5,           14),
    "worm":    ("worm.png",     60,       200,    16,          (190, 120, 60),  0.6,           44),
    "leech":   ("leech.png",    56,       300,    5,           (90, 220, 150),  0.55,          22),
    "beetle":  ("beetle.png",   100,      350,    12,          (170, 210, 60),  0.7,           14),
    "mine":    (None,           None,     25,     0,           (170, 230, 70),  0.0,           7),
}
CREEPER_SHEET, CREEPER_FRAMES, CREEPER_PAD = "creeper_walk.png", 8, 6
CREEPER_STRIDE = 0.42        # tiles travelled per full scuttle cycle (keeps the feet from sliding)
KIND_NAMES = {"spore": "Spore Hoppers", "creeper": "Rock Creepers", "flyer": "Void Rays", "seeker": "Seeker Spores",
              "worm": "Crater Worms", "leech": "Glow Leeches", "beetle": "Mine Beetles", "mine": "Spore Mines"}
KIND_TIPS = {      # one line for the level card and the level's intro banner
    "creeper": "They scuttle along the ground. Shoot them from the ground, too.",
    "flyer": "They glide high and swoop. Meet them at their height.",
    "worm": "They burrow under you and burst out. Watch for the moving mound, then shoot them when they rear up.",
    "leech": "They latch on and drain your stamina. Fire the jetpack hard to shake one off, then shoot it.",
    "beetle": "They drop glowing mines where you are heading. Stay airborne, or shoot the mines from a distance.",
}


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
        if kind == "creeper":
            ay = h - 2 - self.pad * qy
        elif kind == "worm":                       # ground contact: the middle of its rubble ring
            ay = h - 9 * qy
        else:                                      # floating: the point under the body's centre
            ay = h - h * 0.35
        out = (s, w / 2, ay)
        self.cache[k] = out
        return out


class Creeper:
    """Level 2: a low armoured crawler. It scuttles over the ground, can't climb the rover or
    boulders (it turns away), and hunts Jetman when he's on or near the ground."""
    kind = "creeper"
    targetable = harmful = True

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
    targetable = harmful = True

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
    targetable = harmful = True
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


# ---------------------------------------------------------------- level 4, 5 and 6 aliens
WORM_RISE, WORM_UP, WORM_DIVE = 0.55, 1.7, 0.35     # s: rumble warning, reared up, sinking back
WORM_REACH = 1.15                                   # tiles: the bite as it bursts out
WORM_ALT = 60.0                                     # px: hover higher than this and it can't reach you


class Worm:
    """Level 4: a Crater Worm. It tunnels under the regolith (a travelling mound of rubble:
    you can't shoot it there and it can't hurt you), follows a Jetman who stays low, then
    rumbles for a moment and bursts out beneath him. Reared up it can be shot, until it dives."""
    kind = "worm"
    variant = "worm"

    def __init__(self, uid, x, y, seed, speed=1.0):
        self.uid, self.x, self.y, self.z = uid, x, y, 0.0
        self.rng = random.Random(seed)
        self.heading = self.rng.uniform(0, math.tau)
        self.speed = speed
        self.vx = self.vy = self.vz = 0.0
        self.floor, self.hit_cool, self.land_t = 0.0, 0.0, 9.0
        self.spin, self.spin_rate = self.rng.uniform(0, 10), 0.0
        self.state, self.t = "under", 0.0
        self.cool = self.rng.uniform(1.0, 3.0)
        self.rise = 0.0                          # 0 buried .. 1 fully reared up
        self.face = 1.0
        self.rubble_t = 0.0
        self.targetable = self.harmful = False

    def update(self, dt, world, jx, jy, events, jz=0.0):
        self.hit_cool -= dt
        self.t += dt
        self.spin += dt * 6
        dx, dy = jx - self.x, jy - self.y
        d = math.hypot(dx, dy)
        low = jz - world.floor_at(jx, jy) < WORM_ALT
        if self.state == "under":
            self.cool -= dt
            hunting = d < 14.0 and low
            if hunting:
                want = math.atan2(dy, dx)
                diff = (want - self.heading + math.pi) % math.tau - math.pi
                self.heading += max(-2.2 * dt, min(2.2 * dt, diff))
            elif self.rng.random() < dt * 0.5:
                self.heading += self.rng.uniform(-1.2, 1.2)
            sp = (2.5 if hunting else 1.0) * self.speed
            self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp
            nx, ny = self.x + self.vx * dt, self.y + self.vy * dt
            if world.blocked(nx, ny, 0.0, 0.3):              # tunnels round boulders, the rover and the base
                self.heading += self.rng.choice((1, -1)) * self.rng.uniform(1.4, 2.4)
            else:
                self.x, self.y = nx, ny
            self.rubble_t -= dt
            if self.rubble_t <= 0:
                self.rubble_t = 0.09
                events.append(("rubble", self))
            if self.cool <= 0 and ((hunting and d < 1.5) or (not hunting and self.rng.random() < dt * 0.12)):
                self.state, self.t = "rise", 0.0
                events.append(("rumble", self))
        elif self.state == "rise":                           # the warning: the mound heaves and rumbles
            if self.t >= WORM_RISE:
                self.state, self.t = "up", 0.0
                self.hit_cool = 0.0
                events.append(("erupt", self))
        elif self.state == "up":
            self.rise = min(1.0, self.t / 0.16)
            if abs(dx - dy) > 0.2:
                self.face = 1.0 if dx - dy > 0 else -1.0     # rears towards him on screen
            if self.t >= WORM_UP:
                self.state, self.t = "dive", 0.0
                events.append(("dive", self))
        elif self.state == "dive":
            self.rise = max(0.0, 1.0 - self.t / WORM_DIVE)
            if self.t >= WORM_DIVE:
                self.state, self.t, self.rise = "under", 0.0, 0.0
                self.cool = self.rng.uniform(2.0, 3.5)
        self.targetable = self.state in ("up", "dive") and self.rise > 0.35
        self.harmful = self.state == "up"
        self.floor = world.floor_at(self.x, self.y)
        self.z = self.floor

    def flip(self):
        return self.face < 0                     # the painting faces right (its maw opens east)

    def squash(self):
        if self.state == "up":
            return 1.0, 1.0 + 0.05 * math.sin(self.spin * 1.7)
        return 1.0, 1.0


LEECH_DRAIN = 6.0          # stamina per second while one is latched on
LEECH_SHAKE = 1.0          # s of jetpack thrust that shakes a latched leech off
LEECH_DAZE = 2.6           # s it drifts, stunned, after being shaken off


class Leech:
    """Level 5: a Glow Leech. A slow, drifting jellyfish that homes in on Jetman at chest height.
    If it touches him it latches on and drains his stamina until he shakes it off with a good
    burst of jetpack (or lands on the rover roof). Shaken off, it tumbles dazed and is easy to hit."""
    kind = "leech"
    variant = "leech"

    def __init__(self, uid, x, y, seed, speed=1.0):
        self.uid, self.x, self.y = uid, x, y
        self.rng = random.Random(seed)
        self.heading = self.rng.uniform(0, math.tau)
        self.speed = speed
        self.hover = self.rng.uniform(45.0, 120.0)
        self.z = self.hover
        self.vx = self.vy = self.vz = 0.0
        self.floor, self.hit_cool, self.land_t = 0.0, 0.0, 9.0
        self.spin, self.spin_rate = self.rng.uniform(0, 10), 0.0
        self.state, self.t = "drift", 0.0
        self.shake = 0.0
        self.glub_t = self.rng.uniform(1, 3)
        self.targetable = self.harmful = True

    def update(self, dt, world, jx, jy, events, jz=0.0):
        self.hit_cool -= dt
        self.spin += dt * 3.0
        self.t += dt
        if self.state == "latched":                  # the game carries it about on his back
            return
        dx, dy = jx - self.x, jy - self.y
        d = math.hypot(dx, dy)
        fl = world.floor_at(self.x, self.y)
        if self.state == "dazed":
            sp, tz = 0.6, fl + 22
            self.heading += dt * 2.0
            if self.t >= LEECH_DAZE:
                self.state, self.t = "drift", 0.0
        elif d < 13.0:
            want = math.atan2(dy, dx)
            diff = (want - self.heading + math.pi) % math.tau - math.pi
            self.heading += max(-2.0 * dt, min(2.0 * dt, diff))
            sp = 2.9 * self.speed
            tz = max(fl + 30, min(fl + 250, jz + 40))
        else:
            if self.rng.random() < dt * 0.4:
                self.heading += self.rng.uniform(-1.0, 1.0)
            sp, tz = 0.8, fl + self.hover
        self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp
        nx, ny = self.x + self.vx * dt, self.y + self.vy * dt
        if world.blocked(nx, ny, self.z, 0.25):
            self.heading += math.pi * 0.6
        else:
            self.x, self.y = nx, ny
        self.vz += ((tz + 8 * math.sin(self.spin)) - self.z) * 1.4 * dt - self.vz * min(1.0, dt * 2.5)
        self.z += self.vz * dt
        self.floor = world.floor_at(self.x, self.y)
        self.z = max(self.z, self.floor + 12)
        self.targetable = True
        self.harmful = self.state == "drift"
        self.glub_t -= dt
        if self.glub_t <= 0:
            self.glub_t = self.rng.uniform(2.0, 3.5)
            events.append(("glub", self))

    def latch(self):
        self.state, self.t, self.shake = "latched", 0.0, 0.0
        self.targetable = self.harmful = False

    def release(self, nx, ny):
        self.state, self.t = "dazed", 0.0
        self.heading = math.atan2(ny, nx)
        self.x += nx * 0.7; self.y += ny * 0.7
        self.vz = 60.0
        self.hit_cool = 2.0
        self.targetable, self.harmful = True, False

    def flip(self):
        return False

    def squash(self):
        p = math.sin(self.spin * (2.6 if self.state == "latched" else 1.4))   # the bell pulses as it swims (or feeds)
        return 1.0 + 0.06 * p, 1.0 - 0.07 * p


BEETLE_CRUISE = (80.0, 125.0)   # px: below a cruising Jetman, above a walking one
MINE_ARM, MINE_FUSE, MINE_LIFE = 1.0, 0.45, 22.0
MINE_TRIGGER, MINE_TRIGGER_Z = 1.1, 70.0     # tiles / px: how close sets a mine off
MINE_BLAST, MINE_BLAST_Z = 1.7, 95.0         # tiles / px: reach of the blast
MINE_HIT = 22.0                              # stamina
MINES_PER_BEETLE = 3


class Beetle:
    """Level 6: a Mine Beetle. Buzzes about at low altitude; when Jetman is about it flies to
    where he is heading and drops a glowing spore mine there, then veers away. A mine arms
    after a second, blinks, and goes off when he comes close to the ground near it."""
    kind = "beetle"
    variant = "beetle"

    def __init__(self, uid, x, y, seed, speed=1.0):
        self.uid, self.x, self.y = uid, x, y
        self.rng = random.Random(seed)
        self.heading = self.rng.uniform(0, math.tau)
        self.speed = speed
        self.cruise = self.rng.uniform(*BEETLE_CRUISE)
        self.z = self.cruise
        self.vx = self.vy = self.vz = 0.0
        self.floor, self.hit_cool, self.land_t = 0.0, 0.0, 9.0
        self.spin, self.spin_rate = self.rng.uniform(0, 10), 0.0
        self.mode, self.mode_t = "patrol", 0.0
        self.lay_cool = self.rng.uniform(1.0, 2.5)
        self.buzz_t = self.rng.uniform(0.2, 1.0)
        self.mines = 0                       # how many of its mines are still about (the game keeps count)
        self.last_j, self.jv = None, [0.0, 0.0]
        self.targetable = self.harmful = True

    def update(self, dt, world, jx, jy, events, jz=0.0):
        self.hit_cool -= dt
        self.mode_t -= dt
        self.lay_cool -= dt
        self.spin += dt * 22
        if self.last_j is not None and dt > 0:
            k = min(1.0, dt * 3)
            self.jv[0] += ((jx - self.last_j[0]) / dt - self.jv[0]) * k
            self.jv[1] += ((jy - self.last_j[1]) / dt - self.jv[1]) * k
        self.last_j = (jx, jy)
        dx, dy = jx - self.x, jy - self.y
        d = math.hypot(dx, dy)
        if self.mode == "patrol":
            if self.rng.random() < dt * 0.7:
                self.heading += self.rng.uniform(-1.3, 1.3)
            sp = 1.6
            if d < 13.0 and self.mode_t <= 0:
                self.mode = "attack"
        if self.mode == "attack":
            tx, ty = jx + self.jv[0] * 1.1, jy + self.jv[1] * 1.1      # where he'll be in a second
            want = math.atan2(ty - self.y, tx - self.x)
            diff = (want - self.heading + math.pi) % math.tau - math.pi
            self.heading += max(-2.6 * dt, min(2.6 * dt, diff))
            sp = 3.3
            if (math.hypot(tx - self.x, ty - self.y) < 2.5 or d < 2.0) and self.lay_cool <= 0:
                if self.mines < MINES_PER_BEETLE:
                    events.append(("lay", self))
                self.lay_cool = 2.6
                self.mode, self.mode_t = "away", 1.3
                self.heading += self.rng.choice((1, -1)) * 1.3
            elif d > 16.0:
                self.mode = "patrol"
        elif self.mode == "away":
            sp = 3.0
            if self.mode_t <= 0:
                self.mode = "attack" if d < 13.0 else "patrol"
        sp *= self.speed
        self.vx, self.vy = math.cos(self.heading) * sp, math.sin(self.heading) * sp
        self.x += self.vx * dt; self.y += self.vy * dt
        self.floor = world.floor_at(self.x, self.y)
        tz = self.floor + self.cruise + 10 * math.sin(self.spin * 0.13)
        if self.mode == "attack" and d < 7.0:            # closing in: it comes up (or down) to his height
            tz = max(self.floor + 55, min(self.floor + 260, jz + 6))
        self.z += (tz - self.z) * min(1.0, dt * 1.6)
        self.z = max(self.z, self.floor + 55)
        self.buzz_t -= dt
        if self.buzz_t <= 0:
            self.buzz_t = 0.55
            events.append(("buzz", self))

    def flip(self):
        return (self.vx - self.vy) < 0

    def squash(self):
        return 0.88 + 0.12 * abs(math.sin(self.spin)), 1.0          # wing beat (vertical)


class Mine:
    """A Mine Beetle's spore mine: falls, lands, arms, blinks; goes off near Jetman (or when shot)."""
    kind = "mine"
    variant = "mine"
    harmful = False

    def __init__(self, uid, x, y, z, owner=None):
        self.uid, self.x, self.y, self.z = uid, x, y, z
        self.owner = owner
        self.vx = self.vy = 0.0
        self.vz = -20.0
        self.floor, self.hit_cool, self.land_t = 0.0, 0.0, 9.0
        self.spin, self.spin_rate = 0.0, 0.0
        self.state, self.t, self.age = "fall", 0.0, 0.0
        self.targetable = False

    def update(self, dt, world, jx, jy, events, jz=0.0):
        self.age += dt
        self.t += dt
        self.spin += dt
        if self.state == "fall":
            self.vz -= 300.0 * dt
            self.z += self.vz * dt
            self.floor = world.floor_at(self.x, self.y)
            if self.z <= self.floor:
                self.z, self.state, self.t, self.land_t = self.floor, "landed", 0.0, 0.0
                events.append(("thud", self))
        elif self.state == "landed":
            self.land_t += dt
            if self.t >= MINE_ARM:
                self.state, self.t = "armed", 0.0
                events.append(("arm", self))
        elif self.state == "armed":
            if (jx - self.x) ** 2 + (jy - self.y) ** 2 < MINE_TRIGGER ** 2 and jz - self.z < MINE_TRIGGER_Z:
                self.state, self.t = "fuse", 0.0
                events.append(("fuse", self))
            elif self.age > MINE_LIFE:
                events.append(("expire", self))
        elif self.state == "fuse" and self.t >= MINE_FUSE:
            self.state = "gone"
            events.append(("blast", self))
        self.targetable = self.state in ("landed", "armed", "fuse")

    def blink(self):
        """0..1 brightness of its warning light."""
        if self.state == "fuse":
            return 1.0 if int(self.t * 16) % 2 == 0 else 0.25
        if self.state == "armed":
            return 1.0 if (self.t % 1.1) < 0.18 else 0.2
        return 0.15

    def flip(self):
        return False

    def squash(self):
        if self.land_t < 0.2:
            k = math.sin(self.land_t / 0.2 * math.pi)
            return 1 + 0.2 * k, 1 - 0.2 * k
        return 1.0, 1.0


PAINTED_KINDS = ("creeper", "flyer", "worm", "leech", "beetle")
LEVEL_MIX = {1: [("spore", 1.0)], 2: [("creeper", 0.75), ("spore", 0.25)], 3: [("flyer", 0.6), ("creeper", 0.4)],
             4: [("worm", 0.4), ("creeper", 0.2), ("flyer", 0.25), ("spore", 0.15)],
             5: [("leech", 0.35), ("worm", 0.2), ("flyer", 0.15), ("creeper", 0.15), ("spore", 0.15)],
             6: [("beetle", 0.3), ("leech", 0.2), ("worm", 0.2), ("flyer", 0.15), ("creeper", 0.15)]}
NEW_KIND = {1: "spore", 2: "creeper", 3: "flyer", 4: "worm", 5: "leech", 6: "beetle"}


def level_kinds(level):
    if level in LEVEL_MIX:
        return LEVEL_MIX[level]
    return [("beetle", 0.2), ("leech", 0.2), ("worm", 0.2), ("flyer", 0.15), ("creeper", 0.15), ("spore", 0.1)]


def level_new_kind(level):
    """The alien type that is new on this level (for the level card and intro)."""
    return NEW_KIND.get(level)


def chunk_aliens_level(ci, cj, level):
    """Deterministic spawn list for a chunk on a level: (uid, x, y, kind, variant, seed)."""
    r = wd.chunk_rng(ci, cj, 21 + 97 * level)
    mix = level_kinds(level)
    out = []
    for i in range(r.choice((0, 0, 1, 1, 1, 2, 2) if level < 4 else (0, 1, 1, 1, 2, 2, 2, 3))):
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
    if kind == "worm":                    # never faster than a walking Jetman underground
        return Worm(uid, x, y, seed, min(1.16, 1.0 + 0.08 * max(0, level - 4)))
    if kind == "leech":
        return Leech(uid, x, y, seed, 1.0 + 0.08 * max(0, level - 5))
    if kind == "beetle":
        return Beetle(uid, x, y, seed, 1.0 + 0.08 * max(0, level - 6))
    return Alien(uid, x, y, variant, seed)
