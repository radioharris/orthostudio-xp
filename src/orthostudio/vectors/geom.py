"""The small geometry helpers of ``O4_Vector_Utils.py``, transcribed **once**.

Review 5 found ``ensure_MultiPolygon`` (``O4_Vector_Utils.py:779-793``) transcribed three
times (coastline, water, roads) and ``cut_to_tile`` (``:739-777``) twice with two different
signatures. They agreed, which is exactly the state in which one of them drifts unnoticed.
This module is the single transcription the three builders now import; no semantics changed,
the bodies are the ones P4 wave 1 was measured with.

Spec: ``docs/specs/vectors-assembly.md`` section 11 (shared geometry helpers).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from shapely import geometry
from shapely.geometry.base import BaseGeometry

__all__ = [
    "UNIT_SIDES",
    "UNIT_SQUARE",
    "cut_to_tile",
    "ensure_multilinestring",
    "ensure_multipolygon",
    "polygons_of",
    "wrap_local_x",
]

#: The tile in local coordinates, counter-clockwise, as ``cut_to_tile`` builds it
#: (``O4_Vector_Utils.py:743-753``).
UNIT_SQUARE = geometry.Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
#: Its boundary as a line, subtracted by ``strictly_inside`` (``:766-774``).
UNIT_SIDES = geometry.LineString([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])


def cut_to_tile(
    geom: BaseGeometry,
    xmin: float = 0.0,
    xmax: float = 1.0,
    ymin: float = 0.0,
    ymax: float = 1.0,
    *,
    strictly_inside: bool = False,
) -> BaseGeometry:
    """``O4_Vector_Utils.py:739-777``: intersect with the tile square, optionally open.

    ``strictly_inside`` subtracts the four sides, which is a *line*: it removes the parts of
    the input running **along** a side, and leaves an end point sitting on a side alone. That
    is what lets :func:`orthostudio.vectors.coast.coastline_to_multipolygon` recognise where a chain
    leaves the tile (``O4_Vector_Map.py:407``).
    """
    unit = xmin == 0.0 and xmax == 1.0 and ymin == 0.0 and ymax == 1.0
    box = (
        UNIT_SQUARE
        if unit
        else geometry.Polygon(
            [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]
        )
    )
    inside = geom.intersection(box)
    if not strictly_inside:
        return inside
    sides = (
        UNIT_SIDES
        if unit
        else geometry.LineString(
            [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]
        )
    )
    return inside.difference(sides)


def ensure_multilinestring(input_geometry: BaseGeometry) -> geometry.MultiLineString:
    """``O4_Vector_Utils.py:795-811``: anything to a MultiLineString, dropping other types."""
    if input_geometry.is_empty:
        return geometry.MultiLineString()
    kind = input_geometry.geom_type
    if kind == "MultiLineString":
        return input_geometry  # type: ignore[return-value]
    if kind in ("LineString", "LinearRing"):
        return geometry.MultiLineString([input_geometry])  # type: ignore[list-item]
    if "Collection" in kind:
        return geometry.MultiLineString(
            [g for g in input_geometry.geoms if g.geom_type in ("LineString", "LinearRing")]
        )
    return geometry.MultiLineString()


def ensure_multipolygon(input_geometry: BaseGeometry) -> geometry.MultiPolygon:
    """``O4_Vector_Utils.py:779-791``: anything to a MultiPolygon, dropping other types."""
    if input_geometry.is_empty:
        return geometry.MultiPolygon()
    kind = input_geometry.geom_type
    if kind == "MultiPolygon":
        return input_geometry  # type: ignore[return-value]
    if kind == "Polygon":
        return geometry.MultiPolygon([input_geometry])  # type: ignore[list-item]
    if "Collection" in kind:
        return geometry.MultiPolygon([g for g in input_geometry.geoms if g.geom_type == "Polygon"])
    return geometry.MultiPolygon()


def polygons_of(geom: BaseGeometry) -> list[geometry.Polygon]:
    """:func:`ensure_multipolygon` as a *list*, empty parts dropped.

    The form the water and road builders work with: they iterate the parts and never need the
    ``MultiPolygon`` wrapper. The only difference with :func:`ensure_multipolygon` is that an
    empty part of a ``MultiPolygon`` is dropped here and kept there, which is why both exist.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, geometry.Polygon):
        return [geom]
    return [
        part
        for part in getattr(geom, "geoms", ())
        if isinstance(part, geometry.Polygon) and not part.is_empty
    ]


def wrap_local_x(x: NDArray[np.floating]) -> NDArray[np.float64]:
    """Fold a tile-local longitude offset back to the shortest way round the globe.

    ``x = lon - tile.lon`` is what Ortho4XP computes (``O4_OSM_Utils.py:604-615``) and it is right
    everywhere but across the antimeridian: on tile ``+43+179`` a node at ``-179.9`` gives
    ``-358.9``, so a segment leaving the tile eastwards over 0.2 degree is *fabricated* as a
    segment running westwards across the whole tile (review 5). One turn of the globe is added
    or removed here, and nothing else: the test is exact and the arithmetic is a no-op -- not
    even a rounding -- for every value in ``[-180, 180]``, which is every tile but two.
    """
    out = np.asarray(x, dtype=np.float64)
    if out.size == 0:
        return out
    high = out > 180.0
    low = out < -180.0
    if not (high.any() or low.any()):
        return out
    out = out.copy()
    out[high] -= 360.0
    out[low] += 360.0
    return out
