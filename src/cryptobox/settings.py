from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def settings_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Cryptobox"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Cryptobox"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cryptobox"
    return base / "settings.json"


def load_last_root(path: Path) -> Path | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("last_root")
        if not isinstance(value, str):
            return None
        candidate = Path(value).expanduser().resolve()
        return candidate if candidate.is_dir() else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        LOGGER.warning("Unable to load remembered vault directory", exc_info=True)
        return None


def save_last_root(path: Path, root: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps({"last_root": str(root.resolve())}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
