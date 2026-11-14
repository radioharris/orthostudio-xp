# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The files of the squares round the aircraft, made as X-Plane comes and taken away behind it.

X-Plane holds about 4 x 3 squares of DSFs round the aircraft (``orthostudio.live.evict``'s
``xplane_tiles``, measured) and reads, when it loads one, every ``.ter`` file its DSF names, the
water masks and the textures those name. Live's pack keeps those files only for the squares of that
window and a ring round it (:data:`RING`): made when the window comes near, for a start before the
plugin lets X-Plane go, in flight a square of flight ahead (4 to 7 minutes at 480 kt); taken away
once the window is two squares away (:data:`KEEP`), the textures built kept (the disk budget's,
``orthostudio.live.evict``). The pack had them all before: 400,000 textures and 684,000 ``.ter``
for Europe, four minutes to set up and three to lay out again for another zoom, on APFS alone,
whose clones cost nothing (2026-09-29).

A square's files come from the AutoOrtho region that has them: its ``.ter`` files and water masks
copied, small files; each texture not built, a name of the neutral texture of its size (a hard
link, :class:`NeutralPool`), which every disk Live's folder may be on has
(``orthostudio.live.filesystem``). A thread decides, a process of its own does the file operations
(:func:`run_ops`), and a square whose files fail is tried again later, never stopping the others.
Measured with the real names of a start at Frankfurt, the most in Europe: 12,906 textures round
X-Plane's window in 2.7 s on the Mac's SSD, 3.8 s on a hard drive; the heaviest new column in
flight, 1,680 textures, 0.4 s on the hard drive, 0.3 s of processor and 1.5 MB written
(2026-09-29).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from orthostudio.imagery.grid import TextureId
from orthostudio.textures.ter import TerParams, ter_center, ter_kind, ter_text

from .evict import tiles_of, xplane_tiles
from .filesystem import BINARY, replace, too_many_links
from .geo import _TEXTURE_RE, parse_texture_name

Square = tuple[int, int]
# (name, lat, lon, zoom, kinds, source): a texture of the regions, where its centre is, the files
# its region has for it (:data:`TER_KINDS`) and which region (an index into the sources)
Entry = tuple[str, float, float, int, int, int]

RING = 1  # squares made round X-Plane's window: the next column's are there before X-Plane loads it
KEEP = 2  # squares farther than this from X-Plane's window lose their files
TER_KINDS = (("", 1), ("_water_overlay", 2), ("_sea_overlay", 4))  # the .ter files of a texture
OTHER = 8  # a .ter of another kind: its name kept apart (:attr:`RegionTextures.others`)
MASK_RE = re.compile(r"^(\d+)_(\d+)_ZL(\d{2})\.png$")  # a water mask: a sea overlay's border
RELEASE_BATCH = 4  # squares taken away at once, between the squares X-Plane will need
FAIL_RETRY_S = 10.0  # a square whose files could not all be made is tried again after this...
FAIL_RETRY_MAX_S = 300.0  # ... doubled each time, up to this
WAIT_RETRY_S = 1.0  # a square waiting for a texture being built at its old size: tried again
GIVE_UP_S = 60.0  # a square failing this long holds X-Plane no more at a start: the log says it
LIST_VERSION = 2  # of a region's list kept (one_region): another one is listed again


def mask_name(name: str) -> str:
    """The water mask of a texture (``100000_148176_BI18.dds``: ``100000_148176_ZL18.png``)."""
    tex = parse_texture_name(name)
    return f"{tex.y}_{tex.x}_ZL{tex.zoom}.png" if tex is not None else ""


def wrap(lon: int) -> int:
    return (lon + 180) % 360 - 180


def squares_round(points: Iterable[tuple[float, float]], ring: int) -> set[Square]:
    """X-Plane's window round each point (``xplane_tiles``), and ``ring`` squares round it."""
    out: set[Square] = set()
    for lat, lon in points:
        for row, column in xplane_tiles(lat, lon):
            for dr in range(-ring, ring + 1):
                for dc in range(-ring, ring + 1):
                    out.add((row + dr, wrap(column + dc)))
    return out


TERRAIN_LISTS = "terrain-lists"
"""Where a region of Live's own mesh names its ``.ter`` files instead of holding them, a text file a
zip: each is made from its name as a square is made
(:func:`made_ter`). 698,351 files of a few hundred bytes took 2.9 GB of Europe's disk, a block
each, and as many to unpack at an install (2026-10-19)."""

LIVE_TER = TerParams(imprint_masks_to_dds=False, mask_zl=14, use_decal_on_terrain=True)
"""The terrain files of Live's mesh as they were first written: every one
of Europe's 698,351 made again from its name by :func:`made_ter` the same, byte for byte."""


def terrain_folder(source: Path) -> Path:
    """The folder that says a region's terrain: its ``terrain`` folder (AutoOrtho's regions, Live's
    of before), else its :data:`TERRAIN_LISTS`."""
    folder = source / "terrain"
    return folder if folder.is_dir() else source / TERRAIN_LISTS


def made_ter(name: str) -> bytes:
    """A terrain file of Live's mesh made from its name alone, as its bake wrote it."""
    m = _TEXTURE_RE.match(name)
    if m is None or m[6] != "ter":
        raise ValueError(f"not a terrain file's name: {name}")
    t = TextureId(til_x=int(m[2]), til_y=int(m[1]), zl=int(m[4]), provider=m[3])
    lat, lon = ter_center(t)
    return ter_text(t, ter_kind(name), lat_med=lat, lon_med=lon, params=LIVE_TER).encode("ascii")


