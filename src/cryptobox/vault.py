from __future__ import annotations

import json
import hmac
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from .constants import (
    CONTROL_DIR,
    FILE_MAGIC,
    FORMAT_VERSION,
    HEADER_SIZE,
    INDEX_FILE,
    KDF_ITERATIONS,
    KDF_LANES,
    KDF_LENGTH,
    KDF_MEMORY_KIB,
    META_FILE,
)
from .errors import InvalidPassword, InvalidVault
from .util import b64d, b64e, fsync_directory

_PUBLIC_HEADER = struct.Struct(">8sHHI16s")
_DIRECT_KEY_MODE = "password-derived-argon2id"


def _derive_password_key(password: str, salt: bytes, kdf: dict[str, int]) -> bytes:
    encoded = bytearray(password.encode("utf-8"))
    try:
        return Argon2id(
            salt=salt,
            length=int(kdf["length"]),
            iterations=int(kdf["iterations"]),
            lanes=int(kdf["lanes"]),
            memory_cost=int(kdf["memory_kib"]),
        ).derive(encoded)
    finally:
        for index in range(len(encoded)):
            encoded[index] = 0


def _default_kdf() -> dict[str, int]:
    return {
        "algorithm": "argon2id",  # type: ignore[dict-item]
        "memory_kib": KDF_MEMORY_KIB,
        "iterations": KDF_ITERATIONS,
        "lanes": KDF_LANES,
        "length": KDF_LENGTH,
    }


def _direct_verifier(master_key: bytes, vault_id: bytes) -> bytes:
    return hmac.digest(master_key, b"cryptobox/direct-key-verifier/v1\0" + vault_id, "sha256")


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.urandom(6).hex()}")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(slots=True)
class VaultSession:
    root: Path
    vault_id: bytes
    master_key: bytes
    kdf: dict[str, int] = field(default_factory=_default_kdf)

    @property
    def control_dir(self) -> Path:
        return self.root / CONTROL_DIR

    @property
    def index_path(self) -> Path:
        return self.control_dir / INDEX_FILE

    def derive_key(self, purpose: bytes, length: int = 32) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=length,
            salt=self.vault_id,
            info=b"cryptobox/" + purpose + b"/v1",
        ).derive(self.master_key)

    def close(self) -> None:
        # Python immutable bytes cannot be guaranteed to be erased. Drop the
        # reference promptly; packaged builds must never serialize this object.
        self.master_key = b""


