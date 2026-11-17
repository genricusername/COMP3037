#!/usr/bin/env python3
"""
REALM DEFENDERS - a medieval fantasy tower defense game (pygame)

Run:      pip install pygame && python medieval_td.py

Controls
    Left click      pick a tower in the shop, then click the map to build it
                    (hold SHIFT to keep building the same tower)
    Click a tower   select it (shows range, lets you sell it)
    Right click/ESC cancel building / deselect
    SPACE           start the next wave (you control the pace!)
    1-9             pick a tower from the shop
    S               sell selected tower
    F               cycle game speed (x1 / x2 / x3)
    P               pause
    R / SPACE       restart after defeat

HOW TO ADD A NEW TOWER
    1. Subclass `Tower`, set id / name / cost / range / cooldown / damage /
       description.
    2. Implement `attack(self, target, game)` (spawn a projectile, deal damage...)
    3. Implement `draw_sprite(cls, surf, x, y, angle, flash)` for its looks.
    4. Decorate the class with @register_tower. It shows up in the shop,
       gets a hotkey and works with placement/selling automatically.

HOW TO ADD A NEW ENEMY
    Subclass `Enemy`, set stats, implement `draw_sprite`, decorate with
    @register_enemy, then mention it in `wave_composition()`.
"""
import math
import random
import sys
from bisect import bisect_right
from collections import Counter

import pygame

# =============================================================================
# CONFIG
# =============================================================================
SCREEN_W, SCREEN_H = 1140, 700
MAP_W = 900
SIDEBAR_W = SCREEN_W - MAP_W
SIDEBAR_X = MAP_W
FPS = 60

START_MONEY = 350
START_LIVES = 20
PATH_WIDTH = 46
SELL_REFUND = 0.7          # fraction of cost refunded when selling
REWARD_PER_HP = 0.1        # gold per point of enemy max HP
WAVE_BONUS_BASE = 40       # gold for clearing a wave: base + per_wave * wave
WAVE_BONUS_PER_WAVE = 10
SPEED_STEPS = (1, 2, 3)
FLASH_TIME = 0.2           # tower attack animation length

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

    def __init__(self, path, hp_mult=1.0, speed_mult=1.0):
        self.path = path
        self.max_hp = self.base_hp * hp_mult
        self.hp = self.max_hp
        self.move_speed = self.speed * speed_mult
        self.dist = 0.0
        self.pos = path.position_at(0)
        self.reward = max(1, round(self.max_hp * REWARD_PER_HP))
        self.since_damage = 99.0
        self.facing = 1
        self.anim = random.random() * 10
        self.escaped = False

    @property
    def alive(self):
        return self.hp > 0 and not self.escaped

    def take_damage(self, amount):
        if self.hp <= 0:
            return
        self.hp -= amount
        self.since_damage = 0.0

    def special(self, dt):
        """Hook for per-enemy behaviour (e.g. regeneration)."""

    def update(self, dt):
        self.anim += dt
        self.since_damage += dt
        self.special(dt)
        self.dist += self.move_speed * dt
        if self.dist >= self.path.length:
            self.escaped = True
            return
        old_x = self.pos.x
        self.pos = self.path.position_at(self.dist)
        dx = self.pos.x - old_x
        if abs(dx) > 0.01:
            self.facing = 1 if dx > 0 else -1

    def draw(self, s):
        self.draw_sprite(s, self.pos.x, self.pos.y)
        if self.hp < self.max_hp:
            w = int(self.radius * 2 + 10)
            bx, by = int(self.pos.x - w / 2), int(self.pos.y - self.sprite_height)
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

    def special(self, dt):
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


