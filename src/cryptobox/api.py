from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .archive import stream_zip
from .constants import CONTROL_DIR, FILE_MAGIC
from .crypto import iter_decrypted, read_header_path
from .errors import CryptoboxError, InvalidPassword, UnsafePath
from .scanner import iter_regular_files, preview_root
from .service import RuntimeState
from . import __version__
from .preview import content_media_type, preview_kind
from .util import (
    current_executable,
    display_name,
    id_to_relative,
    is_internal_path,
    path_to_id,
    reject_source_tree,
    safe_join,
    secure_compare,
)

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
class InitRequest(BaseModel):
    password: str
    password_confirmation: str


class UnlockRequest(BaseModel):
    password: str


class PasswordRequest(BaseModel):
    new_password: str
    confirmation: str


class RootRequest(BaseModel):
    path: str


class ZipRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5000)


def create_app(runtime: RuntimeState, bootstrap_token: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="Cryptobox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(24)
    bootstrap_used = False
    export_tickets: dict[str, list[tuple[Path, str]]] = {}

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.headers.get("host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "Invalid Host header"}, status_code=400)
        origin = request.headers.get("origin")
        if origin and not any(
            origin.startswith(prefix)
            for prefix in ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
        ):
            return JSONResponse({"detail": "Invalid Origin header"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/static/preview-host.html":
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src blob: data:; media-src blob: data:; font-src blob: data:; "
                "connect-src 'none'; object-src 'none'; frame-src blob:; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'self'"
            )
        elif request.url.path.startswith("/api/content/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; object-src 'self'; base-uri 'none'; frame-ancestors 'self'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; "
                "style-src 'self'; script-src 'self'; object-src 'self'; frame-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            )
        return response

    def require_session(cryptobox_session: Annotated[str | None, Cookie()] = None) -> None:
        if cryptobox_session is None or not secure_compare(cryptobox_session, session_token):
            raise HTTPException(status_code=401, detail="Open the one-time URL printed by Cryptobox")

    def require_csrf(
        cryptobox_session: Annotated[str | None, Cookie()] = None,
        cryptobox_csrf: Annotated[str | None, Cookie()] = None,
        x_cryptobox_csrf: Annotated[str | None, Header()] = None,
    ) -> None:
        if (
            cryptobox_session is None
            or not secure_compare(cryptobox_session, session_token)
            or
            cryptobox_csrf is None
            or x_cryptobox_csrf is None
            or not secure_compare(cryptobox_csrf, csrf_token)
            or not secure_compare(x_cryptobox_csrf, csrf_token)
        ):
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    def unlocked() -> None:
        require_session  # keep dependency visible to static checkers
        if not runtime.unlocked:
            raise HTTPException(status_code=423, detail="Vault is locked")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, token: str | None = Query(default=None)):
        nonlocal bootstrap_used
        if token is not None:
            if bootstrap_used or not secure_compare(token, bootstrap_token):
                raise HTTPException(status_code=403, detail="Startup token is invalid or already used")
            bootstrap_used = True
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(
                "cryptobox_session",
                session_token,
                httponly=True,
                samesite="strict",
                secure=False,
                path="/",
            )
            response.set_cookie(
                "cryptobox_csrf", csrf_token, httponly=False, samesite="strict", secure=False, path="/"
            )
            return response
        return FileResponse(static_dir / "index.html")

    @app.get("/api/version")
    async def app_version() -> dict[str, str]:
        return {"version": __version__}

    @app.get("/api/status", dependencies=[Depends(require_session)])
    async def status() -> dict[str, object]:
        return {
            "initialized": runtime.manager.initialized,
            "unlocked": runtime.unlocked,
            "root": str(runtime.root),
            "operation": runtime.tracker.snapshot(),
            "csrf": csrf_token,
        }

    @app.get("/api/init/preview", dependencies=[Depends(require_session)])
    async def init_preview() -> dict[str, object]:
        if runtime.manager.initialized:
            raise HTTPException(status_code=409, detail="Vault is already initialized")
        summary = await asyncio.to_thread(preview_root, runtime.root)
        return {"root": str(runtime.root), **summary}

    @app.put("/api/root", dependencies=[Depends(require_csrf)])
    async def change_root(payload: RootRequest) -> dict[str, object]:
        if runtime.manager.initialized or runtime.unlocked:
            raise HTTPException(status_code=409, detail="Root can only be changed before initialization")
        candidate = Path(payload.path).expanduser().resolve()
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail="Root must be an existing directory")
        try:
            reject_source_tree(candidate)
            runtime.change_root(candidate)
        except UnsafePath as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"root": str(runtime.root)}

    @app.post("/api/init", dependencies=[Depends(require_csrf)])
    async def initialize(payload: InitRequest) -> dict[str, object]:
        if runtime.manager.initialized:
            raise HTTPException(status_code=409, detail="Vault is already initialized")
        if payload.password != payload.password_confirmation:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        try:
            reject_source_tree(runtime.root)
            session = await asyncio.to_thread(runtime.manager.create, payload.password)
            runtime.attach_session(session)
            runtime.start_scan()
        except CryptoboxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"accepted": True}

    @app.post("/api/unlock", dependencies=[Depends(require_csrf)])
    async def unlock(payload: UnlockRequest) -> dict[str, object]:
        if runtime.unlocked:
            return {"unlocked": True}
        try:
            session = await asyncio.to_thread(runtime.manager.unlock, payload.password)
            runtime.attach_session(session)
            runtime.start_scan()
        except InvalidPassword as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except CryptoboxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"unlocked": True}

    @app.post("/api/lock", dependencies=[Depends(require_csrf)])
    async def lock() -> dict[str, bool]:
        await runtime.lock()
        return {"locked": True}

    @app.post("/api/rescan", dependencies=[Depends(require_csrf)])
    async def rescan() -> dict[str, bool]:
        unlocked()
        runtime.start_scan()
        return {"accepted": True}

    @app.post("/api/verify", dependencies=[Depends(require_csrf)])
    async def verify() -> dict[str, bool]:
        unlocked()
        runtime.start_verify()
        return {"accepted": True}

    @app.post("/api/password", dependencies=[Depends(require_csrf)])
    async def password(payload: PasswordRequest) -> dict[str, bool]:
        unlocked()
        if payload.confirmation != payload.new_password:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        assert runtime.session is not None
        try:
            await runtime.change_password(payload.new_password)
        except CryptoboxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"changed": True}

    @app.get("/api/tree", dependencies=[Depends(require_session)])
    async def tree(
        path_id: str = "", offset: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=1000)
    ) -> dict[str, object]:
        unlocked()
        relative = id_to_relative(path_id)
        directory = safe_join(runtime.root, relative)
        if not directory.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        entries: list[dict[str, object]] = []
        try:
            children = os.scandir(directory)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        assert runtime.index is not None
        executable = current_executable()
        visible_index = 0
        has_more = False
        with children:
            for child in children:
                path = Path(child.path)
                if is_internal_path(runtime.root, path, executable) or child.is_symlink():
                    continue
                child_relative = path.relative_to(runtime.root)
                item: dict[str, object] | None = None
                if child.is_dir(follow_symlinks=False):
                    item = {"id": path_to_id(child_relative), "name": display_name(path), "kind": "directory"}
                elif child.is_file(follow_symlinks=False):
                    cached = runtime.index.get_entry(child_relative)
                    if cached is not None:
                        item = {
                            "id": path_to_id(child_relative),
                            "name": display_name(path),
                            "kind": "file",
                            "size": cached.plain_size,
                            "modified": child.stat(follow_symlinks=False).st_mtime,
                            "media_type": content_media_type(path.name),
                            "preview_kind": preview_kind(path.name),
                        }
                if item is None:
                    continue
                if visible_index < offset:
                    visible_index += 1
                    continue
                if len(entries) >= limit:
                    has_more = True
                    break
                entries.append(item)
                visible_index += 1
        return {"path_id": path_id, "entries": entries, "next_offset": offset + len(entries), "has_more": has_more}

    def resolve_file(path_id: str) -> tuple[Path, object]:
        if not runtime.session:
            raise HTTPException(status_code=423, detail="Vault is locked")
        relative = id_to_relative(path_id)
        path = safe_join(runtime.root, relative)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        try:
            header = read_header_path(path, runtime.session)
        except CryptoboxError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return path, header

    def content_response(request: Request, path_id: str, download: bool = False) -> Response:
        path, header = resolve_file(path_id)
        session = runtime.session
        assert session is not None
        plain_size = header.plain_size  # type: ignore[attr-defined]
        start, end = 0, plain_size
        status_code = 200
        range_value = request.headers.get("range")
        if range_value and "," not in range_value:
            match = _RANGE.match(range_value)
            if match:
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(plain_size, int(right) + 1) if right else plain_size
                elif right:
                    length = min(int(right), plain_size)
                    start, end = plain_size - length, plain_size
                if start >= plain_size or end <= start:
                    return Response(status_code=416, headers={"Content-Range": f"bytes */{plain_size}"})
                status_code = 206
        media_type = content_media_type(path.name)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start),
            "ETag": f'"{hashlib.sha256(header.raw).hexdigest()[:32]}"',  # type: ignore[attr-defined]
            "Content-Disposition": ("attachment" if download else "inline")
            + f"; filename*=UTF-8''{quote(path.name)}",
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{plain_size}"
        if request.method == "HEAD":
            return Response(status_code=status_code, media_type=media_type, headers=headers)
        return StreamingResponse(
            iter_decrypted(path, session, start, end),
            status_code=status_code,
            media_type=media_type,
            headers=headers,
        )

    @app.api_route("/api/content/{path_id}", methods=["GET", "HEAD"], dependencies=[Depends(require_session)])
    async def content(request: Request, path_id: str) -> Response:
        unlocked()
        return content_response(request, path_id, False)

    @app.get("/api/download/{path_id}", dependencies=[Depends(require_session)])
    async def download(request: Request, path_id: str) -> Response:
        unlocked()
        return content_response(request, path_id, True)

    @app.post("/api/export-ticket", dependencies=[Depends(require_csrf)])
    async def export_ticket(payload: ZipRequest) -> dict[str, str]:
        unlocked()
        assert runtime.session is not None
        selected: dict[Path, str] = {}
        for item_id in payload.ids:
            relative = id_to_relative(item_id)
            path = safe_join(runtime.root, relative)
            if path.is_dir():
                for source, child_relative, _ in iter_regular_files(path):
                    read_header_path(source, runtime.session)
                    selected[source] = str(relative / child_relative).replace(os.sep, "/")
            elif path.is_file():
                read_header_path(path, runtime.session)
                selected[path] = str(relative).replace(os.sep, "/")
        if not selected:
            raise HTTPException(status_code=404, detail="No encrypted files selected")
        ticket = secrets.token_urlsafe(24)
        export_tickets[ticket] = list(selected.items())
        return {"url": f"/api/download-zip/{ticket}"}

    @app.get("/api/download-zip/{ticket}", dependencies=[Depends(require_session)])
    async def download_zip(ticket: str) -> StreamingResponse:
        unlocked()
        files = export_tickets.pop(ticket, None)
        if files is None:
            raise HTTPException(status_code=404, detail="Export ticket is invalid or already used")
        assert runtime.session is not None
        return StreamingResponse(
            stream_zip(files, runtime.session),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=cryptobox-export.zip"},
        )

    @app.post("/api/shutdown", dependencies=[Depends(require_csrf)])
    async def shutdown() -> dict[str, bool]:
        runtime.shutdown_event.set()
        return {"shutting_down": True}

    @app.exception_handler(UnsafePath)
    async def unsafe_path_handler(_: Request, exc: UnsafePath) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    return app
