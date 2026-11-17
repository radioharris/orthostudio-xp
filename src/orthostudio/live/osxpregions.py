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

A region's river masks are the masks of its textures whose water is only rivers and lakes, which
Live reads only when the pilot chose soft banks (``LiveSettings.rivers_fade``): Europe's about
3.2 GB of its 14.6, so they come only with soft banks, or once the region has them. Chosen while
the region's mesh is the list's, they come alone (:func:`rivers_to_add`), unpacked into
``.rivers.new`` in the region's folder and put in place as ``rivers`` with one rename once every
one is there, Live streaming or not, used from its next start; once there they stay, whatever the
setting, and an update brings their new version with the region. Night grids added to a region
whose mesh did not change go into it in place, as nothing X-Plane reads changes.
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
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any

from orthostudio.fsutil import atomic_write_text, fsync_dir
from orthostudio.live.aoregions import (
    MARKER,
    CancelledError,
    InstallError,
    Progress,
    _pause,
    lists_dir,
    regions_dir,
)
from orthostudio.live.filesystem import is_folder_link, link_target, remove_folder_link
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
STEP = ".live-install.json"  # in a stage: the zips unpacked so far, the overlays waiting
CHUNK = 1 << 20
ATTEMPTS = 5
PASSING = frozenset({429, 500, 502, 503, 504})
"""The host's answers that pass (busy, or a fault of its own): tried again as a cut line is. Until
2026-11-14 a single 500 stopped the whole install, the pilot left to click Install again."""
DOWNLOADS = 3  # at a time: one stream from the host is not the pilot's whole line
SPARE_BYTES = 2 * 10**9
RIVERS = "rivers"  # a region's river masks, rivers/textures (orthostudio.live.names.RIVER_MASKS)
RIVERS_STAGE = ".rivers.new"  # in the region's folder: river masks added alone, until all are there
RIVERS_OLD = ".rivers.old"  # in the region's folder: those of before, for the moment of the swap
BLOCK = 4096
"""What a file takes on the disk at least, rounded up to it: APFS's, NTFS's and ext4's block. A
river zip's masks average 10 KB (3,732 in 37 MB over Geneva and Vancouver Island, 2026-11-17):
counted at their bytes, Europe's 228,000 would take about half a block each more than said, some
0.45 GB (an estimate)."""

