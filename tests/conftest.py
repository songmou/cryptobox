from __future__ import annotations

import pytest

import cryptobox.vault as vault_module
import cryptobox.service as service_module


class _NoopObserver:
    def schedule(self, *args: object, **kwargs: object) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass


@pytest.fixture(autouse=True)
def lightweight_argon2_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests stable while production retains the 64 MiB KDF cost."""
    monkeypatch.setattr(vault_module, "KDF_MEMORY_KIB", 8 * 1024)
    monkeypatch.setattr(vault_module, "KDF_ITERATIONS", 1)
    monkeypatch.setattr(vault_module, "KDF_LANES", 1)
    monkeypatch.setattr(service_module, "Observer", _NoopObserver)
