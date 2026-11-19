# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a page keeps from one visit to the next, kept by the program that serves it.

OrthoStudio XP's page keeps its choices in ``$OSXP_HOME/page.json`` (:data:`PAGE_FILE`), Live's in
``live-page.json`` (:data:`LIVE_PAGE_FILE`): Live's page is served by another program, and two
programs never write one file. The file is written into the page as it is served
(:func:`kept_page`) and changed with ``PUT /api/page-kept`` (:func:`page_kept_router`), which
answers in the same words on both servers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from orthostudio.api.guard import PAGE_CACHE_CONTROL
from orthostudio.api.stages import error_json
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_text, open_read
from orthostudio.home import osxp_home
from orthostudio.ui import with_json

__all__ = [
    "KEPT_MARK",
    "KEY",
    "LIVE_PAGE_FILE",
    "MAX_VALUE_CHARS",
    "PAGE_FILE",
    "kept_page",
    "page_kept_router",
]

log = logging.getLogger("orthostudio.api")

PAGE_FILE = "page.json"
LIVE_PAGE_FILE = "live-page.json"
KEPT_MARK = '<script type="application/json" id="page-kept">null</script>'
"""Where a page takes what is kept. Left as it is (``null``), the page was served as plain files,
with nothing to keep its choices: it keeps them for the visit alone (``ui/kept.js``)."""

KEY = r"osxp\.[A-Za-z0-9_.-]{1,64}"
MAX_VALUE_CHARS = 256 * 1024  # a long flight plan is a few tens of thousands


def _keepable(key: object, value: object) -> bool:
    """The one rule of what a page keeps: a name ``osxp.*`` and a text of at most
    :data:`MAX_VALUE_CHARS` characters."""
    return (
        isinstance(key, str)
        and re.fullmatch(KEY, key) is not None
        and isinstance(value, str)
        and len(value) <= MAX_VALUE_CHARS
    )


def _load(path: Path) -> dict[str, str]:
    """What ``path`` keeps: nothing when there is no file or it is not a page's. A file that cannot
    be read raises ``OSError``: a change then writes nothing, and nothing kept is lost."""
    try:
        with open_read(path) as f:
            data = json.loads(f.read())
    except (FileNotFoundError, ValueError):  # no file; not JSON, or not UTF-8
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if _keepable(k, v)}


def kept_page(page_file: Path, name: str = PAGE_FILE) -> Response:
    """``page_file`` with what ``name`` keeps written in place of :data:`KEPT_MARK`; never
    cached. A file that cannot be read gives this visit the defaults, and says so in the log."""
    path = osxp_home() / name
    try:
        kept = _load(path)
    except OSError as exc:
        log.warning("the page's choices cannot be read (%s): the defaults are shown", exc)
        kept = {}
    html = with_json(page_file.read_text(encoding="utf-8"), KEPT_MARK, kept)
    return Response(html, media_type="text/html", headers={"Cache-Control": PAGE_CACHE_CONTROL})


def _invalid(what: str) -> OsxpError:
    return OsxpError(
        "CFG_VALUE_INVALID",
        context={
            "name": "page-kept",
            "value": what,
            "type": "a page's choices",
            "range": f"names osxp.*, texts of {MAX_VALUE_CHARS} characters at most",
        },
    )


def _changes(body: bytes) -> dict[str, str | None]:
    """The values a ``PUT`` asks to keep (``null``: to forget), each one a page could keep."""
    try:
        doc = json.loads(body)
    except ValueError:
        raise _invalid("a body that is not JSON") from None
    values = doc.get("values") if isinstance(doc, dict) and set(doc) == {"values"} else None
    if not isinstance(values, dict):
        raise _invalid("a body without its values")
    for key, value in values.items():
        if not _keepable(key, "" if value is None else value):
            raise _invalid(str(key)[:80])
    return values


def page_kept_router(name: str = PAGE_FILE) -> APIRouter:
    """The router of ``PUT /api/page-kept``, keeping in ``$OSXP_HOME/<name>`` (found at every
    request, so a test changing ``OSXP_HOME`` is followed)."""
    router = APIRouter()
    lock = threading.Lock()  # one change at a time: each reads the file, changes it, writes it

    def change(values: dict[str, str | None]) -> None:
        path = osxp_home() / name
        with lock:
            try:
                kept = _load(path)
                for key, value in values.items():
                    if value is None:
                        kept.pop(key, None)
                    else:
                        kept[key] = value
                atomic_write_text(path, json.dumps(kept, sort_keys=True, indent=1) + "\n")
            except OSError as exc:
                log.warning("the page's choices cannot be kept (%s)", exc)
                raise OsxpError(
                    "SYS_WRITE_FAILED", context={"path": str(path), "reason": str(exc)}
                ) from exc

    @router.put("/api/page-kept")
    async def put_page_kept(request: Request) -> Any:
        # the body read and refused here, not by FastAPI: Live's server has none of the engine's
        # error handlers, and both answer in the same words (the engine's statuses)
        try:
            await asyncio.to_thread(change, _changes(await request.body()))
        except OsxpError as exc:
            status = 422 if exc.code.startswith("CFG_") else 400
            return JSONResponse({"error": error_json(exc)}, status_code=status)
        return {}

    return router
