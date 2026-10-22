import os
import json
import socket
import subprocess
import shutil
import re
from pathlib import Path
from typing import List, Optional

CONFIG_DIR = Path.home() / ".config" / "cpughost"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STORAGE = PROJECT_DIR / "storage" if (PROJECT_DIR / "storage").exists() else Path.home() / "GhostStorage"

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
    "audio_port": 8890,
    "screen_fps": 30,
    "screen_quality": 80,
    "screen_scale": 1.0,
    "storage_dir": str(DEFAULT_STORAGE),
    "mount_point": str(Path.home() / "Desktop" / "GhostDrive"),
    "last_peer_ip": "",
    "auto_mount": True,
    "headless_display": ":0",
    "screen_resolution": "1920x1080",
    "screen_zoom": 1.0,
    "force_16_9": False,
    "pointer_color": "zorin_cyan",
    "pointer_scale": 1.2,
    "pointer_click_effect": True
}


def load_config() -> dict:
    """Load configuration from ~/.config/cpughost/config.json with fallback to defaults."""
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        merged = DEFAULT_CONFIG.copy()
        merged.update(cfg)
        return merged
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    """Save configuration dictionary to ~/.config/cpughost/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def is_android() -> bool:
    """Check if executing inside Android or Termux environment."""
    return bool(
        os.environ.get("TERMUX_VERSION")
        or os.environ.get("ANDROID_ROOT")
        or os.path.exists("/system/build.prop")
    )


def get_default_gateway_ip() -> Optional[str]:
    """Retrieve the default gateway IPv4 address (host phone IP on hotspot)."""
    try:
        with open("/proc/net/route", "r") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                if len(fields) >= 3 and fields[1] == "00000000":  # Default route
                    gw_hex = fields[2]
                    gw_ip = socket.inet_ntoa(bytes.fromhex(gw_hex)[::-1])
                    if gw_ip != "0.0.0.0":
                        return gw_ip
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default"],
            text=True,
            stderr=subprocess.DEVNULL
        )
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass

    return None


def get_local_ip() -> str:
    """
    Detect local LAN/hotspot IP address resiliently.
    Works even if hotspot has NO internet data access.
    Prioritizes Wi-Fi/Hotspot/Ethernet interfaces over VPN/tunnels.
    """
    # Strategy 0: Direct interface scan (prefers swlan, wlan, ap, eth, en)
    try:
        if shutil.which("ifconfig"):
            out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
            current_iface = None
            for line in out.splitlines():
                m_iface = re.match(r"^([a-zA-Z0-9_-]+):", line)
                if m_iface:
                    current_iface = m_iface.group(1)
                elif current_iface and any(current_iface.startswith(p) for p in ["swlan", "wlan", "ap", "eth", "en"]):
                    m_ip = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", line)
                    if m_ip:
                        ip = m_ip.group(1)
                        if not ip.startswith("127."):
                            return ip
    except Exception:
        pass

    # Strategy 1: Connect to public DNS via UDP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Strategy 2: Determine default gateway from /proc/net/route and connect to it
    gw_ip = get_default_gateway_ip()
    if gw_ip:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((gw_ip, 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Strategy 3: Query Linux ip command for global IPv4 address
    try:
        ip_bin = shutil.which("ip") or "/system/bin/ip"
        out = subprocess.check_output(
            [ip_bin, "-4", "-o", "addr", "show", "scope", "global"],
            text=True,
            stderr=subprocess.DEVNULL
        )
        for line in out.strip().splitlines():
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/\d+", line)
            if m:
                ip = m.group(1)
                if not ip.startswith("127.") and not ip.startswith("169.254."):
                    return ip
    except Exception:
        pass

    # Strategy 4: Fallback to socket hostname
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "127.0.0.1"


def wait_for_hotspot_ip(timeout: float = 60.0, poll_interval: float = 2.0) -> str:
    """Block until a valid non-loopback IP address is assigned by the hotspot."""
    import time
    start = time.time()
    while time.time() - start < timeout:
        ip = get_local_ip()
        if ip != "127.0.0.1":
            return ip
        time.sleep(poll_interval)
    return "127.0.0.1"


def get_broadcast_ip(local_ip: str) -> str:
    """Calculate broadcast address for /24 hotspot subnet."""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            text=True,
            stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if local_ip in line and "brd" in line:
                m = re.search(r"brd\s+(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    return m.group(1)
    except Exception:
        pass

    parts = local_ip.split(".")
    if len(parts) == 4 and parts[0] != "127":
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"


def get_arp_neighbors() -> List[str]:
    """
    Find active peer IPv4 addresses in ARP table and neighbor cache.
    Allows unicast UDP beaconing directly to peers to bypass hotspot AP isolation.
    """
    neighbors = set()
    local_ip = get_local_ip()

    def is_valid_ipv4(ip: str) -> bool:
        parts = ip.split(".")
        return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)

    # Read /proc/net/arp
    try:
        with open("/proc/net/arp", "r") as f:
            for line in f.readlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 4:
                    ip = parts[0]
                    flags = parts[2]
                    if flags != "0x0" and ip != local_ip and is_valid_ipv4(ip):
                        neighbors.add(ip)
    except Exception:
        pass

    # Read ip neigh
    try:
        out = subprocess.check_output(["ip", "neigh"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            parts = line.strip().split()
            if parts and parts[0] != local_ip and is_valid_ipv4(parts[0]):
                if "FAILED" not in line:
                    neighbors.add(parts[0])
    except Exception:
        pass

    return list(neighbors)
