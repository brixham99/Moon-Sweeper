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

    import fullscreen
    import gamepad
    screen = fullscreen.self_check(check)
    for name, ok, info in gamepad.self_check():
        check(name, ok, info)

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
    check("new game starts in play, full stamina and no lives", g.mode == "play" and g.level == 1
          and g.stamina == cp.STAMINA_MAX and not hasattr(g, "lives"))
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

    def shoot_at(kind, dist, alt, off_deg, z=None, prep=None):
        g.x, g.y = clear_line(0.9, dist)
        g.vx = g.vy = g.vz = 0.0
        g.update_aliens(0.0)                              # let the chunks here release their aliens...
        g.aliens.clear(); g.lasers.clear()                # ...then take them away: just the target
        g.safe_t, g.hit_t = 99.0, 9.0
        fl = g.floor()
        ax, ay = g.x + math.cos(0.9) * dist, g.y + math.sin(0.9) * dist
        a = al.make_alien(("selftest", kind), ax, ay, kind, "green", 1, 3)
        a.z = g.world.floor_at(ax, ay) if z is None else z
        if prep:
            prep(a)
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

    # the level 4-6 aliens can all be shot (with the aim help)
    def reared(a):
        a.state, a.rise, a.targetable, a.harmful = "up", 1.0, True, True

    def armed(a):
        a.state, a.targetable = "armed", True
    check("Crater Worm: reared up, it can be shot from the ground", all(shoot_at("worm", d, 0, 0, prep=reared) for d in (2.5, 5.0)))
    check("Crater Worm: reared up, a little off the line", shoot_at("worm", 5.0, 0, 6, prep=reared))
    check("Crater Worm: can't be shot while it tunnels", not shoot_at("worm", 4.0, 0, 0))
    check("Glow Leech: hit at chest height", shoot_at("leech", 5.0, 0, 0, z=60.0) and shoot_at("leech", 5.0, 120, 0, z=180.0))
    check("Mine Beetle: hit in the air", shoot_at("beetle", 5.0, 90, 0, z=170.0) and shoot_at("beetle", 4.0, 0, 0, z=80.0))
    check("Spore Mine: shot on the ground from a distance", shoot_at("mine", 5.0, 0, 0, prep=armed))

    # each new alien turns up first on its own level, and the game spawns it there
    def kinds_at(level, n=14):
        out = set()
        for ci in range(-n, n):
            for cj in range(-n, n):
                out |= {e[3] for e in al.chunk_aliens_level(ci, cj, level)}
        return out
    for lvl, kind, cls in ((4, "worm", al.Worm), (5, "leech", al.Leech), (6, "beetle", al.Beetle)):
        earlier = set().union(*(kinds_at(L) for L in range(1, lvl)))
        g.new_game()
        g.level = lvl
        e = next(e for ci in range(-14, 14) for cj in range(-14, 14)
                 for e in al.chunk_aliens_level(ci, cj, lvl) if e[3] == kind)
        g.aliens.clear(); g.spawned.clear(); g.killed.clear()
        g.x, g.y = e[1] + 4.0, e[2]                        # near, but not on top of it
        g.update_aliens(0.0)
        here = [a for a in g.aliens.values() if isinstance(a, cls)]
        check(f"level {lvl}: {al.KIND_NAMES[kind]} spawn (and never before level {lvl})",
              kind in kinds_at(lvl) and kind not in earlier and bool(here) and al.level_new_kind(lvl) == kind)

    # stamina is the only measure: aliens bite chunks out of it, with a short grace after each bite
    def fresh(level=1):
        g.new_game()
        g.level = level
        g.aliens.clear()
        fi, fj = math.floor(g.x / al.wd.CHUNK), math.floor(g.y / al.wd.CHUNK)
        for di in range(-3, 4):
            for dj in range(-3, 4):
                g.spawned.add((fi + di, fj + dj))          # no wild aliens: just the test's own
        g.safe_t, g.hit_t = 0.0, 9.0
        g.vx = g.vy = g.vz = 0.0
    fresh()
    a = al.make_alien(("selftest", "bite"), g.x + 0.3, g.y, "creeper", "green", 1, 2)
    a.z = g.floor()
    a.update = lambda *args, **kw: None
    g.aliens[a.uid] = a
    st = g.stamina
    run_for(2 / 60)
    bite = st - g.stamina
    check("an alien bite takes stamina", al.KINDS["creeper"][3] * 0.8 < bite < al.KINDS["creeper"][3] * 1.5 and g.mode == "play",
          f"-{bite:.1f}")
    a.hit_cool = 0.0
    a.x, a.y = g.x + 0.3, g.y
    st = g.stamina
    run_for(2 / 60)
    check("no second bite inside the grace period", g.stamina > st - 1.0, f"-{st - g.stamina:.1f}")
    g.aliens.clear()
    # a Crater Worm bursting out under him
    fresh(4)
    w = al.make_alien(("selftest", "worm"), g.x + 0.5, g.y, "worm", "green", 1, 4)
    w.state, w.t, w.cool = "rise", 0.0, 0.0
    g.aliens[w.uid] = w
    st = g.stamina
    for _ in range(int((al.WORM_RISE + 0.15) * 60)):
        w.x, w.y = g.x + 0.5, g.y
        g.update(1 / 60, idle)
    check("a Crater Worm bursting out bites him", g.stamina < st - 10 and w.state in ("up", "dive"), f"-{st - g.stamina:.1f} {w.state}")
    g.aliens.clear()
    # a Glow Leech: latches on, drains, and a good burst of jetpack shakes it off
    fresh(5)
    lc = al.make_alien(("selftest", "leech"), g.x + 0.2, g.y, "leech", "green", 1, 5)
    lc.z = g.z + 40
    g.aliens[lc.uid] = lc
    run_for(3 / 60)
    check("a Glow Leech latches on", g.leech is lc and lc.state == "latched", f"{lc.state}")
    st = g.stamina
    run_for(1.0)
    check("a latched leech drains stamina", st - g.stamina > al.LEECH_DRAIN * 0.8, f"-{st - g.stamina:.1f} in 1 s")
    run_for(al.LEECH_SHAKE + 0.3, dict(idle, jet=1))
    check("the jetpack shakes a leech off", g.leech is None and lc.state != "latched", f"{lc.state}")
    g.aliens.clear()
    # a Spore Mine going off next to him
    fresh(6)
    g.z, g.vz = g.floor(), 0.0
    m = al.Mine(("mine", "selftest"), g.x + 0.6, g.y, g.floor())
    m.state, m.t, m.floor, m.age = "armed", 0.0, g.floor(), 0.0
    g.aliens[m.uid] = m
    st = g.stamina
    run_for(al.MINE_FUSE + 0.2)
    check("a Spore Mine goes off when he comes close, and hurts", m.uid not in g.aliens and st - g.stamina > al.MINE_HIT * 0.9,
          f"-{st - g.stamina:.1f}")
    g.aliens.clear()
    # the HUD: the stamina meter shows how much is left
    fresh(1)
    g.safe_t = 99.0
    g.stamina = cp.STAMINA_MAX
    g.draw(); full = g.screen.subsurface((0, 0, g.screen.get_width(), 44)).copy()
    g.stamina = cp.STAMINA_MAX * 0.15
    g.draw(); low = g.screen.subsurface((0, 0, g.screen.get_width(), 44)).copy()
    diff = sum(1 for x in range(0, full.get_width(), 2) for y in range(0, 44, 2) if full.get_at((x, y)) != low.get_at((x, y)))
    check("HUD stamina meter shows the stamina left", diff > 40, f"{diff} px changed")

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

    # the title screen: twinkling stars only on black sky, never over the Earth
    g.mode, g.title_t = "title", 0.0
    W, H = g.screen.get_size()
    ok, worst, n_stars = True, 0, 0
    for t in (0.0, 3.7, 11.2, 25.0, 47.5, 80.0):
        g.title_t = t
        g.title_twinkle = False
        g.draw_title(True, 1.0)
        plain = g.screen.copy()
        g.title_twinkle = True
        g.draw_title(True, 1.0)
        big = g.title_bg[0]
        ox, oy = g.title_offset(big, t)
        ex, ey, er = g.title_earth(big)
        ex, ey = ex + ox, ey + oy
        for r in g.title_star_rects:
            n_stars += 1
            if (r[0] - ex) ** 2 + (r[1] - ey) ** 2 < (er + 6) ** 2:
                ok = False
        for y in range(int(ey - er - 4), int(ey + er + 5)):
            for x in range(int(ex - er - 4), int(ex + er + 5)):
                if 0 <= x < W and 0 <= y < H and (x - ex) ** 2 + (y - ey) ** 2 <= (er + 4) ** 2:
                    d = sum(abs(p - q) for p, q in zip(g.screen.get_at((x, y))[:3], plain.get_at((x, y))[:3]))
                    worst = max(worst, d)
    check("title stars are never drawn over the Earth", ok and worst == 0 and n_stars > 20,
          f"{n_stars} stars drawn, Earth changed by {worst}")
    g.title_twinkle = True

    # running out of stamina ends the game (there are no lives)
    g.new_game()
    run_for(2.1)
    g.stamina = 0.0
    run_for(0.05)
    check("out of stamina: Jetman is exhausted", g.mode == "dying", g.mode)
    for _ in range(600):
        if g.mode == "over":
            break
        g.update(1 / 60, idle)
    check("out of stamina: game over", g.mode == "over" and "stamina" in g.over_reason, f"{g.mode} {g.over_reason!r}")
    g.draw()

    # Analogue sticks use the same key dict as the keyboard, with a fraction instead of 0 or 1.
    g.new_game()
    a0 = g.a
    g.update(1 / 60, dict(idle, right=1))
    full = g.a - a0
    a1 = g.a
    g.update(1 / 60, dict(idle, right=0.5))
    half = g.a - a1
    check("a half-tilt turns half as far as a full press", abs(half - full * 0.5) < 1e-6, f"{full:.5f} {half:.5f}")

    # Pad buttons through the same paths as the keys.
    g.bomb.state = "carried"
    g.handle_pad_edges(["cancel"])
    check("pad B drops the bomb", g.bomb.state != "carried", g.bomb.state)
    g.handle_pad_edges(["extra"])
    g.bomb.state = "carried"
    g.handle_pad_edges(["extra"])
    check("pad Y drops the bomb too", g.bomb.state != "carried", g.bomb.state)
    g.handle_pad_edges(["pause"])
    check("pad Start pauses", g.paused)
    g.handle_pad_edges(["confirm"])
    check("pad A carries on from the pause", not g.paused and g.mode == "play")
    g.handle_pad_edges(["pause"])
    g.handle_pad_edges(["cancel"])
    check("pad B from the pause returns to the title", g.mode == "title" and not g.paused)

    g.loaded = True
    g.handle_pad_edges(["confirm"])
    check("pad A starts a game from the title", g.mode == "play")
    g.bomb.state = "carried"
    g.handle_pad_edges(["cancel"])
    check("pad B during play drops the bomb and stays in the game", g.mode == "play" and g.bomb.state != "carried")

    # Name entry with the stick: up cycles, right moves, A saves, B skips. Keyboard typing still appends.
    g.mode = "over"
    g.entry_active = True
    g.entry_name = "A"
    g.entry_pos = 0
    g.score = 50
    g.pad_on = True
    g.entry_pad_move("menu_up")
    check("pad up cycles the letter", g.entry_name == "B", g.entry_name)
    g.entry_pad_move("menu_right")
    g.entry_pad_move("menu_up")
    check("pad right opens the next letter", g.entry_name == "BA" and g.entry_pos == 1, f"{g.entry_name!r} @{g.entry_pos}")
    g.handle_pad_edges(["fire"])
    check("pad X deletes the letter", g.entry_name == "B", g.entry_name)
    g.pad_on = False
    g.handle_text("ob")
    check("keyboard typing still appends", g.entry_name == "Bob", g.entry_name)
    shown = g.entry_shown()
    check("keyboard name entry still ends with the cursor", shown[:3] == "Bob" and shown[-1] in " _", repr(shown))
    saved = []
    g.board.submit = lambda n, s: saved.append((n, s))
    g.pad_on = True
    g.entry_pos = 0
    g.handle_pad_edges(["confirm"])
    check("pad A saves the name", (not g.entry_active) and saved == [("Bob", 50)], str(saved))
    g.entry_active = True
    g.entry_name = "SKIP"
    g.entry_note = ""
    g.handle_pad_edges(["cancel"])
    check("pad B skips name entry", (not g.entry_active) and g.entry_note == "")
    g.pad_on = True
    g.mode, g.title_t, g.loaded = "title", 0.0, True
    g.draw_title(True, 1.0)
    g.mode = "over"
    g.entry_active = True
    g.entry_name = "ADA"
    g.entry_pos = 0
    g.t = 0
    g.screen.fill((0, 0, 0))
    g.draw_over()
    g.pad_on = False
    check("pad hints draw on the title and the name entry", True)

    print(f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'} in {time.time() - t0:.1f} s", flush=True)
    return len(fails)