_SWAP = threading.Lock()  # one rename of a region at a time: an install's and a start's

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
        except (OSError, ValueError, TypeError, httpx.HTTPError) as e:
            if not path.exists():
                raise CatalogError(f"the list of OrthoStudio XP's regions did not come: {e}") from e
    if not path.exists():
        raise CatalogError("this build of Live carries no address for its regions")
    try:
        return parse_manifest(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CatalogError(f"the list of regions kept in {path} is unreadable") from e


def mesh_dir(root: Path, region: str) -> Path:
    return regions_dir(root) / f"z_osxp_{region}"


def overlay_pack(root: Path) -> Path:
    return regions_dir(root) / OVERLAY_PACK


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


def present(root: Path, region: str) -> bool:
    """Whether anything of a region is on the disk: the region, or only its stage or the region of
    before that a stop left beside it."""
    real = _real(root, region)
    return any(
        os.path.lexists(p)
        for p in (mesh_dir(root, region), real, stage_dir(root, region), _old_dir(real))
    )


def staged_whole(root: Path, region: str) -> bool:
    """Whether the region's stage is whole, its marker written: it waits to take the region's
    place (:func:`put_in_place`)."""
    return (stage_dir(root, region) / MARKER).is_file()


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


def wanted(root: Path, region: OsxpRegion, rivers: bool) -> tuple[Zip, ...]:
    """The zips a region wants in its folder: its mesh, its night grids, and its river masks when
    the pilot chose soft banks (``rivers``, ``LiveSettings.rivers_fade``) or the region has them
    already. Once downloaded they are part of the region: going back to even rivers and lakes
    leaves them there, unused, so that soft banks chosen again downloads nothing, and an update of
    the region brings their new version with it, whatever the setting. The one rule of what an
    install, an update, the sizes and the update counts take."""
    keep = rivers or has_rivers(root, region.id)
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
    is still one of the list as it was; else nothing, the stage to be started again: a zip the
    list no longer has, or made again since, would leave its files in the region."""
    step = _read(stage_dir(root, region.id) / STEP)
    zips, waiting = dict(step.get("zips") or {}), dict(step.get("overlays") or {})
    listed = {z.path: z.sha256 for z in (*region.mesh, *region.night, *region.rivers)}
    overlays = {z.path: z.sha256 for z in region.overlays}
    if all(listed.get(p) == s for p, s in zips.items()) and all(
        overlays.get(p) == s for p, s in waiting.items()
    ):
        return {"zips": zips, "overlays": waiting}
    return {"zips": {}, "overlays": {}}


def _rivers_staged(root: Path, region: OsxpRegion) -> dict[str, str]:
    """The river zips a stopped download unpacked into the region's river stage, {path: SHA-256},
    when the stage still holds them and each is the list's as it was; else none, the stage to be
    started again: a zip made again since would leave masks of before among the new ones."""
    stage = mesh_dir(root, region.id) / RIVERS_STAGE
    if not (stage / RIVERS).is_dir():
        return {}
    step = _read(stage / STEP).get("zips") or {}
    listed = {z.path: z.sha256 for z in region.rivers}
    return step if all(listed.get(p) == s for p, s in step.items()) else {}


def not_installed(root: Path, region: OsxpRegion, rivers: bool) -> list[Zip]:
    """The zips the region wants (:func:`wanted`) that its marker does not record as installed:
    downloaded already or not (a whole stage waiting to take its place), an update of it has them
    to bring."""
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


def _rename(src: Path, dst: Path, what: str) -> None:
    """One folder renamed (tried again a moment on Windows, where a folder a program holds open
    cannot be), or :class:`InstallError` saying what holds it."""
    try:
        replace_file(src, dst)
    except OSError as e:
        raise InstallError(
            f"{what} is in use, by X-Plane perhaps: close it, then try again, which downloads "
            f"nothing more ({e})"
        ) from e


def put_in_place(root: Path, region: str, log: Log = print) -> bool:
    """A whole stage (its marker written) put in the region's place with one rename, the region of
    before renamed aside first and deleted after: a start finds the one or the other, whole. Then
    the overlays that waited with it unpacked (:func:`_unpack_waiting`). False, nothing done, where
    no whole stage waits. The caller says whether it may (Live stopped, X-Plane not running, or a
    region not installed yet)."""
    with _SWAP:
        stage = stage_dir(root, region)
        if not (stage / MARKER).is_file():
            return False
        real = _real(root, region)
        old = _old_dir(real)
        _discard(old)  # one an earlier swap could not delete
        if real.exists():
            _rename(real, old, real.name)
        try:
            _rename(stage, real, real.name)
        except InstallError:
            if old.exists() and not real.exists():
                replace_file(old, real)  # the region of before back, as it was
            raise
        fsync_dir(real.parent)
        (real / STEP).unlink(missing_ok=True)
        _discard(old)
    now = time.time()
    for sub in ("Earth nav data", "terrain", "terrain-lists"):  # what the set-up compares
        with contextlib.suppress(OSError):
            os.utime(real / sub, (now, now))
    _unpack_waiting(root, region, log)
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
    download = regions_dir(root) / ".download" / f"osxp_{region}"
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


def _discard(old: Path) -> None:
    """A region of before deleted in the background, renamed first to a name of its own
    (``.z_osxp_<id>.old.<n>``): its hundreds of thousands of files take a minute or more, which no
    start waits for, and a later update's ``.old`` never meets it. A deletion a quit stopped is
    taken up again by :func:`settle`."""
    if not old.exists():
        return
    gone = old.with_name(f"{old.name}.{time.time_ns()}")
    try:
        replace_file(old, gone)
    except OSError:  # held open a moment: deleted where it is, as before
        shutil.rmtree(old, ignore_errors=True)
        return
    _delete_aside(gone)


def _delete_aside(folder: Path) -> None:
    threading.Thread(
        target=shutil.rmtree, args=(folder,), kwargs={"ignore_errors": True}, daemon=True
    ).start()


def _region_ids(root: Path) -> set[str]:
    """Every region something of which is in Live's ``regions`` folder: its folder or link, its
    stage or the region of before (those of a region on another disk are beside it there)."""
    ids = set()
    with contextlib.suppress(OSError):
        for entry in os.scandir(regions_dir(root)):
            m = re.fullmatch(r"\.?z_osxp_(.+?)(\.new|\.old(?:\.\d+)?)?", entry.name)
            if m and (entry.name.startswith("z_") or m.group(2)):
                ids.add(m.group(1))
    return ids


def settle(root: Path, log: Log = print, *, swap: bool, only: Iterable[str] | None = None) -> None:
    """What a stop or a power cut left, made right: a region renamed aside whose successor never
    took its place put back; a region of before left beside its successor deleted; with ``swap``
    (Live stopped and X-Plane not running), each whole stage put in its region's place and the
    overlays that waited with it unpacked. At an install's start and at Live's, before anything
    reads the regions."""
    for region in sorted(only if only is not None else _region_ids(root)):
        real = _real(root, region)
        old = _old_dir(real)
        with _SWAP:
            for left in real.parent.glob(f"{old.name}.*"):  # deletions a quit stopped
                if left.name.removeprefix(f"{old.name}.").isdigit():
                    _delete_aside(left)
            if not real.exists() and old.exists():
                try:
                    replace_file(old, real)
                    log(f"OrthoStudio XP's region {region}: put back as it was before its update")
                except OSError as e:
                    log(f"OrthoStudio XP's region {region} could not be put back: {e}")
                    continue
            elif real.exists() and old.exists():
                _discard(old)
        if not swap:
            continue
        try:
            if put_in_place(root, region, log):
                log(f"OrthoStudio XP's region {region}: its update put in place")
            else:
                _unpack_waiting(root, region, log)  # a stop after it took its place
        except (InstallError, OSError) as e:
            log(f"OrthoStudio XP's region {region}: its update waits ({e})")


def remove(root: Path, region: str, squares: Sequence[tuple[int, int]], log: Log = print) -> None:
    """A region deleted from the disk (the folder a link leads to too, a link or a junction), its
    overlays out of the overlay pack, and what an install of it left behind: its stage, the region
    of before, its downloads. Renamed aside first, in one rename (refused while a program holds it
    open: nothing deleted then), then deleted: never half a region left that a start would take.
    A region of which only a stage or the region of before is left is deleted too."""
    folder = mesh_dir(root, region)
    real = _real(root, region)
    old, stage = _old_dir(real), stage_dir(root, region)
    with _SWAP:
        shutil.rmtree(old, ignore_errors=True)
        if real.exists():
            _rename(real, old, real.name)
        if is_folder_link(folder):
            remove_folder_link(folder)
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(stage, ignore_errors=True)
        for left in real.parent.glob(f"{old.name}.*"):  # regions of before still being deleted
            shutil.rmtree(left, ignore_errors=True)
    shutil.rmtree(regions_dir(root) / ".download" / f"osxp_{region}", ignore_errors=True)
    pack = overlay_pack(root)
    gone = 0
    for lat, lon in squares:
        tens = f"{lat // 10 * 10:+03d}{lon // 10 * 10:+04d}"
        dsf = pack / "Earth nav data" / tens / f"{lat:+03d}{lon:+04d}.dsf"
        with contextlib.suppress(FileNotFoundError):
            dsf.unlink()
            gone += 1
    record = _read(pack / OVERLAY_RECORD)
    if record:
        kept = {k: v for k, v in record.items() if not k.startswith(f"{region}/")}
        _write(pack / OVERLAY_RECORD, kept)
    from orthostudio.live.names import region_list_file

    region_list_file(lists_dir(root), folder).unlink(missing_ok=True)
    log(f"OrthoStudio XP's region {region} deleted, and {gone} of its overlays")


def _xplane_not_running() -> bool:
    from orthostudio.live.scenery import xplane_running

    return not xplane_running()


class Installer:
    """Downloads a region's zips (a few at a time) and unpacks each as soon as it is whole.
    :meth:`run` blocks; :meth:`cancel` stops it at the next megabyte, and the next install goes on
    from there. ``rivers``: the pilot chose soft banks, the region's river masks come with it
    (:func:`wanted`). ``night_only``: a region installed already given its night grids alone
    (:func:`night_to_fetch`), its mesh, its river masks and its record of the set-up left as they
    are. ``may_swap``: whether a region installed already may change now (Live stopped and X-Plane
    not running); else its whole stage waits for Live's next start (:func:`settle`), the install's
    phase ``waiting``.

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
        night_only: bool = False,
        rivers: bool = False,
        may_swap: Callable[[], bool] | None = None,
    ):
        self.root = root
        self.region = region
        self.with_overlays = overlays
        self.night_only = night_only
        self.rivers = rivers
        self.may_swap = may_swap or _xplane_not_running
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
        self.download = regions_dir(root) / ".download" / f"osxp_{region.id}"
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
        except Exception as e:
            self._set(phase="failed", error=str(e) or type(e).__name__)
            self.log(f"{self._what()} failed: {e}")
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
        if not self.night_only:  # what a stop left first, a whole stage put in place if it may
            settle(self.root, self.log, swap=self.may_swap(), only=[region.id])
        whole = not self.night_only and made_whole(self.root, region)
        stage = stage_dir(self.root, region.id)
        step: dict[str, dict[str, str]] = {"zips": {}, "overlays": {}}
        if self.night_only:
            mesh, ovl = night_to_fetch(self.root, region), []
        else:
            if whole:
                step = self._stage_kept(stage)
                # the stage brings the river masks: those coming alone into the region of before
                # are no use any more
                shutil.rmtree(folder / RIVERS_STAGE, ignore_errors=True)
            else:
                shutil.rmtree(stage, ignore_errors=True)  # the region is the list's: none needed
                keep = self.rivers or has_rivers(self.root, region.id)
                if not keep or not _rivers_staged(self.root, region):  # even again, or of before
                    shutil.rmtree(folder / RIVERS_STAGE, ignore_errors=True)
            mesh, ovl = to_fetch(self.root, region, self.with_overlays, self.rivers)
        todo = [*mesh, *ovl]
        self._set(
            total_bytes=sum(z.size for z in todo),
            zips=len(todo),
            phase="downloading" if todo else "done",
        )
        # nothing to download but zips unpacked and not recorded yet (a whole stage waiting, river
        # masks whose swap was refused): only the end's work left
        left = [] if self.night_only else not_installed(self.root, region, self.rivers)
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

    def _stage_kept(self, stage: Path) -> dict[str, dict[str, str]]:
        """What of the region's stage goes on: everything, while each zip it holds is the list's
        as it was, but its river masks where the region does not want them (even again before
        they were in place); else nothing, the stage deleted."""
        region = self.region
        step = _staged(self.root, region)
        if not step["zips"] and not step["overlays"]:
            shutil.rmtree(stage, ignore_errors=True)
            return step
        rivers = {z.path for z in region.rivers}
        if rivers & set(step["zips"]) and not (self.rivers or has_rivers(self.root, region.id)):
            (stage / MARKER).unlink(missing_ok=True)
            shutil.rmtree(stage / RIVERS, ignore_errors=True)
            step["zips"] = {p: s for p, s in step["zips"].items() if p not in rivers}
            _write(stage / STEP, step)
        return step

    def _fetch_all(
        self,
        region: OsxpRegion,
        mesh: list[Zip],
        ovl: list[Zip],
        dest: Path,
        pack: Path,
        step: dict[str, dict[str, str]],
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
            self._clear_download(
                {z.download for z in todo}
                | {_download_name(p, s) for p, s in step["overlays"].items()}
            )
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
                    self._unpack(z, path, small)
                    staged["zips"][z.path] = z.sha256
                    _write(small / STEP, staged)
                else:
                    self._unpack(z, path, dest)
                    step["zips"][z.path] = z.sha256
                    if whole:
                        _write(dest / STEP, step)
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
        """The download folder's files that are none of ``keep``: parts of zips made again since,
        whose bytes would be taken for the beginning of the new one, and those of a Live of
        before."""
        with contextlib.suppress(OSError):
            for entry in os.scandir(self.download):
                if entry.name in keep:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(entry.path)

    def _check_room(
        self, mesh: list[Zip], ovl: list[Zip], dest: Path, pack: Path, whole: bool
    ) -> None:
        """Room for the whole install before its first byte, disk by disk: the region's files on
        its disk (the region of before stays there meanwhile), the overlays' on the overlay pack's,
        the largest zip on the download folder's, and the overlays of an update too, kept there
        until the region is in place; :data:`SPARE_BYTES` left on each."""
        todo = [*mesh, *ovl]
        needs: dict[int, list] = {}

        def add(path: Path, n: int) -> None:
            needs.setdefault(os.stat(path).st_dev, [path, 0])[1] += n

        add(dest, sum(z.unpacked for z in mesh))
        add(pack, sum(z.unpacked for z in ovl))
        add(self.download, max(z.size for z in todo) + (sum(z.size for z in ovl) if whole else 0))
        for path, n in needs.values():
            need, free = n + SPARE_BYTES, shutil.disk_usage(path).free
            if free < need:
                raise InstallError(
                    f"{need / 1e9:.0f} GB needed on the disk of {path.resolve()}, "
                    f"{free / 1e9:.0f} GB free"
                )

    def _room(self, z: Zip, files: int, dest: Path) -> None:
        """Room on the disk of ``dest`` for the zip's ``files`` bytes, each file a whole block at
        least (:func:`disk_need`), and SPARE_BYTES left: a full disk stops the install before the
        zip, never inside it. Checked at each zip, as the start's count reads only the list's bytes
        and the streamer may write meanwhile."""
        need = files + SPARE_BYTES
        free = shutil.disk_usage(dest.resolve()).free
        if free < need:
            raise InstallError(
                f"{need / 1e9:.1f} GB needed on the disk of {dest.resolve()} for {z.local}, "
                f"{free / 1e9:.1f} GB free"
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
                        raise InstallError(f"{z.local}: HTTP {r.status_code}")
                    with open(path, "ab" if have else "wb") as f:
                        for chunk in r.iter_bytes():  # as it comes: a cut loses nothing
                            if self.stop.is_set():
                                raise CancelledError
                            f.write(chunk)
                            self._add(len(chunk))
            except (httpx.HTTPError, OSError) as e:
                if attempt == ATTEMPTS - 1:
                    # the status alone: the host's address stays out of the window and the logs
                    said = (
                        f"HTTP {e.response.status_code}"
                        if isinstance(e, httpx.HTTPStatusError)
                        else e
                    )
                    raise InstallError(f"{z.local}: {said}") from e
                _pause(attempt)
        size = path.stat().st_size
        if size != z.size:
            raise InstallError(f"{z.local}: {size} bytes, not {z.size}")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(CHUNK):
                h.update(block)
        if h.hexdigest() != z.sha256:
            path.unlink()
            self._add(-size)
            raise InstallError(f"{z.local} came damaged (its SHA-256 differs): install again")
        return path

    def _rivers_whole(self) -> bool:
        """Whether the river stage holds every river zip of the list: what to put in place."""
        listed = {z.path: z.sha256 for z in self.region.rivers}
        return bool(listed) and _rivers_staged(self.root, self.region) == listed

    def _put_rivers(self, folder: Path) -> dict[str, str]:
        """The river masks of their stage put in place with one rename once it holds every river
        zip of the list, those of before renamed aside first and deleted after: a start finds the
        ones or the others, whole. Returns the zips put in place, {path: SHA-256}, which the marker
        then records; none while the stage is not whole (a stop), kept for the next time. A folder
        a program holds open is not renamed: those of before stay as they were, the stage whole
        for the next try, which only puts it in place."""
        if not self._rivers_whole():
            return {}
        staged = _rivers_staged(self.root, self.region)
        real, old = folder / RIVERS, folder / RIVERS_OLD
        shutil.rmtree(old, ignore_errors=True)  # one an earlier swap could not delete
        what = f"the river masks of {self.region.id}"
        if real.exists():
            _rename(real, old, what)
        try:
            _rename(folder / RIVERS_STAGE / RIVERS, real, what)
        except InstallError:
            if old.exists() and not real.exists():
                replace_file(old, real)
            raise
        now = time.time()
        with contextlib.suppress(OSError):  # what the next start's set-up compares
            os.utime(real, (now, now))
        shutil.rmtree(old, ignore_errors=True)
        return staged

    def _finish(
        self, folder: Path, stage: Path, step: dict[str, dict[str, str]], whole: bool
    ) -> None:
        """Every zip unpacked. Night grids, river masks or overlays added to a region whose mesh did
        not change: the river masks put in place whole (:meth:`_put_rivers`), then the marker
        records them, nothing else of the region changes; ``night_only`` leaves the river masks
        alone. A region made whole: its marker, which makes Live use it, written in its stage, the
        stage put in the region's place (:func:`put_in_place`), now or, for a region installed
        already while Live streams or X-Plane runs, at Live's next start with X-Plane not
        running."""
        region = self.region
        sizes = {z.path: z.unpacked for z in (*region.mesh, *region.night, *region.rivers)}
        if not whole:
            marker = _read(folder / MARKER)
            put = {} if self.night_only else self._put_rivers(folder)
            zips = {**(marker.get("zips") or {}), **step["zips"], **put}
            size = sum(sizes.get(p, 0) for p in zips)
            _write(folder / MARKER, {**marker, "zips": zips, "size": size})
            if put:
                shutil.rmtree(folder / RIVERS_STAGE, ignore_errors=True)
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
            else:
                self.log(f"OrthoStudio XP's region {region.id} updated")
            return
        update = (folder / MARKER).is_file()
        marker: dict[str, Any] = {
            "id": region.id,
            "release": region.release,
            "installed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": "osxp",
            "size": sum(sizes.get(p, 0) for p in step["zips"]),
            "zips": step["zips"],
        }
        if step["overlays"]:
            marker["overlays_waiting"] = step["overlays"]
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
