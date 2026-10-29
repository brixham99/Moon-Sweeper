"""Synthesised sound effects for Moon Sweeper (numpy, no samples).

All sounds are generated at start-up (a fraction of a second). Alien sounds are
attenuated by their distance from Jetman in world tiles and panned by screen x.
If there is no audio device the game carries on silently.
"""
import math
import numpy as np

RATE = 44100
NEAR, FAR = 2.5, 14.0         # tiles: full volume inside NEAR, silent beyond FAR (about a screen away)
SPIN_VOICES = 3                # nearest aliens that get a whirr loop
rng = np.random.default_rng(7)


def _t(sec):
    return np.arange(int(sec * RATE)) / RATE


def _lowpass(x, cutoff):
    """One-pole low-pass (cutoff may be an array)."""
    a = np.exp(-2 * np.pi * np.broadcast_to(cutoff, x.shape) / RATE)
    y = np.empty_like(x); s = 0.0
    for i in range(len(x)):          # fine for short buffers at start-up
        s = x[i] + a[i] * (s - x[i]); y[i] = s
    return y


def _env(n, att, rel):
    e = np.ones(n)
    a, r = int(att * RATE), int(rel * RATE)
    if a: e[:a] = np.linspace(0, 1, a)
    if r: e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


def _norm(x, peak=0.8):
    return x / (np.abs(x).max() + 1e-9) * peak


