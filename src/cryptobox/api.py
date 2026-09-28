from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .archive import stream_zip
from . import __version__
from .crypto import iter_decrypted, read_header_path
from .errors import CryptoboxError, InvalidPassword, UnsafePath
from .preview import content_media_type, preview_kind
from .scanner import iter_regular_files, preview_root
from .service import RuntimeState
from .settings import AppSettings, load_settings, save_preferences
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
from .web_security import (
    AuthState,
    LoginRateLimiter,
    WebSecurityConfig,
    normalize_client_ip,
    normalize_origin,
)

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
LOGGER = logging.getLogger(__name__)


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


class SettingsRequest(BaseModel):
    auto_lock_minutes: int = Field(ge=1, le=120)
    theme: str


class ZipRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5000)


def create_app(
    runtime: RuntimeState,
    bootstrap_token: str,
    security: WebSecurityConfig | None = None,
) -> FastAPI:
    security = security or WebSecurityConfig.local_default()

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
    auth = AuthState()
    limiter = LoginRateLimiter()
    password_check = asyncio.Lock()
    bootstrap_used = False
    export_tickets: dict[str, list[tuple[Path, str]]] = {}
    session_cookie = "__Host-cryptobox_session" if security.secure_cookies else "cryptobox_session"
    csrf_cookie = "__Host-cryptobox_csrf" if security.secure_cookies else "cryptobox_csrf"
    setup_cookie = "__Host-cryptobox_setup" if security.secure_cookies else "cryptobox_setup"

    def revoke_session() -> None:
        auth.revoke()

    runtime.on_lock = revoke_session

    def client_ip(request: Request) -> str:
        return normalize_client_ip(request.client.host if request.client else None)

    def set_cookie(response: Response, name: str, value: str, *, httponly: bool) -> None:
        response.set_cookie(
            name,
            value,
            httponly=httponly,
            samesite="strict",
            secure=security.secure_cookies,
            path="/",
        )

    def set_session_cookie(response: Response) -> None:
        set_cookie(response, session_cookie, auth.login(), httponly=True)

    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            session_cookie,
            path="/",
            secure=security.secure_cookies,
            httponly=True,
        )

    def set_setup_cookie(response: Response) -> None:
        set_cookie(response, setup_cookie, auth.authorize_setup(), httponly=True)

    def clear_setup_cookie(response: Response) -> None:
        response.delete_cookie(setup_cookie, path="/", secure=security.secure_cookies, httponly=True)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        remote = client_ip(request)
        if not security.client_allowed(remote):
            LOGGER.warning("security source_rejected ip=%s", remote)
            return JSONResponse({"detail": "Client address is not allowed"}, status_code=403)
        host = request.headers.get("host", "").lower()
        if host not in security.allowed_hosts:
            LOGGER.warning("security host_rejected ip=%s", remote)
            return JSONResponse({"detail": "Invalid Host header"}, status_code=400)
        origin = request.headers.get("origin")
        normalized_origin: str | None = None
        if origin:
            try:
                normalized_origin = normalize_origin(origin)
            except ValueError:
                normalized_origin = None
        unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
        if (origin and normalized_origin not in security.origins) or (
            security.external and unsafe and not origin
        ):
            LOGGER.warning("security origin_rejected ip=%s", remote)
            return JSONResponse({"detail": "Invalid Origin header"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if security.external:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
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

    def require_session(request: Request) -> None:
        if not auth.valid_session(request.cookies.get(session_cookie)):
            raise HTTPException(status_code=401, detail="Authentication required")

    def require_session_or_setup(request: Request) -> None:
        if not (
            auth.valid_session(request.cookies.get(session_cookie))
            or (
                not runtime.manager.initialized
                and auth.valid_setup(request.cookies.get(setup_cookie))
            )
        ):
            raise HTTPException(status_code=401, detail="Authentication required")

    def require_public_csrf(
        request: Request,
        x_cryptobox_csrf: Annotated[str | None, Header()] = None,
    ) -> None:
        if not auth.valid_csrf(request.cookies.get(csrf_cookie), x_cryptobox_csrf):
            LOGGER.warning("security csrf_rejected ip=%s", client_ip(request))
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    def require_csrf(
        request: Request,
        x_cryptobox_csrf: Annotated[str | None, Header()] = None,
    ) -> None:
        require_session(request)
        require_public_csrf(request, x_cryptobox_csrf)

    def require_setup_csrf(
        request: Request,
        x_cryptobox_csrf: Annotated[str | None, Header()] = None,
    ) -> None:
        require_session_or_setup(request)
        require_public_csrf(request, x_cryptobox_csrf)

    def unlocked() -> None:
        if not runtime.unlocked:
            raise HTTPException(status_code=423, detail="Vault is locked")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, token: str | None = Query(default=None)):
        nonlocal bootstrap_used
        if token is not None:
            if bootstrap_used or not secure_compare(token, bootstrap_token):
                LOGGER.warning("security setup_token_rejected ip=%s", client_ip(request))
                raise HTTPException(status_code=403, detail="Startup token is invalid or already used")
            bootstrap_used = True
            response = RedirectResponse(url="/", status_code=303)
            if not runtime.manager.initialized:
                set_setup_cookie(response)
            if security.legacy_bootstrap_session:
                set_session_cookie(response)
            return response
        return FileResponse(static_dir / "index.html")

    @app.get("/api/auth/status")
    async def auth_status(request: Request, response: Response) -> dict[str, object]:
        csrf = auth.ensure_csrf(request.cookies.get(csrf_cookie))
        set_cookie(response, csrf_cookie, csrf, httponly=False)
        return {
            "initialized": runtime.manager.initialized,
            "authenticated": auth.valid_session(request.cookies.get(session_cookie)),
            "setup_authorized": auth.valid_setup(request.cookies.get(setup_cookie)),
            "csrf": csrf,
        }

    @app.get("/api/version")
    async def app_version() -> dict[str, str]:
        return {"version": __version__}

    @app.get("/api/status", dependencies=[Depends(require_session)])
    async def status(request: Request, response: Response) -> dict[str, object]:
        csrf = auth.ensure_csrf(request.cookies.get(csrf_cookie))
        set_cookie(response, csrf_cookie, csrf, httponly=False)
        remaining = runtime.ensure_auto_lock(auto_lock_timeout_seconds())
        return {
            "initialized": runtime.manager.initialized,
            "unlocked": runtime.unlocked,
            "root": str(runtime.root),
            "operation": runtime.tracker.snapshot(),
            "csrf": csrf,
            "auto_lock_remaining_seconds": remaining,
        }

    def current_settings() -> AppSettings:
        if runtime.settings_path is None:
            return AppSettings(last_root=runtime.root)
        return load_settings(runtime.settings_path)

    def auto_lock_timeout_seconds() -> int:
        return current_settings().auto_lock_minutes * 60

    @app.get("/api/settings", dependencies=[Depends(require_session)])
    async def get_settings() -> dict[str, object]:
        settings = current_settings()
        return {
            "root": str(runtime.root),
            "auto_lock_minutes": settings.auto_lock_minutes,
            "theme": settings.theme,
        }

    @app.put("/api/settings", dependencies=[Depends(require_csrf)])
    async def update_settings(payload: SettingsRequest) -> dict[str, object]:
        unlocked()
        if payload.theme not in {"system", "light", "dark"}:
            raise HTTPException(status_code=422, detail="Theme must be system, light, or dark")
        if runtime.settings_path is None:
            raise HTTPException(status_code=503, detail="Settings storage is unavailable")
        try:
            settings = await asyncio.to_thread(
                save_preferences,
                runtime.settings_path,
                auto_lock_minutes=payload.auto_lock_minutes,
                theme=payload.theme,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runtime.reset_auto_lock(settings.auto_lock_minutes * 60)
        return {
            "root": str(runtime.root),
            "auto_lock_minutes": settings.auto_lock_minutes,
            "theme": settings.theme,
        }

    @app.get("/api/init/preview", dependencies=[Depends(require_session_or_setup)])
    async def init_preview() -> dict[str, object]:
        if runtime.manager.initialized:
            raise HTTPException(status_code=409, detail="Vault is already initialized")
        summary = await asyncio.to_thread(preview_root, runtime.root)
        return {"root": str(runtime.root), **summary}

    @app.put("/api/root", dependencies=[Depends(require_setup_csrf)])
    async def change_root(payload: RootRequest, response: Response) -> dict[str, object]:
        requested = Path(payload.path).expanduser()
        if not requested.is_absolute():
            raise HTTPException(status_code=400, detail="Root must be an absolute path")
        candidate = requested.resolve()
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail="Root must be an existing directory")
        try:
            reject_source_tree(candidate)
            await runtime.change_root(candidate)
        except UnsafePath as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_session_cookie(response)
        if security.legacy_bootstrap_session:
            set_session_cookie(response)
        if not runtime.manager.initialized:
            set_setup_cookie(response)
        else:
            auth.revoke_setup()
            clear_setup_cookie(response)
        return {"root": str(runtime.root)}

    @app.post("/api/init", dependencies=[Depends(require_setup_csrf)])
    async def initialize(payload: InitRequest, response: Response) -> dict[str, object]:
        if runtime.manager.initialized:
            raise HTTPException(status_code=409, detail="Vault is already initialized")
        if payload.password != payload.password_confirmation:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        try:
            reject_source_tree(runtime.root)
            session = await asyncio.to_thread(runtime.manager.create, payload.password)
            runtime.attach_session(session)
            runtime.reset_auto_lock(auto_lock_timeout_seconds())
            runtime.start_scan()
        except CryptoboxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_session_cookie(response)
        auth.revoke_setup()
        clear_setup_cookie(response)
        LOGGER.info("security vault_initialized")
        return {"accepted": True}

    @app.post("/api/unlock", dependencies=[Depends(require_public_csrf)])
    async def unlock(
        payload: UnlockRequest,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        remote = client_ip(request)
        retry = limiter.retry_after(remote)
        if retry:
            LOGGER.warning("security login_blocked ip=%s retry_after=%s", remote, retry)
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts",
                headers={"Retry-After": str(retry)},
            )
        if password_check.locked():
            raise HTTPException(
                status_code=429,
                detail="Password verification is busy",
                headers={"Retry-After": "1"},
            )
        try:
            async with password_check:
                retry = limiter.retry_after(remote)
                if retry:
                    raise HTTPException(
                        status_code=429,
                        detail="Too many login attempts",
                        headers={"Retry-After": str(retry)},
                    )
                candidate = await asyncio.to_thread(runtime.manager.unlock, payload.password)
                if runtime.unlocked:
                    candidate.close()
                else:
                    runtime.attach_session(candidate)
                    runtime.reset_auto_lock(auto_lock_timeout_seconds())
                    runtime.start_scan()
        except InvalidPassword as exc:
            retry = limiter.record_failure(remote)
            LOGGER.warning("security login_failed ip=%s", remote)
            if retry:
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts",
                    headers={"Retry-After": str(retry)},
                ) from exc
            raise HTTPException(status_code=401, detail="Invalid password") from exc
        except CryptoboxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        limiter.record_success(remote)
        replacing_session = auth.session_token is not None
        set_session_cookie(response)
        clear_setup_cookie(response)
        LOGGER.info(
            "security %s ip=%s",
            "session_replaced" if replacing_session else "login_succeeded",
            remote,
        )
        return {"unlocked": True}

    @app.post("/api/lock", dependencies=[Depends(require_csrf)])
    async def lock(response: Response) -> dict[str, bool]:
        await runtime.lock()
        clear_session_cookie(response)
        if security.legacy_bootstrap_session:
            set_session_cookie(response)
        return {"locked": True}

    @app.post("/api/activity", dependencies=[Depends(require_csrf)])
    async def activity() -> dict[str, int]:
        unlocked()
        if runtime.auto_lock_remaining_seconds() == 0:
            await runtime.lock()
            raise HTTPException(status_code=423, detail="Vault is locked")
        remaining = runtime.reset_auto_lock(auto_lock_timeout_seconds())
        assert remaining is not None
        return {"auto_lock_remaining_seconds": remaining}

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
    async def password(payload: PasswordRequest, response: Response) -> dict[str, bool]:
        unlocked()
        if payload.confirmation != payload.new_password:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        assert runtime.session is not None
        try:
            await runtime.change_password(payload.new_password)
        except CryptoboxError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_session_cookie(response)
        return {"changed": True}

    @app.get("/api/tree", dependencies=[Depends(require_session)])
    async def tree(
        path_id: str = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=1000),
        directories_only: bool = False,
        sort_by: Literal["name", "modified", "type", "size"] = "name",
        sort_order: Literal["asc", "desc"] = "asc",
    ) -> dict[str, object]:
        unlocked()
        relative = id_to_relative(path_id)
        directory = safe_join(runtime.root, relative)
        if not directory.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        all_entries: list[dict[str, object]] = []
        try:
            children = os.scandir(directory)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        assert runtime.index is not None
        executable = current_executable()
        with children:
            for child in children:
                path = Path(child.path)
                if is_internal_path(runtime.root, path, executable) or child.is_symlink():
                    continue
                child_relative = path.relative_to(runtime.root)
                item: dict[str, object] | None = None
                if child.is_dir(follow_symlinks=False):
                    stat_result = path.stat(follow_symlinks=False)
                    item = {
                        "id": path_to_id(child_relative),
                        "name": display_name(path),
                        "kind": "directory",
                        "size": None,
                        "modified": stat_result.st_mtime,
                        "file_type": "folder",
                    }
                elif not directories_only and child.is_file(follow_symlinks=False):
                    # DirEntry.stat() can report zero or otherwise unreliable
                    # device/inode values on Windows.  The scanner records the
                    # full Path.stat() identity in the authenticated index, so
                    # use the same source here when deciding whether a cached
                    # encrypted entry is still the file on disk.
                    stat_result = path.stat(follow_symlinks=False)
                    cached = runtime.index.get_if_unchanged(child_relative, stat_result)
                    item = {
                        "id": path_to_id(child_relative),
                        "name": display_name(path),
                        "kind": "file",
                        "size": cached.plain_size if cached is not None else stat_result.st_size,
                        "modified": stat_result.st_mtime,
                        "media_type": content_media_type(path.name),
                        "preview_kind": preview_kind(path.name),
                        "encrypted": cached is not None,
                        "file_type": path.suffix.lower().lstrip(".") or "file",
                    }
                if item is None:
                    continue
                all_entries.append(item)

        def entry_sort_key(item: dict[str, object]) -> tuple[object, str, str]:
            name = str(item["name"])
            if sort_by == "modified":
                primary: object = float(item["modified"])
            elif sort_by == "type":
                primary = str(item["file_type"]).casefold()
            elif sort_by == "size":
                primary = int(item["size"] or 0)
            else:
                primary = name.casefold()
            return primary, name.casefold(), str(item["id"])

        reverse = sort_order == "desc"
        directories = [item for item in all_entries if item["kind"] == "directory"]
        files = [item for item in all_entries if item["kind"] == "file"]
        directories.sort(key=entry_sort_key, reverse=reverse)
        files.sort(key=entry_sort_key, reverse=reverse)
        sorted_entries = directories + files
        total_entries = len(sorted_entries)
        entries = sorted_entries[offset : offset + limit]
        next_offset = min(total_entries, offset + len(entries))
        return {
            "path_id": path_id,
            "entries": entries,
            "next_offset": next_offset,
            "has_more": next_offset < total_entries,
            "total_entries": total_entries,
        }

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
        try:
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
        except CryptoboxError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
