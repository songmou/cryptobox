from __future__ import annotations

import base64
import errno
import hmac
import os
import stat as stat_module
import sys
import time
from pathlib import Path

from .constants import CONTROL_DIR, TEMP_PREFIX
from .errors import UnsafePath


def b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def path_to_id(relative: Path) -> str:
    return b64e(os.fsencode(str(relative))) if str(relative) != "." else ""


def id_to_relative(value: str) -> Path:
    try:
        raw = b64d(value) if value else b"."
        relative = Path(os.fsdecode(raw))
    except Exception as exc:
        raise UnsafePath("Invalid path identifier") from exc
    if relative.is_absolute() or ".." in relative.parts:
        raise UnsafePath("Path escapes the vault")
    return relative


def safe_join(root: Path, relative: Path) -> Path:
    root = root.resolve()
    if str(relative) in {"", "."}:
        return root
    candidate = root.joinpath(relative)
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise UnsafePath("Parent directory is unavailable") from exc
    if not resolved_parent.is_relative_to(root):
        raise UnsafePath("Path escapes the vault")
    if candidate.is_symlink():
        raise UnsafePath("Symbolic links are not supported")
    return candidate


def display_name(path: Path) -> str:
    return os.fsencode(path.name).decode("utf-8", "replace")


def is_internal_path(root: Path, path: Path, executable: Path | None = None) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if relative.parts and relative.parts[0] == CONTROL_DIR:
        return True
    if path.name.startswith(TEMP_PREFIX):
        return True
    if executable is not None:
        try:
            if path.resolve() == executable.resolve():
                return True
        except OSError:
            pass
    return False


def current_executable() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()


def secure_compare(left: str | bytes, right: str | bytes) -> bool:
    return hmac.compare_digest(left, right)


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def reject_source_tree(root: Path) -> None:
    root = root.resolve()
    if (root / "pyproject.toml").exists() and (root / "src" / "cryptobox").is_dir():
        raise UnsafePath("The Cryptobox source tree cannot be initialized as a vault")


def _atomic_replace(temporary: Path, destination: Path, *, attempts: int = 5) -> None:
    """Move ``temporary`` onto ``destination`` atomically, tolerant of Windows quirks.

    On Windows ``os.replace`` (``MoveFileExW`` with ``MOVEFILE_REPLACE_EXISTING``)
    fails with ``WinError 5`` (ERROR_ACCESS_DENIED) when the destination file is
    read-only, because replacing it requires deleting the existing file first. It can
    also fail transiently with ``WinError 32`` (sharing violation) while anti-virus or
    the Explorer preview handler briefly holds a lock on the file.

    We strip the read-only bit from an existing destination before replacing, and we
    retry transient access errors with a small exponential backoff so a single locked
    file does not abort an otherwise successful batch. Non-access errors are re-raised
    immediately so genuine problems (bad paths, disks full) are not masked.
    """
    destination = Path(destination)
    if os.name == "nt" and destination.exists():
        try:
            mode = destination.stat().st_mode
            if not (mode & stat_module.S_IWRITE):
                os.chmod(destination, mode | stat_module.S_IWRITE)
        except OSError:
            pass
    for attempt in range(max(1, attempts)):
        try:
            os.replace(temporary, destination)
            return
        except OSError as exc:
            transient = (
                getattr(exc, "winerror", None) in (5, 32)
                if os.name == "nt"
                else exc.errno in (errno.EACCES, errno.EPERM)
            )
            if not transient or attempt == max(1, attempts) - 1:
                raise
            time.sleep(0.1 * (attempt + 1))
