#!/usr/bin/env python3
"""
Arcade Control Panel - Main Application - CYBERPUNK THEME
Touchscreen interface for arcade machine control
"""

import os
import sys
import time
import math
import pygame
from pygame.locals import *

from config import Config
from usb_hid import USBHID, KeyCode
from gpio_controller import GPIOController
from keyboard_mapper import ArcadeKeyMapper


# Configure SDL for framebuffer
os.environ['SDL_VIDEODRIVER'] = 'fbcon'
os.environ['SDL_FBDEV'] = '/dev/fb0'
os.environ['SDL_NOMOUSE'] = '1'

# Cyberpunk color scheme
CYBER_BG = (10, 14, 39)          # Dark blue-black
CYBER_CYAN = (0, 255, 255)       # Bright cyan
CYBER_MAGENTA = (255, 0, 255)    # Bright magenta
CYBER_YELLOW = (255, 255, 0)     # Electric yellow
CYBER_GREEN = (0, 255, 100)      # Neon green
CYBER_PURPLE = (138, 43, 226)    # Deep purple
CYBER_DARK = (20, 20, 50)        # Dark accent
CYBER_GLOW_CYAN = (0, 200, 255, 128)   # Semi-transparent cyan glow
CYBER_GLOW_MAGENTA = (255, 0, 200, 128) # Semi-transparent magenta glow


class CyberButton:
    """Cyberpunk styled button widget"""
    
    def __init__(self, rect, text, color, action=None):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.color = color
        self.action = action
        self.is_hovered = False
        self.glow_intensity = 0
        self.animation_time = 0
        
    def draw(self, surface, font):
        """Draw cyberpunk button with glow effects"""
        # Animate glow
        self.animation_time += 0.05
        pulse = abs(math.sin(self.animation_time)) * 0.3 + 0.7
        
        # Glow effect when hovered
        if self.is_hovered:
            self.glow_intensity = min(1.0, self.glow_intensity + 0.1)
        else:
            self.glow_intensity = max(0.0, self.glow_intensity - 0.1)
        
        # Draw outer glow
        if self.glow_intensity > 0:
            glow_surface = pygame.Surface((self.rect.width + 20, self.rect.height + 20), pygame.SRCALPHA)
            glow_color = (*self.color, int(80 * self.glow_intensity * pulse))
            pygame.draw.rect(glow_surface, glow_color, glow_surface.get_rect(), border_radius=15)
            surface.blit(glow_surface, (self.rect.x - 10, self.rect.y - 10))
        
        # Draw main button with corner cuts (cyberpunk style)
        corner_size = 8
        points = [
            (self.rect.x + corner_size, self.rect.y),
            (self.rect.right - corner_size, self.rect.y),
            (self.rect.right, self.rect.y + corner_size),
            (self.rect.right, self.rect.bottom - corner_size),
            (self.rect.right - corner_size, self.rect.bottom),
            (self.rect.x + corner_size, self.rect.bottom),
            (self.rect.x, self.rect.bottom - corner_size),
            (self.rect.x, self.rect.y + corner_size),
        ]
        
        # Button background
        bg_color = tuple(int(c * 0.3) for c in self.color)
        pygame.draw.polygon(surface, bg_color, points)
        
        # Neon border
        border_color = tuple(int(c * (pulse if self.is_hovered else 0.8)) for c in self.color)
        pygame.draw.polygon(surface, border_color, points, 3)
        
        # Scanline effect
        for i in range(0, self.rect.height, 4):
            line_alpha = 30
            line_surf = pygame.Surface((self.rect.width - 16, 1), pygame.SRCALPHA)
            line_surf.fill((255, 255, 255, line_alpha))
            surface.blit(line_surf, (self.rect.x + 8, self.rect.y + i))
        
        # Text with glow
        text_color = tuple(min(255, int(c * (1.2 if self.is_hovered else 1.0))) for c in self.color)
        text_surf = font.render(self.text, True, text_color)
        text_rect = text_surf.get_rect(center=self.rect.center)
        
        # Text shadow/glow
        if self.is_hovered:
            shadow_surf = font.render(self.text, True, (*self.color[:3], 128))
            for offset in [(1, 1), (-1, -1), (1, -1), (-1, 1)]:
                shadow_rect = text_rect.copy()
                shadow_rect.x += offset[0] * 2
                shadow_rect.y += offset[1] * 2
                surface.blit(shadow_surf, shadow_rect)
        
        surface.blit(text_surf, text_rect)
        
    def handle_event(self, event):
        """Handle mouse/touch events"""
        if event.type == MOUSEMOTION:
            self.is_hovered = self.rect.collidepoint(event.pos)
        elif event.type == MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                if self.action:
                    self.action()
                return True
        return False


