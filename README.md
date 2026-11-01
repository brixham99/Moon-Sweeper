# Moon Sweeper

An endless, procedurally generated lunar surface in strict 2:1 isometric, with a
jetpacking Jetman under tank-style controls. pygame 2 + numpy.

## Run
    python3 game.py              # 1280x720 window, title screen first (F toggles full screen; Esc on the title quits)
    python3 game.py --fullscreen # start in full screen
    python3 game.py --shot NAME [walk|high|fire|behind|far]   # -> ../lunar_poc/NAME.png
    python3 game.py --facings NAME                             # sprite sheet of all facings
    python3 game.py --bench
    python3 game.py --selftest                     # quick headless checks of the game rules (PASS/FAIL)
    python3 game.py --playthrough [LEVELS] [SEED]  # the robot in autoplay.py plays; one report line per level
                                                   # (times, hits, rests and lowest stamina – used to tune the difficulty)
    python3 tools/record_clip.py [OUT.mp4] [SECONDS] [SEED] [LEVEL]  # robot gameplay clip with sound (needs ffmpeg)

Needs pygame-ce (or classic pygame 2) and numpy: `pip install -r requirements.txt`.
With pygame-ce, shapes and effects are antialiased (`aa.py`); classic pygame falls back to plain drawing.

## Controls
Left/Right turn · Up forward · Down back up · Space jetpack · Ctrl or Z fire · B drop the bomb ·
P pause · F full screen · Esc twice quits to the title screen · Enter starts a game.
The game also pauses by itself if the window loses focus.

## The game
Pick up the bomb beside the rover (walk or fly into it), carry it to the Alien Missile
Base (follow the red beacons) and drop it with B from above the dome – the force field
lets the bomb through. A sloppy drop that misses the field is a miss: the bomb lands on the
ground for you to pick up again; a bomb that lands hard against the field rolls clear so you
can reach it. On foot Jetman carries the bomb in front of him; in flight it
hangs under his boots. The base goes up, leaving a giant scorched crater, and the next
level starts with a new kind of alien and the base in a new random spot 40–80 tiles from
the rover. Beacons stand every 14 tiles; the ones on the line to the base glow red.

- **Levels:** 1 Spore Hoppers · 2 Rock Creepers (ground crawlers that hunt you when you're
  low) · 3 Void Rays (gliders that cruise high and swoop at you) · 4 Crater Worms · 5 Glow
  Leeches · 6 Mine Beetles · 7+ everything at once. The older kinds get 15% faster each level.
- **Crater Worms (level 4):** tunnel under the regolith – you'll see a travelling trail of
  rubble – and follow you if you stay low (under about 60 px). A rumble and a heaving mound
  give you half a second's warning before one bursts out beneath you. It can only be shot
  while it's reared up; hovering keeps you out of reach.
- **Glow Leeches (level 5):** slow, glowing jellyfish that home in at chest height and
  latch on, draining stamina for as long as they cling. A full second of jetpack shakes one
  off (it drifts away dazed) – or shoot it before it reaches you.
- **Mine Beetles (level 6):** armoured flying beetles that patrol, then race ahead of you
  and drop spore mines on your path. Mines thud down, arm after a second and blink; get
  within about a tile and the fuse runs for half a second before the blast. Shoot mines
  from a distance to clear the way (the blast takes out other mines and aliens nearby).
- **The base fights back:** it keeps a ring of Seeker Spores (glowing orange) circling over
  the dome, and when you come within about 36 tiles it charges up (the dome glows) and
  spits out more. Near the base, up to two at a time break off and home in on you – slow
  enough to dodge or shoot, and each burns out after a few seconds. They can't come down
  to the ground, so walking under them is safe. A spore hit costs stamina and, if you're
  carrying the bomb, knocks it loose (it falls away from the base – pick it up again);
  the rest of the spores pull back for a moment after a hit. More of them, a little
  faster, on each level. They all fizzle out when the base goes up.
- **Jetpack fuel:** 30 s of thrust per tank (about 195 tiles at top speed, so every base is
  well within one tank). Walking costs nothing.
- **Stamina – the only thing that keeps you going (there are no lives):** it ebbs slowly
  (3 min 20 s of standing about, a little faster with the jetpack on), and every alien that
  gets you takes a bite: Spore Hopper 10 · Rock Creeper 12 · Void Ray 15 · Seeker Spore 12 ·
  Crater Worm 16 · Glow Leech 5 plus 6 a second while it clings · Spore Mine 22, with bites
  6% harder each level. After a hit Jetman blinks for a second and can't be hurt again.
  Land on the rover roof to refuel and rest (30 a second). Stamina carries over between
  levels, with 25 back as a breather. The big meter in the HUD flashes and the screen edges
  glow red below a quarter, with a second, urgent warning below 10%. At zero Jetman is
  exhausted and the game is over. At the start of each level he blinks for 2 s and can't
  be hurt.
- **Scoring:** Spore Hopper 100 · Rock Creeper 150 · Crater Worm 200 · Void Ray 250 · Glow Leech 300 ·
  Mine Beetle 350 · Spore Mine 25 · Seeker Spore 50 · base destroyed
  2,000 × level · 5 for each new 4×4-tile patch of ground explored.
- **High scores:** an online Top 10 on dreamlo (as in Daleks in Hedges and Haunted Mansion),
  with country flags, a local fallback in `~/.moon_sweeper/`, and name entry when you make it.

## Files
- `world.py` – infinite surface. Chunks of 16×16 tiles are seeded from a hash of
  (seed, chunk x, chunk y). Craters are painted from the 3×3 neighbourhood and
  carved oldest-first into a heightfield, then lit from the east; boulders and
  ridges are filed under the chunk they sit in, so nothing is cut at a border.
  Ground and boulder pixels are built in worker processes, nearest-first, with
  prefetch around and ahead of the player; chunks beyond 3 away are evicted.
- `jetman.py` – Jetman as a small 3D rig of lit spheres/capsules rendered to 8 true
  facings (idle, 8-frame walk, 5 flight lean levels, recoil variants).
- `game.py` – physics, effects, depth sort, silhouette, shots and bench.
- `campaign.py` – the game: levels, bomb, fuel, stamina, scoring, HUD, title
  screen, level cards, game over and name entry.
- `autoplay.py` – a robot player using the real controls, for play-throughs and clips.
- `selftest.py` – the `--selftest` checks.
- `aa.py` – antialiased drop-ins for `pygame.draw` (pygame-ce), with safe fallbacks.
- `scores.py` – dreamlo high-score table with an offline fallback and flags.
- `sfx.py` – all sound effects, synthesised at start-up.
- `assets/art/` – painted title art, bomb, Rock Creeper and Void Ray sprites
  (cut out by `tools/cutout.py`). `creeper_walk.png` is the Rock Creeper's 8-frame
  scuttle cycle, built from `creeper.png` by `tools/creeper_frames.py` (each painted
  leg swung about its root, alternate legs half a cycle apart).
- `assets/sounds/title_theme.ogg` – title music, generated by `tools/make_music.py`.
- `assets/esa_logo.png` – the ESA emblem, warped onto the rover's side at sprite-build time (keep it alongside the code).

## Aliens (`aliens.py`)
Spiky, coronavirus-like aliens bounce about in low gravity, drift towards Jetman when he is near and bounce off boulders. They spawn per chunk (deterministic) and despawn far away. Sprites are prerendered sphere impostors (lit from the east) in four spike colours with a slow spin; landing squash and airborne stretch are applied at draw time. Shoot them to pop them; touching one knocks Jetman back and costs stamina. `python3 game.py --aliens NAME` saves a sprite sheet.
