from __future__ import annotations

import queue
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

from .crypto import iter_decrypted
from .vault import VaultSession


class _QueueWriter:
    def __init__(self, output: queue.Queue[bytes | BaseException | None]):
        self.output = output
        self.position = 0

    def write(self, data: bytes) -> int:
        if data:
            payload = bytes(data)
            self.output.put(payload)
            self.position += len(payload)
        return len(data)

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        return None

    def seekable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def stream_zip(
    files: list[tuple[Path, str]], session: VaultSession
) -> Iterator[bytes]:
    output: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=8)

    def produce() -> None:
        try:
            writer = _QueueWriter(output)
            with zipfile.ZipFile(
                writer, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as archive:
                for source, archive_name in files:
                    info = zipfile.ZipInfo(archive_name)
                    info.compress_type = zipfile.ZIP_STORED
                    with archive.open(info, mode="w", force_zip64=True) as destination:
                        for data in iter_decrypted(source, session):
                            destination.write(data)
        except BaseException as exc:
            output.put(exc)
        finally:
            output.put(None)

    thread = threading.Thread(target=produce, name="cryptobox-zip", daemon=True)
    thread.start()
    while True:
        item = output.get()
        if item is None:
            break
        if isinstance(item, BaseException):
            raise item
        yield item