class VaultManager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._discovery_done = False
        self._recovery_candidate: tuple[Path, bytes] | None = None

    @property
    def control_dir(self) -> Path:
        return self.root / CONTROL_DIR

    @property
    def meta_path(self) -> Path:
        return self.control_dir / META_FILE

    @property
    def initialized(self) -> bool:
        if self.control_dir.is_symlink():
            return False
        return self.meta_path.is_file() or self._discover_recovery_candidate() is not None

    def _discover_recovery_candidate(self) -> tuple[Path, bytes] | None:
        if self._discovery_done:
            return self._recovery_candidate
        self._discovery_done = True
        stack = [self.root]
        while stack:
            directory = stack.pop()
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    path = Path(entry.path)
                    if path == self.control_dir or entry.is_symlink():
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        with path.open("rb") as handle:
                            public = handle.read(_PUBLIC_HEADER.size)
                        if len(public) != _PUBLIC_HEADER.size:
                            continue
                        magic, version, header_size, flags, vault_id = _PUBLIC_HEADER.unpack(public)
                        if (
                            magic == FILE_MAGIC
                            and version == FORMAT_VERSION
                            and header_size == HEADER_SIZE
                            and flags == 0
                        ):
                            self._recovery_candidate = (path, vault_id)
                            return self._recovery_candidate
                    except OSError:
                        continue
        return None

    def _validate_control_dir(self) -> None:
        if self.control_dir.is_symlink():
            raise InvalidVault("The .cryptobox control directory cannot be a symbolic link")
        if self.control_dir.exists() and not self.control_dir.is_dir():
            raise InvalidVault("The .cryptobox control path is not a directory")

    def _read_meta(self) -> dict[str, object]:
        self._validate_control_dir()
        try:
            meta = json.loads(self.meta_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise InvalidVault("Vault metadata cannot be read") from exc
        if meta.get("format") != "cryptobox-vault" or meta.get("version") != FORMAT_VERSION:
            raise InvalidVault("Unsupported vault metadata")
        return meta

    def _write_direct_meta(self, vault_id: bytes, master_key: bytes, kdf: dict[str, int]) -> None:
        self.control_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
        _atomic_json(
            self.meta_path,
            {
                "format": "cryptobox-vault",
                "version": FORMAT_VERSION,
                "key_mode": _DIRECT_KEY_MODE,
                "vault_id": b64e(vault_id),
                "verifier": b64e(_direct_verifier(master_key, vault_id)),
                "kdf": kdf,
            },
        )

    def _encrypted_paths(self, session: VaultSession) -> list[Path]:
        from .crypto import read_header_path

        paths: list[Path] = []
        stack = [self.root]
        while stack:
            directory = stack.pop()
            try:
                entries = os.scandir(directory)
            except OSError as exc:
                raise InvalidVault(f"Cannot scan vault while updating keys: {exc}") from exc
            with entries:
                for entry in entries:
                    path = Path(entry.path)
                    if path == self.control_dir or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        with path.open("rb") as handle:
                            if handle.read(len(FILE_MAGIC)) == FILE_MAGIC:
                                read_header_path(path, session)
                                paths.append(path)
        return paths

    def _rewrap_all(
        self, paths: list[Path], source_session: VaultSession, target_session: VaultSession
    ) -> None:
        from .crypto import rewrap_file_header

        converted: list[Path] = []
        try:
            for path in paths:
                rewrap_file_header(path, source_session, target_session)
                converted.append(path)
        except Exception:
            for path in reversed(converted):
                rewrap_file_header(path, target_session, source_session)
            raise

    def create(self, password: str) -> VaultSession:
        self._validate_control_dir()
        if self.initialized:
            raise InvalidVault("Vault is already initialized")
        self.control_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
        vault_id = os.urandom(16)
        kdf = _default_kdf()
        master_key = _derive_password_key(password, vault_id, kdf)
        self._write_direct_meta(vault_id, master_key, kdf)
        return VaultSession(self.root, vault_id, master_key, kdf)

    def unlock(self, password: str) -> VaultSession:
        self._validate_control_dir()
        try:
            meta = self._read_meta()
        except InvalidVault as metadata_error:
            candidate = self._discover_recovery_candidate()
            if candidate is None:
                raise metadata_error
            path, vault_id = candidate
            kdf = _default_kdf()
            master_key = _derive_password_key(password, vault_id, kdf)
            session = VaultSession(self.root, vault_id, master_key, kdf)
            try:
                from .crypto import read_header_path

                read_header_path(path, session)
            except Exception as exc:
                session.close()
                raise InvalidPassword("Incorrect password or damaged encrypted file") from exc
            self._write_direct_meta(vault_id, master_key, kdf)
            return session

        if meta.get("key_mode") != _DIRECT_KEY_MODE:
            raise InvalidVault("Vault does not use the password-derived key format")
        try:
            vault_id = b64d(str(meta["vault_id"]))
            verifier = b64d(str(meta["verifier"]))
            kdf = dict(meta["kdf"])  # type: ignore[arg-type]
            master_key = _derive_password_key(password, vault_id, kdf)
            if not hmac.compare_digest(verifier, _direct_verifier(master_key, vault_id)):
                raise InvalidPassword("Incorrect password or damaged vault metadata")
        except InvalidPassword:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidPassword("Incorrect password or damaged vault metadata") from exc
        return VaultSession(self.root, vault_id, master_key, kdf)

    def change_password(self, session: VaultSession, new_password: str) -> None:
        if session.root != self.root or len(session.master_key) != 32:
            raise InvalidVault("Vault is not unlocked")
        paths = self._encrypted_paths(session)
        new_kdf = _default_kdf()
        new_master_key = _derive_password_key(new_password, session.vault_id, new_kdf)
        new_session = VaultSession(self.root, session.vault_id, new_master_key, new_kdf)
        try:
            self._rewrap_all(paths, session, new_session)
            self._write_direct_meta(session.vault_id, new_master_key, new_kdf)
        except Exception:
            new_session.close()
            raise
        session.master_key = new_master_key
        session.kdf = new_kdf
