# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP's own regions, downloaded and installed by Live.

The mesh of the world and X-Plane's roads, forests and buildings cut for each of its squares are
zips on a server that asks for Live's token. Its ``manifest-2.json`` (:data:`MANIFEST`) lists, for
each region, its mesh zips, its overlay zips, its night grids and its river masks, with their path,
their size, their SHA-256 and the bytes they take unpacked. The mesh and river zips of a bake are in
a folder of its date (``eur/2026-11-18/zips/``, ``eur/2026-11-18/zips/rivers/``), the overlay and
night zips keep their paths (``eur/overlay-zips/``, ``eur/night-zips/``). Lives up to 2.2 read
``manifest.json``, another world, and never this list. The address and the token are written into
``host.json`` beside this module when the app is built, never committed (as the OSM library's,
``orthostudio.sources.library``).

A region goes to ``regions/z_osxp_<id>`` in Live's folder (a link to a folder on another disk
too), its overlays into the one overlay pack, ``regions/y_osxp_overlays``, unless the pilot said
"Never" to them. Each zip is downloaded into ``regions/.download/osxp_<id>`` under a name holding
its SHA-256 (resumed with an HTTP range after a cut; one of a zip made again since is deleted),
checked (its size and SHA-256), unpacked file by file, each file in place whole or not at all, and
deleted; one that cannot be unpacked is deleted too. The zips are unpacked in the list's order
whatever order they arrive in: a mask two neighbouring zips both hold is the same file on every
computer.

A region is made whole beside its place, in ``.z_osxp_<id>.new`` (its stage, a hidden folder on the
disk the region is on): its mesh, its night grids and its river masks (:func:`wanted`). Its marker,
which makes Live use it, is written there last, and the stage takes the region's place with one
rename (:func:`put_in_place`): until then a start does not see it. An update does the same as soon
as the mesh the region holds is not the list's (:func:`made_whole`): the region of before stays
whole and in use until the new one takes its place, and is then deleted with all it held. A region
installed already changes only with Live stopped and X-Plane not running: else its stage waits,
whole, and Live's next start with X-Plane not running puts it in place before anything else
(:func:`settle`). The overlays of an update wait with it, downloaded, and are unpacked once it is in
place. A stopped install goes on in its stage; a stage holding a zip the list no longer has as it
was is started again.

The pilot may keep the region of before (:func:`kept_dir`): the answer to the window's question,
written in the stage's record (``keep_old``), goes into the region with the swap, which then
renames the region of before ``.z_osxp_<id>.kept`` beside it, on its disk, instead of deleting it
(:func:`_settle_old`); one per region, a newer one replacing it. It never changes: nothing compares
it to the list, updates it nor downloads it again (the host keeps no release of before). Live
streams onto it, in its region's place, where the Settings' « Mesh » says old
(``scenery.osxp_sources``). Each mesh has its own Delete, the overlays staying, which both have:
:func:`remove_kept` deletes the kept one alone, and the region's own (:func:`remove`) deletes the
new one, the kept one taking its place by the update's swap reversed (:func:`_kept_back`). No room
for an update beside the region: the window offers to delete the kept mesh first only where that
makes every disk enough (:func:`_room_for_update`), never the region itself.

A region's river masks are the masks of its textures whose water is only rivers and lakes, which
Live reads only when the pilot chose soft banks (``LiveSettings.rivers_fade``): Europe's about
3.2 GB of its 14.6, so they come only with soft banks, or once the region has them. Chosen while
the region's mesh is the list's, they come alone (:func:`rivers_to_add`), downloaded and unpacked
into ``.rivers.new`` in the region's folder, Live streaming or not, and put in place as ``rivers``
with one rename once every one is there, by the same rule as a region: at once with Live stopped
and X-Plane not running, else at Live's next start with X-Plane not running (:func:`settle`). Once
there they stay, whatever the setting, and an update brings their new version with the region.
Night grids added to a region whose mesh did not change go into it in place, as nothing X-Plane
reads changes.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any

from orthostudio.errors import OsxpError
from orthostudio.fsutil import WINDOWS, atomic_write_text, fsync_dir
from orthostudio.live.aoregions import (
    MARKER,
    CancelledError,
    InstallError,
    Progress,
    _pause,
    lists_dir,
    regions_dir,
)
from orthostudio.live.filesystem import (
    folder_link,
    is_folder_link,
    link_target,
    remove_folder_link,
)
from orthostudio.live.filesystem import replace as replace_file

HOST_FILE = "host.json"
MANIFEST = "manifest-2.json"
"""The host's list of Live's regions, the light mesh's (2026-11-18): ``manifest.json`` stays the
world of before, for the Lives up to 2.2."""
CATALOG = "osxp-catalog-2.json"
"""Where Live keeps the list it read, in its ``regions`` folder: never ``osxp-catalog.json``, which
a Live up to 2.2 on the same folder reads as its own list."""
CATALOG_MAX_AGE_S = 3600.0
OVERLAY_PACK = "y_osxp_overlays"
OVERLAY_RECORD = ".live-overlays.json"  # in the overlay pack: {zip path: SHA-256}
STEP = ".live-install.json"  # a stage: zips unpacked so far, overlays waiting, keep_old, held
CHUNK = 1 << 20
ATTEMPTS = 5
PASSING = frozenset({429, 500, 502, 503, 504})
"""The host's answers that pass (busy, or a fault of its own): tried again as a cut line is. Until
2026-11-14 a single 500 stopped the whole install, the pilot left to click Install again."""
DOWNLOADS = 3  # at a time: one stream from the host is not the pilot's whole line
SPARE_BYTES = 2 * 10**9
RIVERS = "rivers"  # a region's river masks, rivers/textures (orthostudio.live.names.RIVER_MASKS)
RIVERS_STAGE = ".rivers.new"  # in the region's folder: river masks added alone, until all are there
BLOCK = 4096
"""What a file takes on the disk at least, rounded up to it: APFS's, NTFS's and ext4's block. A
river zip's masks average 10 KB (3,732 in 37 MB over Geneva and Vancouver Island, 2026-11-17):
counted at their bytes, Europe's 228,000 would take about half a block each more than said, some
0.45 GB (an estimate)."""

_SWAP = threading.Lock()  # one rename of a region at a time: an install's and a start's
XPLANE_READS = "X-Plane is running and reads this region: quit X-Plane, then try again"
"""Why a region's folders are not deleted now: the one wording of every deletion's refusal."""

Log = Callable[[str], None]


class CatalogError(RuntimeError):
    """The list of OrthoStudio XP's regions could not be read, and none was kept from before."""


@dataclass(frozen=True)
class Zip:
    path: str  # in the bucket: eur/2026-11-18/zips/z_osxp_eur_+30+016.zip
    size: int
    sha256: str
    unpacked: int

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def local(self) -> str:
        """Its path in the bucket below its region, how the errors name it
        (``2026-11-18/zips/rivers/z_osxp_eur_+30+016.zip``): a river zip has its mesh zip's name,
        beside it."""
        return self.path.split("/", 1)[-1]

    @property
    def download(self) -> str:
        """Its file in the download folder: its SHA-256 in the name, so that a part of a zip made
        again since is never taken for the beginning of the new one, and a river zip never meets
        its mesh zip of the same name."""
        return _download_name(self.path, self.sha256)


def _download_name(path: str, sha256: str) -> str:
    return f"{sha256[:16]}-{path.rsplit('/', 1)[-1]}"


@dataclass(frozen=True)
class OsxpRegion:
    id: str
    release: str  # when the manifest was made
    mesh: tuple[Zip, ...]
    overlays: tuple[Zip, ...] = ()
    # the night lights' density grids of its squares (orthostudio.live.nightwork), unpacked into
    # the region's folder as its mesh zips are: ``night/<square>.png``
    night: tuple[Zip, ...] = ()
    # its river masks, one zip beside each mesh zip that has any: ``rivers/textures/<mask>``; which
    # of them a region wants: :func:`wanted`
    rivers: tuple[Zip, ...] = ()


@functools.cache
def shipped_host() -> tuple[str, str]:
    """The address of Live's downloads and its token, as this build carries them; two empty
    strings when it carries none."""
    try:
        doc = json.loads((resources.files("orthostudio.live") / HOST_FILE).read_bytes())
    except (OSError, ModuleNotFoundError, ValueError):
        return "", ""
    if not isinstance(doc, dict):
        return "", ""
    return str(doc.get("url", "") or "").rstrip("/"), str(doc.get("token", "") or "")


def _client(token: str):
    import httpx

    headers = {"User-Agent": "OrthoStudio-XP-Live"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=15.0), headers=headers)


def parse_manifest(doc: dict) -> dict[str, OsxpRegion]:
    """The regions of the host's list, each kind of zip read by its name: a kind this Live does not
    know is left aside, as every Live since 2.0 does. A zip entry has its four fields and no other:
    ``Zip(**z)`` refuses one more, and a Live refusing the list keeps the one it had for good. A
    kind added never adds a field to the zips of another."""
    made = str(doc.get("made", ""))
    out = {}
    for region_id, kinds in (doc.get("regions") or {}).items():
        out[region_id] = OsxpRegion(
            region_id,
            made,
            tuple(Zip(**z) for z in kinds.get("mesh", ())),
            tuple(Zip(**z) for z in kinds.get("overlays", ())),
            tuple(Zip(**z) for z in kinds.get("night", ())),
            tuple(Zip(**z) for z in kinds.get("rivers", ())),
        )
    return out


def _answered(status: int) -> str:
    """The server's answer, said one way wherever it comes from."""
    return f"the server answered HTTP {status}"


UNREADABLE = (ValueError, TypeError, KeyError, AttributeError)
"""What reading a list that is not one raises (cut short, not JSON, not the list's shape)."""


