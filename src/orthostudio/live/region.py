# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""AutoOrtho's regions as one pack X-Plane can load from the start, and stream into later.

X-Plane lists the DSFs of every scenery pack as it starts, so the pack holds every DSF of its
regions from the start (clones of AutoOrtho's, or hard links, on the same disk: 0.2 s for the 1,981
DSFs of ``z_ao_eur``; a link to their folder from another disk). The files those DSFs name, their
``.ter`` files, water masks and textures, it holds only for the squares round the aircraft, made as
X-Plane comes (:mod:`orthostudio.live.names`): a real texture where one was built, else a name of
one neutral file, a dull uniform colour at every level. The streamer replaces textures with real
ones as the aircraft comes, each written over the neutral texture (a clone, or a hole where the
disk clones nothing): whatever level X-Plane reads, it finds imagery or the neutral colour, never
black on APFS (``orthostudio.live.filesystem``).

Each texture's file is laid out for the zoom set (:class:`Zooms`, AutoOrtho's max_zoom and
max_zoom_near_airports): 4096 px at the mesh's zoom, smaller below it, 8192 px one zoom above, with
a neutral texture of each size. The size is set once, before X-Plane loads anything, and never
changes while it runs: X-Plane reads a file's size when it loads its tile and often keeps it (an
fs_usage trace, 2026-09-28: textures grown from 4096 to 8192 px in flight were not read at 8192 px).
A new zoom lays the pack's textures out again (:func:`relayout`), with X-Plane not running: a few
thousand, round where the aircraft last was.
"""

from __future__ import annotations

import contextlib
import json
import os
import struct
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from . import dds
from .clone import same_disk
from .filesystem import folder_link, is_folder_link, link_target, place_file, remove_folder_link
from .geo import parse_texture_name
from .rings import file_top, zoom_for
from .texture import write_sparse

NEUTRAL_RGB = (86, 92, 74)
NEUTRAL_LEVEL = 5  # the "built level" of a neutral texture: coarser than any real one
REGION_MARKER = Path("_live") / "region.json"
NEUTRAL_FILE = Path("_live") / "neutral.dds"
# 3: the files DSFs name made per square round the aircraft (orthostudio.live.names); 2: the neutral
# texture complete at every level (1 had holes: black up close)
REGION_VERSION = 3
# region.json "layout": 1, every file at its zoom's size; 2, above the mesh's zoom 4096 px files
# that grew to 8192 px in flight; 3, every file at its zoom's size, an 8192 px one with the copy of
# its sharpest level built above it (orthostudio.live.texture.write_sparse)
LAYOUT_GROWING = 2
LAYOUT_FIXED = 3


@dataclass(frozen=True)
class Zooms:
    """The zoom the textures are built to: AutoOrtho's max_zoom and max_zoom_near_airports, None
    for the mesh's own (a file of 4096 px, the pack as it was before zooms could be set)."""

    max_zoom: int | None = None
    max_zoom_airports: int | None = None

    def top(self, zoom: int) -> int:
        """The sharpest level a texture of ``zoom`` gets, its file's size: -1 (8192 px) one zoom
        above the mesh's."""
        cap = zoom_for(zoom, self.max_zoom, self.max_zoom_airports)  # type: ignore[arg-type]
        return 0 if cap is None else file_top(zoom, cap)


def _laid_out_top(zooms: Zooms, layout: int, zoom: int) -> int:
    """The size of a texture's file (its sharpest level) in a pack of ``layout``: at most 4096 px
    in one whose textures grew, but for those grown (recorded apart)."""
    top = zooms.top(zoom)
    return max(0, top) if layout == LAYOUT_GROWING else top


def file_tops(pack_dir: Path) -> Callable[[str], int]:
    """Each texture's file size in the pack (its sharpest level), from its name."""
    zooms, layout = region_layout(pack_dir)

    def top_of(name: str) -> int:
        tex = parse_texture_name(name)
        return 0 if tex is None else _laid_out_top(zooms, layout, tex.zoom)

    return top_of


def neutral_file(top: int = 0) -> Path:
    """The neutral texture of a file whose sharpest level is ``top`` (``neutral.dds``: 4096 px)."""
    return NEUTRAL_FILE if top == 0 else Path("_live") / f"neutral-{dds.size_of(top)}.dds"


