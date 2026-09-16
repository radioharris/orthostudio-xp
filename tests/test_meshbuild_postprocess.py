"""Tests for orthostudio.mesh.postprocess: classification, altitude smoothing, water table.

Spec: docs/specs/mesh-build.md sections 5 and 7.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from orthostudio.mesh.postprocess import (
    ATTRIBUTES,
    HAS_WATER,
    classify_triangles,
    post_process_altitudes,
    read_water_tris_npz,
    water_tris_table,
    write_water_tris_npz,
)

# -- the reference: O4_Mesh_Utils.py:230-324, transcribed line by line -------------------------


def _reference_post_process(
    vertices: np.ndarray,
    tris: np.ndarray,
    attrs: np.ndarray,
    *,
    water_smoothing: int,
    sea_smoothing_mode: str,
) -> np.ndarray:
    """Ortho4XP's ``post_process_nodes_altitudes`` on a flat ``6 * nbr_pt`` array."""
    flat = vertices.reshape(-1).copy()
    water_tris: set[tuple[int, int, int]] = set()
    sea_tris: set[tuple[int, int, int]] = set()
    interp_alt_tris: set[tuple[int, int, int]] = set()
    for (v1, v2, v3), attr in zip(tris.tolist(), attrs.tolist(), strict=True):
        line = f"    {1} {v1 + 1}  {v2 + 1}  {v3 + 1}  {attr}\n"
        if line[-2] == "0":
            continue
        if attr >= ATTRIBUTES["INTERP_ALT"]:
            interp_alt_tris.add((v1, v2, v3))
        elif attr & ATTRIBUTES["SEA"]:
            sea_tris.add((v1, v2, v3))
        elif attr & ATTRIBUTES["WATER"] or attr & ATTRIBUTES["SEA_EQUIV"]:
            water_tris.add((v1, v2, v3))
    if water_smoothing:
        for _ in range(water_smoothing):
            for v1, v2, v3 in water_tris:
                zmean = (flat[6 * v1 + 2] + flat[6 * v2 + 2] + flat[6 * v3 + 2]) / 3
                flat[6 * v1 + 2] = zmean
                flat[6 * v2 + 2] = zmean
                flat[6 * v3 + 2] = zmean
    for v1, v2, v3 in sea_tris:
        if sea_smoothing_mode == "zero":
            flat[6 * v1 + 2] = 0
            flat[6 * v2 + 2] = 0
            flat[6 * v3 + 2] = 0
        elif sea_smoothing_mode == "mean":
            zmean = (flat[6 * v1 + 2] + flat[6 * v2 + 2] + flat[6 * v3 + 2]) / 3
            flat[6 * v1 + 2] = zmean
            flat[6 * v2 + 2] = zmean
            flat[6 * v3 + 2] = zmean
        else:
            flat[6 * v1 + 2] = max(flat[6 * v1 + 2], 0)
            flat[6 * v2 + 2] = max(flat[6 * v2 + 2], 0)
            flat[6 * v3 + 2] = max(flat[6 * v3 + 2], 0)
    for v1, v2, v3 in interp_alt_tris:
        for v in (v1, v2, v3):
            flat[6 * v + 2] = flat[6 * v + 5]
            flat[6 * v + 3] = 0
            flat[6 * v + 4] = 0
    return flat.reshape(-1, 6)


def _random_mesh(seed: int, n_pts: int = 400, n_tris: int = 900) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    points = np.empty((n_pts, 6))
    points[:, 0] = rng.random(n_pts)
    points[:, 1] = rng.random(n_pts)
    points[:, 2] = rng.normal(50, 40, n_pts)  # z, sometimes negative
    points[:, 3] = rng.normal(0, 0.3, n_pts)  # u
    points[:, 4] = rng.normal(0, 0.3, n_pts)  # v
    points[:, 5] = rng.normal(20, 10, n_pts)  # vector altitude
    tris = rng.integers(0, n_pts, (n_tris, 3)).astype(np.int32)
    attrs = rng.choice(
        np.array([0, 1, 2, 3, 4, 5, 8, 9, 10, 16, 20, 32, 128, 160], dtype=np.int64), n_tris
    )
    return points, tris, attrs


# -- classification ----------------------------------------------------------------------------


def test_classification_matches_ortho4xp_for_every_attribute() -> None:
    attrs = np.arange(256, dtype=np.int64)
    classes = classify_triangles(attrs)
    kept = (
        set(classes.interp_alt.tolist()) | set(classes.sea.tolist()) | set(classes.water.tolist())
    )
    for a in range(256):  # Ortho4XP skips a triangle whose .ele line ends with the digit 0
        if f"1 1 2 3 {a}\n"[-2] == "0":
            assert a not in kept, a
    assert set(classes.interp_alt.tolist()) == {a for a in range(256) if a % 10 and a >= 8}
    assert set(classes.sea.tolist()) == {a for a in range(256) if a % 10 and a < 8 and a & 2}
    assert set(classes.water.tolist()) == {
        a for a in range(256) if a % 10 and a < 8 and not a & 2 and (a & 1 or a & 4)
    }


def test_skip_multiples_of_ten_off_only_skips_zero() -> None:
    attrs = np.arange(256, dtype=np.int64)
    fixed = classify_triangles(attrs, skip_multiples_of_ten=False)
    assert 10 in fixed.interp_alt.tolist()  # SEA|INTERP_ALT, skipped by Ortho4XP
    assert 160 in fixed.interp_alt.tolist()  # TAXIWAY|HANGAR
    assert 0 not in set(fixed.interp_alt.tolist()) | set(fixed.sea.tolist())
    default = classify_triangles(attrs)
    assert 10 not in default.interp_alt.tolist()
    assert default.counts["interp_alt"] < fixed.counts["interp_alt"]


