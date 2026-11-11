"""Integration tests for DropFlow FastAPI endpoints and WebSocket sync."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.server import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Create an isolated TestClient backed by a temporary downloads directory."""
    app = create_app(downloads_dir=tmp_path, default_room="general")
    return TestClient(app)


def test_health_and_index(client: TestClient) -> None:
    """Verify health endpoint, system info, QR SVG, and index HTML page."""
    health_res = client.get("/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert health_data["status"] == "ok"
    assert health_data["service"] == "drop-flow"

    info_res = client.get("/api/info")
    assert info_res.status_code == 200
    info_data = info_res.json()
    assert info_data["room"] == "general"
    assert info_data["total_files"] == 0

    qr_res = client.get("/api/qr")
    assert qr_res.status_code == 200
    assert "image/svg+xml" in qr_res.headers["content-type"]
    assert "<svg" in qr_res.text

    index_res = client.get("/")
    assert index_res.status_code == 200
    assert "DropFlow" in index_res.text
    assert "/api/upload" in index_res.text


def test_upload_and_download_single_file(client: TestClient, tmp_path: Path) -> None:
    """Verify single file upload streams to disk and downloads identically."""
    payload = b"Hello DropFlow! Binary bytes: \x00\x01\x02\xff"
    upload_res = client.post(
        "/api/upload",
        files=[("files", ("sample.bin", io.BytesIO(payload), "application/octet-stream"))],
    )
    assert upload_res.status_code == 201
    uploaded = upload_res.json()["uploaded"]
    assert len(uploaded) == 1
    assert uploaded[0]["name"] == "sample.bin"
    assert uploaded[0]["size_bytes"] == len(payload)
    assert (tmp_path / "sample.bin").read_bytes() == payload

    # Verify files listing
    list_res = client.get("/api/files")
    assert list_res.status_code == 200
    files = list_res.json()
    assert len(files) == 1
    assert files[0]["name"] == "sample.bin"

    # Verify download
    dl_res = client.get("/api/download/sample.bin")
    assert dl_res.status_code == 200
    assert dl_res.content == payload


def test_upload_multiple_and_duplicate_names(client: TestClient, tmp_path: Path) -> None:
    """Verify multiple uploads and automatic collision renaming."""
    res1 = client.post(
        "/api/upload",
        files=[
            ("files", ("notes.txt", io.BytesIO(b"first"), "text/plain")),
            ("files", ("image.png", io.BytesIO(b"pngdata"), "image/png")),
        ],
    )
    assert res1.status_code == 201
    assert len(res1.json()["uploaded"]) == 2

    # Upload duplicate filename
    res2 = client.post(
        "/api/upload",
        files=[("files", ("notes.txt", io.BytesIO(b"second"), "text/plain"))],
    )
    assert res2.status_code == 201
    renamed = res2.json()["uploaded"][0]["name"]
    assert renamed == "notes (1).txt"
    assert (tmp_path / "notes.txt").read_bytes() == b"first"
    assert (tmp_path / "notes (1).txt").read_bytes() == b"second"


def test_large_chunked_upload_and_download(client: TestClient) -> None:
    """Verify multi-megabyte file crosses STREAM_CHUNK_SIZE boundary accurately."""
    payload = b"A" * (2 * 1024 * 1024 + 512)  # > 2 MB
    upload_res = client.post(
        "/api/upload",
        files=[("files", ("large.dat", io.BytesIO(payload), "application/octet-stream"))],
    )
    assert upload_res.status_code == 201
    assert upload_res.json()["uploaded"][0]["size_bytes"] == len(payload)

    dl_res = client.get("/api/download/large.dat")
    assert dl_res.status_code == 200
    assert dl_res.content == payload


def test_download_missing_and_path_traversal_sanitization(client: TestClient, tmp_path: Path) -> None:
    """Verify 404 on missing files and safe filename sanitization on traversal attempts."""
    assert client.get("/api/download/does_not_exist.txt").status_code == 404

    upload_res = client.post(
        "/api/upload",
        files=[("files", ("../../evil.txt", io.BytesIO(b"safe"), "text/plain"))],
    )
    assert upload_res.status_code == 201
    saved_name = upload_res.json()["uploaded"][0]["name"]
    assert saved_name == "evil.txt"
    assert (tmp_path / "evil.txt").exists()


def test_delete_file(client: TestClient, tmp_path: Path) -> None:
    """Verify file deletion removes file from disk and returns 404 afterwards."""
    client.post(
        "/api/upload",
        files=[("files", ("temp.txt", io.BytesIO(b"bye"), "text/plain"))],
    )
    assert (tmp_path / "temp.txt").exists()

    del_res = client.delete("/api/files/temp.txt")
    assert del_res.status_code == 200
    assert del_res.json() == {"status": "deleted", "filename": "temp.txt"}
    assert not (tmp_path / "temp.txt").exists()

    assert client.delete("/api/files/temp.txt").status_code == 404


def test_websocket_init_ping_and_clipboard(client: TestClient) -> None:
    """Verify WebSocket connection handshake, ping/pong, clipboard sync, and validation."""
    with client.websocket_connect("/ws/room1") as ws:
        joined = ws.receive_json()
        assert joined["type"] == "peer_joined"
        assert joined["room"] == "room1"
        assert joined["client_count"] == 1

        init = ws.receive_json()
        assert init["type"] == "init"
        assert init["room"] == "room1"
        assert init["files"] == []
        assert init["clipboard_history"] == []

        # Ping / Pong
        ws.send_json({"type": "ping", "timestamp": 987654})
        pong = ws.receive_json()
        assert pong == {"type": "pong", "client_timestamp": 987654}

        # Clipboard send (URL detection)
        ws.send_json({
            "type": "clipboard_send",
            "content": "https://example.com/drop",
            "sender_id": "tester_1",
        })
        clip_msg = ws.receive_json()
        assert clip_msg["type"] == "clipboard_update"
        assert clip_msg["item"]["content"] == "https://example.com/drop"
        assert clip_msg["item"]["content_type"] == "url"

        # Empty clipboard rejected
        ws.send_json({"type": "clipboard_send", "content": "", "sender_id": "tester_1"})
        rej_msg = ws.receive_json()
        assert rej_msg == {"type": "clipboard_rejected", "reason": "empty"}

        # Request files list via WebSocket
        ws.send_json({"type": "get_files"})
        files_msg = ws.receive_json()
        assert files_msg == {"type": "files_list", "files": []}

    # Verify clipboard history persisted in REST endpoint
    hist_res = client.get("/api/clipboard")
    assert hist_res.status_code == 200
    history = hist_res.json()
    assert len(history) == 1
    assert history[0]["content"] == "https://example.com/drop"


def test_websocket_multi_client_broadcast_and_file_events(client: TestClient) -> None:
    """Verify room broadcasts across two WebSocket clients for upload, delete, and disconnect."""
    with client.websocket_connect("/ws/shared") as ws1:
        assert ws1.receive_json()["type"] == "peer_joined"
        assert ws1.receive_json()["type"] == "init"

        with client.websocket_connect("/ws/shared") as ws2:
            # ws1 receives peer_joined for ws2
            p_join_1 = ws1.receive_json()
            assert p_join_1["type"] == "peer_joined"
            assert p_join_1["client_count"] == 2

            # ws2 receives peer_joined and init
            assert ws2.receive_json()["type"] == "peer_joined"
            init2 = ws2.receive_json()
            assert init2["type"] == "init"
            assert init2["client_count"] == 2

            # Upload file to the same room triggers broadcast to both clients
            up_res = client.post(
                "/api/upload?room=shared",
                files=[("files", ("sync.txt", io.BytesIO(b"sync payload"), "text/plain"))],
            )
            assert up_res.status_code == 201

            ev1 = ws1.receive_json()
            ev2 = ws2.receive_json()
            assert ev1["type"] == "file_uploaded"
            assert ev1["file"]["name"] == "sync.txt"
            assert ev2["type"] == "file_uploaded"
            assert ev2["file"]["name"] == "sync.txt"

            # Delete file triggers file_deleted broadcast
            del_res = client.delete("/api/files/sync.txt?room=shared")
            assert del_res.status_code == 200

            del1 = ws1.receive_json()
            del2 = ws2.receive_json()
            assert del1 == {"type": "file_deleted", "filename": "sync.txt"}
            assert del2 == {"type": "file_deleted", "filename": "sync.txt"}

        # After ws2 exits context, ws1 should receive peer_left
        left_msg = ws1.receive_json()
        assert left_msg["type"] == "peer_left"
        assert left_msg["client_count"] == 1
