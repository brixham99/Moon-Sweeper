"""A simple robot player for Moon Sweeper, for headless play-throughs and clips.

It plays with the real controls only (turn, thrust, jetpack, fire, B for the bomb):
walk to the bomb, take off, fly to the Alien Missile Base, shoot anything in the way,
drop the bomb on the dome, and go again on the next level.

    python3 game.py --playthrough [LEVELS] [SEED]   # prints a report per level
"""
import math
import random
import time

import pygame


class Bot:
    CRUISE = 205.0                 # px above the ground: clear of the dome (150) and the rover

    # how quickly he notices a spore, how often he taps fire when lined up, and how many he notices at all
    SKILLS = {"expert": (0.45, 0.35, 1.0), "novice": (0.9, 0.12, 0.6), "pacifist": (9e9, 0.0, 0.0)}

    # stamina below which he heads back to the rover roof for a rest
    REST_AT = {"expert": 45.0, "novice": 22.0, "pacifist": 35.0}

    def __init__(self, game, seed=1, skill="expert"):
        self.g = game
        self.rest_at = self.REST_AT[skill]
        self.REACTION, self.FIRE_P, self.NOTICE = self.SKILLS[skill]
        self.ignored = set()
        self.shoots = skill != "pacifist"
        self.seen = {}
        self.rng = random.Random(seed)
        self.phase = "bomb"
        self.stats = []
        self.cur = None
        self.wobble = 0.0

    # ------------------------------------------------------------ helpers
    def steer(self, tx, ty, k, tol=0.07):
        g = self.g
        want = math.atan2(ty - g.y, tx - g.x) + self.wobble
        da = (want - g.a + math.pi) % math.tau - math.pi
        if da > tol: k["right"] = 1
        elif da < -tol: k["left"] = 1
        return da

    def target(self, reach=7.5):
        """Nearest alien roughly ahead and level with the laser."""
        g = self.g
        best, bd = None, reach
        for a in g.aliens.values():
            if not a.targetable:
                continue
            d = math.hypot(a.x - g.x, a.y - g.y)
            if d < bd:
                best, bd = a, d
        return best, bd

    # ------------------------------------------------------------ per frame
    def keys(self, dt):
        g = self.g
        k = {}
        if g.mode != "play":
            return k
        if self.rng.random() < dt * 0.5:              # a little human sloppiness in the steering
            self.wobble = self.rng.uniform(-0.12, 0.12)
        alt = g.z - g.floor()
        b = g.bomb
        a, d = self.target()
        # tired or nearly out of fuel: go and sit on the rover roof until topped up
        bx, by = g.world.base
        nearly_there = b.state == "carried" and math.hypot(bx - g.x, by - g.y) < 15
        if self.phase == "rest" and g.stamina > 95 and g.fuel > 95:
            self.phase = "fly" if b.state == "carried" else "bomb"
        if self.phase != "rest" and (g.stamina < self.rest_at or g.fuel < 22) and not nearly_there \
                and not getattr(g, "infinite", False):
            self.phase = "rest"
            st = getattr(self, "cur", None)
            if isinstance(st, dict):
                st["rests"] = st.get("rests", 0) + 1
        if self.phase == "rest":
            rx, ry = g.world.rover
            dr = math.hypot(rx - g.x, ry - g.y)
            if g.on_rover_roof():
                if g.stamina > 95 and g.fuel > 95:
                    self.phase = "fly" if b.state == "carried" else "bomb"
                return k
            da = self.steer(rx, ry, k, 0.1)
            if dr > 0.35:
                if alt < (60 if dr < 3 else 120) or g.vz < -80:
                    k["jet"] = 1
                # close in: only push on when facing the rover, or he circles it for ever
                if (dr > 0.6 or math.hypot(g.vx, g.vy) < 0.5) and (dr > 1.5 or abs(da) < 0.15):
                    k["up"] = 1
                if dr < 2.5 and math.hypot(g.vx, g.vy) > 2.0:
                    k.pop("up", None)
            elif g.vz < -120:
                k["jet"] = 1
            self.defend(k)
            return k
        if b.state in ("ground",):
            self.phase = "bomb"
        if self.phase == "bomb":
            if b.state == "carried":
                self.phase = "fly"
            else:
                da = self.steer(b.x, b.y, k)
                db = math.hypot(b.x - g.x, b.y - g.y)
                if db > 0.25 and abs(da) < 1.2:
                    k["up"] = 1
                if db > 4 and (alt < 110 or g.vz < -60):
                    k["jet"] = 1                        # a long way to the bomb: fly, don't walk
                if 1.5 < db < 4 and math.hypot(g.vx, g.vy) > 3.5:
                    k.pop("up", None)                   # ease off before coming down on it
                if alt > 2 and b.z < 1:
                    pass                                # just let him come down
                # shoot what's close on the way (only if it's level with the beam)
                if self.shoots and a and d < 4.5:
                    ang = math.atan2(a.y - g.y, a.x - g.x)
                    if abs((ang - g.a + math.pi) % math.tau - math.pi) < 0.3 and g.can_hit_height(a):
                        k["fire"] = 1
        if self.phase == "fly":
            bx, by = g.world.base
            dist = math.hypot(bx - g.x, by - g.y)
            # climb first, then cruise high above the dome
            if alt < self.CRUISE or g.vz < -60:
                k["jet"] = 1
            if dist < 6 and g.z < 175:
                k["jet"] = 1
            da = self.steer(bx, by, k)
            sp = math.hypot(g.vx, g.vy)
            if abs(da) < 0.5 and alt > 40 and dist > 1.0 and not (dist < 5 and sp > 3.0):
                k["up"] = 1
            if dist < 2.6 and g.z > 168 and b.state == "carried":
                g.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_b))
                self.phase = "away"
            if self.shoots and a and d < 6 and g.can_hit_height(a):
                ang = math.atan2(a.y - g.y, a.x - g.x)
                if abs((ang - g.a + math.pi) % math.tau - math.pi) < 0.35:
                    k["fire"] = 1
        if self.phase == "away":
            if alt < self.CRUISE:
                k["jet"] = 1
        if g.leech is not None and g.fuel > 3:
            k["jet"] = 1                           # a leech on his back: burn hard to shake it off
        self.defend(k)
        return k

    def defend(self, k):
        """A base spore or a Glow Leech closing in: turn to face it and shoot it down."""
        g = self.g
        best, bd = None, 6.0
        for a in g.aliens.values():
            if a.kind == "seeker" or (a.kind == "leech" and a.targetable and g.leech is None):
                d = math.hypot(a.x - g.x, a.y - g.y)
                if d < bd:
                    best, bd = a, d
        if not best:
            self.seen.clear()
            return
        if best.uid not in self.seen and self.rng.random() > self.NOTICE:
            self.ignored.add(best.uid)
        if best.uid in self.ignored:
            return
        t0 = self.seen.setdefault(best.uid, g.t)
        if g.t - t0 < self.REACTION:
            return
        k.pop("left", None); k.pop("right", None)
        da = self.steer(best.x, best.y, k, 0.05)
        if bd < 3.5:
            k.pop("up", None)                      # stand off while it comes into the beam
        if abs(da) < 0.3 and g.can_hit_height(best) and self.rng.random() < self.FIRE_P:
            k["fire"] = 1                          # tapping fire, not holding it


