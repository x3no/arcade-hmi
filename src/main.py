#!/usr/bin/env python3
"""
Arcade Control Panel - Main Application
Touchscreen interface for arcade machine control
"""

import os
import sys
import time
import platform
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import pygame
from pygame.locals import *

from config import Config
from usb_hid import KeyCode, Modifier
from bluetooth_hid import USBHID
from gpio_controller import GPIOController
from keyboard_mapper import ArcadeKeyMapper


# Detect if running on a Raspberry Pi
IS_PI = platform.machine() in ('aarch64', 'armv7l', 'armv6l')

# Configure SDL to use X11
os.environ['SDL_VIDEODRIVER'] = 'x11'
if IS_PI:
    os.environ['SDL_NOMOUSE'] = '1'
    # Do NOT override SDL_RENDER_DRIVER: opengles2 breaks pygame.SCALED on Pi
    # (X11 + fkms — surface renders at 640x360 unscaled in center of screen).
    # SDL2 defaults to software rendering here, which handles SCALED correctly
    # and still benefits from the 9x reduced pixel count at RS=1/3.
    # Enable VSync at the SDL2 renderer level to eliminate tearing
    os.environ.setdefault('SDL_RENDER_VSYNC', '1')

# Color palette
C_BG     = (180, 0, 120)     # DEBUG: bright magenta
C_WHITE  = (255, 255, 255)  # White
C_GRAY   = (140, 140, 140)  # Mid gray
C_DARK   = (20, 20, 20)     # Near-black for fills
C_ORANGE = (255, 140, 0)    # Pressed state
C_BTN    = (55,  55,  55)   # Action button background
# Disabled-state colours — defined once at module level, not inside every draw()
C_DISABLED_BG   = (25, 25, 25)
C_DISABLED_TEXT = (70, 70, 70)

# ── Render-scale infrastructure ──────────────────────────────────────────────
# On Pi we render at ONE-THIRD of native resolution and let SDL2 SCALED + GPU
# upscale 3× to the physical display — 9× fewer pixels drawn per frame.
# 1920/3 = 640, 1080/3 = 360: perfect integer scaling, no sub-pixel blur.
# Set ARCADE_FORCE_SCALE=1 in the environment to test scaled mode on a desktop.
_force_scale = os.environ.get('ARCADE_FORCE_SCALE') == '1'
RS = 1/3 if (IS_PI or _force_scale) else 1.0   # render-to-native scale factor


def _s(v):
    """Scale a design-pixel value to the render resolution (always ≥ 1)."""
    return max(1, int(v * RS))


# ── Global font-render cache ──────────────────────────────────────────────────
# FreeType rasterisation is expensive on the Pi; cache rendered surfaces so
# static text (button labels, tab names, status strings) is only rasterised once.
_TEXT_CACHE: dict = {}


def _rt(font, text, color):
    """Return a cached rendered text Surface (avoids repeated FreeType calls)."""
    key = (id(font), str(text), color)
    surf = _TEXT_CACHE.get(key)
    if surf is None:
        if len(_TEXT_CACHE) > 400:          # evict oldest 200 when cache is fat
            for k in list(_TEXT_CACHE)[:200]:
                del _TEXT_CACHE[k]
        surf = font.render(str(text), True, color)
        _TEXT_CACHE[key] = surf
    return surf


class SimpleButton:
    """Simple square button: black fill, white 3px border, white text."""

    def __init__(self, rect, text, color=None, action=None, icon=None, hold_action=None, disabled=False):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.action = action
        self.hold_action = hold_action  # called repeatedly after 3s hold
        self._press_time = 0            # set when is_pressed goes True
        self._hold_fired = False        # True once repeat has started
        self.is_pressed = False
        self.icon = icon
        self.disabled = disabled

    def draw(self, surface, font, font_icon=None):
        if self.disabled:
            bg, fg = C_DISABLED_BG, C_DISABLED_TEXT
        else:
            bg = C_ORANGE if self.is_pressed else C_BTN
            fg = C_WHITE
        pygame.draw.rect(surface, bg, self.rect)
        if self.icon and font_icon:
            icon_surf = _rt(font_icon, self.icon, fg)
            text_surf = _rt(font, self.text, fg)
            gap       = _s(6)
            total_h   = icon_surf.get_height() + gap + text_surf.get_height()
            top       = self.rect.centery - total_h // 2
            surface.blit(icon_surf, icon_surf.get_rect(centerx=self.rect.centerx, top=top))
            surface.blit(text_surf, text_surf.get_rect(centerx=self.rect.centerx,
                                                        top=top + icon_surf.get_height() + gap))
        else:
            text_surf = _rt(font, self.text, fg)
            surface.blit(text_surf, text_surf.get_rect(center=self.rect.center))

    def handle_event(self, event):
        if self.disabled:
            return False
        if event.type == MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                self.is_pressed = True
                return True
        elif event.type == MOUSEBUTTONUP:
            if self.is_pressed:
                self.is_pressed = False
                if self.rect.collidepoint(event.pos) and self.action:
                    self.action()
                return True
        return False


class TabButton:
    """Tab/slot selector. style='underline' (main tabs) or style='box' (slot sub-tabs)."""

    def __init__(self, rect, text, active=False, action=None, icon=None, style='underline'):
        self.rect   = pygame.Rect(rect)
        self.text   = text
        self.active = active
        self.action = action
        self.icon   = icon
        self.style  = style

    def draw(self, surface, font, font_icon=None):
        pygame.draw.rect(surface, C_BG, self.rect)

        if self.style == 'box':
            if self.active:
                pygame.draw.rect(surface, C_WHITE, self.rect)
                text_surf = _rt(font, self.text, C_BG)
            else:
                pygame.draw.rect(surface, C_GRAY, self.rect, max(1, _s(3)))
                text_surf = _rt(font, self.text, C_GRAY)
            surface.blit(text_surf, text_surf.get_rect(center=self.rect.center))
            return

        # underline style
        fg = C_WHITE if self.active else C_GRAY
        if self.icon and font_icon:
            icon_surf = _rt(font_icon, self.icon, fg)
            text_surf = _rt(font, self.text, fg)
            gap       = _s(8)
            total_w   = icon_surf.get_width() + gap + text_surf.get_width()
            x         = self.rect.centerx - total_w // 2
            cy        = self.rect.centery
            surface.blit(icon_surf, icon_surf.get_rect(left=x, centery=cy))
            surface.blit(text_surf, text_surf.get_rect(left=x + icon_surf.get_width() + gap, centery=cy))
            line_y = cy + max(icon_surf.get_height(), text_surf.get_height()) // 2 + _s(4)
        else:
            text_surf = _rt(font, self.text, fg)
            r = text_surf.get_rect(center=self.rect.center)
            surface.blit(text_surf, r)
            line_y = r.bottom + _s(4)

        line_color = C_WHITE if self.active else C_GRAY
        pygame.draw.line(surface, line_color,
                         (self.rect.left, line_y), (self.rect.right, line_y), max(1, _s(3)))

    def handle_event(self, event):
        if event.type == MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                if self.action:
                    self.action()
                return True
        return False


