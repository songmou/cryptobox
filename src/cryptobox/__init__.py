"""Cryptobox local encrypted-file browser."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
import sys
import tomllib


def _version_from_pyproject() -> str | None:
    """Read the project version in source checkouts and PyInstaller bundles."""
    candidates = [Path(__file__).resolve().parents[2] / "pyproject.toml"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        candidates.insert(0, Path(bundle_root) / "pyproject.toml")
    for candidate in candidates:
        try:
            with candidate.open("rb") as handle:
                value = tomllib.load(handle)["project"]["version"]
            if isinstance(value, str) and value:
                return value
        except (FileNotFoundError, OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
            continue
    return None


try:
    __version__ = _version_from_pyproject() or _pkg_version("cryptobox-local")
except PackageNotFoundError:  # pragma: no cover - only malformed source-only deployments
    __version__ = "unknown"
