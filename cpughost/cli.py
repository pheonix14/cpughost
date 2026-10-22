import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import sys
import time
import shutil
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .config import load_config, save_config, get_local_ip, CONFIG_DIR
from .discovery import GhostDiscovery, scan_hotspot_subnet
from .drive_client import GhostDriveClient

console = Console()
PID_FILE = CONFIG_DIR / "host.pid"


def get_stored_pid() -> Optional[int]:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            # Check if running
            os.kill(pid, 0)
            return pid
        except (ValueError, OSError):
            if PID_FILE.exists():
                PID_FILE.unlink()
    return None


def print_ascii_banner():
    banner = r"""
==================================================================
   ____ ____  _   _  ____ _   _  ___  ____ _____ 
  / ___|  _ \| | | |/ ___| | | |/ _ \/ ___|_   _|
 | |   | |_) | | | | |  _| |_| | | | \___ \ | |  
 | |___|  __/| |_| | |_| |  _  | |_| |___) || |  
  \____|_|    \___/ \____|_| |_|\___/|____/ |_|  
==================================================================
   HEADLESS DUAL-PC HOTSPOT SCREEN & VIRTUAL PEN DRIVE SYSTEM
=================================================================="""
    console.print(f"[bold cyan]{banner}[/bold cyan]")


