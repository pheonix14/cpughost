import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import io
import sys
import json
import time
import queue
import threading
import asyncio
from typing import Optional, Tuple
import pygame
import websockets
from rich.console import Console

console = Console()

# Pygame key to string mapping for remote injection
PYGAME_KEY_MAP = {
    pygame.K_RETURN: "return",
    pygame.K_BACKSPACE: "backspace",
    pygame.K_TAB: "tab",
    pygame.K_SPACE: "space",
    pygame.K_LSHIFT: "shift",
    pygame.K_RSHIFT: "shift",
    pygame.K_LCTRL: "ctrl",
    pygame.K_RCTRL: "ctrl",
    pygame.K_LALT: "alt",
    pygame.K_RALT: "alt",
    pygame.K_LSUPER: "super",
    pygame.K_RSUPER: "super",
    pygame.K_MENU: "menu",
    pygame.K_PRINT: "print_screen",
    pygame.K_INSERT: "insert",
    pygame.K_PAUSE: "pause",
    pygame.K_NUMLOCK: "num_lock",
    pygame.K_SCROLLOCK: "scroll_lock",
    pygame.K_ESCAPE: "esc",
    pygame.K_UP: "up",
    pygame.K_DOWN: "down",
    pygame.K_LEFT: "left",
    pygame.K_RIGHT: "right",
    pygame.K_DELETE: "delete",
    pygame.K_HOME: "home",
    pygame.K_END: "end",
    pygame.K_PAGEUP: "page_up",
    pygame.K_PAGEDOWN: "page_down",
    pygame.K_CAPSLOCK: "caps_lock",
    pygame.K_F1: "f1", pygame.K_F2: "f2", pygame.K_F3: "f3", pygame.K_F4: "f4",
    pygame.K_F5: "f5", pygame.K_F6: "f6", pygame.K_F7: "f7", pygame.K_F8: "f8",
    pygame.K_F9: "f9", pygame.K_F10: "f10", pygame.K_F11: "f11", pygame.K_F12: "f12",
    pygame.K_KP0: "kp_0", pygame.K_KP1: "kp_1", pygame.K_KP2: "kp_2",
    pygame.K_KP3: "kp_3", pygame.K_KP4: "kp_4", pygame.K_KP5: "kp_5",
    pygame.K_KP6: "kp_6", pygame.K_KP7: "kp_7", pygame.K_KP8: "kp_8",
    pygame.K_KP9: "kp_9", pygame.K_KP_ENTER: "enter",
    pygame.K_KP_PLUS: "+", pygame.K_KP_MINUS: "-",
    pygame.K_KP_MULTIPLY: "*", pygame.K_KP_DIVIDE: "/",
    pygame.K_KP_PERIOD: "."
}


def calculate_letterbox(win_w: int, win_h: int, raw_w: int, raw_h: int) -> Tuple[int, int, int, int]:
    """Calculate scaled dimensions and centered offsets to preserve aspect ratio."""
    if win_w <= 0 or win_h <= 0 or raw_w <= 0 or raw_h <= 0:
        return win_w, win_h, 0, 0

    aspect = raw_w / raw_h
    win_aspect = win_w / win_h

    if win_aspect > aspect:
        render_h = win_h
        render_w = int(render_h * aspect)
    else:
        render_w = win_w
        render_h = int(render_w / aspect)

    offset_x = (win_w - render_w) // 2
    offset_y = (win_h - render_h) // 2
    return render_w, render_h, offset_x, offset_y


