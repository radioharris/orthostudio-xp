# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The replaced clouds of one textures step: which are laid, from what, and what the artefact
records of them (``clouds.md`` R5, R6).

A cloud is laid on the photo it was chosen on, whole, or not at all. Before anything is fetched
it is undone when the textures it reaches are not those it was laid in (another level, another
source, another layout), when the colours of the photo around it changed, or when one of its
frozen parts is found nowhere. Once the textures are made it is undone when one of them came of
other pieces than the ones it was laid on, and those textures are made again without it. Every
other is laid from its frozen parts, which the artefact keeps.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from orthostudio.clouds.tile import colours_near
from orthostudio.graph.digest import digest_bytes
from orthostudio.imagery.grid import TextureId, parse_texture_name, texture_name, wgs84_to_tile
from orthostudio.model import TileRef
from orthostudio.textures.clouds import CloudPatch, tile_box, window

__all__ = ["TileClouding", "find_part", "parts_dir", "patches_of", "standing"]

PART_SUFFIX = ".part"


def parts_dir(chunks_root: Path) -> Path:
    """Where the frozen parts are kept beside the image pieces: ``<data>/clouds/parts``."""
    return Path(chunks_root).parent / "clouds" / "parts"


def find_part(digest: str, folders: Iterable[Path]) -> Path | None:
    """The file of the part ``digest`` in the first of ``folders`` holding it whole."""
    for folder in folders:
        path = Path(folder) / f"{digest}{PART_SUFFIX}"
        try:
            if digest_bytes(path.read_bytes()) == digest:
                return path
        except OSError:
            continue
    return None


def patches_of(
    c: Mapping[str, Any],
    tile: TileRef,
    textures: Iterable[TextureId],
    source_zl: Callable[[TextureId], int],
) -> dict[str, CloudPatch]:
    """The cloud ``c`` (a :mod:`clouds.tile` entry) in each of ``textures`` it reaches, by
    texture name; ``source_zl`` gives the level its source is read at in a texture."""
    gain, shift = c["match"]
    match = (tuple(float(v) for v in gain), tuple(float(v) for v in shift))
    out: dict[str, CloudPatch] = {}
    for t in textures:
        rings = tuple(_ring_px(r, t) for r in c["rings"])
        box = tile_box(tile.lat, tile.lon, t)
        p = CloudPatch(c["id"], c["source"], source_zl(t), rings, match, box)  # type: ignore[arg-type]
        if window(p) is not None:
            out[texture_name(t)] = p
    return out


def _ring_px(ring: Sequence[float], t: TextureId) -> tuple[float, ...]:
    """A ring of the zones' format (``[lat0, lon0, ...]``) in the pixels of ``t``."""
    pts: list[float] = []
    for i in range(0, len(ring) - 1, 2):
        x, y = wgs84_to_tile(float(ring[i]), float(ring[i + 1]), t.zl)
        pts += [(x - t.til_x) * 256.0, (y - t.til_y) * 256.0]
    return tuple(pts)


def standing(
    c: Mapping[str, Any],
    tile: TileRef,
    textures: Iterable[TextureId],
    *,
    tile_colours: Sequence[float],
    zones: Sequence[Sequence[Any]],
    chunks: Mapping[str, str] | None = None,
) -> tuple[str | None, dict[str, CloudPatch]]:
    """Why the cloud ``c`` no longer stands on the photo the ``textures`` (and their pieces'
    ``chunks``, when known) make, or ``None``; and its patches in them. The one rule a build lays
    a cloud by, which the cloud mode judges a cloud's images by too."""
    parts = c["parts"]
    zl_of = {name: int(parts[name][0]) for name in parts}
    reached = patches_of(c, tile, textures, lambda t: zl_of.get(texture_name(t), t.zl))
    why = _moved(set(parts), set(reached))
    if (
        why is None
        and chunks is not None
        and any(chunks.get(n) != d for n, d in c["images"].items())
    ):
        why = "images"
    if why is None and colours_near(c["rings"], tile, tile_colours, zones) != c["colours"]:
        why = "colours"
    return why, reached


class TileClouding:
    """The clouds a textures step is given (``TileTexturesParams.clouds``), in laying order."""

    def __init__(
        self,
        tile: TileRef,
        clouds: Sequence[Mapping[str, Any]],
        textures: Iterable[TextureId],
        *,
        tile_colours: Sequence[float],
        zones: Sequence[Sequence[Any]],
        folders: Sequence[Path],
        out: Path,
    ) -> None:
        self.tile = tile
        self.given = list(clouds)
        self.undone: dict[str, str] = {}
        self.folders = [Path(out) / "clouds" / "parts", *folders]
        self.out = Path(out)
        self.laid: dict[str, dict[str, tuple[CloudPatch, Path]]] = {}
        by_name = {texture_name(t): t for t in textures}
        for c in self.given:
            parts = c["parts"]
            why, reached = standing(
                c, tile, by_name.values(), tile_colours=tile_colours, zones=zones
            )
            files = {name: find_part(str(parts[name][1]), self.folders) for name in parts}
            if why is None and None in files.values():
                why = "parts"
            if why is not None:
                self.undone[c["id"]] = why
                continue
            self.laid[c["id"]] = {n: (reached[n], f) for n, f in files.items() if f is not None}

    def layers(self) -> dict[TextureId, tuple[tuple[CloudPatch, Path], ...]]:
        """What each texture lays, in the clouds' order, of the clouds not undone."""
        out: dict[TextureId, list[tuple[CloudPatch, Path]]] = {}
        for c in self.given:
            for name, laid in self.laid.get(c["id"], {}).items():
                out.setdefault(parse_texture_name(name), []).append(laid)
        return {t: tuple(v) for t, v in out.items()}

    def undo_changed(self, outcomes: Iterable[Any]) -> bool:
        """Undo every laid cloud one of whose textures came of other pieces than those it was
        laid on (``TextureOutcome.chunks``); whether any was, for the textures to be made
        again without them."""
        chunks = {o.name: o.chunks for o in outcomes}
        changed = [
            c["id"]
            for c in self.given
            if c["id"] in self.laid and any(chunks.get(n) != d for n, d in c["images"].items())
        ]
        for cid in changed:
            self.undone[cid] = "images"
            del self.laid[cid]
        return bool(changed)

    def manifest(self) -> dict[str, Any]:
        """What the artefact's ``manifest.json`` says of its clouds, their parts kept in it."""
        if not self.given:
            return {}
        keep = self.out / "clouds" / "parts"
        keep.mkdir(parents=True, exist_ok=True)
        for laid in self.laid.values():
            for _patch, path in laid.values():
                dest = keep / path.name
                if not dest.exists():
                    try:
                        os.link(path, dest)
                    except OSError:
                        shutil.copyfile(path, dest)
        return {
            "clouds": {
                "applied": [dict(c) for c in self.given if c["id"] in self.laid],
                "undone": dict(self.undone),
            }
        }


def _moved(then: set[str], now: set[str]) -> str | None:
    """Why a cloud laid in the textures ``then`` cannot be laid in ``now``, or ``None``."""
    if then == now:
        return None
    if not now:
        return "textures"
    zl = {parse_texture_name(n).zl for n in then} != {parse_texture_name(n).zl for n in now}
    if zl:
        return "level"
    source = {parse_texture_name(n).provider for n in then} != {
        parse_texture_name(n).provider for n in now
    }
    return "source" if source else "textures"