def neutral_levels(top: int = 0, rgb=NEUTRAL_RGB, file_top: int = 0) -> list[bytes]:
    """Levels from ``top`` down to 1 x 1 of a uniform BC1 texture whose sharpest level is
    ``file_top``."""
    r, g, b = rgb
    c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    block = struct.pack("<HHI", c, c, 0)
    layout = dds.level_layout(file_top)[top - file_top :]
    return [block * (length // dds.BLOCK_BYTES) for _, length in layout]


def write_neutral(pack_dir: Path, top: int = 0) -> Path:
    neutral = pack_dir / neutral_file(top)
    neutral.parent.mkdir(parents=True, exist_ok=True)
    write_sparse(neutral, top, neutral_levels(top, file_top=top), file_top=top)
    return neutral


def ensure_neutral(pack_dir: Path, top: int) -> Path:
    path = pack_dir / neutral_file(top)
    return path if path.exists() else write_neutral(pack_dir, top)


NIGHT_FOLDER = "textures_night"
"""Where the pack's night textures are (orthostudio.live.night), one beside each land texture."""

NIGHT_NEUTRAL = Path("_live") / "night.dds"
"""What a night texture not built leads to: black, which X-Plane adds to the photo as nothing."""

NIGHT_TOP = 2
"""A night texture's sharpest level, counted as a texture's: 1024 px."""


def ensure_night_neutral(pack_dir: Path) -> Path:
    """The black night texture of the pack, written once: a hole but for its 1 x 1 level, zeros
    being black in BC1, so that what X-Plane reads of it costs no reading of the disk."""
    path = pack_dir / NIGHT_NEUTRAL
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        smallest = NIGHT_TOP + len(dds.level_layout(NIGHT_TOP)) - 1
        black = neutral_levels(smallest, rgb=(0, 0, 0), file_top=NIGHT_TOP)
        write_sparse(path, smallest, black, file_top=NIGHT_TOP)
    return path


def region_layout(pack_dir: Path) -> tuple[Zooms, int]:
    """The zoom the pack's textures are laid out for, and how: :data:`LAYOUT_FIXED`, or one of the
    layouts before it (the constants' comment)."""
    try:
        meta = json.loads((pack_dir / REGION_MARKER).read_text())
    except (OSError, ValueError):
        return Zooms(), LAYOUT_FIXED
    return Zooms(**meta.get("zooms", {})), meta.get("layout", 1)


def region_zooms(pack_dir: Path) -> Zooms:
    """The zoom the pack's textures are laid out for."""
    return region_layout(pack_dir)[0]


def _marker(pack_dir: Path) -> dict:
    try:
        return json.loads((pack_dir / REGION_MARKER).read_text())
    except (OSError, ValueError):
        return {}


def region_version(pack_dir: Path) -> int:
    """How the pack is made (:data:`REGION_VERSION`); 0 where it is not set up."""
    meta = _marker(pack_dir)
    return int(meta.get("version", 1)) if meta else 0


def sources_of_pack(pack_dir: Path) -> list[Path]:
    """The AutoOrtho regions the pack is made from, as its marker records them."""
    return [Path(s) for s in sources_of(_marker(pack_dir))]


def _textures_by_zoom(pack_dir: Path) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    for entry in os.scandir(pack_dir / "textures"):
        if entry.name.endswith(".dds") and not entry.name.startswith("."):
            tex = parse_texture_name(entry.name)
            if tex is not None:
                out[tex.zoom].append(entry.name)
    return out


def _layout_plan(pack_dir: Path, zooms: Zooms) -> dict[int, list[tuple[str, int]]]:
    """What laying the pack out for ``zooms`` changes: by zoom, the textures whose file gets another
    size, with the size it gets (its sharpest level). From a layout before :data:`LAYOUT_FIXED`,
    an 8192 px file built from a far level is written again too, for the copy above it
    (orthostudio.live.texture.needs_copy)."""
    from .pack import load_built
    from .texture import needs_copy

    old, layout = region_layout(pack_dir)
    meta = load_built(pack_dir)
    built = meta.get("textures", {})
    grown = set(meta.get("grown", []))  # a pack whose textures grew: those at 8192 px
    changes: dict[int, list[tuple[str, int]]] = {}
    for zoom, names in _textures_by_zoom(pack_dir).items():
        was, now = _laid_out_top(old, layout, zoom), zooms.top(zoom)
        change = []
        for name in names:
            size = -1 if name in grown else was
            level = built.get(name)
            uncopied = layout < LAYOUT_FIXED and level is not None and needs_copy(level, now)
            if size != now or uncopied:
                change.append((name, now))
        if change:
            changes[zoom] = change
    return changes


def needs_relayout(pack_dir: Path, zooms: Zooms) -> bool:
    """Whether some textures of the pack are laid out for another zoom than ``zooms``: X-Plane must
    not be running while they change size."""
    return bool(_layout_plan(pack_dir, zooms))


RELAYOUT_CHUNK = 500  # textures a process lays out at a time: the processes kept busy to the end


def _relayout_chunk(pack_dir: str, items: list[tuple[str, int, int | None]]) -> dict[str, int]:
    """Worker: ``items``, (name, new top, level built or None), each texture in a file of its new
    size (:func:`relayout`). Returns the level each built one holds now."""
    from .names import NeutralPool
    from .texture import resize

    pack = Path(pack_dir)
    pool = NeutralPool(pack)
    out: dict[str, int] = {}
    for name, new_top, level in items:
        path = pack / "textures" / name
        if level is None:
            pool.replace(new_top, path)
        else:  # the file's size from its header: what it is, whatever was recorded
            out[name] = resize(
                path, level, dds.read_top(path), new_top, pack / neutral_file(new_top)
            )
    return out


def relayout(
    pack_dir: Path, zooms: Zooms, log: Callable[[str], None] = print, workers: int = 1
) -> int:
    """Lay the pack out for ``zooms``: every texture whose file changes size, in a file of the new
    size. A neutral one becomes a name of the neutral texture of that size; a built one keeps its
    levels from the one built down (those sharper than the new file are dropped), nothing
    downloaded. Only while X-Plane does not run: it keeps a texture at the size it first read.
    Records the zoom and the layout even when no file changes. Returns how many textures changed.

    ``workers``: the processes sharing the textures, this computer's encoders at a start (X-Plane
    not running): one alone took 41 to 67 s for 44,305 textures on a Mac (the user: « c'est
    long », 2026-10-10). A pool that cannot start, the textures are laid out here, one by one."""
    from .pack import MARKER, load_built

    marker = pack_dir / REGION_MARKER
    meta = json.loads(marker.read_text())
    changes = _layout_plan(pack_dir, zooms)
    built_meta = load_built(pack_dir)
    built = built_meta.get("textures", {})
    t0 = time.monotonic()
    items: list[tuple[str, int, int | None]] = []
    for zoom in sorted(changes):
        tops = Counter(top for _, top in changes[zoom])
        after = ", ".join(f"{dds.size_of(top)} px" for top in sorted(tops))
        log(f"ZL{zoom} textures: {len(changes[zoom])} to {after} (nothing to download)")
        items += [(name, top, built.get(name)) for name, top in changes[zoom]]
    for top in {top for _, top, _ in items}:
        ensure_neutral(pack_dir, top)  # made once, before the processes share them
    chunks = [items[i : i + RELAYOUT_CHUNK] for i in range(0, len(items), RELAYOUT_CHUNK)]
    n = max(1, min(workers, len(chunks)))
    levels: dict[str, int] = {}
    if n > 1:
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        from . import pools

        try:
            with ProcessPoolExecutor(max_workers=n, mp_context=pools.context()) as ex:
                for got in ex.map(_relayout_chunk, [str(pack_dir)] * len(chunks), chunks):
                    levels.update(got)
            chunks = []
        except (OSError, BrokenProcessPool) as e:  # the rest here, one by one
            log(f"  the textures laid out in Live's own process: {e}")
            chunks = [[item for item in items if item[0] not in levels]]
    for chunk in chunks:
        levels.update(_relayout_chunk(str(pack_dir), chunk))
    built.update(levels)
    if built_meta:
        built_meta.pop("grown", None)  # every file at its size now: nothing grows any more
        tmp = pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(built_meta))
        os.replace(tmp, pack_dir / MARKER)
    meta["zooms"] = asdict(zooms)
    meta["layout"] = LAYOUT_FIXED
    marker.write_text(json.dumps(meta))
    if items:
        how = f" by {n} processes" if n > 1 else ""
        log(f"textures laid out for the zoom in {time.monotonic() - t0:.0f} s{how}")
    return len(items)


ASIDE_PREFIX = ".terrain-old-"  # the squares' terrain files set aside by a new zoom, removed behind


def set_zooms(pack_dir: Path, zooms: Zooms, log: Callable[[str], None] = print) -> None:
    """A new zoom, in an instant: no texture touched. Each one is set to its new size when its
    square's files are made (``orthostudio.live.names``), round a start or ahead of the aircraft,
    before X-Plane can read it; one never flown near again is never touched. Laying every texture
    out at the start took 41 to 67 s for the 44,305 of a pilot's flights, longer with each flight
    (the user: « si j'ai 100000 textures ... il va appliquer aux 100000 », 2026-10-10).

    So that X-Plane reads none at its old size, the squares made before lose their terrain files
    at once (their folder set aside, a rename, removed behind by a thread): they are made again
    as X-Plane comes. The levels recorded as built stay those the files hold: a texture's are
    lowered only when its file is set to the new size (``resize``, as its square is made).
    Lowering them all here forgot 8192 px levels the files still held, and a zoom set back had
    them downloaded and built again (29 textures at a start round Toulouse, 2026-10-10). Only
    while X-Plane does not run, as :func:`relayout`."""
    import threading

    marker = pack_dir / REGION_MARKER
    meta = json.loads(marker.read_text())
    terrain = pack_dir / "terrain"
    if terrain.is_dir() and not is_folder_link(terrain):
        os.replace(terrain, pack_dir / f"{ASIDE_PREFIX}{time.time_ns()}")
    terrain.mkdir(exist_ok=True)
    meta["zooms"] = asdict(zooms)
    meta["layout"] = LAYOUT_FIXED
    marker.write_text(json.dumps(meta))
    log(
        f"zoom {zooms.max_zoom}/{zooms.max_zoom_airports}: each texture set to it as its square's "
        f"files are made, nothing to download"
    )
    threading.Thread(
        target=remove_aside, args=(pack_dir,), name="live-terrain-aside", daemon=True
    ).start()


TEMP_PREFIX = ".live-tmp-"  # a file being written, renamed over its name once whole


def remove_temps(pack_dir: Path) -> int:
    """The files a Live stopped in the middle of a write left beside the textures, removed: at a
    start, before anything writes there (the next writes' own names are the same kind). The
    night textures' too, and the roads' half written (``.<square>.<pid>.tmp``) and their locks,
    none of which a start finds in use (the review of 2026-11-11)."""
    from .nightwork import ROADS_FOLDER

    removed = 0
    for folder, gone in (
        (pack_dir / "textures", lambda n: n.startswith(TEMP_PREFIX)),
        (pack_dir / NIGHT_FOLDER, lambda n: n.startswith(TEMP_PREFIX)),
        (
            pack_dir / ROADS_FOLDER,
            lambda n: (n.startswith(".") and n.endswith(".tmp")) or n.endswith(".lock"),
        ),
    ):
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
            for entry in os.scandir(folder):
                if gone(entry.name):
                    with contextlib.suppress(OSError):
                        os.unlink(entry.path)
                        removed += 1
    return removed


def remove_aside(pack_dir: Path) -> int:
    """The terrain files a new zoom set aside (:func:`set_zooms`), removed: those of a start that
    stopped before they were gone too."""
    removed = 0
    for folder in pack_dir.glob(f"{ASIDE_PREFIX}*"):
        with contextlib.suppress(OSError):
            removed += _remove_tree(folder, lambda _: None, "terrain files")
    return removed


def _upgrade(pack_dir: Path, log: Callable[[str], None]) -> None:
    """From version 1: the complete neutral texture, every built texture's larger levels made
    neutral instead of a hole."""
    from .pack import load_built
    from .texture import downgrade

    t0 = time.monotonic()
    log(f"updating the region in {pack_dir}: textures never black (once)")
    neutral = write_neutral(pack_dir)
    built = load_built(pack_dir).get("textures", {})
    redone = 0
    for name, level in built.items():
        path = pack_dir / "textures" / name
        if level > 0 and path.exists():
            downgrade(path, level, neutral)
            redone += 1
    log(f"  {redone} textures updated in {time.monotonic() - t0:.0f} s")


def _remove_tree(folder: Path, log: Callable[[str], None], what: str) -> int:
    """Every file of ``folder`` removed, then it: file by file, the count logged as it goes (a
    whole tree at once held the disk and froze other programs, 2026-09-28)."""
    removed = 0
    if is_folder_link(folder):
        remove_folder_link(folder)
        return 0
    if not folder.is_dir():
        return 0
    for entry in os.scandir(folder):
        if entry.is_dir(follow_symlinks=False):
            removed += _remove_tree(Path(entry.path), log, what)
            continue
        with contextlib.suppress(FileNotFoundError):
            os.unlink(entry.path)
        removed += 1
        if removed % 100_000 == 0:
            log(f"  {removed} {what} removed...")
    folder.rmdir()
    return removed


def _to_squares(pack_dir: Path, log: Callable[[str], None]) -> None:
    """A pack that had every square's files (versions 1 and 2): the ``.ter`` files, the water masks
    and the neutral textures taken away, the textures built kept, made again per square as X-Plane
    comes (orthostudio.live.names). Once, with X-Plane not running: a minute or two for the 684,000
    ``.ter`` and 400,000 textures of Europe."""
    from .names import MASK_RE
    from .pack import load_built

    t0 = time.monotonic()
    log(f"updating the pack in {pack_dir}: its files made per square from now on (once)")
    built = set(load_built(pack_dir).get("textures", {}))
    removed = 0
    textures = pack_dir / "textures"
    if is_folder_link(textures):
        remove_folder_link(textures)
    textures.mkdir(exist_ok=True)
    for entry in os.scandir(textures):
        name = entry.name
        texture = name.endswith(".dds") and parse_texture_name(name) is not None
        if (texture and name not in built) or MASK_RE.match(name) or name.startswith(".live-tmp-"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(entry.path)
            removed += 1
            if removed % 100_000 == 0:
                log(f"  {removed} neutral textures and masks removed...")
    terrain = _remove_tree(pack_dir / "terrain", log, "terrain files")
    (pack_dir / "terrain").mkdir(exist_ok=True)
    log(
        f"  {removed} neutral textures and masks, {terrain} terrain files removed in "
        f"{time.monotonic() - t0:.0f} s; {len(built)} built textures kept"
    )


def texture_names(terrain_dir: Path) -> set[str]:
    """The ``.dds`` names the ``.ter`` files of a directory use (AutoOrtho names them alike)."""
    names = set()
    for entry in os.scandir(terrain_dir):
        tex = parse_texture_name(entry.name) if entry.name.endswith(".ter") else None
        if tex is not None:
            names.add(tex.dds_name)
    return names


def sources_of(meta: dict) -> list[str]:
    """The AutoOrtho regions a pack holds, as its marker records them (one, before regions)."""
    sources = meta.get("sources")
    if isinstance(sources, list):
        return [str(s) for s in sources]
    return [str(meta["source"])] if meta.get("source") else []


def _merge_dir(src: Path, dst: Path) -> int:
    """Put into ``dst`` what of ``src`` it lacks: a whole folder where ``dst`` has none of that
    name, file by file where both have one; clones or hard links on the same disk, links across
    disks (a copy on Windows, where a file's link needs Developer Mode). A linked folder of ``dst``
    is made a real one first (:func:`_unlink_dir`), so nothing is ever written into another
    region's own folder. Returns how many were put in."""
    if is_folder_link(dst):
        _unlink_dir(dst)
    same = same_disk(src, dst)
    done = 0
    for entry in os.scandir(src):
        target = dst / entry.name
        if entry.is_dir():
            if not os.path.lexists(target):
                if same:
                    _place_tree(Path(entry.path), target, lambda _: None, [0])
                else:
                    folder_link(Path(entry.path), target)
                done += 1
            else:
                done += _merge_dir(Path(entry.path), target)
        elif not os.path.lexists(target):
            place_file(Path(entry.path), target, symlink=True)
            done += 1
    return done


def _unlink_dir(link: Path) -> None:
    """A link to a folder made a real folder of links to what that one holds, so that another
    region's files can go in beside them."""
    target = link_target(link)
    remove_folder_link(link)
    link.mkdir()
    for entry in os.scandir(target):
        if entry.is_dir():
            folder_link(Path(entry.path), link / entry.name)
        else:
            place_file(Path(entry.path), link / entry.name, symlink=True)


def _place_tree(src: Path, dst: Path, log: Callable[[str], None], done: list[int]) -> None:
    """``dst`` made the same as ``src``, entry by entry, each file a clone, else a hard link: many
    short calls, where one call for a whole tree (684,000 terrain files) held the disk for 53 s and
    froze other programs twice (2026-09-28)."""
    dst.mkdir()
    for entry in os.scandir(src):
        target = dst / entry.name
        if entry.is_dir(follow_symlinks=False):
            _place_tree(Path(entry.path), target, log, done)
        else:
            place_file(Path(entry.path), target)
            done[0] += 1
            if done[0] % 100_000 == 0:
                log(f"  {done[0]} files placed...")


def _place_dir(src: Path, dst: Path, log: Callable[[str], None]) -> None:
    """Replace ``dst`` by the same files as ``src``, file by file; where the pack is on another disk
    than ``src``, which neither clones nor hard links cross, by a link to it: X-Plane reads the mesh
    from there."""
    t0 = time.monotonic()
    old = dst.with_name(dst.name + ".old")
    # what a set-up stopped while taking it away left (a Ctrl-C): renamed onto, it refused the
    # start with "Directory not empty" (2026-10-18)
    if is_folder_link(old):
        remove_folder_link(old)
    elif old.exists():
        _remove_tree(old, log, "files")
    if dst.exists() or is_folder_link(dst):
        dst.rename(old)
    if same_disk(src, dst.parent):
        done = [0]
        _place_tree(src, dst, log, done)
        how = f"{done[0]} files placed in {time.monotonic() - t0:.1f} s"
    else:
        folder_link(src, dst)
        how = f"linked to {src}, on another disk"
    if is_folder_link(old):
        remove_folder_link(old)
    elif old.exists():
        _remove_tree(old, log, "files")
    log(f"  {dst.name}: {how}")


def _shared_files(sources: Sequence[Path], textures: Path) -> tuple[int, int]:
    """The files of the regions' ``textures`` folders that are neither textures nor water masks,
    which every square's ``.ter`` may name (``water_transition.png``): placed once. Returns (how
    many, how many copied from another disk)."""
    from .names import MASK_RE

    placed = copied = 0
    for region in sources:
        with contextlib.suppress(OSError), os.scandir(region / "textures") as entries:
            for entry in entries:
                name = entry.name
                if name.endswith(".dds") or MASK_RE.match(name) or not entry.is_file():
                    continue
                target = textures / name
                if os.path.lexists(target):
                    continue
                if place_file(Path(entry.path), target) == "copied":
                    copied += 1
                placed += 1
    return placed, copied


def _fingerprints(sources: Sequence[Path]) -> dict[str, list[int]]:
    """What changes when a region is installed again or updated: its folders' times. The pack
    takes the files its DSFs name from the regions while Live streams (orthostudio.live.names),
    so DSFs of before beside ``.ter`` files of now must not meet."""
    from .names import terrain_folder

    out = {}
    for source in sources:
        times = []
        for sub in ("Earth nav data", terrain_folder(source).name):
            try:
                times.append(os.stat(source / sub).st_mtime_ns)
            except OSError:
                times.append(0)
        out[str(source)] = times
    return out


DECAL_RECORD = "_live/decal.json"
"""The land's decal the ``.ter`` files of the pack name (:func:`set_decal`)."""


def set_decal(
    pack_dir: Path,
    decal: str | None,
    log: Callable[[str], None] = print,
    xplane_running: Callable[[], bool] = lambda: False,
) -> None:
    """The ``.ter`` files of the pack made again when the land's decal Live's settings choose is
    not the one they name (:func:`orthostudio.live.names.with_decal`): taken away, made again per
    square as X-Plane comes. Not while X-Plane runs, which may be reading them: at the next start.
    ``None``: the regions' own decals, nothing to do."""
    if decal is None:
        return
    record = pack_dir / DECAL_RECORD
    try:
        was = json.loads(record.read_text(encoding="utf-8")).get("decal")
    except (OSError, ValueError, AttributeError):
        was = None  # a pack of before: its .ter files name the regions' decals
    if was == decal:
        return
    terrain = pack_dir / "terrain"
    if terrain.is_dir() and next(terrain.glob("*.ter"), None) is not None:
        if xplane_running():
            log(
                f"the land's decal {decal or 'none'}: the terrain files made already keep theirs "
                "until Live's next start with X-Plane not running"
            )
            return
        removed = _remove_tree(terrain, log, "terrain files")
        terrain.mkdir(exist_ok=True)
        log(
            f"the land's decal is {decal or 'none'}: {removed} terrain files made again as "
            "X-Plane comes"
        )
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"decal": decal}), encoding="utf-8")


