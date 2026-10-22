import os
import io
import time
import json
import queue
import threading
import asyncio
from typing import Optional, Tuple, List
import pygame
import pygame.gfxdraw
import websockets
from rich.console import Console
from .config import load_config, save_config, POINTER_COLORS

console = Console()

PYGAME_KEY_MAP = {
    pygame.K_SPACE: "Key.space",
    pygame.K_RETURN: "return",
    pygame.K_TAB: "tab",
    pygame.K_BACKSPACE: "backspace",
    pygame.K_DELETE: "Key.delete",
    pygame.K_ESCAPE: "esc",
    pygame.K_UP: "Key.up",
    pygame.K_DOWN: "Key.down",
    pygame.K_LEFT: "Key.left",
    pygame.K_RIGHT: "Key.right",
    pygame.K_LSHIFT: "shift",
    pygame.K_RSHIFT: "shift",
    pygame.K_LCTRL: "ctrl",
    pygame.K_RCTRL: "ctrl",
    pygame.K_LALT: "alt",
    pygame.K_RALT: "alt",
    pygame.K_LSUPER: "super",
    pygame.K_RSUPER: "super",
    pygame.K_LMETA: "super",
    pygame.K_RMETA: "super",
}


def calculate_letterbox(win_w: int, win_h: int, frame_w: int = 1920, frame_h: int = 1080, raw_w: Optional[int] = None, raw_h: Optional[int] = None) -> Tuple[int, int, int, int]:
    """Calculate scaled dimensions and offsets to preserve aspect ratio within window."""
    fw = raw_w if raw_w is not None else frame_w
    fh = raw_h if raw_h is not None else frame_h
    if fw <= 0 or fh <= 0 or win_w <= 0 or win_h <= 0:
        return win_w, win_h, 0, 0

    scale = min(win_w / fw, win_h / fh)
    render_w = max(1, int(fw * scale))
    render_h = max(1, int(fh * scale))
    offset_x = (win_w - render_w) // 2
    offset_y = (win_h - render_h) // 2
    return render_w, render_h, offset_x, offset_y


def make_cursor_surface(color_rgb: Tuple[int, int, int], scale: float = 1.2) -> pygame.Surface:
    """Generate high-contrast anti-aliased Zorin-styled colored mouse pointer."""
    w, h = max(16, int(28 * scale)), max(16, int(28 * scale))
    surf = pygame.Surface((w, h), pygame.SRCALPHA)

    base_points = [(0, 0), (0, 19), (5, 14), (9, 23), (12, 21), (8, 13), (14, 13)]
    pts = [(int(x * scale), int(y * scale)) for x, y in base_points]

    # Semi-transparent drop-shadow
    shadow_pts = [(x + 2, y + 2) for x, y in pts]
    pygame.gfxdraw.filled_polygon(surf, shadow_pts, (0, 0, 0, 110))

    # Outer border (dark contrast)
    pygame.gfxdraw.filled_polygon(surf, pts, (10, 15, 20, 255))
    pygame.gfxdraw.aapolygon(surf, pts, (10, 15, 20, 255))

    # Inner vibrant accent-colored polygon
    inner_base = [(1, 2), (1, 16), (5, 13), (8, 20), (10, 19), (7, 12), (12, 12)]
    inner_pts = [(int(x * scale), int(y * scale)) for x, y in inner_base]
    pygame.gfxdraw.filled_polygon(surf, inner_pts, (*color_rgb, 255))
    pygame.gfxdraw.aapolygon(surf, inner_pts, (*color_rgb, 255))

    return surf


