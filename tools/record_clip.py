"""Record a gameplay clip with sound, played by the robot in autoplay.py.

    python3 tools/record_clip.py [OUT.mp4] [SECONDS] [SEED]

Renders headless at 1280x720, 30 fps: a few seconds of the title screen, then the robot
plays from level 1. The title music and every logged game sound are mixed offline, so no
audio device is needed. Needs ffmpeg on the PATH.
"""
import math
import os
import subprocess
import sys
import tempfile
import wave

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pygame

pygame.init()
sc = pygame.display.set_mode((1280, 720))
import autoplay
import game as G
import sfx

OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "moon_sweeper_clip.mp4")
T_END = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 1
START = int(sys.argv[4]) if len(sys.argv) > 4 else 1   # level to start on
FPS_OUT, SUB, dt = 30, 2, 1 / 60
TITLE = 3.0
tmp = tempfile.mkdtemp(prefix="ms_clip_")
vid, wav = os.path.join(tmp, "v.mp4"), os.path.join(tmp, "a.wav")

g = G.Game(sc, sound_log=True)
g.wait_ready(); g.loaded = True
ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x720",
                       "-r", str(FPS_OUT), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                       "-pix_fmt", "yuv420p", vid], stdin=subprocess.PIPE)


def frame():
    ff.stdin.write(pygame.image.tobytes(sc, "RGB") if hasattr(pygame.image, "tobytes") else pygame.image.tostring(sc, "RGB"))


g.mode = "title"
for f in range(int(TITLE * FPS_OUT)):
    g.title_t = f / FPS_OUT + 1; g.t = g.title_t
    g.draw_title(True, 1.0); frame()
g.new_game()
if START > 1:
    g.start_level(START)
bot = autoplay.Bot(g, SEED)
level = g.level
t = 0.0
while t < T_END - TITLE:
    for _ in range(SUB):
        if g.level != level:
            level, bot.phase = g.level, "bomb"
        g.update(dt, bot.keys(dt) if g.mode in ("play", "boom", "dying") else {})
        g.stream()
    g.wait_ready(30); g.fps = 60.0
    g.draw(); frame()
    t += 1 / FPS_OUT
    if int(t * FPS_OUT) % 60 == 0:
        print(f"t={t + TITLE:4.1f} s  mode={g.mode} level={g.level} bomb={g.bomb.state} score={g.score}", flush=True)
ff.stdin.close(); ff.wait()

# audio: the title music, then the logged game sounds and the thrust/spin loops
R = sfx.RATE; N = int(T_END * R)
mus = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", os.path.join(ROOT, "assets", "sounds", "title_theme.ogg"),
                      "-f", "s16le", "-ac", "2", "-ar", str(R), "-"], capture_output=True).stdout
mus = np.frombuffer(mus, np.int16).reshape(-1, 2) / 32767.0 * 0.55
mix = np.zeros((N, 2))
m = mus[:int((TITLE + 0.6) * R)].copy()
fade = int(1.2 * R)
if len(m) > fade:
    m[-fade:] *= np.linspace(1, 0, fade)[:, None]
mix[:len(m)] += m
raw = {k: v * sfx.GAIN[k] for k, v in sfx.build_all().items()}
off = int(TITLE * R)
lt, lv = [], []
for e in g.sfx.log:
    if e[0] == "shot":
        _, t0, name, l, r = e
        i = off + int(t0 * R)
        x = raw[name][:max(0, N - i)]
        mix[i:i + len(x), 0] += x * l; mix[i:i + len(x), 1] += x * r
    else:
        lt.append(e[1]); lv.append([v for _, a, b in e[2] for v in (a, b)])
if lt:
    lt = np.array(lt) + TITLE; lv = np.array(lv) * 1.414; ts = np.arange(N) / R
    for j, nm in enumerate(["thrust"] + ["spin"] * sfx.SPIN_VOICES):
        src = np.tile(raw[nm], N // len(raw[nm]) + 2); o = j * 7919
        for c in (0, 1):
            mix[:, c] += src[o:o + N] * np.interp(ts, lt, lv[:, 2 * j + c], left=0)
mix = np.tanh(mix * 0.9) * 0.95
with wave.open(wav, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(R)
    w.writeframes((mix * 32767).astype(np.int16).tobytes())
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", vid, "-i", wav, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-shortest", "-movflags", "+faststart", OUT], check=True)
print("saved", OUT)
