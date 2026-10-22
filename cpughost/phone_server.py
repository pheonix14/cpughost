"""
CPUGhost - Phone Host Service for PC Zero
Provides:
1. Virtual Pen Drive HTTP API & Web Dashboard on port 8888:
   - Live Screen Viewer (MJPEG stream & Canvas)
   - Virtual Pen Drive File Manager (/api/drive/status, /ls, /download, /upload)
   - System Diagnostics & Screen Metadata (/api/info, /api/screens, /api/ping)
   - Wireless ADB Pairing Interface (/api/adb/pair, /api/adb/connect)
2. Low-Latency Binary Screen WebSocket Stream on port 8889 (for GhostScreenViewer)
3. Audio WebSocket Server on port 8890 (for GhostScreenViewer audio sync)
4. Hotspot UDP Discovery Beacon on port 8989 directed to PC zero (10.176.124.73)
"""

import os
import io
import time
import json
import socket
import shutil
import urllib.parse
import asyncio
import threading
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Set, Optional, Dict, Any
import websockets
from PIL import Image, ImageDraw
from rich.console import Console

from .config import load_config, get_local_ip, get_broadcast_ip, get_arp_neighbors

console = Console()
BEACON_MAGIC = "CPUGHOST_BEACON_V1"
STORAGE_DIR = Path.home() / "storage" / "downloads" if (Path.home() / "storage" / "downloads").exists() else Path.home() / "downloads"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# Global reference to active host service for HTTP handler access
_CURRENT_SERVICE: Optional["GhostPhoneHostService"] = None


class PhoneScreenStreamer:
    """Captures and generates frames from Android phone screen for GhostScreenViewer and Web UI."""
    def __init__(self, quality: int = 70, fps: int = 20):
        self.quality = quality
        self.fps = fps
        self.phone_w = 720
        self.phone_h = 1280
        self.adb_bin = shutil.which("adb")
        self.adb_available = False
        self._last_frame_bytes: Optional[bytes] = None
        self._last_frame_time: float = 0.0
        self._check_adb()

    def _check_adb(self):
        adb_path = shutil.which("adb")
        if adb_path:
            self.adb_bin = adb_path
            try:
                subprocess.run([self.adb_bin, "connect", "127.0.0.1:5555"], capture_output=True, timeout=2.0)
            except Exception:
                pass
            res = subprocess.run([self.adb_bin, "devices"], capture_output=True, text=True)
            lines = [l for l in res.stdout.strip().splitlines()[1:] if l.strip()]
            self.adb_available = len(lines) > 0 and any("device" in l for l in lines)

    def capture_frame(self) -> bytes:
        """Capture real phone frame using native screencap or ADB."""
        now = time.time()
        if self._last_frame_bytes and (now - self._last_frame_time) < (1.0 / (self.fps * 1.5)):
            return self._last_frame_bytes

        if not self.adb_available:
            self._check_adb()

        if self.adb_available and self.adb_bin:
            try:
                proc = subprocess.run(
                    [self.adb_bin, "exec-out", "screencap", "-p"],
                    capture_output=True,
                    timeout=2.5
                )
                if proc.returncode == 0 and proc.stdout and proc.stdout.startswith(b"\x89PNG"):
                    img = Image.open(io.BytesIO(proc.stdout)).convert("RGB")
                    img.thumbnail((720, 1600), Image.BILINEAR)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=self.quality)
                    frame = buf.getvalue()
                    self._last_frame_bytes = frame
                    self._last_frame_time = now
                    return frame
            except Exception:
                self._check_adb()

        # Try root screencap if available
        try:
            proc = subprocess.run(["su", "-c", "screencap", "-p"], capture_output=True, timeout=2.0)
            if proc.returncode == 0 and proc.stdout and proc.stdout.startswith(b"\x89PNG"):
                img = Image.open(io.BytesIO(proc.stdout)).convert("RGB")
                img.thumbnail((720, 1600), Image.BILINEAR)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=self.quality)
                frame = buf.getvalue()
                self._last_frame_bytes = frame
                self._last_frame_time = now
                return frame
        except Exception:
            pass

        if self._last_frame_bytes:
            return self._last_frame_bytes

        # Fallback: Clean minimalist standby frame
        w, h = 720, 1280
        img = Image.new("RGB", (w, h), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)
        draw.text((w // 2 - 100, h // 2 - 10), "Connecting Caesar Phone...", fill=(0, 215, 230))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        frame_data = buf.getvalue()
        self._last_frame_bytes = frame_data
        self._last_frame_time = now
        return frame_data
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()

    def handle_input(self, data: dict):
        """Pass input event to Android via ADB if available."""
        if not self.adb_available or not self.adb_bin:
            return

        ev_type = data.get("type")
        try:
            if ev_type in ("mouse_down", "click"):
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
                key = str(data.get("key", "")).lower()
                if key in ("backspace", "back"):
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "67"], timeout=0.5)
                elif key in ("return", "enter"):
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "66"], timeout=0.5)
                elif key == "home":
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "3"], timeout=0.5)
                elif key == "app_switch":
                    subprocess.run([self.adb_bin, "shell", "input", "keyevent", "187"], timeout=0.5)
                elif len(key) == 1 and key.isprintable():
                    subprocess.run([self.adb_bin, "shell", "input", "text", key], timeout=0.5)
        except Exception:
            pass


