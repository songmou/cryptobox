from __future__ import annotations

import hashlib
import hmac
import math
import os
import shutil
import stat as stat_module
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap, aes_key_wrap

from .constants import (
    DEFAULT_CHUNK_SIZE,
    FILE_MAGIC,
    FORMAT_VERSION,
    HEADER_MAC_SIZE,
    HEADER_SIZE,
    NONCE_PREFIX_SIZE,
    TAG_SIZE,
    TEMP_PREFIX,
)
from .errors import ConcurrentModification, CorruptFile, InvalidVault, PlainFile
from .util import fsync_directory
from .vault import VaultSession

_HEADER_FIELDS = struct.Struct(">8sHHI16s16sQIQ4s40s")
_HEADER_PREFIX_SIZE = HEADER_SIZE - HEADER_MAC_SIZE
_AAD = struct.Struct(">8sH16s16sQQI")


@dataclass(frozen=True, slots=True)
class EncryptedHeader:
    vault_id: bytes
    file_id: bytes
    plain_size: int
    chunk_size: int
    chunk_count: int
    nonce_prefix: bytes
    wrapped_key: bytes
    file_key: bytes
    raw: bytes

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self.raw).digest()


def _header_key(file_key: bytes, file_id: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=file_id,
        info=b"cryptobox/file-header/v1",
    ).derive(file_key)


def _pack_header(
    session: VaultSession,
    file_key: bytes,
    file_id: bytes,
    plain_size: int,
    chunk_size: int,
    nonce_prefix: bytes,
) -> bytes:
    chunk_count = math.ceil(plain_size / chunk_size) if plain_size else 0
    wrapped_key = aes_key_wrap(session.master_key, file_key)
    fields = _HEADER_FIELDS.pack(
        FILE_MAGIC,
        FORMAT_VERSION,
        HEADER_SIZE,
        0,
        session.vault_id,
        file_id,
        plain_size,
        chunk_size,
        chunk_count,
        nonce_prefix,
        wrapped_key,
    )
    prefix = fields + bytes(_HEADER_PREFIX_SIZE - len(fields))
    mac = hmac.digest(_header_key(file_key, file_id), prefix, "sha256")
    return prefix + mac


def read_header(handle: BinaryIO, session: VaultSession) -> EncryptedHeader:
    handle.seek(0)
    raw = handle.read(HEADER_SIZE)
    if len(raw) < len(FILE_MAGIC) or raw[: len(FILE_MAGIC)] != FILE_MAGIC:
        raise PlainFile("File is not encrypted")
    if len(raw) != HEADER_SIZE:
        raise CorruptFile("Encrypted header is truncated")
    try:
        (
            magic,
            version,
            header_size,
            flags,
            vault_id,
            file_id,
            plain_size,
            chunk_size,
            chunk_count,
            nonce_prefix,
            wrapped_key,
        ) = _HEADER_FIELDS.unpack(raw[: _HEADER_FIELDS.size])
    except struct.error as exc:
        raise CorruptFile("Encrypted header is malformed") from exc
    if magic != FILE_MAGIC or version != FORMAT_VERSION or header_size != HEADER_SIZE or flags != 0:
        raise CorruptFile("Unsupported encrypted file format")
    if vault_id != session.vault_id:
        raise InvalidVault("File belongs to a different vault")
    if chunk_size < 4096 or chunk_size > 64 * 1024 * 1024:
        raise CorruptFile("Invalid chunk size")
    expected_chunks = math.ceil(plain_size / chunk_size) if plain_size else 0
    if chunk_count != expected_chunks or len(nonce_prefix) != NONCE_PREFIX_SIZE:
        raise CorruptFile("Invalid encrypted file geometry")
    expected_cipher_size = HEADER_SIZE + plain_size + chunk_count * TAG_SIZE
    handle.seek(0, os.SEEK_END)
    if handle.tell() != expected_cipher_size:
        raise CorruptFile("Encrypted file is truncated or has trailing data")
    try:
        file_key = aes_key_unwrap(session.master_key, wrapped_key)
    except InvalidUnwrap as exc:
        raise CorruptFile("File key cannot be unwrapped") from exc
    expected_mac = hmac.digest(_header_key(file_key, file_id), raw[:_HEADER_PREFIX_SIZE], "sha256")
    if not hmac.compare_digest(expected_mac, raw[_HEADER_PREFIX_SIZE:]):
        raise CorruptFile("Encrypted header authentication failed")
    return EncryptedHeader(
        vault_id,
        file_id,
        plain_size,
        chunk_size,
        chunk_count,
        nonce_prefix,
        wrapped_key,
        file_key,
        raw,
    )


