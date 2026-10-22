"""
CPUGhost Phone Module: Seamless Android Phone ("Caesar") Mirroring & Control.
Supports Wireless ADB (Hotspot TCP/IP 5555), USB Debugging, and scrcpy integration.
"""

import os
import re
import time
import shutil
import subprocess
from typing import List, Dict, Optional, Tuple
from rich.console import Console
from .config import load_config, save_config, get_arp_neighbors, get_local_ip

console = Console()


def is_adb_installed() -> bool:
    return shutil.which("adb") is not None


def is_scrcpy_installed() -> bool:
    return shutil.which("scrcpy") is not None


def run_adb_command(args: List[str], timeout: float = 5.0) -> Tuple[int, str, str]:
    """Execute an ADB command safely with a timeout."""
    if not is_adb_installed():
        return 127, "", "adb not installed"
    try:
        res = subprocess.run(
            ["adb"] + args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"
    except Exception as e:
        return 1, "", str(e)


def get_connected_devices() -> List[Dict[str, str]]:
    """List all ADB devices currently detected."""
    code, stdout, _ = run_adb_command(["devices", "-l"], timeout=4.0)
    if code != 0 or not stdout:
        return []

    devices = []
    lines = stdout.splitlines()
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            serial = parts[0]
            state = parts[1]
            model = "Android Phone"
            for p in parts[2:]:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1]
            devices.append({
                "serial": serial,
                "state": state,
                "model": model,
                "is_wireless": ":" in serial
            })
    return devices


def connect_wireless(ip: str, port: int = 5555) -> Tuple[bool, str]:
    """Connect to Android phone wirelessly over hotspot Wi-Fi via ADB TCP/IP."""
    target = f"{ip}:{port}"
    code, out, err = run_adb_command(["connect", target], timeout=6.0)
    if "connected to" in out.lower():
        return True, out
    return False, err or out or "Failed to connect"


def pair_wireless(ip: str, port: int, pairing_code: str) -> Tuple[bool, str]:
    """Pair with Android phone for Wireless Debugging (Android 11+)."""
    target = f"{ip}:{port}"
    code, out, err = run_adb_command(["pair", target, str(pairing_code)], timeout=10.0)
    if "successfully paired" in out.lower():
        return True, out
    return False, err or out or "Pairing failed"


def auto_scan_and_connect_phone(preferred_ip: Optional[str] = None, phone_name: str = "Caesar") -> Optional[str]:
    """
    Search connected USB devices and scan hotspot neighbors for wireless ADB.
    Returns the serial of the connected phone if found.
    """
    # 1. Check already connected devices
    devs = get_connected_devices()
    for d in devs:
        if d.get("state") == "device":
            console.print(f"[PHONE] Detected active phone: [bold green]{d['model']} ({d['serial']})[/bold green]")
            return d["serial"]

    # 2. Try configured or preferred IP
    cfg = load_config()
    target_ip = preferred_ip or cfg.get("phone_ip")
    if target_ip:
        console.print(f"[PHONE] Attempting wireless connection to {target_ip}:5555...")
        ok, msg = connect_wireless(target_ip, 5555)
        if ok:
            console.print(f"[OK] Connected wirelessly to phone: {msg}")
            return f"{target_ip}:5555"

    # 3. Check ARP neighbors on the hotspot network
    neighbors = get_arp_neighbors()
    local_ip = get_local_ip()
    candidates = [ip for ip in neighbors if ip != local_ip and not ip.startswith("127.")]

    for ip in candidates:
        console.print(f"[PHONE] Scanning hotspot neighbor {ip}:5555 for {phone_name}...")
        ok, _ = connect_wireless(ip, 5555)
        if ok:
            console.print(f"[OK] Successfully discovered and connected to phone at [bold cyan]{ip}:5555[/bold cyan]!")
            cfg["phone_ip"] = ip
            save_config(cfg)
            return f"{ip}:5555"

    return None


class PhoneController:
    """Direct touch, swipe, keyboard, and keyevent controller for Android via ADB."""

    def __init__(self, serial: Optional[str] = None):
        self.serial = serial

    def _cmd(self, args: List[str]) -> Tuple[int, str, str]:
        cmd = []
        if self.serial:
            cmd.extend(["-s", self.serial])
        cmd.extend(args)
        return run_adb_command(cmd, timeout=3.0)

    def tap(self, x: int, y: int):
        self._cmd(["shell", "input", "tap", str(int(x)), str(int(y))])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        self._cmd(["shell", "input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms))])

    def type_text(self, text: str):
        # Escape spaces for ADB input
        escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
        self._cmd(["shell", "input", "text", escaped])

    def keyevent(self, keycode: int):
        self._cmd(["shell", "input", "keyevent", str(keycode)])

    def back(self):
        self.keyevent(4)  # KEYCODE_BACK

    def home(self):
        self.keyevent(3)  # KEYCODE_HOME

    def app_switch(self):
        self.keyevent(187)  # KEYCODE_APP_SWITCH (Recent apps)

    def power(self):
        self.keyevent(26)  # KEYCODE_POWER

    def volume_up(self):
        self.keyevent(24)

    def volume_down(self):
        self.keyevent(25)


def capture_phone_frame(serial: Optional[str] = None) -> Optional[bytes]:
    """Capture a live PNG frame from the phone via ADB screencap."""
    if not is_adb_installed():
        return None
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["exec-out", "screencap", "-p"])
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=2.0)
        if res.returncode == 0 and res.stdout and len(res.stdout) > 100:
            return res.stdout
    except Exception:
        pass
    return None


def launch_phone_mirror(
    serial: Optional[str] = None,
    phone_name: str = "Caesar",
    always_on_top: bool = True,
    stay_awake: bool = True,
    turn_screen_off: bool = False,
    max_size: int = 1920,
    bit_rate: str = "8M",
    max_fps: int = 60
):
    """
    Launch native CPUGhost hardware screen mirror and touch controller.
    Runs 100% in CPUGhost without requiring scrcpy.
    """
    from .phone_native import run_native_phone_mirror
    return run_native_phone_mirror(target_ip=serial, phone_name=phone_name)
