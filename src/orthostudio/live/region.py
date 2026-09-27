# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A whole AutoOrtho region as one pack X-Plane can load from the start, and stream into later.

X-Plane must always find every file a DSF names. The pack holds every DSF and ``.ter`` of the region
(APFS clones of AutoOrtho's, whole directories at once: 0.2 s for the 1,981 DSFs of ``z_ao_eur``, a
minute for its 684,455 ``.ter``), its water masks, and every texture: a real one where one was
built, else a clone of one neutral file, a dull uniform colour at every level (one 11 MB file whose
blocks every clone shares, so it costs nothing more). The streamer replaces textures with real ones
as the aircraft comes, each written over a clone of the neutral texture: whatever level X-Plane
reads, it finds imagery or the neutral colour, never black.

Each texture's file is laid out for the zoom set (:class:`Zooms`, AutoOrtho's max_zoom and
max_zoom_near_airports): 4096 px at the mesh's zoom, smaller below it, 8192 px one zoom above, with
a neutral texture of each size. The size is set once, before X-Plane loads anything, and never
changes while it runs: X-Plane reads a file's size when it loads its tile and often keeps it (an
fs_usage trace, 2026-09-28: textures grown from 4096 to 8192 px in flight were not read at 8192 px).
A new zoom lays the pack out again (:func:`relayout`), with X-Plane not running.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from . import dds
from .clone import clone
from .geo import parse_texture_name
from .rings import file_top, zoom_for
from .texture import write_sparse

NEUTRAL_RGB = (86, 92, 74)
NEUTRAL_LEVEL = 5  # the "built level" of a neutral texture: coarser than any real one
REGION_MARKER = Path("_live") / "region.json"
NEUTRAL_FILE = Path("_live") / "neutral.dds"
REGION_VERSION = 2  # 2: the neutral texture complete at every level (1 had holes: black up close)
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
    an 8192 px file built coarser than 8192 px is written again too, for the copy above its sharpest
    level."""
    from .pack import load_built

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
            uncopied = layout < LAYOUT_FIXED and now < 0 and level is not None and level > now
            if size != now or uncopied:
                change.append((name, now))
        if change:
            changes[zoom] = change
    return changes


def needs_relayout(pack_dir: Path, zooms: Zooms) -> bool:
    """Whether some textures of the pack are laid out for another zoom than ``zooms``: X-Plane must
    not be running while they change size."""
    return bool(_layout_plan(pack_dir, zooms))


def relayout(pack_dir: Path, zooms: Zooms, log: Callable[[str], None] = print) -> int:
    """Lay the pack out for ``zooms``: every texture whose file changes size, in a file of the new
    size. A neutral one becomes a clone of the neutral texture of that size; a built one keeps its
    levels from the one built down (those sharper than the new file are dropped), nothing
    downloaded. Only while X-Plane does not run: it keeps a texture at the size it first read.
    Records the zoom and the layout even when no file changes. Returns how many textures changed."""
    from .clone import replace_with_clone
    from .pack import MARKER, load_built
    from .texture import resize

    marker = pack_dir / REGION_MARKER
    meta = json.loads(marker.read_text())
    changes = _layout_plan(pack_dir, zooms)
    built_meta = load_built(pack_dir)
    built = built_meta.get("textures", {})
    t0 = time.monotonic()
    done = 0
    for zoom in sorted(changes):
        tops = Counter(top for _, top in changes[zoom])
        after = ", ".join(f"{dds.size_of(top)} px" for top in sorted(tops))
        log(f"ZL{zoom} textures: {len(changes[zoom])} to {after} (nothing to download)")
        for name, new_top in changes[zoom]:
            path = pack_dir / "textures" / name
            neutral = ensure_neutral(pack_dir, new_top)
            level = built.get(name)
            if level is None:
                replace_with_clone(str(neutral), str(path))
            else:  # the file's size from its header: what it is, whatever was recorded
                built[name] = resize(path, level, dds.read_top(path), new_top, neutral)
            done += 1
            if done % 50_000 == 0:
                log(f"  {done} textures...")
    if built_meta:
        built_meta.pop("grown", None)  # every file at its size now: nothing grows any more
        tmp = pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(built_meta))
        os.replace(tmp, pack_dir / MARKER)
    meta["zooms"] = asdict(zooms)
    meta["layout"] = LAYOUT_FIXED
    marker.write_text(json.dumps(meta))
    if done:
        log(f"textures laid out for the zoom in {time.monotonic() - t0:.0f} s")
    return done


def _upgrade(pack_dir: Path, log: Callable[[str], None]) -> None:
    """From version 1: the complete neutral texture, every neutral clone made again from it, every
    built texture's larger levels made neutral instead of a hole."""
    from .clone import replace_with_clone
    from .pack import load_built
    from .texture import downgrade

    t0 = time.monotonic()
    log(f"updating the region in {pack_dir}: textures never black (once, a few minutes)")
    neutral = write_neutral(pack_dir)
    built = load_built(pack_dir).get("textures", {})
    textures = pack_dir / "textures"
    redone = 0
    for entry in os.scandir(textures):
        if not entry.name.endswith(".dds") or entry.name.startswith("."):
            continue
        level = built.get(entry.name)
        if level is None:
            replace_with_clone(str(neutral), entry.path)
        elif level > 0:
            downgrade(Path(entry.path), level, neutral)
        redone += 1
        if redone % 50_000 == 0:
            log(f"  {redone} textures...")
    log(f"  {redone} textures updated in {time.monotonic() - t0:.0f} s")


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
    """Clone into ``dst`` what of ``src`` it lacks: a whole folder where ``dst`` has none of that
    name, file by file where both have one. Returns how many were cloned."""
    done = 0
    for entry in os.scandir(src):
        target = dst / entry.name
        if entry.is_dir():
            if not target.exists():
                clone(entry.path, str(target))
                done += 1
            else:
                done += _merge_dir(Path(entry.path), target)
        elif not target.exists():
            clone(entry.path, str(target))
            done += 1
    return done


