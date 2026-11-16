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
arrive in: a mask two neighbouring zips both hold is the same file on every computer.

A region is made whole beside its place, in ``.z_osxp_<id>.new`` (the stage, a hidden folder on
the disk the region is on), and put in place with one rename once its marker, which makes Live use
it, is written: until then a start does not see it. An update does the same as soon as one zip of
its mesh changed (every bake makes every zip again): the region of before stays whole and in use
until the new one takes its place, and is then deleted with all it held, so that no file of the
mesh of before stays behind (2026-11-16). A stopped install goes on in its stage; a stage holding a
zip the list no longer has as it was is started again. Night grids added to a region whose mesh did
not change are unpacked into it in place, as nothing else changes.
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


@dataclass(frozen=True)
class OsxpRegion:
    id: str
    release: str  # when the manifest was made
    mesh: tuple[Zip, ...]
    overlays: tuple[Zip, ...] = ()
    # the night lights' density grids of its squares (orthostudio.live.nightwork), unpacked into
    # the region's folder as its mesh zips are: ``night/<square>.png``
    night: tuple[Zip, ...] = ()

    @property
    def size(self) -> int:
        return sum(z.size for z in (*self.mesh, *self.night, *self.overlays))

    @property
    def mesh_size(self) -> int:
        return sum(z.size for z in (*self.mesh, *self.night))

    @property
    def unpacked(self) -> int:
        return sum(z.unpacked for z in (*self.mesh, *self.night))


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
    made = str(doc.get("made", ""))
    out = {}
    for region_id, kinds in (doc.get("regions") or {}).items():
        out[region_id] = OsxpRegion(
            region_id,
            made,
            tuple(Zip(**z) for z in kinds.get("mesh", ())),
            tuple(Zip(**z) for z in kinds.get("overlays", ())),
            tuple(Zip(**z) for z in kinds.get("night", ())),
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


def _real(root: Path, region: str) -> Path:
    """The region's folder where it really is: the folder a link leads to, for one elsewhere."""
    folder = mesh_dir(root, region)
    return folder.resolve() if folder.is_symlink() else folder


def stage_dir(root: Path, region: str) -> Path:
    """Where the region is made whole before it takes its place: beside it, on its disk, hidden
    (no ``z_osxp_`` name: no start takes it for a region)."""
    real = _real(root, region)
    return real.with_name(f".{real.name}.new")


def _old_dir(real: Path) -> Path:
    """Where the region of before goes for the moment its successor takes its place."""
    return real.with_name(f".{real.name}.old")


def _installed_zips(root: Path, region: str) -> dict[str, str] | None:
    """The zips the installed region was made of, {path: SHA-256}; None: not installed."""
    marker = _read(mesh_dir(root, region) / MARKER)
    return (marker.get("zips") or {}) if marker else None


def made_whole(root: Path, region: OsxpRegion) -> bool:
    """Whether an install makes the region whole in its stage: not installed, or one zip of its
    mesh changed since. False: only night grids or overlays to add, in place."""
    have = _installed_zips(root, region.id)
    return have is None or any(have.get(z.path) != z.sha256 for z in region.mesh)


def _staged(root: Path, region: OsxpRegion) -> dict[str, str]:
    """The zips a stopped install unpacked into the region's stage, {path: SHA-256}, when each is
    still one of the list as it was; else none, the stage to be started again: a zip the list no
    longer has, or made again since, would leave its files in the region."""
    step = _read(stage_dir(root, region.id) / STEP).get("zips") or {}
    listed = {z.path: z.sha256 for z in (*region.mesh, *region.night)}
    return step if all(listed.get(p) == s for p, s in step.items()) else {}


def to_fetch(root: Path, region: OsxpRegion, overlays: bool) -> tuple[list[Zip], list[Zip]]:
    """The mesh zips and overlay zips an install downloads. A region made whole
    (:func:`made_whole`): every zip of its mesh and its night grids but those its stage holds
    already; else the night grids it has not got, or that changed since. The overlays not
    installed, or changed since."""
    if made_whole(root, region):
        staged = _staged(root, region)
        mesh = [z for z in (*region.mesh, *region.night) if staged.get(z.path) != z.sha256]
    else:
        mesh = night_to_fetch(root, region)
    record = _read(overlay_pack(root) / OVERLAY_RECORD)
    ovl = [z for z in region.overlays if record.get(z.path) != z.sha256] if overlays else []
    return mesh, ovl


def night_to_fetch(root: Path, region: OsxpRegion) -> list[Zip]:
    """The night grids' zips of a region installed whole that it has not got yet, or that changed
    in the bucket since: a region installed before Live lit its towns (2.0) has none."""
    have = _installed_zips(root, region.id)
    if have is None:
        return []
    return [z for z in region.night if have.get(z.path) != z.sha256]


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
    for left in (stage_dir(root, region), _old_dir(_real(root, region))):
        shutil.rmtree(left, ignore_errors=True)
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
    from there. ``night_only``: a region installed already given its night grids alone
    (:func:`night_to_fetch`), its mesh and its record of the set-up left as they are."""

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
    ):
        self.root = root
        self.region = region
        self.with_overlays = overlays
        self.night_only = night_only
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
        whole = not self.night_only and made_whole(self.root, region)
        stage = stage_dir(self.root, region.id)
        ovl: list[Zip] = []
        if self.night_only:
            mesh = night_to_fetch(self.root, region)
        else:
            mesh, ovl = to_fetch(self.root, region, self.with_overlays)
        todo = [*mesh, *ovl]
        self._set(
            total_bytes=sum(z.size for z in todo),
            zips=len(todo),
            phase="downloading" if todo else "done",
        )
        if not todo and not whole:
            if not self.night_only:
                self.log(f"OrthoStudio XP's region {region.id} is up to date")
            return
        # the region made whole: in its stage, started again unless it holds this list's zips; else
        # the night grids alone, into the region in place
        step: dict[str, dict[str, str]] = {"zips": {}}
        if whole:
            step["zips"] = dict(_staged(self.root, region))
            if not step["zips"]:
                shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True, exist_ok=True)
        dest = stage if whole else folder
        if todo:
            self._fetch_all(region, mesh, ovl, dest, pack, step, whole)
        self._finish(folder, stage, step, whole)

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
        """``mesh`` downloaded and unpacked into ``dest`` (and recorded in the stage's step, for a
        region made whole), ``ovl`` into the overlay pack, a few downloads at a time, the zips
        unpacked one at a time in the list's order."""
        todo = [*mesh, *ovl]
        pack.mkdir(parents=True, exist_ok=True)
        self.download.mkdir(parents=True, exist_ok=True)
        need = sum(z.unpacked for z in todo) + max(z.size for z in todo) + SPARE_BYTES
        free = shutil.disk_usage(dest.resolve()).free
        if free < need:
            raise InstallError(
                f"{need / 1e9:.0f} GB needed on the disk of {dest.resolve()}, "
                f"{free / 1e9:.0f} GB free"
            )
        had = self._have_bytes(todo)
        with self.lock:  # what an earlier try downloaded counts whole, not in the pace
            self.progress.done_bytes += had
            self.rate.samples.clear()
        size = sum(z.size for z in todo) / 1e9
        if not whole:
            verb = "adding to"
        elif (mesh_dir(self.root, region.id) / MARKER).exists():
            verb = "updating"
        else:
            verb = "installing"
        if self.night_only:
            self.log(
                f"the night lights' grids of OrthoStudio XP's region {region.id}: "
                f"{sum(z.size for z in todo) / 1e6:.1f} MB, into {dest.resolve()}"
            )
        else:
            self.log(
                f"{verb} OrthoStudio XP's region {region.id}: "
                f"{len(mesh)} mesh zips, {len(ovl)} overlay zips, {size:.1f} GB, into "
                f"{dest.resolve()}"
            )
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
                    unpack(path, pack, self.stop)
                    record = _read(pack / OVERLAY_RECORD)
                    record[z.path] = z.sha256
                    _write(pack / OVERLAY_RECORD, record)
                else:
                    unpack(path, dest, self.stop)
                    step["zips"][z.path] = z.sha256
                    if whole:
                        _write(dest / STEP, step)
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

    def _have_bytes(self, todo: Sequence[Zip]) -> int:
        total = 0
        for z in todo:
            with contextlib.suppress(OSError):
                total += min(z.size, (self.download / z.name).stat().st_size)
        return total

    def _fetch(self, z: Zip) -> Path:
        """``z`` whole in the download folder, resumed where an earlier try stopped, checked."""
        import httpx

        path = self.download / z.name
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
                        raise InstallError(f"{z.name}: HTTP {r.status_code}")
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
                    raise InstallError(f"{z.name}: {said}") from e
                _pause(attempt)
        size = path.stat().st_size
        if size != z.size:
            raise InstallError(f"{z.name}: {size} bytes, not {z.size}")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(CHUNK):
                h.update(block)
        if h.hexdigest() != z.sha256:
            path.unlink()
            self._add(-size)
            raise InstallError(f"{z.name} came damaged (its SHA-256 differs): install again")
        return path

    def _swap(self, folder: Path, stage: Path) -> None:
        """The stage put in the region's place with one rename, the region of before renamed aside
        first and deleted after: a start finds the one or the other, whole. A folder X-Plane holds
        open (Windows) cannot be renamed: the region of before is left as it was, and the stage,
        whole, waits for the next install, which only puts it in place."""
        real = _real(self.root, self.region.id)
        old = _old_dir(real)
        shutil.rmtree(old, ignore_errors=True)  # one an earlier swap could not delete
        try:
            if real.exists():
                os.rename(real, old)
            try:
                os.rename(stage, real)
            except OSError:
                if old.exists() and not real.exists():
                    os.rename(old, real)
                raise
        except OSError as e:
            raise InstallError(
                f"{folder.name} is in use, by X-Plane perhaps: close it, then click Install again, "
                f"which downloads nothing more ({e})"
            ) from e
        shutil.rmtree(old, ignore_errors=True)

    def _finish(
        self, folder: Path, stage: Path, step: dict[str, dict[str, str]], whole: bool
    ) -> None:
        """Every zip unpacked. Night grids or overlays added in place: the marker records them,
        nothing else changes. A region made whole: its marker, which makes Live use it, written in
        its stage, the stage put in its place, the region of before deleted."""
        region = self.region
        sizes = {z.path: z.unpacked for z in (*region.mesh, *region.night)}
        if not whole:
            marker = _read(folder / MARKER)
            zips = {**(marker.get("zips") or {}), **step["zips"]}
            size = sum(sizes.get(p, 0) for p in zips)
            _write(folder / MARKER, {**marker, "zips": zips, "size": size})
            # its zips deleted as unpacked; an update the pilot stopped keeps what it downloaded
            # there to go on from it (the review of 2026-11-11)
            with contextlib.suppress(OSError):
                self.download.rmdir()
            if self.night_only:
                self.log(f"OrthoStudio XP's region {region.id}: its night lights' grids installed")
            else:
                self.log(f"OrthoStudio XP's region {region.id} updated")
            return
        update = (folder / MARKER).exists()
        _write(
            stage / MARKER,
            {
                "id": region.id,
                "release": region.release,
                "installed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "source": "osxp",
                "size": sum(sizes.get(p, 0) for p in step["zips"]),
                "zips": step["zips"],
            },
        )
        self._swap(folder, stage)
        (folder / STEP).unlink(missing_ok=True)  # kept till then: a swap refused goes on from it
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
