from __future__ import annotations

import asyncio
from pathlib import Path

from cryptobox.service import RuntimeState
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