def folder_now(source: Path) -> list[int] | None:
    """The folder that says a region's terrain (:func:`terrain_folder`) as it is now, None where it
    has none: a file added or taken away there changes its time, a folder put in its place its
    identity too."""
    try:
        st = os.stat(terrain_folder(source))
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_ino, st.st_size]


def region_list_file(lists: Path, source: Path) -> Path:
    """Where ``lists`` keeps a region's textures: named after its folder and its whole path, so
    that two regions of one name (AutoOrtho's and Live's) each have their own."""
    whole = os.path.normcase(os.path.abspath(source))
    return lists / f"{source.name}-{hashlib.sha1(whole.encode('utf-8')).hexdigest()[:12]}.json"


@dataclass
class OneRegion:
    """A region's textures by the square of their centre, {"lat,lon": {name: the kinds of ``.ter``
    it has (:data:`TER_KINDS`, :data:`OTHER`)}}, their zooms, the names of those of another kind,
    and whether its folder was listed for them (not its list kept), with why that list could not
    be kept."""

    squares: dict[str, dict[str, int]]
    zooms: list[int]
    others: dict[str, list[str]]
    listed: bool
    keep_error: str | None = None

    @property
    def count(self) -> int:
        return sum(len(names) for names in self.squares.values())


def square_key(name: str) -> str | None:
    """The square of a texture's centre as a region's list keys it ("46,6"), None for a name that
    is not a texture's."""
    tex = parse_texture_name(name)
    if tex is None:
        return None
    lat, lon = tex.center
    return f"{math.floor(lat)},{math.floor(lon)}"


def _by_square(kinds: dict[str, int], others: dict[str, list[str]], listed: bool) -> OneRegion:
    """A region's textures as :func:`_listed` gives them, put by the square of their centre, once,
    as its list is made: a start reads them so and works out where a square's are only when it
    makes that square (:class:`SquareTextures`). The world's 3,712,375 worked out at every start
    took 5.4 s of it (the user's Mac, 2026-10-31)."""
    squares: dict[str, dict[str, int]] = {}
    zooms: set[int] = set()
    for name, kind in kinds.items():
        tex = parse_texture_name(name)
        if tex is None:
            continue
        lat, lon = tex.center
        squares.setdefault(f"{math.floor(lat)},{math.floor(lon)}", {})[name] = kind
        zooms.add(tex.zoom)
    return OneRegion(squares, sorted(zooms), others, listed=listed)


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}  # a region's list: one thread at a time reads or makes it


def _list_lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


def one_region(source: Path, lists: Path | None) -> OneRegion:
    """A region's textures: from its list kept in ``lists`` while its ``terrain`` folder has not
    changed since, else listed from that folder, and the list kept for the next time.

    Europe's 684,455 ``.ter`` are listed in 2.4 s on the Mac; on a Windows computer in the cloud,
    whose disk is slow for so many files, the start's step that lists them took 57 s, and 19 s the
    next time (2026-10-03). So a region is listed before any start: by Live's install as it ends
    (``orthostudio.live.aoregions``), by the Windows setup and as Live's window opens
    (``session.keep_region_lists``). A start that comes while the window's thread lists a region
    waits for that list, never listing it again alongside."""
    if lists is None:
        return _by_square(*_listed(source), listed=True)
    path = region_list_file(lists, source)
    with _list_lock(path):
        now = folder_now(source)
        if now is None:  # no terrain folder: no textures, and no list kept
            return OneRegion({}, [], {}, listed=True)
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(path.read_bytes())
            if (
                isinstance(data, dict)
                and data.get("version") == LIST_VERSION
                and data.get("folder") == now
                and isinstance(data.get("squares"), dict)
                and isinstance(data.get("zooms"), list)
                and isinstance(data.get("others"), dict)
            ):
                return OneRegion(data["squares"], data["zooms"], data["others"], listed=False)
        one = _by_square(*_listed(source), listed=True)  # now taken before: a change shows later
        data = {
            "version": LIST_VERSION,
            "folder": now,
            "squares": one.squares,
            "zooms": one.zooms,
            "others": one.others,
        }
        tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            replace(tmp, path)
        except OSError as e:
            one.keep_error = str(e)
            with contextlib.suppress(OSError):
                tmp.unlink()
        return one


def _listed(source: Path) -> tuple[dict[str, int], dict[str, list[str]]]:
    """A region's textures from its ``terrain`` folder: {name: kinds}, {name: other .ter names}.
    An error on the way stops the start, never a list kept half made."""
    kinds: dict[str, int] = {}
    others: dict[str, list[str]] = {}
    bits = dict(TER_KINDS)
    folder = terrain_folder(source)
    try:
        if folder.name == TERRAIN_LISTS:
            files = [
                line
                for lst in sorted(folder.glob("*.txt"))
                for line in lst.read_text(encoding="ascii").split()
            ]
        else:
            with os.scandir(folder) as entries:
                files = [entry.name for entry in entries]
    except OSError:
        return kinds, others
    for file in files:
        m = _TEXTURE_RE.match(file)
        if m is None or m[6] != "ter":
            continue
        name = f"{m[1]}_{m[2]}_{m[3]}{m[4]}.dds"
        bit = bits.get(m[5] or "", OTHER)
        if bit == OTHER:
            others.setdefault(name, []).append(file)
        kinds[name] = kinds.get(name, 0) | bit
    return kinds, others


