# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP's own regions, downloaded and installed by Live.

The mesh of the world and X-Plane's roads, forests and buildings cut for each of its squares are
zips on a server that asks for Live's token. Its
``manifest.json`` lists, for each region, its mesh zips and its
overlay zips, one of each per column of squares, with their size, their SHA-256 and the bytes they
take unpacked. The address and the token are written into ``host.json`` beside this module when
the app is built, never committed (as the OSM library's, ``orthostudio.sources.library``).

A region goes to ``regions/z_osxp_<id>`` in Live's folder (a link to a folder on another disk
too), its overlays into the one overlay pack, ``regions/y_osxp_overlays``, unless the pilot said
"Never" to them. Each zip is downloaded into ``regions/.download/osxp_<id>`` (resumed with an
HTTP range after a cut), checked (its size and SHA-256), unpacked file by file, each file in place
whole or not at all, and deleted. The zips are unpacked in the list's order whatever order they
arrive in: a mask two neighbouring zips both hold is the same file on every computer. The
region's marker, which makes Live use it, is written last: until then a start does not see it.
The marker keeps every zip's SHA-256: once a zip of the bucket is made again (a mistake mended),
only the zips whose SHA-256 changed are downloaded again.

A region's river masks (``rivers`` in the manifest, 2026-11-17) are the masks of its textures whose
water is only rivers and lakes, which Live reads only when the pilot chose soft banks
(``LiveSettings.rivers_fade``): Europe's about 3.2 GB of its 14.6, some 228,000 files, so they come
only with soft banks, or once the region has them (:func:`wanted`). They are unpacked into a stage
beside them, ``.rivers.new`` in the region's folder, and put in place as ``rivers`` with one rename
once every one is there, then recorded in the marker: a stop or a full disk on the way leaves the
region's rivers as they were, never half of them faded. Chosen while the region's mesh is the
list's, they come alone, in place, the rest of the region untouched (:func:`rivers_to_add`); once
there they stay, whatever the setting, and an update brings their new version with the region.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import shutil
import threading
import time
import zipfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any

from orthostudio.live.aoregions import (
    MARKER,
    CancelledError,
    InstallError,
    Progress,
    _pause,
    lists_dir,
    regions_dir,
)
from orthostudio.live.filesystem import replace as replace_file

HOST_FILE = "host.json"
CATALOG_MAX_AGE_S = 3600.0
OVERLAY_PACK = "y_osxp_overlays"
OVERLAY_RECORD = ".live-overlays.json"  # in the overlay pack: {zip path: SHA-256}
STEP = ".live-install.json"  # in a region being installed: the zips unpacked so far
CHUNK = 1 << 20
ATTEMPTS = 5
PASSING = frozenset({429, 500, 502, 503, 504})
"""The host's answers that pass (busy, or a fault of its own): tried again as a cut line is. Until
2026-11-14 a single 500 stopped the whole install, the pilot left to click Install again."""
DOWNLOADS = 3  # at a time: one stream from the host is not the pilot's whole line
SPARE_BYTES = 2 * 10**9
RIVERS = "rivers"  # a region's river masks, rivers/textures (orthostudio.live.names.RIVER_MASKS)
RIVERS_STAGE = ".rivers.new"  # in the region's folder: its river masks until all are there
RIVERS_OLD = ".rivers.old"  # in the region's folder: those of before, for the moment of the swap
BLOCK = 4096
"""What a file takes on the disk at least, rounded up to it: APFS's, NTFS's and ext4's block. A
river zip's masks average 10 KB (3,732 in 37 MB over Geneva and Vancouver Island, 2026-11-17):
counted at their bytes, Europe's 228,000 would take about half a block each more than said, some
0.45 GB (an estimate)."""

Log = Callable[[str], None]


class CatalogError(RuntimeError):
    """The list of OrthoStudio XP's regions could not be read, and none was kept from before."""


@dataclass(frozen=True)
class Zip:
    path: str  # in the bucket: eur/zips/z_osxp_eur_+30+016.zip
    size: int
    sha256: str
    unpacked: int

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def local(self) -> str:
        """Its path in the bucket below its region (``zips/rivers/z_osxp_eur_+30+016.zip``), where
        it is downloaded and how the errors name it: a river zip has its mesh zip's name, beside
        it (2026-11-17), and two kinds' zips of one name never meet."""
        return self.path.split("/", 1)[-1]


