import os
import json
import socket
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "cpughost"
CONFIG_FILE = CONFIG_DIR / "config.json"

POINTER_COLORS = {
    "zorin_cyan": {"name": "Zorin Cyan", "rgb": (0, 215, 230)},
    "zorin_blue": {"name": "Zorin Blue", "rgb": (30, 144, 255)},
    "zorin_green": {"name": "Zorin Green", "rgb": (46, 204, 113)},
    "zorin_orange": {"name": "Zorin Orange", "rgb": (255, 128, 0)},
    "zorin_red": {"name": "Zorin Red", "rgb": (255, 70, 70)},
    "zorin_purple": {"name": "Zorin Purple", "rgb": (175, 85, 255)},
    "neon_yellow": {"name": "Neon Yellow", "rgb": (255, 230, 0)},
    "hot_pink": {"name": "Cyberpunk Pink", "rgb": (255, 75, 180)},
    "white": {"name": "Classic White", "rgb": (255, 255, 255)},
}

DEFAULT_CONFIG = {
    "node_name": socket.gethostname(),
    "discovery_port": 8989,
    "api_port": 8888,
    "stream_port": 8889,
    "screen_fps": 30,
    "screen_quality": 80,
    "screen_scale": 1.0,
    "storage_dir": str(Path.home() / "Desktop" / "cpughost" / "storage"),
    "mount_point": str(Path.home() / "Desktop" / "GhostDrive"),
    "last_peer_ip": "",
    "auto_mount": True,
    "headless_display": ":0",
    "screen_resolution": "1920x1080",
    "pointer_color": "zorin_cyan",
    "pointer_scale": 1.2,
    "pointer_click_effect": True
}


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge with defaults
        merged = DEFAULT_CONFIG.copy()
        merged.update(data)
        return merged
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)


def get_local_ip() -> str:
    """Detect local LAN/hotspot IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to public DNS to determine default route interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def wait_for_hotspot_ip(timeout: float = 60.0, poll_interval: float = 2.0) -> str:
    """
    Poll until a valid non-loopback IP is assigned by the phone hotspot.
    Ensures safe, error-free startup when booted headlessly.
    """
    import time
    start = time.time()
    while time.time() - start < timeout:
        ip = get_local_ip()
        if ip != "127.0.0.1":
            return ip
        time.sleep(poll_interval)
    return get_local_ip()


def get_broadcast_ip(local_ip: str) -> str:
    """Calculate broadcast address for /24 hotspot subnet."""
    parts = local_ip.split(".")
    if len(parts) == 4 and parts[0] != "127":
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"
