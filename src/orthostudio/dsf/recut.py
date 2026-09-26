# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Triangle type remap and X-Plane 12 recut of coastal water triangles, vectorised.

Port of ``O4_DSF_Utils.py:475-480`` (type remap) and ``O4_Bathymetry.py:16-183``
(``recut_water_tris``). Spec: ``docs/specs/dsf-terrain-assignment.md`` 3.1-3.2. The node and
triangle numbering of Ortho4XP is reproduced exactly (it drives the pool order of the DSF).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["RecutMesh", "recut_water_tris", "remap_tri_types"]

LAND, INLAND, SEA = 0, 1, 2


def remap_tri_types(
    tri_attr: np.ndarray, mesh_version: float, use_masks_for_inland: bool
) -> np.ndarray:
    """Attribute bits to 0 land / 1 inland water / 2 sea (``O4_DSF_Utils.py:475-480``)."""
    has_water = 7 if mesh_version >= 1.3 else 3
    t = tri_attr.astype(np.int64) & has_water
    out = np.zeros(len(t), dtype=np.uint8)
    water = t != 0
    sea = water & ((t > 1) | bool(use_masks_for_inland))
    out[water] = INLAND
    out[sea] = SEA
    return out


@dataclass
class RecutMesh:
    """The mesh after the recut: nodes and triangles in Ortho4XP order."""

    coords: np.ndarray
    """``(N, 5)`` float64: lon, lat, z, u, v (``node_coords`` of Ortho4XP)."""
    node_types: np.ndarray
    """``(N,)`` uint8 bit field: 1 land, 2 inland water, 4 sea (triangles touching the node)."""
    node_is_coast: np.ndarray
    """``(N,)`` bool: land bit and a water bit."""
    tris: np.ndarray
    """``(M, 3)`` int64."""
    tri_types: np.ndarray
    """``(M,)`` uint8 in {0, 1, 2}."""

    @property
    def n_nodes(self) -> int:
        return len(self.coords)

    @property
    def n_tris(self) -> int:
        return len(self.tris)


def _node_types(n_nodes: int, tris: np.ndarray, tri_types: np.ndarray) -> np.ndarray:
    types = np.zeros(n_nodes, dtype=np.uint8)
    for t in (LAND, INLAND, SEA):
        nodes = np.unique(tris[tri_types == t])
        types[nodes] |= np.uint8(1 << t)
    return types