@dataclass(frozen=True)
class OsxpRegion:
    id: str
    release: str  # when the manifest was made
    mesh: tuple[Zip, ...]
    overlays: tuple[Zip, ...] = ()
    # the night lights' density grids of its squares (orthostudio.live.nightwork), unpacked into
    # the region's folder as its mesh zips are: ``night/<square>.png``
    night: tuple[Zip, ...] = ()
    # its river masks, one zip beside each mesh zip that has any: ``rivers/textures/<mask>``, put
    # in place whole (the module's docstring); which of them a region wants: :func:`wanted`
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
    know is left aside, as every Live since 2.0 does (``rivers``, 2026-11-17). A zip entry has
    its four fields and no other: ``Zip(**z)`` refuses one more, and a Live refusing the list keeps
    the one it had for good. A kind added never adds a field to the zips of another."""
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
    """OrthoStudio XP's regions: from the host's manifest at most every hour, else the list kept in
    Live's folder, which also stands in when the host does not answer."""
    import httpx

    url, token = host or shipped_host()
    path = regions_dir(root) / "osxp-catalog.json"
    try:
        fresh = not refresh and time.time() - path.stat().st_mtime < CATALOG_MAX_AGE_S
    except OSError:
        fresh = False
    if not fresh and url:
        try:
            own = client is None
            c = client or _client(token)
            try:
                r = c.get(f"{url}/manifest.json")
                r.raise_for_status()
                doc = r.json()
                regions = parse_manifest(doc)
            finally:
                if own:
                    c.close()
            if regions:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(".osxp-catalog.json.tmp")
                tmp.write_text(json.dumps(doc))
                replace_file(tmp, path)
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
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    replace_file(tmp, path)


def has_rivers(root: Path, region: str) -> bool:
    """Whether the region holds its river masks: put in place only whole (the module's docstring),
    and kept once there, whatever the setting."""
    return (mesh_dir(root, region) / RIVERS).is_dir()


def wanted(root: Path, region: OsxpRegion, rivers: bool) -> tuple[Zip, ...]:
    """The zips a region wants in its folder: its mesh, its night grids, and its river masks when
    the pilot chose soft banks (``rivers``, ``LiveSettings.rivers_fade``) or the region has them
    already. Once downloaded they are part of the region: going back to even rivers and lakes
    leaves them there, unused, so that soft banks chosen again downloads nothing (the user,
    2026-11-17), and an update of the region brings their new version with it, whatever the
    setting. The one rule of what an install, an update, the sizes and the update counts take."""
    keep = rivers or has_rivers(root, region.id)
    return (*region.mesh, *region.night, *(region.rivers if keep else ()))