class ScrollMenu:
    """
    Horizontally scrollable button strip with inertia and overscroll bounce.
    Each button's rect is stored as a content-relative position (x from 0).
    """

    FRICTION       = 0.88   # velocity multiplier per frame (1.0 = no decay, 0 = instant stop)
    SPRING         = 0.18   # overscroll snap-back per frame (lower = smoother bounce)
    # Drag threshold scales with render resolution so the physical finger
    # movement needed to trigger a scroll is constant regardless of RS.
    DRAG_THRESHOLD = max(4, int(25 * RS))

    def __init__(self, rect, buttons, btn_w, btn_gap):
        self.rect    = pygame.Rect(rect)
        self.buttons = buttons
        self.btn_gap = btn_gap

        # 4 or fewer buttons: expand to fill full width (scroll disabled)
        if len(buttons) <= 4:
            btn_w = (self.rect.width - btn_gap * (len(buttons) + 1)) // len(buttons)
        self.btn_w   = btn_w

        btn_h = min(_s(400), self.rect.height - _s(15) - _s(20))  # 15px top + 20px bottom padding
        btn_y = self.rect.y + _s(15)

        for i, btn in enumerate(self.buttons):
            btn.rect = pygame.Rect(
                btn_gap + i * (btn_w + btn_gap),
                btn_y,
                btn_w,
                btn_h,
            )

        self.content_w  = btn_gap + len(buttons) * (btn_w + btn_gap)
        self.min_scroll = 0
        self.max_scroll = max(0, self.content_w - self.rect.width)

        self.scroll_x      = 0.0
        self.velocity      = 0.0
        self.dragging      = False
        self.drag_origin_x = 0
        self.drag_scroll0  = 0.0
        self.drag_last_x   = 0
        self.drag_last_t   = 0
        self.is_scroll     = False
        self.pressed_btn   = None
        # Callback injected by the app to invalidate the static UI cache on press
        self._cache_dirty_ref = lambda: None

    def _screen_rect(self, btn):
        return pygame.Rect(
            self.rect.x + btn.rect.x - int(self.scroll_x),
            btn.rect.y,
            btn.rect.width,
            btn.rect.height,
        )

    def update(self):
        if self.dragging:
            return

        self.scroll_x += self.velocity
        self.velocity  *= self.FRICTION

        if self.scroll_x < self.min_scroll:
            self.scroll_x += (self.min_scroll - self.scroll_x) * self.SPRING
            self.velocity  *= 0.7
        elif self.scroll_x > self.max_scroll:
            self.scroll_x += (self.max_scroll - self.scroll_x) * self.SPRING
            self.velocity  *= 0.7

        if abs(self.velocity) < 0.05:
            self.velocity = 0.0

    def handle_event(self, event):
        if event.type == MOUSEBUTTONDOWN and event.button == 1:
            if not self.rect.collidepoint(event.pos):
                return False
            self.dragging      = True
            self.is_scroll     = False
            self.drag_origin_x = event.pos[0]
            self.drag_scroll0  = self.scroll_x
            self.drag_last_x   = event.pos[0]
            self.drag_last_t   = pygame.time.get_ticks()
            self.velocity      = 0.0
            for btn in self.buttons:
                if self._screen_rect(btn).collidepoint(event.pos) and not getattr(btn, 'disabled', False):
                    btn.is_pressed   = True
                    btn._press_time  = pygame.time.get_ticks()
                    btn._hold_fired  = False
                    self.pressed_btn = btn
                    # Pressing a button changes its visual state → invalidate static cache
                    self._cache_dirty_ref()
                    break
            return True

        elif event.type == MOUSEMOTION:
            if not self.dragging:
                return False
            dx = event.pos[0] - self.drag_origin_x
            if abs(dx) > self.DRAG_THRESHOLD and self.max_scroll > 0:
                self.is_scroll = True
                if self.pressed_btn:
                    self.pressed_btn.is_pressed = False
                    self.pressed_btn = None

            if not self.is_scroll:
                return True

            now = pygame.time.get_ticks()
            dt  = max(1, now - self.drag_last_t)
            self.velocity    = -(event.pos[0] - self.drag_last_x) / dt * (1000 / 30)
            self.drag_last_x = event.pos[0]
            self.drag_last_t = now

            raw = self.drag_scroll0 - dx
            if raw < self.min_scroll:
                over = self.min_scroll - raw
                raw  = self.min_scroll - over * 0.35
            elif raw > self.max_scroll:
                over = raw - self.max_scroll
                raw  = self.max_scroll + over * 0.35
            self.scroll_x = raw
            return True

        elif event.type == MOUSEBUTTONUP and event.button == 1:
            if not self.dragging:
                return False
            self.dragging = False
            # Keep velocity so inertia carries through after finger lift,
            # but reduce to 1/4 so it feels light, not heavy.
            self.velocity *= 0.25
            # Clamp overscroll boundaries (spring in update() will ease back).
            if self.scroll_x < self.min_scroll:
                self.scroll_x = self.min_scroll
                self.velocity = 0.0
            elif self.scroll_x > self.max_scroll:
                self.scroll_x = self.max_scroll
                self.velocity = 0.0
            if not self.is_scroll and self.pressed_btn:
                self.pressed_btn.is_pressed = False
                if self.pressed_btn.action and not self.pressed_btn._hold_fired:
                    self.pressed_btn.action()
                self._cache_dirty_ref()
                self.pressed_btn = None
            elif self.pressed_btn:
                self.pressed_btn.is_pressed = False
                self._cache_dirty_ref()
                self.pressed_btn = None
            return True

        return False

    def draw(self, surface, font, font_icon=None):
        clip = surface.get_clip()
        surface.set_clip(self.rect)
        c = _s(40)  # corner cut size (scaled)
        for btn in self.buttons:
            sr = self._screen_rect(btn)
            if sr.right <= self.rect.left or sr.left >= self.rect.right:
                continue
            disabled = getattr(btn, 'disabled', False)
            if disabled:
                bg, fg = C_DISABLED_BG, C_DISABLED_TEXT
            else:
                bg = C_ORANGE if btn.is_pressed else C_BTN
                fg = C_WHITE
            # Draw button with cut bottom-right corner
            l, t, r, b = sr.left, sr.top, sr.right, sr.bottom
            pts = [(l, t), (r, t), (r, b - c), (r - c, b), (l, b)]
            pygame.draw.polygon(surface, bg, pts)
            if btn.icon and font_icon:
                icon_surf = _rt(font_icon, btn.icon, fg)
                text_surf = _rt(font, btn.text, fg)
                gap       = _s(6)
                total_h   = icon_surf.get_height() + gap + text_surf.get_height()
                top       = sr.centery - total_h // 2
                surface.blit(icon_surf, icon_surf.get_rect(centerx=sr.centerx, top=top))
                surface.blit(text_surf, text_surf.get_rect(centerx=sr.centerx,
                                                            top=top + icon_surf.get_height() + gap))
            else:
                text_surf = _rt(font, btn.text, fg)
                surface.blit(text_surf, text_surf.get_rect(center=sr.center))
        surface.set_clip(clip)


