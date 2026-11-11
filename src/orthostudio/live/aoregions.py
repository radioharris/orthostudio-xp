# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""AutoOrtho's regions, installed by Live the way AutoOrtho's own installer installs them.

AutoOrtho's author publishes the mesh of the whole planet as the GitHub releases of
``kubilus1/autoortho-scenery``, which AutoOrtho's installer downloads (``autoortho/downloader.py``).
There are six regions: North America, South America, Europe, Africa, Asia and Australia-Pacific.
Each is a release with an ``<id>_info.json``. Its mesh comes in zips of about 125 one-degree squares
each (``z_eur_00.zip``...), holding DSFs, ``.ter`` files and water masks. Its overlays come in one
zip cut in parts (``y_eur_overlays.zip.00``...): roads, forests and buildings taken from X-Plane's
own scenery, which a square of another mesh no longer draws. Live downloads them from there, as
AutoOrtho does. The repository names no licence, so Live never publishes the files elsewhere.

A region Live installs goes to Live's folder, laid out as AutoOrtho lays out its own:
``regions/z_ao_<id>`` holds the mesh, ``regions/y_ao_<id>`` the overlays' ``Earth nav data``. Live
streams onto it as onto AutoOrtho's (:func:`orthostudio.live.scenery.default_sources`). A region
that AutoOrtho installed is used where it is and never downloaded again.

An install survives an interruption. What was downloaded stays in ``regions/.download/<id>`` and is
resumed with an HTTP range. Each zip is unpacked into ``regions/.staging`` as soon as it is whole,
then deleted. The region moves into place, with its marker, only once every zip is unpacked.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import math
import os
import queue
import re
import shutil
import threading
import time
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any

from orthostudio.live.filesystem import replace as replace_file

REPO = "kubilus1/autoortho-scenery"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
CATALOG_MAX_AGE_S = 3600.0  # AutoOrtho asks GitHub again at most every hour too
REGION_IDS = ("eur", "na", "sa", "afr", "asi", "aus_pac")  # the order the Live window lists them
REGION_NAMES = {  # as the plugin says them in X-Plane (the window says them in its own words)
    "eur": "Europe",
    "na": "North America",
    "sa": "South America",
    "afr": "Africa",
    "asi": "Asia",
    "aus_pac": "Australia and the Pacific",
}
REGION_NAMES_FR = {
    "eur": "Europe",
    "na": "Amérique du Nord",
    "sa": "Amérique du Sud",
    "afr": "Afrique",
    "asi": "Asie",
    "aus_pac": "Australie et Pacifique",
}
MAP_FILE = Path(__file__).with_name("aoregions_map.json")  # tools/live/aoregions_map.py
MARKER = ".live-region.json"
# AutoOrtho's rule (downloader.py, Release.parse), anchored: z_ the mesh, y_ the overlays
_ASSET = re.compile(r"(?P<kind>[yz])_(?P<name>.+)\.zip(?:\.(?P<part>\d+))?$")
CHUNK = 1 << 20
ATTEMPTS = 5  # per file, each resuming where the last stopped
# a region takes this much more once unzipped: DSFs from 6.7 to 10.1 MB (z_eur_15), and each
# .ter, a few hundred bytes, one 4 KB block of the disk (z_eur_05: 0.47 GB, 784 MB unzipped)
UNZIPPED_RATIO = 1.7
SPARE_BYTES = 2 * 10**9  # left free on the disk at the end of an install

Log = Callable[[str], None]


class CatalogError(RuntimeError):
    """AutoOrtho's list of regions could not be read, and none was kept from before."""


class InstallError(RuntimeError):
    """An install that cannot go on; the message says why."""


