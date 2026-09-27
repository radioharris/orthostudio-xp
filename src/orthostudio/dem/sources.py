# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Elevation sources: file names, download, unzip, and the negative memo of missing URLs.

Spec: ``docs/specs/dem.md`` sections 2 and 3.
Origin: Ortho4XP ``src/O4_DEM_Utils.py:593-866`` (``ensure_elevation``, ``http_request``)
and ``src/O4_File_Names.py:24-56, 290-328`` (the naming).

The names are kept verbatim from Ortho4XP's ``Elevation_data/``. The retry ladder is not: downloads
go through :mod:`orthostudio.net.fetch`, and a 404 is remembered (:class:`NegativeMemo`) instead
of being re-issued on every run.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import io
import json
import math
import os
import re
import threading
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar

from orthostudio.errors import OsxpError

__all__ = [
    "ALPHABET",
    "COP30_BASE_URL",
    "DEM1_BLOCKS",
    "DEM1_CELLS",
    "FULL_HGT_SIZE",
    "MANUAL_SOURCES",
    "SOURCES",
    "TIFF_MAGIC",
    "CellState",
    "Download",
    "EnsureResult",
    "NegativeMemo",
    "Source",
    "base_file_name",
    "cell_file_in_folder",
    "cop30_name",
    "cop30_url",
    "default_elevation_dir",
    "default_memo_path",
    "elevation_path",
    "ensure_elevation",
    "extract_view_zip",
    "generic_tif",
    "hem_latlon",
    "round_latlon",
    "view_url",
]

Source = Literal["View", "SRTM", "ALOS", "NED1", "NED1/3", "COP30", "HRDEM", "ANADEM"]

SOURCES: tuple[Source, ...] = ("View", "SRTM", "ALOS", "NED1", "NED1/3", "COP30", "HRDEM", "ANADEM")
"""The five elevation sources of Ortho4XP (``O4_DEM_Utils.py:20-31``, short names), plus the two
OrthoStudio XP added for users who asked: ``COP30``, the Copernicus DEM GLO-30 (2026-09-17), and
``HRDEM``, Canada's lidar mosaic (2026-09-18, ``dem/hrdem.py``)."""

GLOBAL_SOURCES: tuple[Source, ...] = ("View", "SRTM", "ALOS", "COP30")
"""Sources assembled from the 3x3 block of neighbouring cells (``O4_DEM_Utils.py:33``)."""

MANUAL_SOURCES: tuple[Source, ...] = ("SRTM", "ALOS")
"""Sources whose cells a user places by hand: OpenTopography stopped serving them directly
(``O4_DEM_Utils.py:735-745``). The others are downloaded, so a missing cell is a cell the source
does not cover, not a file to fetch by hand (a user asked what a European tile does with the
USGS, 2026-09-18)."""

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

FULL_HGT_SIZE = 25934402
"""Size a ``.hgt`` must reach to count as 1" (``O4_DEM_Utils.py:672``); 3601^2 * 2 + 2."""

VIEW_BASE_URL = "http://viewfinderpanoramas.org"
COP30_BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
"""Copernicus DEM GLO-30, 1 arc-second, on the public store of the Open Data programme: one
GeoTIFF of 3600x3600 posts per cell, 20 to 40 MB, no account (checked 2026-09-17)."""
NED_BASE_URL = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation"
ANADEM_BASE_URL = "https://metadados.snirh.gov.br/files/anadem_v1_tiles"
"""ANADEM v1.0, the terrain model of South America: the vegetation bias taken out of Copernicus
GLO-30 by the Federal University of Rio Grande do Sul and Brazil's water agency (Laipelt et al.,
2024, https://hge-iph.github.io/anadem/). Served by the agency as one tiled GeoTIFF of about 2 GB
per MGRS zone; the square of one tile is 37 MB of it, read by byte ranges (``dem/cog.py``)."""

# fmt: off
ANADEM_ZONES: frozenset[str] = frozenset({
    "17L", "17M", "17N", "18F", "18G", "18H", "18K", "18L", "18M", "18N", "18P",
    "19F", "19G", "19H", "19J", "19K", "19L", "19M", "19N", "19P",
    "20F", "20G", "20H", "20J", "20K", "20L", "20M", "20N", "20P",
    "21F", "21H", "21J", "21K", "21L", "21M", "21N", "21P",
    "22H", "22J", "22K", "22L", "22M", "22N",
    "23J", "23K", "23L", "23M", "24K", "24L", "24M", "25L", "25M",
})
# fmt: on
"""The 52 zones published. A square outside them is simply not theirs: the relief laid under
ANADEM answers, as it does for Canada's lidar."""

_MGRS_BANDS = "CDEFGHJKLMNPQRSTUVWX"


