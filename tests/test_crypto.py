from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import cryptobox.crypto as crypto_module

from cryptobox.constants import HEADER_SIZE
from cryptobox.crypto import (
    decrypt_to_path,
    encrypt_file,
    iter_decrypted,
    read_header_path,
    verify_file,
)
from cryptobox.errors import CorruptFile, InvalidPassword, InvalidVault
from cryptobox.vault import VaultManager

PASSWORD = "correct horse battery staple"


def unlocked_vault(root: Path):
    return VaultManager(root).create(PASSWORD)


@pytest.mark.parametrize(
    "payload", [b"", b"hello cryptobox", os.urandom(150_000)], ids=["empty", "small", "multi-chunk"]
)
def test_round_trip_and_export(tmp_path: Path, payload: bytes) -> None:
    session = unlocked_vault(tmp_path)
    source = tmp_path / "sample.bin"
    source.write_bytes(payload)

    header = encrypt_file(source, session, chunk_size=4096)

    assert source.read_bytes()[:8] == b"CRBOXF01"
    assert header.plain_size == len(payload)
    assert b"".join(iter_decrypted(source, session)) == payload
    assert verify_file(source, session, full=True).plain_size == len(payload)

    destination = tmp_path.parent / f"export-{tmp_path.name}.bin"
    decrypt_to_path(source, destination, session)
    assert destination.read_bytes() == payload


def test_range_decryption_reads_exact_plaintext(tmp_path: Path) -> None:
    session = unlocked_vault(tmp_path)
    payload = bytes(range(256)) * 100
    source = tmp_path / "video.mp4"
    source.write_bytes(payload)
    encrypt_file(source, session, chunk_size=4096)

    assert b"".join(iter_decrypted(source, session, 3990, 8501)) == payload[3990:8501]
    assert b"".join(iter_decrypted(source, session, len(payload), len(payload))) == b""


def test_tampered_header_and_chunk_are_rejected(tmp_path: Path) -> None:
    session = unlocked_vault(tmp_path)
    source = tmp_path / "data.bin"
    source.write_bytes(os.urandom(20_000))
    encrypt_file(source, session, chunk_size=4096)

    raw = bytearray(source.read_bytes())
    raw[120] ^= 1
    source.write_bytes(raw)
    with pytest.raises(CorruptFile):
        read_header_path(source, session)

    source.write_bytes(os.urandom(20_000))
    encrypt_file(source, session, chunk_size=4096)
    raw = bytearray(source.read_bytes())
    raw[HEADER_SIZE + 25] ^= 1
    source.write_bytes(raw)
    with pytest.raises(CorruptFile):
        list(iter_decrypted(source, session))


def test_truncated_and_appended_ciphertext_are_rejected(tmp_path: Path) -> None:
    session = unlocked_vault(tmp_path)
    source = tmp_path / "data.bin"
    source.write_bytes(os.urandom(12_000))
    encrypt_file(source, session, chunk_size=4096)
    source.write_bytes(source.read_bytes()[:-1])
    with pytest.raises(CorruptFile):
        verify_file(source, session, full=True)

    source.write_bytes(os.urandom(12_000))
    encrypt_file(source, session, chunk_size=4096)
    with source.open("ab") as handle:
        handle.write(b"trailing")
    with pytest.raises(CorruptFile):
        verify_file(source, session, full=True)


def test_wrong_password_and_password_change(tmp_path: Path) -> None:
    manager = VaultManager(tmp_path)
    session = manager.create(PASSWORD)
    protected = tmp_path / "protected.txt"
    protected.write_bytes(b"password changes only rewrap the authenticated file header")
    encrypt_file(protected, session)
    with pytest.raises(InvalidPassword):
        manager.unlock("definitely the wrong password")

    new_password = "a new and longer cryptobox password"
    manager.change_password(session, new_password)
    with pytest.raises(InvalidPassword):
        manager.unlock(PASSWORD)
    unlocked = manager.unlock(new_password)
    assert unlocked.master_key == session.master_key
    assert b"".join(iter_decrypted(protected, unlocked)) == (
        b"password changes only rewrap the authenticated file header"
    )


def test_timestamp_restore_is_portable_to_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows' os.utime does not accept the follow_symlinks keyword."""
    manager = VaultManager(tmp_path)
    session = manager.create(PASSWORD)
    protected = tmp_path / "portable.txt"
    protected.write_bytes(b"portable timestamp update")
    calls: list[tuple[Path, tuple[int, int]]] = []

    def portable_utime(path: Path, *, ns: tuple[int, int]) -> None:
        calls.append((Path(path), ns))

    monkeypatch.setattr(crypto_module.os, "utime", portable_utime)
    encrypt_file(protected, session)
    manager.change_password(session, "new-password")

    assert len(calls) == 2


def test_vault_metadata_can_be_rebuilt_from_encrypted_file_and_password(tmp_path: Path) -> None:
    manager = VaultManager(tmp_path)
    session = manager.create(PASSWORD)
    protected = tmp_path / "recoverable.bin"
    payload = os.urandom(12_345)
    protected.write_bytes(payload)
    encrypt_file(protected, session, chunk_size=4096)

    metadata = json.loads(manager.meta_path.read_text("utf-8"))
    assert metadata["key_mode"] == "password-derived-argon2id"
    assert "encrypted_master_key" not in metadata
    manager.meta_path.unlink()

    recovered_manager = VaultManager(tmp_path)
    assert recovered_manager.initialized
    with pytest.raises(InvalidPassword):
        recovered_manager.unlock("wrong")
    assert not recovered_manager.meta_path.exists()

    recovered = recovered_manager.unlock(PASSWORD)
    assert recovered_manager.meta_path.is_file()
    assert recovered.master_key == session.master_key
    assert b"".join(iter_decrypted(protected, recovered)) == payload


def test_control_directory_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (tmp_path / ".cryptobox").symlink_to(outside, target_is_directory=True)

    with pytest.raises(InvalidVault):
        VaultManager(tmp_path).create(PASSWORD)
    assert list(outside.iterdir()) == []