class CancelledError(Exception):
    """The install was stopped by its user; what was downloaded stays, for the next time."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256_url: str | None = None  # North and South America publish one per zip


@dataclass(frozen=True)
class AoRegion:
    id: str
    release: str
    mesh: tuple[Asset, ...]
    overlays: tuple[Asset, ...] = ()  # the parts of one zip, in order

    @property
    def size(self) -> int:
        return sum(a.size for a in (*self.mesh, *self.overlays))

    @property
    def mesh_size(self) -> int:
        return sum(a.size for a in self.mesh)

    @staticmethod
    def of(doc: dict) -> AoRegion:
        return AoRegion(
            doc["id"],
            doc["release"],
            tuple(Asset(**a) for a in doc["mesh"]),
            tuple(Asset(**a) for a in doc.get("overlays", ())),
        )


# --- AutoOrtho's list of regions ------------------------------------------------------------


def parse_releases(releases: Sequence[dict]) -> dict[str, AoRegion]:
    """The newest release of each region in GitHub's list (newest first), as AutoOrtho's installer
    reads it: a release is a region's when it has its ``<id>_info.json``; pre-releases do not
    count."""
    regions: dict[str, AoRegion] = {}
    for rel in releases:
        if rel.get("prerelease") or rel.get("draft"):
            continue
        assets = [a for a in rel.get("assets") or () if isinstance(a, dict) and a.get("name")]
        ids = [a["name"][: -len("_info.json")] for a in assets if a["name"].endswith("_info.json")]
        if not ids or ids[0] in regions:
            continue
        by_name = {a["name"]: a for a in assets}
        mesh: list[Asset] = []
        overlays: list[Asset] = []
        for a in assets:
            m = _ASSET.match(a["name"])
            if m is None:
                continue
            sha = by_name.get(a["name"] + ".sha256")
            asset = Asset(
                a["name"],
                a["browser_download_url"],
                int(a["size"]),
                sha["browser_download_url"] if sha else None,
            )
            (mesh if m["kind"] == "z" else overlays).append(asset)
        if mesh:
            regions[ids[0]] = AoRegion(
                ids[0],
                str(rel.get("name") or rel.get("tag_name") or ""),
                tuple(sorted(mesh, key=lambda a: a.name)),
                tuple(sorted(overlays, key=lambda a: a.name)),
            )
    return regions


@cache
def _map() -> tuple[tuple[str, ...], bytes]:
    doc = json.loads(MAP_FILE.read_text())
    return tuple(doc["regions"]), zlib.decompress(base64.b64decode(doc["map"]))


def region_of(lat: float, lon: float) -> str | None:
    """The region whose mesh has the square of (lat, lon); None where no region has one, the open
    sea for instance (``aoregions_map.json``, made from every zip's list of files)."""
    regions, cells = _map()
    south, west = math.floor(lat), math.floor(lon)
    if not (-90 <= south < 90 and -180 <= west < 180):
        return None
    index = cells[(south + 90) * 360 + (west + 180)]
    return regions[index - 1] if index else None


def regions_dir(root: Path) -> Path:
    return root / "regions"


def _client():
    import httpx

    return httpx.Client(
        follow_redirects=True,  # a release's file is served from GitHub's storage
        timeout=httpx.Timeout(30.0, connect=15.0),
        headers={"User-Agent": "OrthoStudio-XP-Live"},
    )


def catalog(root: Path, *, refresh: bool = False, client=None) -> dict[str, AoRegion]:
    """AutoOrtho's regions, the newest release each: from GitHub at most every hour, else the list
    kept in Live's folder, which also stands in when GitHub does not answer."""
    import httpx

    path = regions_dir(root) / "catalog.json"
    try:
        fresh = not refresh and time.time() - path.stat().st_mtime < CATALOG_MAX_AGE_S
    except OSError:
        fresh = False
    if not fresh:
        try:
            own = client is None
            c = client or _client()
            try:
                r = c.get(RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
                r.raise_for_status()
                regions = parse_releases(r.json())
            finally:
                if own:
                    c.close()
            if regions:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(".catalog.json.tmp")
                tmp.write_text(json.dumps({i: asdict(g) for i, g in regions.items()}))
                replace_file(tmp, path)
                return regions
        except (OSError, ValueError, httpx.HTTPError) as e:
            if not path.exists():
                raise CatalogError(f"GitHub did not give AutoOrtho's regions: {e}") from e
    try:
        return {i: AoRegion.of(d) for i, d in json.loads(path.read_text()).items()}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CatalogError(f"the list of AutoOrtho's regions kept in {path} is unreadable") from e


def installed(root: Path) -> dict[str, dict]:
    """The regions Live installed, whole: {id: their marker (release, when)}."""
    out: dict[str, dict] = {}
    with contextlib.suppress(OSError):
        for entry in os.scandir(regions_dir(root)):
            if entry.name.startswith("z_ao_") and entry.is_dir():
                with contextlib.suppress(OSError, ValueError):
                    out[entry.name[len("z_ao_") :]] = json.loads(
                        (Path(entry.path) / MARKER).read_text()
                    )
    return out


def mesh_dir(root: Path, region: str) -> Path:
    return regions_dir(root) / f"z_ao_{region}"


def lists_dir(root: Path) -> Path:
    """Where each region's textures are kept from one start to the next, AutoOrtho's regions' too
    (``orthostudio.live.names.one_region``)."""
    return regions_dir(root) / ".lists"


def overlays_dir(root: Path, region: str) -> Path:
    return regions_dir(root) / f"y_ao_{region}"


def remove(root: Path, region: str, log: Log = print) -> None:
    """A region Live installed deleted, with what an install of it left behind."""
    base = regions_dir(root)
    for path in (
        mesh_dir(root, region),
        overlays_dir(root, region),
        base / ".download" / region,
        base / ".staging" / region,
    ):
        if path.exists():
            shutil.rmtree(path)
    from orthostudio.live.names import region_list_file

    region_list_file(lists_dir(root), mesh_dir(root, region)).unlink(missing_ok=True)
    log(f"AutoOrtho's region {region} deleted from Live's folder")


def remove_autoortho(xplane: Path, region: str, log: Log = print) -> None:
    """A region AutoOrtho installed deleted as AutoOrtho's own uninstall deletes it
    (``Release.uninstall``): its folder, ``z_autoortho/scenery/z_ao_<id>``, and its record,
    ``z_autoortho/<id>_info.json``, so that AutoOrtho no longer takes it for installed. Its
    overlays, merged with the other regions' in ``yAutoOrtho_Overlays``, stay, as AutoOrtho
    leaves them."""
    base = xplane / "Custom Scenery" / "z_autoortho"
    folder = base / "scenery" / f"z_ao_{region}"
    if folder.exists():
        shutil.rmtree(folder)
    with contextlib.suppress(FileNotFoundError):
        (base / f"{region}_info.json").unlink()
    log(f"AutoOrtho's region {region} deleted from {base}: AutoOrtho no longer has it either")


# --- one install ------------------------------------------------------------------------------


@dataclass
class Progress:
    region: str
    phase: str = "starting"  # downloading, unpacking, done, failed, cancelled
    done_bytes: int = 0  # downloaded, the files already unpacked counted whole
    total_bytes: int = 0
    unpacked: int = 0  # zips unpacked
    zips: int = 0
    error: str = ""
    started: float = field(default_factory=time.time)
    mbs: float = 0.0  # the download's pace over its last seconds, in megabytes a second

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Parts(io.RawIOBase):
    """The parts of a zip cut in pieces, read as the one file they make, without joining them."""

    def __init__(self, paths: Sequence[Path]):
        self.files = [open(p, "rb") for p in paths]  # noqa: SIM115 - closed with the reader
        self.sizes = [os.fstat(f.fileno()).st_size for f in self.files]
        self.size = sum(self.sizes)
        self.pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def readinto(self, b) -> int:
        want, done, start = len(b), 0, 0
        for f, size in zip(self.files, self.sizes, strict=True):
            if done >= want:
                break
            if self.pos < start + size:
                f.seek(self.pos - start)
                chunk = f.read(min(want - done, start + size - self.pos))
                b[done : done + len(chunk)] = chunk
                done += len(chunk)
                self.pos += len(chunk)
            start += size
        return done

    def close(self) -> None:
        for f in self.files:
            f.close()
        super().close()


def unpack(source, dest: Path, prefix_after_top: str = "", stop: threading.Event | None = None):
    """Unpack a zip of AutoOrtho's into ``dest``, its top folder taken off (``z_eur_15/``, or
    ``y_eur_overlays/yOrtho4XP_Overlays/`` with ``prefix_after_top``), as AutoOrtho's
    ``extract_scenery`` does; its ``*`` folder is left out as there. Returns the files unpacked.
    Each member's CRC is checked as it is read."""
    count = 0
    with zipfile.ZipFile(source) as zf:
        tops = {n.split("/")[0] for n in zf.namelist() if "/" in n}
        if len(tops) != 1:
            raise InstallError(f"{getattr(source, 'name', source)}: {len(tops)} top folders, not 1")
        prefix = f"{next(iter(tops))}/{prefix_after_top}"
        for info in zf.infolist():
            if stop is not None and stop.is_set():
                raise CancelledError
            if not info.filename.startswith(prefix):
                continue
            relative = info.filename[len(prefix) :]
            if not relative or relative.startswith("*/"):
                continue
            info.filename = relative
            zf.extract(info, dest)  # zipfile keeps every path inside dest
            count += 1
    if count == 0:
        raise InstallError(f"nothing under {prefix} in {getattr(source, 'name', source)}")
    return count


def _pause(attempt: int) -> None:
    """Before a download's next try: longer each time, 30 s at most."""
    time.sleep(min(30.0, 2.0**attempt))


class Installer:
    """Downloads a region and unpacks it into Live's folder, one zip unpacking while the next
    downloads. :meth:`run` blocks; :meth:`cancel` stops it at the next megabyte."""

    def __init__(self, root: Path, region: AoRegion, *, client=None, log: Log = print):
        self.root = root
        self.region = region
        self.client = client
        self.log = log
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.progress = Progress(
            region.id, total_bytes=region.size, zips=len(region.mesh) + bool(region.overlays)
        )
        base = regions_dir(root)
        self.download = base / ".download" / region.id
        self.staging = base / ".staging" / region.id
        self.state_file = self.download / "state.json"

    def cancel(self) -> None:
        self.stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return replace(self.progress).as_dict()

    def _set(self, **changes) -> None:
        with self.lock:
            for k, v in changes.items():
                setattr(self.progress, k, v)

    def _add(self, n: int) -> None:
        with self.lock:
            self.progress.done_bytes += n

    # the state of an install, kept across interruptions
    def _state(self) -> dict:
        try:
            state = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            state = {}
        if state.get("release") != self.region.release:  # another release: from the start
            for path in (self.download, self.staging):
                if path.exists():
                    shutil.rmtree(path)
            state = {"release": self.region.release, "unpacked": []}
        return state

    def _save(self, state: dict) -> None:
        self.download.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_name(".state.json.tmp")
        tmp.write_text(json.dumps(state))
        replace_file(tmp, self.state_file)

    def run(self) -> Progress:
        own = self.client is None
        if own:
            self.client = _client()
        try:
            self._run()
            self._set(phase="done")
        except CancelledError:
            self._set(phase="cancelled")
            self.log(f"install of {self.region.id} stopped: it goes on from there the next time")
        except Exception as e:
            self._set(phase="failed", error=str(e) or type(e).__name__)
            self.log(f"install of {self.region.id} failed: {e}")
        finally:
            if own:
                self.client.close()
        return self.progress

    def _run(self) -> None:
        from orthostudio.live import winsearch

        winsearch.keep_out(self.root, self.log)  # Windows: the region's files too many to index
        region = self.region
        state = self._state()  # another release's leftovers go first
        self._save(state)
        done = set(state["unpacked"])
        mesh = [a for a in region.mesh if a.name not in done]
        overlays = [] if "overlays" in done else list(region.overlays)
        left = [*mesh, *overlays]
        have = sum(self._have(a) for a in left)
        already = region.size - sum(a.size for a in left)
        self._set(done_bytes=already + have, unpacked=len(done), phase="downloading")
        need = int(sum(a.size for a in left) * UNZIPPED_RATIO) - have + SPARE_BYTES
        free = shutil.disk_usage(self.download).free
        if free < need:
            raise InstallError(
                f"{need / 1e9:.0f} GB needed on the disk of Live's folder, {free / 1e9:.0f} GB free"
            )
        self.log(
            f"installing AutoOrtho's region {region.id} ({region.release}, "
            f"{region.size / 1e9:.1f} GB) into {regions_dir(self.root)}"
        )
        work: queue.Queue = queue.Queue(maxsize=1)  # one zip unpacking while the next downloads
        failure: list[BaseException] = []

        def unpacker() -> None:
            while (job := work.get()) is not None:
                if failure:
                    continue
                try:
                    self._set(phase="unpacking")
                    name, unpack_it = job
                    unpack_it()
                    state["unpacked"].append(name)
                    self._save(state)
                    with self.lock:
                        self.progress.unpacked += 1
                        self.progress.phase = "downloading"
                except BaseException as e:
                    failure.append(e)

        thread = threading.Thread(target=unpacker, name="live-region-unpack", daemon=True)
        thread.start()
        try:
            for asset in mesh:
                path = self._fetch(asset)
                self._put(work, failure, (asset.name, lambda p=path: self._unpack_mesh(p)))
            if overlays:
                paths = [self._fetch(a) for a in overlays]
                self._put(work, failure, ("overlays", lambda ps=paths: self._unpack_overlays(ps)))
        finally:
            work.put(None)
            thread.join()
        if failure:
            raise failure[0]
        self._finish(state)

    def _put(self, work: queue.Queue, failure: list, job) -> None:
        while True:
            if failure:
                raise failure[0]
            try:
                work.put(job, timeout=0.5)
                return
            except queue.Full:
                if self.stop.is_set():
                    raise CancelledError from None

    def _have(self, asset: Asset) -> int:
        try:
            return min(asset.size, (self.download / asset.name).stat().st_size)
        except OSError:
            return 0

    def _fetch(self, asset: Asset) -> Path:
        """``asset`` whole in the download folder: resumed where an earlier try stopped."""
        import httpx

        path = self.download / asset.name
        for attempt in range(ATTEMPTS):
            have = path.stat().st_size if path.exists() else 0
            if have > asset.size:
                path.unlink()
                self._add(-have)
                have = 0
            if have == asset.size:
                break
            try:
                headers = {"Range": f"bytes={have}-"} if have else {}
                with self.client.stream("GET", asset.url, headers=headers) as r:
                    if r.status_code == 200 and have:  # the range was not honoured
                        self._add(-have)
                        have = 0
                    elif r.status_code not in (200, 206):
                        raise InstallError(f"{asset.name}: HTTP {r.status_code}")
                    with open(path, "ab" if have else "wb") as f:
                        for chunk in r.iter_bytes():  # as it comes: a drop loses nothing
                            if self.stop.is_set():
                                raise CancelledError
                            f.write(chunk)
                            self._add(len(chunk))
            except (httpx.HTTPError, OSError) as e:
                if attempt == ATTEMPTS - 1:
                    raise InstallError(f"{asset.name}: {e}") from e
                _pause(attempt)
        size = path.stat().st_size
        if size != asset.size:
            raise InstallError(f"{asset.name}: {size} bytes, not {asset.size}")
        if asset.sha256_url:
            self._check_sha256(asset, path)
        return path

    def _check_sha256(self, asset: Asset, path: Path) -> None:
        r = self.client.get(asset.sha256_url)
        r.raise_for_status()
        m = re.match(r"\s*([0-9A-Fa-f]{64})\b", r.text)
        if m is None:
            raise InstallError(f"{asset.name}.sha256 is unreadable")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(CHUNK):
                h.update(block)
        if h.hexdigest().lower() != m.group(1).lower():
            path.unlink()
            raise InstallError(f"{asset.name} is damaged (its SHA-256 differs): download it again")

    def _unpack_mesh(self, path: Path) -> None:
        count = unpack(path, self.staging / "mesh", stop=self.stop)
        self.log(f"  {path.name}: {count} files")
        path.unlink()

    def _unpack_overlays(self, paths: Sequence[Path]) -> None:
        parts = _Parts(paths)
        try:
            with io.BufferedReader(parts, buffer_size=CHUNK) as source:
                count = unpack(source, self.staging / "overlays", "yOrtho4XP_Overlays/", self.stop)
        finally:
            parts.close()
        self.log(f"  overlays: {count} files")
        for p in paths:
            p.unlink()

    def _finish(self, state: dict) -> None:
        """Every zip unpacked: the region in place, with its marker last."""
        region = self.region
        final_mesh, final_overlays = (
            mesh_dir(self.root, region.id),
            overlays_dir(self.root, region.id),
        )
        for staged, final in (
            (self.staging / "mesh", final_mesh),
            (self.staging / "overlays", final_overlays),
        ):
            if not staged.exists():
                continue
            if final.exists():
                shutil.rmtree(final)
            replace_file(staged, final)
        (final_mesh / MARKER).write_text(
            json.dumps(
                {
                    "id": region.id,
                    "release": region.release,
                    "installed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "source": f"https://github.com/{REPO}",
                }
            )
        )
        shutil.rmtree(self.download, ignore_errors=True)
        shutil.rmtree(self.staging, ignore_errors=True)
        self.log(f"AutoOrtho's region {region.id} installed: {final_mesh}")
        # its textures listed now, the install still under way: the first start reads their list
        from orthostudio.live.names import one_region, region_list_file

        lists = lists_dir(self.root)
        t0 = time.monotonic()
        try:
            region_list_file(lists, final_mesh).unlink(missing_ok=True)
            one = one_region(final_mesh, lists)
        except OSError as e:  # the region is installed all the same: a start lists it
            self.log(f"  its textures not listed: {e}")
            return
        kept = f"their list not kept: {one.keep_error}" if one.keep_error else "their list kept"
        self.log(f"  its {one.count} textures listed in {time.monotonic() - t0:.1f} s, {kept}")