def _why(e: BaseException) -> str:
    """What went wrong, in the pilot's words, the host's address never in it (the window and the
    logs show it): the server's answer, the server out of reach, the disk refusing, or else an
    answer that does not read (:data:`UNREADABLE`)."""
    import httpx

    if isinstance(e, httpx.HTTPStatusError):
        return _answered(e.response.status_code)
    if isinstance(e, httpx.HTTPError):
        return "the server could not be reached: check the internet connection"
    if isinstance(e, OSError):
        return f"the disk refused it ({e.strerror or e})"
    return "the server's answer did not read: try again later"


def catalog(
    root: Path, *, refresh: bool = False, client=None, host: tuple[str, str] | None = None
) -> dict[str, OsxpRegion]:
    """OrthoStudio XP's regions: from the host's :data:`MANIFEST` at most every hour, else the list
    kept in Live's folder (:data:`CATALOG`), which also stands in when the host does not answer."""
    import httpx

    url, token = host or shipped_host()
    path = regions_dir(root) / CATALOG
    try:
        fresh = not refresh and time.time() - path.stat().st_mtime < CATALOG_MAX_AGE_S
    except OSError:
        fresh = False
    if not fresh and url:
        try:
            own = client is None
            c = client or _client(token)
            try:
                r = c.get(f"{url}/{MANIFEST}")
                r.raise_for_status()
                doc = r.json()
                regions = parse_manifest(doc)
            finally:
                if own:
                    c.close()
            if regions:
                atomic_write_text(path, json.dumps(doc))
                return regions
        except (OSError, httpx.HTTPError, *UNREADABLE) as e:
            if not path.exists():
                raise CatalogError(_why(e)) from e  # the window says what did not come
    if not path.exists():
        raise CatalogError("this build of Live carries no address for its regions")
    try:
        return parse_manifest(json.loads(path.read_text()))
    except (OSError, *UNREADABLE) as e:
        raise CatalogError("the copy kept on this computer is unreadable") from e


def mesh_dir(root: Path, region: str) -> Path:
    return regions_dir(root) / f"z_osxp_{region}"


def overlay_pack(root: Path) -> Path:
    return regions_dir(root) / OVERLAY_PACK


def download_dir(root: Path, region: str) -> Path:
    """Where a region's zips are downloaded, kept until unpacked (an update's overlays until the
    region is in place)."""
    return regions_dir(root) / ".download" / f"osxp_{region}"


def installed(root: Path) -> dict[str, dict]:
    """The regions installed whole: {id: their marker}."""
    out: dict[str, dict] = {}
    with contextlib.suppress(OSError):
        for entry in os.scandir(regions_dir(root)):
            if entry.name.startswith("z_osxp_"):
                with contextlib.suppress(OSError, ValueError):
                    out[entry.name.removeprefix("z_osxp_")] = json.loads(
                        (Path(entry.path) / MARKER).read_text(encoding="utf-8")
                    )
    return out


