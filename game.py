"""
BlockWorld – Main Game Client
==============================
Controls:
  WASD / Arrow Keys  – Move / Jump
  Space              – Jump
  Left Click         – Mine (hold) / Attack (click player)
  Right Click        – Place selected block
  F                  – Melee swing (hits nearby players)
  E                  – Inventory & Crafting screen
  1-9 / Scroll Wheel – Select hotbar slot
  T                  – Open chat
  Tab                – Player list
  F3                 – Debug overlay
  Esc                – Pause / Back to menu
  R / Space          – Respawn (on death screen)
"""

import base64
import math
import random
import sys
import time
from collections import deque

import pygame

from game_core import (
    ATTACK_CD,
    ATTACK_RANGE,
    B_AIR,
    B_BEDROCK,
    BCOLORS,
    BLOCK_NAMES,
    CRAFT_RECIPES,
    DEFAULT_SERVER,
    FPS,
    MINE_SEC,
    NET_HZ,
    REACH,
    REGEN_DELAY,
    REGEN_RATE,
    SH,
    SW,
    TILE,
    TOOL_SPEED,
    Cloud,
    DamageNumber,
    Net,
    Particle,
    Player,
    World,
    _hex,
    _light,
    _make_block_surf,
    _sky,
    draw_stars,
    draw_sun_moon,
    get_item_surf,
)

# ──────────────────── Inventory UI Constants ───────────────────────
VERSION_TIMEOUT = 5.0  # seconds to wait for server version_ack
SLOT = 44  # slot size in pixels
SPAD = 4  # slot padding
SCOLS = 9  # slots per row


# ──────────────────── Text helpers ────────────────────────────────
def _delete_word(s: str) -> str:
    """Ctrl+Backspace — delete the last word (space-delimited)."""
    s = s.rstrip(" ")  # strip trailing spaces first
    if not s:
        return ""
    idx = s.rfind(" ")
    return s[: idx + 1] if idx >= 0 else ""