class RegionTextures:
    """Every texture of the AutoOrtho regions a pack is made from, with the files each region has
    for it, from their ``terrain`` folders (the first region to have a texture has it), each
    region's list kept in ``lists`` where given (:func:`one_region`).

    ``entries_of`` gives a square's :data:`Entry`, worked out then; ``zooms`` the textures' zooms;
    ``others`` names the rare ``.ter`` of another kind; ``listed`` the regions whose folder was
    listed this time, and ``keep_error`` why a list could not be kept."""

    def __init__(self, sources: Sequence[Path], lists: Path | None = None):
        self.sources = list(sources)
        self._squares: list[dict[str, dict[str, int]]] = []  # each region's, in their order
        self.zooms: set[int] = set()
        self.others: dict[str, list[str]] = {}
        self.listed: list[Path] = []
        self.keep_error: str | None = None
        for i, source in enumerate(self.sources):
            one = one_region(source, lists)
            if one.listed:
                self.listed.append(source)
            self.keep_error = self.keep_error or one.keep_error
            self._squares.append(one.squares)
            self.zooms.update(one.zooms)
            for name, ters in one.others.items():
                if self._region_of(name) == i:  # else another region's already
                    self.others[name] = ters
        self._count = self._counted()

    def _region_of(self, name: str) -> int | None:
        """The first region that has a texture: the one whose files it takes."""
        key = square_key(name)
        found = (i for i, squares in enumerate(self._squares) if name in squares.get(key, ()))
        return next(found, None)

    def _counted(self) -> int:
        """How many textures, a name in two regions once: only the squares of several to look in."""
        seen: dict[str, int] = {}
        for squares in self._squares:
            for key in squares:
                seen[key] = seen.get(key, 0) + 1
        count = sum(len(names) for squares in self._squares for names in squares.values())
        for key, regions in seen.items():
            if regions > 1:
                sets = [set(squares[key]) for squares in self._squares if key in squares]
                count -= sum(len(names) for names in sets) - len(set().union(*sets))
        return count

    def __len__(self) -> int:
        return self._count

    def entries_of(self, square: Square) -> list[Entry]:
        """The textures whose centre is in ``square``, where that centre is worked out now: the
        first region that has a texture gives its files."""
        key = f"{square[0]},{square[1]}"
        out: list[Entry] = []
        seen: set[str] = set()
        for i, squares in enumerate(self._squares):
            for name, kinds in squares.get(key, {}).items():
                if name in seen:
                    continue
                seen.add(name)
                tex = parse_texture_name(name)
                lat, lon = tex.center
                out.append((name, lat, lon, tex.zoom, kinds, i))
        return out

    def entries(self) -> Iterator[Entry]:
        """Every texture, square by square: what a start no longer does (the tests)."""
        keys = dict.fromkeys(key for squares in self._squares for key in squares)
        for key in keys:
            lat, lon = (int(v) for v in key.split(","))
            yield from self.entries_of((lat, lon))


class SquareTextures:
    """The regions' textures by the square of their centre, as the streamer's index gives them
    (``streamer.TextureIndex.by_tile``): a square's worked out the first time it is asked for, as
    its files are made round the aircraft, and kept (:meth:`RegionTextures.entries_of`)."""

    def __init__(self, region: RegionTextures):
        self._region = region
        self._made: dict[Square, list[Entry]] = {}

    def get(self, square: Square, default: Sequence[Entry] = ()) -> Sequence[Entry]:
        made = self._made.get(square)
        if made is None:
            made = self._made[square] = self._region.entries_of(square)
        return made or default


class NeutralPool:
    """The neutral textures the names of textures not built lead to: one of each size, and a copy
    of it each time one has as many names as its disk allows (32,767 on a Mac, 1,023 on NTFS)."""

    def __init__(self, pack_dir: Path):
        self.pack_dir = pack_dir
        self.lock = threading.Lock()
        self.current: dict[int, int] = {}  # a size's copy names are linked to now

    def path(self, top: int, copy: int = 0) -> Path:
        from .region import neutral_file

        base = self.pack_dir / neutral_file(top)
        return base if copy == 0 else base.with_name(f"{base.stem}-{copy + 1}{base.suffix}")

    def _ensure(self, top: int, copy: int) -> Path:
        from .region import ensure_neutral

        path = self.path(top, copy)
        if not path.exists():
            base = ensure_neutral(self.pack_dir, top)
            if copy:
                tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
                shutil.copyfile(base, tmp)
                replace(tmp, path)
        return path

    def link(self, top: int, dst: Path) -> bool:
        """``dst`` made a name of the neutral texture of size ``top``; False when it exists."""
        with self.lock:
            copy = self.current.get(top, 0)
            while True:
                src = self._ensure(top, copy)
                try:
                    os.link(src, dst)
                except FileExistsError:
                    return False
                except OSError as e:
                    if not too_many_links(e):
                        raise
                    copy += 1
                    continue
                self.current[top] = copy
                return True

    def replace(self, top: int, path: Path) -> None:
        """``path`` made a name of the neutral texture of size ``top`` again, atomically."""
        tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        self.link(top, tmp)
        replace(tmp, path)