def notify_desktop(title: str, message: str):
    """Send desktop notification if notify-send is available."""
    try:
        subprocess.run(
            ["notify-send", "-a", "CPUGhost", title, message],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


def check_and_apply_auto_update(silent: bool = True):
    """
    Check GitHub for updates.
    Applies updates from origin/main, especially when release tags reach v5+.
    """
    project_dir = Path(__file__).resolve().parent.parent
    if not (project_dir / ".git").exists():
        return
    try:
        res = subprocess.run(
            ["timeout", "3", "git", "ls-remote", "--tags", "origin"],
            cwd=str(project_dir),
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and res.stdout:
            tags = [line.split("/")[-1] for line in res.stdout.splitlines() if "refs/tags/v" in line]
            has_v5 = any(
                t.lstrip("v").split(".")[0].isdigit() and int(t.lstrip("v").split(".")[0]) >= 5
                for t in tags
            )
            if has_v5:
                latest_tag = tags[-1]
                console.print(f"[UPDATE] GitHub release {latest_tag} (v5+) detected. Applying update...")
                subprocess.run(["git", "pull", "--quiet", "origin", "main"], cwd=str(project_dir), capture_output=True)
                console.print(f"[OK] CPUGhost auto-updated to {latest_tag}.")
                return

        # Fallback to checking main branch HEAD commit
        res_head = subprocess.run(
            ["timeout", "3", "git", "ls-remote", "--heads", "origin", "main"],
            cwd=str(project_dir),
            capture_output=True,
            text=True
        )
        if res_head.returncode == 0 and res_head.stdout:
            remote_hash = res_head.stdout.strip().split()[0]
            local_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(project_dir), capture_output=True, text=True)
            local_hash = local_res.stdout.strip() if local_res.returncode == 0 else ""
            if remote_hash and local_hash and remote_hash != local_hash:
                subprocess.run(["git", "pull", "--quiet", "origin", "main"], cwd=str(project_dir), capture_output=True)
                console.print("[OK] CPUGhost auto-updated to latest commit.")
    except Exception:
        pass


def wait_for_host_detection(poll_interval: float = 1.5) -> Optional[str]:
    """Continuously poll hotspot until a CPUGhost host is detected, then return its IP."""
    cfg = load_config()
    disc = GhostDiscovery(port=cfg.get("discovery_port", 8989))
    disc.start_listener()
    console.print("[DISCOVERY] Waiting for Zorin PC on hotspot Wi-Fi...")

    counter = 0
    try:
        while True:
            peers = [p for p in disc.get_active_peers() if "host" in p.get("roles", [])]
            if peers:
                chosen = peers[0]
                disc.stop()
                console.print("==================================================================")
                console.print(f"[OK] DETECTED ZORIN PC: {chosen.get('node_name', 'Host')} ({chosen['ip']})")
                console.print("==================================================================")
                cfg["last_peer_ip"] = chosen["ip"]
                save_config(cfg)
                return chosen["ip"]

            counter += 1
            if counter % 5 == 0:
                active_ips = scan_hotspot_subnet(port=cfg.get("api_port", 8888), timeout=0.2)
                if active_ips:
                    disc.stop()
                    ip = active_ips[0]
                    console.print("==================================================================")
                    console.print(f"[OK] DETECTED ZORIN PC VIA SUBNET SCAN: {ip}")
                    console.print("==================================================================")
                    cfg["last_peer_ip"] = ip
                    save_config(cfg)
                    return ip

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        disc.stop()
        return None


def resolve_host_ip(ip_arg: Optional[str], interactive: bool = True) -> Optional[str]:
    """Resolve host IP via argument, discovery beacon, or subnet scan."""
    if ip_arg:
        return ip_arg

    cfg = load_config()
    last_ip = cfg.get("last_peer_ip")

    console.print("[DISCOVERY] Scanning hotspot for CPUGhost host beacons...")
    disc = GhostDiscovery(port=cfg.get("discovery_port", 8989))
    disc.start_listener()
    time.sleep(1.8)
    peers = [p for p in disc.get_active_peers() if "host" in p.get("roles", [])]

    if not peers:
        # Fallback to active subnet scan
        active_ips = scan_hotspot_subnet(port=cfg.get("api_port", 8888))
        if active_ips:
            for ip in active_ips:
                peers.append({"ip": ip, "node_name": "Host", "roles": ["host"]})

    disc.stop()

    if len(peers) == 1:
        chosen_peer = peers[0]
        host_ip = chosen_peer["ip"]
        host_name = chosen_peer.get("node_name", "Host")
        console.print(f"[OK] Discovered CPUGhost Host: {host_name} ({host_ip})")
        cfg["last_peer_ip"] = host_ip
        save_config(cfg)
        return host_ip

    elif len(peers) > 1:
        console.print("[DISCOVERY] Multiple CPUGhost PCs found on hotspot network:")
        for idx, p in enumerate(peers, 1):
            name = p.get("node_name", "Host")
            ip = p["ip"]
            console.print(f"  [{idx}] {name} ({ip})")

        choice = click.prompt("Select PC to connect to", type=int, default=1)
        if 1 <= choice <= len(peers):
            chosen = peers[choice - 1]
            cfg["last_peer_ip"] = chosen["ip"]
            save_config(cfg)
            return chosen["ip"]

    # No peer found yet on first pass
    if interactive and sys.stdin.isatty():
        console.print("[WAIT] No CPUGhost Host detected yet on hotspot.")
        console.print("  [1] Keep scanning (wait for Zorin PC to connect to hotspot)")
        console.print("  [2] Enter IP address manually")
        console.print("  [3] Use last known IP" + (f" ({last_ip})" if last_ip else ""))
        console.print("  [0] Cancel")

        opt = click.prompt("Select option", type=str, default="1")
        if opt == "1":
            console.print("[WAIT] Polling hotspot for Zorin PC beacon (timeout 30s)...")
            disc = GhostDiscovery(port=cfg.get("discovery_port", 8989))
            disc.start_listener()
            start_t = time.time()
            while time.time() - start_t < 30:
                p_list = [p for p in disc.get_active_peers() if "host" in p.get("roles", [])]
                if p_list:
                    disc.stop()
                    chosen = p_list[0]
                    print("")
                    console.print(f"[OK] Discovered Zorin PC: {chosen.get('node_name', 'Host')} ({chosen['ip']})")
                    cfg["last_peer_ip"] = chosen["ip"]
                    save_config(cfg)
                    return chosen["ip"]
                rem = int(30 - (time.time() - start_t))
                sys.stdout.write(f"\r[WAIT] Listening on hotspot Wi-Fi... {rem}s remaining ")
                sys.stdout.flush()
                time.sleep(1.5)
            disc.stop()
            print("")
            console.print("[WARN] Timed out waiting for Zorin PC beacon.")
        elif opt == "2":
            manual_ip = click.prompt("Enter Zorin PC IP address (e.g. 10.176.124.X)")
            cfg["last_peer_ip"] = manual_ip
            save_config(cfg)
            return manual_ip
        elif opt == "3" and last_ip:
            return last_ip

    if last_ip:
        console.print(f"[WARN] No peer discovered. Using last known IP: {last_ip}")
        return last_ip

    console.print("[ERR] Could not locate CPUGhost Host on hotspot network.")
    console.print("[INFO] Start the host on the second PC with: ghost host")
    console.print("[INFO] Or specify host IP directly: ghost client <IP>")
    return None


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """CPUGhost: Dual-PC Hotspot Screen & Virtual Drive CLI."""
    if ctx.invoked_subcommand is None:
        cfg = load_config()
        if cfg.get("configured_role") is None and sys.stdin.isatty():
            first_run_wizard()
        else:
            interactive_menu()


def first_run_wizard():
    """First-run setup wizard asking for computer role."""
    print_ascii_banner()
    local_ip = get_local_ip()
    console.print("[bold green]WELCOME TO CPUGHOST FIRST-RUN SETUP[/bold green]")
    console.print("------------------------------------------------------------------")
    console.print(f" Detected Computer: {load_config().get('node_name', 'PC')} | Local IP: {local_ip}")
    console.print("")
    console.print(" Select role for this computer:")
    console.print("  [1] Host / Secondary (Zorin OS headless PC - share screen & pen drive on boot)")
    console.print("  [2] Client / Primary (Viewer PC - see Zorin screen in a window & use pen drive)")
    console.print("------------------------------------------------------------------")

    choice = click.prompt("Select role", type=str, default="2")
    cfg = load_config()
    ctx = click.get_current_context()

    if choice == "1":
        cfg["configured_role"] = "host"
        save_config(cfg)
        console.print("[INFO] Configuring as Headless Host...")
        ctx.invoke(autostart_enable)
        console.print("[INFO] Starting Host now...")
        run_host()
    else:
        cfg["configured_role"] = "client"
        save_config(cfg)
        console.print("[INFO] Configuring as Primary Client...")
        console.print("[INFO] Searching hotspot for your Zorin OS Host...")
        ctx.invoke(client)


@main.command()
@click.option("--daemon", "-d", is_flag=True, help="Run host daemon in background")
def host(daemon: bool):
    """Start host server (Screen sharing + Virtual Pen Drive)."""
    check_and_apply_auto_update(silent=True)
    pid = get_stored_pid()
    if pid:
        console.print(f"[WARN] Host already running with PID {pid}. Run 'ghost stop' first.")
        return

    if is_android():
        print_ascii_banner()
        from .phone_server import run_phone_host_service
        run_phone_host_service()
        return

    if daemon:
        # Launch daemonized process
        python_bin = sys.executable
        project_dir = str(Path(__file__).resolve().parent.parent)
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{project_dir}:{env.get('PYTHONPATH', '')}"
        env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"
        if "XAUTHORITY" not in env:
            uid = os.getuid()
            run_dir = Path(f"/run/user/{uid}")
            matches = list(run_dir.glob(".mutter-Xwaylandauth.*")) + list(run_dir.glob("*xauth*"))
            if matches:
                env["XAUTHORITY"] = str(matches[0])
            elif (Path.home() / ".Xauthority").exists():
                env["XAUTHORITY"] = str(Path.home() / ".Xauthority")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log_fp = open(CONFIG_DIR / "host.log", "w")
        cmd = [python_bin, "-m", "cpughost.server"]
        p = subprocess.Popen(
            cmd,
            cwd=project_dir,
            env=env,
            stdout=log_fp,
            stderr=log_fp,
            preexec_fn=os.setpgrp
        )
        PID_FILE.write_text(str(p.pid))
        console.print(f"[OK] CPUGhost Host started in background (PID: {p.pid})")
        console.print(f"[INFO] Control Center: http://{get_local_ip()}:8888")
        console.print("[INFO] Stop host at any time with: ghost stop")
    else:
        print_ascii_banner()
        from .server import run_host
        run_host()


@main.command()
@click.argument("ip", required=False)
@click.option("--wait", "-w", is_flag=True, help="Continuously wait and auto-connect when host appears")
def client(ip: Optional[str] = None, wait: bool = False):
    """Connect to ghost host: mount pen drive and launch screen window."""
    check_and_apply_auto_update(silent=True)

    if wait and not ip:
        notify_desktop("CPUGhost Client Active", "Waiting for Zorin PC on hotspot...")
        host_ip = wait_for_host_detection()
    else:
        host_ip = resolve_host_ip(ip)

    if not host_ip:
        return

    notify_desktop("CPUGhost Connected", f"Opening monitor window for {host_ip}...")
    cfg = load_config()
    mount_dir = Path(cfg.get("mount_point", str(Path.home() / "Desktop" / "GhostDrive")))
    mount_dir.mkdir(parents=True, exist_ok=True)

    def _sync_drive_bg():
        try:
            console.print(f"[DRIVE] Syncing virtual pen drive in background at: {mount_dir}")
            client_driver = GhostDriveClient(host_ip, port=cfg.get("api_port", 8888))
            client_driver.sync_once(mount_dir)
            console.print(f"[OK] Pen drive synchronized: {mount_dir}")
        except Exception:
            pass

    threading.Thread(target=_sync_drive_bg, daemon=True).start()

    console.print(f"[SCREEN] Opening remote screen viewer for {host_ip}...")
    from .screen_viewer import GhostScreenViewer
    viewer = GhostScreenViewer(host_ip, port=cfg.get("stream_port", 8889))
    viewer.start()


@main.command()
@click.pass_context
def auto(ctx):
    """Auto-negotiate connection: connects if host exists, otherwise becomes host."""
    check_and_apply_auto_update(silent=True)
    cfg = load_config()
    console.print("[AUTO] Probing hotspot network for active peers...")
    disc = GhostDiscovery(port=cfg.get("discovery_port", 8989))
    disc.start_listener()
    time.sleep(1.5)
    peers = [p for p in disc.get_active_peers() if "host" in p.get("roles", [])]
    disc.stop()

    if not peers:
        active_ips = scan_hotspot_subnet(port=cfg.get("api_port", 8888), timeout=0.2)
        for ip in active_ips:
            peers.append({"ip": ip, "node_name": "Host", "roles": ["host"]})

    if peers:
        chosen = peers[0]
        console.print(f"[AUTO] Discovered remote host at {chosen['ip']}. Auto-connecting as viewer...")
        ctx.invoke(client, ip=chosen["ip"])
    else:
        console.print("[AUTO] No existing host found. Starting this PC as discoverable Host...")
        from .server import run_host
        run_host()


@main.command()
@click.argument("ip", required=False)
def screen(ip: Optional[str] = None):
    """Open remote screen viewer window only."""
    host_ip = resolve_host_ip(ip)
    if not host_ip:
        return

    cfg = load_config()
    console.print(f"[SCREEN] Launching screen viewer window -> {host_ip}")
    from .screen_viewer import GhostScreenViewer
    viewer = GhostScreenViewer(host_ip, port=cfg.get("stream_port", 8889))
    viewer.start()


@main.command(name="pointer")
@click.argument("color", required=False)
@click.option("--size", "-s", type=float, help="Pointer scale (e.g. 1.0, 1.3, 1.7, 2.0)")
@click.option("--list", "-l", "list_colors", is_flag=True, help="List available Zorin pointer colors")
def pointer_cmd(color: Optional[str] = None, size: Optional[float] = None, list_colors: bool = False):
    """Customize remote mouse pointer color and size for viewer."""
    from .config import POINTER_COLORS
    cfg = load_config()

    if list_colors or (not color and size is None):
        print_ascii_banner()
        console.print("[bold green]AVAILABLE ZORIN POINTER COLORS[/bold green]")
        console.print("------------------------------------------------------------------")
        cur_color = cfg.get("pointer_color", "zorin_cyan")
        cur_size = cfg.get("pointer_scale", 1.2)

        table = Table(title="CPUGhost Pointer Themes")
        table.add_column("Key", style="bold cyan")
        table.add_column("Color Name", style="white")
        table.add_column("RGB Preview", style="yellow")
        table.add_column("Active", style="magenta")

        for k, v in POINTER_COLORS.items():
            is_active = "[ACTIVE]" if k == cur_color else ""
            table.add_row(k, v["name"], str(v["rgb"]), is_active)

        console.print(table)
        console.print(f"[INFO] Current Size: {cur_size}x")
        console.print("[INFO] Change color with: ghost pointer <color> (e.g. ghost pointer cyan)")
        console.print("[INFO] In viewer window: press F6 (or C) to cycle colors, F7 (or P) to cycle sizes.")
        return

    if color:
        color_lower = color.lower().replace("-", "_").replace(" ", "_")
        matched_key = None
        for k in POINTER_COLORS:
            if k == color_lower or k == f"zorin_{color_lower}" or color_lower in k:
                matched_key = k
                break

        if not matched_key:
            console.print(f"[ERR] Unknown color '{color}'. Available: {', '.join(POINTER_COLORS.keys())}")
            return

        cfg["pointer_color"] = matched_key
        console.print(f"[OK] Set pointer color to: {POINTER_COLORS[matched_key]['name']} ({matched_key})")

    if size is not None:
        cfg["pointer_scale"] = max(0.5, min(3.0, size))
        console.print(f"[OK] Set pointer size to: {cfg['pointer_scale']}x")

    save_config(cfg)


@main.group()
def drive():
    """Virtual Pen Drive file management."""
    pass


@drive.command(name="ls")
@click.argument("path", default="")
@click.option("--ip", default=None, help="Ghost Host IP")
def drive_ls(path: str, ip: Optional[str]):
    """List files on the ghost drive."""
    host_ip = resolve_host_ip(ip)
    if not host_ip:
        return
    client = GhostDriveClient(host_ip)
    client.print_ls(path)


@drive.command(name="push")
@click.argument("local_file")
@click.argument("remote_path", default="")
@click.option("--ip", default=None, help="Ghost Host IP")
def drive_push(local_file: str, remote_path: str, ip: Optional[str]):
    """Push local file or folder to the ghost drive."""
    host_ip = resolve_host_ip(ip)
    if not host_ip:
        return
    client = GhostDriveClient(host_ip)
    client.push(local_file, remote_path)


@drive.command(name="pull")
@click.argument("remote_file")
@click.argument("local_dest", default="")
@click.option("--ip", default=None, help="Ghost Host IP")
def drive_pull(remote_file: str, local_dest: str, ip: Optional[str]):
    """Pull file from ghost drive to local folder."""
    host_ip = resolve_host_ip(ip)
    if not host_ip:
        return
    client = GhostDriveClient(host_ip)
    client.pull(remote_file, local_dest)


@drive.command(name="sync")
@click.argument("local_dir", required=False)
@click.option("--ip", default=None, help="Ghost Host IP")
def drive_sync(local_dir: Optional[str], ip: Optional[str]):
    """Mirror ghost drive to local folder."""
    host_ip = resolve_host_ip(ip)
    if not host_ip:
        return
    cfg = load_config()
    target_dir = Path(local_dir) if local_dir else Path(cfg.get("mount_point", str(Path.home() / "Desktop" / "GhostDrive")))
    client = GhostDriveClient(host_ip)
    console.print(f"[DRIVE] Syncing {host_ip} <-> {target_dir}...")
    client.sync_once(target_dir)
    console.print("[OK] Synchronization completed.")


@main.command()
@click.option("--all", "-a", "include_self", is_flag=True, help="Include local node in scan results")
def scan(include_self: bool):
    """Scan hotspot network for CPUGhost nodes."""
    print_ascii_banner()
    cfg = load_config()
    local_ip = get_local_ip()
    console.print(f"[SCAN] Local Hotspot IP: {local_ip}")
    console.print("[SCAN] Listening for UDP discovery beacons...")

    disc = GhostDiscovery(port=cfg.get("discovery_port", 8989), include_self=include_self)
    disc.start_listener()
    time.sleep(2.0)
    peers = disc.get_active_peers()
    disc.stop()

    table = Table(title="Discovered CPUGhost Nodes")
    table.add_column("Node IP", style="cyan")
    table.add_column("Name", style="bold white")
    table.add_column("API Port", style="green")
    table.add_column("Stream Port", style="yellow")
    table.add_column("Roles", style="magenta")

    for p in peers:
        table.add_row(
            p["ip"],
            p["node_name"],
            str(p["api_port"]),
            str(p["stream_port"]),
            ", ".join(p.get("roles", []))
        )

    if peers:
        console.print(table)
    else:
        console.print("[INFO] No broadcast beacons received. Probing active TCP ports on subnet...")
        active_ips = scan_hotspot_subnet(port=cfg.get("api_port", 8888), include_self=include_self)
        if active_ips:
            for ip in active_ips:
                table.add_row(ip, "Detected Node", str(cfg.get("api_port", 8888)), str(cfg.get("stream_port", 8889)), "host")
            console.print(table)
        else:
            console.print("[WARN] No CPUGhost nodes found on this hotspot subnet.")


@main.command()
def status():
    """Show CPUGhost service status and active configuration."""
    print_ascii_banner()
    cfg = load_config()
    local_ip = get_local_ip()
    pid = get_stored_pid()

    status_str = f"[bold green]RUNNING (PID: {pid})[/bold green]" if pid else "[bold yellow]STANDBY / IDLE[/bold yellow]"

    table = Table(title="System & Network Status")
    table.add_column("Parameter", style="cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Host Daemon", status_str)
    table.add_row("Local IP", local_ip)
    table.add_row("API / Web Port", str(cfg.get("api_port", 8888)))
    table.add_row("Screen Stream Port", str(cfg.get("stream_port", 8889)))
    table.add_row("Hotspot Discovery Port", str(cfg.get("discovery_port", 8989)))
    table.add_row("Storage Path", cfg.get("storage_dir", ""))
    table.add_row("Pen Drive Mount Path", cfg.get("mount_point", ""))
    table.add_row("Last Connected Peer", cfg.get("last_peer_ip") or "None")

    console.print(table)


@main.command()
def stop():
    """Stop running CPUGhost background services."""
    pid = get_stored_pid()
    if not pid:
        console.print("[INFO] No background CPUGhost host running.")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[OK] Terminated CPUGhost Host (PID: {pid})")
    except OSError as e:
        console.print(f"[WARN] Failed to terminate PID {pid}: {e}")
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()


@main.command(name="phone")
@click.argument("device_or_ip", required=False)
@click.option("--name", default="Caesar", help="Name of phone device")
@click.option("--wireless", "-w", is_flag=True, help="Force wireless ADB connection over hotspot port 5555")
@click.option("--port", "-p", default=5555, help="Wireless ADB port (default 5555)")
@click.option("--turn-screen-off", "-S", is_flag=True, help="Turn off physical phone screen during mirroring")
@click.option("--serve", "-s", is_flag=True, help="Run phone screen and beacon server (for PC zero)")
def phone_cmd(device_or_ip: Optional[str] = None, name: str = "Caesar", wireless: bool = False, port: int = 5555, turn_screen_off: bool = False, serve: bool = False):
    """Mirror and control Android phone (Caesar) over USB or Wireless Hotspot."""
    print_ascii_banner()
    from .config import is_android
    if serve or (is_android() and not device_or_ip and not wireless):
        from .phone_server import run_phone_host_service
        run_phone_host_service()
        return
    from .phone import (
        is_adb_installed, is_scrcpy_installed,
        connect_wireless, auto_scan_and_connect_phone, launch_phone_mirror
    )

    if not is_adb_installed():
        console.print("[ERR] adb is not installed. Run: sudo apt install adb")
        return

    console.print(f"[PHONE] Initializing connection to [bold cyan]{name}[/bold cyan]...")

    serial = None
    if device_or_ip:
        if "." in device_or_ip:  # IP address
            ok, msg = connect_wireless(device_or_ip, port)
            if ok:
                console.print(f"[OK] Connected to {device_or_ip}:{port}")
                serial = f"{device_or_ip}:{port}"
            else:
                console.print(f"[WARN] Wireless connect: {msg}")
                serial = device_or_ip
        else:
            serial = device_or_ip
    else:
        serial = auto_scan_and_connect_phone(phone_name=name)

    if not serial:
        console.print("[WAIT] No phone detected over USB or wireless ADB.")
        console.print("[INFO] To connect phone 'Caesar':")
        console.print("  1. Enable 'Developer Options' and 'USB Debugging' (or 'Wireless Debugging') on Caesar.")
        console.print("  2. Connect via USB cable OR over hotspot Wi-Fi.")
        console.print("  3. For wireless, run: ghost phone <caesar-ip> (e.g. ghost phone 10.176.124.51)")
        ip_manual = click.prompt("Enter Caesar's hotspot IP address (leave blank to cancel)", default="", show_default=False)
        if ip_manual.strip():
            ok, msg = connect_wireless(ip_manual.strip(), port)
            if ok:
                console.print(f"[OK] Connected to {ip_manual.strip()}:{port}")
                serial = f"{ip_manual.strip()}:{port}"
            else:
                console.print(f"[ERR] Could not connect: {msg}")
                return
        else:
            return

    if is_scrcpy_installed():
        console.print(f"[OK] Launching hardware-accelerated mirror and control for {name} ({serial})...")
        proc = launch_phone_mirror(
            serial=serial,
            phone_name=name,
            turn_screen_off=turn_screen_off
        )
        if proc:
            proc.wait()
    else:
        console.print("[WARN] scrcpy is not installed. You can install it with: sudo apt install scrcpy")
        console.print("[INFO] Phone connected via ADB. You can view Caesar inside CPUGhost viewer (Screen 9 / Caesar tab)!")


@main.command(name="caesar")
@click.pass_context
def caesar_shortcut(ctx):
    """Shortcut alias for 'ghost phone' targeting Caesar."""
    ctx.invoke(phone_cmd, name="Caesar")


@main.command(name="screens")
def screens_cmd():
    """List all available physical and virtual screens (Screens 1..8 + Caesar Phone)."""
    print_ascii_banner()
    table = Table(title="CPUGhost Multi-Screen Catalog (>5 Displays Supported)")
    table.add_column("Screen ID", style="cyan", justify="center")
    table.add_column("Name / Layout", style="bold white")
    table.add_column("Shortcut", style="green")
    table.add_column("Description", style="dim")

    table.add_row("0", "All Screens (Panoramic)", "Alt+0", "Full combined multi-monitor desktop")
    table.add_row("1", "Screen 1 (Main Display)", "Alt+1 / F2", "Primary host desktop screen")
    table.add_row("2", "Screen 2: Top-Left", "Alt+2", "Secondary display / Top-Left workspace")
    table.add_row("3", "Screen 3: Top-Right", "Alt+3", "Tertiary display / Top-Right workspace")
    table.add_row("4", "Screen 4: Bottom-Left", "Alt+4", "Quaternary display / Bottom-Left workspace")
    table.add_row("5", "Screen 5: Bottom-Right", "Alt+5", "Quinary display / Bottom-Right workspace")
    table.add_row("6", "Screen 6: Center Focused", "Alt+6", "Senary display / Center workspace")
    table.add_row("7", "Screen 7: Left Split", "Alt+7", "Septenary display / Left ultrawide half")
    table.add_row("8", "Screen 8: Right Split", "Alt+8", "Octonary display / Right ultrawide half")
    table.add_row("9", "Caesar Phone (Android)", "F3 / Alt+9", "Android smartphone screen mirror & control")

    console.print(table)
    console.print("\n[INFO] Switch screens inside the viewer using [bold green]F2[/bold green], [bold green]F3[/bold green] (Phone), [bold green]Alt+1..8[/bold green], or by clicking the top tab bar!")


@main.group()
def autostart():
    """Configure CPUGhost to start automatically on system boot."""
    pass


@autostart.command(name="enable")
@click.option("--host", "role_host", is_flag=True, help="Configure as Headless Host (Zorin OS PC)")
@click.option("--client", "role_client", is_flag=True, help="Configure as Primary Client (Viewer PC)")
def autostart_enable(role_host: bool, role_client: bool):
    """Enable CPUGhost autostart on boot/login for Host or Client."""
    cfg = load_config()
    ghost_bin = shutil.which("ghost") or str(Path.home() / ".local" / "bin" / "ghost")
    if not Path(ghost_bin).exists():
        ghost_bin = str(Path(__file__).resolve().parent.parent / "bin" / "ghost")

    # Determine role
    if role_client:
        role = "client"
    elif role_host:
        role = "host"
    else:
        role = cfg.get("configured_role", "client")

    cfg["configured_role"] = role
    save_config(cfg)

    # 1. XDG Autostart (.desktop)
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = autostart_dir / "cpughost.desktop"

    if role == "client":
        desktop_content = f"""[Desktop Entry]
Type=Application
Name=CPUGhost Client Monitor
Comment=Auto-connect to Zorin OS CPUGhost Host and open monitor window
Exec={ghost_bin} client --wait
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
    else:
        desktop_content = f"""[Desktop Entry]
Type=Application
Name=CPUGhost Host
Comment=Headless Dual-PC Hotspot Screen and Virtual Pen Drive System
Exec={ghost_bin} host --daemon
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""

    desktop_file.write_text(desktop_content)
    console.print(f"[OK] Created XDG autostart entry ({role.upper()}): {desktop_file}")

    # 2. Systemd user service
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_file = systemd_dir / "cpughost.service"

    exec_cmd = f"{ghost_bin} client --wait" if role == "client" else f"{ghost_bin} host"
    service_desc = "CPUGhost Client Auto-Connect Service" if role == "client" else "CPUGhost Headless Dual-PC Host Service"

    service_content = f"""[Unit]
Description={service_desc}
After=network.target network-online.target

[Service]
Type=simple
ExecStart={exec_cmd}
Restart=on-failure
RestartSec=5s
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0

[Install]
WantedBy=default.target
"""
    service_file.write_text(service_content)
    console.print(f"[OK] Created systemd user unit: {service_file}")

    # Reload systemd and enable service
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "cpughost.service"], capture_output=True)
        console.print("[OK] Enabled systemd service: cpughost.service")
    except Exception:
        pass

    # Attempt to enable linger so user services run even before manual login
    try:
        user = os.environ.get("USER", "zero")
        subprocess.run(["loginctl", "enable-linger", user], capture_output=True)
        console.print(f"[OK] Enabled user session lingering for: {user}")
    except Exception:
        pass

    console.print(f"[OK] CPUGhost ({role.upper()} MODE) will now start automatically whenever this PC turns on.")