def playthrough(game, levels=3, seed=1, max_level_time=240.0, draw=True, log=print, on_frame=None, skill="expert"):
    """Play `levels` levels with the robot. Returns a list of per-level dicts."""
    g = game
    bot = Bot(g, seed, skill)
    dt = 1 / 60
    g.new_game()
    out = []
    while len(out) < levels:
        lvl = g.level
        st = dict(level=lvl, time=0.0, hits=0, kills0=g.kills_level, min_stamina=100.0, latches=0,
                  stamina_start=g.stamina, fuel_at_drop=None, shots=0, score0=g.score, frame_ms=[])
        hit_prev, n_las, leech_prev = g.hit_t, g.laser_n, None
        bot.phase = "bomb"
        bot.cur = st
        pops0 = g.pops
        st["rests"] = 0
        while g.level == lvl and g.mode != "over":
            k = bot.keys(dt)
            t0 = time.perf_counter()
            g.update(dt, k)
            if draw:
                g.draw()
            st["frame_ms"].append((time.perf_counter() - t0) * 1000)
            if on_frame:
                on_frame(g)
            if g.mode in ("play", "boom", "dying"):
                st["time"] += dt
            if g.hit_t < 0.05 <= hit_prev:
                st["hits"] += 1
            hit_prev = g.hit_t
            if g.leech is not None and leech_prev is None:
                st["latches"] += 1
            leech_prev = g.leech
            st["min_stamina"] = min(st["min_stamina"], g.stamina)
            if bot.phase == "away" and st["fuel_at_drop"] is None:
                st["fuel_at_drop"] = g.fuel
            if st["time"] > max_level_time:
                st["stuck"] = True
                break
        st["shots"] = g.laser_n - n_las
        st["kills"] = g.pops - pops0
        st["score"] = g.score - st["score0"]
        st["out_of_stamina"] = g.mode == "over" or g.mode == "dying"
        ms = sorted(st.pop("frame_ms")) or [0]
        st["frame_ms_median"] = ms[len(ms) // 2]
        st["frame_ms_99"] = ms[int(len(ms) * 0.99)]
        out.append(st)
        log(f"level {lvl}: {st['time']:.1f} s, hits {st['hits']}, leech latches {st['latches']}, "
            f"stamina {st['stamina_start']:.0f} at start, min "
            f"{st['min_stamina']:.0f}, fuel at drop {st['fuel_at_drop'] if st['fuel_at_drop'] is None else round(st['fuel_at_drop'])}, "
            f"shots {st['shots']}, kills {st['kills']}, rests {st['rests']}, score +{st['score']}, frame {st['frame_ms_median']:.1f}/{st['frame_ms_99']:.1f} ms"
            + ("  STUCK" if st.get("stuck") else "") + ("  OUT OF STAMINA: GAME OVER" if st["out_of_stamina"] else ""))
        if g.mode == "over" or st.get("stuck"):
            break
        while g.mode == "card":
            g.update(dt, {})
    return out
