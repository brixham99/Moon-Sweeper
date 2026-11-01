"""Quick headless checks of Moon Sweeper's game rules:  python3 game.py --selftest

Prints PASS/FAIL per check and returns the number of failures."""
import math
import time

import pygame

import aliens as al
import campaign as cp


def _key(k):
    return pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode="")


def run(game_cls, screen):
    fails = []

    def check(name, ok, info=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info else ""), flush=True)
        if not ok:
            fails.append(name)

    t0 = time.time()
    g = game_cls(screen)
    g.wait_ready(); g.loaded = True
    check("sprites build or load", bool(g.jet_frames) if hasattr(g, "jet_frames") else True, f"{time.time() - t0:.1f} s")
    idle = {k: 0 for k in ("left", "right", "up", "down", "jet", "fire")}

    def run_for(sec, keys=idle, draw=False):
        for _ in range(int(sec * 60)):
            g.update(1 / 60, keys)
            if draw:
                g.draw()

    g.new_game()
    check("new game starts in play", g.mode == "play" and g.lives == 3 and g.level == 1)
    run_for(0.5, draw=True)
    check("safe after start", g.safe_t > 0)
    st = g.stamina
    g.hurt_stamina(30)
    check("no stamina lost while safe", g.stamina == st)
    run_for(2.0)
    check("safe time runs out", g.safe_t == 0)
    g.hurt_stamina(10)
    check("stamina lost when not safe", g.stamina < st)

    # pause and Esc
    g.handle_key(_key(pygame.K_p))
    check("P pauses", g.paused)
    g.draw()
    g.handle_key(_key(pygame.K_p))
    check("P again carries on", not g.paused and g.mode == "play")
    g.handle_key(_key(pygame.K_ESCAPE))
    check("first Esc only asks", g.mode == "play" and g.esc_t > 0)
    g.draw()
    run_for(cp.ESC_CONFIRM + 0.1)
    check("Esc prompt times out", g.esc_t == 0 and g.mode == "play")

    # low stamina warning
    g.stamina = cp.STAMINA_MAX * 0.2
    run_for(0.1)
    check("low stamina warning", g.warned_low and g.notice is not None)
    g.stamina = cp.STAMINA_MAX

    # missed drop: bomb dropped from a height on open ground away from the base
    b = g.bomb
    b.state, b.x, b.y, b.z, b.vx, b.vy, b.vz = "falling", g.x + 3, g.y + 3, 200.0, 0, 0, 0
    g.notice = None
    run_for(1.5)
    check("missed drop lands and says so", b.state == "ground" and bool(g.notice) and "Missed" in g.notice[0], f"{b.state} {g.notice}")

    # shooting an alien gives points and a popup
    a = next(iter(g.aliens.values()))
    sc0, n0 = g.score, len(g.popups)
    g.alien_killed(a)
    check("alien kill scores with a popup", g.score == sc0 + al.KINDS[a.kind][2] and len(g.popups) == n0 + 1)

    # the laser can hit a Rock Creeper (they sit below the barrel, so a level beam used to fly over them)
    def clear_line(bearing, dist):
        sx, sy = g.world.start
        for r in range(20, 300, 2):
            for k in range(12):
                x, y = sx - r * math.cos(k), sy - r * math.sin(k)
                if all(not g.world.blocked(x + math.cos(bearing + t) * d, y + math.sin(bearing + t) * d, 0, 0.1)
                       and abs(g.world.floor_at(x + math.cos(bearing + t) * d, y + math.sin(bearing + t) * d)) < 0.5
                       for d in [i * 0.25 for i in range(int(dist * 4) + 6)] for t in (-0.45, -0.2, 0, 0.2, 0.45)):
                    return x, y
        return sx, sy

    def shoot_at(kind, dist, alt, off_deg, z=None):
        g.x, g.y = clear_line(0.9, dist)
        g.vx = g.vy = g.vz = 0.0
        g.update_aliens(0.0)                              # let the chunks here release their aliens...
        g.aliens.clear(); g.lasers.clear()                # ...then take them away: just the target
        g.safe_t, g.hit_t = 99.0, 9.0
        fl = g.floor()
        ax, ay = g.x + math.cos(0.9) * dist, g.y + math.sin(0.9) * dist
        a = al.make_alien(("selftest", kind), ax, ay, kind, "green", 1, 3)
        a.z = g.world.floor_at(ax, ay) if z is None else z
        a.update = lambda *args, **kw: None               # stands still for the test
        g.aliens[a.uid] = a
        g.a, g.cool = 0.9 + math.radians(off_deg), 0.0
        g.z = fl + alt
        g.update(1 / 60, dict(idle, fire=1))
        for _ in range(40):
            g.z, g.vz = fl + alt, 0.0
            g.update(1 / 60, idle)
        hit = a.uid not in g.aliens
        g.aliens.pop(a.uid, None)
        return hit
    check("laser hits a crawler from the ground in front of it", all(shoot_at("creeper", d, 0, 0) for d in (2.0, 4.0, 7.0)))
    check("laser hits a crawler a little off the line", shoot_at("creeper", 5.0, 0, 7))
    check("a shot well wide of a crawler misses", not shoot_at("creeper", 5.0, 0, 25))
    check("a crawler can't be hit from high above", not shoot_at("creeper", 5.0, 160, 0))
    check("a Void Ray far above the beam isn't helped down", not shoot_at("flyer", 5.0, 0, 0, z=200.0))
    check("a Void Ray level with the beam is hit", shoot_at("flyer", 5.0, 150, 0, z=170.0))

    # the Alien Missile Base and its spores
    def seekers():
        return [a for a in g.aliens.values() if a.kind == "seeker"]
    g.new_game()
    check("base starts with its guard spores", len(seekers()) == al.seeker_tuning(1)[1], str(len(seekers())))
    bx, by = g.world.base
    for a in seekers():
        del g.aliens[a.uid]
    g.safe_t, g.seek_cool = 0.0, 0.0
    g.x, g.y, g.z = bx - 10.0, by, 200.0
    run_for(al.SEEK_CHARGE + 0.3)
    check("base fires a spore when Jetman is in range", len(seekers()) == 1)
    a = seekers()[0]
    sc0, n0 = g.score, len(g.popups)
    g.alien_killed(a)
    check("shooting a spore scores 50 with a popup", g.score == sc0 + 50 and len(g.popups) == n0 + 1)
    g.x, g.y, g.z, g.vx, g.vy, g.vz = bx - 8.0, by, 0.0, 0.0, 0.0, 0.0
    for _ in range(int(3.0 * 60)):
        g.update(1 / 60, idle)
        g.safe_t, g.hit_t = 1.0, 0.0                 # don't let them bump him in this check
    hunters = [a for a in seekers() if a.mode == "hunt"]
    check("spores hunt Jetman when he comes close", bool(hunters))
    low = min((a.z - a.floor for a in hunters), default=0)
    check("spores won't come down to the ground", low >= al.SPORE_FLOOR - 1, f"{low:.0f} px")
    # a spore bursting on him knocks the bomb loose
    g.x, g.y, g.z = bx - 12.0, by, 200.0
    b = g.bomb
    b.state = "carried"
    g.safe_t, g.hit_t, g.notice = 0.0, 9.0, None
    a = hunters[0] if hunters else seekers()[0]
    a.x, a.y, a.z, a.hit_cool = g.x + 0.2, g.y, g.z + 30, 0.0
    st = g.stamina
    run_for(0.05)
    check("a spore hit hurts and knocks the bomb loose", b.state == "falling" and b.knocked and g.stamina < st
          and a.uid not in g.aliens and bool(g.notice) and "loose" in g.notice[0], f"{b.state} {g.notice}")
    check("the other spores fall back after a hit", all(x.mode != "hunt" for x in seekers()))
    run_for(1.5)
    check("a knocked bomb isn't called a missed drop", b.state == "ground" and "Missed" not in (g.notice or ("",))[0])
    # a drop that lands hard against the force field rolls clear, where he can reach it
    import base as bs
    b.state, b.knocked = "falling", False
    b.x, b.y, b.z, b.vx, b.vy, b.vz = bx, by - (bs.FIELD_R + 0.1), 30.0, 0.0, 0.0, 0.0
    g.x, g.y, g.z = bx - 30.0, by, 0.0
    run_for(1.5)
    r = math.hypot(b.x - bx, b.y - by)
    check("a bomb by the force field rolls clear", b.state == "ground" and r >= bs.FIELD_R + 0.9, f"{b.state} {r:.2f} tiles")
    g.destroy_base(bx, by, 100.0)
    run_for(0.3)
    check("spores burn out with the base", not seekers())
    g.new_game()
    run_for(0.5, draw=True)
    W, H = g.screen.get_size()
    pts = [(x, y) for x in range(0, W, 16) for y in range(H - 22, H, 4)]
    dark = sum(1 for p in pts if sum(g.screen.get_at(p)[:3]) / 3 < 40) / len(pts)
    check("no hint bar along the bottom", dark < 0.3, f"{dark:.2f} dark")

    # level 1 by the robot, drawn
    import autoplay
    g.new_game()
    res = autoplay.playthrough(g, levels=1, seed=7, max_level_time=180, draw=True, log=lambda *a: None)
    ok = res and not res[0].get("stuck")
    check("robot clears level 1", ok, f"{res[0]['time']:.1f} s game time" if res else "")
    check("base leaves a crater and level 2 starts", g.level == 2 and g.mode == "play")
    g.handle_key(_key(pygame.K_ESCAPE)); g.handle_key(_key(pygame.K_ESCAPE))
    check("Esc twice quits to the title", g.mode == "title")

    # losing every life ends the game
    g.new_game()
    for _ in range(3):
        run_for(2.1)
        g.stamina = 0.0
        run_for(0.05)
        for _ in range(600):
            if g.mode == "play":
                break
            g.update(1 / 60, idle)
    check("three lives lost: game over", g.mode == "over", g.mode)
    g.draw()
    print(f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'} in {time.time() - t0:.1f} s", flush=True)
    return len(fails)
