"""
BlockWorld – Core Engine
========================
Shared constants, block definitions, World, Player, Net, and utility classes.
Import this into game.py.
"""

import asyncio
import json
import math
import random
import threading
from collections import deque

import pygame
import websockets

# ─────────────────────── Screen / Physics ──────────────────────────
SW, SH = 1280, 720
TILE = 32
FPS = 60
GRAVITY = 0.55
JUMP_VEL = -13.5
MOVE_SPD = 4.2
MAX_FALL = 18
REACH = TILE * 5.5
NET_HZ = 20
ATTACK_DMG = 20
ATTACK_RANGE = TILE * 3.5
ATTACK_CD = 0.5
REGEN_RATE = 2
REGEN_DELAY = 5.0

# ─────────────────── Server / Network Config ────────────────────────
CLIENT_VERSION = "1.0.0"


# Switch between these by changing DEPLOY_MODE
DEPLOY_MODE = "cloud"  # "local" | "cloud"

_SERVERS = {
    "local": "ws://127.0.0.1:8765",  # LAN / local machine
    "cloud": "wss://server-game-production.up.railway.app",  # Cloud (replace with your URL)
}
DEFAULT_SERVER = _SERVERS[DEPLOY_MODE]

SERVER_HOST = "0.0.0.0"  # listen on all interfaces (required for cloud/LAN)
SERVER_PORT = 8765  # WebSocket port (change if already in use)

# ──────────────────────── Block IDs ────────────────────────────────
B_AIR, B_GRASS, B_DIRT, B_STONE, B_SAND = 0, 1, 2, 3, 4
B_WOOD, B_LEAVES, B_COAL, B_IRON, B_GOLD = 5, 6, 7, 8, 9
B_BEDROCK, B_WATER, B_GRAVEL = 10, 11, 12
B_PLANK = 13  # crafted from Wood
B_GLASS = 15  # crafted from Sand
B_BRICK = 16  # crafted from Stone

# ──────────────────────── Tool Item IDs (not placeable) ────────────
T_WOOD_PICK = 20
T_STONE_PICK = 21
T_IRON_PICK = 22
T_GOLD_PICK = 23
T_WOOD_AXE = 24
T_IRON_AXE = 25
T_WOOD_SHOVEL = 26
T_IRON_SHOVEL = 27
T_WOOD_SWORD = 28
T_IRON_SWORD = 29

TOOL_IDS = {
    T_WOOD_PICK,
    T_STONE_PICK,
    T_IRON_PICK,
    T_GOLD_PICK,
    T_WOOD_AXE,
    T_IRON_AXE,
    T_WOOD_SHOVEL,
    T_IRON_SHOVEL,
    T_WOOD_SWORD,
    T_IRON_SWORD,
}

# Block categories for tool effectiveness
_STONE_BLOCKS = {B_STONE, B_COAL, B_IRON, B_GOLD, B_BRICK}
_WOOD_BLOCKS = {B_WOOD, B_LEAVES, B_PLANK}
_DIRT_BLOCKS = {B_DIRT, B_GRASS, B_SAND, B_GRAVEL}

# Mining speed multiplier per tool per block  (divides MINE_SEC time)
TOOL_SPEED: dict = {
    T_WOOD_PICK: {b: 2.0 for b in _STONE_BLOCKS},
    T_STONE_PICK: {b: 3.5 for b in _STONE_BLOCKS},
    T_IRON_PICK: {b: 5.0 for b in _STONE_BLOCKS},
    T_GOLD_PICK: {b: 8.0 for b in _STONE_BLOCKS},
    T_WOOD_AXE: {b: 2.0 for b in _WOOD_BLOCKS},
    T_IRON_AXE: {b: 4.0 for b in _WOOD_BLOCKS},
    T_WOOD_SHOVEL: {b: 2.0 for b in _DIRT_BLOCKS},
    T_IRON_SHOVEL: {b: 4.0 for b in _DIRT_BLOCKS},
}
# Swords deal bonus attack damage on top of the base ATTACK_DMG
TOOL_BONUS_DMG = {
    T_WOOD_SWORD: 10,  # 30 total
    T_IRON_SWORD: 20,  # 40 total
}

