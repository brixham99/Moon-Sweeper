"""The game around the sandbox: levels, the bomb, fuel, stamina, lives, scoring, the
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

import pygame

import aliens as al
import base as bs
import rover as rv
import scores
import world as wd
from world import to_screen

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1280, 720

# ---------------------------------------------------------------- tuning
LIVES = 3
FUEL_MAX = 100.0
FUEL_BURN = FUEL_MAX / 30.0       # 30 s of full thrust on a tank
FUEL_REFILL = 40.0                # per second standing on the rover roof
STAMINA_MAX = 100.0
STAMINA_DRAIN = STAMINA_MAX / 150.0   # 2.5 minutes away from the rover
STAMINA_REST = 30.0               # per second standing on the rover roof
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
DIE_TIME = 1.8
TOP10_TIME = 7.0
COPYRIGHT = "\u00a92026 Nathan Anderson. Made in England by Optica."
TITLE_ART = os.path.join(HERE, "assets", "art", "title.jpg")
TITLE_MUSIC = os.path.join(HERE, "assets", "sounds", "title_theme.ogg")
BOMB_ART = os.path.join(HERE, "assets", "art", "bomb.png")


class Bomb:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z
        self.vx = self.vy = self.vz = 0.0
        self.state = "ground"         # ground | carried | falling


class Campaign:
    # ------------------------------------------------------------ set-up
    def campaign_init(self, online=False):
        self.mode = "play"
        self.level = 1
        self.score = 0
        self.lives = LIVES
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

    def bomb_home(self):
        rx, ry = self.world.rover
        return rx - 0.6, ry + rv.HALF_W + 1.1           # beside the rover, on the start side

    def new_game(self):
        self.level, self.score, self.lives = 1, 0, LIVES
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
        self.reset_jetman()
        self.bomb = Bomb(*self.bomb_home())
        self.aliens.clear(); self.spawned.clear(); self.killed.clear()
        self.splats.clear(); self.lasers.clear()

    def reset_jetman(self):
        s = self.world.start
        self.x, self.y, self.z = s[0], s[1], 0.0
        self.vx = self.vy = self.vz = 0.0
        self.a = math.pi / 4
        self.fuel, self.stamina = FUEL_MAX, STAMINA_MAX
        self.hit_t = 9.0
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
        if self.mode in ("play", "boom"):
            if self.thrusting and not self.infinite:
                self.fuel = max(0.0, self.fuel - FUEL_BURN * dt)
            if not self.infinite:
                self.stamina = max(0.0, self.stamina - STAMINA_DRAIN * dt)
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
                self.lose_life()
        if self.mode == "boom" and self.mode_t > BOOM_TIME:
            self.mode, self.mode_t = "card", 0.0
            self.sfx.play("levelup")
        elif self.mode == "card" and self.mode_t > CARD_TIME:
            self.start_level(self.level + 1)
            self.mode = "play"
        elif self.mode == "dying" and self.mode_t > DIE_TIME:
            if self.lives <= 0:
                self.game_over()
            else:
                self.reset_jetman()
                if self.bomb.state != "ground":
                    self.bomb = Bomb(*self.bomb_home())
                self.mode, self.mode_t = "play", 0.0

    def hurt_stamina(self, amount):
        if self.mode == "play" and not self.infinite:
            self.stamina = max(0.0, self.stamina - amount)

    def lose_life(self):
        self.lives -= 1
        self.mode, self.mode_t = "dying", 0.0
        self.sfx.play("hurt")
        if self.bomb.state == "carried":
            self.bomb.state = "lost"
        for _ in range(30):
            a = self.rng.uniform(0, math.tau); s = self.rng.uniform(1, 3.5)
            self.parts.append(self.make_part(self.x, self.y, self.z + 40, math.cos(a) * s, math.sin(a) * s,
                                             self.rng.uniform(40, 200), self.rng.uniform(0.3, 0.8), "spark", 2))

    def alien_killed(self, a):
        self.score += al.KINDS[a.kind][2]
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
                if self.base_alive and (bs.field_e(bx, by, b.x, b.y, b.z) < 1.0 or b.z <= bs.height_at(bx, by, b.x, b.y) + 2):
                    self.destroy_base(b.x, b.y, max(b.z, 30))
                    return
                fl = self.world.floor_at(b.x, b.y)
                if b.z <= fl:
                    b.z, b.state = fl, "ground"
                    b.vx = b.vy = b.vz = 0.0
                    self.sfx.play("bounce", 0.9)
                    break

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
        self.world.set_base(self.world.BASE_GONE)          # gone: the fireball covers the moment
        self.world.add_blast(bx, by)                       # ...and leaves a giant scorched crater
        self.mode, self.mode_t = "boom", 0.0
        self.bonus_last = LEVEL_BONUS * self.level
        self.score += self.bonus_last
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
            self.to_title()
        elif e.key == BOMB_KEY:
            self.drop_bomb()
        elif self.mode == "card" and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.mode_t = CARD_TIME
        return None

    def handle_text(self, text):
        if self.mode == "over" and self.entry_active:
            for ch in text:
                if ch.isascii() and (ch.isalnum() or ch == " ") and len(self.entry_name) < scores.NAME_MAX:
                    if ch == " " and (not self.entry_name or self.entry_name.endswith(" ")):
                        continue
                    self.entry_name += ch

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
            try:
                pygame.key.start_text_input()
            except Exception:
                pass

    def to_title(self):
        if self.mode in ("play", "dying", "boom", "card") and self.score > 0:
            self.board.record_local(self.score)
        self.mode, self.title_t = "title", 0.0
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
        im = font.render(s, True, col)
        if not shadow:
            return im
        out = pygame.Surface((im.get_width() + 2, im.get_height() + 2), pygame.SRCALPHA)
        out.blit(font.render(s, True, (0, 0, 0)), (2, 2))
        out.blit(im, (0, 0))
        return out

    def ctext(self, font, s, col, y):
        im = self.text(font, s, col)
        self.screen.blit(im, ((W - im.get_width()) // 2, y))
        return im

    def bar(self, x, y, w, h, frac, col, label):
        sc = self.screen
        lab = self.rtext(self.font, label, (200, 200, 200))
        sc.blit(lab, (x, y + (h - lab.get_height()) // 2 + 1))
        x += lab.get_width() + 8
        pygame.draw.rect(sc, (40, 40, 44), (x, y, w, h))
        low = frac < 0.25 and int(self.t * 4) % 2 == 0
        pygame.draw.rect(sc, (235, 60, 50) if low else col, (x, y, int(w * max(0.0, min(1.0, frac))), h))
        pygame.draw.rect(sc, (150, 150, 150), (x, y, w, h), 1)
        return x + w

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
            sh = pygame.Surface((40, 18), pygame.SRCALPHA)
            pygame.draw.ellipse(sh, (0, 0, 0, 110), sh.get_rect())
            self.screen.blit(sh, (gx - cx - 26, gy - cy - 9))
        self.screen.blit(img, (px - img.get_width() / 2, py - img.get_height() + 4))
        if int(self.t * 2.5) % 2 == 0:
            g = pygame.transform.smoothscale(self.glow, (26, 26))
            g.fill((255, 60, 40, 255), special_flags=pygame.BLEND_RGBA_MULT)
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
        pygame.draw.rect(sc, (0, 0, 0), (0, 0, W, 34))
        pygame.draw.line(sc, (90, 90, 90), (0, 34), (W, 34))
        x = 14
        for s_, col in ((f"SCORE {self.score:,}", (255, 220, 120)), (f"LEVEL {self.level}", (225, 225, 225)),
                        (f"LIVES {max(0, self.lives)}", (225, 225, 225))):
            im = self.rtext(self.font, s_, col)
            sc.blit(im, (x, 9)); x += im.get_width() + 26
        x = self.bar(x, 10, 130, 14, self.fuel / FUEL_MAX, (255, 190, 60), "FUEL") + 22
        x = self.bar(x, 10, 130, 14, self.stamina / STAMINA_MAX, (90, 220, 120), "STAMINA") + 22
        st = {"carried": ("BOMB CARRIED \u2013 PRESS B TO DROP", (255, 120, 80)), "falling": ("BOMB FALLING", (255, 120, 80)),
              "ground": ("BOMB: PICK IT UP", (200, 200, 200)) if self.bomb_home() == (self.bomb.x, self.bomb.y) else ("BOMB ON THE GROUND", (200, 200, 200)),
              "used": ("BASE DESTROYED", (120, 255, 140)), "lost": ("", (0, 0, 0))}[self.bomb.state]
        sc.blit(self.rtext(self.font, st[0], st[1]), (x, 9))
        alt = self.z - self.floor()
        im = self.rtext(self.font, f"ALT {int(alt):3d} m", (170, 170, 170))
        sc.blit(im, (W - im.get_width() - 14, 9))
        pygame.draw.rect(sc, (0, 0, 0), (0, H - 34, W, 34))
        pygame.draw.line(sc, (90, 90, 90), (0, H - 34), (W, H - 34))
        hint = "Left/Right turn   Up/Down move   Space jetpack   Ctrl or Z fire   B drop bomb   F full screen   Esc title screen"
        im = self.rtext(self.font, hint, (150, 150, 150))
        sc.blit(im, (W - im.get_width() - 14, H - 25))
        if self.on_rover_roof() and (self.fuel < FUEL_MAX - 0.5 or self.stamina < STAMINA_MAX - 0.5):
            self.ctext(self.f_hud, "Refuelling and resting on the rover\u2026", (140, 230, 255), 46)
        elif self.fuel <= 0 and not self.infinite:
            self.ctext(self.f_hud, "Out of fuel! Walk back to the rover and climb aboard", (255, 120, 90), 46)
        if self.mode == "play" and self.mode_t < 4.0:
            self.ctext(self.f_big, f"LEVEL {self.level}", (235, 235, 235), 120)
            self.ctext(self.f_mid, "Take the bomb from beside the rover to the Alien Missile Base", (220, 220, 200), 190)
            self.ctext(self.f_hud, "The red beacons lead the way", (255, 140, 120), 230)
        if self.mode == "dying":
            self.ctext(self.f_big, "EXHAUSTED!", (255, 110, 90), 220)
            self.ctext(self.f_mid, f"Lives left: {max(0, self.lives)}" if self.lives > 0 else "No lives left", (230, 230, 230), 300)
        if self.mode == "card":
            self.draw_card()
        if self.mode == "over":
            self.draw_over()

    def veil(self, a=170):
        v = pygame.Surface((W, H), pygame.SRCALPHA)
        v.fill((0, 0, 0, a))
        self.screen.blit(v, (0, 0))

    def draw_card(self):
        self.veil(min(190, int(self.mode_t * 400)))
        self.ctext(self.f_big, f"LEVEL {self.level} COMPLETE", (120, 255, 150), 120)
        self.ctext(self.f_mid, f"Alien Missile Base destroyed!   Bonus {self.bonus_last:,}", (255, 220, 120), 210)
        self.ctext(self.f_mid, f"Score {self.score:,}", (235, 235, 235), 260)
        nxt = self.level + 1
        kind = al.level_new_kind(nxt)
        if kind:
            self.ctext(self.f_mid, f"Level {nxt}: watch out for the {al.KIND_NAMES[kind]}!", (255, 150, 120), 330)
            if kind == "spore":
                surf = self.alien_spr.get("green", int(self.t * 6) % al.SPIN_FRAMES)[0]
            else:
                surf = self.paint.get(kind, False, 1.0 if kind == "creeper" else 0.75 + 0.25 * abs(math.sin(self.t * 5)), 1.0, int(self.t * 12))[0]
            surf = pygame.transform.smoothscale_by(surf, 1.6)
            self.screen.blit(surf, ((W - surf.get_width()) // 2, 470 - surf.get_height() // 2))
        else:
            self.ctext(self.f_mid, f"Level {nxt}: everything at once, and faster!", (255, 150, 120), 330)
        self.ctext(self.f_hud, "A new base has landed. Follow the red beacons.", (200, 200, 200), 580)

    def draw_over(self):
        self.veil(min(200, int(self.mode_t * 300)))
        self.ctext(self.f_big, "GAME OVER", (255, 110, 90), 110)
        self.ctext(self.f_mid, f"Score {self.score:,}      Level {self.level}", (255, 220, 120), 200)
        if self.entry_active:
            self.ctext(self.f_mid, "You made the Top 10! Type your name:", (235, 235, 235), 280)
            cur = "_" if int(self.t * 2) % 2 == 0 else " "
            box = pygame.Rect(W // 2 - 200, 330, 400, 56)
            pygame.draw.rect(self.screen, (20, 24, 30), box)
            pygame.draw.rect(self.screen, (255, 200, 90), box, 2)
            self.ctext(self.f_big, self.entry_name + cur, (255, 255, 255), 334)
            self.ctext(self.f_hud, f"Up to {scores.NAME_MAX} letters or numbers.   Enter to save, Esc to skip", (180, 180, 180), 400)
        else:
            msg = self.entry_status()
            if msg:
                self.ctext(self.f_mid, msg, (200, 230, 255), 290)
            self.draw_top10(440)
            self.ctext(self.f_hud, "Press Enter to return to the title screen", (200, 200, 200), 660)

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
        panel = pygame.Surface((pw, ph), pygame.SRCALPHA)
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
        lines = ["Left / Right: turn      Up / Down: forwards / backwards      Space: jetpack",
                 "Ctrl or Z: fire      B: drop the bomb      F: full screen      Esc: title screen",
                 "Carry the bomb from the rover to the Alien Missile Base and drop it on the dome.",
                 "Land on the rover roof to refuel the jetpack and get your strength back."]
        panel = pygame.Surface((960, 160), pygame.SRCALPHA)
        panel.fill((4, 8, 14, 180))
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
        rnd = random.Random(11)
        stars = [(rnd.randrange(W), rnd.randrange(250), rnd.uniform(0, 6.28), rnd.uniform(1.5, 4)) for _ in range(70)]
        return big, stars

    def draw_title(self, loaded, ready_frac):
        sc = self.screen
        if self.title_bg is None:
            self.title_bg = self.build_title_bg()
        big, stars = self.title_bg
        t = self.title_t
        ox = -(big.get_width() - W) / 2 + math.sin(t * 0.07) * (big.get_width() - W) / 2   # slow drift
        oy = -(big.get_height() - H) / 2 + math.cos(t * 0.05) * (big.get_height() - H) / 3
        sc.blit(big, (ox, oy))
        for x, y, ph, sp in stars:                               # twinkle
            k = 0.5 + 0.5 * math.sin(t * sp + ph)
            if k > 0.75:
                c = int(150 + 105 * k)
                sc.fill((c, c, c), (x, y, 2, 2))
        # title with a cyan glow
        ttl = "MOON SWEEPER"
        txt = self.f_title.render(ttl, True, (40, 160, 220))
        gw, gh = txt.get_size()
        glow = pygame.Surface((gw, gh)); glow.fill((0, 0, 0)); glow.blit(txt, (0, 0))
        g = pygame.transform.smoothscale(pygame.transform.smoothscale(glow, (gw // 8, gh // 8)), (gw + 40, gh + 30))
        x0 = (W - gw) // 2
        sc.blit(g, (x0 - 20, 22), special_flags=pygame.BLEND_ADD)
        sc.blit(self.f_title.render(ttl, True, (10, 20, 30)), (x0 + 4, 40))
        sc.blit(self.f_title.render(ttl, True, (235, 245, 255)), (x0, 36))
        self.ctext(self.f_mid, "Bomb the Alien Missile Base. Save the Moon.", (255, 210, 120), 150)
        if not loaded:
            self.ctext(self.f_hud, f"Preparing the lunar surface\u2026 {int(ready_frac * 100)}%", (200, 220, 230), 470)
            pygame.draw.rect(sc, (60, 70, 80), (W // 2 - 200, 500, 400, 10), 1)
            pygame.draw.rect(sc, (120, 210, 255), (W // 2 - 199, 501, int(398 * ready_frac), 8))
        else:
            if int(t * 1.6) % 2 == 0:
                self.ctext(self.f_mid, "Press Enter to start", (255, 255, 255), 452)
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
