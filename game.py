#!/usr/bin/env python3
"""Moon Sweeper.

An endless, procedurally generated lunar surface in strict 2:1 isometric,
with a jetpacking Jetman under tank-style controls, and spiky aliens that
bounce about the surface for him to sweep up with his laser.

Run:            python3 game.py            (1280x720 window; F toggles full screen, Esc quits)
                python3 game.py --fullscreen
Screenshots:    python3 game.py --shot NAME [SCENE]   -> ../lunar_poc/NAME.png
                python3 game.py --facings NAME        -> ../lunar_poc/NAME.png
Benchmark:      python3 game.py --bench
"""
import math
import os
import random
import sys
import threading
import time

if any(a in sys.argv for a in ("--shot", "--bench", "--facings", "--aliens", "--loadshot")):
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"

import numpy as np
import pygame

import world as wd
from world import CHUNK, CW, HH, HW, TH, TW, to_screen
import jetman as jm
import aliens as al
from campaign import Campaign
import rover as rv
import base as bs
import sfx as sx
from jetman import JetmanSprites

W, H = 1280, 720


def sprite_cache_path():
    """Cache file named after a hash of the sprite code, so any change rebuilds it."""
    import hashlib
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha1()
    for fn in ("jetman.py", "aliens.py", "rover.py", "base.py", os.path.join("assets", "esa_logo.png")):
        with open(os.path.join(here, fn), "rb") as f:
            h.update(f.read())
    return os.path.join(os.path.expanduser("~"), ".cache", "moon-sweeper", f"sprites-{h.hexdigest()[:12]}.pkl")


def load_sprite_cache(path):
    import pickle
    try:
        with open(path, "rb") as f:
            res = pickle.load(f)
        return res if len(res) == len(jm.all_keys()) + len(al.all_keys()) + 1 + bs.FRAMES else None
    except Exception:
        return None


