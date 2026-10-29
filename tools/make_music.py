"""Moon Sweeper title theme: an original, jaunty space-age loop, synthesised with numpy.

16 bars of 4/4 at 138 bpm in C major with a little swing: a bouncy bass, an arpeggiated
'computer' chord line, a pulse-wave lead with vibrato and a light drum kit. The end runs
straight back into the start, so it loops seamlessly. Writes assets/sounds/title_theme.ogg.
Run:  python3 tools/make_music.py
"""
import os
import subprocess
import wave

import numpy as np

RATE = 44100
BPM = 138
BEAT = 60 / BPM
SW = 0.58                     # swing: the first quaver of each pair is a touch long
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "sounds")

NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def hz(name):
    n, o = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((NOTE[n] + 12 * (o + 1) - 69) / 12)


def qtime(q):
    """Start time of quaver q (with swing)."""
    pair, half = divmod(q, 2)
    return pair * BEAT + (BEAT * SW if half else 0.0)


CHORDS = ["C", "Am", "F", "G", "C", "E7", "Am", "D7", "F", "G", "Em", "Am", "Dm", "G7", "C", "G7"]
TONES = {"C": ["C", "E", "G"], "Am": ["A", "C", "E"], "F": ["F", "A", "C"], "G": ["G", "B", "D"], "E7": ["E", "G#", "D"],
         "D7": ["D", "F#", "C"], "Em": ["E", "G", "B"], "Dm": ["D", "F", "A"], "G7": ["G", "B", "F"]}
# lead: (quaver start within the 16 bars, length in quavers, note)
MEL = """
E5 2,G5 1,C6 2,G5 1,E5 2 | A5 3,G5 1,E5 2,C5 2 | F5 2,A5 1,C6 2,A5 1,F5 2 | G5 3,F5 1,E5 2,D5 2 |
E5 2,G5 1,C6 2,D6 1,E6 2 | D6 1,B5 1,G#5 2,B5 2,E5 2 | A5 2,C6 1,E6 2,C6 1,A5 2 | F#5 3,A5 1,C6 2,D6 2 |
C6 2,A5 1,F5 2,A5 1,C6 2 | B5 3,A5 1,G5 2,D5 2 | G5 2,E5 1,B4 2,E5 1,G5 2 | A5 4,C6 2,E6 2 |
D6 2,C6 1,A5 2,F5 1,D5 2 | B4 2,D5 2,F5 2,G5 2 | C6 3,G5 1,E5 2,G5 2 | B5 2,A5 1,G5 2,F5 1,D5 2
"""


def parse_mel():
    out, q = [], 0
    for bar in MEL.replace("\n", " ").split("|"):
        for tok in bar.split(","):
            tok = tok.strip()
            if not tok:
                continue
            n, l = tok.split()
            out.append((q, int(l), n)); q += int(l)
    return out


def env(n, a, d, s, r):
    e = np.full(n, s, dtype=np.float64)
    a, d, r = int(a * RATE), int(d * RATE), min(int(r * RATE), n)
    e[:a] = np.linspace(0, 1, a) if a else e[:a]
    e[a:a + d] = np.linspace(1, s, len(e[a:a + d]))
    e[-r:] *= np.linspace(1, 0, r)
    return e


def pulse(f, n, width=0.25, vib=0.0):
    t = np.arange(n) / RATE
    ph = np.cumsum(f * (1 + vib * np.sin(2 * np.pi * 5.5 * t) * np.clip(t / 0.15 - 0.5, 0, 1))) / RATE
    x = ((ph % 1) < width).astype(np.float64) * 2 - 1
    return x - x.mean()


def lp(x, a):
    """Cheap one-pole low-pass via a short exponential FIR."""
    k = a ** np.arange(64); k /= k.sum()
    return np.convolve(x, k)[:len(x)]