def recut_water_tris(coords: np.ndarray, tris: np.ndarray, tri_types: np.ndarray) -> RecutMesh:
    """``O4_Bathymetry.py:16-183`` with numpy; same nodes, same triangles, same order."""
    coords = np.asarray(coords, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64)
    tri_types = np.asarray(tri_types, dtype=np.uint8)
    n_nodes, n_tris = len(coords), len(tris)
    node_types = _node_types(n_nodes, tris, tri_types)
    is_coast = ((node_types & 1) != 0) & ((node_types & 6) != 0)
    tri_is_coast = is_coast[tris].any(axis=1)
    coast = np.flatnonzero(tri_is_coast)
    if len(coast) == 0:
        return RecutMesh(coords, node_types, is_coast, tris, tri_types)

    # Edges of the coast triangles, in Ortho4XP scan order: triangle-major, (a,b), (b,c), (c,a).
    ct = tris[coast]
    edges = np.stack([ct[:, [0, 1]], ct[:, [1, 2]], ct[:, [2, 0]]], axis=1).reshape(-1, 2)
    bits = np.repeat(np.left_shift(1, tri_types[coast].astype(np.int64)), 3)
    lo, hi = np.minimum(edges[:, 0], edges[:, 1]), np.maximum(edges[:, 0], edges[:, 1])
    key = lo * n_nodes + hi
    uniq, first_idx, inv = np.unique(key, return_index=True, return_inverse=True)
    edge_type = np.zeros(len(uniq), dtype=np.uint8)
    np.bitwise_or.at(edge_type, inv, bits.astype(np.uint8))
    e_lo, e_hi = uniq // n_nodes, uniq % n_nodes

    # Cut edges: no land bit, both ends coast; numbered by first appearance (dict order).
    cut = ((edge_type & 1) == 0) & is_coast[e_lo] & is_coast[e_hi]
    cut_ids = np.flatnonzero(cut)
    cut_ids = cut_ids[np.argsort(first_idx[cut_ids], kind="stable")]
    n_cut = len(cut_ids)
    cut_node = np.full(len(uniq), -1, dtype=np.int64)
    cut_node[cut_ids] = n_nodes + np.arange(n_cut)
    cut_coords = (coords[e_lo[cut_ids]] + coords[e_hi[cut_ids]]) / 2.0
    cut_types = edge_type[cut_ids]

    # Water coast triangles, in order, with their three (possibly cut) edges.
    e3 = inv.reshape(-1, 3)  # per coast triangle: edge ids of (a,b), (b,c), (c,a)
    water = tri_types[coast] != 0
    wi = coast[water]  # triangle indices
    wt = ct[water]
    a, b, c = wt[:, 0], wt[:, 1], wt[:, 2]
    e_ab, e_bc, e_ca = e3[water, 0], e3[water, 1], e3[water, 2]
    cn, an, bn = cut_node[e_ab], cut_node[e_bc], cut_node[e_ca]
    has_c, has_a, has_b = cn >= 0, an >= 0, bn >= 0
    cuts = has_c.astype(np.int64) + has_a + has_b
    all_land = (
        ((edge_type[e_ab] & 1) != 0) & ((edge_type[e_bc] & 1) != 0) & ((edge_type[e_ca] & 1) != 0)
    )
    bary = (cuts == 0) & all_land
    n_bary = int(bary.sum())
    gn = np.full(len(wi), -1, dtype=np.int64)
    gn[bary] = n_nodes + n_cut + np.arange(n_bary)
    bary_coords = (coords[a[bary]] + coords[b[bary]] + coords[c[bary]]) / 3.0
    # Ortho4XP stores the triangle *type* (1 or 2), not its bit: a sea barycentre gets 2 (``:114``).
    bary_types = tri_types[wi[bary]].astype(np.uint8)

    # Appended triangles: 2 for a barycentre cut, 1 / 2 / 3 for 1 / 2 / 3 cut edges.
    n_app = np.select([bary, cuts == 1, cuts == 2, cuts == 3], [2, 1, 2, 3], default=0)
    total = int(n_app.sum())
    offset = n_tris + np.concatenate(([0], np.cumsum(n_app)[:-1]))
    new_tris = np.concatenate([tris, np.zeros((total, 3), dtype=np.int64)])
    new_types = np.concatenate([tri_types, np.zeros(total, dtype=np.uint8)])

    def emit(mask: np.ndarray, in_place: tuple, appended: tuple[tuple, ...]) -> None:
        idx = np.flatnonzero(mask)
        if len(idx) == 0:
            return
        new_tris[wi[idx]] = np.stack([col[idx] for col in in_place], axis=1)
        for j, tri in enumerate(appended):
            rows = offset[idx] + j
            new_tris[rows] = np.stack([col[idx] for col in tri], axis=1)
            new_types[rows] = tri_types[wi[idx]]

    # Case table (``O4_Bathymetry.py:95-168``), see the spec for the ring arithmetic.
    emit(bary, (a, b, gn), ((b, c, gn), (c, a, gn)))
    one = cuts == 1
    emit(one & has_c, (cn, b, c), ((cn, c, a),))
    emit(one & has_a, (an, c, a), ((an, a, b),))
    emit(one & has_b, (bn, a, b), ((bn, b, c),))
    two = cuts == 2
    emit(two & ~has_c, (a, b, an), ((an, c, bn), (a, an, bn)))
    emit(two & ~has_a, (b, c, bn), ((bn, a, cn), (b, bn, cn)))
    emit(two & ~has_b, (c, a, cn), ((cn, b, an), (c, cn, an)))
    emit(cuts == 3, (bn, a, cn), ((cn, b, an), (an, c, bn), (bn, cn, an)))

    new_coords = np.concatenate([coords, cut_coords, bary_coords])
    new_node_types = np.concatenate([node_types, cut_types, bary_types])
    new_is_coast = np.concatenate([is_coast, np.zeros(n_cut + n_bary, dtype=np.bool_)])
    return RecutMesh(new_coords, new_node_types, new_is_coast, new_tris, new_types)
