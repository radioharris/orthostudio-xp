# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Water triangles of a mesh, sorted into the cells of the ``mask_zl`` texture grid.

Spec: ``docs/specs/masks-build.md`` sections 2 and 4. Origin: ``O4_Mask_Utils.py:394-646``
(``record_water_tris``) and ``:223-242`` (``select_neighbor_meshes``), vectorised over
:class:`orthostudio.mesh.mesh_file.MeshData` instead of re-parsing the ``.mesh`` text twice.

The arrays this module produces are **absolute web-mercator pixel coordinates** at
``mask_zl`` (``O4_Geo_Utils.py:88-95``, ``wgs84_to_pix``), which is what the rasteriser
draws; the cell of a triangle is still decided on its barycentre in degrees, exactly as Ortho4XP
does, so the attribution is bit for bit the same.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from orthostudio.imagery.grid import TEXTURE_TILES, texture_at
from orthostudio.mesh.mesh_file import MeshData
from orthostudio.model import TileRef

__all__ = [
    "NEIGHBOUR_OFFSETS",
    "WATER_TRIS_FORMAT",
    "MaskRange",
    "MeshWaterTris",
    "WaterTriangles",
    "cell_pixel_origin",
    "mask_cells",
    "read_water_tris",
    "tile_pixel_corners",
    "water_triangles",
    "wgs84_to_pix",
]

NEIGHBOUR_OFFSETS: dict[str, tuple[int, int]] = {
    "nb_n": (1, 0),
    "nb_ne": (1, 1),
    "nb_e": (0, 1),
    "nb_se": (-1, 1),
    "nb_s": (-1, 0),
    "nb_sw": (-1, -1),
    "nb_w": (0, -1),
    "nb_nw": (1, -1),
}
"""Rule input name -> ``(dlat, dlon)`` of the neighbour tile whose mesh it carries; the pipeline
wires the masks node from this table (``pipeline.build.declare``)."""

INLAND_BIT = 1
"""``attr & has_water == 1`` is inland water (``O4_Mask_Utils.py:474-480``)."""

WATER_TRIS_FORMAT = "osxp-water-tris-1"
"""``water_tris.npz`` of the native mesh rule (``docs/specs/mesh-build.md`` section 7)."""


class MaskRange(NamedTuple):
    """Cells of the ``mask_zl`` grid the tile overlaps (``O4_Mask_Utils.py:244-247``)."""

    til_x_min: int
    til_y_min: int
    til_x_max: int
    til_y_max: int

    def cells(self) -> list[tuple[int, int]]:
        """Every ``(til_x, til_y)`` of the range, x outer then y (Ortho4XP's loop order)."""
        return [
            (x, y)
            for x in range(self.til_x_min, self.til_x_max + 1, TEXTURE_TILES)
            for y in range(self.til_y_min, self.til_y_max + 1, TEXTURE_TILES)
        ]

    def contains(self, til_x: int, til_y: int) -> bool:
        """Ortho4XP's guard at the top of ``build_mask`` (``O4_Mask_Utils.py:136-141``)."""
        return (
            self.til_x_min <= til_x <= self.til_x_max and self.til_y_min <= til_y <= self.til_y_max
        )


@dataclass(frozen=True, slots=True)
class MeshWaterTris:
    """The mesh-side half of ``record_water_tris``, as the mesh rule publishes it.

    ``docs/specs/mesh-build.md`` section 7: every triangle with ``attr & 7 != 0``, its
    corners (indices into ``MeshData.vertices``), its ``attr & 7`` bits and its barycentre
    in ``(lon, lat)``. The zoom-dependent half (which mask cell, which quarter) stays here,
    because ``mask_zl`` is a parameter of *this* stage and must not rebuild the mesh.
    """

    corners: NDArray[np.int32]
    water_bits: NDArray[np.uint8]
    bary: NDArray[np.float64]


def read_water_tris(path: Path) -> MeshWaterTris:
    """Read a ``water_tris.npz`` written by ``orthostudio.mesh@1``."""
    with np.load(path, allow_pickle=False) as data:
        fmt = str(data["format"])
        if fmt != WATER_TRIS_FORMAT:
            raise ValueError(f"{path}: expected {WATER_TRIS_FORMAT}, found {fmt!r}")
        return MeshWaterTris(
            corners=np.ascontiguousarray(data["corners"], dtype=np.int32),
            water_bits=np.ascontiguousarray(data["water_bits"], dtype=np.uint8),
            bary=np.ascontiguousarray(data["bary"], dtype=np.float64),
        )


@dataclass(frozen=True, slots=True)
class WaterTriangles:
    """Triangles to draw in one cell, as absolute pixel coordinates at ``mask_zl``.

    ``sea`` is drawn black and ``inland`` the grey of ``ratio_water``; both are float64
    ``(n, 3, 2)`` arrays of ``(pix_x, pix_y)`` corners. ``sea`` holds the inland triangles
    too when ``use_masks_for_inland`` is set, exactly like Ortho4XP's ``dico_sea``.
    """

    sea: NDArray[np.float64]
    inland: NDArray[np.float64]

    @property
    def empty(self) -> bool:
        return self.sea.shape[0] == 0 and self.inland.shape[0] == 0


