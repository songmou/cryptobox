from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import pytest
import cryptobox.crypto as crypto_module
import cryptobox.util as util_module

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


@pytest.mark.skipif(sys.platform != "win32", reason="read-only target only blocks os.replace on Windows")
def test_encrypt_succeeds_on_readonly_destination(tmp_path: Path) -> None:
    """A read-only plaintext must still be encrypted in place (WinError 5 fix)."""
    session = unlocked_vault(tmp_path)
    source = tmp_path / "readonly.pdf"
    source.write_bytes(b"confidential payload" * 200)
    os.chmod(source, 0o444)
    try:
        header = encrypt_file(source, session, chunk_size=4096)
    finally:
        # Restore writability so the test harness can clean up tmp_path.
        os.chmod(source, 0o644)
    assert source.read_bytes()[:8] == b"CRBOXF01"
    assert header.plain_size == len(b"confidential payload" * 200)
    assert b"".join(iter_decrypted(source, session)) == b"confidential payload" * 200


def test_atomic_replace_retries_on_transient_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_atomic_replace must retry a transient access error before giving up."""
    temporary = tmp_path / "tmp.bin"
    temporary.write_bytes(b"new-content")
    destination = tmp_path / "dest.bin"
    destination.write_bytes(b"old-content")

    calls = {"n": 0}
    real_replace = util_module.os.replace

    def flaky_replace(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            exc = OSError("Access is denied")
            exc.winerror = 5
            exc.errno = errno.EACCES
            raise exc
        return real_replace(src, dst)

    monkeypatch.setattr(util_module.os, "replace", flaky_replace)

    util_module._atomic_replace(temporary, destination)
    assert calls["n"] == 2
    assert destination.read_bytes() == b"new-content"
    assert not temporary.exists()


def test_atomic_replace_raises_non_access_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-access OSErrors (e.g. a bad path) must not be swallowed or retried."""
    temporary = tmp_path / "tmp.bin"
    temporary.write_bytes(b"x")
    destination = tmp_path / "dest.bin"
    destination.write_bytes(b"old")

    def boom(_src: Path, _dst: Path) -> None:
        raise OSError("something else entirely")

    monkeypatch.setattr(util_module.os, "replace", boom)

    with pytest.raises(OSError):
        util_module._atomic_replace(temporary, destination)
    assert destination.read_bytes() == b"old"