class SquareError(Exception):
    """Files of a square that could not be made or taken away: [(path, error)]."""

    def __init__(self, errors: list[tuple[str, str]]):
        super().__init__(
            f"{errors[0][1]} ({errors[0][0]})"
            + (f", and {len(errors) - 1} more" if len(errors) > 1 else "")
        )
        self.errors = errors


class SquareWaitError(Exception):
    """A square whose files wait: a texture on it, being built, still has its old zoom's size
    (orthostudio.live.region.set_zooms); made a moment later, once it is built at the new one."""


def with_decal(ter: bytes, decal: str) -> bytes:
    """A ``.ter`` file with the land's decal Live's settings choose: its ``DECAL_LIB`` line naming
    ``lib/g10/decals/<decal>``, or gone when ``decal`` is empty. A file with no such line (the sea,
    the water's overlays) is left as it is: decals go on the land only."""
    from orthostudio.decals import decal_lib

    lines = ter.split(b"\n")
    if not any(line.startswith(b"DECAL_LIB ") for line in lines):
        return ter
    out = []
    for line in lines:
        if line.startswith(b"DECAL_LIB "):
            if decal:
                out.append(b"DECAL_LIB " + decal_lib(decal).encode("ascii"))
        else:
            out.append(line)
    return b"\n".join(out)


def night_name(name: str) -> str:
    """The night texture of a texture (``23904_34080_BI16.dds``: ``23904_34080_BI16_LIT.dds``)."""
    return name[: -len(".dds")] + "_LIT.dds"


def with_night(ter: bytes, lit: str | None) -> bytes:
    """A land ``.ter`` file naming its night texture ``lit`` (``LIT_TEX_NOWRAP``) right after its
    photo, or naming none when ``lit`` is None. X-Plane adds it to the photo at night."""
    from .region import NIGHT_FOLDER

    lines = [line for line in ter.split(b"\n") if not line.startswith(b"LIT_TEX")]
    if lit is None:
        return b"\n".join(lines)
    out = []
    for line in lines:
        out.append(line)
        if line.startswith(b"BASE_TEX_NOWRAP "):
            out.append(f"LIT_TEX_NOWRAP ../{NIGHT_FOLDER}/{lit}".encode("ascii"))
    return b"\n".join(out)


class NightPool(NeutralPool):
    """The black night texture the names of night textures not built lead to, and its copies."""

    def path(self, top: int = 0, copy: int = 0) -> Path:
        from .region import NIGHT_NEUTRAL

        base = self.pack_dir / NIGHT_NEUTRAL
        return base if copy == 0 else base.with_name(f"{base.stem}-{copy + 1}{base.suffix}")

    def _ensure(self, top: int, copy: int) -> Path:
        from .region import ensure_night_neutral

        path = self.path(top, copy)
        if not path.exists():
            base = ensure_night_neutral(self.pack_dir)
            if copy:
                tmp = path.with_name(f".live-tmp-{os.getpid()}-{path.name}")
                shutil.copyfile(base, tmp)
                replace(tmp, path)
        return path


def _night_inodes(pack: Path) -> set[tuple[int, int]]:
    """(device, inode) of the black night texture and its copies."""
    out = set()
    for path in (pack / "_live").glob("night*.dds"):
        with contextlib.suppress(OSError):
            st = os.stat(path)
            out.add((st.st_dev, st.st_ino))
    return out


def _ter_text(text: bytes, decal: str | None, lit: str | None, night: bool) -> bytes:
    """A ``.ter`` file as the pack has it: the decal Live's settings choose, and its night texture
    when the night lights are on (``lit``: the land's; None for the water's files)."""
    if decal is not None:
        text = with_decal(text, decal)
    return with_night(text, lit) if night else text


def _copy_small(src: str, dst: str) -> None:
    """A small file (a ``.ter``, a water mask) copied, never over another one. A copy is the
    fastest there is on APFS and writes the least: 0.11 ms and 4.8 KB for a ``.ter``, against 0.17
    ms and 7 KB for a clone and 0.26 ms and 11.7 KB for a hard link (2026-09-30); and the same on
    every disk, across disks too."""
    with open(src, "rb") as f:
        data = f.read()
    _write_new(dst, data)


def _write_new(dst: str, data: bytes) -> None:
    """``data`` as a new file at ``dst``, never over another one (O_EXCL)."""
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | BINARY, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(dst)
        raise
    os.close(fd)


def _neutral_inodes(pack: Path) -> set[tuple[int, int]]:
    """(device, inode) of the neutral texture and its copies: what a texture's name leads to until
    the texture is built."""
    out = set()
    for path in (pack / "_live").glob("neutral*.dds"):
        with contextlib.suppress(OSError):
            st = os.stat(path)
            out.add((st.st_dev, st.st_ino))
    return out


def _neutral_tops(pack: Path) -> dict[tuple[int, int], int]:
    """(device, inode) of the neutral texture and its copies, each with its sharpest level."""
    from . import dds

    out = {}
    for path in (pack / "_live").glob("neutral*.dds"):
        with contextlib.suppress(OSError, ValueError):
            st = os.stat(path)
            out[(st.st_dev, st.st_ino)] = dds.read_top(path)
    return out


_POOLS: dict[str, NeutralPool] = {}  # a worker's neutral textures, by pack
_NIGHT_POOLS: dict[str, NeutralPool] = {}  # its black night textures