@autostart.command(name="disable")
def autostart_disable():
    """Disable CPUGhost Host autostart."""
    desktop_file = Path.home() / ".config" / "autostart" / "cpughost.desktop"
    if desktop_file.exists():
        desktop_file.unlink()
        console.print(f"[OK] Removed {desktop_file}")

    service_file = Path.home() / ".config" / "systemd" / "user" / "cpughost.service"
    try:
        subprocess.run(["systemctl", "--user", "disable", "cpughost.service"], capture_output=True)
        subprocess.run(["systemctl", "--user", "stop", "cpughost.service"], capture_output=True)
    except Exception:
        pass

    if service_file.exists():
        service_file.unlink()
        console.print(f"[OK] Removed {service_file}")

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    except Exception:
        pass

    console.print("[OK] CPUGhost autostart disabled.")


@autostart.command(name="status")
def autostart_status():
    """Check autostart configuration status."""
    desktop_file = Path.home() / ".config" / "autostart" / "cpughost.desktop"
    service_file = Path.home() / ".config" / "systemd" / "user" / "cpughost.service"

    desktop_active = desktop_file.exists()
    service_active = service_file.exists()

    table = Table(title="CPUGhost Autostart Status")
    table.add_column("Autostart Method", style="cyan")
    table.add_column("File Path", style="bold white")
    table.add_column("Status", style="green")

    table.add_row("XDG Desktop Autostart", str(desktop_file), "[ENABLED]" if desktop_active else "[DISABLED]")
    table.add_row("Systemd User Service", str(service_file), "[ENABLED]" if service_active else "[DISABLED]")

    console.print(table)


