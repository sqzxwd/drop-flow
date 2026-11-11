"""FastAPI application for DropFlow: chunked file streaming, WebSocket rooms, and clipboard sync."""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import aiofiles
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src import __version__
from src.clipboard import ClipboardManager
from src.network import get_local_ip
import qrcode
import qrcode.image.svg
from fastapi.responses import Response

# 1MB buffer chunk for high-speed, low-memory streaming of large files (5-10GB+)
STREAM_CHUNK_SIZE = 1024 * 1024


def sanitize_filename(filename: Optional[str]) -> str:
    """Sanitize filename to prevent directory traversal and cross-platform issues."""
    if not filename:
        return f"unnamed_file_{int(time.time())}"

    # Normalize Windows backslashes and strip path components
    normalized = filename.replace("\\", "/")
    name = Path(normalized).name

    # Remove null bytes and illegal control characters
    name = name.replace("\x00", "")
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "_", name).strip(". ")

    # Handle Windows reserved device names
    stem = Path(name).stem.upper()
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if stem in reserved_names:
        name = f"safe_{name}"

    return name or f"unnamed_file_{int(time.time())}"


def format_file_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    num = float(size_bytes)
    for unit in ["KB", "MB", "GB", "TB"]:
        num /= 1024.0
        if num < 1024.0:
            return f"{num:.2f} {unit}"
    return f"{num:.2f} PB"


def get_unique_path(downloads_dir: Path, filename: str) -> Path:
    """
    Generate a non-colliding destination path inside downloads_dir.
    If 'file.txt' exists, generates 'file (1).txt', 'file (2).txt', etc.
    """
    clean_name = sanitize_filename(filename)
    resolved_dir = downloads_dir.resolve()
    target_path = (resolved_dir / clean_name).resolve()

    # Safety check: ensure target stays inside downloads_dir
    try:
        if not target_path.is_relative_to(resolved_dir):
            raise HTTPException(status_code=400, detail="Path traversal detected in filename")
    except AttributeError:
        if resolved_dir not in target_path.parents and target_path != resolved_dir:
            raise HTTPException(status_code=400, detail="Path traversal detected in filename")

    if not target_path.exists():
        return target_path

    stem = target_path.stem
    suffix = target_path.suffix
    counter = 1
    while True:
        candidate = resolved_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


class ConnectionManager:
    """Manages active WebSocket connections grouped into isolated rooms."""

    def __init__(self) -> None:
        self.active_rooms: Dict[str, Set[WebSocket]] = {}
        self.socket_rooms: Dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, room_id: str) -> None:
        await websocket.accept()
        async with self._lock:
            if room_id not in self.active_rooms:
                self.active_rooms[room_id] = set()
            self.active_rooms[room_id].add(websocket)
            self.socket_rooms[websocket] = room_id

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            room_id = self.socket_rooms.pop(websocket, None)
            if room_id and room_id in self.active_rooms:
                self.active_rooms[room_id].discard(websocket)
                if not self.active_rooms[room_id]:
                    del self.active_rooms[room_id]

    async def broadcast(
        self, room_id: str, message: dict, exclude: Optional[WebSocket] = None
    ) -> None:
        async with self._lock:
            sockets = list(self.active_rooms.get(room_id, []))

        dead = []
        for sock in sockets:
            if sock is exclude:
                continue
            try:
                await sock.send_json(message)
            except Exception:
                dead.append(sock)

        for d in dead:
            await self.disconnect(d)

    async def send_personal(self, websocket: WebSocket, message: dict) -> None:
        try:
            await websocket.send_json(message)
        except Exception:
            await self.disconnect(websocket)

    def get_room_clients_count(self, room_id: str) -> int:
        return len(self.active_rooms.get(room_id, []))


def get_file_info(path: Path) -> dict:
    """Get metadata dictionary for a stored file."""
    stat = path.stat()
    return {
        "name": path.name,
        "size_bytes": stat.st_size,
        "size_human": format_file_size(stat.st_size),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "download_url": f"/api/download/{path.name}",
    }


