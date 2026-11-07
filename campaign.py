"""The game around the sandbox: levels, the bomb, fuel, stamina, scoring, the
title screen, level cards, game over and the high score table.

Mixed into Game (game.py). Modes:
  title    -> painted title screen, controls and the Top 10 in turn, music
  play     -> flying about
  dying    -> stamina ran out: a short pause, then back to the rover (or game over)
  boom     -> the base is exploding (play carries on for a moment)
  card     -> level complete card, then the next level
  over     -> game over, name entry if the score makes the Top 10
"""
import math
import os
import random

import numpy as np
import pygame

import aa
import aliens as al
import base as bs
import gamepad
import rover as rv
import scores
import world as wd
from world import to_screen

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1280, 720          # internal frame. A television scales this; nothing draws in panel pixels.

# ---------------------------------------------------------------- tuning
FUEL_MAX = 100.0
FUEL_BURN = FUEL_MAX / 30.0       # 30 s of full thrust on a tank
FUEL_REFILL = 40.0                # per second standing on the rover roof
# Stamina is Jetman's one and only measure of staying power: there are no lives. It ebbs
# slowly while he's out (faster with the jetpack on), every alien that gets him takes a bite
# out of it (aliens.KINDS), and resting on the rover roof fills it again. At 0 the game is over.
STAMINA_MAX = 100.0
STAMINA_DRAIN = STAMINA_MAX / 200.0   # per second, always: 3 min 20 s of standing about
STAMINA_JET = 0.4                 # per second on top of that while the jetpack fires
STAMINA_REST = 30.0               # per second standing on the rover roof
STAMINA_LEVEL = 25.0              # a breather between levels: this much back (stamina carries over)
HIT_GROWTH = 0.06                 # alien bites grow this much stronger each level
HIT_GRACE = 1.0                   # s after a hit when nothing can hurt him again (he blinks)
FLY_SPEED = 6.5                   # tiles/s, game.FLY
# Base placement: worst case, every second of the tank spent at full thrust and top speed
# covers 30 s x 6.5 = 195 tiles. Bases go 40-80 tiles out (level 1: the usual 33), so even
# a clumsy, climb-heavy trip there uses well under half a tank. Walking costs nothing.
BASE_RANGE = (40.0, 80.0)
BOMB_KEY = pygame.K_b
LEVEL_BONUS = 2000                # x the level just finished
EXPLORE_CELL = 4.0                # tiles: each new 4x4 patch of ground visited ...
EXPLORE_POINTS = 5                # ... is worth this (only new ground, so no farming)
BOOM_TIME = 2.6
CARD_TIME = 4.5
DIE_TIME = 2.4                    # "EXHAUSTED!" before the game-over screen
TOP10_TIME = 7.0
SAFE_TIME = 2.0                   # s of blinking invulnerability after a respawn or a new level
ESC_CONFIRM = 2.5                 # s to press Esc a second time to leave a game
INTRO_TIME = 4.0                  # level intro banner
LOW_STAMINA = 0.25                # warning beep (and the meter flashes) below this
CRIT_STAMINA = 0.10               # a second, more urgent warning
COPYRIGHT = "\u00a92026 Nathan Anderson. Made in England by Optica."
TITLE_ART = os.path.join(HERE, "assets", "art", "title.jpg")
TITLE_EARTH = (410, 207, 37)      # the Earth in title.jpg (1280 x 720): centre x, y and radius in px
TITLE_MUSIC = os.path.join(HERE, "assets", "sounds", "title_theme.ogg")
BOMB_ART = os.path.join(HERE, "assets", "art", "bomb.png")


class Bomb:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z
        self.vx = self.vy = self.vz = 0.0
        self.state = "ground"         # ground | carried | falling
        self.knocked = False          # jolted loose by a spore (says so itself, not "Missed!")


