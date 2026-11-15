# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the page keeps from one visit to the next, kept by the engine (docs/specs/ui.md 1,
*What the page keeps*).

The boxes and folds of the map, the Library's order, the Experts band, the folder of the last plan
file, the flight plan of step 1, the notes put away: the page kept them in the browser's storage.
In the app's frame the page is an iframe on another port than the frame's, and the web view of
macOS keeps an iframe's storage only while the window is open: every launch since the frame
(2.0.0) started from nothing (measured 2026-11-15: the same page kept its value as the window's own
page, and lost it in an iframe on the next port). The engine keeps them instead, in
``$OSXP_HOME/page.json``, and writes them into the page as it serves it (:func:`page_with_kept`),
so that the page reads them at once, as it read its storage; a change goes back with
``PUT /api/page-kept``. The frame keeps the language, the theme, the text size and the zoom the
same way, for both pages (``orthostudio.live.frame``).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from orthostudio.errors import OsxpError

__all__ = [
    "KEPT_MARK",
    "PAGE_FILE",
    "load_kept",
    "page_kept_path",
    "page_kept_router",
    "page_with_kept",
]

PAGE_FILE = "page.json"
KEPT_MARK = '<script type="application/json" id="page-kept">null</script>'
"""Where ``ui/index.html`` takes what is kept. Left as it is (``null``), the page was served as
plain files, with no engine to keep anything: it then keeps its choices for the visit alone."""

KEY = r"osxp\.[A-Za-z0-9_.-]{1,64}"
MAX_VALUE_CHARS = 256 * 1024  # a long flight plan is a few tens of kB
MAX_KEYS = 100  # the page has 17 today


def page_kept_path(name: str = PAGE_FILE) -> Path:
    """The file of a page's choices: OrthoStudio XP's (``page.json``), or Live's, whose page is
    served by another program (``orthostudio.live.web.LIVE_PAGE_FILE``): two programs never write
    one file."""
    from orthostudio.home import osxp_home

    return osxp_home() / name


def load_kept(path: Path) -> dict[str, str] | None:
    """What ``path`` keeps, the pairs a page could have written; None when nothing was ever kept or
    the file cannot be read, which the page takes as its first visit."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        k: v
        for k, v in data.items()
        if isinstance(k, str) and re.fullmatch(KEY, k) and isinstance(v, str)
    }


def page_with_kept(html: str, kept: dict[str, str] | None) -> bytes:
    """``html`` (``ui/index.html``) with ``kept`` in place of :data:`KEPT_MARK`: ``{"kept": ...}``,
    ``null`` inside when nothing was ever kept. Every ``<`` is written ``\\u003c``, which JSON reads
    back as it was: nothing a value holds can close the script or open a comment."""
    literal = json.dumps({"kept": kept}).replace("<", "\\u003c")
    return html.replace(KEPT_MARK, KEPT_MARK.replace("null", literal), 1).encode("utf-8")


class KeptChange(BaseModel):
    """``PUT /api/page-kept``: the values to keep, ``null`` for one to forget."""

    model_config = ConfigDict(extra="forbid")
    values: dict[
        Annotated[str, StringConstraints(pattern=f"^{KEY}$")],
        Annotated[str, StringConstraints(max_length=MAX_VALUE_CHARS)] | None,
    ] = Field(max_length=MAX_KEYS)


def page_kept_router(path_of: Callable[[], Path] = page_kept_path) -> APIRouter:
    """The router of ``PUT /api/page-kept``. ``path_of`` is called at every request (default
    ``$OSXP_HOME/page.json``, so a test changing ``OSXP_HOME`` is followed)."""
    router = APIRouter()
    lock = threading.Lock()  # one change at a time: each reads the file, changes it, writes it

    def change(values: dict[str, str | None]) -> dict[str, Any]:
        from orthostudio.fsutil import atomic_write_text

        path = path_of()
        with lock:
            kept = load_kept(path) or {}
            for key, value in values.items():
                if value is None:
                    kept.pop(key, None)
                else:
                    kept[key] = value
            if len(kept) > MAX_KEYS:
                raise OsxpError(
                    "CFG_VALUE_INVALID",
                    context={
                        "name": "page-kept",
                        "value": f"{len(kept)} values",
                        "type": "values kept by the page",
                        "range": f"at most {MAX_KEYS}",
                    },
                )
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(path, json.dumps(kept, sort_keys=True, indent=1) + "\n")
            except OSError as exc:
                raise OsxpError(
                    "CFG_TILE_WRITE_FAILED", context={"path": str(path), "reason": str(exc)}
                ) from exc
        return {"kept": len(kept)}

    @router.put("/api/page-kept")
    async def put_page_kept(req: KeptChange) -> Any:
        return await asyncio.to_thread(change, req.values)

    return router
