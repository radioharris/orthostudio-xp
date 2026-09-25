"""The local HTTP API of the page (spec ``docs/specs/api.md``).

``create_app`` returns a FastAPI application bound to nothing yet (``serve.py`` binds
``127.0.0.1``); tests drive it through ``httpx.ASGITransport``. Every error is one JSON
shape: ``{"error": {code, message, remedy, severity, action, ...}}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import time
import tomllib
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from orthostudio import __version__, config, update
from orthostudio.airports import default_index as default_airport_index
from orthostudio.api.basemap import basemap_router
from orthostudio.api.jobs import Job, JobBusyError, JobManager, TileInBuildError, error_json
from orthostudio.api.map_api import TileClient, TileFetch, map_router
from orthostudio.api.models import (
    ChooseFolderRequest,
    CleanRequest,
    DeleteRequest,
    ForgetRequest,
    ImportRequest,
    InstallRequest,
    JobRequest,
    OverlaysRequest,
    PlanRequest,
    QuitRequest,
    RetryRequest,
    RevealRequest,
    SourceRequest,
    SourceTestRequest,
    UninstallRequest,
)
from orthostudio.api.presence import Presence
from orthostudio.api.serve import package_root
from orthostudio.api.simbrief import simbrief_router
from orthostudio.api.specs import (
    check_ortho4xp_folder,
    make_specs,
    patches_dir_of,
    plan_answer,
    resolve_xplane,
)
from orthostudio.api.zones_api import zones_router
from orthostudio.clean import clean, disk_bytes
from orthostudio.codemark import code_mark
from orthostudio.dem.sources import default_elevation_dir
from orthostudio.doctor import run_doctor
from orthostudio.errors import Action, OsxpError, Severity
from orthostudio.fsutil import choose_folder, platform_name, reveal_in_file_manager
from orthostudio.graph import GraphError, Store
from orthostudio.imagery.grid import wgs84_to_tile
from orthostudio.imagery.providers import (
    USER_SOURCE_IN_FLIGHT,
    Provider,
    cache_name,
    is_placeholder,
    load_registry,
    new_source_code,
    read_user_sources,
    save_user_sources,
    tile_url,
)
from orthostudio.install import (
    Library,
    custom_scenery_dir,
    default_library_path,
    install_pack,
    is_link,
    other_xplane_dirs,
    packs_of_their_own,
    xplane_running,
)
from orthostudio.install.library import ortho4xp_searched
from orthostudio.model import TileRef, pack_dir_name
from orthostudio.net.fetch import FetchRequest
from orthostudio.pipeline.build import BuildEnv, patched_tiles
from orthostudio.pipeline.home import (
    check_data_dir,
    data_root,
    data_root_missing,
    default_chunks_root,
    default_mapcache_root,
    default_store_root,
    default_tiles_root,
    osxp_home,
)
from orthostudio.pipeline.pack import (
    LEFT_OVERLAY,
    MANIFEST_NAME,
    PackManifest,
    delete_receipt,
    install_receipt,
    is_installed,
    leave_overlay,
    library_pack,
    links_to,
    overlay_link,
    overlay_states,
    pack_to_delete,
    read_manifest,
    take_back_overlay,
    uninstall_receipt,
)
from orthostudio.zones import default_zones_path, read_saved_zones

__all__ = [
    "ALLOWED_FETCH_SITES",
    "API_LEVEL",
    "DEFAULT_ALLOWED_HOSTS",
    "MAX_BODY_BYTES",
    "create_app",
    "sse_message",
]

API_LEVEL = 25
"""What this engine's API offers, for the page: 1 = P2b, 2 = zones (``/api/zones``) and the base map
(``/api/map``), 3 = deleting a tile (``POST /api/library/{name}/delete``) and the sizes of the
library, 4 = the disk space of the Library (``GET /api/disk``, ``POST /api/clean``), 5 = clearing
the job list (``POST /api/jobs/clear``), 6 = the setting ``essential.overlays`` (an older engine
refuses a settings document that holds it), 7 = ``POST /api/library/import-ortho4xp`` and
``built_by = "ortho4xp"``, 8 = ``POST /api/quit``, ``POST /api/reveal`` and ``platform`` in the
status, 9 = the ``zOrthoStudio_`` pack names (decision 0011), the import's ``folder`` and the
settings schema's ``ortho4xp`` key, 10 = builds that wait in a queue (``queue`` in ``POST
/api/jobs`` and in a retry, ``queue_position`` in their answer) and ``SYS_TILE_IN_BUILD``, 11 = what
each imagery source covers in ``GET /api/providers`` (``extent``, ``extent_bounds``, ``same_as``,
``custom``) and the sources a user adds (``/api/sources``), 12 = the overlays of other packs
(``overlay`` in the library, ``POST /api/library/overlays``) and ``POST /api/choose-folder``, 13 =
the setting ``essential.data_dir`` (an older engine refuses a settings document that holds it),
``data_dir`` in the status and ``CFG_DATA_DIR_*``, 14 = ``GET /api/engine`` (who serves the
port, answered at once) and ``POST /api/presence`` (a page is open), 15 = ``others`` in the
status's ``xplane`` (the other X-Plane 12 folders of the machine), 16 = ``relief`` in a job
(which relief its tiles are built on), 17 = ``GET /api/photo-sample``, 18 = the squares' own
colours in the zones document, 19 = ``POST /api/library/{name}/forget`` and ``GET /api/patches``,
20 = ``GET /api/sizes`` (the sizes of the store and of the downloaded images, no longer in the
status), 21 = the setting ``essential.simbrief_user`` (an older engine refuses a settings document
that holds it), ``GET /api/simbrief`` and ``tiles_zl`` in a plan or a job (the detail level of some
squares alone), 22 = ``DELETE /api/jobs/{id}`` (one finished build leaves the list), 23 =
``GET /api/flightplan/simbrief`` (the plan's line and its squares, computed here) in place of
``GET /api/simbrief``, 24 = ``POST /api/flightplan`` (a route the page kept, its squares computed
again) and ``radius_km`` in a flight plan, 25 = the setting ``expert.decal`` (an older engine
refuses a settings document that holds it). A page
served by an engine older than itself (a ``osxp serve`` started before an update: the page's files
are read from disk at each load, the routes were imported at start) asks the user to restart
OrthoStudio XP instead of showing "Not Found"."""

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
DOCTOR_TTL_S = 60.0
SSE_POLL_MIN_S = 0.05
SSE_POLL_MAX_S = 0.25
SSE_KEEPALIVE_S = 15.0

_STATUS_BY_CODE = {
    "SYS_RESOURCE_MISSING": 503,
    "XP_RUNNING": 409,
    "XP_PACK_CONFLICT": 409,
    "ZONE_CONFLICT": 409,
    "SYS_PACK_NOT_OSXP": 409,
    "SYS_INTERNAL_ERROR": 500,
}


def _http_status(code: str) -> int:
    if code in _STATUS_BY_CODE:
        return _STATUS_BY_CODE[code]
    if code.startswith(("CFG_", "ZONE_")) or code in (
        "XP_DIR_NOT_FOUND",
        "XP_GLOBAL_SCENERY_NOT_FOUND",
        "SYS_WORKING_DIR_INVALID",
    ):
        return 422
    if code.startswith("NET_"):
        return 502
    return 400


def _error_response(err: OsxpError | dict[str, Any], status: int | None = None) -> JSONResponse:
    body = error_json(err)
    return JSONResponse({"error": body}, status_code=status or _http_status(str(body["code"])))


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
    return _error_response(body, status)


def sse_message(entry: dict[str, Any]) -> str:
    """One SSE message: ``id``, ``event``, ``data`` (JSON on one line)."""
    data = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    return f"id: {entry['seq']}\nevent: {entry['event']}\ndata: {data}\n\n"


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


def _dir_bytes(path: Path, *, links: bool = True) -> int:
    """Bytes of the files under ``path`` on the disk (``orthostudio.clean.disk_bytes``); 0
    if unreadable."""
    try:
        return disk_bytes([path], links=links)
    except OSError:
        return 0


def _sizes() -> dict[str, int]:
    """``GET /api/sizes``: the store's bytes on the disk and the downloaded images'. The images are
    never hard-linked, so their sizes come from the folder listings alone (``links=False``): on
    Windows that saves opening each file."""
    return {
        "store_bytes": _store_bytes(default_store_root()),
        "chunks_bytes": _dir_bytes(default_chunks_root(), links=False),
    }


def _store_bytes(root: Path) -> int:
    """The store's size on the disk: each file once, however many artefacts hard-link it.

    The index's sum counted a DDS once per artefact linking it (``texture.dds`` and
    ``tile.textures``): 50.3 GB for a store ``du`` measured at 24 GB. It remains the answer
    when the folder cannot be read. The index is opened only when the folder exists (opening
    creates it).
    """
    if not root.is_dir():
        return 0
    try:
        return disk_bytes([root])
    except OSError:
        pass
    try:
        with Store(root) as st:
            return st.total_size()
    except Exception:
        return 0


def _language(request: Request) -> str:
    header = request.headers.get("accept-language", "")
    first = header.split(",", 1)[0].strip().lower() if header else ""
    return "fr" if first.startswith("fr") else "en"


def _display_zl(kind: str, zl: int | None) -> int | None:
    """Zoom level to show, ``None`` when the row has none (dette D4).

    Only an ``ortho`` pack has a zoom level; an overlays pack carries ``0`` in
    the library, which the page used to render as "ZL 0". ``None`` is rendered as an em dash.
    """
    return zl if kind == "ortho" and zl else None


def _pack_bytes(path: Path) -> int | None:
    """``size_bytes`` of an ortho row whose folder exists: its files on the disk, each inode
    once. A DDS hard-linked from the store counts in full although deleting the pack alone does
    not free it (the store clean after a delete does). ``None`` when the folder cannot be read."""
    try:
        return disk_bytes([path])
    except OSError:
        return None


def _installed(target: Path | None, pack: Path) -> bool:
    """Whether ``target``, the row's name in Custom Scenery, is this row's pack.

    A link counts only when it leads to this pack (or to where it was, once deleted by hand): two
    builds of a tile in two output folders share the name, and only one of them is linked. A real
    folder of that name tells nothing of its origin: it counts for every row of the name, as it
    always did.
    """
    if target is None:
        return False
    if is_link(target):
        return links_to(target, pack)
    return target.exists()


def _overlay_json(state: Any) -> dict[str, Any]:
    return {"state": state.state, "others": list(state.others)}


def _same_pack(state: Any, path: Path) -> bool:
    """Whether the overlay state is that of the pack at ``path`` (the one X-Plane shows)."""
    return state is not None and os.path.realpath(path) == os.path.realpath(state.pack)


def _library_rows(cs: Path | None) -> list[dict[str, Any]]:
    with Library(default_library_path()) as lib:
        rows = lib.list()
    # whose roads, forests and buildings X-Plane draws on the squares of the tiles it shows
    states = overlay_states(cs) if cs is not None and cs.is_dir() else {}
    photos = _pack_photos([r.path for r in rows if r.kind == "ortho" and r.path.is_dir()])
    built = _pack_built([r.path for r in rows if r.kind == "ortho" and r.built_by == "osxp"])
    shared_links: dict[Path, Path] = {}
    out: list[dict[str, Any]] = []
    for r in rows:
        state = states.get(r.tile.name) if r.kind == "ortho" else None
        target = None if cs is None else cs / r.path.name
        if cs is not None and r.kind == "overlay" and r.built_by == "osxp":
            # an overlays pack is in X-Plane under yOrthoStudio_Overlays, or _2... beside another
            if r.path not in shared_links:
                shared_links[r.path] = overlay_link(cs, r.path) or cs / r.path.name
            target = shared_links[r.path]
        installed = _installed(target, r.path)
        present = r.path.is_dir()
        # an overlay pack is shared by the tiles: no size of its own
        size = _pack_bytes(r.path) if present and r.kind == "ortho" else None
        out.append(
            {
                "tile": r.tile.name,
                "kind": r.kind,
                "provider": r.provider,
                "zl": _display_zl(r.kind, r.zl),
                "path": str(r.path),
                "name": r.path.name,
                "built_by": r.built_by,
                "installed": installed,
                "keys": r.keys,
                "registered_at": r.registered_at,
                "updated_at": r.updated_at,
                "size_bytes": size,
                "present": present,
                "photo": photos.get(r.path),
                "built": built.get(r.path),
                "overlay": _overlay_json(state) if _same_pack(state, r.path) else None,
            }
        )
    return out


def _pack_built(pack_dirs: list[Path]) -> dict[Path, dict[str, Any] | None]:
    """What each of these packs was built with, and when: ``{"facts", "at"}``, None when
    nothing can say.

    ``facts`` is the manifest's ``[built]`` (``PackManifest.built``), empty for a pack written
    before 0.1.10, which the page says as such rather than guessing. ``at`` is the time the
    manifest was written, which is when the pack was assembled: the manifest carries no date of
    its own, so that two identical builds stay identical (a user asked where to see what a tile
    was built with, 2026-09-21).
    """
    out: dict[Path, dict[str, Any] | None] = {}
    for pack_dir in pack_dirs:
        out[pack_dir] = None
        manifest_path = pack_dir / MANIFEST_NAME
        try:
            manifest = PackManifest.from_toml(manifest_path.read_text(encoding="utf-8"))
            at = manifest_path.stat().st_mtime
        except (OSError, ValueError, KeyError, TypeError, tomllib.TOMLDecodeError):
            continue
        out[pack_dir] = {"facts": manifest.built, "at": at}
    return out


def _pack_photos(pack_dirs: list[Path]) -> dict[Path, dict[str, float] | None]:
    """The colours each of these packs was built with; ``None`` when nothing can say.

    The manifest holds them since 0.1.18 (it had the table from 2026-09-18, but ``declare`` left
    the colours out of the pack's params until 2026-09-26). Before that it did not, and a pack
    built bright then looked plain to the page, which said nothing while X-Plane showed a bright
    tile (a user, 2026-09-18): the textures artefact the manifest names is asked instead, since
    its recorded params are what the build encoded. The store is opened once, and only if an
    older pack needs it. A build with the plain colours records none of the three, and a pack
    whose artefact has left the store cannot be asked: both answer ``None``, and the page says
    nothing.
    """
    photos: dict[Path, dict[str, float] | None] = {}
    older: dict[Path, str] = {}
    for pack_dir in pack_dirs:
        photos[pack_dir] = None
        try:
            manifest = read_manifest(pack_dir)
        except (OSError, ValueError):
            continue
        if manifest.photo:
            photos[pack_dir] = dict(manifest.photo)
        elif (entry := manifest.artefacts.get("textures")) is not None:
            older[pack_dir] = entry.key
    if older:
        try:
            with Store(default_store_root()) as store:
                for pack_dir, key in older.items():
                    photos[pack_dir] = _artefact_photo(store, key)
        except (OSError, GraphError):
            pass  # no store to ask: the page says nothing rather than guessing "plain"
    return photos


def _artefact_photo(store: Store, key: str) -> dict[str, float] | None:
    """The colours recorded in the params of a textures artefact, or ``None``."""
    try:
        params = store.why(key).params
    except (OSError, GraphError, ValueError):
        return None
    names = ("photo_brightness", "photo_contrast", "photo_saturation")
    if not any(name in params for name in names):
        return None  # built before the colours existed at all
    return {name.removeprefix("photo_"): float(params.get(name) or 0.0) for name in names}


def provider_json(p: Provider) -> dict[str, Any]:
    """A provider as ``GET /api/providers`` gives it: the page groups them by what they cover."""
    doc: dict[str, Any] = {
        "code": p.code,
        "name": p.name or p.attribution or p.code,
        "max_zl": p.max_zl,
        "attribution": p.attribution,
        "terms_url": p.terms_url,
        "licence": p.licence,
        "alive": None,
        "extent": p.extent,
        "extent_bounds": list(p.extent_bounds) if p.extent_bounds is not None else None,
        "same_as": p.same_as,
        "custom": p.custom,
    }
    if p.custom:
        doc["url_template"] = p.url_template  # shown in the list of the sources the user added
        # the folder its images are kept in, which carries its address: the page puts it in the
        # URL of its map tiles, which a browser keeps a day (see map.js tileVersion)
        doc["cache"] = cache_name(p)
    return doc


_TILE_PLACE = (("{x}",), ("{y}", "{-y}", "{|y|}"), ("{zoom}", "{zoom:02d}"))


def check_source_template(template: str) -> Provider:
    """An address the user typed, as a provider (``CFG_VALUE_INVALID`` when it cannot be one):
    http(s), and the tile's place given by ``{x}`` ``{y}`` ``{zoom}`` or by ``{quadkey}``."""
    template = template.strip()

    def refuse(reason: str) -> OsxpError:
        return OsxpError(
            "CFG_VALUE_INVALID",
            context={
                "name": "url_template",
                "value": template[:120],
                "type": "tile address",
                "range": "http(s) with {x} {y} {zoom}, or {quadkey}",
            },
            message=f"The address of the source {reason}.",
            remedy="Give the address of one tile with {x}, {y} and {zoom} (or {quadkey}) in "
            "place of its numbers, for example https://host/tiles/{zoom}/{x}/{y}.jpg.",
        )

    if not template.startswith(("https://", "http://")):
        raise refuse("does not start with https:// or http://")
    if "{quadkey}" not in template and not all(
        any(p in template for p in group) for group in _TILE_PLACE
    ):
        raise refuse("does not say where the tile's numbers go")
    try:
        return Provider(code="Test", url_template=template, max_zl=19, custom=True)
    except ValidationError as exc:
        reason = str(exc.errors()[0].get("msg", "")).removeprefix("Value error, ")
        raise refuse(f"has a placeholder it cannot use ({reason})") from exc


def image_kind(body: bytes) -> str | None:
    """``jpeg``, ``png`` or ``webp`` from the first bytes of an answer, else ``None``."""
    if body.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    return None


IN_BUILD_PACK = "the end of that build decides what X-Plane shows of it: its pack stays as it is."
"""Why a pack OrthoStudio XP built is not added to or taken out of X-Plane while its tile builds."""


def _find_pack(name: str, path: str | None = None) -> tuple[Path, Any]:
    """The library row of ``name`` (a tile ``+43+005`` or ``zOrthoStudio_+43+005``) at ``path``, the
    row the page's button belongs to; without ``path``, the newest (``pack.library_pack``)."""
    entry = library_pack(name, path=path, library_path=default_library_path())
    return entry.path, entry.tile


def create_app(
    *,
    env_factory: Callable[..., Any] | None = BuildEnv.create,
    jobs: JobManager | None = None,
    ui_dir: Path | None = None,
    airports: Any = None,
    settings_path: Path | None = None,
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS,
    map_fetch: Any = None,
    shutdown: Callable[[], None] | None = None,
    reveal: Callable[[Path], None] | None = None,
    source_fetch: TileFetch | None = None,
    folder_dialog: Callable[[str, Path | None], Path | None] | None = None,
) -> FastAPI:
    """The application (spec section 2). ``jobs`` defaults to a manager on ``env_factory``.

    ``map_fetch`` replaces the base map's upstream fetch (``map_api.map_router``); tests pass a stub
    so that no request leaves the machine. ``shutdown`` stops the server (``osxp serve`` gives it;
    without it the page cannot quit OrthoStudio XP); ``reveal`` shows a path in the file manager
    (tests pass a recorder); ``source_fetch`` asks for the tile of a source the user tries
    (tests pass a stub, so that no request leaves the machine); ``folder_dialog`` asks for a folder
    in the platform's own dialog (tests pass a stub: no dialog opens).
    """
    app = FastAPI(title="osxp", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(_GuardMiddleware, allowed_hosts=tuple(allowed_hosts))
    # P5 (docs/specs/map-zones.md): the zones document and the base map. The map router carries
    # its own lifespan, which closes its upstream fetchers when the server stops.
    app.include_router(zones_router())
    app.include_router(map_router(fetch=map_fetch))
    app.include_router(basemap_router())
    manager = jobs if jobs is not None else JobManager(env_factory=env_factory)
    # Held while a tile is deleted: deletes run one at a time, and no build starts meanwhile, since
    # the store clean that ends a delete could take an artefact a new build is about to reuse.
    deleting = asyncio.Lock()

    def busy_deleting() -> JSONResponse:
        return _plain_error(
            "SYS_BUSY",
            "A tile is being deleted, and a build starts only once that is done.",
            "Try again in a moment.",
            status=409,
        )

    def tile_in_build(err: TileInBuildError, why: str) -> JSONResponse:
        tiles = " ".join(err.tiles)
        verb = "is" if len(err.tiles) == 1 else "are"
        return _plain_error(
            "SYS_TILE_IN_BUILD",
            f"{tiles} {verb} in a build already, running or queued "
            f"({' '.join(err.job_ids)}): {why}",
            "Wait for that build to end (see Works), or cancel it, then try again.",
            status=409,
            context={"tiles": err.tiles, "jobs": err.job_ids},
        )

    def refuse_tile_in_build(pack_dir: Path, tile: Any) -> None:
        """A pack OrthoStudio XP built stays as it is while its tile is in a build, running or
        queued: the build's end decides what X-Plane shows of the tile."""
        job_id = manager.building_tiles().get(tile.name)
        if job_id is not None and (pack_dir / MANIFEST_NAME).is_file():
            raise TileInBuildError([tile.name], [job_id])

    # which installation serves, and the code it started with: a second launch of the same one,
    # its files unchanged, shows its page; another installation, or this one started before its
    # files changed, is asked to make way (serve.take_over, desktop.engine_here)
    this_engine = {"root": str(package_root()), "pid": os.getpid(), "code": code_mark()}
    state: dict[str, Any] = {
        "jobs": manager,
        "env_factory": env_factory,
        "airports": airports,
        "airports_tried": airports is not None,
        "settings_path": settings_path,
        "doctor": None,
        "doctor_at": 0.0,
        # the measure of GET /api/sizes under way, shared by the pages that ask meanwhile
        "sizes": None,
        # values of config.toml this version could not read, said by the status
        "settings_problems": [],
        # the first run reads what is installed once, then the file answers (see settings())
        "first_run_done": False,
        "ui_dir": Path(ui_dir) if ui_dir is not None else None,
        # when a page last said it was open: the app stops a while after the last one closed
        "presence": Presence(),
    }
    app.state.orthostudio = state

    # -- errors ------------------------------------------------------------------------------

    @app.exception_handler(OsxpError)
    async def _osxp_error(_request: Request, exc: OsxpError) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return _plain_error(
            "CFG_VALUE_INVALID",
            f"Field {loc or 'body'}: {first.get('msg', 'invalid value')}.",
            "Fix the field and send the request again.",
            status=422,
        )

    # -- helpers -----------------------------------------------------------------------------

    def settings() -> Any:
        path = Path(state["settings_path"] or config.default_config_path())
        if not state["first_run_done"] and not path.is_file():
            state["first_run_done"] = True
            fresh = _settings_of_a_first_run()
            if fresh is not None:
                return fresh
        problems: list[str] = []
        loaded = config.load_settings(state["settings_path"], problems)
        # Values this version cannot read are left at their default rather than refusing the file
        # (a user came back to an older version and every screen stayed empty, 2026-09-20); the
        # page says which ones, from the status.
        state["settings_problems"] = problems
        return loaded

    def _settings_of_a_first_run() -> Any:
        """The defaults adjusted to what is already installed, written once, or ``None``.

        A pack that brings its own roads, forests and buildings over the whole world (simHeaven
        X-World and its family) answers the overlay question: a user of the X-Plane.Org page who
        never opened Settings had everything drawn twice, ours over theirs (2026-09-20). Written
        to the file, not only shown, so that the page, a build and the command line agree; the
        page then shows *None* chosen, with the pack named beside it. Nothing is written when
        nothing was adjusted, and a file that cannot be written is not an error here.
        """
        xp = resolve_xplane(None, "")
        if xp is None or not packs_of_their_own(xp):
            return None
        fresh = config.Settings()
        chosen = fresh.model_copy(
            update={"essential": fresh.essential.model_copy(update={"overlays": "none"})}
        )
        with contextlib.suppress(OsxpError, OSError):
            config.save_settings(chosen, state["settings_path"])
        return chosen

    def xplane_dir(explicit: str | None = None) -> Path | None:
        return resolve_xplane(explicit, settings().essential.xplane_dir)

    def global_scenery() -> Path | None:
        """X-Plane 12's own scenery, which a flight plan asks square by square; ``None`` when no
        X-Plane folder is known, and then no square is left out for want of it."""
        xp = xplane_dir()
        if xp is None:
            return None
        from orthostudio.install.xplane import global_scenery_dir
        from orthostudio.overlays.source import resolve_global_scenery_dir

        return resolve_global_scenery_dir(global_scenery_dir(xp))

    # The Plan's flight plan: the name and the X-Plane folder are read on each request, so a change
    # in Settings counts at once, and nothing is asked of simbrief.com until the button is pressed.
    app.include_router(
        simbrief_router(
            lambda: settings().essential.simbrief_user, global_scenery_of=global_scenery
        )
    )

    def airport_index() -> Any:
        if state["airports"] is None and not state["airports_tried"]:
            state["airports_tried"] = True
            # The tests never build the 383 MB index of a real X-Plane.
            if not os.environ.get("OSXP_API_NO_AIRPORTS"):
                state["airports"] = default_airport_index(xplane_dir())
        return state["airports"]

    def doctor_checks() -> list[dict[str, Any]]:
        now = time.monotonic()
        if state["doctor"] is None or now - state["doctor_at"] > DOCTOR_TTL_S:
            report = run_doctor(offline=True, xplane=xplane_dir())
            state["doctor"] = [c.to_dict() for c in report.checks]
            state["doctor_at"] = now
        return list(state["doctor"])

    def job_or_404(job_id: str) -> Job | JSONResponse:
        job = manager.get(job_id)
        if job is None:
            return _plain_error(
                "SYS_WORKING_DIR_INVALID",
                f"No job {job_id}.",
                "List the jobs with GET /api/jobs.",
                status=404,
            )
        return job

    # -- status, providers -------------------------------------------------------------------

    @app.get("/api/update")
    async def update_check() -> dict[str, Any]:
        """Whether a newer OrthoStudio XP has been published (``orthostudio.update``): GitHub is
        asked at most once a day, and not at all when the setting says no."""
        if not settings().expert.check_updates:
            return {"current": __version__, "latest": None, "url": None, "available": False}
        path = osxp_home() / update.CACHE_NAME
        return await asyncio.to_thread(update.check, __version__, path=path)

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        """What this machine looks like, for the page's screens and its status bar. What takes
        time runs side by side in worker threads, never on the loop: listing the processes there
        held every other request of the page. The sizes of the store and of the downloaded images
        are ``GET /api/sizes``, measured apart: on Windows each file of the store is opened, and
        some users saw the menu alone while the page waited for them (2026-09-22)."""
        xp = await asyncio.to_thread(xplane_dir)

        def library_count() -> int:
            # tiles, not rows: an installed OrthoStudio XP tile has an ortho row and an overlay row
            lib = default_library_path()
            if not lib.is_file():
                return 0
            with Library(lib) as library:
                return len({r.tile for r in library.list(kind="ortho")})

        checks, running, others, own, missing, count = await asyncio.gather(
            asyncio.to_thread(doctor_checks),
            asyncio.to_thread(lambda: xp is not None and xplane_running()),
            asyncio.to_thread(other_xplane_dirs, xp),
            asyncio.to_thread(lambda: [] if xp is None else packs_of_their_own(xp)),
            asyncio.to_thread(data_root_missing),
            asyncio.to_thread(library_count),
        )
        home = osxp_home()
        active = manager.active()
        root = data_root()
        return {
            "version": __version__,
            "api_level": API_LEVEL,
            "xplane": {
                "path": None if xp is None else str(xp),
                "detected": xp is not None,
                "running": running,
                # a user installed a tile into an X-Plane 12 he had forgotten (2026-09-17)
                "others": [str(p) for p in others],
                # packs that bring their own roads, forests and buildings: the Settings question
                # about the overlays answers itself when one of them is there (2026-09-20)
                "packs_of_their_own": own,
            },
            "doctor": checks,
            "settings_problems": list(state["settings_problems"]),
            "home": str(home),
            # the page writes the paths under it with "~": docs/specs/ui.md 1.9
            "user_home": str(Path.home()),
            # where the tiles and the downloads go: an external disk may be unplugged
            "data_dir": {"path": str(root), "chosen": root != home, "present": missing is None},
            "library_count": count,
            "language": _language(request),
            "active_job": None if active is None else active.id,
            "platform": platform_name(),
            "can_quit": shutdown is not None,
            "engine": this_engine,
        }

    @app.get("/api/sizes")
    async def sizes() -> dict[str, Any]:
        """The bytes of the store and of the downloaded images on the disk, for the status bar,
        which shows them when they come: no screen waits for them (``GET /api/status``). Pages
        that ask while a measure runs share it."""
        task = state["sizes"]
        if task is None or task.done():
            task = state["sizes"] = asyncio.ensure_future(asyncio.to_thread(_sizes))
        return await asyncio.shield(task)

    @app.get("/api/engine")
    async def engine() -> dict[str, Any]:
        """Which OrthoStudio XP serves this port, answered at once: a second launch of the app
        recognises the running one with it (``serve.running_osxp``). ``/api/status`` measured the
        store and listed the processes first, which took longer on Windows than the launch waited,
        and the app showed nothing (2026-09-17)."""
        active = manager.active()
        return {
            "version": __version__,
            "api_level": API_LEVEL,
            "can_quit": shutdown is not None,
            "active_job": None if active is None else active.id,
            "engine": this_engine,
        }

    @app.post("/api/presence")
    async def presence() -> dict[str, Any]:
        """A page is open. The app started from its icon stops a while after the last word from a
        page, unless a build runs or waits (``orthostudio.api.presence``)."""
        state["presence"].seen()
        return {"ok": True}

    @app.post("/api/quit")
    async def quit_engine(req: QuitRequest) -> Any:
        """Stop OrthoStudio XP from the page: a running build only when the page confirmed
        it (``force``)."""
        if shutdown is None:
            return _plain_error(
                "SYS_NOT_STOPPABLE",
                "This OrthoStudio XP was not started by osxp serve: the page cannot stop it.",
                "Stop it where it was started.",
                status=409,
            )
        active = manager.active()
        if active is not None and not req.force:
            return _plain_error(
                "SYS_BUSY",
                f"A build is running ({active.id}): quitting OrthoStudio XP stops it.",
                "Wait for the build to finish, or confirm that it may stop.",
                status=409,
            )
        # the queued builds first, so that none starts when the running one stops
        cancelled = manager.cancel_all() if active is not None else []
        active_id = None if active is None else active.id
        # after the answer has left: the page learns that OrthoStudio XP is stopping
        asyncio.get_running_loop().call_later(0.3, shutdown)
        return {
            "stopping": True,
            "cancelled": active_id,
            "queued_cancelled": [job_id for job_id in cancelled if job_id != active_id],
        }

    def reveal_roots() -> list[Path]:
        """What the page may show in the file manager: OrthoStudio XP's folder, its data folder,
        the X-Plane folder and the folders of the library's tiles (an Ortho4XP tile lives
        elsewhere)."""
        roots = [osxp_home(), data_root()]
        xp = xplane_dir()
        if xp is not None:
            roots.append(xp)
        lib = default_library_path()
        if lib.is_file():
            with Library(lib) as library:
                roots.extend(r.path for r in library.list())
        return roots

    @app.post("/api/reveal")
    async def reveal_path(req: RevealRequest) -> Any:
        """Show a folder or a file of OrthoStudio XP, of X-Plane or of a library tile in the
        file manager."""
        target = Path(req.path).expanduser()
        roots = await asyncio.to_thread(reveal_roots)

        def within(root: Path) -> bool:
            try:
                target.resolve().relative_to(root.expanduser().resolve())
            except (OSError, ValueError):
                return False
            return True

        if not target.is_absolute() or not any(within(r) for r in roots):
            return _plain_error(
                "SYS_FORBIDDEN_PATH",
                f"{target} is not a folder of OrthoStudio XP, of X-Plane or of a tile of the "
                "library.",
                "The page only shows the folders OrthoStudio XP works with.",
                status=403,
            )
        if not target.exists():
            return _plain_error(
                "SYS_WORKING_DIR_INVALID",
                f"{target} does not exist (any more).",
                "Refresh the page: the folder may have been moved or deleted.",
                status=404,
            )
        show = reveal if reveal is not None else reveal_in_file_manager
        await asyncio.to_thread(show, target)
        return {"revealed": str(target)}

    dialog_open = asyncio.Lock()

    @app.post("/api/choose-folder")
    async def choose_folder_route(req: ChooseFolderRequest) -> Any:
        """Ask for a folder in the Finder, the File Explorer or the Linux file manager's dialog, on
        the computer OrthoStudio XP runs on (a user asked for a button instead of typing the X-Plane
        folder, 2026-09-15). ``{path}``, ``null`` when the user cancelled."""
        if dialog_open.locked():
            return _plain_error(
                "SYS_BUSY",
                "A folder dialog is already open.",
                "Answer it (it may be behind another window), then try again.",
                status=409,
            )
        start = Path(req.start).expanduser() if req.start else None
        if start is not None and not start.is_dir():
            start = None
        ask = folder_dialog if folder_dialog is not None else choose_folder
        async with dialog_open:
            try:
                chosen = await asyncio.to_thread(ask, req.prompt, start)
            except (OSError, subprocess.SubprocessError) as exc:
                return _plain_error(
                    "SYS_NO_FOLDER_DIALOG",
                    f"No folder dialog could be opened on this computer ({exc}).",
                    "Type the folder's path in the field (on Linux, installing zenity gives the "
                    "button its dialog).",
                    status=501,
                )
        return {"path": None if chosen is None else str(chosen)}

    @app.get("/api/providers")
    async def providers() -> list[dict[str, Any]]:
        # the registry's order (Bing Maps, then Esri), then the sources the user added
        return [provider_json(p) for p in (await asyncio.to_thread(load_registry)).values()]

    # -- the sources a user adds (docs/specs/imagery-providers.md) ---------------------------

    @app.post("/api/sources", status_code=201)
    async def add_source(req: SourceRequest) -> Any:
        """Add an imagery source of the user's to ``$OSXP_HOME/sources.toml``: OrthoStudio XP does
        not ship it, and its terms of use apply to that user."""

        def run() -> dict[str, Any]:
            checked = check_source_template(req.url_template)
            sources, _problems = read_user_sources()
            code = new_source_code(req.name, sources)
            name = req.name.strip()
            source = Provider(
                code=code,
                name=name,
                attribution=name,
                url_template=checked.url_template,
                max_zl=req.max_zl,
                max_in_flight=USER_SOURCE_IN_FLIGHT,
                custom=True,
            )
            save_user_sources({**sources, code: source})
            return provider_json(source)

        return await asyncio.to_thread(run)

    @app.get("/api/photo-sample")
    async def photo_sample(
        provider: str = Query("BI"),
        lat: float = Query(...),
        lon: float = Query(...),
        zl: int = Query(15, ge=10, le=18),
    ) -> Any:
        """One image of a provider where the page is looking, for the colours preview.

        The Settings screen shows what a tile will look like before a build downloads gigabytes
        (a user asked for the colours, 2026-09-18). One tile of the provider's own grid, at a
        modest zoom, cached by the browser; the answer is the image itself.
        """
        registry = await asyncio.to_thread(load_registry)
        source = registry.get(provider)
        if source is None:
            raise OsxpError(
                "CFG_VALUE_INVALID",
                context={"name": "provider", "value": provider, "type": "str", "range": "-"},
                message=f"No imagery source with the code {provider!r}.",
                remedy="Choose one of the sources of the Plan.",
            )
        level = min(zl, source.max_zl)
        x, y = wgs84_to_tile(lat, lon, level)
        url = tile_url(source, int(x), int(y), level)
        request = FetchRequest(key="photo-sample", url=url, host_group="photo-sample")
        if source_fetch is not None:
            result = await source_fetch(request)
        else:
            client = TileClient()
            try:
                result = await client.fetch(request)
            finally:
                await client.aclose()
        kind = image_kind(result.body) if result.status == 200 else None
        # Over open water Bing answers its own "no imagery" tile, 1033 bytes of flat grey with a
        # crossed-out picture on it, rather than a 404. Drawn as it came, the page showed it twice
        # under the words "the ground where the map is looking", and a user read that as a broken
        # preview (2026-09-20). The registry already holds the signals of such a tile, which the
        # build has always used to fall back to the parent level; the page says "no image here".
        empty = is_placeholder(source, result.headers, result.body) if kind else None
        if empty:
            raise OsxpError(
                "IMG_TILE_PLACEHOLDER",
                context={"provider": provider, "chunk": f"{level}/{int(x)}/{int(y)}"},
                message=f"{source.name} has no photo at this place.",
                remedy="Look at another place on the map; the colours still apply to a build.",
            )
        if kind is None:
            # NET_UNAVAILABLE stood here, which is not a code this program has: raising it threw
            # a ValueError out of the route, so a provider that answered nothing gave a traceback
            # rather than the error this line meant to give (2026-09-20). The host, not the whole
            # address, since a source of one's own can carry a key in it.
            raise OsxpError(
                "NET_UNEXPECTED_STATUS",
                context={
                    "provider": provider,
                    "status": str(result.status),
                    "url": url.split("/")[2] if "//" in url else provider,
                },
            )
        return Response(
            content=result.body,
            media_type=f"image/{kind}",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post("/api/sources/test")
    async def try_source(req: SourceTestRequest) -> Any:
        """Ask for one tile through an address before it is added: at zoom 15 (or the source's
        maximum) at ``lat``/``lon``, the place the page is looking at."""
        checked = check_source_template(req.url_template)
        zl = min(15, req.max_zl)
        x, y = wgs84_to_tile(req.lat, req.lon, zl)
        url = tile_url(checked, int(x), int(y), zl)
        request = FetchRequest(key="source-test", url=url, host_group="source-test")
        if source_fetch is not None:
            result = await source_fetch(request)
        else:
            client = TileClient()
            try:
                result = await client.fetch(request)
            finally:
                await client.aclose()
        kind = image_kind(result.body) if result.status == 200 else None
        return {
            "ok": kind is not None,
            "status": result.status,
            "image": kind,
            "bytes": len(result.body),
            "url": url,
            "error": result.error,
        }

    @app.delete("/api/sources/{code}")
    async def remove_source(code: str) -> Any:
        """Remove a source the user added. Refused while a build under way or waiting uses it,
        or a zone does; step 1's saved source goes back to Bing Maps when it was this one."""

        def run() -> dict[str, Any] | JSONResponse:
            sources, _problems = read_user_sources()
            if code not in sources:
                return _plain_error(
                    "CFG_PROVIDER_UNKNOWN",
                    f"{code} is not a source you added.",
                    "Only the sources you added can be removed; list them in the Plan, step 1.",
                    status=404,
                )
            for job in manager.list():
                if job.finished:
                    continue
                used = any(
                    spec.provider == code
                    or any(entry[2] == code for entry in spec.config.get("zone_list") or [])
                    for spec in job.specs
                )
                if used:
                    return _plain_error(
                        "SYS_BUSY",
                        f"The build {job.id} uses the source {code}.",
                        "Wait for that build to end (see Works), or cancel it, then remove the "
                        "source again.",
                        status=409,
                    )
            saved = read_saved_zones(default_zones_path())
            zones = [zone.name or zone.id for zone in saved.zones if zone.provider == code]
            if zones:
                return _plain_error(
                    "SYS_SOURCE_IN_USE",
                    f"The source {code} is the imagery of zone(s) {', '.join(zones)}.",
                    "Give those zones another source (the Plan, step 2), then remove it again.",
                    status=409,
                    context={"zones": zones},
                )
            save_user_sources({k: v for k, v in sources.items() if k != code})
            reset = None
            current = settings()
            if current.essential.provider == code:
                data = current.model_dump(mode="json")
                data["essential"]["provider"] = "BI"
                config.save_settings(config.settings_from_dict(data), state["settings_path"])
                reset = "BI"
            return {"removed": code, "settings_provider": reset}

        return await asyncio.to_thread(run)

    # -- settings ----------------------------------------------------------------------------

    def new_data_dir(value: str | None, xplane: str | None) -> str | None:
        """The data folder to save: ``None`` for OrthoStudio XP's own folder, else a folder
        ``check_data_dir`` accepts. Nothing is moved: what was downloaded before stays where it is
        (the user who asked would delete it, 2026-09-15)."""
        if value is None:
            return None
        xp = resolve_xplane(None, xplane)
        scenery = None if xp is None else custom_scenery_dir(xp)
        folder = check_data_dir(value, custom_scenery=scenery)
        home = osxp_home()
        with contextlib.suppress(OSError):
            home = home.resolve()
        return None if folder == home else str(folder)

    @app.get("/api/settings")
    async def get_settings() -> Any:
        return settings().model_dump(mode="json")

    @app.put("/api/settings")
    async def put_settings(body: dict[str, Any]) -> Any:
        try:
            s = config.settings_from_dict(body)
        except ValueError as exc:
            raise OsxpError(
                "CFG_VALUE_INVALID",
                context={"name": "settings", "value": "-", "type": "-", "range": "-"},
                message=f"Settings rejected: {exc}",
                remedy="See GET /api/settings/schema for the accepted values.",
            ) from None
        previous = settings().essential
        before = previous.xplane_dir
        # checked when it changes: the other settings save while the disk of X-Plane is unplugged
        if s.essential.xplane_dir and s.essential.xplane_dir != before:
            from orthostudio.api.specs import check_xplane_dir

            xp = str(check_xplane_dir(s.essential.xplane_dir))
            essential = s.essential.model_copy(update={"xplane_dir": xp})
            s = s.model_copy(update={"essential": essential})
        data_dir = (s.essential.data_dir or "").strip() or None
        if data_dir != previous.data_dir:
            if manager.active() is not None:
                return _plain_error(
                    "SYS_BUSY",
                    "A build is running or waiting, and the data folder changes between builds.",
                    "Wait for the builds to finish, or cancel them, then save again.",
                    status=409,
                )
            data_dir = await asyncio.to_thread(new_data_dir, data_dir, s.essential.xplane_dir)
        s = s.model_copy(
            update={"essential": s.essential.model_copy(update={"data_dir": data_dir})}
        )
        config.save_settings(s, state["settings_path"])
        state["doctor"] = None
        if s.essential.xplane_dir != before and state["airports"] is None:
            # The airport search failed without X-Plane: it tries again with the folder just chosen.
            state["airports_tried"] = False
        return s.model_dump(mode="json")

    @app.get("/api/settings/schema")
    async def settings_schema() -> Any:
        return config.settings_schema()

    # -- airports ----------------------------------------------------------------------------

    def _no_index() -> JSONResponse:
        return _plain_error(
            "SYS_RESOURCE_MISSING",
            "The airport index is not available on this machine.",
            "Set the X-Plane folder in Settings (apt.dat is needed), or give tiles.",
            status=503,
        )

    @app.get("/api/airports")
    async def airports_search(q: str = "", limit: int = 10) -> Any:
        q = q.strip()[:64]
        limit = max(1, min(50, limit))
        index = await asyncio.to_thread(airport_index)
        if index is None:
            return _no_index()
        if not q:
            return []
        rows = await asyncio.to_thread(index.search, q, limit)
        return [{"icao": a.icao, "name": a.name, "lat": a.lat, "lon": a.lon} for a in rows]

    @app.get("/api/airports/in")
    async def airports_in(
        west: float,
        south: float,
        east: float,
        north: float,
        limit: int = 400,
        icao_only: bool = False,
    ) -> Any:
        """The airports of a rectangle, for the map's layer (a user asked to see whether a square
        holds the airport he wants, 2026-09-19). Read from the index shipped with the app: no
        request leaves the machine, and it works over the aerial imagery."""
        index = await asyncio.to_thread(airport_index)
        if index is None:
            return _no_index()
        try:
            rows = await asyncio.to_thread(
                index.in_bounds,
                west,
                south,
                east,
                north,
                limit=max(1, min(2000, limit)),
                icao_only=icao_only,
            )
        except TypeError:  # an index of an older shape (the tests' own) knows no icao_only
            rows = await asyncio.to_thread(
                index.in_bounds, west, south, east, north, limit=max(1, min(2000, limit))
            )
        return [
            {
                "icao": a.icao,
                "name": a.name,
                "lat": a.lat,
                "lon": a.lon,
                "kind": getattr(a, "kind", "land"),
            }
            for a in rows
        ]

    @app.get("/api/airports/{icao}")
    async def airport_get(icao: str) -> Any:
        index = await asyncio.to_thread(airport_index)
        if index is None:
            return _no_index()
        a = await asyncio.to_thread(index.get, icao.strip().upper()[:7])
        if a is None:
            return _plain_error(
                "CFG_LATLON_INVALID", f"Airport {icao} is unknown.", "Check the ICAO code.",
                status=404,
            )  # fmt: skip
        return {"icao": a.icao, "name": a.name, "lat": a.lat, "lon": a.lon}

    # -- plan, jobs --------------------------------------------------------------------------

    def _specs(req: PlanRequest, *, install: bool) -> list[Any]:
        return make_specs(
            req,
            settings=settings(),
            install=install,
            airports=airport_index() if req.airport is not None else None,
            config_module=config,
        )

    @app.post("/api/plan")
    async def plan(req: PlanRequest) -> Any:
        from orthostudio.estimate import estimate

        def run() -> dict[str, Any]:
            specs = _specs(req, install=False)
            factory = state["env_factory"]
            env = None
            if factory is not None:
                from orthostudio.api.jobs import _call_env_factory

                env = _call_env_factory(factory, specs)
            est = estimate(specs, online=req.online, env=env)
            return plan_answer(est, specs)

        return await asyncio.to_thread(run)

    @app.post("/api/jobs", status_code=201)
    async def post_job(req: JobRequest) -> Any:
        specs = await asyncio.to_thread(_specs, req, install=req.install)
        if deleting.locked():
            return busy_deleting()
        try:
            job = manager.start(
                specs,
                install=req.install,
                request=req.model_dump(mode="json", exclude={"queue"}),
                queue=req.queue,
            )
        except JobBusyError as busy:
            return _plain_error(
                "SYS_BUSY",
                f"A build is already running ({busy}).",
                "Wait for it to finish, or cancel it.",
                status=409,
            )
        except TileInBuildError as err:
            return tile_in_build(err, "a tile is in one build at a time.")
        position = manager.queue_position(job.id)
        return {"job_id": job.id, "status": job.status, "queue_position": position}

    @app.get("/api/jobs")
    async def list_jobs() -> list[dict[str, Any]]:
        return [j.summary() for j in manager.list()]

    @app.post("/api/jobs/clear")
    async def clear_jobs() -> dict[str, Any]:
        return {"removed": await asyncio.to_thread(manager.forget_finished)}

    @app.delete("/api/jobs/{job_id}")
    async def forget_job(job_id: str) -> Any:
        """Remove one finished build from the list, its progress and its journal with it. The
        tiles it built stay, in the Library and in X-Plane. A build running or waiting is
        refused: cancel it first."""
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        if not await asyncio.to_thread(manager.forget, job_id):
            return _plain_error(
                "SYS_BUSY",
                f"Job {job_id} is {job.status}.",
                "Only a build that has finished can leave the list; cancel it first.",
                status=409,
            )
        return {"job_id": job_id, "removed": True}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> Any:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        return job.state()

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> Any:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        if not manager.cancel(job_id):
            return _plain_error(
                "SYS_BUSY", f"Job {job_id} is already {job.status}.", "Nothing to cancel.",
                status=409,
            )  # fmt: skip
        return {"job_id": job.id, "status": job.status, "cancel_requested": True}

    @app.post("/api/jobs/{job_id}/retry", status_code=201)
    async def retry_job(job_id: str, req: RetryRequest | None = None) -> Any:
        req = req or RetryRequest()
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        if deleting.locked():
            return busy_deleting()
        try:
            new = manager.retry(job_id, queue=req.queue)
        except JobBusyError as busy:
            return _plain_error(
                "SYS_BUSY",
                f"A build is already running ({busy}).",
                "Wait for it to finish, or cancel it.",
                status=409,
            )
        except TileInBuildError as err:
            return tile_in_build(err, "a tile is in one build at a time.")
        return {
            "job_id": new.id,
            "status": new.status,
            "retry_of": job.id,
            "queue_position": manager.queue_position(new.id),
        }

    async def _stream(job: Job, after: int) -> AsyncIterator[str]:
        yield "retry: 2000\n\n"
        seq = after
        last = time.monotonic()
        delay = SSE_POLL_MIN_S
        while True:
            finished = job.finished
            events = job.events(seq)
            if events:
                for e in events:
                    seq = e["seq"]
                    yield sse_message(e)
                last = time.monotonic()
                delay = SSE_POLL_MIN_S
                if events[-1]["event"] == "finished":
                    return
                continue
            if finished:
                return
            if time.monotonic() - last >= SSE_KEEPALIVE_S:
                yield ": keepalive\n\n"
                last = time.monotonic()
            await asyncio.sleep(delay)
            delay = min(SSE_POLL_MAX_S, delay * 1.5)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, after: int = 0) -> Any:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        header = request.headers.get("last-event-id")
        if header is not None and header.strip().isdigit():
            after = int(header.strip())
        return StreamingResponse(
            _stream(job, max(0, after)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- library -----------------------------------------------------------------------------

    @app.get("/api/library")
    async def library(xplane_dir_q: str | None = Query(default=None, alias="xplane_dir")) -> Any:
        """The library. ``installed`` is read against ``xplane_dir``, else the settings, else
        the detected X-Plane, so a page pointed at another install reports the truth."""
        xp = await asyncio.to_thread(xplane_dir, xplane_dir_q)
        cs = custom_scenery_dir(xp) if xp is not None else None
        return await asyncio.to_thread(_library_rows, cs)

    @app.get("/api/patches")
    async def patches(
        typed: str | None = Query(default=None, alias="dir", max_length=4096),
    ) -> Any:
        """The tiles the folder of hand-made mesh patches has something for, and what a build of
        each reads (``pipeline.build.patched_tiles``): for the Plan and Settings to name them
        before anything is built (a user took "Patches: none" in a report for his patch not being
        found, 2026-09-21). ``dir`` is the folder Settings shows before it is saved."""

        def run() -> dict[str, Any]:
            folder = patches_dir_of(settings(), typed)
            return {
                "dir": None if folder is None else str(folder),
                "exists": folder is not None and folder.is_dir(),
                "tiles": patched_tiles(folder),
            }

        return await asyncio.to_thread(run)

    @app.post("/api/library/import-ortho4xp")
    async def import_ortho4xp(req: ImportRequest) -> Any:
        def run() -> dict[str, Any]:
            # in the thread, not on the loop: it walks the folder, and a user's tiles live on
            # another disk that may be asleep or on the network (found in review, 2026-09-23)
            folder = check_ortho4xp_folder(req.folder)
            with Library(default_library_path()) as lib:
                rows = lib.import_ortho4xp(folder)
            # where it looked as well as what it found: finding nothing then says where not
            return {
                "entries": [
                    {
                        "tile": r.tile.name,
                        "kind": r.kind,
                        "provider": r.provider,
                        "zl": _display_zl(r.kind, r.zl),
                        "path": str(r.path),
                        "name": r.path.name,
                        "built_by": r.built_by,
                    }
                    for r in rows
                ],
                "searched": [str(p) for p in ortho4xp_searched(folder)],
            }

        return await asyncio.to_thread(run)

    @app.post("/api/library/{name}/install")
    async def library_install(name: str, req: InstallRequest | None = None) -> Any:
        req = req or InstallRequest()

        def run() -> dict[str, Any]:
            xp = xplane_dir(req.xplane_dir)
            if xp is None:
                raise OsxpError(
                    "XP_DIR_NOT_FOUND",
                    context={"path": "<not detected>"},
                    remedy="Choose the X-Plane 12 folder in Settings.",
                )
            pack_dir, tile = _find_pack(name, req.path)
            refuse_tile_in_build(pack_dir, tile)
            cs = custom_scenery_dir(xp)
            if (pack_dir / MANIFEST_NAME).is_file():
                return install_receipt(
                    pack_dir, cs, tile=tile, link=req.link, library_path=default_library_path()
                )
            target = install_pack(pack_dir, cs, link=req.link)
            return {"pack": str(pack_dir), "target": str(target), "custom_scenery": str(cs)}

        try:
            return await asyncio.to_thread(run)
        except TileInBuildError as err:
            return tile_in_build(err, IN_BUILD_PACK)

    @app.post("/api/library/{name}/uninstall")
    async def library_uninstall(name: str, req: UninstallRequest | None = None) -> Any:
        req = req or UninstallRequest()

        def run() -> dict[str, Any]:
            xp = xplane_dir(req.xplane_dir)
            if xp is None:
                raise OsxpError(
                    "XP_DIR_NOT_FOUND",
                    context={"path": "<not detected>"},
                    remedy="Choose the X-Plane 12 folder in Settings.",
                )
            pack_dir, tile = _find_pack(name, req.path)
            refuse_tile_in_build(pack_dir, tile)
            cs = custom_scenery_dir(xp)
            target = cs / pack_dir.name
            if (
                req.path is not None
                and os.path.lexists(target)
                and not is_installed(pack_dir, cs, library_path=default_library_path())
            ):
                # The row clicked is not what X-Plane shows under its name: two builds of a tile
                # in two output folders share the name, and only one of them is in Custom Scenery.
                if is_link(target):
                    what = f"its link leads to {os.path.realpath(target)}"
                else:
                    what = f"{target} is another folder"
                raise OsxpError(
                    "XP_PACK_CONFLICT",
                    context={"tile": pack_dir.name, "pack": str(target), "reason": "other pack"},
                    message=f"X-Plane shows another {pack_dir.name} than {pack_dir}: {what}. "
                    "Nothing was changed.",
                    remedy="If it belongs to another row of the library, use that row's button; "
                    "otherwise take it out of Custom Scenery by hand.",
                    severity=Severity.BLOCKING,
                    action=Action.STOP,
                )
            # the tile's overlay DSF goes with it (uninstall_receipt), else X-Plane keeps it
            return uninstall_receipt(pack_dir.name, cs)

        try:
            return await asyncio.to_thread(run)
        except TileInBuildError as err:
            return tile_in_build(err, IN_BUILD_PACK)

    @app.post("/api/library/overlays")
    async def library_overlays(req: OverlaysRequest) -> Any:
        """Leave the roads, forests and buildings of squares to the other packs' overlays
        (AutoOrtho's, XPME's, Ortho4XP's), or draw the tiles' own again (``install.md`` 4.3).
        Refused while X-Plane runs, and for a tile in a build under way or waiting."""

        def run() -> dict[str, Any]:
            xp = xplane_dir(req.xplane_dir)
            if xp is None:
                raise OsxpError(
                    "XP_DIR_NOT_FOUND",
                    context={"path": "<not detected>"},
                    remedy="Choose the X-Plane 12 folder in Settings.",
                )
            cs = custom_scenery_dir(xp)
            building = manager.building_tiles()
            busy = sorted({name for name in req.tiles if name in building})
            if busy:
                raise TileInBuildError(busy, sorted({building[name] for name in busy}))
            changed: list[str] = []
            for name in dict.fromkeys(req.tiles):
                tile = TileRef.parse(name)
                link = cs / pack_dir_name(tile)
                if not is_link(link):
                    continue
                pack_dir = Path(os.path.realpath(link))
                if not (pack_dir / MANIFEST_NAME).is_file():
                    continue
                if req.use == "others":
                    if leave_overlay(pack_dir, tile):
                        changed.append(name)
                elif (pack_dir / LEFT_OVERLAY).is_file():
                    take_back_overlay(pack_dir, cs, tile=tile, library_path=default_library_path())
                    changed.append(name)
            states = overlay_states(cs)
            return {
                "changed": changed,
                "states": {n: _overlay_json(s) for n, s in states.items() if n in req.tiles},
            }

        try:
            return await asyncio.to_thread(run)
        except TileInBuildError as err:
            return tile_in_build(err, IN_BUILD_PACK)

    @app.post("/api/library/{name}/forget")
    async def library_forget(name: str, req: ForgetRequest | None = None) -> Any:
        """Take a tile imported from Ortho4XP off the list, and touch nothing on the disk: the way
        back from an import (a user asked, 2026-09-21). Importing the folder again lists it again.

        Refused for a tile OrthoStudio XP built, whose way out is Delete; and while X-Plane shows
        the tile, whose link the Library would otherwise no longer know to take out.
        """
        req = req or ForgetRequest()

        def look() -> tuple[Any, bool]:
            entry = library_pack(name, path=req.path, library_path=default_library_path())
            xp = xplane_dir(req.xplane_dir)
            cs = custom_scenery_dir(xp) if xp is not None else None
            shown = cs is not None and is_installed(
                entry.path, cs, library_path=default_library_path()
            )
            return entry, shown

        entry, shown = await asyncio.to_thread(look)
        if entry.built_by == "osxp":
            return _plain_error(
                "SYS_PACK_NOT_IMPORTED",
                f"{entry.tile.name} was built by OrthoStudio XP: Delete takes it away, and the "
                "list with it.",
                "Use Delete for a tile OrthoStudio XP built.",
                status=409,
                context={"tile": entry.tile.name, "path": str(entry.path)},
            )
        if shown:
            return _plain_error(
                "SYS_PACK_IN_XPLANE",
                f"{entry.tile.name} is in X-Plane: taken off the list now, the Library could no "
                "longer take it out.",
                "Remove it from X-Plane first, then remove it from the list.",
                status=409,
                context={"tile": entry.tile.name, "path": str(entry.path)},
            )

        def forget() -> dict[str, Any]:
            with Library(default_library_path()) as lib:
                n = lib.forget(entry.tile, kind="ortho", path=entry.path)
                # the overlay row the import wrote goes with the tile's last imported pack: a tile
                # imported from two Ortho4XP folders keeps it for the other
                ortho = lib.list(tile=entry.tile, kind="ortho")
                if not any(r.built_by == "ortho4xp" for r in ortho):
                    for row in lib.list(tile=entry.tile, kind="overlay"):
                        if row.built_by == "ortho4xp":
                            n += lib.forget(entry.tile, kind="overlay", path=row.path)
            return {"tile": entry.tile.name, "forgotten": n, "path": str(entry.path)}

        return await asyncio.to_thread(forget)

    @app.post("/api/library/{name}/delete")
    async def library_delete(name: str, req: DeleteRequest | None = None) -> Any:
        """Delete a tile OrthoStudio XP built, for good (``delete_receipt``): out of X-Plane when it
        is installed there, its folder, its library rows, and the store space no pack needs."""
        req = req or DeleteRequest()

        def run() -> dict[str, Any]:
            xp = xplane_dir(req.xplane_dir)
            entry = pack_to_delete(name, path=req.path, library_path=default_library_path())
            return delete_receipt(
                entry.path,
                tile=entry.tile,
                custom_scenery=None if xp is None else custom_scenery_dir(xp),
                library_path=default_library_path(),
            )

        async with deleting:  # a second delete waits for this one
            if manager.active() is not None:
                # the store clean that follows could take an artefact the build is about to reuse
                return _plain_error(
                    "SYS_BUSY",
                    "A build is running, and OrthoStudio XP deletes tiles only between builds.",
                    "Wait for the build to finish, or cancel it, then delete the tile again.",
                    status=409,
                )
            return await asyncio.to_thread(run)

    # -- disk space (the Library's "Free space") ---------------------------------------------

    def other_builds() -> set[int]:
        """Processes other than this one building into the store now (a CLI build)."""
        root = default_store_root()
        if not root.is_dir():
            return set()
        with Store(root) as st:
            return st.building_pids()

    def collect(*, images: bool, relief: bool, dry_run: bool) -> Any:
        # No grace period: the callers check first that nothing is building (osxp clean --all).
        return clean(
            default_store_root(),
            default_chunks_root(),
            library_path=default_library_path(),
            tiles_root=default_tiles_root(),
            images=images,
            dry_run=dry_run,
            grace_s=0.0,
            mapcache_root=default_mapcache_root(),
            elevation_root=default_elevation_dir(),
            relief=relief,
        )

    def busy_building(other: bool) -> JSONResponse:
        where = "in a terminal" if other else "in OrthoStudio XP"
        return _plain_error(
            "SYS_BUSY",
            f"A build is running {where}, and OrthoStudio XP frees space only between builds.",
            "Wait for the build to finish, or stop it, then try again.",
            status=409,
        )

    @app.get("/api/disk")
    async def disk() -> Any:
        """What the Library's "Free space" would give back, measured without deleting anything:
        the tile data no tile on disk needs (``unused_bytes``, whatever its age), the downloaded
        image pieces, the map background, and the relief downloaded and kept."""

        def run() -> dict[str, Any]:
            report = collect(images=True, relief=True, dry_run=True)
            return {
                "store_bytes": _store_bytes(default_store_root()),
                "unused_bytes": report.freed_bytes,
                "images_bytes": report.images_bytes - report.mapcache_bytes,
                "mapcache_bytes": report.mapcache_bytes,
                "relief_bytes": report.relief_bytes,
                "tiles": len(report.packs),
                "building": manager.active() is not None or bool(other_builds()),
            }

        return await asyncio.to_thread(run)

    @app.post("/api/clean")
    async def free_space(req: CleanRequest | None = None) -> Any:
        """Free the space: every piece of tile data no tile on disk needs, with ``images`` the
        downloaded image pieces and the map background, and with ``relief`` the elevation cells.
        Refused while a build runs, here or in another process, since nothing protects what a
        build is about to use otherwise."""
        req = req or CleanRequest()
        async with deleting:  # no build starts meanwhile, and deletes wait
            if manager.active() is not None:
                return busy_building(other=False)
            if await asyncio.to_thread(other_builds):
                return busy_building(other=True)
            report = await asyncio.to_thread(
                collect, images=req.images, relief=req.relief, dry_run=False
            )
            return {
                "format": "osxp-clean-1",
                "freed_bytes": report.freed_bytes,
                "images_freed_bytes": report.images_bytes if req.images else 0,
                "relief_freed_bytes": report.relief_bytes if req.relief else 0,
                "removed": report.removed,
            }

    # -- the page ----------------------------------------------------------------------------

    ui = state["ui_dir"]
    if ui is not None and ui.is_dir():
        app.mount("/static", _RevalidatedStaticFiles(directory=str(ui)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> Any:
        if ui is None or not (ui / "index.html").is_file():
            return _plain_error(
                "SYS_RESOURCE_MISSING",
                "The page (src/orthostudio/ui) is not installed.",
                "Use the API (/api/status) or reinstall OrthoStudio XP.",
                status=503,
            )
        return FileResponse(
            ui / "index.html", media_type="text/html", headers={"Cache-Control": PAGE_CACHE_CONTROL}
        )

    return app
