from __future__ import annotations

import json
from pathlib import Path

from cryptobox.main import parse_args, select_root
from cryptobox.settings import (
    DEFAULT_AUTO_LOCK_MINUTES,
    DEFAULT_THEME,
    load_last_root,
    load_settings,
    save_last_root,
    save_preferences,
    settings_path,
)
import cryptobox.settings as settings_module


def test_settings_round_trip_and_invalid_values_fall_back(tmp_path: Path) -> None:
    config = tmp_path / "config" / "settings.json"
    vault = tmp_path / "vault"
    vault.mkdir()

    save_last_root(config, vault)
    assert load_last_root(config) == vault.resolve()

    config.write_text("not json", encoding="utf-8")
    assert load_last_root(config) is None
    config.write_text(json.dumps({"last_root": str(tmp_path / "missing")}), encoding="utf-8")
    assert load_last_root(config) is None


def test_preferences_default_migrate_validate_and_preserve_root(tmp_path: Path) -> None:
    config = tmp_path / "config" / "settings.json"
    vault = tmp_path / "vault"
    vault.mkdir()
    config.parent.mkdir()
    config.write_text(json.dumps({"last_root": str(vault)}), encoding="utf-8")

    legacy = load_settings(config)
    assert legacy.last_root == vault.resolve()
    assert legacy.auto_lock_minutes == DEFAULT_AUTO_LOCK_MINUTES
    assert legacy.theme == DEFAULT_THEME

    updated = save_preferences(config, auto_lock_minutes=15, theme="light")
    assert updated.last_root == vault.resolve()
    save_last_root(config, vault)
    assert load_settings(config) == updated

    config.write_text(
        json.dumps({"last_root": str(vault), "auto_lock_minutes": 0, "theme": "neon"}),
        encoding="utf-8",
    )
    invalid = load_settings(config)
    assert invalid.auto_lock_minutes == DEFAULT_AUTO_LOCK_MINUTES
    assert invalid.theme == DEFAULT_THEME


def test_preferences_reject_out_of_range_values(tmp_path: Path) -> None:
    config = tmp_path / "settings.json"
    for value in (0, 121):
        try:
            save_preferences(config, auto_lock_minutes=value, theme="system")
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid auto-lock value was accepted")
    try:
        save_preferences(config, auto_lock_minutes=3, theme="neon")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid theme was accepted")


def test_explicit_root_wins_then_remembered_root_then_cwd(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    remembered = tmp_path / "remembered"
    cwd = tmp_path / "cwd"
    for path in (explicit, remembered, cwd):
        path.mkdir()
    config = tmp_path / "settings.json"
    save_last_root(config, remembered)

    assert parse_args([]).root is None
    assert select_root(explicit, config, cwd) == explicit.resolve()
    assert select_root(None, config, cwd) == remembered.resolve()

    config.unlink()
    assert select_root(None, config, cwd) == cwd.resolve()


def test_settings_path_uses_platform_conventions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(settings_module.sys, "platform", "darwin")
    assert settings_path() == tmp_path / "Library" / "Application Support" / "Cryptobox" / "settings.json"

    monkeypatch.setattr(settings_module.sys, "platform", "linux")
    monkeypatch.setattr(settings_module.os, "name", "posix")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert settings_path() == tmp_path / "xdg" / "cryptobox" / "settings.json"
