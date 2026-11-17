#!/usr/bin/env python3
"""
REALM DEFENDERS - a medieval fantasy tower defense game (pygame)

Run:      pip install pygame      (or: pip install pygame-ce)
          python medieval_td.py

Controls
    Left click      pick a tower in the shop, then click the map to build it
                    (hold SHIFT to keep building the same tower)
    Click a tower   select it: shows its range, upgrade paths and a sell button
    Right click/ESC cancel building / deselect
    SPACE           start the next wave (you control the pace!)
    1-9             pick a tower from the shop
    Q / W           buy the next upgrade on path 1 / path 2 of the selected tower
    S               sell selected tower
    F               cycle game speed (x1 / x2 / x3)
    P               pause
    R / SPACE       restart after defeat

UPGRADES
    Every tower has two upgrade paths with three tiers each. A path's third tier
    is only available while the other path is at tier 1 or lower, so you have to
    commit (3/1, 2/2 or 1/3).

HOW TO ADD A NEW TOWER
    1. Subclass `Tower`, set id / name / cost / range / cooldown / damage /
       description and any extra stats (they can be changed by upgrades).
    2. Define `paths` with two UpgradePath objects (see the existing towers).
       An upgrade is U(name, cost, description, stat=("*"|"+"|"=", value)).
    3. Implement `attack(self, target, game)` and `draw_sprite(...)`.
       Support towers can set `attacks = False` and use `update_extra` /
       `apply_aura` instead (see Bard).
    4. Decorate the class with @register_tower. It appears in the shop with a
       hotkey and works with placing, upgrading and selling automatically.

HOW TO ADD A NEW ENEMY
    Subclass `Enemy`, set stats, implement `draw_sprite`, optionally override
    `special(dt, game)` (per-frame behaviour) or `on_death(game)` (return a list
    of enemies to spawn), decorate with @register_enemy and add it to
    `wave_composition()`.
"""
import math
import random
import sys
from bisect import bisect_right
from dataclasses import dataclass

import pygame

# =============================================================================
# CONFIG
# =============================================================================
SCREEN_W, SCREEN_H = 1200, 700
MAP_W = 900
SIDEBAR_W = SCREEN_W - MAP_W
SIDEBAR_X = MAP_W
FPS = 60

START_MONEY = 350
START_LIVES = 20
PATH_WIDTH = 46
SELL_REFUND = 0.7          # fraction of everything spent that is refunded on sell
REWARD_PER_HP = 0.1        # gold per point of (unscaled) enemy HP
WAVE_BONUS_BASE = 40       # gold for clearing a wave: base + per_wave * wave
WAVE_BONUS_PER_WAVE = 10
SPEED_STEPS = (1, 2, 3)
FLASH_TIME = 0.2           # tower attack animation length
LOCK_TIER = 3              # tier that needs the other path at <= 1
THORN_TICK = 0.15          # seconds between thorn damage ticks
WEB_SLOW = 0.5             # tower attack-rate multiplier while webbed

# Path the enemies walk (first point is off-screen, last is the castle gate)
WAYPOINTS = [(-40, 100), (760, 100), (760, 260), (140, 260), (140, 420),
             (760, 420), (760, 580), (140, 580), (140, 656)]
CASTLE_RECT = pygame.Rect(78, 626, 124, 74)

# Colours
GRASS = (88, 140, 70)
PATH_COL = (160, 122, 80)
PATH_EDGE = (110, 80, 50)
GOLD = (236, 190, 70)
INK = (58, 38, 20)
PARCHMENT = (226, 204, 156)
ACCENTS = ((214, 160, 50), (80, 140, 214))   # upgrade path 1 / path 2 colours


# =============================================================================
# HELPERS
# =============================================================================
_font_cache = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = pygame.font.SysFont("georgia,palatino,timesnewroman,serif", size, bold=bold)
    return _font_cache[key]


def draw_text(surf, text, size, color, pos, anchor="topleft", bold=False, shadow=False):
    fnt = font(size, bold)
    img = fnt.render(text, True, color)
    rect = img.get_rect(**{anchor: pos})
    if shadow:
        surf.blit(fnt.render(text, True, (20, 12, 6)), rect.move(2, 2))
    surf.blit(img, rect)
    return rect


def wrap_text(text, fnt, width):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if fnt.size(trial)[0] <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def seg_dist(p, a, b):
    ab = b - a
    l2 = ab.length_squared()
    if l2 == 0:
        return p.distance_to(a)
    t = max(0.0, min(1.0, (p - a).dot(ab) / l2))
    return p.distance_to(a + ab * t)


def draw_range(surf, pos, radius, color, alpha=50):
    size = int(radius * 2) + 6
    tmp = pygame.Surface((size, size), pygame.SRCALPHA)
    c = size // 2
    pygame.draw.circle(tmp, (*color, alpha), (c, c), int(radius))
    pygame.draw.circle(tmp, (*color, 190), (c, c), int(radius), 2)
    surf.blit(tmp, (pos[0] - c, pos[1] - c))