def read_header_path(path: Path, session: VaultSession) -> EncryptedHeader:
    with path.open("rb") as handle:
        return read_header(handle, session)


def _nonce(header: EncryptedHeader, chunk_index: int) -> bytes:
    return header.nonce_prefix + chunk_index.to_bytes(8, "big")


def _aad(header: EncryptedHeader, chunk_index: int, plain_length: int) -> bytes:
    return _AAD.pack(
        FILE_MAGIC,
        FORMAT_VERSION,
        header.vault_id,
        header.file_id,
        chunk_index,
        header.plain_size,
        plain_length,
    )


def _plain_chunk_length(header: EncryptedHeader, chunk_index: int) -> int:
    if chunk_index < 0 or chunk_index >= header.chunk_count:
        raise IndexError(chunk_index)
    if chunk_index == header.chunk_count - 1:
        return header.plain_size - (chunk_index * header.chunk_size)
    return header.chunk_size


def _cipher_chunk_offset(header: EncryptedHeader, chunk_index: int) -> int:
    return HEADER_SIZE + chunk_index * (header.chunk_size + TAG_SIZE)


def encrypt_file(path: Path, session: VaultSession, chunk_size: int = DEFAULT_CHUNK_SIZE) -> EncryptedHeader:
    path = path.resolve()
    initial = path.stat(follow_symlinks=False)
    if not stat_module.S_ISREG(initial.st_mode):
        raise PlainFile("Only regular files can be encrypted")
    if initial.st_nlink > 1:
        raise ConcurrentModification("Hard-linked files are not supported")
    file_key = os.urandom(32)
    file_id = uuid.uuid4().bytes
    nonce_prefix = os.urandom(NONCE_PREFIX_SIZE)
    raw_header = _pack_header(session, file_key, file_id, initial.st_size, chunk_size, nonce_prefix)
    temporary = path.with_name(f"{TEMP_PREFIX}{uuid.uuid4().hex}")
    try:
        with path.open("rb") as source, temporary.open("xb") as target:
            source_initial = os.fstat(source.fileno())
            target.write(raw_header)
            cipher = ChaCha20Poly1305(file_key)
            chunk_index = 0
            while True:
                plain = source.read(chunk_size)
                if not plain:
                    break
                header = EncryptedHeader(
                    session.vault_id,
                    file_id,
                    initial.st_size,
                    chunk_size,
                    math.ceil(initial.st_size / chunk_size),
                    nonce_prefix,
                    raw_header[_HEADER_FIELDS.size - 40 : _HEADER_FIELDS.size],
                    file_key,
                    raw_header,
                )
                target.write(cipher.encrypt(_nonce(header, chunk_index), plain, _aad(header, chunk_index, len(plain))))
                chunk_index += 1
            source_final = os.fstat(source.fileno())
            if (
                source_initial.st_dev != source_final.st_dev
                or source_initial.st_ino != source_final.st_ino
                or source_initial.st_size != source_final.st_size
                or source_initial.st_mtime_ns != source_final.st_mtime_ns
            ):
                raise ConcurrentModification("Source changed while it was being encrypted")
            target.flush()
            os.fsync(target.fileno())
            if os.name != "nt":
                os.fchmod(target.fileno(), stat_module.S_IMODE(initial.st_mode))
        with temporary.open("rb") as check:
            verified = read_header(check, session)
        current = path.stat(follow_symlinks=False)
        if (
            current.st_dev != initial.st_dev
            or current.st_ino != initial.st_ino
            or current.st_size != initial.st_size
            or current.st_mtime_ns != initial.st_mtime_ns
        ):
            raise ConcurrentModification("Source changed before atomic replacement")
        os.utime(temporary, ns=(initial.st_atime_ns, initial.st_mtime_ns), follow_symlinks=False)
        os.replace(temporary, path)
        fsync_directory(path.parent)
        return verified
    finally:
        temporary.unlink(missing_ok=True)


