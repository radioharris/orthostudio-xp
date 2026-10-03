# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a page server of OrthoStudio XP answers before its routes: the guard of its API (spec
``docs/specs/api.md`` section 3) and its page's files. The engine's app (``app.py``) serves with
it, and OrthoStudio XP Live's window too (``orthostudio.live.web``).

It stands apart from ``app.py``, which imports the whole tile engine: Live's window took it from
there and loaded the engine for nothing, half of the code its start read, which took 44 s on a
cloud PC the first time after an install (2026-10-03)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from orthostudio.api.stages import error_json

PAGE_CACHE_CONTROL = "no-cache"
"""The page's files are revalidated on every load (ETag / Last-Modified make it cheap). Without
it a browser kept an old ``geo.js`` next to a new ``map.js`` after an update, and the page did not
start: an ES module import of a missing export fails the whole page."""


PAGE_MEDIA_TYPES: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
"""The type of each kind of file of the page, fixed here rather than asked of the system. Python
asks Windows' registry, where a program may have written ``text/plain`` for ``.js``, and a browser
refuses to run a module of that type: users on Windows saw the menu alone, greyed, in every
browser, and nothing answered a click (2026-09-22)."""


class _RevalidatedStaticFiles(StaticFiles):
    """``StaticFiles`` answering with ``Cache-Control: no-cache`` (``PAGE_CACHE_CONTROL``), and
    with the type of ``PAGE_MEDIA_TYPES`` for the page's own kinds of file."""

    def file_response(self, full_path: Any, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(full_path, *args, **kwargs)
        response.headers["Cache-Control"] = PAGE_CACHE_CONTROL
        media_type = PAGE_MEDIA_TYPES.get(Path(full_path).suffix.lower())
        if media_type is not None and isinstance(response, FileResponse):
            response.headers["Content-Type"] = media_type
        return response


MAX_BODY_BYTES = 4 * 1024 * 1024
"""Largest request body: a zones document of hundreds of detailed polygons (``map-zones.md`` 3)."""
DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
ALLOWED_FETCH_SITES: frozenset[str] = frozenset({"same-origin", "none"})
"""``Sec-Fetch-Site`` values accepted under ``/api/``: the page itself, or a URL the user typed."""
JSON_MEDIA_TYPE = "application/json"
BODY_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
"""Methods whose ``Content-Type`` under ``/api/`` must be JSON when there is one (section 3)."""


def _plain_error(
    code: str,
    message: str,
    remedy: str,
    *,
    status: int,
    severity: str = "blocking",
    context: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """An API-only error whose code is not in the registry (``SYS_BUSY``, ``SYS_FORBIDDEN_*``)."""
    body = {
        "schema": 1,
        "code": code,
        "domain": code.split("_", 1)[0],
        "severity": severity,
        "action": "stop",
        "message": message,
        "remedy": remedy,
        "context": dict(context or {}),
        "cause": None,
    }
    return JSONResponse({"error": error_json(body)}, status_code=status)


class _GuardMiddleware(BaseHTTPMiddleware):
    """DNS-rebinding guard (``Host`` allow-list), cross-site guard of the API (``Sec-Fetch-Site``
    and JSON-only bodies) and request-body cap (spec section 3)."""

    def __init__(self, app: Any, *, allowed_hosts: Iterable[str]) -> None:
        super().__init__(app)
        self.allowed = {h.lower() for h in allowed_hosts}

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Any:
        host = request.headers.get("host", "")
        if host.startswith("["):
            name = host.split("]")[0].lower() + "]"
        else:
            name = host.rsplit(":", 1)[0].lower()
        if name not in self.allowed:
            return _plain_error(
                "SYS_FORBIDDEN_HOST",
                f"Request refused: the Host header {host[:64]!r} is not an address OrthoStudio XP "
                "answers to.",
                "Open OrthoStudio XP at the address osxp serve prints (http://127.0.0.1 and its "
                "port).",
                status=400,
            )
        # A browser says where a request comes from: another website (an <img>, a form, a script,
        # another server on localhost) must not make OrthoStudio XP fetch map tiles, start a build
        # or rewrite the zones. A request without Sec-Fetch-Site (curl, a script) is let through.
        site = request.headers.get("sec-fetch-site")
        if (
            site is not None
            and request.url.path.startswith("/api/")
            and site.strip().lower() not in ALLOWED_FETCH_SITES
        ):
            return _plain_error(
                "SYS_FORBIDDEN_ORIGIN",
                f"Request from another website refused (Sec-Fetch-Site: {site[:32]}).",
                "Use OrthoStudio XP from its own page, at the address osxp serve prints; other "
                "websites cannot use its API.",
                status=403,
            )
        # A form on another website can post to OrthoStudio XP without any script, in a browser that
        # sends no Sec-Fetch-Site; it cannot send JSON. The page always does, or sends no body
        # at all.
        media = request.headers.get("content-type")
        if (
            media is not None
            and request.method in BODY_METHODS
            and request.url.path.startswith("/api/")
            and media.split(";", 1)[0].strip().lower() != JSON_MEDIA_TYPE
        ):
            return _plain_error(
                "SYS_BAD_CONTENT_TYPE",
                "Request refused: OrthoStudio XP's API reads JSON only, and this one came as "
                f"{media[:64]!r}.",
                "Use OrthoStudio XP from its own page; a script sends its request body as "
                "application/json.",
                status=415,
            )
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return _plain_error(
                "CFG_VALUE_INVALID",
                f"Request body above {MAX_BODY_BYTES} bytes.",
                "Send fewer tiles or overrides.",
                status=413,
            )
        return await call_next(request)
