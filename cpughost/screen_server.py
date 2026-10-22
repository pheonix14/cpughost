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
    "super": Key.cmd,
    "menu": Key.menu,
    "print_screen": Key.print_screen,
    "insert": Key.insert,
    "pause": Key.pause,
    "num_lock": Key.num_lock,
    "scroll_lock": Key.scroll_lock,
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
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    "kp_0": "0", "kp_1": "1", "kp_2": "2", "kp_3": "3", "kp_4": "4",
    "kp_5": "5", "kp_6": "6", "kp_7": "7", "kp_8": "8", "kp_9": "9"
}


import glob

def find_xauthority() -> Optional[str]:
    """Auto-discover valid Xauthority file for headless/daemon processes."""
    existing = os.environ.get("XAUTHORITY")
    if existing and os.path.exists(existing) and os.path.getsize(existing) > 0:
        return existing

    uid = os.getuid()
    # Check candidate paths in /run/user/<uid>
    candidates = (
        glob.glob(f"/run/user/{uid}/.mutter-Xwaylandauth.*") +
        glob.glob(f"/run/user/{uid}/gdm/Xauthority*") +
        glob.glob(f"/run/user/{uid}/*xauth*") +
        [os.path.expanduser("~/.Xauthority")]
    )
    for c in candidates:
        if "ICE" not in c and os.path.exists(c) and os.path.getsize(c) > 0:
            return c

    # Search running desktop processes for XAUTHORITY
    for proc_env in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(proc_env, "rb") as f:
                data = f.read().split(b"\0")
                for item in data:
                    if item.startswith(b"XAUTHORITY="):
                        path = item.split(b"=", 1)[1].decode("utf-8", errors="ignore")
                        if os.path.exists(path) and os.access(path, os.R_OK):
                            return path
        except (PermissionError, FileNotFoundError):
            continue
    return None


def ensure_display_environment(resolution: str = "1920x1080"):
    """Ensure valid DISPLAY and XAUTHORITY environment variables exist."""
    # 1. Ensure XAUTHORITY is set
    auth_file = find_xauthority()
    if auth_file:
        os.environ["XAUTHORITY"] = auth_file

    # 2. Ensure DISPLAY is set
    if not os.environ.get("DISPLAY"):
        # Check active processes for DISPLAY
        found_disp = None
        for proc_env in glob.glob("/proc/[0-9]*/environ"):
            try:
                with open(proc_env, "rb") as f:
                    data = f.read().split(b"\0")
                    for item in data:
                        if item.startswith(b"DISPLAY="):
                            val = item.split(b"=", 1)[1].decode("utf-8", errors="ignore")
                            if val.startswith(":"):
                                found_disp = val
                                break
                if found_disp:
                    break
            except (PermissionError, FileNotFoundError):
                continue

        if found_disp:
            os.environ["DISPLAY"] = found_disp
        else:
            # Check standard active X11 sockets
            for num in range(5):
                sock = f"/tmp/.X11-unix/X{num}"
                if os.path.exists(sock):
                    os.environ["DISPLAY"] = f":{num}"
                    break

        if not os.environ.get("DISPLAY"):
            # If no display found and Xvfb is available, spawn virtual framebuffer
            xvfb_bin = shutil.which("Xvfb")
            if xvfb_bin:
                try:
                    res_spec = f"{resolution}x24"
                    subprocess.Popen(
                        [xvfb_bin, ":99", "-screen", "0", res_spec, "-nocursor"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=os.setpgrp
                    )
                    time.sleep(0.5)
                    os.environ["DISPLAY"] = ":99"
                    console.print(f"[SCREEN] Spawned virtual monitor on display :99 ({resolution} Xvfb)")
                except Exception:
                    os.environ["DISPLAY"] = ":0"
            else:
                os.environ["DISPLAY"] = ":0"

    # 3. Disable DPMS screen blanking / sleep timeouts on X server
    try:
        subprocess.run(["xset", "-dpms"], capture_output=True, timeout=1)
        subprocess.run(["xset", "s", "off"], capture_output=True, timeout=1)
        subprocess.run(["xset", "s", "noblank"], capture_output=True, timeout=1)
    except Exception:
        pass


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
                    except Exception as e:
                        now = time.time()
                        if now - getattr(self, "_last_grab_err_time", 0) > 3.0:
                            console.print(f"[WARN] Screen capture error: {e}")
                            self._last_grab_err_time = now
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
