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


def test_mask_pixels_go_back_to_degrees() -> None:
    from orthostudio.masks.water import wgs84_to_pix

    for lat, lon in ((46.5, 6.5), (-33.9, 18.4), (64.2, -51.7)):
        px, py = wgs84_to_pix(lat, lon, 14)
        back_lon, back_lat = witness.pixels_to_lonlat(np.array([px]), np.array([py]), 14)
        assert abs(back_lon[0] - lon) < 1e-4 and abs(back_lat[0] - lat) < 1e-4


def test_mask_pixels_are_told_near_or_far_from_the_pavement_taken_out(tmp_path: Path) -> None:
    """Only the pixels over the tile count: the rest of a cell, beyond the tile's border, is
    black where no neighbour was built, and no part of this tile's DSF uses it."""
    import shapely
    from PIL import Image

    from orthostudio.imagery.grid import texture_at
    from orthostudio.masks.water import mask_cells
    from orthostudio.model import TileRef

    zl = 14
    middle = texture_at(46.5, 6.5, zl, "")  # a cell well inside the tile +46+006
    corner_x, corner_y = mask_cells(TileRef(46, 6), zl).cells()[0]  # one across its border
    names = [f"{middle.til_y}_{middle.til_x}.png", f"{corner_y}_{corner_x}.png"]
    ref = np.zeros((4096, 4096), np.uint8)
    new = ref.copy()
    new[100:110, 200:210] = 255  # next to the pavement taken out
    new[3000, 3000] = 40  # about 18 km away
    edge = ref.copy()
    edge[0, 0] = 255  # the north-west corner of the cell across the border: not over the tile
    for side, images in (("ref", (ref, ref)), ("new", (new, edge))):
        (tmp_path / side / TILE / "masks").mkdir(parents=True)
        for name, image in zip(names, images, strict=True):
            Image.fromarray(image).save(tmp_path / side / TILE / "masks" / name)
    x0, y0 = witness.mask_origin(names[0], zl)
    lon, lat = witness.pixels_to_lonlat(np.array([x0 + 205.0]), np.array([y0 + 105.0]), zl)
    dry = shapely.Point(lon[0], lat[0]).buffer(0.0001)
    files_ref = {"zl": zl, "files": dict.fromkeys(names, "a")}
    files_new = {"zl": zl, "files": dict.fromkeys(names, "b")}
    cells = witness.compare_masks(
        tmp_path / "ref", tmp_path / "new", TILE, files_ref, files_new, dry
    )
    cell, across = sorted(cells, key=lambda c: c["file"] != names[0])
    assert cell["pixels"] == 101 and cell["max"] == 255 and cell["outside_pixels"] == 0
    assert cell["far_pixels"] == 1 and cell["far_max"] == 40
    far_lon, far_lat = witness.pixels_to_lonlat(
        np.array([x0 + 3000.5]), np.array([y0 + 3000.5]), zl
    )
    assert cell["far_at"] == [round(float(far_lat[0]), 5), round(float(far_lon[0]), 5)]
    assert across["pixels"] == 0 and across["outside_pixels"] == 1
    alone = witness.compare_masks(
        tmp_path / "ref", tmp_path / "new", TILE, files_ref, files_new, None
    )
    assert sorted(c["far_pixels"] for c in alone) == [0, 101]


def test_what_was_taken_out_next_door_counts_only_in_the_same_batch(tmp_path: Path) -> None:
    import shapely

    def record(tile: str, batch: list[str], dry: object = None) -> dict:
        (tmp_path / tile).mkdir()
        rec: dict = {"batch": batch, "xp_pavement": ["RJTT"] if dry is not None else []}
        if dry is not None:
            (tmp_path / tile / witness.XP_DRY[0]).write_bytes(shapely.to_wkb(dry))
            rec["xp_dry"] = {"m2": 5.0}
        return rec

    east = shapely.box(140.0, 35.5, 140.001, 35.501)
    me = record("+35+139", ["+35+139", "+35+140"])
    record("+35+140", ["+35+139", "+35+140"], east)
    record("+36+139", ["+36+139"], shapely.box(139.5, 36.0, 139.501, 36.001))
    out = witness.taken_out(tmp_path, "+35+139", me)
    assert out["here_m2"] == 0.0 and out["neighbours"] == ["+35+140"]
    assert out["geometry"].equals(east)


def test_a_change_with_nothing_taken_out_around_is_called_out() -> None:
    same = {"vectors": True, "mesh": True, "masks": False, "dsf": False}
    nothing = {"here_m2": 0.0, "airports": [], "neighbours": []}
    alone = {"verdict": "changed", "same": {**same, "vectors": False}, "taken_out": nothing}
    assert witness.isolation(alone) == [
        "changed, yet no pavement was taken out of the water in this tile"
    ]
    next_door = {
        "verdict": "changed",
        "same": same,
        "taken_out": {**nothing, "neighbours": ["+35+140"]},
        "mask_cells": [{"far_pixels": 0}],
    }
    assert witness.isolation(next_door) == []
    far = {**next_door, "mask_cells": [{"far_pixels": 3, "far_max": 40, "far_at": [35.1, 139.2]}]}
    assert "3 pixels" in witness.isolation(far)[0]
    assert witness.isolation({**next_door, "taken_out": nothing}) == [
        "masks changed, yet no pavement was taken out of the water here or next door"
    ]
    here = {"verdict": "identical", "taken_out": {**nothing, "here_m2": 12.0}}
    assert witness.isolation(here) == ["pavement taken out of the water, yet nothing changed"]
    assert witness.isolation({"verdict": "identical", "taken_out": nothing}) == []


def test_a_tile_kept_as_digests_only_is_still_told_identical_or_changed(tmp_path: Path) -> None:
    import json

    def record(side: str, tile: str, mesh_sha: str) -> None:
        (tmp_path / side / tile).mkdir(parents=True)
        rec = {
            "ok": True,
            "batch": [tile],
            "vectors": {
                "node_sha": "n",
                "poly_sha": "p",
                "nodes": 1,
                "segments": 1,
                "seeds": 1,
                "crossings": 0,
            },
            "mesh": {
                "npz_sha": mesh_sha,
                "triangles": 2,
                "needles": 0,
                "fan": 3,
                "area_m2": {"land": 1.0, "inland": 0.0, "sea": 0.0},
            },
            "masks": {"zl": 14, "index_sha": "i", "files": {}},
            "dsf": {"sha": "d", "bytes": 1},
        }
        (tmp_path / side / tile / "witness.json").write_text(json.dumps(rec))

    record("ref", "+46+006", "a")
    record("new", "+46+006", "a")
    record("ref", "+46+007", "a")
    record("new", "+46+007", "b")
    same = witness.compare_tile(tmp_path / "ref", tmp_path / "new", "+46+006")
    assert same["verdict"] == "identical"
    moved = witness.compare_tile(tmp_path / "ref", tmp_path / "new", "+46+007")
    assert moved["verdict"] == "changed" and moved["digests_only"]
    assert "places" not in moved
    assert witness.isolation(moved) == [
        "changed, yet no pavement was taken out of the water in this tile"
    ]
