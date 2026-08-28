from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from cryptobox.service import RuntimeState, _ChangeHandler
from cryptobox.vault import VaultManager


def unlocked_runtime(root: Path) -> RuntimeState:
    runtime = RuntimeState(root)
    runtime.attach_session(VaultManager(root).create("test password"))
    return runtime


def test_auto_lock_deadline_locks_vault(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = unlocked_runtime(tmp_path)
        runtime.reset_auto_lock(0.02)
        assert runtime.auto_lock_remaining_seconds() == 1
        await asyncio.sleep(0.08)
        assert runtime.unlocked is False
        assert runtime.auto_lock_task is None
        assert runtime.auto_lock_deadline is None

    asyncio.run(scenario())


def test_activity_extends_deadline_and_manual_lock_cancels_timer(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = unlocked_runtime(tmp_path)
        runtime.reset_auto_lock(0.06)
        first_deadline = runtime.auto_lock_deadline
        await asyncio.sleep(0.03)
        runtime.reset_auto_lock(0.09)
        assert runtime.auto_lock_deadline is not None
        assert first_deadline is not None
        assert runtime.auto_lock_deadline > first_deadline
        await asyncio.sleep(0.05)
        assert runtime.unlocked is True

        await runtime.lock()
        assert runtime.auto_lock_task is None
        assert runtime.auto_lock_deadline is None

    asyncio.run(scenario())


def test_change_handler_ignores_read_only_file_events(tmp_path: Path) -> None:
    callbacks: list[object] = []
    change_notifications: list[bool] = []
    loop = SimpleNamespace(call_soon_threadsafe=lambda callback: callbacks.append(callback))
    event = SimpleNamespace(set=lambda: change_notifications.append(True))
    handler = _ChangeHandler(tmp_path, loop, event)
    source = str(tmp_path / "photo.heic")

    for event_type in ("opened", "closed", "closed_no_write"):
        handler.on_any_event(SimpleNamespace(event_type=event_type, src_path=source))

    assert callbacks == []
    assert change_notifications == []


def test_change_handler_reports_mutating_file_events(tmp_path: Path) -> None:
    callbacks: list[object] = []
    change_notifications: list[bool] = []

    def schedule(callback: object) -> None:
        callbacks.append(callback)
        callback()  # type: ignore[operator]

    loop = SimpleNamespace(call_soon_threadsafe=schedule)
    event = SimpleNamespace(set=lambda: change_notifications.append(True))
    handler = _ChangeHandler(tmp_path, loop, event)
    source = str(tmp_path / "changed.txt")

    for event_type in ("created", "modified", "deleted", "moved"):
        handler.on_any_event(SimpleNamespace(event_type=event_type, src_path=source))

    assert len(callbacks) == 4
    assert change_notifications == [True, True, True, True]
