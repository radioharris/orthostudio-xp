# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Per-node water depth ratio (``O4_Bathymetry.py:8-13`` and ``:187-223``).

Spec: ``docs/specs/dsf-terrain-assignment.md`` 3.3. Without distance masks (the default) every
non-coast water node gets ratio 1 and every coast node 0.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import shapely

from orthostudio.textures.imprint import load_mask

__all__ = [
    "SHORE_SHALLOW_M",
    "depth_ratio_u16",
    "near_shore",
    "node_bathy_from_distance_masks",
    "orthogrid",
    "st_coord_arrays",
]

MaskLookup = Callable[[int, int], "Path | None"]

_SEA_BIT = 4


def orthogrid(lat: np.ndarray, lon: np.ndarray, zl: int) -> tuple[np.ndarray, np.ndarray]:
    """``wgs84_to_orthogrid`` (``O4_Geo_Utils.py:127-134``) on arrays: ``(til_x, til_y)`` int64.

    Same float64 operations as the scalar version; ``int()`` truncation is ``astype(int64)``
    (the values are non-negative for the web-mercator range).
    """
    ratio_x = lon / 180
    ratio_y = np.log(np.tan((90 + lat) * np.pi / 360)) / np.pi
    mult = 2 ** (zl - 5)
    til_x = ((ratio_x + 1) * mult).astype(np.int64) * 16
    til_y = ((1 - ratio_y) * mult).astype(np.int64) * 16
    return til_x, til_y


def st_coord_arrays(
    lat: np.ndarray, lon: np.ndarray, tex_x: np.ndarray, tex_y: np.ndarray, zl: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``st_coord`` (``O4_Geo_Utils.py:137-150``) on arrays, clamped to [0, 1]."""
    ratio_x = lon / 180
    ratio_y = np.log(np.tan((90 + lat) * np.pi / 360)) / np.pi
    mult = np.exp2(zl.astype(np.float64) - 5)
    s = (ratio_x + 1) * mult - (tex_x // 16)
    t = 1 - ((1 - ratio_y) * mult - tex_y // 16)
    return np.clip(s, 0, 1), np.clip(t, 0, 1)


def node_bathy_from_distance_masks(
    coords: np.ndarray,
    node_types: np.ndarray,
    mask_zl: int,
    distance_masks: MaskLookup | None,
) -> np.ndarray:
    """``compute_depth_ratio_bounds_from_masks`` (``O4_Bathymetry.py:187-223``): uint8 per node.

    255 everywhere, replaced for sea nodes by the pixel of the ``_dist.png`` mask of their
    texture at ``mask_zl`` when ``distance_masks`` returns a file for it.
    """
    n = len(coords)
    bathy = np.full(n, 255, dtype=np.uint8)
    if distance_masks is None:
        return bathy
    sea = np.flatnonzero((node_types & _SEA_BIT) != 0)
    if len(sea) == 0:
        return bathy
    lon, lat = coords[sea, 0], coords[sea, 1]
    til_x, til_y = orthogrid(lat, lon, mask_zl)
    key = til_x * (1 << 32) + til_y
    uniq, inv = np.unique(key, return_inverse=True)
    for k, packed in enumerate(uniq):
        m_til_x, m_til_y = int(packed >> 32), int(packed & 0xFFFFFFFF)
        path = distance_masks(m_til_x, m_til_y)
        if path is None:
            continue
        mask = load_mask(Path(path))
        sel = inv == k
        s, t = st_coord_arrays(
            lat[sel],
            lon[sel],
            np.full(int(sel.sum()), m_til_x),
            np.full(int(sel.sum()), m_til_y),
            np.full(int(sel.sum()), mask_zl),
        )
        pixx = (s * 4095).astype(np.int64)
        pixy = ((1 - t) * 4095).astype(np.int64)
        bathy[sea[sel]] = mask[pixy, pixx]
    return bathy


def depth_ratio_u16(
    node_is_coast: np.ndarray, node_bathy: np.ndarray, ratio_bathy: float
) -> np.ndarray:
    """``int(65535 * set_depth_ratio(n))`` per node (``O4_Bathymetry.py:8-13``, ``:815``).

    ``max(min(10 * ratio_bathy * bathy / 255, 1), 0.1)``, 0 for coast nodes, truncated.
    """
    r = np.minimum(10 * float(ratio_bathy) * node_bathy.astype(np.float64) / 255, 1.0)
    r = np.maximum(r, 0.1)
    r[node_is_coast] = 0.0
    return (65535 * r).astype(np.uint16)


SHORE_SHALLOW_M = 1.0
"""A water node closer than this to the shore is shallow, wherever it comes from. Ortho4XP marks
every water node that touches no land deep, and X-Plane then reads the seabed under it from its
own raster; a node a hair's breadth off the shore got that seabed at the shore itself: 200 m under
Groom Lake at Homey Airport (KXTA), seen in X-Plane as blue triangles, then as blue needles along
the straight shore an aerodrome's cut makes (2026-10-15). Thin slivers along such a shore put
mesh nodes there (7 to 34 cm off it), and the recut put its midpoints on it."""


def near_shore(
    coords: np.ndarray,
    tris: np.ndarray,
    tri_types: np.ndarray,
    node_is_coast: np.ndarray,
    lat: float,
    metres: float = SHORE_SHALLOW_M,
) -> np.ndarray:
    """``(N,)`` bool: the water nodes, not on the shore, closer than ``metres`` to it.

    The shore is the edges of the land triangles whose two ends are coast nodes. Only the nodes of
    a water triangle with a coast corner are measured: a node that close to the shore has one."""
    out = np.zeros(len(coords), dtype=bool)
    land = tris[tri_types == 0]
    water = tris[tri_types != 0]
    if len(land) == 0 or len(water) == 0:
        return out
    edges = np.concatenate([land[:, [0, 1]], land[:, [1, 2]], land[:, [2, 0]]])
    edges = edges[node_is_coast[edges[:, 0]] & node_is_coast[edges[:, 1]]]
    if len(edges) == 0:
        return out
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    shore_tris = water[node_is_coast[water].any(axis=1)]
    candidates = np.unique(shore_tris)
    candidates = candidates[~node_is_coast[candidates]]
    if len(candidates) == 0:
        return out
    kx, ky = 111320.0 * np.cos(np.radians(lat)), 111320.0
    x, y = coords[:, 0] * kx, coords[:, 1] * ky
    lines = shapely.linestrings(
        np.stack(
            [
                np.stack([x[edges[:, 0]], y[edges[:, 0]]], 1),
                np.stack([x[edges[:, 1]], y[edges[:, 1]]], 1),
            ],
            1,
        )
    )
    tree = shapely.STRtree(lines)
    found, _ = tree.query_nearest(
        shapely.points(x[candidates], y[candidates]), max_distance=metres, return_distance=True
    )
    out[candidates[np.unique(found[0])]] = True
    return out