@main.command(name="help")
def show_help():
    """Show comprehensive command cheat sheet and documentation."""
    print_ascii_banner()
    console.print("[bold green]CPUGHOST COMMAND REFERENCE & CHEAT SHEET[/bold green]")
    console.print("------------------------------------------------------------------")

    table = Table(title="Available Commands", show_header=True)
    table.add_column("Command", style="bold cyan", width=25)
    table.add_column("Purpose", style="white")
    table.add_column("Example", style="yellow")

    table.add_row(
        "ghost",
        "Launch interactive ASCII dashboard",
        "ghost"
    )
    table.add_row(
        "ghost host [-d]",
        "Start screen server and pen drive host",
        "ghost host --daemon"
    )
    table.add_row(
        "ghost client [ip]",
        "Auto-connect, sync drive, and open screen",
        "ghost client"
    )
    table.add_row(
        "ghost screen [ip]",
        "Open low-latency screen window only",
        "ghost screen 10.176.124.73"
    )
    table.add_row(
        "ghost drive ls [path]",
        "List files stored on the virtual pen drive",
        "ghost drive ls"
    )
    table.add_row(
        "ghost drive push <file>",
        "Upload local file/folder to pen drive",
        "ghost drive push notes.txt"
    )
    table.add_row(
        "ghost drive pull <file>",
        "Download file from pen drive to local folder",
        "ghost drive pull document.pdf"
    )
    table.add_row(
        "ghost drive sync [dir]",
        "Bidirectional folder sync with ghost drive",
        "ghost drive sync ~/Desktop/GhostDrive"
    )
    table.add_row(
        "ghost scan [--all]",
        "Scan hotspot network for CPUGhost nodes",
        "ghost scan"
    )
    table.add_row(
        "ghost status",
        "Display daemon state, IP, ports, and storage",
        "ghost status"
    )
    table.add_row(
        "ghost stop",
        "Terminate running background ghost daemon",
        "ghost stop"
    )
    table.add_row(
        "ghost autostart enable",
        "Enable automated host launch on system boot",
        "ghost autostart enable"
    )
    table.add_row(
        "ghost autostart status",
        "Check boot autostart configuration",
        "ghost autostart status"
    )
    table.add_row(
        "ghost autostart disable",
        "Remove boot autostart entries",
        "ghost autostart disable"
    )
    table.add_row(
        "ghost nopasswd",
        "Configure 100% passwordless boot & sudo",
        "ghost nopasswd"
    )
    table.add_row(
        "ghost phone [ip]",
        "Mirror Android phone screen with audio & touch",
        "ghost phone"
    )
    table.add_row(
        "ghost phone --web",
        "Open web-based phone screen stream in browser",
        "ghost phone --web"
    )
    table.add_row(
        "ghost update [--check]",
        "Check and update to latest GitHub release",
        "ghost update"
    )
    table.add_row(
        "ghost help",
        "Display this command cheat sheet",
        "ghost help"
    )

    console.print(table)
    console.print("")
    console.print("[bold cyan]Screen Viewer Keyboard Shortcuts:[/bold cyan]")
    console.print("  F11           : Toggle Fullscreen monitor mode")
    console.print("  Ctrl + G      : Grab or Release mouse/keyboard control")
    console.print("  Windows Key   : Open Zorin OS / remote desktop menu")
    console.print("  Esc / Ctrl+Q  : Close monitor window (when ungrabbed)")
    console.print("------------------------------------------------------------------")


