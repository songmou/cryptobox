from __future__ import annotations

import hashlib
import ipaddress
import os
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


PER_IP_FAILURES = 5
GLOBAL_FAILURES = 30
FAILURE_WINDOW_SECONDS = 5 * 60
BLOCK_SECONDS = 15 * 60
SETUP_SESSION_SECONDS = 15 * 60


def _origin_parts(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Invalid origin URL: {value}")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid origin URL: {value}") from exc
    return parsed


def normalize_origin(value: str) -> str:
    parsed = _origin_parts(value)
    host = parsed.hostname.lower()
    try:
        host_text = f"[{ipaddress.ip_address(host).compressed}]" if ":" in host else host
    except ValueError:
        host_text = host
    default = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default
    suffix = "" if port == default else f":{port}"
    return f"{parsed.scheme}://{host_text}{suffix}"


def origin_authority(origin: str) -> str:
    parsed = _origin_parts(origin)
    host = parsed.hostname.lower()
    try:
        host = f"[{ipaddress.ip_address(host).compressed}]" if ":" in host else host
    except ValueError:
        pass
    default = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default
    return host if port == default else f"{host}:{port}"


def is_loopback_bind(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_client_ip(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return address.compressed
    except ValueError:
        return value.lower()


@dataclass(frozen=True)
class WebSecurityConfig:
    origins: tuple[str, ...]
    allowed_hosts: frozenset[str]
    secure_cookies: bool = False
    external: bool = False
    allowed_clients: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    legacy_bootstrap_session: bool = False

    @classmethod
    def local_default(cls) -> "WebSecurityConfig":
        origins = (
            "http://127.0.0.1",
            "http://localhost",
            "http://[::1]",
            "http://testserver",
        )
        return cls(
            origins=origins,
            allowed_hosts=frozenset({"127.0.0.1", "localhost", "[::1]", "testserver"}),
            legacy_bootstrap_session=True,
        )

    def client_allowed(self, value: str | None) -> bool:
        if not self.allowed_clients:
            return True
        try:
            address = ipaddress.ip_address(normalize_client_ip(value))
        except ValueError:
            return False
        if address.is_loopback:
            return True
        return any(address.version == network.version and address in network for network in self.allowed_clients)


def build_security_config(
    *,
    host: str,
    port: int,
    public_url: str | None,
    allowed_origins: list[str],
    allowed_clients: list[str],
    tls_enabled: bool,
) -> WebSecurityConfig:
    external = not is_loopback_bind(host)
    if external and not public_url:
        raise ValueError("--public-url is required for non-loopback listeners")
    if external and not tls_enabled:
        raise ValueError("HTTPS is required for non-loopback listeners")

    if public_url:
        normalized_public = normalize_origin(public_url)
        if external and not normalized_public.startswith("https://"):
            raise ValueError("--public-url must use https for non-loopback listeners")
        origins = [normalized_public]
    else:
        scheme = "https" if tls_enabled else "http"
        display_host = f"[{host}]" if ":" in host else host
        origins = [normalize_origin(f"{scheme}://{display_host}:{port}")]
    origins.extend(normalize_origin(value) for value in allowed_origins)
    origins = list(dict.fromkeys(origins))
    if external and any(not value.startswith("https://") for value in origins):
        raise ValueError("All external origins must use https")

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in allowed_clients:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(f"Invalid client IP or CIDR: {value}") from exc

    hosts: set[str] = set()
    for value in origins:
        hosts.add(origin_authority(value))
        parsed = _origin_parts(value)
        default = 443 if parsed.scheme == "https" else 80
        if (parsed.port or default) == default:
            host_name = parsed.hostname.lower()
            if ":" in host_name:
                host_name = f"[{host_name}]"
            hosts.add(f"{host_name}:{default}")
    if not external:
        hosts.update({"127.0.0.1", "localhost", "[::1]", "testserver"})
    return WebSecurityConfig(
        origins=tuple(origins),
        allowed_hosts=frozenset(item.lower() for item in hosts),
        secure_cookies=tls_enabled,
        external=external,
        allowed_clients=tuple(networks),
        legacy_bootstrap_session=not external,
    )


@dataclass
class LoginRateLimiter:
    clock: Callable[[], float] = time.monotonic
    per_ip: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    global_failures: deque[float] = field(default_factory=deque)
    ip_blocked_until: dict[str, float] = field(default_factory=dict)
    global_blocked_until: float = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - FAILURE_WINDOW_SECONDS
        while self.global_failures and self.global_failures[0] <= cutoff:
            self.global_failures.popleft()
        for key in list(self.per_ip):
            failures = self.per_ip[key]
            while failures and failures[0] <= cutoff:
                failures.popleft()
            if not failures:
                del self.per_ip[key]
        for key, until in list(self.ip_blocked_until.items()):
            if until <= now:
                del self.ip_blocked_until[key]
        if self.global_blocked_until <= now:
            self.global_blocked_until = 0.0

    def retry_after(self, client_ip: str) -> int:
        now = float(self.clock())
        self._prune(now)
        until = max(self.global_blocked_until, self.ip_blocked_until.get(client_ip, 0.0))
        return max(0, int(until - now + 0.999))

    def record_failure(self, client_ip: str) -> int:
        now = float(self.clock())
        self._prune(now)
        failures = self.per_ip[client_ip]
        failures.append(now)
        self.global_failures.append(now)
        if len(failures) >= PER_IP_FAILURES:
            self.ip_blocked_until[client_ip] = now + BLOCK_SECONDS
        if len(self.global_failures) >= GLOBAL_FAILURES:
            self.global_blocked_until = now + BLOCK_SECONDS
        return self.retry_after(client_ip)

    def record_success(self, client_ip: str) -> None:
        self.per_ip.pop(client_ip, None)
        self.ip_blocked_until.pop(client_ip, None)


@dataclass
class AuthState:
    clock: Callable[[], float] = time.monotonic
    session_token: str | None = None
    setup_token: str | None = None
    setup_expires_at: float = 0.0

    def new_csrf(self) -> str:
        return secrets.token_urlsafe(24)

    def ensure_csrf(self, value: str | None) -> str:
        return value or self.new_csrf()

    def valid_csrf(self, cookie: str | None, header: str | None) -> bool:
        return bool(cookie and header and secrets.compare_digest(cookie, header))

    def login(self) -> str:
        self.session_token = secrets.token_urlsafe(32)
        return self.session_token

    def revoke(self) -> None:
        self.session_token = None

    def valid_session(self, value: str | None) -> bool:
        return bool(value and self.session_token and secrets.compare_digest(value, self.session_token))

    def authorize_setup(self) -> str:
        self.setup_token = secrets.token_urlsafe(32)
        self.setup_expires_at = float(self.clock()) + SETUP_SESSION_SECONDS
        return self.setup_token

    def valid_setup(self, value: str | None) -> bool:
        if self.setup_expires_at <= float(self.clock()):
            self.setup_token = None
        return bool(value and self.setup_token and secrets.compare_digest(value, self.setup_token))

    def revoke_setup(self) -> None:
        self.setup_token = None
        self.setup_expires_at = 0.0


def _safe_certificate_stem(origins: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(origins).encode()).hexdigest()[:16]


def ensure_self_signed_certificate(
    config_dir: Path, origins: tuple[str, ...]
) -> tuple[Path, Path, str]:
    target = config_dir / "tls"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = _safe_certificate_stem(origins)
    cert_path = target / f"self-signed-{stem}.crt.pem"
    key_path = target / f"self-signed-{stem}.key.pem"
    regenerate = not (cert_path.is_file() and key_path.is_file())
    if not regenerate:
        try:
            current = x509.load_pem_x509_certificate(cert_path.read_bytes())
            regenerate = (
                current.not_valid_after_utc <= datetime.now(UTC) + timedelta(days=7)
                or not certificate_covers_origins(cert_path, origins)
            )
        except (OSError, ValueError):
            regenerate = True
    if regenerate:
        hosts = [urlsplit(origin).hostname for origin in origins]
        hosts = [host for host in hosts if host]
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=397))
        )
        sans: list[x509.GeneralName] = []
        for host in dict.fromkeys(hosts):
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(host)))
            except ValueError:
                sans.append(x509.DNSName(host))
        certificate = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False).sign(
            key, hashes.SHA256()
        )
        key_payload = (
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        cert_payload = certificate.public_bytes(serialization.Encoding.PEM)
        key_tmp = key_path.with_suffix(".tmp")
        cert_tmp = cert_path.with_suffix(".tmp")
        key_tmp.write_bytes(key_payload)
        cert_tmp.write_bytes(cert_payload)
        if os.name != "nt":
            os.chmod(key_tmp, 0o600)
            os.chmod(cert_tmp, 0o644)
        os.replace(key_tmp, key_path)
        os.replace(cert_tmp, cert_path)
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex(":").upper()
    return cert_path, key_path, fingerprint


def certificate_hosts(cert_path: Path) -> set[str]:
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return set()
    result = {value.lower() for value in extension.value.get_values_for_type(x509.DNSName)}
    result.update(str(value) for value in extension.value.get_values_for_type(x509.IPAddress))
    return result


def certificate_covers_origins(cert_path: Path, origins: tuple[str, ...]) -> bool:
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    now = datetime.now(UTC)
    if certificate.not_valid_before_utc > now or certificate.not_valid_after_utc <= now:
        return False
    names = certificate_hosts(cert_path)
    for origin in origins:
        host = urlsplit(origin).hostname
        if not host:
            return False
        try:
            if str(ipaddress.ip_address(host)) not in names:
                return False
        except ValueError:
            host = host.lower()
            if host in names:
                continue
            labels = host.split(".")
            if len(labels) < 2 or f"*.{'.'.join(labels[1:])}" not in names:
                return False
    return True
