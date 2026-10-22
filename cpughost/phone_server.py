"""
CPUGhost - Dedicated Android Phone Host & Screen Service
Runs on the smartphone (Termux) to serve screen stream and virtual pen drive to PC zero.
Compatible with GhostScreenViewer on ws://<phone_ip>:8889 and GhostDrive on http://<phone_ip>:8888.
"""

import os
import io
import time
import json
import socket
import shutil
import asyncio
import threading
import subprocess
from pathlib import Path
from typing import Set, Optional
import websockets
from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

from .config import load_config, get_local_ip, get_broadcast_ip, get_arp_neighbors

console = Console()
BEACON_MAGIC = "CPUGHOST_BEACON_V1"


class PhoneScreenStreamer:
    """Captures and generates frames from Android phone screen for GhostScreenViewer."""
    def __init__(self, quality: int = 70, fps: int = 15):
        self.quality = quality
        self.fps = fps
        self.adb_available = False
        self.adb_bin = None
        self.phone_w = 1080
        self.phone_h = 2400
        self._check_adb()

    def _check_adb(self):
        adb_path = shutil.which("adb")
        if adb_path:
            self.adb_bin = adb_path
            res = subprocess.run([self.adb_bin, "devices"], capture_output=True, text=True)
            lines = [l for l in res.stdout.strip().splitlines()[1:] if l.strip()]
            self.adb_available = len(lines) > 0 and any("device" in l for l in lines)

    def capture_frame(self) -> bytes:
        """Capture screenshot via ADB if available, or generate live phone dashboard frame."""
        if self.adb_available and self.adb_bin:
            try:
                proc = subprocess.run(
                    [self.adb_bin, "exec-out", "screencap", "-p"],
                    capture_output=True,
                    timeout=1.5
                )
                if proc.returncode == 0 and proc.stdout and proc.stdout.startswith(b"\x89PNG"):
                    img = Image.open(io.BytesIO(proc.stdout)).convert("RGB")
                    # Scale down for smooth streaming
                    img.thumbnail((720, 1600), Image.BILINEAR)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=self.quality)
                    return buf.getvalue()
            except Exception:
                pass

        # Fallback: Live Phone Status Display Canvas
        return self._generate_status_canvas()

    def _generate_status_canvas(self) -> bytes:
        w, h = 720, 1280
        img = Image.new("RGB", (w, h), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Header bar
        draw.rectangle([(0, 0), (w, 100)], fill=(30, 41, 59))
        draw.text((30, 35), "CPUGHOST PHONE HOST", fill=(0, 215, 230))
        draw.text((w - 220, 35), time.strftime("%H:%M:%S"), fill=(148, 163, 184))

        # Status cards
        local_ip = get_local_ip()
        draw.rectangle([(40, 140), (w - 40, 320)], fill=(30, 41, 59), outline=(51, 65, 85), width=2)
        draw.text((60, 160), "CONNECTED PC ZERO ACTIVE", fill=(34, 197, 94))
        draw.text((60, 200), f"Phone IP: {local_ip}", fill=(255, 255, 255))
        draw.text((60, 240), "Screen Stream: ws://0.0.0.0:8889", fill=(148, 163, 184))
        draw.text((60, 275), "Virtual Pen Drive: http://0.0.0.0:8888", fill=(148, 163, 184))

        # Tip box
        draw.rectangle([(40, 360), (w - 40, 700)], fill=(24, 32, 47), outline=(0, 215, 230), width=2)
        draw.text((60, 385), "PHONE SCREEN MIRRORING INSTRUCTIONS", fill=(0, 215, 230))
        draw.text((60, 435), "1. On PC zero, run: ghost phone", fill=(255, 255, 255))
        draw.text((60, 485), "   (Launches 60fps hardware scrcpy window)", fill=(148, 163, 184))
        draw.text((60, 545), "2. Or enable Wireless Debugging on Phone:", fill=(255, 255, 255))
        draw.text((60, 595), "   Settings -> Developer Options -> Wireless Debugging", fill=(148, 163, 184))
        draw.text((60, 645), "   Then run 'ghost-phone --pair' in Termux", fill=(148, 163, 184))

        # Live pulsating activity meter
        sec = int(time.time()) % 10
        for i in range(10):
            color = (0, 215, 230) if i <= sec else (51, 65, 85)
            draw.rectangle([(60 + i * 60, 750), (100 + i * 60, 780)], fill=color)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()

    def handle_input(self, data: dict):
        """Pass input event to Android via ADB if available."""
        if not self.adb_available or not self.adb_bin:
            return

        ev_type = data.get("type")
        try:
            if ev_type == "mouse_down":
                x = int(float(data.get("x", 0.0)) * self.phone_w)
                y = int(float(data.get("y", 0.0)) * self.phone_h)
                btn = data.get("button", "left")
                if btn == "right":
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "4"], timeout=0.5)  # BACK
                elif btn == "middle":
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "3"], timeout=0.5)  # HOME
                else:
                    subprocess.run([self.adb_bin, "shell", "input", "tap", str(x), str(y)], timeout=0.5)
            elif ev_type == "key_down":
                key = str(data.get("key", ""))
                if key == "backspace":
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "67"], timeout=0.5)
                elif key == "return":
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "66"], timeout=0.5)
                elif len(key) == 1 and key.isprintable():
                    subprocess.run([self.adb_bin, "shell", "input", "text", key], timeout=0.5)
        except Exception:
            pass


