from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .api import create_app
from .constants import APP_NAME, DEFAULT_PORT
from .service import RuntimeState


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
    parser = argparse.ArgumentParser(description="Local encrypted-file browser")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Vault root (default: current directory)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Loopback port")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Vault root does not exist: {root}")
    configure_logging(args.log_level)
    runtime = RuntimeState(root)
    bootstrap_token = secrets.token_urlsafe(32)
    app = create_app(runtime, bootstrap_token)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level=args.log_level,
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    url = f"http://127.0.0.1:{args.port}/?token={bootstrap_token}"
    print(f"{APP_NAME} is available at http://127.0.0.1:{args.port}/")
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