def thrust():
    """1.5 s seamless loop: low filtered rumble plus a breathy hiss."""
    n = int(1.5 * RATE)
    noise = rng.standard_normal(n + RATE // 2)
    rumble = _lowpass(_lowpass(noise, 180), 260)
    hiss = noise - _lowpass(noise, 2500)
    hiss = _lowpass(hiss, 6000) * 0.12
    x = rumble * 3 + hiss
    flick = 1 + 0.15 * np.sin(2 * np.pi * 9 * np.arange(len(x)) / RATE)
    x = x * flick
    # crossfade the tail into the head for a click-free loop
    body, tail = x[:n].copy(), x[n:n + RATE // 2]
    f = np.linspace(0, 1, len(tail))
    body[:len(tail)] = body[:len(tail)] * np.sqrt(f) + tail * np.sqrt(1 - f)
    return _norm(body, 0.7)


def phaser():
    """Retro descending zap: square-ish sweep 1800 -> 220 Hz with a ring."""
    t = _t(0.22)
    f = 220 + 1580 * np.exp(-t * 18)
    ph = 2 * np.pi * np.cumsum(f) / RATE
    x = np.tanh(3 * np.sin(ph)) * 0.7 + 0.3 * np.sin(ph * 1.503)
    return _norm(x * _env(len(t), 0.002, 0.12), 0.55)


def hurt():
    """Thud + crunchy noise burst + a falling 'oof' tone."""
    t = _t(0.45)
    thud = np.sin(2 * np.pi * np.cumsum(140 * np.exp(-t * 6) + 45) / RATE) * np.exp(-t * 9)
    crunch = _lowpass(rng.standard_normal(len(t)), 1800) * np.exp(-t * 22) * 4
    oof = np.sign(np.sin(2 * np.pi * np.cumsum(420 - 260 * t / 0.45) / RATE)) * 0.25 * np.exp(-t * 5)
    return _norm((thud + crunch + oof) * _env(len(t), 0.001, 0.08), 0.85)


def bounce():
    """Soft rubbery boing: low sine with a pitch bend up then wobble."""
    t = _t(0.28)
    f = 95 + 120 * (1 - np.exp(-t * 30)) + 18 * np.sin(2 * np.pi * 14 * t) * np.exp(-t * 8)
    x = np.sin(2 * np.pi * np.cumsum(f) / RATE)
    x += 0.25 * np.sin(2 * np.pi * np.cumsum(f * 2.01) / RATE)
    return _norm(x * np.exp(-t * 11) * _env(len(t), 0.004, 0.05), 0.6)


def spin():
    """1.0 s seamless whirr loop: whole-number cycles so it wraps cleanly."""
    t = _t(1.0)
    wob = np.sin(2 * np.pi * 6 * t)                         # 6 Hz warble
    f = 330 + 40 * wob
    ph = 2 * np.pi * np.cumsum(f) / RATE
    ph *= round(ph[-1] / (2 * np.pi)) * 2 * np.pi / ph[-1]
    x = np.sin(ph) * 0.6 + 0.3 * np.sin(2 * ph + 1) + 0.15 * np.sin(3 * ph)
    x *= 0.65 + 0.35 * np.sin(2 * np.pi * 12 * t) ** 2       # blade-like chop
    return _norm(x, 0.45)


def pop():
    """Wet splat: noise burst with a quick upward chirp."""
    t = _t(0.3)
    chirp = np.sin(2 * np.pi * np.cumsum(300 + 1500 * t / 0.3) / RATE) * np.exp(-t * 20)
    splat = _lowpass(rng.standard_normal(len(t)), 900 + 2500 * np.exp(-t * 15)) * np.exp(-t * 12) * 3
    return _norm((chirp + splat) * _env(len(t), 0.001, 0.06), 0.8)


def boom():
    """The base going up: a sub-bass drop, a crackling roar and a long rumbling tail."""
    t = _t(2.8)
    n = len(t)
    noise = rng.standard_normal(n)
    sub = np.sin(2 * np.pi * np.cumsum(30 + 90 * np.exp(-t * 5)) / RATE) * np.exp(-t * 1.6)
    roar = _lowpass(noise, 300 + 3000 * np.exp(-t * 4)) * np.exp(-t * 1.4) * 3
    crackle = (rng.random(n) < 0.002 * np.exp(-t * 1.5)) * rng.standard_normal(n) * 6
    x = np.tanh(1.6 * (sub * 1.4 + roar + crackle))
    return _norm(x * _env(n, 0.002, 1.2), 0.95)


def pickup():
    """Bomb clipped on: a two-note rising blip."""
    t = _t(0.22)
    f = np.where(t < 0.09, 660, 990)
    x = np.sign(np.sin(2 * np.pi * np.cumsum(f) / RATE)) * 0.5 + np.sin(2 * np.pi * np.cumsum(f * 2) / RATE) * 0.3
    return _norm(x * _env(len(t), 0.002, 0.08), 0.55)


def drop():
    """Bomb away: a falling whistle."""
    t = _t(0.7)
    f = 1500 * np.exp(-t * 1.6) + 250
    x = np.sin(2 * np.pi * np.cumsum(f) / RATE) * (1 + 0.2 * np.sin(2 * np.pi * 9 * t))
    return _norm(x * _env(len(t), 0.01, 0.25), 0.5)


def levelup():
    """Level complete: a quick major arpeggio fanfare."""
    notes = [523.25, 659.25, 783.99, 1046.5, 783.99, 1046.5]
    parts = []
    for i, f in enumerate(notes):
        t = _t(0.11 if i < 5 else 0.5)
        x = np.sign(np.sin(2 * np.pi * f * t)) * 0.4 + np.sin(2 * np.pi * f * 2 * t) * 0.25
        parts.append(x * _env(len(t), 0.003, 0.05 if i < 5 else 0.35))
    return _norm(np.concatenate(parts), 0.6)


BUILDERS = {"thrust": thrust, "phaser": phaser, "hurt": hurt, "bounce": bounce, "spin": spin, "pop": pop,
            "boom": boom, "pickup": pickup, "drop": drop, "levelup": levelup}
GAIN = {"thrust": 0.55, "phaser": 0.45, "hurt": 0.9, "bounce": 0.3, "spin": 0.18, "pop": 0.48,
        "boom": 1.0, "pickup": 0.5, "drop": 0.45, "levelup": 0.55}   # alien sounds at 60%


def build_all():
    return {k: f().astype(np.float32) for k, f in BUILDERS.items()}


def falloff(d):
    """Smooth distance attenuation: 1 within NEAR, 0 beyond FAR, smoothstep between."""
    if d <= NEAR: return 1.0
    if d >= FAR: return 0.0
    u = 1 - (d - NEAR) / (FAR - NEAR)
    return u * u * (3 - 2 * u)


def pan(sx, w):
    """Equal-power stereo gains from screen x."""
    p = min(1.0, max(0.0, sx / w)) * 0.7 + 0.15
    return math.cos(p * math.pi / 2), math.sin(p * math.pi / 2)


class SFX:
    """Plays sounds through pygame.mixer when available; always logs for offline mixing if asked."""

    def __init__(self, log=False):
        self.log = [] if log else None
        self.t = 0.0
        self.ok = False
        self.loops = {}             # name -> current (l, r) volume, for the log
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.pre_init(RATE, -16, 2, 512)
                pygame.mixer.init()
            pygame.mixer.set_num_channels(32)
            pygame.mixer.set_reserved(1 + SPIN_VOICES)
            raw = build_all()
            self.snd = {}
            for k, x in raw.items():
                s16 = (x * GAIN[k] * 32767).astype(np.int16)
                self.snd[k] = pygame.mixer.Sound(buffer=np.ascontiguousarray(np.column_stack([s16, s16])).tobytes())
            self.ch_thrust = pygame.mixer.Channel(0)
            self.ch_spin = [pygame.mixer.Channel(1 + i) for i in range(SPIN_VOICES)]
            self.ch_thrust.play(self.snd["thrust"], loops=-1); self.ch_thrust.set_volume(0)
            for c in self.ch_spin:
                c.play(self.snd["spin"], loops=-1); c.set_volume(0)
            self.ok = True
        except Exception as e:      # no audio device, no numpy, etc.: carry on silently
            print("sound disabled:", e)
        self.thrust_v = 0.0
        self.spin_v = [[0.0, 0.0] for _ in range(SPIN_VOICES)]

    def play(self, name, vol=1.0, lr=(0.707, 0.707)):
        if vol <= 0.003: return
        l, r = vol * lr[0] * 1.414, vol * lr[1] * 1.414
        if self.log is not None:
            self.log.append(("shot", self.t, name, min(1, l), min(1, r)))
        if self.ok:
            ch = self.snd[name].play()
            if ch: ch.set_volume(min(1, l), min(1, r))

    def update(self, dt, thrusting, spinners):
        """thrusting: bool. spinners: list of (vol, (l, r)) for the nearest aliens, loudest first."""
        self.t += dt
        tgt = 1.0 if thrusting else 0.0
        k = min(1.0, dt / (0.06 if thrusting else 0.18))      # quick fade in, softer fade out
        self.thrust_v += (tgt - self.thrust_v) * k
        vols = [("thrust", self.thrust_v * 0.707, self.thrust_v * 0.707)]
        for i in range(SPIN_VOICES):
            v, lr = spinners[i] if i < len(spinners) else (0.0, (0.707, 0.707))
            cur = self.spin_v[i]
            for j in (0, 1):
                cur[j] += (v * lr[j] - cur[j]) * min(1.0, dt * 10)      # glide, no zipper clicks
            vols.append((f"spin{i}", cur[0], cur[1]))
        if self.ok:
            self.ch_thrust.set_volume(vols[0][1] * 1.414, vols[0][2] * 1.414)
            for c, (_, l, r) in zip(self.ch_spin, vols[1:]):
                c.set_volume(min(1, l * 1.414), min(1, r * 1.414))
        if self.log is not None:
            self.log.append(("loops", self.t, vols))
