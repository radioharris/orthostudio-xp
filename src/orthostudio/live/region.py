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
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
from collections.abc import Callable
from pathlib import Path

from . import dds
from .clone import clone
from .geo import parse_texture_name
from .texture import write_sparse

NEUTRAL_RGB = (86, 92, 74)
NEUTRAL_LEVEL = 5  # the "built level" of a neutral texture: coarser than any real one
REGION_MARKER = Path("_live") / "region.json"
NEUTRAL_FILE = Path("_live") / "neutral.dds"
REGION_VERSION = 2  # 2: the neutral texture complete at every level (1 had holes: black up close)


def neutral_levels(top: int = 0, rgb=NEUTRAL_RGB) -> list[bytes]:
    """Levels ``top`` to 12 of a uniform BC1 texture."""
    r, g, b = rgb
    c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    block = struct.pack("<HHI", c, c, 0)
    return [block * (length // dds.BLOCK_BYTES) for _, length in dds.mip_layout()[top:]]


def write_neutral(pack_dir: Path) -> Path:
    neutral = pack_dir / NEUTRAL_FILE
    write_sparse(neutral, 0, neutral_levels(0))
    return neutral


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
    source: Path, pack_dir: Path, force: bool = False, log: Callable[[str], None] = print
) -> dict:
    """Make ``pack_dir`` hold the whole region of ``source``; keep the textures already built."""
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

    neutral = write_neutral(pack_dir)
    names = texture_names(pack_dir / "terrain")
    added = 0
    for _i, name in enumerate(sorted(names - have)):
        clone(str(neutral), str(textures / name))
        added += 1
        if added % 50_000 == 0:
            log(f"  {added} neutral textures...")
    log(f"  {len(names)} textures: {added} neutral clones, {len(names & have)} already built")

    meta = {
        "source": str(source),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "textures": len(names),
        "version": REGION_VERSION,
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
    neutral = str(pack_dir / NEUTRAL_FILE)
    for name in built:
        path = pack_dir / "textures" / name
        if path.exists():
            replace_with_clone(neutral, str(path))
    if meta:
        meta["textures"], meta["seen"] = {}, {}
        tmp = pack_dir / MARKER.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta))
        os.replace(tmp, pack_dir / MARKER)
    log(f"{len(built)} built textures back to the neutral one")
    return len(built)
