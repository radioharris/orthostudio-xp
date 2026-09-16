"""``GET`` and ``PUT /api/zones``: the zones document of the page (spec ``map-zones.md`` 3).

:func:`zones_router` returns an ``APIRouter`` that ``orthostudio.api.app.create_app`` includes::

    app.include_router(zones_router())

``GET`` answers the saved ``osxp-zones-1`` document read zone by zone
(``orthostudio.zones.read_saved_zones``), whatever is wrong in it::

    {"format": "osxp-zones-1", "revision": "<sha256 of the file>", "zones": [...],
     "problems": [{"zone", "index", "code", "reason", "message"}, ...]}

with the header ``ETag: "<revision>"`` (revision ``""`` when there is no file). ``PUT``
validates the whole document, writes it atomically (the previous file is kept as
``zones.json.bak``) and answers it normalised, with the revision of the new file and no
problem. A ``PUT`` carrying ``If-Match`` is written only when the header names the current
revision (quoted or not), else ``409 ZONE_CONFLICT`` and nothing is written: two windows never
overwrite each other's zones. Without ``If-Match`` (the command line, a script) nothing is
compared.

Errors are ``OsxpError`` in the API's one JSON shape (``{"error": error_json(...)}``):
``ZONE_CONFLICT`` answers ``409``, the other ``ZONE_*`` codes ``422`` whatever the application
maps them to; any other code (a failed write) goes to the application's ``OsxpError`` handler.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header
from fastapi.responses import JSONResponse

from orthostudio.api.jobs import error_json
from orthostudio.errors import OsxpError
from orthostudio.imagery.providers import Provider
from orthostudio.zones import (
    default_zones_path,
    parse_zones_document,
    read_saved_zones,
    save_zones,
    zone_conflict,
    zones_file_revision,
)

__all__ = ["ZONE_CONFLICT_STATUS", "ZONE_ERROR_STATUS", "revision_matches", "zones_router"]

ZONE_ERROR_STATUS = 422
"""HTTP status of ``ZONE_*`` errors (spec section 3)."""
ZONE_CONFLICT_STATUS = 409
"""HTTP status of ``ZONE_CONFLICT``: the save was based on another revision of the file."""


def _zone_error(exc: OsxpError) -> JSONResponse:
    status = ZONE_CONFLICT_STATUS if exc.code == "ZONE_CONFLICT" else ZONE_ERROR_STATUS
    return JSONResponse({"error": error_json(exc)}, status_code=status)


def _document(body: dict[str, Any], revision: str) -> JSONResponse:
    """A zones answer: the revision as a strong ``ETag``, never kept by the browser's cache."""
    headers = {"ETag": f'"{revision}"', "Cache-Control": "no-store"}
    return JSONResponse(body, headers=headers)


def revision_matches(if_match: str, revision: str) -> bool:
    """``If-Match`` names ``revision``: one revision or a comma-separated list, each quoted or
    not; ``*`` matches any existing file. An empty value names the revision of no file."""
    for tag in if_match.split(","):
        tag = tag.strip()
        if tag == "*":
            if revision:
                return True
            continue
        if len(tag) >= 2 and tag[0] == tag[-1] == '"':
            tag = tag[1:-1]
        if tag == revision:
            return True
    return False


def zones_router(
    zones_path: Callable[[], Path] = default_zones_path,
    *,
    registry: Callable[[], Mapping[str, Provider]] | None = None,
) -> APIRouter:
    """The router of ``/api/zones``.

    ``zones_path`` is called at every request (default ``$OSXP_HOME/zones.json``, so a test
    changing ``OSXP_HOME`` is followed); ``registry`` gives the providers the zones are checked
    against (default: the embedded registry).
    """
    router = APIRouter()
    # a save compares the revision, keeps the .bak and renames without another save in between
    lock = threading.Lock()

    def providers() -> Mapping[str, Provider] | None:
        return registry() if registry is not None else None

    @router.get("/api/zones")
    async def get_zones() -> Any:
        try:
            saved = await asyncio.to_thread(read_saved_zones, zones_path(), registry=providers())
        except OsxpError as exc:
            if exc.code.startswith("ZONE_"):
                return _zone_error(exc)
            raise
        return _document(saved.to_json(), saved.revision)

    @router.put("/api/zones")
    async def put_zones(
        body: Annotated[Any, Body()],
        if_match: Annotated[str | None, Header()] = None,
    ) -> Any:
        def run() -> tuple[dict[str, Any], str]:
            doc = parse_zones_document(body, registry=providers())
            path = zones_path()
            with lock:
                if if_match is not None and not revision_matches(
                    if_match, zones_file_revision(path)
                ):
                    raise zone_conflict(path)
                save_zones(path, doc)
                revision = zones_file_revision(path)
            zones = doc.model_dump(mode="json")["zones"]
            answer = {"format": doc.format, "revision": revision, "zones": zones, "problems": []}
            return answer, revision

        try:
            answer, revision = await asyncio.to_thread(run)
        except OsxpError as exc:
            if exc.code.startswith("ZONE_"):
                return _zone_error(exc)
            raise
        return _document(answer, revision)

    return router
