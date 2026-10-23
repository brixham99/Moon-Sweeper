#!/usr/bin/env python3
"""Lunar Jetman (isometric) - proof of concept.

An endless, procedurally generated lunar surface in strict 2:1 isometric,
with a jetpacking Jetman under tank-style controls.

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

if "--shot" in sys.argv or "--bench" in sys.argv or "--facings" in sys.argv:
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame

import world as wd
from world import CHUNK, CW, HH, HW, TH, TW, to_screen
import jetman as jm
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
    def __init__(self, screen, seed=wd.SEED, start=START):
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

    # ------------------------------------------------------------ helpers
    def collect_frames(self):
        if self.jet_jobs:
            left = []
            for fu in self.jet_jobs:
                if fu.done():
                    jm.accept(self.jet.cache, fu.result())
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
            self.vx -= fx * 0.25; self.vy -= fy * 0.25      # a little kick
        for L in self.lasers:
            L.t += dt
            if L.dead:
                L.dead += dt
                continue
            step = LASER_SPEED * dt
            for i in range(4):
                L.x += math.cos(L.a) * step / 4; L.y += math.sin(L.a) * step / 4
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
            else:
                p.vz -= 300 * dt
        self.parts = [p for p in self.parts if p.age < p.life]
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
        items.sort(key=lambda t: t[0])
        jet_rect = None
        front = []
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
            elif kind == 1:
                jet_rect = self.draw_jetman(cx, cy, alt)
            else:
                self.draw_dust(obj, cx, cy)
        self.draw_silhouette(front, jet_rect)
        self.draw_lasers(cx, cy)
        for p in self.parts:
            if p.kind != "dust":
                px, py = to_screen(p.x, p.y, p.z)
                k = 1 - p.age / p.life
                if p.kind == "spark":
                    pygame.draw.circle(sc, (255, int(200 * k + 55), int(120 * k)), (px - cx, py - cy), 1.5 + k)
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
        jmask = pygame.mask.from_surface(jsurf, 60)
        hid = pygame.mask.Mask(jsurf.get_size())
        for surf, pos in front:
            r = pygame.Rect(pos, surf.get_size())
            if not r.colliderect(jet_rect):
                continue
            rmask = pygame.mask.from_surface(surf, 200)
            off = (int(pos[0] - jpos[0]), int(pos[1] - jpos[1]))
            hid.draw(jmask.overlap_mask(rmask, off), (0, 0))
        n = hid.count()
        if n < 30:
            return
        self.hidden = True
        fill = hid.to_surface(setcolor=(150, 205, 255, 95), unsetcolor=(0, 0, 0, 0))
        self.screen.blit(fill, jpos)
        edge = hid.outline()
        jo = jmask.outline()
        # draw Jetman's own outline, but only the stretches that are hidden
        for x, y in jo[::1]:
            if hid.get_at((x, y)):
                self.screen.set_at((int(x + jpos[0]), int(y + jpos[1])), (200, 235, 255))

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
               f"POSITION {self.x:8.1f}, {self.y:8.1f}    {self.fps:4.0f} FPS")
        sc.blit(self.font.render(txt, True, (225, 225, 225)), (14, 9))
        pygame.draw.rect(sc, (0, 0, 0), (0, H - 34, W, 34))
        pygame.draw.line(sc, (90, 90, 90), (0, H - 34), (W, H - 34))
        hint = "Left/Right turn   Up/Down move   Space jetpack   Ctrl or Z fire   F full screen   Esc quit"
        im = self.font.render(hint, True, (150, 150, 150))
        sc.blit(im, (W - im.get_width() - 14, H - 25))
        if self.t < 4:
            sc.blit(self.big.render("LUNAR JETMAN  -  proof of concept", True, (235, 235, 235)), (14, 46))


CH_ = wd.CH


# ---------------------------------------------------------------- scripted scenes for --shot
SCENES = {
    "walk": [(0.45, {"left": 1}), (1.25, {"up": 1})],
    "high": [(2.2, {"jet": 1, "up": 1}), (0.9, {"jet": 1, "up": 1, "right": 1}), (0.5, {"up": 1, "jet": 1})],
    "fire": [(0.3, {"right": 1}), (0.62, {"fire": 1}), (0.016, {"fire": 1})],
    "behind": [(0.5, {"up": 1})],
    "far": [(0.6, {"jet": 1, "up": 1}), (0.25, {"up": 1, "fire": 1})],
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


def run_script(game, script):
    dt = 1 / FPS
    for secs, keys in script:
        for _ in range(max(1, int(round(secs * FPS)))):
            game.update(dt, keys)
            game.stream()
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


def main():
    pygame.init()
    headless = any(a in sys.argv for a in ("--shot", "--bench", "--facings"))
    if headless or "--windowed" in sys.argv:
        screen = pygame.display.set_mode((W, H))
    else:
        screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.SCALED)
    pygame.display.set_caption("Lunar Jetman - proof of concept")
    outdir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lunar_poc"))
    os.makedirs(outdir, exist_ok=True)
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
