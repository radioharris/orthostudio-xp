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

from orthostudio.textures.imprint import load_mask

__all__ = ["depth_ratio_u16", "node_bathy_from_distance_masks", "orthogrid", "st_coord_arrays"]

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