def draw_coin(s, x, y, r=8):
    pygame.draw.circle(s, (150, 105, 20), (x, y), r)
    pygame.draw.circle(s, GOLD, (x, y), r - 2)
    pygame.draw.circle(s, (255, 230, 140), (x - 2, y - 2), max(1, r // 4))


def draw_heart(s, x, y):
    pygame.draw.circle(s, (210, 40, 50), (x - 4, y - 2), 5)
    pygame.draw.circle(s, (210, 40, 50), (x + 4, y - 2), 5)
    pygame.draw.polygon(s, (210, 40, 50), [(x - 9, y), (x + 9, y), (x, y + 10)])


def draw_banner_icon(s, x, y):
    pygame.draw.line(s, (200, 200, 210), (x - 6, y - 9), (x - 6, y + 9), 2)
    pygame.draw.polygon(s, (170, 40, 40), [(x - 5, y - 9), (x + 9, y - 5), (x - 5, y)])


def draw_note(s, x, y, color=GOLD, size=1.0):
    """A little musical note."""
    x, y = int(x), int(y)
    pygame.draw.circle(s, color, (x, y), max(2, int(3 * size)))
    pygame.draw.line(s, color, (x + int(3 * size), y), (x + int(3 * size), y - int(10 * size)), 2)
    pygame.draw.line(s, color, (x + int(3 * size), y - int(10 * size)), (x + int(7 * size), y - int(7 * size)), 2)


# =============================================================================
# PATH
# =============================================================================
class Path:
    def __init__(self, points):
        self.points = [pygame.Vector2(p) for p in points]
        self.cum = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.cum.append(self.cum[-1] + a.distance_to(b))
        self.length = self.cum[-1]

    def position_at(self, d):
        d = max(0.0, min(self.length, d))
        i = bisect_right(self.cum, d) - 1
        i = max(0, min(i, len(self.points) - 2))
        seg = self.cum[i + 1] - self.cum[i]
        t = (d - self.cum[i]) / seg if seg else 0
        return self.points[i].lerp(self.points[i + 1], t)

    def distance_to_point(self, p):
        p = pygame.Vector2(p)
        return min(seg_dist(p, a, b) for a, b in zip(self.points, self.points[1:]))


# =============================================================================
# EFFECTS (purely visual)
# =============================================================================
class Effect:
    life = 0.5

    def __init__(self):
        self.age = 0.0

    def update(self, dt):
        self.age += dt

    @property
    def dead(self):
        return self.age >= self.life

    @property
    def p(self):
        return min(1.0, self.age / self.life)


class Explosion(Effect):
    life = 0.4

    def __init__(self, pos, radius):
        super().__init__()
        self.pos, self.radius = pygame.Vector2(pos), radius

    def draw(self, s):
        p = self.p
        r = max(2, int(self.radius * (0.35 + 0.65 * p)))
        a = int(230 * (1 - p))
        tmp = pygame.Surface((r * 2 + 4, r * 2 + 4), pygame.SRCALPHA)
        c = r + 2
        pygame.draw.circle(tmp, (255, 130, 30, a // 2), (c, c), r)
        pygame.draw.circle(tmp, (255, 220, 90, a), (c, c), r, 3)
        core = int(r * 0.55 * (1 - p))
        if core > 1:
            pygame.draw.circle(tmp, (255, 250, 200, a), (c, c), core)
        s.blit(tmp, (self.pos.x - c, self.pos.y - c))


class Ring(Effect):
    """Expanding coloured ring (spells, upgrades, summons)."""
    life = 0.45

    def __init__(self, pos, color, max_r=30, life=0.45):
        super().__init__()
        self.pos, self.color, self.max_r, self.life = pygame.Vector2(pos), color, max_r, life

    def draw(self, s):
        p = self.p
        r = max(2, int(self.max_r * (0.2 + 0.8 * p)))
        a = int(220 * (1 - p))
        tmp = pygame.Surface((r * 2 + 6, r * 2 + 6), pygame.SRCALPHA)
        c = r + 3
        pygame.draw.circle(tmp, (*self.color, a // 4), (c, c), r)
        pygame.draw.circle(tmp, (*self.color, a), (c, c), r, 3)
        s.blit(tmp, (self.pos.x - c, self.pos.y - c))


class Slash(Effect):
    life = 0.16

    def __init__(self, pos, angle):
        super().__init__()
        self.pos, self.angle = pygame.Vector2(pos), angle

    def draw(self, s):
        p = self.p
        pts = []
        for i in range(7):
            a = self.angle - 1.0 + 2.0 * (i / 6) * min(1.0, p * 1.6 + 0.3)
            pts.append((self.pos.x + math.cos(a) * 15, self.pos.y + math.sin(a) * 15))
        c = int(255 - 90 * p)
        pygame.draw.lines(s, (c, c, 255), False, pts, 3)


class Particle(Effect):
    def __init__(self, pos, vel, color, life=0.5, size=4):
        super().__init__()
        self.pos, self.vel = pygame.Vector2(pos), pygame.Vector2(vel)
        self.color, self.life, self.size = color, life, size

    def update(self, dt):
        super().update(dt)
        self.pos += self.vel * dt

    def draw(self, s):
        r = max(1, int(self.size * (1 - self.p)))
        pygame.draw.circle(s, self.color, (int(self.pos.x), int(self.pos.y)), r)


class Note(Effect):
    """Floating musical note (Bard)."""
    life = 1.1

    def __init__(self, pos, color=GOLD):
        super().__init__()
        self.pos, self.color = pygame.Vector2(pos), color
        self.phase = random.uniform(0, 6)

    def update(self, dt):
        super().update(dt)
        self.pos.y -= 22 * dt

    def draw(self, s):
        x = self.pos.x + math.sin(self.age * 5 + self.phase) * 4
        if self.p < 0.85:
            draw_note(s, x, self.pos.y, self.color, 0.8)


class WebShot(Effect):
    """A strand of web flying from a spider to a tower."""
    life = 0.45

    def __init__(self, a, b):
        super().__init__()
        self.a, self.b = pygame.Vector2(a), pygame.Vector2(b)

    def draw(self, s):
        end = self.a.lerp(self.b, min(1.0, self.p * 2.2))
        c = int(240 - 80 * self.p)
        pygame.draw.line(s, (c, c, c + 10), self.a, end, 2)
        pygame.draw.circle(s, (245, 245, 250), (int(end.x), int(end.y)), 3)


class FloatText(Effect):
    life = 1.0

    def __init__(self, pos, text, color=GOLD, size=16):
        super().__init__()
        self.pos = pygame.Vector2(pos)
        self.img = font(size, True).render(text, True, color)
        self.shadow = font(size, True).render(text, True, (20, 12, 6))

    def update(self, dt):
        super().update(dt)
        self.pos.y -= 34 * dt

    def draw(self, s):
        alpha = int(255 * (1 - self.p ** 2))
        for img, off in ((self.shadow, 1), (self.img, 0)):
            img.set_alpha(alpha)
            s.blit(img, img.get_rect(center=(self.pos.x + off, self.pos.y + off)))


class ThornPatch:
    """Thorny brambles on the road (Druid). Damages every enemy standing in it."""

    def __init__(self, pos, owner):
        self.pos = pygame.Vector2(pos)
        self.owner = owner
        self.radius = owner.patch_radius
        self.life = owner.patch_time
        self.damage = owner.damage
        self.slow = owner.thorn_slow
        self.age = 0.0
        self.timer = 0.0
        rng = random.Random(random.random())
        self.spikes = []
        for _ in range(18):
            a, d = rng.uniform(0, math.tau), math.sqrt(rng.random()) * self.radius * 0.9
            bx, by = math.cos(a) * d, math.sin(a) * d
            self.spikes.append((bx, by, bx + rng.uniform(-5, 5), by - rng.uniform(7, 14)))
        self.vines = []
        for _ in range(5):
            pts = [(rng.uniform(-1, 1) * self.radius * 0.8, rng.uniform(-1, 1) * self.radius * 0.7) for _ in range(3)]
            self.vines.append(pts)

    @property
    def dead(self):
        return self.age >= self.life

    def update(self, dt, game):
        self.age += dt
        self.timer += dt
        while self.timer >= THORN_TICK:
            self.timer -= THORN_TICK
            for e in game.enemies:
                if e.alive and e.pos.distance_to(self.pos) <= self.radius + e.radius * 0.5:
                    e.take_damage(self.damage)
                    if self.slow:
                        e.apply_slow(self.slow, 0.5)

    def draw(self, s):
        g = min(1.0, self.age / 0.35)
        fade = min(1.0, (self.life - self.age) / 0.8)
        r = int(self.radius * g) + 2
        size = r * 2 + 40
        tmp = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size // 2
        a = int(255 * fade)
        pygame.draw.circle(tmp, (30, 80, 35, int(95 * fade)), (c, c), r)
        for pts in self.vines:
            poly = [(c + x * g, c + y * g) for x, y in pts]
            pygame.draw.lines(tmp, (40, 100, 40, a), False, poly, 3)
        for bx, by, tx, ty in self.spikes:
            pygame.draw.line(tmp, (60, 130, 55, a), (c + bx * g, c + by * g), (c + tx * g, c + ty * g), 2)
            pygame.draw.circle(tmp, (190, 230, 120, a), (int(c + tx * g), int(c + ty * g)), 1)
        s.blit(tmp, (self.pos.x - c, self.pos.y - c))


# =============================================================================
# ENEMIES
# =============================================================================
ENEMY_TYPES = {}


def register_enemy(cls):
    ENEMY_TYPES[cls.id] = cls
    return cls


class Enemy:
    id = "enemy"
    name = "Enemy"
    base_hp = 50
    speed = 70               # pixels per second
    radius = 11              # used for range / splash checks
    sprite_height = 24       # where the health bar floats
    spawn_gap = 1.0          # multiplies the delay after this enemy spawns
    lives_cost = 1           # lives lost if it reaches the castle
    color = (200, 200, 200)  # used in UI

    def __init__(self, path, hp_mult=1.0, speed_mult=1.0, dist=0.0):
        self.path = path
        self.hp_mult, self.speed_mult = hp_mult, speed_mult
        self.max_hp = self.base_hp * hp_mult
        self.hp = self.max_hp
        self.move_speed = self.speed * speed_mult
        self.dist = max(0.0, min(dist, path.length - 1))
        self.pos = path.position_at(self.dist)
        self.reward = max(1, round(self.base_hp * REWARD_PER_HP * math.sqrt(hp_mult)))
        self.since_damage = 99.0
        self.facing = 1
        self.anim = random.random() * 10
        self.escaped = False
        self.stopped = False
        # status effects
        self.slow_amt = self.slow_t = 0.0
        self.vuln = self.vuln_t = 0.0
        self.burn_dps = self.burn_t = 0.0

    @property
    def alive(self):
        return self.hp > 0 and not self.escaped

    # ---- damage & status ----------------------------------------------------
    def take_damage(self, amount):
        if self.hp <= 0:
            return
        if self.vuln_t > 0:
            amount *= 1.0 + self.vuln
        self.hp -= amount
        self.since_damage = 0.0

    def apply_slow(self, amount, secs):
        amount = min(amount, 0.8)
        if self.slow_t <= 0 or amount >= self.slow_amt:
            self.slow_amt = amount
        self.slow_t = max(self.slow_t, secs)

    def apply_vuln(self, amount, secs):
        if self.vuln_t <= 0 or amount >= self.vuln:
            self.vuln = amount
        self.vuln_t = max(self.vuln_t, secs)

    def apply_burn(self, dps, secs):
        if self.burn_t <= 0 or dps >= self.burn_dps:
            self.burn_dps = dps
        self.burn_t = max(self.burn_t, secs)

    # ---- hooks --------------------------------------------------------------
    def special(self, dt, game):
        """Per-frame behaviour (regeneration, summoning, shooting webs...)."""

    def on_death(self, game):
        """Return a list of new enemies to spawn where this one died."""
        return []

    # ---- update / draw ------------------------------------------------------
    def update(self, dt, game):
        self.anim += dt
        self.since_damage += dt
        if self.slow_t > 0:
            self.slow_t -= dt
            if self.slow_t <= 0:
                self.slow_amt = 0.0
        if self.vuln_t > 0:
            self.vuln_t -= dt
            if self.vuln_t <= 0:
                self.vuln = 0.0
        if self.burn_t > 0:
            self.burn_t -= dt
            self.take_damage(self.burn_dps * dt)
            if self.burn_t <= 0:
                self.burn_dps = 0.0
        self.special(dt, game)
        if self.hp <= 0:
            return
        if not self.stopped:
            self.dist += self.move_speed * max(0.2, 1.0 - self.slow_amt) * dt
        if self.dist >= self.path.length:
            self.escaped = True
            return
        old_x = self.pos.x
        self.pos = self.path.position_at(self.dist)
        dx = self.pos.x - old_x
        if abs(dx) > 0.01:
            self.facing = 1 if dx > 0 else -1

    def draw(self, s):
        x, y = self.pos
        self.draw_sprite(s, x, y)
        r = self.radius
        if self.slow_t > 0:
            pygame.draw.ellipse(s, (130, 205, 255), (x - r - 3, y + r * 0.3, 2 * r + 6, r * 0.9 + 4), 2)
        if self.vuln_t > 0:
            pygame.draw.circle(s, (205, 90, 230), (int(x), int(y - self.sprite_height * 0.3)), int(r + 4), 2)
        if self.burn_t > 0:
            for _ in range(2):
                pygame.draw.circle(s, (255, random.randint(100, 200), 30),
                                   (int(x + random.uniform(-r, r)), int(y - random.uniform(0, self.sprite_height * 0.7))), 2)
        if self.hp < self.max_hp:
            w = int(r * 2 + 10)
            bx, by = int(x - w / 2), int(y - self.sprite_height)
            pygame.draw.rect(s, (30, 10, 10), (bx - 1, by - 1, w + 2, 7))
            pygame.draw.rect(s, (190, 40, 40), (bx, by, w, 5))
            pygame.draw.rect(s, (80, 210, 80), (bx, by, int(w * max(0, self.hp) / self.max_hp), 5))

    def draw_sprite(self, s, x, y):
        pygame.draw.circle(s, self.color, (int(x), int(y)), self.radius)


@register_enemy
class Skeleton(Enemy):
    id, name = "skeleton", "Skeleton"
    base_hp, speed, radius, sprite_height = 60, 70, 11, 26
    color = (235, 230, 212)

    def draw_sprite(self, s, x, y):
        f, t = self.facing, self.anim * 9
        y += math.sin(t) * 1.2
        bone, shade = (235, 230, 212), (70, 64, 56)
        sw = math.sin(t) * 3
        pygame.draw.line(s, bone, (x - 3, y + 5), (x - 3 + sw, y + 12), 2)
        pygame.draw.line(s, bone, (x + 3, y + 5), (x + 3 - sw, y + 12), 2)
        pygame.draw.ellipse(s, bone, (x - 6, y - 4, 12, 12))
        for i in range(3):
            pygame.draw.line(s, shade, (x - 4, y - 1 + i * 3), (x + 4, y - 1 + i * 3), 1)
        pygame.draw.line(s, (170, 175, 185), (x + f * 6, y + 3), (x + f * 14, y - 8), 2)
        pygame.draw.line(s, (120, 80, 40), (x + f * 4, y + 3), (x + f * 8, y + 5), 2)
        pygame.draw.circle(s, bone, (x, y - 9), 6)
        pygame.draw.circle(s, shade, (x - 2 + f, y - 10), 2)
        pygame.draw.circle(s, shade, (x + 2 + f, y - 10), 2)


@register_enemy
class Goblin(Enemy):
    id, name = "goblin", "Goblin"
    base_hp, speed, radius, sprite_height, spawn_gap = 28, 125, 9, 22, 0.6
    color = (96, 170, 70)

    def draw_sprite(self, s, x, y):
        f, t = self.facing, self.anim * 14
        y += math.sin(t) * 1.5
        green, dark = (96, 170, 70), (40, 80, 35)
        sw = math.sin(t) * 3
        pygame.draw.line(s, dark, (x - 2, y + 4), (x - 2 + sw, y + 10), 2)
        pygame.draw.line(s, dark, (x + 2, y + 4), (x + 2 - sw, y + 10), 2)
        pygame.draw.ellipse(s, (125, 82, 45), (x - 5, y - 3, 10, 10))
        pygame.draw.polygon(s, green, [(x - 4, y - 9), (x - 13, y - 14), (x - 4, y - 5)])
        pygame.draw.polygon(s, green, [(x + 4, y - 9), (x + 13, y - 14), (x + 4, y - 5)])
        pygame.draw.circle(s, green, (x, y - 8), 6)
        pygame.draw.circle(s, (230, 40, 40), (x - 2 + f, y - 9), 1)
        pygame.draw.circle(s, (230, 40, 40), (x + 2 + f, y - 9), 1)
        pygame.draw.line(s, (200, 200, 210), (x + f * 5, y + 1), (x + f * 11, y - 5), 2)


@register_enemy
class Zombie(Enemy):
    id, name = "zombie", "Zombie"
    base_hp, speed, radius, sprite_height = 70, 65, 12, 28
    color = (120, 150, 110)
    HEAL_DELAY = 4.0       # seconds without damage before regeneration starts
    HEAL_RATE = 0.07       # fraction of max HP regenerated per second

    @property
    def healing(self):
        return self.since_damage >= self.HEAL_DELAY and self.hp < self.max_hp

    def special(self, dt, game):
        if self.healing:
            self.hp = min(self.max_hp, self.hp + self.max_hp * self.HEAL_RATE * dt)

    def draw_sprite(self, s, x, y):
        f, t = self.facing, self.anim * 6
        y += math.sin(t) * 1.2
        skin, cloth = (120, 150, 110), (82, 70, 104)
        sw = math.sin(t) * 2
        if self.healing:
            r = 16 + int(math.sin(self.anim * 6) * 2)
            pygame.draw.circle(s, (90, 230, 120), (int(x), int(y - 2)), r, 2)
        pygame.draw.line(s, cloth, (x - 3, y + 6), (x - 3 + sw, y + 13), 3)
        pygame.draw.line(s, cloth, (x + 3, y + 6), (x + 3 - sw, y + 13), 3)
        pygame.draw.rect(s, cloth, (x - 6, y - 5, 12, 13))
        pygame.draw.polygon(s, cloth, [(x - 6, y + 8), (x - 3, y + 12), (x, y + 8)])
        pygame.draw.line(s, skin, (x, y - 2), (x + f * 13, y - 5 + sw), 3)
        pygame.draw.line(s, skin, (x, y + 1), (x + f * 12, y - 1 - sw), 3)
        pygame.draw.circle(s, skin, (x + f, y - 10), 6)
        pygame.draw.circle(s, (235, 235, 200), (x + f * 3, y - 11), 2)
        pygame.draw.circle(s, (30, 30, 30), (x + f * 3, y - 11), 1)
        pygame.draw.line(s, (50, 70, 45), (x + f * 1, y - 7), (x + f * 5, y - 7), 1)


@register_enemy
class Spider(Enemy):
    """Fast-ish. Spits webs at nearby towers, slowing their attack speed."""
    id, name = "spider", "Spider"
    base_hp, speed, radius, sprite_height, spawn_gap = 80, 85, 10, 22, 0.9
    color = (110, 60, 130)
    WEB_RANGE = 135
    WEB_COOLDOWN = 3.5
    WEB_TIME = 4.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.web_cd = random.uniform(0.5, 2.0)

    def special(self, dt, game):
        self.web_cd -= dt
        if self.web_cd > 0 or not game.towers:
            return
        tower = min(game.towers, key=lambda t: t.pos.distance_to(self.pos))
        if tower.pos.distance_to(self.pos) <= self.WEB_RANGE:
            tower.web_time = max(tower.web_time, self.WEB_TIME)
            game.effects.append(WebShot(self.pos + (0, -4), tower.pos))
            self.web_cd = self.WEB_COOLDOWN

    def draw_sprite(self, s, x, y):
        f, t = self.facing, self.anim * 12
        dark = (46, 34, 54)
        for side in (-1, 1):
            for i in range(4):
                spread = (i - 1.5) * 5
                wig = math.sin(t + i * 1.3 + (0.8 if side > 0 else 0)) * 2.5
                knee = (x + side * 10, y - 6 + spread * 0.6 + wig)
                foot = (x + side * 16, y + spread + 3 + wig * 0.5)
                pygame.draw.lines(s, dark, False, [(x, y), knee, foot], 2)
        pygame.draw.ellipse(s, (96, 56, 116), (x - 8 - f * 3, y - 6, 16, 14))
        pygame.draw.polygon(s, (210, 40, 50), [(x - 3 - f * 3, y - 2), (x + 3 - f * 3, y - 2), (x - f * 3, y + 2)])
        pygame.draw.polygon(s, (210, 40, 50), [(x - 3 - f * 3, y + 6), (x + 3 - f * 3, y + 6), (x - f * 3, y + 2)])
        pygame.draw.circle(s, (70, 40, 86), (int(x + f * 7), int(y)), 5)
        pygame.draw.circle(s, (255, 70, 70), (int(x + f * 9), int(y - 2)), 1)
        pygame.draw.circle(s, (255, 70, 70), (int(x + f * 9), int(y + 1)), 1)


@register_enemy
class Slime(Enemy):
    """Splits in two when it dies: big -> 2 medium -> 4 small (fast)."""
    id, name = "slime", "Slime"
    spawn_gap = 1.4
    color = (90, 200, 100)
    SIZES = {3: (450, 45, 18), 2: (130, 65, 13), 1: (35, 125, 8)}   # size: (hp, speed, radius)
    SHADES = {3: (80, 190, 90), 2: (105, 208, 105), 1: (140, 226, 125)}

    def __init__(self, path, hp_mult=1.0, speed_mult=1.0, dist=0.0, size=3):
        self.size = size
        self.base_hp, self.speed, self.radius = self.SIZES[size]
        self.sprite_height = self.radius * 2 + 10
        super().__init__(path, hp_mult, speed_mult, dist)

    def on_death(self, game):
        if self.size <= 1:
            return []
        kids = []
        for off in (9, -9):
            kids.append(Slime(self.path, self.hp_mult, self.speed_mult, self.dist + off, self.size - 1))
        game.effects.append(Ring(self.pos, (120, 230, 120), self.radius + 14, 0.35))
        return kids

    def draw_sprite(self, s, x, y):
        r = self.radius
        sq = math.sin(self.anim * (5 if self.size > 1 else 9)) * 0.12
        w, h = r * (1 + sq), r * (1 - sq) * 0.85
        body, edge = self.SHADES[self.size], (40, 120, 55)
        bottom = y + r * 0.55
        pygame.draw.ellipse(s, (40, 80, 40), (x - w, bottom - h * 0.5, 2 * w, h * 0.9))
        rect = (x - w, bottom - 2 * h, 2 * w, 2 * h)
        pygame.draw.ellipse(s, body, rect)
        pygame.draw.ellipse(s, edge, rect, 2)
        pygame.draw.ellipse(s, (200, 255, 205), (x - w * 0.55, bottom - 1.7 * h, w * 0.5, h * 0.4))
        if self.size >= 2:
            pygame.draw.circle(s, (150, 230, 150), (int(x + w * 0.35), int(bottom - h * 0.9)), max(2, int(r * 0.15)))
            pygame.draw.circle(s, (150, 230, 150), (int(x - w * 0.2), int(bottom - h * 0.5)), max(2, int(r * 0.1)))
        ey = bottom - h * 1.1
        for ex in (-0.3, 0.3):
            pygame.draw.circle(s, (250, 250, 250), (int(x + w * ex + self.facing * 2), int(ey)), max(2, int(r * 0.22)))
            pygame.draw.circle(s, (20, 40, 20), (int(x + w * ex + self.facing * 3), int(ey)), max(1, int(r * 0.11)))


@register_enemy
class Necromancer(Enemy):
    """Walks, then stops to channel a spell that raises skeletons, then walks on."""
    id, name = "necromancer", "Necromancer"
    base_hp, speed, radius, sprite_height, spawn_gap = 110, 58, 12, 34, 2.0
    color = (110, 70, 170)
    WALK_TIME = 4.5
    CAST_TIME = 2.2
    SUMMONS = 2

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.phase = "walk"
        self.timer = self.WALK_TIME * random.uniform(0.6, 1.0)
        self.summoned = False

    def special(self, dt, game):
        self.timer -= dt
        if self.phase == "walk":
            if self.timer <= 0:
                self.phase, self.timer, self.summoned, self.stopped = "cast", self.CAST_TIME, False, True
        else:
            if random.random() < dt * 14:
                game.effects.append(Particle(self.pos + (random.uniform(-14, 14), 12),
                                             (random.uniform(-8, 8), -random.uniform(30, 60)),
                                             (130, 255, 150), 0.6, 3))
            if not self.summoned and self.timer <= self.CAST_TIME * 0.45:
                self.summoned = True
                game.effects.append(Ring(self.pos, (130, 255, 150), 38, 0.5))
                for i in range(self.SUMMONS):
                    sk = Skeleton(self.path, self.hp_mult * 0.8, self.speed_mult, self.dist - 14 - 18 * i)
                    sk.reward = max(1, sk.reward // 2)
                    game.spawn(sk)
            if self.timer <= 0:
                self.phase, self.timer, self.stopped = "walk", self.WALK_TIME, False

    def draw_sprite(self, s, x, y):
        f = self.facing
        cast = self.phase == "cast"
        y += 0 if cast else math.sin(self.anim * 6) * 1.2
        robe, trim = (58, 40, 92), (118, 84, 170)
        if cast:
            r = 14 + int((self.CAST_TIME - self.timer) * 12) % 16
            pygame.draw.circle(s, (110, 240, 140), (int(x), int(y + 12)), r, 2)
        pygame.draw.polygon(s, robe, [(x - 10, y + 13), (x + 10, y + 13), (x + 6, y - 6), (x - 6, y - 6)])
        pygame.draw.line(s, trim, (x - 10, y + 12), (x + 10, y + 12), 2)
        pygame.draw.line(s, (200, 200, 150), (x, y - 4), (x, y + 11), 1)
        # arms
        if cast:
            pygame.draw.line(s, robe, (x - 5, y - 3), (x - 11, y - 15), 3)
            pygame.draw.line(s, robe, (x + 5, y - 3), (x + 11, y - 15), 3)
            pygame.draw.circle(s, (130, 255, 150), (int(x - 11), int(y - 16)), 3)
            pygame.draw.circle(s, (130, 255, 150), (int(x + 11), int(y - 16)), 3)
        else:
            pygame.draw.line(s, robe, (x - 5, y - 3), (x - 9, y + 6), 3)
        pygame.draw.circle(s, (42, 28, 70), (int(x), int(y - 10)), 8)
        pygame.draw.circle(s, (12, 8, 20), (int(x + f), int(y - 10)), 5)
        pygame.draw.circle(s, (130, 255, 150), (int(x + f - 2), int(y - 11)), 1)
        pygame.draw.circle(s, (130, 255, 150), (int(x + f + 2), int(y - 11)), 1)
        # staff with skull
        sx = x + f * 11
        pygame.draw.line(s, (100, 70, 40), (sx, y + 12), (sx, y - 14), 3)
        pygame.draw.circle(s, (232, 228, 210), (int(sx), int(y - 17)), 4)
        pygame.draw.circle(s, (30, 30, 30), (int(sx - 1), int(y - 18)), 1)
        pygame.draw.circle(s, (30, 30, 30), (int(sx + 2), int(y - 18)), 1)
        if cast:
            pygame.draw.circle(s, (130, 255, 150), (int(sx), int(y - 17)), 7 + int(math.sin(self.anim * 14) * 2), 2)


@register_enemy
class Dragon(Enemy):
    id, name = "dragon", "Dragon"
    base_hp, speed, radius, sprite_height, spawn_gap = 600, 38, 20, 44, 2.2
    color = (170, 38, 38)

    def draw_sprite(self, s, x, y):
        f = self.facing
        flap = math.sin(self.anim * 5) * 10
        red, dark, belly = (170, 38, 38), (110, 24, 28), (225, 170, 80)
        tail = [(x - f * 16, y + 2), (x - f * 30, y + 6 + math.sin(self.anim * 4) * 3), (x - f * 42, y - 2)]
        pygame.draw.lines(s, red, False, tail, 6)
        pygame.draw.polygon(s, dark, [(x - f * 2, y - 8), (x - f * 16, y - 28 - flap), (x + f * 8, y - 10)])
        pygame.draw.ellipse(s, red, (x - 20, y - 10, 40, 24))
        pygame.draw.ellipse(s, belly, (x - 14, y + 2, 28, 10))
        pygame.draw.line(s, dark, (x - 8, y + 12), (x - 8, y + 18), 4)
        pygame.draw.line(s, dark, (x + 8, y + 12), (x + 8, y + 18), 4)
        pygame.draw.polygon(s, (200, 60, 50), [(x + f * 2, y - 8), (x - f * 10, y - 32 - flap), (x + f * 14, y - 10)])
        pygame.draw.line(s, red, (x + f * 14, y - 4), (x + f * 22, y - 12), 9)
        pygame.draw.circle(s, red, (x + f * 25, y - 13), 8)
        pygame.draw.polygon(s, red, [(x + f * 28, y - 17), (x + f * 39, y - 11), (x + f * 28, y - 8)])
        pygame.draw.polygon(s, (230, 220, 190), [(x + f * 22, y - 19), (x + f * 17, y - 28), (x + f * 27, y - 20)])
        pygame.draw.circle(s, (255, 230, 60), (x + f * 27, y - 15), 2)
        pygame.draw.circle(s, (20, 20, 20), (x + f * 28, y - 15), 1)


@register_enemy
class Golem(Enemy):
    id, name = "golem", "Golem"
    base_hp, speed, radius, sprite_height, spawn_gap = 1500, 26, 24, 54, 3.5
    color = (130, 124, 118)

    def draw_sprite(self, s, x, y):
        t = self.anim * 3
        bob = abs(math.sin(t)) * 2.5
        y -= bob
        stone, dark, light, moss = (128, 122, 116), (84, 80, 76), (158, 152, 144), (84, 132, 72)
        swing = math.sin(t) * 5
        pygame.draw.ellipse(s, (50, 70, 45), (x - 26, y + 16 + bob, 52, 14))
        pygame.draw.rect(s, dark, (x - 15, y + 4, 12, 18), border_radius=3)
        pygame.draw.rect(s, dark, (x + 3, y + 4, 12, 18), border_radius=3)
        pygame.draw.rect(s, stone, (x - 29, y - 14 + swing, 11, 26), border_radius=4)
        pygame.draw.rect(s, stone, (x + 18, y - 14 - swing, 11, 26), border_radius=4)
        pygame.draw.circle(s, dark, (int(x - 24), int(y + 13 + swing)), 7)
        pygame.draw.circle(s, dark, (int(x + 24), int(y + 13 - swing)), 7)
        pygame.draw.rect(s, stone, (x - 19, y - 20, 38, 31), border_radius=9)
        pygame.draw.rect(s, light, (x - 19, y - 20, 38, 9), border_radius=9)
        pygame.draw.rect(s, dark, (x - 19, y - 20, 38, 31), 2, border_radius=9)
        pygame.draw.line(s, dark, (x - 6, y - 10), (x - 2, y + 2), 2)
        pygame.draw.line(s, dark, (x + 8, y - 6), (x + 5, y + 6), 2)
        pygame.draw.circle(s, moss, (int(x - 12), int(y - 15)), 5)
        pygame.draw.circle(s, moss, (int(x + 14), int(y + 6)), 4)
        pygame.draw.rect(s, stone, (x - 10, y - 34, 20, 16), border_radius=5)
        pygame.draw.rect(s, dark, (x - 10, y - 34, 20, 16), 2, border_radius=5)
        pygame.draw.rect(s, (255, 170, 50), (x - 7, y - 29, 5, 4))
        pygame.draw.rect(s, (255, 170, 50), (x + 2, y - 29, 5, 4))


# =============================================================================
# WAVES
# =============================================================================
def _ramp(v, cap):
    """Linear up to `cap`, then half speed."""
    return int(v if v <= cap else cap + (v - cap) * 0.5)


def wave_composition(n):
    """How many of each enemy appear in wave n. Edit this to rebalance."""
    comp = {"skeleton": _ramp(5 + n, 17)}
    if n >= 2:
        comp["goblin"] = _ramp((n - 1) * 3 // 2, 14)
    if n >= 4:
        comp["zombie"] = _ramp((n - 2) // 2 + 1, 8)
    if n >= 5:
        comp["spider"] = _ramp((n - 3) // 2, 8)
    if n >= 7:
        comp["slime"] = (n - 4) // 3
    if n >= 8:
        comp["dragon"] = (n - 6) // 2
    if n >= 9:
        comp["necromancer"] = (n - 6) // 3
    if n >= 12:
        comp["golem"] = (n - 9) // 3
    return comp


def generate_wave(n):
    """Returns a list of (enemy_id, delay_before_next_spawn)."""
    rng = random.Random(n * 7919)
    ids = []
    for eid, count in wave_composition(n).items():
        ids += [eid] * count
    rng.shuffle(ids)
    interval = max(0.35, 1.0 - 0.025 * n)
    return [(eid, interval * ENEMY_TYPES[eid].spawn_gap * rng.uniform(0.85, 1.15)) for eid in ids]


def wave_hp_mult(n):
    return 1.0 + 0.12 * (n - 1)


def wave_speed_mult(n):
    return min(1.3, 1.0 + 0.01 * (n - 1))


# =============================================================================
# PROJECTILES
# =============================================================================
class Projectile:
    def __init__(self, pos, target, damage, speed):
        self.pos = pygame.Vector2(pos)
        self.target = target
        self.damage = damage
        self.speed = speed
        self.aim = pygame.Vector2(target.pos)
        self.dir = pygame.Vector2(1, 0)
        self.dead = False

    def update(self, dt, game):
        if self.target.alive:
            self.aim = pygame.Vector2(self.target.pos)
        to = self.aim - self.pos
        d = to.length()
        step = self.speed * dt
        if d <= step + 4:
            self.on_hit(game)
            self.dead = True
            return
        self.dir = to / d
        self.pos += self.dir * step

    def on_hit(self, game):
        pass

    def draw(self, s):
        pass


class Arrow(Projectile):
    def __init__(self, pos, target, damage):
        super().__init__(pos, target, damage, 560)

    def on_hit(self, game):
        if self.target.alive:
            self.target.take_damage(self.damage)

    def draw(self, s):
        tail = self.pos - self.dir * 13
        pygame.draw.line(s, (120, 84, 44), tail, self.pos, 2)
        side = pygame.Vector2(-self.dir.y, self.dir.x)
        tip = self.pos + self.dir * 4
        pygame.draw.polygon(s, (190, 190, 200), [tip, self.pos - self.dir * 2 + side * 3, self.pos - self.dir * 2 - side * 3])
        pygame.draw.line(s, (220, 220, 220), tail, tail - self.dir * 3 + side * 3, 1)
        pygame.draw.line(s, (220, 220, 220), tail, tail - self.dir * 3 - side * 3, 1)


class Fireball(Projectile):
    def __init__(self, pos, target, damage, splash, burn=(0.0, 0.0)):
        super().__init__(pos, target, damage, 270)
        self.splash = splash
        self.burn = burn

    def update(self, dt, game):
        super().update(dt, game)
        if random.random() < 0.8:
            game.effects.append(Particle(self.pos + (random.uniform(-3, 3), random.uniform(-3, 3)),
                                         (random.uniform(-15, 15), random.uniform(-15, 15)),
                                         random.choice([(255, 150, 40), (255, 90, 30), (255, 210, 80)]),
                                         0.35, 5))

    def on_hit(self, game):
        for e in game.enemies:
            if e.alive and e.pos.distance_to(self.aim) <= self.splash + e.radius:
                e.take_damage(self.damage)
                if self.burn[0] > 0:
                    e.apply_burn(*self.burn)
        game.effects.append(Explosion(self.aim, self.splash))
        for _ in range(8):
            ang = random.uniform(0, math.tau)
            game.effects.append(Particle(self.aim, (math.cos(ang) * 90, math.sin(ang) * 90),
                                         random.choice([(255, 170, 50), (255, 100, 30)]), 0.4, 4))

    def draw(self, s):
        p = (int(self.pos.x), int(self.pos.y))
        pygame.draw.circle(s, (230, 80, 20), p, 8)
        pygame.draw.circle(s, (255, 170, 40), p, 6)
        pygame.draw.circle(s, (255, 250, 190), p, 3)


class SpellOrb(Projectile):
    """Cleric spell: 'weaken' (enemy takes more damage) or 'slow'."""
    COLORS = {"weaken": (200, 100, 235), "slow": (120, 205, 255)}

    def __init__(self, pos, target, kind, amount, secs, radius):
        super().__init__(pos, target, 0, 380)
        self.kind, self.amount, self.secs, self.radius = kind, amount, secs, radius

    def update(self, dt, game):
        super().update(dt, game)
        if random.random() < 0.6:
            game.effects.append(Particle(self.pos, (random.uniform(-12, 12), random.uniform(-12, 12)),
                                         self.COLORS[self.kind], 0.3, 3))

    def on_hit(self, game):
        victims = [e for e in game.enemies if e.alive and e.pos.distance_to(self.aim) <= self.radius + e.radius]
        if self.target.alive and self.target not in victims:
            victims.append(self.target)
        for e in victims:
            if self.kind == "weaken":
                e.apply_vuln(self.amount, self.secs)
            else:
                e.apply_slow(self.amount, self.secs)
        game.effects.append(Ring(self.aim, self.COLORS[self.kind], max(22, self.radius + 14), 0.4))

    def draw(self, s):
        p = (int(self.pos.x), int(self.pos.y))
        col = self.COLORS[self.kind]
        pygame.draw.circle(s, col, p, 7)
        pygame.draw.circle(s, (255, 255, 255), p, 3)


# =============================================================================
# UPGRADES
# =============================================================================
@dataclass
class Upgrade:
    name: str
    cost: int
    desc: str
    mods: dict      # stat -> ("*" | "+" | "=", value)


@dataclass
class UpgradePath:
    name: str
    tiers: list


def U(name, cost, desc, **mods):
    return Upgrade(name, cost, desc, mods)


# =============================================================================
# TOWERS
# =============================================================================
TOWER_TYPES = {}


def register_tower(cls):
    TOWER_TYPES[cls.id] = cls
    return cls


class Tower:
    id = "tower"
    name = "Tower"
    description = ""
    cost = 100
    range = 120
    cooldown = 1.0       # seconds between attacks
    damage = 10
    attacks = True       # False for pure support towers
    paths = ()           # two UpgradePath objects

    def __init__(self, pos):
        self.pos = pygame.Vector2(pos)
        self.cd = 0.0
        self.flash = 0.0
        self.angle = -math.pi / 2
        self.spent = self.cost
        self.tiers = [0] * len(self.paths)
        self.speed_buff = 1.0     # set by Bards each frame
        self.dmg_buff = 1.0
        self.web_time = 0.0       # set by Spiders

    # ---- stats ---------------------------------------------------------------
    @property
    def sell_value(self):
        return int(self.spent * SELL_REFUND)

    @property
    def eff_damage(self):
        return self.damage * self.dmg_buff

    def info_lines(self):
        return [f"Damage: {self.damage:.0f}", f"Range: {int(self.range)}",
                f"Speed: {1 / self.cooldown:.1f} attacks/s"]

    # ---- upgrades ------------------------------------------------------------
    def next_upgrade(self, i):
        if i < len(self.paths) and self.tiers[i] < len(self.paths[i].tiers):
            return self.paths[i].tiers[self.tiers[i]]
        return None

    def can_upgrade(self, i):
        if i >= len(self.paths):
            return False, "No such path"
        if self.next_upgrade(i) is None:
            return False, "Fully upgraded"
        other = self.tiers[1 - i] if len(self.tiers) == 2 else 0
        if self.tiers[i] + 1 >= LOCK_TIER and other > 1:
            return False, "Locked by the other path"
        return True, ""

    def upgrade(self, i):
        up = self.next_upgrade(i)
        for stat, (op, v) in up.mods.items():
            cur = getattr(self, stat)
            setattr(self, stat, cur * v if op == "*" else cur + v if op == "+" else v)
        self.spent += up.cost
        self.tiers[i] += 1

    # ---- behaviour -----------------------------------------------------------
    def find_targets(self, enemies, n=1):
        """Up to n enemies in range, furthest along the path first."""
        found = [e for e in enemies
                 if e.hp > 0 and self.pos.distance_to(e.pos) <= self.range + e.radius]
        found.sort(key=lambda e: -e.dist)
        return found[:n]

    def apply_aura(self, towers):
        """Called every frame before updates; support towers buff `towers` here."""

    def update_extra(self, dt, game):
        """Hook for per-frame behaviour of non-attacking towers."""

    def update(self, dt, game):
        self.flash = max(0.0, self.flash - dt)
        self.web_time = max(0.0, self.web_time - dt)
        rate = self.speed_buff * (WEB_SLOW if self.web_time > 0 else 1.0)
        self.cd = max(0.0, self.cd - dt * rate)
        self.update_extra(dt, game)
        if not self.attacks:
            return
        found = self.find_targets(game.enemies, 1)
        if not found:
            return
        target = found[0]
        d = target.pos - self.pos
        self.angle = math.atan2(d.y, d.x)
        if self.cd <= 0:
            if self.attack(target, game) is not False:
                self.cd = self.cooldown
                self.flash = FLASH_TIME

    def attack(self, target, game):
        """Fire at `target`. Return False if nothing happened (no cooldown spent)."""
        raise NotImplementedError

    # ---- drawing -------------------------------------------------------------
    def draw(self, s):
        x, y = self.pos
        self.draw_sprite(s, x, y, self.angle, self.flash)
        if self.speed_buff > 1.0:
            draw_note(s, x + 15, y - 24, GOLD, 0.7)
        if self.web_time > 0:
            self.draw_web(s)
        for i, tier in enumerate(self.tiers):
            for j in range(tier):
                px = x + (-4 - 7 * j if i == 0 else 4 + 7 * j)
                pygame.draw.circle(s, (30, 20, 10), (int(px), int(y + 25)), 4)
                pygame.draw.circle(s, ACCENTS[i], (int(px), int(y + 25)), 3)

    def draw_web(self, s):
        x, y = self.pos.x, self.pos.y - 4
        col = (238, 238, 244)
        for k in range(8):
            a = k * math.pi / 4
            pygame.draw.line(s, col, (x, y), (x + math.cos(a) * 21, y + math.sin(a) * 21), 1)
        for rr in (8, 14, 21):
            pts = [(x + math.cos(k * math.pi / 4) * rr, y + math.sin(k * math.pi / 4) * rr) for k in range(8)]
            pygame.draw.polygon(s, col, pts, 1)

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.circle(s, (180, 180, 180), (int(x), int(y)), 14)


@register_tower
class Archer(Tower):
    id, name = "archer", "Archer"
    cost, range, cooldown, damage = 100, 150, 0.8, 14
    shots = 1
    description = "Cheap and reliable. Looses arrows at the enemy furthest along the path."
    paths = (
        UpgradePath("Long Range", [
            U("Sharp Eyes", 70, "+15% range", range=("*", 1.15)),
            U("Longbow", 130, "+20% range, +20% damage", range=("*", 1.2), damage=("*", 1.2)),
            U("Eagle Eye", 240, "+25% range, +40% damage", range=("*", 1.25), damage=("*", 1.4)),
        ]),
        UpgradePath("Rapid Fire", [
            U("Light Fletching", 60, "+20% attack speed", cooldown=("*", 0.83)),
            U("Quick Draw", 120, "+25% attack speed", cooldown=("*", 0.8)),
            U("Volley", 230, "2 arrows per shot, +10% speed", shots=("=", 2), cooldown=("*", 0.9)),
        ]),
    )

    def info_lines(self):
        multi = f" x{self.shots}" if self.shots > 1 else ""
        return [f"Damage: {self.damage:.0f}{multi}", f"Range: {int(self.range)}",
                f"Speed: {1 / self.cooldown:.1f} attacks/s"]

    def attack(self, target, game):
        targets = self.find_targets(game.enemies, self.shots)
        while len(targets) < self.shots:
            targets.append(target)
        for i, tgt in enumerate(targets):
            start = self.pos + pygame.Vector2(math.cos(self.angle), math.sin(self.angle)) * 14 + (0, -8) + (0, i * 3)
            game.projectiles.append(Arrow(start, tgt, self.eff_damage))

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.ellipse(s, (50, 80, 40), (x - 19, y + 8, 38, 14))
        pygame.draw.rect(s, (92, 64, 38), (x - 15, y - 10, 30, 26), border_radius=3)
        pygame.draw.rect(s, (122, 88, 52), (x - 15, y - 10, 30, 7), border_radius=3)
        for i in range(1, 4):
            pygame.draw.line(s, (66, 44, 26), (x - 15 + i * 7.5, y - 3), (x - 15 + i * 7.5, y + 15), 1)
        for dx in (-15, -5, 5):
            pygame.draw.rect(s, (70, 48, 28), (x + dx, y - 16, 10, 7))
        pygame.draw.circle(s, (56, 130, 62), (int(x), int(y - 8)), 7)
        pygame.draw.circle(s, (238, 204, 164), (int(x), int(y - 7)), 3)
        d = pygame.Vector2(math.cos(angle), math.sin(angle))
        perp = pygame.Vector2(-d.y, d.x)
        c = pygame.Vector2(x, y - 8) + d * 12
        top, bot, mid = c + perp * 9, c - perp * 9, c + d * 4
        pygame.draw.lines(s, (150, 100, 50), False, [top, mid, bot], 3)
        pull = c - d * (6 if flash > 0 else 1)
        pygame.draw.lines(s, (235, 235, 220), False, [top, pull, bot], 1)


@register_tower
class Mage(Tower):
    id, name = "mage", "Mage"
    cost, range, cooldown, damage = 200, 140, 2.2, 30
    splash = 60
    burn_dps = 0.0
    burn_time = 3.0
    description = "Slowly hurls fireballs that explode, burning every enemy in a circle."
    paths = (
        UpgradePath("Firepower", [
            U("Hotter Flames", 120, "+25% damage", damage=("*", 1.25)),
            U("Wide Blast", 200, "+30% blast radius, +10% damage", splash=("*", 1.3), damage=("*", 1.1)),
            U("Inferno", 380, "+30% blast, ignites enemies", splash=("*", 1.3), burn_dps=("=", 9.0)),
        ]),
        UpgradePath("Arcane Focus", [
            U("Focus Crystal", 110, "+15% range, +15% speed", range=("*", 1.15), cooldown=("*", 0.87)),
            U("Quickcasting", 190, "+25% attack speed", cooldown=("*", 0.8)),
            U("Arcane Overload", 360, "+30% speed, +25% damage", cooldown=("*", 0.77), damage=("*", 1.25)),
        ]),
    )

    def info_lines(self):
        burn = "  Ignites" if self.burn_dps > 0 else ""
        return [f"Damage: {self.damage:.0f}   Blast: {self.splash:.0f}", f"Range: {int(self.range)}",
                f"Speed: {1 / self.cooldown:.1f} attacks/s{burn}"]

    def attack(self, target, game):
        game.projectiles.append(Fireball(self.pos + (0, -34), target, self.eff_damage, self.splash,
                                         (self.burn_dps, self.burn_time)))

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.ellipse(s, (50, 80, 40), (x - 19, y + 8, 38, 14))
        pygame.draw.rect(s, (128, 130, 150), (x - 13, y - 10, 26, 26), border_radius=3)
        for r in range(3):
            pygame.draw.line(s, (92, 94, 114), (x - 13, y - 3 + r * 8), (x + 13, y - 3 + r * 8), 1)
        pygame.draw.rect(s, (40, 30, 70), (x - 3, y + 2, 6, 10), border_radius=3)
        pygame.draw.polygon(s, (110, 60, 170), [(x - 18, y - 9), (x + 18, y - 9), (x, y - 33)])
        pygame.draw.polygon(s, (140, 90, 200), [(x - 18, y - 9), (x - 4, y - 9), (x, y - 33)])
        pygame.draw.circle(s, (240, 210, 90), (int(x + 4), int(y - 20)), 2)
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() / 250)
        r = 5 + int(pulse * 1.5) + (3 if flash > 0 else 0)
        pygame.draw.circle(s, (255, 120, 40), (int(x), int(y - 38)), r + 2)
        pygame.draw.circle(s, (255, 210, 90), (int(x), int(y - 38)), r)
        pygame.draw.circle(s, (255, 250, 200), (int(x), int(y - 38)), max(2, r - 3))


@register_tower
class Knight(Tower):
    id, name = "knight", "Knight"
    cost, range, cooldown, damage = 120, 85, 0.3, 7
    cleave = 1
    description = "Strikes a single enemy with blinding speed, but only at close range."
    paths = (
        UpgradePath("Sharpened Steel", [
            U("Whetstone", 80, "+40% damage", damage=("*", 1.4)),
            U("Cleaving Blows", 140, "Hits 2 enemies, +15% damage", cleave=("=", 2), damage=("*", 1.15)),
            U("Executioner", 260, "Hits 3 enemies, +50% damage", cleave=("=", 3), damage=("*", 1.5)),
        ]),
        UpgradePath("Swift Guard", [
            U("Long Reach", 70, "+20% range", range=("*", 1.2)),
            U("Battle Rhythm", 130, "+25% attack speed", cooldown=("*", 0.8)),
            U("Whirlwind", 250, "+30% speed, +25% range", cooldown=("*", 0.77), range=("*", 1.25)),
        ]),
    )

    def info_lines(self):
        multi = f"  ({self.cleave} targets)" if self.cleave > 1 else ""
        return [f"Damage: {self.damage:.1f}{multi}", f"Range: {int(self.range)}",
                f"Speed: {1 / self.cooldown:.1f} attacks/s"]

    def attack(self, target, game):
        for tgt in self.find_targets(game.enemies, self.cleave):
            tgt.take_damage(self.eff_damage)
            d = tgt.pos - self.pos
            game.effects.append(Slash(tgt.pos, math.atan2(d.y, d.x)))

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.ellipse(s, (50, 80, 40), (x - 19, y + 8, 38, 14))
        pygame.draw.circle(s, (96, 96, 104), (int(x), int(y + 4)), 17)
        pygame.draw.circle(s, (140, 140, 150), (int(x), int(y + 4)), 14)
        pygame.draw.circle(s, (176, 180, 192), (int(x), int(y - 2)), 10)
        pygame.draw.polygon(s, (190, 40, 40), [(x - 2, y - 11), (x + 2, y - 11), (x + 7, y - 20), (x - 6, y - 17)])
        pygame.draw.circle(s, (200, 204, 214), (int(x), int(y - 6)), 7)
        pygame.draw.line(s, (40, 40, 50), (x - 5, y - 6), (x + 5, y - 6), 2)
        pygame.draw.polygon(s, (50, 80, 160), [(x - 17, y - 2), (x - 7, y - 2), (x - 7, y + 8), (x - 12, y + 14), (x - 17, y + 8)])
        pygame.draw.line(s, GOLD, (x - 12, y - 1), (x - 12, y + 11), 2)
        pygame.draw.line(s, GOLD, (x - 16, y + 4), (x - 8, y + 4), 2)
        swing = (flash / FLASH_TIME) * 1.3 - 0.65 if flash > 0 else -0.2
        a = angle + swing
        d = pygame.Vector2(math.cos(a), math.sin(a))
        base = pygame.Vector2(x, y + 2) + d * 8
        pygame.draw.line(s, (214, 218, 230), base, base + d * 17, 3)
        perp = pygame.Vector2(-d.y, d.x)
        pygame.draw.line(s, GOLD, base - perp * 4, base + perp * 4, 3)


@register_tower
class Druid(Tower):
    id, name = "druid", "Druid"
    cost, range, cooldown, damage = 160, 85, 1.8, 3      # damage = per thorn tick
    patch_radius = 32
    patch_time = 6.0
    max_patches = 4
    thorn_slow = 0.0
    description = "Grows thorny brambles on the road. Small damage, but very rapid ticks to everything inside."
    paths = (
        UpgradePath("Thorn Mastery", [
            U("Sharper Thorns", 90, "+50% thorn damage", damage=("*", 1.5)),
            U("Wide Briars", 150, "+30% patch size, lasts 2s longer", patch_radius=("*", 1.3), patch_time=("+", 2.0)),
            U("Briar Thicket", 280, "+60% damage, +2 patches", damage=("*", 1.6), max_patches=("+", 2)),
        ]),
        UpgradePath("Verdant Growth", [
            U("Swift Growth", 80, "Plants 25% faster", cooldown=("*", 0.8)),
            U("Far Roots", 140, "+25% range", range=("*", 1.25)),
            U("Entangling Roots", 260, "Thorns slow enemies by 35%", thorn_slow=("=", 0.35), cooldown=("*", 0.85)),
        ]),
    )

    def info_lines(self):
        slow = "  Slows" if self.thorn_slow > 0 else ""
        return [f"Thorns: {self.damage:.1f} dmg / {THORN_TICK}s{slow}",
                f"Range: {int(self.range)}   Patches: {self.max_patches}",
                f"Plants one every {self.cooldown:.1f}s"]

    def attack(self, target, game):
        mine = [p for p in game.patches if p.owner is self and not p.dead]
        if len(mine) >= self.max_patches:
            return False
        spot = None
        for off in (45, 30, 15, 0):
            p = game.path.position_at(target.dist + off)
            if self.pos.distance_to(p) <= self.range:
                spot = p
                break
        if spot is None:
            spot = pygame.Vector2(target.pos)
        if any(spot.distance_to(m.pos) < self.patch_radius * 0.7 for m in mine):
            return False
        game.patches.append(ThornPatch(spot, self))
        game.effects.append(Ring(spot, (90, 200, 90), self.patch_radius, 0.35))
        for _ in range(6):
            ang = random.uniform(0, math.tau)
            game.effects.append(Particle(spot, (math.cos(ang) * 50, math.sin(ang) * 50 - 20),
                                         random.choice([(70, 170, 70), (150, 220, 100)]), 0.5, 3))

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.ellipse(s, (50, 80, 40), (x - 19, y + 8, 38, 14))
        pygame.draw.ellipse(s, (112, 114, 110), (x - 17, y + 4, 34, 16))
        pygame.draw.ellipse(s, (88, 150, 80), (x - 15, y + 5, 30, 11))
        for k in range(5):
            a = k * 1.26 + 0.3
            px, py = x + math.cos(a) * 14, y + 11 + math.sin(a) * 4
            pygame.draw.line(s, (40, 110, 45), (px, py), (px + math.cos(a) * 5, py - 7), 2)
        pygame.draw.polygon(s, (76, 58, 36), [(x - 9, y + 12), (x + 9, y + 12), (x + 5, y - 5), (x - 5, y - 5)])
        pygame.draw.polygon(s, (60, 130, 60), [(x - 9, y + 12), (x + 9, y + 12), (x + 7, y + 7), (x - 7, y + 7)])
        pygame.draw.circle(s, (70, 120, 60), (int(x), int(y - 9)), 7)
        pygame.draw.circle(s, (238, 204, 164), (int(x), int(y - 8)), 4)
        for sx in (-1, 1):
            pygame.draw.line(s, (150, 110, 60), (x + sx * 3, y - 14), (x + sx * 8, y - 22), 2)
            pygame.draw.line(s, (150, 110, 60), (x + sx * 6, y - 19), (x + sx * 10, y - 18), 2)
        pygame.draw.line(s, (110, 76, 40), (x + 12, y + 14), (x + 12, y - 14), 3)
        glow = 4 + (2 if flash > 0 else 0)
        pygame.draw.circle(s, (60, 170, 70), (int(x + 12), int(y - 17)), glow + 2)
        pygame.draw.circle(s, (150, 240, 120), (int(x + 12), int(y - 17)), glow)


@register_tower
class Bard(Tower):
    id, name = "bard", "Bard"
    cost, range, cooldown, damage = 180, 110, 1.0, 0
    attacks = False
    buff = 1.15        # attack-speed multiplier given to towers in range
    inspire = 1.0      # damage multiplier given to towers in range
    description = "Doesn't fight. Plays an inspiring tune that makes nearby towers attack faster."
    paths = (
        UpgradePath("Rousing Song", [
            U("Livelier Tune", 100, "Attack speed buff +10%", buff=("+", 0.10)),
            U("Marching Beat", 160, "Attack speed buff +10% more", buff=("+", 0.10)),
            U("Battle Hymn", 300, "Buff +15%, allies deal +15% damage", buff=("+", 0.15), inspire=("=", 1.15)),
        ]),
        UpgradePath("Wide Melody", [
            U("Louder Lute", 90, "+25% aura range", range=("*", 1.25)),
            U("Echoing Hall", 150, "+30% aura range", range=("*", 1.3)),
            U("Anthem", 280, "+30% range, allies deal +10% damage", range=("*", 1.3), inspire=("=", 1.10)),
        ]),
    )

    def info_lines(self):
        extra = f"Allies deal +{(self.inspire - 1) * 100:.0f}% damage" if self.inspire > 1 else "No damage boost"
        return [f"Allies attack +{(self.buff - 1) * 100:.0f}% faster", f"Aura range: {int(self.range)}", extra]

    def apply_aura(self, towers):
        for t in towers:
            if t is not self and t.attacks and self.pos.distance_to(t.pos) <= self.range:
                t.speed_buff = max(t.speed_buff, self.buff)
                t.dmg_buff = max(t.dmg_buff, self.inspire)

    def update_extra(self, dt, game):
        if random.random() < dt * 1.6:
            game.effects.append(Note(self.pos + (random.uniform(-12, 12), -22),
                                     random.choice([GOLD, (255, 140, 170), (140, 220, 255)])))
        if self.flash <= 0 and random.random() < dt * 0.8:
            self.flash = FLASH_TIME

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.ellipse(s, (50, 80, 40), (x - 19, y + 8, 38, 14))
        pygame.draw.ellipse(s, (96, 66, 38), (x - 18, y + 6, 36, 14))
        pygame.draw.ellipse(s, (140, 98, 56), (x - 16, y + 6, 32, 9))
        pygame.draw.polygon(s, (176, 48, 92), [(x - 8, y + 11), (x + 8, y + 11), (x + 5, y - 5), (x - 5, y - 5)])
        pygame.draw.line(s, GOLD, (x - 6, y + 4), (x + 6, y + 4), 2)
        pygame.draw.circle(s, (238, 204, 164), (int(x), int(y - 9)), 6)
        pygame.draw.circle(s, (40, 30, 30), (int(x - 2), int(y - 10)), 1)
        pygame.draw.circle(s, (40, 30, 30), (int(x + 2), int(y - 10)), 1)
        pygame.draw.polygon(s, (50, 150, 150), [(x - 9, y - 12), (x + 9, y - 12), (x + 3, y - 21)])
        pygame.draw.line(s, (240, 200, 60), (x + 3, y - 21), (x + 11, y - 27), 2)
        pygame.draw.line(s, (240, 200, 60), (x + 11, y - 27), (x + 8, y - 24), 2)
        sw = 2 if flash > 0 else 0
        pygame.draw.line(s, (130, 90, 50), (x - 4, y + 2), (x - 18, y - 8 - sw), 2)
        pygame.draw.ellipse(s, (166, 112, 60), (x - 12, y - 1, 13, 11))
        pygame.draw.ellipse(s, (60, 36, 20), (x - 8, y + 2, 5, 5))
        pygame.draw.line(s, (240, 240, 220), (x - 10, y + 1), (x - 3, y + 8), 1)
        if flash > 0:
            draw_note(s, x + 14, y - 8, (255, 220, 120), 0.8)


@register_tower
class Cleric(Tower):
    id, name = "cleric", "Cleric"
    cost, range, cooldown, damage = 180, 130, 1.5, 0
    weaken = 0.25          # extra damage enemies take from the Hex
    weaken_time = 3.0
    slow_amount = 0.35     # fraction of speed removed by Bind
    slow_time = 2.5
    hex_radius = 0
    bind_radius = 0
    description = "Doesn't damage enemies. Casts Hex (they take more damage) and Bind (they move slower)."
    paths = (
        UpgradePath("Curses", [
            U("Lingering Hex", 100, "Hex: +10% damage taken, +1s", weaken=("+", 0.10), weaken_time=("+", 1.0)),
            U("Spreading Doom", 170, "Hex also hits nearby enemies", hex_radius=("+", 34)),
            U("Doom", 300, "Hex: +20% damage taken, wider", weaken=("+", 0.20), hex_radius=("+", 20)),
        ]),
        UpgradePath("Binding", [
            U("Heavy Chains", 100, "Bind: -10% speed, +1s", slow_amount=("+", 0.10), slow_time=("+", 1.0)),
            U("Frost Nova", 170, "Bind also hits nearby enemies", bind_radius=("+", 34)),
            U("Deep Freeze", 300, "Bind: -20% speed, wider", slow_amount=("+", 0.20), bind_radius=("+", 20)),
        ]),
    )
    last_kind = "weaken"

    def info_lines(self):
        return [f"Hex: +{self.weaken * 100:.0f}% damage taken ({self.weaken_time:.1f}s)",
                f"Bind: -{self.slow_amount * 100:.0f}% speed ({self.slow_time:.1f}s)",
                f"Range: {int(self.range)}   Casts {1 / self.cooldown:.1f}/s"]

    def attack(self, target, game):
        # cast whichever spell is running out on this target
        w = target.vuln_t if target.vuln > 0 else 0.0
        kind = "weaken" if w <= target.slow_t else "slow"
        self.last_kind = kind
        start = self.pos + (0, -30)
        if kind == "weaken":
            game.projectiles.append(SpellOrb(start, target, "weaken", self.weaken, self.weaken_time, self.hex_radius))
        else:
            game.projectiles.append(SpellOrb(start, target, "slow", self.slow_amount, self.slow_time, self.bind_radius))

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.ellipse(s, (50, 80, 40), (x - 19, y + 8, 38, 14))
        pygame.draw.ellipse(s, (170, 172, 180), (x - 18, y + 5, 36, 16))
        pygame.draw.ellipse(s, (206, 208, 216), (x - 16, y + 5, 32, 10))
        pygame.draw.polygon(s, (242, 242, 248), [(x - 10, y + 12), (x + 10, y + 12), (x + 6, y - 5), (x - 6, y - 5)])
        pygame.draw.line(s, GOLD, (x - 10, y + 11), (x + 10, y + 11), 2)
        pygame.draw.line(s, GOLD, (x, y - 4), (x, y + 11), 2)
        pygame.draw.circle(s, (238, 204, 164), (int(x), int(y - 9)), 6)
        pygame.draw.circle(s, (230, 230, 240), (int(x), int(y - 12)), 6, 2)
        pygame.draw.circle(s, (40, 30, 30), (int(x - 2), int(y - 9)), 1)
        pygame.draw.circle(s, (40, 30, 30), (int(x + 2), int(y - 9)), 1)
        halo = 255 if flash > 0 else 210
        pygame.draw.ellipse(s, (halo, halo - 20, 90), (x - 8, y - 24, 16, 6), 2)
        pygame.draw.line(s, GOLD, (x + 13, y + 14), (x + 13, y - 14), 3)
        pygame.draw.line(s, GOLD, (x + 8, y - 9), (x + 18, y - 9), 3)
        r = 4 + (3 if flash > 0 else 0)
        pygame.draw.circle(s, (255, 235, 140), (int(x + 13), int(y - 18)), r + 1)
        pygame.draw.circle(s, (255, 255, 235), (int(x + 13), int(y - 18)), max(2, r - 2))


# =============================================================================
# BACKGROUND ART (pre-rendered once)
# =============================================================================
def stroke_path(surf, path, width, color):
    pts = [(int(p.x), int(p.y)) for p in path.points]
    for a, b in zip(pts, pts[1:]):
        pygame.draw.line(surf, color, a, b, width)
    for p in pts:
        pygame.draw.circle(surf, color, p, width // 2)


def draw_tree(s, x, y):
    pygame.draw.ellipse(s, (60, 100, 50), (x - 20, y + 10, 40, 14))
    pygame.draw.rect(s, (92, 62, 36), (x - 4, y - 2, 8, 18))
    for dx, dy, r, c in [(-9, -4, 13, (34, 92, 44)), (9, -4, 13, (34, 92, 44)),
                         (0, -14, 15, (44, 110, 54)), (0, 0, 14, (38, 100, 48))]:
        pygame.draw.circle(s, c, (x + dx, y + dy), r)
    pygame.draw.circle(s, (66, 140, 72), (x - 3, y - 16), 6)


def draw_castle(s, cx, bottom):
    stone, dark, light = (138, 138, 150), (92, 92, 106), (170, 170, 182)
    wall = pygame.Rect(cx - 48, bottom - 50, 96, 50)
    pygame.draw.rect(s, stone, wall)
    for r in range(5):
        pygame.draw.line(s, dark, (wall.x, wall.y + r * 10), (wall.right, wall.y + r * 10), 1)
    for i in range(5):
        pygame.draw.rect(s, stone, (wall.x + i * 20 + 2, wall.y - 8, 12, 8))
    for sx in (cx - 66, cx + 44):
        pygame.draw.rect(s, light, (sx, bottom - 72, 22, 72))
        pygame.draw.rect(s, dark, (sx, bottom - 72, 22, 72), 2)
        for i in range(3):
            pygame.draw.rect(s, light, (sx + i * 8 - 1, bottom - 80, 6, 8))
        pygame.draw.rect(s, (30, 30, 40), (sx + 9, bottom - 52, 4, 12))
        pygame.draw.line(s, (200, 200, 210), (sx + 11, bottom - 80), (sx + 11, bottom - 98), 2)
        pygame.draw.polygon(s, (50, 80, 170), [(sx + 12, bottom - 98), (sx + 26, bottom - 93), (sx + 12, bottom - 88)])
    pygame.draw.rect(s, (36, 24, 16), (cx - 13, bottom - 32, 26, 32))
    pygame.draw.circle(s, (36, 24, 16), (cx, bottom - 32), 13)
    for i in range(-2, 3):
        pygame.draw.line(s, (80, 60, 40), (cx + i * 5, bottom - 42), (cx + i * 5, bottom), 1)


def build_background(path):
    rng = random.Random(42)
    surf = pygame.Surface((MAP_W, SCREEN_H))
    surf.fill(GRASS)
    for _ in range(260):
        x, y, r = rng.randint(0, MAP_W), rng.randint(0, SCREEN_H), rng.randint(18, 60)
        d = rng.randint(-8, 8)
        pygame.draw.circle(surf, (GRASS[0] + d, GRASS[1] + d, GRASS[2] + d), (x, y), r)
    for _ in range(700):
        x, y = rng.randint(0, MAP_W), rng.randint(0, SCREEN_H)
        pygame.draw.line(surf, (64, 112, 52), (x, y), (x + rng.randint(-2, 2), y - rng.randint(3, 6)), 1)

    stroke_path(surf, path, PATH_WIDTH + 10, PATH_EDGE)
    stroke_path(surf, path, PATH_WIDTH, PATH_COL)
    for _ in range(900):
        p = path.position_at(rng.uniform(0, path.length))
        ang, rad = rng.uniform(0, math.tau), rng.uniform(0, PATH_WIDTH * 0.42)
        d = rng.randint(-14, 14)
        pygame.draw.circle(surf, (PATH_COL[0] + d, PATH_COL[1] + d, PATH_COL[2] + d),
                           (int(p.x + math.cos(ang) * rad), int(p.y + math.sin(ang) * rad)), rng.randint(1, 3))

    for _ in range(40):
        x, y = rng.randint(10, MAP_W - 10), rng.randint(10, SCREEN_H - 10)
        if path.distance_to_point((x, y)) > PATH_WIDTH / 2 + 12 and not CASTLE_RECT.collidepoint(x, y):
            pygame.draw.ellipse(surf, (120, 122, 128), (x - 6, y - 3, 12, 8))
            pygame.draw.ellipse(surf, (158, 160, 166), (x - 4, y - 3, 7, 4))
    for _ in range(90):
        x, y = rng.randint(10, MAP_W - 10), rng.randint(10, SCREEN_H - 10)
        if path.distance_to_point((x, y)) > PATH_WIDTH / 2 + 10 and not CASTLE_RECT.collidepoint(x, y):
            pygame.draw.circle(surf, rng.choice([(240, 230, 90), (240, 240, 240), (220, 120, 180)]), (x, y), 2)

    trees, tries = [], 0
    while len(trees) < 46 and tries < 5000:
        tries += 1
        x, y = rng.randint(10, MAP_W - 10), rng.randint(10, SCREEN_H - 10)
        if not (x < 68 or x > MAP_W - 68 or y < 42 or y > SCREEN_H - 45):
            continue
        if path.distance_to_point((x, y)) < PATH_WIDTH / 2 + 32:
            continue
        if CASTLE_RECT.inflate(90, 90).collidepoint(x, y):
            continue
        if any(math.hypot(x - tx, y - ty) < 34 for tx, ty in trees):
            continue
        trees.append((x, y))
    for x, y in sorted(trees, key=lambda p: p[1]):
        draw_tree(surf, x, y)

    draw_castle(surf, 140, SCREEN_H)
    pygame.draw.line(surf, (60, 40, 30), (24, 26), (24, 72), 3)
    pygame.draw.polygon(surf, (150, 30, 40), [(25, 28), (54, 36), (25, 46)])
    pygame.draw.circle(surf, (235, 230, 215), (36, 37), 3)
    return surf


def build_sidebar():
    s = pygame.Surface((SIDEBAR_W, SCREEN_H))
    s.fill((74, 50, 30))
    rng = random.Random(7)
    for i in range(0, SIDEBAR_W, 40):
        pygame.draw.line(s, (50, 32, 18), (i, 0), (i, SCREEN_H), 2)
    for _ in range(260):
        x, y = rng.randint(0, SIDEBAR_W), rng.randint(0, SCREEN_H)
        pygame.draw.line(s, (62, 40, 24), (x, y), (x, y + rng.randint(8, 30)), 1)
    pygame.draw.rect(s, (200, 160, 60), (0, 0, SIDEBAR_W, SCREEN_H), 4)
    pygame.draw.line(s, (30, 18, 10), (0, 0), (0, SCREEN_H), 5)
    return s


# =============================================================================
# GAME
# =============================================================================
class Game:
    def __init__(self, screen):
        self.screen = screen
        self.path = Path(WAYPOINTS)
        self.background = build_background(self.path)
        self.sidebar_bg = build_sidebar()
        self.tower_classes = list(TOWER_TYPES.values())
        self.protos = {cls.id: cls((0, 0)) for cls in self.tower_classes}   # for stat text
        self.best_survived = 0
        self._build_layout()
        self._build_icons()
        self.reset()

    # ------------------------------------------------------------------ setup
    def _build_layout(self):
        x0, w = SIDEBAR_X + 14, SIDEBAR_W - 28
        n = len(self.tower_classes)
        cols, gap = 2, 6
        rows = (n + cols - 1) // cols
        top, bottom = 164, 350
        cw = (w - gap) // cols
        ch = min(58, (bottom - top - gap * (rows - 1)) // rows)
        self.card_rects = [pygame.Rect(x0 + (i % cols) * (cw + gap), top + (i // cols) * (ch + gap), cw, ch)
                           for i in range(n)]
        self.icon_size = min(48, ch - 8)
        self.panel_rect = p = pygame.Rect(x0, 358, w, 240)
        self.up_rects = [pygame.Rect(p.x + 6, p.y + 80, p.w - 12, 58),
                         pygame.Rect(p.x + 6, p.y + 142, p.w - 12, 58)]
        self.sell_rect = pygame.Rect(p.right - 96, p.bottom - 34, 90, 26)
        self.start_rect = pygame.Rect(x0, 604, w, 40)
        half = (w - 8) // 2
        self.speed_rect = pygame.Rect(x0, 652, half, 34)
        self.pause_rect = pygame.Rect(x0 + half + 8, 652, half, 34)
        self.restart_rect = pygame.Rect(SCREEN_W // 2 - 95, 400, 190, 52)

    def _build_icons(self):
        self.icons = {}
        for cls in self.tower_classes:
            tmp = pygame.Surface((80, 80), pygame.SRCALPHA)
            cls.draw_sprite(tmp, 40, 52)
            self.icons[cls.id] = pygame.transform.smoothscale(tmp, (self.icon_size, self.icon_size))

    def reset(self):
        self.money = START_MONEY
        self.lives = START_LIVES
        self.wave = 0
        self.state = "ready"            # ready | wave | gameover
        self.towers, self.enemies, self.projectiles, self.effects, self.patches = [], [], [], [], []
        self._spawned = []
        self.spawn_queue = []
        self.spawn_timer = 0.0
        self.hp_mult = self.speed_mult = 1.0
        self.speed_idx = 0
        self.paused = False
        self.placing = None             # tower class being placed
        self.selected_tower = None
        self.banner_text, self.banner_time = "", 0.0
        self.show("Place your defenders, then press SPACE to begin!", 4.0)

    @property
    def speed(self):
        return SPEED_STEPS[self.speed_idx]

    def show(self, text, secs=2.2):
        self.banner_text, self.banner_time = text, secs

    def spawn(self, enemy):
        """Queue an enemy to join the field at the end of this update step."""
        self._spawned.append(enemy)

    # ------------------------------------------------------------------ rules
    def can_place(self, pos, cls):
        p = pygame.Vector2(pos)
        if self.money < cls.cost:
            return False, "Not enough gold!"
        if not (20 <= p.x <= MAP_W - 20 and 20 <= p.y <= SCREEN_H - 20):
            return False, "Too close to the edge."
        if self.path.distance_to_point(p) < PATH_WIDTH / 2 + 17:
            return False, "You can't build on the road!"
        if CASTLE_RECT.inflate(30, 30).collidepoint(p):
            return False, "That's the castle!"
        if any(p.distance_to(t.pos) < 38 for t in self.towers):
            return False, "Too close to another tower."
        return True, ""

    def start_wave(self):
        if self.state != "ready":
            return
        self.wave += 1
        self.spawn_queue = generate_wave(self.wave)
        self.spawn_timer = 0.5
        self.hp_mult = wave_hp_mult(self.wave)
        self.speed_mult = wave_speed_mult(self.wave)
        self.state = "wave"
        self.show(f"Wave {self.wave} begins!")

    def sell_selected(self):
        t = self.selected_tower
        if t:
            self.money += t.sell_value
            self.effects.append(FloatText(t.pos + (0, -30), f"+{t.sell_value}"))
            self.towers.remove(t)
            self.selected_tower = None

    def buy_upgrade(self, i):
        t = self.selected_tower
        if not t:
            return
        ok, why = t.can_upgrade(i)
        if not ok:
            self.show(why, 1.5)
            return
        up = t.next_upgrade(i)
        if self.money < up.cost:
            self.show("Not enough gold!", 1.5)
            return
        self.money -= up.cost
        t.upgrade(i)
        self.effects.append(Ring(t.pos, ACCENTS[i], 34, 0.5))
        self.effects.append(FloatText(t.pos + (0, -34), up.name, ACCENTS[i], 15))
        for _ in range(8):
            ang = random.uniform(0, math.tau)
            self.effects.append(Particle(t.pos, (math.cos(ang) * 70, math.sin(ang) * 70 - 30),
                                         random.choice([GOLD, (255, 255, 255), ACCENTS[i]]), 0.6, 3))

    def select_tower_type(self, idx):
        if idx >= len(self.tower_classes):
            return
        cls = self.tower_classes[idx]
        if self.placing is cls:
            self.placing = None
            return
        if self.money < cls.cost:
            self.show("Not enough gold!", 1.5)
            return
        self.placing = cls
        self.selected_tower = None

    # ------------------------------------------------------------------ input
    def handle_event(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN:
            self.handle_click(e.pos, e.button, bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
        elif e.type == pygame.KEYDOWN:
            self.handle_key(e.key)

    def handle_key(self, key):
        if self.state == "gameover":
            if key in (pygame.K_r, pygame.K_SPACE, pygame.K_RETURN):
                self.reset()
            return
        if key == pygame.K_ESCAPE:
            self.placing = self.selected_tower = None
        elif key in (pygame.K_SPACE, pygame.K_RETURN):
            self.start_wave()
        elif key == pygame.K_f:
            self.speed_idx = (self.speed_idx + 1) % len(SPEED_STEPS)
        elif key == pygame.K_p:
            self.paused = not self.paused
        elif key == pygame.K_s:
            self.sell_selected()
        elif key == pygame.K_q:
            self.buy_upgrade(0)
        elif key == pygame.K_w:
            self.buy_upgrade(1)
        elif pygame.K_1 <= key <= pygame.K_9:
            self.select_tower_type(key - pygame.K_1)

    def handle_click(self, pos, button, shift=False):
        if self.state == "gameover":
            if button == 1 and self.restart_rect.collidepoint(pos):
                self.reset()
            return
        if button == 3:
            self.placing = self.selected_tower = None
            return
        if button != 1:
            return
        if pos[0] >= SIDEBAR_X:
            self._click_sidebar(pos)
        else:
            self._click_map(pos, shift)

    def _click_sidebar(self, pos):
        for i, r in enumerate(self.card_rects):
            if r.collidepoint(pos):
                self.select_tower_type(i)
                return
        if self.start_rect.collidepoint(pos):
            self.start_wave()
        elif self.speed_rect.collidepoint(pos):
            self.speed_idx = (self.speed_idx + 1) % len(SPEED_STEPS)
        elif self.pause_rect.collidepoint(pos):
            self.paused = not self.paused
        elif self.selected_tower:
            for i, r in enumerate(self.up_rects[:len(self.selected_tower.paths)]):
                if r.collidepoint(pos):
                    self.buy_upgrade(i)
                    return
            if self.sell_rect.collidepoint(pos):
                self.sell_selected()

    def _click_map(self, pos, shift):
        if self.placing:
            ok, why = self.can_place(pos, self.placing)
            if not ok:
                self.show(why, 1.5)
                return
            cls = self.placing
            self.towers.append(cls(pos))
            self.money -= cls.cost
            self.effects.append(FloatText(pygame.Vector2(pos) + (0, -28), f"-{cls.cost}", (230, 120, 100)))
            if not shift or self.money < cls.cost:
                self.placing = None
            return
        p = pygame.Vector2(pos)
        self.selected_tower = None
        for t in self.towers:
            if p.distance_to(t.pos) <= 22:
                self.selected_tower = t
                break

    # ----------------------------------------------------------------- update
    def update(self, real_dt):
        self.banner_time = max(0.0, self.banner_time - real_dt)
        if self.state == "gameover" or self.paused:
            return
        dt = real_dt * self.speed

        if self.state == "wave" and self.spawn_queue:
            self.spawn_timer -= dt
            if self.spawn_timer <= 0:
                eid, delay = self.spawn_queue.pop(0)
                self.enemies.append(ENEMY_TYPES[eid](self.path, self.hp_mult, self.speed_mult))
                self.spawn_timer = delay

        # support auras first, so buffs are fresh when towers fire
        for t in self.towers:
            t.speed_buff = t.dmg_buff = 1.0
        for t in self.towers:
            t.apply_aura(self.towers)

        for t in self.towers:
            t.update(dt, self)
        for p in self.patches:
            p.update(dt, self)
        for e in self.enemies:
            e.update(dt, self)
        for p in self.projectiles:
            p.update(dt, self)
        for fx in self.effects:
            fx.update(dt)

        fresh, self._spawned = self._spawned, []
        for e in self.enemies:
            if e.escaped:
                self.lives -= e.lives_cost
                self.effects.append(FloatText(e.pos + (0, -20), f"-{e.lives_cost}", (240, 70, 70), 20))
            elif e.hp <= 0:
                self.money += e.reward
                self.effects.append(FloatText(e.pos + (0, -18), f"+{e.reward}"))
                for _ in range(7):
                    ang = random.uniform(0, math.tau)
                    sp = random.uniform(30, 90)
                    self.effects.append(Particle(e.pos, (math.cos(ang) * sp, math.sin(ang) * sp),
                                                 random.choice([e.color, (90, 90, 90)]), 0.5, 4))
                fresh.extend(e.on_death(self))
        self.enemies = [e for e in self.enemies if e.alive] + fresh
        self.projectiles = [p for p in self.projectiles if not p.dead]
        self.effects = [fx for fx in self.effects if not fx.dead]
        self.patches = [p for p in self.patches if not p.dead]

        if self.lives <= 0:
            self.lives = 0
            self.state = "gameover"
            self.best_survived = max(self.best_survived, self.wave - 1)
            self.placing = self.selected_tower = None
            return

        if self.state == "wave" and not self.spawn_queue and not self.enemies:
            bonus = WAVE_BONUS_BASE + WAVE_BONUS_PER_WAVE * self.wave
            self.money += bonus
            self.state = "ready"
            self.show(f"Wave {self.wave} cleared!  +{bonus} gold", 3.0)

    # ------------------------------------------------------------------- draw
    def draw(self):
        s = self.screen
        mouse = pygame.mouse.get_pos()
        s.blit(self.background, (0, 0))

        for p in self.patches:
            p.draw(s)
        if self.selected_tower:
            col = (255, 220, 120) if not self.selected_tower.attacks else (255, 255, 255)
            draw_range(s, self.selected_tower.pos, self.selected_tower.range, col, 35)
        for t in sorted(self.towers, key=lambda t: t.pos.y):
            t.draw(s)
        if self.selected_tower:
            pygame.draw.circle(s, GOLD, (int(self.selected_tower.pos.x), int(self.selected_tower.pos.y)), 21, 2)
        for e in sorted(self.enemies, key=lambda e: e.pos.y):
            e.draw(s)
        for p in self.projectiles:
            p.draw(s)
        for fx in self.effects:
            fx.draw(s)

        if self.placing and mouse[0] < MAP_W and self.state != "gameover":
            ok, _ = self.can_place(mouse, self.placing)
            draw_range(s, mouse, self.placing.range, (90, 220, 110) if ok else (230, 70, 70), 55)
            self.placing.draw_sprite(s, mouse[0], mouse[1])

        self._draw_banner(s)
        self._draw_sidebar(s, mouse)
        if self.paused and self.state != "gameover":
            draw_text(s, "PAUSED", 54, GOLD, (MAP_W // 2, SCREEN_H // 2), "center", True, True)
        if self.state == "gameover":
            self._draw_gameover(s, mouse)

    def _draw_banner(self, s):
        if self.banner_time <= 0:
            return
        fnt = font(20, True)
        img = fnt.render(self.banner_text, True, INK)
        r = img.get_rect(center=(MAP_W // 2, 34)).inflate(36, 16)
        box = pygame.Surface(r.size, pygame.SRCALPHA)
        alpha = int(255 * min(1.0, self.banner_time / 0.5))
        pygame.draw.rect(box, (*PARCHMENT, alpha), box.get_rect(), border_radius=10)
        pygame.draw.rect(box, (120, 84, 40, alpha), box.get_rect(), 3, border_radius=10)
        img.set_alpha(alpha)
        box.blit(img, img.get_rect(center=(r.w // 2, r.h // 2)))
        s.blit(box, r)

    def _button(self, s, rect, label, enabled=True, hover=False, color=(96, 140, 70), size=18):
        col = color if enabled else (110, 100, 88)
        if hover and enabled:
            col = tuple(min(255, c + 22) for c in col)
        pygame.draw.rect(s, col, rect, border_radius=8)
        pygame.draw.rect(s, (40, 26, 14), rect, 3, border_radius=8)
        draw_text(s, label, size, (250, 240, 215) if enabled else (190, 180, 165), rect.center, "center", True, True)

    def _draw_sidebar(self, s, mouse):
        s.blit(self.sidebar_bg, (SIDEBAR_X, 0))
        cx = SIDEBAR_X + SIDEBAR_W // 2
        draw_text(s, "REALM DEFENDERS", 24, GOLD, (cx, 12), "midtop", True, True)
        lx = SIDEBAR_X + 34
        draw_coin(s, lx, 62)
        draw_text(s, f"{self.money}", 20, (250, 235, 190), (lx + 20, 50), "topleft", True, True)
        draw_heart(s, lx, 92)
        draw_text(s, f"{self.lives}", 20, (250, 235, 190), (lx + 20, 80), "topleft", True, True)
        draw_banner_icon(s, lx, 122)
        draw_text(s, f"Wave {self.wave}", 20, (250, 235, 190), (lx + 20, 110), "topleft", True, True)
        draw_text(s, "~ Towers ~", 15, GOLD, (cx, 144), "midtop", True, True)

        hover_idx = None
        for i, cls in enumerate(self.tower_classes):
            r = self.card_rects[i]
            afford = self.money >= cls.cost
            hover = r.collidepoint(mouse)
            if hover:
                hover_idx = i
            base = PARCHMENT if afford else (150, 136, 112)
            if hover and afford:
                base = (240, 220, 172)
            pygame.draw.rect(s, base, r, border_radius=8)
            active = self.placing is cls
            pygame.draw.rect(s, GOLD if active else (110, 76, 38), r, 4 if active else 3, border_radius=8)
            s.blit(self.icons[cls.id], (r.x + 4, r.centery - self.icon_size // 2))
            tx = r.x + self.icon_size + 9
            draw_text(s, cls.name, 15, INK, (tx, r.y + 6), "topleft", True)
            draw_coin(s, tx + 7, r.y + r.h // 2 + 12, 7)
            draw_text(s, f"{cls.cost}", 15, INK if afford else (120, 40, 30), (tx + 18, r.y + r.h // 2 + 3), "topleft", True)
            draw_text(s, f"{i + 1}", 11, (92, 66, 40), (r.right - 6, r.top + 4), "topright")

        self._draw_panel(s, hover_idx)

        if self.state == "ready":
            self._button(s, self.start_rect, f"Start Wave {self.wave + 1}", True,
                         self.start_rect.collidepoint(mouse))
        else:
            self._button(s, self.start_rect, "Wave in progress...", False)
        self._button(s, self.speed_rect, f"Speed x{self.speed}", True,
                     self.speed_rect.collidepoint(mouse), (140, 100, 50), 15)
        self._button(s, self.pause_rect, "Resume" if self.paused else "Pause", True,
                     self.pause_rect.collidepoint(mouse), (140, 100, 50), 15)

    def _draw_upgrade_button(self, s, rect, t, i, mouse):
        path = t.paths[i]
        tier = t.tiers[i]
        up = t.next_upgrade(i)
        ok, why = t.can_upgrade(i)
        afford = ok and self.money >= up.cost
        accent = ACCENTS[i]
        if afford:
            base = (232, 214, 168)
            if rect.collidepoint(mouse):
                base = (246, 232, 190)
        else:
            base = (190, 172, 134)
        pygame.draw.rect(s, base, rect, border_radius=7)
        pygame.draw.rect(s, accent, rect, 3, border_radius=7)
        pygame.draw.rect(s, accent, (rect.x, rect.y + 6, 6, rect.h - 12), border_radius=3)
        x = rect.x + 12
        draw_text(s, f"{path.name}  [{'QW'[i]}]", 14, INK, (x, rect.y + 3), "topleft", True)
        for j in range(len(path.tiers)):
            c = (rect.right - 16 - 14 * (len(path.tiers) - 1 - j), rect.y + 12)
            pygame.draw.circle(s, (40, 26, 14), c, 6)
            pygame.draw.circle(s, accent if j < tier else (150, 134, 104), c, 4)
        if up is None:
            draw_text(s, "Fully upgraded", 13, (90, 70, 40), (x, rect.y + 24), "topleft", True)
            draw_text(s, path.tiers[-1].name, 12, (110, 84, 50), (x, rect.y + 40))
        elif not ok:
            draw_text(s, f"{up.name}", 13, (120, 96, 64), (x, rect.y + 24), "topleft", True)
            draw_text(s, "Locked: other path is too far along", 12, (150, 50, 40), (x, rect.y + 40))
        else:
            draw_text(s, up.name, 13, INK, (x, rect.y + 24), "topleft", True)
            cc = INK if afford else (150, 40, 30)
            tw = font(13, True).size(str(up.cost))[0]
            draw_coin(s, rect.right - 14 - tw - 10, rect.y + 32, 6)
            draw_text(s, str(up.cost), 13, cc, (rect.right - 10, rect.y + 24), "topright", True)
            draw_text(s, up.desc, 12, (84, 62, 36), (x, rect.y + 40))

    def _draw_panel(self, s, hover_idx):
        r = self.panel_rect
        pygame.draw.rect(s, PARCHMENT, r, border_radius=8)
        pygame.draw.rect(s, (120, 84, 40), r, 3, border_radius=8)
        x, y = r.x + 10, r.y + 6
        mouse = pygame.mouse.get_pos()
        t = self.selected_tower
        if t:
            draw_text(s, t.name, 18, INK, (x, y), "topleft", True)
            draw_text(s, f"Value: {t.spent}g", 12, (110, 84, 50), (r.right - 10, y + 4), "topright")
            for i, line in enumerate(t.info_lines()):
                draw_text(s, line, 13, INK, (x, y + 24 + i * 16))
            for i in range(len(t.paths)):
                self._draw_upgrade_button(s, self.up_rects[i], t, i, mouse)
            self._button(s, self.sell_rect, f"Sell  {t.sell_value}g", True,
                         self.sell_rect.collidepoint(mouse), (170, 80, 50), 13)
            draw_text(s, "Third tier needs the other path at tier 1 or less", 10, (110, 84, 50),
                      (x, r.bottom - 22))
        elif hover_idx is not None:
            cls = self.tower_classes[hover_idx]
            proto = self.protos[cls.id]
            draw_text(s, cls.name, 18, INK, (x, y), "topleft", True)
            for i, line in enumerate(wrap_text(cls.description, font(13), r.w - 20)[:4]):
                draw_text(s, line, 13, (84, 62, 36), (x, y + 24 + i * 16))
            for i, line in enumerate(proto.info_lines()):
                draw_text(s, line, 13, INK, (x, y + 96 + i * 16), "topleft", True)
            draw_text(s, "Upgrade paths:", 13, INK, (x, y + 152), "topleft", True)
            for i, pth in enumerate(cls.paths):
                pygame.draw.circle(s, ACCENTS[i], (x + 6, y + 180 + i * 18), 5)
                draw_text(s, pth.name, 13, INK, (x + 18, y + 172 + i * 18))
        elif self.state == "ready":
            draw_text(s, f"Next: Wave {self.wave + 1}", 17, INK, (x, y), "topleft", True)
            comp = wave_composition(self.wave + 1)
            prev = wave_composition(self.wave) if self.wave > 0 else {}
            for i, (eid, n) in enumerate(comp.items()):
                cls = ENEMY_TYPES[eid]
                yy = y + 28 + i * 20
                pygame.draw.circle(s, cls.color, (x + 7, yy + 8), 6)
                pygame.draw.circle(s, (40, 26, 14), (x + 7, yy + 8), 6, 1)
                rr = draw_text(s, f"{n} x {cls.name}", 14, INK, (x + 20, yy))
                if self.wave > 0 and eid not in prev:
                    draw_text(s, "NEW!", 12, (190, 50, 40), (rr.right + 8, yy + 2), "topleft", True)
        else:
            left = len(self.enemies) + len(self.spawn_queue)
            draw_text(s, f"Wave {self.wave}", 17, INK, (x, y), "topleft", True)
            draw_text(s, f"Enemies left: {left}", 14, INK, (x, y + 28))
            draw_text(s, "Click a tower to inspect and upgrade it.", 12, (110, 80, 50), (x, y + 56))
            draw_text(s, "Hover a shop card to learn about a tower.", 12, (110, 80, 50), (x, y + 74))

    def _draw_gameover(self, s, mouse):
        veil = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        veil.fill((15, 5, 5, 185))
        s.blit(veil, (0, 0))
        cx = SCREEN_W // 2
        draw_text(s, "DEFEAT", 84, (200, 50, 50), (cx, 190), "center", True, True)
        draw_text(s, "The castle has fallen...", 26, (240, 225, 190), (cx, 258), "center", False, True)
        draw_text(s, f"You held out for {max(0, self.wave - 1)} waves   (best: {self.best_survived})", 22,
                  GOLD, (cx, 310), "center", True, True)
        draw_text(s, f"Reached wave {self.wave}  -  {len(self.towers)} towers built", 17,
                  (220, 205, 170), (cx, 348), "center", False, True)
        self._button(s, self.restart_rect, "Play Again", True, self.restart_rect.collidepoint(mouse), (96, 140, 70), 24)
        draw_text(s, "or press R", 14, (200, 190, 160), (cx, 470), "center")


# =============================================================================
# MAIN
# =============================================================================
def main():
    pygame.init()
    pygame.display.set_caption("Realm Defenders - Medieval Tower Defense")
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    clock = pygame.time.Clock()
    game = Game(screen)
    while True:
        dt = min(clock.tick(FPS) / 1000.0, 0.05)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            game.handle_event(event)
        game.update(dt)
        game.draw()
        pygame.display.flip()


if __name__ == "__main__":
    main()
