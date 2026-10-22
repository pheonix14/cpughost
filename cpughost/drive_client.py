import os
import sys
import time
import shutil
import hashlib
from pathlib import Path
from typing import List, Optional
import requests
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, TransferSpeedColumn

from .config import load_config

console = Console()


def get_file_hash(path: Path) -> str:
    """Compute quick MD5 checksum for file sync comparison."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


class GhostDriveClient:
    def __init__(self, host_ip: str, port: int = 8888):
        self.host_ip = host_ip
        self.port = port
        self.base_url = f"http://{host_ip}:{port}/api/drive"

    def status(self) -> Optional[dict]:
        try:
            r = requests.get(f"{self.base_url}/status", timeout=2.5)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def list_files(self, remote_path: str = "") -> List[dict]:
        try:
            r = requests.get(f"{self.base_url}/ls", params={"path": remote_path}, timeout=5.0)
            if r.status_code == 200:
                return r.json().get("items", [])
            else:
                console.print(f"[ERR] Error listing files: {r.text}")
        except Exception as e:
            console.print(f"[ERR] Failed to connect to GhostDrive at {self.host_ip}: {e}")
        return []

    def push(self, local_path: str, remote_path: str = "") -> bool:
        src = Path(local_path).resolve()
        if not src.exists():
            console.print(f"[ERR] Local file not found: {local_path}")
            return False

        if src.is_dir():
            # Recursively upload directory
            success = True
            for root, dirs, files in os.walk(src):
                rel_dir = os.path.relpath(root, src)
                dest_dir = (Path(remote_path) / rel_dir).as_posix() if rel_dir != "." else remote_path
                for f in files:
                    file_path = os.path.join(root, f)
                    if not self.push(file_path, dest_dir):
                        success = False
            return success

        file_size = src.stat().st_size
        console.print(f"[DRIVE] Pushing: {src.name} ({self._format_size(file_size)}) -> {self.host_ip}")

        try:
            with open(src, "rb") as f:
                with Progress(
                    TextColumn("[bold blue]{task.fields[filename]}"),
                    BarColumn(),
                    "[progress.percentage]{task.percentage:>3.1f}%",
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                ) as progress:
                    task_id = progress.add_task("upload", total=file_size, filename=src.name)

                    class ProgressReader:
                        def __init__(self, fp, progress_obj, tid):
                            self.fp = fp
                            self.p = progress_obj
                            self.tid = tid

                        def read(self, size=-1):
                            chunk = self.fp.read(size)
                            if chunk:
                                self.p.update(self.tid, advance=len(chunk))
                            return chunk

                        def __len__(self):
                            return file_size

                    reader = ProgressReader(f, progress, task_id)
                    files = {"file": (src.name, reader)}
                    data = {"path": remote_path}
                    r = requests.post(f"{self.base_url}/upload", files=files, data=data, timeout=600)

                    if r.status_code == 200:
                        console.print(f"[OK] Pushed: {src.name}")
                        return True
                    else:
                        console.print(f"[ERR] Push failed: {r.text}")
                        return False
        except Exception as e:
            console.print(f"[ERR] Push failed: {e}")
            return False

    def pull(self, remote_file: str, local_dest: str = "") -> bool:
        filename = Path(remote_file).name
        dest = Path(local_dest).resolve() if local_dest else Path.cwd()
        if dest.is_dir():
            target_path = dest / filename
        else:
            target_path = dest

        console.print(f"[DRIVE] Pulling: {remote_file} from {self.host_ip}...")

        try:
            with requests.get(f"{self.base_url}/download", params={"path": remote_file}, stream=True, timeout=600) as r:
                if r.status_code != 200:
                    console.print(f"[ERR] Pull failed: {r.status_code} {r.text}")
                    return False

                total_size = int(r.headers.get("content-length", 0))

                with open(target_path, "wb") as f:
                    with Progress(
                        TextColumn("[bold cyan]{task.fields[filename]}"),
                        BarColumn(),
                        "[progress.percentage]{task.percentage:>3.1f}%",
                        TransferSpeedColumn(),
                        TimeRemainingColumn(),
                    ) as progress:
                        task_id = progress.add_task("download", total=total_size, filename=filename)
                        for chunk in r.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                                progress.update(task_id, advance=len(chunk))

            console.print(f"[OK] Pulled: {target_path}")
            return True
        except Exception as e:
            console.print(f"[ERR] Pull error: {e}")
            return False

    def print_ls(self, remote_path: str = ""):
        items = self.list_files(remote_path)
        if not items:
            console.print(f"[INFO] Empty directory or unable to fetch files from {self.host_ip}")
            return

        table = Table(title=f"CPUGhost Drive: {self.host_ip}:{remote_path or '/'}")
        table.add_column("Type", style="cyan", width=8)
        table.add_column("Name", style="bold white")
        table.add_column("Size", style="green", justify="right")
        table.add_column("Last Modified", style="yellow")

        for item in items:
            t = "[DIR]" if item["is_dir"] else "[FILE]"
            size = "-" if item["is_dir"] else self._format_size(item["size"])
            table.add_row(t, item["name"], size, item["modified"])

        console.print(table)

    def sync_once(self, local_dir: Path) -> None:
        """Mirror remote drive state into local directory."""
        local_dir = Path(local_dir).resolve()
        local_dir.mkdir(parents=True, exist_ok=True)

        remote_items = self.list_files("")
        for item in remote_items:
            rel = item["rel_path"]
            loc = local_dir / rel
            if item["is_dir"]:
                loc.mkdir(parents=True, exist_ok=True)
            else:
                # If local doesn't exist or size mismatch, pull
                if not loc.exists() or loc.stat().st_size != item["size"]:
                    self.pull(rel, str(loc.parent))

        # Check local files that do not exist remotely and push them
        for f in local_dir.glob("**/*"):
            if f.is_file():
                rel = str(f.relative_to(local_dir))
                # Check if in remote_items
                exists_remote = any(it["rel_path"] == rel for it in remote_items)
                if not exists_remote:
                    parent_rel = str(f.parent.relative_to(local_dir))
                    if parent_rel == ".":
                        parent_rel = ""
                    self.push(str(f), parent_rel)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
