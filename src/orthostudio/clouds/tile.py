# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What a tile holds of its replaced clouds (``clouds.md`` 2.1).

The clouds a tile shows replaced are the clouds its textures were made with. The build that made
them writes them in the textures artefact's own ``manifest.json`` (``clouds``), beside the list of
its textures, and the pack places that description in the tile's folder with the textures, named
by their key, before the ``orthostudio.toml`` that names them (``pack.CLOUDS_DIR``). So what the
tile holds can never disagree with what X-Plane shows, a build stopped half way leaves the folder
naming the description before, and the tile keeps its clouds whatever the store loses.

A cloud there is whole: its outline, its source, its colours (``match``), and per texture it
reaches its frozen part (the source's pieces) and the digest of the tile's own pieces it was laid
on (``images``), plus the colours of the photo around it (``colours``). Every later build of the
tile lays it again from these alone, or undoes it, whole, when the photo under it changed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely import affinity, normalize, set_precision, to_wkt, union_all
from shapely.geometry import Polygon, box
from shapely.validation import make_valid

from orthostudio.home import osxp_home
from orthostudio.imagery.grid import TextureId, parse_texture_name
from orthostudio.install.library import default_library_path
from orthostudio.model import TileRef
from orthostudio.pipeline.filing import tile_home
from orthostudio.pipeline.home import default_chunks_root, default_tiles_root
from orthostudio.pipeline.pack import (
    CLOUDS_DIR,
    ORIGINALS,
    PARTS,
    cloud_record,
    pack_dir_name,
    pack_identity,
)

__all__ = [
    "COLOURS_REACH_M",
    "TileClouds",
    "artefact_originals",
    "artefact_parts",
    "colours_near",
    "entry_problem",
    "got_parts_dir",
    "list_path",
    "own_folder",
    "previews_dir",
    "read_tile",
    "tile_folder",
]

# -- where the cloud files are (a tile folder holds pack.CLOUD_FILES) ------------------------


def artefact_parts(out: Path) -> Path:
    """The frozen parts a textures artefact keeps."""
    return Path(out) / CLOUDS_DIR / PARTS


def artefact_originals(out: Path) -> Path:
    """Where a textures step makes the originals: its ``textures/`` comes under it."""
    return Path(out) / CLOUDS_DIR / ORIGINALS


def got_parts_dir(chunks_root: Path) -> Path:
    """The parts of the clouds whose images were got, beside the image pieces (``<data>/clouds/
    parts``), until a build keeps them."""
    return Path(chunks_root).parent / CLOUDS_DIR / PARTS


def previews_dir(tile: TileRef) -> Path:
    """The tile's picture and its clouds' previews (``<data>/clouds/<tile>``), made at will."""
    return default_chunks_root().parent / CLOUDS_DIR / tile.name


def list_path(tile: TileRef) -> Path:
    """The user's list of the tile's clouds (``$OSXP_HOME/clouds/<tile>.json``)."""
    return osxp_home() / CLOUDS_DIR / f"{tile.name}.json"


COLOURS_REACH_M = 1000.0
"""How far from a cloud the photo's colours count for it: the band its colours were matched on
(400 m) and a zone's soft edge, with room."""


@dataclass(frozen=True, slots=True)
class TileClouds:
    """What the tile's folder says of its clouds: the description of the textures it lays."""

    folder: Path
    textures_key: str
    shown: str = ""
    """What the folder shows, one digest (``pack.pack_identity``): the picture and the previews
    are named by it, so a new DSF or new textures make them again."""
    applied: tuple[dict[str, Any], ...] = ()
    """The clouds its textures show replaced, in laying order (:func:`entry_problem`)."""
    chunks: Mapping[str, str] = field(default_factory=dict)
    """Each texture's name with the digest of the pieces it was made of."""
    colours: Mapping[str, Any] | None = None
    """The photo's colours as the textures have them (``tile``: brightness, contrast,
    saturation; ``zones``); ``None`` for textures an earlier version made, which say neither
    these nor their pieces: such a tile is built again once before its clouds are looked at."""

    @property
    def described(self) -> bool:
        """Whether its textures say what a cloud is chosen on: their pieces and colours."""
        return self.colours is not None and bool(self.chunks)

    def held(self, cloud: str) -> dict[str, Any] | None:
        return next((c for c in self.applied if c["id"] == cloud), None)

    @property
    def textures(self) -> list[TextureId]:
        """Its textures, as their names say them."""
        return [parse_texture_name(n) for n in self.chunks]

    @property
    def textures_dir(self) -> Path:
        """The textures X-Plane shows."""
        return self.folder / "textures"

    @property
    def originals_dir(self) -> Path:
        """The textures holding a cloud as they are without their clouds."""
        return artefact_originals(self.folder) / "textures"

    @property
    def parts_dir(self) -> Path:
        """The frozen parts of the clouds it holds."""
        return artefact_parts(self.folder)


def tile_folder(tile: TileRef, *, workshop: Path, home: Path | None = None) -> Path:
    """The folder a build of ``tile`` writes: where it is filed (``filing.tile_home``), else the
    workshop's."""
    return Path(home) if home is not None else Path(workshop) / pack_dir_name(tile)


def own_folder(tile: TileRef, *, custom_scenery: Path | None) -> Path:
    """The tile's folder its clouds are kept for: the one the app's next build of it writes."""
    home, _gone = tile_home(
        tile,
        atelier=default_tiles_root(),
        custom_scenery=custom_scenery,
        library_path=default_library_path(),
    )
    return tile_folder(tile, workshop=default_tiles_root(), home=home)


def read_tile(folder: Path) -> TileClouds | None:
    """What the pack in ``folder`` holds of its clouds; ``None`` when there is no pack of ours
    there, or one built before its folder described its textures."""
    found = cloud_record(folder)
    if found is None:
        return None
    key, doc = found
    clouds = doc.get("clouds") or {}
    applied = tuple(c for c in clouds.get("applied") or () if entry_problem(c) is None)
    return TileClouds(
        folder=Path(folder),
        textures_key=key,
        shown=pack_identity(folder) or key,
        applied=applied,
        chunks={
            str(t["name"]): str(t["chunks"])
            for t in doc.get("textures") or ()
            if isinstance(t, dict) and t.get("chunks")
        },
        colours=doc.get("colours") if isinstance(doc.get("colours"), dict) else None,
    )


def entry_problem(c: Any) -> str | None:
    """Why ``c`` is not a whole cloud as a build takes it, or ``None``: ``{id, source, match,
    rings, parts: {texture: digest}, images: {texture: digest}, colours}``."""
    if not isinstance(c, Mapping):
        return "not an object"
    if not (isinstance(c.get("id"), str) and isinstance(c.get("source"), str)):
        return "no id or source"
    try:
        gain, shift = c["match"]
        if len(gain) != 3 or len(shift) != 3:
            return "a match is three gains and three shifts"
    except (KeyError, TypeError, ValueError):
        return "no match"
    rings = c.get("rings")
    if not (isinstance(rings, list) and rings and all(isinstance(r, list) for r in rings)):
        return "no rings"
    parts, images = c.get("parts"), c.get("images")
    if not (isinstance(parts, Mapping) and parts and isinstance(images, Mapping)):
        return "no parts"
    if set(parts) != set(images):
        return "parts and images of other textures"
    return None


def colours_near(
    rings: Sequence[Sequence[float]], tile: TileRef, colours: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The colours of the photo around a cloud, as data: the tile's own where some of its reach
    is under no zone, and each zone that reaches it, by its part in that reach inside the tile.
    ``colours`` are the photo's as a tile's textures say them (``{"tile": [brightness, contrast,
    saturation], "zones": [[ring, brightness, contrast, saturation], ...]}``, plain when
    ``None``); ``rings`` and the zones' rings are the zones' ring format (``[lat0, lon0, ...]``).
    A cloud's preview was matched on the photo with these colours: with others it shows lighter or
    darker than the photo around it, so another build with others there lays it no more."""
    tile_colours = (colours or {}).get("tile") or (0.0, 0.0, 0.0)
    zones = (colours or {}).get("zones") or ()
    cloud = union_all([_shape(r) for r in rings])
    k = math.cos(math.radians(cloud.centroid.y))  # metres alike on both axes there

    def flat(shape: Any) -> Any:
        return affinity.scale(shape, xfact=k, yfact=1.0, origin=(0, 0))

    cell = box(tile.lon, tile.lat, tile.lon + 1, tile.lat + 1)
    reach = flat(cloud).buffer(COLOURS_REACH_M / 111_320.0).intersection(flat(cell))
    near: list[list[Any]] = []
    covered: list[Any] = []
    for ring, brightness, contrast, saturation in zones:
        part = flat(_shape(ring)).intersection(reach)
        if part.area <= 1e-12:
            continue
        covered.append(part)
        shape = to_wkt(normalize(set_precision(part, 1e-7)), rounding_precision=7)
        near.append([shape, *_rounded((brightness, contrast, saturation))])
    bare = reach.difference(union_all(covered)).area > 1e-12 if covered else True
    return {"tile": _rounded(tile_colours) if bare else None, "zones": near}


def _shape(ring: Sequence[float]) -> Any:
    shape = Polygon([(float(ring[i + 1]), float(ring[i])) for i in range(0, len(ring) - 1, 2)])
    return shape if shape.is_valid else make_valid(shape)


def _rounded(values: Sequence[Any]) -> list[float]:
    return [round(float(v), 6) for v in values]