def list_files_in_dir(downloads_dir: Path) -> List[dict]:
    """Return sorted list of files in downloads directory."""
    if not downloads_dir.exists():
        return []
    files = []
    for item in downloads_dir.iterdir():
        if item.is_file() and not item.name.startswith("."):
            try:
                files.append(get_file_info(item))
            except OSError:
                pass
    # Sort newest first
    files.sort(key=lambda x: x["modified_at"], reverse=True)
    return files


def create_app(
    downloads_dir: Optional[Path] = None,
    default_room: str = "general",
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="drop-flow",
        version=__version__,
        description="Production-ready local file transfer and clipboard sync service",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    storage_dir = (downloads_dir or (Path.cwd() / "downloads")).resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)

    app.state.downloads_dir = storage_dir
    app.state.default_room = default_room
    app.state.start_time = time.time()
    app.state.ws_manager = ConnectionManager()
    app.state.clipboard_manager = ClipboardManager()

    static_dir = (Path(__file__).parent / "static").resolve()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health_check() -> dict:
        """Health check endpoint."""
        return {
            "status": "ok",
            "service": "drop-flow",
            "version": __version__,
            "uptime_seconds": round(time.time() - app.state.start_time, 2),
            "storage_dir": str(app.state.downloads_dir),
            "active_rooms": len(app.state.ws_manager.active_rooms),
        }

    @app.get("/api/info")
    async def get_system_info(room: str = Query(default=default_room)) -> dict:
        """Get system, network, and room connection info."""
        return {
            "service": "drop-flow",
            "version": __version__,
            "local_ip": get_local_ip(),
            "room": room,
            "connected_clients": app.state.ws_manager.get_room_clients_count(room),
            "storage_dir": str(app.state.downloads_dir),
            "total_files": len(list_files_in_dir(app.state.downloads_dir)),
        }

    @app.get("/api/qr")
    async def get_qr_code(url: Optional[str] = Query(default=None)) -> Response:
        """Generate SVG QR code for mobile connection."""
        target_url = url or f"http://{get_local_ip()}:8000"
        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(target_url, image_factory=factory)
        svg_content = img.to_string()
        return Response(content=svg_content, media_type="image/svg+xml")

    @app.get("/api/files")
    async def get_files() -> List[dict]:
        """List all available downloadable files."""
        return list_files_in_dir(app.state.downloads_dir)

    @app.post("/api/upload", status_code=201)
    async def upload_files(
        files: List[UploadFile] = File(...),
        room: str = Query(default=default_room),
    ) -> dict:
        """
        Upload one or more files using chunked streaming.
        Saves files directly to disk in 1MB chunks to ensure minimal RAM usage
        for files up to 5-10+ GB.
        """
        uploaded = []
        for file in files:
            safe_dest = get_unique_path(app.state.downloads_dir, file.filename or "uploaded_file")
            # Stream write chunk by chunk
            async with aiofiles.open(safe_dest, "wb") as out_f:
                while chunk := await file.read(STREAM_CHUNK_SIZE):
                    await out_f.write(chunk)

            file_info = get_file_info(safe_dest)
            uploaded.append(file_info)

            # Broadcast live update to room
            await app.state.ws_manager.broadcast(
                room,
                {
                    "type": "file_uploaded",
                    "file": file_info,
                },
            )

        return {"uploaded": uploaded}

    @app.get("/api/download/{filename:path}")
    async def download_file(filename: str) -> FileResponse:
        """
        Stream file download with path traversal protection.
        Uses Starlette's FileResponse for chunked async streaming and Range header support.
        """
        clean_name = sanitize_filename(filename)
        resolved_dir = app.state.downloads_dir.resolve()
        target_path = (resolved_dir / clean_name).resolve()

        # Path traversal protection
        try:
            if not target_path.is_relative_to(resolved_dir):
                raise HTTPException(status_code=400, detail="Invalid path: path traversal detected")
        except AttributeError:
            if resolved_dir not in target_path.parents and target_path != resolved_dir:
                raise HTTPException(status_code=400, detail="Invalid path: path traversal detected")

        if not target_path.exists() or not target_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(
            path=str(target_path),
            filename=target_path.name,
            media_type="application/octet-stream",
        )

    @app.delete("/api/files/{filename:path}")
    async def delete_file(
        filename: str, room: str = Query(default=default_room)
    ) -> dict:
        """Delete a file safely with path traversal protection."""
        clean_name = sanitize_filename(filename)
        resolved_dir = app.state.downloads_dir.resolve()
        target_path = (resolved_dir / clean_name).resolve()

        try:
            if not target_path.is_relative_to(resolved_dir):
                raise HTTPException(status_code=400, detail="Invalid path: path traversal detected")
        except AttributeError:
            if resolved_dir not in target_path.parents and target_path != resolved_dir:
                raise HTTPException(status_code=400, detail="Invalid path: path traversal detected")

        if not target_path.exists() or not target_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        target_path.unlink()

        # Broadcast file deletion
        await app.state.ws_manager.broadcast(
            room,
            {
                "type": "file_deleted",
                "filename": target_path.name,
            },
        )
        return {"status": "deleted", "filename": target_path.name}

    @app.get("/api/clipboard")
    async def get_clipboard_history() -> List[dict]:
        """Fetch clipboard history."""
        return [i.to_dict() for i in app.state.clipboard_manager.get_history()]

    @app.websocket("/ws")
    @app.websocket("/ws/{room_id}")
    async def websocket_endpoint(
        websocket: WebSocket, room_id: Optional[str] = None
    ) -> None:
        """WebSocket endpoint for real-time room communication and clipboard sync."""
        room = room_id or default_room
        manager: ConnectionManager = app.state.ws_manager
        clipboard: ClipboardManager = app.state.clipboard_manager

        await manager.connect(websocket, room)

        # Notify others in room of new peer & broadcast updated client count
        peer_count = manager.get_room_clients_count(room)
        await manager.broadcast(
            room,
            {"type": "peer_joined", "room": room, "client_count": peer_count},
        )

        try:
            # Send initial state to the newly connected client
            await manager.send_personal(
                websocket,
                {
                    "type": "init",
                    "room": room,
                    "client_count": peer_count,
                    "clipboard_history": [i.to_dict() for i in clipboard.get_history()],
                    "files": list_files_in_dir(app.state.downloads_dir),
                },
            )

            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")

                if msg_type == "ping":
                    await manager.send_personal(
                        websocket,
                        {"type": "pong", "client_timestamp": data.get("timestamp")},
                    )

                elif msg_type == "clipboard_send":
                    content = data.get("content", "")
                    sender_id = data.get("sender_id", "anonymous")

                    item, err = clipboard.process_incoming(content, sender_id)
                    if item:
                        # Broadcast clipboard update to everyone in the room
                        await manager.broadcast(
                            room,
                            {
                                "type": "clipboard_update",
                                "item": item.to_dict(),
                            },
                        )
                    elif err != "duplicate":
                        # Send rejection reason back to sender only (unless it was just a duplicate)
                        await manager.send_personal(
                            websocket,
                            {
                                "type": "clipboard_rejected",
                                "reason": err,
                            },
                        )

                elif msg_type == "get_files":
                    await manager.send_personal(
                        websocket,
                        {
                            "type": "files_list",
                            "files": list_files_in_dir(app.state.downloads_dir),
                        },
                    )

        except WebSocketDisconnect:
            await manager.disconnect(websocket)
            peer_count = manager.get_room_clients_count(room)
            await manager.broadcast(
                room,
                {"type": "peer_left", "room": room, "client_count": peer_count},
            )
        except Exception:
            await manager.disconnect(websocket)

    @app.get("/", response_class=HTMLResponse)
    async def index_page() -> HTMLResponse:
        """Serve the DropFlow single-page application."""
        index_file = static_dir / "index.html"
        if index_file.exists():
            async with aiofiles.open(index_file, "r", encoding="utf-8") as f:
                content = await f.read()
            return HTMLResponse(content)
        return HTMLResponse("<h1>DropFlow API is running</h1>")

    return app


# Default app instance
app = create_app()