def _ignore_ctrl_c() -> None:
    """The file operations' process: Ctrl-C is for Live's, which closes it. It printed a
    KeyboardInterrupt at every stop of Live since it came (2026-09-30)."""
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGBREAK"):  # Windows: Live's Ctrl-Break reaches its whole group
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)


def run_ops(pack_dir: str, ops: Sequence[tuple]) -> dict:
    """The file operations of a square (:class:`Names`), in a process of its own: made from Live's
    process they waited for Python's lock after each system call, 5.8 s alone becoming 14.3 s at a
    real start, several minutes behind a busy thread (the review of 2026-09-30).

    ``ops``: ("file", source, destination), a ``.ter`` or a water mask copied if not there;
    ("ter", source, destination, decal), a ``.ter`` copied with its decal (:func:`with_decal`);
    ("name", top, destination), a name of the neutral texture made if not there; ("fit", top,
    destination, level built or None), the same, and a texture there already set to the size
    ``top`` gives if it has another (a new zoom: orthostudio.live.region.set_zooms), its levels
    kept, nothing downloaded; ("check", top, destination), a texture being built: a name of the
    neutral texture made if it has no file yet, said ``stale`` if it still has another size;
    ("drop", path), a file removed; ("drop_name", path), a texture's
    name removed only while it leads to the neutral texture (one built since stays); ("night",
    destination), a night texture's name made black if not there; ("drop_night", path), one
    removed while still black. The ``.ter`` operations may carry two more: the land's night
    texture and whether the night lights are on (:func:`with_night`). Every error is reported,
    none stops the others."""
    pack = Path(pack_dir)
    pool = _POOLS.get(pack_dir) or _POOLS.setdefault(pack_dir, NeutralPool(pack))
    placed = dropped = 0
    named: list[str] = []
    missing: list[str] = []
    errors: list[tuple[str, str]] = []
    neutral: set[tuple[int, int]] | None = None
    night_black: set[tuple[int, int]] | None = None
    tops: dict[tuple[int, int], int] | None = None
    fitted: dict[str, int] = {}
    stale: list[str] = []
    for op in ops:
        kind = op[0]
        try:
            if kind == "file":
                if not os.path.lexists(op[2]):
                    _copy_small(op[1], op[2])
                    placed += 1
            elif kind == "ter":
                if not os.path.lexists(op[2]):
                    with open(op[1], "rb") as f:
                        text = f.read()
                    lit, night = (op[4], op[5]) if len(op) > 5 else (None, False)
                    _write_new(op[2], _ter_text(text, op[3], lit, night))
                    placed += 1
            elif kind == "make":  # a region naming its terrain files: made from the name
                if not os.path.lexists(op[2]):
                    ter = made_ter(os.path.basename(op[2]))
                    lit, night = (op[4], op[5]) if len(op) > 5 else (None, False)
                    _write_new(op[2], _ter_text(ter, op[3], lit, night))
                    placed += 1
            elif kind == "night":  # a night texture's name, black until it is built
                if not os.path.lexists(op[1]):
                    night_pool = _NIGHT_POOLS.get(pack_dir) or _NIGHT_POOLS.setdefault(
                        pack_dir, NightPool(pack)
                    )
                    night_pool.link(0, Path(op[1]))
            elif kind == "drop_night":
                if night_black is None:
                    night_black = _night_inodes(pack)
                st = os.stat(op[1])
                if (st.st_dev, st.st_ino) in night_black:
                    os.unlink(op[1])
                    dropped += 1
            elif kind == "name":
                if not os.path.lexists(op[2]) and pool.link(op[1], Path(op[2])):
                    named.append(os.path.basename(op[2]))
            elif kind in ("fit", "check"):
                top, dst = op[1], Path(op[2])
                if not os.path.lexists(dst):
                    # a texture being built gets the neutral name too, until the builder writes
                    # its file over it: its .ter files come next, and X-Plane loading the square
                    # before it was written found them naming no file and stopped (issue 28)
                    if pool.link(top, dst) and kind == "fit":
                        named.append(dst.name)
                    continue
                if tops is None:
                    tops = _neutral_tops(pack)
                st = os.stat(dst)
                was = tops.get((st.st_dev, st.st_ino))
                if was is None:
                    from . import dds

                    try:
                        was = dds.read_top(dst)
                    except ValueError:  # no texture of Live's (the user's own file): left as it is
                        continue
                    neutral_name = False
                else:
                    neutral_name = True
                if was == top:
                    continue
                if kind == "check":
                    stale.append(dst.name)
                elif neutral_name or op[3] is None:  # nothing built in it: the neutral of its size
                    pool.replace(top, dst)
                else:  # its neutral texture made first: another process may not have yet
                    from .region import ensure_neutral
                    from .texture import resize

                    fitted[dst.name] = resize(dst, op[3], was, top, ensure_neutral(pack, top))
            elif kind == "drop":
                os.unlink(op[1])
                dropped += 1
            elif kind == "drop_name":
                if neutral is None:
                    neutral = _neutral_inodes(pack)
                st = os.stat(op[1])
                if (st.st_dev, st.st_ino) in neutral:
                    os.unlink(op[1])
                    dropped += 1
        except FileExistsError:
            pass
        except FileNotFoundError as e:
            if kind in ("file", "ter"):
                missing.append(op[1])
            elif kind in ("fit", "check"):  # never a square readable with a texture not set
                errors.append((str(op[2]), f"{type(e).__name__}: {e}"))
        except OSError as e:
            errors.append((str(op[-1]), f"{type(e).__name__}: {e}"))
    return {
        "placed": placed,
        "named": named,
        "dropped": dropped,
        "missing": missing,
        "errors": errors,
        "fitted": fitted,
        "stale": stale,
    }