def rewrap_file_header(
    path: Path, source_session: VaultSession, target_session: VaultSession
) -> EncryptedHeader:
    """Atomically rewrap a file key without decrypting or rewriting its data chunks."""
    if source_session.vault_id != target_session.vault_id:
        raise InvalidVault("Password changes cannot alter the vault identifier")
    path = path.resolve()
    initial = path.stat(follow_symlinks=False)
    if not stat_module.S_ISREG(initial.st_mode):
        raise PlainFile("Only regular files can be rewrapped")
    with path.open("rb") as source:
        header = read_header(source, source_session)
        replacement = _pack_header(
            target_session,
            header.file_key,
            header.file_id,
            header.plain_size,
            header.chunk_size,
            header.nonce_prefix,
        )
        temporary = path.with_name(f"{TEMP_PREFIX}{uuid.uuid4().hex}")
        try:
            with temporary.open("xb") as target:
                target.write(replacement)
                source.seek(HEADER_SIZE)
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
                if os.name != "nt":
                    os.fchmod(target.fileno(), stat_module.S_IMODE(initial.st_mode))
            source_final = os.fstat(source.fileno())
            source.close()
            current = path.stat(follow_symlinks=False)
            if (
                source_final.st_dev != initial.st_dev
                or source_final.st_ino != initial.st_ino
                or source_final.st_size != initial.st_size
                or source_final.st_mtime_ns != initial.st_mtime_ns
                or current.st_dev != initial.st_dev
                or current.st_ino != initial.st_ino
                or current.st_size != initial.st_size
                or current.st_mtime_ns != initial.st_mtime_ns
            ):
                raise ConcurrentModification("Source changed during password update")
            with temporary.open("rb") as check:
                verified = read_header(check, target_session)
            os.utime(temporary, ns=(initial.st_atime_ns, initial.st_mtime_ns), follow_symlinks=False)
            os.replace(temporary, path)
            fsync_directory(path.parent)
            return verified
        finally:
            temporary.unlink(missing_ok=True)


def iter_decrypted(
    path: Path,
    session: VaultSession,
    start: int = 0,
    end_exclusive: int | None = None,
) -> Iterator[bytes]:
    with path.open("rb") as handle:
        header = read_header(handle, session)
        end = header.plain_size if end_exclusive is None else min(end_exclusive, header.plain_size)
        start = max(0, start)
        if start > end:
            return
        if start == end or header.chunk_count == 0:
            return
        first_chunk = start // header.chunk_size
        last_chunk = (end - 1) // header.chunk_size
        cipher = ChaCha20Poly1305(header.file_key)
        for chunk_index in range(first_chunk, last_chunk + 1):
            plain_length = _plain_chunk_length(header, chunk_index)
            handle.seek(_cipher_chunk_offset(header, chunk_index))
            ciphertext = handle.read(plain_length + TAG_SIZE)
            if len(ciphertext) != plain_length + TAG_SIZE:
                raise CorruptFile("Encrypted content is truncated")
            try:
                plain = cipher.decrypt(
                    _nonce(header, chunk_index),
                    ciphertext,
                    _aad(header, chunk_index, plain_length),
                )
            except InvalidTag as exc:
                raise CorruptFile(f"Chunk {chunk_index} authentication failed") from exc
            left = start - chunk_index * header.chunk_size if chunk_index == first_chunk else 0
            right = end - chunk_index * header.chunk_size if chunk_index == last_chunk else len(plain)
            yield plain[left:right]


def decrypt_to_path(source: Path, destination: Path, session: VaultSession) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{TEMP_PREFIX}{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as output:
            for data in iter_decrypted(source, session):
                output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def verify_file(path: Path, session: VaultSession, full: bool = False) -> EncryptedHeader:
    header = read_header_path(path, session)
    if full:
        for _ in iter_decrypted(path, session):
            pass
    return header
