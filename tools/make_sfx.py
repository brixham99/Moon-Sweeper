"""Export every synthesised effect as a 16-bit WAV: python3 tools/make_sfx.py [outdir]"""
import os, sys, wave
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sfx

out = sys.argv[1] if len(sys.argv) > 1 else "assets/sfx"
os.makedirs(out, exist_ok=True)
for k, x in sfx.build_all().items():
    if k in ("thrust", "spin"):
        x = np.tile(x, 3)              # three loops so you can hear it repeat
    with wave.open(os.path.join(out, k + ".wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sfx.RATE)
        w.writeframes((x * sfx.GAIN[k] / max(sfx.GAIN.values()) * 32767).astype(np.int16).tobytes())
    print("wrote", os.path.join(out, k + ".wav"))
