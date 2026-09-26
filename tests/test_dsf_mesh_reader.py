# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The DSF stage reads the mesh through the shared ``orthostudio.mesh.mesh_file`` (one
mesh object)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from orthostudio.dsf import MeshData, read_mesh
from orthostudio.dsf._mesh_reader import mesh_version
from orthostudio.mesh import mesh_file

TEXT = """MeshVersionFormatted 2
Dimension 3

Vertices
4
5.100000000000000 43.200000000000003 0.000123456789012 0
5.200000000000000 43.200000000000003 -0.000000100000000 0
5.200000000000000 43.300000000000004 0.001000000000000 0
5.100000000000000 43.300000000000004 0.000000000000000 0

Normals
4
0.00 -0.40 0
0.97 0.12 0
-1.00 1.00 0
0.10 0.00 0

Triangles
2
1 2 3 0
1 3 4 130
"""


def test_the_dsf_reader_is_the_shared_reader() -> None:
    assert read_mesh is mesh_file.read_mesh
    assert MeshData is mesh_file.MeshData


def test_reads_the_ortho4xp_layout(tmp_path: Path) -> None:
    p = tmp_path / "Data+43+005.mesh"
    p.write_text(TEXT)
    m = read_mesh(p)
    assert isinstance(m, MeshData)
    assert m.vertices.shape == (4, 3) and m.vertices.dtype == np.float64
    assert m.vertices[0, 0] == float("5.100000000000000")
    assert m.vertices[0, 2] == float("0.000123456789012") * 100000
    assert m.vertices[1, 2] == float("-0.000000100000000") * 100000
    assert m.normals.dtype == np.float32
    assert np.allclose(m.normals, [[0.0, -0.4], [0.97, 0.12], [-1.0, 1.0], [0.1, 0.0]])
    assert m.tris.tolist() == [[0, 1, 2], [0, 2, 3]] and m.tris.dtype == np.int32
    assert m.tri_attr.tolist() == [0, 130] and m.tri_attr.dtype == np.uint8
    assert mesh_version(m) == 2.0


def test_mesh_version_honours_both_spellings() -> None:
    base = dict(
        vertices=np.zeros((1, 3)), normals=np.zeros((1, 2)), tris=np.zeros((0, 3)),
        tri_attr=np.zeros((0,)),
    )  # fmt: skip
    assert mesh_version(MeshData(**base, extra={"version": "1", "dimension": "3"})) == 1.0
    assert mesh_version(MeshData(**base)) == 2.0
    # a plain MeshLike (the DSF tests' shim) may spell it the old way
    assert mesh_version(SimpleNamespace(extra={"mesh_version": 1.0})) == 1.0  # type: ignore[arg-type]
    assert mesh_version(SimpleNamespace(extra={})) == 2.0  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda t: t.replace("Vertices\n4", "Vertices\n5"),
        lambda t: t.replace("Normals\n4", "Normals\n3"),
        lambda t: t.replace("Triangles\n2", "Triangles\n3"),
        lambda t: t.replace("1 3 4 130", "1 3 9 130"),
        lambda t: "garbage\n" + t,
    ],
)
def test_corrupted_files_raise(tmp_path: Path, mutation) -> None:
    p = tmp_path / "Data+43+005.mesh"
    p.write_text(mutation(TEXT))
    with pytest.raises((mesh_file.MeshFormatError, ValueError)):
        read_mesh(p)