class GhostScreenViewer:
    def __init__(self, host_ip: str, port: int = 8889, audio_port: int = 8890, initial_width: int = 1280, initial_height: int = 720):
        self.host_ip = host_ip
        self.port = port
        self.audio_port = audio_port
        self.uri = f"ws://{host_ip}:{port}"
        self.audio_uri = f"ws://{host_ip}:{audio_port}"
        self.win_w = initial_width
        self.win_h = initial_height
        self.fullscreen = False
        self.running = False
        self.input_grabbed = False
        self.mouse_pressed = False

        self.frame_queue = queue.Queue(maxsize=3)
        self.event_queue = queue.Queue()
        self.pending_mouse_pos: Optional[Tuple[float, float]] = None

        self.last_frame_time = time.time()
        self.fps = 0
        self.frame_count = 0
        self.connected = False

        # Mouse pointer configuration & Zorin color styles
        cfg = load_config()
        self.pointer_color_key = cfg.get("pointer_color", "zorin_cyan")
        if self.pointer_color_key not in POINTER_COLORS:
            self.pointer_color_key = "zorin_cyan"
        self.pointer_scale = float(cfg.get("pointer_scale", 1.2))
        self.pointer_click_effect = bool(cfg.get("pointer_click_effect", True))

        self.pointer_keys = list(POINTER_COLORS.keys())
        try:
            self.pointer_idx = self.pointer_keys.index(self.pointer_color_key)
        except ValueError:
            self.pointer_idx = 0

        self.cursor_surface = make_cursor_surface(
            POINTER_COLORS[self.pointer_color_key]["rgb"], self.pointer_scale
        )
        self.cursor_pos: Optional[Tuple[int, int]] = None
        self.cursor_visible: bool = False
        self.click_ripples: List[dict] = []
        self.toast_msg: Optional[str] = None
        self.toast_until: float = 0.0

    def cycle_pointer_color(self):
        """Cycle through Zorin accent colors in real-time."""
        self.pointer_idx = (self.pointer_idx + 1) % len(self.pointer_keys)
        self.pointer_color_key = self.pointer_keys[self.pointer_idx]
        color_info = POINTER_COLORS[self.pointer_color_key]
        self.cursor_surface = make_cursor_surface(color_info["rgb"], self.pointer_scale)
        cfg = load_config()
        cfg["pointer_color"] = self.pointer_color_key
        save_config(cfg)
        self.show_toast(f"Pointer Color: {color_info['name']} (F6 / C to cycle)")

    def cycle_pointer_size(self):
        """Cycle through pointer sizes in real-time."""
        scales = [1.0, 1.3, 1.7, 2.0]
        try:
            cur = round(self.pointer_scale, 1)
            idx = scales.index(cur) if cur in scales else 0
            self.pointer_scale = scales[(idx + 1) % len(scales)]
        except Exception:
            self.pointer_scale = 1.3

        color_info = POINTER_COLORS[self.pointer_color_key]
        self.cursor_surface = make_cursor_surface(color_info["rgb"], self.pointer_scale)
        cfg = load_config()
        cfg["pointer_scale"] = self.pointer_scale
        save_config(cfg)
        self.show_toast(f"Pointer Size: {self.pointer_scale}x (F7 / P to cycle)")

    def show_toast(self, msg: str, duration: float = 2.0):
        self.toast_msg = msg
        self.toast_until = time.time() + duration

    def start(self):
        console.print(f"[SCREEN] Connecting viewer window to ws://{self.host_ip}:{self.port}...")
        self.running = True

        net_thread = threading.Thread(target=self._run_network_loop, daemon=True)
        net_thread.start()

        audio_thread = threading.Thread(target=self._run_audio_loop, daemon=True)
        audio_thread.start()

        self._run_gui_loop()

    def _run_audio_loop(self):
        asyncio.run(self._audio_client())

    async def _audio_client(self):
        try:
            import pygame.mixer
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        except Exception:
            return

        while self.running:
            try:
                async with websockets.connect(
                    self.audio_uri,
                    max_size=1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    console.print(f"[OK] Connected to desktop audio stream at {self.host_ip}:{self.audio_port}")
                    while self.running:
                        msg = await ws.recv()
                        if isinstance(msg, bytes) and len(msg) > 0:
                            try:
                                sound = pygame.mixer.Sound(buffer=msg)
                                sound.play()
                            except Exception:
                                pass
            except Exception:
                if self.running:
                    await asyncio.sleep(2.0)

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
                                if self.pending_mouse_pos:
                                    pos = self.pending_mouse_pos
                                    self.pending_mouse_pos = None
                                    await ws.send(json.dumps({"type": "mouse_move", "x": pos[0], "y": pos[1]}))

                                while not self.event_queue.empty():
                                    event_data = self.event_queue.get_nowait()
                                    await ws.send(json.dumps(event_data))

                                await asyncio.sleep(0.002)
                            except queue.Empty:
                                await asyncio.sleep(0.002)
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
                    console.print(f"[WARN] Screen connection lost: {e}. Retrying in 1.5s...")
                    await asyncio.sleep(1.5)
            finally:
                self.connected = False

    def _run_gui_loop(self):
        pygame.init()
        pygame.display.set_caption(f"CPUGhost Monitor - [{self.host_ip}] (Ctrl+G Grab/Ungrab | F6 Color | F7 Size | F11 Fullscreen)")
        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
        clock = pygame.time.Clock()

        current_surface = None
        raw_w, raw_h = 1920, 1080
        render_w, render_h = self.win_w, self.win_h
        offset_x, offset_y = 0, 0
        font = pygame.font.SysFont("monospace", 14)

        while self.running:
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
                    # F6 or (ungrabbed and K_c): cycle pointer color
                    if event.key == pygame.K_F6 or (not self.input_grabbed and event.key == pygame.K_c):
                        self.cycle_pointer_color()
                        continue

                    # F7 or (ungrabbed and K_p): cycle pointer size
                    if event.key == pygame.K_F7 or (not self.input_grabbed and event.key == pygame.K_p):
                        self.cycle_pointer_size()
                        continue

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
                        try:
                            pygame.event.set_grab(self.input_grabbed)
                        except Exception:
                            pass
                        status_str = "Grabbed" if self.input_grabbed else "Released"
                        console.print(f"[SCREEN] Input focus: {status_str}")
                        if not self.input_grabbed:
                            pygame.mouse.set_visible(True)
                        self.show_toast(f"Input Focus: {status_str}")
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
                    mx, my = event.pos
                    inside = (offset_x <= mx < offset_x + render_w and offset_y <= my < offset_y + render_h)
                    self.cursor_pos = (mx, my)
                    self.cursor_visible = inside

                    pygame.mouse.set_visible(not inside)

                    if render_w > 0 and render_h > 0 and (self.input_grabbed or self.mouse_pressed):
                        rx = max(0.0, min(1.0, (mx - offset_x) / render_w))
                        ry = max(0.0, min(1.0, (my - offset_y) / render_h))
                        self.pending_mouse_pos = (rx, ry)
                        # During drag-selection, immediately queue mouse moves for smooth tracking
                        if self.mouse_pressed:
                            self.event_queue.put({"type": "mouse_move", "x": rx, "y": ry})

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    mx, my = event.pos
                    inside = (offset_x <= mx < offset_x + render_w and offset_y <= my < offset_y + render_h)
                    self.cursor_pos = (mx, my)
                    self.cursor_visible = inside

                    # Auto-grab focus when clicking inside the remote desktop
                    if inside and not self.input_grabbed:
                        self.input_grabbed = True
                        try:
                            pygame.event.set_grab(True)
                        except Exception:
                            pass
                        self.show_toast("Input Grabbed (Ctrl+G to release)")

                    if inside and render_w > 0 and render_h > 0:
                        rx = max(0.0, min(1.0, (mx - offset_x) / render_w))
                        ry = max(0.0, min(1.0, (my - offset_y) / render_h))
                        self.pending_mouse_pos = (rx, ry)
                        self.mouse_pressed = True

                        # Click ripple animation
                        if self.pointer_click_effect:
                            c_rgb = POINTER_COLORS[self.pointer_color_key]["rgb"]
                            self.click_ripples.append({
                                "x": mx, "y": my, "radius": 4.0, "max_radius": 24.0, "alpha": 220, "color": c_rgb
                            })

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
                        self.event_queue.put({"type": "mouse_down", "button": btn, "x": rx, "y": ry})

                elif event.type == pygame.MOUSEBUTTONUP:
                    mx, my = event.pos
                    inside = (offset_x <= mx < offset_x + render_w and offset_y <= my < offset_y + render_h)
                    self.cursor_pos = (mx, my)
                    self.mouse_pressed = False

                    if (inside or self.input_grabbed) and render_w > 0 and render_h > 0:
                        rx = max(0.0, min(1.0, (mx - offset_x) / render_w))
                        ry = max(0.0, min(1.0, (my - offset_y) / render_h))
                        self.pending_mouse_pos = (rx, ry)

                        btn = "left"
                        if event.button == 3:
                            btn = "right"
                        elif event.button == 2:
                            btn = "middle"
                        elif event.button in (4, 5):
                            continue
                        self.event_queue.put({"type": "mouse_up", "button": btn, "x": rx, "y": ry})

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

                if (render_w, render_h) != (raw_w, raw_h):
                    current_surface = pygame.transform.scale(raw_surface, (render_w, render_h))
                else:
                    current_surface = raw_surface

                self.frame_count += 1
                now = time.time()
                if now - self.last_frame_time >= 1.0:
                    self.fps = self.frame_count
                    self.frame_count = 0
                    self.last_frame_time = now
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

            # Click ripple animations
            if self.click_ripples:
                ripple_surf = pygame.Surface((self.win_w, self.win_h), pygame.SRCALPHA)
                active_ripples = []
                for r in self.click_ripples:
                    r["radius"] += 2.0
                    r["alpha"] = max(0, r["alpha"] - 18)
                    if r["alpha"] > 0 and r["radius"] <= r["max_radius"]:
                        c = r["color"]
                        pygame.gfxdraw.aacircle(ripple_surf, int(r["x"]), int(r["y"]), int(r["radius"]), (*c, int(r["alpha"])))
                        active_ripples.append(r)
                self.click_ripples = active_ripples
                screen.blit(ripple_surf, (0, 0))

            # Draw custom colored pointer
            if self.cursor_visible and self.cursor_pos and self.cursor_surface:
                screen.blit(self.cursor_surface, self.cursor_pos)

            # Toast banner (e.g. pointer color changed)
            if self.toast_msg and time.time() < self.toast_until:
                t_surf = font.render(f"[ {self.toast_msg} ]", True, (255, 255, 255))
                t_bg = pygame.Surface((t_surf.get_width() + 24, t_surf.get_height() + 12), pygame.SRCALPHA)
                t_bg.fill((20, 25, 35, 230))
                c_accent = POINTER_COLORS[self.pointer_color_key]["rgb"]
                pygame.draw.rect(t_bg, c_accent, t_bg.get_rect(), 2)
                t_bg.blit(t_surf, (12, 6))
                screen.blit(t_bg, ((self.win_w - t_bg.get_width()) // 2, 70))

            # HUD overlay
            color_name = POINTER_COLORS[self.pointer_color_key]["name"]
            hud_text = f"{self.fps} FPS | {raw_w}x{raw_h} | Pointer: {color_name} (F6) | {'Grabbed (Ctrl+G)' if self.input_grabbed else 'Click inside to interact'} | F11"
            hud_surface = font.render(hud_text, True, (200, 200, 200))
            hud_bg = pygame.Surface((hud_surface.get_width() + 16, hud_surface.get_height() + 8), pygame.SRCALPHA)
            hud_bg.fill((0, 0, 0, 160))
            hud_bg.blit(hud_surface, (8, 4))
            screen.blit(hud_bg, (self.win_w - hud_bg.get_width() - 15, 12))

            if not self.input_grabbed:
                banner = font.render("[CLICK SCREEN TO INTERACT] Auto-grabs focus on click | Ctrl+G to release", True, (255, 200, 50))
                screen.blit(banner, (15, 15))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()
        console.print("[SCREEN] Screen viewer closed.")