def recorded(root: Path, region: OsxpRegion) -> dict[str, str]:
    """The zips the region's marker records as installed, {path: SHA-256}. Its river zips count
    all together or not at all: only while its ``rivers`` folder is there and each is the list's as
    it was. One of them made again, they all come again into the stage, which is put in place
    whole: a ``rivers`` folder is the list's whole or of before whole, and a bake makes every zip
    again anyway."""
    zips = dict(_read(mesh_dir(root, region.id) / MARKER).get("zips") or {})
    whole = has_rivers(root, region.id) and all(zips.get(z.path) == z.sha256 for z in region.rivers)
    if not whole:
        for z in region.rivers:
            zips.pop(z.path, None)
    return zips


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
    downloaded already or not, an update of it has them to bring."""
    have = recorded(root, region)
    return [z for z in wanted(root, region, rivers) if have.get(z.path) != z.sha256]


def to_fetch(
    root: Path, region: OsxpRegion, overlays: bool, rivers: bool = False
) -> tuple[list[Zip], list[Zip]]:
    """The zips of the region's folder (:func:`wanted`: its mesh, night grids and river masks) and
    the overlay zips an install downloads: not installed yet, or changed in the bucket since, and
    not unpacked already by an install that stopped. The overlays only with ``overlays``, as the
    river masks only where :func:`wanted` takes them."""
    folder = mesh_dir(root, region.id)
    have = {
        **recorded(root, region),
        **(_read(folder / STEP).get("zips") or {}),
        **_rivers_staged(root, region),
    }
    mesh = [z for z in wanted(root, region, rivers) if have.get(z.path) != z.sha256]
    record = _read(overlay_pack(root) / OVERLAY_RECORD)
    ovl = [z for z in region.overlays if record.get(z.path) != z.sha256] if overlays else []
    return mesh, ovl


def mesh_changed(root: Path, region: OsxpRegion) -> bool:
    """Whether an install changes the region's mesh: not installed whole, or one zip of its mesh
    changed since (the test branch region-swap's ``made_whole`` makes). False: night grids, river
    masks or overlays to add alone, the rest of the region as it was."""
    marker = _read(mesh_dir(root, region.id) / MARKER)
    if not marker:
        return True
    have = marker.get("zips") or {}
    return any(have.get(z.path) != z.sha256 for z in region.mesh)


def rivers_to_add(root: Path, region: OsxpRegion) -> list[Zip]:
    """The river zips soft banks downloads alone for a region installed whole whose mesh is the
    list's; none for one whose mesh changed: its update brings the region whole, rivers in."""
    if mesh_changed(root, region):
        return []
    mesh, _ = to_fetch(root, region, False, rivers=True)
    return [z for z in mesh if z in region.rivers]


def night_to_fetch(root: Path, region: OsxpRegion) -> list[Zip]:
    """The night grids' zips of a region installed whole that it has not got yet, or that changed
    in the bucket since: a region installed before Live lit its towns (2.0) has none."""
    folder = mesh_dir(root, region.id)
    if not (folder / MARKER).is_file():
        return []
    marker, step = _read(folder / MARKER), _read(folder / STEP)
    have = {**(marker.get("zips") or {}), **(step.get("zips") or {})}
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


def remove(root: Path, region: str, squares: Sequence[tuple[int, int]], log: Log = print) -> None:
    """A region deleted from the disk (the folder a link leads to too), its overlays out of the
    overlay pack, and what an install of it left behind."""
    folder = mesh_dir(root, region)
    if folder.is_symlink():
        target = folder.resolve()
        folder.unlink()
        shutil.rmtree(target, ignore_errors=True)
    elif folder.exists():
        shutil.rmtree(folder)
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


class Installer:
    """Downloads a region's zips (a few at a time) and unpacks each as soon as it is whole.
    :meth:`run` blocks; :meth:`cancel` stops it at the next megabyte, and the next install goes on
    from there. ``rivers``: the pilot chose soft banks, the region's river masks come with it
    (:func:`wanted`). ``night_only``: a region installed already given its night grids alone
    (:func:`night_to_fetch`), its mesh, its river masks and its record of the set-up left as they
    are.

    Where the region's mesh does not change (:func:`mesh_changed`), what comes is added in place:
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
    ):
        self.root = root
        self.region = region
        self.with_overlays = overlays
        self.night_only = night_only
        self.rivers = rivers
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
            self._set(phase="done")
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
        if self.night_only:
            mesh, ovl = night_to_fetch(self.root, region), []
        else:
            mesh, ovl = to_fetch(self.root, region, self.with_overlays, self.rivers)
            if not _rivers_staged(self.root, region):  # none, or one of zips made again since
                shutil.rmtree(folder / RIVERS_STAGE, ignore_errors=True)
        whole = not self.night_only and mesh_changed(self.root, region)
        todo = [*mesh, *ovl]
        self._set(
            total_bytes=sum(z.size for z in todo),
            zips=len(todo),
            phase="downloading" if todo else "done",
        )
        # nothing to download but zips unpacked and not recorded yet (river masks whose swap was
        # refused, a stop before the marker): only the end's work left
        left = [] if self.night_only else not_installed(self.root, region, self.rivers)
        if not todo and not left:
            if not self.night_only:
                self.log(f"OrthoStudio XP's region {region.id} is up to date")
            return
        # the night grids alone keep their record to themselves: an update the pilot stopped
        # keeps its step for the next one
        step = {"zips": {}} if self.night_only else _read(folder / STEP)
        step.setdefault("zips", {})
        if todo:
            self._fetch_all(region, mesh, ovl, folder, pack, step, whole)
        self._finish(folder, step, whole)

    def _fetch_all(
        self,
        region: OsxpRegion,
        mesh: list[Zip],
        ovl: list[Zip],
        folder: Path,
        pack: Path,
        step: dict[str, dict[str, str]],
        whole: bool,
    ) -> None:
        """``mesh`` downloaded and unpacked into the region's folder, its river masks into their
        stage, each recorded (the region's step, the stage's), ``ovl`` into the overlay pack: a few
        downloads at a time, the zips unpacked one at a time in the list's order."""
        todo = [*mesh, *ovl]
        stage = folder / RIVERS_STAGE
        folder.mkdir(parents=True, exist_ok=True)
        pack.mkdir(parents=True, exist_ok=True)
        self.download.mkdir(parents=True, exist_ok=True)
        need = sum(z.unpacked for z in todo) + max(z.size for z in todo) + SPARE_BYTES
        free = shutil.disk_usage(folder.resolve()).free
        if free < need:
            raise InstallError(
                f"{need / 1e9:.0f} GB needed on the disk of {folder.resolve()}, "
                f"{free / 1e9:.0f} GB free"
            )
        had = self._have_bytes(todo)
        with self.lock:  # what an earlier try downloaded counts whole, not in the pace
            self.progress.done_bytes += had
            self.rate.samples.clear()
        size = sum(z.size for z in todo) / 1e9
        rivers = [z for z in mesh if z in region.rivers]
        if self.night_only:
            self.log(
                f"the night lights' grids of OrthoStudio XP's region {region.id}: "
                f"{sum(z.size for z in todo) / 1e6:.1f} MB, into {folder.resolve()}"
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
                f"{folder.resolve()}"
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
                if z in ovl:
                    self._room(z, path, pack)
                    unpack(path, pack, self.stop)
                    record = _read(pack / OVERLAY_RECORD)
                    record[z.path] = z.sha256
                    _write(pack / OVERLAY_RECORD, record)
                elif z in rivers:  # into their stage, as into the region: rivers/textures/...
                    stage.mkdir(exist_ok=True)
                    self._room(z, path, stage)
                    unpack(path, stage, self.stop)
                    staged["zips"][z.path] = z.sha256
                    _write(stage / STEP, staged)
                else:
                    self._room(z, path, folder)
                    unpack(path, folder, self.stop)
                    step["zips"][z.path] = z.sha256
                    if not self.night_only:
                        _write(folder / STEP, step)
                path.unlink()
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

    def _room(self, z: Zip, path: Path, dest: Path) -> None:
        """Room on the disk of ``dest`` for the zip's files, each a whole block at least
        (:func:`disk_need`), and SPARE_BYTES left: a full disk stops the install before the zip,
        never inside it. Checked at each zip, as the start's count reads only the list's bytes and
        the streamer may write meanwhile."""
        need = disk_need(path) + SPARE_BYTES
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
                total += min(z.size, (self.download / z.local).stat().st_size)
        return total

    def _fetch(self, z: Zip) -> Path:
        """``z`` whole in the download folder, resumed where an earlier try stopped, checked."""
        import httpx

        path = self.download / z.local
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def _drop_terrain(self, folder: Path) -> None:
        """A region of before brought to zips that name their terrain files: once each of its zips
        has its list, the ``terrain`` folder of before is taken away, Live then making the files
        from their names (:data:`orthostudio.live.names.TERRAIN_LISTS`). Renamed first, so that a
        stop on the way leaves no folder half there to read."""
        from orthostudio.live.names import TERRAIN_LISTS

        old, gone = folder / "terrain", folder / ".terrain-old"
        shutil.rmtree(gone, ignore_errors=True)  # one a stop left
        lists = {p.stem for p in (folder / TERRAIN_LISTS).glob("*.txt")}
        if not old.is_dir() or lists != {z.name.removesuffix(".zip") for z in self.region.mesh}:
            return
        replace_file(old, gone)
        shutil.rmtree(gone, ignore_errors=True)
        self.log("  its terrain files of before taken away: made from their names now")

    def _rivers_whole(self) -> bool:
        """Whether the river stage holds every river zip of the list: what to put in place."""
        listed = {z.path: z.sha256 for z in self.region.rivers}
        return bool(listed) and _rivers_staged(self.root, self.region) == listed

    def _put_rivers(self, folder: Path) -> dict[str, str]:
        """The river masks of the stage put in place with one rename once it holds every river zip
        of the list, those of before renamed aside first and deleted after: a start finds the ones
        or the others, whole. Returns the zips put in place, {path: SHA-256}, which the marker then
        records; none while the stage is not whole (a stop, or rivers not wanted any more), kept
        for the next time. Windows does not rename a folder a program holds open: those of before
        stay as they were, the stage whole for the next try, which only puts it in place."""
        if not self._rivers_whole():
            return {}
        staged = _rivers_staged(self.root, self.region)
        real, old = folder / RIVERS, folder / RIVERS_OLD
        shutil.rmtree(old, ignore_errors=True)  # one an earlier swap could not delete
        try:  # renames tried again a moment on Windows (filesystem.replace)
            if real.exists():
                replace_file(real, old)
            try:
                replace_file(folder / RIVERS_STAGE / RIVERS, real)
            except OSError:
                if old.exists() and not real.exists():
                    replace_file(old, real)
                raise
        except OSError as e:
            raise InstallError(
                f"the river masks of {self.region.id} are in use, by X-Plane perhaps: close it, "
                f"then try again, which downloads nothing more ({e})"
            ) from e
        now = time.time()
        with contextlib.suppress(OSError):  # what the next start's set-up compares
            os.utime(real, (now, now))
        shutil.rmtree(old, ignore_errors=True)
        return staged

    def _finish(self, folder: Path, step: dict[str, dict[str, str]], whole: bool) -> None:
        """Every zip unpacked: the river masks put in place whole (:meth:`_put_rivers`), then the
        marker, which makes Live use the region, written last. Night grids, river masks or
        overlays added to a region whose mesh did not change: the marker records them, nothing
        else of the region changes. ``night_only`` leaves the river masks alone: it runs while
        Live streams, which reads them."""
        region = self.region
        marker = _read(folder / MARKER)
        put = {} if self.night_only else self._put_rivers(folder)
        zips = {**(marker.get("zips") or {}), **step["zips"], **put}
        sizes = {z.path: z.unpacked for z in (*region.mesh, *region.night, *region.rivers)}
        if not whole:  # the region as it was, what came recorded: nothing to set up again
            size = sum(sizes.get(p, 0) for p in zips)
            _write(folder / MARKER, {**marker, "zips": zips, "size": size})
            if put:
                shutil.rmtree(folder / RIVERS_STAGE, ignore_errors=True)
            if self.night_only:
                # its zips deleted as unpacked; an update the pilot stopped keeps what it
                # downloaded there to go on from it (the review of 2026-11-11)
                for kind in self.download.glob("*"):
                    with contextlib.suppress(OSError):  # its kinds' folders, only those empty
                        kind.rmdir()
                with contextlib.suppress(OSError):
                    self.download.rmdir()
                self.log(f"OrthoStudio XP's region {region.id}: its night lights' grids installed")
                return
            (folder / STEP).unlink(missing_ok=True)
            shutil.rmtree(self.download, ignore_errors=True)
            if put:
                self.log(
                    f"OrthoStudio XP's region {region.id}: its river masks installed, used from "
                    "Live's next start"
                )
            else:
                self.log(f"OrthoStudio XP's region {region.id} updated")
            return
        update = (folder / MARKER).exists()
        _write(
            folder / MARKER,
            {
                "id": region.id,
                "release": region.release,
                "installed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "source": "osxp",
                "size": sum(sizes.get(p, 0) for p in zips),
                "zips": zips,
            },
        )
        (folder / STEP).unlink(missing_ok=True)
        if put:
            shutil.rmtree(folder / RIVERS_STAGE, ignore_errors=True)
        self._drop_terrain(folder)
        now = time.time()
        for sub in ("Earth nav data", "terrain", "terrain-lists"):  # what the set-up compares
            with contextlib.suppress(OSError):
                os.utime(folder / sub, (now, now))
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
