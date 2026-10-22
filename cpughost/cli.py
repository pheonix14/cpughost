import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import sys
import time
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .config import load_config, save_config, get_local_ip, CONFIG_DIR
from .discovery import GhostDiscovery, scan_hotspot_subnet
from .drive_client import GhostDriveClient
from .screen_viewer import GhostScreenViewer
from .server import run_host

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


def resolve_host_ip(ip_arg: Optional[str]) -> Optional[str]:
    """Resolve host IP via argument, discovery beacon, or subnet scan."""
    if ip_arg:
        return ip_arg

    cfg = load_config()
    last_ip = cfg.get("last_peer_ip")

    console.print("[DISCOVERY] Scanning hotspot for CPUGhost host beacon...")
    disc = GhostDiscovery(port=cfg.get("discovery_port", 8989))
    disc.start_listener()
    host_info = disc.find_host(timeout=2.0)
    disc.stop()

    if host_info:
        host_ip = host_info["ip"]
        console.print(f"[OK] Discovered CPUGhost Host at: {host_ip}")
        cfg["last_peer_ip"] = host_ip
        save_config(cfg)
        return host_ip

    # Fallback to active subnet scan
    console.print("[DISCOVERY] No beacon received, probing local subnet...")
    active_ips = scan_hotspot_subnet(port=cfg.get("api_port", 8888))
    if active_ips:
        host_ip = active_ips[0]
        console.print(f"[OK] Found CPUGhost node via subnet scan: {host_ip}")
        cfg["last_peer_ip"] = host_ip
        save_config(cfg)
        return host_ip

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
        interactive_menu()


@main.command()
@click.option("--daemon", "-d", is_flag=True, help="Run host daemon in background")
def host(daemon: bool):
    """Start host server (Screen sharing + Virtual Pen Drive)."""
    pid = get_stored_pid()
    if pid:
        console.print(f"[WARN] Host already running with PID {pid}. Run 'ghost stop' first.")
        return

    if daemon:
        # Launch daemonized process
        python_bin = sys.executable
        project_dir = str(Path(__file__).resolve().parent.parent)
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{project_dir}:{env.get('PYTHONPATH', '')}"
        env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
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
        run_host()


@main.command()
@click.argument("ip", required=False)
def client(ip: Optional[str] = None):
    """Connect to ghost host: mount pen drive and launch screen window."""
    host_ip = resolve_host_ip(ip)
    if not host_ip:
        return

    cfg = load_config()
    mount_dir = Path(cfg.get("mount_point", str(Path.home() / "Desktop" / "GhostDrive")))
    mount_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[DRIVE] Syncing virtual pen drive at: {mount_dir}")
    client_driver = GhostDriveClient(host_ip, port=cfg.get("api_port", 8888))
    client_driver.sync_once(mount_dir)
    console.print(f"[OK] Pen drive synchronized: {mount_dir}")

    console.print(f"[SCREEN] Opening remote screen viewer for {host_ip}...")
    viewer = GhostScreenViewer(host_ip, port=cfg.get("stream_port", 8889))
    viewer.start()


@main.command()
@click.argument("ip", required=False)
def screen(ip: Optional[str] = None):
    """Open remote screen viewer window only."""
    host_ip = resolve_host_ip(ip)
    if not host_ip:
        return

    cfg = load_config()
    console.print(f"[SCREEN] Launching screen viewer window -> {host_ip}")
    viewer = GhostScreenViewer(host_ip, port=cfg.get("stream_port", 8889))
    viewer.start()


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


@main.group()
def autostart():
    """Configure CPUGhost to start automatically on system boot."""
    pass


@autostart.command(name="enable")
def autostart_enable():
    """Enable CPUGhost Host autostart on boot/login."""
    ghost_bin = shutil.which("ghost") or str(Path.home() / ".local" / "bin" / "ghost")
    if not Path(ghost_bin).exists():
        ghost_bin = str(Path(__file__).resolve().parent.parent / "bin" / "ghost")

    # 1. XDG Autostart (.desktop)
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = autostart_dir / "cpughost.desktop"
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
    console.print(f"[OK] Created XDG autostart entry: {desktop_file}")

    # 2. Systemd user service
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_file = systemd_dir / "cpughost.service"
    service_content = f"""[Unit]
Description=CPUGhost Headless Dual-PC Host Service
After=network.target network-online.target

[Service]
Type=simple
ExecStart={ghost_bin} host
Restart=on-failure
RestartSec=5s
Environment=PYTHONUNBUFFERED=1

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

    console.print("[OK] CPUGhost will now start automatically whenever this PC turns on.")


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
        console.print(" [H] Command Reference & Help Sheet")
        console.print(" [0] Exit")
        console.print("------------------------------------------------------------------")

        choice = click.prompt(" Select option", type=str, default="0")
        if choice == "1":
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
