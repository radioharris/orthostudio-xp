"""The 1001 x 1001 curvature-tolerance weight map Triangle4XP reads (`.weight`).

Spec: ``docs/specs/mesh-build.md`` section 3. Origin: ``O4_Mesh_Utils.py:132-228``
(``build_curv_tol_weight_map``).

The map says, per 1/1000 degree cell of the tile, by how much the curvature tolerance must be
divided there: 1 everywhere, ``curvature_tol / apt_curv_tol`` over airports,
``curvature_tol / coast_curv_tol`` within ``coast_curv_ext`` kilometres of a coastline node.
Ortho4XP paints one rectangle per airport and one per coastline node in a Python loop;
OrthoStudio XP paints the same integer rectangles with a two-dimensional difference array, which is
exact (the two edges of a window are rounded independently, so a fixed-footprint ``maximum_filter``
is not equivalent -- see the spec) and an order of magnitude faster.

Nothing here reads a file or the network: the airport boxes and the coastline nodes are
arguments.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "AIRPORTS_FORMAT",
    "COASTLINE_FORMAT",
    "LAT_TO_M",
    "M_TO_LAT",
    "WEIGHT_SIDE",
    "build_weight_map",
    "m_to_lon",
    "read_airport_bounds",
    "read_coastline_nodes",
    "weight_stats",
    "write_coastline_nodes",
    "write_weight_file",
]

EARTH_RADIUS_M = 6378137
"""``O4_Geo_Utils.py:4``."""

LAT_TO_M = math.pi * EARTH_RADIUS_M / 180
"""Metres per degree of latitude (``O4_Geo_Utils.py:5``): 111319.49079327358."""

M_TO_LAT = 1 / LAT_TO_M
"""``O4_Geo_Utils.py:6``."""

KM_PER_DEGREE_LAT_ORTHO4XP = 111.12
"""The hard-coded constant Ortho4XP uses for the coastline window only
(``O4_Mesh_Utils.py:211``)."""

WEIGHT_SIDE = 1001
"""Triangle4XP reads exactly ``1001 * 1001`` float32 (``Triangle4XP.c``, one ``fread``)."""

AIRPORTS_FORMAT = "osxp-airports-1"
COASTLINE_FORMAT = "osxp-coastline-nodes-1"


def m_to_lon(lat: float) -> float:
    """Degrees of longitude per metre at latitude ``lat`` (``O4_Geo_Utils.py:13-15``)."""
    return M_TO_LAT / math.cos(math.pi * lat / 180)


def _paint(
    covered: NDArray[np.bool_],
    rowmin: NDArray[np.int64],
    rowmax: NDArray[np.int64],
    colmin: NDArray[np.int64],
    colmax: NDArray[np.int64],
) -> None:
    """Mark the union of the inclusive rectangles ``[rowmin, rowmax] x [colmin, colmax]``.

    Two-dimensional difference array: ``+1`` at the top-left corner of every rectangle,
    ``-1`` past its right and bottom edges, ``+1`` past both; the double prefix sum then
    counts how many rectangles cover each cell, and a positive count is their union. Exact
    by construction and independent of the number of rectangles.

    A rectangle whose bounds cross (``rowmin > rowmax`` or ``colmin > colmax``) is dropped:
    that is Ortho4XP's empty slice for a box that falls entirely outside the tile.
    """
    keep = (rowmin <= rowmax) & (colmin <= colmax)
    if not keep.all():
        rowmin, rowmax, colmin, colmax = (a[keep] for a in (rowmin, rowmax, colmin, colmax))
    if rowmin.size == 0:
        return
    side = covered.shape[0]
    diff = np.zeros((side + 1, side + 1), dtype=np.int32)
    np.add.at(diff, (rowmin, colmin), 1)
    np.add.at(diff, (rowmin, colmax + 1), -1)
    np.add.at(diff, (rowmax + 1, colmin), -1)
    np.add.at(diff, (rowmax + 1, colmax + 1), 1)
    counts = np.cumsum(np.cumsum(diff, axis=0), axis=1)
    covered |= counts[:side, :side] > 0


def _round(values: NDArray[np.float64]) -> NDArray[np.int64]:
    """Python's ``round`` on floats: nearest integer, halves to even (numpy's ``rint``)."""
    return np.rint(values).astype(np.int64)


def build_weight_map(
    *,
    lat: int,
    curvature_tol: float,
    apt_curv_tol: float,
    apt_curv_ext: float,
    coast_curv_tol: float,
    coast_curv_ext: float,
    apt_bounds: NDArray[np.float64] | None = None,
    coast_nodes: NDArray[np.float64] | None = None,
    lon: int = 0,
) -> NDArray[np.float32]:
    """Build the ``(1001, 1001)`` float32 weight map (spec section 3).

    ``apt_bounds`` is an ``(A, 4)`` array of ``(xmin, ymin, xmax, ymax)`` airport bounding
    boxes in **tile-relative** degrees; ``coast_nodes`` an ``(C, 2)`` array of **absolute**
    ``(lon, lat)`` coastline nodes, unclipped. Row 0 is the north edge of the tile, column 0
    its west edge. ``lat`` is the tile's integer latitude (Ortho4XP scales with it, not with the
    tile centre); ``lon`` its integer longitude, only used to bring the nodes back to
    tile-relative coordinates.
    """
    weight = np.ones((WEIGHT_SIDE, WEIGHT_SIDE), dtype=np.float32)
    last = WEIGHT_SIDE - 1

    if apt_curv_tol != curvature_tol and apt_curv_tol > 0 and apt_bounds is not None:
        boxes = np.asarray(apt_bounds, dtype=np.float64).reshape(-1, 4)
        if boxes.size:
            x_shift = 1000 * apt_curv_ext * m_to_lon(lat)
            y_shift = 1000 * apt_curv_ext * M_TO_LAT
            colmin = np.maximum(_round((boxes[:, 0] - x_shift) * 1000), 0)
            colmax = np.minimum(_round((boxes[:, 2] + x_shift) * 1000), last)
            rowmax = np.minimum(_round(((1 - boxes[:, 1]) + y_shift) * 1000), last)
            rowmin = np.maximum(_round(((1 - boxes[:, 3]) - y_shift) * 1000), 0)
            covered = np.zeros((WEIGHT_SIDE, WEIGHT_SIDE), dtype=bool)
            _paint(covered, rowmin, rowmax, colmin, colmax)
            # Ortho4XP assigns (it does not take a maximum): every airport carries the same
            # value, so painting their union with it is the same thing.
            weight[covered] = np.float32(curvature_tol / apt_curv_tol)

    if coast_curv_tol != curvature_tol and coast_nodes is not None:
        nodes = np.asarray(coast_nodes, dtype=np.float64).reshape(-1, 2)
        if nodes.size:
            inside = (
                (nodes[:, 0] >= lon)
                & (nodes[:, 0] <= lon + 1)
                & (nodes[:, 1] >= lat)
                & (nodes[:, 1] <= lat + 1)
            )
            nodes = nodes[inside]
        if nodes.size:
            x_shift = 1000 * coast_curv_ext * m_to_lon(lat)
            y_shift = coast_curv_ext / KM_PER_DEGREE_LAT_ORTHO4XP
            colmin = np.maximum(_round((nodes[:, 0] - lon - x_shift) * 1000), 0)
            colmax = np.minimum(_round((nodes[:, 0] - lon + x_shift) * 1000), last)
            rowmax = np.minimum(_round((lat + 1 - nodes[:, 1] + y_shift) * 1000), last)
            rowmin = np.maximum(_round((lat + 1 - nodes[:, 1] - y_shift) * 1000), 0)
            covered = np.zeros((WEIGHT_SIDE, WEIGHT_SIDE), dtype=bool)
            _paint(covered, rowmin, rowmax, colmin, colmax)
            weight[covered] = np.maximum(
                weight[covered], np.float32(curvature_tol / coast_curv_tol)
            )
    return weight


def write_weight_file(path: str | Path, weight: NDArray[np.float32]) -> None:
    """Write the raw float32 raster Triangle4XP reads (``.tmp`` + rename)."""
    if weight.shape != (WEIGHT_SIDE, WEIGHT_SIDE):
        raise ValueError(f"weight map must be ({WEIGHT_SIDE}, {WEIGHT_SIDE}), got {weight.shape}")
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    np.ascontiguousarray(weight, dtype=np.float32).tofile(tmp)
    tmp.replace(path)


def weight_stats(weight: NDArray[np.float32]) -> tuple[int, float]:
    """``(cells different from 1, maximum)``: the two numbers the P0 measurement reports."""
    return int((weight != 1).sum()), float(weight.max())


# -- inputs ----------------------------------------------------------------------------------


def read_airport_bounds(vectors_dir: str | Path, tile_name: str) -> NDArray[np.float64]:
    """Airport bounding boxes of the tile, ``(A, 4)`` in tile-relative degrees.

    Read from ``airports.json``, which the vector stage writes when the tile has airports (spec
    section 3.2). Returns an empty array when it is absent or does not parse, as Ortho4XP does
    (``O4_Mesh_Utils.py:140-149``); the caller records ``MESH_WEIGHT_MAP_INCOMPLETE``.
    """
    native = Path(vectors_dir) / "airports.json"
    if not native.is_file():
        return np.zeros((0, 4), dtype=np.float64)
    try:
        payload = json.loads(native.read_text(encoding="utf-8"))
        rows = [[float(v) for v in entry["bounds"]] for entry in payload.get("airports", ())]
    except (OSError, ValueError, KeyError, TypeError):
        return np.zeros((0, 4), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64).reshape(-1, 4)


def read_coastline_nodes(path: str | Path) -> NDArray[np.float64]:
    """Read ``coastline.npz`` (spec section 2.3): ``(C, 2)`` absolute ``(lon, lat)``."""
    path = Path(path)
    if path.is_dir():
        path = path / "coastline.npz"
    with np.load(path, allow_pickle=False) as z:
        fmt = str(z["format"]) if "format" in z else ""
        if fmt != COASTLINE_FORMAT:
            raise ValueError(f"{path}: format {fmt!r}, expected {COASTLINE_FORMAT!r}")
        return np.ascontiguousarray(z["nodes"], dtype=np.float64).reshape(-1, 2)


def write_coastline_nodes(path: str | Path, nodes: Iterable[tuple[float, float]]) -> None:
    """Write a ``coastline.npz`` (used by the tests and by the future OSM client)."""
    array = np.asarray(list(nodes), dtype=np.float64).reshape(-1, 2)
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez(f, format=np.array(COASTLINE_FORMAT), nodes=array)
    tmp.replace(path)