BLOCK_NAMES = {
    B_AIR: "Air",
    B_GRASS: "Grass",
    B_DIRT: "Dirt",
    B_STONE: "Stone",
    B_SAND: "Sand",
    B_WOOD: "Wood",
    B_LEAVES: "Leaves",
    B_COAL: "Coal Ore",
    B_IRON: "Iron Ore",
    B_GOLD: "Gold Ore",
    B_BEDROCK: "Bedrock",
    B_WATER: "Water",
    B_GRAVEL: "Gravel",
    B_PLANK: "Wood Plank",
    B_GLASS: "Glass",
    B_BRICK: "Brick",
    # Tools
    T_WOOD_PICK: "Wood Pickaxe",
    T_STONE_PICK: "Stone Pickaxe",
    T_IRON_PICK: "Iron Pickaxe",
    T_GOLD_PICK: "Gold Pickaxe",
    T_WOOD_AXE: "Wood Axe",
    T_IRON_AXE: "Iron Axe",
    T_WOOD_SHOVEL: "Wood Shovel",
    T_IRON_SHOVEL: "Iron Shovel",
    T_WOOD_SWORD: "Wood Sword",
    T_IRON_SWORD: "Iron Sword",
}

MINE_SEC = {
    B_GRASS: 0.5,
    B_DIRT: 0.5,
    B_SAND: 0.4,
    B_WOOD: 1.0,
    B_LEAVES: 0.25,
    B_STONE: 1.5,
    B_COAL: 2.0,
    B_IRON: 2.5,
    B_GOLD: 3.0,
    B_WATER: 0.3,
    B_GRAVEL: 0.6,
    B_BEDROCK: 9999,
    B_PLANK: 0.8,
    B_GLASS: 0.8,
    B_BRICK: 2.0,
}

# Block colours: (main, shade, highlight, extra)
BCOLORS = {
    B_GRASS: ((72, 120, 56), (58, 90, 42), (110, 160, 80), None),
    B_DIRT: ((130, 90, 60), (110, 74, 48), (155, 110, 78), None),
    B_STONE: ((130, 133, 134), (105, 108, 110), (158, 162, 163), None),
    B_SAND: ((215, 200, 140), (195, 182, 118), (235, 220, 165), None),
    B_WOOD: ((100, 74, 48), (80, 58, 36), (124, 94, 65), (80, 58, 36)),
    B_LEAVES: ((48, 96, 40), (36, 76, 30), (68, 120, 56), (36, 76, 30)),
    B_COAL: ((70, 70, 70), (50, 50, 50), (95, 95, 95), (30, 30, 30)),
    B_IRON: ((170, 140, 115), (145, 118, 95), (195, 162, 135), (200, 160, 120)),
    B_GOLD: ((200, 170, 40), (168, 140, 28), (225, 195, 60), (255, 215, 0)),
    B_BEDROCK: ((22, 22, 28), (15, 15, 20), (38, 38, 45), None),
    B_WATER: ((40, 80, 200), (30, 65, 175), (70, 110, 220), None),
    B_GRAVEL: ((145, 140, 130), (120, 115, 106), (165, 160, 150), (115, 110, 100)),
    # Craftable
    B_PLANK: ((180, 130, 80), (150, 105, 60), (210, 160, 100), None),
    B_GLASS: ((180, 220, 240), (160, 200, 220), (220, 240, 255), None),
    B_BRICK: ((160, 80, 60), (130, 60, 45), (190, 105, 80), (100, 45, 35)),
}

SOLID = {
    B_GRASS,
    B_DIRT,
    B_STONE,
    B_SAND,
    B_WOOD,
    B_LEAVES,
    B_COAL,
    B_IRON,
    B_GOLD,
    B_BEDROCK,
    B_GRAVEL,
    B_PLANK,
    B_GLASS,
    B_BRICK,
}