def main():
    total = 16 * 4 * BEAT
    N = int(total * RATE)
    mix = np.zeros((N + RATE, 2))

    def add(sig, t0, gain, pan=0.5):
        i = int(t0 * RATE)
        mix[i:i + len(sig), 0] += sig * gain * np.cos(pan * np.pi / 2)
        mix[i:i + len(sig), 1] += sig * gain * np.sin(pan * np.pi / 2)

    rng = np.random.default_rng(3)
    for bar, ch in enumerate(CHORDS):
        tones = TONES[ch]
        root = tones[0]
        # bass: root - fifth bounce on each beat, octave pop on the off-beat
        for b in range(4):
            nm = root if b % 2 == 0 else tones[2]
            f = hz(nm + "2")
            n = int(BEAT * 0.55 * RATE)
            s = np.sin(2 * np.pi * np.cumsum(np.full(n, f)) / RATE) + 0.35 * np.sign(np.sin(2 * np.pi * np.cumsum(np.full(n, f)) / RATE))
            add(lp(s, 0.6) * env(n, 0.004, 0.12, 0.5, 0.05), qtime(bar * 8 + b * 2), 0.32)
            n2 = int(BEAT * 0.2 * RATE)
            s2 = np.sin(2 * np.pi * np.cumsum(np.full(n2, f * 2)) / RATE)
            add(s2 * env(n2, 0.003, 0.05, 0.3, 0.04), qtime(bar * 8 + b * 2 + 1), 0.16)
        # arpeggio: semiquaver 'computer' twinkle across the chord
        for i in range(16):
            nm = tones[[0, 1, 2, 1][i % 4]] + ("5" if i % 8 < 4 else "4")
            n = int(BEAT / 4 * 0.9 * RATE)
            t0 = bar * 4 * BEAT + i * BEAT / 4
            add(pulse(hz(nm), n, 0.125) * env(n, 0.002, 0.04, 0.25, 0.03), t0, 0.05, 0.25 + 0.5 * (i % 2))
        # drums
        for b in range(4):
            t0 = bar * 4 * BEAT + b * BEAT
            if b in (0, 2):
                n = int(0.18 * RATE); t = np.arange(n) / RATE
                add(np.sin(2 * np.pi * np.cumsum(55 + 110 * np.exp(-t * 35)) / RATE) * np.exp(-t * 16), t0, 0.5)
            else:
                n = int(0.16 * RATE); t = np.arange(n) / RATE
                sn = rng.standard_normal(n); sn = sn - lp(sn, 0.7)
                add((sn * 0.7 + 0.4 * np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 22), t0, 0.22)
            for h in (0, 1):
                n = int(0.05 * RATE); t = np.arange(n) / RATE
                hh = rng.standard_normal(n); hh = hh - lp(hh, 0.9)
                add(hh * np.exp(-t * 70), qtime(bar * 8 + b * 2 + h), 0.07 if h else 0.1, 0.7)
    # lead, a touch of echo
    for q, l, nm in parse_mel():
        t0, t1 = qtime(q), qtime(q + l)
        n = int((t1 - t0) * 0.92 * RATE)
        s = pulse(hz(nm), n, 0.3, 0.008) * 0.6 + pulse(hz(nm) * 1.004, n, 0.5) * 0.4
        s = lp(s, 0.45) * env(n, 0.006, 0.08, 0.7, 0.05)
        add(s, t0, 0.16, 0.45)
        add(s, t0 + BEAT * 0.75, 0.05, 0.75)
    # wrap the tail (echoes, release) round to the start for a seamless loop
    out = mix[:N].copy()
    out[:RATE] += mix[N:N + RATE]
    out = out / np.abs(out).max() * 0.85
    os.makedirs(OUT, exist_ok=True)
    wav = os.path.join(OUT, "title_theme.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes((out * 32767).astype("<i2").tobytes())
    ogg = os.path.join(OUT, "title_theme.ogg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "libvorbis", "-q:a", "5", ogg], check=True)
    os.remove(wav)
    print("wrote", ogg, f"{total:.1f} s")


if __name__ == "__main__":
    main()
