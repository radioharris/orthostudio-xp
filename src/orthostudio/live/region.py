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
a neutral texture of each size. X-Plane must find a texture at the size it will keep: a new zoom
lays the pack out again (:func:`relayout`), with X-Plane not running, before it loads any.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
from collections import defaultdict
from collections.abc import Callable
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


@dataclass(frozen=True)
class Zooms:
    """The zoom the textures are built to: AutoOrtho's max_zoom and max_zoom_near_airports, None
    for the mesh's own (a file of 4096 px, the pack as it was before zooms could be set)."""

    max_zoom: int | None = None
    max_zoom_airports: int | None = None

    def top(self, zoom: int) -> int:
        """The sharpest level of a texture of ``zoom``: its file is ``dds.size_of(top)`` px."""
        cap = zoom_for(zoom, self.max_zoom, self.max_zoom_airports)  # type: ignore[arg-type]
        return 0 if cap is None else file_top(zoom, cap)


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


def region_zooms(pack_dir: Path) -> Zooms:
    """The zoom the pack's textures are laid out for."""
    try:
        meta = json.loads((pack_dir / REGION_MARKER).read_text())
    except (OSError, ValueError):
        return Zooms()
    return Zooms(**meta.get("zooms", {}))


def _textures_by_zoom(pack_dir: Path) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    for entry in os.scandir(pack_dir / "textures"):
        if entry.name.endswith(".dds") and not entry.name.startswith("."):
            tex = parse_texture_name(entry.name)
            if tex is not None:
                out[tex.zoom].append(entry.name)
    return out


def needs_relayout(pack_dir: Path, zooms: Zooms) -> bool:
    """Whether some textures of the pack are laid out for another zoom than ``zooms``."""
    old = region_zooms(pack_dir)
    return any(old.top(z) != zooms.top(z) for z in _textures_by_zoom(pack_dir))


def relayout(pack_dir: Path, zooms: Zooms, log: Callable[[str], None] = print) -> int:
    """Lay the pack out for ``zooms``: every texture of a zoom whose size changes, in a file of the
    new size. A neutral one becomes a clone of the neutral texture of that size; a built one keeps
    its levels from the one built down (those sharper than the new file are dropped), nothing
    downloaded. Only while X-Plane does not run: it keeps a texture at the size it first read.
    Returns how many textures changed."""
    from .clone import replace_with_clone
    from .pack import MARKER, load_built
    from .texture import resize

    marker = pack_dir / REGION_MARKER
    meta = json.loads(marker.read_text())
    old = Zooms(**meta.get("zooms", {}))
    by_zoom = _textures_by_zoom(pack_dir)
    changed = [z for z in sorted(by_zoom) if old.top(z) != zooms.top(z)]
    built_meta = load_built(pack_dir)
    built = built_meta.get("textures", {})
    t0 = time.monotonic()
    done = 0
    for zoom in changed:
        old_top, new_top = old.top(zoom), zooms.top(zoom)
        log(
            f"ZL{zoom} textures: {dds.size_of(old_top)} px -> {dds.size_of(new_top)} px "
            f"({len(by_zoom[zoom])} textures, nothing to download)"
        )
        neutral = ensure_neutral(pack_dir, new_top)
        for name in by_zoom[zoom]:
            path = pack_dir / "textures" / name
            level = built.get(name)
            if level is None:
                replace_with_clone(str(neutral), str(path))
            else:
                built[name] = resize(path, level, old_top, new_top, neutral)
            done += 1
            if done % 50_000 == 0:
                log(f"  {done} textures...")
    if changed and built_meta:
        tmp = pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(built_meta))
        os.replace(tmp, pack_dir / MARKER)
    meta["zooms"] = asdict(zooms)
    marker.write_text(json.dumps(meta))
    if changed:
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
    source: Path,
    pack_dir: Path,
    force: bool = False,
    log: Callable[[str], None] = print,
    zooms: Zooms | None = None,
) -> dict:
    """Make ``pack_dir`` hold the whole region of ``source``; keep the textures already built.
    ``zooms``: what the textures are laid out for, the first time (:func:`relayout` after)."""
    marker = pack_dir / REGION_MARKER
    if marker.exists() and not force:
        meta = json.loads(marker.read_text())
        if meta.get("source") == str(source):
            if meta.get("version", 1) < REGION_VERSION:
                _upgrade(pack_dir, log)
                meta["version"] = REGION_VERSION
                marker.write_text(json.dumps(meta))
            return meta
    t0 = time.monotonic()
    log(f"setting up the region of {source.name} in {pack_dir} (once)")
    (pack_dir / "_live").mkdir(parents=True, exist_ok=True)
    _clone_dir(source / "Earth nav data", pack_dir / "Earth nav data", log)
    _clone_dir(source / "terrain", pack_dir / "terrain", log)

    textures = pack_dir / "textures"
    textures.mkdir(exist_ok=True)
    have = set(os.listdir(textures))
    masks = 0
    for entry in os.scandir(source / "textures"):
        if not entry.name.endswith(".dds") and entry.name not in have:
            clone(entry.path, str(textures / entry.name))
            masks += 1
    log(f"  {masks} water masks and other files cloned")

    zooms = zooms or Zooms()
    write_neutral(pack_dir)
    names = texture_names(pack_dir / "terrain")
    added = 0
    for _i, name in enumerate(sorted(names - have)):
        tex = parse_texture_name(name)
        top = zooms.top(tex.zoom) if tex is not None else 0
        clone(str(ensure_neutral(pack_dir, top)), str(textures / name))
        added += 1
        if added % 50_000 == 0:
            log(f"  {added} neutral textures...")
    log(f"  {len(names)} textures: {added} neutral clones, {len(names & have)} already built")

    meta = {
        "source": str(source),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "textures": len(names),
        "version": REGION_VERSION,
        "zooms": asdict(zooms),
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
    zooms = region_zooms(pack_dir)
    for name in built:
        path = pack_dir / "textures" / name
        tex = parse_texture_name(name)
        top = zooms.top(tex.zoom) if tex is not None else 0
        if path.exists():
            replace_with_clone(str(ensure_neutral(pack_dir, top)), str(path))
    if meta:
        meta["textures"], meta["seen"] = {}, {}
        tmp = pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta))
        os.replace(tmp, pack_dir / MARKER)
    log(f"{len(built)} built textures back to the neutral one")
    return len(built)
