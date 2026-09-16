"""Region seeds: the points Triangle4XP floods an attribute from.

A seed is a point inside a polygon plus an attribute value. Triangle4XP triangulates the
PSLG, drops one seed per region and lets its attribute plague the triangles until an edge
carrying that bit stops it (``docs/specs/vectors-pslg.md`` section 1). Ortho4XP drops one
seed per *encoded polygon*, at ``representative_point()`` (``O4_Vector_Utils.py:409-424``).

Two consequences shape this module:

* a seed belongs to the layer builder, not to the assembler: only the builder knows which
  polygons it really encoded (after ``cut_to_tile``, ``simplify``, the area filter and
  ``orient``). :func:`polygon_seeds` is the shared primitive, :class:`SeedSet` the
  accumulator the assembler fills in layer order;
* a map with no seed at all is not a map with no region: Ortho4XP then floods the whole tile with
  SEA when the elevation raster is flat at sea level (:func:`default_seeds`).

Spec: ``docs/specs/vectors-assembly.md`` section 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry.base import BaseGeometry

from orthostudio.vectors.noding import MARKERS

__all__ = [
    "CENTRE_SEED",
    "MARKER_NAMES",
    "MIN_LAND_ALTITUDE",
    "OUTSIDE_SEED",
    "SeedSet",
    "default_seeds",
    "marker_label",
    "marker_name",
    "polygon_seeds",
    "seed_rows",
]

MARKER_NAMES: dict[int, str] = {value: name for name, value in MARKERS.items()}
"""Attribute value to attribute name (``O4_Vector_Utils.py:44-54`` is a bijection)."""

OUTSIDE_SEED = (1000.0, 1000.0)
"""Default seed of a tile with relief: outside the tile, so it floods nothing (``:162-163``)."""

CENTRE_SEED = (0.5, 0.5)
"""Default seed of a tile whose raster never reaches 1 m: everything is sea (``:164-166``)."""

MIN_LAND_ALTITUDE = 1.0
"""``tile.dem.alt_dem.max() >= 1`` separates the two defaults (``O4_Vector_Map.py:161``)."""

_EMPTY = np.zeros((0, 2), dtype=np.float64)


def marker_name(marker: int) -> str:
    """``16 -> "RUNWAY"``; an unnamed combination is an error, Ortho4XP never produces one."""
    try:
        return MARKER_NAMES[int(marker)]
    except KeyError:
        raise ValueError(f"no attribute name for marker {marker} (not in {MARKER_NAMES})") from None


def marker_label(marker: int) -> str:
    """Readable form of an *edge* marker, which is a bitwise OR: ``3 -> "WATER|SEA"``.

    Seeds and layers carry a single attribute and use :func:`marker_name`; an edge carries
    everything that covers it (``docs/specs/vectors-pslg.md`` 2.5), so ``WATER|SEA`` is a
    lake shore that is also coastline, not an error.
    """
    value = int(marker)
    if value == 0:
        return MARKER_NAMES[0]
    names = [name for bit, name in sorted(MARKER_NAMES.items()) if bit and value & bit]
    rest = value & ~sum(bit for bit in MARKER_NAMES if bit)
    if rest:
        names.append(f"0x{rest:x}")
    return "|".join(names)


def polygon_seeds(geometry: BaseGeometry) -> tuple[NDArray[np.float64], int]:
    """One representative point per polygon of ``geometry``, in polygon order.

    ``representative_point()`` is ``point_on_surface``: a point guaranteed inside the
    polygon, unlike the centroid. Ortho4XP calls it inside the encoding loop and catches whatever
    GEOS raises on a broken polygon, keeping the edges and losing the seed
    (``O4_Vector_Utils.py:419-424``); the count of those is returned here so the stage can
    report it instead of losing it in a log.

    Returns ``((K, 2) seeds, number of polygons that produced none)``.
    """
    polygons = shapely.get_parts(shapely.get_parts(geometry))
    polygons = polygons[shapely.get_type_id(polygons) == shapely.GeometryType.POLYGON]
    if len(polygons) == 0:
        return _EMPTY, 0
    try:
        points = shapely.point_on_surface(polygons)
    except shapely.errors.GEOSException:
        kept, skipped = [], 0
        for polygon in polygons:
            try:
                kept.append(shapely.point_on_surface(polygon))
            except shapely.errors.GEOSException:
                skipped += 1
        if not kept:
            return _EMPTY, skipped
        return shapely.get_coordinates(np.asarray(kept, dtype=object)), skipped
    good = shapely.is_valid_input(points) & ~shapely.is_missing(points) & ~shapely.is_empty(points)
    return shapely.get_coordinates(points[good]), int((~good).sum())


@dataclass(slots=True)
class SeedSet:
    """Seeds accumulated by attribute value, in insertion order (``Vector_Map.seeds``).

    Ortho4XP keys them by attribute *name* and sorts by value when writing; keying by value here
    is the same order (the mapping is a bijection) and refuses an unnamed combination of bits
    instead of filing it under a made-up name.
    """

    by_marker: dict[int, list[NDArray[np.float64]]] = field(default_factory=dict)
    skipped: int = 0
    """Polygons whose representative point GEOS refused (spec 4.1)."""

    def add(self, marker: int, seeds: NDArray[np.floating] | None) -> None:
        """Append the seeds of one encoded layer under its attribute value."""
        if seeds is None:
            return
        points = np.asarray(seeds, dtype=np.float64).reshape(-1, 2)
        if len(points) == 0:
            return
        marker_name(marker)  # refuse an unnamed combination before it reaches the file
        self.by_marker.setdefault(int(marker), []).append(points)

    def add_polygons(self, marker: int, geometry: BaseGeometry) -> None:
        """Convenience for a builder that hands over its polygons rather than its seeds."""
        points, skipped = polygon_seeds(geometry)
        self.skipped += skipped
        self.add(marker, points)

    def __len__(self) -> int:
        return sum(len(chunk) for chunks in self.by_marker.values() for chunk in chunks)

    def to_dict(self) -> dict[int, NDArray[np.float64]]:
        """Attribute value -> ``(K, 2)`` seeds, concatenated in insertion order."""
        return {marker: np.concatenate(chunks) for marker, chunks in self.by_marker.items()}

    def finalise(self, max_altitude: float) -> dict[int, NDArray[np.float64]]:
        """The seed map to write: the accumulated seeds, or the default when there is none."""
        return self.to_dict() if len(self) else default_seeds(max_altitude)

    def counts(self) -> dict[str, int]:
        """Number of seeds per attribute name, for ``stats.json``."""
        return {
            marker_name(marker): sum(len(chunk) for chunk in chunks)
            for marker, chunks in sorted(self.by_marker.items())
        }


def default_seeds(max_altitude: float) -> dict[int, NDArray[np.float64]]:
    """The single SEA seed of a tile with no vector region at all (``O4_Vector_Map.py:160-166``).

    ``max_altitude`` is the maximum of the *whole* elevation raster, margins included, which
    is what ``tile.dem.alt_dem.max()`` is. At or above 1 m the seed is placed outside the
    tile, so nothing is flooded and the tile stays land; below, it sits at the centre and the
    tile is entirely sea.
    """
    point = OUTSIDE_SEED if max_altitude >= MIN_LAND_ALTITUDE else CENTRE_SEED
    return {MARKERS["SEA"]: np.array([point], dtype=np.float64)}


def seed_rows(seeds: dict[int, NDArray[np.float64]]) -> NDArray[np.float64]:
    """``(S, 3)`` rows ``x, y, marker`` in the order the ``.poly`` writes them.

    Sorted by attribute value, stable, so that seeds of one attribute keep their insertion
    order (``O4_Vector_Utils.py:596-615``). Same order as
    ``orthostudio.vectors.triangle_files.write_poly_file``; this function exists to check it.
    """
    rows = [
        np.column_stack([points, np.full(len(points), marker, dtype=np.float64)])
        for marker, points in seeds.items()
        if len(points)
    ]
    if not rows:
        return np.zeros((0, 3), dtype=np.float64)
    table = np.concatenate(rows)
    return table[np.argsort(table[:, 2], kind="stable")]