class ArcadeControlApp:
    """Main application class"""
    
    def __init__(self):
        """Initialize application"""
        # Initialize pygame without audio (no sound card available)
        pygame.display.init()
        pygame.font.init()
        
        print(f"SDL Video Driver: {pygame.display.get_driver()}")
        
        # Load configuration
        self.config = Config()
        
        # Screen setup
        # On Pi: render at one-third native resolution; SDL2 SCALED + GPU upscales 3×.
        # DOUBLEBUF + vsync=1 eliminates tearing.
        # Auto-detect actual screen resolution so scaling is always correct regardless
        # of what resolution the monitor negotiated over HDMI.
        _info = pygame.display.Info()
        if _info.current_w > 0 and _info.current_h > 0:
            phys_w, phys_h = _info.current_w, _info.current_h
        else:
            phys_w = self.config['screen_width']
            phys_h = self.config['screen_height']
        self.width  = int(phys_w * RS)
        self.height = int(phys_h * RS)
        print(f"Render: {self.width}x{self.height}  native: {phys_w}x{phys_h}  RS={RS}")
        if IS_PI or _force_scale:
            flags = pygame.FULLSCREEN | pygame.SCALED | pygame.DOUBLEBUF
        else:
            flags = 0
        self.screen = pygame.display.set_mode((self.width, self.height), flags, vsync=1)
        print(f"Display created: surface={self.screen.get_size()}  flags={flags:#010x}")
        print(f"  desktop_sizes={pygame.display.get_desktop_sizes()}")
        _info2 = pygame.display.Info()
        print(f"  display.Info after set_mode: {_info2.current_w}x{_info2.current_h}")
        pygame.display.set_caption("Arcade Control")

        # Helper: scale a design font-size to the render resolution
        def _fs(size):
            return max(6, int(size * RS))

        # Fonts
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'Rajdhani-Bold.ttf')
            self.font        = pygame.font.Font(font_path, _fs(72))
            self.font_large  = pygame.font.Font(font_path, _fs(108))
            self.font_action = pygame.font.Font(font_path, _fs(36))
        except:
            self.font        = pygame.font.Font(None, _fs(72))
            self.font_large  = pygame.font.Font(None, _fs(96))
            self.font_action = pygame.font.Font(None, _fs(36))

        self.bg_color = C_BG
        self.text_color = C_WHITE

        # Smaller font for tab bar and slot sub-tabs
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'Rajdhani-Bold.ttf')
            self.font_tab  = pygame.font.Font(font_path, _fs(42))
            self.font_slot = pygame.font.Font(font_path, _fs(30))
        except:
            self.font_tab  = pygame.font.Font(None, _fs(42))
            self.font_slot = pygame.font.Font(None, _fs(30))

        # Monospace font for clock — bundled with the project
        try:
            mono_path = os.path.join(os.path.dirname(__file__), 'fonts', 'NotoSansMono-Regular.ttf')
            self.font_mono = pygame.font.Font(mono_path, _fs(34))
        except:
            self.font_mono = pygame.font.SysFont('monospace', _fs(34))

        # Material Symbols icon font
        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'fonts', 'MaterialSymbols.ttf')
            _f = pygame.font.Font(icon_path, _fs(52))
            _f.render('test', True, (255, 255, 255))  # validate not a null/variable font
            self.font_icon        = _f
            self.font_icon_sm     = pygame.font.Font(icon_path, _fs(36))
            self.font_icon_action = pygame.font.Font(icon_path, _fs(80))
            self.font_icon_lock   = pygame.font.Font(icon_path, _fs(180))
        except Exception as e:
            print(f"[WARN] Icon font not loaded: {e}")
            self.font_icon        = None
            self.font_icon_sm     = None
            self.font_icon_action = None
            self.font_icon_lock   = None

        # Static-UI render cache: rebuilt only when tabs/buttons change state
        self._main_cache       = None
        self._main_cache_dirty = True
        # Idle-frame-skip: track last rendered second so we skip identical frames
        self._prev_second      = -1

        # State
        self.running = True
        self.locked = True
        self.screen_on = True
        self.pin_input = ""
        self.confirmation_dialog = None
        self.debug_screen = False
        self.bt_screen = False
        self.bt_data = {'devices': [], 'server_active': False}
        self.bt_status_msg = ''
        self.bt_logs = []
        self.bt_log_scroll = 0   # lines scrolled from bottom (0 = show newest)
        self.bt_remove_buttons = []
        self.bt_activate_btn  = None
        self.bt_refresh_btn   = None
        self.bt_close_btn     = None
        self.bt_diagnose_btn  = None
        self.bt_connected = False  # live BT connection indicator
        self.wifi_connected = False
        self.pc_on = False          # driven by GPIO Power LED
        self.pc_on_since = None     # time.time() when PC turned on
        self.hdd_on = False         # driven by GPIO HDD LED
        self.coin_p1_count = 0
        self.coin_p2_count = 0
        self.weather_data = None    # {'temp': float, 'condition': str}
        self._start_bt_status_poller()
        self._start_wifi_poller()
        self._start_weather_poller()
        self.current_tab  = 'sistema'
        self.current_slot = 0  # 0 = general, 1-9 = save slots

        # Hardware controllers
        self.hid = None
        self.gpio = GPIOController(self.config['gpio_power_pin'])
        self.pc_on = self.gpio.read_power_led()
        self.pc_on_since = time.time() if self.pc_on else None
        self._start_gpio_poller()

        # Clock
        self.clock = pygame.time.Clock()

        # UI Elements
        self.numpad_buttons = []

        self.setup_ui()
        
    def setup_ui(self):
        """Setup UI elements"""
        # Numpad (lock screen) — centered in full screen
        numpad_layout = [
            ['1', '2', '3'],
            ['4', '5', '6'],
            ['7', '8', '9'],
            ['C', '0', 'OK']
        ]
        GAP_N    = _s(8)
        NUM_ROWS = 4
        NUM_COLS = 3
        NUM_TOP  = _s(310)                              # below icon + pin dots
        PAD_SIDE = _s(226)                              # horizontal margin
        PAD_BOT  = _s(20)
        avail_w  = self.width - PAD_SIDE * 2
        avail_h  = self.height - NUM_TOP - PAD_BOT
        btn_w    = (avail_w - GAP_N * (NUM_COLS - 1)) // NUM_COLS
        btn_h    = (avail_h - GAP_N * (NUM_ROWS - 1)) // NUM_ROWS
        numpad_w = NUM_COLS * btn_w + (NUM_COLS - 1) * GAP_N
        numpad_h = NUM_ROWS * btn_h + (NUM_ROWS - 1) * GAP_N
        start_x  = (self.width - numpad_w) // 2
        start_y  = NUM_TOP + (avail_h - numpad_h) // 2
        for row_idx, row in enumerate(numpad_layout):
            for col_idx, label in enumerate(row):
                x = start_x + col_idx * (btn_w + GAP_N)
                y = start_y + row_idx * (btn_h + GAP_N)
                self.numpad_buttons.append(SimpleButton(
                    (x, y, btn_w, btn_h),
                    label, action=lambda l=label: self.numpad_press(l)
                ))

        # Layout constants
        GAP      = _s(4)
        TAB_Y    = _s(90)
        TAB_H    = _s(140)
        TAB_W    = (self.width - GAP * 3) // 4   # 4 tabs with 3 gaps between
        SLOT_Y   = TAB_Y + TAB_H + _s(10)
        SLOT_H   = _s(130)
        SLOT_W   = (self.width - GAP * 9) // 10  # 10 items (GENERAL + 1-9) with 9 gaps
        CONT_Y   = TAB_Y + TAB_H        # non-Partida content starts here
        CONT_Y_P = SLOT_Y + SLOT_H      # Partida content starts here

        # Main tab bar (4 tabs)
        # Material Icons codepoints (static font, U+E000 range)
        tab_defs = [
            ('sistema',  'SISTEMA',  '\ue30a'),  # computer
            ('sonido',   'SONIDO',   '\ue050'),  # volume_up
            ('partida',  'PARTIDA',  '\ue30f'),  # gamepad
            ('monedero', 'MONEDERO', '\ue850'),  # account_balance_wallet
        ]
        self.tab_buttons = [
            TabButton(
                (i * (TAB_W + GAP), TAB_Y, TAB_W, TAB_H),
                name,
                active=(tid == self.current_tab),
                action=lambda t=tid: self.switch_tab(t),
                icon=icon,
            )
            for i, (tid, name, icon) in enumerate(tab_defs)
        ]

        # Slot sub-tabs: GENERAL first, then 1-9
        slot_labels = ['GENERAL'] + [str(i) for i in range(1, 10)]
        self.slot_buttons = [
            TabButton(
                (i * (SLOT_W + GAP), SLOT_Y, SLOT_W, SLOT_H),
                label,
                active=(i == self.current_slot),
                action=lambda s=i: self.switch_slot(s),
                style='box',
            )
            for i, label in enumerate(slot_labels)
        ]

        # Per-tab scroll menus
        def make_scroll(y, h, btns):
            return ScrollMenu((0, y, self.width, h), btns, btn_w=_s(340), btn_gap=_s(20))

        CLOCK_H  = _s(60)                        # reserved for bottom status bar
        h_norm = self.height - CONT_Y - CLOCK_H
        h_part = self.height - CONT_Y_P - CLOCK_H

        self.partida_general_btns = [
            SimpleButton((0,0,0,0), "PAUSAR",       action=self.pause_game,    icon='\ue034', disabled=True),
            SimpleButton((0,0,0,0), "INFO",         action=self.game_info,     icon='\ue88e', disabled=True),
            SimpleButton((0,0,0,0), "FAST FORWARD", action=self.fast_forward,  icon='\ue01f', disabled=True),
            SimpleButton((0,0,0,0), "SCREENSHOT",   action=self.screenshot,    icon='\ue3b0', disabled=True),
            SimpleButton((0,0,0,0), "MENU",         action=self.mame_menu,     icon='\ue5d2', disabled=True),
            SimpleButton((0,0,0,0), "REINICIAR",    action=self.restart_game,  icon='\ue5d5', disabled=True),
            SimpleButton((0,0,0,0), "SALIR",        action=self.exit_game,     icon='\ue9ba', disabled=True),
        ]
        self.partida_slot_btns = [
            SimpleButton((0,0,0,0), "SAVE", action=self.save_state, icon='\ue161', disabled=True),
            SimpleButton((0,0,0,0), "LOAD", action=self.load_state, icon='\ue2c4', disabled=True),
        ]
        self.partida_general_scroll = make_scroll(CONT_Y_P, h_part, self.partida_general_btns)
        self.partida_slot_scroll    = make_scroll(CONT_Y_P, h_part, self.partida_slot_btns)

        self.power_btn = SimpleButton(
            (0,0,0,0), 'ENCENDER PC', action=self.toggle_power_confirm, icon='\ue8ac'
        )
        self.reset_btn = SimpleButton(
            (0,0,0,0), 'REINICIAR PC', action=self.reset_pc_confirm, icon='\ue5d5', disabled=True
        )
        self.tab_scroll_menus = {
            'sistema':  make_scroll(CONT_Y, h_norm, [
                self.power_btn,
                self.reset_btn,
                SimpleButton((0,0,0,0), "PANTALLA OFF", action=self.screen_off,        icon='\ue8d9'),  # screen_lock_power
                SimpleButton((0,0,0,0), "BLOQUEAR",     action=self.lock_screen,       icon='\ue897'),  # lock
                SimpleButton((0,0,0,0), "WiFi",         action=self.open_debug,        icon='\ue63e'),  # wifi
                SimpleButton((0,0,0,0), "UPDATE",       action=self.update_confirm,    icon='\ue923'),  # system_update
                SimpleButton((0,0,0,0), "BLUETOOTH",    action=self.open_bt_screen,    icon='\ue1a8'),  # bluetooth
            ]),
            'sonido':   make_scroll(CONT_Y, h_norm, [
                SimpleButton((0,0,0,0), "VOL +", action=self.volume_up,   icon='\ue050', hold_action=self.volume_up,   disabled=True),
                SimpleButton((0,0,0,0), "VOL -", action=self.volume_down, icon='\ue04d', hold_action=self.volume_down, disabled=True),
                SimpleButton((0,0,0,0), "MUTE",  action=self.mute,        icon='\ue04f',                               disabled=True),
            ]),
            'monedero': make_scroll(CONT_Y, h_norm, [
                SimpleButton((0,0,0,0), "COIN P1", action=self.coin_p1, icon='\ue227', disabled=True),
                SimpleButton((0,0,0,0), "COIN P2", action=self.coin_p2, icon='\ue227', disabled=True),
            ]),
        }
            
        # Collect all buttons that require an active BT connection
        self.bt_action_btns = (
            self.partida_general_btns
            + self.partida_slot_btns
            + self.tab_scroll_menus['sonido'].buttons
            + self.tab_scroll_menus['monedero'].buttons
        )

        # Wire cache-invalidation callback into every scroll menu so button
        # press/release triggers a static-cache rebuild before the next draw.
        def _mark_cache_dirty():
            self._main_cache_dirty = True

        all_menus = list(self.tab_scroll_menus.values()) + [
            self.partida_general_scroll, self.partida_slot_scroll
        ]
        for menu in all_menus:
            menu._cache_dirty_ref = _mark_cache_dirty

    def _active_scroll_menu(self):
        """Return the scroll menu for the currently active tab/sub-tab."""
        if self.current_tab == 'partida':
            return self.partida_general_scroll if self.current_slot == 0 else self.partida_slot_scroll
        return self.tab_scroll_menus[self.current_tab]

    def switch_tab(self, tab_id):
        self.current_tab = tab_id
        tab_ids = ['sistema', 'sonido', 'partida', 'monedero']
        for btn, tid in zip(self.tab_buttons, tab_ids):
            btn.active = (tid == tab_id)
        self._main_cache_dirty = True

    def switch_slot(self, slot):
        self.current_slot = slot
        for i, btn in enumerate(self.slot_buttons):
            btn.active = (i == slot)
        self._main_cache_dirty = True

    def numpad_press(self, key):
        """Handle numpad key press"""
        if key == 'C':
            self.pin_input = ""
        elif key == 'OK':
            if self.pin_input == self.config['pin']:
                self.unlock()
            else:
                self.pin_input = ""
        else:
            if len(self.pin_input) < 6:
                self.pin_input += key
                
    def unlock(self):
        """Unlock the interface"""
        self.locked = False
        self.screen_on = True
        self.pin_input = ""
        
    def lock_screen(self):
        """Lock the interface"""
        self.locked = True
        self.screen_on = False
        self.pin_input = ""
        
    def screen_off(self):
        """Turn screen off (standby)"""
        self.screen_on = False
        # Try to turn off HDMI via DPMS (requires vcgencmd)
        try:
            import subprocess
            subprocess.run(['vcgencmd', 'display_power', '0'], check=False)
        except:
            pass  # Silently fail if not available
        
    def wake_screen(self):
        """Wake screen from touch"""
        if not self.screen_on:
            self.screen_on = True
            # Turn on HDMI via DPMS
            try:
                import subprocess
                subprocess.run(['vcgencmd', 'display_power', '1'], check=False)
            except:
                pass
            
    # HID Actions
    def volume_up(self):
        """Increase volume"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(ArcadeKeyMapper.get_key('volume_up'))
        except Exception as e:
            print(f"Error sending volume up: {e}")
            
    def volume_down(self):
        """Decrease volume"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(ArcadeKeyMapper.get_key('volume_down'))
        except Exception as e:
            print(f"Error sending volume down: {e}")
            
    def mute(self):
        """Toggle mute"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(ArcadeKeyMapper.get_key('mute'))
        except Exception as e:
            print(f"Error sending mute: {e}")
            
    def coin_p1(self):
        """Insert coin for Player 1"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(ArcadeKeyMapper.get_key('coin_p1'))
        except Exception as e:
            print(f"Error sending coin P1: {e}")
            
    def coin_p2(self):
        """Insert coin for Player 2"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(ArcadeKeyMapper.get_key('coin_p2'))
        except Exception as e:
            print(f"Error sending coin P2: {e}")

    _SLOT_KEYS = [
        KeyCode.KEY_1, KeyCode.KEY_2, KeyCode.KEY_3,
        KeyCode.KEY_4, KeyCode.KEY_5, KeyCode.KEY_6,
        KeyCode.KEY_7, KeyCode.KEY_8, KeyCode.KEY_9,
    ]

    def save_state(self):
        """RetroArch: navigate to slot (F6/F7) then F2 = save state"""
        try:
            slot = self.current_slot  # 1-9
            with USBHID(self.config['hid_device']) as hid:
                # Slot 0 in RetroArch is default; slots 1-9 → press F7 N times
                # First reset to slot 0 via F6 * 9, then advance to desired slot
                for _ in range(9):
                    hid.send_key(KeyCode.KEY_F6)
                for _ in range(slot):
                    hid.send_key(KeyCode.KEY_F7)
                hid.send_key(KeyCode.KEY_F2)
        except Exception as e:
            print(f"Error saving state slot {self.current_slot}: {e}")

    def load_state(self):
        """RetroArch: navigate to slot (F6/F7) then F4 = load state"""
        try:
            slot = self.current_slot  # 1-9
            with USBHID(self.config['hid_device']) as hid:
                for _ in range(9):
                    hid.send_key(KeyCode.KEY_F6)
                for _ in range(slot):
                    hid.send_key(KeyCode.KEY_F7)
                hid.send_key(KeyCode.KEY_F4)
        except Exception as e:
            print(f"Error loading state slot {self.current_slot}: {e}")

    def pause_game(self):
        """RetroArch: P = pause toggle"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_P)
        except Exception as e:
            print(f"Error sending pause: {e}")

    def game_info(self):
        """RetroArch: F1 = menu (no dedicated info key)"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F1)
        except Exception as e:
            print(f"Error sending info: {e}")

    def fast_forward(self):
        """RetroArch: Space = fast-forward toggle"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_SPACE)
        except Exception as e:
            print(f"Error sending fast forward: {e}")

    def screenshot(self):
        """RetroArch: F8 = screenshot"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F8)
        except Exception as e:
            print(f"Error sending screenshot: {e}")

    def mame_menu(self):
        """RetroArch: F1 = menu toggle"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F1)
        except Exception as e:
            print(f"Error sending menu: {e}")

    def restart_game(self):
        """RetroArch: H = reset content"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_H)
        except Exception as e:
            print(f"Error sending restart: {e}")

    def exit_game(self):
        """RetroArch: ESC = quit"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_ESC)
        except Exception as e:
            print(f"Error sending exit: {e}")

    def toggle_power_confirm(self):
        """Show confirmation to toggle PC power (on→off or off→on)."""
        if self.pc_on:
            self.confirmation_dialog = {'title': '¿Apagar PC?',    'action': self.power_off}
        else:
            self.confirmation_dialog = {'title': '¿Encender PC?',  'action': self.power_on}

    # Power actions with confirmation
    def power_on_confirm(self):
        """Show confirmation for power on"""
        self.confirmation_dialog = {
            'title': '¿Encender PC?',
            'action': self.power_on
        }
        
    def power_off_confirm(self):
        """Show confirmation for power off"""
        self.confirmation_dialog = {
            'title': '¿Apagar PC?',
            'action': self.power_off
        }
        
    def power_on(self):
        """Power on external PC"""
        self.gpio.pulse_power_button(0.2)
        self.confirmation_dialog = None
        
    def power_off(self):
        """Power off external PC"""
        self.gpio.pulse_power_button(0.2)
        self.confirmation_dialog = None

    def reset_pc_confirm(self):
        """Show confirmation for PC reset"""
        self.confirmation_dialog = {
            'title': '¿Reiniciar PC?',
            'action': self.reset_pc,
        }

    def reset_pc(self):
        """Pulse the reset button"""
        self.gpio.pulse_reset_button(0.2)
        self.confirmation_dialog = None

    def update_confirm(self):
        self.confirmation_dialog = {
            'title': '¿Actualizar app?',
            'action': self.do_update,
        }

    def do_update(self):
        import subprocess, select
        self.confirmation_dialog = None

        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            env = os.environ.copy()
            env['GIT_TERMINAL_PROMPT'] = '0'
            env['GIT_ASKPASS'] = '/bin/echo'
            env['SSH_ASKPASS'] = '/bin/echo'
            env['GIT_SSH_COMMAND'] = 'ssh -o StrictHostKeyChecking=no -o BatchMode=yes'
            # Run as the repo owner so stored credentials are found
            repo_owner = os.stat(repo_dir).st_uid
            import pwd
            repo_user = pwd.getpwuid(repo_owner).pw_name
            env['HOME'] = pwd.getpwuid(repo_owner).pw_dir
            cmd = ['sudo', '-u', repo_user, 'git', 'pull'] if os.geteuid() == 0 else ['git', 'pull']
            proc = subprocess.Popen(
                cmd,
                cwd=repo_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        except Exception as e:
            self._show_update_result(False, str(e))
            return

        last_line = "Iniciando..."
        start = pygame.time.get_ticks()

        while proc.poll() is None:
            for event in pygame.event.get():
                if event.type == QUIT:
                    proc.terminate()
                    self.running = False
                    return
                if event.type == KEYDOWN and event.key == K_ESCAPE:
                    proc.terminate()
                    return

            # Read any available output without blocking
            r, _, _ = select.select([proc.stdout], [], [], 0)
            if r:
                raw = proc.stdout.readline()
                if raw:
                    last_line = raw.decode('utf-8', errors='ignore').strip()

            elapsed = (pygame.time.get_ticks() - start) // 500
            dots = "." * (elapsed % 4)
            self.screen.fill(C_BG)
            self.screen.blit(
                _rt(self.font, "Actualizando" + dots, C_WHITE),
                _rt(self.font, "Actualizando" + dots, C_WHITE).get_rect(
                    center=(self.width // 2, self.height // 2 - _s(50)))
            )
            self.screen.blit(
                _rt(self.font_tab, last_line[:55], C_GRAY),
                _rt(self.font_tab, last_line[:55], C_GRAY).get_rect(
                    center=(self.width // 2, self.height // 2 + _s(20)))
            )
            pygame.display.flip()
            self.clock.tick(10)

            if pygame.time.get_ticks() - start > 30000:
                proc.terminate()
                self._show_update_result(False, "Timeout (30s)")
                return

        remaining = proc.stdout.read().decode('utf-8', errors='ignore').strip().splitlines()
        all_lines = ([last_line] + remaining) if last_line != "Iniciando..." else remaining
        line1 = all_lines[0] if all_lines else ('OK' if proc.returncode == 0 else 'Error')
        self._show_update_result(proc.returncode == 0, line1)

        if proc.returncode == 0:
            pygame.quit()
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def _show_update_result(self, success, line1):
        self.screen.fill(C_BG)
        color = C_WHITE if success else C_ORANGE
        l1 = _rt(self.font, line1[:40], color)
        self.screen.blit(l1, l1.get_rect(center=(self.width // 2, self.height // 2 - _s(40))))
        if success:
            l2 = _rt(self.font, "Reiniciando...", C_GRAY)
            self.screen.blit(l2, l2.get_rect(center=(self.width // 2, self.height // 2 + _s(40))))
        pygame.display.flip()
        pygame.time.wait(2000)

    # ── Bluetooth pairing ─────────────────────────────────────────────────────

    def _start_bt_status_poller(self):
        """Background thread: check BT connection every 3 s via status file."""
        import threading
        def _poll():
            import time as _t
            while True:
                try:
                    with open('/var/run/arcade-hid-status') as f:
                        connected = f.read().strip() == 'connected'
                except Exception:
                    connected = False
                if connected != self.bt_connected:
                    self.bt_connected = connected
                    self._main_cache_dirty = True
                else:
                    self.bt_connected = connected
                _t.sleep(3)
        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def _start_gpio_poller(self):
        """Background thread: poll GPIO inputs at 20 Hz for LED and coin state."""
        import threading
        def _poll():
            import time as _t
            prev_coin1 = False
            prev_coin2 = False
            while True:
                # Power LED → pc_on
                pc_on = self.gpio.read_power_led()
                if pc_on != self.pc_on:
                    self._main_cache_dirty = True
                if pc_on and not self.pc_on:
                    self.pc_on_since = _t.time()
                elif not pc_on and self.pc_on:
                    self.pc_on_since = None
                self.pc_on = pc_on

                # HDD LED
                self.hdd_on = self.gpio.read_hdd_led()

                # Coin P1 — rising edge
                coin1 = self.gpio.read_coin1()
                if coin1 and not prev_coin1:
                    self.coin_p1_count += 1
                prev_coin1 = coin1

                # Coin P2 — rising edge
                coin2 = self.gpio.read_coin2()
                if coin2 and not prev_coin2:
                    self.coin_p2_count += 1
                prev_coin2 = coin2

                _t.sleep(0.05)
        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def _start_wifi_poller(self):
        """Background thread: check internet connectivity every 30 s."""
        import threading
        def _poll():
            import time as _t, socket
            while True:
                connected = False
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.settimeout(2)
                    s.connect(('8.8.8.8', 80))
                    s.close()
                    connected = True
                except Exception:
                    pass
                self.wifi_connected = connected
                _t.sleep(30)
        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def _start_weather_poller(self):
        """Background thread: fetch weather for Cervelló (BCN) every 10 min."""
        import threading
        # WMO weather interpretation codes → Spanish labels
        _WMO = {
            0: 'Despejado', 1: 'Despejado', 2: 'Parcialmente nublado', 3: 'Nublado',
            45: 'Niebla', 48: 'Niebla',
            51: 'Llovizna', 53: 'Llovizna', 55: 'Llovizna',
            61: 'Lluvia', 63: 'Lluvia', 65: 'Lluvia intensa',
            71: 'Nieve', 73: 'Nieve', 75: 'Nieve intensa',
            80: 'Chubascos', 81: 'Chubascos', 82: 'Chubascos fuertes',
            95: 'Tormenta', 96: 'Tormenta', 99: 'Tormenta intensa',
        }
        _URL = (
            'https://api.open-meteo.com/v1/forecast'
            '?latitude=41.38&longitude=1.95'
            '&current=temperature_2m,weather_code'
            '&timezone=Europe%2FMadrid'
        )
        def _poll():
            import time as _t, urllib.request, json
            while True:
                if self.wifi_connected:
                    try:
                        with urllib.request.urlopen(_URL, timeout=10) as resp:
                            data = json.loads(resp.read())
                        temp = data['current']['temperature_2m']
                        code = int(data['current']['weather_code'])
                        cond = _WMO.get(code, f'Cód {code}')
                        self.weather_data = {'temp': temp, 'condition': cond}
                    except Exception:
                        pass
                _t.sleep(600)
        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def _draw_bt_status(self, msg, error=False):
        pass  # kept for compat; full screen replaced by open_bt_screen

    # ── Bluetooth management screen ──────────────────────────────────────

    def _bt_load_data(self):
        """Query bluetoothctl for paired devices and bt-hid-server status."""
        import subprocess
        devices = []
        try:
            out = subprocess.check_output(
                ['bluetoothctl', 'paired-devices'],
                timeout=6, stderr=subprocess.DEVNULL, text=True,
            )
            for line in out.strip().splitlines():
                parts = line.split(' ', 2)
                if len(parts) >= 2 and parts[0] == 'Device':
                    mac  = parts[1]
                    name = parts[2].strip() if len(parts) > 2 else mac
                    connected = False
                    try:
                        info = subprocess.check_output(
                            ['bluetoothctl', 'info', mac],
                            timeout=4, stderr=subprocess.DEVNULL, text=True,
                        )
                        connected = 'Connected: yes' in info
                    except Exception:
                        pass
                    devices.append({'mac': mac, 'name': name, 'connected': connected})
        except Exception:
            pass

        server_active = False
        try:
            r = subprocess.run(
                ['systemctl', 'is-active', 'bt-hid-server'],
                timeout=4, capture_output=True, text=True,
            )
            server_active = r.stdout.strip() == 'active'
        except Exception:
            pass

        self.bt_data = {'devices': devices, 'server_active': server_active}
        self._bt_fetch_logs()
        self._bt_build_buttons()

    def _bt_fetch_logs(self, n=35):
        import subprocess

        def _journal(unit, count):
            """Return cleaned (time, msg) lines from a systemd unit."""
            try:
                out = subprocess.check_output(
                    ['journalctl', '-u', unit, f'-n{count}',
                     '--no-pager', '--output=short'],
                    timeout=5, stderr=subprocess.DEVNULL, text=True,
                )
                result = []
                for line in out.splitlines():
                    parts = line.split(None, 4)
                    if len(parts) >= 5:
                        t = parts[2]
                        rest = parts[4]
                        msg  = rest.split(': ', 1)[1] if ': ' in rest else rest
                        result.append(f"{t}  [{unit}]  {msg.rstrip()}")
                return result
            except Exception as e:
                return [f'Error {unit}: {e}']

        # L2CAP listening sockets — shows whether PSM 17/19 are actually bound
        l2cap_lines = []
        try:
            raw = subprocess.check_output(
                ['bash', '-c', 'cat /proc/net/bluetooth/l2cap 2>/dev/null || echo "no l2cap file"'],
                timeout=3, text=True, stderr=subprocess.DEVNULL,
            )
            for ln in raw.strip().splitlines():
                l2cap_lines.append(f'         [l2cap]  {ln.strip()}')
        except Exception:
            pass

        # Combine: our service (newest 20) + bluetoothd (newest 10) + l2cap table
        lines_hid = _journal('bt-hid-server', 20)
        lines_btd = _journal('bluetooth',     10)
        combined  = lines_hid + ['---'] + lines_btd + [' --- /proc/net/bluetooth/l2cap ---'] + l2cap_lines
        self.bt_logs = combined[-n:]
        self.bt_log_scroll = 0  # reset scroll so newest lines are visible
        # Always save to /tmp so user can scp it
        self._bt_save_log_file('refresh')
    def _bt_build_buttons(self):
        devices = self.bt_data.get('devices', [])
        ROW_H       = _s(110)
        ROW_Y_START = _s(185)
        BTN_W       = _s(260)
        BTN_H       = _s(72)

        self.bt_remove_buttons = []
        for i, dev in enumerate(devices):
            y   = ROW_Y_START + i * ROW_H
            mac = dev['mac']
            btn = SimpleButton(
                (self.width - BTN_W - _s(30), y + (ROW_H - BTN_H) // 2, BTN_W, BTN_H),
                'DESEMPAREJAR',
                action=lambda m=mac: self.bt_remove_device(m),
                icon='\ue872',   # delete
            )
            self.bt_remove_buttons.append(btn)

        # Bottom row: activate + refresh + diagnose + close
        btn_h = _s(90)
        btn_y = self.height - btn_h - _s(25)
        btn_w = _s(260)
        gap   = _s(18)
        n     = 4
        total_w = n * btn_w + (n - 1) * gap
        x = (self.width - total_w) // 2

        self.bt_activate_btn = SimpleButton(
            (x, btn_y, btn_w, btn_h), 'ACTIVAR EMPAR.',
            action=self.bt_activate_pairing, icon='\ue1a8',
        )
        self.bt_refresh_btn = SimpleButton(
            (x + btn_w + gap, btn_y, btn_w, btn_h), 'ACTUALIZAR',
            action=self.bt_refresh, icon='\ue5d5',
        )
        self.bt_diagnose_btn = SimpleButton(
            (x + 2 * (btn_w + gap), btn_y, btn_w, btn_h), 'DIAGNÓSTICO',
            action=self.bt_diagnose, icon='\ue868',
        )
        self.bt_close_btn = SimpleButton(
            (x + 3 * (btn_w + gap), btn_y, btn_w, btn_h), 'CERRAR',
            action=self.close_bt_screen, icon='\ue5cd',
        )

    def open_bt_screen(self):
        self.bt_screen    = True
        self.bt_status_msg = 'Cargando...'
        self.draw_bt_screen()
        self._bt_load_data()
        self.bt_status_msg = ''

    def close_bt_screen(self):
        self.bt_screen    = False
        self.bt_status_msg = ''

    def bt_refresh(self):
        self.bt_status_msg = 'Actualizando...'
        self.draw_bt_screen()
        pygame.event.pump()
        self._bt_load_data()
        self.bt_status_msg = ''

    def bt_activate_pairing(self):
        import subprocess, glob, shutil, time as _time

        # ── Step 1: delete ALL stale link keys from Pi ───────────────────────
        self.bt_status_msg = 'Borrando claves de emparejamiento antiguas...'
        self.draw_bt_screen()
        pygame.event.pump()
        try:
            for dev_dir in glob.glob('/var/lib/bluetooth/*/*'):
                if os.path.isdir(dev_dir):
                    shutil.rmtree(dev_dir, ignore_errors=True)
        except Exception:
            pass  # non-fatal

        # ── Step 2: restart bluetoothd to clear in-memory bond state ────────
        self.bt_status_msg = 'Reiniciando bluetoothd...'
        self.draw_bt_screen()
        pygame.event.pump()
        subprocess.run(['sudo', 'systemctl', 'restart', 'bluetooth'],
                       timeout=10, capture_output=True)
        _time.sleep(1)

        # ── Step 3: copy + restart bt-hid-server ─────────────────────────────
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src      = os.path.join(repo_dir, 'src', 'bt_hid_server.py')
        for cmd, label in [
            (['sudo', 'cp', src, '/usr/local/bin/bt-hid-server'], 'Copiando servidor BT...'),
            (['sudo', 'systemctl', 'restart', 'bt-hid-server'],   'Reiniciando servicio BT...'),
        ]:
            self.bt_status_msg = label
            self.draw_bt_screen()
            pygame.event.pump()
            try:
                r = subprocess.run(cmd, timeout=15, capture_output=True)
                if r.returncode != 0:
                    err = r.stderr.decode('utf-8', errors='ignore').strip()
                    self.bt_status_msg = f'Error: {err[:70] or " ".join(cmd)}'
                    self.draw_bt_screen()
                    pygame.time.wait(3000)
                    self._bt_load_data()
                    return
            except Exception as e:
                self.bt_status_msg = f'Error: {e}'
                self.draw_bt_screen()
                pygame.time.wait(3000)
                self._bt_load_data()
                return

        self.bt_status_msg = '✓ Listo — ahora quita "Arcade HID Keyboard" de Windows y vuelve a añadir'
        self._bt_load_data()

    def bt_remove_device(self, mac):
        import subprocess, glob, shutil
        self.bt_status_msg = f'Desemparejando {mac}...'
        self.draw_bt_screen()
        pygame.event.pump()
        errors = []
        # 1) BlueZ logical remove
        try:
            subprocess.run(['bluetoothctl', 'remove', mac], timeout=8, capture_output=True)
        except Exception as e:
            errors.append(f'bluetoothctl: {e}')
        # 2) Delete link-key directory so stale MITM keys can't block re-pairing
        try:
            for path in glob.glob(f'/var/lib/bluetooth/*/{mac}'):
                shutil.rmtree(path, ignore_errors=True)
                log_msg = f'Deleted {path}'
        except Exception as e:
            errors.append(f'rm keys: {e}')
        # 3) Restart bluetoothd so it reloads without the stale key
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'bluetooth'], timeout=10, capture_output=True)
        except Exception as e:
            errors.append(f'restart bt: {e}')
        if errors:
            self.bt_status_msg = 'Errores: ' + '; '.join(errors)
        else:
            self.bt_status_msg = f'✓ {mac} eliminado — ahora desempareja en Windows y vuelve a emparejar'
        self._bt_load_data()

    def bt_diagnose(self):
        """Run btmon for 8 s + sdptool + l2cap sockets. User should try
        connecting from Windows during those 8 seconds."""
        import subprocess, tempfile, os, time as _time

        self.bt_status_msg = 'Iniciando btmon — conecta desde Windows AHORA (8s)...'
        self.draw_bt_screen()
        pygame.event.pump()

        # Start btmon in background writing to a temp file
        tmpf = '/tmp/arcade-btmon.log'
        try:
            proc = subprocess.Popen(
                ['btmon', '--no-pager'],
                stdout=open(tmpf, 'wb'), stderr=subprocess.STDOUT,
            )
        except Exception as e:
            self.bt_status_msg = f'btmon no disponible: {e}'
            self._bt_fetch_logs()
            return

        # Count down 8 seconds with live screen update
        for remaining in range(8, 0, -1):
            self.bt_status_msg = f'btmon capturando... conecta desde Windows ({remaining}s restantes)'
            self.draw_bt_screen()
            pygame.event.pump()
            pygame.time.wait(1000)

        proc.terminate()
        proc.wait(timeout=2)

        # Parse btmon output — save ALL lines to file, show filtered on screen
        diag_lines = ['=== btmon ===']  
        btmon_all  = []
        keywords = ('l2cap', 'psm', 'sdp', 'hid', 'conn req', 'conn rsp',
                    'security', 'auth', 'reject', 'refused', 'error', 'mgmt')
        try:
            with open(tmpf, encoding='utf-8', errors='replace') as f:
                for ln in f:
                    btmon_all.append(ln.rstrip())
                    if any(k in ln.lower() for k in keywords):
                        diag_lines.append(ln.rstrip()[:160])
        except Exception as e:
            diag_lines.append(f'Error leyendo btmon: {e}')

        if len(diag_lines) == 1:
            diag_lines.append('(sin eventos L2CAP/SDP capturados)')

        # bluetoothd service status + config dir
        diag_lines.append('=== systemctl status bluetooth ===')
        try:
            r = subprocess.run(
                ['systemctl', 'status', 'bluetooth', '--no-pager', '-l'],
                capture_output=True, text=True, timeout=5,
            )
            for ln in (r.stdout + r.stderr).splitlines():
                diag_lines.append(ln.rstrip()[:160])
        except Exception as e:
            diag_lines.append(f'systemctl status bluetooth: {e}')

        diag_lines.append('=== /etc/bluetooth permissions ===')
        try:
            r = subprocess.run(
                ['ls', '-la', '/etc/bluetooth', '/var/lib/bluetooth'],
                capture_output=True, text=True, timeout=3,
            )
            for ln in r.stdout.splitlines():
                diag_lines.append(ln.rstrip()[:160])
        except Exception as e:
            diag_lines.append(f'ls bluetooth dirs: {e}')

        # sdptool browse own MAC
        diag_lines.append('=== sdptool browse local ===')
        try:
            import subprocess as sp
            r = sp.run(['sdptool', 'browse', 'local'],
                       capture_output=True, text=True, timeout=5)
            for ln in (r.stdout + r.stderr).splitlines():
                if any(k in ln.lower() for k in ('psm', 'hid', 'uuid', 'service name')):
                    diag_lines.append(ln.rstrip()[:160])
        except Exception as e:
            diag_lines.append(f'sdptool: {e}')

        # L2CAP socket table
        diag_lines.append('=== /proc/net/bluetooth/l2cap ===')
        try:
            with open('/proc/net/bluetooth/l2cap') as f:
                for ln in f:
                    diag_lines.append(ln.rstrip()[:160])
        except Exception as e:
            diag_lines.append(f'l2cap proc: {e}')

        self.bt_logs = diag_lines
        self.bt_log_scroll = 0  # show from top so user sees === btmon === header
        # Save full untruncated output (all btmon lines + sections) to file
        full_output = btmon_all + diag_lines
        saved_path  = self._bt_save_log_file('diag', lines=full_output)
        self.bt_status_msg = f'Listo — archivo: {saved_path or "/tmp/arcade-bt-diag-*.log"}'
        self.draw_bt_screen()

    def _bt_save_log_file(self, tag='', lines=None):
        """Write bt_logs to a timestamped file in /tmp. Returns the path on success."""
        import datetime
        ts   = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        path = f'/tmp/arcade-bt-{tag}-{ts}.log'
        try:
            with open(path, 'w') as f:
                f.write('\n'.join(lines or self.bt_logs))
                f.write('\n')
            # Keep at most 10 files
            import glob, os as _os
            old = sorted(glob.glob('/tmp/arcade-bt-*.log'))[:-10]
            for p in old:
                try: _os.remove(p)
                except: pass
            return path
        except Exception as e:
            self.bt_status_msg = f'Error guardando log: {e}'
            return None

    def bt_pair(self):  # backwards compat
        self.open_bt_screen()

    def draw_bt_screen(self):
        self.screen.fill(C_BG)

        # ── Header ───────────────────────────────────────────────────
        title = _rt(self.font, 'BLUETOOTH', C_WHITE)
        self.screen.blit(title, title.get_rect(centerx=self.width // 2, top=_s(18)))

        srv_ok    = self.bt_data.get('server_active', False)
        srv_color = C_WHITE if srv_ok else C_GRAY
        srv_text  = '● SERVIDOR ACTIVO' if srv_ok else '○ SERVIDOR INACTIVO'
        srv_surf  = _rt(self.font_action, srv_text, srv_color)
        self.screen.blit(srv_surf, (_s(30), _s(28)))

        pygame.draw.line(self.screen, C_GRAY, (_s(20), _s(105)), (self.width - _s(20), _s(105)), max(1, _s(2)))

        # ── Column headers ────────────────────────────────────────────
        for text, x in [('DISPOSITIVO', _s(80)), ('MAC', _s(520)), ('ESTADO', _s(870))]:
            self.screen.blit(_rt(self.font_slot, text, C_GRAY), (x, _s(112)))
        pygame.draw.line(self.screen, C_GRAY, (_s(20), _s(155)), (self.width - _s(20), _s(155)), 1)

        # ── Device rows ───────────────────────────────────────────────
        ROW_H       = _s(110)
        ROW_Y_START = _s(158)
        devices     = self.bt_data.get('devices', [])

        if not devices:
            s = _rt(self.font_tab, 'No hay dispositivos emparejados', C_GRAY)
            self.screen.blit(s, s.get_rect(centerx=self.width // 2, top=_s(230)))
        else:
            for i, dev in enumerate(devices):
                y  = ROW_Y_START + i * ROW_H
                cy = y + ROW_H // 2

                if i % 2 == 0:
                    pygame.draw.rect(self.screen, (15, 15, 30), (0, y, self.width, ROW_H))

                # BT icon + name
                x_icon = _s(30)
                if self.font_icon_sm:
                    ic = _rt(self.font_icon_sm, '\ue1a8', C_WHITE)
                    self.screen.blit(ic, (x_icon, cy - ic.get_height() // 2))
                    x_icon += ic.get_width() + _s(8)
                name_s = _rt(self.font_tab, dev['name'][:26], C_WHITE)
                self.screen.blit(name_s, (x_icon, cy - name_s.get_height() // 2))

                # MAC
                mac_s = _rt(self.font_action, dev['mac'], C_GRAY)
                self.screen.blit(mac_s, (_s(520), cy - mac_s.get_height() // 2))

                # Connection badge
                if dev['connected']:
                    badge_s = _rt(self.font_action, '● CONECTADO',    C_WHITE)
                else:
                    badge_s = _rt(self.font_action, '○ NO CONECTADO', C_GRAY)
                self.screen.blit(badge_s, (_s(870), cy - badge_s.get_height() // 2))

                # Remove button
                if i < len(self.bt_remove_buttons):
                    self.bt_remove_buttons[i].draw(self.screen, self.font_action, self.font_icon_sm)

        # ── Log panel ────────────────────────────────────────────────────
        LOG_Y = max(_s(510), ROW_Y_START + max(1, len(devices)) * ROW_H + _s(20))
        pygame.draw.line(self.screen, C_GRAY, (_s(20), LOG_Y), (self.width - _s(20), LOG_Y), 1)
        n_logs    = len(self.bt_logs)
        line_h    = _s(24)
        log_top   = LOG_Y + _s(40)
        log_bot   = self.height - _s(140)
        max_lines = max(1, (log_bot - log_top) // line_h)
        # Clamp scroll so 0 = bottom (newest), positive = older
        max_scroll = max(0, n_logs - max_lines)
        self.bt_log_scroll = max(0, min(self.bt_log_scroll, max_scroll))
        scroll = self.bt_log_scroll
        visible_logs = self.bt_logs[max(0, n_logs - max_lines - scroll) : (n_logs - scroll) if scroll else n_logs]
        # Header
        hdr_text = f'LOGS ({n_logs} líneas)  ▲▼ scroll  — guardado en /tmp/arcade-bt-*.log'
        self.screen.blit(_rt(self.font_slot, hdr_text, C_GRAY), (_s(30), LOG_Y + _s(8)))
        # Lines
        y_log = log_top
        for ln in visible_logs:
            lc = (C_ORANGE
                  if any(k in ln.lower() for k in ('error', 'fail', 'traceback', 'exception'))
                  else (255, 200, 80)
                  if any(k in ln.lower() for k in ('warning', 'warn', 'bad', 'intercepting'))
                  else (180, 255, 180)
                  if any(k in ln.lower() for k in ('connected', 'ready', 'registered', 'keyboard ready', 'listo'))
                  else C_GRAY)
            self.screen.blit(_rt(self.font_slot, ln[:160], lc), (_s(30), y_log))
            y_log += line_h
        # Scrollbar
        if n_logs > max_lines:
            sb_h   = log_bot - log_top
            tb_h   = max(_s(20), sb_h * max_lines // n_logs)
            tb_y   = log_top + (sb_h - tb_h) * scroll // max(1, max_scroll)
            pygame.draw.rect(self.screen, (60, 60, 80),
                             (self.width - _s(18), log_top, _s(10), sb_h), border_radius=_s(5))
            pygame.draw.rect(self.screen, (160, 160, 200),
                             (self.width - _s(18), tb_y, _s(10), tb_h), border_radius=_s(5))

        # ── Status message ───────────────────────────────────────────────
        if self.bt_status_msg:
            color = C_ORANGE if self.bt_status_msg.startswith('Error') else C_WHITE
            msg_s = _rt(self.font_tab, self.bt_status_msg[:70], color)
            self.screen.blit(msg_s, msg_s.get_rect(
                centerx=self.width // 2, bottom=self.height - _s(130)))

        # ── Bottom buttons ───────────────────────────────────────────────
        for btn in (self.bt_activate_btn, self.bt_refresh_btn,
                    self.bt_diagnose_btn, self.bt_close_btn):
            if btn:
                btn.draw(self.screen, self.font_action, self.font_icon_sm)

        pygame.display.flip()
    
    def draw_cyberpunk_bg(self):
        pass
    
    def draw_lock_screen_base(self):
        self.screen.fill(self.bg_color)

        if not self.screen_on:
            return

        # Lock icon centered at top
        icon_y = _s(120)
        if self.font_icon_lock:
            icon_surf = _rt(self.font_icon_lock, '\ue897', C_WHITE)
            self.screen.blit(icon_surf, icon_surf.get_rect(center=(self.width // 2, icon_y)))
            dots_y = icon_y + icon_surf.get_height() // 2 + _s(30)
        else:
            dots_y = _s(200)

        # PIN dot indicators centered
        max_digits = 6
        dot_r   = _s(12)
        dot_gap = _s(16)
        total_w = max_digits * (dot_r * 2) + (max_digits - 1) * dot_gap
        dot_cx  = (self.width - total_w) // 2 + dot_r
        for i in range(max_digits):
            cx = dot_cx + i * (dot_r * 2 + dot_gap)
            if i < len(self.pin_input):
                pygame.draw.circle(self.screen, C_WHITE, (cx, dots_y), dot_r)
            else:
                pygame.draw.circle(self.screen, C_GRAY, (cx, dots_y), dot_r, max(1, _s(2)))

        for btn in self.numpad_buttons:
            btn.draw(self.screen, self.font_tab)
        
    def draw_lock_screen(self):
        """Draw lock screen with numpad"""
        self.draw_lock_screen_base()
        pygame.display.flip()
        
    def _rebuild_main_cache(self):
        """Pre-render tab bar and slot sub-tabs into a Surface (NOT the scroll area)."""
        if self._main_cache is None:
            self._main_cache = pygame.Surface((self.width, self.height)).convert()
        s = self._main_cache
        s.fill(C_BG)

        # Sync button state so disabled flags are correct when cached
        self.power_btn.text = 'APAGAR PC' if self.pc_on else 'ENCENDER PC'
        self.reset_btn.disabled = not self.pc_on
        for btn in self.bt_action_btns:
            btn.disabled = not self.bt_connected

        for btn in self.tab_buttons:
            btn.draw(s, self.font_tab, self.font_icon)

        if self.current_tab == 'partida':
            for btn in self.slot_buttons:
                btn.draw(s, self.font_slot)

        # Scroll menu is NOT drawn into the cache — it is drawn live every frame
        # so that scroll_x changes are visible immediately without cache invalidation.
        self._main_cache_dirty = False

    def draw_main_screen_base(self):
        # Rebuild tab/slot layer only when something structural changed
        if self._main_cache_dirty:
            self._rebuild_main_cache()
        self.screen.blit(self._main_cache, (0, 0))

        # Scroll area: always fill background and draw live (scroll position changes every frame)
        menu = self._active_scroll_menu()
        pygame.draw.rect(self.screen, C_BG, menu.rect)
        menu.draw(self.screen, self.font_action, self.font_icon_action)

        # ── Top status bar (always repainted — clock ticks every second) ────────
        BAR_CY  = _s(44)
        MARGIN  = _s(20)
        BAR_H   = _s(90)   # height of the top strip (up to the tab row)
        pygame.draw.rect(self.screen, C_BG, (0, 0, self.width, BAR_H))

        # Clock — top left
        datetime_str = datetime.now(ZoneInfo('Europe/Madrid')).strftime('%d-%m-%Y  %H:%M:%S')
        time_surf = _rt(self.font_mono, datetime_str, C_GRAY)
        clock_rect = time_surf.get_rect(midleft=(MARGIN, BAR_CY))
        self.screen.blit(time_surf, clock_rect)

        # Weather — right of clock (only when WiFi up and data available)
        if self.wifi_connected and self.weather_data:
            wx_str  = f"{self.weather_data['temp']:.0f}°C  {self.weather_data['condition']}"
            wx_surf = _rt(self.font_mono, wx_str, (180, 210, 255))
            self.screen.blit(wx_surf, wx_surf.get_rect(midleft=(clock_rect.right + _s(22), BAR_CY)))

        # Right-side indicators (rendered right→left: BT, HDD, PC, WiFi)
        DOT_R = _s(7)
        x = self.width - MARGIN
        for label, connected in [('BT',   self.bt_connected),
                                   ('HDD',  self.hdd_on),
                                   ('PC',   self.pc_on),
                                   ('WiFi', self.wifi_connected)]:
            color    = (0, 210, 100) if connected else (120, 120, 120)
            lbl_surf = _rt(self.font_mono, label, color)
            lbl_rect = lbl_surf.get_rect(midright=(x, BAR_CY))
            dot_cx   = lbl_rect.left - _s(10)
            pygame.draw.circle(self.screen, color, (dot_cx, BAR_CY), DOT_R)
            self.screen.blit(lbl_surf, lbl_rect)
            x = dot_cx - _s(20)

        # ── Bottom status bar ────────────────────────────────────────────────
        BAR_BOT_CY = self.height - _s(30)
        pygame.draw.rect(self.screen, C_BG,
                         (0, self.height - _s(60), self.width, _s(60)))
        # Coin counters — left
        coin_str  = f'P1: {self.coin_p1_count}   P2: {self.coin_p2_count}'
        coin_surf = _rt(self.font_mono, coin_str, C_GRAY)
        self.screen.blit(coin_surf, coin_surf.get_rect(midleft=(MARGIN, BAR_BOT_CY)))
        # PC uptime — right (only while PC is on)
        if self.pc_on and self.pc_on_since is not None:
            elapsed = int(time.time() - self.pc_on_since)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            up_surf = _rt(self.font_mono, f'PC  {h:02d}:{m:02d}:{s:02d}', (0, 210, 100))
            self.screen.blit(up_surf, up_surf.get_rect(midright=(self.width - MARGIN, BAR_BOT_CY)))

    def draw_main_screen(self):
        """Draw main control interface — skips render if nothing changed."""
        now_s    = datetime.now(ZoneInfo('Europe/Madrid')).second
        menu     = self._active_scroll_menu()
        scrolling = abs(menu.velocity) > 0.05
        if not (self._main_cache_dirty or scrolling or now_s != self._prev_second):
            return
        self._prev_second = now_s
        self.draw_main_screen_base()
        pygame.display.flip()

    def open_debug(self):
        self.debug_screen = True
        self.debug_info = self.get_network_info()
        btn_w, btn_h = _s(240), _s(65)
        self.debug_close_btn = SimpleButton(
            ((self.width - btn_w) // 2, _s(370), btn_w, btn_h),
            "CERRAR",
            action=self.close_debug,
        )

    def close_debug(self):
        self.debug_screen = False

    def get_network_info(self):
        import subprocess, socket, re
        info = {'connected': False, 'ssid': None, 'signal_pct': None, 'ip': None}
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            info['ip'] = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                info['ip'] = socket.gethostbyname(socket.gethostname())
            except Exception:
                pass
        # Try iwconfig first
        try:
            out = subprocess.check_output(
                ['iwconfig'], stderr=subprocess.STDOUT, timeout=3
            ).decode('utf-8', errors='ignore')
            m = re.search(r'ESSID:"([^"]+)"', out)
            if m:
                info['ssid'] = m.group(1)
                info['connected'] = True
            m = re.search(r'Link Quality=(\d+)/(\d+)', out)
            if m:
                info['signal_pct'] = int(m.group(1)) * 100 // int(m.group(2))
        except Exception:
            pass
        # Fallback: nmcli
        if not info['connected']:
            try:
                out = subprocess.check_output(
                    ['nmcli', '-t', '-f', 'active,ssid,signal', 'dev', 'wifi'],
                    stderr=subprocess.DEVNULL, timeout=3
                ).decode('utf-8', errors='ignore')
                for line in out.splitlines():
                    parts = line.split(':')
                    if len(parts) >= 3 and parts[0] == 'yes':
                        info['connected'] = True
                        info['ssid'] = parts[1]
                        try:
                            info['signal_pct'] = int(parts[2])
                        except ValueError:
                            pass
                        break
            except Exception:
                pass
        return info

    def draw_debug_screen(self):
        self.screen.fill(C_BG)
        info = self.debug_info

        title = _rt(self.font, "DEBUG", C_WHITE)
        self.screen.blit(title, title.get_rect(center=(self.width // 2, _s(45))))
        pygame.draw.line(self.screen, C_GRAY, (_s(20), _s(85)), (self.width - _s(20), _s(85)), max(1, _s(2)))

        rows = [
            ("WiFi",      "CONECTADO" if info['connected'] else "NO CONECTADO",
                          C_WHITE if info['connected'] else C_GRAY),
            ("SSID",      info['ssid'] or "\u2014",       C_WHITE),
            ("Cobertura", f"{info['signal_pct']}%" if info['signal_pct'] is not None else "\u2014", C_WHITE),
            ("IP",        info['ip'] or "\u2014",          C_WHITE),
        ]

        y = _s(110)
        row_h     = _s(55)
        col_label = _s(40)
        col_value = _s(240)
        for label, value, color in rows:
            lbl = _rt(self.font_tab, label + ":", C_GRAY)
            self.screen.blit(lbl, (col_label, y + (row_h - lbl.get_height()) // 2))
            val = _rt(self.font_tab, value, color)
            self.screen.blit(val, (col_value, y + (row_h - val.get_height()) // 2))
            y += row_h

        pygame.draw.line(self.screen, C_GRAY, (_s(20), y + _s(10)), (self.width - _s(20), y + _s(10)), max(1, _s(2)))
        self.debug_close_btn.draw(self.screen, self.font_tab)
        pygame.display.flip()

    def draw_confirmation_dialog(self):
        if self.locked:
            self.draw_lock_screen_base()
        else:
            self.draw_main_screen_base()

        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        self.screen.blit(overlay, (0, 0))

        # Dialog: 50% x 38% of screen
        dialog_w = int(self.width * 0.50)
        dialog_h = int(self.height * 0.38)
        dialog_x = (self.width - dialog_w) // 2
        dialog_y = (self.height - dialog_h) // 2

        pygame.draw.rect(self.screen, C_DARK, (dialog_x, dialog_y, dialog_w, dialog_h))
        pygame.draw.rect(self.screen, C_WHITE, (dialog_x, dialog_y, dialog_w, dialog_h), 3)

        # Lay out content (title + gap + buttons) centered inside the dialog
        cx = self.width // 2
        btn_w   = int(dialog_w * 0.36)
        btn_h   = int(dialog_h * 0.30)
        btn_gap = int(dialog_w * 0.08)

        title_surf  = _rt(self.font, self.confirmation_dialog['title'], C_WHITE)
        inner_gap   = int(dialog_h * 0.10)
        total_h     = title_surf.get_height() + inner_gap + btn_h
        content_top = dialog_y + (dialog_h - total_h) // 2

        # Title
        self.screen.blit(title_surf, title_surf.get_rect(
            center=(cx, content_top + title_surf.get_height() // 2)))

        # Buttons
        btn_y = content_top + title_surf.get_height() + inner_gap
        total_btn_w = btn_w * 2 + btn_gap
        btn_left_x  = dialog_x + (dialog_w - total_btn_w) // 2

        self.dialog_yes_btn = SimpleButton(
            (btn_left_x, btn_y, btn_w, btn_h), "SÍ",
            action=self.confirmation_dialog['action'],
        )
        self.dialog_no_btn = SimpleButton(
            (btn_left_x + btn_w + btn_gap, btn_y, btn_w, btn_h), "NO",
            action=lambda: setattr(self, 'confirmation_dialog', None),
        )

        # Draw button shell without text, then render icon + text inline
        for btn in (self.dialog_yes_btn, self.dialog_no_btn):
            pygame.draw.rect(self.screen, C_BTN, btn.rect)

        for btn, icon_cp, label in [
            (self.dialog_yes_btn, '\ue876', 'SÍ'),
            (self.dialog_no_btn,  '\ue5cd', 'NO'),
        ]:
            text_surf = _rt(self.font, label, C_WHITE)
            if self.font_icon:
                icon_surf = _rt(self.font_icon, icon_cp, C_WHITE)
                gap = _s(10)
                total_w = icon_surf.get_width() + gap + text_surf.get_width()
                x  = btn.rect.centerx - total_w // 2
                cy = btn.rect.centery
                self.screen.blit(icon_surf, (x, cy - icon_surf.get_height() // 2))
                self.screen.blit(text_surf, (x + icon_surf.get_width() + gap,
                                             cy - text_surf.get_height() // 2))
            else:
                self.screen.blit(text_surf, text_surf.get_rect(center=btn.rect.center))

        pygame.display.flip()
                    
    def handle_events(self):
        """Handle pygame events"""
        for event in pygame.event.get():
            if event.type == QUIT:
                self.running = False
            elif event.type == KEYDOWN:
                if event.key == K_ESCAPE:
                    self.running = False
                elif event.key == K_F1:
                    self.do_update()
            elif event.type in (MOUSEBUTTONDOWN, MOUSEMOTION, MOUSEBUTTONUP):
                # If dialog is open, fire action immediately on MOUSEBUTTONDOWN
                if self.confirmation_dialog:
                    if event.type == MOUSEBUTTONDOWN and hasattr(self, 'dialog_yes_btn'):
                        if self.dialog_yes_btn.rect.collidepoint(event.pos):
                            act = self.confirmation_dialog.get('action')
                            if act:
                                act()
                        elif self.dialog_no_btn.rect.collidepoint(event.pos):
                            self.confirmation_dialog = None
                    continue

                # Debug screen: only the close button
                if self.debug_screen:
                    if event.type in (MOUSEBUTTONDOWN, MOUSEBUTTONUP):
                        self.debug_close_btn.handle_event(event)
                    continue

                # BT management screen
                if self.bt_screen:
                    if event.type in (MOUSEBUTTONDOWN, MOUSEBUTTONUP):
                        for btn in self.bt_remove_buttons:
                            btn.handle_event(event)
                        for btn in (self.bt_activate_btn, self.bt_refresh_btn,
                                    self.bt_diagnose_btn, self.bt_close_btn):
                            if btn:
                                btn.handle_event(event)
                    elif event.type == pygame.MOUSEWHEEL:
                        self.bt_log_scroll = max(0, self.bt_log_scroll + event.y * -3)
                        self.draw_bt_screen()
                    continue

                # Wake screen on any touch
                if not self.screen_on:
                    if event.type == MOUSEBUTTONDOWN:
                        self.wake_screen()
                    continue

                # Lock screen: numpad (no motion handling needed)
                if self.locked:
                    if event.type != MOUSEMOTION:
                        for btn in self.numpad_buttons:
                            btn.handle_event(event)
                else:
                    # Tab bar and slot sub-tabs: simple tap only
                    if event.type == MOUSEBUTTONDOWN:
                        for btn in self.tab_buttons:
                            if btn.handle_event(event):
                                break
                        if self.current_tab == 'partida':
                            for btn in self.slot_buttons:
                                if btn.handle_event(event):
                                    break
                    # Scrollable content (handles all mouse events incl. motion)
                    self._active_scroll_menu().handle_event(event)

                        
    def run(self):
        """Main application loop"""
        _hold_last_t = 0   # last time a hold_action was fired
        HOLD_DELAY   = 3000  # ms before repeat starts
        HOLD_REPEAT  = 300   # ms between repeat fires
        while self.running:
            self.handle_events()

            # Skip all rendering when screen is off (saves ~100% CPU in standby)
            if not self.screen_on:
                self.clock.tick(10)
                continue

            # Hold-to-repeat for buttons with hold_action
            now = pygame.time.get_ticks()
            if not self.bt_screen and not self.locked and not self.confirmation_dialog:
                menu = self._active_scroll_menu()
                btn  = menu.pressed_btn if menu else None
                if btn and btn.hold_action and btn.is_pressed:
                    elapsed = now - btn._press_time
                    if elapsed >= HOLD_DELAY and now - _hold_last_t >= HOLD_REPEAT:
                        btn._hold_fired = True
                        btn.hold_action()
                        _hold_last_t = now

            if not self.locked and not self.confirmation_dialog and not self.debug_screen and not self.bt_screen:
                self._active_scroll_menu().update()

            # Draw appropriate screen
            if self.confirmation_dialog:
                self.draw_confirmation_dialog()
            elif self.debug_screen:
                self.draw_debug_screen()
            elif self.bt_screen:
                self.draw_bt_screen()
            elif self.locked:
                self.draw_lock_screen()
            else:
                self.draw_main_screen()

            self.clock.tick(60)
            
        self.cleanup()
        
    def cleanup(self):
        """Cleanup resources"""
        self.gpio.cleanup()
        pygame.quit()
        

def main():
    """Entry point"""
    try:
        app = ArcadeControlApp()
        app.run()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
