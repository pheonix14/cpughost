import os
import shutil
import mimetypes
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

drive_router = APIRouter(prefix="/api/drive", tags=["drive"])


def get_storage_path(base_dir: str, rel_path: str = "") -> Path:
    """Resolve and sanitize path inside storage directory."""
    base = Path(base_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    # Remove leading slashes
    clean_rel = rel_path.lstrip("/")
    target = (base / clean_rel).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Path traversal forbidden")
    return target


def create_drive_routes(storage_dir: str) -> APIRouter:
    router = APIRouter(prefix="/api/drive", tags=["drive"])

    @router.get("/status")
    def drive_status():
        base = Path(storage_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        total, used, free = shutil.disk_usage(base)
        return {
            "status": "online",
            "storage_path": str(base),
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "total_gb": round(total / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
        }

    @router.get("/ls")
    def list_files(path: str = ""):
        target = get_storage_path(storage_dir, path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path not found")

        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

        items = []
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                stat = entry.stat()
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": stat.st_size if entry.is_file() else 0,
                    "mtime": stat.st_mtime,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "rel_path": str(entry.relative_to(Path(storage_dir).resolve()))
                })
            except Exception:
                continue

        return {
            "current_path": path,
            "items": items
        }

    @router.get("/download")
    def download_file(path: str):
        target = get_storage_path(storage_dir, path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        mime_type, _ = mimetypes.guess_type(str(target))
        return FileResponse(
            path=str(target),
            media_type=mime_type or "application/octet-stream",
            filename=target.name
        )

    @router.post("/upload")
    async def upload_file(path: str = Form(""), file: UploadFile = File(...)):
        dest_dir = get_storage_path(storage_dir, path)
        if not dest_dir.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
        elif not dest_dir.is_dir():
            raise HTTPException(status_code=400, detail="Destination is not a directory")

        dest_file = dest_dir / file.filename
        try:
            with open(dest_file, "wb") as buffer:
                while True:
                    chunk = await file.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    buffer.write(chunk)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
        finally:
            await file.close()

        stat = dest_file.stat()
        return {
            "status": "success",
            "name": file.filename,
            "size": stat.st_size,
            "path": str(dest_file.relative_to(Path(storage_dir).resolve()))
        }

    @router.post("/mkdir")
    def make_directory(path: str = Form(...)):
        target = get_storage_path(storage_dir, path)
        target.mkdir(parents=True, exist_ok=True)
        return {"status": "success", "path": path}

    @router.post("/delete")
    def delete_item(path: str = Form(...)):
        target = get_storage_path(storage_dir, path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Item not found")

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return {"status": "success", "deleted": path}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router
