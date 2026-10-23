import os
import sys
import asyncio
import threading
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from rich.console import Console

from . import __version__
from .config import load_config, get_local_ip, wait_for_hotspot_ip, DEFAULT_STORAGE, PID_FILE
from .discovery import GhostDiscovery
from .drive_server import create_drive_routes
from .screen_server import GhostScreenServer
from .audio_server import GhostAudioServer

console = Console()


def create_app(cfg: dict) -> FastAPI:
    app = FastAPI(title="CPUGhost", docs_url=None, redoc_url=None)

    storage_dir = cfg.get("storage_dir") or str(DEFAULT_STORAGE)
    drive_router = create_drive_routes(storage_dir)
    app.include_router(drive_router)

    @app.get("/api/info")
    async def node_info():
        import socket
        return {
            "status": "online",
            "node_name": cfg.get("node_name", socket.gethostname()),
            "role": "host",
            "api_port": cfg.get("api_port", 8888),
            "stream_port": cfg.get("stream_port", 8889),
            "audio_port": cfg.get("audio_port", 8890),
            "discovery_port": cfg.get("discovery_port", 8989),
            "version": __version__
        }

    @app.get("/api/ping")
    async def ping():
        return {"status": "ok", "ping": "pong"}

    # Static web files
    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(str(web_dir / "index.html"))

        @app.get("/drive")
        async def drive_page():
            return FileResponse(str(web_dir / "index.html"))

    return app


class GhostHostServer:
    def __init__(self):
        self.cfg = load_config()
        self.api_port = self.cfg.get("api_port", 8888)
        self.stream_port = self.cfg.get("stream_port", 8889)
        self.audio_port = self.cfg.get("audio_port", 8890)
        self.discovery_port = self.cfg.get("discovery_port", 8989)
        self.local_ip = get_local_ip()

        self.discovery = GhostDiscovery(port=self.discovery_port)
        self.screen_server = GhostScreenServer(
            host="0.0.0.0",
            port=self.stream_port,
            fps=self.cfg.get("screen_fps", 30),
            quality=self.cfg.get("screen_quality", 80),
            scale=self.cfg.get("screen_scale", 1.0)
        )
        self.audio_server = GhostAudioServer(
            host="0.0.0.0",
            port=self.audio_port
        )
        self.app = create_app(self.cfg)
        self.running = False

    def start(self):
        # Prevent double-binding if host is already running locally
        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{self.api_port}/api/ping", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    console.print(f"[WARN] CPUGhost Host is already running on port {self.api_port}.")
                    return
        except Exception:
            pass

        self.running = True
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))

        # Ensure network IP is ready (critical for headless boot)
        if self.local_ip == "127.0.0.1":
            console.print("[WAIT] Waiting for phone hotspot Wi-Fi connection...")
            self.local_ip = wait_for_hotspot_ip(timeout=60.0)

        console.print(f"[HOST] Starting CPUGhost Host on {self.local_ip}")
        console.print(f"[HOST] HTTP API & Pen Drive: http://{self.local_ip}:{self.api_port}")
        console.print(f"[HOST] Screen Stream WebSocket: ws://{self.local_ip}:{self.stream_port}")
        console.print(f"[HOST] Audio Stream WebSocket: ws://{self.local_ip}:{self.audio_port}")
        console.print(f"[HOST] Hotspot Discovery Beacon: UDP {self.discovery_port}")

        # 1. Start discovery beacon
        self.discovery.start_broadcaster(roles=["host", "drive", "screen", "audio"])

        # 2. Start screen streaming server in separate thread
        def _run_screen():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.screen_server.start())

        screen_thread = threading.Thread(target=_run_screen, daemon=True)
        screen_thread.start()

        # 3. Start audio streaming server in separate thread
        def _run_audio():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.audio_server.start())

        audio_thread = threading.Thread(target=_run_audio, daemon=True)
        audio_thread.start()

        # 4. Start FastAPI server
        uvicorn_config = uvicorn.Config(
            app=self.app,
            host="0.0.0.0",
            port=self.api_port,
            log_level="warning"
        )
        server = uvicorn.Server(uvicorn_config)
        try:
            server.run()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.running = False
        self.discovery.stop()
        self.screen_server.stop()
        self.audio_server.stop()
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass
        console.print("[HOST] CPUGhost Host stopped.")


def run_host():
    host = GhostHostServer()
    host.start()


if __name__ == "__main__":
    run_host()
