from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cryptobox.api import create_app
from cryptobox.service import RuntimeState
from cryptobox.vault import VaultManager
from cryptobox.web_security import (
    BLOCK_SECONDS,
    SETUP_SESSION_SECONDS,
    AuthState,
    LoginRateLimiter,
    build_security_config,
    certificate_covers_origins,
    ensure_self_signed_certificate,
)


PUBLIC_ORIGIN = "https://vault.example:8787"


def external_config(*, allowed_clients: list[str] | None = None):
    return build_security_config(
        host="0.0.0.0",
        port=8787,
        public_url=PUBLIC_ORIGIN,
        allowed_origins=[],
        allowed_clients=allowed_clients or [],
        tls_enabled=True,
    )


def locked_runtime(root: Path, password: str = "correct password") -> RuntimeState:
    VaultManager(root).create(password).close()
    return RuntimeState(root)


def test_external_configuration_requires_https_and_public_url() -> None:
    with pytest.raises(ValueError, match="public-url"):
        build_security_config(
            host="0.0.0.0",
            port=8787,
            public_url=None,
            allowed_origins=[],
            allowed_clients=[],
            tls_enabled=True,
        )
    with pytest.raises(ValueError, match="HTTPS"):
        build_security_config(
            host="0.0.0.0",
            port=8787,
            public_url="http://vault.example:8787",
            allowed_origins=[],
            allowed_clients=[],
            tls_enabled=False,
        )

    config = external_config(allowed_clients=["198.51.100.0/24", "2001:db8::/32"])
    assert config.external is True
    assert config.secure_cookies is True
    assert config.allowed_hosts == frozenset({"vault.example:8787"})
    assert config.client_allowed("198.51.100.9")
    assert config.client_allowed("::ffff:198.51.100.9")
    assert not config.client_allowed("203.0.113.9")
    assert config.client_allowed("127.0.0.1")


def test_self_signed_certificate_is_stable_and_covers_dns_and_ip(tmp_path: Path) -> None:
    origins = ("https://vault.example:8787", "https://192.0.2.10:8787")
    cert, key, fingerprint = ensure_self_signed_certificate(tmp_path, origins)
    first_cert = cert.read_bytes()
    first_key = key.read_bytes()

    cert_again, key_again, fingerprint_again = ensure_self_signed_certificate(tmp_path, origins)
    assert (cert_again, key_again, fingerprint_again) == (cert, key, fingerprint)
    assert cert.read_bytes() == first_cert
    assert key.read_bytes() == first_key
    assert certificate_covers_origins(cert, origins)
    if os.name != "nt":
        assert key.stat().st_mode & 0o777 == 0o600


def test_fixed_per_ip_and_global_rate_limits() -> None:
    now = [1000.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])

    for _ in range(4):
        assert limiter.record_failure("198.51.100.1") == 0
    assert limiter.record_failure("198.51.100.1") == BLOCK_SECONDS
    assert limiter.retry_after("198.51.100.1") == BLOCK_SECONDS
    assert limiter.retry_after("198.51.100.2") == 0

    now[0] += BLOCK_SECONDS + 1
    assert limiter.retry_after("198.51.100.1") == 0
    for index in range(30):
        limiter.record_failure(f"203.0.113.{index}")
    assert limiter.retry_after("192.0.2.1") == BLOCK_SECONDS


def test_auth_state_rotates_single_session_and_expires_setup_authorization() -> None:
    now = [1000.0]
    auth = AuthState(clock=lambda: now[0])
    first = auth.login()
    second = auth.login()
    assert first != second
    assert not auth.valid_session(first)
    assert auth.valid_session(second)

    setup = auth.authorize_setup()
    assert auth.valid_setup(setup)
    auth.revoke_setup()
    assert not auth.valid_setup(setup)
    setup = auth.authorize_setup()
    now[0] += SETUP_SESSION_SECONDS + 1
    assert not auth.valid_setup(setup)


