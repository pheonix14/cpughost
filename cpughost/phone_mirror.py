"""
CPUGhost - Phone Screen Mirroring Module
Seamlessly mirrors Android phone screen with audio & touch/keyboard pass-through to PC zero.
Supports scrcpy (ultra-low latency hardware H.264/H.265) and Web Screen Stream fallback.
"""

import os
import sys
import time
import shutil
import subprocess
import socket
import re
from typing import Optional, List, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import load_config, save_config, get_local_ip, get_default_gateway_ip, get_arp_neighbors, is_android

console = Console()


def print_phone_banner():
    banner = r"""
==================================================================
   ____  _   _  ___  _   _ _____   __  __ ___ ____  ____   ___  ____  
  |  _ \| | | |/ _ \| \ | | ____| |  \/  |_ _|  _ \|  _ \ / _ \|  _ \ 
  | |_) | |_| | | | |  \| |  _|   | |\/| || || |_) | |_) | | | | |_) |
  |  __/|  _  | |_| | |\  | |___  | |  | || ||  _ <|  _ <| |_| |  _ < 
  |_|   |_| |_|\___/|_| \_|_____| |_|  |_|___|_| \_\_| \_\\___/|_| \_\
==================================================================
   ANDROID PHONE SCREEN MIRROR & CONTROL FOR PC ZERO
==================================================================
"""
    console.print(f"[bold cyan]{banner}[/bold cyan]")


def get_connected_adb_devices() -> List[Tuple[str, str]]:
    """Return list of (serial, status) for attached ADB devices."""
    adb_bin = shutil.which("adb")
    if not adb_bin:
        return []

    devices = []
    try:
        out = subprocess.check_output([adb_bin, "devices"], text=True, stderr=subprocess.DEVNULL)
        for line in out.strip().splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                devices.append((parts[0], parts[1]))
    except Exception:
        pass
    return devices


def detect_phone_ip(target_ip: Optional[str] = None) -> Optional[str]:
    """
    Auto-detect the Android phone's IP address on the hotspot/Wi-Fi network.
    Strategy 1: User-specified IP.
    Strategy 2: Connected ADB TCP/IP devices (e.g. 10.176.124.51:5555).
    Strategy 3: Default Gateway IP (when PC zero is connected to phone's hotspot, the gateway is the phone).
    Strategy 4: Scan ARP neighbors & hotspot subnet for ADB (5555) or Web Stream (8080).
    Strategy 5: Last saved phone IP in config.
    """
    if target_ip and target_ip != "auto":
        return target_ip

    cfg = load_config()

    # 1. Check if ADB already has an IP device connected
    for serial, status in get_connected_adb_devices():
        if ":" in serial and status == "device":
            ip = serial.split(":")[0]
            console.print(f"[PHONE] Detected active ADB connection to phone at {ip}")
            return ip

    # 2. Check default gateway (hotspot host phone IP)
    gw = get_default_gateway_ip()
    local_ip = get_local_ip()
    if gw and gw != "0.0.0.0" and gw != local_ip and not gw.startswith("127."):
        # Check if gateway is reachable
        try:
            res = subprocess.run(["ping", "-c", "1", "-W", "1", gw], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                console.print(f"[PHONE] Detected phone hotspot host gateway: [bold green]{gw}[/bold green]")
                return gw
        except Exception:
            return gw

    # 3. Check ARP neighbors
    neighbors = get_arp_neighbors()
    for n in neighbors:
        if n != local_ip:
            # Check port 5555 or 8080
            for port in [5555, 8080]:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.2)
                    if s.connect_ex((n, port)) == 0:
                        s.close()
                        console.print(f"[PHONE] Detected phone node at {n} (port {port} open)")
                        return n
                    s.close()
                except Exception:
                    pass

    # 4. Fallback to last saved peer or phone IP
    if cfg.get("last_phone_ip"):
        return cfg["last_phone_ip"]
    if cfg.get("last_peer_ip") and cfg["last_peer_ip"] != local_ip:
        return cfg["last_peer_ip"]

    return None


