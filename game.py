#!/usr/bin/env python3
"""Moon Sweeper.

An endless, procedurally generated lunar surface in strict 2:1 isometric,
with a jetpacking Jetman under tank-style controls, and spiky aliens that
bounce about the surface for him to sweep up with his laser.

Run:            python3 game.py            (full screen; F toggles, Esc quits)
                python3 game.py --windowed
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

if any(a in sys.argv for a in ("--shot", "--bench", "--facings", "--aliens")):
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame

import world as wd
from world import CHUNK, CW, HH, HW, TH, TW, to_screen
import jetman as jm
import aliens as al
import sfx as sx
from jetman import JetmanSprites

W, H = 1280, 720
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


class Game:
    def __init__(self, screen, seed=wd.SEED, start=START, sound_log=False):
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
        # every Jetman frame is drawn in the worker processes, off the main thread
        self.jet_jobs = [self.world.procs.submit(jm.frame_bytes, k) for k in jm.all_keys()]
        self.alien_spr = al.AlienSprites()
        self.jet_jobs += [self.world.procs.submit(al.frame_bytes, k) for k in al.all_keys()]
        self.aliens = {}            # uid -> Alien
        self.spawned = set()        # chunks whose aliens are out
        self.killed = set()         # uids popped for good
        self.splats = []            # goo decals: [x, y, colour, age]
        self.hit_t = 9.0
        self.pops = 0

    # ------------------------------------------------------------ helpers
    def collect_frames(self):
        if self.jet_jobs:
            left = []
            for fu in self.jet_jobs:
                if fu.done():
                    res = fu.result()
                    if isinstance(res[0][0], str):
                        self.alien_spr.accept(res)
                    else:
                        jm.accept(self.jet.cache, res)
                else:
                    left.append(fu)
            self.jet_jobs = left

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
    def update(self, dt, keys):
        self.t += dt
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
        alt = self.z - fl
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
                    self.pop(hit)
                    break
                if self.world.blocked(L.x, L.y, L.z - 6, 0.02) or L.z < self.floor(L.x, L.y) + 4:
                    L.dead = 1e-6
                    self.sparks(L.x, L.y, L.z, L.col)
                    break
        self.lasers = [L for L in self.lasers if L.t < 0.6 and L.dead < 0.08]
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
                p.vz -= 300 * dt
        self.parts = [p for p in self.parts if p.age < p.life]
        self.update_aliens(dt)
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
                for uid, x, y, var, seed in al.chunk_aliens(*k):
                    if uid in self.killed or uid in self.aliens:
                        continue
                    if (x - self.x) ** 2 + (y - self.y) ** 2 < 9:      # never pop out on top of him
                        continue
                    if not self.world.blocked(x, y, 0, 0.35):
                        self.aliens[uid] = al.Alien(uid, x, y, var, seed)
        # despawn far away; let far chunks spawn again later so the population stays steady
        for uid, a in list(self.aliens.items()):
            if abs(a.x - self.x) > 2.6 * CHUNK or abs(a.y - self.y) > 2.6 * CHUNK:
                del self.aliens[uid]
        self.spawned = {k for k in self.spawned if abs(k[0] - fi) <= 2 and abs(k[1] - fj) <= 2}
        ev = []
        for a in self.aliens.values():
            a.update(dt, self.world, self.x, self.y, ev)
        for kind, a in ev:
            self.alien_sound("bounce", a)
            if (a.x - self.x) ** 2 + (a.y - self.y) ** 2 < 600:
                for _ in range(5):
                    ang = self.rng.uniform(0, math.tau); s = self.rng.uniform(0.5, 1.4)
                    self.parts.append(Particle(a.x, a.y, a.floor + 1, math.cos(ang) * s, math.sin(ang) * s,
                                               self.rng.uniform(2, 10), self.rng.uniform(0.3, 0.6), "dust",
                                               self.rng.uniform(2, 3.5)))
        # touching Jetman: hit flash and knockback (placeholder, no lives yet)
        self.hit_t += dt
        for a in self.aliens.values():
            dx, dy = self.x - a.x, self.y - a.y
            d = math.hypot(dx, dy)
            body_z = a.z + 22
            if d < 0.55 and self.z - 10 < body_z < self.z + 100 and a.hit_cool <= 0:
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
            if (a.x - L.x) ** 2 + (a.y - L.y) ** 2 < 0.42 ** 2 and abs(L.z - (a.z + 24)) < 34:
                return a
        return None

    def pop(self, a):
        """Splat: spike fragments, goo, sparks and a flash."""
        del self.aliens[a.uid]
        self.alien_sound("pop", a)
        self.killed.add(a.uid)
        self.pops += 1
        stalk, club, _ = al.VARIANTS[a.variant]
        z = a.z + 24
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

    def draw_alien_shadows(self, cx, cy):
        for a in self.aliens.values():
            gx, gy = to_screen(a.x, a.y, a.floor)
            gx -= cx; gy -= cy
            if not (-60 < gx < W + 60 and -60 < gy < H + 60):
                continue
            alt = max(0.0, a.z - a.floor)
            k = 1.0 / (1.0 + alt / 70.0)
            rx, ry = 6 + 16 * k, 3 + 8 * k
            sh = pygame.Surface((int(rx * 2) + 6, int(ry * 2) + 6), pygame.SRCALPHA)
            al_ = int(150 * (0.3 + 0.7 * k))
            pygame.draw.ellipse(sh, (0, 0, 0, al_ // 2), sh.get_rect())
            pygame.draw.ellipse(sh, (0, 0, 0, al_ // 2), sh.get_rect().inflate(-5, -3))
            self.screen.blit(sh, (gx - rx - 3 - alt * 0.04, gy - ry - 3))

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
        surf, ax, ay = self.alien_spr.get(a.variant, int(a.spin) % al.SPIN_FRAMES, sx, sy)
        pos = (px - ax, py - ay + (2 if a.land_t < 0.15 else 0))
        self.screen.blit(surf, pos)
        return surf, pos

    # ------------------------------------------------------------ world streaming
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
        vis = self.visible_chunks(cx, cy)
        for (ci, cj) in vis:
            sx, sy = to_screen(ci * CHUNK, cj * CHUNK)
            sc.blit(self.world.ground_surface(ci, cj), (sx - CW / 2 - cx, sy - cy))
        fl = self.floor()
        alt = max(0.0, self.z - fl)
        self.draw_splats(cx, cy)
        self.draw_alien_shadows(cx, cy)
        self.draw_shadow(cx, cy, fl, alt)
        # depth-sorted objects
        items = []
        for (ci, cj) in self.visible_chunks(cx, cy, 260):
            for rk in self.world.chunk_rocks(ci, cj):
                items.append((rk.x + rk.y, 0, rk))
        items.append((self.x + self.y + 0.01, 1, None))
        for p in self.parts:
            if p.kind == "dust":
                items.append((p.x + p.y, 3, p))
        for a in self.aliens.values():
            items.append((a.x + a.y + 0.005, 4, a))
        items.sort(key=lambda t: t[0])
        jet_rect = None
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
        self.draw_lasers(cx, cy)
        for p in self.parts:
            if p.kind != "dust":
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
                elif p.kind == "ring":
                    r = 8 + 60 * (1 - k)
                    pygame.draw.ellipse(sc, p.size, (px - cx - r, py - cy - r * 0.6, r * 2, r * 1.2), max(1, int(4 * k)))
                else:
                    g = pygame.transform.smoothscale(self.glow, (int(70 * k + 10), int(70 * k + 10)))
                    sc.blit(g, (px - cx - g.get_width() / 2, py - cy - g.get_height() / 2), special_flags=pygame.BLEND_ADD)
        self.hud()

    def draw_shadow(self, cx, cy, fl, alt):
        gx, gy = to_screen(self.x, self.y, fl)
        k = 1.0 / (1.0 + alt / 160.0)
        fade = max(0.0, 1 - alt / 700)
        rx, ry = 10 + 22 * k, 5 + 11 * k
        sh = pygame.Surface((int(rx * 2) + 8, int(ry * 2) + 8), pygame.SRCALPHA)
        a = int(170 * (0.35 + 0.65 * k) * (0.4 + 0.6 * fade))
        pygame.draw.ellipse(sh, (0, 0, 0, a // 2), sh.get_rect())               # soft edge
        pygame.draw.ellipse(sh, (0, 0, 0, a // 2), sh.get_rect().inflate(-6, -4))
        self.screen.blit(sh, (gx - cx - rx - 4 - alt * 0.03, gy - cy - ry - 4))

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
        sc.blit(surf, (bx - ax, by - ay))
        if self.hit_t < 0.35 and int(self.hit_t * 20) % 2 == 0:          # hit flash
            fl_ = surf.copy()
            fl_.fill((255, 70, 60, 0), special_flags=pygame.BLEND_RGBA_MAX)
            sc.blit(fl_, (bx - ax, by - ay))
        if self.thrusting and not pack_behind:
            self.draw_flames(bx, by, noz)
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

    def draw_lasers(self, cx, cy):
        sc = self.screen
        for L in self.lasers:
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


# ---------------------------------------------------------------- scripted scenes for --shot
SCENES = {
    "walk": [(0.45, {"left": 1}), (1.25, {"up": 1})],
    "high": [(2.2, {"jet": 1, "up": 1}), (0.9, {"jet": 1, "up": 1, "right": 1}), (0.5, {"up": 1, "jet": 1})],
    "fire": [(0.3, {"right": 1}), (0.62, {"fire": 1}), (0.016, {"fire": 1})],
    "behind": [(0.5, {"up": 1})],
    "far": [(0.6, {"jet": 1, "up": 1}), (0.25, {"up": 1, "fire": 1})],
    "aliens": [(1.6, {}), (0.5, {"right": 1})],
    "airborne": [(0.016, {})],
    "pop": [(0.25, {}), (1.2, {"fire": 1})],
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
    headless = any(a in sys.argv for a in ("--shot", "--bench", "--facings", "--aliens"))
    if headless or "--windowed" in sys.argv:
        screen = pygame.display.set_mode((W, H))
    else:
        screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.SCALED)
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
        while game.jet_jobs:
            game.collect_frames(); time.sleep(0.02)
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
    game = Game(screen)
    if "--bench" in sys.argv:
        # fly fast in a straight line so new chunks stream in all the way
        game.a = 0.3
        for _ in range(3):
            game.draw()
        while game.jet_jobs:
            game.collect_frames(); time.sleep(0.05)
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
    while True:
        dt = min(clock.tick(FPS) / 1000.0, 1 / 20)
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                pygame.quit(); return
            if e.type == pygame.KEYDOWN and e.key == pygame.K_f:
                pygame.display.toggle_fullscreen()
        k = pygame.key.get_pressed()
        keys = {"left": k[pygame.K_LEFT], "right": k[pygame.K_RIGHT], "up": k[pygame.K_UP], "down": k[pygame.K_DOWN],
                "jet": k[pygame.K_SPACE], "fire": k[pygame.K_LCTRL] or k[pygame.K_RCTRL] or k[pygame.K_z]}
        game.update(dt, keys)
        game.draw()
        game.fps = clock.get_fps() or game.fps
        pygame.display.flip()


if __name__ == "__main__":
    main()
