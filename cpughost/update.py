"""
CPUGhost High-Power Auto-Update Engine.
Checks and upgrades CPUGhost to the latest GitHub release (pheonix14/cpughost).
Supports force updates, automatic environment healing, desktop package synchronization,
and background update detection. Zero emojis.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple
from rich.console import Console

console = Console()
GITHUB_REPO = "https://github.com/pheonix14/cpughost.git"


def get_current_version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:
        return "7.4.0"


def get_latest_remote_info(project_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Query GitHub for the latest release tag and commit hash on main branch.
    Returns (latest_tag, latest_commit_hash).
    """
    latest_tag = None
    latest_commit = None

    try:
        res_tags = subprocess.run(
            ["git", "ls-remote", "--tags", "origin"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=5.0
        )
        if res_tags.returncode == 0 and res_tags.stdout:
            tags = []
            for line in res_tags.stdout.splitlines():
                if "refs/tags/" in line and not line.endswith("^{}"):
                    t = line.split("refs/tags/")[-1].strip()
                    tags.append(t)
            if tags:
                latest_tag = tags[-1]
    except Exception:
        pass

    try:
        res_heads = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "main"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=5.0
        )
        if res_heads.returncode == 0 and res_heads.stdout:
            parts = res_heads.stdout.strip().split()
            if parts:
                latest_commit = parts[0]
    except Exception:
        pass

    return latest_tag, latest_commit


def sync_desktop_packages(project_dir: Path):
    """Keep desktop v2, v3, and GhostDrive zip synchronized."""
    desktop_dir = Path.home() / "Desktop"
    if not desktop_dir.exists():
        return

    v2_dir = desktop_dir / "v2"
    v3_dir = desktop_dir / "v3"
    drive_zip = desktop_dir / "GhostDrive" / "ghost-v7.zip"

    try:
        if v2_dir.exists():
            subprocess.run(
                ["rsync", "-a", "--exclude=.git", "--exclude=.venv", "--exclude=__pycache__", f"{project_dir}/", f"{v2_dir}/"],
                capture_output=True, timeout=10.0
            )
        if v3_dir.exists():
            subprocess.run(
                ["rsync", "-a", "--exclude=.git", "--exclude=.venv", "--exclude=__pycache__", f"{project_dir}/", f"{v3_dir}/"],
                capture_output=True, timeout=10.0
            )
        if drive_zip.parent.exists():
            subprocess.run(
                ["zip", "-q", "-r", str(drive_zip), "cpughost", "-x", "cpughost/.git/*", "-x", "cpughost/.venv/*", "-x", "*/__pycache__/*"],
                cwd=str(desktop_dir), capture_output=True, timeout=10.0
            )
    except Exception:
        pass


def run_full_update(force: bool = False, check_only: bool = False) -> bool:
    """
    Execute high-powered upgrade from GitHub repository.
    Handles git pull, stash auto-heal, dependencies reinstallation, and binary relinking.
    """
    project_dir = Path(__file__).resolve().parent.parent
    git_dir = project_dir / ".git"

    current_ver = get_current_version()
    console.print(f"[UPDATE] Current CPUGhost version: v{current_ver}")
    console.print(f"[UPDATE] Querying GitHub repository (pheonix14/cpughost)...")

    if not git_dir.exists():
        console.print("[ERR] CPUGhost is not installed as a git repository at: " + str(project_dir))
        return False

    latest_tag, latest_commit = get_latest_remote_info(project_dir)
    console.print(f"[INFO] Latest GitHub release tag: {latest_tag or 'unknown'}")

    # Check local commit
    try:
        local_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(project_dir), text=True).strip()
    except Exception:
        local_hash = ""

    is_behind = False
    try:
        subprocess.run(["git", "fetch", "origin", "main", "--tags"], cwd=str(project_dir), capture_output=True, timeout=10.0)
        diff_res = subprocess.check_output(["git", "rev-list", "HEAD..origin/main", "--count"], cwd=str(project_dir), text=True).strip()
        is_behind = int(diff_res or 0) > 0
    except Exception:
        pass

    if not is_behind and not force:
        console.print("[OK] CPUGhost is already up to date with origin/main.")
        return True

    if check_only:
        console.print(f"[INFO] Update available on GitHub! Run 'ghost update' to apply.")
        return True

    console.print("[UPDATE] Applying high-power update from GitHub origin/main...")

    try:
        if force:
            subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=str(project_dir), check=True, capture_output=True)
        else:
            res = subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(project_dir), capture_output=True, text=True)
            if res.returncode != 0:
                console.print("[WARN] Rebase conflict detected. Auto-healing to origin/main...")
                subprocess.run(["git", "rebase", "--abort"], cwd=str(project_dir), capture_output=True)
                subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=str(project_dir), check=True, capture_output=True)

        console.print("[OK] Git codebase updated.")

        # Update environment dependencies
        console.print("[UPDATE] Upgrading Python environment packages...")
        venv_python = project_dir / ".venv" / "bin" / "python"
        deps = "fastapi uvicorn websockets mss pillow pynput requests rich click pygame-ce python-multipart httpx"
        if shutil.which("uv"):
            subprocess.run(["uv", "pip", "install", "--python", str(venv_python)] + deps.split(), capture_output=True)
        else:
            pip_bin = project_dir / ".venv" / "bin" / "pip"
            if pip_bin.exists():
                subprocess.run([str(pip_bin), "install", "-q"] + deps.split(), capture_output=True)

        # Relink launcher executable
        bin_source = project_dir / "bin" / "ghost"
        bin_dest = Path.home() / ".local" / "bin" / "ghost"
        if bin_source.exists():
            bin_dest.parent.mkdir(parents=True, exist_ok=True)
            bin_dest.unlink(missing_ok=True)
            bin_dest.symlink_to(bin_source)

        # Sync desktop packages
        sync_desktop_packages(project_dir)

        new_ver = get_current_version()
        console.print(f"[OK] CPUGhost successfully upgraded to v{new_ver}!")
        return True

    except Exception as e:
        console.print(f"[ERR] Failed during update: {e}")
        return False
