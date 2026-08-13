from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_AUTO_LOCK_MINUTES = 3
MIN_AUTO_LOCK_MINUTES = 1
MAX_AUTO_LOCK_MINUTES = 120
DEFAULT_THEME = "system"
THEMES = frozenset({"system", "light", "dark"})


@dataclass(frozen=True)
class AppSettings:
    last_root: Path | None = None
    auto_lock_minutes: int = DEFAULT_AUTO_LOCK_MINUTES
    theme: str = DEFAULT_THEME


def settings_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Cryptobox"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Cryptobox"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cryptobox"
    return base / "settings.json"


def _read_payload(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        LOGGER.warning("Unable to load Cryptobox settings", exc_info=True)
        return {}


def load_settings(path: Path) -> AppSettings:
    payload = _read_payload(path)

    last_root: Path | None = None
    value = payload.get("last_root")
    if isinstance(value, str):
        candidate = Path(value).expanduser().resolve()
        if candidate.is_dir():
            last_root = candidate

    auto_lock = payload.get("auto_lock_minutes")
    if (
        not isinstance(auto_lock, int)
        or isinstance(auto_lock, bool)
        or not MIN_AUTO_LOCK_MINUTES <= auto_lock <= MAX_AUTO_LOCK_MINUTES
    ):
        auto_lock = DEFAULT_AUTO_LOCK_MINUTES

    theme = payload.get("theme")
    if not isinstance(theme, str) or theme not in THEMES:
        theme = DEFAULT_THEME

    return AppSettings(last_root=last_root, auto_lock_minutes=auto_lock, theme=theme)


def load_last_root(path: Path) -> Path | None:
    return load_settings(path).last_root


def _write_settings(path: Path, settings: AppSettings) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    payload = {
        "last_root": str(settings.last_root.resolve()) if settings.last_root is not None else None,
        "auto_lock_minutes": settings.auto_lock_minutes,
        "theme": settings.theme,
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_last_root(path: Path, root: Path) -> None:
    current = load_settings(path)
    _write_settings(
        path,
        AppSettings(
            last_root=root.resolve(),
            auto_lock_minutes=current.auto_lock_minutes,
            theme=current.theme,
        ),
    )


def save_preferences(path: Path, *, auto_lock_minutes: int, theme: str) -> AppSettings:
    if not MIN_AUTO_LOCK_MINUTES <= auto_lock_minutes <= MAX_AUTO_LOCK_MINUTES:
        raise ValueError("Auto-lock time must be between 1 and 120 minutes")
    if theme not in THEMES:
        raise ValueError("Theme must be system, light, or dark")
    current = load_settings(path)
    updated = AppSettings(
        last_root=current.last_root,
        auto_lock_minutes=auto_lock_minutes,
        theme=theme,
    )
    _write_settings(path, updated)
    return updated
