"""``.mesh`` reader / writer / npz twin (spec ``docs/specs/mesh-file.md``)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from orthostudio.mesh.mesh_file import (
    NPZ_FORMAT,
    MeshData,
    MeshFormatError,
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