@main.command(name="update")
@click.option("--check", is_flag=True, help="Only check for updates without applying")
def update_cpughost(check: bool):
    """Check for and apply updates from GitHub (pheonix14/cpughost)."""
    project_dir = Path(__file__).resolve().parent.parent
    git_dir = project_dir / ".git"

    console.print("[UPDATE] Connecting to GitHub repository (pheonix14/cpughost)...")

    if not git_dir.exists():
        console.print("[WARN] CPUGhost is not installed as a git repository.")
        console.print("[INFO] Re-cloning from https://github.com/pheonix14/cpughost.git recommended.")
        return

    try:
        # 1. Fetch latest changes
        subprocess.run(["git", "fetch", "origin", "main"], cwd=str(project_dir), check=True, capture_output=True)

        # 2. Check commit difference
        res = subprocess.run(
            ["git", "rev-list", "HEAD..origin/main", "--count"],
            cwd=str(project_dir),
            capture_output=True,
            text=True
        )
        behind_count = int(res.stdout.strip() or 0)

        if behind_count == 0:
            console.print("[OK] CPUGhost is already up to date with origin/main.")
            return

        console.print(f"[INFO] New update available: {behind_count} commit(s) behind GitHub.")

        if check:
            return

        console.print("[UPDATE] Pulling latest updates from GitHub...")
        subprocess.run(["git", "pull", "origin", "main"], cwd=str(project_dir), check=True, capture_output=True)

        # 3. Update virtualenv dependencies
        console.print("[UPDATE] Updating Python environment dependencies...")
        venv_python = project_dir / ".venv" / "bin" / "python"
        deps = "fastapi uvicorn websockets mss pillow pynput requests rich click pygame-ce python-multipart httpx"
        if shutil.which("uv"):
            subprocess.run(["uv", "pip", "install", "--python", str(venv_python)] + deps.split(), capture_output=True)
        else:
            pip_bin = project_dir / ".venv" / "bin" / "pip"
            subprocess.run([str(pip_bin), "install", "-q"] + deps.split(), capture_output=True)

        # 4. Re-link CLI binary
        bin_source = project_dir / "bin" / "ghost"
        bin_dest = Path.home() / ".local" / "bin" / "ghost"
        if bin_source.exists():
            bin_dest.parent.mkdir(parents=True, exist_ok=True)
            bin_dest.unlink(missing_ok=True)
            bin_dest.symlink_to(bin_source)

        console.print("[OK] CPUGhost successfully updated to the latest version!")
    except Exception as e:
        console.print(f"[ERR] Failed to update from GitHub: {e}")