# =============================================================================
# WAVES
# =============================================================================
def wave_composition(n):
    """How many of each enemy appear in wave n. Edit this to rebalance."""
    comp = {"skeleton": 5 + n}
    if n >= 2:
        comp["goblin"] = (n - 1) * 3 // 2
    if n >= 4:
        comp["zombie"] = (n - 2) // 2 + 1
    if n >= 8:
        comp["dragon"] = (n - 6) // 2
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
    def __init__(self, pos, target, damage, splash):
        super().__init__(pos, target, damage, 270)
        self.splash = splash

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
    splash = 0           # radius of area damage (display only; used by subclasses)

    def __init__(self, pos):
        self.pos = pygame.Vector2(pos)
        self.cd = 0.0
        self.flash = 0.0
        self.angle = -math.pi / 2
        self.spent = self.cost

    @property
    def sell_value(self):
        return int(self.spent * SELL_REFUND)

    def find_target(self, enemies):
        """Targets the enemy furthest along the path that is inside the range."""
        best, best_d = None, -1.0
        for e in enemies:
            if e.hp <= 0:
                continue
            if self.pos.distance_to(e.pos) <= self.range + e.radius and e.dist > best_d:
                best, best_d = e, e.dist
        return best

    def update(self, dt, game):
        self.cd = max(0.0, self.cd - dt)
        self.flash = max(0.0, self.flash - dt)
        target = self.find_target(game.enemies)
        if target is None:
            return
        d = target.pos - self.pos
        self.angle = math.atan2(d.y, d.x)
        if self.cd <= 0:
            self.attack(target, game)
            self.cd = self.cooldown
            self.flash = FLASH_TIME

    def attack(self, target, game):
        raise NotImplementedError

    def draw(self, s):
        self.draw_sprite(s, self.pos.x, self.pos.y, self.angle, self.flash)

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.circle(s, (180, 180, 180), (int(x), int(y)), 14)


