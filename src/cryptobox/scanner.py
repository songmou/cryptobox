from __future__ import annotations

import os
import stat as stat_module
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .constants import FILE_MAGIC, MAX_WORKERS
from .crypto import encrypt_file, read_header_path, verify_file
from .errors import CryptoboxError, PlainFile
from .index import VaultIndex
from .util import current_executable, is_internal_path
from .vault import VaultSession


@dataclass(slots=True)
class OperationStatus:
    phase: str = "idle"
    total_files: int = 0
    processed_files: int = 0
    cached_files: int = 0
    encrypted_files: int = 0
    total_bytes: int = 0
    processed_bytes: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    errors: list[str] = field(default_factory=list)

    def public(self) -> dict[str, object]:
        value = asdict(self)
        if self.started_at:
            value["elapsed"] = (self.finished_at or time.time()) - self.started_at
        else:
            value["elapsed"] = 0
        return value


class StatusTracker:
    def __init__(self) -> None:
        self._status = OperationStatus()
        self._lock = threading.RLock()

    def reset(self, phase: str) -> None:
        with self._lock:
            self._status = OperationStatus(phase=phase, started_at=time.time())

    def update(self, **values: object) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(self._status, key, value)

    def increment(self, **values: int) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(self._status, key, getattr(self._status, key) + value)

    def error(self, message: str) -> None:
        with self._lock:
            self._status.errors.append(message)

    def finish(self, phase: str = "ready") -> None:
        with self._lock:
            self._status.phase = phase
            self._status.finished_at = time.time()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._status.public()


def iter_regular_files(
    root: Path, on_error: Callable[[str], None] | None = None
) -> Iterable[tuple[Path, Path, os.stat_result]]:
    executable = current_executable()
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            if on_error:
                on_error(f"{directory}: {exc}")
            continue
        for entry in entries:
            path = Path(entry.path)
            if is_internal_path(root, path, executable):
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                stat_result = entry.stat(follow_symlinks=False)
                if stat_module.S_ISREG(stat_result.st_mode):
                    yield path, path.relative_to(root), stat_result
            except OSError as exc:
                if on_error:
                    on_error(f"{path}: {exc}")
                continue


def preview_root(root: Path) -> dict[str, int]:
    files = 0
    total_bytes = 0
    for _, _, stat_result in iter_regular_files(root):
        files += 1
        total_bytes += stat_result.st_size
    return {"files": files, "bytes": total_bytes}


def scan_and_encrypt(
    root: Path,
    session: VaultSession,
    index: VaultIndex,
    tracker: StatusTracker,
    workers: int = MAX_WORKERS,
) -> dict[str, object]:
    tracker.reset("scanning")
    pending: list[tuple[Path, Path, os.stat_result]] = []
    seen: set[bytes] = set()
    validation_errors: list[str] = []
    for path, relative, stat_result in iter_regular_files(root, validation_errors.append):
        seen.add(os.fsencode(str(relative)))
        tracker.increment(total_files=1, total_bytes=stat_result.st_size)
        cached = index.get_if_unchanged(relative, stat_result)
        if cached is not None:
            tracker.increment(processed_files=1, processed_bytes=stat_result.st_size, cached_files=1)
            continue
        try:
            if stat_result.st_nlink > 1:
                validation_errors.append(f"{relative}: hard-linked files are not supported")
                continue
            with path.open("rb") as handle:
                magic = handle.read(len(FILE_MAGIC))
            if magic == FILE_MAGIC:
                header = read_header_path(path, session)
                index.put_encrypted(relative, stat_result, header.plain_size, header.digest)
                tracker.increment(processed_files=1, processed_bytes=stat_result.st_size)
            else:
                pending.append((path, relative, stat_result))
        except (OSError, CryptoboxError) as exc:
            validation_errors.append(f"{relative}: {exc}")
    index.remove_missing(seen)
    if validation_errors:
        for error in validation_errors:
            tracker.error(error)
        tracker.finish("error")
        return tracker.snapshot()

    tracker.update(phase="encrypting")
    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        futures = {pool.submit(encrypt_file, path, session): (path, relative, old) for path, relative, old in pending}
        for future in as_completed(futures):
            path, relative, old = futures[future]
            try:
                header = future.result()
                current = path.stat(follow_symlinks=False)
                index.put_encrypted(relative, current, header.plain_size, header.digest)
                tracker.increment(
                    processed_files=1,
                    encrypted_files=1,
                    processed_bytes=old.st_size,
                )
            except (OSError, CryptoboxError) as exc:
                tracker.error(f"{relative}: {exc}")
    snapshot = tracker.snapshot()
    tracker.finish("error" if snapshot["errors"] else "ready")
    return tracker.snapshot()


def verify_all(
    root: Path,
    session: VaultSession,
    tracker: StatusTracker,
    full: bool = True,
) -> dict[str, object]:
    tracker.reset("verifying")
    items = list(iter_regular_files(root))
    tracker.update(
        total_files=len(items),
        total_bytes=sum(item[2].st_size for item in items),
    )
    for path, relative, stat_result in items:
        try:
            verify_file(path, session, full=full)
            tracker.increment(processed_files=1, processed_bytes=stat_result.st_size)
        except (OSError, CryptoboxError) as exc:
            tracker.error(f"{relative}: {exc}")
    snapshot = tracker.snapshot()
    tracker.finish("error" if snapshot["errors"] else "ready")
    return tracker.snapshot()