class GhostScreenViewer:
    def __init__(self, host_ip: str, port: int = 8889, initial_width: int = 1280, initial_height: int = 720):
        self.host_ip = host_ip
        self.port = port
        self.uri = f"ws://{host_ip}:{port}"
        self.win_w = initial_width
        self.win_h = initial_height
        self.fullscreen = False
        self.input_grabbed = True
        self.running = False

        # Zero-lag frame queue: size 1 drops stale frames immediately
        self.frame_queue = queue.Queue(maxsize=1)
        self.event_queue = queue.Queue()
        self.pending_mouse_pos: Optional[Tuple[float, float]] = None

        self.last_frame_time = time.time()
        self.fps = 0
        self.frame_count = 0
        self.connected = False
        self.black_screen_warning = False
        self.consecutive_black_frames = 0

    def start(self):
        console.print(f"[SCREEN] Connecting viewer window to ws://{self.host_ip}:{self.port}...")
        self.running = True

        net_thread = threading.Thread(target=self._run_network_loop, daemon=True)
        net_thread.start()

        self._run_gui_loop()

    def _run_network_loop(self):
        asyncio.run(self._network_client())

    async def _network_client(self):
        while self.running:
            try:
                async with websockets.connect(
                    self.uri,
                    max_size=10 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    self.connected = True
                    console.print(f"[OK] Connected to screen stream at {self.host_ip}")

                    async def send_events():
                        while self.running:
                            try:
                                # Dispatch coalesced mouse position if available
                                if self.pending_mouse_pos:
                                    mx, my = self.pending_mouse_pos
                                    self.pending_mouse_pos = None
                                    await ws.send(json.dumps({"type": "mouse_move", "x": mx, "y": my}))

                                event_data = self.event_queue.get_nowait()
                                await ws.send(json.dumps(event_data))
                            except queue.Empty:
                                await asyncio.sleep(0.005)
                            except Exception:
                                break

                    async def receive_frames():
                        while self.running:
                            try:
                                msg = await ws.recv()
                                if isinstance(msg, bytes):
                                    if self.frame_queue.full():
                                        try:
                                            self.frame_queue.get_nowait()
                                        except queue.Empty:
                                            pass
                                    self.frame_queue.put(msg)
                            except Exception:
                                break

                    await asyncio.gather(send_events(), receive_frames())
            except Exception as e:
                self.connected = False
                if self.running:
                    console.print(f"[WARN] Connection lost: {e}. Retrying in 1.5s...")
                    await asyncio.sleep(1.5)
            finally:
                self.connected = False

    def _run_gui_loop(self):
        pygame.init()
        pygame.display.set_caption(f"CPUGhost Monitor - [{self.host_ip}] (Ctrl+G Grab/Ungrab | F11 Fullscreen)")
        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
        clock = pygame.time.Clock()

        current_surface = None
        raw_w, raw_h = 1920, 1080
        render_w, render_h = self.win_w, self.win_h
        offset_x, offset_y = 0, 0
        font = pygame.font.SysFont("monospace", 14)

        while self.running:
            # Handle Pygame input events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break

                elif event.type == pygame.VIDEORESIZE:
                    if not self.fullscreen:
                        self.win_w, self.win_h = event.w, event.h
                        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h
                        )

                elif event.type == pygame.KEYDOWN:
                    # F11 Fullscreen toggle
                    if event.key == pygame.K_F11:
                        self.fullscreen = not self.fullscreen
                        if self.fullscreen:
                            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                            self.win_w, self.win_h = screen.get_size()
                        else:
                            screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
                            self.win_w, self.win_h = 1280, 720
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h
                        )
                        continue

                    # Ctrl+G to toggle input grab
                    if event.key == pygame.K_g and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                        self.input_grabbed = not self.input_grabbed
                        status_str = "Grabbed" if self.input_grabbed else "Released"
                        console.print(f"[SCREEN] Input focus: {status_str}")
                        if not self.input_grabbed:
                            pygame.mouse.set_visible(True)
                        continue

                    # Ctrl+Q or ESC when ungrabbed to quit
                    if not self.input_grabbed and event.key == pygame.K_ESCAPE:
                        self.running = False
                        break
                    if (event.key == pygame.K_q and (pygame.key.get_mods() & pygame.KMOD_CTRL)):
                        self.running = False
                        break

                    # Send key press to remote
                    if self.input_grabbed:
                        key_name = PYGAME_KEY_MAP.get(event.key, event.unicode)
                        if key_name:
                            self.event_queue.put({"type": "key_down", "key": key_name})

                elif event.type == pygame.KEYUP:
                    if self.input_grabbed:
                        key_name = PYGAME_KEY_MAP.get(event.key, event.unicode)
                        if key_name:
                            self.event_queue.put({"type": "key_up", "key": key_name})

                elif event.type == pygame.MOUSEMOTION:
                    if self.input_grabbed and render_w > 0 and render_h > 0:
                        mx, my = event.pos
                        inside = (offset_x <= mx < offset_x + render_w and offset_y <= my < offset_y + render_h)

                        # Hide local cursor when hovering inside the remote desktop to avoid double cursor
                        pygame.mouse.set_visible(not inside)

                        rx = max(0.0, min(1.0, (mx - offset_x) / render_w))
                        ry = max(0.0, min(1.0, (my - offset_y) / render_h))
                        self.pending_mouse_pos = (rx, ry)

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if self.input_grabbed and render_w > 0 and render_h > 0:
                        mx, my = event.pos
                        rx = max(0.0, min(1.0, (mx - offset_x) / render_w))
                        ry = max(0.0, min(1.0, (my - offset_y) / render_h))
                        self.pending_mouse_pos = (rx, ry)

                        btn = "left"
                        if event.button == 3:
                            btn = "right"
                        elif event.button == 2:
                            btn = "middle"
                        elif event.button == 4:
                            self.event_queue.put({"type": "scroll", "dx": 0, "dy": 2})
                            continue
                        elif event.button == 5:
                            self.event_queue.put({"type": "scroll", "dx": 0, "dy": -2})
                            continue
                        self.event_queue.put({"type": "mouse_down", "button": btn})

                elif event.type == pygame.MOUSEBUTTONUP:
                    if self.input_grabbed:
                        btn = "left"
                        if event.button == 3:
                            btn = "right"
                        elif event.button == 2:
                            btn = "middle"
                        elif event.button in (4, 5):
                            continue
                        self.event_queue.put({"type": "mouse_up", "button": btn})

            # Check for new video frame
            try:
                frame_bytes = self.frame_queue.get_nowait()
                raw_surface = pygame.image.load(io.BytesIO(frame_bytes))
                nw, nh = raw_surface.get_size()
                if (nw, nh) != (raw_w, raw_h):
                    raw_w, raw_h = nw, nh
                    render_w, render_h, offset_x, offset_y = calculate_letterbox(
                        self.win_w, self.win_h, raw_w, raw_h
                    )

                current_surface = pygame.transform.scale(raw_surface, (render_w, render_h))
                self.frame_count += 1
                now = time.time()
                if now - self.last_frame_time >= 1.0:
                    self.fps = self.frame_count
                    self.frame_count = 0
                    self.last_frame_time = now

                # Quick check if frame is solid black
                is_black = True
                samples = [
                    (nw // 2, nh // 2), (nw // 4, nh // 4), (3 * nw // 4, 3 * nh // 4),
                    (nw // 2, nh // 4), (nw // 4, nh // 2)
                ]
                for sx, sy in samples:
                    c = raw_surface.get_at((sx, sy))
                    if c[0] > 5 or c[1] > 5 or c[2] > 5:
                        is_black = False
                        break
                if is_black:
                    self.consecutive_black_frames += 1
                    if self.consecutive_black_frames >= 20:
                        self.black_screen_warning = True
                else:
                    self.consecutive_black_frames = 0
                    self.black_screen_warning = False
            except queue.Empty:
                pass

            # Render frame
            if self.connected and current_surface:
                screen.fill((0, 0, 0))  # Black letterbox bars
                screen.blit(current_surface, (offset_x, offset_y))
            else:
                screen.fill((16, 20, 26))
                title = font.render(f"[CPUGHOST MONITOR] Connecting to {self.host_ip}:{self.port}...", True, (0, 255, 170))
                sub = font.render("Waiting for host screen service to stream. Auto-reconnecting in background...", True, (160, 170, 185))
                screen.blit(title, (40, self.win_h // 2 - 25))
                screen.blit(sub, (40, self.win_h // 2 + 5))

            # Black screen diagnosis alert
            if self.connected and self.black_screen_warning:
                alert_box = pygame.Surface((min(self.win_w - 60, 800), 75), pygame.SRCALPHA)
                alert_box.fill((45, 10, 10, 220))
                pygame.draw.rect(alert_box, (255, 70, 70), alert_box.get_rect(), 2)

                t1 = font.render("[ALERT: BLACK SCREEN DETECTED - HOST RUNNING WAYLAND]", True, (255, 120, 120))
                t2 = font.render("Wayland isolates display buffers. The Zorin PC must switch to an Xorg session.", True, (240, 240, 240))
                t3 = font.render("Fix on Zorin PC: sudo sed -i 's/#*WaylandEnable=false/WaylandEnable=false/' /etc/gdm3/custom.conf", True, (255, 220, 120))

                alert_box.blit(t1, (12, 8))
                alert_box.blit(t2, (12, 28))
                alert_box.blit(t3, (12, 48))
                screen.blit(alert_box, (30, 45))

            # HUD overlay
            hud_text = f"{self.fps} FPS | {raw_w}x{raw_h} | {'Grabbed (Ctrl+G)' if self.input_grabbed else 'Released (Ctrl+G)'} | F11 Fullscreen"
            hud_surface = font.render(hud_text, True, (200, 200, 200))
            hud_bg = pygame.Surface((hud_surface.get_width() + 16, hud_surface.get_height() + 8), pygame.SRCALPHA)
            hud_bg.fill((0, 0, 0, 160))
            hud_bg.blit(hud_surface, (8, 4))
            screen.blit(hud_bg, (self.win_w - hud_bg.get_width() - 15, 12))

            if not self.input_grabbed:
                banner = font.render("[INPUT RELEASED] Press Ctrl+G to grab mouse & keyboard", True, (255, 200, 50))
                screen.blit(banner, (15, 15))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()
        console.print("[SCREEN] Viewer window closed.")