def ensure_scrcpy_installed() -> bool:
    """Check if scrcpy and adb are installed; prompt/install on Linux if missing."""
    adb_bin = shutil.which("adb")
    scrcpy_bin = shutil.which("scrcpy")

    if adb_bin and scrcpy_bin:
        return True

    console.print("[WARN] scrcpy or adb is missing on this system.")
    if is_android():
        console.print("[INFO] On Termux, install using: [bold yellow]pkg install -y android-tools scrcpy[/bold yellow]")
        return False

    # Linux (Zorin OS / Ubuntu / Debian)
    if shutil.which("apt-get"):
        console.print("[INFO] scrcpy is required for low-latency hardware-accelerated phone mirroring.")
        try:
            choice = input("Would you like to install scrcpy & adb now via apt? [Y/n]: ").strip().lower()
        except EOFError:
            choice = "y"

        if choice in ("", "y", "yes"):
            console.print("[INFO] Installing scrcpy and android-tools via apt...")
            cmd = ["sudo", "apt-get", "update", "-y"] if os.geteuid() != 0 else ["apt-get", "update", "-y"]
            subprocess.run(cmd, check=False)
            cmd_inst = ["sudo", "apt-get", "install", "-y", "scrcpy", "adb"] if os.geteuid() != 0 else ["apt-get", "install", "-y", "scrcpy", "adb"]
            res = subprocess.run(cmd_inst, check=False)
            if res.returncode == 0 and shutil.which("scrcpy"):
                console.print("[OK] scrcpy successfully installed!")
                return True

    console.print("[FAIL] scrcpy could not be auto-installed. Install manually with: sudo apt install scrcpy")
    return False