def test_external_login_requires_origin_and_sets_secure_single_session(tmp_path: Path) -> None:
    runtime = locked_runtime(tmp_path)
    app = create_app(runtime, "setup-secret", external_config(allowed_clients=["198.51.100.0/24"]))
    with TestClient(
        app,
        base_url=PUBLIC_ORIGIN,
        client=("198.51.100.10", 50123),
    ) as client:
        auth = client.get("/api/auth/status")
        assert auth.status_code == 200
        assert auth.json()["authenticated"] is False
        csrf = auth.json()["csrf"]
        assert client.get("/api/status").status_code == 401
        assert client.post(
            "/api/unlock",
            headers={"X-Cryptobox-CSRF": csrf},
            json={"password": "correct password"},
        ).status_code == 403

        login = client.post(
            "/api/unlock",
            headers={"Origin": PUBLIC_ORIGIN, "X-Cryptobox-CSRF": csrf},
            json={"password": "correct password"},
        )
        assert login.status_code == 200
        cookies = "\n".join(login.headers.get_list("set-cookie"))
        assert "__Host-cryptobox_session=" in cookies
        assert all(part in cookies for part in ("HttpOnly", "Path=/", "SameSite=strict", "Secure"))
        status = client.get("/api/status")
        assert status.status_code == 200
        assert status.headers["strict-transport-security"] == "max-age=31536000"

        csrf = status.json()["csrf"]
        locked = client.post(
            "/api/lock",
            headers={"Origin": PUBLIC_ORIGIN, "X-Cryptobox-CSRF": csrf},
        )
        assert locked.status_code == 200
        assert client.get("/api/status").status_code == 401


def test_external_login_blocks_fifth_failure_without_trusting_forwarded_ip(tmp_path: Path) -> None:
    runtime = locked_runtime(tmp_path)
    app = create_app(runtime, "setup-secret", external_config())
    with TestClient(app, base_url=PUBLIC_ORIGIN, client=("198.51.100.20", 50123)) as client:
        csrf = client.get("/api/auth/status").json()["csrf"]
        headers = {
            "Origin": PUBLIC_ORIGIN,
            "X-Cryptobox-CSRF": csrf,
            "X-Forwarded-For": "203.0.113.99",
        }
        for _ in range(4):
            assert client.post("/api/unlock", headers=headers, json={"password": "wrong"}).status_code == 401
        blocked = client.post("/api/unlock", headers=headers, json={"password": "wrong"})
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == str(BLOCK_SECONDS)
        assert client.post(
            "/api/unlock", headers=headers, json={"password": "correct password"}
        ).status_code == 429


def test_external_initialization_needs_one_time_setup_link(tmp_path: Path) -> None:
    runtime = RuntimeState(tmp_path)
    app = create_app(runtime, "one-time-setup", external_config())
    with TestClient(app, base_url=PUBLIC_ORIGIN, client=("198.51.100.30", 50123)) as client:
        auth = client.get("/api/auth/status").json()
        assert auth["initialized"] is False
        assert auth["setup_authorized"] is False
        assert client.get("/api/init/preview").status_code == 401

        exchanged = client.get("/?token=one-time-setup", follow_redirects=False)
        assert exchanged.status_code == 303
        setup_cookie = client.cookies.get("__Host-cryptobox_setup")
        assert setup_cookie
        assert client.get("/?token=one-time-setup", follow_redirects=False).status_code == 403
        auth = client.get("/api/auth/status").json()
        assert auth["setup_authorized"] is True
        initialized = client.post(
            "/api/init",
            headers={"Origin": PUBLIC_ORIGIN, "X-Cryptobox-CSRF": auth["csrf"]},
            json={"password": "x", "password_confirmation": "x"},
        )
        assert initialized.status_code == 200
        assert client.get("/api/status").status_code == 200

        other_root = tmp_path / "other"
        other_root.mkdir()
        client.cookies.clear()
        stale_setup = client.put(
            "/api/root",
            headers={
                "Origin": PUBLIC_ORIGIN,
                "Cookie": (
                    f"__Host-cryptobox_setup={setup_cookie}; "
                    "__Host-cryptobox_csrf=stale-csrf"
                ),
                "X-Cryptobox-CSRF": "stale-csrf",
            },
            json={"path": str(other_root)},
        )
        assert stale_setup.status_code == 401


def test_source_allowlist_rejects_tcp_peer_even_with_forwarded_header(tmp_path: Path) -> None:
    runtime = locked_runtime(tmp_path)
    app = create_app(runtime, "setup-secret", external_config(allowed_clients=["198.51.100.0/24"]))
    with TestClient(app, base_url=PUBLIC_ORIGIN, client=("203.0.113.10", 50000)) as client:
        rejected = client.get(
            "/api/auth/status", headers={"X-Forwarded-For": "198.51.100.10"}
        )
        assert rejected.status_code == 403
