from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .constants import CONTROL_DIR, TEMP_PREFIX
from .index import VaultIndex
from .scanner import StatusTracker, scan_and_encrypt, verify_all
from .vault import VaultManager, VaultSession

LOGGER = logging.getLogger(__name__)


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, root: Path, loop: asyncio.AbstractEventLoop, event: asyncio.Event):
        self.root = root
        self.loop = loop
        self.event = event

    def on_any_event(self, event: FileSystemEvent) -> None:
        try:
            relative = Path(event.src_path).relative_to(self.root)
        except (ValueError, TypeError):
            return
        if (relative.parts and relative.parts[0] == CONTROL_DIR) or Path(event.src_path).name.startswith(
            TEMP_PREFIX
        ):
            return
        self.loop.call_soon_threadsafe(self.event.set)


class RuntimeState:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.manager = VaultManager(self.root)
        self.session: VaultSession | None = None
        self.index: VaultIndex | None = None
        self.tracker = StatusTracker()
        self.scan_lock = asyncio.Lock()
        self.change_event = asyncio.Event()
        self.observer: Observer | None = None
        self.watch_task: asyncio.Task[None] | None = None
        self.active_task: asyncio.Task[dict[str, object]] | None = None
        self.shutdown_event = asyncio.Event()

    @property
    def unlocked(self) -> bool:
        return self.session is not None and self.index is not None

    def change_root(self, root: Path) -> None:
        if self.unlocked:
            raise RuntimeError("Lock the vault before changing its root")
        self.root = root.resolve()
        self.manager = VaultManager(self.root)

    def attach_session(self, session: VaultSession) -> None:
        self.session = session
        self.index = VaultIndex(session.index_path, session.derive_key(b"index"))

    def _rebuild_index(self) -> None:
        assert self.session is not None
        index_path = self.session.index_path
        if self.index:
            self.index.close()
        for candidate in (
            index_path,
            Path(f"{index_path}-wal"),
            Path(f"{index_path}-shm"),
        ):
            candidate.unlink(missing_ok=True)
        self.index = VaultIndex(index_path, self.session.derive_key(b"index"))

    async def change_password(self, new_password: str) -> None:
        if not self.session:
            raise RuntimeError("Vault is locked")
        await self.stop_watcher()
        async with self.scan_lock:
            await asyncio.to_thread(self.manager.change_password, self.session, new_password)
            self._rebuild_index()
        self.start_scan()

    async def lock(self) -> None:
        if self.active_task and not self.active_task.done():
            try:
                await self.active_task
            except asyncio.CancelledError:
                pass
            except Exception:
                LOGGER.exception("Active vault operation failed while locking")
        self.active_task = None
        await self.stop_watcher()
        if self.index:
            self.index.close()
        if self.session:
            self.session.close()
        self.index = None
        self.session = None
        self.tracker.reset("locked")
        self.tracker.finish("locked")

    async def _scan(self) -> dict[str, object]:
        if not self.session or not self.index:
            raise RuntimeError("Vault is locked")
        async with self.scan_lock:
            result = await asyncio.to_thread(
                scan_and_encrypt, self.root, self.session, self.index, self.tracker
            )
        if result.get("phase") == "ready":
            await self.start_watcher()
        return result

    def start_scan(self) -> asyncio.Task[dict[str, object]]:
        if self.active_task and not self.active_task.done():
            return self.active_task
        self.active_task = asyncio.create_task(self._scan())
        return self.active_task

    async def _verify(self) -> dict[str, object]:
        if not self.session:
            raise RuntimeError("Vault is locked")
        async with self.scan_lock:
            return await asyncio.to_thread(verify_all, self.root, self.session, self.tracker, True)

    def start_verify(self) -> asyncio.Task[dict[str, object]]:
        if self.active_task and not self.active_task.done():
            return self.active_task
        self.active_task = asyncio.create_task(self._verify())
        return self.active_task

    async def start_watcher(self) -> None:
        if self.observer is not None or not self.unlocked:
            return
        loop = asyncio.get_running_loop()
        handler = _ChangeHandler(self.root, loop, self.change_event)
        self.observer = Observer()
        self.observer.schedule(handler, str(self.root), recursive=True)
        self.observer.start()
        self.watch_task = asyncio.create_task(self._watch_changes())

    async def _watch_changes(self) -> None:
        while True:
            await self.change_event.wait()
            self.change_event.clear()
            await asyncio.sleep(1.25)
            if self.change_event.is_set():
                continue
            try:
                task = self.start_scan()
                await task
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Background rescan failed")

    async def stop_watcher(self) -> None:
        if self.watch_task:
            self.watch_task.cancel()
            try:
                await self.watch_task
            except asyncio.CancelledError:
                pass
            self.watch_task = None
        if self.observer:
            self.observer.stop()
            await asyncio.to_thread(self.observer.join, 3)
            self.observer = None

    async def close(self) -> None:
        await self.lock()