NIGHT_RECORD = "_live/night.json"
"""Whether the pack's ``.ter`` files name night textures (:func:`set_night`)."""


def night_on(pack_dir: Path) -> bool:
    """Whether the pack's ``.ter`` files name night textures (:func:`set_night`): the night is
    made only then, not for files X-Plane would never read (turned on while X-Plane ran)."""
    try:
        return bool(json.loads((pack_dir / NIGHT_RECORD).read_text(encoding="utf-8")).get("night"))
    except (OSError, ValueError, AttributeError):
        return False  # a pack of before: no night lines


def set_night(
    pack_dir: Path,
    night: bool,
    log: Callable[[str], None] = print,
    xplane_running: Callable[[], bool] = lambda: False,
) -> None:
    """The ``.ter`` files of the pack made again when Live's settings turn its night lights on or
    off and the files say otherwise (orthostudio.live.names.with_night), as :func:`set_decal`
    does: not while X-Plane runs, at the next start then. Turned off, the night textures go with
    them, and the roads kept for them: "No" said no night texture, and they stayed on the disk,
    uncounted (the review of 2026-11-11)."""
    record = pack_dir / NIGHT_RECORD
    was = night_on(pack_dir)
    if was == night:
        return
    terrain = pack_dir / "terrain"
    if terrain.is_dir() and next(terrain.glob("*.ter"), None) is not None:
        if xplane_running():
            log(
                f"night lights {'on' if night else 'off'}: the terrain files made already stay as "
                "they are until Live's next start with X-Plane not running"
            )
            return
        removed = _remove_tree(terrain, log, "terrain files")
        terrain.mkdir(exist_ok=True)
        log(
            f"night lights {'on' if night else 'off'}: {removed} terrain files made again as "
            "X-Plane comes"
        )
    if not night:
        _drop_night(pack_dir, log)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"night": night}), encoding="utf-8")