def _clone_dir(src: Path, dst: Path, log: Callable[[str], None]) -> None:
    """Replace ``dst`` by a clone of ``src`` (one call for the whole tree)."""
    t0 = time.monotonic()
    old = dst.with_name(dst.name + ".old")
    if dst.exists():
        dst.rename(old)
    clone(str(src), str(dst))
    if old.exists():
        shutil.rmtree(old)
    log(f"  {dst.name}: cloned in {time.monotonic() - t0:.0f} s")


def setup_region(
    source: Path | Sequence[Path],
    pack_dir: Path,
    force: bool = False,
    log: Callable[[str], None] = print,
    zooms: Zooms | None = None,
) -> dict:
    """Make ``pack_dir`` hold the whole of the AutoOrtho regions of ``source`` (one, or several:
    Europe, North America...); keep the textures already built. A region added since is merged
    in, once; one taken away sets the pack up again. ``zooms``: what the textures are laid out
    for, the first time (:func:`relayout` after)."""
    sources = [source] if isinstance(source, Path) else list(source)
    keys = [str(s) for s in sources]
    marker = pack_dir / REGION_MARKER
    old: dict = json.loads(marker.read_text()) if marker.exists() else {}
    had = sources_of(old)
    if old and not force and set(had) == set(keys):
        if old.get("version", 1) < REGION_VERSION:
            _upgrade(pack_dir, log)
            old["version"] = REGION_VERSION
            marker.write_text(json.dumps(old))
        return old
    t0 = time.monotonic()
    names_of = ", ".join(s.name for s in sources)
    adding = bool(had) and set(had) < set(keys)  # only regions to add: merged in
    if adding:
        new = [s for s in sources if str(s) not in had]
        log(f"adding {', '.join(s.name for s in new)} to the region in {pack_dir} (once)")
    else:
        new = sources
        log(f"setting up the region of {names_of} in {pack_dir} (once)")
        (pack_dir / "_live").mkdir(parents=True, exist_ok=True)
        _clone_dir(new[0] / "Earth nav data", pack_dir / "Earth nav data", log)
        _clone_dir(new[0] / "terrain", pack_dir / "terrain", log)
        new = new[1:]
    for extra in new:  # a DSF or a terrain another region has already stays that region's
        merged = _merge_dir(extra / "Earth nav data", pack_dir / "Earth nav data")
        merged += _merge_dir(extra / "terrain", pack_dir / "terrain")
        log(f"  {extra.name}: {merged} folders and files merged in")

    textures = pack_dir / "textures"
    textures.mkdir(exist_ok=True)
    have = set(os.listdir(textures))
    masks = 0
    for region in sources:
        for entry in os.scandir(region / "textures"):
            if not entry.name.endswith(".dds") and entry.name not in have:
                clone(entry.path, str(textures / entry.name))
                have.add(entry.name)
                masks += 1
    log(f"  {masks} water masks and other files cloned")

    # a pack that is there keeps its layout, the textures added included: a new zoom lays them all
    # out again together (relayout)
    if old:
        zooms, layout = Zooms(**old.get("zooms", {})), old.get("layout", 1)
    else:
        zooms, layout = zooms or Zooms(), LAYOUT_FIXED

    write_neutral(pack_dir)
    names = texture_names(pack_dir / "terrain")
    added = 0
    for _i, name in enumerate(sorted(names - have)):
        tex = parse_texture_name(name)
        top = _laid_out_top(zooms, layout, tex.zoom) if tex is not None else 0
        clone(str(ensure_neutral(pack_dir, top)), str(textures / name))
        added += 1
        if added % 50_000 == 0:
            log(f"  {added} neutral textures...")
    log(f"  {len(names)} textures: {added} neutral clones, {len(names & have)} already built")

    meta = {
        **old,
        "source": keys[0],
        "sources": keys,
        "created": old.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "textures": len(names),
        "version": REGION_VERSION,
        "zooms": asdict(zooms),
        "layout": layout,
    }
    marker.write_text(json.dumps(meta))
    log(f"region ready in {time.monotonic() - t0:.0f} s")
    return meta


def reset_built(pack_dir: Path, log: Callable[[str], None] = print) -> int:
    """Every built texture back to the neutral one, and forgotten: the pack as the first time.

    Only the textures the streamer recorded as built are touched (``_live/ortho.json``), so a reset
    takes seconds where setting the region up again takes minutes."""
    from .clone import replace_with_clone
    from .pack import MARKER, load_built

    meta = load_built(pack_dir)
    built = meta.get("textures", {})
    zooms, layout = region_layout(pack_dir)
    for name in built:
        path = pack_dir / "textures" / name
        tex = parse_texture_name(name)
        top = 0 if tex is None else _laid_out_top(zooms, layout, tex.zoom)
        if path.exists():
            replace_with_clone(str(ensure_neutral(pack_dir, top)), str(path))
    if meta:
        meta["textures"], meta["seen"], meta["patchy"] = {}, {}, []
        meta.pop("grown", None)
        tmp = pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta))
        os.replace(tmp, pack_dir / MARKER)
    log(f"{len(built)} built textures back to the neutral one")
    return len(built)
