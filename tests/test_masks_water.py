"""Water triangles of a mesh sorted into the cells of the mask grid (`orthostudio.masks.water`)."""

from __future__ import annotations

import numpy as np
import pytest

from orthostudio.imagery.grid import texture_at, tile_to_wgs84
from orthostudio.masks.water import (
    NEIGHBOUR_OFFSETS,
    MaskRange,
    MeshWaterTris,
    cell_pixel_origin,
    mask_cells,
    tile_pixel_corners,
    water_triangles,
    wgs84_to_pix,
)
from orthostudio.mesh.mesh_file import MeshData
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
ZL = 14


def _mesh(points: list[tuple[float, float]], attrs: list[int], version: str = "2") -> MeshData:
    """One triangle per attribute; ``points[i]`` is the centre of triangle ``i``."""
    vertices = []
    tris = []
    for i, (lat, lon) in enumerate(points):
        d = 1e-5
        vertices += [(lon - d, lat - d, 0.0), (lon + d, lat - d, 0.0), (lon, lat + d, 0.0)]
        tris.append((3 * i, 3 * i + 1, 3 * i + 2))
    return MeshData(
        vertices=np.array(vertices, dtype=np.float64),
        normals=np.zeros((len(vertices), 2), dtype=np.float32),
        tris=np.array(tris, dtype=np.int32),
        tri_attr=np.array(attrs, dtype=np.uint8),
        extra={"version": version, "dimension": "3"},
    )


def _cell_centre(til_x: int, til_y: int, quarter_x: int, quarter_y: int) -> tuple[float, float]:
    """A point inside the ``(quarter_x, quarter_y)`` quarter of a cell, at ZL + 2 resolution."""
    fine = 16 * (quarter_x + 0.5), 16 * (quarter_y + 0.5)
    lat, lon = tile_to_wgs84(4 * til_x + fine[0], 4 * til_y + fine[1], ZL + 2)
    return lat, lon


def test_mask_range_of_the_reference_tile() -> None:
    rng = mask_cells(TILE, ZL)
    assert rng == MaskRange(8416, 5952, 8464, 6016)
    assert rng.contains(8416, 5952) and rng.contains(8464, 6016)
    assert not rng.contains(8400, 6000) and not rng.contains(8416, 6032)
    assert len(rng.cells()) == 4 * 5


def test_pixel_helpers_follow_the_ortho4xp_formulas() -> None:
    import math

    expected_y = round((1 - math.log(math.tan(133 * math.pi / 360)) / math.pi) * 2 ** (ZL + 7))
    assert wgs84_to_pix(43.0, 5.0, ZL) == (round((5 / 180 + 1) * 2 ** (ZL + 7)), expected_y)
    corners = tile_pixel_corners(TILE, ZL)
    assert corners[0] == wgs84_to_pix(43, 5, ZL)
    assert corners[2] == wgs84_to_pix(44, 6, ZL)
    px0, py0 = cell_pixel_origin(8432, 6000, ZL, 1024)
    lat, lon = tile_to_wgs84(8432, 6000, ZL)
    cx, cy = wgs84_to_pix(lat, lon, ZL)
    assert (px0, py0) == (cx - 1024, cy - 1024)


@pytest.mark.parametrize(
    ("attr", "inland_flag", "expect_sea", "expect_inland"),
    [
        (0, False, False, False),
        (1, False, False, True),
        (1, True, True, True),
        (2, False, True, False),
        (3, False, True, False),
        (8, False, False, False),
        (10, False, True, False),
        (9, True, True, True),
    ],
)
def test_triangle_selection(
    attr: int, inland_flag: bool, expect_sea: bool, expect_inland: bool
) -> None:
    """``O4_Mask_Utils.py:473-480`` and ``:590-593``, attribute by attribute."""
    lat, lon = _cell_centre(8432, 6000, 1, 1)
    mesh = _mesh([(lat, lon)], [attr])
    by_cell = water_triangles({TILE: mesh}, TILE, ZL, use_masks_for_inland=inland_flag)
    cell = texture_at(lat, lon, ZL, "")
    key = (cell.til_x, cell.til_y)
    if not expect_sea:
        assert key not in by_cell
        return
    assert by_cell[key].sea.shape == (1, 3, 2)
    # the second, inland-only pass of Ortho4XP runs only when use_masks_for_inland is off
    assert (by_cell[key].inland.shape[0] == 1) is (expect_inland and not inland_flag)


