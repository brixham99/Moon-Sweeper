"""Infinite, deterministic lunar surface for Moon Sweeper.

The world is split into CHUNK x CHUNK tile chunks. Everything about a chunk
(craters, boulders, ridges, the albedo noise) comes from a hash of its
coordinates and the world seed, so the same place always looks the same and
the surface carries on forever in every direction.

Seamless borders: a feature belongs to the chunk it was born in, but craters
are painted from the 3x3 neighbourhood and rocks are filed under the chunk
they actually sit in, so nothing is cut off at a border. Ground heights are a
pure function of world position, so shading matches across borders too.
"""
import math
import random
import threading
import time
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pygame

import rover as rv
import base as bs

TW, TH = 64, 32            # strict 2:1 isometric tile
HW, HH = TW // 2, TH // 2
CHUNK = 16                 # tiles per chunk side
CW, CH = CHUNK * TW, CHUNK * TH
SEED = 1983
# Light comes from the east (screen right) and from above. World +x is screen
# south-east and +y is screen south-west, so screen east is (+x, -y).
LIGHT = np.array([0.62, -0.62, 0.48]); LIGHT /= np.linalg.norm(LIGHT)


def to_screen(x, y, z=0.0):
    return (x - y) * HW, (x + y) * HH - z


def mix(*v):
    """Small deterministic integer hash (Python's own hash is not stable for tuples)."""
    h = 0x9E3779B9
    for k in v:
        h = (h ^ (k & 0xFFFFFFFF)) * 0x85EBCA6B & 0xFFFFFFFF
        h ^= h >> 13
        h = h * 0xC2B2AE35 & 0xFFFFFFFF
        h ^= h >> 16
    return h


def chunk_rng(ci, cj, salt=0):
    return random.Random(mix(SEED, ci, cj, salt))


