import io
import os
import sys
import time
import json
import shutil
import asyncio
import subprocess
from typing import Set, Optional
import mss
from PIL import Image
from pynput.mouse import Controller as MouseController, Button as MouseButton
from pynput.keyboard import Controller as KeyboardController, Key, KeyCode
import websockets
from rich.console import Console

console = Console()

# Mapping for special keys from client
KEY_MAPPING = {
    "return": Key.enter,
    "enter": Key.enter,
    "backspace": Key.backspace,
    "tab": Key.tab,
    "space": Key.space,
    "shift": Key.shift,
    "ctrl": Key.ctrl,
    "alt": Key.alt,
    "esc": Key.esc,
    "escape": Key.esc,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "delete": Key.delete,
    "home": Key.home,
    "end": Key.end,
    "page_up": Key.page_up,
    "page_down": Key.page_down,
    "caps_lock": Key.caps_lock,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12
}


def ensure_display_environment():
    """Ensure a valid X11 or Wayland display environment variable exists."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        # Check standard active X11 sockets
        for num in range(5):
            sock = f"/tmp/.X11-unix/X{num}"
            if os.path.exists(sock):
                os.environ["DISPLAY"] = f":{num}"
                console.print(f"[SCREEN] Attached to detected display: :{num}")
                return

        # If no display found and Xvfb is available, spawn virtual framebuffer
        xvfb_bin = shutil.which("Xvfb")
        if xvfb_bin:
            try:
                subprocess.Popen(
                    [xvfb_bin, ":99", "-screen", "0", "1920x1080x24", "-nocursor"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setpgrp
                )
                time.sleep(0.5)
                os.environ["DISPLAY"] = ":99"
                console.print("[SCREEN] Spawned virtual framebuffer on display :99 (Xvfb)")
                return
            except Exception:
                pass

        # Fallback default
        os.environ["DISPLAY"] = ":0"


class GhostScreenServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8889, fps: int = 30, quality: int = 75, scale: float = 1.0):
        ensure_display_environment()
        self.host = host
        self.port = port
        self.fps = fps
        self.quality = quality
        self.scale = scale
        self.running = False
        self.clients: Set[websockets.WebSocketServerProtocol] = set()

        self.mouse: Optional[MouseController] = None
        self.keyboard: Optional[KeyboardController] = None
        self.sct: Optional[mss.mss] = None
        self.monitor = None

        self._init_input_controllers()
        self._init_screen_capture()

    def _init_input_controllers(self):
        try:
            self.mouse = MouseController()
            self.keyboard = KeyboardController()
        except Exception:
            self.mouse = None
            self.keyboard = None

    def _init_screen_capture(self) -> bool:
        try:
            ensure_display_environment()
            self.sct = mss.mss()
            if self.sct.monitors and len(self.sct.monitors) > 1:
                self.monitor = self.sct.monitors[1]
            elif self.sct.monitors:
                self.monitor = self.sct.monitors[0]
            else:
                self.monitor = None
            return self.monitor is not None
        except Exception:
            self.sct = None
            self.monitor = None
            return False

    def handle_input_event(self, data: dict):
        if not self.mouse or not self.keyboard or not self.monitor:
            self._init_input_controllers()
            if not self.mouse or not self.keyboard:
                return

        event_type = data.get("type")
        if not event_type:
            return

        mon_w = self.monitor["width"]
        mon_h = self.monitor["height"]
        mon_left = self.monitor["left"]
        mon_top = self.monitor["top"]

        try:
            if event_type == "mouse_move":
                rel_x = max(0.0, min(1.0, float(data.get("x", 0))))
                rel_y = max(0.0, min(1.0, float(data.get("y", 0))))
                abs_x = int(mon_left + rel_x * mon_w)
                abs_y = int(mon_top + rel_y * mon_h)
                self.mouse.position = (abs_x, abs_y)

            elif event_type == "mouse_down":
                btn = MouseButton.left
                btn_name = data.get("button", "left")
                if btn_name == "right":
                    btn = MouseButton.right
                elif btn_name == "middle":
                    btn = MouseButton.middle
                self.mouse.press(btn)

            elif event_type == "mouse_up":
                btn = MouseButton.left
                btn_name = data.get("button", "left")
                if btn_name == "right":
                    btn = MouseButton.right
                elif btn_name == "middle":
                    btn = MouseButton.middle
                self.mouse.release(btn)

            elif event_type == "scroll":
                dx = int(data.get("dx", 0))
                dy = int(data.get("dy", 0))
                self.mouse.scroll(dx, dy)

            elif event_type == "key_down":
                key_str = data.get("key", "").lower()
                k = KEY_MAPPING.get(key_str)
                if k is None and len(key_str) == 1:
                    k = key_str
                if k:
                    self.keyboard.press(k)

            elif event_type == "key_up":
                key_str = data.get("key", "").lower()
                k = KEY_MAPPING.get(key_str)
                if k is None and len(key_str) == 1:
                    k = key_str
                if k:
                    self.keyboard.release(k)
        except Exception:
            pass

    async def client_handler(self, websocket):
        self.clients.add(websocket)
        console.print(f"[SCREEN] Client connected from {websocket.remote_address}")

        try:
            async for message in websocket:
                if isinstance(message, str):
                    try:
                        event = json.loads(message)
                        self.handle_input_event(event)
                    except Exception:
                        pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            console.print(f"[SCREEN] Client disconnected: {websocket.remote_address}")

    async def capture_and_broadcast_loop(self):
        interval = 1.0 / max(1, self.fps)

        while self.running:
            start_time = time.time()
            if self.clients:
                if not self.sct or not self.monitor:
                    self._init_screen_capture()

                if self.sct and self.monitor:
                    try:
                        sct_img = self.sct.grab(self.monitor)
                        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

                        if self.scale != 1.0:
                            new_w = int(img.width * self.scale)
                            new_h = int(img.height * self.scale)
                            img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=self.quality, optimize=False)
                        frame_bytes = buf.getvalue()

                        websockets.broadcast(self.clients, frame_bytes)
                    except Exception:
                        # Display might be momentarily unavailable; attempt re-init on next tick
                        self.sct = None
                        self.monitor = None

            elapsed = time.time() - start_time
            sleep_time = max(0.001, interval - elapsed)
            await asyncio.sleep(sleep_time)

    async def start(self):
        self.running = True
        console.print(f"[SCREEN] GhostScreen server listening on ws://{self.host}:{self.port}")
        server = await websockets.serve(
            self.client_handler,
            self.host,
            self.port,
            max_size=10 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20
        )
        await asyncio.gather(server.wait_closed(), self.capture_and_broadcast_loop())

    def stop(self):
        self.running = False