# Crafting recipes – must match server CRAFT_RECIPES
# (display_name, result_id, result_count, {ingredient_id: count})
CRAFT_RECIPES = [
    # ── Blocks ───────────────────────────────────────────────────────
    ("Wood Planks", B_PLANK, 4, {B_WOOD: 1}),
    ("Glass", B_GLASS, 2, {B_SAND: 2}),
    ("Brick", B_BRICK, 2, {B_STONE: 4}),
    ("Sand", B_SAND, 2, {B_DIRT: 1, B_GRAVEL: 1}),
    # ── Pickaxes ─────────────────────────────────────────────────────
    ("Wood Pickaxe", T_WOOD_PICK, 1, {B_PLANK: 3, B_WOOD: 2}),
    ("Stone Pickaxe", T_STONE_PICK, 1, {B_STONE: 3, B_PLANK: 2}),
    ("Iron Pickaxe", T_IRON_PICK, 1, {B_IRON: 3, B_PLANK: 2}),
    ("Gold Pickaxe", T_GOLD_PICK, 1, {B_GOLD: 3, B_PLANK: 2}),
    # ── Axes ─────────────────────────────────────────────────────────
    ("Wood Axe", T_WOOD_AXE, 1, {B_PLANK: 3, B_WOOD: 2}),
    ("Iron Axe", T_IRON_AXE, 1, {B_IRON: 3, B_PLANK: 2}),
    # ── Shovels ──────────────────────────────────────────────────────
    ("Wood Shovel", T_WOOD_SHOVEL, 1, {B_PLANK: 1, B_WOOD: 2}),
    ("Iron Shovel", T_IRON_SHOVEL, 1, {B_IRON: 1, B_PLANK: 2}),
    # ── Swords ───────────────────────────────────────────────────────
    ("Wood Sword", T_WOOD_SWORD, 1, {B_PLANK: 2, B_WOOD: 1}),
    ("Iron Sword", T_IRON_SWORD, 1, {B_IRON: 2, B_PLANK: 1}),
]


# Resolution
def update_screen_size(w, h):
    global SW, SH
    SW, SH = w, h
    _gen_stars()


# ──────────────────── Block Surface Cache ──────────────────────────
_BSURF: dict = {}