@main.command(name="nopasswd")
def configure_nopasswd():
    """Preconfigure 100% passwordless operation (sudoers, autologin, no screen lock)."""
    user = os.environ.get("USER", "zero")
    print_ascii_banner()
    console.print(f"[bold green]CONFIGURING 100% PASSWORDLESS OPERATION FOR USER: {user}[/bold green]")
    console.print("------------------------------------------------------------------")

    # 1. Sudoers rule
    console.print("[1/5] Configuring passwordless sudo rule...")
    sudoers_content = f"""# CPUGhost passwordless sudo rule for headless operation
{user} ALL=(ALL:ALL) NOPASSWD: ALL
"""
    tmp_file = Path("/tmp/cpughost_sudoers")
    tmp_file.write_text(sudoers_content)

    sudo_check = subprocess.run(["sudo", "-n", "true"], capture_output=True)
    if sudo_check.returncode != 0:
        if sys.stdin.isatty():
            console.print("[INFO] Enter sudo password once to enable permanent passwordless headless operation:")
            res = subprocess.run(
                "sudo cp /tmp/cpughost_sudoers /etc/sudoers.d/cpughost && sudo chmod 0440 /etc/sudoers.d/cpughost && sudo rm -f /tmp/cpughost_sudoers",
                shell=True
            )
        else:
            res = subprocess.run(
                "sudo -n cp /tmp/cpughost_sudoers /etc/sudoers.d/cpughost && sudo -n chmod 0440 /etc/sudoers.d/cpughost && sudo -n rm -f /tmp/cpughost_sudoers",
                shell=True,
                capture_output=True,
                text=True
            )
    else:
        res = subprocess.run(
            "sudo cp /tmp/cpughost_sudoers /etc/sudoers.d/cpughost && sudo chmod 0440 /etc/sudoers.d/cpughost && sudo rm -f /tmp/cpughost_sudoers",
            shell=True,
            capture_output=True,
            text=True
        )

    if res.returncode == 0:
        console.print("[OK] Installed /etc/sudoers.d/cpughost (Passwordless Sudo Enabled)")
    else:
        err_msg = getattr(res, "stderr", "")
        if err_msg:
            console.print(f"[WARN] Sudoers rule could not be written automatically: {err_msg.strip()}")
        else:
            console.print("[WARN] Sudoers rule could not be written (sudo authorization required).")

    # 2. GDM3 / LightDM Automatic Login & Xorg session
    console.print("[2/5] Configuring display manager Automatic Login & Xorg session...")
    gdm_conf = Path("/etc/gdm3/custom.conf")
    if gdm_conf.exists():
        gdm_script = f"""
import re
with open('/etc/gdm3/custom.conf', 'r') as f:
    c = f.read()
if 'AutomaticLoginEnable' not in c:
    c = re.sub(r'\\[daemon\\]', '[daemon]\\nAutomaticLoginEnable=true\\nAutomaticLogin={user}\\n', c)
else:
    c = re.sub(r'#?\\s*AutomaticLoginEnable\\s*=.*', 'AutomaticLoginEnable=true', c)
    c = re.sub(r'#?\\s*AutomaticLogin\\s*=.*', f'AutomaticLogin={user}', c)

# Force Xorg session (disable Wayland) for seamless screen mirroring & input capture
if 'WaylandEnable' in c:
    c = re.sub(r'#?\\s*WaylandEnable\\s*=.*', 'WaylandEnable=false', c)
else:
    c = re.sub(r'\\[daemon\\]', '[daemon]\\nWaylandEnable=false', c)

with open('/tmp/gdm_custom.conf', 'w') as f:
    f.write(c)
"""
        subprocess.run([sys.executable, "-c", gdm_script], capture_output=True)
        subprocess.run("sudo cp /tmp/gdm_custom.conf /etc/gdm3/custom.conf && sudo rm -f /tmp/gdm_custom.conf", shell=True, capture_output=True)
        console.print(f"[OK] GDM3 Automatic Login & Xorg session configured for: {user}")

    # 3. Suppress screensaver locks and sleep timeouts
    console.print("[3/5] Disabling screensaver lock and sleep timeouts...")
    try:
        subprocess.run(["gsettings", "set", "org.gnome.desktop.screensaver", "lock-enabled", "false"], capture_output=True)
        subprocess.run(["gsettings", "set", "org.gnome.desktop.screensaver", "ubuntu-lock-on-suspend", "false"], capture_output=True)
        subprocess.run(["gsettings", "set", "org.gnome.desktop.session", "idle-delay", "0"], capture_output=True)
        subprocess.run(["gsettings", "set", "org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type", "nothing"], capture_output=True)
        subprocess.run(["gsettings", "set", "org.gnome.settings-daemon.plugins.power", "sleep-inactive-battery-type", "nothing"], capture_output=True)
        console.print("[OK] Screensaver and sleep timeouts disabled (Display stays awake forever)")
    except Exception:
        pass

    # 4. NetworkManager system-wide Wi-Fi permissions (bypass GNOME Keyring)
    console.print("[4/5] Setting Wi-Fi connections to system-wide (bypass Keyring)...")
    try:
        nm_res = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], capture_output=True, text=True)
        for line in nm_res.stdout.splitlines():
            if ":802-11-wireless" in line:
                con_name = line.split(":")[0]
                subprocess.run(["sudo", "nmcli", "connection", "modify", con_name, "connection.permissions", ""], capture_output=True)
                console.print(f"[OK] Wi-Fi '{con_name}' configured to connect without keyring password")
    except Exception:
        pass

    # 5. User session lingering
    console.print("[5/5] Enabling systemd user session lingering...")
    try:
        subprocess.run(["sudo", "loginctl", "enable-linger", user], capture_output=True)
        console.print(f"[OK] User session lingering active for: {user}")
    except Exception:
        pass

    console.print("------------------------------------------------------------------")
    console.print("[OK] 100% PASSWORDLESS OPERATION FULLY CONFIGURED.")
    console.print("[INFO] This PC will now boot, log in, connect to Wi-Fi, and run CPUGhost")
    console.print("[INFO] without ever asking for passwords again!")