# ────────────────────── Main Game ─────────────────────────────────
class Game:
    def __init__(self):
        pygame.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode(
            (SW, SH),
            pygame.RESIZABLE,  # allows dragging to resize
        )
        self.fullscreen = False
        pygame.display.set_caption("BlockWorld – Multiplayer")
        pygame.scrap.init()  # clipboard — must be called after set_mode
        pygame.key.set_repeat(
            400, 50
        )  # hold-backspace / hold-delete / key repeat in text boxes
        self.clock = pygame.time.Clock()

        self.font_sm = pygame.font.SysFont("Arial", 12)
        self.font_md = pygame.font.SysFont("Arial", 16)
        self.font_lg = pygame.font.SysFont("Arial", 26, bold=True)
        self.font_xl = pygame.font.SysFont("Arial", 52, bold=True)
        self.version_ok = False
        self.version_sent = False
        self.version_check_t = 0.0
        self.state = "menu"
        self.net = Net()
        self.world = None
        self.me = None
        self.pid = None
        self.others = {}
        self.gtime = 6000
        self.particles = []
        self.clouds = []
        self.chat_log = deque(maxlen=20)
        self.chat_input = ""
        self.chat_open = False
        self.show_list = False
        self.debug = False
        self.cam_x = self.cam_y = 0.0
        self.mine_target = None
        self.mine_start = 0.0
        self.mine_prog = 0.0
        self.mining_held = (
            False  # True only when LMB was pressed for mining (not attack)
        )
        self.last_move_t = 0.0
        self.join_sent = False

        # Menu
        self.menu_name = "Miner"
        self.menu_server = DEFAULT_SERVER
        self.menu_focus = "name"
        self.menu_err = ""
        self._pending_name = "Miner"

        # Combat
        self.attack_cd = 0.0
        self.attack_swing = 0.0
        self.last_hit_t = -999.0
        self.hit_flash = 0.0
        self.dmg_numbers = []
        self.dead = False
        self.killed_by = ""
        self.spawn_flash = 0.0
        self.respawn_btn = pygame.Rect(SW // 2 - 120, SH // 2 + 80, 240, 54)

        # Screen shake
        self.shake_t = 0.0
        self.shake_amp = 0

        # Inventory / Crafting
        self.craft_scroll = 0
        self.craft_search = ""
        self.craft_search_active = False
        self.inv_layout_dirty = True
        self.inv_open = False
        self.inv_slots = [None] * 18  # mirrors server inventory
        self.inv_held = None  # item currently being dragged
        self.inv_held_src = None  # ('h'|'i', index)
        self.inv_rects = []
        self.hotbar_inv_rects = []
        self.craft_btns = []

        # Player
        self.other_targets = {}

    # ─────── Main Loop ────────────────────────────────────
    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                self.handle_event(ev)
            self.update(dt)
            self.draw()
            pygame.display.flip()

    # ─────── Event Dispatch ───────────────────────────────
    def handle_event(self, ev):
        if ev.type == pygame.VIDEORESIZE:
            # Pass ev.w/ev.h as a hint, but trust the actual surface size —
            # left/top-edge drags can report the wrong values in ev on some
            # pygame 2 builds.
            self.screen = pygame.display.set_mode((ev.w, ev.h), pygame.RESIZABLE)
            w, h = self.screen.get_size()
            import game_core as _gc

            _gc.update_screen_size(w, h)
            # CRITICAL: game.py imported SW/SH as plain ints at module load.
            # We must re-bind the module-level names here so that every
            # draw-time calculation (SW // 2, SH - 30, …) uses the new size.
            global SW, SH
            SW, SH = w, h
            self.inv_layout_dirty = True
            self.respawn_btn = pygame.Rect(SW // 2 - 120, SH // 2 + 80, 240, 54)

        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_F11:
                self._toggle_fullscreen()
        if self.state == "menu":
            self._ev_menu(ev)
        elif self.state == "playing":
            if self.dead:
                self._ev_death(ev)
            elif self.inv_open:
                self._ev_inventory(ev)
            else:
                self._ev_game(ev)

    # ─────── Menu Events ──────────────────────────────────
    def _ev_menu(self, ev):
        if ev.type == pygame.MOUSEBUTTONDOWN:
            mx, my = ev.pos
            nr = pygame.Rect(SW // 2 - 200, SH // 2 - 35, 400, 38)
            sr = pygame.Rect(SW // 2 - 200, SH // 2 + 25, 400, 38)
            br = pygame.Rect(SW // 2 - 110, SH // 2 + 100, 220, 52)
            if nr.collidepoint(mx, my):
                self.menu_focus = "name"
            elif sr.collidepoint(mx, my):
                self.menu_focus = "server"
            elif br.collidepoint(mx, my):
                self._do_connect()
            else:
                self.menu_focus = None

        elif ev.type == pygame.KEYDOWN:
            ctrl = pygame.key.get_mods() & pygame.KMOD_CTRL

            if self.menu_focus == "name":
                if ev.key == pygame.K_BACKSPACE:
                    self.menu_name = (
                        _delete_word(self.menu_name) if ctrl else self.menu_name[:-1]
                    )
                elif ev.key == pygame.K_RETURN:
                    self.menu_focus = "server"
                elif ev.key == pygame.K_TAB:
                    self.menu_focus = "server"
                elif ctrl and ev.key == pygame.K_a:
                    pass
                elif ctrl and ev.key == pygame.K_c:
                    pygame.scrap.put(pygame.SCRAP_TEXT, self.menu_name.encode())
                elif ctrl and ev.key == pygame.K_v:
                    pasted = self._clipboard_get()
                    if pasted:
                        self.menu_name = (self.menu_name + pasted)[:16]
                elif not ctrl and ev.unicode and ev.unicode.isprintable():
                    if len(self.menu_name) < 16:
                        self.menu_name += ev.unicode

            elif self.menu_focus == "server":
                if ev.key == pygame.K_BACKSPACE:
                    self.menu_server = (
                        _delete_word(self.menu_server)
                        if ctrl
                        else self.menu_server[:-1]
                    )
                elif ev.key == pygame.K_RETURN:
                    self._do_connect()
                elif ctrl and ev.key == pygame.K_a:
                    pass
                elif ctrl and ev.key == pygame.K_c:
                    pygame.scrap.put(pygame.SCRAP_TEXT, self.menu_server.encode())
                elif ctrl and ev.key == pygame.K_v:
                    pasted = self._clipboard_get()
                    if pasted:
                        self.menu_server = (self.menu_server + pasted)[:200]
                elif not ctrl and ev.unicode and ev.unicode.isprintable():
                    self.menu_server += ev.unicode

    def _toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        w, h = self.screen.get_size()
        import game_core as _gc

        _gc.update_screen_size(w, h)
        global SW, SH
        SW, SH = w, h
        self.inv_layout_dirty = True
        self.respawn_btn = pygame.Rect(SW // 2 - 120, SH // 2 + 80, 240, 54)

    def _clipboard_get(self) -> str:
        """Return clipboard text, or empty string on any failure."""
        try:
            pygame.scrap.init()
            raw = pygame.scrap.get(pygame.SCRAP_TEXT)
            if raw is None:
                return ""
            # pygame returns bytes; strip the null terminator Windows adds
            text = raw.decode("utf-8", errors="ignore").rstrip("\x00").strip()
            # Keep only printable, single-line content
            text = "".join(ch for ch in text if ch.isprintable())
            return text
        except Exception:
            return ""

    # ─────── Death Screen Events ──────────────────────────
    def _leave_game(self):
        """Cleanly disconnect and return to menu — used by all ESC paths."""
        self.net.disconnect()
        self.net = Net()  # fresh instance so reconnect works cleanly
        self.state = "menu"
        self.world = None
        self.me = None
        self.others = {}
        self.other_targets = {}
        self.dead = False
        self.inv_open = False
        self.inv_held = None
        self.inv_held_src = None
        self.mine_target = None
        self.mine_prog = 0
        self.mining_held = False
        self.join_sent = False
        self.version_sent = False
        self.version_ok = False
        self.version_check_t = 0.0

    def _ev_death(self, ev):
        if ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_r, pygame.K_SPACE):
                self._do_respawn()
            elif ev.key == pygame.K_ESCAPE:
                self._leave_game()
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            if self.respawn_btn.collidepoint(ev.pos):
                self._do_respawn()

    # ─────── Inventory Screen Events ──────────────────────
    def _ev_inventory(self, ev):
        if ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_e, pygame.K_ESCAPE):
                self._inv_close()
            elif self.craft_search_active:
                if ev.key == pygame.K_BACKSPACE:
                    self.craft_search = self.craft_search[:-1]
                elif ev.unicode and ev.unicode.isprintable():
                    self.craft_search = (self.craft_search + ev.unicode)[:24]
                self.craft_scroll = 0

        elif ev.type == pygame.MOUSEWHEEL:  # ← top level, not inside KEYDOWN
            mx, my = pygame.mouse.get_pos()
            PW = SW - 100
            px = 50
            divider = px + PW // 2 - 10
            if mx > divider:
                ROW_H = 72
                PH = SH - 80
                py = 40
                term = self.craft_search.lower()
                visible = (
                    [r for r in CRAFT_RECIPES if term in r[0].lower()]
                    if term
                    else CRAFT_RECIPES
                )
                craft_area_h = (py + PH - 10) - (py + 76 + 32)
                max_scroll = max(0, len(visible) * ROW_H - craft_area_h)
                self.craft_scroll = max(
                    0, min(self.craft_scroll - ev.y * ROW_H, max_scroll)
                )

        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            mx, my = ev.pos
            # Search bar click
            PW = SW - 100
            px = 50
            py = 40
            divider = px + PW // 2 - 10
            cx2 = divider + 16
            search_r = pygame.Rect(cx2, py + 76, PW - (divider - px) - 26, 26)
            if search_r.collidepoint(mx, my):
                self.craft_search_active = True
                return
            else:
                self.craft_search_active = False
            # Inventory slots
            for i, r in enumerate(self.inv_rects):
                if r.collidepoint(mx, my):
                    self._inv_click("i", i)
                    return
            # Hotbar slots (in inventory view)
            for i, r in enumerate(self.hotbar_inv_rects):
                if r.collidepoint(mx, my):
                    self._inv_click("h", i)
                    return
            # Craft buttons
            for btn_r, idx, can_craft in self.craft_btns:
                if btn_r.collidepoint(mx, my) and can_craft:
                    self._do_craft(idx)
                    return

        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
            # Right-click to split a stack (take half)
            mx, my = ev.pos
            for i, r in enumerate(self.inv_rects):
                if r.collidepoint(mx, my):
                    self._inv_split("i", i)
                    return
            for i, r in enumerate(self.hotbar_inv_rects):
                if r.collidepoint(mx, my):
                    self._inv_split("h", i)
                    return

    def _inv_close(self):
        if self.inv_held and self.inv_held_src:
            tp, idx = self.inv_held_src
            self._set_slot(tp, idx, self.inv_held)
            # No net.send — item just goes back, server state unchanged
        self.inv_held = None
        self.inv_held_src = None
        self.inv_open = False
        self.inv_layout_dirty = True  # reset for next open
        self.craft_search_active = False

    def _get_slot(self, tp, idx):
        if tp == "h" and self.me:
            return self.me.hotbar[idx]
        if tp == "i":
            return self.inv_slots[idx]
        return None

    def _set_slot(self, tp, idx, val):
        if tp == "h" and self.me:
            self.me.hotbar[idx] = val
        if tp == "i":
            self.inv_slots[idx] = val

    def _sync_hotbar(self):
        """Notify server of hotbar change (optimistic; server may correct)."""
        pass  # Server is authoritative; local moves are visual until server confirms

    def _inv_click(self, tp, idx):
        current = self._get_slot(tp, idx)
        if self.inv_held is None:
            if current:
                self.inv_held = dict(current)
                self.inv_held_src = (tp, idx)
                self._set_slot(tp, idx, None)
        else:
            # Try to stack
            if current and current["b"] == self.inv_held["b"]:
                take = min(99 - current["n"], self.inv_held["n"])
                current["n"] += take
                self.inv_held["n"] -= take
                self._set_slot(tp, idx, current)
                if self.inv_held["n"] <= 0:
                    self.inv_held = None
                    self.inv_held_src = None
            else:
                # Swap
                self._set_slot(tp, idx, self.inv_held)
                src_tp, src_idx = self.inv_held_src
                self._set_slot(src_tp, src_idx, current)
                self.net.send(
                    {
                        "type": "inv_move",  # ← this one stays, fires on drop
                        "ft": src_tp,
                        "fi": src_idx,
                        "tt": tp,
                        "ti": idx,
                    }
                )
                self.inv_held = None
                self.inv_held_src = None

    def _inv_split(self, tp, idx):
        """Pick up half of a stack."""
        if self.inv_held:
            return
        current = self._get_slot(tp, idx)
        if not current or current["n"] < 2:
            return
        half = current["n"] // 2
        current["n"] -= half
        self._set_slot(tp, idx, current)
        self.inv_held = {"b": current["b"], "n": half}
        self.inv_held_src = (tp, idx)

    def _do_craft(self, idx):
        _, result_blk, result_n, ingredients = CRAFT_RECIPES[idx]
        self.net.send({"type": "craft", "idx": idx})
        # Optimistic client deduction
        for blk, count in ingredients.items():
            remaining = count
            # Deduct from hotbar first
            if self.me:
                for i in range(9):
                    slot = self.me.hotbar[i]
                    if slot and slot["b"] == blk and remaining > 0:
                        take = min(slot["n"], remaining)
                        slot["n"] -= take
                        remaining -= take
                        if slot["n"] <= 0:
                            self.me.hotbar[i] = None
            # Then from inventory
            for i in range(18):
                slot = self.inv_slots[i]
                if slot and slot["b"] == blk and remaining > 0:
                    take = min(slot["n"], remaining)
                    slot["n"] -= take
                    remaining -= take
                    if slot["n"] <= 0:
                        self.inv_slots[i] = None
        # Add result (optimistic)
        for _ in range(result_n):
            self._give_local(result_blk)
        self.chat_log.append(
            (
                "⚒",
                f"Crafted {result_n}× {BLOCK_NAMES.get(result_blk, '?')}",
                "#AAFFAA",
                time.time(),
            )
        )

    def _give_local(self, blk):
        """Add one block to local hotbar/inventory (optimistic, server will confirm)."""
        if self.me:
            for slot in self.me.hotbar:
                if slot and slot["b"] == blk and slot["n"] < 99:
                    slot["n"] += 1
                    return
            for i in range(9):
                if self.me.hotbar[i] is None:
                    self.me.hotbar[i] = {"b": blk, "n": 1}
                    return
        for slot in self.inv_slots:
            if slot and slot["b"] == blk and slot["n"] < 99:
                slot["n"] += 1
                return
        for i in range(18):
            if self.inv_slots[i] is None:
                self.inv_slots[i] = {"b": blk, "n": 1}
                return

    # ─────── Game Events ──────────────────────────────────
    def _ev_game(self, ev):
        if self.chat_open:
            if ev.type == pygame.KEYDOWN:
                ctrl = pygame.key.get_mods() & pygame.KMOD_CTRL
                if ev.key == pygame.K_RETURN:
                    txt = self.chat_input.strip()
                    if txt:
                        self.net.send({"type": "chat", "msg": txt})
                    self.chat_input = ""
                    self.chat_open = False
                elif ev.key == pygame.K_ESCAPE:
                    self.chat_input = ""
                    self.chat_open = False
                elif ev.key == pygame.K_BACKSPACE:
                    self.chat_input = (
                        _delete_word(self.chat_input) if ctrl else self.chat_input[:-1]
                    )
                elif ctrl and ev.key == pygame.K_v:
                    pasted = self._clipboard_get()
                    if pasted:
                        self.chat_input = (self.chat_input + pasted)[:120]
                elif not ctrl and ev.unicode and ev.unicode.isprintable():
                    if len(self.chat_input) < 120:
                        self.chat_input += ev.unicode
            return

        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_t:
                self.chat_open = True
            elif ev.key == pygame.K_e:
                self.inv_open = True
            elif ev.key == pygame.K_f:
                self._try_attack(melee_key=True)
            elif ev.key == pygame.K_ESCAPE:
                self._leave_game()
            elif ev.key == pygame.K_TAB:
                self.show_list = True
            elif ev.key == pygame.K_F3:
                self.debug = not self.debug
            elif ev.key in (
                pygame.K_1,
                pygame.K_2,
                pygame.K_3,
                pygame.K_4,
                pygame.K_5,
                pygame.K_6,
                pygame.K_7,
                pygame.K_8,
                pygame.K_9,
            ):
                i = ev.key - pygame.K_1
                if self.me:
                    self.me.sel = i
                    self.net.send({"type": "sel", "i": i})

        elif ev.type == pygame.KEYUP:
            if ev.key == pygame.K_TAB:
                self.show_list = False

        elif ev.type == pygame.MOUSEWHEEL:
            if self.me:
                self.me.sel = (self.me.sel - ev.y) % 9
                self.net.send({"type": "sel", "i": self.me.sel})

        elif ev.type == pygame.MOUSEBUTTONDOWN:
            if ev.button == 1:
                attacked = self._try_attack(melee_key=False)
                if not attacked:
                    self.mining_held = True
                    self._start_mine()
                # If an attack landed, do NOT enter mining mode
            elif ev.button == 3:
                self._place()

        elif ev.type == pygame.MOUSEBUTTONUP:
            if ev.button == 1:
                self.mine_target = None
                self.mine_prog = 0
                self.mining_held = False

    # ─────── Actions ──────────────────────────────────────
    def _cursor_block(self):
        """Return (bx, by) of the tile under the cursor, or (None, None) if out of reach."""
        if not (self.world and self.me):
            return None, None
        mx, my = pygame.mouse.get_pos()
        wx = mx + self.cam_x
        wy = my + self.cam_y
        bx = int(wx // TILE)
        by = int(wy // TILE)
        dist = math.hypot(
            self.me.cx - (bx * TILE + TILE // 2),
            self.me.cy - (by * TILE + TILE // 2),
        )
        if dist > REACH:
            return None, None
        return bx, by

    def _start_mine(self):
        if not (self.world and self.me):
            return
        bx, by = self._cursor_block()
        if bx is None:
            return
        b = self.world.get(bx, by)
        if b == B_AIR:
            return
        self.mine_target = (bx, by)
        self.mine_start = time.time()
        self.mine_prog = 0.0

    def _place(self):
        if not (self.world and self.me):
            return
        bx, by = self._cursor_block()
        if bx is None or self.world.get(bx, by) != B_AIR:
            return
        if self._would_overlap_any_player(bx, by):
            return
        slot = self.me.hotbar[self.me.sel]
        if not slot:
            return
        # Optimistic update — same pattern as mining
        self.world.set(bx, by, slot["b"])
        self.net.send({"type": "place", "x": bx, "y": by, "b": slot["b"]})
        # Optimistic inventory deduction
        slot["n"] -= 1
        if slot["n"] <= 0:
            self.me.hotbar[self.me.sel] = None

    def _would_overlap_player(self, bx, by):
        """True if placing a block at (bx,by) would intersect the local player."""
        block_rect = pygame.Rect(bx * TILE, by * TILE, TILE, TILE)
        player_rect = pygame.Rect(int(self.me.x), int(self.me.y), self.me.W, self.me.H)
        return block_rect.colliderect(player_rect)

    def _would_overlap_any_player(self, bx, by):
        block_rect = pygame.Rect(bx * TILE, by * TILE, TILE, TILE)
        # Check self
        if block_rect.colliderect(
            pygame.Rect(int(self.me.x), int(self.me.y), self.me.W, self.me.H)
        ):
            return True
        # Check others
        for p in self.others.values():
            if block_rect.colliderect(pygame.Rect(int(p.x), int(p.y), p.W, p.H)):
                return True
        return False

    def _try_attack(self, melee_key=False) -> bool:
        if not self.me or self.attack_cd > 0:
            return False
        target_pid = None
        best_dist = ATTACK_RANGE

        if melee_key:
            for pid, p in self.others.items():
                dx = p.cx - self.me.cx
                dy = p.cy - self.me.cy
                dist = math.hypot(dx, dy)
                if dist > ATTACK_RANGE:
                    continue
                facing_right = self.me.facing == "right"
                if (facing_right and dx > -16) or (not facing_right and dx < 16):
                    if dist < best_dist:
                        best_dist, target_pid = dist, pid
        else:
            # Click-to-attack: hit the player the cursor is closest to
            mx, my = pygame.mouse.get_pos()
            wx, wy = mx + self.cam_x, my + self.cam_y
            for pid, p in self.others.items():
                cursor_dist = math.hypot(p.cx - wx, p.cy - wy)
                player_dist = math.hypot(p.cx - self.me.cx, p.cy - self.me.cy)
                if cursor_dist < TILE * 1.5 and player_dist < ATTACK_RANGE:
                    if cursor_dist < best_dist:
                        best_dist, target_pid = cursor_dist, pid

        if target_pid is not None:
            self.net.send({"type": "attack", "target": target_pid})
            self.attack_cd = ATTACK_CD
            self.attack_swing = 1.0
            return True
        return False

    def _do_respawn(self):
        self.net.send({"type": "respawn"})
        self.dead = False
        self.killed_by = ""
        self.spawn_flash = 0.8
        if self.me:
            self.me.health = 100

    def _do_connect(self):
        name = self.menu_name.strip() or "Miner"
        url = self.menu_server.strip()
        if not url.startswith(("ws://", "wss://")):
            self.menu_err = "URL must start with ws:// or wss://"
            return
        self.menu_err = ""
        self.join_sent = False
        self.net.disconnect()  # close any existing session cleanly first
        self.net = Net()
        self.net.connect(url)
        self._pending_name = name
        self.state = "connecting"

    # ─────── Update ───────────────────────────────────────
    def update(self, dt):
        if self.state in ("connecting", "playing"):  # ← was just 'playing'
            for msg in self.net.poll():
                try:
                    self._on_msg(msg)
                except Exception as e:
                    print(f"[_on_msg error] {e}")  # log and continue, don't drop

        if self.state == "connecting":
            if self.net.error:
                self.menu_err = self.net.error
                self.state = "menu"
            elif self.net.alive and not self.version_sent:
                from game_core import CLIENT_VERSION

                self.net.send({"type": "version", "v": CLIENT_VERSION})
                self.version_sent = True
                self.version_check_t = time.time()
            elif self.version_sent and not self.version_ok:
                if time.time() - self.version_check_t > VERSION_TIMEOUT:
                    self.menu_err = (
                        "Server didn't respond to version check — may be outdated"
                    )
                    self.state = "menu"
            elif self.version_ok and not self.join_sent:
                self.net.send({"type": "join", "name": self._pending_name})
                self.join_sent = True
        if self.me and self.world and not self.dead:
            keys = pygame.key.get_pressed()
            self.me.update_physics(self.world, keys)

            # ── Mining ──────────────────────────────────────────────────
            mb1 = pygame.mouse.get_pressed()[0]
            if not mb1 or self.inv_open:
                # Mouse released or inventory open — stop any active mining
                self.mine_target = None
                self.mine_prog = 0
            elif self.mine_target:
                bx, by = self.mine_target
                btype = self.world.get(bx, by)
                if btype == B_AIR:
                    # Block already gone (server update arrived) — seek next
                    self.mine_target = None
                    self.mine_prog = 0
                else:
                    elapsed = time.time() - self.mine_start
                    # Base time, then divide by tool speed multiplier
                    mt = MINE_SEC.get(btype, 1.0)
                    if self.me:
                        slot = self.me.hotbar[self.me.sel]
                        if slot and slot["b"] in TOOL_SPEED:
                            mt = mt / TOOL_SPEED[slot["b"]].get(btype, 1.0)
                    mt = max(0.05, mt)  # minimum 50 ms so animation always shows
                    self.mine_prog = min(1.0, elapsed / mt)
                    if elapsed >= mt:
                        # Spawn break particles
                        bc = BCOLORS.get(btype)
                        if bc:
                            for _ in range(8):
                                self.particles.append(
                                    Particle(
                                        bx * TILE + TILE // 2,
                                        by * TILE + TILE // 2,
                                        bc[0],
                                    )
                                )
                        # Optimistic local update so hold-mining doesn't re-mine air
                        self.world.set(bx, by, B_AIR)
                        self.net.send({"type": "break", "x": bx, "y": by})
                        # Optimistic inventory update — same pattern as place/craft.
                        # The server's authoritative inv reply will correct any
                        # discrepancy; without this the hotbar never updates if the
                        # inv message is dropped by the poll race or network lag.
                        self._give_local(btype)
                        self.mine_target = None
                        self.mine_prog = 0

            # Auto-start next block when holding mouse in mining mode
            if (
                mb1
                and self.mining_held
                and not self.mine_target
                and not self.inv_open
                and self.me
                and self.world
            ):
                nbx, nby = self._cursor_block()
                if nbx is not None and self.world.get(nbx, nby) not in (
                    B_AIR,
                    B_BEDROCK,
                ):
                    self.mine_target = (nbx, nby)
                    self.mine_start = time.time()
                    self.mine_prog = 0.0

            # Smooth camera
            tcx = self.me.cx - SW // 2
            tcy = self.me.cy - SH // 2
            self.cam_x += (tcx - self.cam_x) * 0.12
            self.cam_y += (tcy - self.cam_y) * 0.12
            self.cam_x = max(0, min(self.cam_x, self.world.ww * TILE - SW))
            self.cam_y = max(0, min(self.cam_y, self.world.wh * TILE - SH))

            # Network position send
            now = time.time()
            if now - self.last_move_t > 1 / NET_HZ:
                self.net.send(
                    {
                        "type": "move",
                        "x": round(self.me.x, 1),
                        "y": round(self.me.y, 1),
                        "vx": round(self.me.vx, 2),
                        "vy": round(self.me.vy, 2),
                        "f": self.me.facing,
                    }
                )
                self.last_move_t = now

            # Particles & clouds
            self.particles = [p for p in self.particles if p.life > 0]
            for p in self.particles:
                p.update(dt)
            for c in self.clouds:
                c.update(dt)

            # Day/night
            self.gtime = (self.gtime + dt * 10) % 24000

            # Screen flash timers
            if self.spawn_flash > 0:
                self.spawn_flash -= dt * 1.5
            if self.hit_flash > 0:
                self.hit_flash -= dt * 2.5
            if self.shake_t > 0:
                self.shake_t -= dt

            # Attack timers
            if self.attack_cd > 0:
                self.attack_cd = max(0, self.attack_cd - dt)
            if self.attack_swing > 0:
                self.attack_swing = max(0, self.attack_swing - dt * 4)

            # HP regen
            if self.me.health < 100 and time.time() - self.last_hit_t > REGEN_DELAY:
                self.me.health = min(100, self.me.health + REGEN_RATE * dt)

            # Damage numbers
            self.dmg_numbers = [d for d in self.dmg_numbers if d.life > 0]
            for d in self.dmg_numbers:
                d.update(dt)

    # ─────── Network Messages ─────────────────────────────
    def _on_msg(self, msg):
        t = msg.get("type")
        if t == "version_ack":
            if msg.get("ok"):
                self.version_ok = True
            else:
                self.menu_err = msg.get("msg", "Version mismatch")
                self._leave_game()
                self.state = "menu"
        elif t == "init":
            raw = base64.b64decode(msg["world"])
            self.world = World(raw, msg["ww"], msg["wh"])
            self.pid = msg["pid"]
            self.gtime = msg.get("time", 6000)
            self.me = Player(
                msg["ww"] * TILE // 2,
                0,
                color=msg.get("color", "#FF6B6B"),
                name=self._pending_name,
            )
            self.me.hotbar = msg.get("hotbar", [None] * 9)
            self.me.sel = msg.get("sel", 0)
            self.inv_slots = msg.get("inventory", [None] * 18)
            for bid in range(17):
                _make_block_surf(bid)
            from game_core import TOOL_IDS as _TI
            from game_core import _make_tool_surf

            for tid in _TI:
                _make_tool_surf(tid)
            self.others = {}
            for pid, pd in msg.get("players", {}).items():
                if pid != self.pid:
                    self._make_remote(pid, pd)
            self.clouds = [Cloud(self.world.ww) for _ in range(12)]
            self.state = "playing"
            self.spawn_flash = 0.8
            self.dead = False

        elif t == "join":
            pid = msg["id"]
            if pid != self.pid:
                self._make_remote(pid, msg["player"])

        elif t == "leave":
            self.others.pop(msg["id"], None)
            self.other_targets.pop(msg["id"], None)

        elif t == "move":
            pid = msg["id"]
            if pid in self.others:
                p = self.others[pid]
                p.vx, p.vy = msg.get("vx", 0), msg.get("vy", 0)
                p.facing = msg.get("f", "right")
                self.other_targets[pid] = (msg["x"], msg["y"])  # ← msg is valid here
                # Remove the hard snap: p.x, p.y = msg['x'], msg['y']

        elif t == "blk":
            if self.world:
                self.world.set(msg["x"], msg["y"], msg["b"])

        elif t == "inv":
            if self.me:
                self.me.hotbar = msg["hotbar"]
                self.me.sel = msg.get("sel", self.me.sel)
            if "inventory" in msg:
                self.inv_slots = msg["inventory"]

        elif t == "chat":
            self.chat_log.append(
                (msg["name"], msg["msg"], msg.get("color", "#FFFFFF"), time.time())
            )

        elif t == "time":
            self.gtime = msg["t"]

        elif t == "damage":
            victim_pid = msg.get("victim")
            dmg = msg.get("dmg", 0)
            hp = msg.get("hp", 100)
            kbx = msg.get("kbx", 0)
            kby = msg.get("kby", 0)

            if victim_pid == self.pid and self.me:
                self.me.health = hp
                self.last_hit_t = time.time()
                self.hit_flash = 1.0
                self.shake_t = 0.25
                self.shake_amp = 6
                self.me.vx += kbx
                self.me.vy += kby
                self.dmg_numbers.append(
                    DamageNumber(self.me.cx, self.me.cy - 20, dmg, (255, 60, 60))
                )
            elif victim_pid in self.others:
                p = self.others[victim_pid]
                p.health = hp
                p.vx += kbx
                p.vy += kby
                self.dmg_numbers.append(
                    DamageNumber(p.cx, p.cy - 20, dmg, (255, 180, 60))
                )

        elif t == "die":
            victim_pid = msg.get("victim")
            killer_name = msg.get("killer", "someone")
            victim_name = (
                self.me.name
                if victim_pid == self.pid
                else self.others.get(victim_pid, Player(0, 0)).name
            )
            self.chat_log.append(
                (
                    "☠",
                    f"{victim_name} was slain by {killer_name}",
                    "#FF4444",
                    time.time(),
                )
            )
            if victim_pid == self.pid:
                self.dead = True
                self.killed_by = killer_name
                if self.me:
                    self.me.health = 0
                # Close inventory so it doesn't bleed into the death screen or respawn
                self.inv_open = False
                self.inv_held = None
                self.inv_held_src = None

        elif t == "respawn":
            if self.me:
                self.me.health = 100
                self.me.x = float(msg.get("x", self.me.x))
                self.me.y = float(msg.get("y", self.me.y))
                self.me.vy = 0
            self.dead = False
            self.spawn_flash = 0.8

    def _make_remote(self, pid, pd):
        p = Player(pd["x"], pd["y"], color=pd["color"], name=pd["name"])
        p.facing = pd.get("facing", "right")
        p.health = pd.get("health", 100)
        self.others[pid] = p
        self.other_targets[pid] = (pd["x"], pd["y"])

    # ─────── Draw Dispatch ────────────────────────────────
    def draw(self):
        if self.state == "menu":
            self._draw_menu()
        elif self.state == "connecting":
            self._draw_connecting()
        elif self.state == "playing":
            self._draw_game()
            if self.dead:
                self._draw_death_screen()
            elif self.inv_open:
                self._draw_inventory()
        pygame.display.set_caption(f"BlockWorld  •  {self.clock.get_fps():.0f} fps")

    # ─────── Menu ─────────────────────────────────────────
    def _draw_menu(self):
        self.screen.fill((22, 22, 38))
        tile_strip = [1, 2, 3, 7, 8, 9]
        for i in range(SW // TILE + 1):
            b = tile_strip[i % len(tile_strip)]
            s = _make_block_surf(b)
            if s:
                self.screen.blit(s, (i * TILE, SH - TILE))

        for i, b in enumerate([9, 8, 7, 3, 2]):
            yoff = int(math.sin(time.time() * 1.2 + i) * 6)
            s = _make_block_surf(b)
            if s:
                self.screen.blit(s, (SW // 2 - 80 + i * 40, SH // 4 - 60 + yoff))

        title = self.font_xl.render("BlockWorld", True, (255, 215, 50))
        sub = self.font_md.render(
            "2-D Multiplayer Mining Adventure", True, (170, 170, 200)
        )
        self.screen.blit(title, (SW // 2 - title.get_width() // 2, SH // 4))
        self.screen.blit(sub, (SW // 2 - sub.get_width() // 2, SH // 4 + 66))

        def field(label, value, rect, active):
            lbl = self.font_sm.render(label, True, (180, 180, 200))
            self.screen.blit(lbl, (rect.x, rect.y - 18))
            bc = (90, 130, 220) if active else (48, 48, 68)
            pygame.draw.rect(self.screen, bc, rect, border_radius=6)
            pygame.draw.rect(self.screen, (140, 160, 255), rect, 2, border_radius=6)
            cursor = "|" if active and int(time.time() * 2) % 2 == 0 else ""
            vs = self.font_md.render(value[-38:] + cursor, True, (240, 240, 240))
            self.screen.blit(vs, (rect.x + 8, rect.y + 9))

        nr = pygame.Rect(SW // 2 - 200, SH // 2 - 35, 400, 38)
        sr = pygame.Rect(SW // 2 - 200, SH // 2 + 25, 400, 38)
        field("Your Name", self.menu_name, nr, self.menu_focus == "name")
        field("Server URL", self.menu_server, sr, self.menu_focus == "server")

        br = pygame.Rect(SW // 2 - 110, SH // 2 + 100, 220, 52)
        hov = br.collidepoint(*pygame.mouse.get_pos())
        pygame.draw.rect(
            self.screen, (0, 175, 80) if hov else (0, 140, 60), br, border_radius=8
        )
        pygame.draw.rect(self.screen, (0, 240, 120), br, 2, border_radius=8)
        bt = self.font_lg.render("CONNECT", True, (255, 255, 255))
        self.screen.blit(bt, (SW // 2 - bt.get_width() // 2, SH // 2 + 114))

        if self.menu_err:
            es = self.font_sm.render(f"⚠ {self.menu_err}", True, (255, 100, 100))
            self.screen.blit(es, (SW // 2 - es.get_width() // 2, SH // 2 + 170))

        hints = [
            "WASD: Move   Space: Jump   LClick: Mine/Attack   RClick: Place",
            "E: Inventory & Crafting   1-9/Scroll: Hotbar   F: Melee   T: Chat",
        ]
        for i, h in enumerate(hints):
            hs = self.font_sm.render(h, True, (100, 100, 130))
            self.screen.blit(hs, (SW // 2 - hs.get_width() // 2, SH - 50 + i * 16))

    # ─────── Connecting ───────────────────────────────────
    def _draw_connecting(self):
        self.screen.fill((15, 15, 30))
        if not self.version_sent:
            status = "Connecting"
        elif not self.version_ok:
            status = "Checking version"
        else:
            status = "Joining world"
        dots = "." * (int(time.time() * 2) % 4)
        ts = self.font_lg.render(f"{status}{dots}", True, (180, 180, 255))
        self.screen.blit(ts, (SW // 2 - ts.get_width() // 2, SH // 2 - 30))
        hs = self.font_sm.render(self.menu_server, True, (100, 100, 150))
        self.screen.blit(hs, (SW // 2 - hs.get_width() // 2, SH // 2 + 10))
        if self.net.error:
            es = self.font_md.render(f"Error: {self.net.error}", True, (255, 80, 80))
            self.screen.blit(es, (SW // 2 - es.get_width() // 2, SH // 2 + 50))

    # ─────── In-Game ──────────────────────────────────────
    def _draw_game(self):
        if not (self.world and self.me):
            return
        sky = _sky(self.gtime)
        light = _light(self.gtime)
        self.screen.fill(sky)

        draw_stars(self.screen, light)
        draw_sun_moon(self.screen, self.gtime)

        if light > 0.1:
            for c in self.clouds:
                c.draw(self.screen, self.cam_x, self.cam_y, light)

        self.world.draw(self.screen, self.cam_x, self.cam_y, light)

        # Mining overlay
        if self.mine_target and self.mine_prog > 0:
            bx, by = self.mine_target
            sx, sy = bx * TILE - self.cam_x, by * TILE - self.cam_y
            ov = pygame.Surface((TILE, TILE), pygame.SRCALPHA)
            ov.fill((0, 0, 0, int(self.mine_prog * 200)))
            self.screen.blit(ov, (sx, sy))
            for i in range(int(self.mine_prog * 6)):
                cx2 = sx + (i * 9) % TILE
                cy2 = sy + (i * 11 + 3) % TILE
                pygame.draw.line(
                    self.screen, (255, 255, 255), (cx2, cy2), (cx2 + 5, cy2 + 4), 1
                )

        for p in self.particles:
            p.draw(self.screen, self.cam_x, self.cam_y)

        # Screen shake offset
        shake = (0, 0)
        if self.shake_t > 0:
            intensity = self.shake_amp * (self.shake_t / 0.25)
            shake = (
                random.randint(-int(intensity), int(intensity)),
                random.randint(-int(intensity), int(intensity)),
            )

        # Remote players with attack targeting highlight
        mx2, my2 = pygame.mouse.get_pos()
        wx2, wy2 = mx2 + self.cam_x, my2 + self.cam_y
        for pid, p in self.others.items():
            if pid in self.other_targets:
                tx, ty = self.other_targets[pid]
                p.x += (tx - p.x) * 0.3
                p.y += (ty - p.y) * 0.3
            near_cursor = math.hypot(p.cx - wx2, p.cy - wy2) < TILE * 1.5
            near_player = (
                self.me
                and math.hypot(p.cx - self.me.cx, p.cy - self.me.cy) < ATTACK_RANGE
            )
            if near_cursor and near_player:
                pr = pygame.Rect(
                    p.x - self.cam_x + shake[0] - 3,
                    p.y - self.cam_y + shake[1] - 3,
                    p.W + 6,
                    p.H + 6,
                )
                # Pulsing red outline
                pulse = int(abs(math.sin(time.time() * 6)) * 80 + 175)
                pygame.draw.rect(
                    self.screen, (255, pulse // 2, pulse // 2), pr, 2, border_radius=4
                )
                # "ATTACK" hint above
                atk = self.font_sm.render("⚔ ATTACK", True, (255, 100, 100))
                self.screen.blit(
                    atk, (pr.x + pr.w // 2 - atk.get_width() // 2, pr.y - 16)
                )
            p.draw(
                self.screen,
                self.cam_x,
                self.cam_y,
                local=False,
                font_sm=self.font_sm,
                shake=shake,
            )

        # Attack swing arc
        if self.me and self.attack_swing > 0:
            cx3 = int(self.me.cx - self.cam_x + shake[0])
            cy3 = int(self.me.cy - self.cam_y + shake[1])
            arc_r = int(ATTACK_RANGE * 0.75)
            start_a = -math.pi / 3 if self.me.facing == "right" else -math.pi * 2 / 3
            sweep = math.pi / 1.5
            alpha = int(self.attack_swing * 180)
            arc_surf = pygame.Surface((arc_r * 2 + 4, arc_r * 2 + 4), pygame.SRCALPHA)
            pygame.draw.arc(
                arc_surf,
                (255, 220, 80, alpha),
                (0, 0, arc_r * 2, arc_r * 2),
                start_a,
                start_a + sweep,
                8,
            )
            self.screen.blit(arc_surf, (cx3 - arc_r - 2, cy3 - arc_r - 2))

        # Local player
        self.me.draw(self.screen, self.cam_x, self.cam_y, local=True, shake=shake)

        # Damage numbers
        for dn in self.dmg_numbers:
            dn.draw(self.screen, self.cam_x, self.cam_y, self.font_lg)

        # Spawn flash (white)
        if self.spawn_flash > 0:
            ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov.fill((255, 255, 255, int(max(0, self.spawn_flash) * 200)))
            self.screen.blit(ov, (0, 0))

        # Hit flash (red)
        if self.hit_flash > 0:
            hf = pygame.Surface((SW, SH), pygame.SRCALPHA)
            # Vignette-style: darker at edges
            intensity = int(max(0, self.hit_flash) * 120)
            hf.fill((200, 0, 0, intensity))
            self.screen.blit(hf, (0, 0))

        # Block cursor
        bx, by = self._cursor_block()
        if bx is not None:
            cr = pygame.Rect(bx * TILE - self.cam_x, by * TILE - self.cam_y, TILE, TILE)
            pulse_w = 1 + int(abs(math.sin(time.time() * 4)))
            pygame.draw.rect(self.screen, (255, 255, 255), cr, pulse_w)
            bn = BLOCK_NAMES.get(self.world.get(bx, by), "")
            if bn and bn != "Air":
                bs = self.font_sm.render(bn, True, (220, 220, 220))
                bsb = pygame.Surface(
                    (bs.get_width() + 8, bs.get_height() + 4), pygame.SRCALPHA
                )
                bsb.fill((0, 0, 0, 140))
                mx2, my2 = pygame.mouse.get_pos()
                self.screen.blit(bsb, (mx2 + 14, my2 - 20))
                self.screen.blit(bs, (mx2 + 18, my2 - 18))

        # Night overlay
        if light < 0.99:
            night_ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
            night_ov.fill((0, 0, 30, int((1 - light) * 120)))
            self.screen.blit(night_ov, (0, 0))

        self._draw_hud()
        self._draw_chat()
        if self.show_list:
            self._draw_player_list()
        if self.debug:
            self._draw_debug()

    # ─────── HUD ──────────────────────────────────────────
    def _draw_hud(self):
        if not self.me:
            return
        SS, PAD = 52, 4
        hb_w = 9 * (SS + PAD) + PAD
        hb_x = SW // 2 - hb_w // 2
        hb_y = SH - SS - 10

        for i in range(9):
            sx = hb_x + i * (SS + PAD) + PAD
            sy = hb_y
            sel = i == self.me.sel
            pygame.draw.rect(
                self.screen,
                (80, 80, 110) if not sel else (130, 130, 180),
                (sx, sy, SS, SS),
                border_radius=5,
            )
            pygame.draw.rect(
                self.screen,
                (255, 220, 50) if sel else (90, 90, 120),
                (sx, sy, SS, SS),
                2,
                border_radius=5,
            )
            slot = self.me.hotbar[i] if i < len(self.me.hotbar) else None
            if slot:
                bsurf = get_item_surf(slot["b"])
                if bsurf:
                    sc = pygame.transform.scale(bsurf, (SS - 12, SS - 12))
                    self.screen.blit(sc, (sx + 6, sy + 4))
                cnt = self.font_sm.render(str(slot["n"]), True, (255, 255, 255))
                self.screen.blit(cnt, (sx + SS - cnt.get_width() - 3, sy + SS - 14))
                if sel:
                    nm = BLOCK_NAMES.get(slot["b"], "")
                    ns = self.font_sm.render(nm, True, (255, 230, 150))
                    self.screen.blit(ns, (SW // 2 - ns.get_width() // 2, hb_y - 18))

        # HP bar
        hp_x, hp_y = 12, SH - 30
        pygame.draw.rect(
            self.screen, (60, 0, 0), (hp_x, hp_y, 180, 14), border_radius=4
        )
        hw = int(180 * max(0, self.me.health) / 100)
        hc = (
            (0, 200, 80)
            if self.me.health > 60
            else (220, 150, 0)
            if self.me.health > 30
            else (220, 40, 30)
        )
        if hw > 0:
            pygame.draw.rect(self.screen, hc, (hp_x, hp_y, hw, 14), border_radius=4)
        pygame.draw.rect(
            self.screen, (180, 180, 180), (hp_x, hp_y, 180, 14), 1, border_radius=4
        )
        hps = self.font_sm.render(
            f"♥ {max(0, int(self.me.health))}/100", True, (255, 255, 255)
        )
        self.screen.blit(hps, (hp_x + 4, hp_y + 1))

        # Attack cooldown
        if self.attack_cd > 0:
            cd_frac = self.attack_cd / ATTACK_CD
            pygame.draw.rect(
                self.screen, (40, 40, 60), (hp_x, hp_y - 18, 60, 6), border_radius=3
            )
            pygame.draw.rect(
                self.screen,
                (255, 180, 50),
                (hp_x, hp_y - 18, int(60 * cd_frac), 6),
                border_radius=3,
            )
            sw = self.font_sm.render("⚔ CD", True, (200, 180, 120))
            self.screen.blit(sw, (hp_x + 64, hp_y - 19))
        else:
            sw = self.font_sm.render("⚔ Ready", True, (100, 220, 100))
            self.screen.blit(sw, (hp_x, hp_y - 19))

        # Inventory hint
        ei = self.font_sm.render("E: Inventory", True, (100, 100, 140))
        self.screen.blit(ei, (hp_x, hp_y - 34))

        # Time of day
        hour = int(self.gtime / 24000 * 24)
        mins = int((self.gtime / 24000 * 24 - hour) * 60)
        tod = "Day" if 6 <= hour < 20 else "Night"
        tstr = f"{tod}  {hour:02d}:{mins:02d}"
        ts = self.font_sm.render(tstr, True, (240, 240, 200))
        self.screen.blit(ts, (SW - ts.get_width() - 10, 10))

        pc = self.font_sm.render(
            f"Players: {1 + len(self.others)}", True, (180, 240, 180)
        )
        self.screen.blit(pc, (SW - pc.get_width() - 10, 26))

    # ─────── Chat ─────────────────────────────────────────
    def _draw_chat(self):
        now = time.time()
        msgs = [m for m in self.chat_log if self.chat_open or now - m[3] < 12]
        msgs = list(msgs)[-8:]

        base_y = SH - 130
        for i, (name, msg, col, ts) in enumerate(msgs):
            y = base_y - (len(msgs) - 1 - i) * 18
            alpha = 1.0 if self.chat_open else min(1.0, (12 - (now - ts)) / 3)
            text = f"{name}: {msg}"
            sh = self.font_sm.render(text, True, (0, 0, 0))
            self.screen.blit(sh, (13, y + 1))
            c = _hex(col)
            fs = self.font_sm.render(text, True, tuple(int(v * alpha) for v in c))
            self.screen.blit(fs, (12, y))

        if self.chat_open:
            ir = pygame.Rect(12, SH - 108, 420, 24)
            ov = pygame.Surface((ir.width, ir.height), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 160))
            self.screen.blit(ov, (ir.x, ir.y))
            pygame.draw.rect(self.screen, (80, 120, 240), ir, 1)
            ct = (
                "Say: "
                + self.chat_input
                + ("|" if int(time.time() * 2) % 2 == 0 else " ")
            )
            cs = self.font_sm.render(ct, True, (255, 255, 255))
            self.screen.blit(cs, (ir.x + 5, ir.y + 4))
        else:
            hint = self.font_sm.render("T = Chat", True, (80, 80, 110))
            self.screen.blit(hint, (12, SH - 110))

    # ─────── Player List ──────────────────────────────────
    def _draw_player_list(self):
        all_p = [
            (
                "You (" + self.me.name + ")",
                self.me.color if self.me else (200, 200, 200),
                self.me.health if self.me else 100,
            )
        ] + [(p.name, p.color, p.health) for p in self.others.values()]
        w, row = 280, 26
        h = len(all_p) * row + 40
        ox, oy = SW // 2 - w // 2, SH // 2 - h // 2
        ov = pygame.Surface((w, h), pygame.SRCALPHA)
        ov.fill((10, 10, 30, 210))
        self.screen.blit(ov, (ox, oy))
        pygame.draw.rect(
            self.screen, (100, 140, 255), (ox, oy, w, h), 2, border_radius=6
        )
        title = self.font_md.render(
            f"Players Online ({len(all_p)})", True, (220, 220, 255)
        )
        self.screen.blit(title, (ox + w // 2 - title.get_width() // 2, oy + 8))
        for i, (name, col, hp) in enumerate(all_p):
            y = oy + 38 + i * row
            pygame.draw.rect(self.screen, _hex(col), (ox + 10, y + 5, 12, 12))
            ns = self.font_sm.render(name, True, (220, 220, 220))
            self.screen.blit(ns, (ox + 28, y + 5))
            # Mini HP
            hpw = int(60 * max(0, hp) / 100)
            pygame.draw.rect(self.screen, (60, 0, 0), (ox + w - 75, y + 7, 60, 8))
            hc = (
                (0, 200, 80) if hp > 60 else (220, 150, 0) if hp > 30 else (220, 40, 30)
            )
            if hpw > 0:
                pygame.draw.rect(self.screen, hc, (ox + w - 75, y + 7, hpw, 8))

    # ─────── Inventory & Crafting Screen ──────────────────

    def _build_inv_layout(self):
        PW = SW - 100
        px, py = 50, 40
        divider = px + PW // 2 - 10
        ox, oy = px + 16, py + 48 + 22  # matches inv label offset
        self.inv_rects = []
        for i in range(18):
            row, col = i // 9, i % 9
            self.inv_rects.append(
                pygame.Rect(
                    ox + col * (SLOT + SPAD), oy + row * (SLOT + SPAD), SLOT, SLOT
                )
            )
        hb_oy = oy + 2 * (SLOT + SPAD) + 14 + 22
        self.hotbar_inv_rects = []
        for i in range(9):
            self.hotbar_inv_rects.append(
                pygame.Rect(ox + i * (SLOT + SPAD), hb_oy, SLOT, SLOT)
            )
        cx2 = divider + 16
        cy2 = py + 48 + 28
        ROW_H = 72
        BTN_W = 72
        ROW_W = px + PW - cx2 - 10
        self.craft_btns = []
        for i, (name, result_blk, result_n, ingredients) in enumerate(CRAFT_RECIPES):
            ry = cy2 + i * ROW_H
            btn_r = pygame.Rect(
                cx2 + ROW_W - BTN_W - 4, ry + ROW_H // 2 - 16, BTN_W, 32
            )
            self.craft_btns.append((btn_r, i, False))  # can_craft updated at draw time

    def _draw_inventory(self):
        if self.inv_layout_dirty:
            self._build_inv_layout()
            self.inv_layout_dirty = False

        # Dark overlay
        ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 200))
        self.screen.blit(ov, (0, 0))

        # Panel
        PW, PH = SW - 100, SH - 80
        px, py = 50, 40
        panel = pygame.Surface((PW, PH), pygame.SRCALPHA)
        panel.fill((18, 20, 38, 240))
        self.screen.blit(panel, (px, py))
        pygame.draw.rect(
            self.screen, (80, 100, 200), (px, py, PW, PH), 2, border_radius=8
        )

        # Title
        title = self.font_lg.render("Inventory & Crafting", True, (200, 215, 255))
        close = self.font_sm.render("E or Esc to close", True, (120, 120, 160))
        self.screen.blit(title, (px + 16, py + 10))
        self.screen.blit(close, (px + PW - close.get_width() - 12, py + 14))

        divider = px + PW // 2 - 10
        pygame.draw.line(
            self.screen, (60, 70, 110), (divider, py + 40), (divider, py + PH - 10), 1
        )

        # ── Left: Inventory + Hotbar ──────────────────────
        self.inv_rects = []
        self.hotbar_inv_rects = []
        ox, oy = px + 16, py + 48

        # Inventory label
        inv_lbl = self.font_md.render("Inventory  (18 slots)", True, (160, 180, 220))
        self.screen.blit(inv_lbl, (ox, oy))
        oy += 22

        for i in range(18):
            row = i // 9
            col = i % 9
            sx = ox + col * (SLOT + SPAD)
            sy = oy + row * (SLOT + SPAD)
            r = pygame.Rect(sx, sy, SLOT, SLOT)
            self.inv_rects.append(r)

            is_src = self.inv_held_src == ("i", i)
            bg = (70, 50, 25) if is_src else (28, 32, 52)
            pygame.draw.rect(self.screen, bg, r, border_radius=4)
            pygame.draw.rect(self.screen, (55, 65, 95), r, 1, border_radius=4)

            slot = self.inv_slots[i]
            if slot and not is_src:
                bs = get_item_surf(slot["b"])
                if bs:
                    sc = pygame.transform.scale(bs, (SLOT - 10, SLOT - 10))
                    self.screen.blit(sc, (sx + 5, sy + 5))
                cnt = self.font_sm.render(str(slot["n"]), True, (255, 255, 255))
                self.screen.blit(cnt, (sx + SLOT - cnt.get_width() - 3, sy + SLOT - 14))

        oy += 2 * (SLOT + SPAD) + 14

        # Hotbar label
        hb_lbl = self.font_md.render(
            "Hotbar  (9 slots — used for placing blocks)", True, (160, 180, 220)
        )
        self.screen.blit(hb_lbl, (ox, oy))
        oy += 22

        for i in range(9):
            sx = ox + i * (SLOT + SPAD)
            sy = oy
            r = pygame.Rect(sx, sy, SLOT, SLOT)
            self.hotbar_inv_rects.append(r)

            is_src = self.inv_held_src == ("h", i)
            is_sel = i == (self.me.sel if self.me else 0)
            bg = (70, 55, 20) if is_src else (40, 44, 62)
            pygame.draw.rect(self.screen, bg, r, border_radius=4)
            bord = (255, 220, 50) if is_sel else (70, 80, 110)
            pygame.draw.rect(self.screen, bord, r, 2 if is_sel else 1, border_radius=4)

            slot = self.me.hotbar[i] if self.me and i < len(self.me.hotbar) else None
            if slot and not is_src:
                bs = get_item_surf(slot["b"])
                if bs:
                    sc = pygame.transform.scale(bs, (SLOT - 10, SLOT - 10))
                    self.screen.blit(sc, (sx + 5, sy + 5))
                cnt = self.font_sm.render(str(slot["n"]), True, (255, 255, 255))
                self.screen.blit(cnt, (sx + SLOT - cnt.get_width() - 3, sy + SLOT - 14))

        oy += SLOT + 14
        # Drag tip
        tip = self.font_sm.render(
            "LClick: pick up/place    RClick: split stack", True, (90, 90, 130)
        )
        self.screen.blit(tip, (ox, oy))

        # ── Right: Crafting ───────────────────────────────
        self.craft_btns = []
        cx2 = divider + 16
        cy2 = py + 48
        ROW_H = 72
        BTN_W = 72
        ROW_W = px + PW - cx2 - 10

        # Label
        cr_lbl = self.font_md.render("Crafting Recipes", True, (200, 220, 160))
        self.screen.blit(cr_lbl, (cx2, cy2))
        cy2 += 28

        # Search bar
        search_r = pygame.Rect(cx2, cy2, ROW_W - 4, 26)
        s_bg = (35, 38, 60) if self.craft_search_active else (25, 28, 48)
        pygame.draw.rect(self.screen, s_bg, search_r, border_radius=4)
        pygame.draw.rect(
            self.screen,
            (100, 130, 220) if self.craft_search_active else (55, 65, 95),
            search_r,
            1,
            border_radius=4,
        )
        search_hint = self.craft_search if self.craft_search else "Search recipes..."
        search_col = (240, 240, 240) if self.craft_search else (80, 80, 110)
        cursor_str = (
            "|" if self.craft_search_active and int(time.time() * 2) % 2 == 0 else ""
        )
        ss = self.font_sm.render(search_hint + cursor_str, True, search_col)
        self.screen.blit(ss, (search_r.x + 6, search_r.y + 6))
        cy2 += 32

        # Filter recipes
        term = self.craft_search.lower()
        filtered = [
            (i, r)
            for i, r in enumerate(CRAFT_RECIPES)
            if not term or term in r[0].lower()
        ]

        # Clip region — recipes don't draw outside the panel
        craft_area_top = cy2
        craft_area_bot = py + PH - 10
        craft_area_h = craft_area_bot - craft_area_top
        clip_r = pygame.Rect(cx2 - 2, craft_area_top, ROW_W + 4, craft_area_h)
        self.screen.set_clip(clip_r)

        # Clamp scroll
        max_scroll = max(0, len(filtered) * ROW_H - craft_area_h)
        self.craft_scroll = max(0, min(self.craft_scroll, max_scroll))

        for draw_i, (real_i, (name, result_blk, result_n, ingredients)) in enumerate(
            filtered
        ):
            ry = cy2 + draw_i * ROW_H - self.craft_scroll

            # Skip rows fully outside clip
            if ry + ROW_H < craft_area_top or ry > craft_area_bot:
                continue

            # Check if player can craft
            can_craft = True
            for blk, count in ingredients.items():
                total = 0
                if self.me:
                    for s in self.me.hotbar:
                        if s and s["b"] == blk:
                            total += s["n"]
                for s in self.inv_slots:
                    if s and s["b"] == blk:
                        total += s["n"]
                if total < count:
                    can_craft = False
                    break

            # Row background
            rr = pygame.Rect(cx2, ry, ROW_W, ROW_H - 4)
            rbg = (22, 40, 22) if can_craft else (22, 22, 32)
            pygame.draw.rect(self.screen, rbg, rr, border_radius=5)
            rbd = (50, 100, 50) if can_craft else (45, 45, 65)
            pygame.draw.rect(self.screen, rbd, rr, 1, border_radius=5)

            # Result icon
            bs = get_item_surf(result_blk)
            if bs:
                sc = pygame.transform.scale(bs, (SLOT - 4, SLOT - 4))
                self.screen.blit(sc, (cx2 + 4, ry + 4))

            # Recipe name + result count
            rname_col = (220, 230, 180) if can_craft else (140, 140, 140)
            rname_s = self.font_md.render(f"{name}  → ×{result_n}", True, rname_col)
            self.screen.blit(rname_s, (cx2 + SLOT + 4, ry + 4))

            # Ingredients list
            tx = cx2 + SLOT + 4
            for blk, count in ingredients.items():
                avail = 0
                if self.me:
                    for s in self.me.hotbar:
                        if s and s["b"] == blk:
                            avail += s["n"]
                for s in self.inv_slots:
                    if s and s["b"] == blk:
                        avail += s["n"]
                pc_ = (120, 200, 120) if avail >= count else (200, 100, 100)
                ps = self.font_sm.render(
                    f"{count}× {BLOCK_NAMES.get(blk, '?')} ({avail})", True, pc_
                )
                self.screen.blit(ps, (tx, ry + 26))
                tx += ps.get_width() + 10

            # Craft button
            btn_r = pygame.Rect(
                cx2 + ROW_W - BTN_W - 4, ry + ROW_H // 2 - 16, BTN_W, 32
            )
            self.craft_btns.append((btn_r, real_i, can_craft))
            bc_ = (0, 150, 55) if can_craft else (50, 50, 55)
            bbd_ = (0, 220, 90) if can_craft else (70, 70, 80)
            mx2r, my2r = pygame.mouse.get_pos()
            hov_ = btn_r.collidepoint(mx2r, my2r) and can_craft
            if hov_:
                bc_ = (0, 180, 70)
            pygame.draw.rect(self.screen, bc_, btn_r, border_radius=5)
            pygame.draw.rect(self.screen, bbd_, btn_r, 1, border_radius=5)
            bs_txt = self.font_sm.render(
                "CRAFT", True, (255, 255, 255) if can_craft else (100, 100, 100)
            )
            self.screen.blit(
                bs_txt, (btn_r.x + btn_r.w // 2 - bs_txt.get_width() // 2, btn_r.y + 10)
            )

        # Remove clip
        self.screen.set_clip(None)

        # Scrollbar
        if max_scroll > 0:
            sb_x = cx2 + ROW_W + 2
            sb_h = craft_area_h
            thumb_h = max(
                24, int(craft_area_h * craft_area_h / (len(filtered) * ROW_H))
            )
            thumb_y = craft_area_top + int(
                (craft_area_h - thumb_h) * self.craft_scroll / max_scroll
            )
            pygame.draw.rect(
                self.screen,
                (35, 38, 58),
                (sb_x, craft_area_top, 6, sb_h),
                border_radius=3,
            )
            pygame.draw.rect(
                self.screen,
                (90, 110, 180),
                (sb_x, thumb_y, 6, thumb_h),
                border_radius=3,
            )

        # Scroll hint
        if filtered:
            hint_col = (70, 70, 100)
            sh = self.font_sm.render(
                "Scroll to see more" if max_scroll > 0 else "", True, hint_col
            )
            self.screen.blit(sh, (cx2, craft_area_bot + 2))

        # Held item follows cursor
        if self.inv_held:
            mx2c, my2c = pygame.mouse.get_pos()
            bs = get_item_surf(self.inv_held["b"])
            if bs:
                sc = pygame.transform.scale(bs, (SLOT - 4, SLOT - 4))
                self.screen.blit(sc, (mx2c - SLOT // 2 + 2, my2c - SLOT // 2 + 2))
            cnt = self.font_md.render(str(self.inv_held["n"]), True, (255, 255, 255))
            self.screen.blit(cnt, (mx2c + SLOT // 2 - 18, my2c + SLOT // 2 - 16))

    # ─────── Death Screen ─────────────────────────────────
    def _draw_death_screen(self):
        ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
        ov.fill((80, 0, 0, 200))
        self.screen.blit(ov, (0, 0))

        pulse = 0.85 + 0.15 * math.sin(time.time() * 3)
        font_death = pygame.font.SysFont("Arial", int(80 * pulse), bold=True)
        title = font_death.render("YOU DIED", True, (255, 60, 60))
        shadow = font_death.render("YOU DIED", True, (80, 0, 0))
        tx, ty = SW // 2 - title.get_width() // 2, SH // 2 - 130
        self.screen.blit(shadow, (tx + 4, ty + 4))
        self.screen.blit(title, (tx, ty))

        skull_font = pygame.font.SysFont("Arial", 48)
        sk = skull_font.render("💀", True, (200, 50, 50))
        self.screen.blit(sk, (tx - 64, ty + 16))
        self.screen.blit(sk, (tx + title.get_width() + 14, ty + 16))

        if self.killed_by:
            ks = self.font_lg.render(
                f"Slain by  {self.killed_by}", True, (255, 160, 160)
            )
            self.screen.blit(ks, (SW // 2 - ks.get_width() // 2, SH // 2 - 30))

        mx, my = pygame.mouse.get_pos()
        hov = self.respawn_btn.collidepoint(mx, my)
        pygame.draw.rect(
            self.screen,
            (200, 50, 50) if hov else (140, 30, 30),
            self.respawn_btn,
            border_radius=10,
        )
        pygame.draw.rect(
            self.screen,
            (255, 120, 120) if hov else (200, 80, 80),
            self.respawn_btn,
            3,
            border_radius=10,
        )
        rs = self.font_lg.render("RESPAWN", True, (255, 230, 230))
        self.screen.blit(
            rs,
            (
                SW // 2 - rs.get_width() // 2,
                self.respawn_btn.y
                + self.respawn_btn.height // 2
                - rs.get_height() // 2,
            ),
        )

        hs = self.font_sm.render(
            "Press R or Space to respawn  •  Esc to quit to menu", True, (180, 100, 100)
        )
        self.screen.blit(hs, (SW // 2 - hs.get_width() // 2, SH // 2 + 160))

    # ─────── Debug ────────────────────────────────────────
    def _draw_debug(self):
        if not self.me:
            return
        bx = int(self.me.x // TILE)
        by = int(self.me.y // TILE)
        b = self.world.get(bx, by) if self.world else 0
        lines = [
            f"XY: {self.me.x:.0f}, {self.me.y:.0f}",
            f"Block: {bx},{by}  ({BLOCK_NAMES.get(b, b)})",
            f"Vel: {self.me.vx:.1f},{self.me.vy:.1f}",
            f"Grnd: {self.me.on_ground}",
            f"Time: {self.gtime:.0f}  Light: {_light(self.gtime):.2f}",
            f"FPS:  {self.clock.get_fps():.1f}",
            f"Others: {len(self.others)}",
        ]
        for i, v in enumerate(lines):
            s = self.font_sm.render(v, True, (200, 255, 200))
            sb = pygame.Surface(
                (s.get_width() + 6, s.get_height() + 2), pygame.SRCALPHA
            )
            sb.fill((0, 0, 0, 140))
            self.screen.blit(sb, (SW - sb.get_width() - 8, 50 + i * 16))
            self.screen.blit(s, (SW - s.get_width() - 11, 51 + i * 16))


# ──────────────────────── Entry ───────────────────────────
if __name__ == "__main__":
    Game().run()
