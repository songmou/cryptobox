from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from cryptobox.api import create_app
from cryptobox.crypto import encrypt_file
from cryptobox.index import VaultIndex
from cryptobox.scanner import StatusTracker, scan_and_encrypt
from cryptobox.service import RuntimeState
from cryptobox.settings import load_last_root
from cryptobox.vault import VaultManager
import cryptobox.api as api_module


def prepared_file_runtime(root: Path, name: str, payload: bytes) -> RuntimeState:
    (root / name).write_bytes(payload)
    session = VaultManager(root).create("correct horse battery staple")
    index = VaultIndex(session.index_path, session.derive_key(b"index"))
    scan_and_encrypt(root, session, index, StatusTracker())
    index.close()
    runtime = RuntimeState(root)
    runtime.attach_session(session)
    runtime.tracker.reset("ready")
    runtime.tracker.finish("ready")
    return runtime


def prepared_runtime(root: Path) -> tuple[RuntimeState, bytes]:
    payload = (b"0123456789abcdef" * 4096) + b"tail"
    runtime = prepared_file_runtime(root, "movie.mp4", payload)
    return runtime, payload


def test_bootstrap_is_one_time_and_range_is_exact(tmp_path: Path) -> None:
    runtime, payload = prepared_runtime(tmp_path)
    app = create_app(runtime, "one-time-token")
    with TestClient(app) as client:
        response = client.get("/?token=one-time-token", follow_redirects=False)
        assert response.status_code == 303
        response = client.get("/?token=one-time-token", follow_redirects=False)
        assert response.status_code == 403

        status = client.get("/api/status")
        assert status.status_code == 200
        tree = client.get("/api/tree")
        file_id = tree.json()["entries"][0]["id"]
        ranged = client.get(f"/api/content/{file_id}", headers={"Range": "bytes=4090-8200"})
        assert ranged.status_code == 206
        assert ranged.content == payload[4090:8201]
        assert ranged.headers["content-range"] == f"bytes 4090-8200/{len(payload)}"

        csrf = status.json()["csrf"]
        ticket = client.post(
            "/api/export-ticket",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"ids": [""]},
        )
        assert ticket.status_code == 200
        archive_response = client.get(ticket.json()["url"])
        with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
            assert archive.read("movie.mp4") == payload
        assert client.get(ticket.json()["url"]).status_code == 404


def test_pdf_content_is_inline_and_same_origin_embeddable(tmp_path: Path) -> None:
    payload = b"%PDF-1.4\n% minimal preview fixture\n"
    runtime = prepared_file_runtime(tmp_path, "REPORT.PDF", payload)
    app = create_app(runtime, "pdf-token")
    with TestClient(app) as client:
        index = client.get("/?token=pdf-token")
        assert "frame-ancestors 'none'" in index.headers["content-security-policy"]

        tree = client.get("/api/tree").json()
        entry = tree["entries"][0]
        assert entry["media_type"] == "application/pdf"
        assert entry["preview_kind"] == "pdf"

        content = client.get(f"/api/content/{entry['id']}")
        assert content.content == payload
        assert content.headers["content-type"] == "application/pdf"
        assert content.headers["content-disposition"].startswith("inline;")
        assert "frame-ancestors 'self'" in content.headers["content-security-policy"]
        assert "frame-ancestors 'none'" not in content.headers["content-security-policy"]

        preview_host = client.get("/static/preview-host.html")
        preview_policy = preview_host.headers["content-security-policy"]
        assert "connect-src 'none'" in preview_policy
        assert "frame-ancestors 'self'" in preview_policy

        app_script = client.get("/static/app.js").text
        assert "switchRootButton" in app_script
        assert "尝试以文本打开" in app_script
        assert "FILE_ICONS" in app_script


def test_host_and_csrf_are_enforced(tmp_path: Path) -> None:
    runtime, _ = prepared_runtime(tmp_path)
    app = create_app(runtime, "token")
    with TestClient(app) as client:
        assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
        client.get("/?token=token")
        assert client.post("/api/rescan").status_code == 403


def test_web_initialization_requires_matching_passwords(tmp_path: Path) -> None:
    payload = b"created through the web initialization flow"
    (tmp_path / "note.txt").write_bytes(payload)
    runtime = RuntimeState(tmp_path)
    app = create_app(runtime, "init-token")
    with TestClient(app) as client:
        client.get("/?token=init-token")
        status = client.get("/api/status").json()
        csrf = status["csrf"]
        rejected = client.post(
            "/api/init",
            headers={"X-Cryptobox-CSRF": csrf},
            json={
                "password": "short",
                "password_confirmation": "different",
            },
        )
        assert rejected.status_code == 400
        assert (tmp_path / "note.txt").read_bytes() == payload

        accepted = client.post(
            "/api/init",
            headers={"X-Cryptobox-CSRF": csrf},
            json={
                "password": "x",
                "password_confirmation": "x",
            },
        )
        assert accepted.status_code == 200
        for _ in range(50):
            current = client.get("/api/status").json()
            if current["operation"]["phase"] in {"ready", "error"}:
                break
            time.sleep(0.05)
        assert current["operation"]["phase"] == "ready"
        assert (tmp_path / "note.txt").read_bytes()[:8] == b"CRBOXF01"
        client.post("/api/lock", headers={"X-Cryptobox-CSRF": csrf})