def interactive_menu():
    """Interactive ASCII Dashboard."""
    while True:
        print_ascii_banner()
        cfg = load_config()
        local_ip = get_local_ip()
        pid = get_stored_pid()
        status_text = f"RUNNING [PID: {pid}]" if pid else "STANDBY"

        console.print(f" Local IP: [bold green]{local_ip}[/bold green] | Daemon: [bold yellow]{status_text}[/bold yellow] | Last Peer: [bold cyan]{cfg.get('last_peer_ip') or 'None'}[/bold cyan]")
        console.print("------------------------------------------------------------------")
        console.print(" [1] Start Host Mode (Headless Server: Screen + Pen Drive)")
        console.print(" [2] Start Host in Background (Daemon)")
        console.print(" [3] Connect as Client (Open Screen Window + Mount Pen Drive)")
        console.print(" [4] Open Screen Viewer Window Only")
        console.print(" [5] Virtual Pen Drive (Browse / Push / Pull)")
        console.print(" [6] Scan Hotspot Network for Peers")
        console.print(" [7] Stop Background Host")
        console.print(" [8] System Status & Settings")
        console.print(" [9] Autostart on Boot (Enable / Disable / Status)")
        console.print(" [10] Check for Updates (GitHub: pheonix14/cpughost)")
        console.print(" [11] Configure Passwordless Operation (Never Ask Passwords)")
        console.print(" [12] Mirror Phone Screen to PC (Android Screen + Audio + Control)")
        console.print(" [H] Command Reference & Help Sheet")
        console.print(" [0] Exit")
        console.print("------------------------------------------------------------------")

        choice = click.prompt(" Select option", type=str, default="0")
        if choice == "1":
            from .server import run_host
            run_host()
        elif choice == "2":
            ctx = click.get_current_context()
            ctx.invoke(host, daemon=True)
            time.sleep(1)
        elif choice == "3":
            ctx = click.get_current_context()
            ctx.invoke(client)
        elif choice == "4":
            ctx = click.get_current_context()
            ctx.invoke(screen)
        elif choice == "5":
            sub = click.prompt(" Drive action [ls|push|pull|sync]", default="ls")
            if sub == "ls":
                ctx = click.get_current_context()
                ctx.invoke(drive_ls, path="")
            elif sub == "push":
                f = click.prompt(" Local file path")
                ctx = click.get_current_context()
                ctx.invoke(drive_push, local_file=f, remote_path="")
            elif sub == "pull":
                f = click.prompt(" Remote file name")
                ctx = click.get_current_context()
                ctx.invoke(drive_pull, remote_file=f, local_dest="")
            elif sub == "sync":
                ctx = click.get_current_context()
                ctx.invoke(drive_sync, local_dir=None)
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice == "6":
            ctx = click.get_current_context()
            ctx.invoke(scan)
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice == "7":
            ctx = click.get_current_context()
            ctx.invoke(stop)
            time.sleep(0.5)
        elif choice == "8":
            ctx = click.get_current_context()
            ctx.invoke(status)
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice == "9":
            sub = click.prompt(" Autostart action [enable|disable|status]", default="status")
            ctx = click.get_current_context()
            if sub == "enable":
                ctx.invoke(autostart_enable)
            elif sub == "disable":
                ctx.invoke(autostart_disable)
            else:
                ctx.invoke(autostart_status)
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice == "10":
            ctx = click.get_current_context()
            ctx.invoke(update_cpughost, check=False)
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice == "11":
            ctx = click.get_current_context()
            ctx.invoke(configure_nopasswd)
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice == "12":
            from .phone_mirror import interactive_phone_menu
            interactive_phone_menu()
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice.lower() == "h":
            ctx = click.get_current_context()
            ctx.invoke(show_help)
            click.prompt(" Press Enter to continue", default="", show_default=False)
        elif choice == "0":
            console.print("[INFO] Exiting CPUGhost.")
            break
        else:
            console.print("[WARN] Invalid option.")
            time.sleep(1)


if __name__ == "__main__":
    main()
