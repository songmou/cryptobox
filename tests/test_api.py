from __future__ import annotations

import io
import os
import time
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cryptobox.api import create_app
from cryptobox.crypto import encrypt_file
from cryptobox.index import VaultIndex
from cryptobox.scanner import StatusTracker, scan_and_encrypt
from cryptobox.service import RuntimeState
from cryptobox.settings import load_last_root, load_settings, save_last_root
from cryptobox.vault import VaultManager
import cryptobox.api as api_module
import cryptobox


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


def test_web_version_matches_project_version(tmp_path: Path) -> None:
    project_file = Path(__file__).parents[1] / "pyproject.toml"
    with project_file.open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]
    runtime = RuntimeState(tmp_path)
    app = create_app(runtime, "version-token")

    with TestClient(app) as client:
        assert cryptobox.__version__ == project_version
        assert client.get("/api/version").json() == {"version": project_version}


def test_static_ui_integrates_workspace_actions_into_header_and_settings(tmp_path: Path) -> None:
    runtime = RuntimeState(tmp_path)
    app = create_app(runtime, "static-ui-token")

    with TestClient(app) as client:
        client.get("/?token=static-ui-token")
        html = client.get("/").text
        script = client.get("/static/app.js").text

    header = html[html.index('<header class="topbar">'):html.index("</header>")]
    assert all(item in header for item in ("previewTitle", "drawerButton", "downloadButton", "exportFolderButton"))
    assert 'id="settingsButton" class="icon-button settings-button hidden"' in header
    assert '<form id="unlockForm" class="stack hidden" autocomplete="off">' in html
    assert '<form id="initForm" class="stack hidden" autocomplete="off">' in html
    assert html.count('type="password" autocomplete="off"') == 3
    settings_start = html.index('<section id="settingsDialog"')
    settings_dialog = html[settings_start:html.index("</form>", settings_start)]
    assert all(item in settings_dialog for item in ("verifyButton", "passwordButton", "lockButton", "shutdownButton"))
    assert "content-head" not in html
    assert "moreButton" not in html
    assert "moreMenu" not in html
    assert 'if (!state.status?.unlocked || state.locking) return;' in script
    assert '$("#settingsButton").classList.add("hidden")' in script
    assert '$("#unlockPassword").value = "";' in script
    assert '$("#initPassword").value = $("#initConfirmation").value = "";' in script


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


def test_heic_content_uses_native_image_preview_without_conversion(tmp_path: Path) -> None:
    payload = b"\x00\x00\x00\x18ftypheic" + bytes(range(64))
    runtime = prepared_file_runtime(tmp_path, "CAPTURE.HEIC", payload)
    app = create_app(runtime, "heic-token")

    with TestClient(app) as client:
        client.get("/?token=heic-token")
        entry = client.get("/api/tree").json()["entries"][0]
        assert entry["preview_kind"] == "image"
        assert entry["media_type"] == "image/heic"

        content = client.get(f"/api/content/{entry['id']}")
        assert content.status_code == 200
        assert content.content == payload
        assert content.headers["content-type"] == "image/heic"
        assert content.headers["content-disposition"].startswith("inline;")


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


def test_tree_uses_full_path_stat_to_match_encrypted_index_on_windows(
    tmp_path: Path, monkeypatch
) -> None:
    payload = b"encrypted content remains available through the API"
    runtime = prepared_file_runtime(tmp_path, "protected.txt", payload)
    real_scandir = api_module.os.scandir

    class EntryWithUnreliableWindowsStat:
        def __init__(self, entry: os.DirEntry[str]):
            self._entry = entry

        def __getattr__(self, name: str):
            return getattr(self._entry, name)

        def stat(self, *, follow_symlinks: bool = True):
            actual = self._entry.stat(follow_symlinks=follow_symlinks)
            return SimpleNamespace(
                st_dev=0,
                st_ino=0,
                st_size=actual.st_size,
                st_mtime=actual.st_mtime,
                st_mtime_ns=actual.st_mtime_ns,
            )

    class ScandirWithUnreliableWindowsStat:
        def __init__(self, path: Path):
            self._entries = real_scandir(path)

        def __enter__(self):
            self._entries.__enter__()
            return self

        def __exit__(self, *args: object):
            return self._entries.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            return EntryWithUnreliableWindowsStat(next(self._entries))

    monkeypatch.setattr(
        api_module.os,
        "scandir",
        lambda path: ScandirWithUnreliableWindowsStat(Path(path)),
    )
    app = create_app(runtime, "windows-stat-token")

    with TestClient(app) as client:
        client.get("/?token=windows-stat-token")
        entry = client.get("/api/tree").json()["entries"][0]

        assert entry["encrypted"] is True
        assert entry["size"] == len(payload)
        assert client.get(f"/api/content/{entry['id']}").content == payload
        assert client.get(f"/api/download/{entry['id']}").content == payload