def _read(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(path: Path, doc: dict) -> None:
    """A record written whole, flushed to the disk before it takes its name: a power cut leaves
    the one of before or this one."""
    atomic_write_text(path, json.dumps(doc), fsync=True)


# --- where a region is ----------------------------------------------------------------------------


def _real(root: Path, region: str) -> Path:
    """The region's folder where it really is: the folder a link leads to (a symbolic link, or a
    junction on Windows), for one on another disk."""
    folder = mesh_dir(root, region)
    return link_target(folder) if is_folder_link(folder) else folder


def stage_dir(root: Path, region: str) -> Path:
    """Where the region is made whole before it takes its place: beside it, on its disk, hidden
    (no ``z_osxp_`` name: no start takes it for a region)."""
    real = _real(root, region)
    return real.with_name(f".{real.name}.new")


def _old_dir(real: Path) -> Path:
    """Where the region of before goes for the moment its successor takes its place."""
    return real.with_name(f".{real.name}.old")


def _kept_of(real: Path) -> Path:
    return real.with_name(f".{real.name}.kept")


def kept_dir(root: Path, region: str) -> Path:
    """Where the region's kept old mesh is: beside the region, on its disk (renamed there, never
    copied), hidden and of a name nothing else takes: no ``z_osxp_`` one (no start nor
    :func:`installed` takes it for a region), no stage nor region of before (``.new``, ``.old``),
    nothing set aside (:data:`ASIDE`)."""
    return _kept_of(_real(root, region))


def kept_mesh(root: Path, region: str) -> Path | None:
    """The region's kept old mesh, its marker there (its region's marker of before: its release,
    its size); None where it has none."""
    kept = kept_dir(root, region)
    return kept if (kept / MARKER).is_file() else None


def _keeping(real: Path) -> bool:
    """Whether the region of before waits beside ``real`` to be renamed as its kept old mesh: it
    is there, and the record the swap carried into the region says to keep it (``keep_old``)."""
    return _old_dir(real).exists() and bool(_read(real / STEP).get("keep_old"))


def keeps_old(root: Path, region: str) -> bool:
    """Whether the region keeps an old mesh: kept (:func:`kept_mesh`), or the region of before
    still waiting to be renamed as kept, its rename refused a moment (:func:`_keeping`)."""
    return kept_mesh(root, region) is not None or _keeping(_real(root, region))


def keeps_any_old(root: Path) -> bool:
    """Whether some region keeps an old mesh (:func:`keeps_old`), or may: one linked to a disk not
    connected, whose kept mesh cannot be seen."""
    for region in _region_ids(root):
        link = mesh_dir(root, region)
        if (is_folder_link(link) and not link.exists()) or keeps_old(root, region):
            return True
    return False


def kept_region(root: Path, folder: Path) -> str | None:
    """The region whose kept old mesh ``folder`` is (:func:`kept_dir`); None for any other: the
    one place that knows its name."""
    return next((r for r in installed(root) if kept_dir(root, r) == folder), None)


def present(root: Path, region: str) -> bool:
    """Whether anything of a region is on the disk: the region, or only its stage, the region of
    before that a stop left beside it, or its kept old mesh."""
    real = _real(root, region)
    return any(
        os.path.lexists(p)
        for p in (
            mesh_dir(root, region),
            real,
            stage_dir(root, region),
            _old_dir(real),
            _kept_of(real),
        )
    )


def staged_whole(root: Path, region: str) -> bool:
    """Whether an update of the region waits whole to take its place: its stage, or its river
    masks' (:func:`settle`)."""
    return _waiting_stage(root, region) is not None


def _rivers_waiting(root: Path, region: str) -> bool:
    """Whether the region's river masks wait whole in their stage, its marker written."""
    return (mesh_dir(root, region) / RIVERS_STAGE / MARKER).is_file()


def _waiting_stage(root: Path, region: str) -> Path | None:
    """The stage of the region's update waiting whole (:func:`staged_whole`): its own, else its
    river masks'."""
    for stage in (stage_dir(root, region), mesh_dir(root, region) / RIVERS_STAGE):
        if (stage / MARKER).is_file():
            return stage
    return None


def held(root: Path, region: str) -> str | None:
    """Why the region's update waiting whole did not take its place at the last start that tried
    (a program holding a folder of it open): :func:`settle`'s refusal, kept in its stage's record
    until the region's next install (:func:`_try_again`); None where none was refused or nothing
    waits."""
    stage = _waiting_stage(root, region)
    return _read(stage / STEP).get("held") if stage is not None else None


def _try_again(root: Path, region: str) -> None:
    """The refusal :func:`held` says forgotten, as the pilot installs the region again."""
    stage = _waiting_stage(root, region)
    step = _read(stage / STEP) if stage is not None else {}
    if stage is not None and step.pop("held", None) is not None:
        _write(stage / STEP, step)


# --- what a region wants and has --------------------------------------------------------------


def _mesh_path(path: str) -> bool:
    """Whether a zip's path is a mesh zip's: ``<region>/zips/<name>`` or, since the light mesh,
    ``<region>/<release>/zips/<name>``; not a river zip's (``zips/rivers/``) nor a night grid's."""
    parts = path.split("/")
    return len(parts) >= 3 and parts[-2] == "zips"


def has_rivers(root: Path, region: str) -> bool:
    """Whether the region holds its river masks: put in place only whole, and kept once there,
    whatever the setting."""
    return (mesh_dir(root, region) / RIVERS).is_dir()


def keeps_rivers(root: Path, region: str, rivers: bool) -> bool:
    """Whether the region wants its river masks: the pilot chose soft banks (``rivers``,
    ``LiveSettings.rivers_fade``), or the region has them already, in place or waiting whole. Once
    downloaded they are part of the region: going back to even rivers and lakes leaves them there,
    unused, so that soft banks chosen again downloads nothing, and an update of the region brings
    their new version with it, whatever the setting. The one rule of whether an install, an
    update, the sizes and the update counts take them, and of whether their stages are kept;
    read before anything is deleted."""
    return rivers or has_rivers(root, region) or _rivers_waiting(root, region)


def wanted(root: Path, region: OsxpRegion, rivers: bool) -> tuple[Zip, ...]:
    """The zips a region wants in its folder: its mesh, its night grids, and its river masks where
    it keeps them (:func:`keeps_rivers`)."""
    keep = keeps_rivers(root, region.id, rivers)
    return (*region.mesh, *region.night, *(region.rivers if keep else ()))


def _installed_zips(root: Path, region: str) -> dict[str, str] | None:
    """The zips the installed region was made of, {path: SHA-256}; None: not installed."""
    marker = _read(mesh_dir(root, region) / MARKER)
    return dict(marker.get("zips") or {}) if marker else None


def made_whole(root: Path, region: OsxpRegion) -> bool:
    """Whether an install makes the region whole in its stage: not installed, or the mesh it holds
    is not the list's, one zip of it made again since or one the list no longer has (every bake
    makes every zip again; a region updated in place by a Live of before keeps the names of zips
    gone). False: only night grids, river masks or overlays to add."""
    have = _installed_zips(root, region.id)
    if have is None:
        return True
    mesh = {p: s for p, s in have.items() if _mesh_path(p)}
    return mesh != {z.path: z.sha256 for z in region.mesh}


def recorded(root: Path, region: OsxpRegion) -> dict[str, str]:
    """The zips the region's marker records as installed, {path: SHA-256}. Its river zips count
    all together or not at all: only while its ``rivers`` folder is there and each is the list's as
    it was. One of them made again, they all come again, put in place whole."""
    zips = _installed_zips(root, region.id) or {}
    whole = has_rivers(root, region.id) and all(zips.get(z.path) == z.sha256 for z in region.rivers)
    if not whole:
        for z in region.rivers:
            zips.pop(z.path, None)
    return zips


def _staged(root: Path, region: OsxpRegion) -> dict[str, dict[str, str]]:
    """What a stopped install left in the region's stage, ``{"zips": {path: SHA-256}, "overlays":
    {path: SHA-256}}`` (the overlays downloaded, waiting for the region to be in place), when each
    is still one of the list as it was, the zip it was unpacking as it stopped too; else nothing,
    the stage to be started again: a zip the list no longer has, or made again since, would leave
    its files in the region."""
    step = _read(stage_dir(root, region.id) / STEP)
    zips = _still_listed(step, (*region.mesh, *region.night, *region.rivers))
    waiting = _still_listed({"zips": step.get("overlays")}, region.overlays)
    if zips is None or waiting is None:
        return {"zips": {}, "overlays": {}}
    return {"zips": zips, "overlays": waiting}


def _still_listed(step: dict, listed: Iterable[Zip]) -> dict[str, str] | None:
    """The zips a stage's step records, {path: SHA-256}, when each, and the one it was unpacking
    as it stopped, is still one of ``listed`` as it was; None where one is not: the stage to be
    started again."""
    zips = dict(step.get("zips") or {})
    holds = {**zips, **(step.get("unpacking") or {})}
    have = {z.path: z.sha256 for z in listed}
    return zips if all(have.get(p) == s for p, s in holds.items()) else None


def _rivers_staged(root: Path, region: OsxpRegion) -> dict[str, str]:
    """The river zips a stopped download unpacked into the region's river stage, {path: SHA-256},
    when the stage still holds them, or is whole (its marker: a stop between their rename and
    their record), and each is the list's as it was; else none, the stage to be started again: a
    zip made again since would leave masks of before among the new ones."""
    stage = mesh_dir(root, region.id) / RIVERS_STAGE
    if not (stage / RIVERS).is_dir() and not (stage / MARKER).is_file():
        return {}
    return _still_listed(_read(stage / STEP), region.rivers) or {}


def not_installed(root: Path, region: OsxpRegion, rivers: bool) -> list[Zip]:
    """The zips an update of the region brings, downloaded already or not (a whole stage waiting
    to take its place): every one it wants (:func:`wanted`) where it makes the region whole
    (:func:`made_whole`), whatever its marker records, else those its marker does not record as
    installed."""
    if made_whole(root, region):
        return list(wanted(root, region, rivers))
    have = recorded(root, region)
    return [z for z in wanted(root, region, rivers) if have.get(z.path) != z.sha256]


def to_fetch(
    root: Path, region: OsxpRegion, overlays: bool, rivers: bool = False
) -> tuple[list[Zip], list[Zip]]:
    """The zips of the region's folder (:func:`wanted`) and the overlay zips an install downloads.
    A region made whole (:func:`made_whole`): every zip it wants but those its stage holds already,
    and the overlays not installed or changed since, but those waiting in the stage. Else the night
    grids and river masks it has not got, or that changed since, but the river masks its river
    stage holds, and the overlays not installed or changed since. The overlays only with
    ``overlays``."""
    record = _read(overlay_pack(root) / OVERLAY_RECORD)
    if made_whole(root, region):
        step = _staged(root, region)
        have, waiting = step["zips"], step["overlays"]
    else:
        have, waiting = {**recorded(root, region), **_rivers_staged(root, region)}, {}
    mesh = [z for z in wanted(root, region, rivers) if have.get(z.path) != z.sha256]
    ovl = [
        z
        for z in region.overlays
        if record.get(z.path) != z.sha256 and waiting.get(z.path) != z.sha256
    ]
    return mesh, ovl if overlays else []


def rivers_to_add(root: Path, region: OsxpRegion) -> list[Zip]:
    """The river zips soft banks downloads alone for a region installed whole whose mesh is the
    list's; none for one whose mesh changed: its update brings the region whole, rivers in."""
    if made_whole(root, region):
        return []
    mesh, _ = to_fetch(root, region, False, rivers=True)
    return [z for z in mesh if z in region.rivers]


def night_to_fetch(root: Path, region: OsxpRegion) -> list[Zip]:
    """The night grids' zips of a region installed whole that it has not got yet, or that changed
    in the bucket since: a region installed before Live lit its towns (2.0) has none. None for a
    region whose mesh changed: its update brings them."""
    if made_whole(root, region):
        return []
    have = _installed_zips(root, region.id) or {}
    return [z for z in region.night if have.get(z.path) != z.sha256]


def _size(region: OsxpRegion, zips: Iterable[str]) -> int:
    """What a region holding these zips takes unpacked, from the list: its marker's ``size``,
    which the page shows (a zip the list no longer has counts nothing)."""
    sizes = {z.path: z.unpacked for z in (*region.mesh, *region.night, *region.rivers)}
    return sum(sizes.get(p, 0) for p in zips)


def disk_need(path: Path) -> int:
    """The room a zip's files take once unpacked: each rounded up to a whole block (:data:`BLOCK`),
    from the zip's own directory, as the list gives no count of files."""
    with zipfile.ZipFile(path) as zf:
        return sum(-(-i.file_size // BLOCK) * BLOCK for i in zf.infolist() if not i.is_dir())


def unpack(path: Path, dest: Path, stop: threading.Event | None = None) -> int:
    """A zip's files into ``dest``, its top folder taken off; each written beside its place and
    renamed onto it whole, so that a stop leaves the file of before or the new one, never half of
    one. Each member's CRC is checked as it is read. Returns the files unpacked."""
    count = 0
    with zipfile.ZipFile(path) as zf:
        tops = {n.split("/")[0] for n in zf.namelist() if "/" in n}
        if len(tops) != 1:
            raise InstallError(f"{path.name}: {len(tops)} top folders, not 1")
        prefix = f"{next(iter(tops))}/"
        root = dest.resolve()
        for info in zf.infolist():
            if stop is not None and stop.is_set():
                raise CancelledError
            relative = info.filename[len(prefix) :]
            if info.is_dir() or not relative:
                continue
            target = (root / relative).resolve()
            if root not in target.parents:
                raise InstallError(f"{path.name}: {info.filename} would go outside the region")
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(f".{target.name}.part")
            with zf.open(info) as src, open(part, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            replace_file(part, target)
            count += 1
    if count == 0:
        raise InstallError(f"nothing in {path.name}")
    return count


# --- a region put in place, and what a stop left -----------------------------------------------


UPDATE_AGAIN = "try again, which downloads nothing more"
DELETE_AGAIN = "delete it again"
"""What the pilot does once the program holding a folder is closed (:func:`_in_use`): an update
tried again downloads nothing it has, a deletion is asked again."""


def _in_use(what: str, again: str, e: OSError) -> InstallError:
    """A folder a program holds open (Windows), said: close it, then ``again``. Never X-Plane:
    a region changes only with X-Plane not running."""
    which = " (File Explorer, an antivirus…)" if WINDOWS else ""
    return InstallError(
        f"another program holds {what} open{which}: close it, then {again} ({_why(e)})"
    )


def _rename(src: Path, dst: Path, what: str, again: str) -> None:
    """One folder renamed (tried again a moment on Windows, where a folder a program holds open
    cannot be), or :class:`InstallError` saying what holds it (:func:`_in_use`)."""
    try:
        replace_file(src, dst)
    except OSError as e:
        raise _in_use(what, again, e) from e


def _swap_in(
    new: Path,
    real: Path,
    what: str,
    touch: Sequence[str] = (),
    beside: Path | None = None,
    again: str = UPDATE_AGAIN,
) -> bool:
    """``new`` put in ``real``'s place with one rename, the one of before renamed aside first
    (:func:`_old_dir`), put back where the rename is refused, then kept or deleted
    (:func:`_settle_old`): a start finds the one or the other, whole. ``real`` and its ``touch``
    folders then dated now, what the next start's set-up compares (``region._fingerprints``); the
    one of before keeps its dates. The one swap of a region's stage, of its river masks' and of
    its kept old mesh put back (:func:`_kept_back`) alike; :class:`InstallError` when a program
    holds a folder open, saying what to do once it is closed (``again``, an update's by default).
    ``beside``: where the one of before is set aside, out of the region's own folder for its river
    masks (:func:`_set_aside`). Whether the one of before was kept. The lock held."""
    old = _old_dir(real)
    _settle_old(real, beside)  # one an earlier swap left: kept or deleted as its record says
    if real.exists():
        _rename(real, old, what, again)
    try:
        _rename(new, real, what, again)
    except InstallError:
        if old.exists() and not real.exists():
            replace_file(old, real)  # the one of before back, as it was
        raise
    fsync_dir(real.parent)
    now = time.time()
    for path in (real, *(real / sub for sub in touch)):
        with contextlib.suppress(OSError):
            os.utime(path, (now, now))
    return _settle_old(real, beside)


def _settle_old(real: Path, beside: Path | None = None) -> bool:
    """The one of before, once its successor is in ``real``'s place: kept as the region's old mesh
    (:func:`kept_dir`) where the pilot said so, the answer its stage's record carried into the
    region with the swap (``keep_old``), else deleted (:func:`_discard`); that record dropped
    last, so that a stop between leaves the answer to the next :func:`_repair`. One kept mesh per
    region: the one kept before is set aside first and deleted only once the new one has its
    place; a rename refused a moment (Windows) puts it back and leaves the one of before and the
    record for the next settle, the region keeping a kept mesh all along. The one way of a swap
    and of a repair after a stop between their renames; whether it was kept. The lock held."""
    old = _old_dir(real)
    kept = False
    if _keeping(real):
        place = _kept_of(real)
        try:
            before = _set_aside(place) if place.exists() else None
        except OSError:  # held a moment: kept at the next settle
            return False
        try:
            replace_file(old, place)
        except OSError:  # held a moment: the one kept before back, this one kept at the next settle
            if before is not None:
                replace_file(before, place)
            return False
        fsync_dir(real.parent)
        if before is not None:
            _delete_aside(before)
        kept = True
    else:
        _discard(old, beside)
    with contextlib.suppress(OSError):  # a disk that answers otherwise: tried at the next settle
        (real / STEP).unlink(missing_ok=True)
    return kept


DATED = ("Earth nav data", "terrain", "terrain-lists")
"""A region's folders a swap dates now with the region (:func:`_swap_in`): what the next start's
set-up compares (``region._fingerprints``)."""


def put_in_place(root: Path, region: str, log: Log = print) -> bool:
    """A whole stage (its marker written) put in the region's place (:func:`_swap_in`), the region
    of before kept or deleted as its record says (:func:`_settle_old`), then the overlays that
    waited with it unpacked (:func:`_unpack_waiting`). False, nothing done, where no whole stage
    waits. The caller says whether it may: Live stopped and X-Plane not running, or a region not
    installed yet."""
    with _SWAP:
        stage = stage_dir(root, region)
        if not (stage / MARKER).is_file():
            return False
        real = _real(root, region)
        if _swap_in(stage, real, real.name, touch=DATED):
            log(f"  the mesh of before of {region} kept beside it, {_kept_of(real).name}")
    _unpack_waiting(root, region, log)
    return True


def put_rivers_in_place(root: Path, region: str) -> bool:
    """A region's river masks waiting whole in their stage (its marker written) put in place as
    ``rivers`` (:func:`_swap_in`), then recorded in the region's marker, last; the stage deleted.
    False, nothing done, where none wait. The caller says whether it may, as for a region: Live
    stopped and X-Plane not running, since the next start's set-up makes the region's terrain
    files again for them, and a start with X-Plane running on a region changed is refused."""
    with _SWAP:
        folder = mesh_dir(root, region)
        beside = _real(root, region).parent  # what is set aside goes there, out of the region
        stage = folder / RIVERS_STAGE
        done = _read(stage / MARKER)
        if not done:
            return False
        if (stage / RIVERS).is_dir():
            what = f"the river masks of {region}"
            _swap_in(stage / RIVERS, folder / RIVERS, what, beside=beside)
        elif not (folder / RIVERS).is_dir():  # neither here nor there: downloaded again
            _discard(stage, beside)
            return False
        # else a stop between their rename and their record: in place already
        marker = _read(folder / MARKER)
        have = marker.get("zips") or {}
        if any(have.get(p) != s for p, s in done["zips"].items()):  # else recorded before a stop
            size = int(marker.get("size") or 0) + done["adds"]  # :meth:`Installer._rivers_done`
            _write(folder / MARKER, {**marker, "zips": {**have, **done["zips"]}, "size": size})
        _discard(stage, beside)
    return True


def _unpack_waiting(root: Path, region: str, log: Log) -> None:
    """The overlays an update downloaded, unpacked once its region is in place, in the list's order:
    new roads, forests and buildings never stand on the mesh of before. One whose zip is gone is
    left to the next install, which downloads it again."""
    folder = mesh_dir(root, region)
    marker = _read(folder / MARKER)
    waiting = dict(marker.get("overlays_waiting") or {})
    if not waiting:
        return
    download = download_dir(root, region)
    pack = overlay_pack(root)
    pack.mkdir(parents=True, exist_ok=True)
    done = 0
    for path, sha in waiting.items():
        zipped = download / _download_name(path, sha)
        if not zipped.is_file():
            continue
        try:
            unpack(zipped, pack)
        except OSError:
            raise
        except Exception as e:  # a zip that cannot be unpacked: downloaded again
            name = path.rsplit("/", 1)[-1]
            log(f"  {name} could not be unpacked ({e}): it is downloaded again the next time")
            zipped.unlink(missing_ok=True)
            continue
        record = _read(pack / OVERLAY_RECORD)
        record[path] = sha
        _write(pack / OVERLAY_RECORD, record)
        zipped.unlink(missing_ok=True)
        done += 1
    marker.pop("overlays_waiting", None)
    _write(folder / MARKER, marker)
    shutil.rmtree(download, ignore_errors=True)
    log(f"  {done} overlay zips of {region} unpacked with it")


ASIDE = re.compile(r"\..+\.(?:new|old|kept)\.\d+")
"""A folder set aside to be deleted (:func:`_set_aside`): ``.z_osxp_eur.old.<n>``,
``.z_osxp_eur.new.<n>``, ``.z_osxp_eur.kept.<n>``, ``.rivers.new.<n>``..."""


def _set_aside(path: Path, beside: Path | None = None) -> Path:
    """``path`` renamed to a hidden name of its own, ``<its hidden name>.<n>`` of each kind
    (``.z_osxp_<id>.old.<n>`` for a region, ``.z_osxp_<id>.new.<n>`` for its stage...), which
    nothing takes for a region, a stage or an update stopped halfway; :func:`take_up_deletions`
    deletes what a quit left of it. ``beside``: a folder out of the one ``path`` is in, for what a
    region's folder or stage holds (``.z_osxp_<id>.rivers.new.<n>``): never left inside a folder
    renamed next, which Windows refuses while a deletion holds a folder of it. OSError where a
    program holds it open."""
    base = path.name if path.name.startswith(".") else _old_dir(path).name
    if beside is not None:
        base = f".{path.parent.name.lstrip('.')}{base}"
    gone = (beside or path.parent) / f"{base}.{time.time_ns()}"
    replace_file(path, gone)
    return gone


def _aside(path: Path, beside: Path | None = None) -> Path | None:
    """A stage, or a region or river masks of before, made ready to be deleted: its marker and its
    record taken away first, so that a stop never leaves one half deleted that a start or an
    install would trust, then set aside (:func:`_set_aside`). None where there is none, or where
    it could not be set aside (held open a moment): deleted where it is then."""
    if not path.exists():
        return None
    (path / MARKER).unlink(missing_ok=True)
    (path / STEP).unlink(missing_ok=True)
    try:
        return _set_aside(path, beside)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)
        return None


def _discard(path: Path, beside: Path | None = None, *, wait: bool = False) -> None:
    """A stage, or a region or river masks of before, deleted, the one way: set aside
    (:func:`_aside`), then deleted, in the background (hundreds of thousands of files take a
    minute or more, which a start waits for nowhere), or before returning with ``wait``, where
    the room they leave is counted next (:func:`_drop_stale`)."""
    aside = _aside(path, beside)
    if aside is not None:
        (_delete if wait else _delete_aside)(aside)


def _delete(*folders: Path, links: Sequence[Path] = ()) -> None:
    """``folders`` deleted, then ``links``, the folder links that kept them within reach of Live's
    ``regions`` folder (:func:`remove`), last."""
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)
    for link in links:
        with contextlib.suppress(OSError):
            remove_folder_link(link)


def _delete_aside(*folders: Path, links: Sequence[Path] = ()) -> None:
    """:func:`_delete` in the background."""
    threading.Thread(target=_delete, args=folders, kwargs={"links": links}, daemon=True).start()


def _set_asides_in(place: Path) -> list[Path]:
    """The folders of ``place`` set aside to be deleted (:data:`ASIDE`), never a link."""
    found = []
    with contextlib.suppress(OSError), os.scandir(place) as entries:
        for entry in entries:
            path = Path(entry.path)
            if (
                ASIDE.fullmatch(entry.name)
                and entry.is_dir(follow_symlinks=False)
                and not is_folder_link(path)
            ):
                found.append(path)
    return found


def take_up_deletions(root: Path) -> int:
    """The folders set aside to be deleted that a quit left (:data:`ASIDE`), beside Live's regions,
    deleted in the background; for each link set aside in Live's ``regions`` folder
    (:func:`remove`), what was set aside beside the folder it leads to, that folder, then the link,
    in one deletion, the link last: everything set aside stays within reach of that folder until
    it is deleted. A link whose disk is away is kept for a later time. Once, as Live's window
    opens, before anything else: never beside a deletion still under way. Returns how many
    folders and links."""

    def key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(path))

    regions = regions_dir(root)
    places = {key(regions): regions}
    for region in _region_ids(root):
        place = _real(root, region).parent
        places[key(place)] = place
    found = 0
    links: list[Path] = []
    with contextlib.suppress(OSError), os.scandir(regions) as entries:
        links = [Path(e.path) for e in entries if ASIDE.fullmatch(e.name)]
    for link in links:
        if not is_folder_link(link):
            continue
        target = link_target(link)
        if not target.parent.is_dir():  # its disk away: taken up when it is back
            continue
        places.pop(key(target.parent), None)
        folders = [f for f in _set_asides_in(target.parent) if key(f) != key(target)]
        folders += [target] if target.exists() else []
        _delete_aside(*folders, links=[link])
        found += len(folders) + 1
    for place in places.values():
        folders = _set_asides_in(place)
        if folders:
            _delete_aside(*folders)
            found += len(folders)
    return found


def _region_ids(root: Path) -> set[str]:
    """Every region something of which is in Live's ``regions`` folder: its folder or link, its
    stage or the region of before (those of a region on another disk are beside it there)."""
    ids = set()
    with contextlib.suppress(OSError):
        for entry in os.scandir(regions_dir(root)):
            m = re.fullmatch(r"\.?z_osxp_([a-z0-9_]+)(\.(?:new|old)(?:\.\d+)?)?", entry.name)
            if m and (entry.name.startswith("z_") or m.group(2)):
                ids.add(m.group(1))
    return ids


def _repair(real: Path, log: Log, what: str) -> bool:
    """What a stop or a power cut left beside ``real`` made right: the one of before renamed aside
    whose successor never took its place put back; one left beside its successor kept or deleted
    as the stage's record says (:func:`_settle_old`, a stop between the two renames and the one
    that keeps it), that record then dropped: a region in place never holds one (the stage keeps
    it until then, a refused swap downloading nothing). False where it could not be put back. The
    lock held."""
    old = _old_dir(real)
    if not real.exists() and old.exists():
        try:
            replace_file(old, real)
        except OSError as e:
            log(f"{what} could not be put back: {e}")
            return False
        log(f"{what}: put back as it was before its update")
    elif real.exists() and _settle_old(real):
        log(f"{what}: the mesh of before kept beside it")
    return True


def settle(root: Path, log: Log = print, *, swap: bool, only: Iterable[str] | None = None) -> None:
    """What a stop or a power cut left, made right (:func:`_repair`), for each region and for its
    river masks; with ``swap`` (Live stopped and X-Plane not running), each whole stage put in its
    region's place and the overlays that waited with it unpacked, then the river masks waiting
    whole put in place. At an install's start, as Live's window opens (``swap`` False) and at
    Live's start, before anything reads the regions."""
    for region in sorted(only if only is not None else _region_ids(root)):
        real = _real(root, region)
        with _SWAP:
            if not _repair(real, log, f"OrthoStudio XP's region {region}"):
                continue
            _repair(real / RIVERS, log, f"the river masks of {region}")
        if not swap:
            continue
        try:
            if put_in_place(root, region, log):
                log(f"OrthoStudio XP's region {region}: its update put in place")
            else:
                _unpack_waiting(root, region, log)  # a stop after it took its place
            if put_rivers_in_place(root, region):
                log(f"OrthoStudio XP's region {region}: its river masks put in place")
        except (InstallError, OSError) as e:
            log(f"OrthoStudio XP's region {region}: its update waits ({e})")
            stage = _waiting_stage(root, region)
            if stage is not None:  # the window says why (held)
                with contextlib.suppress(OSError):
                    _write(stage / STEP, {**_read(stage / STEP), "held": str(e)})


def _set_aside_whole(path: Path) -> Path:
    """``path`` set aside to be deleted in one rename (:func:`_set_aside`), or
    :class:`InstallError` saying what holds it open: nothing deleted then."""
    try:
        return _set_aside(path)
    except OSError as e:
        raise _in_use(path.name, DELETE_AGAIN, e) from e


def remove(root: Path, region: str, squares: Sequence[tuple[int, int]], log: Log = print) -> None:
    """The region's Delete: its installed mesh deleted. Where it keeps an old mesh
    (:func:`keeps_old`), that one takes its place (:func:`_kept_back`), the overlays staying. Else
    the region deleted from the disk (the folder a link leads to too, a link or a junction), its
    overlays out of the overlay pack, and what an install of it left behind: its stage, the region
    of before, its downloads. Set aside first, in one rename (:func:`_set_aside_whole`, refused
    while a program holds it open: nothing deleted then), then deleted in the background: never
    half a region left that a start would take, and no request waiting a minute for it. A region
    of which only a stage or the region of before is left is deleted too. A region on another disk
    keeps a link within reach of Live's ``regions`` folder, set aside there too, until what was set
    aside beside it is deleted (:func:`take_up_deletions`)."""
    if keeps_old(root, region):
        _kept_back(root, region, log)
        return
    folder = mesh_dir(root, region)
    real = _real(root, region)
    # beside the region: before its link goes
    stage, old, kept = stage_dir(root, region), _old_dir(real), _kept_of(real)
    linked = is_folder_link(folder)
    with _SWAP:
        whole = _set_aside_whole(real) if real.exists() else None
        left = [p for p in (_aside(stage), _aside(old), _aside(kept), whole) if p is not None]
        links = []
        if linked and left:  # the region's disk reached from regions/ until all is deleted
            link = regions_dir(root) / left[-1].name
            try:
                folder_link(left[-1], link)
                links.append(link)
            except (OSError, OsxpError):  # no link (a junction refused): deleted all the same
                pass
        if linked:
            remove_folder_link(folder)
        if left:
            _delete_aside(*left, links=links)
    shutil.rmtree(download_dir(root, region), ignore_errors=True)
    pack = overlay_pack(root)
    # its overlays out of the record before their files: a stop between leaves files the record no
    # longer names, which an install of the region downloads again over them, never a record naming
    # files that are gone (that region's roads and forests then missing for good)
    record = _read(pack / OVERLAY_RECORD)
    if record:
        others = {k: v for k, v in record.items() if not k.startswith(f"{region}/")}
        _write(pack / OVERLAY_RECORD, others)
    gone = 0
    for lat, lon in squares:
        tens = f"{lat // 10 * 10:+03d}{lon // 10 * 10:+04d}"
        dsf = pack / "Earth nav data" / tens / f"{lat:+03d}{lon:+04d}.dsf"
        with contextlib.suppress(FileNotFoundError):
            dsf.unlink()
            gone += 1
    _forget_list(root, folder, kept)
    log(f"OrthoStudio XP's region {region} deleted, and {gone} of its overlays")


def _kept_back(root: Path, region: str, log: Log) -> None:
    """The region's Delete where it keeps an old mesh (:func:`remove`): the reverse of the update
    that kept it, by the same swap (:func:`_swap_in`). The kept mesh renamed into the region's
    place, its marker with it (its release: the window offers its Update again), dated now for the
    next start's set-up; the installed mesh renamed aside first, then deleted in the background,
    no record keeping it (:func:`_settle_old`). A stop or a power cut between the renames leaves
    what the next opening makes right (:func:`_repair`): the installed mesh back and the old one
    still kept, or the old one in place and the other deleted. Its stage and its downloads go too,
    the stage made not whole first: never put in place over the old mesh at a later start, with
    an answer that would delete it. The overlays stay, which both meshes have. :class:`InstallError`
    where a program holds a folder open: nothing deleted then, an update waiting whole again."""
    folder = mesh_dir(root, region)
    real = _real(root, region)
    with _SWAP:
        stage = stage_dir(root, region)
        waiting = _read(stage / MARKER)
        (stage / MARKER).unlink(missing_ok=True)
        try:
            _swap_in(_kept_of(real), real, real.name, touch=DATED, again=DELETE_AGAIN)
        except InstallError:
            if waiting:  # refused, nothing deleted: the update waiting is whole again
                _write(stage / MARKER, waiting)
            raise
        _discard(stage)
    shutil.rmtree(download_dir(root, region), ignore_errors=True)
    _forget_list(root, folder, _kept_of(real))
    log(f"the new mesh of OrthoStudio XP's region {region} deleted, its old mesh back in its place")


def _forget_list(root: Path, *folders: Path) -> None:
    """The lists of textures Live kept for these folders (``names.region_list_file``) deleted:
    a folder deleted leaves none behind."""
    from orthostudio.live.names import region_list_file

    for folder in folders:
        region_list_file(lists_dir(root), folder).unlink(missing_ok=True)


def remove_kept(root: Path, region: str, *, wait: bool = False, log: Log = print) -> None:
    """A region's kept old mesh deleted from the disk (:func:`kept_dir`), alone: the region, its
    stage, its downloads and the overlays of the overlay pack stay, which its new mesh has too.
    Set aside first, in one rename (:func:`_set_aside_whole`, refused while a program holds it
    open: nothing deleted then; never taken for a kept mesh), then deleted in the background, or
    before returning with ``wait``: the room an update makes is counted once it is free
    (:meth:`Installer._make_room`). The caller says whether it may (``LiveControl._deleting``,
    ``Installer.may_swap``). The window's Delete on the kept mesh's line, and its « Delete the kept
    old one and install the new one »."""
    folder = kept_dir(root, region)
    with _SWAP:
        if not folder.exists():
            return
        aside = _set_aside_whole(folder)
    _forget_list(root, folder)
    if wait:
        _delete(aside)
    else:
        _delete_aside(aside)
    log(f"the old mesh of OrthoStudio XP's region {region} deleted")


@dataclass(frozen=True)
class Room:
    """A disk short of room for an install: a folder of it, what the install needs there
    (:data:`SPARE_BYTES` in), what is free."""

    path: Path
    need: int
    free: int


def _disk_of(path: Path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def _short_of_room(
    mesh: Sequence[Zip],
    ovl: Sequence[Zip],
    dest: Path,
    pack: Path,
    download: Path,
    whole: bool,
    freed: Mapping[int | None, int] | None = None,
) -> list[Room]:
    """Every disk short of room for an install, disk by disk: the region's files on its disk (the
    region of before stays there meanwhile), the overlays' on the overlay pack's, the largest zip
    on the download folder's, and the overlays of an update too, kept there until the region is in
    place; :data:`SPARE_BYTES` left on each. A folder not made yet counts on the disk of the
    nearest one that is; ``freed``, the bytes a disk gains first ({disk: bytes}). None short, or
    nothing to download: an empty list. The one rule of the install's check
    (:meth:`Installer._check_room`) and of the room it may make (:func:`_room_for_update`)."""
    todo = [*mesh, *ovl]
    if not todo:
        return []
    needs: dict[int | None, list] = {}

    def add(path: Path, n: int) -> None:
        while not path.exists() and path.parent != path:
            path = path.parent
        needs.setdefault(_disk_of(path), [path, 0])[1] += n

    add(dest, sum(z.unpacked for z in mesh))
    add(pack, sum(z.unpacked for z in ovl))
    add(download, max(z.size for z in todo) + (sum(z.size for z in ovl) if whole else 0))
    short = []
    for disk, (path, n) in needs.items():
        need = n + SPARE_BYTES
        free = shutil.disk_usage(path).free + (freed or {}).get(disk, 0)
        if free < need:
            short.append(Room(path, need, free))
    return short


def _downloads_kept(todo: Iterable[Zip], waiting: Mapping[str, str]) -> set[str]:
    """The files of a region's download folder an install keeps: its zips' (resumed), and the
    overlays of an update waiting for its region ({path: SHA-256})."""
    return {z.download for z in todo} | {_download_name(p, sha) for p, sha in waiting.items()}


def _stale_downloads(download: Path, kept: set[str]) -> list[Path]:
    """The files and folders of a download folder that are none of ``kept``
    (:func:`_downloads_kept`): parts of zips made again since, whose bytes would be taken for the
    beginning of the new one, and those of a Live of before."""
    with contextlib.suppress(OSError), os.scandir(download) as entries:
        return [Path(e.path) for e in entries if e.name not in kept]
    return []


@dataclass(frozen=True)
class Stale:
    """A part of a region's stages an install deletes (:func:`_stale_stages`): where it is set
    aside (:func:`_discard`), and the zips of the stage's record that go with it."""

    path: Path
    beside: Path | None = None
    zips: frozenset[str] = frozenset()


def _stale_stages(root: Path, region: OsxpRegion, rivers: bool) -> list[Stale]:
    """What of a region's stages an install deletes, before it counts its room: its stage where
    its mesh is the list's (none needed), or where it holds a zip the list no longer has as it was
    (:func:`_staged`: an update of before stopped or waiting, a newer one listed since), or only
    its river masks where the region does not keep them (:func:`keeps_rivers`: Even again before
    they were in place), their zips with them; the river masks coming alone, not the list's or
    half come and no longer wanted. Read before anything is deleted; deleted by
    :func:`_drop_stale`, counted by :func:`_room_for_update`."""
    keep = keeps_rivers(root, region.id, rivers)
    stage = stage_dir(root, region.id)
    out: list[Stale] = []
    if stage.exists():
        step = _staged(root, region)
        theirs = {z.path for z in region.rivers} & set(step["zips"])
        if not made_whole(root, region) or not (step["zips"] or step["overlays"]):
            out.append(Stale(stage))
        elif theirs and not keep:
            out.append(Stale(stage / RIVERS, stage.parent, frozenset(theirs)))
    small = mesh_dir(root, region.id) / RIVERS_STAGE
    if small.exists() and (not keep or not _rivers_staged(root, region)):
        out.append(Stale(small, _real(root, region.id).parent))
    return out


def _drop_stale(stale: Stale) -> None:
    """A stale part of a region's stages deleted before returning (:func:`_stale_stages`): a part
    of the stage takes its marker with it first (whole again only once all are there), and its
    zips leave the stage's record after."""
    if stale.zips:
        (stale.path.parent / MARKER).unlink(missing_ok=True)
    _discard(stale.path, stale.beside, wait=True)
    if stale.zips:
        stage = stale.path.parent
        step = _read(stage / STEP)
        step["zips"] = {p: h for p, h in (step.get("zips") or {}).items() if p not in stale.zips}
        _write(stage / STEP, step)


def _kept_frees(root: Path, region: str) -> int | None:
    """What deleting the region's kept old mesh frees on its disk (None: none kept): the size its
    marker gives, but nothing where Live's pack on that disk holds its files still, its DSFs and
    masks clones or hard links of them (``region.place_file``) until a start sets the pack up from
    another mesh (``region.holds_files_of``: the pack made from it, or from the region before the
    swap that kept it, with Live stopped and no start since). Deleted then, it would free nothing
    and could not be downloaded again. The one rule of the window's offer and of the install's
    deletion (:func:`_room_for_update`)."""
    from orthostudio.live.pack import pack_dir
    from orthostudio.live.region import holds_files_of

    kept = kept_mesh(root, region)
    if kept is None:
        return None
    pack = pack_dir(root)
    if _disk_of(pack) == _disk_of(kept) and holds_files_of(pack, kept):
        return 0
    return int(_read(kept / MARKER).get("size") or 0)


def _room_for_update(
    root: Path,
    region: OsxpRegion,
    rivers: bool,
    mesh: Sequence[Zip],
    ovl: Sequence[Zip],
    waiting: Mapping[str, str],
) -> dict | None:
    """What an update made whole beside the region lacks on the disk: the one count of the
    window's word before it (:func:`update_room`) and of the room the install makes
    (:meth:`Installer._make_room`), so that both agree. What the install deletes before it counts
    its room counts as freed: its stale stages (:func:`_stale_stages`) and downloads
    (:func:`_stale_downloads`), which it deletes first, before the next look. None where every
    disk it needs has room then; else what it needs and what is free on the first disk short,
    whether that disk is Live's folder's rather than the region's (``live``) and what is free
    there counts what the install deletes first (``cleared``), and whether deleting the region's
    kept old mesh makes every disk the install needs enough (``kept``), by what it frees
    (:func:`_kept_frees`), or would free after Live's next start only (``shared``)."""
    from orthostudio.clean import disk_bytes

    download = download_dir(root, region.id)
    stage = stage_dir(root, region.id)
    freed: dict[int | None, int] = {}
    stale = [item.path for item in _stale_stages(root, region, rivers)]
    stale += _stale_downloads(download, _downloads_kept([*mesh, *ovl], waiting))
    for path in stale:
        try:
            n = disk_bytes([path], links=False)
        except OSError:
            continue
        disk = _disk_of(path)
        freed[disk] = freed.get(disk, 0) + n
    short = _short_of_room(mesh, ovl, stage, overlay_pack(root), download, True, freed)
    if not short:
        return None
    first, disk = short[0], _disk_of(short[0].path)
    cleared = freed.get(disk, 0) > 0  # what the install deletes first: the window says it counts
    frees = _kept_frees(root, region.id)
    if frees:
        disk_kept = _disk_of(kept_dir(root, region.id))
        freed[disk_kept] = freed.get(disk_kept, 0) + frees
    enough = bool(frees) and not _short_of_room(
        mesh, ovl, stage, overlay_pack(root), download, True, freed
    )
    return {
        "need": first.need,
        "free": first.free,
        "kept": enough,
        # a kept mesh the pack holds still: it frees its room only after Live's next start
        "shared": frees == 0,
        # not the region's disk: Live's folder's, which holds its downloads and its overlays
        "live": disk != _disk_of(_real(root, region.id).parent),
        "cleared": cleared,
    }


def update_room(root: Path, region: OsxpRegion, overlays: bool, rivers: bool) -> dict | None:
    """The window's word as Update is clicked: for an installed region whose update makes it whole
    beside it (:func:`made_whole`), :func:`_room_for_update` for the zips it would download; None
    for any other (nothing of before beside it). ``overlays`` and ``rivers`` as the install's.
    Asked once at the click, never at each look of the page: a stale stage is read whole."""
    if _installed_zips(root, region.id) is None or not made_whole(root, region):
        return None
    mesh, ovl = to_fetch(root, region, overlays, keeps_rivers(root, region.id, rivers))
    return _room_for_update(root, region, rivers, mesh, ovl, _staged(root, region)["overlays"])


class Installer:
    """Downloads a region's zips (a few at a time) and unpacks each as soon as it is whole.
    :meth:`run` blocks; :meth:`cancel` stops it at the next megabyte, and the next install goes on
    from there. ``rivers``: the pilot chose soft banks, the region's river masks come with it
    (:func:`wanted`). ``night_only``: a region installed already given its night grids alone
    (:func:`night_to_fetch`), its mesh, its river masks and its record of the set-up left as they
    are. ``may_swap``, the caller's to say: whether a region installed already, or its river
    masks, may change now (Live stopped and X-Plane not running); else the whole stage waits for
    Live's next start (:func:`settle`), the install's phase ``waiting``. ``keep_old``: the
    pilot's answer, whether the region of before is kept at the swap (:func:`kept_dir`), written
    in the stage's record at each install, so that the swap, now or at a later start, honours the
    last one. ``make_room``: the window's « Delete the kept old one and install the new one »,
    where a disk lacks room (:meth:`_make_room`).

    Where the region's mesh does not change (:func:`made_whole`), what comes is added in place:
    the marker records it, the region's set-up and its list of textures stay as they were; river
    masks put in place change only the time of their folder, which the next start's set-up
    compares (``region._fingerprints``)."""

    def __init__(
        self,
        root: Path,
        region: OsxpRegion,
        *,
        overlays: bool = True,
        client=None,
        host: tuple[str, str] | None = None,
        log: Log = print,
        may_swap: Callable[[], bool],
        night_only: bool = False,
        rivers: bool = False,
        keep_old: bool = False,
        make_room: bool = False,
    ):
        self.root = root
        self.region = region
        self.with_overlays = overlays
        self.night_only = night_only
        self.rivers = rivers
        self.keep_old = keep_old
        self.make_room = make_room
        self.may_swap = may_swap
        self.host = host or shipped_host()
        self.client = client
        self.log = log
        self.stop = threading.Event()
        self.lock = threading.Lock()
        # the zips unpacked one at a time and in the list's order, whatever order they arrive in: a
        # mask two neighbouring zips both hold (a photo across two squares' border) is then the
        # same file on every computer, the later zip's (2026-10-19)
        self.turn = threading.Condition()
        self.next_up = 0
        self.progress = Progress(region.id)
        self.download = download_dir(root, region.id)
        self.waiting = False  # the region whole in its stage, waiting to take its place
        from orthostudio.live.streamer import Rate

        self.rate = Rate(window_s=5.0)
        self.clock = time.monotonic  # the pace's, a test's own

    def cancel(self) -> None:
        self.stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._pace()  # a download that stalls slows down on the page too
            return replace(self.progress).as_dict()

    def _set(self, **changes) -> None:
        with self.lock:
            for k, v in changes.items():
                setattr(self.progress, k, v)

    def _add(self, n: int) -> None:
        with self.lock:
            self.progress.done_bytes += n
            self._pace()

    def _pace(self) -> None:
        """``progress.mbs`` from the bytes downloaded so far, sampled at most twice a second; the
        lock held."""
        now = self.clock()
        if self.rate.samples and now - self.rate.samples[-1][0] < 0.5:
            return
        self.progress.mbs = max(0.0, self.rate.mbs(now, self.progress.done_bytes))

    def run(self) -> Progress:
        own = self.client is None
        if own:
            self.client = _client(self.host[1])
        try:
            self._run()
            self._set(phase="waiting" if self.waiting else "done")
        except CancelledError:
            self._set(phase="cancelled")
            self.log(f"{self._what()} stopped: it goes on from there the next time")
        except Exception as e:  # the page's words: a disk's refusal worded as everywhere (_why)
            said = _why(e) if isinstance(e, OSError) else e
            self._set(phase="failed", error=str(said) or type(e).__name__)
            self.log(f"{self._what()} failed: {e}")  # the cause itself in the log
        finally:
            if own:
                self.client.close()
        return self.progress

    def _what(self) -> str:
        if self.night_only:
            return f"the night lights' grids of {self.region.id}"
        return f"install of {self.region.id}"

    def _run(self) -> None:
        from orthostudio.live import winsearch

        if not self.host[0]:
            raise InstallError("this build of Live carries no address for its regions")
        winsearch.keep_out(self.root, self.log)  # Windows: the region's files too many to index
        region = self.region
        folder = mesh_dir(self.root, region.id)
        pack = overlay_pack(self.root)
        if not self.night_only:
            # what a stop left made right first, nothing put in place here: a whole stage waiting
            # takes its place at the end (_finish), with this install's answer (keep_old) and only
            # while each zip it holds is still the list's (_stale_stages); swapped here, with the
            # answer of before, it lost the mesh the pilot now keeps, or put in place a release
            # the list no longer has before its successor (the reviews of 2026-11-18)
            settle(self.root, self.log, swap=False, only=[region.id])
            _try_again(self.root, region.id)
            if self.may_swap():
                _unpack_waiting(self.root, region.id, self.log)  # a stop right after a swap
        whole = not self.night_only and made_whole(self.root, region)
        stage = stage_dir(self.root, region.id)
        step: dict[str, Any] = {"zips": {}, "overlays": {}}
        keep = keeps_rivers(self.root, region.id, self.rivers)  # before anything is deleted
        if self.night_only:
            mesh, ovl = night_to_fetch(self.root, region), []
        else:
            # its stale stages deleted before the next look, a minute or more, the room they leave
            # counted next (_room_for_update, the window's rule too); a Stop meanwhile honoured
            # after, before anything else is deleted
            stale = _stale_stages(self.root, region, self.rivers)
            if stale:
                self._set(phase="clearing")
            for item in stale:
                _drop_stale(item)
            if self.stop.is_set():
                raise CancelledError
            if whole:
                step = {**_staged(self.root, region), "keep_old": self.keep_old}  # the last
            mesh, ovl = to_fetch(self.root, region, self.with_overlays, keep)
        todo = [*mesh, *ovl]
        self._set(
            total_bytes=sum(z.size for z in todo),
            zips=len(todo),
            phase="downloading" if todo else "done",
        )
        # nothing to download but zips unpacked and not recorded yet (a whole stage waiting, river
        # masks whose swap was refused): only the end's work left
        left = [] if self.night_only else not_installed(self.root, region, keep)
        if not todo and not whole and not left:
            if not self.night_only:
                self.log(f"OrthoStudio XP's region {region.id} is up to date")
            return
        if todo:
            if whole:
                (stage / MARKER).unlink(missing_ok=True)  # whole again only once all are there
                stage.mkdir(parents=True, exist_ok=True)
            self._fetch_all(region, mesh, ovl, stage if whole else folder, pack, step, whole)
        self._finish(folder, stage, step, whole)

    def _fetch_all(
        self,
        region: OsxpRegion,
        mesh: list[Zip],
        ovl: list[Zip],
        dest: Path,
        pack: Path,
        step: dict[str, Any],
        whole: bool,
    ) -> None:
        """``mesh`` downloaded and unpacked into ``dest`` (the stage, recorded in its step, for a
        region made whole; else the region, its river masks into their own stage), ``ovl`` into
        the overlay pack, or kept downloaded in the stage's step until the region is in place: a
        few downloads at a time, the zips unpacked one at a time in the list's order."""
        todo = [*mesh, *ovl]
        folder = mesh_dir(self.root, region.id)
        small = folder / RIVERS_STAGE
        dest.mkdir(parents=True, exist_ok=True)
        pack.mkdir(parents=True, exist_ok=True)
        self.download.mkdir(parents=True, exist_ok=True)
        if not self.night_only:
            self._clear_download(_downloads_kept(todo, step["overlays"]))
        self._make_room(mesh, ovl, step, whole)
        self._check_room(mesh, ovl, dest, pack, whole)
        had = self._have_bytes(todo)
        with self.lock:  # what an earlier try downloaded counts whole, not in the pace
            self.progress.done_bytes += had
            self.rate.samples.clear()
        size = sum(z.size for z in todo) / 1e9
        rivers = [z for z in mesh if z in region.rivers]
        if self.night_only:
            self.log(
                f"the night lights' grids of OrthoStudio XP's region {region.id}: "
                f"{sum(z.size for z in todo) / 1e6:.1f} MB, into {dest.resolve()}"
            )
        else:
            if not whole:
                verb = "adding to"
            elif (folder / MARKER).exists():
                verb = "updating"
            else:
                verb = "installing"
            self.log(
                f"{verb} OrthoStudio XP's region {region.id}: {len(mesh) - len(rivers)} mesh "
                f"zips, {len(rivers)} river zips, {len(ovl)} overlay zips, {size:.1f} GB, into "
                f"{dest.resolve()}"
            )
        staged = {"zips": dict(_rivers_staged(self.root, region))}
        rank = {z.path: i for i, z in enumerate(todo)}

        def one(z: Zip) -> None:
            if self.stop.is_set():
                raise CancelledError
            path = self._fetch(z)
            with self.turn:
                while self.next_up != rank[z.path]:
                    if self.stop.is_set():
                        raise CancelledError
                    self.turn.wait(0.5)
                if self.stop.is_set():
                    raise CancelledError
                self._set(phase="unpacking")
                if z in ovl and whole:  # unpacked once the region is in place
                    step["overlays"][z.path] = z.sha256
                    _write(dest / STEP, step)
                elif z in ovl:
                    self._unpack(z, path, pack)
                    record = _read(pack / OVERLAY_RECORD)
                    record[z.path] = z.sha256
                    _write(pack / OVERLAY_RECORD, record)
                elif z in rivers and not whole:  # into their stage, as into the region
                    small.mkdir(exist_ok=True)
                    self._staged_unpack(z, path, small, staged)
                elif whole:
                    self._staged_unpack(z, path, dest, step)
                else:  # night grids into the region in place
                    self._unpack(z, path, dest)
                    step["zips"][z.path] = z.sha256
                with self.lock:
                    self.progress.unpacked += 1
                    self.progress.phase = "downloading"
                self.next_up += 1
                self.turn.notify_all()

        with ThreadPoolExecutor(max_workers=DOWNLOADS) as pool:
            futures = [pool.submit(one, z) for z in todo]
            errors: list[BaseException] = []
            for f in futures:
                try:
                    f.result()
                except BaseException as e:  # the others stop at their next megabyte
                    self.stop.set()
                    errors.append(e)
            # the cause, not the stops it brought about in the other downloads
            real = [e for e in errors if not isinstance(e, CancelledError)]
            if real or errors:
                raise (real or errors)[0]

    def _staged_unpack(self, z: Zip, path: Path, stage: Path, step: dict) -> None:
        """``z`` unpacked into a stage, named in the stage's step first as the zip being unpacked:
        a stop halfway, then the zip made again under the same path, starts the stage again
        rather than leave the files of its version of before among the new ones."""
        _write(stage / STEP, {**step, "unpacking": {z.path: z.sha256}})
        self._unpack(z, path, stage)
        step["zips"][z.path] = z.sha256
        _write(stage / STEP, step)

    def _unpack(self, z: Zip, path: Path, dest: Path) -> None:
        """``z``'s files into ``dest``, room for them checked first (:meth:`_room`), its zip deleted
        then; deleted too when it cannot be unpacked, so that the next try downloads it again
        instead of failing on it for ever. A full disk or a stop keeps it."""
        try:
            need = disk_need(path)
        except OSError:
            raise
        except Exception as e:
            raise self._damaged(z, path, e) from e
        self._room(z, need, dest)
        try:
            unpack(path, dest, self.stop)
        except (CancelledError, OSError):
            raise
        except Exception as e:
            raise self._damaged(z, path, e) from e
        path.unlink()

    def _damaged(self, z: Zip, path: Path, e: Exception) -> InstallError:
        path.unlink(missing_ok=True)
        return InstallError(
            f"{z.local} could not be unpacked ({e}): it is downloaded again the next time"
        )

    def _clear_download(self, keep: set[str]) -> None:
        """The download folder's stale files deleted (:func:`_stale_downloads`), before its room
        is counted."""
        for path in _stale_downloads(self.download, keep):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()

    def _check_room(
        self, mesh: list[Zip], ovl: list[Zip], dest: Path, pack: Path, whole: bool
    ) -> None:
        """Room for the whole install before its first byte, disk by disk
        (:func:`_short_of_room`), or :class:`InstallError` saying the first disk short."""
        short = _short_of_room(mesh, ovl, dest, pack, self.download, whole)
        if short:
            raise self._no_room(short[0], "while it installs")

    def _make_room(
        self, mesh: list[Zip], ovl: list[Zip], step: dict[str, Any], whole: bool
    ) -> None:
        """The pilot's « Delete the kept old one and install the new one » (``make_room``), checked
        again here by the window's own rule (:func:`_room_for_update`), never taken on trust: for
        an update made whole only, where a disk lacks room and deleting the region's kept old mesh
        makes every disk the install needs enough, and only with Live stopped and X-Plane not
        running (``may_swap``), the rule of every deletion of a region; deleted before the next
        look, not in the background, the room counted being the disk's (phase ``deleting``).
        Else nothing is deleted, and :meth:`_check_room` says the room it lacks; nor after the
        pilot's Stop."""
        if not self.make_room or not whole:
            return
        room = _room_for_update(self.root, self.region, self.rivers, mesh, ovl, step["overlays"])
        if room is None or not room["kept"]:
            return
        if not self.may_swap():
            raise InstallError(XPLANE_READS)
        if self.stop.is_set():
            raise CancelledError
        self._set(phase="deleting")
        remove_kept(self.root, self.region.id, wait=True, log=self.log)
        self._set(phase="downloading")

    def _room(self, z: Zip, files: int, dest: Path) -> None:
        """Room on the disk of ``dest`` for the zip's ``files`` bytes, each file a whole block at
        least (:func:`disk_need`), and SPARE_BYTES left: a full disk stops the install before the
        zip, never inside it. Checked at each zip, as the start's count reads only the list's bytes
        and the streamer may write meanwhile."""
        need = files + SPARE_BYTES
        free = shutil.disk_usage(dest.resolve()).free
        if free < need:
            raise self._no_room(Room(dest, need, free), "for its next part")

    def _no_room(self, room: Room, need_for: str) -> InstallError:
        """A disk short of room, said with the folder the pilot knows: Live's folder for one inside
        it, else the folder holding it (a region on another disk), never a hidden stage; the need
        (``need_for``) counts :data:`SPARE_BYTES` left free."""
        place = room.path.resolve()
        if place.is_relative_to(self.root.resolve()):
            place = self.root
        else:
            while place.name.startswith(".") and place.parent != place:
                place = place.parent
        return InstallError(
            f"not enough room on the disk of {place} ({room.need / 1e9:.1f} GB needed {need_for}, "
            f"{room.free / 1e9:.1f} GB free): free some room"
        )

    def _have_bytes(self, todo: Sequence[Zip]) -> int:
        total = 0
        for z in todo:
            with contextlib.suppress(OSError):
                total += min(z.size, (self.download / z.download).stat().st_size)
        return total

    def _fetch(self, z: Zip) -> Path:
        """``z`` whole in the download folder, resumed where an earlier try stopped, checked."""
        import httpx

        path = self.download / z.download
        for attempt in range(ATTEMPTS):
            have = path.stat().st_size if path.exists() else 0
            if have > z.size:
                path.unlink()
                self._add(-have)
                have = 0
            if have == z.size:
                break
            try:
                headers = {"Range": f"bytes={have}-"} if have else {}
                with self.client.stream("GET", f"{self.host[0]}/{z.path}", headers=headers) as r:
                    if r.status_code == 200 and have:  # the range was not honoured
                        self._add(-have)
                        have = 0
                    elif r.status_code == 401:
                        raise InstallError("the host refused Live's key: a newer Live has another")
                    elif r.status_code in PASSING:
                        r.raise_for_status()  # an httpx.HTTPError: tried again below
                    elif r.status_code not in (200, 206):
                        raise InstallError(f"{z.local}: {_answered(r.status_code)}")
                    with open(path, "ab" if have else "wb") as f:
                        for chunk in r.iter_bytes():  # as it comes: a cut loses nothing
                            if self.stop.is_set():
                                raise CancelledError
                            f.write(chunk)
                            self._add(len(chunk))
            except (httpx.HTTPError, OSError) as e:
                # the line, or on Windows a file held a moment (fsutil's rule): tried again; the
                # disk otherwise would refuse again
                again = isinstance(e, httpx.HTTPError) or (
                    WINDOWS and isinstance(e, PermissionError)
                )
                if not again or attempt == ATTEMPTS - 1:
                    raise InstallError(f"{z.local}: {_why(e)}") from e
                _pause(attempt)
        size = path.stat().st_size
        if size != z.size:
            raise InstallError(f"{z.local} arrived incomplete ({size} of {z.size} bytes)")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(CHUNK):
                h.update(block)
        if h.hexdigest() != z.sha256:
            path.unlink()
            self._add(-size)
            raise InstallError(f"{z.local} arrived damaged: it is downloaded again the next time")
        return path

    def _rivers_done(self, folder: Path) -> None:
        """The river stage marked whole once it holds every river zip of the list: its zips, and
        what they add to the region's size (:func:`_size`), which :func:`put_rivers_in_place`
        adds to the size its marker has then, night grids recorded meanwhile included."""
        stage = folder / RIVERS_STAGE
        listed = {z.path: z.sha256 for z in self.region.rivers}
        if (stage / MARKER).is_file() or not listed:
            return
        if _rivers_staged(self.root, self.region) != listed:
            return
        have = _read(folder / MARKER).get("zips") or {}
        adds = _size(self.region, {**have, **listed}) - _size(self.region, have)
        _write(stage / MARKER, {"zips": listed, "adds": adds})

    def _finish(self, folder: Path, stage: Path, step: dict[str, Any], whole: bool) -> None:
        """Every zip unpacked. Night grids or overlays added to a region whose mesh did not change:
        the marker records them, nothing else of the region changes. River masks added alone:
        their stage marked whole (:meth:`_rivers_done`) and put in place by the rule of a region
        (:func:`put_rivers_in_place`): now with Live stopped and X-Plane not running, else at Live's
        next start with X-Plane not running; ``night_only`` leaves them alone. A region made whole:
        its record written with the pilot's last answer (``keep_old``), its marker, which makes Live
        use it, written in its stage, the stage put in the region's place (:func:`put_in_place`),
        now or, for a region installed already while Live streams or X-Plane runs, at Live's next
        start with X-Plane not running."""
        region = self.region
        if not whole:
            if step["zips"]:
                marker = _read(folder / MARKER)
                zips = {**(marker.get("zips") or {}), **step["zips"]}
                _write(folder / MARKER, {**marker, "zips": zips, "size": _size(region, zips)})
            put = False
            if not self.night_only:
                self._rivers_done(folder)
                if _rivers_waiting(self.root, region.id):
                    put = self.may_swap() and put_rivers_in_place(self.root, region.id)
                    self.waiting = not put
            if self.night_only:
                # its zips deleted as unpacked; an update the pilot stopped keeps what it
                # downloaded there to go on from it (the review of 2026-11-11)
                with contextlib.suppress(OSError):
                    self.download.rmdir()
                self.log(f"OrthoStudio XP's region {region.id}: its night lights' grids installed")
                return
            shutil.rmtree(self.download, ignore_errors=True)
            if put:
                self.log(
                    f"OrthoStudio XP's region {region.id}: its river masks installed, used from "
                    "Live's next start"
                )
            elif self.waiting:
                self.log(
                    f"OrthoStudio XP's region {region.id}: its river masks are whole and wait; "
                    "they take their place at Live's next start with X-Plane not running"
                )
            else:
                self.log(f"OrthoStudio XP's region {region.id} updated")
            return
        update = (folder / MARKER).is_file()
        marker: dict[str, Any] = {
            "id": region.id,
            "release": region.release,
            "installed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": "osxp",
            "size": _size(region, step["zips"]),
            "zips": step["zips"],
        }
        if step["overlays"]:
            marker["overlays_waiting"] = step["overlays"]
        _write(stage / STEP, step)  # the answer of this install, a whole stage's too
        if not (stage / MARKER).is_file():
            _write(stage / MARKER, marker)
        if update and not self.may_swap():
            self.waiting = True
            self.log(
                f"OrthoStudio XP's region {region.id}: its update is whole and waits, the region "
                "of before in use; it takes its place at Live's next start with X-Plane not running"
            )
            return
        put_in_place(self.root, region.id, self.log)
        shutil.rmtree(self.download, ignore_errors=True)
        self.log(f"OrthoStudio XP's region {region.id} {'updated' if update else 'installed'}")
        from orthostudio.live.names import one_region, region_list_file

        lists = lists_dir(self.root)
        t0 = time.monotonic()
        try:
            region_list_file(lists, folder).unlink(missing_ok=True)
            one = one_region(folder, lists)
        except OSError as e:  # installed all the same: a start lists it
            self.log(f"  its textures not listed: {e}")
            return
        self.log(f"  its {one.count} textures listed in {time.monotonic() - t0:.1f} s")
