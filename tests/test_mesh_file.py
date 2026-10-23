# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``.mesh`` reader / writer / npz twin (spec ``docs/specs/mesh-file.md``)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from orthostudio.mesh.mesh_file import (
    NPZ_FORMAT,
    MeshData,
    MeshFormatError,
    as_written,
    read_mesh,
    read_mesh_npz,
    write_mesh_npz,
    write_mesh_text,
)

pytestmark = pytest.mark.usefixtures("number_parsing")

SAMPLE = (
    "MeshVersionFormatted 2\n"
    "Dimension 3\n"
    "\n"
    "Vertices\n"
    "4\n"
    "5.117254700000000 43.605783099999996 0.000596498252070 0\n"
    "5.117222000000000 43.605866200000001 0.000596801106090 0\n"
    "5.000000000000000 43.000000000000000 0.000000000000000 0\n"
    "6.000000000000000 44.000000000000000 0.012345678901234 0\n"
    "\n"
    "Normals\n"
    "4\n"
    "0.06 -0.11 0\n"
    "-0.00 0.00 0\n"
    "1.00 -1.00 0\n"
    "0.50 0.25 0\n"
    "\n"
    "Triangles\n"
    "2\n"
    "1 2 3 0\n"
    "2 3 4 128\n"
)


def _sample(tmp_path: Path) -> Path:
    p = tmp_path / "Data+43+005.mesh"
    p.write_text(SAMPLE, encoding="ascii", newline="\n")
    return p


def test_read_sample_has_ortho4xp_semantics(tmp_path: Path) -> None:
    m = read_mesh(_sample(tmp_path))
    assert m.n_vertices == 4 and m.n_triangles == 2
    assert m.vertices.dtype == np.float64 and m.vertices.shape == (4, 3)
    assert m.vertices[0, 0] == 5.1172547 and m.vertices[0, 1] == 43.605783099999996
    # altitude scaled by 100000 as read_mesh_file does (O4_Mesh_Utils.py:864)
    assert m.vertices[0, 2] == 0.000596498252070 * 100000
    assert m.normals.dtype == np.float32 and m.normals.shape == (4, 2)
    assert m.normals[0, 0] == np.float32(0.06)
    assert np.signbit(m.normals[1, 0])  # -0.00 survives as -0.0
    assert m.tris.dtype == np.int32 and m.tris.tolist() == [[0, 1, 2], [1, 2, 3]]  # 0-based
    assert m.tri_attr.dtype == np.uint8 and m.tri_attr.tolist() == [0, 128]
    assert m.extra == {"version": "2", "dimension": "3"} and m.version == 2.0


def test_write_text_reproduces_sample_bytes(tmp_path: Path) -> None:
    src = _sample(tmp_path)
    m = read_mesh(src)
    out = tmp_path / "rewrite.mesh"
    write_mesh_text(out, m)
    assert out.read_bytes() == src.read_bytes()
    assert not out.with_name(out.name + ".tmp").exists()


def test_npz_round_trip_is_exact(tmp_path: Path) -> None:
    m = read_mesh(_sample(tmp_path))
    npz = tmp_path / "mesh.npz"
    write_mesh_npz(npz, m)
    back = read_mesh_npz(npz)
    for name in ("vertices", "normals", "tris", "tri_attr"):
        a, b = getattr(m, name), getattr(back, name)
        assert a.dtype == b.dtype and a.shape == b.shape
        assert a.tobytes() == b.tobytes()
    assert back.extra == m.extra
    with np.load(npz) as z:
        assert str(z["format"]) == NPZ_FORMAT


def test_npz_with_wrong_format_is_rejected(tmp_path: Path) -> None:
    npz = tmp_path / "x.npz"
    np.savez(npz, format=np.array("something-else"), vertices=np.zeros((0, 3)))
    with pytest.raises(MeshFormatError):
        read_mesh_npz(npz)


def test_meshdata_validates_shapes() -> None:
    v = np.zeros((3, 3))
    with pytest.raises(MeshFormatError):
        MeshData(v, np.zeros((2, 2), np.float32), np.zeros((1, 3), np.int32), np.zeros(1, np.uint8))
    with pytest.raises(MeshFormatError):  # corner out of range
        MeshData(v, np.zeros((3, 2), np.float32), np.array([[0, 1, 3]]), np.zeros(1, np.uint8))
    m = MeshData(v, np.zeros((3, 2), np.float32), np.array([[0, 1, 2]]), np.zeros(1, np.uint8))
    assert m.extra["version"] == "2"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.replace("MeshVersionFormatted", "Bogus"),
        lambda s: s.replace("Vertices\n4\n", "Vertices\n5\n"),
        lambda s: s.replace("0.06 -0.11 0\n", "0.06 -0.11 1\n"),
        lambda s: s.replace("1 2 3 0\n", "1 2 9 0\n"),
        lambda s: s.replace("2 3 4 128\n", "2 3 4 300\n"),
        lambda s: s.replace("\nTriangles\n", "\nTriangle\n"),
    ],
)
def test_malformed_files_are_rejected(tmp_path: Path, mutation) -> None:
    p = tmp_path / "bad.mesh"
    p.write_text(mutation(SAMPLE), encoding="ascii", newline="\n")
    with pytest.raises(MeshFormatError):
        read_mesh(p)


