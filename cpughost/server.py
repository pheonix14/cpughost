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

from .config import load_config, get_local_ip
from .discovery import GhostDiscovery
from .drive_server import create_drive_routes
from .screen_server import GhostScreenServer

console = Console()


def create_app(cfg: dict) -> FastAPI:
    app = FastAPI(title="CPUGhost", docs_url=None, redoc_url=None)

    storage_dir = cfg.get("storage_dir", str(Path.home() / "Desktop" / "cpughost" / "storage"))
    drive_router = create_drive_routes(storage_dir)
    app.include_router(drive_router)

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
        self.app = create_app(self.cfg)
        self.running = False

    def start(self):
        self.running = True
        console.print(f"[HOST] Starting CPUGhost Host on {self.local_ip}")
        console.print(f"[HOST] HTTP API & Pen Drive: http://{self.local_ip}:{self.api_port}")
        console.print(f"[HOST] Screen Stream WebSocket: ws://{self.local_ip}:{self.stream_port}")
        console.print(f"[HOST] Hotspot Discovery Beacon: UDP {self.discovery_port}")

        # 1. Start discovery beacon
        self.discovery.start_broadcaster(roles=["host", "drive", "screen"])

        # 2. Start screen streaming server in separate thread
        def _run_screen():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.screen_server.start())

        screen_thread = threading.Thread(target=_run_screen, daemon=True)
        screen_thread.start()

        # 3. Start FastAPI server
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
        console.print("[HOST] CPUGhost Host stopped.")


def run_host():
    host = GhostHostServer()
    host.start()


if __name__ == "__main__":
    run_host()
