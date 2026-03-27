#!/usr/bin/env python3
"""
Arcade Control Panel - Main Application
Touchscreen interface for arcade machine control
"""

import os
import sys
import time
import platform
import pygame
from pygame.locals import *

from config import Config
from usb_hid import USBHID, KeyCode, Modifier
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


class SimpleButton:
    """Simple square button: black fill, white 3px border, white text."""

    def __init__(self, rect, text, color=None, action=None):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.action = action
        self.is_pressed = False

    def draw(self, surface, font):
        color = C_ORANGE if self.is_pressed else C_WHITE
        pygame.draw.rect(surface, C_BG, self.rect)
        pygame.draw.rect(surface, color, self.rect, 3)
        text_surf = font.render(self.text, True, color)
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
    """Tab/slot selector: active = white bg + black text, inactive = black bg + white border."""

    def __init__(self, rect, text, active=False, action=None):
        self.rect   = pygame.Rect(rect)
        self.text   = text
        self.active = active
        self.action = action

    def draw(self, surface, font):
        if self.active:
            pygame.draw.rect(surface, C_WHITE, self.rect)
            text_surf = font.render(self.text, True, C_BG)
        else:
            pygame.draw.rect(surface, C_BG, self.rect)
            pygame.draw.rect(surface, C_WHITE, self.rect, 3)
            text_surf = font.render(self.text, True, C_WHITE)
        surface.blit(text_surf, text_surf.get_rect(center=self.rect.center))

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

    FRICTION       = 0      # velocity multiplier per frame
    SPRING         = 0.20   # overscroll pull-back factor per frame
    DRAG_THRESHOLD = 25     # px of horizontal movement to start a scroll

    def __init__(self, rect, buttons, btn_w, btn_gap):
        self.rect    = pygame.Rect(rect)
        self.buttons = buttons
        self.btn_gap = btn_gap

        # 4 or fewer buttons: expand to fill full width (scroll disabled)
        if len(buttons) <= 4:
            btn_w = (self.rect.width - btn_gap * (len(buttons) + 1)) // len(buttons)
        self.btn_w   = btn_w

        btn_h = self.rect.height - 30   # 15 px padding top & bottom
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

    def draw(self, surface, font):
        clip = surface.get_clip()
        surface.set_clip(self.rect)
        for btn in self.buttons:
            sr = self._screen_rect(btn)
            if sr.right <= self.rect.left or sr.left >= self.rect.right:
                continue
            color = C_ORANGE if btn.is_pressed else C_WHITE
            pygame.draw.rect(surface, C_BG, sr)
            pygame.draw.rect(surface, color, sr, 3)
            text_surf = font.render(btn.text, True, color)
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
        except:
            self.font = pygame.font.Font(None, 72)
            self.font_large = pygame.font.Font(None, 96)

        self.bg_color = C_BG
        self.text_color = C_WHITE

        # Smaller font for tab bar and slot sub-tabs
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'Rajdhani-Bold.ttf')
            self.font_tab = pygame.font.Font(font_path, 38)
        except:
            self.font_tab = pygame.font.Font(None, 38)

        # State
        self.running = True
        self.locked = True
        self.screen_on = True
        self.pin_input = ""
        self.confirmation_dialog = None
        self.debug_screen = False
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
        # Numpad (lock screen)
        numpad_layout = [
            ['1', '2', '3'],
            ['4', '5', '6'],
            ['7', '8', '9'],
            ['C', '0', 'OK']
        ]
        btn_width = 240
        btn_height = 160
        spacing = 30
        start_x = (self.width - btn_width * 3 - spacing * 2) // 2
        start_y = 380
        for row_idx, row in enumerate(numpad_layout):
            for col_idx, label in enumerate(row):
                x = start_x + col_idx * (btn_width + spacing)
                y = start_y + row_idx * (btn_height + spacing)
                self.numpad_buttons.append(SimpleButton(
                    (x, y, btn_width, btn_height),
                    label, action=lambda l=label: self.numpad_press(l)
                ))

        # Layout constants
        GAP      = 4
        TAB_Y    = 90
        TAB_H    = 55
        TAB_W    = (self.width - GAP * 3) // 4   # 4 tabs with 3 gaps between
        SLOT_Y   = TAB_Y + TAB_H + 10  # 155  – 10px gap below tab bar
        SLOT_H   = 65
        SLOT_W   = (self.width - GAP * 9) // 10  # 10 items (GENERAL + 1-9) with 9 gaps
        CONT_Y   = TAB_Y + TAB_H        # 145  – non-Partida content starts here
        CONT_Y_P = SLOT_Y + SLOT_H      # 220  – Partida content starts here

        # Main tab bar (4 tabs)
        tab_defs = [
            ('sistema',  'SISTEMA'),
            ('sonido',   'SONIDO'),
            ('partida',  'PARTIDA'),
            ('monedero', 'MONEDERO'),
        ]
        self.tab_buttons = [
            TabButton(
                (i * (TAB_W + GAP), TAB_Y, TAB_W, TAB_H),
                name,
                active=(tid == self.current_tab),
                action=lambda t=tid: self.switch_tab(t)
            )
            for i, (tid, name) in enumerate(tab_defs)
        ]

        # Slot sub-tabs: GENERAL first, then 1-9
        slot_labels = ['GENERAL'] + [str(i) for i in range(1, 10)]
        self.slot_buttons = [
            TabButton(
                (i * (SLOT_W + GAP), SLOT_Y, SLOT_W, SLOT_H),
                label,
                active=(i == self.current_slot),
                action=lambda s=i: self.switch_slot(s)
            )
            for i, label in enumerate(slot_labels)
        ]

        # Per-tab scroll menus
        def make_scroll(y, h, btns):
            return ScrollMenu((0, y, self.width, h), btns, btn_w=340, btn_gap=20)

        h_norm = self.height - CONT_Y    # 335
        h_part = self.height - CONT_Y_P  # 285

        self.partida_general_btns = [
            SimpleButton((0, 0, 0, 0), "PAUSAR",       action=self.pause_game),
            SimpleButton((0, 0, 0, 0), "INFO",         action=self.game_info),
            SimpleButton((0, 0, 0, 0), "FAST FORWARD", action=self.fast_forward),
            SimpleButton((0, 0, 0, 0), "SCREENSHOT",   action=self.screenshot),
            SimpleButton((0, 0, 0, 0), "MENU",         action=self.mame_menu),
            SimpleButton((0, 0, 0, 0), "REINICIAR",    action=self.restart_game),
            SimpleButton((0, 0, 0, 0), "SALIR",        action=self.exit_game),
        ]
        self.partida_slot_btns = [
            SimpleButton((0, 0, 0, 0), "SAVE", action=self.save_state),
            SimpleButton((0, 0, 0, 0), "LOAD", action=self.load_state),
        ]
        self.partida_general_scroll = make_scroll(CONT_Y_P, h_part, self.partida_general_btns)
        self.partida_slot_scroll    = make_scroll(CONT_Y_P, h_part, self.partida_slot_btns)

        self.tab_scroll_menus = {
            'sistema':  make_scroll(CONT_Y, h_norm, [
                SimpleButton((0, 0, 0, 0), "ENCENDER PC",  action=self.power_on_confirm),
                SimpleButton((0, 0, 0, 0), "APAGAR PC",    action=self.power_off_confirm),
                SimpleButton((0, 0, 0, 0), "PANTALLA OFF", action=self.screen_off),
                SimpleButton((0, 0, 0, 0), "BLOQUEAR",     action=self.lock_screen),
                SimpleButton((0, 0, 0, 0), "DEBUG",        action=self.open_debug),
                SimpleButton((0, 0, 0, 0), "UPDATE",       action=self.update_confirm),
            ]),
            'sonido':   make_scroll(CONT_Y, h_norm, [
                SimpleButton((0, 0, 0, 0), "VOL +", action=self.volume_up),
                SimpleButton((0, 0, 0, 0), "VOL -", action=self.volume_down),
                SimpleButton((0, 0, 0, 0), "MUTE",  action=self.mute),
            ]),
            'monedero': make_scroll(CONT_Y, h_norm, [
                SimpleButton((0, 0, 0, 0), "COIN P1", action=self.coin_p1),
                SimpleButton((0, 0, 0, 0), "COIN P2", action=self.coin_p2),
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
        import subprocess
        self.confirmation_dialog = None

        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            env = os.environ.copy()
            env['GIT_TERMINAL_PROMPT'] = '0'  # never prompt for credentials
            proc = subprocess.Popen(
                ['git', 'pull'],
                cwd=repo_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        except Exception as e:
            self._show_update_result(False, str(e))
            return

        # Keep pumping events so pygame stays responsive (ESC cancels wait)
        dots = 0
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

            elapsed = (pygame.time.get_ticks() - start) // 500
            label = "Actualizando" + "." * (elapsed % 4)
            self.screen.fill(C_BG)
            msg = self.font.render(label, True, C_WHITE)
            self.screen.blit(msg, msg.get_rect(center=(self.width // 2, self.height // 2)))
            pygame.display.flip()
            self.clock.tick(10)

            if pygame.time.get_ticks() - start > 30000:
                proc.terminate()
                self._show_update_result(False, "Timeout")
                return

        stdout, _ = proc.communicate()
        success = proc.returncode == 0
        detail = (stdout or b'').decode('utf-8', errors='ignore').strip().splitlines()
        line1 = detail[0] if detail else ('OK' if success else 'Error')
        self._show_update_result(success, line1)

        if success:
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
    
    def draw_cyberpunk_bg(self):
        pass
    
    def draw_lock_screen_base(self):
        self.screen.fill(self.bg_color)

        if not self.screen_on:
            return

        title = self.font_large.render("ACCESS CONTROL", True, C_WHITE)
        self.screen.blit(title, title.get_rect(center=(self.width // 2, 150)))

        subtitle = self.font.render("ENTER PIN CODE", True, C_GRAY)
        self.screen.blit(subtitle, subtitle.get_rect(center=(self.width // 2, 260)))

        pin_box_width = 480
        pin_box_height = 120
        pin_box_x = (self.width - pin_box_width) // 2
        pin_box_y = 310

        pygame.draw.rect(self.screen, C_BG, (pin_box_x, pin_box_y, pin_box_width, pin_box_height))
        pygame.draw.rect(self.screen, C_WHITE, (pin_box_x, pin_box_y, pin_box_width, pin_box_height), 3)

        pin_display = "*" * len(self.pin_input) if self.pin_input else "----"
        pin_text = self.font_large.render(pin_display, True, C_WHITE)
        self.screen.blit(pin_text, pin_text.get_rect(center=(self.width // 2, pin_box_y + pin_box_height // 2)))

        for btn in self.numpad_buttons:
            btn.draw(self.screen, self.font)
        
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
            btn.draw(self.screen, self.font_tab)

        # Slot sub-tabs (only for Partida)
        if self.current_tab == 'partida':
            for btn in self.slot_buttons:
                btn.draw(self.screen, self.font_tab)

        # Scrollable content area for active tab
        self._active_scroll_menu().draw(self.screen, self.font)
        
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

        dialog_w = 450
        dialog_h = 220
        dialog_x = (self.width - dialog_w) // 2
        dialog_y = (self.height - dialog_h) // 2

        pygame.draw.rect(self.screen, C_BG, (dialog_x, dialog_y, dialog_w, dialog_h))
        pygame.draw.rect(self.screen, C_WHITE, (dialog_x, dialog_y, dialog_w, dialog_h), 3)

        title = self.font_large.render(self.confirmation_dialog['title'], True, C_WHITE)
        self.screen.blit(title, title.get_rect(center=(self.width // 2, dialog_y + 70)))

        if not hasattr(self, 'dialog_yes_btn'):
            self.dialog_yes_btn = SimpleButton((dialog_x + 60,  dialog_y + 140, 140, 55), "SI")
            self.dialog_no_btn  = SimpleButton((dialog_x + 250, dialog_y + 140, 140, 55), "NO")

        self.dialog_yes_btn.action = self.confirmation_dialog['action']
        self.dialog_no_btn.action  = lambda: setattr(self, 'confirmation_dialog', None)

        self.dialog_yes_btn.draw(self.screen, self.font)
        self.dialog_no_btn.draw(self.screen, self.font)

        pygame.display.flip()
                    
    def handle_events(self):
        """Handle pygame events"""
        for event in pygame.event.get():
            if event.type == QUIT:
                self.running = False
            elif event.type == KEYDOWN:
                if event.key == K_ESCAPE:
                    self.running = False
            elif event.type in (MOUSEBUTTONDOWN, MOUSEMOTION, MOUSEBUTTONUP):
                # If dialog is open, only handle dialog buttons (no motion needed)
                if self.confirmation_dialog:
                    if event.type != MOUSEMOTION and hasattr(self, 'dialog_yes_btn'):
                        self.dialog_yes_btn.handle_event(event)
                        self.dialog_no_btn.handle_event(event)
                    continue

                # Debug screen: only the close button
                if self.debug_screen:
                    if event.type in (MOUSEBUTTONDOWN, MOUSEBUTTONUP):
                        self.debug_close_btn.handle_event(event)
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

            if not self.locked and not self.confirmation_dialog and not self.debug_screen:
                self._active_scroll_menu().update()

            # Draw appropriate screen
            if self.confirmation_dialog:
                self.draw_confirmation_dialog()
            elif self.debug_screen:
                self.draw_debug_screen()
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