@pytest.mark.parametrize("quarter_x", [0, 1, 2, 3])
@pytest.mark.parametrize("quarter_y", [0, 1, 2, 3])
def test_outer_quarters_are_duplicated_into_the_neighbour_cells(
    quarter_x: int, quarter_y: int
) -> None:
    """``O4_Mask_Utils.py:506-582``: a, b == 0 or 3 push the triangle into 1, 2 or 3 cells."""
    til_x, til_y = 8432, 6000
    lat, lon = _cell_centre(til_x, til_y, quarter_x, quarter_y)
    mesh = _mesh([(lat, lon)], [2])
    by_cell = water_triangles({TILE: mesh}, TILE, ZL)
    expected = {(til_x, til_y)}
    dx = -16 if quarter_x == 0 else 16 if quarter_x == 3 else None
    dy = -16 if quarter_y == 0 else 16 if quarter_y == 3 else None
    if dx is not None:
        expected.add((til_x + dx, til_y))
    if dy is not None:
        expected.add((til_x, til_y + dy))
    if dx is not None and dy is not None:
        expected.add((til_x + dx, til_y + dy))
    assert set(by_cell) == expected


def test_cells_outside_the_widened_range_are_dropped() -> None:
    """A triangle far west of the tile is ignored (``O4_Mask_Utils.py:494-501``)."""
    mesh = _mesh([(43.5, 2.0)], [2])
    assert water_triangles({TILE: mesh}, TILE, ZL) == {}


def test_neighbour_meshes_contribute_their_own_triangles() -> None:
    west = TILE.neighbour(0, -1)
    lat, lon = _cell_centre(8416, 6000, 0, 1)
    here = _mesh([(lat, lon)], [2])
    there = _mesh([(lat, lon - 0.0)], [2])
    by_cell = water_triangles({TILE: here, west: there}, TILE, ZL)
    assert by_cell[(8416, 6000)].sea.shape[0] == 2


def test_mesh_version_below_1_3_uses_the_three_bit_mask() -> None:
    """``has_water`` is 3 below version 1.3 and 7 above (``O4_Mask_Utils.py:440-441``)."""
    lat, lon = _cell_centre(8432, 6000, 1, 1)
    old = _mesh([(lat, lon)], [4], version="1.2")
    assert water_triangles({TILE: old}, TILE, ZL) == {}
    new = _mesh([(lat, lon)], [4], version="2")
    assert set(new_cells := water_triangles({TILE: new}, TILE, ZL)) == {(8432, 6000)}
    assert new_cells[(8432, 6000)].sea.shape[0] == 1


def test_neighbour_offsets_cover_the_eight_directions() -> None:
    assert len(NEIGHBOUR_OFFSETS) == 8
    assert set(NEIGHBOUR_OFFSETS.values()) == {
        (a, b) for a in (-1, 0, 1) for b in (-1, 0, 1) if (a, b) != (0, 0)
    }


def _water_tris_npz(mesh: MeshData) -> MeshWaterTris:
    """Build the mesh rule's ``water_tris.npz`` contract from a ``MeshData``."""
    attr = mesh.tri_attr.astype(np.int64)
    bits = attr & (7 if mesh.version >= 1.3 else 3)
    keep = np.flatnonzero((attr != 0) & (bits != 0))
    tris = mesh.tris[keep]
    lon = mesh.vertices[:, 0]
    lat = mesh.vertices[:, 1]
    bary = np.stack(
        (
            (lon[tris[:, 0]] + lon[tris[:, 1]] + lon[tris[:, 2]]) / 3,
            (lat[tris[:, 0]] + lat[tris[:, 1]] + lat[tris[:, 2]]) / 3,
        ),
        axis=1,
    )
    return MeshWaterTris(
        corners=tris.astype(np.int32), water_bits=bits[keep].astype(np.uint8), bary=bary
    )


def test_precomputed_water_tris_give_the_same_cells() -> None:
    """``docs/specs/mesh-build.md`` section 7: the split mesh-side / zoom-side is confirmed."""
    points = [_cell_centre(8432, 6000, q, 0) for q in range(4)]
    mesh = _mesh(points, [2, 1, 3, 0])
    from_mesh = water_triangles({TILE: mesh}, TILE, ZL)
    from_npz = water_triangles({TILE: mesh}, TILE, ZL, water_tris={TILE: _water_tris_npz(mesh)})
    assert set(from_mesh) == set(from_npz)
    for cell, tris in from_mesh.items():
        assert np.array_equal(tris.sea, from_npz[cell].sea)
        assert np.array_equal(tris.inland, from_npz[cell].inland)


def test_read_water_tris_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from orthostudio.masks.water import WATER_TRIS_FORMAT, read_water_tris

    mesh = _mesh([_cell_centre(8432, 6000, 1, 1)], [2])
    wt = _water_tris_npz(mesh)
    path = tmp_path / "water_tris.npz"
    np.savez(
        path,
        format=WATER_TRIS_FORMAT,
        tile="+43+005",
        tri_index=np.arange(wt.corners.shape[0], dtype=np.int32),
        corners=wt.corners,
        water_bits=wt.water_bits,
        attr=wt.water_bits,
        bary=wt.bary,
    )
    back = read_water_tris(path)
    assert np.array_equal(back.corners, wt.corners)
    assert np.array_equal(back.bary, wt.bary)
    np.savez(path, format="nope", corners=wt.corners, water_bits=wt.water_bits, bary=wt.bary)
    with pytest.raises(ValueError, match="osxp-water-tris-1"):
        read_water_tris(path)
