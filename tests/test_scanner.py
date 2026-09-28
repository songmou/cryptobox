from __future__ import annotations

from pathlib import Path
import os

from cryptobox.crypto import iter_decrypted
from cryptobox.index import VaultIndex
from cryptobox.scanner import StatusTracker, scan_and_encrypt
from cryptobox.vault import VaultManager
import cryptobox.scanner as scanner_module


def test_scan_encrypts_plain_files_and_uses_cache(tmp_path: Path) -> None:
    payloads = {"a.txt": b"alpha", "nested/b.bin": bytes(range(255)) * 100}
    for name, payload in payloads.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    session = VaultManager(tmp_path).create("correct horse battery staple")
    index = VaultIndex(session.index_path, session.derive_key(b"index"))
    tracker = StatusTracker()

    first = scan_and_encrypt(tmp_path, session, index, tracker)
    assert first["phase"] == "ready"
    assert first["encrypted_files"] == 2
    for name, payload in payloads.items():
        assert b"".join(iter_decrypted(tmp_path / name, session)) == payload

    second = scan_and_encrypt(tmp_path, session, index, tracker)
    assert second["phase"] == "ready"
    assert second["cached_files"] == 2
    assert second["encrypted_files"] == 0
    index.close()


def test_scan_reports_progress_for_only_the_new_encryption_batch(tmp_path: Path, monkeypatch) -> None:
    cached = tmp_path / "cached.txt"
    cached.write_bytes(b"already protected")
    session = VaultManager(tmp_path).create("correct horse battery staple")
    index = VaultIndex(session.index_path, session.derive_key(b"index"))
    tracker = StatusTracker()
    assert scan_and_encrypt(tmp_path, session, index, tracker)["encrypted_files"] == 1

    (tmp_path / "new-a.txt").write_bytes(b"a" * 10)
    (tmp_path / "new-b.txt").write_bytes(b"b" * 20)
    original_encrypt = scanner_module.encrypt_file
    snapshots: list[dict[str, object]] = []

    def recording_encrypt(path: Path, active_session):
        snapshots.append(tracker.snapshot())
        return original_encrypt(path, active_session)

    monkeypatch.setattr(scanner_module, "encrypt_file", recording_encrypt)
    result = scan_and_encrypt(tmp_path, session, index, tracker)

    assert result["phase"] == "ready"
    assert result["cached_files"] == 1
    assert result["pending_files"] == 2
    assert result["encrypted_files"] == 2
    assert any(snapshot["pending_files"] == 2 for snapshot in snapshots)
    assert any(snapshot["encrypted_files"] == 0 for snapshot in snapshots)
    index.close()


def test_failed_new_file_does_not_advance_successful_encryption_progress(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "good.txt").write_bytes(b"good")
    (tmp_path / "bad.txt").write_bytes(b"bad")
    session = VaultManager(tmp_path).create("correct horse battery staple")
    index = VaultIndex(session.index_path, session.derive_key(b"index"))
    original_encrypt = scanner_module.encrypt_file

    def sometimes_fails(path: Path, active_session):
        if path.name == "bad.txt":
            raise OSError("simulated encryption failure")
        return original_encrypt(path, active_session)

    monkeypatch.setattr(scanner_module, "encrypt_file", sometimes_fails)
    result = scan_and_encrypt(tmp_path, session, index, StatusTracker())

    assert result["phase"] == "error"
    assert result["pending_files"] == 2
    assert result["encrypted_files"] == 1
    assert len(result["errors"]) == 1
    index.close()


def test_scan_validates_changed_ciphertext_before_encrypting_plaintext(tmp_path: Path) -> None:
    encrypted = tmp_path / "existing.bin"
    encrypted.write_bytes(b"existing")
    session = VaultManager(tmp_path).create("correct horse battery staple")
    index = VaultIndex(session.index_path, session.derive_key(b"index"))
    tracker = StatusTracker()
    assert scan_and_encrypt(tmp_path, session, index, tracker)["phase"] == "ready"

    plain = tmp_path / "new.txt"
    plain.write_bytes(b"must remain plaintext when validation fails")
    damaged = bytearray(encrypted.read_bytes())
    damaged[30] ^= 1
    encrypted.write_bytes(damaged)

    result = scan_and_encrypt(tmp_path, session, index, tracker)
    assert result["phase"] == "error"
    assert plain.read_bytes() == b"must remain plaintext when validation fails"
    index.close()


def test_hardlink_aborts_before_other_plain_files_are_changed(tmp_path: Path) -> None:
    original = tmp_path / "linked.txt"
    original.write_bytes(b"linked")
    os.link(original, tmp_path / "linked-again.txt")
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_bytes(b"ordinary remains untouched")
    session = VaultManager(tmp_path).create("correct horse battery staple")
    index = VaultIndex(session.index_path, session.derive_key(b"index"))

    result = scan_and_encrypt(tmp_path, session, index, StatusTracker())

    assert result["phase"] == "error"
    assert ordinary.read_bytes() == b"ordinary remains untouched"
    index.close()


def test_current_executable_is_skipped_but_sibling_is_encrypted(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "cryptobox-test"
    sibling = tmp_path / "document.txt"
    executable.write_bytes(b"running application")
    sibling.write_bytes(b"protect me")
    monkeypatch.setattr(scanner_module, "current_executable", lambda: executable)
    session = VaultManager(tmp_path).create("password")
    index = VaultIndex(session.index_path, session.derive_key(b"index"))

    result = scan_and_encrypt(tmp_path, session, index, StatusTracker())

    assert result["phase"] == "ready"
    assert executable.read_bytes() == b"running application"
    assert sibling.read_bytes().startswith(b"CRBOXF01")
    assert index.get_entry(Path("cryptobox-test")) is None
    index.close()