class ArcadeControlApp:
    """Main application class"""
    
    def __init__(self):
        """Initialize application"""
        pygame.init()
        
        # Load configuration
        self.config = Config()
        
        # Screen setup
        self.width = self.config['screen_width']
        self.height = self.config['screen_height']
        self.screen = pygame.display.set_mode((self.width, self.height), pygame.FULLSCREEN)
        pygame.display.set_caption("CYBER//ARCADE Control")
        
        # Fonts - Use Rajdhani for cyberpunk look
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'fonts', 'Rajdhani-Bold.ttf')
            self.font = pygame.font.Font(font_path, 32)
            self.font_large = pygame.font.Font(font_path, 52)
        except:
            # Fallback to default
            self.font = pygame.font.Font(None, 32)
            self.font_large = pygame.font.Font(None, 48)
        
        # Cyberpunk colors
        self.bg_color = CYBER_BG
        self.text_color = CYBER_CYAN
        
        # State
        self.running = True
        self.locked = True
        self.screen_on = False
        self.pin_input = ""
        self.confirmation_dialog = None
        self.scanline_offset = 0
        
        # Hardware controllers
        self.hid = None
        self.gpio = GPIOController(self.config['gpio_power_pin'])
        
        # Clock
        self.clock = pygame.time.Clock()
        
        # UI Elements
        self.buttons = []
        self.numpad_buttons = []
        self.main_buttons = []
        
        self.setup_ui()
        
    def setup_ui(self):
        """Setup UI elements"""
        # Numpad for PIN entry - cyberpunk colors
        numpad_layout = [
            ['1', '2', '3'],
            ['4', '5', '6'],
            ['7', '8', '9'],
            ['C', '0', 'OK']
        ]
        
        numpad_colors = [
            [CYBER_CYAN, CYBER_CYAN, CYBER_CYAN],
            [CYBER_CYAN, CYBER_CYAN, CYBER_CYAN],
            [CYBER_CYAN, CYBER_CYAN, CYBER_CYAN],
            [CYBER_MAGENTA, CYBER_CYAN, CYBER_GREEN]  # C, 0, OK with different colors
        ]
        
        btn_width = 100
        btn_height = 65
        spacing = 12
        start_x = (self.width - btn_width * 3 - spacing * 2) // 2
        start_y = 150
        
        for row_idx, row in enumerate(numpad_layout):
            for col_idx, label in enumerate(row):
                x = start_x + col_idx * (btn_width + spacing)
                y = start_y + row_idx * (btn_height + spacing)
                
                btn = CyberButton(
                    (x, y, btn_width, btn_height),
                    label,
                    numpad_colors[row_idx][col_idx],
                    lambda l=label: self.numpad_press(l)
                )
                self.numpad_buttons.append(btn)
        
        # Main control buttons with cyberpunk colors
        btn_w = 180
        btn_h = 80
        spacing = 20
        cols = 3
        rows = 3
        
        start_x = (self.width - (btn_w * cols + spacing * (cols - 1))) // 2
        start_y = 100
        
        actions = [
            ("VOL +", self.volume_up, CYBER_GREEN),
            ("VOL -", self.volume_down, CYBER_GREEN),
            ("MUTE", self.mute, CYBER_YELLOW),
            ("COIN P1", self.coin_p1, CYBER_MAGENTA),
            ("COIN P2", self.coin_p2, CYBER_MAGENTA),
            ("PANTALLA OFF", self.screen_off, CYBER_PURPLE),
            ("ENCENDER PC", self.power_on_confirm, CYBER_CYAN),
            ("APAGAR PC", self.power_off_confirm, (255, 50, 50)),  # Red
            ("BLOQUEAR", self.lock_screen, CYBER_YELLOW),
        ]
        
        for idx, (label, action, color) in enumerate(actions):
            row = idx // cols
            col = idx % cols
            x = start_x + col * (btn_w + spacing)
            y = start_y + row * (btn_h + spacing)
            
            btn = CyberButton(
                (x, y, btn_w, btn_h),
                label,
                color,
                action
            )
            self.main_buttons.append(btn)
            
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
    
    def draw_cyberpunk_bg(self):
        """Draw cyberpunk background effects"""
        # Scanline effect
        self.scanline_offset = (self.scanline_offset + 1) % 4
        for i in range(self.scanline_offset, self.height, 4):
            pygame.draw.line(self.screen, (15, 20, 45), (0, i), (self.width, i), 1)
        
        # Corner decorations
        corner_color = CYBER_CYAN
        corner_size = 40
        corner_thickness = 3
        
        # Top-left
        pygame.draw.line(self.screen, corner_color, (10, 10), (10 + corner_size, 10), corner_thickness)
        pygame.draw.line(self.screen, corner_color, (10, 10), (10, 10 + corner_size), corner_thickness)
        
        # Top-right
        pygame.draw.line(self.screen, corner_color, (self.width - 10, 10), (self.width - 10 - corner_size, 10), corner_thickness)
        pygame.draw.line(self.screen, corner_color, (self.width - 10, 10), (self.width - 10, 10 + corner_size), corner_thickness)
        
        # Bottom-left
        pygame.draw.line(self.screen, corner_color, (10, self.height - 10), (10 + corner_size, self.height - 10), corner_thickness)
        pygame.draw.line(self.screen, corner_color, (10, self.height - 10), (10, self.height - 10 - corner_size), corner_thickness)
        
        # Bottom-right
        pygame.draw.line(self.screen, corner_color, (self.width - 10, self.height - 10), (self.width - 10 - corner_size, self.height - 10), corner_thickness)
        pygame.draw.line(self.screen, corner_color, (self.width - 10, self.height - 10), (self.width - 10, self.height - 10 - corner_size), corner_thickness)
    
    def draw_lock_screen_base(self):
        """Draw lock screen content without flip"""
        self.screen.fill(self.bg_color)
        
        if not self.screen_on:
            return
        
        # Cyberpunk background
        self.draw_cyberpunk_bg()
            
        # Title with glitch effect
        title_text = ">> ACCESS CONTROL <<"
        title = self.font_large.render(title_text, True, CYBER_CYAN)
        title_rect = title.get_rect(center=(self.width // 2, 70))
        
        # Glow effect
        glow = self.font_large.render(title_text, True, (*CYBER_CYAN, 128))
        for offset in [(2, 2), (-2, -2)]:
            glow_rect = title_rect.copy()
            glow_rect.x += offset[0]
            glow_rect.y += offset[1]
            self.screen.blit(glow, glow_rect)
        
        self.screen.blit(title, title_rect)
        
        # Subtitle
        subtitle = self.font.render("ENTER PIN CODE", True, CYBER_MAGENTA)
        subtitle_rect = subtitle.get_rect(center=(self.width // 2, 105))
        self.screen.blit(subtitle, subtitle_rect)
        
        # PIN display with box
        pin_box_width = 200
        pin_box_height = 50
        pin_box_x = (self.width - pin_box_width) // 2
        pin_box_y = 130
        
        pygame.draw.rect(self.screen, CYBER_DARK, (pin_box_x, pin_box_y, pin_box_width, pin_box_height))
        pygame.draw.rect(self.screen, CYBER_CYAN, (pin_box_x, pin_box_y, pin_box_width, pin_box_height), 2)
        
        pin_display = "*" * len(self.pin_input) if self.pin_input else "----"
        pin_text = self.font_large.render(pin_display, True, CYBER_GREEN)
        pin_rect = pin_text.get_rect(center=(self.width // 2, pin_box_y + pin_box_height // 2))
        self.screen.blit(pin_text, pin_rect)
        
        # Numpad
        for btn in self.numpad_buttons:
            btn.draw(self.screen, self.font)
        
    def draw_lock_screen(self):
        """Draw lock screen with numpad"""
        self.draw_lock_screen_base()
        pygame.display.flip()
        
    def draw_main_screen_base(self):
        """Draw main screen content without flip"""
        self.screen.fill(self.bg_color)
        
        # Cyberpunk background
        self.draw_cyberpunk_bg()
        
        # Title with cyberpunk style
        title_text = "// CYBER ARCADE CONTROL //"
        title = self.font_large.render(title_text, True, CYBER_CYAN)
        title_rect = title.get_rect(center=(self.width // 2, 35))
        
        # Glow
        for offset in [(1, 1), (-1, -1), (2, 0), (-2, 0)]:
            glow = self.font_large.render(title_text, True, (*CYBER_CYAN, 80))
            glow_rect = title_rect.copy()
            glow_rect.x += offset[0]
            glow_rect.y += offset[1]
            self.screen.blit(glow, glow_rect)
        
        self.screen.blit(title, title_rect)
        
        # Status line
        status_text = "[ SYSTEM ONLINE ]"
        status = self.font.render(status_text, True, CYBER_GREEN)
        status_rect = status.get_rect(center=(self.width // 2, 65))
        self.screen.blit(status, status_rect)
        
        # Buttons
        for btn in self.main_buttons:
            btn.draw(self.screen, self.font)
        
    def draw_main_screen(self):
        """Draw main control interface"""
        self.draw_main_screen_base()
        pygame.display.flip()
        
    def draw_confirmation_dialog(self):
        """Draw confirmation dialog with cyberpunk style"""
        # First draw the main screen behind it
        if self.locked:
            self.draw_lock_screen_base()
        else:
            self.draw_main_screen_base()
        
        # Semi-transparent overlay with tint
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((10, 14, 39, 220))
        self.screen.blit(overlay, (0, 0))
        
        # Dialog box with cyberpunk style
        dialog_w = 450
        dialog_h = 220
        dialog_x = (self.width - dialog_w) // 2
        dialog_y = (self.height - dialog_h) // 2
        
        # Background
        pygame.draw.rect(self.screen, CYBER_DARK, (dialog_x, dialog_y, dialog_w, dialog_h))
        
        # Neon border with glow
        for thickness in range(4, 0, -1):
            alpha = 80 - thickness * 15
            border_surf = pygame.Surface((dialog_w + thickness * 4, dialog_h + thickness * 4), pygame.SRCALPHA)
            pygame.draw.rect(border_surf, (*CYBER_CYAN, alpha), border_surf.get_rect(), thickness)
            self.screen.blit(border_surf, (dialog_x - thickness * 2, dialog_y - thickness * 2))
        
        pygame.draw.rect(self.screen, CYBER_CYAN, (dialog_x, dialog_y, dialog_w, dialog_h), 3)
        
        # Warning symbol
        warning_y = dialog_y + 30
        pygame.draw.polygon(self.screen, CYBER_YELLOW, [
            (self.width // 2, warning_y),
            (self.width // 2 - 15, warning_y + 25),
            (self.width // 2 + 15, warning_y + 25)
        ])
        pygame.draw.circle(self.screen, CYBER_DARK, (self.width // 2, warning_y + 15), 3)
        pygame.draw.line(self.screen, CYBER_DARK, (self.width // 2, warning_y + 8), (self.width // 2, warning_y + 12), 2)
        
        # Title
        title = self.font_large.render(self.confirmation_dialog['title'], True, CYBER_CYAN)
        title_rect = title.get_rect(center=(self.width // 2, dialog_y + 80))
        self.screen.blit(title, title_rect)
        
        # Create buttons if not exist
        if not hasattr(self, 'dialog_yes_btn'):
            self.dialog_yes_btn = CyberButton(
                (dialog_x + 60, dialog_y + 140, 140, 55),
                "SÍ",
                CYBER_GREEN,
                None  # Action set dynamically
            )
            self.dialog_no_btn = CyberButton(
                (dialog_x + 250, dialog_y + 140, 140, 55),
                "NO",
                CYBER_MAGENTA,
                None  # Action set dynamically
            )
        
        # Update button actions
        self.dialog_yes_btn.action = self.confirmation_dialog['action']
        self.dialog_no_btn.action = lambda: setattr(self, 'confirmation_dialog', None)
        
        # Draw buttons
        self.dialog_yes_btn.draw(self.screen, self.font)
        self.dialog_no_btn.draw(self.screen, self.font)
        
        pygame.display.flip()
                    
    def handle_events(self):
        """Handle pygame events"""
        for event in pygame.event.get():
            if event.type == QUIT:
                self.running = False
            elif event.type == MOUSEBUTTONDOWN:
                # If dialog is open, only handle dialog buttons
                if self.confirmation_dialog:
                    if hasattr(self, 'dialog_yes_btn'):
                        if self.dialog_yes_btn.handle_event(event):
                            continue
                        if self.dialog_no_btn.handle_event(event):
                            continue
                    return  # Don't process other buttons
                
                # Wake screen on any touch
                if not self.screen_on:
                    self.wake_screen()
                    continue
                    
                # Handle button presses
                if self.locked:
                    for btn in self.numpad_buttons:
                        btn.handle_event(event)
                else:
                    for btn in self.main_buttons:
                        btn.handle_event(event)
            elif event.type == MOUSEMOTION:
                # Update hover states
                if self.confirmation_dialog:
                    if hasattr(self, 'dialog_yes_btn'):
                        self.dialog_yes_btn.handle_event(event)
                        self.dialog_no_btn.handle_event(event)
                elif self.locked:
                    for btn in self.numpad_buttons:
                        btn.handle_event(event)
                else:
                    for btn in self.main_buttons:
                        btn.handle_event(event)
                        
    def run(self):
        """Main application loop"""
        while self.running:
            self.handle_events()
            
            # Draw appropriate screen
            if self.confirmation_dialog:
                self.draw_confirmation_dialog()
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
