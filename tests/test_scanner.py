from __future__ import annotations

from pathlib import Path
import os

from cryptobox.crypto import iter_decrypted
from cryptobox.index import VaultIndex
from cryptobox.scanner import StatusTracker, scan_and_encrypt
from cryptobox.vault import VaultManager


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
