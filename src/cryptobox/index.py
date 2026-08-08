from __future__ import annotations

import hmac
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CacheEntry:
    relative: Path
    state: str
    plain_size: int
    header_digest: bytes


class VaultIndex:
    def __init__(self, path: Path, index_key: bytes):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = path
        self._key = index_key
        self._lock = threading.RLock()
        try:
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path BLOB PRIMARY KEY,
                    device INTEGER NOT NULL,
                    inode INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    plain_size INTEGER NOT NULL,
                    header_digest BLOB NOT NULL,
                    record_mac BLOB NOT NULL
                )
                """
            )
            self._db.commit()
        except sqlite3.DatabaseError:
            try:
                self._db.close()
            except Exception:
                pass
            path.replace(path.with_suffix(".corrupt"))
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute(
                """
                CREATE TABLE files (
                    path BLOB PRIMARY KEY, device INTEGER NOT NULL, inode INTEGER NOT NULL,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, state TEXT NOT NULL,
                    plain_size INTEGER NOT NULL, header_digest BLOB NOT NULL, record_mac BLOB NOT NULL
                )
                """
            )
            self._db.commit()

    @staticmethod
    def _path_bytes(relative: Path) -> bytes:
        return os.fsencode(str(relative))

    def _mac(
        self,
        path: bytes,
        device: int,
        inode: int,
        size: int,
        mtime_ns: int,
        state: str,
        plain_size: int,
        digest: bytes,
    ) -> bytes:
        fields = [
            path,
            str(device).encode(),
            str(inode).encode(),
            str(size).encode(),
            str(mtime_ns).encode(),
            state.encode(),
            str(plain_size).encode(),
            digest,
        ]
        return hmac.digest(self._key, b"\0".join(fields), "sha256")

    def get_if_unchanged(self, relative: Path, stat_result: os.stat_result) -> CacheEntry | None:
        path = self._path_bytes(relative)
        with self._lock:
            row = self._db.execute(
                "SELECT device,inode,size,mtime_ns,state,plain_size,header_digest,record_mac "
                "FROM files WHERE path=?",
                (path,),
            ).fetchone()
        if row is None:
            return None
        device, inode, size, mtime_ns, state, plain_size, digest, record_mac = row
        expected = self._mac(path, device, inode, size, mtime_ns, state, plain_size, digest)
        if not hmac.compare_digest(expected, record_mac):
            self.delete(relative)
            return None
        if (
            device != stat_result.st_dev
            or inode != stat_result.st_ino
            or size != stat_result.st_size
            or mtime_ns != stat_result.st_mtime_ns
            or state != "encrypted"
        ):
            return None
        return CacheEntry(relative, state, plain_size, digest)

    def get_entry(self, relative: Path) -> CacheEntry | None:
        path = self._path_bytes(relative)
        with self._lock:
            row = self._db.execute(
                "SELECT device,inode,size,mtime_ns,state,plain_size,header_digest,record_mac "
                "FROM files WHERE path=?",
                (path,),
            ).fetchone()
        if row is None:
            return None
        device, inode, size, mtime_ns, state, plain_size, digest, record_mac = row
        if not hmac.compare_digest(
            self._mac(path, device, inode, size, mtime_ns, state, plain_size, digest), record_mac
        ):
            return None
        return CacheEntry(relative, state, plain_size, digest)

    def put_encrypted(
        self, relative: Path, stat_result: os.stat_result, plain_size: int, header_digest: bytes
    ) -> None:
        path = self._path_bytes(relative)
        values = (
            path,
            int(stat_result.st_dev),
            int(stat_result.st_ino),
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            "encrypted",
            int(plain_size),
            header_digest,
        )
        record_mac = self._mac(*values)
        with self._lock:
            self._db.execute(
                """
                INSERT INTO files(path,device,inode,size,mtime_ns,state,plain_size,header_digest,record_mac)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    device=excluded.device, inode=excluded.inode, size=excluded.size,
                    mtime_ns=excluded.mtime_ns, state=excluded.state,
                    plain_size=excluded.plain_size, header_digest=excluded.header_digest,
                    record_mac=excluded.record_mac
                """,
                values + (record_mac,),
            )
            self._db.commit()

    def delete(self, relative: Path) -> None:
        with self._lock:
            self._db.execute("DELETE FROM files WHERE path=?", (self._path_bytes(relative),))
            self._db.commit()

    def remove_missing(self, seen: set[bytes]) -> None:
        with self._lock:
            existing = [bytes(row[0]) for row in self._db.execute("SELECT path FROM files")]
            missing = [(item,) for item in existing if item not in seen]
            if missing:
                self._db.executemany("DELETE FROM files WHERE path=?", missing)
                self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
