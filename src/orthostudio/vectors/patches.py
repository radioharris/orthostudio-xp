"""Patches: a folder of ``*.patch.osm`` files and OBJ8 objects, read as Ortho4XP reads them.

Specification: ``docs/specs/vectors-water-roads.md`` section 4. Origin:
``O4_Vector_Map.py:639-873`` (``include_patches``) and ``:875-968`` (``keep_obj8``).

A patch is a hand-made JOSM file that overrides the elevation of a piece of ground: a closed
way becomes an ``INTERP_ALT`` polygon whose vertices carry the altitude the tags ask for, an
open way a ``DUMMY`` line that only constrains the triangulation. An OBJ8 object brings its
triangles in the same way, anchored at a given longitude, latitude, altitude and heading.

Ortho4XP calls this from ``include_airports``, before the runways, so that a patched airport is
left alone by the airport builder. A build gives the stage no patch folder yet: Ortho4XP's
``Patches/`` is not read (decision 0010).

The shared helpers (``Altitudes``, ``M_TO_LAT``, ``zero_alt``) come from ``water.py``, which
is where P4 wave 1 parked what all three builders need.

Nothing here touches the network.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from shapely import geometry, ops
from shapely.geometry.base import BaseGeometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.noding import MARKERS, Layer
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.water import M_TO_LAT, Altitudes, EventHandler, zero_alt

__all__ = [
    "DEFAULT_CELL_SIZE_M",
    "DEFAULT_STEEPNESS",
    "PATCH_SUFFIX",
    "Obj8",
    "PatchResult",
    "Runs",
    "build_patch_layers",
    "plane_profile",
    "read_obj8",
    "read_patch_file",
    "spline_profile",
    "tanh_profile",
]

PATCH_SUFFIX = ".patch.osm"
"""``O4_Vector_Map.py:655``: only these files are read as patches."""

DEFAULT_CELL_SIZE_M = 10.0
"""Length of a ramp cell when the way has no ``cell_size`` tag (``:729``)."""

DEFAULT_STEEPNESS = 2.0
"""``steepness`` of the ``tanh`` profile when the way does not give one (``:737``)."""

RAMP_DEG_TO_M = 111120.0
"""``:753``: the ramp length uses this constant and ``cos(lat)``, not ``cos(lat + 0.5)``."""


def tanh_profile(alpha: float, x: NDArray[np.float64]) -> NDArray[np.float64]:
    """``(tanh((x - 0.5) * alpha) / tanh(0.5 * alpha) + 1) / 2`` (``:640-641``)."""
    return (np.tanh((x - 0.5) * alpha) / math.tanh(0.5 * alpha) + 1) / 2


def spline_profile(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """``3x^2 - 2x^3`` (``:643-644``)."""
    return 3 * x**2 - 2 * x**3


def plane_profile(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """``x`` (``:646-647``)."""
    return x


@dataclass(frozen=True, slots=True)
class Obj8:
    """One OBJ8 object placed on the tile: its triangles, their altitudes and their seeds."""

    triangles: tuple[geometry.LinearRing, ...] = ()
    altitudes: tuple[NDArray[np.float64], ...] = ()
    seeds: NDArray[np.float64] = field(default_factory=lambda: np.zeros((0, 2)))
    area: BaseGeometry = field(default_factory=geometry.Polygon)


@dataclass(frozen=True, slots=True)
class PatchResult:
    """The patch contribution to the PSLG of one tile."""

    layers: tuple[Layer, ...] = ()
    """The ways in reading order, consecutive ways of the same marker grouped into one layer."""
    seeds: dict[str, NDArray[np.float64]] = field(default_factory=dict)
    """``{"INTERP_ALT": (S, 2)}``: one seed per closed patch and per OBJ8 triangle."""
    area: BaseGeometry = field(default_factory=geometry.Polygon)
    """``patches_area``: what wave 2 must not cover with airport surfaces."""
    names: tuple[str, ...] = ()
    """``patches_list``: one entry per patch file and per OBJ8 directory (``:657, :838``)."""
    counts: dict[str, int] = field(default_factory=dict)
    """``files``, ``ways``, ``polygons``, ``lines``, ``objects``, ``triangles``, ``skipped``."""


# -- layer accumulation ------------------------------------------------------------------------


class Runs:
    """Ways in insertion order, grouped into as few ``node_layers`` layers as possible.

    Public since wave 2: the airport encoder groups its passes exactly the same way
    (``orthostudio.airports_vec.encode``), and the alternative was a third transcription of the same
    six lines -- the class of duplication review 5 removed (blocker 5 of ``aptencode``).

    A layer carries one marker, and :func:`orthostudio.vectors.noding.node_layers` walks polygon
    rings after line parts, so a run is closed whenever the marker or the kind changes (spec 7.5).
    """

    def __init__(self) -> None:
        self.layers: list[Layer] = []
        self._marker: int | None = None
        self._kind: str = ""
        self._geoms: list[BaseGeometry] = []
        self._z: list[NDArray[np.float64]] = []

    def add(self, geom: BaseGeometry, marker: int, z: NDArray[np.float64]) -> None:
        """Append one way; ``z`` has one value per coordinate of ``geom``."""
        kind = "polygon" if isinstance(geom, geometry.Polygon) else "line"
        if self._marker is not None and (marker != self._marker or kind != self._kind):
            self.close()
        self._marker, self._kind = marker, kind
        self._geoms.append(geom)
        self._z.append(np.asarray(z, dtype=np.float64).reshape(-1))

    def close(self) -> None:
        """Flush the current run into a layer."""
        if self._marker is None or not self._geoms:
            self._marker, self._geoms, self._z = None, [], []
            return
        collection: BaseGeometry = (
            geometry.MultiPolygon(self._geoms)  # type: ignore[arg-type]
            if self._kind == "polygon"
            else geometry.MultiLineString([np.array(g.coords) for g in self._geoms])
        )
        self.layers.append((collection, self._marker, np.concatenate(self._z)))
        self._marker, self._geoms, self._z = None, [], []


# -- one patch file ----------------------------------------------------------------------------


def _way_coords(data: OsmData, way_id: int, tile: TileRef) -> NDArray[np.float64]:
    """Local coordinates of a patch way, **unrounded** (``:681-684``).

    ``OsmData.node_coords`` rounds to 7 decimals as ``OSM_to_MultiPolygon`` does; a patch is
    the one place where Ortho4XP subtracts the tile origin and keeps every digit.
    """
    raw = np.array([data.nodes[i] for i in data.ways[way_id]], dtype=np.float64).reshape(-1, 2)
    return raw - np.array([[tile.lon, tile.lat]], dtype=np.float64)


def _way_order(data: OsmData) -> list[int]:
    """Tagged ways first, then untagged ones (``:673-676``), in reading order inside each."""
    ids = sorted(data.first["w"], reverse=True)  # -1, -2, ... is the reading order
    tagged = [i for i in ids if i in data.tags["w"] and data.tags["w"][i]]
    untagged = [i for i in ids if i not in tagged]
    return tagged + untagged


def _ramp(
    way: NDArray[np.float64], tags: dict[str, str], tile: TileRef, alt: Callable[..., NDArray]
) -> tuple[NDArray[np.float64], NDArray[np.float64], int] | None:
    """The ``altitude_high`` / ``altitude_low`` way of ``:711-770``; ``None`` when malformed."""
    if len(way) != 5 or (way[0] != way[-1]).all():
        return None
    short_high, short_low = way[-2:], way[1:3]
    try:
        altitude_high = float(tags["altitude_high"])
        altitude_low = float(tags["altitude_low"])
    except (KeyError, ValueError):
        altitude_high = float(alt(short_high).mean())
        altitude_low = float(alt(short_low).mean())
    try:
        cell_size = float(tags["cell_size"])
    except (KeyError, ValueError):
        cell_size = DEFAULT_CELL_SIZE_M
    try:
        alpha = float(tags["steepness"])
    except (KeyError, ValueError):
        alpha = DEFAULT_STEEPNESS
    name = tags.get("profile", "plane")

    def tanh_with_alpha(x: NDArray[np.float64]) -> NDArray[np.float64]:
        return tanh_profile(alpha, x)

    profile: Callable[[NDArray[np.float64]], NDArray[np.float64]] = plane_profile
    if "tanh" in name:
        profile = tanh_with_alpha
    elif name == "spline":
        profile = spline_profile
    vect = (short_high[0] + short_high[1] - short_low[0] - short_low[1]) / 2
    length = (
        math.sqrt(vect[0] ** 2 * math.cos(tile.lat * math.pi / 180) ** 2 + vect[1] ** 2)
        * RAMP_DEG_TO_M
    )
    cuts_long = int(length / cell_size)
    if not cuts_long:
        # Ortho4XP leaves ``alti_way`` unset here (``:750``) and crashes or reuses the previous
        # way's altitudes; OrthoStudio XP falls back on the DEM (spec 7, wanted difference).
        return way, np.asarray(alt(way), dtype=np.float64), 0
    cuts_long += 1
    steps = np.arange(cuts_long, dtype=np.float64) / cuts_long
    side_a = way[0] + steps[:, None] * (way[1] - way[0])
    side_b = way[2] + steps[:, None] * (way[3] - way[2])
    refined = np.vstack([side_a, way[1], side_b, way[3], way[4]])
    profile_z = altitude_high - profile(np.arange(cuts_long + 1, dtype=np.float64) / cuts_long) * (
        altitude_high - altitude_low
    )
    altitudes = np.hstack([profile_z, profile_z[::-1], profile_z[0]])
    return refined, altitudes, cuts_long


def _way_altitudes(
    way: NDArray[np.float64], tags: dict[str, str], alt: Callable[..., NDArray]
) -> NDArray[np.float64]:
    """The altitude tags of a simple way (``:685-717``), first match wins."""
    original = np.asarray(alt(way), dtype=np.float64).reshape(-1)
    if "cst_alt_abs" in tags:
        return np.full(len(way), float(tags["cst_alt_abs"]))
    if "cst_alt_rel" in tags:
        return np.full(len(way), float(original.mean()) + float(tags["cst_alt_rel"]))
    if "var_alt_rel" in tags:
        return original + float(tags["var_alt_rel"])
    if "altitude" in tags:  # deprecated, kept for backward compatibility (``:700-710``)
        try:
            return np.full(len(way), float(tags["altitude"]))
        except ValueError:
            return np.full(len(way), float(original.mean()))
    return original


def read_patch_file(
    path: Path | str,
    tile: TileRef,
    dem: Altitudes | None = None,
    *,
    runs: Runs | None = None,
    on_event: EventHandler | None = None,
) -> tuple[Runs, list[NDArray[np.float64]], BaseGeometry, dict[str, int]]:
    """Read one ``*.patch.osm`` into layers, seeds, area and counts (``:655-822``)."""
    runs = Runs() if runs is None else runs
    alt = zero_alt if dem is None else dem.alt_vec
    seeds: list[NDArray[np.float64]] = []
    area: BaseGeometry = geometry.Polygon()
    counts = {"ways": 0, "polygons": 0, "lines": 0, "skipped": 0}
    try:
        data = OsmData.load(Path(path), layer=None, tile=tile)
    except (OSError, ValueError, KeyError) as exc:
        if on_event is not None:
            on_event(
                OsxpError("OSM_PATCH_INVALID", context={"path": str(path), "reason": str(exc)})
            )
        return runs, seeds, area, counts
    for way_id in _way_order(data):
        way = _way_coords(data, way_id, tile)
        if len(way) < 2:
            counts["skipped"] += 1
            continue
        tags = dict(data.tags["w"].get(way_id, {}))
        counts["ways"] += 1
        cuts_long = 0
        ramp = _ramp(way, tags, tile, alt) if "altitude_high" in tags else None
        if "altitude_high" in tags and ramp is None:
            counts["skipped"] += 1
            continue
        if ramp is not None:
            way, altitudes, cuts_long = ramp
        else:
            altitudes = _way_altitudes(way, tags, alt)
            node_tags = data.tags["n"]
            original = np.asarray(alt(way), dtype=np.float64).reshape(-1)
            for i, node_id in enumerate(data.ways[way_id]):
                ntags = node_tags.get(node_id)
                if not ntags:
                    continue
                if "alt_abs" in ntags:
                    altitudes[i] = float(ntags["alt_abs"])
                elif "alt_rel" in ntags:
                    altitudes[i] = original[i] + float(ntags["alt_rel"])
        if (way[0] == way[-1]).all():
            polygon = geometry.Polygon(way)
            if not polygon.is_valid or not polygon.area:
                counts["skipped"] += 1
                if on_event is not None:
                    on_event(
                        OsxpError(
                            "OSM_PATCH_INVALID",
                            context={"path": str(path), "reason": f"way {way_id} is not a polygon"},
                        )
                    )
                continue
            area = area.union(polygon)
            runs.add(polygon, MARKERS["INTERP_ALT"], altitudes)
            seeds.append(np.array(polygon.representative_point().coords[0], dtype=np.float64))
            counts["polygons"] += 1
            if cuts_long > 1:
                bars = [geometry.LineString([way[i], way[-2 - i]]) for i in range(1, cuts_long)]
                bar_z = np.concatenate(
                    [[altitudes[i], altitudes[-2 - i]] for i in range(1, cuts_long)]
                )
                for bar, k in zip(bars, range(len(bars)), strict=True):
                    runs.add(bar, MARKERS["DUMMY"], bar_z[2 * k : 2 * k + 2])
        else:
            runs.add(geometry.LineString(way), MARKERS["DUMMY"], altitudes)
            counts["lines"] += 1
    return runs, seeds, area, counts


# -- OBJ8 --------------------------------------------------------------------------------------


def read_obj8(path: Path | str, tile: TileRef, dem: Altitudes | None = None) -> Obj8 | None:
    """One OBJ8 file anchored by its first line (``keep_obj8``, ``:875-968``).

    ``None`` when the first line does not carry a usable ``ANCHOR``. The index arrays are read
    as one flat array and ``TRIS offset count`` slices it, which is what the format means;
    Ortho4XP indexes the ``IDX`` *lines* instead and only agrees for a single ``TRIS`` at offset 0
    (spec 4.6).
    """
    text = Path(path).read_text(errors="replace").splitlines()
    if not text or "ANCHOR" not in text[0]:
        return None
    fields = text[0].split()[1:]
    try:
        values = [float(v) for v in fields]
    except ValueError:
        return None
    if len(values) == 4:
        lon_anchor, lat_anchor, alt_anchor, heading = values
    elif len(values) == 3:
        lon_anchor, lat_anchor, heading = values
        alt = zero_alt if dem is None else dem.alt_vec
        alt_anchor = float(alt(np.array([[lon_anchor - tile.lon, lat_anchor - tile.lat]]))[0])
    else:
        return None
    latscale = M_TO_LAT
    lonscale = latscale / math.cos(lat_anchor * math.pi / 180)
    cos_h, sin_h = math.cos(heading * math.pi / 180), math.sin(heading * math.pi / 180)
    vertices: list[tuple[float, float, float]] = []
    indices: list[int] = []
    tris: list[tuple[int, int]] = []
    for line in text[1:]:
        head = line[:4]
        if head[:2] == "VT":
            xo, yo, zo = (float(s) for s in line.split()[1:4])
            big_x = xo * cos_h - zo * sin_h
            big_z = xo * sin_h + zo * cos_h
            x = round(lon_anchor + lonscale * big_x - tile.lon, 7)
            y = round(lat_anchor - latscale * big_z - tile.lat, 7)
            vertices.append((x, y, yo + alt_anchor))
        elif head[:3] == "IDX":
            indices.extend(int(v) for v in line.split()[1:])
        elif head == "TRIS":
            offset, count = (int(v) for v in line.split()[1:3])
            tris.append((offset, count))
    rings: list[geometry.LinearRing] = []
    altitudes: list[NDArray[np.float64]] = []
    seeds: list[NDArray[np.float64]] = []
    polygons: list[geometry.Polygon] = []
    for offset, count in tris:
        chunk = indices[offset : offset + count]
        for j in range(len(chunk) // 3):
            try:
                a, b, c = (vertices[i] for i in chunk[3 * j : 3 * j + 3])
            except IndexError:
                continue
            if a in (b, c) or b == c:
                continue
            ring = geometry.LinearRing([a[:2], b[:2], c[:2], a[:2]])
            rings.append(ring)
            altitudes.append(np.array([a[2], b[2], c[2], a[2]], dtype=np.float64))
            seeds.append((np.array(a[:2]) + np.array(b[:2]) + np.array(c[:2])) / 3)
            polygons.append(geometry.Polygon(ring))
    return Obj8(
        tuple(rings),
        tuple(altitudes),
        np.array(seeds, dtype=np.float64).reshape(-1, 2),
        ops.unary_union(polygons) if polygons else geometry.Polygon(),
    )


# -- the builder -------------------------------------------------------------------------------


def build_patch_layers(
    directory: Path | str,
    tile: TileRef,
    dem: Altitudes | None = None,
    *,
    on_event: EventHandler | None = None,
) -> PatchResult:
    """Every patch of ``directory`` as layers, seeds, area and names (``:649-873``).

    ``directory`` is ``Patches/<tile>``; a missing directory gives an empty result, as it does
    in Ortho4XP (``:652-653``), but it is *reported* through ``on_event`` so that a folder that is
    absent is never indistinguishable from a folder that is empty (review 5). Files are read
    in sorted order, not in ``os.listdir`` order (spec 7.4).
    """
    root = Path(directory)
    if not root.is_dir():
        if on_event is not None:
            on_event(
                OsxpError(
                    "OSM_PATCH_INVALID",
                    context={"path": str(root), "reason": "no such directory"},
                )
            )
        return PatchResult(counts={"files": 0, "ways": 0, "objects": 0, "triangles": 0})
    runs = Runs()
    seeds: list[NDArray[np.float64]] = []
    area: BaseGeometry = geometry.Polygon()
    names: list[str] = []
    counts = {
        "files": 0,
        "ways": 0,
        "polygons": 0,
        "lines": 0,
        "objects": 0,
        "triangles": 0,
        "skipped": 0,
    }
    for path in sorted(p for p in root.iterdir() if p.name.endswith(PATCH_SUFFIX)):
        names.append(path.name[: -len(PATCH_SUFFIX)])
        counts["files"] += 1
        said = 0

        def watch(error: OsxpError) -> None:
            nonlocal said
            said += 1
            if on_event is not None:
                on_event(error)

        runs, file_seeds, file_area, file_counts = read_patch_file(
            path, tile, dem, runs=runs, on_event=watch
        )
        seeds.extend(file_seeds)
        area = area.union(file_area)
        for key, value in file_counts.items():
            counts[key] += value
        used = file_counts["polygons"] + file_counts["lines"]
        if not used and not said and on_event is not None:
            # A file that changes nothing was silent: its ways were counted as skipped and the
            # tile came out as if no patch had been given (found while trying a patch written by
            # hand whose <nd> elements were all on one line, which this reader does not see,
            # 2026-09-17). A file that already said what it refused is not repeated.
            on_event(
                OsxpError(
                    "OSM_PATCH_INVALID",
                    context={"path": str(path), "reason": "no way of this file could be used"},
                )
            )
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        names.append(folder.name)
        for path in sorted(folder.iterdir()):
            obj = read_obj8(path, tile, dem)
            if obj is None:
                continue
            counts["objects"] += 1
            counts["triangles"] += len(obj.triangles)
            for ring, z in zip(obj.triangles, obj.altitudes, strict=True):
                runs.add(ring, MARKERS["INTERP_ALT"], z)
            seeds.extend(obj.seeds)
            area = area.union(obj.area)
    runs.close()
    return PatchResult(
        layers=tuple(runs.layers),
        seeds=({"INTERP_ALT": np.array(seeds, dtype=np.float64).reshape(-1, 2)} if seeds else {}),
        area=area,
        names=tuple(names),
        counts=counts,
    )