def _drop_night(pack_dir: Path, log: Callable[[str], None]) -> None:
    """The night textures, their black one and the roads kept for them, deleted (night lights off:
    no terrain file names them any more)."""
    from .nightwork import ROADS_FOLDER

    gone = _remove_tree(pack_dir / NIGHT_FOLDER, log, "night textures")
    gone += _remove_tree(pack_dir / ROADS_FOLDER, log, "roads kept for the night")
    neutral = pack_dir / NIGHT_NEUTRAL
    for black in neutral.parent.glob(f"{neutral.stem}*{neutral.suffix}"):  # its copies too (NTFS)
        with contextlib.suppress(OSError):
            black.unlink()
    if gone:
        log(f"night lights off: {gone} night files deleted")


def setup_region(
    source: Path | Sequence[Path],
    pack_dir: Path,
    force: bool = False,
    log: Callable[[str], None] = print,
    zooms: Zooms | None = None,
    xplane_running: Callable[[], bool] = lambda: False,
) -> dict:
    """Make ``pack_dir`` hold the AutoOrtho regions of ``source`` (one, or several: Europe, North
    America...): their DSFs, their shared files, the neutral texture; keep the textures already
    built. A region added since is merged in, once; one taken away sets the pack up again. A pack
    of before, which had every square's files, has them taken away once (:func:`_to_squares`), not
    while X-Plane runs, which may be reading them. ``zooms``: what the textures are laid out for,
    the first time (:func:`relayout` after)."""
    sources = [source] if isinstance(source, Path) else list(source)
    keys = [str(s) for s in sources]
    marker = pack_dir / REGION_MARKER
    old: dict = json.loads(marker.read_text()) if marker.exists() else {}
    had = sources_of(old)
    version = old.get("version", 1) if old else REGION_VERSION
    now = _fingerprints(sources)
    changed = [k for k, v in old.get("fingerprints", {}).items() if k in now and now[k] != v]
    if changed and xplane_running():  # X-Plane may be reading the DSFs of before
        log(
            f"the region {Path(changed[0]).name} changed since the pack was set up: it is set up "
            f"again at Live's next start with X-Plane not running"
        )
    elif changed:
        log(f"the region {Path(changed[0]).name} changed since the pack was set up: set up again")
        force = True
    if old and not force and set(had) == set(keys):
        if "fingerprints" not in old:  # a pack of before: what its regions are from now on
            old["fingerprints"] = now
            marker.write_text(json.dumps(old))
        if version < REGION_VERSION:
            if xplane_running():
                log(
                    "the pack keeps every square's files while X-Plane runs: they are made per "
                    "square from Live's next start with X-Plane not running"
                )
                return old
            if version < 2:
                _upgrade(pack_dir, log)
            _to_squares(pack_dir, log)
            old["version"] = REGION_VERSION
            marker.write_text(json.dumps(old))
        return old
    t0 = time.monotonic()
    names_of = ", ".join(s.name for s in sources)
    # only regions to add after those of before: merged in. One that comes first (Live's own
    # mesh, orthostudio.live.scenery.osxp_regions) must take the squares it has: merged in, the
    # DSFs of before stayed and X-Plane read them with that region's terrain files (2026-10-18)
    adding = bool(had) and set(had) < set(keys) and keys[: len(had)] == had
    if old and (version < REGION_VERSION or not adding) and not xplane_running():
        _to_squares(pack_dir, log)  # the files of the regions of before
    if adding:
        new = [s for s in sources if str(s) not in had]
        log(f"adding {', '.join(s.name for s in new)} to the region in {pack_dir} (once)")
    else:
        new = sources
        log(f"setting up the region of {names_of} in {pack_dir} (once)")
        (pack_dir / "_live").mkdir(parents=True, exist_ok=True)
        _place_dir(new[0] / "Earth nav data", pack_dir / "Earth nav data", log)
        new = new[1:]
    for extra in new:  # a DSF another region has already stays that region's
        merged = _merge_dir(extra / "Earth nav data", pack_dir / "Earth nav data")
        log(f"  {extra.name}: {merged} folders and files merged in")

    terrain = pack_dir / "terrain"  # made per square (orthostudio.live.names)
    if is_folder_link(terrain):
        remove_folder_link(terrain)
    terrain.mkdir(exist_ok=True)
    textures = pack_dir / "textures"
    textures.mkdir(exist_ok=True)
    placed, copied = _shared_files(sources, textures)
    log(
        f"  {placed} shared files placed"
        + (f", {copied} of them copied from another disk" if copied else "")
    )

    # a pack that is there keeps its layout, the textures added included: a new zoom lays them all
    # out again together (relayout)
    if old:
        zooms, layout = Zooms(**old.get("zooms", {})), old.get("layout", 1)
    else:
        zooms, layout = zooms or Zooms(), LAYOUT_FIXED
    write_neutral(pack_dir)
    meta = {
        **old,
        "source": keys[0],
        "sources": keys,
        "created": old.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "version": REGION_VERSION,
        "zooms": asdict(zooms),
        "layout": layout,
        "fingerprints": now,
    }
    meta.pop("textures", None)  # a count of before: the regions' own now (orthostudio.live.names)
    marker.write_text(json.dumps(meta))
    log(
        f"region ready in {time.monotonic() - t0:.0f} s: the files of its squares are made as "
        f"X-Plane comes"
    )
    return meta


