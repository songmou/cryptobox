from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import secrets
import ssl
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .api import create_app
from .constants import APP_NAME, DEFAULT_PORT
from .service import RuntimeState
from .settings import load_last_root, save_last_root, settings_path
from .web_security import (
    build_security_config,
    certificate_covers_origins,
    ensure_self_signed_certificate,
)
from . import __version__


def configure_logging(level: str) -> None:
    if sys.platform == "darwin":
        log_dir = Path.home() / "Library" / "Logs" / "Cryptobox"
    elif os.name == "nt":
        log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Cryptobox" / "Logs"
    else:
        log_dir = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cryptobox"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_dir / "cryptobox.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
        )
    except OSError:
        pass
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encrypted-file browser with secure web access")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Vault root (default: last used directory, then current directory)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Listen address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Listen port")
    parser.add_argument("--public-url", help="Canonical browser origin, required outside loopback")
    parser.add_argument(
        "--allowed-origin", action="append", default=[], help="Additional allowed HTTPS origin"
    )
    parser.add_argument(
        "--allow-client", action="append", default=[], help="Allowed client IP or CIDR (repeatable)"
    )
    parser.add_argument("--tls-cert", type=Path, help="PEM certificate chain")
    parser.add_argument("--tls-key", type=Path, help="PEM private key")
    parser.add_argument(
        "--self-signed", action="store_true", help="Generate and reuse a self-signed TLS certificate"
    )
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    parser.add_argument("--version", action="version", version=f"cryptobox {__version__}")
    return parser.parse_args(argv)


def select_root(explicit: Path | None, config_path: Path, cwd: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    return load_last_root(config_path) or (cwd or Path.cwd()).resolve()


async def run(args: argparse.Namespace) -> None:
    config_path = settings_path()
    root = select_root(args.root, config_path)
    if not root.is_dir():
        raise SystemExit(f"Vault root does not exist: {root}")
    configure_logging(args.log_level)
    if bool(args.tls_cert) != bool(args.tls_key):
        raise SystemExit("--tls-cert and --tls-key must be provided together")
    if args.self_signed and (args.tls_cert or args.tls_key):
        raise SystemExit("--self-signed cannot be combined with --tls-cert or --tls-key")
    tls_enabled = bool(args.self_signed or args.tls_cert)
    try:
        web_security = build_security_config(
            host=args.host,
            port=args.port,
            public_url=args.public_url,
            allowed_origins=args.allowed_origin,
            allowed_clients=args.allow_client,
            tls_enabled=tls_enabled,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    cert_path: Path | None = args.tls_cert.expanduser().resolve() if args.tls_cert else None
    key_path: Path | None = args.tls_key.expanduser().resolve() if args.tls_key else None
    fingerprint: str | None = None
    if args.self_signed:
        try:
            cert_path, key_path, fingerprint = ensure_self_signed_certificate(
                config_path.parent, web_security.origins
            )
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Unable to create or load the self-signed certificate: {exc}") from exc
    if cert_path is not None and key_path is not None:
        if not cert_path.is_file() or not key_path.is_file():
            raise SystemExit("TLS certificate or private key does not exist")
        try:
            if not certificate_covers_origins(cert_path, web_security.origins):
                raise SystemExit(
                    "TLS certificate is expired, not yet valid, or its SAN does not cover "
                    "every configured public origin"
                )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
        except (OSError, ValueError, ssl.SSLError) as exc:
            raise SystemExit(f"Unable to load TLS certificate and key: {exc}") from exc
    try:
        save_last_root(config_path, root)
    except OSError:
        logging.getLogger(__name__).warning("Unable to remember vault directory", exc_info=True)
    runtime = RuntimeState(root, settings_path=config_path)
    bootstrap_token = secrets.token_urlsafe(32)
    app = create_app(runtime, bootstrap_token, web_security)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
        lifespan="off",
        proxy_headers=False,
        server_header=False,
        ssl_certfile=str(cert_path) if cert_path else None,
        ssl_keyfile=str(key_path) if key_path else None,
    )
    server = uvicorn.Server(config)
    base_url = web_security.origins[0]
    url = f"{base_url}/?token={bootstrap_token}" if not runtime.manager.initialized else f"{base_url}/"
    print(f"{APP_NAME} is available at {base_url}/")
    if not runtime.manager.initialized:
        print(f"One-time initialization URL: {url}")
    if fingerprint:
        print(f"Self-signed certificate SHA-256: {fingerprint}")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    server_task = asyncio.create_task(server.serve())
    shutdown_task = asyncio.create_task(runtime.shutdown_event.wait())
    try:
        done, pending = await asyncio.wait(
            {server_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if shutdown_task in done and not server_task.done():
            server.should_exit = True
            await server_task
        for task in pending:
            task.cancel()
    finally:
        await runtime.close()


def main() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("Cryptobox requires Python 3.11 or newer")
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
