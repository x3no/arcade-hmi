#!/usr/bin/env python3
"""
Arcade Control Panel - Main Application
import concurrent.futures
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

# Configure SDL to use X11 on Linux
if platform.system() == 'Linux':
    os.environ['SDL_VIDEODRIVER'] = 'x11'
if IS_PI:
    os.environ['SDL_NOMOUSE'] = '1'
    os.environ.setdefault('SDL_RENDER_VSYNC', '1')

# Color palette
C_BG     = (0, 0, 0)        # Pure black
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
                fg_color = C_BG
            else:
                pygame.draw.rect(surface, C_GRAY, self.rect, max(1, _s(3)))
                fg_color = C_GRAY
                
            if self.icon and font_icon:
                icon_surf = _rt(font_icon, self.icon, fg_color)
                if self.text:
                    text_surf = _rt(font, self.text, fg_color)
                    gap = _s(8)
                    total_w = icon_surf.get_width() + gap + text_surf.get_width()
                    x = self.rect.centerx - total_w // 2
                    cy = self.rect.centery
                    surface.blit(icon_surf, icon_surf.get_rect(left=x, centery=cy))
                    surface.blit(text_surf, text_surf.get_rect(left=x + icon_surf.get_width() + gap, centery=cy))
                else:
                    surface.blit(icon_surf, icon_surf.get_rect(center=self.rect.center))
            else:
                text_surf = _rt(font, self.text, fg_color)
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

    FRICTION       = 0.80   # Stronger friction to stop quickly without slippery floatiness
    SPRING         = 0.25   # Snappier overscroll snap-back
    # Drag threshold scales with render resolution so the physical finger
    # movement needed to trigger a scroll is constant regardless of RS.
    DRAG_THRESHOLD = max(2, int(15 * RS))

    def __init__(self, rect, buttons, btn_w, btn_gap):
        self.rect    = pygame.Rect(rect)
        self.buttons = buttons
        self.btn_gap = btn_gap

        # 4 or fewer buttons: expand to fill full width (scroll disabled)
        if 0 < len(buttons) <= 4:
            btn_w = (self.rect.width - btn_gap * (len(buttons) + 1)) // len(buttons)
        elif len(buttons) == 0:
            btn_w = 0
            
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

    # Maximum pixels the content can be pulled beyond its limits (hard wall)
    MAX_OVERSCROLL = _s(120)

    def update(self):
        if self.dragging:
            return

        self.scroll_x += self.velocity
        self.velocity  *= self.FRICTION

        if self.scroll_x < self.min_scroll:
            # Kill velocity if it keeps pushing further out (wrong direction)
            if self.velocity < 0:
                self.velocity = 0.0
            # Hard wall: never go more than MAX_OVERSCROLL past the boundary
            self.scroll_x = max(self.scroll_x, self.min_scroll - self.MAX_OVERSCROLL)
            # Spring pulls back toward the limit
            self.scroll_x += (self.min_scroll - self.scroll_x) * self.SPRING
        elif self.scroll_x > self.max_scroll:
            # Kill velocity if it keeps pushing further out (wrong direction)
            if self.velocity > 0:
                self.velocity = 0.0
            # Hard wall
            self.scroll_x = min(self.scroll_x, self.max_scroll + self.MAX_OVERSCROLL)
            # Spring pulls back toward the limit
            self.scroll_x += (self.max_scroll - self.scroll_x) * self.SPRING

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
            # Rebalance velocity for 60fps mapping avoiding artificial huge throws
            self.velocity    = -(event.pos[0] - self.drag_last_x) / dt * (1000 / 60)
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
            # Instantly stop scrolling instead of floating away loosely
            self.velocity *= 0.05
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


class MediaController:
    """Media controller widget."""
    def __init__(self, rect, app):
        self.rect = pygame.Rect(rect)
        self._app = app
        
        btn_w = _s(200)
        gap = _s(10)
        # Prev a la izquierda
        self.btn_prev = pygame.Rect(self.rect.left, self.rect.top, btn_w, self.rect.height)
        # Next a la derecha
        self.btn_next = pygame.Rect(self.rect.right - btn_w, self.rect.top, btn_w, self.rect.height)
        # Play/Pause a la izquierda de Next
        self.btn_play = pygame.Rect(self.btn_next.left - btn_w - gap, self.rect.top, btn_w, self.rect.height)
        
        # Central area for the track bar and info
        self.content_rect = pygame.Rect(self.btn_prev.right + gap, self.rect.top, self.btn_play.left - self.btn_prev.right - gap*2, self.rect.height)
        self.bar_y = self.content_rect.centery + _s(25)
        self.bar_rect = pygame.Rect(self.content_rect.left + _s(20), self.bar_y, self.content_rect.width - _s(40), _s(12))
        self.dragging = False
        self.btn_prev_down = False
        self.btn_play_down = False
        self.btn_next_down = False

    def handle_event(self, event):
        info = self._app.media_info
        if not info or not info.get('playing'):
            return False

        if event.type == MOUSEBUTTONDOWN:
            if self.bar_rect.collidepoint(event.pos) or (
                self.content_rect.left < event.pos[0] < self.content_rect.right and 
                self.bar_y - _s(30) < event.pos[1] < self.bar_y + _s(30)
            ):
                self.dragging = True
                self._update_pos(event.pos[0], send=False)
                return True
            elif self.btn_prev.collidepoint(event.pos):
                self.btn_prev_down = True
                self._app._main_cache_dirty = True
                return True
            elif self.btn_play.collidepoint(event.pos):
                self.btn_play_down = True
                self._app._main_cache_dirty = True
                return True
            elif self.btn_next.collidepoint(event.pos):
                self.btn_next_down = True
                self._app._main_cache_dirty = True
                return True
        elif event.type == MOUSEMOTION and self.dragging:
            self._update_pos(event.pos[0], send=False)
            return True
        elif event.type == MOUSEBUTTONUP:
            dirty = False
            if self.dragging:
                self.dragging = False
                self._update_pos(event.pos[0], send=True)
                return True
                
            if self.btn_prev_down:
                self.btn_prev_down = False
                dirty = True
                if self.btn_prev.collidepoint(event.pos):
                    self._send_action("prev")
            if self.btn_play_down:
                self.btn_play_down = False
                dirty = True
                if self.btn_play.collidepoint(event.pos):
                    self._send_action("toggle")
            if self.btn_next_down:
                self.btn_next_down = False
                dirty = True
                if self.btn_next.collidepoint(event.pos):
                    self._send_action("next")
            if dirty:
                self._app._main_cache_dirty = True
                return True
        return False

    def _update_pos(self, mx, send=True):
        info = self._app.media_info
        w = max(1, self.bar_rect.width)
        rel_x = max(0, min(w, mx - self.bar_rect.x))
        new_pos = int((rel_x / w) * info.get('duration', 0))
        # Optimistic update locally
        info['position'] = new_pos
        import time
        self._app.media_last_update = time.time()
        self._app.media_ignore_sync_until = time.time() + 4.0
        if send:
            self._send_action("seek", new_pos)
        self._app._main_cache_dirty = True

    def _send_action(self, action, value=None):
        if not self._app.lan_pc_ip: return
        
        info = self._app.media_info
        if info:
            import time
            self._app.media_ignore_sync_until = time.time() + 4.0
            if action == 'toggle':
                info['paused'] = not info.get('paused', False)
                self._app._main_cache_dirty = True
            elif action in ('next', 'prev'):
                info['position'] = 0
                self._app._main_cache_dirty = True
                
        import threading, json, urllib.request
        def _req():
            try:
                data = json.dumps({'action': action, 'value': value}).encode('utf-8')
                req = urllib.request.Request(f"http://{self._app.lan_pc_ip}:5000/media", data=data, headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    pass
            except Exception: pass
        threading.Thread(target=_req, daemon=True).start()
        
    def draw(self, surface, font, font_icon):
        info = self._app.media_info
        if not info or not info.get('playing'):
            return

        # Center info background
        pygame.draw.rect(surface, (40, 40, 40), self.content_rect, border_radius=_s(8))
        
        # Previous track button (left notch)
        c = _s(30)
        lb = self.btn_prev
        color_prev = C_ORANGE if getattr(self, 'btn_prev_down', False) else C_BTN
        pts_lb = [(lb.left, lb.top), (lb.right, lb.top), (lb.right, lb.bottom), (lb.left + c, lb.bottom), (lb.left, lb.bottom - c)]
        pygame.draw.polygon(surface, color_prev, pts_lb)

        # Next track button (right notch)
        rb = self.btn_next
        color_next = C_ORANGE if getattr(self, 'btn_next_down', False) else C_BTN
        pts_rb = [(rb.left, rb.top), (rb.right, rb.top), (rb.right, rb.bottom - c), (rb.right - c, rb.bottom), (rb.left, rb.bottom)]
        pygame.draw.polygon(surface, color_next, pts_rb)

        # Play/Pause button (right notch)
        pb = self.btn_play
        color_play = C_ORANGE if getattr(self, 'btn_play_down', False) else C_BTN
        pts_pb = [(pb.left, pb.top), (pb.right, pb.top), (pb.right, pb.bottom - c), (pb.right - c, pb.bottom), (pb.left, pb.bottom)]
        pygame.draw.polygon(surface, color_play, pts_pb)
            
        import time
        dur = info.get('duration', 0)
        # extrapolate position since last poll
        pos = info.get('position', 0)
        if info.get('playing') and not info.get('paused') and dur > 0:
            pos += int(time.time() - self._app.media_last_update)
            pos = min(dur, pos)

        # Title
        song = info.get('song') or "Unknown"
        art  = info.get('artist') or ""
        text = f"{art} - {song}" if art else song
        if len(text) > 40: text = text[:37] + "..."
        txt = _rt(font, text, C_WHITE)
        txt_rect = txt.get_rect(centerx=self.content_rect.centerx, bottom=self.bar_y - _s(25))
        surface.blit(txt, txt_rect)
        
        # Track Bar
        pygame.draw.rect(surface, (70, 70, 70), self.bar_rect, border_radius=_s(4))
        w = max(0, int((pos / max(1, dur)) * self.bar_rect.width))
        fill_r = pygame.Rect(self.bar_rect.x, self.bar_rect.y, w, self.bar_rect.height)
        pygame.draw.rect(surface, (0, 210, 100), fill_r, border_radius=_s(4))
        pygame.draw.circle(surface, (255, 255, 255), (fill_r.right, fill_r.centery), _s(8))
        
        # Times
        s_m, s_s = divmod(pos, 60)
        d_m, d_s = divmod(dur, 60)
        t_pos = _rt(font, f"{s_m}:{s_s:02d}", C_GRAY)
        t_dur = _rt(font, f"{d_m}:{d_s:02d}", C_GRAY)
        surface.blit(t_pos, t_pos.get_rect(midleft=(self.content_rect.left + _s(20), txt_rect.centery)))
        surface.blit(t_dur, t_dur.get_rect(midright=(self.content_rect.right - _s(20), txt_rect.centery)))

        # Buttons
        font_icon_large = self._app.font_icon_action
        icon_prev = font_icon_large.render('\ue045', True, C_WHITE) # skip_previous
        icon_play = font_icon_large.render('\ue034' if not info.get('paused') else '\ue037', True, C_WHITE)
        icon_next = font_icon_large.render('\ue044', True, C_WHITE) # skip_next
        
        surface.blit(icon_prev, icon_prev.get_rect(center=self.btn_prev.center))
        surface.blit(icon_play, icon_play.get_rect(center=self.btn_play.center))
        surface.blit(icon_next, icon_next.get_rect(center=self.btn_next.center))

class VolumeSlider:
    """Touch-friendly horizontal volume slider that sends to LAN API.

    Sending strategy: a single background thread reads `_pending_vol` and
    sends it.  The main thread always overwrites `_pending_vol` with the
    latest dragged value — intermediate positions that arrive while the
    HTTP request is in flight are automatically discarded (no queue).
    """

    _TRACK_H = max(1, int(20 * RS))
    _THUMB_R = max(4, int(26 * RS))

    def __init__(self, rect, app):
        self.rect     = pygame.Rect(rect)
        self._app     = app
        self._dragging   = False
        self._drag_val   = None      # 0–100 while finger is touching
        self._pending_vol = None     # latest value waiting to be sent
        self._sending    = False
        self._user_vol   = None      # authoritative value; cleared when server confirms
        self._start_sender()

    def _start_sender(self):
        import threading
        def _loop():
            import time as _t, json, urllib.request
            while True:
                val = self._pending_vol
                if val is not None and self._app.lan_pc_ip:
                    self._pending_vol = None
                    self._sending = True
                    try:
                        body = json.dumps({'volume': val}).encode()
                        req = urllib.request.Request(
                            f"http://{self._app.lan_pc_ip}:5000/vol",
                            data=body,
                            headers={'Content-Type': 'application/json'},
                            method='POST',
                        )
                        with urllib.request.urlopen(req, timeout=1.5) as resp:
                            res = json.loads(resp.read())
                            confirmed_vol = res.get('volume', self._app.lan_volume)
                            self._app.lan_mute = res.get('mute', self._app.lan_mute)
                            self._app._main_cache_dirty = True
                            if self._user_vol is not None:
                                # Solo aceptar la respuesta si confirma el valor del usuario.
                                # Respuestas de POSTs anteriores (durante el arrastre) se ignoran.
                                if confirmed_vol == self._user_vol:
                                    self._app.lan_volume = confirmed_vol
                                    self._user_vol = None
                            else:
                                self._app.lan_volume = confirmed_vol
                    except Exception:
                        pass
                    self._sending = False
                _t.sleep(0.02)
        threading.Thread(target=_loop, daemon=True).start()

    @property
    def _display_vol(self):
        if self._drag_val is not None:
            return self._drag_val
        if self._user_vol is not None:
            return self._user_vol
        return self._app.lan_volume

    def _vol_from_x(self, x):
        pad   = max(1, int(30 * RS))
        left  = self.rect.left  + pad
        right = self.rect.right - pad
        return max(0, min(100, int((x - left) * 100 / max(1, right - left))))

    def handle_event(self, event):
        if not self._app.lan_connected:
            return False
        if event.type == MOUSEBUTTONDOWN and self.rect.collidepoint(event.pos):
            self._dragging = True
            new_val = self._vol_from_x(event.pos[0])
            self._drag_val = new_val
            self._pending_vol = new_val
            self._user_vol = new_val
            self._app._main_cache_dirty = True
            return True
        if event.type == MOUSEMOTION and self._dragging:
            new_val = self._vol_from_x(event.pos[0])
            if new_val != self._drag_val:
                self._drag_val = new_val
                self._pending_vol = new_val
                self._user_vol = new_val
                self._app._main_cache_dirty = True
            return True
        if event.type == MOUSEBUTTONUP and self._dragging:
            if self._drag_val is not None:
                self._user_vol = self._drag_val
                self._app.lan_volume = self._drag_val
            self._dragging = False
            self._drag_val = None
            return True
        return False

    def draw(self, surface, font):
        if not self._app.lan_connected:
            return

        muted = self._app.lan_mute
        vol   = self._display_vol

        pad   = max(1, int(30 * RS))
        cx    = self.rect.centerx
        cy    = self.rect.centery  + max(1, int(8 * RS))   # nudge down to leave label room
        left  = self.rect.left  + pad
        right = self.rect.right - pad
        tw    = right - left
        th    = self._TRACK_H
        tr    = self._THUMB_R

        # Track background
        pygame.draw.rect(surface, (50, 50, 50),
                         (left, cy - th // 2, tw, th),
                         border_radius=th // 2)
        # Filled portion — color interpolates green→orange→red by volume level
        def _vol_color(v):
            if v <= 20:
                # green (0,200,80) constant for low volumes
                return (0, 200, 80)
            elif v <= 50:
                # green → orange across 20..50
                t = (v - 20) / 30
                return (int(0   + 255 * t), int(200 - 60 * t), int(80 - 80 * t))
            else:
                # orange → red across 50..100
                t = (v - 50) / 50
                return (255, int(140 - 140 * t), 0)

        if not muted and vol > 0:
            fw = max(1, int(tw * vol / 100))
            pygame.draw.rect(surface, _vol_color(vol),
                             (left, cy - th // 2, fw, th),
                             border_radius=th // 2)
        # Thumb
        tx = left + int(tw * vol / 100)
        c_thumb = (100, 100, 100) if muted else _vol_color(vol)
        pygame.draw.circle(surface, c_thumb, (tx, cy), tr)
        pygame.draw.circle(surface, C_WHITE,  (tx, cy), tr, max(1, int(2 * RS)))

        # Label
        if muted:
            label, lc = "SILENCIADO", (120, 120, 120)
        else:
            label, lc = f"{vol}%", C_WHITE
        txt = _rt(font, label, lc)
        surface.blit(txt, txt.get_rect(centerx=cx,
                                       bottom=cy - th // 2 - max(1, int(4 * RS))))


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
        # On Pi: start_x11.sh tries xrandr --scale-from 640x360 (VC4 HW upscale).
        # If it succeeds ARCADE_HW_SCALE=1 is exported and we render directly at
        # 640x360 — no CPU transform.scale in the loop.
        # Fallback: display reports 1920x1080 → manual SW scale each frame.
        _info = pygame.display.Info()
        if _info.current_w > 0 and _info.current_h > 0:
            disp_w, disp_h = _info.current_w, _info.current_h
        else:
            disp_w = self.config['screen_width']
            disp_h = self.config['screen_height']

        phys_w = self.config['screen_width']
        phys_h = self.config['screen_height']
        self.width  = int(phys_w * RS)   # 640
        self.height = int(phys_h * RS)   # 360

        _hw_scale = os.environ.get('ARCADE_HW_SCALE') == '1'

        if IS_PI or _force_scale:
            if _hw_scale:
                # xrandr --scale-from maps top-left 640x360 of the virtual FB to
                # 1920x1080 on hardware. Open a borderless window at (0,0) exactly
                # filling that region — SDL2 renders direct, no CPU scale.
                os.environ['SDL_VIDEO_WINDOW_POS'] = '0,0'
                self.screen = pygame.display.set_mode(
                    (self.width, self.height),
                    pygame.NOFRAME | pygame.DOUBLEBUF,
                    vsync=1
                )
                self._display_surf   = self.screen
                self._scale_to_display = False
                print(f"Render: {self.width}x{self.height} via VC4 HW scaler")
            else:
                # SW scale fallback
                flags = pygame.FULLSCREEN | pygame.DOUBLEBUF
                self._display_surf = pygame.display.set_mode((disp_w, disp_h), flags, vsync=1)
                self.screen = pygame.Surface((self.width, self.height)).convert()
                self._scale_to_display = True
                print(f"Render: {self.width}x{self.height}  display: {disp_w}x{disp_h}  (SW scale)")
        else:
            flags = 0
            self.screen = pygame.display.set_mode((self.width, self.height), flags, vsync=1)
            self._display_surf   = self.screen
            self._scale_to_display = False
        self._phys_w = disp_w
        self._phys_h = disp_h
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
            self.font_slot = pygame.font.Font(font_path, _fs(48)) # Increased font size for slots
        except:
            self.font_tab  = pygame.font.Font(None, _fs(42))
            self.font_slot = pygame.font.Font(None, _fs(48))

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
        # Lock screen: dirty on startup and whenever pin_input changes
        self._lock_dirty       = True

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
        self.lan_pc_ip = None       # Stores PC IP if found via UDP broadcast
        self.lan_connected = False
        self.lan_volume = 0
        self.lan_mute = False
        self.lan_is_game_running = False
        self.lan_is_menu_running = False
        self.lan_game_title = None
        self.lan_game_system = None
        self.lan_is_game_paused = False
        self.lan_game_image_bytes = None  # Buffer crudo para convertir en thread principal
        self.lan_game_image_surf = None   # Pygame surface de la captura en vivo

        # Media status
        self.media_info = {'playing': False, 'paused': False, 'artist': None, 'song': None, 'position': 0, 'duration': 0}
        self.media_last_update = 0
        
        self._start_bt_status_poller()
        self._start_wifi_poller()
        self._start_weather_poller()
        self._start_lan_poller()
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
        TAB_H    = _s(160)
        TAB_W    = (self.width - GAP * 3) // 4   # 4 tabs with 3 gaps between
        SLOT_Y   = TAB_Y + TAB_H + _s(10)
        SLOT_H   = _s(180)
        SLOT_W   = (self.width - GAP * 9) // 10  # 10 items (GENERAL + 1-9) with 9 gaps
        CONT_Y   = TAB_Y + TAB_H        # non-Partida content starts here
        CONT_Y_P = SLOT_Y + SLOT_H      # Partida content starts here

        # Main tab bar (4 tabs)
        # Material Icons codepoints (static font, U+E000 range)
        tab_defs = [
            ('sistema',  'SISTEMA',  '\ue30a'),  # computer
            ('sonido',   'SONIDO',   '\ue050'),  # volume_up
            ('partida',  'JUEGO',    '\ue30f'),  # gamepad
            ('raton',    'RATÓN',    '\ue323'),  # mouse
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

        # Slot sub-tabs: GENERAL first, MONEDERO second, then 1-8
        # \ue8b8 is settings, \ue227 is attach_money, \ue30f is gamepad setting, \ue88e is info, \ue5d2 is menu
        slot_labels = [('', '\ue8b8'), ('$', None)] + [(str(i), None) for i in range(1, 9)]
        self.slot_buttons = [
            TabButton(
                (i * (SLOT_W + GAP), SLOT_Y, SLOT_W, SLOT_H),
                text=label[0],
                active=(i == self.current_slot),
                action=lambda s=i: self.switch_slot(s),
                icon=label[1],
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

        # ── Media Controller y Volume slider (sonido tab only) ─────────────────
        MEDIA_H = _s(220)
        SLIDER_H = _s(120)  # slightly reduced to fit media
        h_sonido  = h_norm - SLIDER_H - MEDIA_H - _s(20)
        
        MEDIA_Y   = CONT_Y + h_sonido + _s(5)
        SLIDER_Y  = MEDIA_Y + MEDIA_H + _s(10)
        
        self.media_controller = MediaController(
            (_s(16), MEDIA_Y, self.width - _s(32), MEDIA_H), self
        )
        self.volume_slider = VolumeSlider(
            (_s(16), SLIDER_Y, self.width - _s(32), SLIDER_H), self
        )

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
        self.partida_monedero_btns = [
            SimpleButton((0,0,0,0), "MONEDA J1", action=self.coin_p1, icon='\ue227', disabled=True),
            SimpleButton((0,0,0,0), "MONEDA J2", action=self.coin_p2, icon='\ue227', disabled=True),
        ]
        self.partida_general_scroll = make_scroll(CONT_Y_P, h_part, self.partida_general_btns)
        self.partida_monedero_scroll = make_scroll(CONT_Y_P, h_part, self.partida_monedero_btns)
        self.partida_slot_scroll    = make_scroll(CONT_Y_P, h_part, self.partida_slot_btns)

        self.power_btn = SimpleButton(
            (0,0,0,0), 'ENCENDER PC', action=self.toggle_power_confirm, icon='\ue8ac'
        )
        self.reset_btn = SimpleButton(
            (0,0,0,0), 'REINICIAR PC', action=self.reset_pc_confirm, icon='\ue5d5', disabled=True
        )

        RATON_Y = CONT_Y + _s(10)
        RATON_H = h_norm - _s(20)
        btn_height = _s(120)
        # Touchpad height reduced to fit the buttons below with a small margin
        self._raton_rect = pygame.Rect(_s(20), RATON_Y, self.width - _s(40), RATON_H - btn_height - _s(10))
        
        btn_y = self._raton_rect.bottom + _s(10)
        btn_w = (self.width - _s(50)) // 2
        
        self._raton_btn_left_rect = pygame.Rect(_s(20), btn_y, btn_w, btn_height)
        self._raton_btn_right_rect = pygame.Rect(_s(20) + btn_w + _s(10), btn_y, btn_w, btn_height)

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
            'sonido':   make_scroll(CONT_Y, h_sonido, [
                SimpleButton((0,0,0,0), "VOL +", action=self.volume_up,   icon='\ue050', hold_action=self.volume_up,   disabled=True),
                SimpleButton((0,0,0,0), "VOL -", action=self.volume_down, icon='\ue04d', hold_action=self.volume_down, disabled=True),
                SimpleButton((0,0,0,0), "SILENCIAR", action=self.mute,    icon='\ue04f',                               disabled=True),
            ]),
            'raton': make_scroll(CONT_Y, h_norm, []),
        }

        # Keep explicit references for sonido buttons so we can update them
        # independently from the general bt_action_btns disable logic.
        self.vol_up_btn   = self.tab_scroll_menus['sonido'].buttons[0]
        self.vol_down_btn = self.tab_scroll_menus['sonido'].buttons[1]
        self.mute_btn     = self.tab_scroll_menus['sonido'].buttons[2]
        self.sonido_btns  = [self.vol_up_btn, self.vol_down_btn, self.mute_btn]

        # Collect all buttons that require an active BT connection
        self.bt_action_btns = (
            self.partida_general_btns
            + self.partida_slot_btns
            + self.partida_monedero_btns
        )

        # Wire cache-invalidation callback into every scroll menu so button
        # press/release triggers a static-cache rebuild before the next draw.
        def _mark_cache_dirty():
            self._main_cache_dirty = True

        all_menus = list(self.tab_scroll_menus.values()) + [
            self.partida_general_scroll, self.partida_monedero_scroll, self.partida_slot_scroll
        ]
        for menu in all_menus:
            menu._cache_dirty_ref = _mark_cache_dirty

    def _active_scroll_menu(self):
        """Return the scroll menu for the currently active tab/sub-tab."""
        if self.current_tab == 'partida':
            if self.current_slot == 0:
                return self.partida_general_scroll
            elif self.current_slot == 1:
                return self.partida_monedero_scroll
            else:
                return self.partida_slot_scroll
        return self.tab_scroll_menus[self.current_tab]

    def switch_tab(self, tab_id):
        self.current_tab = tab_id
        tab_ids = ['sistema', 'sonido', 'partida', 'raton']
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
        self._lock_dirty = True
                
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
        if self._lan_send_action('up'): return
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(ArcadeKeyMapper.get_key('volume_up'))
        except Exception as e:
            print(f"Error sending volume up: {e}")
            
    def volume_down(self):
        """Decrease volume"""
        if self._lan_send_action('down'): return
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(ArcadeKeyMapper.get_key('volume_down'))
        except Exception as e:
            print(f"Error sending volume down: {e}")
            
    def mute(self):
        """Toggle mute"""
        if self._lan_send_action('mute'): return
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

    def _lan_send_retroarch(self, cmd):
        """Send command to RetroArch via PC Server."""
        if not self.lan_pc_ip: return False
        import json, urllib.request
        try:
            data = json.dumps({'command': cmd}).encode('utf-8')
            req = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/retroarch", data=data, headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return json.loads(resp.read().decode()).get('status') == 'ok'
        except Exception:
            return False

    def save_state(self):
        """RetroArch: navigate to slot (F6/F7) then F2 = save state"""
        if self._lan_send_retroarch('SAVE_STATE'): return
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
        if self._lan_send_retroarch('LOAD_STATE'): return
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
        import threading
        # Feedback visual instantáneo
        self.lan_is_game_paused = not getattr(self, "lan_is_game_paused", False)
        self._main_cache_dirty = True
        
        def _send():
            if self._lan_send_retroarch('PAUSE_TOGGLE'): return
            try:
                with USBHID(self.config['hid_device']) as hid:
                    hid.send_key(KeyCode.KEY_P)
            except Exception as e:
                print(f"Error sending pause: {e}")
        threading.Thread(target=_send, daemon=True).start()

    def game_info(self):
        """RetroArch: F1 = menu (no dedicated info key)"""
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F1)
        except Exception as e:
            print(f"Error sending info: {e}")

    def fast_forward(self):
        """RetroArch: Space = fast-forward toggle"""
        if self._lan_send_retroarch('FAST_FORWARD'): return
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_SPACE)
        except Exception as e:
            print(f"Error sending fast forward: {e}")

    def screenshot(self):
        """RetroArch: F8 = screenshot"""
        if self._lan_send_retroarch('SCREENSHOT'): return
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F8)
        except Exception as e:
            print(f"Error sending screenshot: {e}")

    def mame_menu(self):
        """RetroArch: F1 = menu toggle"""
        if self._lan_send_retroarch('MENU_TOGGLE'): return
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_F1)
        except Exception as e:
            print(f"Error sending menu: {e}")

    def restart_game(self):
        """RetroArch: H = reset content"""
        if self._lan_send_retroarch('RESET'): return
        try:
            with USBHID(self.config['hid_device']) as hid:
                hid.send_key(KeyCode.KEY_H)
        except Exception as e:
            print(f"Error sending restart: {e}")

    def exit_game(self):
        """RetroArch: ESC = quit"""
        if self._lan_send_retroarch('QUIT'): return
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
            self._present()
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
        self._present()
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

    def _start_lan_poller(self):
        """Background thread: UDP Broadcast to find PC and poll volume status."""
        import threading
        def _poll():
            import time as _t, socket, json, urllib.request
            _consecutive_fails = 0
            _MAX_FAILS = 3  # fallos consecutivos antes de marcar desconexión
            while True:
                if not self.lan_pc_ip:
                    # UDP Broadcast
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.settimeout(2.0)
                    try:
                        print("[LAN] 🔍 Iniciando búsqueda UDP de PC Arcade...")
                        # Prueba 1: Global
                        try:
                            s.sendto(b"ARCADE_DISCOVER", ("255.255.255.255", 50019))
                            print("  -> Broadcast global (255.255.255.255) enviado")
                        except OSError as e:
                            print(f"  -> Error broadcast global: {e}")
                            
                        # Prueba 2: Fallback subred actual (si es un portátil con Wi-Fi)
                        try:
                            import subprocess
                            out = subprocess.check_output("ip -4 addr show | grep inet", shell=True).decode()
                            for line in out.splitlines():
                                if 'brd' in line:
                                    bcast = line.split('brd')[1].split()[0].strip()
                                    if '172.' not in bcast and '127.' not in bcast:
                                        s.sendto(b"ARCADE_DISCOVER", (bcast, 50019))
                                        print(f"  -> Broadcast específico IP route ({bcast}) enviado")
                        except Exception:
                            # Ignorado, no está el comando IP disponible
                            s.sendto(b"ARCADE_DISCOVER", ("<broadcast>", 50019))
                            print("  -> Broadcast genérico (<broadcast>) enviado")
                            
                        # Prueba 3: Fuerza bruta a subredes de casa clásicas (192.168.1.255 / 192.168.0.255)
                        try: s.sendto(b"ARCADE_DISCOVER", ("192.168.1.255", 50019))
                        except Exception: pass
                        try: s.sendto(b"ARCADE_DISCOVER", ("192.168.0.255", 50019))
                        except Exception: pass

                        data, addr = s.recvfrom(1024)
                        if data == b"ARCADE_PC_HERE":
                            print(f"[LAN] ✅ ¡PC ENCONTRADO! IP: {addr[0]}")
                            self.lan_pc_ip = addr[0]
                            self.lan_connected = True
                            self._main_cache_dirty = True
                            try:
                                req_s = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/vol", method="GET")
                                with urllib.request.urlopen(req_s, timeout=2.0) as resp_s:
                                    d = json.loads(resp_s.read().decode())
                                    self.lan_volume = d.get('volume', 0)
                                    self.lan_mute   = d.get('mute', False)
                                    self._main_cache_dirty = True
                            except Exception:
                                pass
                    except Exception as e:
                        if "timed out" in str(e):
                            print("[LAN] ❌ UDP agotado. Iniciando escaneo TCP de Fuerza Bruta en puerto 5000...")
                            # --- BRUTE FORCE TCP ---
                            found_ip = None
                            try:
                                import concurrent.futures
                                import socket as _sock
                                
                                def _check_ip(ip):
                                    # Hacemos un ping TCP rápido al puerto 5000 (Flask)
                                    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as tcp_s:
                                        tcp_s.settimeout(0.3)
                                        if tcp_s.connect_ex((ip, 5000)) == 0:
                                            return ip
                                    return None

                                # Sacar la IP local para saber qué subred barrer (ej: 192.168.1)
                                base_ip = "192.168.1"
                                try:
                                    import subprocess
                                    out = subprocess.check_output("ip -4 addr show | grep inet", shell=True).decode()
                                    for line in out.splitlines():
                                        if 'brd' in line:
                                            ip_full = line.split()[1].split('/')[0]
                                            if not ip_full.startswith('127') and not ip_full.startswith('172'):
                                                base_ip = ip_full.rsplit('.', 1)[0]
                                                break
                                except Exception: pass
                                
                                print(f"[LAN] -> Barriendo IPs masivamente: {base_ip}.2 hasta {base_ip}.254")
                                ips_to_check = [f"{base_ip}.{i}" for i in range(2, 255)]
                                
                                # Escanear 50 IPs a la vez en paralelo
                                with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                                    for result in executor.map(_check_ip, ips_to_check):
                                        if result:
                                            found_ip = result
                                            # (concurrent.futures terminará los demás hilos rápido en background)
                                            break
                            except Exception as ex: 
                                print(f"[LAN] Fallo en fuerza bruta: {ex}")
                                
                            if found_ip:
                                print(f"[LAN] ✅ ¡PC ENCONTRADO POR FUERZA BRUTA TCP! IP: {found_ip}")
                                self.lan_pc_ip = found_ip
                                self.lan_connected = True
                                self._main_cache_dirty = True
                                try:
                                    req_s = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/vol", method="GET")
                                    with urllib.request.urlopen(req_s, timeout=2.0) as resp_s:
                                        d = json.loads(resp_s.read().decode())
                                        self.lan_volume = d.get('volume', 0)
                                        self.lan_mute   = d.get('mute', False)
                                        self._main_cache_dirty = True
                                except Exception:
                                    pass
                            else:
                                print("[LAN] 🛑 Escaneo completo. Nadie tiene el puerto 5000 abierto.")
                        else:
                            print(f"[LAN] ❌ Excepción de red: {e}")
                            
                        # Si tras UDP y TCP no hay suerte, reseteamos UI
                        if not self.lan_pc_ip:
                            self.lan_connected = False
                            self._main_cache_dirty = True
                    finally:
                        s.close()
                    _t.sleep(5)
                else:
                    # HTTP polling for volume and game
                    try:
                        req_vol = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/vol", method="GET")
                        with urllib.request.urlopen(req_vol, timeout=2.0) as resp:
                            data = json.loads(resp.read().decode())
                            slider = self.volume_slider
                            srv_vol  = data.get('volume', 0)
                            srv_mute = data.get('mute', False)
                            if slider._user_vol is not None:
                                # El usuario tiene un valor autoritativo no confirmado.
                                # Si el servidor devuelve algo distinto, re-enviamos.
                                if srv_vol != slider._user_vol:
                                    slider._pending_vol = slider._user_vol
                                if self.lan_mute != srv_mute:
                                    self.lan_mute = srv_mute
                                    self._main_cache_dirty = True
                                self.lan_connected = True
                            else:
                                if self.lan_volume != srv_vol or self.lan_mute != srv_mute:
                                    self.lan_volume = srv_vol
                                    self.lan_mute = srv_mute
                                    self.lan_connected = True
                                    self._main_cache_dirty = True
                        req_game = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/game", method="GET")
                        with urllib.request.urlopen(req_game, timeout=2.0) as resp:
                            data = json.loads(resp.read().decode())
                            new_running = data.get('is_game_running', False)
                            new_paused = data.get('is_game_paused', False)
                            new_menu = data.get('is_menu_running', False)
                            new_title = data.get('game')
                            new_system = data.get('system')
                            
                            if new_title and len(new_title) > 30:
                                new_title = new_title[:27] + "..."
                                
                            if (self.lan_is_game_running != new_running or 
                                getattr(self, "lan_is_game_paused", False) != new_paused or
                                self.lan_is_menu_running != new_menu or 
                                self.lan_game_title != new_title or
                                getattr(self, "lan_game_system", None) != new_system):
                                self.lan_is_game_running = new_running
                                self.lan_is_game_paused = new_paused
                                self.lan_is_menu_running = new_menu
                                self.lan_game_title = new_title
                                self.lan_game_system = new_system
                                self.lan_connected = True
                                self._main_cache_dirty = True
                                
                        # Si hay un juego o menú, pedir la captura de pantalla en vivo
                        if (self.lan_is_game_running or self.lan_is_menu_running) and self.current_tab == 'partida':
                            try:
                                req_img = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/game/preview", method="GET")
                                with urllib.request.urlopen(req_img, timeout=2.0) as resp:
                                    if resp.status == 200:
                                        self.lan_game_image_bytes = resp.read()
                                        self._main_cache_dirty = True
                            except Exception:
                                pass

                        # Media
                        if self.current_tab == 'sonido':
                            try:
                                req_spot = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/media", method="GET")
                                with urllib.request.urlopen(req_spot, timeout=1.0) as resp:
                                    sdata = json.loads(resp.read().decode())
                                    if not sdata.get('error'):
                                        import time
                                        now = time.time()
                                        
                                        srv_pos = sdata.get('position', 0)
                                        local_pos = self.media_info.get('position', 0)
                                        is_playing = self.media_info.get('playing') and not self.media_info.get('paused')
                                        if is_playing:
                                            local_pos += int(now - getattr(self, 'media_last_update', now))
                                            
                                        ignore = getattr(self, 'media_ignore_sync_until', 0) > now
                                        
                                        if (ignore or abs(local_pos - srv_pos) < 4) and self.media_info.get('song') == sdata.get('song'):
                                            sdata['position'] = min(local_pos, sdata.get('duration', 0))

                                        # Only dirty cache if playstate or song changes
                                        if (self.media_info.get('playing') != sdata.get('playing') or 
                                            self.media_info.get('song') != sdata.get('song')):
                                            self._main_cache_dirty = True
                                        self.media_info = sdata
                                        self.media_last_update = now
                            except Exception:
                                pass

                        _consecutive_fails = 0  # petición exitosa
                        _t.sleep(2)
                    except Exception as _e:
                        _consecutive_fails += 1
                        print(f'[LAN] ⚠️  Fallo #{_consecutive_fails}/{_MAX_FAILS}: {_e}')
                        if _consecutive_fails >= _MAX_FAILS:
                            self.lan_pc_ip = None
                            self.lan_connected = False
                            self.lan_is_game_running = False
                            self.lan_is_menu_running = False
                            self.lan_game_title = None
                            self.lan_game_system = None
                            self.lan_is_game_paused = False
                            self._main_cache_dirty = True
                            _consecutive_fails = 0
                        _t.sleep(2)
        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def _lan_send_action(self, action):
        """Send volume change to PC via LAN."""
        if not self.lan_pc_ip: return False
        import json, urllib.request
        try:
            data = json.dumps({'action': action}).encode('utf-8')
            req = urllib.request.Request(f"http://{self.lan_pc_ip}:5000/vol", data=data, headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                res = json.loads(resp.read().decode())
                self.lan_volume = res.get('volume', self.lan_volume)
                self.lan_mute = res.get('mute', self.lan_mute)
                self._main_cache_dirty = True
            return True
        except Exception:
            return False

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

        self._present()
    
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
        """Draw lock screen — only redraws when pin_input changes."""
        if not self._lock_dirty:
            return
        self._lock_dirty = False
        self.draw_lock_screen_base()
        self._present()
        
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

        # Sonido buttons are usable via BT *or* LAN
        lan_or_bt = self.bt_connected or self.lan_connected
        for btn in self.sonido_btns:
            btn.disabled = not lan_or_bt

        # Mute button reflects current audio state, defaults to SILENCIAR if no LAN
        if self.lan_connected and self.lan_mute:
            self.mute_btn.text = "DESILENCIAR"
            self.mute_btn.icon = '\ue050'   # volume_up
        else:
            self.mute_btn.text = "SILENCIAR"
            self.mute_btn.icon = '\ue04f'   # volume_off

        # Control de botones de Partida y Monedero
        # Por defecto ya se activan/desactivan arriba según self.bt_connected.
        # Si la LAN está conectada y dice que NO hay juego, los desactivamos.
        if self.lan_connected and not self.lan_is_game_running:
            for btn in self.partida_general_btns + self.partida_slot_btns + self.partida_monedero_btns:
                btn.disabled = True

        # Botón de Pausar / Reanudar
        if getattr(self, "lan_is_game_paused", False):
            self.partida_general_btns[0].text = "REANUDAR"
            self.partida_general_btns[0].icon = '\ue037' # play_arrow
        else:
            self.partida_general_btns[0].text = "PAUSAR"
            self.partida_general_btns[0].icon = '\ue034' # pause

        for btn in self.tab_buttons:
            btn.draw(s, self.font_tab, self.font_icon)

        if self.current_tab == 'partida':
            for btn in self.slot_buttons:
                btn.draw(s, self.font_slot, self.font_icon)

        # Scroll menu is NOT drawn into the cache — it is drawn live every frame
        # so that scroll_x changes are visible immediately without cache invalidation.
        self._main_cache_dirty = False

    def draw_main_screen_base(self):
        import io
        # Procesar nueva captura en el hilo principal de Pygame
        if self.lan_game_image_bytes and self.current_tab == 'partida':
            try:
                img = pygame.image.load(io.BytesIO(self.lan_game_image_bytes)).convert()
                self.lan_game_image_surf = img
            except Exception:
                pass
            self.lan_game_image_bytes = None

        # Rebuild tab/slot layer only when something structural changed
        if self._main_cache_dirty:
            self._rebuild_main_cache()
        self.screen.blit(self._main_cache, (0, 0))

        # Scroll area: always fill background and draw live (scroll position changes every frame)
        menu = self._active_scroll_menu()
        pygame.draw.rect(self.screen, C_BG, menu.rect)
        menu.draw(self.screen, self.font_action, self.font_icon_action)

        # Volume slider — drawn live below the sonido scroll menu
        if self.current_tab == 'sonido':
            # Draw Media Controller
            self.media_controller.draw(self.screen, self.font_mono, self.font_icon)
            
            pygame.draw.rect(self.screen, C_BG, self.volume_slider.rect)
            self.volume_slider.draw(self.screen, self.font_mono)

        if self.current_tab == 'raton':
            # Draw touchpad background and icon
            cr = self._raton_rect
            c_len = _s(50)
            c_th = max(3, _s(6))
            
            # Highlight corners if touchpad is active
            c_color = C_ORANGE if getattr(self, '_tp_active', False) else (80, 80, 80)
            
            # Top-left corner
            pygame.draw.rect(self.screen, c_color, (cr.left, cr.top, c_len, c_th))
            pygame.draw.rect(self.screen, c_color, (cr.left, cr.top, c_th, c_len))
            # Top-right corner
            pygame.draw.rect(self.screen, c_color, (cr.right - c_len, cr.top, c_len, c_th))
            pygame.draw.rect(self.screen, c_color, (cr.right - c_th, cr.top, c_th, c_len))
            # Bottom-left corner
            pygame.draw.rect(self.screen, c_color, (cr.left, cr.bottom - c_th, c_len, c_th))
            pygame.draw.rect(self.screen, c_color, (cr.left, cr.bottom - c_len, c_th, c_len))
            # Bottom-right corner
            pygame.draw.rect(self.screen, c_color, (cr.right - c_len, cr.bottom - c_th, c_len, c_th))
            pygame.draw.rect(self.screen, c_color, (cr.right - c_th, cr.bottom - c_len, c_th, c_len))
            
            # Draw left and right buttons
            c = _s(40)
            
            lb = getattr(self, '_raton_btn_left_rect', None)
            if lb:
                # Left button: solid, notch in bottom-left
                color_left = C_ORANGE if getattr(self, '_raton_left_pressed', False) else C_DISABLED_BG
                pts_lb = [(lb.left, lb.top), (lb.right, lb.top), (lb.right, lb.bottom), (lb.left + c, lb.bottom), (lb.left, lb.bottom - c)]
                pygame.draw.polygon(self.screen, color_left, pts_lb)
                
            rb = getattr(self, '_raton_btn_right_rect', None)
            if rb:
                # Right button: solid, notch in bottom-right
                color_right = C_ORANGE if getattr(self, '_raton_right_pressed', False) else C_DISABLED_BG
                pts_rb = [(rb.left, rb.top), (rb.right, rb.top), (rb.right, rb.bottom - c), (rb.right - c, rb.bottom), (rb.left, rb.bottom)]
                pygame.draw.polygon(self.screen, color_right, pts_rb)

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
            self.screen.blit(wx_surf, wx_surf.get_rect(midleft=(clock_rect.right + _s(60), BAR_CY)))

        # Right-side indicators (rendered right→left: BT, HDD, PC, WiFi, LAN)
        DOT_R = _s(7)
        x = self.width - MARGIN
        for label, connected in [('BT',   self.bt_connected),
                                   ('LAN',  self.lan_connected),
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

        # Current Game - center (only if known via LAN)
        if self.lan_is_game_running and self.lan_game_title:
            if getattr(self, "lan_game_system", None):
                game_text = f"[{self.lan_game_system.upper()}] {self.lan_game_title}"
            else:
                game_text = f"{self.lan_game_title}"
                
            if getattr(self, "lan_is_game_paused", False):
                # Intermitente en amarillo o amarillo oscuro transparente (segundo a segundo)
                if int(time.time()) % 2 == 0:
                    game_surf = _rt(self.font_mono, f"PAUSADO: {game_text}", (255, 255, 0))
                else:
                    game_surf = _rt(self.font_mono, f"PAUSADO: {game_text}", (120, 120, 0))
                # Forzar refresco el próximo fotograma si está en pausa para asegurar el parpadeo
                self._main_cache_dirty = True
            else:
                game_surf = _rt(self.font_mono, f"JUGANDO: {game_text}", (255, 165, 0))
                
            self.screen.blit(game_surf, game_surf.get_rect(center=(self.width // 2, BAR_BOT_CY)))
        elif self.lan_is_menu_running:
            game_surf = _rt(self.font_mono, "NAVEGANDO EN MENÚ", (0, 210, 100))
            self.screen.blit(game_surf, game_surf.get_rect(center=(self.width // 2, BAR_BOT_CY)))

        # Dibujar Live Preview si estamos en la tab "Partida" y tenemos imagen cacheada
        if self.current_tab == 'partida' and self.lan_game_image_surf:
            from pygame import transform
            # Calcular altura disponible en el centro encima del menú inferior si cabe o usar tamaño fijo
            iw, ih = self.lan_game_image_surf.get_size()
            max_w, max_h = _s(280), _s(180)
            scale = min(max_w / iw, max_h / ih)
            if scale < 1.0:
                img_scaled = transform.smoothscale(self.lan_game_image_surf, (int(iw * scale), int(ih * scale)))
            else:
                img_scaled = self.lan_game_image_surf

            # Situarlo en la zona superior central (aprovechando espacio vacío cerca del header)
            img_rect = img_scaled.get_rect(center=(self.width // 2, BAR_CY + _s(80)))
            self.screen.blit(img_scaled, img_rect)
            # Dibujar un borde Cyberpunk alrededor de la captura
            pygame.draw.rect(self.screen, (255, 165, 0), img_rect, 2)

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
        scrolling = (menu.dragging
                     or abs(menu.velocity) > 0.05
                     or menu.scroll_x < menu.min_scroll
                     or menu.scroll_x > menu.max_scroll)
        if not (self._main_cache_dirty or scrolling or now_s != self._prev_second):
            return
        self._prev_second = now_s
        self.draw_main_screen_base()
        self._present()

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
        self._present()

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

        self._present()
                    
    def handle_events(self):
        """Handle pygame events"""
        for event in pygame.event.get():
            # Track fingers for multi-touch (e.g. 2-finger scroll)
            if event.type == getattr(pygame, 'FINGERDOWN', -1):
                fx, fy = int(event.x * self.width), int(event.y * self.height)
                if hasattr(self, '_raton_rect') and self._raton_rect.collidepoint((fx, fy)):
                    if not hasattr(self, '_fingers'): self._fingers = {}
                    self._fingers[event.finger_id] = (fx, fy)
            elif event.type == getattr(pygame, 'FINGERUP', -1):
                if hasattr(self, '_fingers') and event.finger_id in self._fingers:
                    del self._fingers[event.finger_id]
            elif event.type == getattr(pygame, 'FINGERMOTION', -1):
                if hasattr(self, '_fingers') and event.finger_id in self._fingers:
                    self._fingers[event.finger_id] = (int(event.x * self.width), int(event.y * self.height))
                    # Trigger scroll if 2 fingers
                    if len(self._fingers) >= 2 and getattr(self, "bt_connected", False) and self.current_tab == 'raton':
                        # Acumulamos el desplazamiento para lograr un scroll suave
                        if not hasattr(self, '_wheel_acc'):
                            self._wheel_acc = 0.0
                        
                        # Multiplicador ajustado (ej. 30 en vez de 150)
                        self._wheel_acc += -event.dy * 30  
                        wheel_val = int(self._wheel_acc)
                        
                        if wheel_val != 0:
                            self._wheel_acc -= wheel_val
                            import bluetooth_hid
                            try:
                                with bluetooth_hid.USBHID() as hid:
                                    hid.send_mouse(0, 0, 0, wheel_val)
                            except: pass
            
            if event.type == QUIT:
                self.running = False
            elif event.type == KEYDOWN:
                if event.key == K_ESCAPE:
                    self.running = False
                elif event.key == K_F1:
                    self.do_update()
            elif event.type in (MOUSEBUTTONDOWN, MOUSEMOTION, MOUSEBUTTONUP):
                # Scale touch/mouse coordinates from physical display space to canvas space
                if self._scale_to_display:
                    sx = event.pos[0] * self.width  // self._phys_w
                    sy = event.pos[1] * self.height // self._phys_h
                    event = pygame.event.Event(event.type,
                                               {**event.__dict__, 'pos': (sx, sy)})
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
                                    
                    # Raton/Touchpad handling
                    if self.current_tab == 'raton' and hasattr(self, '_raton_rect'):
                        is_tp_event = False
                        is_btn_event = False
                        btn_clicked = 0
                        now = pygame.time.get_ticks()
                        if event.type == MOUSEBUTTONDOWN:
                            if self._raton_rect.collidepoint(event.pos):
                                self._tp_active = True
                                self._main_cache_dirty = True
                                self._tp_down_time = now
                                self._tp_moved = False
                                
                                # Comprobar doble tap: menos de 400ms desde el último click
                                last_tap = getattr(self, "_tp_last_tap_time", 0)
                                if now - last_tap < 400:
                                    self._tp_is_dragging = True
                                else:
                                    self._tp_is_dragging = False
                                    
                                is_tp_event = True
                            elif hasattr(self, "_raton_btn_left_rect") and self._raton_btn_left_rect.collidepoint(event.pos):
                                self._raton_left_pressed = True
                                self._main_cache_dirty = True
                                is_btn_event = True
                                btn_clicked = 1 # Left click
                            elif hasattr(self, "_raton_btn_right_rect") and self._raton_btn_right_rect.collidepoint(event.pos):
                                self._raton_right_pressed = True
                                self._main_cache_dirty = True
                                is_btn_event = True
                                btn_clicked = 2 # Right click
                                
                        elif event.type == MOUSEMOTION:
                            if getattr(self, "_tp_active", False):
                                is_tp_event = True
                        elif event.type == MOUSEBUTTONUP:
                            if getattr(self, "_tp_active", False):
                                is_tp_event = True
                                self._tp_active = False
                                self._main_cache_dirty = True
                            if getattr(self, "_raton_left_pressed", False):
                                self._raton_left_pressed = False
                                self._main_cache_dirty = True
                                is_btn_event = True
                                btn_clicked = 0
                            if getattr(self, "_raton_right_pressed", False):
                                self._raton_right_pressed = False
                                self._main_cache_dirty = True
                                is_btn_event = True
                                btn_clicked = 0

                        if (is_tp_event or is_btn_event) and getattr(self, "bt_connected", False):
                            try:
                                import bluetooth_hid
                                with bluetooth_hid.USBHID() as hid:
                                    if is_btn_event:
                                        if event.type == MOUSEBUTTONDOWN:
                                            hid.send_mouse(btn_clicked, 0, 0)
                                        elif event.type == MOUSEBUTTONUP:
                                            hid.send_mouse(0, 0, 0)
                                    elif event.type == MOUSEBUTTONDOWN:
                                        if getattr(self, "_tp_is_dragging", False):
                                            # Enviar click izquierdo sostenido para iniciar drag & drop / selección
                                            hid.send_mouse(1, 0, 0)
                                    elif event.type == MOUSEBUTTONUP:
                                        if getattr(self, "_tp_is_dragging", False):
                                            # Soltar el click sostenido
                                            hid.send_mouse(0, 0, 0)
                                            self._tp_is_dragging = False
                                            self._tp_last_tap_time = 0
                                        else:
                                            # No estábamos arrastrando. ¿Fue un toque rápido sin moverse apenas?
                                            if not getattr(self, "_tp_moved", False) and (now - getattr(self, "_tp_down_time", 0) < 300):
                                                # Click rápido
                                                hid.send_mouse(1, 0, 0)
                                                hid.send_mouse(0, 0, 0)
                                                self._tp_last_tap_time = now
                                            else:
                                                hid.send_mouse(0, 0, 0)
                                    elif event.type == MOUSEMOTION:
                                        if hasattr(self, '_fingers') and len(self._fingers) >= 2:
                                            pass # Skip pointer move if multi-touch scroll
                                        else:
                                            rx, ry = event.rel if not self._scale_to_display else (
                                                event.rel[0] * self.width // self._phys_w,
                                                event.rel[1] * self.height // self._phys_h
                                            )
                                            # Tolerar un poco de "jitter" estático del dedo
                                            if abs(rx) > 2 or abs(ry) > 2:
                                                self._tp_moved = True
                                            
                                            if abs(rx) > 0 or abs(ry) > 0:
                                                # Cuando deslizamos, si es un arrastre de doble toque mandamos btn1 = 1, si no = 0
                                                btn = 1 if getattr(self, "_tp_is_dragging", False) else 0
                                                hid.send_mouse(btn, int(rx*1.5), int(ry*1.5))
                            except Exception:
                                pass

                    # Scrollable content (handles all mouse events incl. motion)
                    # On sonido tab, let the volume slider grab touch inside its rect first
                    if self.current_tab == 'sonido':
                        if self.volume_slider.handle_event(event):
                            pass
                        elif self.media_controller.handle_event(event):
                            pass
                        else:
                            self._active_scroll_menu().handle_event(event)
                    elif self.current_tab != 'raton':
                        self._active_scroll_menu().handle_event(event)

                        
    def run(self):
        """Main application loop"""
        _hold_last_t = 0   # last time a hold_action was fired
        HOLD_DELAY   = 3000  # ms before repeat starts
        HOLD_REPEAT  = 300   # ms between repeat fires
        _fps_frames  = 0
        _fps_t0      = pygame.time.get_ticks()
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

            # FPS counter — prints every 3 seconds
            _fps_frames += 1
            _fps_elapsed = pygame.time.get_ticks() - _fps_t0
            if _fps_elapsed >= 3000:
                print(f"FPS: {_fps_frames * 1000 / _fps_elapsed:.1f}  "
                      f"scale={'SW' if self._scale_to_display else 'HW'}")
                _fps_frames = 0
                _fps_t0 = pygame.time.get_ticks()
            
        self.cleanup()
        
    def _present(self):
        """Upscale canvas to display surface and flip (nearest-neighbour, fast)."""
        if self._scale_to_display:
            pygame.transform.scale(self.screen, (self._phys_w, self._phys_h),
                                   self._display_surf)
        pygame.display.flip()

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
