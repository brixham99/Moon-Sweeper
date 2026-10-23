# Moon Sweeper

An endless, procedurally generated lunar surface in strict 2:1 isometric, with a
jetpacking Jetman under tank-style controls. pygame 2 + numpy.

## Run
    python3 game.py              # full screen (F toggles, Esc quits)
    python3 game.py --windowed
    python3 game.py --shot NAME [walk|high|fire|behind|far]   # -> ../lunar_poc/NAME.png
    python3 game.py --facings NAME                             # sprite sheet of all facings
    python3 game.py --bench

## Controls
Left/Right turn · Up forward · Down back up · Space jetpack · Ctrl or Z fire

## Files
- `world.py` – infinite surface. Chunks of 16×16 tiles are seeded from a hash of
  (seed, chunk x, chunk y). Craters are painted from the 3×3 neighbourhood and
  carved oldest-first into a heightfield, then lit from the east; boulders and
  ridges are filed under the chunk they sit in, so nothing is cut at a border.
  Ground and boulder pixels are built in worker processes, nearest-first, with
  prefetch around and ahead of the player; chunks beyond 3 away are evicted.
- `jetman.py` – Jetman as a small 3D rig of lit spheres/capsules rendered to 8 true
  facings (idle, 8-frame walk, 5 flight lean levels, recoil variants).
- `game.py` – physics, effects, depth sort, silhouette, HUD, shots and bench.

## Aliens (`aliens.py`)
Spiky, coronavirus-like aliens bounce about in low gravity, drift towards Jetman when he is near and bounce off boulders. They spawn per chunk (deterministic) and despawn far away. Sprites are prerendered sphere impostors (lit from the east) in four spike colours with a slow spin; landing squash and airborne stretch are applied at draw time. Shoot them to pop them; touching one knocks Jetman back. `python3 game.py --aliens NAME` saves a sprite sheet.
