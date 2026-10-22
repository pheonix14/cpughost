import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import io
import sys
import json
import time
import queue
import threading
import asyncio
from typing import Optional
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
    pygame.K_F9: "f9", pygame.K_F10: "f10", pygame.K_F11: "f11", pygame.K_F12: "f12"
}


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

        self.frame_queue = queue.Queue(maxsize=2)
        self.event_queue = queue.Queue()
        self.remote_res = (1920, 1080)
        self.last_frame_time = time.time()
        self.fps = 0
        self.frame_count = 0

    def start(self):
        console.print(f"[SCREEN] Connecting viewer window to ws://{self.host_ip}:{self.port}...")
        self.running = True

        # Start network worker thread
        net_thread = threading.Thread(target=self._run_network_loop, daemon=True)
        net_thread.start()

        # Start Pygame rendering in main thread
        self._run_gui_loop()

    def _run_network_loop(self):
        asyncio.run(self._network_client())

    async def _network_client(self):
        while self.running:
            try:
                async with websockets.connect(self.uri, max_size=10 * 1024 * 1024) as ws:
                    console.print(f"[OK] Connected to screen stream at {self.host_ip}")

                    async def send_events():
                        while self.running:
                            try:
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
                if self.running:
                    console.print(f"[WARN] Connection lost: {e}. Retrying in 2s...")
                    time.sleep(2)

    def _run_gui_loop(self):
        pygame.init()
        pygame.display.set_caption(f"CPUGhost Screen - [{self.host_ip}] (Ctrl+G Grab/Ungrab | F11 Fullscreen)")
        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
        clock = pygame.time.Clock()

        current_surface = None
        font = pygame.font.SysFont("monospace", 14)

        while self.running:
            # Handle Pygame events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break

                elif event.type == pygame.VIDEORESIZE:
                    if not self.fullscreen:
                        self.win_w, self.win_h = event.w, event.h
                        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)

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
                        continue

                    # Ctrl+G to toggle input grab
                    if event.key == pygame.K_g and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                        self.input_grabbed = not self.input_grabbed
                        status_str = "Grabbed" if self.input_grabbed else "Released"
                        console.print(f"[SCREEN] Input focus: {status_str}")
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
                    if self.input_grabbed and current_surface:
                        # Normalize coordinates
                        mx, my = event.pos
                        rx = mx / self.win_w
                        ry = my / self.win_h
                        self.event_queue.put({"type": "mouse_move", "x": rx, "y": ry})

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if self.input_grabbed:
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
                current_surface = pygame.transform.scale(raw_surface, (self.win_w, self.win_h))
                self.frame_count += 1
                now = time.time()
                if now - self.last_frame_time >= 1.0:
                    self.fps = self.frame_count
                    self.frame_count = 0
                    self.last_frame_time = now
            except queue.Empty:
                pass

            # Render
            if current_surface:
                screen.blit(current_surface, (0, 0))
            else:
                screen.fill((18, 20, 24))
                txt = font.render(f"[WAIT] Connecting to CPUGhost at {self.host_ip}:{self.port}...", True, (150, 150, 150))
                screen.blit(txt, (30, self.win_h // 2))

            # HUD overlay when ungrabbed
            if not self.input_grabbed:
                banner = font.render("[INPUT RELEASED] Press Ctrl+G to interact with remote desktop", True, (255, 200, 50))
                screen.blit(banner, (15, 15))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()
        console.print("[SCREEN] Viewer window closed.")