@register_tower
class Archer(Tower):
    id, name = "archer", "Archer"
    cost, range, cooldown, damage = 100, 150, 0.8, 14
    description = "Cheap and reliable. Looses arrows at the enemy furthest along the path."

    def attack(self, target, game):
        start = self.pos + pygame.Vector2(math.cos(self.angle), math.sin(self.angle)) * 14 + (0, -8)
        game.projectiles.append(Arrow(start, target, self.damage))

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
        # bow, pointing at the target
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
    cost, range, cooldown, damage, splash = 200, 140, 2.2, 30, 60
    description = "Slowly hurls fireballs that explode, burning every enemy in a circle."

    def attack(self, target, game):
        start = self.pos + (0, -34)
        game.projectiles.append(Fireball(start, target, self.damage, self.splash))

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
    description = "Strikes a single enemy with blinding speed, but only at close range."

    def attack(self, target, game):
        target.take_damage(self.damage)
        game.effects.append(Slash(target.pos, self.angle))

    @classmethod
    def draw_sprite(cls, s, x, y, angle=-math.pi / 2, flash=0.0):
        pygame.draw.ellipse(s, (50, 80, 40), (x - 19, y + 8, 38, 14))
        pygame.draw.circle(s, (96, 96, 104), (int(x), int(y + 4)), 17)
        pygame.draw.circle(s, (140, 140, 150), (int(x), int(y + 4)), 14)
        pygame.draw.circle(s, (176, 180, 192), (int(x), int(y - 2)), 10)
        # plume + helmet
        pygame.draw.polygon(s, (190, 40, 40), [(x - 2, y - 11), (x + 2, y - 11), (x + 7, y - 20), (x - 6, y - 17)])
        pygame.draw.circle(s, (200, 204, 214), (int(x), int(y - 6)), 7)
        pygame.draw.line(s, (40, 40, 50), (x - 5, y - 6), (x + 5, y - 6), 2)
        # shield
        pygame.draw.polygon(s, (50, 80, 160), [(x - 17, y - 2), (x - 7, y - 2), (x - 7, y + 8), (x - 12, y + 14), (x - 17, y + 8)])
        pygame.draw.line(s, GOLD, (x - 12, y - 1), (x - 12, y + 11), 2)
        pygame.draw.line(s, GOLD, (x - 16, y + 4), (x - 8, y + 4), 2)
        # sword swings when attacking
        swing = (flash / FLASH_TIME) * 1.3 - 0.65 if flash > 0 else -0.2
        a = angle + swing
        d = pygame.Vector2(math.cos(a), math.sin(a))
        base = pygame.Vector2(x, y + 2) + d * 8
        pygame.draw.line(s, (214, 218, 230), base, base + d * 17, 3)
        perp = pygame.Vector2(-d.y, d.x)
        pygame.draw.line(s, GOLD, base - perp * 4, base + perp * 4, 3)


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

    # rocks & flowers
    for _ in range(40):
        x, y = rng.randint(10, MAP_W - 10), rng.randint(10, SCREEN_H - 10)
        if path.distance_to_point((x, y)) > PATH_WIDTH / 2 + 12 and not CASTLE_RECT.collidepoint(x, y):
            pygame.draw.ellipse(surf, (120, 122, 128), (x - 6, y - 3, 12, 8))
            pygame.draw.ellipse(surf, (158, 160, 166), (x - 4, y - 3, 7, 4))
    for _ in range(90):
        x, y = rng.randint(10, MAP_W - 10), rng.randint(10, SCREEN_H - 10)
        if path.distance_to_point((x, y)) > PATH_WIDTH / 2 + 10 and not CASTLE_RECT.collidepoint(x, y):
            pygame.draw.circle(surf, rng.choice([(240, 230, 90), (240, 240, 240), (220, 120, 180)]), (x, y), 2)

    # trees only in the margins so they never block building space
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
    # enemy spawn banner
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
    for _ in range(220):
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
        self.best_survived = 0
        self._build_layout()
        self._build_icons()
        self.reset()

    # ------------------------------------------------------------------ setup
    def _build_layout(self):
        x0, w = SIDEBAR_X + 14, SIDEBAR_W - 28
        n = len(self.tower_classes)
        top, bottom, gap = 168, 488, 6
        ch = min(58, (bottom - top - gap * (n - 1)) // max(1, n))
        self.card_rects = [pygame.Rect(x0, top + i * (ch + gap), w, ch) for i in range(n)]
        self.panel_rect = pygame.Rect(x0, 496, w, 98)
        self.start_rect = pygame.Rect(x0, 602, w, 42)
        half = (w - 8) // 2
        self.speed_rect = pygame.Rect(x0, 652, half, 34)
        self.pause_rect = pygame.Rect(x0 + half + 8, 652, half, 34)
        self.sell_rect = pygame.Rect(self.panel_rect.right - 78, self.panel_rect.bottom - 32, 70, 24)
        self.restart_rect = pygame.Rect(SCREEN_W // 2 - 95, 400, 190, 52)

    def _build_icons(self):
        self.icons = {}
        for cls in self.tower_classes:
            tmp = pygame.Surface((80, 80), pygame.SRCALPHA)
            cls.draw_sprite(tmp, 40, 52)
            self.icons[cls.id] = pygame.transform.smoothscale(tmp, (48, 48))

    def reset(self):
        self.money = START_MONEY
        self.lives = START_LIVES
        self.wave = 0
        self.state = "ready"            # ready | wave | gameover
        self.towers, self.enemies, self.projectiles, self.effects = [], [], [], []
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
        elif self.selected_tower and self.sell_rect.collidepoint(pos):
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

        for t in self.towers:
            t.update(dt, self)
        for e in self.enemies:
            e.update(dt)
        for p in self.projectiles:
            p.update(dt, self)
        for fx in self.effects:
            fx.update(dt)

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
        self.enemies = [e for e in self.enemies if e.alive]
        self.projectiles = [p for p in self.projectiles if not p.dead]
        self.effects = [fx for fx in self.effects if not fx.dead]

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

        if self.selected_tower:
            draw_range(s, self.selected_tower.pos, self.selected_tower.range, (255, 255, 255), 35)
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
        draw_text(s, "REALM DEFENDERS", 22, GOLD, (cx, 12), "midtop", True, True)
        lx = SIDEBAR_X + 30
        draw_coin(s, lx, 62)
        draw_text(s, f"{self.money}", 20, (250, 235, 190), (lx + 20, 50), "topleft", True, True)
        draw_heart(s, lx, 92)
        draw_text(s, f"{self.lives}", 20, (250, 235, 190), (lx + 20, 80), "topleft", True, True)
        draw_banner_icon(s, lx, 122)
        draw_text(s, f"Wave {self.wave}", 20, (250, 235, 190), (lx + 20, 110), "topleft", True, True)
        draw_text(s, "~ Towers ~", 15, GOLD, (cx, 146), "midtop", True, True)

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
            icon = self.icons[cls.id]
            s.blit(icon, (r.x + 4, r.centery - 24))
            draw_text(s, cls.name, 17, INK, (r.x + 58, r.y + 4), "topleft", True)
            draw_coin(s, r.right - 46, r.y + 15, 7)
            draw_text(s, f"{cls.cost}", 16, INK, (r.right - 36, r.y + 5), "topleft", True)
            small = (92, 66, 40)
            draw_text(s, f"Dmg {cls.damage}   Range {cls.range}", 12, small, (r.x + 58, r.y + 25))
            draw_text(s, f"Speed {1 / cls.cooldown:.1f}/s", 12, small, (r.x + 58, r.y + 39))
            draw_text(s, f"[{i + 1}]", 12, small, (r.right - 6, r.bottom - 5), "bottomright")

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

    def _draw_panel(self, s, hover_idx):
        r = self.panel_rect
        pygame.draw.rect(s, PARCHMENT, r, border_radius=8)
        pygame.draw.rect(s, (120, 84, 40), r, 3, border_radius=8)
        x, y = r.x + 10, r.y + 6
        t = self.selected_tower
        if t:
            draw_text(s, t.name, 17, INK, (x, y), "topleft", True)
            rows = [f"Damage: {t.damage}" + (f"  (splash {t.splash})" if t.splash else ""),
                    f"Range: {t.range}",
                    f"Attacks: {1 / t.cooldown:.1f} per second"]
            for i, line in enumerate(rows):
                draw_text(s, line, 13, INK, (x, y + 24 + i * 16))
            mouse = pygame.mouse.get_pos()
            self._button(s, self.sell_rect, f"Sell {t.sell_value}", True,
                         self.sell_rect.collidepoint(mouse), (170, 80, 50), 13)
        elif hover_idx is not None:
            cls = self.tower_classes[hover_idx]
            draw_text(s, cls.name, 17, INK, (x, y), "topleft", True)
            for i, line in enumerate(wrap_text(cls.description, font(13), r.w - 20)[:5]):
                draw_text(s, line, 13, INK, (x, y + 24 + i * 16))
        elif self.state == "ready":
            draw_text(s, f"Next: Wave {self.wave + 1}", 16, INK, (x, y), "topleft", True)
            comp = wave_composition(self.wave + 1)
            for i, (eid, n) in enumerate(comp.items()):
                cls = ENEMY_TYPES[eid]
                yy = y + 25 + i * 16
                pygame.draw.circle(s, cls.color, (x + 6, yy + 7), 5)
                pygame.draw.circle(s, (40, 26, 14), (x + 6, yy + 7), 5, 1)
                draw_text(s, f"{n} x {cls.name}", 13, INK, (x + 18, yy))
        else:
            left = len(self.enemies) + len(self.spawn_queue)
            draw_text(s, f"Wave {self.wave}", 16, INK, (x, y), "topleft", True)
            draw_text(s, f"Enemies left: {left}", 14, INK, (x, y + 28))
            draw_text(s, "Click a tower to inspect it", 12, (110, 80, 50), (x, y + 52))

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