def mask_cells(tile: TileRef, mask_zl: int) -> MaskRange:
    """Range of mask cells of a tile (``O4_Mask_Utils.py:244-247``)."""
    nw = texture_at(tile.lat + 1, tile.lon, mask_zl, "")
    se = texture_at(tile.lat, tile.lon + 1, mask_zl, "")
    return MaskRange(nw.til_x, nw.til_y, se.til_x, se.til_y)


def wgs84_to_pix(lat: float, lon: float, zl: int) -> tuple[int, int]:
    """Absolute web-mercator pixel of a point (``O4_Geo_Utils.py:88-95``)."""
    ratio_y = math.log(math.tan((90 + lat) * math.pi / 360)) / math.pi
    return (round((lon / 180 + 1) * 2 ** (zl + 7)), round((1 - ratio_y) * 2 ** (zl + 7)))


def _pix_vec(
    lat: NDArray[np.float64], lon: NDArray[np.float64], zl: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Vectorised :func:`wgs84_to_pix`; the powers of two are exact, so this is bit-identical."""
    ratio_y = np.log(np.tan((90 + lat) * np.pi / 360)) / np.pi
    return (
        np.round((lon / 180 + 1) * 2 ** (zl + 7)),
        np.round((1 - ratio_y) * 2 ** (zl + 7)),
    )


def _orthogrid_vec(
    lat: NDArray[np.float64], lon: NDArray[np.float64], zl: int
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Vectorised ``wgs84_to_orthogrid`` (``O4_Geo_Utils.py:125-133``), ``int()`` truncation."""
    ratio_y = np.log(np.tan((90 + lat) * np.pi / 360)) / np.pi
    mult = 2 ** (zl - 5)
    return (
        ((lon / 180 + 1) * mult).astype(np.int64) * TEXTURE_TILES,
        ((1 - ratio_y) * mult).astype(np.int64) * TEXTURE_TILES,
    )


def tile_pixel_corners(tile: TileRef, mask_zl: int) -> list[tuple[int, int]]:
    """The four corners of a 1 degree tile in absolute pixels, Ortho4XP's winding.

    ``(lat, lon)``, ``(lat, lon + 1)``, ``(lat + 1, lon + 1)``, ``(lat + 1, lon)``
    (``O4_Mask_Utils.py:274-283``).
    """
    return [
        wgs84_to_pix(tile.lat, tile.lon, mask_zl),
        wgs84_to_pix(tile.lat, tile.lon + 1, mask_zl),
        wgs84_to_pix(tile.lat + 1, tile.lon + 1, mask_zl),
        wgs84_to_pix(tile.lat + 1, tile.lon, mask_zl),
    ]


def cell_pixel_origin(til_x: int, til_y: int, mask_zl: int, margin: int) -> tuple[int, int]:
    """Absolute pixel of the top-left corner of a cell's working image.

    ``gtile_to_wgs84`` then ``wgs84_to_pix`` then minus the margin
    (``O4_Mask_Utils.py:262-266``). The round trip is not the identity in Ortho4XP either; it is
    reproduced as written.
    """
    rat_y = 1 - til_y / (2 ** (mask_zl - 1))
    lat = 360 / math.pi * math.atan(math.exp(math.pi * rat_y)) - 90
    lon = (til_x / (2 ** (mask_zl - 1)) - 1) * 180
    px, py = wgs84_to_pix(lat, lon, mask_zl)
    return (px - margin, py - margin)


def water_triangles(
    meshes: Mapping[TileRef, MeshData],
    tile: TileRef,
    mask_zl: int,
    *,
    use_masks_for_inland: bool = False,
    water_tris: Mapping[TileRef, MeshWaterTris] | None = None,
) -> dict[tuple[int, int], WaterTriangles]:
    """Water triangles of every mesh, sorted into the cells of the ``mask_zl`` grid.

    ``meshes`` maps a tile (the one being built and any of its eight neighbours) to its mesh.
    ``water_tris`` is the optional ``water_tris.npz`` of the same artefact: when it is there
    the water triangles are not searched for again, only their cells are computed. Cells
    outside the tile's range widened by one cell are dropped, and a triangle sitting in an
    outer quarter of its cell is duplicated into the one, two or three neighbouring cells
    whose margin it falls in (``O4_Mask_Utils.py:494-582``).

    The order of the triangles inside a cell is not Ortho4XP's: every triangle of one colour is
    drawn with the same fill, so the image does not depend on it.
    """
    rng = mask_cells(tile, mask_zl)
    step = TEXTURE_TILES
    sea_cells: list[NDArray[np.int64]] = []
    sea_tris: list[NDArray[np.float64]] = []
    inland_cells: list[NDArray[np.int64]] = []
    inland_tris: list[NDArray[np.float64]] = []
    for nb in sorted(meshes, key=lambda t: (t.lat, t.lon)):
        mesh = meshes[nb]
        precomputed = None if water_tris is None else water_tris.get(nb)
        tris, bits, bary_lon, bary_lat = _water_of(mesh, precomputed)
        if tris.shape[0] == 0:
            continue
        sea_sel = bits >= 2 if not use_masks_for_inland else bits != 0
        inland_sel = bits == INLAND_BIT
        til_x, til_y = _orthogrid_vec(bary_lat, bary_lon, mask_zl)
        in_range = (
            (til_x >= rng.til_x_min - step)
            & (til_x <= rng.til_x_max + step)
            & (til_y >= rng.til_y_min - step)
            & (til_y <= rng.til_y_max + step)
        )
        px, py = _pix_vec(mesh.vertices[:, 1], mesh.vertices[:, 0], mask_zl)
        corners = np.stack((px[tris], py[tris]), axis=-1)  # (n, 3, 2)

        keep = np.flatnonzero(sea_sel & in_range)
        if keep.size:
            fine_x, fine_y = _orthogrid_vec(bary_lat[keep], bary_lon[keep], mask_zl + 2)
            qa = (fine_x // step) % 4
            qb = (fine_y // step) % 4
            cx, cy = til_x[keep], til_y[keep]
            dx = np.zeros((keep.size, 4), dtype=np.int64)
            dy = np.zeros((keep.size, 4), dtype=np.int64)
            use = np.zeros((keep.size, 4), dtype=bool)
            use[:, 0] = True
            left, right = qa == 0, qa == 3
            top, bottom = qb == 0, qb == 3
            dx[:, 1] = np.where(left, -step, step)
            use[:, 1] = left | right
            dy[:, 2] = np.where(top, -step, step)
            use[:, 2] = top | bottom
            dx[:, 3] = dx[:, 1]
            dy[:, 3] = dy[:, 2]
            use[:, 3] = use[:, 1] & use[:, 2]
            flat = use.ravel()
            cells = np.stack(
                (
                    (cx[:, None] + dx).ravel()[flat],
                    (cy[:, None] + dy).ravel()[flat],
                ),
                axis=1,
            )
            sea_cells.append(cells)
            sea_tris.append(np.repeat(corners[keep], use.sum(axis=1), axis=0))

        # Ortho4XP only runs its second, inland-only pass when use_masks_for_inland is off.
        keep = (
            np.flatnonzero(inland_sel & in_range)
            if not use_masks_for_inland
            else np.empty(0, dtype=np.int64)
        )
        if keep.size:
            inland_cells.append(np.stack((til_x[keep], til_y[keep]), axis=1))
            inland_tris.append(corners[keep])

    sea_by_cell = _group(sea_cells, sea_tris)
    inland_by_cell = _group(inland_cells, inland_tris)
    empty = np.zeros((0, 3, 2), dtype=np.float64)
    return {
        # Ortho4XP keeps an inland triangle only where sea water was already recorded.
        cell: WaterTriangles(sea=sea, inland=inland_by_cell.get(cell, empty))
        for cell, sea in sea_by_cell.items()
    }


def _water_of(
    mesh: MeshData, precomputed: MeshWaterTris | None
) -> tuple[NDArray[np.int32], NDArray[np.int64], NDArray[np.float64], NDArray[np.float64]]:
    """``(corners, attr & has_water, bary_lon, bary_lat)`` of the water triangles of a mesh."""
    if precomputed is not None:
        return (
            precomputed.corners,
            precomputed.water_bits.astype(np.int64),
            precomputed.bary[:, 0],
            precomputed.bary[:, 1],
        )
    if mesh.n_triangles == 0:
        return (
            np.zeros((0, 3), dtype=np.int32),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.float64),
            np.zeros(0, dtype=np.float64),
        )
    has_water = 7 if mesh.version >= 1.3 else 3
    attr = mesh.tri_attr.astype(np.int64)
    bits = attr & has_water
    keep = np.flatnonzero((attr != 0) & (bits != 0))
    tris = mesh.tris[keep]
    lon_v = mesh.vertices[:, 0]
    lat_v = mesh.vertices[:, 1]
    bary_lat = (lat_v[tris[:, 0]] + lat_v[tris[:, 1]] + lat_v[tris[:, 2]]) / 3
    bary_lon = (lon_v[tris[:, 0]] + lon_v[tris[:, 1]] + lon_v[tris[:, 2]]) / 3
    return tris, bits[keep], bary_lon, bary_lat


def _group(
    cells: list[NDArray[np.int64]], tris: list[NDArray[np.float64]]
) -> dict[tuple[int, int], NDArray[np.float64]]:
    """Group ``(n, 3, 2)`` triangle arrays by their ``(til_x, til_y)`` cell."""
    if not cells:
        return {}
    keys = np.concatenate(cells)
    values = np.concatenate(tris)
    order = np.lexsort((keys[:, 0], keys[:, 1]))
    keys, values = keys[order], values[order]
    starts = np.flatnonzero(np.any(keys[1:] != keys[:-1], axis=1) if keys.shape[0] > 1 else [])
    bounds = np.concatenate(([0], starts + 1, [keys.shape[0]]))
    return {
        (int(keys[a, 0]), int(keys[a, 1])): np.ascontiguousarray(values[a:b])
        for a, b in pairwise(bounds)
    }