# -- altitudes ---------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["zero", "mean", "positive"])
@pytest.mark.parametrize("seed", [11, 12])
def test_post_process_matches_the_ortho4xp_transcription(mode: str, seed: int) -> None:
    points, tris, attrs = _random_mesh(seed)
    want = _reference_post_process(points, tris, attrs, water_smoothing=10, sea_smoothing_mode=mode)
    got = points.copy()
    post_process_altitudes(
        got,
        tris,
        classify_triangles(attrs),
        water_smoothing=10,
        sea_smoothing_mode=mode,
    )
    assert np.array_equal(got, want)


def test_no_water_smoothing_leaves_water_alone() -> None:
    points, tris, attrs = _random_mesh(13)
    got = points.copy()
    post_process_altitudes(got, tris, classify_triangles(attrs), water_smoothing=0)
    want = _reference_post_process(
        points, tris, attrs, water_smoothing=0, sea_smoothing_mode="zero"
    )
    assert np.array_equal(got, want)


def test_water_order_changes_the_result_and_the_flag_selects_it() -> None:
    """``water_in_set_order`` reproduces Ortho4XP; the other order gives a different mesh.

    The sweep is Gauss-Seidel, so the order matters as soon as the water triangles share
    vertices and the sweeps have not converged. On the reference tile the difference is
    97 622 vertex lines out of 603 649 (``mesh-build.md``); here we only need
    one draw where the two orders disagree.
    """
    differed = False
    for seed in range(14, 24):
        points, tris, attrs = _random_mesh(seed, n_pts=400, n_tris=900)
        classes = classify_triangles(attrs)
        set_order, mesh_order = points.copy(), points.copy()
        post_process_altitudes(set_order, tris, classes, water_smoothing=1, water_in_set_order=True)
        post_process_altitudes(
            mesh_order, tris, classes, water_smoothing=1, water_in_set_order=False
        )
        want = _reference_post_process(
            points, tris, attrs, water_smoothing=1, sea_smoothing_mode="zero"
        )
        assert np.array_equal(set_order, want), seed  # the default is always Ortho4XP's order
        differed |= not np.array_equal(mesh_order, want)
    assert differed, "the two orders never disagreed: the quirk would be untestable"


def test_interp_alt_wins_over_water_and_zeroes_the_normal() -> None:
    points = np.array(
        [
            [0.0, 0.0, 100.0, 0.5, 0.5, 7.0],
            [1.0, 0.0, 200.0, 0.5, 0.5, 8.0],
            [0.0, 1.0, 300.0, 0.5, 0.5, 9.0],
        ]
    )
    tris = np.array([[0, 1, 2]], dtype=np.int32)
    attrs = np.array([ATTRIBUTES["RUNWAY"]], dtype=np.int64)
    post_process_altitudes(points, tris, classify_triangles(attrs))
    assert points[:, 2].tolist() == [7.0, 8.0, 9.0]
    assert points[:, 3].tolist() == [0.0, 0.0, 0.0]
    assert points[:, 4].tolist() == [0.0, 0.0, 0.0]


# -- water table -------------------------------------------------------------------------------


def test_water_tris_table_selects_and_barycentres(tmp_path: Path) -> None:
    rng = np.random.default_rng(21)
    vertices = np.column_stack([5 + rng.random(200), 43 + rng.random(200), rng.normal(0, 10, 200)])
    tris = rng.integers(0, 200, (500, 3)).astype(np.int32)
    attrs = rng.choice(np.array([0, 1, 2, 3, 4, 8, 9, 10, 16, 128], dtype=np.int64), 500)
    table = water_tris_table("+43+005", vertices, tris, attrs)

    expected = [i for i, a in enumerate(attrs.tolist()) if a != 0 and a & HAS_WATER]
    assert table.tri_index.tolist() == expected
    assert np.array_equal(table.corners, tris[expected])
    assert table.water_bits.tolist() == [attrs[i] & HAS_WATER for i in expected]
    assert table.attr.tolist() == [attrs[i] for i in expected]
    # barycentre: Ortho4XP sums the three corners then divides, per axis
    for row, i in enumerate(expected):
        a, b, c = tris[i]
        assert table.bary[row, 0] == (vertices[a, 0] + vertices[b, 0] + vertices[c, 0]) / 3
        assert table.bary[row, 1] == (vertices[a, 1] + vertices[b, 1] + vertices[c, 1]) / 3
    assert sum(table.counts.values()) == len(table)

    write_water_tris_npz(tmp_path / "water_tris.npz", table)
    back = read_water_tris_npz(tmp_path / "water_tris.npz")
    assert back.tile == "+43+005"
    assert np.array_equal(back.tri_index, table.tri_index)
    assert np.array_equal(back.corners, table.corners)
    assert np.array_equal(back.water_bits, table.water_bits)
    assert np.array_equal(back.attr, table.attr)
    assert np.array_equal(back.bary, table.bary)
    assert read_water_tris_npz(tmp_path).tile == "+43+005"  # a directory works too


def test_water_tris_rejects_a_foreign_format(tmp_path: Path) -> None:
    np.savez(tmp_path / "x.npz", format=np.array("nope"))
    with pytest.raises(ValueError, match="osxp-water-tris-1"):
        read_water_tris_npz(tmp_path / "x.npz")


def test_water_tris_of_an_empty_mesh(tmp_path: Path) -> None:
    table = water_tris_table(
        "+43+005",
        np.zeros((0, 3)),
        np.zeros((0, 3), dtype=np.int32),
        np.zeros((0,), dtype=np.int64),
    )
    assert len(table) == 0 and table.bary.shape == (0, 2)
    write_water_tris_npz(tmp_path / "w.npz", table)
    assert len(read_water_tris_npz(tmp_path / "w.npz")) == 0