def connect_adb(phone_ip: str, port: int = 5555, timeout: float = 3.0) -> bool:
    """Connect to phone via ADB over Wi-Fi / TCP/IP."""
    adb_bin = shutil.which("adb")
    if not adb_bin:
        return False

    target = f"{phone_ip}:{port}"

    # Check if already connected
    for serial, status in get_connected_adb_devices():
        if serial == target and status == "device":
            return True

    console.print(f"[PHONE] Connecting ADB to [bold cyan]{target}[/bold cyan]...")
    try:
        res = subprocess.run(
            [adb_bin, "connect", target],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        out = res.stdout.strip() + " " + res.stderr.strip()
        if "connected to" in out.lower() and "cannot" not in out.lower():
            console.print(f"[OK] ADB connected to {target}")
            return True
        elif "already connected" in out.lower():
            return True
        elif "failed to authenticate" in out.lower():
            console.print("[WARN] Please look at your phone screen and tap 'ALLOW' for USB/Wireless Debugging.")
            time.sleep(2)
            return True
        else:
            console.print(f"[INFO] ADB connect output: {out.strip()}")
            return False
    except Exception as e:
        console.print(f"[WARN] ADB connect failed: {e}")
        return False


def pair_adb_wireless(phone_ip: str, pair_port: int, pairing_code: str) -> bool:
    """Pair Android 11+ Wireless Debugging using 6-digit code."""
    adb_bin = shutil.which("adb")
    if not adb_bin:
        return False

    console.print(f"[PHONE] Pairing with {phone_ip}:{pair_port} using code {pairing_code}...")
    try:
        res = subprocess.run(
            [adb_bin, "pair", f"{phone_ip}:{pair_port}", str(pairing_code)],
            capture_output=True,
            text=True,
            timeout=10.0
        )
        out = res.stdout.strip() + " " + res.stderr.strip()
        if "successfully paired" in out.lower():
            console.print("[OK] Wireless Debugging successfully paired!")
            return True
        else:
            console.print(f"[WARN] Pairing result: {out}")
            return False
    except Exception as e:
        console.print(f"[FAIL] Pairing error: {e}")
        return False


def enable_phone_tcpip_local():
    """Run on Android/Termux to enable port 5555 for PC zero."""
    adb_bin = shutil.which("adb")
    if not adb_bin:
        console.print("[WARN] adb not found. Install in Termux: pkg install android-tools")
        return

    console.print("[PHONE] Enabling ADB TCP/IP port 5555 on phone...")
    try:
        res = subprocess.run([adb_bin, "tcpip", "5555"], capture_output=True, text=True, timeout=5)
        out = res.stdout.strip() + " " + res.stderr.strip()
        if "restarting in tcp mode" in out.lower() or res.returncode == 0:
            console.print("[OK] Phone ADB is now listening on port 5555 over Wi-Fi/Hotspot!")
            console.print("[INFO] On PC zero, you can now run: ghost phone")
        else:
            console.print(f"[INFO] Result: {out}")
            if "no devices" in out.lower():
                console.print("[TIP] In Developer Options -> enable Wireless Debugging, then run 'ghost-phone --pair'.")
    except Exception as e:
        console.print(f"[WARN] Failed: {e}")


def launch_phone_mirror(
    target_ip: Optional[str] = None,
    bitrate: str = "12M",
    max_fps: int = 60,
    stay_awake: bool = True,
    turn_screen_off: bool = False,
    audio: bool = True,
    web_mode: bool = False
):
    """
    Launch full phone screen mirror on PC zero.
    Handles device resolution, auto-connection, hotkey hints, and scrcpy launch.
    """
    print_phone_banner()

    # 1. Web stream mode
    if web_mode:
        phone_ip = detect_phone_ip(target_ip) or "10.176.124.51"
        web_url = f"http://{phone_ip}:8080"
        console.print(f"[PHONE] Launching Web Screen Stream at [bold cyan]{web_url}[/bold cyan]...")
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", web_url])
        elif shutil.which("firefox"):
            subprocess.Popen(["firefox", web_url])
        elif shutil.which("google-chrome"):
            subprocess.Popen(["google-chrome", web_url])
        else:
            console.print(f"[INFO] Open this URL in your browser: {web_url}")
        return

    # 2. Check for scrcpy
    if not ensure_scrcpy_installed():
        return

    # 3. Detect phone target
    adb_devices = get_connected_adb_devices()
    device_target = None

    # Check for connected USB or existing device
    for serial, status in adb_devices:
        if status == "device":
            if not target_ip or target_ip in serial:
                device_target = serial
                console.print(f"[PHONE] Using active ADB device: [bold green]{device_target}[/bold green]")
                break

    # If no active device, attempt Wi-Fi/Hotspot connect
    if not device_target:
        phone_ip = detect_phone_ip(target_ip)
        if not phone_ip:
            console.print("[WARN] Could not automatically detect phone IP.")
            try:
                phone_ip = input("Enter Phone IP (e.g. 10.176.124.51 or 192.168.43.1): ").strip()
            except EOFError:
                phone_ip = ""

        if phone_ip:
            # Save in config
            cfg = load_config()
            cfg["last_phone_ip"] = phone_ip
            save_config(cfg)

            connected = connect_adb(phone_ip, 5555)
            if connected:
                device_target = f"{phone_ip}:5555"
            else:
                # Offer wireless pairing
                console.print("------------------------------------------------------------------")
                console.print("[INFO] ADB connection to port 5555 was not ready.")
                console.print("[TIP] Enable 'Wireless Debugging' in Android Developer Options.")
                console.print("------------------------------------------------------------------")
                choice = input("Would you like to pair via Android Wireless Debugging code now? [Y/n]: ").strip().lower()
                if choice in ("", "y", "yes"):
                    pair_port = input("Enter Pairing Port shown on phone (e.g. 38475): ").strip()
                    pair_code = input("Enter 6-digit Pairing Code: ").strip()
                    connect_port = input(f"Enter Connect Port (from main Wireless Debugging screen, default {pair_port}): ").strip() or pair_port
                    if pair_port and pair_code:
                        if pair_adb_wireless(phone_ip, int(pair_port), pair_code):
                            time.sleep(1)
                            if connect_adb(phone_ip, int(connect_port)):
                                device_target = f"{phone_ip}:{connect_port}"

    if not device_target:
        console.print("[FAIL] Could not establish connection to phone.")
        console.print("[TIP] You can also plug the phone via USB cable once and run 'ghost phone'.")
        return

    # 4. Display Controls Cheatsheet
    table = Table(title="Phone Mirror Hotkeys & Controls", border_style="cyan")
    table.add_column("Action / Shortcut", style="bold yellow")
    table.add_column("Effect on Android Phone", style="white")

    table.add_row("Left Click & Drag", "Touch, swipe, tap on phone screen")
    table.add_row("Right Click or Ctrl + B", "Android BACK button")
    table.add_row("Middle Click or Ctrl + H", "Android HOME button")
    table.add_row("Ctrl + S", "Open Android App Switcher (Recents)")
    table.add_row("Ctrl + N", "Expand Android Notification shade")
    table.add_row("Ctrl + P", "Power button (turn phone screen on/off)")
    table.add_row("Ctrl + F", "Toggle Fullscreen mirror window")
    table.add_row("Ctrl + Up / Down", "Volume Up / Down")
    table.add_row("Ctrl + C / Ctrl + V", "Sync clipboard between PC zero and Phone")
    table.add_row("Drag-and-Drop file", "Push file to phone Download folder / install APK")

    console.print(table)
    console.print(f"[OK] Launching hardware-accelerated mirror for [bold green]{device_target}[/bold green]...")

    # 5. Build scrcpy command
    scrcpy_cmd = [
        "scrcpy",
        "-s", device_target,
        f"--window-title=CPUGhost - Phone Mirror ({device_target})",
        f"--video-bit-rate={bitrate}",
        f"--max-fps={max_fps}",
    ]

    if stay_awake:
        scrcpy_cmd.append("--stay-awake")

    if turn_screen_off:
        scrcpy_cmd.append("--turn-screen-off")

    if not audio:
        scrcpy_cmd.append("--no-audio")
    else:
        scrcpy_cmd.append("--audio-buffer=50")

    try:
        proc = subprocess.run(scrcpy_cmd)
        if proc.returncode != 0:
            console.print(f"[INFO] scrcpy exited with code {proc.returncode}")
    except KeyboardInterrupt:
        console.print("\n[INFO] Phone mirror closed.")
    except Exception as e:
        console.print(f"[FAIL] Error launching scrcpy: {e}")


def interactive_phone_menu():
    """Interactive CLI menu for Phone Mirroring."""
    while True:
        print_phone_banner()
        cfg = load_config()
        local_ip = get_local_ip()
        detected_ip = detect_phone_ip() or "Not detected"

        console.print(f" Local IP: [bold green]{local_ip}[/bold green] | Detected Phone: [bold cyan]{detected_ip}[/bold cyan]")
        console.print("------------------------------------------------------------------")
        console.print(" [1] Launch Phone Mirror (Auto-Connect Hotspot / Wi-Fi)")
        console.print(" [2] Launch Phone Mirror with Custom IP")
        console.print(" [3] Pair Wireless Debugging (Android 11+ Pairing Code)")
        console.print(" [4] Turn Phone Screen OFF while mirroring on PC (Save Battery)")
        console.print(" [5] Open Web Screen Stream in Browser (No ADB needed)")
        if is_android():
            console.print(" [6] Phone Helper: Enable ADB Port 5555 on this phone")
            console.print(" [7] Phone Helper: Pair Termux with Phone Wireless Debugging")
        console.print(" [0] Back to Main Menu")
        console.print("------------------------------------------------------------------")

        try:
            choice = input(" Select option: ").strip()
        except EOFError:
            break

        if choice == "1":
            launch_phone_mirror()
            input("\nPress Enter to continue...")
        elif choice == "2":
            custom_ip = input(" Enter phone IP address: ").strip()
            if custom_ip:
                launch_phone_mirror(target_ip=custom_ip)
            input("\nPress Enter to continue...")
        elif choice == "3":
            phone_ip = detect_phone_ip() or input(" Enter phone IP: ").strip()
            pair_port = input(" Enter Pairing Port: ").strip()
            pair_code = input(" Enter 6-digit Code: ").strip()
            if phone_ip and pair_port and pair_code:
                pair_adb_wireless(phone_ip, int(pair_port), pair_code)
            input("\nPress Enter to continue...")
        elif choice == "4":
            launch_phone_mirror(turn_screen_off=True)
            input("\nPress Enter to continue...")
        elif choice == "5":
            launch_phone_mirror(web_mode=True)
            input("\nPress Enter to continue...")
        elif choice == "6" and is_android():
            enable_phone_tcpip_local()
            input("\nPress Enter to continue...")
        elif choice == "7" and is_android():
            port = input(" Enter local pairing port from Wireless Debugging: ").strip()
            code = input(" Enter 6-digit code: ").strip()
            if port and code:
                pair_adb_wireless("127.0.0.1", int(port), code)
                connect_port = input(f" Enter connect port (default {port}): ").strip() or port
                connect_adb("127.0.0.1", int(connect_port))
                enable_phone_tcpip_local()
            input("\nPress Enter to continue...")
        elif choice == "0":
            break
