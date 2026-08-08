"""Cryptobox local encrypted-file browser."""

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("cryptobox-local")
except Exception:  # pragma: no cover - fallback for source-only runs without install metadata
    __version__ = "0.1.0"