def reset_built(pack_dir: Path, log: Callable[[str], None] = print) -> int:
    """Every built texture back to the neutral one, and forgotten: the pack as the first time.

    Only the textures the streamer recorded as built are touched (``_live/ortho.json``), so a reset
    takes seconds; those built in another season and not made neutral yet too (``stale``). Their
    night textures are black again too: one made before stayed over the neutral texture until its
    texture was built again at night."""
    from .names import NeutralPool, NightPool, night_name
    from .pack import MARKER, load_built

    meta = load_built(pack_dir)
    built = {**meta.get("stale", {}), **meta.get("textures", {})}
    zooms, layout = region_layout(pack_dir)
    pool = NeutralPool(pack_dir)
    nights: NightPool | None = None
    for name in built:
        path = pack_dir / "textures" / name
        tex = parse_texture_name(name)
        top = 0 if tex is None else _laid_out_top(zooms, layout, tex.zoom)
        if path.exists():
            pool.replace(top, path)
        lit = pack_dir / NIGHT_FOLDER / night_name(name)
        if lit.exists():
            nights = nights or NightPool(pack_dir)
            nights.replace(0, lit)
    if meta:
        meta["textures"], meta["seen"], meta["patchy"], meta["stale"] = {}, {}, [], {}
        meta.pop("grown", None)
        tmp = pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta))
        os.replace(tmp, pack_dir / MARKER)
    from .nightwork import ROADS_FOLDER

    _remove_tree(pack_dir / ROADS_FOLDER, lambda _: None, "roads")  # the night's, read again
    log(f"{len(built)} built textures back to the neutral one")
    return len(built)