class GhostPhoneHostService:
    def __init__(self, host: str = "0.0.0.0", port: int = 8889, api_port: int = 8888, fps: int = 15):
        self.host = host
        self.port = port
        self.api_port = api_port
        self.fps = fps
        self.running = False
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.streamer = PhoneScreenStreamer(fps=fps)
        self.storage_dir = Path.home() / "storage" / "downloads" if (Path.home() / "storage" / "downloads").exists() else Path.home()

    def start_beacon(self, target_ip: str = "10.176.124.73"):
        """Continuous UDP beacon so PC zero auto-discovers this phone."""
        def _beacon_loop():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            cfg = load_config()

            while self.running:
                try:
                    local_ip = get_local_ip()
                    payload = {
                        "magic": BEACON_MAGIC,
                        "node_name": "Samsung-Phone",
                        "node_ip": local_ip,
                        "api_port": self.api_port,
                        "stream_port": self.port,
                        "roles": ["host", "phone", "screen", "drive"],
                        "time": time.time()
                    }
                    data = json.dumps(payload).encode()
                    # Direct to PC zero
                    sock.sendto(data, (target_ip, 8989))
                    # Hotspot broadcast
                    bcast = get_broadcast_ip(local_ip)
                    sock.sendto(data, (bcast, 8989))
                except Exception:
                    pass
                time.sleep(1.2)
            sock.close()

        threading.Thread(target=_beacon_loop, daemon=True).start()

    async def broadcast_loop(self):
        interval = 1.0 / max(1, self.fps)
        while self.running:
            start = time.time()
            if self.clients:
                frame = self.streamer.capture_frame()
                if frame:
                    websockets.broadcast(list(self.clients), frame)
            elapsed = time.time() - start
            await asyncio.sleep(max(0.01, interval - elapsed))

    async def run(self):
        self.running = True
        self.start_beacon()
        console.print(f"[PHONE HOST] Screen server active on ws://{self.host}:{self.port}")
        console.print(f"[PHONE HOST] Broadcasting presence to PC zero (10.176.124.73:8989)...")

        async def handler(websocket):
            self.clients.add(websocket)
            console.print(f"[PHONE HOST] PC Zero viewer connected from {websocket.remote_address}!")
            try:
                async for msg in websocket:
                    try:
                        ev = json.loads(msg)
                        self.streamer.handle_input(ev)
                    except Exception:
                        pass
            finally:
                self.clients.discard(websocket)
                console.print("[PHONE HOST] PC Zero viewer disconnected.")

        self._server = await websockets.serve(handler, self.host, self.port)
        await asyncio.gather(self._server.wait_closed(), self.broadcast_loop())

    def stop(self):
        self.running = False
        if hasattr(self, "_server") and self._server:
            self._server.close()


def run_phone_host_service():
    """CLI entrypoint to launch phone host service."""
    srv = GhostPhoneHostService()
    try:
        asyncio.run(srv.run())
    except KeyboardInterrupt:
        srv.stop()
        console.print("\n[PHONE HOST] Service stopped.")
