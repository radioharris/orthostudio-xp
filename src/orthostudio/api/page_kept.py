# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a page keeps from one visit to the next, kept by the program that serves it
(docs/specs/ui.md 1, *What the page keeps*).

OrthoStudio XP's page keeps its choices in ``$OSXP_HOME/page.json``, Live's in ``live-page.json``:
Live's page is served by another program, and two programs never write one file. The file is
written into the page as it is served (:func:`kept_page`) and changed with ``PUT /api/page-kept``
(:func:`page_kept_router`), which answers in the same words on both servers.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from orthostudio.api.guard import PAGE_CACHE_CONTROL
from orthostudio.api.stages import error_json
from orthostudio.errors import OsxpError

__all__ = ["KEPT_MARK", "MAX_KEYS", "MAX_VALUE_CHARS", "PAGE_FILE", "kept_page", "page_kept_router"]

PAGE_FILE = "page.json"
KEPT_MARK = '<script type="application/json" id="page-kept">null</script>'
"""Where a page takes what is kept. Left as it is (``null``), the page was served as plain files,
with nothing to keep its choices: it keeps them for the visit alone (``ui/kept.js``)."""

KEY = r"osxp\.[A-Za-z0-9_.-]{1,64}"
MAX_VALUE_CHARS = 256 * 1024  # a long flight plan is a few tens of kB
MAX_KEYS = 100  # the two pages have 18 today


def _path(name: str) -> Path:
    from orthostudio.home import osxp_home

    return osxp_home() / name


def _load(path: Path) -> dict[str, str]:
    """What ``path`` keeps, the pairs a page could have written: nothing when there is no file or
    it cannot be read (the next change writes it whole again)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: v
        for k, v in data.items()
        if isinstance(k, str) and re.fullmatch(KEY, k) and isinstance(v, str)
    }


def kept_page(page_file: Path, name: str = PAGE_FILE) -> Response:
    """``page_file`` with what ``name`` keeps written in place of :data:`KEPT_MARK`; never
    cached."""
    from orthostudio.ui import with_json

    html = with_json(page_file.read_text(encoding="utf-8"), KEPT_MARK, {"kept": _load(_path(name))})
    return Response(html, media_type="text/html", headers={"Cache-Control": PAGE_CACHE_CONTROL})


class KeptChange(BaseModel):
    """``PUT /api/page-kept``: the values to keep, ``null`` for one to forget."""

    model_config = ConfigDict(extra="forbid")
    values: dict[
        Annotated[str, StringConstraints(pattern=f"^{KEY}$")],
        Annotated[str, StringConstraints(max_length=MAX_VALUE_CHARS)] | None,
    ]


def _refused(exc: OsxpError, status: int) -> JSONResponse:
    return JSONResponse({"error": error_json(exc)}, status_code=status)


def _invalid(value: str) -> OsxpError:
    return OsxpError(
        "CFG_VALUE_INVALID",
        context={
            "name": "page-kept",
            "value": value,
            "type": "a page's choices",
            "range": f"names osxp.*, texts of {MAX_VALUE_CHARS // 1024} kB, {MAX_KEYS} at most",
        },
    )


def page_kept_router(name: str = PAGE_FILE) -> APIRouter:
    """The router of ``PUT /api/page-kept``, keeping in ``$OSXP_HOME/<name>`` (found at every
    request, so a test changing ``OSXP_HOME`` is followed)."""
    router = APIRouter()
    lock = threading.Lock()  # one change at a time: each reads the file, changes it, writes it

    def change(values: dict[str, str | None]) -> dict[str, Any]:
        from orthostudio.fsutil import atomic_write_text

        path = _path(name)
        with lock:
            kept = _load(path)
            for key, value in values.items():
                if value is None:
                    kept.pop(key, None)
                else:
                    kept[key] = value
            if len(kept) > MAX_KEYS:
                raise _invalid(f"{len(kept)} values")
            try:
                atomic_write_text(path, json.dumps(kept, sort_keys=True, indent=1) + "\n")
            except OSError as exc:
                raise OsxpError(
                    "SYS_WRITE_FAILED", context={"path": str(path), "reason": str(exc)}
                ) from exc
        return {"kept": len(kept)}

    @router.put("/api/page-kept")
    async def put_page_kept(body: Annotated[Any, Body()] = None) -> Any:
        # validated and refused here: Live's server has none of the engine's error handlers
        try:
            req = KeptChange.model_validate(body)
        except ValidationError as exc:
            return _refused(_invalid(str(exc.errors()[0].get("msg", "invalid"))[:200]), 422)
        try:
            return await asyncio.to_thread(change, req.values)
        except OsxpError as exc:
            return _refused(exc, 422 if exc.code == "CFG_VALUE_INVALID" else 500)

    return router