def save_sprite_cache(path, res):
    import pickle
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for old in os.listdir(os.path.dirname(path)):          # drop stale versions
            if old.startswith("sprites-") and old != os.path.basename(path):
                os.remove(os.path.join(os.path.dirname(path), old))
        with open(path + ".tmp", "wb") as f:
            pickle.dump(res, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(path + ".tmp", path)
    except Exception:
        pass
FPS = 60
BLACK = (0, 0, 0)          # the sky: pure black. No stars, no moon.

# Movement (distances in tiles, heights in pixels)
WALK = 3.0                 # top speed on foot, tiles/s
WALK_ACC = 16.0            # how quickly feet reach that speed
AIR_ACC = 6.5              # jetpack's horizontal push, tiles/s^2
AIR_DRAG = 0.55            # per second: momentum carries in the air
FLY = 6.5                  # top air speed, tiles/s
TURN = 3.0                 # rad/s
GRAVITY = 430.0            # px/s^2
THRUST = 820.0
MAX_ALT = 320.0
LASER_SPEED = 30.0
LASER_COLS = [(255, 60, 80), (255, 210, 60), (90, 255, 120), (80, 200, 255), (230, 90, 255), (255, 255, 255)]
DIRS = ["SOUTH-EAST", "SOUTH", "SOUTH-WEST", "WEST", "NORTH-WEST", "NORTH", "NORTH-EAST", "EAST"]
START = (8.5, 8.5)
BEACON_CORRIDOR = 8.0     # tiles either side of the Jetman-to-base line that turn a beacon red


class Laser:
    def __init__(self, x, y, z, a, col):
        self.x, self.y, self.z, self.a = x, y, z, a
        self.x0, self.y0 = x, y
        self.t = 0.0
        self.col = col
        self.dead = 0.0


class Particle:
    __slots__ = ("x", "y", "z", "vx", "vy", "vz", "life", "age", "kind", "size")

    def __init__(self, x, y, z, vx, vy, vz, life, kind, size):
        self.x, self.y, self.z, self.vx, self.vy, self.vz = x, y, z, vx, vy, vz
        self.life, self.age, self.kind, self.size = life, 0.0, kind, size


class Game(Campaign):
    def __init__(self, screen, seed=wd.SEED, start=START, sound_log=False, online=False):
        self.sfx = sx.SFX(log=sound_log)
        self.screen = screen
        self.world = wd.World(seed, start)
        self.jet = JetmanSprites()
        self.x, self.y = start
        self.z = 0.0
        self.vx = self.vy = self.vz = 0.0
        self.a = math.pi / 4             # facing south
        self.cam = list(to_screen(self.x, self.y))
        self.lasers, self.parts = [], []
        self.cool = 0.0
        self.fire_t = 9.0
        self.thrusting = False
        self.dist = 0.0
        self.t = 0.0
        self.fps = 60.0
        self.laser_n = 0
        self.rng = random.Random(7)
        self.font = pygame.font.Font(None, 26)
        self.big = pygame.font.Font(None, 44)
        self.glow = self._glow_sprite()
        self.hidden = False
        self.frame_ms = []
        # every Jetman/alien frame is drawn in the worker processes, off the main
        # thread, then kept in a small disk cache so later start-ups are quick
        self.alien_spr = al.AlienSprites()
        self.rover_spr = None
        self.base_spr = bs.BaseSprites()
        self.field_hits = []          # force-field strikes: (x, y, z, age)
        self.beacon_spr = self._beacon_sprites()
        self.n_red = 0
        self.frame_res, self.cache_path = [], sprite_cache_path()
        cached = load_sprite_cache(self.cache_path)
        if cached:
            for res in cached:
                self.accept_frame(res)
            self.jet_jobs, self.frame_res = [], None
        else:
            self.jet_jobs = [self.world.sprite_pool.submit(jm.frame_bytes, k) for k in jm.all_keys()]
            self.jet_jobs += [self.world.sprite_pool.submit(al.frame_bytes, k) for k in al.all_keys()]
            self.jet_jobs.insert(0, self.world.sprite_pool.submit(rv.frame_bytes))
            self.jet_jobs[1:1] = [self.world.sprite_pool.submit(bs.frame_bytes, k) for k in bs.all_keys()]
        self.n_frames = len(self.jet_jobs)
        self.aliens = {}            # uid -> Alien
        self.spawned = set()        # chunks whose aliens are out
        self.killed = set()         # uids popped for good
        self.splats = []            # goo decals: [x, y, colour, age]
        self.hit_t = 9.0
        self.pops = 0
        self.campaign_init(online)

    @staticmethod
    def make_part(*a):
        return Particle(*a)

    # ------------------------------------------------------------ helpers
    def collect_frames(self):
        if self.jet_jobs:
            left = []
            for fu in self.jet_jobs:
                if fu.done():
                    res = fu.result()
                    self.accept_frame(res)
                    if self.frame_res is not None:
                        self.frame_res.append(res)
                else:
                    left.append(fu)
            self.jet_jobs = left
            if not left and self.frame_res:
                threading.Thread(target=save_sprite_cache, args=(self.cache_path, self.frame_res), daemon=True).start()
                self.frame_res = None

    def accept_frame(self, res):
        if res[0] == "rover":
            self.rover_spr = rv.make_sprite(res)
        elif isinstance(res[0], str) and res[0].startswith("base:"):
            self.base_spr.accept(res)
        elif isinstance(res[0][0], str):
            self.alien_spr.accept(res)
        else:
            jm.accept(self.jet.cache, res)

    @staticmethod
    def _beacon_sprites():
        """A beacon: footing plate, slim metal post, gold collar, lamp; plus red/green glows."""
        post = pygame.Surface((24, 56), pygame.SRCALPHA)
        ax, ay = 12, 48                                   # anchor: foot of the post on the ground
        pygame.draw.polygon(post, (18, 18, 22), [(ax - 9, ay), (ax, ay - 4.5), (ax + 9, ay), (ax, ay + 4.5)])
        pygame.draw.polygon(post, (92, 94, 100), [(ax - 8, ay), (ax, ay - 4), (ax + 8, ay), (ax, ay + 4)])
        pygame.draw.polygon(post, (64, 66, 72), [(ax - 8, ay), (ax, ay + 4), (ax, ay + 6), (ax - 8, ay + 2)])
        pygame.draw.polygon(post, (120, 122, 128), [(ax + 8, ay), (ax, ay + 4), (ax, ay + 6), (ax + 8, ay + 2)])
        pygame.draw.rect(post, (18, 18, 22), (ax - 3, ay - 36, 6, 37))
        pygame.draw.rect(post, (150, 154, 160), (ax - 2, ay - 35, 2, 35))
        pygame.draw.rect(post, (100, 104, 110), (ax, ay - 35, 2, 35))
        pygame.draw.rect(post, (18, 18, 22), (ax - 5, ay - 39, 10, 6))
        pygame.draw.rect(post, (200, 160, 56), (ax - 4, ay - 38, 4, 4))
        pygame.draw.rect(post, (150, 116, 36), (ax, ay - 38, 4, 4))
        out = {}
        for name, lamp, hi in (("red", (230, 40, 34), (255, 190, 170)), ("green", (40, 220, 70), (200, 255, 200))):
            s = post.copy()
            pygame.draw.circle(s, (18, 18, 22), (ax, ay - 43), 6)
            pygame.draw.circle(s, lamp, (ax, ay - 43), 5)
            pygame.draw.circle(s, hi, (ax - 1, ay - 44), 2)
            if pygame.display.get_surface():
                s = s.convert_alpha()
            glow = pygame.Surface((72, 72), pygame.SRCALPHA)
            for r in range(36, 0, -1):
                k = 1.5 * (1 - r / 36) ** 2.0
                pygame.draw.circle(glow, tuple(min(255, int(c * k)) for c in lamp), (36, 36), r)
            pool = pygame.Surface((64, 32), pygame.SRCALPHA)       # soft light pooled on the ground, 2:1
            for r in range(32, 0, -1):
                k = 0.5 * (1 - r / 32) ** 1.6
                pygame.draw.ellipse(pool, tuple(int(c * k) for c in lamp), (32 - r, 16 - r / 2, r * 2, r))
            out[name] = (s, ax, ay, glow, pool)
        return out

    @staticmethod
    def _glow_sprite():
        s = pygame.Surface((64, 64), pygame.SRCALPHA)
        for r in range(32, 0, -1):
            a = int(255 * (1 - r / 32) ** 2.2)
            pygame.draw.circle(s, (a, a, a), (32, 32), r)
        return s

    def facing(self):
        return int(round((self.a % math.tau) / (math.pi / 4))) % 8

    def floor(self, x=None, y=None):
        return self.world.floor_at(self.x if x is None else x, self.y if y is None else y)

    # ------------------------------------------------------------ simulation
    def field_hit(self, x, y, z, quiet=False):
        bx, by = self.world.base
        e = math.sqrt(bs.field_e(bx, by, x, y, z))          # put the splash on the dome's skin
        x, y, z = bx + (x - bx) / e, by + (y - by) / e, z / e
        if not any((x - h[0]) ** 2 + (y - h[1]) ** 2 < 0.5 and h[3] < 0.2 for h in self.field_hits):
            self.field_hits.append((x, y, z, 0.0))
            if not quiet:
                self.sfx.play("bounce", 0.8)

    def field_check(self, ox, oy, oz):
        """The force field is a hard shell: Jetman can't pass into it (he bounces off with a flash).
        If he's somehow inside (e.g. a test scene) he may leave freely."""
        bx, by = self.world.base
        R, ZP = 0.3, 20.0
        if bs.field_e(bx, by, ox, oy, oz, R, ZP) < 1 or bs.field_e(bx, by, self.x, self.y, self.z, R, ZP) >= 1:
            return
        # outward normal at the contact, and bounce off it
        nx_, ny_, nz_ = (ox - bx) / (bs.FIELD_R + R) ** 2, (oy - by) / (bs.FIELD_R + R) ** 2, oz / rv.ZT / ((bs.FIELD_H + ZP) / rv.ZT) ** 2
        ln = math.sqrt(nx_ * nx_ + ny_ * ny_ + nz_ * nz_) or 1.0
        nx_, ny_, nz_ = nx_ / ln, ny_ / ln, nz_ / ln
        vn = self.vx * nx_ + self.vy * ny_ + self.vz / rv.ZT * nz_
        if vn < 0:
            k = 1.5
            self.vx -= k * vn * nx_; self.vy -= k * vn * ny_; self.vz -= k * vn * nz_ * rv.ZT
        self.vx += nx_ * 0.6; self.vy += ny_ * 0.6
        if nz_ > 0.3: self.vz = max(self.vz, 90.0)
        self.x, self.y, self.z = ox, oy, oz
        self.field_hit(ox, oy, oz + 36)

    def update(self, dt, keys):
        self.t += dt
        keys = self.campaign_keys(dt, keys)
        turn = keys.get("right", 0) - keys.get("left", 0)
        self.a += turn * TURN * dt
        move = keys.get("up", 0) - keys.get("down", 0) * 0.6
        fx, fy = math.cos(self.a), math.sin(self.a)
        fl = self.floor()
        alt = self.z - fl
        grounded = alt <= 0.5 and self.vz <= 0
        if grounded:
            tx, ty = fx * WALK * move, fy * WALK * move
            k = min(1.0, WALK_ACC * dt)
            self.vx += (tx - self.vx) * k; self.vy += (ty - self.vy) * k
        else:
            self.vx += fx * AIR_ACC * move * dt; self.vy += fy * AIR_ACC * move * dt
            drag = math.exp(-AIR_DRAG * dt * (1 if move else 2.2))
            self.vx *= drag; self.vy *= drag
            sp = math.hypot(self.vx, self.vy)
            if sp > FLY:
                self.vx *= FLY / sp; self.vy *= FLY / sp
        ox_, oy_, oz_ = self.x, self.y, self.z
        nx, ny = self.x + self.vx * dt, self.y + self.vy * dt
        if not self.world.blocked(nx, self.y, self.z): self.x = nx
        else: self.vx *= -0.2
        if not self.world.blocked(self.x, ny, self.z): self.y = ny
        else: self.vy *= -0.2
        if grounded:
            self.dist += math.hypot(self.vx, self.vy) * dt
        self.thrusting = bool(keys.get("jet", 0))
        acc = (THRUST if self.thrusting else 0) - GRAVITY
        # cushion: the pack automatically feathers a fast descent just above the ground
        if not self.thrusting and 0 < alt < 45 and self.vz < -140:
            acc += 900
        self.vz += acc * dt
        self.vz = max(-380, min(240, self.vz))
        self.z += self.vz * dt
        if self.z >= MAX_ALT: self.z, self.vz = MAX_ALT, min(0, self.vz)
        fl = self.floor()
        if self.z <= fl:
            if self.vz < -60 and alt > 0.5:
                self.puff(int(10 + min(30, -self.vz / 6)), fl)
            self.z, self.vz = fl, 0.0
        self.field_check(ox_, oy_, oz_)
        alt = self.z - self.floor()
        if self.thrusting and alt < 90 and self.rng.random() < 0.9:
            self.dust(alt, fl)
        # firing
        self.cool -= dt
        self.fire_t += dt
        if keys.get("fire") and self.cool <= 0:
            self.laser_n += 1
            self.lasers.append(Laser(self.x + fx * 0.75, self.y + fy * 0.75, self.z + 1.26 * 64 * 0.866 + 2, self.a,
                                     LASER_COLS[self.laser_n % len(LASER_COLS)]))
            self.cool = 0.13
            self.fire_t = 0.0
            self.sfx.play("phaser")
            self.vx -= fx * 0.25; self.vy -= fy * 0.25      # a little kick
        for L in self.lasers:
            L.t += dt
            if L.dead:
                L.dead += dt
                continue
            step = LASER_SPEED * dt
            for i in range(4):
                L.x += math.cos(L.a) * step / 4; L.y += math.sin(L.a) * step / 4
                hit = self.alien_hit(L)
                if hit:
                    L.dead = 1e-6
                    self.alien_killed(hit)
                    self.pop(hit)
                    break
                if self.world.blocked(L.x, L.y, L.z - 6, 0.02) or L.z < self.floor(L.x, L.y) + 4:
                    L.dead = 1e-6
                    self.sparks(L.x, L.y, L.z, L.col)
                    break
                if self.base_alive and bs.field_e(*self.world.base, L.x, L.y, L.z) < 1:
                    L.dead = 1e-6
                    self.field_hit(L.x, L.y, L.z, quiet=True)
                    break
        self.lasers = [L for L in self.lasers if L.t < 0.6 and L.dead < 0.08]
        self.field_hits = [(x, y, z, a + dt) for x, y, z, a in self.field_hits if a + dt < 0.5]
        for p in self.parts:
            p.age += dt
            p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt
            if p.kind == "dust":
                d = math.exp(-2.2 * dt); p.vx *= d; p.vy *= d; p.vz = p.vz * d - 30 * dt
                p.z = max(p.z, 0)
            elif p.kind == "frag":
                p.vz -= 260 * dt
                if p.z < 0:
                    p.z, p.vz = 0, -p.vz * 0.35; p.vx *= 0.6; p.vy *= 0.6
            else:
                if p.kind not in ("fire", "shock", "smoke"):
                    p.vz -= 300 * dt
        self.parts = [p for p in self.parts if p.age < p.life]
        self.update_aliens(dt)
        self.campaign_update(dt)
        # camera with look-ahead
        tx, ty = to_screen(self.x + self.vx * 0.35, self.y + self.vy * 0.35, self.z * 0.55)
        k = min(1.0, dt * 5)
        self.cam[0] += (tx - self.cam[0]) * k; self.cam[1] += (ty - self.cam[1]) * k

    def puff(self, n, fl):
        for _ in range(n):
            a = self.rng.uniform(0, math.tau); s = self.rng.uniform(0.8, 2.6)
            self.parts.append(Particle(self.x, self.y, fl + 2, math.cos(a) * s, math.sin(a) * s,
                                       self.rng.uniform(4, 28), self.rng.uniform(0.5, 1.0), "dust", self.rng.uniform(3, 7)))

    def dust(self, alt, fl):
        k = 1 - alt / 90
        for _ in range(1 + int(k * 3)):
            a = self.rng.uniform(0, math.tau); s = self.rng.uniform(1.5, 3.5) * (0.5 + k)
            r0 = 0.15 + alt / 200
            self.parts.append(Particle(self.x + math.cos(a) * r0, self.y + math.sin(a) * r0, fl + 1,
                                       math.cos(a) * s, math.sin(a) * s, self.rng.uniform(2, 14) * k,
                                       self.rng.uniform(0.35, 0.8), "dust", self.rng.uniform(2.5, 5.5) * (0.6 + k)))

    def sparks(self, x, y, z, col):
        for _ in range(14):
            a = self.rng.uniform(0, math.tau); s = self.rng.uniform(1, 4)
            self.parts.append(Particle(x, y, z, math.cos(a) * s, math.sin(a) * s, self.rng.uniform(40, 160),
                                       self.rng.uniform(0.15, 0.4), "spark", 2))
        self.parts.append(Particle(x, y, z, 0, 0, 0, 0.12, "flash", 1))

    # ------------------------------------------------------------ aliens
    def update_aliens(self, dt):
        fi, fj = math.floor(self.x / CHUNK), math.floor(self.y / CHUNK)
        # spawn: each chunk near Jetman releases its (deterministic) aliens once
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                k = (fi + di, fj + dj)
                if k in self.spawned:
                    continue
                self.spawned.add(k)
                for uid, x, y, kind, var, seed in al.chunk_aliens_level(*k, self.level):
                    if uid in self.killed or uid in self.aliens:
                        continue
                    if (x - self.x) ** 2 + (y - self.y) ** 2 < 9:      # never pop out on top of him
                        continue
                    if not self.world.blocked(x, y, 0, 0.35):
                        self.aliens[uid] = al.make_alien(uid, x, y, kind, var, seed, self.level)
        # despawn far away; let far chunks spawn again later so the population stays steady
        for uid, a in list(self.aliens.items()):
            if abs(a.x - self.x) > 2.6 * CHUNK or abs(a.y - self.y) > 2.6 * CHUNK:
                del self.aliens[uid]
        self.spawned = {k for k in self.spawned if abs(k[0] - fi) <= 2 and abs(k[1] - fj) <= 2}
        ev = []
        for a in self.aliens.values():
            a.update(dt, self.world, self.x, self.y, ev, self.z)
        for kind, a in ev:
            self.alien_sound("bounce", a)
            if (a.x - self.x) ** 2 + (a.y - self.y) ** 2 < 600:
                for _ in range(5):
                    ang = self.rng.uniform(0, math.tau); s = self.rng.uniform(0.5, 1.4)
                    self.parts.append(Particle(a.x, a.y, a.floor + 1, math.cos(ang) * s, math.sin(ang) * s,
                                               self.rng.uniform(2, 10), self.rng.uniform(0.3, 0.6), "dust",
                                               self.rng.uniform(2, 3.5)))
        # touching Jetman: hit flash, knockback and a bite out of his stamina
        self.hit_t += dt
        for a in self.aliens.values():
            dx, dy = self.x - a.x, self.y - a.y
            d = math.hypot(dx, dy)
            kd = al.KINDS[a.kind]
            body_z = a.z + kd[6]
            if self.mode == "play" and d < kd[5] and self.z - 10 < body_z < self.z + 100 and a.hit_cool <= 0 and self.hit_t > 0.9:
                self.hurt_stamina(kd[3])
                nx, ny = (dx / d, dy / d) if d > 1e-3 else (1.0, 0.0)
                self.vx += nx * 5.0; self.vy += ny * 5.0
                self.vz = max(self.vz, 170.0); self.z += 1
                a.vx, a.vy = -nx * 2.5, -ny * 2.5
                a.hit_cool = 0.8
                self.hit_t = 0.0
                self.sfx.play("hurt")
                self.sparks(a.x + nx * 0.3, a.y + ny * 0.3, self.z + 40, (255, 255, 255))
        # spin whirr on the nearest few aliens, attenuated by distance
        sp = []
        for a in self.aliens.values():
            v = sx.falloff(math.hypot(a.x - self.x, a.y - self.y))
            if v > 0:
                sp.append((v * min(1.0, abs(a.spin_rate) / 6), self.alien_pan(a)))
        sp.sort(key=lambda e: -e[0])
        self.sfx.update(dt, self.thrusting, sp[:sx.SPIN_VOICES])
        for s in self.splats:
            s[3] += dt
        self.splats = [s for s in self.splats if s[3] < 4.0]

    def alien_pan(self, a):
        sx_, _ = to_screen(a.x, a.y, 0)
        jx, _ = to_screen(self.x, self.y, 0)
        return sx.pan(W / 2 + (sx_ - jx), W)

    def alien_sound(self, name, a):
        self.sfx.play(name, sx.falloff(math.hypot(a.x - self.x, a.y - self.y)), self.alien_pan(a))

    def alien_hit(self, L):
        for a in self.aliens.values():
            # generous: a 0.75-tile radius and 55 px of height around the body (was 0.42 / 34)
            if (a.x - L.x) ** 2 + (a.y - L.y) ** 2 < 0.75 ** 2 and abs(L.z - (a.z + al.KINDS[a.kind][6])) < 55:
                return a
        return None

    def pop(self, a):
        """Splat: spike fragments, goo, sparks and a flash."""
        del self.aliens[a.uid]
        self.alien_sound("pop", a)
        self.killed.add(a.uid)
        self.pops += 1
        if a.kind == "spore":
            stalk, club, _ = al.VARIANTS[a.variant]
        else:
            stalk = club = al.KINDS[a.kind][4]
        z = a.z + al.KINDS[a.kind][6]
        for i in range(22):
            ang = self.rng.uniform(0, math.tau); s = self.rng.uniform(1.5, 4.5)
            p = Particle(a.x, a.y, z, math.cos(ang) * s, math.sin(ang) * s, self.rng.uniform(40, 220),
                         self.rng.uniform(0.6, 1.1), "frag", 0)
            p.size = (club if i % 2 else stalk, self.rng.uniform(0, math.tau), self.rng.uniform(-14, 14))
            self.parts.append(p)
        for _ in range(16):
            ang = self.rng.uniform(0, math.tau); s = self.rng.uniform(1, 3)
            self.parts.append(Particle(a.x, a.y, z, math.cos(ang) * s, math.sin(ang) * s, self.rng.uniform(30, 150),
                                       self.rng.uniform(0.2, 0.45), "spark", 2))
        self.parts.append(Particle(a.x, a.y, z, 0, 0, 0, 0.2, "flash", 1))
        self.parts.append(Particle(a.x, a.y, z, 0, 0, 0, 0.3, "ring", club))
        self.splats.append([a.x, a.y, club, 0.0, self.rng.random()])

    def shadow_at(self, x, y, z, fl=None):
        """Where a point's sun shadow lands: (x, y, floor) on whatever is under it."""
        if fl is None:
            fl = self.world.floor_at(x, y)
        if z - fl < 0.5:
            return x, y, fl                           # standing: the shadow starts at his feet
        for _ in range(2):
            d = max(0.0, z - fl)
            sx, sy = x - SHADOW_W * d, y + SHADOW_W * d
            fl = self.world.floor_at(sx, sy)
        return sx, sy, min(fl, z)

    def queue_shadow(self, surf, ax, ay, x, y, z, raised, fl=None):
        """Shadow on bare ground: draw now. On a raised top: return it for the depth sort."""
        sh, ox, oy = cast_shadow(surf, ax, ay)
        sx, sy, fl = self.shadow_at(x, y, z, fl)
        rx, ry = self.world.rover
        if fl > 0.5 and self.rover_spr and abs(fl - rv.ROOF_Z) < 1 and rv.in_footprint(rx, ry, sx, sy, 0.6):
            self.rover_split_shadow(sh, ox, oy, x, y, z, sx, sy, raised)
            return
        px, py = to_screen(sx, sy, fl)
        item = (sh, px - ox, py - oy)
        if fl > 0.5:
            raised.append((sx, sy, fl, item))
        else:
            self.screen.blit(sh, (item[1] - self.cam_draw[0], item[2] - self.cam_draw[1]))

    def rover_split_shadow(self, sh, ox, oy, x, y, z, sx, sy, raised):
        """A shadow landing on the rover roof: the part over the real roof face stays up
        there (clipped to it); the rest carries on down to the ground, further west, and
        is hidden by the rover body / its own shadow where the roof blocked the sun."""
        rx, ry = self.world.rover
        rpx, rpy = to_screen(rx, ry)
        w, h = sh.get_size()
        # roof part, clipped to the roof face
        px, py = to_screen(sx, sy, rv.ROOF_Z)
        mask, mx, my = rv.roof_mask()
        top = pygame.Surface((w, h), pygame.SRCALPHA); top.fill((255, 255, 255, 0))
        top.blit(mask, (rpx - mx - (px - ox), rpy - my - (py - oy)))
        top.blit(sh, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        raised.append((sx, sy, rv.ROOF_Z, (top, px - ox, py - oy)))
        # ground part, minus where the rover already shades the ground
        gx, gy = (x, y) if z < 0.5 else (x - SHADOW_W * z, y + SHADOW_W * z)
        if z < 0.5:
            return
        px, py = to_screen(gx, gy, self.world.floor_at(gx, gy))
        inv, ix, iy = rv.shadow_inverse()
        low = pygame.Surface((w, h), pygame.SRCALPHA); low.fill((255, 255, 255, 255))
        low.blit(inv, (rpx - ix - (px - ox), rpy - iy - (py - oy)))
        low.blit(sh, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        self.screen.blit(low, (px - ox - self.cam_draw[0], py - oy - self.cam_draw[1]))

    def draw_alien_shadows(self, cx, cy, raised):
        for a in self.aliens.values():
            gx, gy = to_screen(a.x, a.y, a.floor)
            gx -= cx; gy -= cy
            if not (-120 - (a.z - a.floor) * SHADOW_K < gx < W + 60 and -60 < gy < H + 60):
                continue
            if a.kind == "spore":
                surf, ax, ay = self.alien_spr.get(a.variant, int(a.spin) % al.SPIN_FRAMES)
            else:
                surf, ax, ay = self.paint.get(a.kind, a.flip(), *a.squash(), frame=a.frame() if a.kind == "creeper" else 0)
            self.queue_shadow(surf, ax, ay, a.x, a.y, a.z, raised, a.floor)

    def draw_splats(self, cx, cy):
        for x, y, col, age, seed in self.splats:
            px, py = to_screen(x, y)
            k = max(0.0, 1 - age / 4.0)
            r = random.Random(int(seed * 1e6))
            s = pygame.Surface((70, 36), pygame.SRCALPHA)
            for _ in range(9):
                ex, ey = r.uniform(-18, 18), r.uniform(-8, 8)
                rr = r.uniform(4, 10)
                pygame.draw.ellipse(s, col + (int(150 * k),), (35 + ex - rr, 18 + ey - rr / 2, rr * 2, rr))
            self.screen.blit(s, (px - cx - 35, py - cy - 18))

    def draw_alien(self, a, cx, cy):
        px, py = to_screen(a.x, a.y, a.z)
        px -= cx; py -= cy
        if not (-60 < px < W + 60 and -90 < py < H + 40):
            return None
        sx, sy = a.squash()
        if a.kind == "spore":
            surf, ax, ay = self.alien_spr.get(a.variant, int(a.spin) % al.SPIN_FRAMES, sx, sy)
        else:
            surf, ax, ay = self.paint.get(a.kind, a.flip(), sx, sy, a.frame() if a.kind == "creeper" else 0)
        pos = (px - ax, py - ay + (2 if a.land_t < 0.15 else 0))
        self.screen.blit(surf, pos)
        return surf, pos

    # ------------------------------------------------------------ world streaming
    def load_step(self):
        """One non-blocking slice of start-up work. Returns (ready, total, status)."""
        w = self.world
        self.stream()
        self.collect_frames()
        w.collect(4)
        w.collect_rocks(0.008)
        cx, cy = self.cam[0] - W / 2, self.cam[1] - H / 2
        gnd = self.visible_chunks(cx, cy)
        rks = self.visible_chunks(cx, cy, 260)
        for k in gnd:
            w.request_ground(*k)
        for k in rks:
            w.request_rocks(*k)
        f_ok = self.n_frames - len(self.jet_jobs)
        g_ok = sum(1 for k in gnd if w.ground_ready(*k))
        r_ok = sum(1 for k in rks if k in w.rock_done)
        total = self.n_frames + len(gnd) + len(rks)
        if g_ok < len(gnd):
            msg = "Preparing the lunar surface..."
        elif r_ok < len(rks):
            msg = "Scattering the boulders..."
        elif f_ok < self.n_frames:
            msg = "Suiting up Jetman and waking the aliens..."
        else:
            msg = "Ready for launch!"
        return f_ok + g_ok + r_ok, total, msg

    def draw_loading(self, ready, total, msg):
        sc = self.screen
        sc.fill(BLACK)
        t = self.big.render("Moon Sweeper", True, (222, 222, 218))
        sc.blit(t, t.get_rect(center=(W // 2, H // 2 - 60)))
        bw, bh = 520, 18
        x0, y0 = W // 2 - bw // 2, H // 2 - bh // 2
        pygame.draw.rect(sc, (58, 58, 60), (x0, y0, bw, bh), border_radius=4)
        fw = int(bw * ready / max(1, total))
        if fw > 0:
            pygame.draw.rect(sc, (176, 176, 172), (x0, y0, fw, bh), border_radius=4)
        pygame.draw.rect(sc, (120, 120, 122), (x0, y0, bw, bh), 1, border_radius=4)
        m = self.font.render(msg, True, (150, 150, 148))
        sc.blit(m, m.get_rect(center=(W // 2, H // 2 + 40)))
        p = self.font.render(f"{100 * ready // max(1, total)}%", True, (110, 110, 110))
        sc.blit(p, p.get_rect(center=(W // 2, H // 2 + 70)))

    def wait_ready(self, timeout=600):
        """Headless: block (politely) until start-up work is done."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            r, n, _ = self.load_step()
            if r >= n:
                return
            time.sleep(0.01)

    def visible_chunks(self, cx, cy, margin=140):
        us, vs = [], []
        for sx, sy in ((-margin, -margin), (W + margin, -margin), (-margin, H + margin + 120), (W + margin, H + margin + 120)):
            X, Y = sx + cx, sy + cy
            us.append((Y / HH + X / HW) / 2); vs.append((Y / HH - X / HW) / 2)
        i0, i1 = math.floor(min(us) / CHUNK), math.floor(max(us) / CHUNK)
        j0, j1 = math.floor(min(vs) / CHUNK), math.floor(max(vs) / CHUNK)
        out = []
        for ci in range(i0, i1 + 1):
            for cj in range(j0, j1 + 1):
                sx, sy = to_screen(ci * CHUNK, cj * CHUNK)
                if sx - CW / 2 - cx < W and sx + CW / 2 - cx > 0 and sy - cy < H + 120 and sy + CH_ - cy > -margin:
                    out.append((ci, cj))
        return out

    def stream(self):
        w = self.world
        ccx, ccy = self.cam
        u = (ccy / HH + ccx / HW) / 2; v = (ccy / HH - ccx / HW) / 2
        ci, cj = math.floor(u / CHUNK), math.floor(v / CHUNK)
        w.focus = (ci, cj)
        # also prefetch where we are heading (about 2.5 s ahead)
        ai, aj = math.floor((u + self.vx * 2.5) / CHUNK), math.floor((v + self.vy * 2.5) / CHUNK)
        if (ai, aj) != (ci, cj):
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    w.request_ground(ai + di, aj + dj)
        for r in (0, 1, 2):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if max(abs(di), abs(dj)) == r:
                        w.request_ground(ci + di, cj + dj)
        w.collect(2)
        # boulder pixels are built in the worker threads; turn a few into sprites each frame
        for r in (0, 1, 2):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if max(abs(di), abs(dj)) == r:
                        w.request_rocks(ci + di, cj + dj)
        w.collect_rocks(0.002)
        w.evict(ci, cj, 3)

    # ------------------------------------------------------------ drawing
    def draw(self):
        sc = self.screen
        sc.fill(BLACK)
        self.stream()
        self.collect_frames()
        cx, cy = self.cam[0] - W / 2, self.cam[1] - H / 2
        if self.shake > 0:
            k = self.shake ** 2 * 14
            cx += self.rng.uniform(-k, k); cy += self.rng.uniform(-k, k)
        vis = self.visible_chunks(cx, cy)
        for (ci, cj) in vis:
            sx, sy = to_screen(ci * CHUNK, cj * CHUNK)
            sc.blit(self.world.ground_surface(ci, cj), (sx - CW / 2 - cx, sy - cy))
        fl = self.floor()
        alt = max(0.0, self.z - fl)
        self.draw_splats(cx, cy)
        if self.rover_spr:
            sh, sx_, sy_ = self.rover_spr[3:]
            px, py = to_screen(*self.world.rover)
            sc.blit(sh, (px - cx - sx_, py - cy - sy_))
        bx, by = self.world.base
        BK = bx + by
        if self.base_spr.shadow:
            sh, sx_, sy_ = self.base_spr.shadow
            px, py = to_screen(bx, by)
            sc.blit(sh, (px - cx - sx_, py - cy - sy_))
        beacons = self.visible_beacons(cx, cy)
        for x, y, red in beacons:                     # light pooled on the ground round each lamp
            pool = self.beacon_spr["red" if red else "green"][4]
            px, py = to_screen(x, y)
            sc.blit(pool, (px - cx - 32, py - cy - 16), special_flags=pygame.BLEND_ADD)
        self.cam_draw = (cx, cy)
        raised = []                                   # shadows falling on the rover/base/boulder tops
        self.draw_alien_shadows(cx, cy, raised)
        self.draw_shadow(cx, cy, fl, alt, raised)
        # depth-sorted objects
        items = []
        rx, ry = self.world.rover
        RK = rx + ry

        def key(x, y, own, r=0.3):
            # iso box rule against the rover, so its long diagonal body sorts right
            rel = rv.relation(rx, ry, x, y, r)
            own = max(own, RK + 0.01) if rel > 0 else (min(own, RK - 0.01) if rel < 0 else own)
            rel = bs.relation(bx, by, x, y, r)
            return max(own, BK + 0.01) if rel > 0 else (min(own, BK - 0.01) if rel < 0 else own)
        for (ci, cj) in self.visible_chunks(cx, cy, 260):
            for rk in self.world.chunk_rocks(ci, cj):
                items.append((key(rk.x, rk.y, rk.x + rk.y, rk.size * 0.5), 0, rk))
        jd = key(self.x, self.y, self.x + self.y + 0.01)
        top = rv.height_at(rx, ry, self.x, self.y, 0.3)
        if top > 0 and self.z >= top * 0.9:
            jd = max(jd, RK + 0.02)                   # on (or just over) the rover: draw him on top
        top = bs.height_at(bx, by, self.x, self.y, 0.3)
        if top > 0 and self.z >= top * 0.9:
            jd = max(jd, BK + 0.02)
        items.append((jd, 1, None))
        if self.rover_spr:
            items.append((RK, 5, None))
        if self.base_spr.ready():
            items.append((BK, 6, None))
        if self.bomb.state in ("ground", "falling"):
            b_ = self.bomb
            items.append((key(b_.x, b_.y, b_.x + b_.y, 0.2), 9, None))
        for b in beacons:
            items.append((key(b[0], b[1], b[0] + b[1], 0.15), 7, b))
        for p in self.parts:
            if p.kind == "dust":
                items.append((key(p.x, p.y, p.x + p.y, 0.1), 3, p))
        for a in self.aliens.values():
            ak = key(a.x, a.y, a.x + a.y + 0.005, 0.28)
            if a.z >= rv.height_at(rx, ry, a.x, a.y, 0.28) * 0.9 and rv.in_footprint(rx, ry, a.x, a.y, 0.28):
                ak = max(ak, RK + 0.015)
            if a.z >= bs.height_at(bx, by, a.x, a.y, 0.28) * 0.9 and bs.in_footprint(bx, by, a.x, a.y, 0.28):
                ak = max(ak, BK + 0.015)
            items.append((ak, 4, a))
        for sx_, sy_, sfl, it in raised:
            k_ = key(sx_, sy_, sx_ + sy_, 0.2)
            if rv.height_at(rx, ry, sx_, sy_) > 0.5:
                k_ = max(k_, RK + 0.005)
            if bs.height_at(bx, by, sx_, sy_) > 0.5:
                k_ = max(k_, BK + 0.005)
            ci_, cj_ = int(sx_ // CHUNK), int(sy_ // CHUNK)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for rk in self.world.chunk_rocks(ci_ + di, cj_ + dj):
                        if (rk.x - sx_) ** 2 + (rk.y - sy_) ** 2 < (rk.size * 0.42) ** 2:
                            k_ = max(k_, key(rk.x, rk.y, rk.x + rk.y, rk.size * 0.5) + 0.005)
            items.append((k_, 8, it))               # just after the top it lies on
        items.sort(key=lambda t: t[0])
        jet_rect = None
        rover_drawn = False
        front = []
        watched = []      # aliens drawn so far: (surf, pos, rect, rocks drawn over them)
        for _, kind, obj in items:
            if kind == 0:
                px, py = to_screen(obj.x, obj.y)
                px -= cx; py -= cy
                rw = obj.size * TW * 0.7 + 12
                if px - rw > W or px + rw < 0 or py - obj.h - 40 > H or py + rw * 0.5 < 0:
                    continue
                surf, ox, oy = self.world.rock_sprite(obj)
                pos = (px - ox, py - oy)
                sc.blit(surf, pos)
                if jet_rect is not None:
                    front.append((surf, pos))
                if watched:
                    rr = pygame.Rect(pos, surf.get_size())
                    for w_ in watched:
                        if rr.colliderect(w_[2]):
                            w_[3].append((surf, pos))
            elif kind == 5:
                surf, ox, oy = self.rover_spr[:3]
                px, py = to_screen(rx, ry)
                pos = (px - cx - ox, py - cy - oy)
                if pos[0] < W and pos[0] + surf.get_width() > 0 and pos[1] < H and pos[1] + surf.get_height() > 0:
                    # lasers and sparks truly behind the rover go down first, so its body hides them
                    self.draw_lasers(cx, cy, behind_rover=True)
                    self.draw_parts(cx, cy, behind_rover=True)
                    rover_drawn = True
                    sc.blit(surf, pos)            # no x-ray outline: what's behind the rover is hidden
            elif kind == 6:
                surf, ox, oy = self.base_spr.get(self.t)
                px, py = to_screen(bx, by)
                pos = (px - cx - ox, py - cy - oy)
                fb, ff, fax, fay = self.base_spr.get_field(self.t)
                fpos = (px - cx - fax, py - cy - fay)
                field_on = fpos[0] < W and fpos[0] + fb.get_width() > 0 and fpos[1] < H and fpos[1] + fb.get_height() > 0
                if field_on:                      # far half of the dome, behind the base
                    sc.blit(fb, fpos, special_flags=pygame.BLEND_ADD)
                if pos[0] < W and pos[0] + surf.get_width() > 0 and pos[1] < H and pos[1] + surf.get_height() > 0:
                    sc.blit(surf, pos)
                if field_on:                      # near half, over the base
                    sc.blit(ff, fpos, special_flags=pygame.BLEND_ADD)
                if pos[0] < W and pos[0] + surf.get_width() > 0 and pos[1] < H and pos[1] + surf.get_height() > 0:
                    if jet_rect is not None:
                        front.append((surf, pos))
                    rr = pygame.Rect(pos, surf.get_size())
                    for w_ in watched:
                        if rr.colliderect(w_[2]):
                            w_[3].append((surf, pos))
            elif kind == 7:
                x, y, red = obj
                s, ax, ay, glow, _ = self.beacon_spr["red" if red else "green"]
                px, py = to_screen(x, y)
                sc.blit(s, (px - cx - ax, py - cy - ay))
                pulse = 0.8 + 0.2 * math.sin(self.t * 4.0 + x * 0.7 + y * 1.3)
                g = glow if pulse > 0.9 else self.beacon_dim(red)
                sc.blit(g, (px - cx - 36, py - cy - ay + 5 - 36), special_flags=pygame.BLEND_ADD)
            elif kind == 9:
                self.draw_bomb_obj(cx, cy)
            elif kind == 8:
                sc.blit(obj[0], (obj[1] - cx, obj[2] - cy))
            elif kind == 1:
                jet_rect = self.draw_jetman(cx, cy, alt)
            elif kind == 4:
                d = self.draw_alien(obj, cx, cy)
                if d:
                    watched.append((d[0], d[1], pygame.Rect(d[1], d[0].get_size()), []))
            else:
                self.draw_dust(obj, cx, cy)
        self.draw_silhouette(front, jet_rect)
        for surf, pos, rect, fr in watched:
            if fr:
                self.silhouette(surf, pos, fr, (255, 235, 150, 80), (255, 240, 190))
        self.draw_lasers(cx, cy, behind_rover=False)
        self.draw_parts(cx, cy, behind_rover=False)
        if not rover_drawn:                                 # rover off screen: nothing to hide behind
            self.draw_lasers(cx, cy, behind_rover=True)
            self.draw_parts(cx, cy, behind_rover=True)
        for hx, hy, hz, ht in self.field_hits:           # force-field strikes flash over everything
            i_ = int(ht / 0.04)
            if self.base_spr.ripples and i_ < len(self.base_spr.ripples):
                rs = self.base_spr.ripples[i_]
                qx, qy = to_screen(hx, hy, hz)
                sc.blit(rs, (qx - cx - rs.get_width() / 2, qy - cy - rs.get_height() / 2), special_flags=pygame.BLEND_ADD)
        self.campaign_hud()

    def beacon_dim(self, red):
        k = "red_dim" if red else "green_dim"
        if k not in self.beacon_spr:
            g = self.beacon_spr["red" if red else "green"][3].copy()
            g.fill((170, 170, 170), special_flags=pygame.BLEND_MULT)
            self.beacon_spr[k] = g
        return self.beacon_spr[k]

    def visible_beacons(self, cx, cy):
        """Beacons on screen, each (x, y, red). Red if it stands in the corridor from Jetman to the base."""
        us, vs = [], []
        for sx, sy in ((-60, -60), (W + 60, -60), (-60, H + 80), (W + 60, H + 80)):
            X, Y = sx + cx, sy + cy
            us.append((Y / HH + X / HW) / 2); vs.append((Y / HH - X / HW) / 2)
        bx, by = self.world.base
        dx, dy = bx - self.x, by - self.y
        L2 = dx * dx + dy * dy or 1e-9
        out = []
        n_red = 0
        for x, y in self.world.beacons_in(min(us), min(vs), max(us), max(vs)):
            t = ((x - self.x) * dx + (y - self.y) * dy) / L2
            red = False
            if 0.0 < t < 1.0:
                ex, ey = self.x + dx * t - x, self.y + dy * t - y
                red = ex * ex + ey * ey < BEACON_CORRIDOR ** 2
            n_red += red
            out.append((x, y, red))
        self.n_red = n_red
        return out

    def draw_shadow(self, cx, cy, fl, alt, raised):
        surf, ax, ay = self.jet_frame(alt)[:3]
        self.queue_shadow(surf, ax, ay, self.x, self.y, self.z, raised, fl)

    def jet_frame(self, alt, recoil=None):
        f = self.facing()
        rc = 1 if (self.fire_t < 0.07 if recoil is None else recoil) else 0
        if alt > 0.5 or self.vz > 0:
            fwd = self.vx * math.cos(self.a) + self.vy * math.sin(self.a)
            lean = max(-1, min(3, int(round(fwd / 1.8))))
            return self.jet.get(f, "fly", 0, lean, rc)
        sp = math.hypot(self.vx, self.vy)
        if sp > 0.4:
            return self.jet.get(f, "walk", int(self.dist / 0.2) % 8, 0, rc)
        return self.jet.get(f, "idle", 0, 0, rc)

    def draw_jetman(self, cx, cy, alt):
        sc = self.screen
        surf, ax, ay, noz, muz, _, fvec = self.jet_frame(alt)
        px, py = to_screen(self.x, self.y, self.z)
        bx, by = px - cx, py - cy
        pack_behind = (fvec[0] + fvec[1]) > 0.1          # facing the viewer: pack hidden behind him
        if self.thrusting and pack_behind:
            self.draw_flames(bx, by, noz)
        self.draw_carried_bomb(bx, by, "back")
        if self.mode == "dying" and int(self.mode_t * 10) % 2 == 0:
            self.jet_draw = (surf, (bx - ax, by - ay))
            return pygame.Rect(bx - ax, by - ay, surf.get_width(), surf.get_height())
        sc.blit(surf, (bx - ax, by - ay))
        if self.hit_t < 0.35 and int(self.hit_t * 20) % 2 == 0:          # hit flash
            fl_ = surf.copy()
            fl_.fill((255, 70, 60, 0), special_flags=pygame.BLEND_RGBA_MAX)
            sc.blit(fl_, (bx - ax, by - ay))
        if self.thrusting and not pack_behind:
            self.draw_flames(bx, by, noz)
        self.draw_carried_bomb(bx, by, "front")
        if self.fire_t < 0.06:
            mx, my = bx + muz[0], by + muz[1]
            g = pygame.transform.smoothscale(self.glow, (54, 54))
            sc.blit(g, (mx - 27, my - 27), special_flags=pygame.BLEND_ADD)
            col = self.lasers[-1].col if self.lasers else (255, 255, 255)
            for i in range(6):
                a = i * math.pi / 3 + self.t * 40
                r = 9 if i % 2 else 5
                pygame.draw.line(sc, col, (mx, my), (mx + math.cos(a) * r, my + math.sin(a) * r * 0.7), 2)
            pygame.draw.circle(sc, (255, 255, 255), (mx, my), 3)
        self.jet_draw = (surf, (bx - ax, by - ay))
        return pygame.Rect(bx - ax, by - ay, surf.get_width(), surf.get_height())

    def draw_flames(self, bx, by, noz):
        sc = self.screen
        for i, (nx, ny) in enumerate(noz):
            x, y = bx + nx, by + ny
            fl = 0.75 + 0.25 * math.sin(self.t * 61 + i * 2.1) + self.rng.uniform(-0.15, 0.15)
            ln = 46 * fl
            g = pygame.transform.smoothscale(self.glow, (int(30 * fl), int(60 * fl)))
            sc.blit(g, (x - g.get_width() / 2, y - 6), special_flags=pygame.BLEND_ADD)
            pygame.draw.polygon(sc, (255, 120, 30), [(x - 5, y), (x + 5, y), (x + self.rng.uniform(-1.5, 1.5), y + ln)])
            pygame.draw.polygon(sc, (255, 220, 110), [(x - 3, y), (x + 3, y), (x, y + ln * 0.62)])
            pygame.draw.polygon(sc, (255, 255, 235), [(x - 1.5, y), (x + 1.5, y), (x, y + ln * 0.3)])

    def draw_dust(self, p, cx, cy):
        px, py = to_screen(p.x, p.y, p.z)
        k = 1 - p.age / p.life
        r = p.size * (1 + 1.6 * (1 - k))
        s = pygame.Surface((int(r * 2) + 2, int(r * 2) + 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (178, 178, 175, int(120 * k)), (r + 1, r + 1), r)
        self.screen.blit(s, (px - cx - r, py - cy - r * 0.7))

    def draw_silhouette(self, front, jet_rect):
        """Where boulders hide Jetman, show him as a bright outline so he is never lost."""
        self.hidden = False
        if jet_rect is None or not front:
            return
        jsurf, jpos = self.jet_draw
        self.hidden = self.silhouette(jsurf, jpos, front, (150, 205, 255, 95), (200, 235, 255))

    def silhouette(self, surf, spos, front, fill_col, edge_col, min_px=30):
        jmask = pygame.mask.from_surface(surf, 60)
        hid = pygame.mask.Mask(surf.get_size())
        rect = pygame.Rect(spos, surf.get_size())
        for rs, pos in front:
            if not pygame.Rect(pos, rs.get_size()).colliderect(rect):
                continue
            off = (int(pos[0] - spos[0]), int(pos[1] - spos[1]))
            hid.draw(jmask.overlap_mask(pygame.mask.from_surface(rs, 200), off), (0, 0))
        if hid.count() < min_px:
            return False
        self.screen.blit(hid.to_surface(setcolor=fill_col, unsetcolor=(0, 0, 0, 0)), spos)
        for x, y in jmask.outline():
            if hid.get_at((x, y)):
                self.screen.set_at((int(x + spos[0]), int(y + spos[1])), edge_col)
        return True

    def behind_rover(self, x, y, z):
        rx, ry = self.world.rover
        return rv.relation(rx, ry, x, y, 0.05) < 0

    def draw_parts(self, cx, cy, behind_rover):
        sc = self.screen
        for p in self.parts:
            if p.kind != "dust" and self.behind_rover(p.x, p.y, p.z) == behind_rover:
                px, py = to_screen(p.x, p.y, p.z)
                k = 1 - p.age / p.life
                if p.kind == "spark":
                    pygame.draw.circle(sc, (255, int(200 * k + 55), int(120 * k)), (px - cx, py - cy), 1.5 + k)
                elif p.kind == "frag":
                    col, ang, spin = p.size
                    ang += spin * p.age
                    x0, y0 = px - cx, py - cy
                    x1, y1 = x0 + math.cos(ang) * 7, y0 + math.sin(ang) * 7
                    pygame.draw.line(sc, (40, 40, 40), (x0, y0 + 1), (x1, y1 + 1), 3)
                    pygame.draw.line(sc, col, (x0, y0), (x1, y1), 2)
                    pygame.draw.circle(sc, col, (x1, y1), 3)
                    pygame.draw.circle(sc, (255, 255, 255), (x1 - 1, y1 - 1), 1)
                elif p.kind == "fire":
                    r = int(p.size * (0.45 + 0.55 * (1 - k)))
                    g = pygame.transform.smoothscale(self.glow, (r * 2, int(r * 1.6)))
                    g.fill((255, int(120 + 120 * k), int(60 * k), 255), special_flags=pygame.BLEND_RGBA_MULT)
                    if k < 1:
                        g.fill((int(255 * k),) * 3 + (255,), special_flags=pygame.BLEND_RGBA_MULT)
                    sc.blit(g, (px - cx - r, py - cy - r * 0.8), special_flags=pygame.BLEND_ADD)
                elif p.kind == "smoke":
                    r = int(p.size * (0.6 + 0.8 * (1 - k)))
                    s_ = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
                    al_ = 46 * min(1.0, k * 2.5) * min(1.0, p.age * 3)
                    for f_ in (1.0, 0.78, 0.55):             # soft edged puff
                        pygame.draw.circle(s_, (52, 50, 48, int(al_)), (r, r), int(r * f_))
                    sc.blit(s_, (px - cx - r, py - cy - r))
                elif p.kind == "shock":
                    r = p.size * (1 - k) + 10
                    pygame.draw.ellipse(sc, (255, int(180 * k + 60), int(120 * k)), (px - cx - r, py - cy - r * 0.5, r * 2, r), max(1, int(6 * k)))
                elif p.kind == "ring":
                    r = 8 + 60 * (1 - k)
                    pygame.draw.ellipse(sc, p.size, (px - cx - r, py - cy - r * 0.6, r * 2, r * 1.2), max(1, int(4 * k)))
                else:
                    g = pygame.transform.smoothscale(self.glow, (int(70 * k + 10), int(70 * k + 10)))
                    sc.blit(g, (px - cx - g.get_width() / 2, py - cy - g.get_height() / 2), special_flags=pygame.BLEND_ADD)

    def draw_lasers(self, cx, cy, behind_rover=False):
        sc = self.screen
        for L in self.lasers:
            if self.behind_rover(L.x, L.y, L.z) != behind_rover:
                continue
            ca, sa = math.cos(L.a), math.sin(L.a)
            back = min(2.6, math.hypot(L.x - L.x0, L.y - L.y0))
            x1, y1 = to_screen(L.x, L.y, L.z)
            x0, y0 = to_screen(L.x - ca * back, L.y - sa * back, L.z)
            p0, p1 = (x0 - cx, y0 - cy), (x1 - cx, y1 - cy)
            fade = 1 - L.dead / 0.08 if L.dead else 1
            col = tuple(int(c * fade) for c in L.col)
            # classic chunky beam: broken into dashes, bright core, coloured halo
            n = 6
            for i in range(n):
                if i % 3 == 2 and L.t > 0.05:
                    continue
                a0 = i / n; a1 = (i + 1) / n
                q0 = (p0[0] + (p1[0] - p0[0]) * a0, p0[1] + (p1[1] - p0[1]) * a0)
                q1 = (p0[0] + (p1[0] - p0[0]) * a1, p0[1] + (p1[1] - p0[1]) * a1)
                pygame.draw.line(sc, col, q0, q1, 7)
                pygame.draw.line(sc, (255, 255, 255), q0, q1, 3)
            g = pygame.transform.smoothscale(self.glow, (40, 40))
            g.fill(col + (255,), special_flags=pygame.BLEND_RGBA_MULT)
            sc.blit(g, (p1[0] - 20, p1[1] - 20), special_flags=pygame.BLEND_ADD)

    def hud(self):
        sc = self.screen
        pygame.draw.rect(sc, (0, 0, 0), (0, 0, W, 34))
        pygame.draw.line(sc, (90, 90, 90), (0, 34), (W, 34))
        alt = self.z - self.floor()
        txt = (f"ALTITUDE {int(alt):4d} m    HEADING {DIRS[self.facing()]:<10}    "
               f"POSITION {self.x:8.1f}, {self.y:8.1f}    ALIENS {len(self.aliens):2d}   SWEPT {self.pops:3d}    {self.fps:4.0f} FPS")
        sc.blit(self.font.render(txt, True, (225, 225, 225)), (14, 9))
        pygame.draw.rect(sc, (0, 0, 0), (0, H - 34, W, 34))
        pygame.draw.line(sc, (90, 90, 90), (0, H - 34), (W, H - 34))
        hint = "Left/Right turn   Up/Down move   Space jetpack   Ctrl or Z fire   F full screen   Esc quit"
        im = self.font.render(hint, True, (150, 150, 150))
        sc.blit(im, (W - im.get_width() - 14, H - 25))
        if self.t < 4:
            sc.blit(self.big.render("MOON SWEEPER", True, (235, 235, 235)), (14, 46))


CH_ = wd.CH



# ---------------------------------------------------------------- cast shadows
# Same sun as the rover, base and terrain: east and above. A point h px up
# lands on the ground SHADOW_K px further west on screen (rover.shadow_sprite).
SHADOW_W = 0.55 * rv.LIGHT[0] / rv.LIGHT[2] / 32.0     # world tiles of shift per px of height (-x, +y)
SHADOW_K = 2 * SHADOW_W * 32.0                          # screen px left per px of height
_SHADOW_CACHE = {}


def cast_shadow(surf, ax, ay):
    """Ground shadow of a sprite (anchor = ground contact) in the scene's style:
    each row of the silhouette is a horizontal slice, laid flat on the ground as a
    2:1 ellipse and pushed west by its height. Flat 0,0,0,120 with a short blur,
    like the rover and base shadows. Returns (surface, anchor x, anchor y)."""
    hit = _SHADOW_CACHE.get(id(surf))
    if hit and hit[0] is surf:
        return hit[1]
    a = pygame.surfarray.pixels_alpha(surf)
    cols = a > 100
    del a
    w, h = cols.shape
    ell = []
    for y in range(0, h, 2):
        xs = np.nonzero(cols[:, y])[0]
        if len(xs) == 0:
            continue
        hh = max(0.0, ay - y)
        r = (xs[-1] - xs[0] + 1) / 2 * 0.8
        ell.append((xs[0] + r / 0.8 - ax - hh * SHADOW_K, r))
    if not ell:
        res = (pygame.Surface((1, 1), pygame.SRCALPHA), 0, 0)
    else:
        x0 = min(c - r for c, r in ell) - 6; x1 = max(c + r for c, r in ell) + 6
        ry = max(r for _, r in ell) / 2 + 6
        S = 2
        W_, H_ = int(x1 - x0) + 1, int(2 * ry) + 1
        big = pygame.Surface((W_ * S, H_ * S), pygame.SRCALPHA)
        for c, r in ell:
            pygame.draw.ellipse(big, (0, 0, 0, 255), ((c - r - x0) * S, (ry - r / 2) * S, max(2, 2 * r * S), max(2, r * S)))
        m = pygame.surfarray.pixels_alpha(big)
        m[m > 0] = 120
        del m
        small = pygame.transform.smoothscale(big, (max(1, W_ // 2), max(1, H_ // 2)))
        s = pygame.transform.smoothscale(small, (W_, H_))
        if pygame.display.get_surface():
            s = s.convert_alpha()
        res = (s, -x0, ry)
    _SHADOW_CACHE[id(surf)] = (surf, res)
    return res

# ---------------------------------------------------------------- scripted scenes for --shot
SCENES = {
    "walk": [(0.45, {"left": 1}), (1.25, {"up": 1})],
    "high": [(2.2, {"jet": 1, "up": 1}), (0.9, {"jet": 1, "up": 1, "right": 1}), (0.5, {"up": 1, "jet": 1})],
    "fire": [(0.3, {"right": 1}), (0.62, {"fire": 1}), (0.016, {"fire": 1})],
    "behind": [(0.5, {"up": 1})],
    "far": [(0.6, {"jet": 1, "up": 1}), (0.25, {"up": 1, "fire": 1})],
    "aliens": [(1.6, {}), (0.5, {"right": 1})],
    "airborne": [(0.016, {})],
    "rover": [(0.3, {"left": 1}), (0.05, {})],
    "rover_behind": [(0.35, {"up": 1}), (0.05, {})],
    "rover_front": [(0.3, {"up": 1}), (0.05, {})],
    "pop": [(0.25, {}), (1.2, {"fire": 1})],
    "base": [(0.05, {})],
    "base_far": [(0.05, {})],
    "base_touch": [(0.5, {"up": 1})],
    "base_zap": [(0.3, {}), (0.2, {"fire": 1})],
    "base_on": [(0.05, {})],
    "beacons": [(0.05, {})],
    "beacons_wide": [(0.05, {})],
    "beacons_start": [(0.05, {})],
    "hover": [(0.45, {"jet": 1}), (0.4, {"jet": 1, "up": 1})],
}


def place_behind_boulder(game):
    """Stand Jetman just north of a tall ridge boulder, walking south behind it."""
    w = game.world
    best = None
    for ci in range(-2, 3):
        for cj in range(-2, 3):
            for rk in w.chunk_rocks(ci, cj):
                if rk.h > 22 and (best is None or abs(rk.x) + abs(rk.y) < abs(best.x) + abs(best.y)):
                    best = rk
    for d in (0.75, 0.9, 1.05, 1.2, 1.4):
        x, y = best.x - d, best.y - d
        if not w.blocked(x, y, 0, 0.35):
            game.x, game.y, game.z = x, y, 0.0
            game.a = math.pi / 4 + 0.2
            game.cam = list(to_screen(x, y))
            return


def ring_of_aliens(game, n=7, rad=2.6, seed=3):
    r = random.Random(seed)
    for i in range(n):
        a = i / n * math.tau + r.uniform(-.3, .3)
        x, y = game.x + math.cos(a) * rad * r.uniform(0.7, 1.4), game.y + math.sin(a) * rad * r.uniform(0.7, 1.4)
        if not game.world.blocked(x, y, 0, 0.35):
            uid = ("shot", i)
            game.aliens[uid] = al.Alien(uid, x, y, al.NAMES[i % len(al.NAMES)], r.randrange(1 << 30))


def setup_scene(game, scene):
    if scene in ("base", "base_far", "base_on", "base_touch", "base_zap", "beacons", "beacons_wide", "beacons_start"):
        bx, by = game.world.base
        pos = {"base": (bx + 4.6, by + 1.2, -2.6), "base_touch": (bx + 5.2, by + 1.6, -2.85), "base_zap": (bx + 7.5, by + 2.5, -2.82), "base_far": (bx - 7.0, by + 6.0, 0.0),
               "base_on": (bx + 1.6, by - 1.0, 2.4), "beacons": (bx - 14.0, by - 7.5, 0.5), "beacons_wide": (bx - 11.5, by + 12.0, -0.8), "beacons_start": (bx - 24.0, by - 19.0, 0.6)}[scene]
        game.x, game.y, game.a = pos
        if scene == "base_on":
            game.z = bs.PAD_Z
        cx = (game.x + bx) / 2 if scene.startswith("beacons") else bx + 1.5
        cy = (game.y + by) / 2 if scene.startswith("beacons") else by + 1.0
        if scene == "beacons_start":
            cx, cy = game.x + 4.0, game.y + 3.0
        game.cam = list(to_screen(cx, cy)); game.shot_cam = list(game.cam)
        game.wait_ready()
        game.update(1 / FPS, {})
        game.aliens.clear(); game.spawned.update((i, j) for i in range(-20, 20) for j in range(-20, 20))
    if scene in ("rover", "rover_behind", "rover_front"):
        game.update(1 / FPS, {})
        for uid in list(game.aliens):       # a tidy shot of the rover
            if (game.aliens[uid].x - game.x) ** 2 + (game.aliens[uid].y - game.y) ** 2 < 64:
                del game.aliens[uid]
    if scene in ("rover_behind", "rover_front"):
        rx, ry = game.world.rover
        if scene == "rover_behind":          # north of it, walking towards it (south-west)
            game.x, game.y, game.a = rx - 0.6, ry - rv.HALF_W - 1.3, math.pi / 2
        else:                                # just past the nose, in front of it
            game.x, game.y, game.a = rx + rv.HALF_L + 1.0, ry + 0.9, -math.pi / 2
        game.cam = list(to_screen(game.x, game.y))
    if scene in ("aliens", "airborne", "pop"):
        game.update(1 / FPS, {})            # let the natural spawn happen first
        for uid in list(game.aliens):       # keep the shot tidy: only staged aliens near him
            if (game.aliens[uid].x - game.x) ** 2 + (game.aliens[uid].y - game.y) ** 2 < 49:
                del game.aliens[uid]
    if scene == "aliens":
        ring_of_aliens(game)
    elif scene == "airborne":
        for i, (dx, dy, z, vz, var) in enumerate(((-1.4, 2.0, 104.0, 10.0, "crimson"), (2.6, 0.6, 50.0, -120.0, "violet"),
                                                   (2.6, 2.4, 0.5, -60.0, "green"))):
            a = al.Alien(("air", i), game.x + dx, game.y + dy, var, 11 + i)
            a.z, a.vz, a.vx, a.vy = z, vz, 0.3, -0.2
            game.aliens[("air", i)] = a
    elif scene == "pop":
        ang = game.a
        for i, d in enumerate((3.0, 5.5)):
            a = al.Alien(("pop", i), game.x + math.cos(ang) * d, game.y + math.sin(ang) * d, ("green", "violet")[i], 7 + i)
            a.vx = a.vy = 0.0
            a.z, a.vz = 8.0, 40.0
            game.aliens[("pop", i)] = a


def run_script(game, script):
    dt = 1 / FPS
    for secs, keys in script:
        for _ in range(max(1, int(round(secs * FPS)))):
            game.update(dt, keys)
            game.stream()
    if script is SCENES["pop"]:
        pass
    if script is SCENES["fire"]:
        while game.fire_t > 0:      # finish on the very frame a shot leaves the barrel
            game.update(dt, {"fire": 1})
    game.t = 10
    if getattr(game, "shot_cam", None):
        game.cam = list(game.shot_cam)
        game.aliens.clear()
        for _ in range(30):
            game.stream(); time.sleep(0.03)
    game.draw()


def facings_sheet(screen, out):
    jet = JetmanSprites()
    screen.fill((0, 0, 0))
    g = wd.World(wd.SEED, (8, 8))
    for ci in range(-1, 2):
        for cj in range(-1, 2):
            sx, sy = to_screen(ci * CHUNK + 8, cj * CHUNK + 8)
            screen.blit(g.ground_surface(ci, cj), (sx - CW / 2 + W / 2, sy - wd.CH / 2 + H / 2 - 120))
    shade = pygame.Surface((W, H), pygame.SRCALPHA); shade.fill((0, 0, 0, 40)); screen.blit(shade, (0, 0))
    font = pygame.font.Font(None, 24)
    rows = [("idle", 0, 0, 0), ("walk", 2, 0, 0), ("fly", 0, 2, 0), ("idle", 0, 0, 1)]
    labels = ["STANDING", "WALKING", "FLYING (lean)", "FIRING (recoil)"]
    for r, (pose, fr, lean, rc) in enumerate(rows):
        screen.blit(font.render(labels[r], True, (255, 255, 255), (0, 0, 0)), (10, 52 + r * 165))
        for f in range(8):
            surf, ax, ay, noz, muz, _, fv = jet.get(f, pose, fr, lean, rc)
            x, y = 190 + f * 136, 170 + r * 165
            sh = pygame.Surface((50, 24), pygame.SRCALPHA)
            pygame.draw.ellipse(sh, (0, 0, 0, 120), sh.get_rect())
            screen.blit(sh, (x - 25, y - 12 + (30 if pose == "fly" else 0)))
            screen.blit(surf, (x - ax, y - ay))
            if rc:
                pygame.draw.circle(screen, (255, 255, 230), (x + muz[0], y + muz[1]), 4)
            if r == 0:
                screen.blit(font.render(DIRS[f], True, (255, 255, 255), (0, 0, 0)), (x - 40, 40))
    pygame.image.save(screen, out)


def alien_sheet(screen, out):
    """Close-up sheet: each variant at 2x, spin frames, and the squash-and-stretch cycle."""
    spr = al.AlienSprites()
    g = wd.World(wd.SEED, (8, 8))
    for ci in range(-1, 2):
        for cj in range(-1, 2):
            sx, sy = to_screen(ci * CHUNK + 8, cj * CHUNK + 8)
            screen.blit(g.ground_surface(ci, cj), (sx - CW / 2 + W / 2, sy - wd.CH / 2 + H / 2 - 120))
    font = pygame.font.Font(None, 24)
    lab = lambda t, x, y: screen.blit(font.render(t, True, (255, 255, 255), (0, 0, 0)), (x, y))
    probe = al.Alien("p", 0, 0, "green", 1)
    squash = []
    for t in (0.5, 0.0, 0.04, 0.08, 0.12, 0.17, 0.22):     # stretched in the air, then the landing
        probe.land_t = t; probe.vz = -230 if t == 0.5 else 0
        squash.append(probe.squash())
    lab("VARIANTS (2x)", 10, 44); lab("SPIN", 10, 250); lab("SQUASH + STRETCH ON LANDING", 10, 470)
    for i, v in enumerate(al.NAMES):
        s, ax, ay = spr.get(v, 0)
        big = pygame.transform.smoothscale(s, (s.get_width() * 2, s.get_height() * 2))
        x, y = 200 + i * 270, 225
        sh = pygame.Surface((100, 44), pygame.SRCALPHA); pygame.draw.ellipse(sh, (0, 0, 0, 120), sh.get_rect())
        screen.blit(sh, (x - 50, y - 22)); screen.blit(big, (x - ax * 2, y - ay * 2))
        lab(v.upper(), x - 30, 40)
        for f in range(0, al.SPIN_FRAMES, 2):
            s, ax, ay = spr.get(v, f)
            screen.blit(s, (85 + i * 270 + f * 20 - ax, 360 + (f // 2 % 2) * 48 - ay))
    for j, (sx, sy) in enumerate(squash):
        s, ax, ay = spr.get(al.NAMES[j % 4], 3, sx, sy)
        x, y = 140 + j * 160, 650
        sh = pygame.Surface((60, 26), pygame.SRCALPHA); pygame.draw.ellipse(sh, (0, 0, 0, 120), sh.get_rect())
        screen.blit(sh, (x - 30, y - 13)); screen.blit(s, (x - ax, y - ay + 50 * (j == 0) * -1))
        lab(f"{sx:.2f}x{sy:.2f}", x - 35, 670)
    pygame.image.save(screen, out)


def main():
    pygame.init()
    headless = any(a in sys.argv for a in ("--shot", "--bench", "--facings", "--aliens", "--loadshot"))
    if headless:
        screen = pygame.display.set_mode((W, H))
    elif "--fullscreen" in sys.argv:
        screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.SCALED)
    else:                                   # start in a 1280x720 window; F toggles full screen
        screen = pygame.display.set_mode((W, H), pygame.SCALED)
    pygame.display.set_caption("Moon Sweeper")
    outdir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lunar_poc"))
    os.makedirs(outdir, exist_ok=True)
    if "--aliens" in sys.argv:
        name = sys.argv[sys.argv.index("--aliens") + 1]
        alien_sheet(screen, os.path.join(outdir, name + ".png"))
        print("saved", name)
        return
    if "--facings" in sys.argv:
        name = sys.argv[sys.argv.index("--facings") + 1]
        facings_sheet(screen, os.path.join(outdir, name + ".png"))
        print("saved", name)
        return
    if "--shot" in sys.argv:
        i = sys.argv.index("--shot")
        name = sys.argv[i + 1]
        scene = sys.argv[i + 2] if len(sys.argv) > i + 2 and not sys.argv[i + 2].startswith("-") else name
        start = (5123.5, -3311.5) if scene == "far" else START
        game = Game(screen, start=start)
        if scene == "behind":
            place_behind_boulder(game)
        game.wait_ready()
        setup_scene(game, scene)
        if scene == "pop":
            dt = 1 / FPS
            n = 0
            while game.pops == 0 and n < 200:
                game.update(dt, {"fire": int(n > 10)}); n += 1
            for _ in range(5):
                game.update(dt, {})
            game.t = 10; game.draw()
        else:
            run_script(game, SCENES[scene])
        pygame.image.save(screen, os.path.join(outdir, name + ".png"))
        print("saved", name, f"at {game.x:.1f},{game.y:.1f} alt {game.z:.0f} hidden={game.hidden}")
        return
    game = Game(screen, online=not headless)
    if "--loadshot" in sys.argv:          # screenshot of the loading screen part-way through
        while True:
            r, n, m = game.load_step()
            if r >= n * 0.45:
                break
            time.sleep(0.01)
        game.draw_loading(r, n, m)
        pygame.image.save(screen, os.path.join(outdir, "ms_loading.png"))
        print("saved ms_loading", r, n)
        return
    if "--bench" in sys.argv:
        # fly fast in a straight line so new chunks stream in all the way
        game.a = 0.3
        game.infinite = True
        game.wait_ready()
        times = []
        n = 1200
        for f in range(n):
            keys = {"up": 1, "jet": int((f // 50) % 3 != 0), "fire": 1, "left": int((f // 240) % 2)}
            t0 = time.perf_counter()
            game.update(1 / FPS, keys); game.draw()
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        print(f"{n} frames: mean {sum(times) / n:.2f} ms, median {times[n // 2]:.2f} ms, 99th pct {times[int(n * .99)]:.2f} ms, "
              f"worst {times[-1]:.2f} ms; travelled to {game.x:.0f},{game.y:.0f}; "
              f"chunks built {len(game.world.gen_ms)} (worker mean {sum(game.world.gen_ms) / max(1, len(game.world.gen_ms)):.0f} ms), "
              f"cached now {len(game.world.ground)}")
        return
    clock = pygame.time.Clock()
    # title screen while the world loads in the background (never blocks); Enter starts
    game.mode, game.title_t = "title", 0.0
    game.music(True)
    t_start = time.time()
    total = 1
    while True:
        dt = min(clock.tick(FPS) / 1000.0, 1 / 20)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); return
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_f and not (game.mode == "over" and game.entry_active):
                    pygame.display.toggle_fullscreen()
                    continue
                if game.handle_key(e) == "quit":
                    pygame.quit(); return
            elif e.type == pygame.TEXTINPUT:
                game.handle_text(e.text)
        ready = total
        if not game.loaded:
            ready, total, msg = game.load_step()
            if ready >= total:
                game.loaded = True
                print(f"ready after {time.time() - t_start:.1f} s", flush=True)
        if game.mode == "title":
            game.title_t += dt
            game.draw_title(game.loaded, min(1.0, ready / max(1, total)))
        else:
            k = pygame.key.get_pressed()
            keys = {"left": k[pygame.K_LEFT], "right": k[pygame.K_RIGHT], "up": k[pygame.K_UP], "down": k[pygame.K_DOWN],
                    "jet": k[pygame.K_SPACE], "fire": k[pygame.K_LCTRL] or k[pygame.K_RCTRL] or k[pygame.K_z]}
            game.update(dt, keys)
            game.draw()
        game.fps = clock.get_fps() or game.fps
        pygame.display.flip()


if __name__ == "__main__":
    main()