class PhoneDriveHandler(BaseHTTPRequestHandler):
    """Zero-dependency HTTP Handler for Virtual Pen Drive, Live MJPEG Screen & Web UI."""
    def log_message(self, format, *args):
        console.print(f"[HTTP] {self.client_address[0]} - {format % args}")

    def _send_json(self, data: dict, code: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. API System Information & Diagnostics
        if path == "/api/info":
            self._send_json({
                "status": "online",
                "node_name": "Samsung-Phone",
                "role": "host",
                "api_port": 8888,
                "stream_port": 8889,
                "audio_port": 8890,
                "discovery_port": 8989,
                "version": "7.4.0",
                "storage_dir": str(STORAGE_DIR)
            })
            return

        if path == "/api/ping":
            self._send_json({"status": "ok", "ping": "pong"})
            return

        # 2. Screens Catalog (required by PC zero ghost cli & viewer)
        if path == "/api/screens":
            self._send_json({
                "screens": [
                    {
                        "id": 1,
                        "name": "Samsung Phone Display (Caesar)",
                        "width": 720,
                        "height": 1280,
                        "active": True,
                        "is_phone": True
                    }
                ],
                "active_screen": 1
            })
            return

        # 3. Live Screen Capture (Single JPEG frame snapshot)
        if path == "/api/frame":
            frame = _CURRENT_SERVICE.streamer.capture_frame() if _CURRENT_SERVICE else b""
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(frame)
            return

        # 4. Real-time Live Screen MJPEG Stream (For any web browser or video player)
        if path in ("/stream.mjpg", "/stream.mjpg/", "/api/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()

            streamer = _CURRENT_SERVICE.streamer if _CURRENT_SERVICE else PhoneScreenStreamer()
            try:
                while True:
                    frame = streamer.capture_frame()
                    header = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(frame)).encode("utf-8") + b"\r\n\r\n"
                    )
                    self.wfile.write(header)
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    time.sleep(1.0 / max(1, streamer.fps))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # 5. Virtual Pen Drive endpoints
        if path == "/api/drive/status":
            stat = shutil.disk_usage(STORAGE_DIR)
            self._send_json({
                "status": "online",
                "storage_dir": str(STORAGE_DIR),
                "mount_point": str(STORAGE_DIR),
                "free_space_gb": round(stat.free / (1024 ** 3), 2),
                "total_files": len(list(STORAGE_DIR.glob("*")))
            })
            return

        if path == "/api/drive/ls":
            sub_path = query.get("path", [""])[0].strip("/")
            target = (STORAGE_DIR / sub_path).resolve()
            items = []
            if target.exists() and target.is_dir():
                for p in sorted(target.iterdir()):
                    try:
                        st = p.stat()
                        items.append({
                            "name": p.name,
                            "type": "directory" if p.is_dir() else "file",
                            "size": st.st_size if p.is_file() else 0,
                            "modified": st.st_mtime
                        })
                    except Exception:
                        pass
            self._send_json({"items": items, "path": sub_path})
            return

        if path == "/api/drive/download":
            file_name = query.get("path", [""])[0].strip("/")
            target = (STORAGE_DIR / file_name).resolve()
            if target.exists() and target.is_file():
                try:
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception:
                    pass
            self.send_response(404)
            self.end_headers()
            return

        # 6. Interactive Web UI Dashboard
        if path in ("/", "/index.html", "/drive", "/screen"):
            local_ip = get_local_ip()
            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CPUGhost Phone - PC Zero Live Console</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ background: #0b0f19; color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; display: flex; flex-direction: column; min-height: 100vh; }}
        header {{ background: #111827; border-bottom: 1px solid #1f2937; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; }}
        .brand {{ display: flex; align-items: center; gap: 10px; font-weight: bold; font-size: 1.15rem; color: #00d7e6; }}
        .badge {{ background: #22c55e; color: #000; font-size: 0.75rem; padding: 3px 8px; border-radius: 9999px; font-weight: 700; }}
        .main-container {{ display: flex; flex: 1; padding: 24px; gap: 24px; max-width: 1400px; margin: 0 auto; width: 100%; }}
        .screen-col {{ flex: 1.2; display: flex; flex-direction: column; align-items: center; }}
        .drive-col {{ flex: 1; display: flex; flex-direction: column; gap: 20px; }}
        .card {{ background: #161f30; border: 1px solid #243046; border-radius: 12px; padding: 20px; width: 100%; }}
        .card-title {{ font-size: 1rem; color: #00d7e6; margin-bottom: 14px; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }}
        .screen-wrapper {{ position: relative; border-radius: 16px; overflow: hidden; border: 3px solid #00d7e6; box-shadow: 0 0 25px rgba(0, 215, 230, 0.2); max-width: 360px; width: 100%; cursor: pointer; }}
        .screen-img {{ display: block; width: 100%; height: auto; }}
        .btn-group {{ display: flex; gap: 10px; margin-top: 14px; justify-content: center; width: 100%; max-width: 360px; }}
        .btn {{ background: #1e293b; color: #f8fafc; border: 1px solid #334155; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; transition: 0.15s; }}
        .btn:hover {{ background: #00d7e6; color: #000; border-color: #00d7e6; }}
        .btn-primary {{ background: #00d7e6; color: #000; font-weight: 600; border: none; }}
        .file-list {{ list-style: none; max-height: 280px; overflow-y: auto; margin-top: 10px; }}
        .file-item {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; border-bottom: 1px solid #1f2937; font-size: 0.85rem; }}
        .file-item a {{ color: #38bdf8; text-decoration: none; }}
        .file-item a:hover {{ text-decoration: underline; }}
        input[type="text"], input[type="file"] {{ width: 100%; padding: 8px 12px; border-radius: 6px; border: 1px solid #334155; background: #0f172a; color: #fff; font-size: 0.85rem; margin-top: 6px; }}
        .status-pill {{ font-size: 0.8rem; color: #94a3b8; display: flex; align-items: center; gap: 6px; }}
        .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #22c55e; display: inline-block; }}
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <span>CPUGHOST</span>
            <span class="badge">ONLINE</span>
        </div>
        <div class="status-pill">
            <span class="dot"></span>
            <span>Target Node: <b>zero (10.176.124.73)</b> | Phone: <b>{local_ip}</b></span>
        </div>
    </header>

    <div class="main-container">
        <!-- Live Phone Screen Column -->
        <div class="screen-col">
            <div class="card-title" style="width: 100%; max-width: 360px;">
                <span>Live Phone Display</span>
                <span style="font-size: 0.8rem; color: #94a3b8;">Stream: 30 FPS</span>
            </div>
            <div class="screen-wrapper" id="screenBox">
                <img id="liveStream" class="screen-img" src="/stream.mjpg" alt="Live Phone Screen" />
            </div>
            <div class="btn-group">
                <button class="btn" onclick="sendInput('key', 'back')">[Back]</button>
                <button class="btn" onclick="sendInput('key', 'home')">[Home]</button>
                <button class="btn" onclick="sendInput('key', 'app_switch')">[Apps]</button>
                <button class="btn" onclick="toggleFullscreen()">[Fullscreen]</button>
            </div>
            <p style="font-size: 0.75rem; color: #64748b; margin-top: 8px;">Tap / Click on screen to interact directly with phone</p>
        </div>

        <!-- Drive & Controls Column -->
        <div class="drive-col">
            <!-- Virtual Pen Drive Card -->
            <div class="card">
                <div class="card-title">
                    <span>Virtual Pen Drive</span>
                    <button class="btn" onclick="loadFiles()">⟳ Refresh</button>
                </div>
                <p style="font-size: 0.8rem; color: #94a3b8; margin-bottom: 8px;">Directory: <code style="color:#00d7e6">{STORAGE_DIR}</code></p>
                <ul class="file-list" id="filesList">
                    <li class="file-item">Loading files...</li>
                </ul>
                <div style="margin-top: 14px;">
                    <label style="font-size: 0.8rem; color: #94a3b8;">Upload File to Phone:</label>
                    <input type="file" id="fileUpload" onchange="uploadSelectedFile()" />
                </div>
            </div>

            <!-- Wireless Debugging / Pairing Card -->
            <div class="card">
                <div class="card-title">
                    <span>Android Wireless Mirroring</span>
                </div>
                <p style="font-size: 0.8rem; color: #94a3b8; line-height: 1.4;">
                    To enable 60 FPS live Android OS mirroring, open <b>Settings &rarr; Developer Options &rarr; Wireless Debugging &rarr; Pair with code</b>:
                </p>
                <div style="display: flex; gap: 8px; margin-top: 10px;">
                    <input type="text" id="pairPort" placeholder="Pairing Port (e.g. 38451)" style="flex: 1;" />
                    <input type="text" id="pairCode" placeholder="6-digit Code" style="flex: 1;" />
                    <button class="btn btn-primary" onclick="pairWirelessAdb()">Pair</button>
                </div>
                <div id="pairResult" style="font-size: 0.8rem; margin-top: 8px; color: #38bdf8;"></div>
            </div>
        </div>
    </div>

    <script>
        const screenBox = document.getElementById('screenBox');
        const liveStream = document.getElementById('liveStream');

        // Click-to-touch interaction
        screenBox.addEventListener('click', (e) => {{
            const rect = liveStream.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;
            if (x >= 0 && x <= 1 && y >= 0 && y <= 1) {{
                fetch('/api/input', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ type: 'mouse_down', x: x, y: y, button: 'left' }})
                }}).catch(() => {{}});
            }}
        }});

        function sendInput(type, val) {{
            if (type === 'key') {{
                fetch('/api/input', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ type: 'key_down', key: val }})
                }}).catch(() => {{}});
            }}
        }}

        function toggleFullscreen() {{
            if (!document.fullscreenElement) {{
                screenBox.requestFullscreen().catch(() => {{}});
            }} else {{
                document.exitFullscreen().catch(() => {{}});
            }}
        }}

        function loadFiles() {{
            fetch('/api/drive/ls')
                .then(r => r.json())
                .then(data => {{
                    const list = document.getElementById('filesList');
                    list.innerHTML = '';
                    if (!data.items || data.items.length === 0) {{
                        list.innerHTML = '<li class="file-item" style="color: #64748b;">No files in drive yet</li>';
                        return;
                    }}
                    data.items.forEach(item => {{
                        const li = document.createElement('li');
                        li.className = 'file-item';
                        const sz = item.type === 'directory' ? 'DIR' : (item.size / 1024).toFixed(1) + ' KB';
                        li.innerHTML = `
                            <span>${{item.name}} <small style="color: #64748b;">(${{sz}})</small></span>
                            ${{item.type === 'file' ? `<a href="/api/drive/download?path=${{encodeURIComponent(item.name)}}" download>Download</a>` : ''}}
                        `;
                        list.appendChild(li);
                    }});
                }})
                .catch(() => {{}});
        }}

        function uploadSelectedFile() {{
            const fileInput = document.getElementById('fileUpload');
            if (!fileInput.files.length) return;
            const file = fileInput.files[0];
            fetch('/api/drive/upload?name=' + encodeURIComponent(file.name), {{
                method: 'POST',
                body: file
            }}).then(() => {{
                fileInput.value = '';
                loadFiles();
            }}).catch(e => alert('Upload failed: ' + e));
        }}

        function pairWirelessAdb() {{
            const port = document.getElementById('pairPort').value.trim();
            const code = document.getElementById('pairCode').value.trim();
            const out = document.getElementById('pairResult');
            out.innerText = 'Pairing with Android...';
            fetch('/api/adb/pair', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ port: port, code: code }})
            }})
            .then(r => r.json())
            .then(res => {{
                out.innerText = res.message || res.status;
            }})
            .catch(e => {{ out.innerText = 'Error: ' + e; }});
        }}

        // Initial files load
        loadFiles();
    </script>
</body>
</html>"""
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Remote input event forwarding (clicks, keys)
        if path == "/api/input":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                ev = json.loads(body.decode("utf-8"))
                if _CURRENT_SERVICE:
                    _CURRENT_SERVICE.streamer.handle_input(ev)
                self._send_json({"status": "ok"})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 400)
            return

        # 2. Virtual Pen Drive file upload
        if path == "/api/drive/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            file_name = query.get("name", ["upload.bin"])[0]
            target_path = STORAGE_DIR / Path(file_name).name
            if content_length > 0:
                with open(target_path, "wb") as f:
                    remaining = content_length
                    while remaining > 0:
                        chunk = self.rfile.read(min(remaining, 65536))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                self._send_json({"status": "uploaded", "file": target_path.name, "size": content_length})
                return
            self._send_json({"status": "empty"}, 400)
            return

        # 3. Wireless ADB Pairing
        if path == "/api/adb/pair":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
                port = data.get("port")
                code = data.get("code")
                if not port or not code:
                    self._send_json({"status": "error", "message": "Missing port or code"}, 400)
                    return

                res = subprocess.run(["adb", "pair", f"127.0.0.1:{port}", str(code)], capture_output=True, text=True, timeout=5.0)
                out = (res.stdout + " " + res.stderr).strip()
                self._send_json({"status": "ok" if res.returncode == 0 else "failed", "message": out})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return

        # 4. Wireless ADB Connect
        if path == "/api/adb/connect":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
                port = data.get("port", 36579)
                res = subprocess.run(["adb", "connect", f"127.0.0.1:{port}"], capture_output=True, text=True, timeout=5.0)
                out = (res.stdout + " " + res.stderr).strip()
                # If connected, enable global port 5555
                if "connected" in out.lower():
                    subprocess.run(["adb", "tcpip", "5555"], capture_output=True, timeout=2.0)
                self._send_json({"status": "ok" if res.returncode == 0 else "failed", "message": out})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
            return

        self.send_response(404)
        self.end_headers()


class GhostPhoneHostService:
    def __init__(self, host: str = "0.0.0.0", port: int = 8889, api_port: int = 8888, audio_port: int = 8890, fps: int = 20):
        self.host = host
        self.port = port
        self.api_port = api_port
        self.audio_port = audio_port
        self.fps = fps
        self.running = False
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.audio_clients: Set[websockets.WebSocketServerProtocol] = set()
        self.streamer = PhoneScreenStreamer(fps=fps)
        self.http_server: Optional[HTTPServer] = None
        self._server = None
        self._audio_server = None

    def start_http_server(self):
        """Run Virtual Pen Drive and Web UI HTTP server on port 8888."""
        global _CURRENT_SERVICE
        _CURRENT_SERVICE = self
        try:
            self.http_server = HTTPServer((self.host, self.api_port), PhoneDriveHandler)
            def _http_loop():
                self.http_server.serve_forever()
            threading.Thread(target=_http_loop, daemon=True).start()
            console.print(f"[PHONE HOST] Virtual Pen Drive & Web UI active on http://0.0.0.0:{self.api_port}")
        except Exception as e:
            console.print(f"[WARN] HTTP Server on port {self.api_port}: {e}")

    def start_beacon(self, target_ip: str = "10.176.124.73"):
        """Continuous UDP beacon so PC zero auto-discovers this phone."""
        def _beacon_loop():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            while self.running:
                try:
                    local_ip = get_local_ip()
                    payload = {
                        "magic": BEACON_MAGIC,
                        "node_name": "Samsung-Phone",
                        "node_ip": local_ip,
                        "api_port": self.api_port,
                        "stream_port": self.port,
                        "audio_port": self.audio_port,
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
                    dead = set()
                    for ws in list(self.clients):
                        try:
                            await ws.send(frame)
                        except Exception:
                            dead.add(ws)
                    self.clients.difference_update(dead)
            elapsed = time.time() - start
            await asyncio.sleep(max(0.01, interval - elapsed))

    async def run(self):
        self.running = True
        self.start_http_server()
        self.start_beacon()
        console.print(f"[PHONE HOST] Screen server active on ws://{self.host}:{self.port}")
        console.print(f"[PHONE HOST] Audio server active on ws://{self.host}:{self.audio_port}")
        console.print(f"[PHONE HOST] Broadcasting presence to PC zero (10.176.124.73:8989)...")

        async def handler(websocket):
            self.clients.add(websocket)
            console.print(f"[PHONE HOST] PC Zero connected from {websocket.remote_address}!")
            try:
                async for msg in websocket:
                    try:
                        ev = json.loads(msg)
                        self.streamer.handle_input(ev)
                    except Exception:
                        pass
            except websockets.exceptions.ConnectionClosed:
                pass
            finally:
                self.clients.discard(websocket)
                console.print("[PHONE HOST] PC Zero disconnected.")

        async def audio_handler(websocket):
            self.audio_clients.add(websocket)
            try:
                async for _ in websocket:
                    pass
            except websockets.exceptions.ConnectionClosed:
                pass
            finally:
                self.audio_clients.discard(websocket)

        self._server = await websockets.serve(handler, self.host, self.port)
        self._audio_server = await websockets.serve(audio_handler, self.host, self.audio_port)
        await asyncio.gather(
            self._server.wait_closed(),
            self._audio_server.wait_closed(),
            self.broadcast_loop()
        )

    def stop(self):
        self.running = False
        if hasattr(self, "_server") and self._server:
            self._server.close()
        if hasattr(self, "_audio_server") and self._audio_server:
            self._audio_server.close()
        if self.http_server:
            self.http_server.shutdown()


def run_phone_host_service():
    """CLI entrypoint to launch phone host service."""
    srv = GhostPhoneHostService()
    try:
        asyncio.run(srv.run())
    except KeyboardInterrupt:
        srv.stop()
        console.print("\n[PHONE HOST] Service stopped.")


if __name__ == "__main__":
    run_phone_host_service()