class Names:
    """The pack's files of the squares round the aircraft (the module's docstring): a thread
    decides which squares, a process of its own does their file operations (:func:`run_ops`).
    :meth:`want` says which squares, :meth:`ready` whether they are there.

    ``busy(name)``, called under ``lock`` (the streamer's): whether the streamer has that texture
    built, queued or building, never taken away. ``forget(name)``, under ``lock`` too: a texture
    whose file was missing and got a neutral name, built again if it was recorded as built.
    ``release``: whether squares lose their files at all (not in a pack of before, which has
    every square's: orthostudio.live.region). A square whose files could not all be made is tried
    again later (:data:`FAIL_RETRY_S`), and holds a start no longer than :data:`GIVE_UP_S`.

    A new zoom sets each texture to its size as its square is made (:meth:`make`): ``building``,
    ``level_of`` and ``refit``, under ``lock`` too, say which the builder has taken (being
    written soon: the square waits for it), the level built, and the level a texture set to its
    size keeps, said before the builder may take it. One only queued is set at once: waiting for
    every queued texture of a square held the square of a start up to :data:`GIVE_UP_S`, and
    X-Plane went on without it (three starts, 2026-10-10). ``fitting``: the textures being set,
    which the builder does not write meanwhile. One process sets them, at a start too: shared
    between the builder's processes, they took the start's encoders from the photos X-Plane waits
    for, 7 s more on an 8-core Shadow PC at a cold start, 14 s at a new zoom (2026-10-11)."""

    def __init__(
        self,
        pack_dir: Path,
        textures: dict[Square, list[Entry]],
        sources: Sequence[Path],
        top_of: Callable[[str], int],
        lock: threading.Lock,
        busy: Callable[[str], bool],
        forget: Callable[[str], None] = lambda name: None,
        log: Callable[[str], None] = print,
        release: bool = True,
        others: dict[str, list[str]] | None = None,
        building: Callable[[str], bool] = lambda name: False,
        level_of: Callable[[str], int | None] = lambda name: None,
        refit: Callable[[str, int], None] = lambda name, level: None,
        decal: str | None = None,
        night: bool = False,
    ):
        self.pack_dir = pack_dir
        self.decal = decal  # the land's decal of its .ter files (:func:`with_decal`); None: theirs
        self.night = night  # the land's .ter files name a night texture (:func:`with_night`)
        self.by_tile = textures  # the streamer's index: textures by the square of their centre
        self.sources = list(sources)
        self.others = others or {}
        self.top_of = top_of
        self.lock = lock
        self.busy = busy
        self.forget = forget
        self.log = log
        self.release_ok = release
        self.building = building
        self.level_of = level_of
        self.refit = refit
        self.fitting: set[str] = set()  # under ``lock``: textures being set to the zoom's size
        self.pool = NeutralPool(pack_dir)  # Live's own process's: the disk budget's neutral names
        self.yielding: Callable[[], bool] = lambda: False  # X-Plane loading scenery: no release
        self.done: set[Square] = set()  # squares made since the streamer started
        self.present: set[Square] = self._present() if release else set()  # made before it
        self.target: set[Square] = set()
        self.keep: set[Square] = set()
        self.center: tuple[float, float] | None = None
        self.failing: dict[Square, list] = {}  # square: [first failure, next try, tries, error]
        self._given_up: set[Square] = set()
        self._said: set[str] = set()
        self.made = self.released = 0  # squares, for the log and the tests
        self._cond = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None
        self._workers = None  # the process of the file operations, once started
        self.crashed: BaseException | None = None

    # --- what a square holds -----------------------------------------------------------------

    def textures_on(self, square: Square) -> list[Entry]:
        """The textures on ``square``: those whose centre is there or next to it and whose ground
        reaches into it (a texture on a square's edge is on both, their DSFs name it both)."""
        lat, lon = square
        out = []
        for dl in (-1, 0, 1):
            for dn in (-1, 0, 1):
                for entry in self.by_tile.get((lat + dl, wrap(lon + dn)), ()):
                    tex = parse_texture_name(entry[0])
                    if tex is not None and square in tiles_of(tex):
                        out.append(entry)
        return out

    def _files(self, entry: Entry) -> Iterator[tuple[str, str]]:
        """(folder, name) of the ``.ter`` files and the water mask of a texture."""
        name, _, _, _, kinds, _ = entry
        stem = name[: -len(".dds")]
        for suffix, bit in TER_KINDS:
            if kinds & bit:
                yield "terrain", f"{stem}{suffix}.ter"
        if kinds & OTHER:
            for other in self.others.get(name, ()):
                yield "terrain", other
        if kinds & 4:  # a sea overlay: its border is the texture's water mask
            yield "textures", mask_name(name)

    def _present(self) -> set[Square]:
        """The squares whose ``.ter`` files are in the pack already: made before the streamer
        started, taken away once out of reach like the others."""
        out: set[Square] = set()
        with contextlib.suppress(OSError), os.scandir(self.pack_dir / "terrain") as entries:
            for entry in entries:
                tex = parse_texture_name(entry.name)
                if tex is not None:
                    out |= tiles_of(tex)
        return out

    def _run_ops(self, ops: list[tuple]) -> dict:
        if self._workers is None:  # not started (the tests), or no process to be had
            return run_ops(str(self.pack_dir), ops)
        return self._workers.submit(run_ops, str(self.pack_dir), ops).result()

    def _say_once(self, text: str) -> None:
        if text not in self._said:
            self._said.add(text)
            self.log(text)

    def make(self, square: Square) -> tuple[int, int]:
        """Every file ``square`` needs, those there already left: (files placed, texture names
        made). Its textures first, each set to the zoom's size if a new zoom left it another
        (region.set_zooms), then its terrain files, which make X-Plane read them: never one of
        another size. Raises :class:`SquareError` when some could not be made,
        :class:`SquareWaitError` while a texture being built has still its old size."""
        entries = self.textures_on(square)
        names = [entry[0] for entry in entries]
        with self.lock:
            building = {name for name in names if self.building(name)}
            claimed = [name for name in names if name not in building]
            levels = {name: self.level_of(name) for name in claimed}
            self.fitting.update(claimed)
        textures = self.pack_dir / "textures"
        from .region import ensure_neutral

        for top in {self.top_of(name) for name in claimed}:  # here, before processes share them
            ensure_neutral(self.pack_dir, top)
        result: dict = {"fitted": {}, "named": []}
        try:
            result = self._run_ops(
                [("fit", self.top_of(n), str(textures / n), levels[n]) for n in claimed]
                + [("check", self.top_of(n), str(textures / n)) for n in building]
            )
        finally:
            with self.lock:  # the levels said before the builder may take one: it reads them
                for name, level in result["fitted"].items():
                    self.refit(name, level)
                for name in result["named"]:
                    self.forget(name)  # recorded as built, its file was gone: built again
                self.fitting.difference_update(claimed)
        if result["errors"]:
            raise SquareError(result["errors"])
        if result["stale"]:
            raise SquareWaitError(result["stale"])
        ops: list[tuple] = []
        if self.night:  # the night textures' names first: never a .ter naming one not there
            from .region import NIGHT_FOLDER

            (self.pack_dir / NIGHT_FOLDER).mkdir(exist_ok=True)
            ops += [
                ("night", str(self.pack_dir / NIGHT_FOLDER / night_name(entry[0])))
                for entry in entries
                if entry[4] & 1
            ]
        for entry in entries:
            source = self.sources[entry[5]] if self.sources else None
            if source is not None:
                named = terrain_folder(source).name == TERRAIN_LISTS
                land = entry[0][: -len(".dds")] + ".ter"
                for folder, file in self._files(entry):
                    src, dst = str(source / folder / file), str(self.pack_dir / folder / file)
                    lit = night_name(entry[0]) if file == land else None
                    if named and file.endswith(".ter"):
                        ops.append(("make", src, dst, self.decal, lit, self.night))
                    elif (self.decal is not None or self.night) and file.endswith(".ter"):
                        ops.append(("ter", src, dst, self.decal, lit, self.night))
                    else:
                        ops.append(("file", src, dst))
        placed = self._run_ops(ops)
        for src in placed["missing"]:
            region = Path(src).parent.parent.name
            self._say_once(f"  {Path(src).name} is gone from {region}: the region changed")
        if placed["errors"]:
            raise SquareError(placed["errors"])
        return placed["placed"], len(result["named"])

    def take_away(self, square: Square) -> int:
        """``square``'s files removed, those of a texture on a square still made kept, and every
        texture the streamer holds. Returns how many. Raises :class:`SquareError`."""
        with self._cond:
            held = (self.done | self.present | self.target) - {square}
        ops: list[tuple] = []
        names = []
        for entry in self.textures_on(square):
            name = entry[0]
            if tiles_of(parse_texture_name(name)) & held:
                continue
            ops += [("drop", str(self.pack_dir / f / file)) for f, file in self._files(entry)]
            names.append(name)
        with self.lock:  # decided under the streamer's lock: nothing it builds or holds goes
            ops += [
                ("drop_name", str(self.pack_dir / "textures" / n))
                for n in names
                if not self.busy(n)
            ]
        if self.night:  # those still black; one built stays, as a texture built does
            from .region import NIGHT_FOLDER

            night = self.pack_dir / NIGHT_FOLDER
            ops += [("drop_night", str(night / night_name(n))) for n in names]
        result = self._run_ops(ops)
        if result["errors"]:
            raise SquareError(result["errors"])
        return result["dropped"]

    def holds(self, name: str) -> bool:
        """Whether a texture is on a square whose files are made: X-Plane may read it there."""
        tex = parse_texture_name(name)
        if tex is None:
            return False
        with self._cond:
            return bool(tiles_of(tex) & (self.done | self.present | self.target))

    def leads_to_neutral(self, name: str) -> bool:
        """Whether a texture's name leads to the neutral texture or one of its copies: nothing
        built written there since the name was made."""
        try:
            st = os.stat(self.pack_dir / "textures" / name)
        except OSError:
            return False
        return (st.st_dev, st.st_ino) in _neutral_inodes(self.pack_dir)

    # --- the thread ---------------------------------------------------------------------------

    def want(
        self,
        target: set[Square],
        keep: set[Square],
        center: tuple[float, float] | None = None,
    ) -> None:
        """The squares to make (nearest ``center`` first), and those to keep: the rest lose their
        files."""
        with self._cond:
            if target == self.target and keep == self.keep:
                return
            self.target, self.keep, self.center = set(target), set(keep) | set(target), center
            self._cond.notify_all()

    def ready(self, squares: Iterable[Square]) -> bool:
        """Whether every file of ``squares`` is made; a square failing for :data:`GIVE_UP_S`
        counts as made, so that X-Plane is not held for ever (the log says so, once)."""
        now = time.monotonic()
        with self._cond:
            for square in squares:
                if square in self.done:
                    continue
                failing = self.failing.get(square)
                if failing is None or now - failing[0] < GIVE_UP_S:
                    return False
                if square not in self._given_up:
                    self._given_up.add(square)
                    self.log(
                        f"  X-Plane goes on without the files of square {square[0]:+03d}"
                        f"{square[1]:+04d}: they could not all be made ({failing[3]})"
                    )
            return True

    def wait_ready(self, squares: Iterable[Square], timeout: float) -> bool:
        wanted = set(squares)
        with self._cond:
            return (
                self._cond.wait_for(
                    lambda: wanted <= self.done or self._stop or self.crashed, timeout
                )
                and wanted <= self.done
            )

    def _todo(self) -> tuple[list[Square], list[Square]]:
        """(squares to make, nearest first; squares to take away, farthest first), those failing
        not before their next try."""
        now = time.monotonic()
        later = {s for s, f in self.failing.items() if f[1] > now}
        make = sorted(self.target - self.done - later, key=self._distance)
        gone = []
        if self.release_ok and self.keep and not self.yielding():
            gone = sorted(
                (self.done | self.present) - self.keep - later, key=self._distance, reverse=True
            )
        return make, gone[:RELEASE_BATCH]

    def _failed(self, square: Square, error: SquareError) -> None:
        """A square's files failed: tried again later, a little later each time."""
        now = time.monotonic()
        with self._cond:
            first, _, tries, _ = self.failing.get(square, [now, now, 0, ""])
            wait = min(FAIL_RETRY_MAX_S, FAIL_RETRY_S * 2**tries)
            self.failing[square] = [first, now + wait, tries + 1, str(error)]
        if tries == 0:
            self.log(
                f"  the files of square {square[0]:+03d}{square[1]:+04d} could not all be made or "
                f"taken away ({error}): tried again in {wait:.0f} s"
            )

    def _distance(self, square: Square) -> float:
        if self.center is None:
            return 0.0
        lat, lon = self.center
        dlon = (square[1] + 0.5 - lon + 180) % 360 - 180
        return math.hypot(square[0] + 0.5 - lat, dlon * math.cos(math.radians(lat)))

    def run(self) -> None:
        try:
            self._run()
        except BaseException as e:  # the streamer stops on it, as on a builder's crash
            import traceback

            self.crashed = e
            self.log("the squares' files stopped:\n" + "".join(traceback.format_exception(e)))
            with self._cond:
                self._cond.notify_all()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stop:
                    make, gone = self._todo()
                    if make or gone:
                        break
                    self._cond.wait(timeout=1.0)
                if self._stop:
                    return
            if make:
                self._make_batch(make)
            else:
                for square in gone:
                    try:
                        self.take_away(square)
                    except SquareError as e:
                        self._failed(square, e)
                        continue
                    with self._cond:
                        self.done.discard(square)
                        self.present.discard(square)
                        self.failing.pop(square, None)
                        self.released += 1

    def _make_batch(self, squares: list[Square]) -> None:
        t0 = time.monotonic()
        placed = named = count = 0
        for square in squares:
            with self._cond:
                if self._stop or square not in self.target:
                    break  # the target moved on meanwhile: the next pass takes the new one
            try:
                files, names = self.make(square)
            except SquareError as e:
                self._failed(square, e)
                continue
            except SquareWaitError:  # a texture on it being built at its old size: in a moment
                now = time.monotonic()
                with self._cond:
                    first = self.failing.get(square, [now])[0]
                    self.failing[square] = [first, now + WAIT_RETRY_S, 0, "a texture being built"]
                continue
            placed, named, count = placed + files, named + names, count + 1
            with self._cond:
                self.done.add(square)
                self.present.discard(square)
                self.failing.pop(square, None)
                self.made += 1
                self._cond.notify_all()
        if placed or named:
            from . import writes

            writes.add("terrain files and masks (a 4 KB block each)", placed * 4096, placed)
            self.log(
                f"  files of {count} squares round the aircraft made: {named} textures, "
                f"{placed} terrain files and masks, in {time.monotonic() - t0:.1f} s"
            )

    def start(self, own_process: bool = True) -> None:
        """The thread, and the process of its file operations (Live's fork server's); without
        one (``own_process`` False: the tests), the operations are done in Live's own process."""
        from concurrent.futures import ProcessPoolExecutor

        from . import pools

        try:
            if own_process:
                self._workers = pools.RenewingPool(
                    lambda: ProcessPoolExecutor(
                        max_workers=1, mp_context=pools.context(), initializer=_ignore_ctrl_c
                    ),
                    self.log,
                )
        except (OSError, ValueError) as e:
            self.log(f"  the squares' files are made in Live's own process: {e}")
            self._workers = None
        self._thread = threading.Thread(target=self.run, name="live-names", daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join()
        if self._workers is not None:
            self._workers.shutdown()
