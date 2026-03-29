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

# Color palette
C_BG     = (0, 0, 0)        # Pure black
C_WHITE  = (255, 255, 255)  # White
C_GRAY   = (140, 140, 140)  # Mid gray
C_DARK   = (20, 20, 20)     # Near-black for fills
C_ORANGE = (255, 140, 0)    # Pressed state
C_BTN    = (55,  55,  55)   # Action button background


class SimpleButton:
    """Simple square button: black fill, white 3px border, white text."""

    def __init__(self, rect, text, color=None, action=None, icon=None):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.action = action
        self.is_pressed = False
        self.icon = icon

    def draw(self, surface, font, font_icon=None):
        bg = C_ORANGE if self.is_pressed else C_BTN
        pygame.draw.rect(surface, bg, self.rect)
        if self.icon and font_icon:
            icon_surf = font_icon.render(self.icon, True, C_WHITE)
            text_surf = font.render(self.text, True, C_WHITE)
            gap       = 6
            total_h   = icon_surf.get_height() + gap + text_surf.get_height()
            top       = self.rect.centery - total_h // 2
            surface.blit(icon_surf, icon_surf.get_rect(centerx=self.rect.centerx, top=top))
            surface.blit(text_surf, text_surf.get_rect(centerx=self.rect.centerx,
                                                        top=top + icon_surf.get_height() + gap))
        else:
            text_surf = font.render(self.text, True, C_WHITE)
            surface.blit(text_surf, text_surf.get_rect(center=self.rect.center))

    def handle_event(self, event):
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
                text_surf = font.render(self.text, True, C_BG)
            else:
                pygame.draw.rect(surface, C_GRAY, self.rect, 3)
                text_surf = font.render(self.text, True, C_GRAY)
            surface.blit(text_surf, text_surf.get_rect(center=self.rect.center))
            return

        # underline style
        fg = C_WHITE if self.active else C_GRAY
        if self.icon and font_icon:
            icon_surf = font_icon.render(self.icon, True, fg)
            text_surf = font.render(self.text, True, fg)
            gap       = 8
            total_w   = icon_surf.get_width() + gap + text_surf.get_width()
            x         = self.rect.centerx - total_w // 2
            cy        = self.rect.centery
            surface.blit(icon_surf, icon_surf.get_rect(left=x, centery=cy))
            surface.blit(text_surf, text_surf.get_rect(left=x + icon_surf.get_width() + gap, centery=cy))
            line_y = cy + max(icon_surf.get_height(), text_surf.get_height()) // 2 + 4
        else:
            text_surf = font.render(self.text, True, fg)
            r = text_surf.get_rect(center=self.rect.center)
            surface.blit(text_surf, r)
            line_y = r.bottom + 4

        line_color = C_WHITE if self.active else C_GRAY
        pygame.draw.line(surface, line_color,
                         (self.rect.left, line_y), (self.rect.right, line_y), 3)

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

    FRICTION       = 0      # velocity multiplier per frame (inertia disabled)
    SPRING         = 1.0    # overscroll snap-back (1.0 = instant)
    DRAG_THRESHOLD = 25     # px of horizontal movement to start a scroll

    def __init__(self, rect, buttons, btn_w, btn_gap):
        self.rect    = pygame.Rect(rect)
        self.buttons = buttons
        self.btn_gap = btn_gap

        # 4 or fewer buttons: expand to fill full width (scroll disabled)
        if len(buttons) <= 4:
            btn_w = (self.rect.width - btn_gap * (len(buttons) + 1)) // len(buttons)
        self.btn_w   = btn_w

        btn_h = min(400, self.rect.height - 15 - 20)  # max 200px, 15px top + 20px bottom padding
        btn_y = self.rect.y + 15

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
                if self._screen_rect(btn).collidepoint(event.pos):
                    btn.is_pressed   = True
                    self.pressed_btn = btn
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
            self.velocity = 0.0  # no inertia on release
            # Clamp overscroll immediately
            if self.scroll_x < self.min_scroll:
                self.scroll_x = self.min_scroll
            elif self.scroll_x > self.max_scroll:
                self.scroll_x = self.max_scroll
            if not self.is_scroll and self.pressed_btn:
                self.pressed_btn.is_pressed = False
                if self.pressed_btn.action:
                    self.pressed_btn.action()
                self.pressed_btn = None
            elif self.pressed_btn:
                self.pressed_btn.is_pressed = False
                self.pressed_btn = None
            return True

        return False

    def draw(self, surface, font, font_icon=None):
        clip = surface.get_clip()
        surface.set_clip(self.rect)
        for btn in self.buttons:
            sr = self._screen_rect(btn)
            if sr.right <= self.rect.left or sr.left >= self.rect.right:
                continue
            bg = C_ORANGE if btn.is_pressed else C_BTN
            # Draw button with cut bottom-right corner
            c = 40  # corner cut size
            l, t, r, b = sr.left, sr.top, sr.right, sr.bottom
            pts = [(l, t), (r, t), (r, b - c), (r - c, b), (l, b)]
            pygame.draw.polygon(surface, bg, pts)
            if btn.icon and font_icon:
                icon_surf = font_icon.render(btn.icon, True, C_WHITE)
                text_surf = font.render(btn.text, True, C_WHITE)
                gap       = 6
                total_h   = icon_surf.get_height() + gap + text_surf.get_height()
                top       = sr.centery - total_h // 2
                surface.blit(icon_surf, icon_surf.get_rect(centerx=sr.centerx, top=top))
                surface.blit(text_surf, text_surf.get_rect(centerx=sr.centerx,
                                                            top=top + icon_surf.get_height() + gap))
            else:
                text_surf = font.render(btn.text, True, C_WHITE)
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
        self.width = self.config['screen_width']
        self.height = self.config['screen_height']
        print(f"Creating display: {self.width}x{self.height}")
        flags = pygame.FULLSCREEN if IS_PI else 0
        self.screen = pygame.display.set_mode((self.width, self.height), flags)
        print("Display created successfully")
        pygame.display.set_caption("Arcade Control")

        # Fonts
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'Rajdhani-Bold.ttf')
            self.font = pygame.font.Font(font_path, 72)
            self.font_large = pygame.font.Font(font_path, 108)
            self.font_action = pygame.font.Font(font_path, 36)
        except:
            self.font = pygame.font.Font(None, 72)
            self.font_large = pygame.font.Font(None, 96)
            self.font_action = pygame.font.Font(None, 36)

        self.bg_color = C_BG
        self.text_color = C_WHITE

        # Smaller font for tab bar and slot sub-tabs
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'Rajdhani-Bold.ttf')
            self.font_tab = pygame.font.Font(font_path, 42)
            self.font_slot = pygame.font.Font(font_path, 30)
        except:
            self.font_tab = pygame.font.Font(None, 42)
            self.font_slot = pygame.font.Font(None, 30)

        # Monospace font for clock
        # Monospace font for clock — bundled with the project
        try:
            mono_path = os.path.join(os.path.dirname(__file__), 'fonts', 'NotoSansMono-Regular.ttf')
            self.font_mono = pygame.font.Font(mono_path, 34)
        except:
            self.font_mono = pygame.font.SysFont('monospace', 34)

        # Material Symbols icon font
        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'fonts', 'MaterialSymbols.ttf')
            _f = pygame.font.Font(icon_path, 52)
            _f.render('test', True, (255,255,255))  # validate not a null/variable font
            self.font_icon        = _f
            self.font_icon_sm     = pygame.font.Font(icon_path, 36)
            self.font_icon_action = pygame.font.Font(icon_path, 80)
            self.font_icon_lock   = pygame.font.Font(icon_path, 180)
        except Exception as e:
            print(f"[WARN] Icon font not loaded: {e}")
            self.font_icon        = None
            self.font_icon_sm     = None
            self.font_icon_action = None
            self.font_icon_lock   = None

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
        self.bt_remove_buttons = []
        self.bt_activate_btn = None
        self.bt_refresh_btn  = None
        self.bt_close_btn    = None
        self.current_tab  = 'sistema'
        self.current_slot = 0  # 0 = general, 1-9 = save slots

        # Hardware controllers
        self.hid = None
        self.gpio = GPIOController(self.config['gpio_power_pin'])

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
        GAP_N    = 8
        NUM_ROWS = 4
        NUM_COLS = 3
        NUM_TOP  = 310                             # below icon + pin dots
        PAD_SIDE = 226                             # horizontal margin
        PAD_BOT  = 20
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
        GAP      = 4
        TAB_Y    = 90
        TAB_H    = 140
        TAB_W    = (self.width - GAP * 3) // 4   # 4 tabs with 3 gaps between
        SLOT_Y   = TAB_Y + TAB_H + 10  # 155  – 10px gap below tab bar
        SLOT_H   = 130
        SLOT_W   = (self.width - GAP * 9) // 10  # 10 items (GENERAL + 1-9) with 9 gaps
        CONT_Y   = TAB_Y + TAB_H        # 145  – non-Partida content starts here
        CONT_Y_P = SLOT_Y + SLOT_H      # 220  – Partida content starts here

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
            return ScrollMenu((0, y, self.width, h), btns, btn_w=340, btn_gap=20)

        CLOCK_H  = 50                        # space reserved at bottom for the clock
        h_norm = self.height - CONT_Y - CLOCK_H
        h_part = self.height - CONT_Y_P - CLOCK_H

        self.partida_general_btns = [
            SimpleButton((0,0,0,0), "PAUSAR",       action=self.pause_game,    icon='\ue034'),  # pause
            SimpleButton((0,0,0,0), "INFO",         action=self.game_info,     icon='\ue88e'),  # info
            SimpleButton((0,0,0,0), "FAST FORWARD", action=self.fast_forward,  icon='\ue01f'),  # fast_forward
            SimpleButton((0,0,0,0), "SCREENSHOT",   action=self.screenshot,    icon='\ue3b0'),  # add_a_photo
            SimpleButton((0,0,0,0), "MENU",         action=self.mame_menu,     icon='\ue5d2'),  # menu
            SimpleButton((0,0,0,0), "REINICIAR",    action=self.restart_game,  icon='\ue5d5'),  # refresh
            SimpleButton((0,0,0,0), "SALIR",        action=self.exit_game,     icon='\ue9ba'),  # exit_to_app
        ]
        self.partida_slot_btns = [
            SimpleButton((0,0,0,0), "SAVE", action=self.save_state, icon='\ue161'),  # save
            SimpleButton((0,0,0,0), "LOAD", action=self.load_state, icon='\ue2c4'),  # folder_open
        ]
        self.partida_general_scroll = make_scroll(CONT_Y_P, h_part, self.partida_general_btns)
        self.partida_slot_scroll    = make_scroll(CONT_Y_P, h_part, self.partida_slot_btns)

        self.tab_scroll_menus = {
            'sistema':  make_scroll(CONT_Y, h_norm, [
                SimpleButton((0,0,0,0), "ENCENDER PC",  action=self.power_on_confirm,  icon='\ue1a7'),  # power
                SimpleButton((0,0,0,0), "APAGAR PC",    action=self.power_off_confirm, icon='\ue8ac'),  # power_off
                SimpleButton((0,0,0,0), "PANTALLA OFF", action=self.screen_off,        icon='\ue8d9'),  # screen_lock_power
                SimpleButton((0,0,0,0), "BLOQUEAR",     action=self.lock_screen,       icon='\ue897'),  # lock
                SimpleButton((0,0,0,0), "DEBUG",        action=self.open_debug,        icon='\ue868'),  # bug_report
                SimpleButton((0,0,0,0), "UPDATE",       action=self.update_confirm,    icon='\ue923'),  # system_update
                SimpleButton((0,0,0,0), "BLUETOOTH",    action=self.open_bt_screen,    icon='\ue1a8'),  # bluetooth
            ]),
            'sonido':   make_scroll(CONT_Y, h_norm, [
                SimpleButton((0,0,0,0), "VOL +", action=self.volume_up,   icon='\ue050'),  # volume_up
                SimpleButton((0,0,0,0), "VOL -", action=self.volume_down, icon='\ue04d'),  # volume_down
                SimpleButton((0,0,0,0), "MUTE",  action=self.mute,        icon='\ue04f'),  # volume_off
            ]),
            'monedero': make_scroll(CONT_Y, h_norm, [
                SimpleButton((0,0,0,0), "COIN P1", action=self.coin_p1, icon='\ue227'),  # monetization_on
                SimpleButton((0,0,0,0), "COIN P2", action=self.coin_p2, icon='\ue227'),  # monetization_on
            ]),
        }
            
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

    def switch_slot(self, slot):
        self.current_slot = slot
        for i, btn in enumerate(self.slot_buttons):
            btn.active = (i == slot)

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
        """Save MAME state: Shift+F7, then slot digit"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F7, Modifier.LEFT_SHIFT)
                hid.send_key(self._SLOT_KEYS[self.current_slot - 1])
        except Exception as e:
            print(f"Error saving state slot {self.current_slot}: {e}")

    def load_state(self):
        """Load MAME state: F7, then slot digit"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F7)
                hid.send_key(self._SLOT_KEYS[self.current_slot - 1])
        except Exception as e:
            print(f"Error loading state slot {self.current_slot}: {e}")

    def pause_game(self):
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_P)
        except Exception as e:
            print(f"Error sending pause: {e}")

    def game_info(self):
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F2)
        except Exception as e:
            print(f"Error sending info: {e}")

    def fast_forward(self):
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_INSERT)
        except Exception as e:
            print(f"Error sending fast forward: {e}")

    def screenshot(self):
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F12)
        except Exception as e:
            print(f"Error sending screenshot: {e}")

    def mame_menu(self):
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_TAB)
        except Exception as e:
            print(f"Error sending menu: {e}")

    def restart_game(self):
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F3)
        except Exception as e:
            print(f"Error sending restart: {e}")

    def exit_game(self):
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_ESC)
        except Exception as e:
            print(f"Error sending exit: {e}")

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
                self.font.render("Actualizando" + dots, True, C_WHITE),
                self.font.render("Actualizando" + dots, True, C_WHITE).get_rect(
                    center=(self.width // 2, self.height // 2 - 50))
            )
            self.screen.blit(
                self.font_tab.render(last_line[:55], True, C_GRAY),
                self.font_tab.render(last_line[:55], True, C_GRAY).get_rect(
                    center=(self.width // 2, self.height // 2 + 20))
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
        l1 = self.font.render(line1[:40], True, color)
        self.screen.blit(l1, l1.get_rect(center=(self.width // 2, self.height // 2 - 40)))
        if success:
            l2 = self.font.render("Reiniciando...", True, C_GRAY)
            self.screen.blit(l2, l2.get_rect(center=(self.width // 2, self.height // 2 + 40)))
        pygame.display.flip()
        pygame.time.wait(2000)

    # ── Bluetooth pairing ─────────────────────────────────────────────────────

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
        self._bt_build_buttons()

    def _bt_build_buttons(self):
        devices = self.bt_data.get('devices', [])
        ROW_H       = 110
        ROW_Y_START = 185
        BTN_W       = 260
        BTN_H       = 72

        self.bt_remove_buttons = []
        for i, dev in enumerate(devices):
            y   = ROW_Y_START + i * ROW_H
            mac = dev['mac']
            btn = SimpleButton(
                (self.width - BTN_W - 30, y + (ROW_H - BTN_H) // 2, BTN_W, BTN_H),
                'DESEMPAREJAR',
                action=lambda m=mac: self.bt_remove_device(m),
                icon='\ue872',   # delete
            )
            self.bt_remove_buttons.append(btn)

        # Bottom row: activate + refresh + close
        btn_h = 90
        btn_y = self.height - btn_h - 25
        btn_w = 360
        gap   = 24
        n     = 3
        total_w = n * btn_w + (n - 1) * gap
        x = (self.width - total_w) // 2

        self.bt_activate_btn = SimpleButton(
            (x, btn_y, btn_w, btn_h), 'ACTIVAR EMPAREJAMIENTO',
            action=self.bt_activate_pairing, icon='\ue1a8',
        )
        self.bt_refresh_btn = SimpleButton(
            (x + btn_w + gap, btn_y, btn_w, btn_h), 'ACTUALIZAR',
            action=self.bt_refresh, icon='\ue5d5',
        )
        self.bt_close_btn = SimpleButton(
            (x + 2 * (btn_w + gap), btn_y, btn_w, btn_h), 'CERRAR',
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
        import subprocess
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

        self.bt_status_msg = '✓ Listo — busca Arcade HID Keyboard en Windows'
        self._bt_load_data()

    def bt_remove_device(self, mac):
        import subprocess
        self.bt_status_msg = f'Desemparejando {mac}...'
        self.draw_bt_screen()
        pygame.event.pump()
        try:
            subprocess.run(['bluetoothctl', 'remove', mac], timeout=8, capture_output=True)
            self.bt_status_msg = f'✓ Dispositivo {mac} eliminado'
        except Exception as e:
            self.bt_status_msg = f'Error: {e}'
        self._bt_load_data()

    def bt_pair(self):  # backwards compat
        self.open_bt_screen()

    def draw_bt_screen(self):
        self.screen.fill(C_BG)

        # ── Header ───────────────────────────────────────────────────
        title = self.font.render('BLUETOOTH', True, C_WHITE)
        self.screen.blit(title, title.get_rect(centerx=self.width // 2, top=18))

        srv_ok    = self.bt_data.get('server_active', False)
        srv_color = C_WHITE if srv_ok else C_GRAY
        srv_text  = '● SERVIDOR ACTIVO' if srv_ok else '○ SERVIDOR INACTIVO'
        srv_surf  = self.font_action.render(srv_text, True, srv_color)
        self.screen.blit(srv_surf, (30, 28))

        pygame.draw.line(self.screen, C_GRAY, (20, 105), (self.width - 20, 105), 2)

        # ── Column headers ────────────────────────────────────────────
        for text, x in [('DISPOSITIVO', 80), ('MAC', 520), ('ESTADO', 870)]:
            self.screen.blit(self.font_slot.render(text, True, C_GRAY), (x, 112))
        pygame.draw.line(self.screen, C_GRAY, (20, 155), (self.width - 20, 155), 1)

        # ── Device rows ───────────────────────────────────────────────
        ROW_H       = 110
        ROW_Y_START = 158
        devices     = self.bt_data.get('devices', [])

        if not devices:
            s = self.font_tab.render('No hay dispositivos emparejados', True, C_GRAY)
            self.screen.blit(s, s.get_rect(centerx=self.width // 2, top=230))
        else:
            for i, dev in enumerate(devices):
                y  = ROW_Y_START + i * ROW_H
                cy = y + ROW_H // 2

                if i % 2 == 0:
                    pygame.draw.rect(self.screen, (15, 15, 30), (0, y, self.width, ROW_H))

                # BT icon + name
                x_icon = 30
                if self.font_icon_sm:
                    ic = self.font_icon_sm.render('\ue1a8', True, C_WHITE)
                    self.screen.blit(ic, (x_icon, cy - ic.get_height() // 2))
                    x_icon += ic.get_width() + 8
                name_s = self.font_tab.render(dev['name'][:26], True, C_WHITE)
                self.screen.blit(name_s, (x_icon, cy - name_s.get_height() // 2))

                # MAC
                mac_s = self.font_action.render(dev['mac'], True, C_GRAY)
                self.screen.blit(mac_s, (520, cy - mac_s.get_height() // 2))

                # Connection badge
                if dev['connected']:
                    badge_s = self.font_action.render('● CONECTADO',    True, C_WHITE)
                else:
                    badge_s = self.font_action.render('○ NO CONECTADO', True, C_GRAY)
                self.screen.blit(badge_s, (870, cy - badge_s.get_height() // 2))

                # Remove button
                if i < len(self.bt_remove_buttons):
                    self.bt_remove_buttons[i].draw(self.screen, self.font_action, self.font_icon_sm)

        # ── Status message ───────────────────────────────────────────────
        if self.bt_status_msg:
            color = C_ORANGE if self.bt_status_msg.startswith('Error') else C_WHITE
            msg_s = self.font_tab.render(self.bt_status_msg[:70], True, color)
            self.screen.blit(msg_s, msg_s.get_rect(
                centerx=self.width // 2, bottom=self.height - 130))

        # ── Bottom buttons ───────────────────────────────────────────────
        for btn in (self.bt_activate_btn, self.bt_refresh_btn, self.bt_close_btn):
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
        icon_y = 120
        if self.font_icon_lock:
            icon_surf = self.font_icon_lock.render('\ue897', True, C_WHITE)
            self.screen.blit(icon_surf, icon_surf.get_rect(center=(self.width // 2, icon_y)))
            dots_y = icon_y + icon_surf.get_height() // 2 + 30
        else:
            dots_y = 200

        # PIN dot indicators centered
        max_digits = 6
        dot_r   = 12
        dot_gap = 16
        total_w = max_digits * (dot_r * 2) + (max_digits - 1) * dot_gap
        dot_cx  = (self.width - total_w) // 2 + dot_r
        for i in range(max_digits):
            cx = dot_cx + i * (dot_r * 2 + dot_gap)
            if i < len(self.pin_input):
                pygame.draw.circle(self.screen, C_WHITE, (cx, dots_y), dot_r)
            else:
                pygame.draw.circle(self.screen, C_GRAY, (cx, dots_y), dot_r, 2)

        for btn in self.numpad_buttons:
            btn.draw(self.screen, self.font_tab)
        
    def draw_lock_screen(self):
        """Draw lock screen with numpad"""
        self.draw_lock_screen_base()
        pygame.display.flip()
        
    def draw_main_screen_base(self):
        self.screen.fill(self.bg_color)

        # Title
        title = self.font.render("ARCADE CONTROL", True, C_WHITE)
        self.screen.blit(title, title.get_rect(center=(self.width // 2, 48)))

        # Main tab bar
        for btn in self.tab_buttons:
            btn.draw(self.screen, self.font_tab, self.font_icon)

        # Slot sub-tabs (only for Partida)
        if self.current_tab == 'partida':
            for btn in self.slot_buttons:
                btn.draw(self.screen, self.font_slot)

        # Scrollable content area for active tab
        self._active_scroll_menu().draw(self.screen, self.font_action, self.font_icon_action)

        # Clock — bottom center, monospace, gray
        datetime_str = datetime.now(ZoneInfo('Europe/Madrid')).strftime('%d-%m-%Y  %H:%M:%S')
        time_surf = self.font_mono.render(datetime_str, True, C_GRAY)
        self.screen.blit(time_surf, time_surf.get_rect(
            centerx=self.width // 2, centery=self.height - 120))
        
    def draw_main_screen(self):
        """Draw main control interface"""
        self.draw_main_screen_base()
        pygame.display.flip()

    def open_debug(self):
        self.debug_screen = True
        self.debug_info = self.get_network_info()
        btn_w, btn_h = 240, 65
        self.debug_close_btn = SimpleButton(
            ((self.width - btn_w) // 2, 370, btn_w, btn_h),
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

        title = self.font.render("DEBUG", True, C_WHITE)
        self.screen.blit(title, title.get_rect(center=(self.width // 2, 45)))
        pygame.draw.line(self.screen, C_GRAY, (20, 85), (self.width - 20, 85), 2)

        rows = [
            ("WiFi",      "CONECTADO" if info['connected'] else "NO CONECTADO",
                          C_WHITE if info['connected'] else C_GRAY),
            ("SSID",      info['ssid'] or "\u2014",       C_WHITE),
            ("Cobertura", f"{info['signal_pct']}%" if info['signal_pct'] is not None else "\u2014", C_WHITE),
            ("IP",        info['ip'] or "\u2014",          C_WHITE),
        ]

        y = 110
        row_h = 55
        col_label = 40
        col_value = 240
        for label, value, color in rows:
            lbl = self.font_tab.render(label + ":", True, C_GRAY)
            self.screen.blit(lbl, (col_label, y + (row_h - lbl.get_height()) // 2))
            val = self.font_tab.render(value, True, color)
            self.screen.blit(val, (col_value, y + (row_h - val.get_height()) // 2))
            y += row_h

        pygame.draw.line(self.screen, C_GRAY, (20, y + 10), (self.width - 20, y + 10), 2)
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

        title_surf  = self.font.render(self.confirmation_dialog['title'], True, C_WHITE)
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
            text_surf = self.font.render(label, True, C_WHITE)
            if self.font_icon:
                icon_surf = self.font_icon.render(icon_cp, True, C_WHITE)
                gap = 10
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
                        for btn in (self.bt_activate_btn, self.bt_refresh_btn, self.bt_close_btn):
                            if btn:
                                btn.handle_event(event)
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
        while self.running:
            self.handle_events()

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
                
            self.clock.tick(30)
            
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