def test_change_root_locks_current_vault_and_remembers_new_root(tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()
    settings = tmp_path / "settings.json"
    runtime = prepared_file_runtime(old_root, "old.txt", b"old")
    runtime.settings_path = settings
    save_last_root(settings, old_root)
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


def test_settings_api_validates_and_preserves_preferences_across_root_change(tmp_path: Path) -> None:
    old_root = tmp_path / "old-settings-root"
    new_root = tmp_path / "new-settings-root"
    old_root.mkdir()
    new_root.mkdir()
    settings_path = tmp_path / "settings.json"
    save_last_root(settings_path, old_root)
    runtime = RuntimeState(old_root, settings_path=settings_path)
    runtime.attach_session(VaultManager(old_root).create("settings password"))
    app = create_app(runtime, "settings-token")

    with TestClient(app) as client:
        client.get("/?token=settings-token")
        status = client.get("/api/status").json()
        csrf = status["csrf"]
        assert client.get("/api/settings").json() == {
            "root": str(old_root.resolve()),
            "auto_lock_minutes": 3,
            "theme": "system",
        }
        assert client.put(
            "/api/settings",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"auto_lock_minutes": 20, "theme": "dark"},
        ).json()["theme"] == "dark"
        assert client.put(
            "/api/settings",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"auto_lock_minutes": 0, "theme": "dark"},
        ).status_code == 422
        assert client.put(
            "/api/settings",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"auto_lock_minutes": 3, "theme": "neon"},
        ).status_code == 422
        assert client.put(
            "/api/settings",
            json={"auto_lock_minutes": 3, "theme": "light"},
        ).status_code == 403
        assert client.put(
            "/api/root",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"path": str(new_root)},
        ).status_code == 200

    stored = load_settings(settings_path)
    assert stored.last_root == new_root.resolve()
    assert stored.auto_lock_minutes == 20
    assert stored.theme == "dark"


def test_locked_vault_cannot_save_settings(tmp_path: Path) -> None:
    root = tmp_path / "locked-settings-root"
    root.mkdir()
    settings_path = tmp_path / "locked-settings.json"
    save_last_root(settings_path, root)
    runtime = RuntimeState(root, settings_path=settings_path)
    app = create_app(runtime, "locked-settings-token")

    with TestClient(app) as client:
        client.get("/?token=locked-settings-token")
        status = client.get("/api/status").json()
        assert status["unlocked"] is False
        assert client.get("/api/settings").status_code == 200
        response = client.put(
            "/api/settings",
            headers={"X-Cryptobox-CSRF": status["csrf"]},
            json={"auto_lock_minutes": 20, "theme": "dark"},
        )
        assert response.status_code == 423

    stored = load_settings(settings_path)
    assert stored.auto_lock_minutes == 3
    assert stored.theme == "system"


def test_activity_api_resets_authoritative_auto_lock_deadline(tmp_path: Path) -> None:
    runtime = prepared_file_runtime(tmp_path, "protected.txt", b"protected")
    app = create_app(runtime, "activity-token")

    with TestClient(app) as client:
        client.get("/?token=activity-token")
        status = client.get("/api/status").json()
        assert 1 <= status["auto_lock_remaining_seconds"] <= 180
        csrf = status["csrf"]

        assert client.post("/api/activity").status_code == 403
        runtime.auto_lock_deadline -= 30
        before = client.get("/api/status").json()["auto_lock_remaining_seconds"]
        refreshed = client.post(
            "/api/activity", headers={"X-Cryptobox-CSRF": csrf}
        )
        assert refreshed.status_code == 200
        remaining = refreshed.json()["auto_lock_remaining_seconds"]
        assert remaining > before
        assert 179 <= remaining <= 180

        assert client.post(
            "/api/lock", headers={"X-Cryptobox-CSRF": csrf}
        ).status_code == 200
        assert client.get("/api/status").json()["auto_lock_remaining_seconds"] is None
        assert client.post(
            "/api/activity", headers={"X-Cryptobox-CSRF": csrf}
        ).status_code == 423


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