def test_web_recognizes_vault_when_metadata_is_missing(tmp_path: Path) -> None:
    payload = b"recover through the normal unlock page"
    protected = tmp_path / "recover.txt"
    protected.write_bytes(payload)
    manager = VaultManager(tmp_path)
    session = manager.create("p")
    encrypt_file(protected, session)
    manager.meta_path.unlink()

    runtime = RuntimeState(tmp_path)
    app = create_app(runtime, "recover-token")
    with TestClient(app) as client:
        client.get("/?token=recover-token")
        status = client.get("/api/status")
        assert status.json()["initialized"] is True
        csrf = status.json()["csrf"]
        unlocked = client.post(
            "/api/unlock",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"password": "p"},
        )
        assert unlocked.status_code == 200
        assert manager.meta_path.is_file()


def test_web_password_change_rewraps_files_and_rebuilds_index(tmp_path: Path) -> None:
    runtime, payload = prepared_runtime(tmp_path)
    app = create_app(runtime, "password-token")
    with TestClient(app) as client:
        client.get("/?token=password-token")
        status = client.get("/api/status").json()
        csrf = status["csrf"]
        changed = client.post(
            "/api/password",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"new_password": "n", "confirmation": "n"},
        )
        assert changed.status_code == 200
        for _ in range(50):
            if client.get("/api/status").json()["operation"]["phase"] in {"ready", "error"}:
                break
            time.sleep(0.05)
        assert client.post("/api/lock", headers={"X-Cryptobox-CSRF": csrf}).status_code == 200
        assert client.post(
            "/api/unlock",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"password": "correct horse battery staple"},
        ).status_code == 401
        assert client.post(
            "/api/unlock", headers={"X-Cryptobox-CSRF": csrf}, json={"password": "n"}
        ).status_code == 200
        for _ in range(50):
            if client.get("/api/status").json()["operation"]["phase"] in {"ready", "error"}:
                break
            time.sleep(0.05)
        tree = client.get("/api/tree").json()
        file_id = tree["entries"][0]["id"]
        assert client.get(f"/api/content/{file_id}").content == payload


def test_tree_shows_encrypted_and_plain_files_but_plain_content_is_rejected(tmp_path: Path) -> None:
    runtime = prepared_file_runtime(tmp_path, "protected.txt", b"protected")
    plain = tmp_path / "arrived-later.bin"
    plain.write_bytes(b"not encrypted yet")
    app = create_app(runtime, "tree-state-token")

    with TestClient(app) as client:
        client.get("/?token=tree-state-token")
        entries = {item["name"]: item for item in client.get("/api/tree").json()["entries"]}
        assert entries["protected.txt"]["encrypted"] is True
        assert entries["protected.txt"]["size"] == len(b"protected")
        assert entries["arrived-later.bin"]["encrypted"] is False
        assert entries["arrived-later.bin"]["size"] == len(b"not encrypted yet")
        assert client.get(f"/api/content/{entries['arrived-later.bin']['id']}").status_code == 409
        assert client.get(f"/api/download/{entries['arrived-later.bin']['id']}").status_code == 409


def test_change_root_locks_current_vault_and_remembers_new_root(tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()
    settings = tmp_path / "settings.json"
    runtime = prepared_file_runtime(old_root, "old.txt", b"old")
    runtime.settings_path = settings
    app = create_app(runtime, "switch-token")

    with TestClient(app) as client:
        client.get("/?token=switch-token")
        csrf = client.get("/api/status").json()["csrf"]
        relative = client.put(
            "/api/root", headers={"X-Cryptobox-CSRF": csrf}, json={"path": "relative/path"}
        )
        assert relative.status_code == 400
        switched = client.put(
            "/api/root", headers={"X-Cryptobox-CSRF": csrf}, json={"path": str(new_root)}
        )
        assert switched.status_code == 200
        status = client.get("/api/status").json()
        assert status["root"] == str(new_root.resolve())
        assert status["unlocked"] is False
        assert status["initialized"] is False
        assert load_last_root(settings) == new_root.resolve()


def test_tree_hides_current_executable_only(tmp_path: Path, monkeypatch) -> None:
    runtime = prepared_file_runtime(tmp_path, "visible.txt", b"visible")
    executable = tmp_path / "cryptobox-running"
    executable.write_bytes(b"application")
    monkeypatch.setattr(api_module, "current_executable", lambda: executable)
    app = create_app(runtime, "executable-token")

    with TestClient(app) as client:
        client.get("/?token=executable-token")
        names = {item["name"] for item in client.get("/api/tree").json()["entries"]}
        assert names == {"visible.txt"}
