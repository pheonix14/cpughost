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

# Standard key name mapping for special keys
PYGAME_KEY_MAP = {
    pygame.K_SPACE: "space",
    pygame.K_RETURN: "return",
    pygame.K_KP_ENTER: "return",
    pygame.K_TAB: "tab",
    pygame.K_BACKSPACE: "backspace",
    pygame.K_DELETE: "delete",
    pygame.K_ESCAPE: "esc",
    pygame.K_UP: "up",
    pygame.K_DOWN: "down",
    pygame.K_LEFT: "left",
    pygame.K_RIGHT: "right",
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
    pygame.K_HOME: "home",
    pygame.K_END: "end",
    pygame.K_PAGEUP: "page up",
    pygame.K_PAGEDOWN: "page down",
    pygame.K_INSERT: "insert",
}
if hasattr(pygame, "K_LGUI"):
    PYGAME_KEY_MAP[pygame.K_LGUI] = "super"
if hasattr(pygame, "K_RGUI"):
    PYGAME_KEY_MAP[pygame.K_RGUI] = "super"


def calculate_letterbox(
    win_w: int, win_h: int,
    frame_w: int = 1920, frame_h: int = 1080,
    raw_w: Optional[int] = None, raw_h: Optional[int] = None,
    zoom: float = 1.0,
    force_16_9: bool = False
) -> Tuple[int, int, int, int]:
    """
    Calculate scaled dimensions and offsets preserving natural display ratio without distortion.
    Supports zoom and optional forced 16:9 ratio.
    """
    fw = raw_w if raw_w is not None else frame_w
    fh = raw_h if raw_h is not None else frame_h
    if fw <= 0 or fh <= 0 or win_w <= 0 or win_h <= 0:
        return win_w, win_h, 0, 0

    if force_16_9:
        target_aspect = 16.0 / 9.0
    else:
        target_aspect = fw / fh

    # Fit target aspect ratio inside window bounds
    if (win_w / win_h) > target_aspect:
        base_h = win_h
        base_w = int(win_h * target_aspect)
    else:
        base_w = win_w
        base_h = int(win_w / target_aspect)

    scale = max(0.2, min(2.5, zoom))
    render_w = max(1, int(base_w * scale))
    render_h = max(1, int(base_h * scale))
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
    def __init__(self, host_ip: str, port: int = 8889, audio_port: int = 8890, initial_width: int = 1280, initial_height: int = 720, screen_id: Optional[int] = None):
        self.host_ip = host_ip
        self.port = port
        self.audio_port = audio_port
        self.uri = f"ws://{host_ip}:{port}"
        self.audio_uri = f"ws://{host_ip}:{audio_port}"
        # 16:9 Landscape window dimensions
        self.win_w = initial_width
        self.win_h = initial_height
        self.fullscreen = False
        self.running = False
        self.mouse_pressed = False

        self.frame_queue = queue.Queue(maxsize=3)
        self.event_queue = queue.Queue()
        self.pending_mouse_pos: Optional[Tuple[float, float]] = None

        self.last_frame_time = time.time()
        self.fps = 0
        self.frame_count = 0
        self.connected = False

        # Load configurations: pointer styles, normal zoom level, and aspect ratio
        cfg = load_config()
        self.pointer_color_key = cfg.get("pointer_color", "zorin_cyan")
        if self.pointer_color_key not in POINTER_COLORS:
            self.pointer_color_key = "zorin_cyan"
        self.pointer_scale = float(cfg.get("pointer_scale", 1.2))
        self.pointer_click_effect = bool(cfg.get("pointer_click_effect", True))
        self.zoom = float(cfg.get("screen_zoom", 1.0))
        self.force_16_9 = bool(cfg.get("force_16_9", False))

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

        # Multi-Screen (>5 Screens + Caesar Phone) Management
        self.current_screen_id: int = screen_id if screen_id is not None else int(cfg.get("active_screen", 1))
        self.show_screen_tabs: bool = True
        self.screen_tabs: List[Tuple[int, str, pygame.Rect]] = []
        self.available_screens = {
            1: "Main 1",
            2: "Screen 2",
            3: "Screen 3",
            4: "Screen 4",
            5: "Screen 5",
            6: "Screen 6",
            7: "Left 7",
            8: "Right 8",
            9: "Caesar Phone",
            0: "All (0)"
        }

    def switch_screen(self, screen_id: int):
        """Switch active remote screen (supports screens 0-8 and phone screen 9)."""
        self.current_screen_id = screen_id
        cfg = load_config()
        cfg["active_screen"] = screen_id
        save_config(cfg)
        self.event_queue.put({"type": "select_screen", "screen_id": screen_id})
        sname = self.available_screens.get(screen_id, f"Screen {screen_id}")
        self.show_toast(f"Display: {sname} (F2 Next | F3 Caesar)")

    def cycle_next_screen(self):
        """Cycle through all available screens (1..8, 9/Caesar, 0/All)."""
        screen_order = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0]
        try:
            cur_idx = screen_order.index(self.current_screen_id)
            next_screen = screen_order[(cur_idx + 1) % len(screen_order)]
        except ValueError:
            next_screen = 1
        self.switch_screen(next_screen)

    def toggle_phone_screen(self):
        """Toggle directly to/from Android phone screen (Caesar)."""
        if self.current_screen_id == 9:
            self.switch_screen(1)
        else:
            self.switch_screen(9)

    def launch_phone_popout(self):
        """Launch dedicated high-performance scrcpy mirror window for Caesar."""
        try:
            from .phone import launch_phone_mirror
            proc = launch_phone_mirror(phone_name="Caesar")
            if proc:
                self.show_toast("Launched Caesar Mirror (scrcpy 60fps)")
            else:
                self.show_toast("Check adb/scrcpy connection for Caesar")
        except Exception as e:
            self.show_toast(f"Phone mirror: {e}")

    def adjust_zoom(self, delta: float):
        """Adjust screen zoom level (zoom in / zoom out)."""
        self.zoom = round(max(0.40, min(1.80, self.zoom + delta)), 2)
        cfg = load_config()
        cfg["screen_zoom"] = self.zoom
        save_config(cfg)
        self.show_toast(f"Screen Zoom: {int(self.zoom * 100)}% (F8/F9)")

    def set_zoom(self, val: float = 1.0):
        """Set screen zoom level directly (default 1.0 normal)."""
        self.zoom = round(max(0.40, min(1.80, val)), 2)
        cfg = load_config()
        cfg["screen_zoom"] = self.zoom
        save_config(cfg)
        self.show_toast(f"Screen Zoom Reset: {int(self.zoom * 100)}% (F10)")

    def toggle_aspect_ratio(self):
        """Toggle between normal native aspect ratio and forced 16:9 landscape."""
        self.force_16_9 = not self.force_16_9
        cfg = load_config()
        cfg["force_16_9"] = self.force_16_9
        save_config(cfg)
        mode_str = "16:9 Landscape" if self.force_16_9 else "Normal (Native Ratio)"
        self.show_toast(f"Aspect Ratio: {mode_str} (F5)")

    def cycle_pointer_color(self):
        """Cycle through Zorin accent colors in real-time."""
        self.pointer_idx = (self.pointer_idx + 1) % len(self.pointer_keys)
        self.pointer_color_key = self.pointer_keys[self.pointer_idx]
        color_info = POINTER_COLORS[self.pointer_color_key]
        self.cursor_surface = make_cursor_surface(color_info["rgb"], self.pointer_scale)
        cfg = load_config()
        cfg["pointer_color"] = self.pointer_color_key
        save_config(cfg)
        self.show_toast(f"Pointer Color: {color_info['name']} (F6)")

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
        self.show_toast(f"Pointer Size: {self.pointer_scale}x (F7)")

    def show_toast(self, msg: str, duration: float = 2.0):
        self.toast_msg = msg
        self.toast_until = time.time() + duration

    def _apply_grab_settings(self, grab_kb: bool = False):
        """
        Ensure mouse cursor is NEVER locked or trapped inside the window.
        Keeps mouse 100% free to move anywhere in and out of the viewer.
        """
        try:
            import pygame._sdl2.video as sdl2_video
            win = sdl2_video.Window.from_display_module()
            if hasattr(win, "grab_keyboard"):
                win.grab_keyboard = False
            if hasattr(win, "grab_mouse"):
                win.grab_mouse = False
        except Exception:
            pass
        try:
            pygame.event.set_grab(False)
        except Exception:
            pass
        try:
            pygame.mouse.set_visible(True)
        except Exception:
            pass

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
                                elif isinstance(msg, str):
                                    try:
                                        evt = json.loads(msg)
                                        if evt.get("type") == "screen_changed":
                                            sid = evt.get("screen_id", 1)
                                            sname = evt.get("name", f"Screen {sid}")
                                            self.current_screen_id = sid
                                            self.show_toast(f"Display: {sname}")
                                    except Exception:
                                        pass
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
        title_focused = f"CPUGhost Monitor - [{self.host_ip}] [Zorin Keyboard Active | Mouse Free]"
        title_unfocused = f"CPUGhost Monitor - [{self.host_ip}] (Click inside to capture keyboard)"
        pygame.display.set_caption(title_focused)
        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
        clock = pygame.time.Clock()

        # Keyboard routed exclusively to remote Zorin while keeping mouse cursor 100% free
        self._apply_grab_settings(grab_kb=True)

        current_surface = None
        raw_w, raw_h = 1920, 1080
        render_w, render_h, offset_x, offset_y = calculate_letterbox(
            self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
        )
        font = pygame.font.SysFont("monospace", 14)

        focus_gained_ev = getattr(pygame, "WINDOWFOCUSGAINED", 32784)
        focus_lost_ev = getattr(pygame, "WINDOWFOCUSLOST", 32785)

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break

                elif event.type == focus_gained_ev:
                    self._apply_grab_settings(grab_kb=True)
                    pygame.display.set_caption(title_focused)

                elif event.type == focus_lost_ev:
                    self._apply_grab_settings(grab_kb=False)
                    pygame.display.set_caption(title_unfocused)

                elif event.type == pygame.ACTIVEEVENT:
                    if hasattr(event, "gain"):
                        if event.gain:
                            self._apply_grab_settings(grab_kb=True)
                            pygame.display.set_caption(title_focused)
                        else:
                            self._apply_grab_settings(grab_kb=False)
                            pygame.display.set_caption(title_unfocused)

                elif event.type == pygame.VIDEORESIZE:
                    if not self.fullscreen:
                        self.win_w, self.win_h = event.w, event.h
                        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
                        self._apply_grab_settings(grab_kb=True)
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
                        )

                elif event.type == pygame.KEYDOWN:
                    # Dedicated Function Keys (NEVER intercept regular letters or symbols like c, p, -, +, =)
                    # F1: Toggle Multi-Screen Selector Tabs Bar
                    if event.key == pygame.K_F1:
                        self.show_screen_tabs = not self.show_screen_tabs
                        status = "Visible" if self.show_screen_tabs else "Hidden"
                        self.show_toast(f"Multi-Screen Bar: {status} (F1)")
                        continue

                    # F2: Cycle next screen (Screens 1..8, Caesar Phone, All)
                    if event.key == pygame.K_F2:
                        self.cycle_next_screen()
                        continue

                    # F3: Toggle Phone "Caesar" screen (Shift+F3 launches scrcpy popout)
                    if event.key == pygame.K_F3:
                        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                            self.launch_phone_popout()
                        else:
                            self.toggle_phone_screen()
                        continue

                    # F4: Android Back / Home button when on Caesar Phone
                    if event.key == pygame.K_F4:
                        if self.current_screen_id == 9:
                            self.event_queue.put({"type": "key_down", "key": "home"})
                            self.show_toast("Phone: Sent HOME")
                        continue

                    # Alt + 0..8: Direct Screen Switch
                    if (pygame.key.get_mods() & pygame.KMOD_ALT):
                        alt_screen_map = {
                            pygame.K_0: 0,
                            pygame.K_1: 1,
                            pygame.K_2: 2,
                            pygame.K_3: 3,
                            pygame.K_4: 4,
                            pygame.K_5: 5,
                            pygame.K_6: 6,
                            pygame.K_7: 7,
                            pygame.K_8: 8,
                            pygame.K_9: 9,
                        }
                        if event.key in alt_screen_map:
                            self.switch_screen(alt_screen_map[event.key])
                            continue

                    # F11: Fullscreen toggle
                    if event.key == pygame.K_F11:
                        self.fullscreen = not self.fullscreen
                        if self.fullscreen:
                            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                            self.win_w, self.win_h = screen.get_size()
                        else:
                            screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
                            self.win_w, self.win_h = 1280, 720
                        self._apply_grab_settings(grab_kb=True)
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
                        )
                        continue

                    # F5: Toggle 16:9 Landscape vs Native aspect ratio
                    if event.key == pygame.K_F5:
                        self.toggle_aspect_ratio()
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
                        )
                        continue

                    # F6: cycle pointer color
                    if event.key == pygame.K_F6:
                        self.cycle_pointer_color()
                        continue

                    # F7: cycle pointer size
                    if event.key == pygame.K_F7:
                        self.cycle_pointer_size()
                        continue

                    # F8: Zoom out (reduce screen size)
                    if event.key == pygame.K_F8:
                        self.adjust_zoom(-0.05)
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
                        )
                        continue

                    # F9: Zoom in (increase screen size)
                    if event.key == pygame.K_F9:
                        self.adjust_zoom(+0.05)
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
                        )
                        continue

                    # F10: Reset zoom to 100% normal
                    if event.key == pygame.K_F10:
                        self.set_zoom(1.0)
                        render_w, render_h, offset_x, offset_y = calculate_letterbox(
                            self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
                        )
                        continue

                    # Ctrl+Shift+Q to quit viewer
                    if event.key == pygame.K_q and (pygame.key.get_mods() & pygame.KMOD_CTRL) and (pygame.key.get_mods() & pygame.KMOD_SHIFT):
                        self.running = False
                        break

                    # ALL OTHER KEYS PASS DIRECTLY THROUGH TO ZORIN (letters, symbols, enter, backspace, bash shortcuts)
                    key_name = PYGAME_KEY_MAP.get(event.key)
                    if not key_name:
                        key_name = pygame.key.name(event.key)

                    if key_name in ("left meta", "right meta", "left super", "right super", "meta", "windows", "win"):
                        key_name = "super"
                    elif key_name in ("left shift", "right shift"):
                        key_name = "shift"
                    elif key_name in ("left ctrl", "right ctrl"):
                        key_name = "ctrl"
                    elif key_name in ("left alt", "right alt"):
                        key_name = "alt"

                    if key_name:
                        self.event_queue.put({"type": "key_down", "key": key_name})

                elif event.type == pygame.KEYUP:
                    key_name = PYGAME_KEY_MAP.get(event.key)
                    if not key_name:
                        key_name = pygame.key.name(event.key)

                    if key_name in ("left meta", "right meta", "left super", "right super", "meta", "windows", "win"):
                        key_name = "super"
                    elif key_name in ("left shift", "right shift"):
                        key_name = "shift"
                    elif key_name in ("left ctrl", "right ctrl"):
                        key_name = "ctrl"
                    elif key_name in ("left alt", "right alt"):
                        key_name = "alt"

                    if key_name:
                        self.event_queue.put({"type": "key_up", "key": key_name})

                elif event.type == pygame.MOUSEMOTION:
                    mx, my = event.pos
                    inside = (offset_x <= mx < offset_x + render_w and offset_y <= my < offset_y + render_h)
                    self.cursor_pos = (mx, my)
                    self.cursor_visible = False

                    # OS pointer always visible, mouse never locked or trapped
                    pygame.mouse.set_visible(True)

                    # Seamless mouse tracking: cursor roams freely, never trapped
                    if inside and render_w > 0 and render_h > 0:
                        rx = max(0.0, min(1.0, (mx - offset_x) / render_w))
                        ry = max(0.0, min(1.0, (my - offset_y) / render_h))
                        self.pending_mouse_pos = (rx, ry)
                        if self.mouse_pressed:
                            self.event_queue.put({"type": "mouse_move", "x": rx, "y": ry})

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    mx, my = event.pos

                    # Check click on Multi-Screen Selector Tabs
                    if self.show_screen_tabs and event.button == 1:
                        tab_clicked = False
                        for sid, sname, rect in self.screen_tabs:
                            if rect.collidepoint(mx, my):
                                self.switch_screen(sid)
                                tab_clicked = True
                                break
                        if tab_clicked:
                            continue

                    inside = (offset_x <= mx < offset_x + render_w and offset_y <= my < offset_y + render_h)
                    self.cursor_pos = (mx, my)
                    self.cursor_visible = inside

                    # Focus & grab keyboard for Zorin while leaving mouse completely free
                    self._apply_grab_settings(grab_kb=True)
                    pygame.display.set_caption(title_focused)

                    # Never lock mouse cursor inside the window
                    if inside and render_w > 0 and render_h > 0:
                        rx = max(0.0, min(1.0, (mx - offset_x) / render_w))
                        ry = max(0.0, min(1.0, (my - offset_y) / render_h))
                        self.pending_mouse_pos = (rx, ry)
                        self.mouse_pressed = True

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

                    if inside and render_w > 0 and render_h > 0:
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
                        self.win_w, self.win_h, raw_w, raw_h, zoom=self.zoom, force_16_9=self.force_16_9
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
            screen.fill((16, 20, 26))  # Matte dark background
            if self.connected and current_surface:
                screen.blit(current_surface, (offset_x, offset_y))
                # 16:9 Landscape boundary frame
                border_rect = pygame.Rect(offset_x - 1, offset_y - 1, render_w + 2, render_h + 2)
                pygame.draw.rect(screen, (40, 52, 68), border_rect, 1)
            else:
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

            # Draw custom colored pointer over remote canvas
            if self.cursor_visible and self.cursor_pos and self.cursor_surface:
                screen.blit(self.cursor_surface, self.cursor_pos)

            # Toast banner
            if self.toast_msg and time.time() < self.toast_until:
                t_surf = font.render(f"[ {self.toast_msg} ]", True, (255, 255, 255))
                t_bg = pygame.Surface((t_surf.get_width() + 24, t_surf.get_height() + 12), pygame.SRCALPHA)
                t_bg.fill((20, 25, 35, 230))
                c_accent = POINTER_COLORS[self.pointer_color_key]["rgb"]
                pygame.draw.rect(t_bg, c_accent, t_bg.get_rect(), 2)
                t_bg.blit(t_surf, (12, 6))
                screen.blit(t_bg, ((self.win_w - t_bg.get_width()) // 2, 70))

            # Render Multi-Screen Selector Tabs Bar at top (Screens 1..8, Caesar Phone, All 0)
            if self.show_screen_tabs:
                self.screen_tabs = []
                tab_h = 24
                tab_y = 6
                cur_x = 12
                tab_defs = [
                    (1, "1:Main"),
                    (2, "2:Scrn2"),
                    (3, "3:Scrn3"),
                    (4, "4:Scrn4"),
                    (5, "5:Scrn5"),
                    (6, "6:Scrn6"),
                    (7, "7:Left"),
                    (8, "8:Right"),
                    (9, "Caesar"),
                    (0, "0:All")
                ]
                for sid, label in tab_defs:
                    is_active = (sid == self.current_screen_id)
                    txt_color = (255, 255, 255) if is_active else (160, 175, 195)
                    t_txt = font.render(label, True, txt_color)
                    t_w = t_txt.get_width() + 14
                    t_rect = pygame.Rect(cur_x, tab_y, t_w, tab_h)
                    self.screen_tabs.append((sid, label, t_rect))

                    bg_color = (25, 38, 48, 230) if is_active else (15, 20, 26, 180)
                    t_surf = pygame.Surface((t_w, tab_h), pygame.SRCALPHA)
                    t_surf.fill(bg_color)
                    border_color = POINTER_COLORS[self.pointer_color_key]["rgb"] if is_active else (45, 55, 70)
                    pygame.draw.rect(t_surf, border_color, t_surf.get_rect(), 2 if is_active else 1)
                    t_surf.blit(t_txt, (7, 4))
                    screen.blit(t_surf, (cur_x, tab_y))
                    cur_x += t_w + 5

            # HUD overlay
            color_name = POINTER_COLORS[self.pointer_color_key]["name"]
            aspect_name = "16:9 Landscape" if self.force_16_9 else "Normal Ratio"
            cur_sname = self.available_screens.get(self.current_screen_id, f"Screen {self.current_screen_id}")
            hud_text = f"{self.fps} FPS | {cur_sname} (F2/F3) | {aspect_name} (F5) | Zoom: {int(self.zoom * 100)}% (F8/F9) | F11"
            hud_surface = font.render(hud_text, True, (200, 200, 200))
            hud_bg = pygame.Surface((hud_surface.get_width() + 16, hud_surface.get_height() + 8), pygame.SRCALPHA)
            hud_bg.fill((0, 0, 0, 160))
            hud_bg.blit(hud_surface, (8, 4))
            hud_y = 36 if self.show_screen_tabs else 12
            screen.blit(hud_bg, (self.win_w - hud_bg.get_width() - 15, hud_y))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()
        console.print("[SCREEN] Screen viewer closed.")