class Campaign:
    # ------------------------------------------------------------ set-up
    def campaign_init(self, online=False):
        self.mode = "play"
        self.level = 1
        self.score = 0
        self.fuel, self.stamina = FUEL_MAX, STAMINA_MAX
        self.infinite = False          # benches / screenshots: never run dry
        self.explored = set()
        self.mode_t = 0.0
        self.shake = 0.0
        self.base_alive = True
        self.bonus_last = 0
        self.kills_level = 0
        self.title_t = 0.0
        self.title_bg = None
        self.board = scores.Leaderboard(online=online)
        self.flags = scores.Flags(download=online)
        if online:
            self.board.refresh()
        self.entry_active, self.entry_name, self.entry_note = False, "", ""
        self.entry_pos = 0             # pad name entry: which letter the stick is on
        self.pad_on = False            # a gamepad is connected: hints name its buttons
        self.qualified = False
        self.paint = al.PaintedSprites()
        img = pygame.image.load(BOMB_ART)
        self.bomb_img = img.convert_alpha() if pygame.display.get_surface() else img
        self.bomb_held = pygame.transform.smoothscale(self.bomb_img, (round(img.get_width() * 0.85), round(img.get_height() * 0.85)))
        self.f_hud = pygame.font.Font(None, 28)
        self.f_mid = pygame.font.Font(None, 40)
        self.f_big = pygame.font.Font(None, 84)
        self.f_title = pygame.font.Font(None, 150)
        self.music_on = False
        self.loaded = False
        self._tc = {}
        self.base_rng = random.Random()
        self.bomb = Bomb(*self.bomb_home())
        self.paused = False
        self.esc_t = 0.0               # > 0: a second Esc leaves the game
        self.safe_t = 0.0              # > 0: aliens can't hurt him (just respawned)
        self.notice = None             # (text, colour, seconds left)
        self.popups = []               # floating scores: [x, y, z, text, colour, age]
        self.warned_low = self.warned_crit = False
        self.seek_cool, self.seek_charge, self.seek_n = 1.5, 0.0, 0
        self.leech = None              # the Glow Leech latched on to him, if any
        self.hurt_flash, self.hurt_last = 0.0, 0   # the stamina meter flashes (and shows the bite) after a hit
        self.over_reason = ""
        self.mine_n = 0

    def bomb_home(self):
        rx, ry = self.world.rover
        return rx - 0.6, ry + rv.HALF_W + 1.1           # beside the rover, on the start side

    def new_game(self):
        self.level, self.score = 1, 0
        self.over_reason = ""
        self.stamina = STAMINA_MAX
        self.explored = set()
        self.world.clear_blasts()
        self.start_level(1)
        self.mode = "play"
        self.music(False)

    def start_level(self, level):
        """Jetman back at the rover with a full tank, the bomb beside it, the base somewhere new."""
        self.level = level
        self.mode_t = 0.0
        self.kills_level = 0
        if level == 1:
            s = self.world.start
            self.world.set_base((s[0] + 27.0, s[1] + 19.0))
        else:
            self.world.set_base(self.world.pick_base_site(self.base_rng, *BASE_RANGE))
        self.base_alive = True
        left = self.stamina if level > 1 else STAMINA_MAX
        self.reset_jetman()
        self.stamina = min(STAMINA_MAX, left + STAMINA_LEVEL)
        self.bomb = Bomb(*self.bomb_home())
        self.aliens.clear(); self.spawned.clear(); self.killed.clear()
        self.leech = None
        self.splats.clear(); self.lasers.clear()
        self.seek_cool, self.seek_charge = 1.5, 0.0
        bx, by = self.world.base                          # the base starts with its guards up
        most = al.seeker_tuning(level)[1]
        for i in range(most):
            self.seek_n += 1
            a = al.Seeker(("seek", level, self.seek_n), bx, by, 0.0, i * math.tau / most, level, self.seek_n, armed=True)
            self.aliens[a.uid] = a

    def reset_jetman(self):
        s = self.world.start
        self.x, self.y, self.z = s[0], s[1], 0.0
        self.vx = self.vy = self.vz = 0.0
        self.a = math.pi / 4
        self.fuel, self.stamina = FUEL_MAX, STAMINA_MAX
        self.hit_t = 9.0
        self.safe_t = SAFE_TIME
        self.warned_low = self.warned_crit = False
        self.hurt_flash = 0.0
        self.paused = False
        self.cam = list(to_screen(self.x, self.y))

    # ------------------------------------------------------------ per frame
    def on_rover_roof(self):
        rx, ry = self.world.rover
        return (abs(self.z - rv.ROOF_Z) < 1.0 and self.vz <= 0 and rv.in_footprint(rx, ry, self.x, self.y, 0.3))

    def campaign_keys(self, dt, keys):
        """Fuel: no thrust on an empty tank."""
        if self.mode not in ("play", "boom") or (self.fuel <= 0 and not self.infinite):
            keys = dict(keys); keys["jet"] = 0
        if self.mode not in ("play", "boom"):
            keys = {k: 0 for k in keys}
        return keys

    def campaign_update(self, dt):
        self.mode_t += dt
        self.shake = max(0.0, self.shake - dt * 2.5)
        self.safe_t = max(0.0, self.safe_t - dt)
        self.esc_t = max(0.0, self.esc_t - dt)
        if self.notice:
            self.notice = self.notice[:2] + (self.notice[2] - dt,) if self.notice[2] > dt else None
        for p in self.popups:
            p[5] += dt
        self.popups = [p for p in self.popups if p[5] < 1.1]
        self.base_fire(dt)
        if self.mode in ("play", "boom"):
            if self.thrusting and not self.infinite:
                self.fuel = max(0.0, self.fuel - FUEL_BURN * dt)
            if not self.infinite and self.mode == "play":
                self.stamina = max(0.0, self.stamina - (STAMINA_DRAIN + (STAMINA_JET if self.thrusting else 0.0)) * dt)
            if self.on_rover_roof():
                self.fuel = min(FUEL_MAX, self.fuel + FUEL_REFILL * dt)
                self.stamina = min(STAMINA_MAX, self.stamina + STAMINA_REST * dt)
            cell = (int(self.x // EXPLORE_CELL), int(self.y // EXPLORE_CELL))
            if cell not in self.explored:
                self.explored.add(cell)
                if len(self.explored) > 1:
                    self.score += EXPLORE_POINTS
            self.update_bomb(dt)
            if self.stamina <= 0 and self.mode == "play" and not self.infinite:
                self.exhausted()
            elif self.stamina < STAMINA_MAX * CRIT_STAMINA and not self.warned_crit and self.mode == "play":
                self.warned_crit = self.warned_low = True
                self.sfx.play("warn")
                self.say("Stamina critical! Get back to the rover now", (255, 90, 70), 3.5)
            elif self.stamina < STAMINA_MAX * LOW_STAMINA and not self.warned_low and self.mode == "play":
                self.warned_low = True
                self.sfx.play("warn")
                self.say("Stamina low! Land on the rover roof to rest", (255, 170, 90), 3.0)
            if self.stamina > STAMINA_MAX * (LOW_STAMINA + 0.1):
                self.warned_low = False
            if self.stamina > STAMINA_MAX * (CRIT_STAMINA + 0.05):
                self.warned_crit = False
        self.hurt_flash = max(0.0, self.hurt_flash - dt)
        if self.mode == "boom" and self.mode_t > BOOM_TIME:
            self.mode, self.mode_t = "card", 0.0
            self.sfx.play("levelup")
        elif self.mode == "card" and self.mode_t > CARD_TIME:
            self.start_level(self.level + 1)
            self.mode = "play"
        elif self.mode == "dying" and self.mode_t > DIE_TIME:
            self.game_over()

    def base_fire(self, dt):
        """The Alien Missile Base fights back: when Jetman comes within range it glows at the top
        of its force field for a moment, then lets go a spore that guards it and hunts him."""
        al.Seeker.hunting = sum(1 for a in self.aliens.values() if a.kind == "seeker" and a.mode == "hunt")
        if self.mode != "play" or not self.base_alive:
            self.seek_charge = 0.0
            return
        bx, by = self.world.base
        interval, most, _, _ = al.seeker_tuning(self.level)
        alive = sum(1 for a in self.aliens.values() if a.kind == "seeker")
        near = math.hypot(self.x - bx, self.y - by) < al.SEEK_RANGE
        self.seek_cool -= dt
        if self.seek_charge > 0:
            self.seek_charge += dt
            if self.seek_charge >= al.SEEK_CHARGE:
                close = math.hypot(self.x - bx, self.y - by) < bs.FIELD_R + 3
                self.seek_charge, self.seek_cool = 0.0, interval * (0.6 if close else 1.0)   # angrier up close
                self.seek_n += 1
                ang = math.atan2(self.y - by, self.x - bx) + self.rng.uniform(-1.2, 1.2)
                a = al.Seeker(("seek", self.level, self.seek_n), bx, by, bs.FIELD_H - 20, ang, self.level, self.seek_n)
                self.aliens[a.uid] = a
                self.alien_sound("spit", a)
        elif near and self.seek_cool <= 0 and alive < most and self.safe_t <= 0:
            self.seek_charge = 1e-6

    def draw_base_charge(self, cx, cy):
        """The warning glow on top of the dome before a spore is fired."""
        if self.seek_charge <= 0 or not self.base_alive:
            return
        bx, by = self.world.base
        px, py = to_screen(bx, by, bs.FIELD_H - 6)
        k = self.seek_charge / al.SEEK_CHARGE
        size = int(20 + 70 * k + 8 * math.sin(self.t * 30))
        sx, sy = px - cx, py - cy
        g = self.tinted_glow(max(8, size), (255, int(120 + 80 * k), 40))
        for _ in range(2):                                   # bright enough to read against the dome
            self.screen.blit(g, (sx - g.get_width() / 2, sy - g.get_height() / 2), special_flags=pygame.BLEND_ADD)
        core = self.tinted_glow(max(6, int(size * 0.4)), (255, 240, 200))
        self.screen.blit(core, (sx - core.get_width() / 2, sy - core.get_height() / 2), special_flags=pygame.BLEND_ADD)

    def say(self, text, col=(235, 235, 235), secs=2.5):
        """A short message under the HUD."""
        self.notice = (text, col, secs)

    def popup(self, x, y, z, points, col=(255, 230, 120)):
        self.popups.append([x, y, z, f"+{points:,}", col, 0.0])

    def hurt_stamina(self, amount):
        """An alien got him: a bite out of his stamina (none while he's safe), a little bigger on
        each level (+6% a level). True if it hurt."""
        amount *= 1.0 + HIT_GROWTH * (self.level - 1)
        if self.mode == "play" and not self.infinite and self.safe_t <= 0 and amount > 0:
            self.stamina = max(0.0, self.stamina - amount)
            self.hurt_flash, self.hurt_last = 0.6, int(round(amount))
            return True
        return False

    def exhausted(self):
        """Stamina has run out: that's the game."""
        self.mode, self.mode_t = "dying", 0.0
        self.over_reason = f"Out of stamina on level {self.level}"
        self.sfx.play("hurt")
        self.release_leech()
        if self.bomb.state == "carried":
            self.bomb.state = "lost"
        for _ in range(30):
            a = self.rng.uniform(0, math.tau); s = self.rng.uniform(1, 3.5)
            self.parts.append(self.make_part(self.x, self.y, self.z + 40, math.cos(a) * s, math.sin(a) * s,
                                             self.rng.uniform(40, 200), self.rng.uniform(0.3, 0.8), "spark", 2))

    def release_leech(self, quiet=True):
        lc = self.leech
        if lc is None:
            return
        self.leech = None
        a = self.a + math.pi                                # it drops off behind him
        lc.release(math.cos(a), math.sin(a))
        lc.x, lc.y, lc.z = self.x, self.y, self.z + 50
        self.hit_t = min(self.hit_t, -1.0)                  # a breather: 2 s before anything can hurt him again
        if not quiet:
            self.sfx.play("shake")

    def alien_killed(self, a):
        self.score += al.KINDS[a.kind][2]
        self.popup(a.x, a.y, a.z + al.KINDS[a.kind][6] + 30, al.KINDS[a.kind][2])
        self.kills_level += 1

    # ------------------------------------------------------------ the bomb
    def update_bomb(self, dt):
        b = self.bomb
        if b.state == "ground" and self.mode == "play":
            if (b.x - self.x) ** 2 + (b.y - self.y) ** 2 < 0.65 ** 2 and abs(self.z - b.z) < 40:
                b.state = "carried"
                self.sfx.play("pickup")
        elif b.state == "carried":
            b.x, b.y, b.z = self.x, self.y, self.z - 14
        elif b.state == "falling":
            b.vz -= 430.0 * dt
            for _ in range(3):
                b.x += b.vx * dt / 3; b.y += b.vy * dt / 3; b.z += b.vz * dt / 3
                bx, by = self.world.base
                top = bs.height_at(bx, by, b.x, b.y)       # 0 away from the base: that's a miss, not a hit
                if self.base_alive and (bs.field_e(bx, by, b.x, b.y, b.z) < 1.0 or (top > 0 and b.z <= top + 2)):
                    self.destroy_base(b.x, b.y, max(b.z, 30))
                    return
                fl = self.world.floor_at(b.x, b.y)
                if b.z <= fl:
                    r = math.hypot(b.x - bx, b.y - by)
                    if self.base_alive and 0 < r < bs.FIELD_R + 1.0:     # roll it clear of the force field,
                        k = (bs.FIELD_R + 1.0) / r                       # where Jetman can get at it
                        nx, ny = bx + (b.x - bx) * k, by + (b.y - by) * k
                        if not self.world.blocked(nx, ny, 0.0, 0.2):
                            b.x, b.y = nx, ny
                            fl = self.world.floor_at(b.x, b.y)
                    b.z, b.state = fl, "ground"
                    b.vx = b.vy = b.vz = 0.0
                    self.sfx.play("bounce", 0.9)
                    if not getattr(b, "knocked", False):
                        self.say("Missed! Pick the bomb up and try again", (255, 170, 90), 3.0)
                    b.knocked = False
                    break

    def knock_bomb_loose(self, nx=0.0, ny=0.0):
        """A spore bursting on Jetman jolts the bomb out of his hands: it tumbles away the way
        the spore pushed him, so near the base it's a miss rather than a lucky hit."""
        b = self.bomb
        if b.state != "carried" or self.mode != "play" or self.infinite:
            return
        b.state, b.knocked = "falling", True
        b.vx, b.vy, b.vz = nx * 1.8, ny * 1.8, 40.0
        b.x, b.y, b.z = self.x, self.y, self.z - 14
        self.sfx.play("drop")
        self.say("A spore knocked the bomb loose! Pick it up again", (255, 170, 90), 3.5)

    def spores_regroup(self):
        """After a spore gets him, the others go back on guard and the base waits a moment,
        so he has a fair chance to pick the bomb up again rather than being hit over and over."""
        for a in self.aliens.values():
            if a.kind == "seeker" and a.mode == "hunt" and a.age < a.life:
                a.mode, a.life, a.calm_t = "guard", 1e9, 4.0
        self.seek_cool = max(self.seek_cool, al.seeker_tuning(self.level)[0] * 1.5)

    def drop_bomb(self):
        b = self.bomb
        if b.state != "carried" or self.mode != "play":
            return
        b.state = "falling"
        b.vx, b.vy, b.vz = self.vx, self.vy, min(0.0, self.vz)
        b.x, b.y, b.z = self.x, self.y, self.z - 14
        self.sfx.play("drop")
        if self.z - self.floor() < 4:          # set down on the ground where he was holding it
            wx, wy = self.held_bomb_offset()[:2]
            if not self.world.blocked(self.x + wx, self.y + wy, 0.0, 0.2):
                b.x, b.y = self.x + wx, self.y + wy
            b.state, b.z = "ground", self.floor(b.x, b.y)

    def destroy_base(self, x, y, z):
        bx, by = self.world.base
        self.bomb.state = "used"
        self.base_alive = False
        for a in [a for a in self.aliens.values() if a.kind == "seeker"]:
            a.age = a.life                                  # its spores burn out with it
        self.world.set_base(self.world.BASE_GONE)          # gone: the fireball covers the moment
        self.world.add_blast(bx, by)                       # ...and leaves a giant scorched crater
        self.mode, self.mode_t = "boom", 0.0
        self.bonus_last = LEVEL_BONUS * self.level
        self.score += self.bonus_last
        self.popup(bx, by, 180, self.bonus_last, (140, 255, 160))
        self.shake = 1.0
        self.sfx.play("boom")
        rnd = self.rng
        cols = [bs.SHELL, bs.BONE, bs.VEIN, bs.ORB, bs.SHELL_L, bs.BONE_D]
        for i in range(70):                                # debris
            a = rnd.uniform(0, math.tau); s = rnd.uniform(1.5, 7.0)
            p = self.make_part(bx + rnd.uniform(-1.5, 1.5), by + rnd.uniform(-1.5, 1.5), rnd.uniform(10, 90),
                               math.cos(a) * s, math.sin(a) * s, rnd.uniform(120, 420), rnd.uniform(1.2, 2.4), "frag", 0)
            p.size = (rnd.choice(cols), rnd.uniform(0, math.tau), rnd.uniform(-14, 14))
            self.parts.append(p)
        for i in range(90):                                # sparks
            a = rnd.uniform(0, math.tau); s = rnd.uniform(2, 9)
            self.parts.append(self.make_part(bx, by, rnd.uniform(20, 120), math.cos(a) * s, math.sin(a) * s,
                                             rnd.uniform(60, 380), rnd.uniform(0.4, 1.4), "spark", 2))
        for i in range(14):                                # fireballs, rising
            a = rnd.uniform(0, math.tau); r = rnd.uniform(0, 2.4)
            self.parts.append(self.make_part(bx + math.cos(a) * r, by + math.sin(a) * r, rnd.uniform(10, 80), 0, 0,
                                             rnd.uniform(20, 70), rnd.uniform(1.4, 3.0), "fire", rnd.uniform(110, 260)))
        for i in range(26):                                # a slow pall of dark smoke
            a = rnd.uniform(0, math.tau); r = rnd.uniform(0, 2.8)
            self.parts.append(self.make_part(bx + math.cos(a) * r, by + math.sin(a) * r, rnd.uniform(20, 120),
                                             math.cos(a) * 0.4, math.sin(a) * 0.4, rnd.uniform(25, 60),
                                             rnd.uniform(2.6, 4.2), "smoke", rnd.uniform(26, 60)))
        for i in range(3):
            self.parts.append(self.make_part(bx, by, 6, 0, 0, 0, 0.9 + i * 0.35, "shock", 180 + i * 90))
        self.parts.append(self.make_part(bx, by, 60, 0, 0, 0, 0.5, "fire", 420))
        for uid, a in list(self.aliens.items()):           # the blast clears the aliens around it
            if (a.x - bx) ** 2 + (a.y - by) ** 2 < 9 ** 2:
                self.pop(a)

    # ------------------------------------------------------------ events
    def handle_key(self, e):
        """Key presses for every mode. Returns 'quit' to leave the game."""
        if self.mode == "title":
            if e.key == pygame.K_ESCAPE:
                return "quit"
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE) and self.loaded:
                self.new_game()
            return None
        if self.mode == "over":
            if self.entry_active:
                self.entry_key(e.key)
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE) and self.mode_t > 0.8:
                self.to_title()
            return None
        if e.key == pygame.K_ESCAPE:
            if self.paused or self.esc_t > 0 or self.mode not in ("play", "boom"):
                self.to_title()                 # a second Esc (or Esc from the pause panel) leaves
            else:
                self.esc_t = ESC_CONFIRM
            return None
        if e.key == pygame.K_p and self.mode in ("play", "boom", "dying", "card"):
            self.set_paused(not self.paused)
            return None
        if self.paused:
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.set_paused(False)
            return None
        self.esc_t = 0.0
        if e.key == BOMB_KEY:
            self.drop_bomb()
        elif self.mode == "card" and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.mode_t = CARD_TIME
        return None

    def handle_pad_edges(self, edges):
        """Discrete pad presses. Held sticks and buttons go through the key dict instead.

        Returns 'quit' to leave the process (title screen Back, or Esc from the title).
        """
        if not edges:
            return None
        rank = {name: i for i, name in enumerate((
            "menu_left", "menu_right", "menu_up", "menu_down",
            "fire", "extra", "cancel", "pause", "confirm"))}
        for action in sorted(edges, key=lambda a: rank.get(a, 50)):
            result = self._pad_action(action)
            if result == "quit":
                return "quit"
        return None

    def _pad_action(self, action):
        playing = self.mode in ("play", "boom") and not self.paused
        if action == "pause":
            # Start pauses a game. On a menu, where there is nothing to pause, it confirms.
            if self.mode == "title":
                return self.handle_key(self._ev(pygame.K_RETURN))
            if self.mode == "over":
                return self.handle_key(self._ev(pygame.K_RETURN))
            if self.mode == "card":
                return self.handle_key(self._ev(pygame.K_RETURN))
            return self.handle_key(self._ev(pygame.K_p))
        if action == "confirm":
            if playing:
                return None                       # A is the jetpack, held, not a key press
            return self.handle_key(self._ev(pygame.K_RETURN))
        if action == "cancel":
            if self.mode == "over" and self.entry_active:
                return self.handle_key(self._ev(pygame.K_ESCAPE))
            if playing:
                return self.handle_key(self._ev(pygame.K_b))   # drop the bomb
            return self.handle_key(self._ev(pygame.K_ESCAPE))
        if action == "extra":
            if playing:
                return self.handle_key(self._ev(pygame.K_b))
            return None
        if action == "fire":
            if self.mode == "over" and self.entry_active:
                self.entry_pad_delete()
            return None
        if action in ("menu_up", "menu_down", "menu_left", "menu_right"):
            if self.mode == "over" and self.entry_active:
                self.entry_pad_move(action)
            return None
        return None

    @staticmethod
    def _ev(key):
        return pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="")

    def set_paused(self, on):
        if on and self.mode not in ("play", "boom", "dying", "card"):
            return
        self.paused = on
        try:
            (pygame.mixer.pause if on else pygame.mixer.unpause)()
        except Exception:
            pass

    def handle_text(self, text):
        if self.mode == "over" and self.entry_active:
            for ch in text:
                if ch.isascii() and (ch.isalnum() or ch == " ") and len(self.entry_name) < scores.NAME_MAX:
                    if ch == " " and (not self.entry_name or self.entry_name.endswith(" ")):
                        continue
                    self.entry_name += ch
            self.entry_pos = len(self.entry_name)

    def entry_key(self, key):
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            name = scores.submit_name(self.entry_name)        # "nathan" goes on the board as "Nathan"
            if not name:
                return
            self.entry_active = False
            self.entry_note = name
            self.board.submit(name, self.score)
            self._stop_text()
        elif key == pygame.K_ESCAPE:
            self.entry_active, self.entry_note = False, ""
            self._stop_text()
        elif key == pygame.K_BACKSPACE:
            self.entry_name = self.entry_name[:-1]
            self.entry_pos = min(self.entry_pos, len(self.entry_name))

    @staticmethod
    def _stop_text():
        try:
            pygame.key.stop_text_input()
        except Exception:
            pass

    def game_over(self):
        self.mode, self.mode_t = "over", 0.0
        self.qualified = self.board.qualifies(self.score)
        self.board.record_local(self.score)
        self.entry_note = ""
        self.entry_active = self.qualified
        if self.entry_active:
            self.entry_name = self.board.last_name
            self.entry_pos = 0
            try:
                pygame.key.start_text_input()
            except Exception:
                pass

    def to_title(self):
        if self.mode in ("play", "dying", "boom", "card") and self.score > 0:
            self.board.record_local(self.score)
        self.mode, self.title_t = "title", 0.0
        self.set_paused(False)
        self.esc_t = 0.0
        self._stop_text()
        self.board.refresh()
        self.music(True)

    def music(self, on):
        if on == self.music_on:
            return
        self.music_on = on
        try:
            if on:
                pygame.mixer.music.load(TITLE_MUSIC)
                pygame.mixer.music.set_volume(0.55)
                pygame.mixer.music.play(-1)
            else:
                pygame.mixer.music.fadeout(600)
        except Exception:
            pass

    # ------------------------------------------------------------ drawing helpers
    def rtext(self, font, s, col):
        """font.render with a small cache: the HUD redraws the same strings every frame."""
        k = (id(font), s, col)
        im = self._tc.get(k)
        if im is None:
            if len(self._tc) > 400:
                self._tc.clear()
            im = self._tc[k] = font.render(s, True, col)
        return im

    def text(self, font, s, col, shadow=True):
        """Text with a thin dark outline and a soft drop shadow, so it reads on bright ground. Cached."""
        k = ("t", id(font), s, col, shadow)
        out = self._tc.get(k)
        if out is not None:
            return out
        im = font.render(s, True, col)
        if not shadow:
            return im
        dark = font.render(s, True, (0, 0, 0))
        out = pygame.Surface((im.get_width() + 4, im.get_height() + 4), pygame.SRCALPHA)
        sh = dark.copy()
        sh.set_alpha(150)
        out.blit(sh, (3, 3))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            out.blit(dark, (1 + dx, 1 + dy))
        out.blit(im, (1, 1))
        if len(self._tc) > 400:
            self._tc.clear()
        self._tc[k] = out
        return out

    def ctext(self, font, s, col, y, dst=None):
        im = self.text(font, s, col)
        (dst or self.screen).blit(im, ((W - im.get_width()) // 2, y))
        return im

    def band(self, y, h, alpha=150):
        """A dark horizontal band, soft at the top and bottom edges, to set text on."""
        k = ("band", h, alpha)
        im = self._tc.get(k)
        if im is None:
            im = pygame.Surface((W, h), pygame.SRCALPHA)
            edge = min(24, h // 3)
            for r in range(h):
                f = min(1.0, min(r, h - 1 - r) / edge)
                im.fill((6, 8, 14, int(alpha * f * f * (3 - 2 * f))), (0, r, W, 1))
            self._tc[k] = im
        self.screen.blit(im, (0, y))

    def bar(self, x, y, w, h, frac, col, label):
        sc = self.screen
        lab = self.rtext(self.font, label, (200, 200, 200))
        sc.blit(lab, (x, y + (h - lab.get_height()) // 2 + 1))
        x += lab.get_width() + 8
        r = h // 2
        frac = max(0.0, min(1.0, frac))
        pygame.draw.rect(sc, (34, 34, 40), (x, y, w, h), border_radius=r)
        if frac < 0.25:                                # low: pulse towards red
            k = 0.5 + 0.5 * math.sin(self.t * 9)
            col = tuple(int(c + (t - c) * k) for c, t in zip(col, (240, 55, 45)))
        fw = int(w * frac)
        if fw > 0:
            pygame.draw.rect(sc, col, (x, y, max(fw, h), h), border_radius=r)
            hi = tuple(min(255, c + 70) for c in col)    # a sheen along the top
            pygame.draw.rect(sc, hi, (x + 3, y + 2, max(0, max(fw, h) - 6), 3), border_radius=2)
            if fw < h:                                 # nearly empty: clip the rounded end back
                pygame.draw.rect(sc, (34, 34, 40), (x + fw, y, h - fw, h))
        pygame.draw.rect(sc, (150, 150, 158), (x, y, w, h), 1, border_radius=r)
        return x + w

    def heart_icon(self, col):
        """A little heart beside the stamina meter, drawn as shapes (cached per colour)."""
        k = ("heart", col)
        im = self._tc.get(k)
        if im is None:
            S = 3
            big = pygame.Surface((22 * S, 20 * S), pygame.SRCALPHA)
            pygame.draw.circle(big, col, (6 * S, 7 * S), 5 * S)
            pygame.draw.circle(big, col, (16 * S, 7 * S), 5 * S)
            pygame.draw.polygon(big, col, [(1.2 * S, 9 * S), (20.8 * S, 9 * S), (11 * S, 19 * S)])
            pygame.draw.circle(big, tuple(min(255, c + 90) for c in col), (5 * S, 5 * S), int(1.6 * S))
            im = pygame.transform.smoothscale(big, (22, 20))
            self._tc[k] = im
        return im

    def stamina_meter(self, x, y):
        """The big stamina meter: the only thing between Jetman and GAME OVER."""
        sc = self.screen
        frac = max(0.0, min(1.0, self.stamina / STAMINA_MAX))
        low = frac < LOW_STAMINA and self.mode in ("play", "dying")
        beat = 0.5 + 0.5 * math.sin(self.t * (14 if frac < CRIT_STAMINA else 9))
        col = (90, 225, 120) if frac >= 0.5 else (235, 205, 70) if frac >= LOW_STAMINA else (240, 70, 50)
        hc = (240, 70, 60) if not low or beat > 0.5 else (120, 30, 30)
        heart = self.heart_icon(hc)
        sc.blit(heart, (x, y + 2 + (1 if low and beat > 0.5 else 0)))
        x += 28
        w, h = 236, 20
        r = h // 2
        pygame.draw.rect(sc, (34, 34, 40), (x, y, w, h), border_radius=r)
        if low:
            col = tuple(int(c + (255 - c) * beat * 0.35) for c in col)
        fw = int(w * frac)
        if fw > 0:
            pygame.draw.rect(sc, col, (x, y, max(fw, h), h), border_radius=r)
            hi = tuple(min(255, c + 70) for c in col)
            pygame.draw.rect(sc, hi, (x + 4, y + 3, max(0, max(fw, h) - 8), 4), border_radius=2)
            if fw < h:
                pygame.draw.rect(sc, (34, 34, 40), (x + fw, y, h - fw, h))
        for i in (1, 2, 3):                                  # quarter ticks
            pygame.draw.line(sc, (20, 20, 24), (x + w * i // 4, y + 3), (x + w * i // 4, y + h - 4))
        edge = (150, 150, 158)
        if self.hurt_flash > 0 and int(self.hurt_flash * 12) % 2 == 0:
            edge = (255, 255, 255)
        elif low and beat > 0.5:
            edge = (255, 90, 70)
        pygame.draw.rect(sc, edge, (x, y, w, h), 2 if edge != (150, 150, 158) else 1, border_radius=r)
        lab = self.rtext(self.font, f"STAMINA {int(math.ceil(self.stamina)):d}%", (16, 16, 16) if frac > 0.45 else (235, 235, 235))
        sc.blit(lab, (x + 10, y + (h - lab.get_height()) // 2 + 1))
        if self.hurt_flash > 0:                              # the bite just taken, falling away under the meter
            im = self.text(self.font, f"-{self.hurt_last}", (255, 110, 90))
            k = 1 - self.hurt_flash / 0.6
            px = min(max(x + fw + 6, x + 18 + lab.get_width()), x + w - im.get_width() - 8)   # clear of the label
            sc.blit(im, (px, y + 2 + int(10 * k)))
        return x + w

    def low_vignette(self):
        """Red, pulsing screen edges while stamina is low."""
        frac = self.stamina / STAMINA_MAX
        if frac >= LOW_STAMINA or self.mode != "play" or self.infinite:
            return
        im = self._tc.get("vignette")
        if im is None:
            im = pygame.Surface((W // 8, H // 8), pygame.SRCALPHA)
            for yy in range(H // 8):
                for xx in range(W // 8):
                    dx = abs(xx - W / 16) / (W / 16); dy = abs(yy - H / 16) / (H / 16)
                    e = max(0.0, max(dx, dy) - 0.62) / 0.38
                    im.set_at((xx, yy), (200, 20, 10, int(255 * min(1.0, e) ** 1.6)))
            im = pygame.transform.smoothscale(im, (W, H))
            self._tc["vignette"] = im
        beat = 0.5 + 0.5 * math.sin(self.t * (14 if frac < CRIT_STAMINA else 9))
        a = (0.35 + 0.65 * (1 - frac / LOW_STAMINA)) * (0.35 + 0.65 * beat)
        im.set_alpha(int(190 * a))
        self.screen.blit(im, (0, 0))

    def draw_bomb_obj(self, cx, cy):
        b = self.bomb
        if b.state not in ("ground", "falling"):
            return
        img = self.bomb_img
        px, py = to_screen(b.x, b.y, b.z)
        px -= cx; py -= cy
        if b.state == "ground":                        # little ground shadow, and a beacon blink
            fl = b.z
            gx, gy = to_screen(b.x, b.y, fl)
            sh = self._tc.get("bomb_sh")
            if sh is None:
                sh = self._tc["bomb_sh"] = pygame.Surface((40, 18), pygame.SRCALPHA)
                pygame.draw.ellipse(sh, (0, 0, 0, 110), sh.get_rect())
            self.screen.blit(sh, (gx - cx - 26, gy - cy - 9))
        self.screen.blit(img, (px - img.get_width() / 2, py - img.get_height() + 4))
        if int(self.t * 2.5) % 2 == 0:
            g = self.tinted_glow(26, (255, 60, 40))
            self.screen.blit(g, (px - 13 + 1, py - img.get_height() + 4 - 7), special_flags=pygame.BLEND_ADD)

    def held_bomb_offset(self):
        """Where the bomb is held, in tiles from his feet: out in front along the sprite's own
        (8-way) facing and a little to one side, so it still peeks out when he walks away."""
        a = self.facing() * math.pi / 4
        fx, fy = math.cos(a), math.sin(a)
        d, side = 0.42, 0.28
        if abs((fx - fy) * d - (fy + fx) * side) < abs((fx - fy) * d + (fy + fx) * side):
            side = -side                               # whichever side shows more of it on screen
        return fx * d - fy * side, fy * d + fx * side, fx, fy

    def held_bomb_pos(self, bx, by):
        """On foot he carries the bomb in front of him: half a tile out along his facing, at
        waist height, bobbing with his stride. Returns (screen x, screen y, in front of him?)."""
        wx, wy, fx, fy = self.held_bomb_offset()
        bob = abs(math.sin(self.dist / 0.2 * math.pi / 4)) * 2.0 if math.hypot(self.vx, self.vy) > 0.4 else 0.0
        x = bx + (wx - wy) * 32
        y = by + (wx + wy) * 16 - 38 - bob
        return x, y, fx + fy > -0.1                    # facing the viewer or side-on: bomb in front

    def draw_carried_bomb(self, bx, by, layer=None):
        """On foot: held in front of him (drawn before his sprite when he faces away, after it
        when he faces the viewer; layer = "back" / "front"). Flying: hanging under his boots
        on a short tether, so it reads as slung while he's airborne."""
        if self.bomb.state != "carried":
            return
        img = self.bomb_img
        if self.z - self.floor() <= 0.5 and self.vz <= 0:
            x, y, front = self.held_bomb_pos(bx, by)
            if layer == ("front" if front else "back"):
                img = self.bomb_held
                self.screen.blit(img, (x - img.get_width() / 2, y - img.get_height() / 2))
            return
        if layer == "front":
            return
        x, y = bx + 2, by - 16
        pygame.draw.line(self.screen, (60, 60, 60), (x, y - 8), (x, y + 2), 2)
        self.screen.blit(img, (x - img.get_width() / 2, y))

    # ------------------------------------------------------------ HUD and overlays
    def campaign_hud(self):
        sc = self.screen
        self.low_vignette()
        pygame.draw.rect(sc, (0, 0, 0), (0, 0, W, 34))
        pygame.draw.line(sc, (90, 90, 90), (0, 34), (W, 34))
        x = 14
        for s_, col in ((f"SCORE {self.score:,}", (255, 220, 120)), (f"LEVEL {self.level}", (225, 225, 225))):
            im = self.rtext(self.font, s_, col)
            sc.blit(im, (x, 9)); x += im.get_width() + 26
        x = self.stamina_meter(x - 6, 7) + 22
        x = self.bar(x, 11, 96, 12, self.fuel / FUEL_MAX, (255, 190, 60), "FUEL") + 20
        st = {"carried": ("BOMB CARRIED \u2013 PRESS B TO DROP", (255, 120, 80)), "falling": ("BOMB FALLING", (255, 120, 80)),
              "ground": ("BOMB: PICK IT UP", (200, 200, 200)) if self.bomb_home() == (self.bomb.x, self.bomb.y) else ("BOMB ON THE GROUND", (200, 200, 200)),
              "used": ("BASE DESTROYED", (120, 255, 140)), "lost": ("", (0, 0, 0))}[self.bomb.state]
        sc.blit(self.rtext(self.font, st[0], st[1]), (x, 9))
        alt = self.z - self.floor()
        im = self.rtext(self.font, f"ALT {int(alt):3d} m", (170, 170, 170))
        sc.blit(im, (W - im.get_width() - 14, 9))
        self.draw_popups()
        msg = None
        if self.esc_t > 0:
            msg = ("Press Esc again to quit to the title screen", (255, 230, 150))
        elif self.on_rover_roof() and (self.fuel < FUEL_MAX - 0.5 or self.stamina < STAMINA_MAX - 0.5):
            msg = ("Refuelling and resting on the rover\u2026", (140, 230, 255))
        elif self.fuel <= 0 and not self.infinite:
            msg = ("Out of fuel! Walk back to the rover and climb aboard", (255, 120, 90))
        elif self.leech is not None and self.mode == "play":
            msg = ("A Glow Leech is draining you! Fire the jetpack hard to shake it off", (140, 255, 170))
        elif self.notice:
            msg = self.notice[:2]
        if msg:
            self.ctext(self.f_hud, msg[0], msg[1], 46)
        if self.mode == "play" and self.mode_t < INTRO_TIME:
            t = self.mode_t                            # fade in, hold, fade out
            a = min(1.0, t / 0.35, (INTRO_TIME - t) / 0.6)
            lay = self.intro_layer()
            self.band(40, lay.get_height() + 26, int(150 * a))
            lay.set_alpha(int(255 * a))
            sc.blit(lay, (0, 56))
        if self.mode == "dying":
            self.band(196, 150, 160)
            self.ctext(self.f_big, "EXHAUSTED!", (255, 110, 90), 220)
            self.ctext(self.f_mid, "Jetman has run out of stamina", (230, 230, 230), 300)
        if self.mode == "card":
            self.draw_card()
        if self.mode == "over":
            self.draw_over()
        if self.paused:
            self.veil(120)
            self.band(250, 200, 190)
            self.ctext(self.f_big, "PAUSED", (240, 240, 240), 290)
            lines = gamepad.pause_lines(self.pad_on)
            if len(lines) == 1:
                self.ctext(self.f_hud, lines[0], (200, 200, 200), 380)
            else:
                self.ctext(self.f_hud, lines[0], (200, 200, 200), 386)
                self.ctext(self.f_hud, lines[1], (200, 200, 200), 420)

    def draw_popups(self):
        """Floating '+500' scores where an alien or the base was destroyed."""
        cx, cy = self.cam
        for x, y, z, s_, col, age in self.popups:
            im = self.text(self.f_mid, s_, col)
            px, py = to_screen(x, y, z)
            a = min(1.0, (1.1 - age) / 0.4)
            if a < 1.0:
                im = im.copy()
                im.set_alpha(int(255 * a))
            self.screen.blit(im, (px - cx + W / 2 - im.get_width() / 2, py - cy + H / 2 - 44 * age))

    def veil(self, a=170):
        v = self._tc.get("veil")
        if v is None:                                  # one plain black sheet, faded with surface alpha
            v = self._tc["veil"] = pygame.Surface((W, H))
            v.fill((0, 0, 0))
        v.set_alpha(int(a))
        self.screen.blit(v, (0, 0))

    def intro_layer(self):
        """The level's intro banner (cached per level): what to do, and what's new."""
        k = ("intro", self.level)
        lay = self._tc.get(k)
        if lay is not None:
            return lay
        kind = al.level_new_kind(self.level)
        tip = al.KIND_TIPS.get(kind) if kind else None
        lay = pygame.Surface((W, 206 if tip else 170), pygame.SRCALPHA)
        self.ctext(self.f_big, f"LEVEL {self.level}", (240, 240, 240), 6, lay)
        self.ctext(self.f_mid, "Take the bomb from beside the rover to the Alien Missile Base", (235, 230, 205), 80, lay)
        if tip:
            self.ctext(self.f_hud, f"New: {al.KIND_NAMES[kind]}! {tip}", (255, 190, 120), 124, lay)
            self.ctext(self.f_hud, "The red beacons lead the way", (255, 150, 130), 160, lay)
        else:
            self.ctext(self.f_hud, "The red beacons lead the way", (255, 150, 130), 124, lay)
        self._tc[k] = lay
        return lay

    def draw_card(self):
        self.veil(min(190, int(self.mode_t * 400)))
        self.ctext(self.f_big, f"LEVEL {self.level} COMPLETE", (120, 255, 150), 120)
        self.ctext(self.f_mid, f"Alien Missile Base destroyed!   Bonus {self.bonus_last:,}", (255, 220, 120), 210)
        self.ctext(self.f_mid, f"Score {self.score:,}", (235, 235, 235), 260)
        nxt = self.level + 1
        kind = al.level_new_kind(nxt)
        if kind:
            self.ctext(self.f_mid, f"Level {nxt}: watch out for the {al.KIND_NAMES[kind]}!", (255, 150, 120), 330)
            if kind in al.KIND_TIPS:
                self.ctext(self.f_hud, al.KIND_TIPS[kind], (230, 220, 200), 372)
            if kind == "spore":
                surf = self.alien_spr.get("green", int(self.t * 6) % al.SPIN_FRAMES)[0]
            else:
                sy = 1.0 if kind in ("creeper", "worm") else 0.75 + 0.25 * abs(math.sin(self.t * 5))
                surf = self.paint.get(kind, False, sy, 1.0, int(self.t * 12))[0]
            k = ("card_alien", id(surf))
            big = self._tc.get(k)
            if big is None or big[0] is not surf:
                big = self._tc[k] = (surf, pygame.transform.smoothscale(surf, (int(surf.get_width() * 1.6), int(surf.get_height() * 1.6))))
            surf = big[1]
            self.screen.blit(surf, ((W - surf.get_width()) // 2, 480 - surf.get_height() // 2))
        else:
            self.ctext(self.f_mid, f"Level {nxt}: everything at once, and faster!", (255, 150, 120), 330)
        self.ctext(self.f_hud, "A new base has landed. Follow the red beacons.", (200, 200, 200), 580)

    def draw_over(self):
        self.veil(min(200, int(self.mode_t * 300)))
        self.ctext(self.f_big, "GAME OVER", (255, 110, 90), 110)
        self.ctext(self.f_mid, f"Score {self.score:,}      Level {self.level}", (255, 220, 120), 200)
        if self.over_reason and not self.entry_active:
            self.ctext(self.f_hud, self.over_reason, (255, 160, 140), 246)
        if self.entry_active:
            self.ctext(self.f_mid, gamepad.entry_heading(self.pad_on), (235, 235, 235), 280)
            box = pygame.Rect(W // 2 - 200, 330, 400, 56)
            pygame.draw.rect(self.screen, (20, 24, 30), box)
            pygame.draw.rect(self.screen, (255, 200, 90), box, 2)
            self.ctext(self.f_big, self.entry_shown(), (255, 255, 255), 334)
            hint = gamepad.entry_hint(self.pad_on, scores.NAME_MAX)
            self.ctext(self.f_hud, hint, (180, 180, 180), 400)
        else:
            msg = self.entry_status()
            if msg:
                self.ctext(self.f_mid, msg, (200, 230, 255), 290)
            self.draw_top10(440)
            self.ctext(self.f_hud, gamepad.over_prompt(self.pad_on), (200, 200, 200), 660)

    # Pad name entry. Up and down cycle the letter in the brackets, left and
    # right move, A accepts (the same as Enter), B skips, X deletes a letter.
    _ENTRY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "

    def entry_shown(self):
        name = self.entry_name
        if not self.pad_on:
            cur = "_" if int(self.t * 2) % 2 == 0 else " "
            return name + cur
        pos = self.entry_pos
        if pos < 0:
            pos = 0
        blink = "_" if int(self.t * 2) % 2 == 0 else " "
        if pos >= len(name):
            return name + "[" + blink + "]"
        return name[:pos] + "[" + name[pos] + "]" + name[pos + 1:]

    def entry_pad_move(self, action):
        name = self.entry_name
        pos = self.entry_pos
        if pos < 0:
            pos = 0
        if action == "menu_left":
            self.entry_pos = max(0, pos - 1)
            return
        if action == "menu_right":
            if len(name) >= scores.NAME_MAX:
                self.entry_pos = min(max(0, len(name) - 1), pos + 1)
            else:
                self.entry_pos = min(len(name), pos + 1)
            return
        if action not in ("menu_up", "menu_down"):
            return
        if pos > len(name):
            pos = len(name)
        if pos == len(name):
            if len(name) >= scores.NAME_MAX:
                self.entry_pos = max(0, len(name) - 1)
                return
            # The first press on an empty slot starts at A; the next one cycles.
            self.entry_name = name + "A"
            self.entry_pos = pos
            return
        delta = 1 if action == "menu_up" else -1
        ch = name[pos].upper()
        chars = self._ENTRY_CHARS
        idx = chars.index(ch) if ch in chars else 0
        if ch not in chars:
            nxt = chars[0]
        else:
            nxt = chars[(idx + delta) % len(chars)]
        self.entry_name = name[:pos] + nxt + name[pos + 1:]
        self.entry_pos = pos

    def entry_pad_delete(self):
        name = self.entry_name
        pos = self.entry_pos
        if not name:
            self.entry_pos = 0
            return
        if pos >= len(name):
            self.entry_name = name[:-1]
            self.entry_pos = len(self.entry_name)
            return
        self.entry_name = name[:pos] + name[pos + 1:]
        self.entry_pos = min(pos, len(self.entry_name))

    def entry_status(self):
        name = self.entry_note
        if self.score <= 0:
            return ""
        if not name:
            return "Score not sent" if self.qualified else ""
        if not self.board.online:
            return f"Saved as {name}"
        if (name, self.score) in self.board.pending:
            return f"Sending as {name}\u2026" if self.board.busy else "Offline \u2013 will send later"
        return f"Score sent as {name}"

    def draw_top10(self, py):
        sc = self.screen
        pw, ph = 960, 196
        px = (W - pw) // 2
        panel = self._tc.get("top10_panel")
        if panel is None:
            panel = self._tc["top10_panel"] = pygame.Surface((pw, ph), pygame.SRCALPHA)
            panel.fill((4, 8, 14, 200))
            pygame.draw.rect(panel, (90, 180, 210, 220), panel.get_rect(), 1, border_radius=6)
        sc.blit(panel, (px, py))
        hd = self.text(self.f_mid, "Top 10 Scores", (140, 230, 255))
        sc.blit(hd, ((W - hd.get_width()) // 2, py + 6))
        rows = self.board.top(scores.TOP_N)
        if not rows:
            msg = {"loading": "Fetching the scores\u2026", "offline": "Scores unavailable offline"}.get(
                self.board.status, "No scores yet \u2013 be the first!")
            im = self.text(self.f_hud, msg, (220, 220, 220))
            sc.blit(im, ((W - im.get_width()) // 2, py + 90))
        for i, (name, score, country) in enumerate(rows):
            col_x = px + (60 if i < 5 else pw // 2 + 30)
            y = py + 46 + (i % 5) * 28
            colour = (255, 210, 100) if i == 0 else (225, 230, 235)
            num = self.text(self.f_hud, f"{i + 1}.", colour)
            sc.blit(num, (col_x + 34 - num.get_width(), y))
            flag = self.flags.get(country) if country else None
            if flag:
                sc.blit(flag, (col_x + 46, y + 2))
            sc.blit(self.text(self.f_hud, name, colour), (col_x + 84, y))
            s_ = self.text(self.f_hud, f"{score:,}", colour)
            sc.blit(s_, (col_x + 380 - s_.get_width(), y))

    def draw_controls(self, py):
        sc = self.screen
        lines = gamepad.control_lines(self.pad_on)
        panel = self._tc.get("controls_panel")
        if panel is None:
            panel = self._tc["controls_panel"] = pygame.Surface((960, 160), pygame.SRCALPHA)
            pygame.draw.rect(panel, (4, 8, 14, 180), panel.get_rect(), border_radius=6)
            pygame.draw.rect(panel, (90, 180, 210, 200), panel.get_rect(), 1, border_radius=6)
        sc.blit(panel, ((W - 960) // 2, py))
        for i, ln in enumerate(lines):
            col = (225, 230, 235) if i < 2 else (255, 220, 140)
            im = self.text(self.f_hud, ln, col)
            sc.blit(im, ((W - im.get_width()) // 2, py + 18 + i * 34))

    def build_title_bg(self):
        try:
            img = pygame.image.load(TITLE_ART)
            img = img.convert() if pygame.display.get_surface() else img
        except Exception:
            img = pygame.Surface((W, H)); img.fill((0, 0, 0))
        big = pygame.transform.smoothscale(img, (int(W * 1.08), int(H * 1.08)))
        shade = pygame.Surface(big.get_size(), pygame.SRCALPHA)
        for y in range(big.get_height()):                       # darken the bottom for the panels
            a = int(max(0, min(1, (y - big.get_height() * 0.55) / (big.get_height() * 0.35))) * 150)
            pygame.draw.line(shade, (0, 0, 0, a), (0, y), (big.get_width(), y))
        big.blit(shade, (0, 0))
        stars = self.title_stars(big)
        # the title lettering and its cyan glow never change: render them once
        ttl = "MOON SWEEPER"
        txt = self.f_title.render(ttl, True, (40, 160, 220))
        gw, gh = txt.get_size()
        glow = pygame.Surface((gw, gh)); glow.fill((0, 0, 0)); glow.blit(txt, (0, 0))
        g = pygame.transform.smoothscale(pygame.transform.smoothscale(glow, (gw // 8, gh // 8)), (gw + 40, gh + 30))
        lettering = (g, self.f_title.render(ttl, True, (10, 20, 30)), self.f_title.render(ttl, True, (235, 245, 255)), gw)
        return big, stars, lettering

    @staticmethod
    def title_earth(big):
        """The Earth in the (scaled) title picture: centre x, y and radius, in its own pixels."""
        k = big.get_width() / W
        return TITLE_EARTH[0] * k, TITLE_EARTH[1] * k, TITLE_EARTH[2] * k

    @classmethod
    def title_stars(cls, big):
        """Twinkling stars, placed in the background picture's own coordinates (so they drift
        with it) and only on empty black sky: never on the Earth, Jetman or the base."""
        lum = pygame.surfarray.array3d(big).astype(np.int32).sum(axis=2) // 3    # (w, h)
        hw, hh = lum.shape
        # the brightest thing within 9 px either way: a star must have nothing but sky round it
        m = lum.copy()
        for _ in range(3):
            m = np.maximum.reduce([m, np.roll(m, 3, 0), np.roll(m, -3, 0), np.roll(m, 3, 1), np.roll(m, -3, 1)])
        ex, ey, er = cls.title_earth(big)
        rnd = random.Random(11)
        stars = []
        tries = 0
        while len(stars) < 70 and tries < 6000:
            tries += 1
            x, y = rnd.randrange(4, hw - 4), rnd.randrange(4, int(hh * 0.42))
            if (x - ex) ** 2 + (y - ey) ** 2 < (er + 14) ** 2:
                continue                                  # never in front of the Earth (or its glow)
            if m[x, y] < 34 and all((x - a) ** 2 + (y - b) ** 2 > 400 for a, b, *_ in stars):
                stars.append((x, y, rnd.uniform(0, 6.28), rnd.uniform(1.5, 4)))
        return stars

    @staticmethod
    def title_offset(big, t):
        ox = -(big.get_width() - W) / 2 + math.sin(t * 0.07) * (big.get_width() - W) / 2   # slow drift
        oy = -(big.get_height() - H) / 2 + math.cos(t * 0.05) * (big.get_height() - H) / 3
        return int(ox), int(oy)

    def draw_title(self, loaded, ready_frac):
        sc = self.screen
        if self.title_bg is None:
            self.title_bg = self.build_title_bg()
        big, stars, (g, dark, light, gw) = self.title_bg
        t = self.title_t
        ox, oy = self.title_offset(big, t)
        sc.blit(big, (ox, oy))
        self.title_star_rects = []
        if getattr(self, "title_twinkle", True):
            for x, y, ph, sp in stars:                           # twinkle, moving with the picture
                k = 0.5 + 0.5 * math.sin(t * sp + ph)
                if k > 0.75:
                    c = int(150 + 105 * k)
                    r = (int(x + ox), int(y + oy), 2, 2)
                    sc.fill((c, c, c), r)
                    self.title_star_rects.append(r)
        # title with a cyan glow
        x0 = (W - gw) // 2
        sc.blit(g, (x0 - 20, 22), special_flags=pygame.BLEND_ADD)
        sc.blit(dark, (x0 + 4, 40))
        sc.blit(light, (x0, 36))
        self.ctext(self.f_mid, "Bomb the Alien Missile Base. Save the Moon.", (255, 210, 120), 150)
        if not loaded:
            self.ctext(self.f_hud, f"Preparing the lunar surface\u2026 {int(ready_frac * 100)}%", (200, 220, 230), 470)
            pygame.draw.rect(sc, (60, 70, 80), (W // 2 - 200, 500, 400, 10), 1)
            pygame.draw.rect(sc, (120, 210, 255), (W // 2 - 199, 501, int(398 * ready_frac), 8))
        else:
            if int(t * 1.6) % 2 == 0:
                self.ctext(self.f_mid, gamepad.title_prompt(self.pad_on), (255, 255, 255), 452)
            if int(t / TOP10_TIME) % 2 == 1:
                self.draw_top10(490)
            else:
                self.draw_controls(500)
        best = max(self.board.high_score(), 0)
        if best:
            im = self.text(self.f_hud, f"High score: {best:,}", (255, 210, 120))
            sc.blit(im, (W - im.get_width() - 18, H - 30))
        cp = self.text(self.font, COPYRIGHT, (190, 190, 190))
        sc.blit(cp, ((W - cp.get_width()) // 2, H - 26))