def _hash_grid(ix, iy, salt):
    h = (ix.astype(np.int64) * 374761393 + iy.astype(np.int64) * 668265263 + (SEED + salt) * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return h.astype(np.float64) / 4294967295.0 * 2 - 1


def value_noise(x, y, cell, salt):
    gx, gy = x / cell, y / cell
    ix, iy = np.floor(gx), np.floor(gy)
    fx, fy = gx - ix, gy - iy
    fx = fx * fx * (3 - 2 * fx); fy = fy * fy * (3 - 2 * fy)
    a = _hash_grid(ix, iy, salt); b = _hash_grid(ix + 1, iy, salt)
    c = _hash_grid(ix, iy + 1, salt); d = _hash_grid(ix + 1, iy + 1, salt)
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def ground_pixels(ci, cj, craters, seed=None, R=2):
    """Pixels for one chunk's ground. Pure function of (seed, ci, cj): safe in a worker thread.
    Heights and shading are worked out at 1/R resolution, then grain is added at full size."""
    global SEED
    if seed is not None:
        SEED = seed
    t0 = time.perf_counter()
    M = 2
    w, h = CW // R + 2 * M, CH // R + 2 * M
    px = (np.arange(w, dtype=np.float64)[:, None] - M + 0.5) * R - CW / 2
    py = (np.arange(h, dtype=np.float64)[None, :] - M + 0.5) * R
    u = np.broadcast_to((py / HH + px / HW) / 2 + ci * CHUNK, (w, h))
    v = np.broadcast_to((py / HH - px / HW) / 2 + cj * CHUNK, (w, h))
    hgt = value_noise(u, v, 9.0, 1) * 7 + value_noise(u, v, 2.2, 2) * 2.2
    alb = 124 + value_noise(u, v, 26.0, 3) * 20 + value_noise(u, v, 6.0, 4) * 8
    for (cx, cy, cr, age) in sorted(craters, key=lambda c: -c[2]):
        sx = ((cx - ci * CHUNK) - (cy - cj * CHUNK)) * HW + CW / 2
        sy = ((cx - ci * CHUNK) + (cy - cj * CHUNK)) * HH
        ext = cr * 1.9 * 1.42
        x_lo = int(max(0, (sx - ext * HW) / R + M)); x_hi = int(min(w, (sx + ext * HW) / R + M + 1))
        y_lo = int(max(0, (sy - ext * HH) / R + M)); y_hi = int(min(h, (sy + ext * HH) / R + M + 1))
        if x_lo >= x_hi or y_lo >= y_hi:
            continue
        uu, vv = u[x_lo:x_hi, y_lo:y_hi], v[x_lo:x_hi, y_lo:y_hi]
        d = np.sqrt((uu - cx) ** 2 + (vv - cy) ** 2) / cr
        depth, rim = cr * 11.0, cr * 3.4
        inside = d < 1.0
        prof = np.where(inside, -depth + (depth + rim) * d ** 2.2, rim * np.exp(-((d - 1.0) / 0.3) ** 2))
        hh = hgt[x_lo:x_hi, y_lo:y_hi]
        # the level the new crater was punched into: the old surface just outside its rim
        ring = (d > 1.0) & (d < 1.25)
        base = float(hh[ring].mean()) if ring.any() else float(hh.mean())
        wgt = np.clip((1.1 - d) / 0.25, 0, 1)      # carve: older relief inside the bowl is erased
        hgt[x_lo:x_hi, y_lo:y_hi] = hh * (1 - wgt) + base * wgt + prof
        if cr > 2.6:
            hgt[x_lo:x_hi, y_lo:y_hi] += cr * 3.0 * np.exp(-(d / 0.13) ** 2)
        bright = (1 - age) ** 2 * 22             # young craters: bright ejecta, darker floor
        alb[x_lo:x_hi, y_lo:y_hi] += np.where(inside, -7 + d * 5, bright * np.exp(-((d - 1.0) / 0.55) ** 2))
    gx = np.gradient(hgt, axis=0) / R; gy = np.gradient(hgt, axis=1) / R
    dhu = gx * HW + gy * HH; dhv = -gx * HW + gy * HH
    nx, ny = -dhu / 45.25, -dhv / 45.25
    lam = (nx * LIGHT[0] + ny * LIGHT[1] + LIGHT[2]) / np.sqrt(nx * nx + ny * ny + 1)
    shade = alb * (0.12 + 1.0 * np.clip(lam, 0, 1)) / (0.12 + LIGHT[2])
    shade = np.clip(shade[M:-M, M:-M], 0, 255).astype(np.float32)
    # upsample to full size, then add grain
    full = np.repeat(np.repeat(shade, R, axis=0), R, axis=1)
    k = np.array([0.25, 0.5, 0.25], np.float32)
    full = (np.roll(full, 1, 0) * k[0] + full * k[1] + np.roll(full, -1, 0) * k[2])
    full = (np.roll(full, 1, 1) * k[0] + full * k[1] + np.roll(full, -1, 1) * k[2])
    rng = np.random.default_rng(mix(SEED, ci, cj, 7))
    full = np.clip(full + rng.normal(0, 5.0, full.shape).astype(np.float32), 0, 255)
    rgb = np.stack([full, full, full * 0.985], axis=-1).astype(np.uint8)
    X = (np.arange(CW)[:, None] + 0.5 - CW / 2); Y = (np.arange(CH)[None, :] + 0.5)
    uu = (Y / HH + X / HW) / 2; vv = (Y / HH - X / HW) / 2
    mask = ((uu >= 0) & (uu < CHUNK) & (vv >= 0) & (vv < CHUNK)).astype(np.uint8) * 255
    return rgb, mask, None, (time.perf_counter() - t0) * 1000


def rock_pixels(rk):
    """Boulder pixels (pure numpy, safe in a worker thread)."""
    r = random.Random(rk.seed)
    SS = 2
    a = rk.size * 0.62                         # footprint radius, tiles
    hpx = rk.h
    n = max(48, int(a * 300))
    g = np.linspace(-1, 1, n)
    fu, fv = np.meshgrid(g, g, indexing="ij")
    ang = np.arctan2(fv, fu)
    lump = 1 + sum(r.uniform(-.13, .13) * np.cos(k * ang + r.uniform(0, 6.3)) for k in (2, 3, 5))
    rr = np.sqrt(fu ** 2 + fv ** 2) / lump
    ins = rr < 1
    # rounded dome, flattened a little on top, broken into facets
    dome = np.minimum(1.0, 1.35 * np.clip(1 - rr ** 2, 0, 1))
    z = dome * hpx
    tilt = r.uniform(-0.25, 0.25), r.uniform(-0.25, 0.25)
    z = z * (1 + tilt[0] * fu + tilt[1] * fv)
    for k1, k2 in ((r.uniform(3, 6), r.uniform(-6, 6)), (r.uniform(-9, 9), r.uniform(5, 9)), (r.uniform(8, 14), r.uniform(-14, 14))):
        z += np.abs(np.sin(fu * k1 + fv * k2 + r.uniform(0, 6))) * hpx * 0.07 * dome
    z = np.where(ins, np.maximum(z, 0), 0)
    sc = 45.25
    step = (2 * a / (n - 1)) * sc
    du = np.gradient(z, axis=0) / step; dv = np.gradient(z, axis=1) / step
    nl = np.sqrt(du * du + dv * dv + 1)
    lam = (-du * LIGHT[0] - dv * LIGHT[1] + LIGHT[2]) / nl
    tone = rk.shade + r.uniform(-6, 6)
    col = tone * (0.1 + 1.08 * np.clip(lam, 0, 1)) + np.clip(-du * 0.0, 0, 0)
    col += np.random.default_rng(rk.seed).normal(0, 4, col.shape)
    wu, wv = fu * a, fv * a
    sxp = (wu - wv) * HW * SS
    syp = ((wu + wv) * HH - z) * SS
    W_ = int(a * TW * 1.3) + 16; H_ = int(hpx + a * TH * 1.3 + 16)
    ox, oy = W_ / 2, H_ - a * TH * 0.65 - 6
    Wb, Hb = W_ * SS, H_ * SS
    img = np.zeros((Wb, Hb, 3), np.float32)
    al = np.zeros((Wb, Hb), np.uint8)
    depth = (wu + wv) * 0.612 * sc + z * 0.5
    order = np.argsort(depth[ins], kind="stable")
    X = (sxp[ins][order] + ox * SS).astype(int); Y = (syp[ins][order] + oy * SS).astype(int)
    C = np.clip(col[ins][order], 0, 255)
    # each sample covers the drop to its neighbours, so steep sides have no gaps
    slope = (np.sqrt(du * du + dv * dv) * step * SS)[ins][order]
    ext = np.clip(np.ceil(slope) + 1, 2, 6).astype(int)
    for dy in range(int(ext.max())):
        m = ext > dy
        Yi = np.clip(Y[m] + dy, 0, Hb - 1)
        for dx in (0, 1):
            Xi = np.clip(X[m] + dx, 0, Wb - 1)
            img[Xi, Yi, 0] = C[m]; al[Xi, Yi] = 255
    img[..., 1] = img[..., 0]; img[..., 2] = img[..., 0] * 0.985
    return img.astype(np.uint8), al, W_, H_, ox, oy, a


def rock_batch(rocks):
    return [(rk, rock_pixels(rk)) for rk in rocks]


class Rock:
    __slots__ = ("x", "y", "size", "h", "shade", "seed", "key")

    def __getstate__(self):
        return (self.x, self.y, self.size, self.h, self.shade, self.seed)

    def __setstate__(self, st):
        self.x, self.y, self.size, self.h, self.shade, self.seed = st
        self.key = self.seed

    def __init__(self, x, y, size, h, shade, seed):
        self.x, self.y, self.size, self.h, self.shade, self.seed = x, y, size, h, shade, seed
        self.key = seed


BEACON_GAP = 26.0           # beacons stand on a regular grid, every 26 tiles (at most two on screen), for ever


def beacon_clear(x, y, d):
    """True if (x, y) is at least d tiles from the nearest beacon grid point."""
    gx = (math.floor(x / BEACON_GAP) + 0.5) * BEACON_GAP; gy = (math.floor(y / BEACON_GAP) + 0.5) * BEACON_GAP
    return (x - gx) ** 2 + (y - gy) ** 2 > d * d


class World:
    def __init__(self, seed=SEED, start=(0.5, 0.5)):
        global SEED
        SEED = seed
        self.start = start
        self.rover = (start[0] + 0.4, start[1] - 4.8)    # parked just behind the landing spot, nose south-east
        self.base = (start[0] + 27.0, start[1] + 19.0)  # the Alien Missile Base, ~33 tiles away to the south
        self.raw = {}        # (ci, cj) -> (craters, rocks) born in that chunk
        self.rocks = {}      # (ci, cj) -> rocks sitting in that chunk
        self.ground = {}     # (ci, cj) -> Surface
        self.sprites = {}    # rock seed -> (Surface, ox, oy)
        self.gen_ms = []
        self.pending = {}
        self.rock_pending, self.rock_queue, self.rock_done = {}, {}, set()
        self.queue, self.done, self.focus = {}, {}, (0, 0)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        # Two separate pools. Ground chunks get their own workers so they never
        # queue behind the ~270 Jetman/alien sprite frames rendered at start-up:
        # on a 4-core Mac (2 workers) that backlog took ~30 s, and the main
        # thread, waiting for its first ground chunk, froze (spinning beachball).
        n = max(2, min(6, (os.cpu_count() or 4) - 2))
        self.procs = ProcessPoolExecutor(max_workers=n)   # ground + boulders
        self.sprite_pool = ProcessPoolExecutor(max_workers=max(2, min(6, (os.cpu_count() or 4) - 1)))   # busy only while loading
        self.rock_pool = self.procs          # boulders must not queue behind the sprite frames
        for _ in range(2):
            threading.Thread(target=self._builder, daemon=True).start()

    # ------------------------------------------------------------ features
    def _raw(self, ci, cj):
        k = (ci, cj)
        if k in self.raw:
            return self.raw[k]
        r = chunk_rng(ci, cj)
        x0, y0 = ci * CHUNK, cj * CHUNK
        craters = []
        for _ in range(r.randint(3, 6)):
            rad = r.choice((0.5, 0.6, 0.8, 0.9, 1.1, 1.3, 1.6, 2.0, 2.6)) * r.uniform(0.85, 1.15)
            if r.random() < 0.07:
                rad = r.uniform(3.0, 4.2)          # the odd big one
            craters.append((x0 + r.uniform(0, CHUNK), y0 + r.uniform(0, CHUNK), rad, r.random()))
        rocks = []

        def rock(x, y, size, tall=False):
            h = size * r.uniform(15, 24) * (1.3 if tall else 1.0)
            rocks.append(Rock(x, y, size, h, r.uniform(105, 170), r.randrange(1 << 30)))

        for _ in range(r.randint(18, 34)):
            rock(x0 + r.uniform(0, CHUNK), y0 + r.uniform(0, CHUNK), r.uniform(0.12, 0.42))
        for (cx, cy, cr, _) in craters:           # boulders thrown out of the bigger craters
            if cr > 1.4:
                for _ in range(int(cr * 4)):
                    a = r.uniform(0, math.tau); d = cr * r.uniform(1.05, 1.7)
                    rock(cx + math.cos(a) * d, cy + math.sin(a) * d, r.uniform(0.18, 0.55))
        if r.random() < 0.35:                      # a ridge: a chain of big boulders
            x, y = x0 + r.uniform(2, CHUNK - 2), y0 + r.uniform(2, CHUNK - 2)
            a = r.uniform(0, math.tau)
            for _ in range(r.randint(9, 20)):      # at most ~11 tiles long, so it stays within the neighbours
                a += r.uniform(-0.35, 0.35)
                x += math.cos(a) * 0.55; y += math.sin(a) * 0.55
                rock(x, y, r.uniform(0.6, 1.25), tall=True)
                if r.random() < 0.6:
                    rock(x + r.uniform(-.9, .9), y + r.uniform(-.9, .9), r.uniform(0.25, 0.6))
        sx, sy = self.start                        # keep the landing site clear
        rx, ry = self.rover
        bx, by = self.base
        rocks = [k for k in rocks if (k.x - sx) ** 2 + (k.y - sy) ** 2 > 7 and not rv.in_footprint(rx, ry, k.x, k.y, 1.6)
                 and not bs.in_footprint(bx, by, k.x, k.y, 1.6) and beacon_clear(k.x, k.y, 0.7 + k.size * 0.5)]
        craters = [c for c in craters if (c[0] - sx) ** 2 + (c[1] - sy) ** 2 > (c[2] + 2.5) ** 2
                   and (c[0] - rx) ** 2 + (c[1] - ry) ** 2 > (c[2] + 3.2) ** 2
                   and (c[0] - bx) ** 2 + (c[1] - by) ** 2 > (c[2] + 5.5) ** 2]
        craters.sort(key=lambda c: -c[2])          # big (old) first; younger, smaller ones carve into them
        self.raw[k] = (craters, rocks)
        return self.raw[k]

    def chunk_rocks(self, ci, cj):
        k = (ci, cj)
        if k not in self.rocks:
            out = []
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for rk in self._raw(ci + di, cj + dj)[1]:
                        if int(math.floor(rk.x / CHUNK)) == ci and int(math.floor(rk.y / CHUNK)) == cj \
                                and not bs.in_footprint(self.base[0], self.base[1], rk.x, rk.y, 1.6):
                            out.append(rk)
            out.sort(key=lambda q: q.x + q.y)
            self.rocks[k] = out
        return self.rocks[k]

    # ------------------------------------------------------------ moving the base (new level)
    BASE_GONE = (1.0e6, 1.0e6)

    def set_base(self, pos):
        """Put the Alien Missile Base somewhere else (or far away while it's destroyed).
        Boulders under its new footprint are cleared."""
        self.base = pos
        self.rocks = {}
        self.__dict__.pop("_floor_cache", None)

    def base_site_ok(self, x, y):
        """A clear spot for the base: no crater near the pad, no beacon inside the field,
        well away from the rover."""
        if (x - self.rover[0]) ** 2 + (y - self.rover[1]) ** 2 < 15 ** 2 or not beacon_clear(x, y, 6.0):
            return False
        ci, cj = int(math.floor(x / CHUNK)), int(math.floor(y / CHUNK))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for (cx, cy, cr, _) in self._raw(ci + di, cj + dj)[0]:
                    if (cx - x) ** 2 + (cy - y) ** 2 < (cr + 5.5) ** 2:
                        return False
        return True

    def pick_base_site(self, rng, dmin, dmax):
        """A random clear spot dmin..dmax tiles from the rover (any direction)."""
        rx, ry = self.rover
        for _ in range(400):
            a = rng.uniform(0, math.tau); d = rng.uniform(dmin, dmax)
            x, y = rx + math.cos(a) * d, ry + math.sin(a) * d
            if self.base_site_ok(x, y):
                return (round(x * 2) / 2, round(y * 2) / 2)
        return (rx + dmin, ry + 6.0)

    def blocked(self, x, y, z, rad=0.3):
        if z < rv.height_at(self.rover[0], self.rover[1], x, y, rad) * 0.95:
            return True
        if z < bs.height_at(self.base[0], self.base[1], x, y, rad) * 0.95:
            return True
        ci, cj = int(math.floor(x / CHUNK)), int(math.floor(y / CHUNK))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for rk in self.chunk_rocks(ci + di, cj + dj):
                    if z < rk.h * 0.9 and (rk.x - x) ** 2 + (rk.y - y) ** 2 < (rk.size * 0.48 + rad) ** 2:
                        return True
        return False

    def beacons_in(self, x0, y0, x1, y1):
        """Beacon positions in a tile rectangle (none on the rover or the base)."""
        out = []
        g = BEACON_GAP
        for i in range(math.floor(x0 / g), math.floor(x1 / g) + 1):
            for j in range(math.floor(y0 / g), math.floor(y1 / g) + 1):
                x, y = (i + 0.5) * g, (j + 0.5) * g
                if rv.in_footprint(*self.rover, x, y, 0.8) or bs.in_footprint(*self.base, x, y, 0.8):
                    continue
                out.append((x, y))
        return out

    def floor_at(self, x, y):
        """Height of whatever is underfoot. Memoised on a 1/16-tile grid: aliens and
        particles ask this every frame and the answer only changes when the base moves."""
        k = (int(x * 16.0 + 0.5) if x >= 0 else -int(-x * 16.0 + 0.5), int(y * 16.0 + 0.5) if y >= 0 else -int(-y * 16.0 + 0.5))
        fc = self.__dict__.setdefault("_floor_cache", {})
        f = fc.get(k)
        if f is None:
            if len(fc) > 60000:
                fc.clear()
            f = fc[k] = self._floor_at(k[0] / 16.0, k[1] / 16.0)
        return f

    def _floor_at(self, x, y):
        ci, cj = int(math.floor(x / CHUNK)), int(math.floor(y / CHUNK))
        f = max(rv.height_at(self.rover[0], self.rover[1], x, y, 0.3), bs.height_at(self.base[0], self.base[1], x, y))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for rk in self.chunk_rocks(ci + di, cj + dj):
                    if (rk.x - x) ** 2 + (rk.y - y) ** 2 < (rk.size * 0.42) ** 2:
                        f = max(f, rk.h * 0.92)
        return f

    # ------------------------------------------------------------ ground
    def ground_ready(self, ci, cj):
        return (ci, cj) in self.ground

    def request_ground(self, ci, cj):
        """Queue a chunk's ground for the background builders (no-op if done or queued)."""
        k = (ci, cj)
        if k in self.ground or k in self.pending or k in self.queue:
            return
        crs = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                crs += self._raw(ci + di, cj + dj)[0]
        with self.lock:
            self.queue[k] = crs
        self.wake.set()

    def _builder(self):
        # always builds the queued chunk nearest the player first
        while True:
            self.wake.wait()
            with self.lock:
                if not self.queue:
                    self.wake.clear()
                    continue
                fi, fj = self.focus
                k = min(self.queue, key=lambda q: (q[0] - fi) ** 2 + (q[1] - fj) ** 2)
                crs = self.queue.pop(k)
                self.pending[k] = None
            try:
                res = self.procs.submit(ground_pixels, k[0], k[1], crs, SEED).result()
            except Exception:
                res = ground_pixels(k[0], k[1], crs)
            with self.lock:
                if k in self.pending:
                    self.done[k] = res

    def collect(self, limit=2):
        """Turn finished background chunks into Surfaces (cheap, main thread)."""
        with self.lock:
            ks = list(self.done)[:limit]
            items = [(k, self.done.pop(k)) for k in ks]
            for k in ks:
                self.pending.pop(k, None)
        for k, res in items:
            self._finish(k, res)

    def ground_surface(self, ci, cj, wait=True):
        k = (ci, cj)
        if k in self.ground:
            return self.ground[k]
        with self.lock:
            crs = self.queue.pop(k, None)
            res = self.done.pop(k, None)
            if res is not None:
                self.pending.pop(k, None)
        if res is None and crs is not None:
            res = ground_pixels(ci, cj, crs)       # not started yet: build it now
        elif res is None and k in self.pending:
            if not wait:
                return None
            while res is None:                      # a builder is on it: wait for that
                time.sleep(0.001)
                with self.lock:
                    res = self.done.pop(k, None)
            with self.lock:
                self.pending.pop(k, None)
        elif res is None:
            if not wait:
                self.request_ground(ci, cj)
                return None
            crs = []
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    crs += self._raw(ci + di, cj + dj)[0]
            res = ground_pixels(ci, cj, crs)
        self._finish(k, res)
        return self.ground[k]

    def _finish(self, k, res):
        rgb, mask, shade, ms = res
        surf = pygame.Surface((CW, CH), pygame.SRCALPHA)
        surf.blit(pygame.surfarray.make_surface(rgb), (0, 0))
        al = pygame.surfarray.pixels_alpha(surf); al[:] = mask; del al
        r = chunk_rng(k[0], k[1], 9)              # pebbles, lit from the east
        for _ in range(300):
            a, b = r.uniform(0, CHUNK), r.uniform(0, CHUNK)
            x, y = (a - b) * HW + CW / 2, (a + b) * HH
            if 1 <= x < CW - 3 and 1 <= y < CH - 3:
                g = int(rgb[int(x), int(y), 0])
                rr = r.choice((1, 1, 1, 1.5, 2, 2.5))
                pygame.draw.circle(surf, (max(0, g - 55),) * 3, (x - 1, y + 1), rr)
                pygame.draw.circle(surf, (min(255, g + 35),) * 3, (x, y), max(1, rr - 0.5))
        al = pygame.surfarray.pixels_alpha(surf); al[:] = np.minimum(al, mask); del al
        if pygame.display.get_surface():
            surf = surf.convert_alpha()
        self.ground[k] = surf
        self.gen_ms.append(ms)

    # ------------------------------------------------------------ boulders
    def rock_sprite(self, rk, pix=None):
        """A lumpy boulder rendered as a lit heightfield (light from the east)."""
        s = self.sprites.get(rk.key)
        if s:
            return s
        img, al, W_, H_, ox, oy, a = pix if pix is not None else rock_pixels(rk)
        SS = 2
        big = pygame.Surface((W_ * SS, H_ * SS), pygame.SRCALPHA)
        pygame.surfarray.blit_array(big, img)
        pa = pygame.surfarray.pixels_alpha(big); pa[:] = al; del pa
        small = pygame.transform.smoothscale(big, (W_, H_))
        out = pygame.Surface((W_, H_), pygame.SRCALPHA)
        sh = pygame.Rect(0, 0, a * TW * 1.15, a * TH * 1.0)
        sh.center = (ox - a * HW * 0.45, oy + 1)
        pygame.draw.ellipse(out, (0, 0, 0, 70), sh.inflate(6, 3))
        pygame.draw.ellipse(out, (0, 0, 0, 70), sh)
        out.blit(small, (0, 0))
        res = (out.convert_alpha() if pygame.display.get_surface() else out, ox, oy)
        self.sprites[rk.key] = res
        return res

    def request_rocks(self, ci, cj):
        k = (ci, cj)
        if k in self.rock_pending or k in self.rock_done:
            return
        todo = [rk for rk in self.chunk_rocks(ci, cj) if rk.key not in self.sprites]
        self.rock_pending[k] = self.rock_pool.submit(rock_batch, todo)

    def collect_rocks(self, budget=0.002):
        t0 = time.perf_counter()
        for k in [k for k, f in self.rock_pending.items() if f.done()]:
            if k not in self.rock_queue:
                self.rock_queue[k] = list(self.rock_pending[k].result())
        for k in list(self.rock_queue):
            q = self.rock_queue[k]
            while q and time.perf_counter() - t0 < budget:
                rk, pix = q.pop()
                self.rock_sprite(rk, pix)
            if not q:
                del self.rock_queue[k]; del self.rock_pending[k]; self.rock_done.add(k)
            if time.perf_counter() - t0 >= budget:
                break

    # ------------------------------------------------------------ cache
    def evict(self, ci, cj, keep=3):
        with self.lock:
            for d in (self.queue, self.done, self.pending):
                for k in [k for k in d if abs(k[0] - ci) > keep or abs(k[1] - cj) > keep]:
                    del d[k]
        for k in [k for k in self.rock_pending if abs(k[0] - ci) > keep or abs(k[1] - cj) > keep]:
            self.rock_pending.pop(k).cancel(); self.rock_queue.pop(k, None)
        self.rock_done = {k for k in self.rock_done if abs(k[0] - ci) <= keep and abs(k[1] - cj) <= keep}
        for store in (self.ground, self.rocks):
            for k in [k for k in store if abs(k[0] - ci) > keep or abs(k[1] - cj) > keep]:
                if store is self.rocks:
                    for rk in store[k]:
                        self.sprites.pop(rk.key, None)
                del store[k]
        for k in [k for k in self.raw if abs(k[0] - ci) > keep + 1 or abs(k[1] - cj) > keep + 1]:
            del self.raw[k]