def test_empty_mesh_round_trips(tmp_path: Path) -> None:
    m = MeshData(
        np.zeros((0, 3)),
        np.zeros((0, 2), np.float32),
        np.zeros((0, 3), np.int32),
        np.zeros(0, np.uint8),
    )
    p = tmp_path / "empty.mesh"
    write_mesh_text(p, m)
    back = read_mesh(p)
    assert back.n_vertices == 0 and back.n_triangles == 0


def _same(a: MeshData, b: MeshData) -> bool:
    """Bit for bit, NaN and the sign of zero included."""
    return (
        a.vertices.tobytes() == b.vertices.tobytes()
        and a.normals.tobytes() == b.normals.tobytes()
        and a.tris.tobytes() == b.tris.tobytes()
        and a.tri_attr.tobytes() == b.tri_attr.tobytes()
        and a.extra == b.extra
    )


def _through_text(tmp_path: Path, mesh: MeshData) -> MeshData:
    p = tmp_path / "Data+46+006.mesh"
    write_mesh_text(p, mesh)
    return read_mesh(p)


def _mesh(vertices: np.ndarray, normals: np.ndarray, extra: dict | None = None) -> MeshData:
    n = len(vertices)
    tris = np.array([[i, (i + 1) % n, (i + 2) % n] for i in range(max(0, n - 2))], np.int32)
    attr = (np.arange(len(tris)) * 37 % 256).astype(np.uint8)
    return MeshData(vertices, normals, tris.reshape(-1, 3), attr, extra or {})


def test_the_npz_read_as_written_is_the_text_read_back(tmp_path: Path) -> None:
    """The DSF reads the npz as the text would have given it: the cache keeps the npz alone since
    2026-10-23, the text being the same mesh a second time (272 GB of a user's 1.1 TB). Bit for bit
    on a real-sized mesh over several write chunks, with Triangle4XP's full precision."""
    rng = np.random.default_rng(7)
    n = 140_000  # more than two chunks of the writer
    vertices = np.column_stack(
        [
            5 + rng.random(n),
            43 + rng.random(n),
            rng.uniform(-430.0, 4810.0, n),  # the Dead Sea's shore to Mont Blanc, in metres
        ]
    )
    normals = rng.uniform(-1, 1, (n, 2)).astype(np.float32)
    mesh = _mesh(vertices, normals)
    written = as_written(mesh)
    assert _same(written, _through_text(tmp_path, mesh))
    assert not np.array_equal(written.vertices, mesh.vertices)  # the text does round
    assert _same(as_written(written), written)  # and once rounded, stays


def test_the_npz_read_as_written_holds_at_the_edges(tmp_path: Path) -> None:
    """Numbers on a rounding boundary of 15 or of 2 decimals, both signs of zero, the ends of the
    globe, the deepest and highest ground, the tiniest and NaN, an empty mesh, a single point, and
    header tokens as the npz keeps them."""
    halves = [0.1234567890123455, -0.1234567890123455, 2.0000000000000005, 1e-16, -1e-16, 5e-16]
    lons = [-180.0, 180.0, -0.0, 0.0, 179.99999999999997, *halves, float("nan")]
    lats = [90.0, -90.0, 0.0, -0.0, 89.99999999999999, *halves, 45.0]
    zs = [-11034.0, 8848.86, 0.0, -0.0, 1e-12, -1e-12, 0.000005, 4.999999999999999e-11,
          5e-11, 1234.5678901234567, 1e-300, 3.0]  # fmt: skip
    normal_values = [0.005, -0.005, 0.015, 0.125, -0.125, 0.995, -0.995, 1.0, -0.0, 0.0, 0.335,
                     -0.665, float("nan")]  # fmt: skip
    vertices = np.column_stack([lons, lats, zs])
    normals = np.column_stack([normal_values[:12], normal_values[1:13]]).astype(np.float32)
    mesh = _mesh(vertices, normals, {"version": "2", "dimension": "3"})
    assert _same(as_written(mesh), _through_text(tmp_path, mesh))
    one = _mesh(vertices[:1], normals[:1], {"version": " 2 ", "dimension": 3})
    assert _same(as_written(one), _through_text(tmp_path, one))
    empty = _mesh(np.zeros((0, 3)), np.zeros((0, 2), np.float32))
    assert _same(as_written(empty), _through_text(tmp_path, empty))