def _make_block_surf(bid: int):
    if bid == B_AIR:
        return None
    if bid in _BSURF:
        return _BSURF[bid]

    s = pygame.Surface((TILE, TILE))
    cols = BCOLORS.get(bid)
    if not cols:
        s.fill((200, 0, 200))
        _BSURF[bid] = s
        return s

    main, shade, hi, extra = cols
    s.fill(main)

    if bid == B_GRASS:
        pygame.draw.rect(s, (80, 140, 52), (0, 0, TILE, 5))
        pygame.draw.rect(s, shade, (0, 5, TILE, TILE - 5))

    elif bid in (B_STONE, B_GRAVEL):
        pygame.draw.line(s, shade, (0, TILE // 2), (TILE // 2, TILE // 3), 1)
        pygame.draw.line(s, shade, (TILE // 2, TILE // 3), (TILE, TILE * 2 // 3), 1)
        pygame.draw.line(s, hi, (2, 2), (8, 10), 1)

    elif bid == B_WOOD:
        for xg in range(4, TILE, 8):
            pygame.draw.line(s, shade, (xg, 0), (xg, TILE), 1)
        pygame.draw.rect(s, hi, (0, 0, 3, TILE))

    elif bid == B_LEAVES:
        for k in range(12):
            rx = (hash((k * 7 + bid * 3)) & 0xFF) % TILE
            ry = (hash((k * 13 + bid * 5)) & 0xFF) % TILE
            pygame.draw.circle(s, hi if k % 3 == 0 else shade, (rx, ry), 3)

    elif bid in (B_COAL, B_IRON, B_GOLD):
        nugget = extra or hi
        positions = [(7, 7), (20, 14), (13, 22), (24, 6), (5, 24)]
        for nx, ny in positions:
            pygame.draw.ellipse(s, nugget, (nx, ny, 5, 4))
            pygame.draw.ellipse(s, hi, (nx + 1, ny + 1, 3, 2))

    elif bid == B_WATER:
        pygame.draw.rect(s, hi, (0, 0, TILE, 6))
        for wx in range(0, TILE, 8):
            pygame.draw.arc(s, hi, (wx - 4, 1, 10, 5), 0, math.pi, 2)

    elif bid == B_PLANK:
        # Horizontal wood grain
        for yg in range(6, TILE, 8):
            pygame.draw.line(s, shade, (0, yg), (TILE, yg), 1)
        pygame.draw.rect(s, hi, (0, 0, TILE, 3))
        pygame.draw.line(s, shade, (TILE // 2, 0), (TILE // 2, TILE), 1)

    elif bid == B_GLASS:
        # Paned glass look
        hw, hh = TILE // 2, TILE // 2
        pygame.draw.rect(s, hi, (1, 1, hw - 2, hh - 2))
        pygame.draw.rect(s, shade, (hw + 1, 1, hw - 2, hh - 2))
        pygame.draw.rect(s, shade, (1, hh + 1, hw - 2, hh - 2))
        pygame.draw.rect(s, hi, (hw + 1, hh + 1, hw - 2, hh - 2))
        pygame.draw.line(s, (220, 240, 255), (hw, 0), (hw, TILE), 2)
        pygame.draw.line(s, (220, 240, 255), (0, hh), (TILE, hh), 2)

    elif bid == B_BRICK:
        # Brick-pattern mortar
        pygame.draw.rect(s, shade, (0, TILE // 2 - 1, TILE, 2))
        for xi in [TILE // 4, 3 * TILE // 4]:
            pygame.draw.line(s, shade, (xi, 0), (xi, TILE // 2 - 1), 2)
        for xi in [TILE // 2]:
            pygame.draw.line(s, shade, (xi, TILE // 2 + 1), (xi, TILE), 2)
        # Highlight face
        pygame.draw.rect(s, hi, (2, 2, TILE // 4 - 4, TILE // 2 - 5))
        pygame.draw.rect(
            s, hi, (TILE // 2 + 2, TILE // 2 + 3, TILE // 4 - 4, TILE // 2 - 5)
        )

    # Universal edge shading
    pygame.draw.line(s, hi, (0, 0), (TILE - 1, 0), 1)
    pygame.draw.line(s, hi, (0, 0), (0, TILE - 1), 1)
    pygame.draw.line(s, shade, (TILE - 1, 0), (TILE - 1, TILE - 1), 1)
    pygame.draw.line(s, shade, (0, TILE - 1), (TILE - 1, TILE - 1), 1)

    _BSURF[bid] = s
    return s


# ──────────────────── Tool Surface Renderer ────────────────────────
_TOOL_COLORS = {
    "wood": (165, 115, 55),
    "stone": (128, 130, 132),
    "iron": (200, 205, 215),
    "gold": (245, 200, 35),
}


def _make_tool_surf(tid: int):
    if tid in _BSURF:
        return _BSURF[tid]

    s = pygame.Surface((TILE, TILE), pygame.SRCALPHA)

    if tid == T_WOOD_PICK:
        _draw_pickaxe(s, _TOOL_COLORS["wood"])
    elif tid == T_STONE_PICK:
        _draw_pickaxe(s, _TOOL_COLORS["stone"])
    elif tid == T_IRON_PICK:
        _draw_pickaxe(s, _TOOL_COLORS["iron"])
    elif tid == T_GOLD_PICK:
        _draw_pickaxe(s, _TOOL_COLORS["gold"])
    elif tid == T_WOOD_AXE:
        _draw_axe(s, _TOOL_COLORS["wood"])
    elif tid == T_IRON_AXE:
        _draw_axe(s, _TOOL_COLORS["iron"])
    elif tid == T_WOOD_SHOVEL:
        _draw_shovel(s, _TOOL_COLORS["wood"])
    elif tid == T_IRON_SHOVEL:
        _draw_shovel(s, _TOOL_COLORS["iron"])
    elif tid == T_WOOD_SWORD:
        _draw_sword(s, _TOOL_COLORS["wood"])
    elif tid == T_IRON_SWORD:
        _draw_sword(s, _TOOL_COLORS["iron"])

    _BSURF[tid] = s
    return s


def _draw_pickaxe(surf, head_col):
    hc = (140, 90, 42)  # handle wood
    # Handle: diagonal thick line bottom-right to mid
    pygame.draw.line(surf, hc, (26, 27), (10, 11), 3)
    pygame.draw.line(
        surf, (hc[0] - 30, hc[1] - 20, max(0, hc[2] - 10)), (27, 26), (11, 10), 1
    )
    # Pick head: horizontal bar
    pygame.draw.line(surf, head_col, (3, 10), (22, 5), 5)
    # Front tip (top-right)
    pygame.draw.line(surf, head_col, (20, 3), (28, 1), 3)
    # Rear tip (bottom-left)
    pygame.draw.line(surf, head_col, (5, 12), (1, 17), 3)
    # Highlight
    hi = tuple(min(255, c + 55) for c in head_col)
    pygame.draw.line(surf, hi, (5, 9), (20, 4), 1)


def _draw_axe(surf, head_col):
    hc = (140, 90, 42)
    # Handle diagonal
    pygame.draw.line(surf, hc, (24, 28), (8, 8), 3)
    # Axe head polygon (top area)
    pts = [(6, 4), (18, 2), (20, 12), (10, 16)]
    pygame.draw.polygon(surf, head_col, pts)
    dk = tuple(max(0, c - 40) for c in head_col)
    pygame.draw.polygon(surf, dk, pts, 1)
    # Cutting edge highlight
    hi = tuple(min(255, c + 60) for c in head_col)
    pygame.draw.line(surf, hi, (18, 2), (20, 12), 2)


def _draw_shovel(surf, blade_col):
    hc = (140, 90, 42)
    # Handle
    pygame.draw.line(surf, hc, (16, 28), (16, 12), 3)
    # T-grip at top
    pygame.draw.line(surf, hc, (10, 5), (22, 5), 3)
    pygame.draw.line(surf, hc, (16, 5), (16, 9), 3)
    # Blade rectangle
    pygame.draw.rect(surf, blade_col, (10, 10, 12, 10))
    # Blade point (triangle)
    pygame.draw.polygon(surf, blade_col, [(10, 20), (22, 20), (16, 27)])
    dk = tuple(max(0, c - 35) for c in blade_col)
    pygame.draw.rect(surf, dk, (10, 10, 12, 10), 1)
    hi = tuple(min(255, c + 55) for c in blade_col)
    pygame.draw.line(surf, hi, (11, 11), (11, 19), 1)


def _draw_sword(surf, blade_col):
    hc = (140, 90, 42)
    dk = tuple(max(0, c - 35) for c in blade_col)
    hi = tuple(min(255, c + 65) for c in blade_col)
    # Pommel
    pygame.draw.rect(surf, hc, (13, 24, 6, 5))
    # Handle
    pygame.draw.rect(surf, hc, (14, 14, 4, 11))
    # Guard (cross)
    pygame.draw.rect(surf, (100, 75, 35), (8, 13, 16, 3))
    pygame.draw.rect(surf, (130, 100, 50), (8, 13, 16, 3), 1)
    # Blade (tapered polygon)
    blade = [(13, 1), (19, 1), (18, 13), (14, 13)]
    pygame.draw.polygon(surf, blade_col, blade)
    pygame.draw.polygon(surf, dk, blade, 1)
    # Tip
    pygame.draw.polygon(surf, blade_col, [(14, 0), (18, 0), (16, 0)])
    # Highlight
    pygame.draw.line(surf, hi, (14, 2), (15, 12), 1)


def get_item_surf(item_id: int):
    """Return the display surface for any item — block or tool."""
    if item_id in TOOL_IDS:
        return _make_tool_surf(item_id)
    return _make_block_surf(item_id)


# ──────────────────────── Particles ────────────────────────────────
class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "color", "size")

    def __init__(self, x, y, color):
        self.x, self.y = x, y
        self.vx = random.uniform(-3, 3)
        self.vy = random.uniform(-5, -1)
        self.life = self.max_life = random.uniform(0.3, 0.7)
        self.color = color
        self.size = random.randint(3, 7)

    def update(self, dt):
        self.vy += GRAVITY * dt * 30
        self.x += self.vx
        self.y += self.vy
        self.life -= dt

    def draw(self, surf, cx, cy):
        a = self.life / self.max_life
        c = tuple(max(0, min(255, int(v * a))) for v in self.color)
        sz = max(1, int(self.size * a))
        pygame.draw.rect(
            surf, c, (self.x - cx - sz // 2, self.y - cy - sz // 2, sz, sz)
        )


# ─────────────────── Damage Numbers ────────────────────────────────
class DamageNumber:
    __slots__ = ("x", "y", "vy", "life", "max_life", "text", "color")

    def __init__(self, x, y, amount, color=(255, 60, 60)):
        self.x, self.y = float(x), float(y)
        self.vy = -2.2
        self.life = self.max_life = 1.1
        self.text = f"-{amount}"
        self.color = color

    def update(self, dt):
        self.y += self.vy
        self.vy *= 0.92
        self.life -= dt

    def draw(self, surf, cx, cy, font):
        a = max(0, min(255, int(self.life / self.max_life * 255)))
        col = self.color
        ts = font.render(self.text, True, col)
        ts.set_alpha(a)
        surf.blit(
            ts, (self.x - cx - ts.get_width() // 2, self.y - cy - ts.get_height() // 2)
        )


# ──────────────────────── World ─────────────────────────────────────
class World:
    def __init__(self, data: bytes, ww: int, wh: int):
        self.ww, self.wh = ww, wh
        self.tiles = bytearray(data)

    def get(self, bx, by):
        if 0 <= bx < self.ww and 0 <= by < self.wh:
            return self.tiles[by * self.ww + bx]
        return B_BEDROCK if by >= self.wh else B_AIR

    def set(self, bx, by, b):
        if 0 <= bx < self.ww and 0 <= by < self.wh:
            self.tiles[by * self.ww + bx] = b

    def solid(self, bx, by):
        return self.get(bx, by) in SOLID

    def draw(self, surf, cx, cy, light=1.0):
        sx0 = max(0, int(cx // TILE))
        sx1 = min(self.ww, int((cx + SW) // TILE) + 2)
        sy0 = max(0, int(cy // TILE))
        sy1 = min(self.wh, int((cy + SH) // TILE) + 2)

        dark_surf = None
        if light < 0.99:
            dark_surf = pygame.Surface((TILE, TILE), pygame.SRCALPHA)
            dark_surf.fill((0, 0, 30, int((1 - light) * 180)))

        for by in range(sy0, sy1):
            for bx in range(sx0, sx1):
                b = self.get(bx, by)
                if b == B_AIR:
                    continue
                bsurf = _make_block_surf(b)
                if bsurf is None:
                    continue
                px, py = bx * TILE - cx, by * TILE - cy
                surf.blit(bsurf, (px, py))
                if dark_surf:
                    surf.blit(dark_surf, (px, py))


# ──────────────────────── Player ────────────────────────────────────
class Player:
    W, H = 22, 44

    def __init__(self, x, y, color="#FF6B6B", name="Miner"):
        self.x, self.y = float(x), float(y)
        self.vx = self.vy = 0.0
        self.on_ground = False
        self.facing = "right"
        self.color = _hex(color)
        self.name = name[:16]
        self.health = 100
        self.anim = 0.0
        self.hotbar = [None] * 9
        self.sel = 0

    @property
    def cx(self):
        return self.x + self.W / 2

    @property
    def cy(self):
        return self.y + self.H / 2

    def update_physics(self, world: World, keys):
        self.vx = 0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            self.vx, self.facing = -MOVE_SPD, "left"
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            self.vx, self.facing = MOVE_SPD, "right"
        if self.vx != 0:
            self.anim += 0.18
        if (
            keys[pygame.K_w] or keys[pygame.K_UP] or keys[pygame.K_SPACE]
        ) and self.on_ground:
            self.vy = JUMP_VEL
        self.vy = min(self.vy + GRAVITY, MAX_FALL)
        self._move(world, self.vx, 0)
        self._move(world, 0, self.vy)
        if self.y > world.wh * TILE + 200:
            self.x, self.y, self.vy = world.ww * TILE // 2, -200.0, 0.0

    def _move(self, world, dx, dy):
        self.x += dx
        if dx != 0:
            self._resolve(world, dx, 0)
        self.y += dy
        if dy != 0:
            self._resolve(world, 0, dy)

    def _resolve(self, world, dx, dy):
        bx0 = int(self.x // TILE)
        by0 = int(self.y // TILE)
        bx1 = int((self.x + self.W - 1) // TILE)
        by1 = int((self.y + self.H - 1) // TILE)
        self.on_ground = False
        for by in range(by0, by1 + 1):
            for bx in range(bx0, bx1 + 1):
                if not world.solid(bx, by):
                    continue
                bl = pygame.Rect(bx * TILE, by * TILE, TILE, TILE)
                me = pygame.Rect(int(self.x), int(self.y), self.W, self.H)
                if not me.colliderect(bl):
                    continue
                if dy > 0:
                    self.y = bl.top - self.H
                    self.vy = 0
                    self.on_ground = True
                elif dy < 0:
                    self.y = bl.bottom
                    self.vy = 0
                elif dx > 0:
                    self.x = bl.left - self.W
                    self.vx = 0
                elif dx < 0:
                    self.x = bl.right
                    self.vx = 0

    def draw(self, surf, cx, cy, local=False, font_sm=None, shake=(0, 0)):
        px = self.x - cx + shake[0]
        py = self.y - cy + shake[1]
        c = self.color
        dark = tuple(max(0, v - 50) for v in c)
        lite = tuple(min(255, v + 60) for v in c)

        offset = int(math.sin(self.anim) * 5) if self.vx != 0 else 0
        lw = (self.W - 6) // 2
        pygame.draw.rect(surf, dark, (px + 3, py + self.H - 14 + offset, lw, 14))
        pygame.draw.rect(
            surf, dark, (px + 3 + lw + 2, py + self.H - 14 - offset, lw, 14)
        )
        pygame.draw.rect(
            surf, (0, 0, 0), (px + 3, py + self.H - 14 + offset, lw, 14), 1
        )
        pygame.draw.rect(
            surf, (0, 0, 0), (px + 3 + lw + 2, py + self.H - 14 - offset, lw, 14), 1
        )

        body = pygame.Rect(px + 2, py + 16, self.W - 4, self.H - 30)
        pygame.draw.rect(surf, c, body)
        pygame.draw.rect(surf, (0, 0, 0), body, 1)

        head = pygame.Rect(px + 1, py, self.W - 2, 18)
        pygame.draw.rect(surf, lite, head)
        pygame.draw.rect(surf, (0, 0, 0), head, 1)
        pygame.draw.rect(surf, dark, (px + 1, py, self.W - 2, 4))

        ex = px + (self.W - 8) if self.facing == "right" else px + 4
        pygame.draw.circle(surf, (255, 255, 255), (int(ex + 2), int(py + 9)), 4)
        ep = 1 if self.facing == "right" else -1
        pygame.draw.circle(surf, (30, 30, 30), (int(ex + 2 + ep), int(py + 9)), 2)

        if not local and font_sm:
            ns = font_sm.render(self.name, True, (255, 255, 255))
            nb = pygame.Surface(
                (ns.get_width() + 6, ns.get_height() + 4), pygame.SRCALPHA
            )
            nb.fill((0, 0, 0, 150))
            surf.blit(nb, (px + self.W // 2 - nb.get_width() // 2, py - 20))
            surf.blit(ns, (px + self.W // 2 - ns.get_width() // 2, py - 18))
            # Remote health bar
            hw_ = ns.get_width() + 6
            hb_x = px + self.W // 2 - hw_ // 2
            hb_y = py - 8
            pygame.draw.rect(surf, (60, 0, 0), (hb_x, hb_y, hw_, 4))
            fill = int(hw_ * max(0, self.health) / 100)
            hc = (
                (0, 200, 80)
                if self.health > 60
                else (220, 160, 0)
                if self.health > 30
                else (220, 50, 30)
            )
            if fill > 0:
                pygame.draw.rect(surf, hc, (hb_x, hb_y, fill, 4))

        if local:
            hb_w = self.W + 4
            hb_x, hb_y = px - 2, py - 10
            pygame.draw.rect(surf, (60, 0, 0), (hb_x, hb_y, hb_w, 5))
            hw = int(hb_w * max(0, self.health) / 100)
            hcol = (
                (0, 200, 80)
                if self.health > 60
                else (220, 160, 0)
                if self.health > 30
                else (220, 50, 30)
            )
            pygame.draw.rect(surf, hcol, (hb_x, hb_y, hw, 5))


# ──────────────────── Network Manager ──────────────────────────────
class Net:
    def __init__(self):
        self.loop = None
        self.thread = None
        self.ws = None
        self.inbox = deque()
        self.outbox = deque()
        self.alive = False
        self.error = ""

    def connect(self, url):
        self.alive = False
        self.error = ""
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, args=(url,), daemon=True)
        self.thread.start()

    def _run(self, url):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main(url))

    async def _main(self, url):
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                self.ws = ws
                self.alive = True
                send_task = asyncio.create_task(self._sender())
                async for raw in ws:
                    self.inbox.append(json.loads(raw))
                send_task.cancel()
        except Exception as e:
            self.error = str(e)
        finally:
            self.alive = False

    async def _sender(self):
        while True:
            while self.outbox:
                await self.ws.send(json.dumps(self.outbox.popleft()))
            await asyncio.sleep(0.002)

    def disconnect(self):
        """Close the WebSocket cleanly so the server gets a proper disconnect."""
        self.alive = False
        if self.ws and self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.ws.close(), self.loop)

    def send(self, msg):
        self.outbox.append(msg)

    def poll(self):
        # Use popleft() in a loop instead of list()+clear() — the latter has a
        # thread-race where the async receiver thread can append a new message
        # between the snapshot and the clear, silently dropping it.
        # deque.popleft() is atomic under CPython's GIL so this is safe.
        out = []
        while self.inbox:
            out.append(self.inbox.popleft())
        return out


# ─────────────────────── Helpers ────────────────────────────────────
def _hex(h):
    if isinstance(h, tuple):
        return h
    h = h.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return (200, 200, 200)


def _sky(t):
    phases = [
        (0, (10, 10, 35)),
        (5000, (10, 10, 35)),
        (6500, (180, 120, 80)),
        (8000, (100, 160, 220)),
        (12000, (120, 185, 240)),
        (16000, (120, 185, 240)),
        (17500, (200, 130, 80)),
        (19000, (15, 12, 40)),
        (24000, (10, 10, 35)),
    ]
    for i in range(len(phases) - 1):
        t0, c0 = phases[i]
        t1, c1 = phases[i + 1]
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0)
            f = f * f * (3 - 2 * f)
            return tuple(int(a + (b - a) * f) for a, b in zip(c0, c1))
    return (10, 10, 35)


def _light(t):
    phases = [
        (0, 0.05),
        (5500, 0.05),
        (7000, 0.7),
        (9000, 1.0),
        (15000, 1.0),
        (17000, 0.6),
        (19000, 0.05),
        (24000, 0.05),
    ]
    for i in range(len(phases) - 1):
        t0, v0 = phases[i]
        t1, v1 = phases[i + 1]
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * f
    return 0.05


# ──────────────────── Cloud System ──────────────────────────────────
class Cloud:
    def __init__(self, world_w):
        self.x = random.uniform(0, world_w * TILE)
        self.y = random.uniform(20, 200)
        self.w = random.randint(120, 300)
        self.h = random.randint(30, 70)
        self.spd = random.uniform(0.2, 0.8)
        self.world_w = world_w

    def update(self, dt):
        self.x += self.spd
        if self.x > self.world_w * TILE + self.w:
            self.x = -self.w

    def draw(self, surf, cx, cy, alpha):
        if alpha < 0.02:
            return
        sx = self.x - cx
        sy = self.y - cy
        a = int(min(180, alpha * 180))
        cs = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        pygame.draw.ellipse(cs, (255, 255, 255, a), (0, 0, self.w, self.h))
        pygame.draw.ellipse(
            cs, (255, 255, 255, a), (self.w // 4, -self.h // 2, self.w // 2, self.h)
        )
        surf.blit(cs, (sx, sy))


# ─────────────────── Star Field & Sky Objects ────────────────────────
_STARS = []


def _gen_stars():
    global _STARS
    _STARS = [
        (random.randint(0, SW), random.randint(0, SH // 2), random.randint(1, 3))
        for _ in range(140)
    ]


def draw_stars(surf, light):
    if light > 0.4:
        return
    a = min(255, int((0.4 - light) / 0.4 * 220))
    for sx, sy, sz in _STARS:
        pygame.draw.circle(surf, (a, a, a + 30), (sx, sy), sz)


def draw_sun_moon(surf, t):
    angle = (t / 24000) * math.tau - math.pi / 2
    sx = int(SW / 2 + math.cos(angle) * SW * 0.55)
    sy = int(SH / 2 + math.sin(angle) * SH * 0.85)
    if 0 < t / 24000 < 0.5:
        pygame.draw.circle(surf, (255, 220, 80), (sx, sy), 28)
        pygame.draw.circle(surf, (255, 245, 150), (sx, sy), 22)
        for i in range(8):
            a2 = i * math.pi / 4 + t / 1000
            x1, y1 = sx + int(math.cos(a2) * 32), sy + int(math.sin(a2) * 32)
            x2, y2 = sx + int(math.cos(a2) * 44), sy + int(math.sin(a2) * 44)
            pygame.draw.line(surf, (255, 200, 60), (x1, y1), (x2, y2), 3)
    mx = int(SW / 2 + math.cos(angle + math.pi) * SW * 0.55)
    my = int(SH / 2 + math.sin(angle + math.pi) * SH * 0.85)
    if t < 6000 or t > 18000:
        pygame.draw.circle(surf, (225, 225, 245), (mx, my), 20)
        pygame.draw.circle(surf, (195, 195, 215), (mx - 5, my - 4), 7)
        pygame.draw.circle(surf, (195, 195, 215), (mx + 6, my + 5), 5)


# Initialise the star field once at import time (update_screen_size regenerates it
# on resize/fullscreen; without this call _STARS stays empty and stars never appear).
_gen_stars()
