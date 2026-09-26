# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Tests for orthostudio.mesh.weights: the vectorised weight map against a literal
Ortho4XP transcription.

Spec: docs/specs/mesh-build.md section 3.
"""

from __future__ import annotations

import math
import pickle
from pathlib import Path

import numpy as np
import pytest

from orthostudio.mesh import weights as wmap

# -- the reference: O4_Mesh_Utils.py:132-228, transcribed line by line -------------------------


def _reference_weight_map(
    lat: int,
    lon: int,
    curvature_tol: float,
    apt_curv_tol: float,
    apt_curv_ext: float,
    coast_curv_tol: float,
    coast_curv_ext: float,
    apt_bounds: np.ndarray,
    coast_nodes: np.ndarray,
) -> np.ndarray:
    """``build_curv_tol_weight_map`` of Ortho4XP, with its Python loops, as the reference."""
    lat_to_m = math.pi * 6378137 / 180
    m_to_lat = 1 / lat_to_m

    def m_to_lon(la: float) -> float:
        return m_to_lat / math.cos(math.pi * la / 180)

    weight = np.ones((1001, 1001), dtype=np.float32)
    if apt_curv_tol != curvature_tol and apt_curv_tol > 0:
        for xmin, ymin, xmax, ymax in apt_bounds.reshape(-1, 4):
            x_shift = 1000 * apt_curv_ext * m_to_lon(lat)
            y_shift = 1000 * apt_curv_ext * m_to_lat
            colmin = max(round((xmin - x_shift) * 1000), 0)
            colmax = min(round((xmax + x_shift) * 1000), 1000)
            rowmax = min(round(((1 - ymin) + y_shift) * 1000), 1000)
            rowmin = max(round(((1 - ymax) - y_shift) * 1000), 0)
            weight[rowmin : rowmax + 1, colmin : colmax + 1] = curvature_tol / apt_curv_tol
    if coast_curv_tol != curvature_tol:
        for lonp, latp in coast_nodes.reshape(-1, 2):
            if lonp < lon or lonp > lon + 1 or latp < lat or latp > lat + 1:
                continue
            x_shift = 1000 * coast_curv_ext * m_to_lon(lat)
            y_shift = coast_curv_ext / 111.12
            colmin = max(round((lonp - lon - x_shift) * 1000), 0)
            colmax = min(round((lonp - lon + x_shift) * 1000), 1000)
            rowmax = min(round((lat + 1 - latp + y_shift) * 1000), 1000)
            rowmin = max(round((lat + 1 - latp - y_shift) * 1000), 0)
            weight[rowmin : rowmax + 1, colmin : colmax + 1] = np.maximum(
                weight[rowmin : rowmax + 1, colmin : colmax + 1], curvature_tol / coast_curv_tol
            )
    return weight


def _build(**kw: object) -> np.ndarray:
    return wmap.build_weight_map(**kw)  # type: ignore[arg-type]


PARAMS = dict(
    curvature_tol=2.0,
    apt_curv_tol=0.5,
    apt_curv_ext=0.5,
    coast_curv_tol=1.0,
    coast_curv_ext=0.5,
)


# -- tests -------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_vectorised_map_equals_the_ortho4xp_loops(seed: int) -> None:
    rng = np.random.default_rng(seed)
    lat, lon = 43, 5
    # Airport boxes that straddle the border are realistic (a big airport on a tile edge);
    # a box that misses the tile entirely is not, and Ortho4XP mishandles it (see the test below).
    boxes = np.empty((25, 4))
    boxes[:, 0] = rng.random(25) * 1.04 - 0.02
    boxes[:, 1] = rng.random(25) * 1.04 - 0.02
    boxes[:, 2] = np.clip(boxes[:, 0] + rng.random(25) * 0.05, -0.005, None)
    boxes[:, 3] = np.clip(boxes[:, 1] + rng.random(25) * 0.05, -0.005, None)
    boxes[:, 0] = np.minimum(boxes[:, 0], 1.005)
    boxes[:, 1] = np.minimum(boxes[:, 1], 1.005)
    nodes = np.column_stack(
        [lon + rng.random(3000) * 1.2 - 0.1, lat + rng.random(3000) * 1.2 - 0.1]
    )
    got = _build(lat=lat, lon=lon, apt_bounds=boxes, coast_nodes=nodes, **PARAMS)
    want = _reference_weight_map(lat, lon, apt_bounds=boxes, coast_nodes=nodes, **PARAMS)  # type: ignore[arg-type]
    assert got.dtype == np.float32 and got.shape == (1001, 1001)
    assert np.array_equal(got, want)


def test_borders_and_clamping() -> None:
    """Nodes and boxes on the four borders: the map must reach rows/columns 0 and 1000."""
    lat, lon = 43, 5
    nodes = np.array(
        [[lon, lat], [lon + 1, lat], [lon, lat + 1], [lon + 1, lat + 1], [lon + 0.5, lat + 0.5]]
    )
    boxes = np.array([[0.0, 0.0, 0.001, 0.001], [0.999, 0.999, 1.0, 1.0]])
    got = _build(lat=lat, lon=lon, apt_bounds=boxes, coast_nodes=nodes, **PARAMS)
    want = _reference_weight_map(lat, lon, apt_bounds=boxes, coast_nodes=nodes, **PARAMS)  # type: ignore[arg-type]
    assert np.array_equal(got, want)
    for corner in ((0, 0), (0, 1000), (1000, 0), (1000, 1000)):
        assert got[corner] > 1


def test_nodes_outside_the_tile_are_ignored() -> None:
    lat, lon = 43, 5
    inside = np.array([[lon + 0.5, lat + 0.5]])
    outside = np.array([[lon - 0.5, lat + 0.5], [lon + 0.5, lat + 2.0], [lon + 1.5, lat + 1.5]])
    only_inside = _build(lat=lat, lon=lon, coast_nodes=inside, **PARAMS)
    with_outside = _build(lat=lat, lon=lon, coast_nodes=np.vstack([inside, outside]), **PARAMS)
    assert np.array_equal(only_inside, with_outside)


def test_terms_are_disabled_when_the_tolerances_match() -> None:
    lat, lon = 43, 5
    nodes = np.array([[lon + 0.5, lat + 0.5]])
    boxes = np.array([[0.4, 0.4, 0.6, 0.6]])
    flat = _build(
        lat=lat,
        lon=lon,
        curvature_tol=2.0,
        apt_curv_tol=2.0,  # equal -> airport term off
        apt_curv_ext=0.5,
        coast_curv_tol=2.0,  # equal -> coastline term off
        coast_curv_ext=0.5,
        apt_bounds=boxes,
        coast_nodes=nodes,
    )
    assert np.array_equal(flat, np.ones((1001, 1001), dtype=np.float32))
    # apt_curv_tol <= 0 also disables the airport term (Ortho4XP guards against the division)
    zero = _build(
        lat=lat,
        lon=lon,
        curvature_tol=2.0,
        apt_curv_tol=0.0,
        apt_curv_ext=0.5,
        coast_curv_tol=2.0,
        coast_curv_ext=0.5,
        apt_bounds=boxes,
        coast_nodes=nodes,
    )
    assert np.array_equal(zero, np.ones((1001, 1001), dtype=np.float32))


def test_empty_inputs_give_a_flat_map() -> None:
    flat = _build(lat=43, lon=5, apt_bounds=None, coast_nodes=None, **PARAMS)
    assert np.array_equal(flat, np.ones((1001, 1001), dtype=np.float32))
    assert wmap.weight_stats(flat) == (0, 1.0)
    empty = _build(
        lat=43, lon=5, apt_bounds=np.zeros((0, 4)), coast_nodes=np.zeros((0, 2)), **PARAMS
    )
    assert np.array_equal(empty, flat)


def test_airports_are_assigned_then_the_coast_takes_the_maximum() -> None:
    """The airport term assigns, so a value below 1 survives where the coast does not reach."""
    lat, lon = 43, 5
    boxes = np.array([[0.2, 0.2, 0.3, 0.3]])
    got = _build(
        lat=lat,
        lon=lon,
        curvature_tol=1.0,
        apt_curv_tol=4.0,  # ratio 0.25 < 1: Ortho4XP lowers the map there
        apt_curv_ext=0.0,
        coast_curv_tol=0.5,  # ratio 2
        coast_curv_ext=0.1,
        apt_bounds=boxes,
        coast_nodes=np.array([[lon + 0.8, lat + 0.8]]),
    )
    assert got[750, 250] == pytest.approx(0.25)
    assert got[200, 800] == pytest.approx(2.0)
    assert got[0, 0] == 1.0


def test_weight_file_round_trip(tmp_path: Path) -> None:
    weight = _build(lat=43, lon=5, coast_nodes=np.array([[5.5, 43.5]]), apt_bounds=None, **PARAMS)
    path = tmp_path / "Data+43+005.weight"
    wmap.write_weight_file(path, weight)
    assert path.stat().st_size == 4 * 1001 * 1001
    back = np.fromfile(path, dtype=np.float32).reshape(1001, 1001)
    assert np.array_equal(back, weight)
    with pytest.raises(ValueError, match="1001"):
        wmap.write_weight_file(tmp_path / "bad", np.ones((10, 10), dtype=np.float32))


# -- input readers -----------------------------------------------------------------------------


def test_read_airport_bounds_prefers_airports_json(tmp_path: Path) -> None:
    (tmp_path / "airports.json").write_text(
        '{"format": "osxp-airports-1", "tile": "+43+005",'
        ' "airports": [{"id": "LFML", "bounds": [0.1, 0.2, 0.3, 0.4]}]}',
        encoding="utf-8",
    )
    bounds = wmap.read_airport_bounds(tmp_path, "+43+005")
    assert bounds.shape == (1, 4)
    assert bounds[0].tolist() == [0.1, 0.2, 0.3, 0.4]


def test_read_airport_bounds_missing_is_empty_not_an_error(tmp_path: Path) -> None:
    assert wmap.read_airport_bounds(tmp_path, "+43+005").shape == (0, 4)
    (tmp_path / "airports.json").write_text("{not json", encoding="utf-8")
    assert wmap.read_airport_bounds(tmp_path, "+43+005").shape == (0, 4)


def test_an_ortho4xp_airport_pickle_is_never_read(tmp_path: Path) -> None:
    """Only ``airports.json`` counts: a ``Data<tile>.apt`` beside it is not unpickled."""
    shapely = pytest.importorskip("shapely")
    box = shapely.geometry.box(0.1, 0.2, 0.3, 0.4)
    (tmp_path / "Data+43+005.apt").write_bytes(pickle.dumps({"LFML": {"boundary": box}}))
    assert wmap.read_airport_bounds(tmp_path, "+43+005").shape == (0, 4)


def test_coastline_npz_round_trip(tmp_path: Path) -> None:
    nodes = [(5.1, 43.2), (5.3, 43.4)]
    wmap.write_coastline_nodes(tmp_path / "coastline.npz", nodes)
    back = wmap.read_coastline_nodes(tmp_path / "coastline.npz")
    assert back.shape == (2, 2)
    assert back.tolist() == [[5.1, 43.2], [5.3, 43.4]]
    assert wmap.read_coastline_nodes(tmp_path).shape == (2, 2)  # a directory works too
    np.savez(tmp_path / "other.npz", format=np.array("nope"), nodes=np.zeros((1, 2)))
    with pytest.raises(ValueError, match="osxp-coastline-nodes-1"):
        wmap.read_coastline_nodes(tmp_path / "other.npz")


def test_a_box_entirely_outside_the_tile_is_dropped_unlike_ortho4xp() -> None:
    """Wanted difference (spec section 12.10): Ortho4XP paints most of the tile instead.

    ``weight[rowmin:rowmax + 1]`` with a negative ``rowmax + 1`` is a *negative* slice bound
    in Python, so an airport box lying entirely north of the tile makes Ortho4XP refine rows 0 to
    ``1000 + rowmax`` at airport resolution. OrthoStudio XP drops the rectangle.
    """
    lat, lon = 43, 5
    far_north = np.array([[0.2, 1.05, 0.3, 1.10]])
    got = _build(lat=lat, lon=lon, apt_bounds=far_north, coast_nodes=None, **PARAMS)
    want = _reference_weight_map(
        lat,
        lon,
        apt_bounds=far_north,
        coast_nodes=np.zeros((0, 2)),
        **PARAMS,  # type: ignore[arg-type]
    )
    assert np.array_equal(got, np.ones((1001, 1001), dtype=np.float32))
    assert (want != 1).sum() > 100_000  # Ortho4XP refines a big slab of the tile