def anadem_zone(lat: int, lon: int) -> str | None:
    """The MGRS zone of a square (``21L``), or ``None`` outside what ANADEM covers.

    A one-degree square never straddles a zone: their sides are multiples of six degrees of
    longitude and eight of latitude, and a square sits on whole degrees.
    """
    if not -80 <= lat < 80:
        return None
    zone = int((lon + 180) // 6) + 1
    band = _MGRS_BANDS[int((lat + 80) // 8)]
    name = f"{zone:02d}{band}"
    return name if name in ANADEM_ZONES else None


def anadem_url(zone: str) -> str:
    """Where the zone's GeoTIFF lives."""
    return f"{ANADEM_BASE_URL}/anadem_v1_{zone}.tif"


DEM1_CELLS: frozenset[tuple[int, int]] = frozenset(
    {
        (44, 5), (45, 5), (46, 5),
        (43, 6), (44, 6), (45, 6), (46, 6), (47, 6),
        (43, 7), (44, 7), (45, 7), (46, 7), (47, 7),
        (45, 8), (46, 8), (47, 8),
        (45, 9), (46, 9), (47, 9),
        (45, 10), (46, 10), (47, 10),
        (45, 11), (46, 11), (47, 11),
        (45, 12), (46, 12), (47, 12),
        (46, 13), (47, 13),
        (46, 14), (47, 14),
        (46, 15), (47, 15),
    }
)  # fmt: skip
"""The 34 cells served one-per-zip under ``dem1/`` (``O4_DEM_Utils.py:599-635``)."""

DEM1_BLOCKS: frozenset[str] = frozenset(
    {
        "O31", "P31",
        "N32", "O32", "P32", "Q32",
        "N33", "O33", "P33", "Q33", "R33",
        "O34", "P34", "Q34", "R34",
        "O35", "P35", "Q35", "R35",
        "P36", "Q36", "R36",
    }
)  # fmt: skip
"""De Ferranti blocks that are 1" although served under ``dem3/`` (``:645-667``)."""

_MEMBER_RE = re.compile(r"^([NSns])(\d{2})([EWew])(\d{3})")


class CellState(StrEnum):
    """Why a 1-degree cell of the 3x3 block holds what it holds."""

    LOCAL = "local"
    """The file was already on disk and was reused."""
    DOWNLOADED = "downloaded"
    """The file was fetched during this run."""
    OCEAN = "ocean"
    """``world_tiles.png`` says the cell is open sea; no file is expected."""
    MISSING = "missing"
    """No local file and no download; the cell degrades to zeros."""


# -- naming (O4_File_Names.py) --------------------------------------------------------------


def hem_latlon(lat: int, lon: int) -> str:
    """``N43E005`` (``O4_File_Names.py:44-56``)."""
    hemisphere = "N" if lat >= 0 else "S"
    side = "E" if lon >= 0 else "W"
    return f"{hemisphere}{abs(lat):02d}{side}{abs(lon):03d}"


def round_latlon(lat: int, lon: int) -> str:
    """``+40+000``: the 10-degree cell, floored towards minus infinity (``:30-35``)."""
    return f"{math.floor(lat / 10) * 10:+03.0f}{math.floor(lon / 10) * 10:+04.0f}"


def base_file_name(elevation_dir: Path, lat: int, lon: int) -> Path:
    """``<elevation_dir>/+40+000/N43E005`` without extension (``:290-293``)."""
    return elevation_dir / round_latlon(lat, lon) / hem_latlon(lat, lon)


_SUFFIX: dict[str, str] = {
    "View": ".hgt",
    "COP30": "_COP30.tif",
    "SRTM": "_SRTMv3.hgt",
    "ALOS": "_ALOS3W30.tif",
    "NED1/3": "_NED13.tif",
    "NED1": "_NED1.tif",
    "HRDEM": "_HRDEM.hgt",
    "ANADEM": "_ANADEM.tif",
}


def elevation_path(source: str, elevation_dir: Path, lat: int, lon: int) -> Path:
    """Local file of one cell for one source (``O4_File_Names.py:299-310``)."""
    try:
        suffix = _SUFFIX[source]
    except KeyError:
        raise ValueError(f"unknown elevation source {source!r}") from None
    base = base_file_name(elevation_dir, lat, lon)
    return base.with_name(base.name + suffix)


def generic_tif(elevation_dir: Path, lat: int, lon: int) -> Path:
    """``<base>.tif``: the implicit source when ``custom_dem`` is empty (``:313-314``)."""
    base = base_file_name(elevation_dir, lat, lon)
    return base.with_name(base.name + ".tif")


OWN_SUFFIXES: tuple[str, ...] = (".hgt", ".tif", ".tiff", ".raw")
"""What a file of one's own may be, in the order they are tried. ``.hgt`` first: that is how the
sets of the community come (Sonny's lidar models of Europe, viewfinderpanoramas)."""


def _points_in(path: Path) -> int:
    """How many points a candidate file holds, to tell a finer set from a coarser one.

    Cheap for the raw formats, whose side is the square root of their size; a GeoTIFF is asked for
    its header, which costs a few milliseconds and no pixel.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if path.suffix.lower() in (".hgt", ".raw"):
        return size // 2  # int16 a point
    try:
        from PIL import Image

        guard = Image.MAX_IMAGE_PIXELS
        try:
            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as im:
                return int(im.size[0]) * int(im.size[1])
        finally:
            Image.MAX_IMAGE_PIXELS = guard
    except Exception:
        return size // 4  # unreadable header: float32 a point, near enough to compare


def cell_file_in_folder(folder: Path, lat: int, lon: int) -> Path | None:
    """The file of cell ``(lat, lon)`` somewhere under ``folder``, or ``None``.

    A user has his own elevation files by the hundred, one per one-degree cell, named after that
    cell as the SRTM format does (``N47E011.hgt``), and often sorted into a directory per country
    (a user of the X-Plane.Org page, 2026-09-19). He names the folder once, and each tile takes the
    file of its own square from it, whatever the depth it sits at and whatever its resolution: the
    reader works the side out from the size of the file.

    **The finest wins.** The same sets come at 3", 1" and 0.5", under the same names, one folder
    each, and a user who has all three keeps all three (same user, same day): the candidate with
    the most points is taken, so his 0.5" file is used where he has one and his 3" file elsewhere.
    Between two of the same size the order of ``OWN_SUFFIXES`` decides, and between two files
    alike, the shortest path, so the answer never depends on the order the disk hands them over.
    """
    cell = hem_latlon(lat, lon).lower()
    try:
        found = [
            path
            for path in folder.rglob("*")
            if path.stem.lower() == cell and path.suffix.lower() in OWN_SUFFIXES and path.is_file()
        ]
    except OSError:  # an unplugged disk, a folder that went away: no file, no crash
        return None
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    return min(
        found,
        key=lambda path: (
            -_points_in(path),
            OWN_SUFFIXES.index(path.suffix.lower()),
            str(path),
        ),
    )


def default_elevation_dir() -> Path:
    """``$OSXP_ELEVATION_DIR`` or ``<data folder>/elevation``."""
    env = os.environ.get("OSXP_ELEVATION_DIR")
    if env:
        return Path(env).expanduser()
    from orthostudio.home import data_root

    return data_root() / "elevation"


def default_memo_path() -> Path:
    """``<data folder>/dem/misses.json`` (spec section 3.4)."""
    from orthostudio.home import data_root

    return data_root() / "dem" / "misses.json"


# -- URLs -----------------------------------------------------------------------------------


def view_url(lat: int, lon: int) -> tuple[str, int]:
    """``(url, resolution)`` of the viewfinderpanoramas archive holding this cell.

    ``O4_DEM_Utils.py:595-668``. The resolution is 1 or 3 and only drives the recycling test
    of :func:`ensure_elevation`, never the URL.
    """
    if (lat, lon) in DEM1_CELLS:
        return f"{VIEW_BASE_URL}/dem1/{hem_latlon(lat, lon).lower()}.zip", 1
    number = 31 + lon // 6
    letter = ALPHABET[lat // 4] if lat >= 0 else "S" + ALPHABET[(-1 - lat) // 4]
    block = f"{letter}{number:02d}"
    resol = 1 if block in DEM1_BLOCKS else 3
    return f"{VIEW_BASE_URL}/dem3/{block}.zip", resol


def ned_url(source: str, lat: int, lon: int) -> str:
    """USGS NED tile URL (``O4_DEM_Utils.py:809-824``).

    ``fix``: Ortho4XP builds ``tid`` as ``tid + "w" if lon < 0 else "e"``, which drops the
    latitude part for eastern longitudes. OrthoStudio XP builds the documented ``n44w072`` form.
    """
    nbr = "1" if source == "NED1" else "13"
    tid = ("n" if lat >= 0 else "s") + f"{abs(lat + 1):02d}"
    tid += ("w" if lon < 0 else "e") + f"{abs(lon):03d}"
    return f"{NED_BASE_URL}/{nbr}/TIFF/current/{tid}/USGS_{nbr}_{tid}.tif"


# -- negative memo --------------------------------------------------------------------------


class NegativeMemo:
    """Remembers URLs a server said do not exist, so they are not requested every run.

    Spec section 3.4. Ortho4XP re-issues three ``dem1`` 404s on every build of ``+43+005``; the
    memo is the fix. It records *final* refusals only (404 and friends), never a timeout or a
    5xx. It never changes an output: a memoised URL degrades its cell exactly like a fresh
    404 would.
    """

    FORMAT = "osxp-dem-misses-1"
    DEFAULT_TTL_S = 30 * 86400.0

    __slots__ = ("_dirty", "misses", "path", "ttl_s")

    def __init__(self, path: Path | None = None, *, ttl_s: float | None = None) -> None:
        self.path = path
        self.ttl_s = self.DEFAULT_TTL_S if ttl_s is None else float(ttl_s)
        self.misses: dict[str, float] = {}
        self._dirty = False
        if path is not None and path.is_file():
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            doc = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict) or doc.get("format") != self.FORMAT:
            return
        misses = doc.get("misses")
        if isinstance(misses, dict):
            self.misses = {str(k): float(v) for k, v in misses.items() if _is_number(v)}

    def is_missing(self, url: str, *, now: float | None = None) -> bool:
        """True when ``url`` was refused less than ``ttl_s`` ago."""
        seen = self.misses.get(url)
        if seen is None:
            return False
        return (time.time() if now is None else now) - seen < self.ttl_s

    def record(self, url: str, *, now: float | None = None) -> None:
        """Remember that ``url`` does not exist."""
        self.misses[url] = time.time() if now is None else now
        self._dirty = True

    def forget(self, url: str) -> None:
        """Drop one URL from the memo (the escape hatch for a server that came back)."""
        if self.misses.pop(url, None) is not None:
            self._dirty = True

    def save(self) -> None:
        """Write the memo back, atomically. A memo without a path is in-memory only."""
        if self.path is None or not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}")
        doc = {"format": self.FORMAT, "misses": self.misses}
        tmp.write_text(json.dumps(doc, sort_keys=True, indent=1), "utf-8")
        os.replace(tmp, self.path)
        self._dirty = False


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


# -- downloading ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Download:
    """One fetched body, or the reason there is none."""

    url: str
    body: bytes = b""
    status: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300 and bool(self.body)

    @property
    def final(self) -> bool:
        """True when the server gave a definitive answer that is not a success (memoisable)."""
        return self.error is None and not self.ok and self.status != 0


DownloadFn = Callable[[str], Download]
"""How :func:`ensure_elevation` reaches the network; injected so tests never do."""


RELIEF_SILENCE_S = 30.0
"""Seconds a relief file may go without a byte arriving before its transfer is given up.

It was 30 s for the whole file, through the imagery's fetcher, which also started a second copy of
it after 3 s: a USGS square of 443 MB needed some 215 Mbit/s to the USGS to arrive in time, and
every attempt that did not was thrown away and started again from nothing. A user's +32-111 lost
one (2026-09-26); downloading beside the images, a test's +34-118 lost all four and its tile
failed (2026-09-27). A file still arriving, however slowly, is waited for now; a silent server is
not."""
RELIEF_CONNECT_S = 10.0
"""Seconds to open a relief's connection, as the fetcher gave any transfer."""
RELIEF_ATTEMPTS = 4
"""Transfers of one file before its failure is final, as the fetcher counted them (a 429 obeyed
within its budget is not one)."""
_RELIEF_BACKOFF_S = (0.5, 1.0, 2.0)
"""Waits before the second, third and fourth transfers: the fetcher's, without its jitter."""
_RELIEF_ERRORS = {
    "server": "NET_SERVER_ERROR",
    "pushback": "NET_RATE_LIMITED",
    "timeout": "NET_TIMEOUT",
    "connect": "NET_CONNECTION_FAILED",
}
"""The codes the fetcher gave the same failures, which the page's words and the memo read."""

_T = TypeVar("_T")


class _KeptConnections:
    """One HTTP/1.1 session for the reliefs, on an event loop of its own, kept for the engine.

    The machinery of the map data library (``sources.library._Connections``), which streams its
    files on Windows, macOS and Linux since 0.1.18, in a session of its own: a relief of 400 MB
    never shares a connection with the library's small files.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: Any = None

    def run(self, work: Callable[[Any], Awaitable[_T]]) -> _T:
        """``work(session)``, run on the kept loop, from whichever thread asks."""
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                self._session = None
                threading.Thread(target=self._loop.run_forever, name=self.name, daemon=True).start()
            loop = self._loop
        return asyncio.run_coroutine_threadsafe(self._with_session(work), loop).result()

    async def _with_session(self, work: Callable[[Any], Awaitable[_T]]) -> _T:
        if self._session is None:  # made on its loop: nothing awaits between test and assignment
            from curl_cffi.requests import AsyncSession

            from orthostudio.net.certs import ca_bundle

            # HTTP/1.1 pinned, as the library's: left to negotiate, or pinned to HTTP/2, a session
            # stalled on large files there (``sources.library``, 2026-09-23)
            self._session = AsyncSession(verify=ca_bundle(), http_version="v1", max_clients=4)
        return await work(self._session)

    def close(self) -> None:
        """The session and its loop, closed: at exit, and in the tests."""
        with self._lock:
            loop, session = self._loop, self._session
            self._loop, self._session = None, None
        if loop is None or loop.is_closed():
            return
        if session is not None:
            with contextlib.suppress(Exception):  # best effort: the process is going anyway
                asyncio.run_coroutine_threadsafe(session.close(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)


_RELIEF_HTTP = _KeptConnections("osxp-relief-http")
atexit.register(_RELIEF_HTTP.close)


def _cancelled() -> OsxpError:
    return OsxpError("SYS_CANCELLED", context={"stage": "dem"})


def _pause(seconds: float, cancel: threading.Event | None) -> None:
    """Wait ``seconds``, or raise ``SYS_CANCELLED`` within 0.2 s of a cancel."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        if cancel is not None and cancel.is_set():
            raise _cancelled()
        time.sleep(min(0.2, left))


def _stop(answer: Any) -> None:
    """Tell curl to give up a streamed answer at its next piece (curl_cffi's ``quit_now``)."""
    quit_now = getattr(answer, "quit_now", None)
    if quit_now is not None:
        quit_now.set()


async def _relief_transfer(
    session: Any, url: str, silence_s: float, cancel: threading.Event | None
) -> tuple[str, int, bytes, float | None]:
    """One transfer: ``(kind, status, body, retry_after)``. ``kind`` is ``ok`` for any answer the
    fetcher counted as one (a 404 included), ``server`` for a 5xx, ``pushback`` for a 429,
    ``timeout``, ``connect`` or ``cancelled``; ``retry_after`` is a 429's ``Retry-After``.

    A cancel returns at once, the transfer winding down on the kept loop: closing a streamed
    answer waits for its transfer to end, 400 MB away, so curl is told to stop at its next piece
    (:func:`_stop`). Before the headers there is no answer to tell: the transfer is left to reach
    them and stops there, for cancelling it then would leave curl filling its queue with the whole
    file, read by nobody (curl_cffi 0.16).
    """
    from curl_cffi.requests.exceptions import Timeout

    from orthostudio.net.fetch import MAX_REDIRECTS, USER_AGENT, _retry_after

    held: list[Any] = []  # the answer being read, once its headers are in

    async def read() -> tuple[str, int, bytes, float | None]:
        async with session.stream(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT},
            # streamed, curl_cffi turns this pair into a limit on silence (no byte for their sum),
            # not on the whole transfer (``sources.library._http_get_many``)
            timeout=(RELIEF_CONNECT_S, max(1.0, silence_s - RELIEF_CONNECT_S)),
            allow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        ) as answer:
            if cancel is not None and cancel.is_set():  # cancelled while the headers came
                _stop(answer)
                return "cancelled", 0, b"", None
            held.append(answer)
            status = int(answer.status_code)
            if status == 429 or status >= 500:  # a refusal's body is not the file: not kept
                headers = {str(k).lower(): str(v) for k, v in answer.headers.items()}
                retry_after = _retry_after(headers) if status == 429 else None
                return ("pushback" if status == 429 else "server"), status, b"", retry_after
            pieces: list[bytes] = []
            async for piece in answer.aiter_content():
                pieces.append(piece)
            return "ok", status, b"".join(pieces), None

    task = asyncio.ensure_future(read())
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.2)
            if done:
                return task.result()
            if cancel is not None and cancel.is_set():
                if held:
                    _stop(held[0])
                    task.cancel()
                task.add_done_callback(lambda t: t.cancelled() or t.exception())
                return "cancelled", 0, b"", None
    except Exception as exc:  # a transport failure is an answer here, never a crash
        timed_out = isinstance(exc, Timeout) or getattr(exc, "code", None) == 28
        return ("timeout" if timed_out else "connect"), 0, b"", None


def http_download(
    url: str,
    *,
    timeout_s: float = RELIEF_SILENCE_S,
    max_attempts: int = RELIEF_ATTEMPTS,
    cancel: threading.Event | None = None,
) -> Download:
    """One relief file (spec section 3.5), read as it arrives on a connection kept from one call
    to the next, and given up only after ``timeout_s`` without a byte.

    Not through the imagery's fetcher: it is built for thousands of small pieces, cut each
    transfer at ``timeout_s`` whole and doubled any transfer still running after 3 s, which a file
    of 400 MB always is (:data:`RELIEF_SILENCE_S`). The answers and their rules are the
    fetcher's: a 404 and the like are the server's word, memoised by the caller
    (:attr:`Download.final`); a 5xx, a silence or a refused connection are asked again, up to
    ``max_attempts`` transfers, then carry the fetcher's code; a 429's pause is obeyed, and costs
    no attempt while the pauses stay within their budget (``net-download.md`` R2). ``cancel``
    stops the transfer within 0.2 s: ``SYS_CANCELLED`` is raised.
    """
    from orthostudio.net import fetch

    last = Download(url, error=_RELIEF_ERRORS["connect"])
    attempts = max(1, max_attempts)
    failures = pushbacks = 0
    pushback_wait, next_pause = 0.0, fetch.PAUSE_BASE_S
    while failures < attempts:
        if cancel is not None and cancel.is_set():
            raise _cancelled()
        kind, status, body, retry_after = _RELIEF_HTTP.run(
            lambda session: _relief_transfer(session, url, timeout_s, cancel)
        )
        if kind == "cancelled":
            raise _cancelled()
        if kind == "ok":
            return Download(url, body=body, status=status)
        last = Download(url, status=status, error=_RELIEF_ERRORS[kind])
        if kind == "pushback":
            if retry_after is not None:
                wait = min(max(retry_after, fetch.RETRY_AFTER_MIN_S), fetch.RETRY_AFTER_MAX_S)
            else:
                wait, next_pause = next_pause, min(next_pause * 2, fetch.PAUSE_MAX_S)
            if pushbacks < fetch.MAX_PUSHBACKS and pushback_wait + wait <= fetch.PUSHBACK_BUDGET_S:
                pushbacks += 1
                pushback_wait += wait
            else:
                failures += 1
        else:
            failures += 1
            wait = _RELIEF_BACKOFF_S[min(failures - 1, len(_RELIEF_BACKOFF_S) - 1)]
        if failures < attempts:
            _pause(wait, cancel)
    return last


def _host_group(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0]


def no_download(url: str) -> Download:
    """A :data:`DownloadFn` that refuses every request (offline builds and tests)."""
    return Download(url, error="NET_CONNECTION_FAILED")


RangesFn = Callable[[str, Sequence[tuple[int, int]]], list[bytes]]
"""How :func:`ensure_elevation` reads parts of a file too big to fetch whole (ANADEM); injected
like :data:`DownloadFn`, so tests never reach a service. A part that could not be read comes back
empty rather than missing, so the caller sees which one."""


def http_ranges(
    url: str, parts: Sequence[tuple[int, int]], *, timeout_s: float = 120.0, max_attempts: int = 3
) -> list[bytes]:
    """``(offset, length)`` pairs of one file, eight in flight, in the order asked."""
    from orthostudio.net.fetch import FetchRequest, fetch_all

    if not parts:
        return []
    results = fetch_all(
        [
            FetchRequest(
                key=i,
                url=url,
                headers={"Range": f"bytes={at}-{at + size - 1}"},
                host_group=_host_group(url),
            )
            for i, (at, size) in enumerate(parts)
        ],
        timeout_s=timeout_s,
        max_attempts=max_attempts,
        max_in_flight=8,
        start_in_flight=4,
    )
    return [r.body if r.status in (200, 206) and r.body else b"" for r in results]


def no_ranges(url: str, parts: Sequence[tuple[int, int]]) -> list[bytes]:
    """A :data:`RangesFn` that reads nothing (offline builds and tests)."""
    return []


# -- ensure ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnsureResult:
    """Outcome of :func:`ensure_elevation` for one cell."""

    lat: int
    lon: int
    state: CellState
    path: Path | None = None
    url: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state in (CellState.LOCAL, CellState.DOWNLOADED)

    @property
    def cell(self) -> str:
        return hem_latlon(self.lat, self.lon)


@dataclass(slots=True)
class EnsureOptions:
    """Everything :func:`ensure_elevation` needs beyond the cell it is asked about."""

    elevation_dir: Path
    """Where the cells are read, and where the ones this run downloads are written."""
    download: DownloadFn = no_download
    ranges: RangesFn = no_ranges
    """Reads parts of a file served whole (ANADEM's zones of 2 GB); ``no_ranges`` reads none."""
    memo: NegativeMemo = field(default_factory=NegativeMemo)
    dem1_local_fallback: bool = False
    """TODO (spec 9.1, blocker B2): reuse a local 3" file when the 1" zip is unavailable.
    Off by default because it changes ``Data<tile>.alt`` and therefore the mesh."""
    cancel: threading.Event | None = None
    """The scheduler's cancellation token (``DemJob.cancel``). Polled before every download
    and between the nine cells of a block; raises ``SYS_CANCELLED`` (review 4, finding C3)."""
    xp12_dsfs: Mapping[tuple[int, int], Path] = field(default_factory=dict)
    """``(lat, lon) -> Global Scenery DSF`` for ``custom_dem = "XP12"``. The rule fills it from
    its inputs, whose digests are in its key; a cell it does not name has no DSF."""
    global_scenery_dir: Path | None = None
    """Where to find a DSF ``xp12_dsfs`` does not name. Only for direct calls outside the
    store (a notebook, the CLI): the rule leaves it ``None`` so that it reads nothing unkeyed."""

    def xp12_dsf(self, lat: int, lon: int) -> Path | None:
        """The Global Scenery DSF of cell ``(lat, lon)``, or ``None`` when there is none."""
        path = self.xp12_dsfs.get((lat, lon))
        if path is None and self.global_scenery_dir is not None and -90 <= lat < 90:
            from orthostudio.dsf.xp12 import global_scenery_dsf
            from orthostudio.model import TileRef

            candidate = global_scenery_dsf(self.global_scenery_dir, TileRef(lat, lon))
            path = candidate if candidate.is_file() else None
        return path

    def check_cancelled(self) -> None:
        """Raise ``SYS_CANCELLED`` when the build was cancelled; called between cells."""
        if self.cancel is not None and self.cancel.is_set():
            raise OsxpError("SYS_CANCELLED", context={"stage": "dem"})


def _atomic_write_bytes(path: Path, body: bytes) -> None:
    """Write ``body`` to ``path`` through a temporary file in the same directory.

    A download killed half way would otherwise leave a stump that every later run accepts
    (review 4, finding A1). Same protocol as :class:`NegativeMemo`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.part-{os.getpid()}")
    try:
        tmp.write_bytes(body)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


TIFF_MAGIC = (b"II*\x00", b"MM\x00*")
"""Byte order marks of a TIFF: what a complete NED cell starts with (``:809-824``)."""


def _is_tiff(path: Path) -> bool:
    """True when the local NED cell at least *starts* like a TIFF.

    A stump left by an interrupted download (a file written before the writes became atomic, or
    copied from Ortho4XP, which does not write atomically) used to be accepted for ever, because
    only ``is_file()`` was tested.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(4) in TIFF_MAGIC
    except OSError:
        return False


def ensure_elevation(source: str, lat: int, lon: int, opts: EnsureOptions) -> EnsureResult:
    """Make the elevation file of one cell available, downloading it if needed.

    ``O4_DEM_Utils.py:593-828``, with the negative memo of section 3.4 on top. Never raises
    for a cell that simply is not available: the caller degrades it to zeros, as Ortho4XP does.
    """
    if source == "View":
        return _ensure_view(lat, lon, opts)
    if source == "COP30":
        return _ensure_cop30(lat, lon, opts)
    if source in MANUAL_SOURCES:
        return _ensure_manual(source, lat, lon, opts)
    if source in ("NED1", "NED1/3"):
        return _ensure_ned(source, lat, lon, opts)
    if source == "HRDEM":
        return _ensure_hrdem(lat, lon, opts)
    if source == "ANADEM":
        return _ensure_anadem(lat, lon, opts)
    raise ValueError(f"unknown elevation source {source!r}")


def _local(source: str, lat: int, lon: int, opts: EnsureOptions) -> Path:
    return elevation_path(source, opts.elevation_dir, lat, lon)


def _ensure_view(lat: int, lon: int, opts: EnsureOptions) -> EnsureResult:
    path = _local("View", lat, lon, opts)
    url, resol = view_url(lat, lon)
    if path.is_file() and (resol == 3 or path.stat().st_size >= FULL_HGT_SIZE):
        return EnsureResult(lat, lon, CellState.LOCAL, path, url)
    if opts.memo.is_missing(url):
        return _view_fallback(lat, lon, path, url, opts, "the archive is in the negative memo")
    opts.check_cancelled()
    got = opts.download(url)
    if got.ok:
        extract_view_zip(got.body, opts.elevation_dir)
        if path.is_file():
            return EnsureResult(lat, lon, CellState.DOWNLOADED, path, url)
        return _view_fallback(lat, lon, path, url, opts, "the archive did not hold this cell")
    if got.final:
        opts.memo.record(url)
    return _view_fallback(lat, lon, path, url, opts, got.error or f"HTTP {got.status}")


def cop30_name(lat: int, lon: int) -> str:
    """``Copernicus_DSM_COG_10_N46_00_E006_00_DEM``: the name of a GLO-30 cell."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def cop30_url(lat: int, lon: int) -> str:
    """Where the GLO-30 cell is downloaded from; a cell all at sea has no file (404)."""
    name = cop30_name(lat, lon)
    return f"{COP30_BASE_URL}/{name}/{name}.tif"


def _ensure_cop30(lat: int, lon: int, opts: EnsureOptions) -> EnsureResult:
    """One GLO-30 cell, downloaded once into the elevation folder.

    Cells with no land have no file: the 404 is remembered like any other, and the caller
    degrades the cell to 0 m, as it does for a missing viewfinderpanoramas cell.
    """
    path = _local("COP30", lat, lon, opts)
    url = cop30_url(lat, lon)
    if path.is_file() and path.stat().st_size > 0:
        return EnsureResult(lat, lon, CellState.LOCAL, path, url)
    if opts.memo.is_missing(url):
        return EnsureResult(lat, lon, CellState.MISSING, None, url, "in the negative memo")
    opts.check_cancelled()
    got = opts.download(url)
    if got.ok:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(got.body)
        tmp.replace(path)
        return EnsureResult(lat, lon, CellState.DOWNLOADED, path, url)
    if got.final:
        opts.memo.record(url)
    return EnsureResult(lat, lon, CellState.MISSING, None, url, got.error or f"HTTP {got.status}")


def _view_fallback(
    lat: int, lon: int, path: Path, url: str, opts: EnsureOptions, detail: str
) -> EnsureResult:
    if path.is_file() and opts.dem1_local_fallback:
        return EnsureResult(lat, lon, CellState.LOCAL, path, url, detail)
    return EnsureResult(lat, lon, CellState.MISSING, None, url, detail)


def _ensure_manual(source: str, lat: int, lon: int, opts: EnsureOptions) -> EnsureResult:
    """``SRTM``/``ALOS``: Ortho4XP stopped downloading these (``O4_DEM_Utils.py:768-778``)."""
    path = _local(source, lat, lon, opts)
    if path.is_file():
        return EnsureResult(lat, lon, CellState.LOCAL, path)
    return EnsureResult(
        lat, lon, CellState.MISSING, None, None, "this source requires a manual download"
    )


def _ensure_ned(source: str, lat: int, lon: int, opts: EnsureOptions) -> EnsureResult:
    path = _local(source, lat, lon, opts)
    if path.is_file() and _is_tiff(path):
        return EnsureResult(lat, lon, CellState.LOCAL, path)
    url = ned_url(source, lat, lon)
    if opts.memo.is_missing(url):
        return EnsureResult(lat, lon, CellState.MISSING, None, url, "in the negative memo")
    opts.check_cancelled()
    got = opts.download(url)
    if got.ok:
        _atomic_write_bytes(path, got.body)
        return EnsureResult(lat, lon, CellState.DOWNLOADED, path, url)
    if got.final:
        opts.memo.record(url)
    return EnsureResult(lat, lon, CellState.MISSING, None, url, got.error or f"HTTP {got.status}")


def _ensure_hrdem(lat: int, lon: int, opts: EnsureOptions) -> EnsureResult:
    """Canada's lidar cell, built from the WCS of NRCan (``dem/hrdem.py``).

    A cell the lidar does not reach is ``MISSING`` and remembered, so the next build asks once
    and lays Copernicus alone. The written file is a plain ``.hgt``.
    """
    from orthostudio.dem.hrdem import ProbeError, hrdem_cell, hrdem_url, write_hgt

    path = _local("HRDEM", lat, lon, opts)
    if path.is_file():
        return EnsureResult(lat, lon, CellState.LOCAL, path)
    url = hrdem_url(lat, lon, lat + 1, lon + 1, 2, 2)  # the cell, as the memo names it
    if opts.memo.is_missing(url):
        return EnsureResult(lat, lon, CellState.MISSING, None, url, "in the negative memo")
    opts.check_cancelled()
    try:
        cell = hrdem_cell(lat, lon, opts.download, check_cancelled=opts.check_cancelled)
    except ProbeError as exc:
        # The service did not answer: the cell is unknown, not empty. Remembering it here would
        # hide the lidar for a month over a region that has it (a user in Alberta, 2026-09-19).
        return EnsureResult(lat, lon, CellState.MISSING, None, url, f"the service: {exc}")
    if cell is None:
        opts.memo.record(url)
        return EnsureResult(lat, lon, CellState.MISSING, None, url, "no lidar over this cell")
    write_hgt(path, cell)
    return EnsureResult(lat, lon, CellState.DOWNLOADED, path, url)


def _ensure_anadem(lat: int, lon: int, opts: EnsureOptions) -> EnsureResult:
    """The square of ANADEM, cut out of its zone's GeoTIFF without fetching the whole 2 GB.

    The header comes first, in one read; it says where each of the file's 512 by 512 tiles lies,
    and only those the square falls in are asked for (37 MB for a square, against 2 GB for the
    zone). What is read is written as a small deflated GeoTIFF of the square, so every stage
    after this one reads it as it reads any other elevation file.

    A square outside the zones published is ``MISSING`` without a single request, and the relief
    laid under ANADEM answers there, as it does for Canada's lidar.
    """
    from orthostudio.dem.cog import (
        HEADER_BYTES,
        read_header,
        read_window,
        window_for,
        write_geotiff,
    )

    path = _local("ANADEM", lat, lon, opts)
    if path.is_file() and _is_tiff(path):
        return EnsureResult(lat, lon, CellState.LOCAL, path)
    zone = anadem_zone(lat, lon)
    if zone is None:
        return EnsureResult(lat, lon, CellState.MISSING, None, "", "outside what ANADEM covers")
    url = anadem_url(zone)
    if opts.memo.is_missing(url):
        return EnsureResult(lat, lon, CellState.MISSING, None, url, "in the negative memo")
    opts.check_cancelled()
    head = opts.ranges(url, [(0, HEADER_BYTES)])
    if not head or not head[0]:
        return EnsureResult(lat, lon, CellState.MISSING, None, url, "the zone could not be read")
    try:
        info = read_header(head[0], path=url)
        window = window_for(info, lat, lat + 1, lon, lon + 1)
        opts.check_cancelled()
        values = read_window(info, window, lambda parts: opts.ranges(url, parts), path=url)
    except OsxpError as err:
        return EnsureResult(lat, lon, CellState.MISSING, None, url, str(err))
    write_geotiff(
        path,
        values,
        north=window.north,
        west=window.west,
        step_x=window.step_x,
        step_y=window.step_y,
        nodata=info.nodata,
    )
    return EnsureResult(lat, lon, CellState.DOWNLOADED, path, url)


def manual_download_error(source: str, lat: int, lon: int, elevation_dir: Path) -> OsxpError:
    """The error raised when the *centre* cell of a manual source is missing."""
    return OsxpError(
        "DEM_SOURCE_MANUAL_DOWNLOAD",
        context={
            "source": source,
            "expected_name": str(elevation_path(source, elevation_dir, lat, lon)),
            "cell": hem_latlon(lat, lon),
        },
    )


# -- zip extraction -------------------------------------------------------------------------


def extract_view_zip(content: bytes, elevation_dir: Path) -> list[Path]:
    """Write the ``.hgt`` members of a viewfinderpanoramas archive (``:736-767``).

    A member whose basename does not parse as ``[NS]dd[EW]ddd`` is skipped, and an existing
    file is kept unless it is at most as large as the archived member (a 1" file must not be
    overwritten by the 3" copy carried in a neighbouring block's archive).
    """
    written: list[Path] = []
    with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
        for info in zf.filelist:
            name = os.path.basename(info.filename)
            m = _MEMBER_RE.match(name)
            if not name or m is None:
                continue
            lat0 = int(m.group(2)) * (-1 if m.group(1) in "Ss" else 1)
            lon0 = int(m.group(4)) * (-1 if m.group(3) in "Ww" else 1)
            out = elevation_path("View", elevation_dir, lat0, lon0)
            if out.exists() and out.stat().st_size > info.file_size:
                continue
            with zf.open(info, "r") as src:
                _atomic_write_bytes(out, src.read())
            written.append(out)
    return written


def cells_of_block(lat: int, lon: int) -> Sequence[tuple[int, int]]:
    """The 3x3 block of cells the combined raster of ``(lat, lon)`` reads, in Ortho4XP's order.

    ``itertools.product((lat, lat - 1, lat + 1), (lon, lon - 1, lon + 1))``
    (``O4_DEM_Utils.py:379``), longitudes wrapped to [-180, 180).
    """
    out: list[tuple[int, int]] = []
    for lat0 in (lat, lat - 1, lat + 1):
        for lon0 in (lon, lon - 1, lon + 1):
            out.append((lat0, (lon0 + 180) % 360 - 180))
    return out


def iter_urls(source: str, cells: Iterable[tuple[int, int]]) -> list[str]:
    """URLs that would be requested for these cells (used by the CLI and the tests)."""
    urls: list[str] = []
    for lat, lon in cells:
        if source == "View":
            urls.append(view_url(lat, lon)[0])
        elif source in ("NED1", "NED1/3"):
            urls.append(ned_url(source, lat, lon))
    return urls
