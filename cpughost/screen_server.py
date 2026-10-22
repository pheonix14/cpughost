import io
import time
import json
import asyncio
from typing import Set
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


class GhostScreenServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8889, fps: int = 30, quality: int = 75, scale: float = 1.0):
        self.host = host
        self.port = port
        self.fps = fps
        self.quality = quality
        self.scale = scale
        self.running = False
        self.clients: Set[websockets.WebSocketServerProtocol] = set()

        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[1] if len(self.sct.monitors) > 1 else self.sct.monitors[0]

    def handle_input_event(self, data: dict):
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
                try:
                    # Grab screen frame
                    sct_img = self.sct.grab(self.monitor)
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

                    if self.scale != 1.0:
                        new_w = int(img.width * self.scale)
                        new_h = int(img.height * self.scale)
                        img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=self.quality, optimize=False)
                    frame_bytes = buf.getvalue()

                    # Broadcast to connected clients
                    websockets.broadcast(self.clients, frame_bytes)
                except Exception as e:
                    pass

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
