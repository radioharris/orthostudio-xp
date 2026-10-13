# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The comparison of the witness tiles (``docs/testing/witness-tiles.md``), on small meshes."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "witness"
sys.path.insert(0, str(TOOLS))

import witness  # noqa: E402

TILE = "+46+006"


def rectangle(diagonal: str, z: tuple[float, float, float, float]) -> witness.Mesh:
    """A land rectangle of 0.01 degree cut along one of its diagonals, corners SW SE NE NW."""
    x0, y0, x1, y1 = 6.5, 46.5, 6.51, 46.51
    v = np.array([[x0, y0, z[0]], [x1, y0, z[1]], [x1, y1, z[2]], [x0, y1, z[3]]])
    tris = [[0, 1, 2], [0, 2, 3]] if diagonal == "SW-NE" else [[0, 1, 3], [1, 2, 3]]
    return witness.Mesh(v, np.array(tris), np.zeros(2, dtype=np.uint8))


def test_the_list_names_each_tile_once_with_its_reason() -> None:
    tiles = witness.witness_tiles()
    assert len(tiles) >= 60
    assert all(why for why in tiles.values())
    assert "+35+139" in tiles and "+35+051" in tiles


def test_the_random_draw_is_the_same_for_the_same_seed_and_leaves_the_list_out() -> None:
    pool = [f"+{lat:02d}+{lon:03d}" for lat in range(10, 40) for lon in range(0, 30)]
    a = witness.random_tiles(pool, 20, 7, {"+10+000"})
    assert a == witness.random_tiles(pool, 20, 7, {"+10+000"}) and len(a) == 20
    assert "+10+000" not in a and a != witness.random_tiles(pool, 20, 8, {"+10+000"})


def test_a_point_is_found_on_its_triangle_at_its_height() -> None:
    mesh = rectangle("SW-NE", (100, 200, 300, 400))
    centres = mesh.vertices[mesh.tris].mean(1)
    found, z = witness.locate(mesh, centres[:, :2])
    assert list(found) == [0, 1]
    assert np.allclose(z, centres[:, 2])
    outside, _ = witness.locate(mesh, np.array([[7.0, 47.0]]))
    assert outside[0] == -1


def test_the_same_mesh_changes_nothing() -> None:
    mesh = rectangle("SW-NE", (100, 200, 300, 400))
    out = witness.compare_meshes(mesh, mesh, TILE)
    assert out["places"] == 0 and out["triangles_kept"] == 1.0
    assert out["land_vertices_moved"] == 0
    assert "land_surface_move_m" not in out and "diagonal_turn_m" not in out


def test_a_turned_diagonal_is_told_apart_from_a_change_of_the_terrain() -> None:
    """The same four corners cut the other way: no vertex moved, the middle differs by
    |z_sw + z_ne - z_se - z_nw| / 2, and it is counted as a turned diagonal."""
    z = (100.0, 300.0, 200.0, 140.0)
    out = witness.compare_meshes(rectangle("SW-NE", z), rectangle("SE-NW", z), TILE)
    assert out["places"] >= 1 and out["land_vertices_moved"] == 0
    assert "land_surface_move_m" not in out
    assert abs(out["diagonal_turn_m"]["max"] - abs(z[0] + z[2] - z[1] - z[3]) / 2) < 0.01


def test_a_new_corner_at_another_height_is_a_change_of_the_terrain() -> None:
    ref = rectangle("SW-NE", (100, 100, 100, 100))
    v = np.vstack([ref.vertices, [[6.505, 46.505, 130.0]]])
    tris = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    new = witness.Mesh(v, tris, np.zeros(4, dtype=np.uint8))
    out = witness.compare_meshes(ref, new, TILE)
    assert abs(out["land_surface_move_m"]["max"] - 30.0) < 0.01
    assert out["land_surface_move_m"]["max_at"] == [46.505, 6.505]


def test_a_land_vertex_that_moves_is_counted() -> None:
    ref = rectangle("SW-NE", (100, 200, 300, 400))
    new = rectangle("SW-NE", (100, 200, 300, 402))
    out = witness.compare_meshes(ref, new, TILE)
    assert out["land_vertices_moved"] == 1 and out["land_max_move_m"] == 2.0


def test_the_report_asks_to_look_only_at_what_matters() -> None:
    base = {
        "verdict": "changed",
        "vectors.crossings": [0, 0],
        "mesh.fan": [40, 45],
        "mesh.needles": [3, 4],
        "area_change_m2": {"land": 12.0, "inland": -12.0, "sea": 0.0},
        "diagonal_turn_m": {
            "median": 0.0,
            "p99": 1.0,
            "max": 38.0,
            "max_at": [35.1, 139.0],
            "over_1m": 9,
        },
    }
    assert witness.alerts(base) == []
    moved = {
        **base,
        "land_surface_move_m": {
            "median": 1,
            "p99": 5,
            "max": 6.1,
            "max_at": [35.5, 139.8],
            "over_1m": 40,
        },
    }
    assert witness.alerts(moved) and "6.1" in witness.alerts(moved)[0]
    assert witness.alerts({**base, "vectors.crossings": [0, 2]}) == ["crossings 0 -> 2"]
    assert witness.alerts({"verdict": "failed"}) == ["failed"]
