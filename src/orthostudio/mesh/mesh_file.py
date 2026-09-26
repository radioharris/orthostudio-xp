# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The ``.mesh`` file of Ortho4XP: typed reader, byte-identical writer, ``.npz`` twin.

Spec: ``docs/specs/mesh-file.md``. Origin: ``O4_Mesh_Utils.py:326-372`` (``write_mesh_file``)
and ``O4_Mesh_Utils.py:847-894`` (``read_mesh_file``).

:class:`MeshData` keeps Ortho4XP's *in-memory* semantics, not the file's: altitudes in metres (the
file stores ``z / 100000``) and 0-based triangle corners (the file is 1-based), because every
consumer in OrthoStudio XP (masks, DSF) is a port of an Ortho4XP consumer of ``read_mesh_file``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from orthostudio.numtext import parse_numbers

__all__ = [
    "NPZ_FORMAT",
    "Z_SCALE",
    "MeshData",
    "MeshFormatError",
    "read_mesh",
    "read_mesh_npz",
    "write_mesh_npz",
    "write_mesh_text",
]

NPZ_FORMAT = "osxp-mesh-npz-1"
Z_SCALE = 100000
"""Altitudes are written as metres / 100000 (``O4_Mesh_Utils.py:348``) and read back times it."""

_VERSION_TAG = b"MeshVersionFormatted"
_DIMENSION_TAG = b"Dimension"
_VERTICES_TAG = b"Vertices\n"
_NORMALS_TAG = b"Normals\n"
_TRIANGLES_TAG = b"Triangles\n"
_WRITE_CHUNK = 65536


class MeshFormatError(ValueError):
    """A ``.mesh`` file (or its npz twin) is malformed."""


@dataclass(slots=True)
class MeshData:
    """Content of one ``.mesh`` file with Ortho4XP's ``read_mesh_file`` semantics.

    ``vertices``: float64 ``(N, 3)`` absolute lon, lat (degrees) and **z in metres**.
    ``normals``: float32 ``(N, 2)``, the two written components (the third column of the file
    is the constant ``0``). ``tris``: int32 ``(M, 3)`` **0-based** corners. ``tri_attr``:
    uint8 ``(M,)`` Triangle4XP region attribute (bit set, DUMMY 0 ... HANGAR 128).
    ``extra``: ``version`` and ``dimension`` header tokens, kept verbatim as strings.
    """

    vertices: NDArray[np.float64]
    normals: NDArray[np.float32]
    tris: NDArray[np.int32]
    tri_attr: NDArray[np.uint8]
    extra: dict[str, Any] = field(default_factory=lambda: {"version": "2", "dimension": "3"})

    def __post_init__(self) -> None:
        self.vertices = np.ascontiguousarray(self.vertices, dtype=np.float64)
        self.normals = np.ascontiguousarray(self.normals, dtype=np.float32)
        self.tris = np.ascontiguousarray(self.tris, dtype=np.int32)
        self.tri_attr = np.ascontiguousarray(self.tri_attr, dtype=np.uint8)
        n, m = self.n_vertices, self.n_triangles
        if self.vertices.ndim != 2 or self.vertices.shape[1] != 3:
            raise MeshFormatError("vertices must be an (N, 3) array")
        if self.normals.shape != (n, 2):
            raise MeshFormatError(f"normals must be an ({n}, 2) array, got {self.normals.shape}")
        if self.tris.ndim != 2 or self.tris.shape[1] != 3:
            raise MeshFormatError("tris must be an (M, 3) array")
        if self.tri_attr.shape != (m,):
            raise MeshFormatError(f"tri_attr must be an ({m},) array, got {self.tri_attr.shape}")
        if m and (self.tris.min() < 0 or self.tris.max() >= n):
            raise MeshFormatError("triangle corners must be 0-based indices into vertices")
        self.extra.setdefault("version", "2")
        self.extra.setdefault("dimension", "3")

    @property
    def n_vertices(self) -> int:
        return int(self.vertices.shape[0])

    @property
    def n_triangles(self) -> int:
        return int(self.tris.shape[0])

    @property
    def version(self) -> float:
        """The ``MeshVersionFormatted`` token as Ortho4XP reads it (``float``)."""
        return float(self.extra["version"])

    def __repr__(self) -> str:
        return f"MeshData({self.n_vertices} vertices, {self.n_triangles} triangles)"


# -- text reader ---------------------------------------------------------------------------


def _parse_block(block: bytes, dtype: type, count: int, width: int, what: str) -> NDArray[Any]:
    """Parse whitespace-separated numbers of ``block`` into a ``(count, width)`` array."""
    if not block.strip():
        if count:
            raise MeshFormatError(f"{what}: expected {count} lines, found none")
        return np.zeros((0, width), dtype=dtype)
    values: NDArray[Any] = parse_numbers(block, dtype)
    if values.size != count * width:
        raise MeshFormatError(
            f"{what}: expected {count} lines of {width} values, parsed {values.size} values"
        )
    return values.reshape(count, width)


def _header_int(data: bytes, pos: int, what: str) -> tuple[int, int]:
    """Read the integer line starting at ``pos``; return ``(value, position after '\\n')``."""
    end = data.find(b"\n", pos)
    if end < 0:
        raise MeshFormatError(f"{what}: unexpected end of file")
    try:
        return int(data[pos:end]), end + 1
    except ValueError:
        raise MeshFormatError(f"{what}: {data[pos:end]!r} is not a count") from None


def _find(data: bytes, tag: bytes, start: int, what: str) -> int:
    pos = data.find(tag, start)
    if pos < 0:
        raise MeshFormatError(f"{what}: section {tag.decode().strip()!r} not found")
    return pos


def read_mesh(path: str | Path) -> MeshData:
    """Read an Ortho4XP ``.mesh`` file into a :class:`MeshData` (spec section 6)."""
    data = Path(path).read_bytes()
    if b"\r\n" in data[:64]:
        # Ortho4XP writes the file in text mode (``O4_Mesh_Utils.py:326-372``): CRLF on Windows.
        data = data.replace(b"\r\n", b"\n")
    first_eol = data.find(b"\n")
    if first_eol < 0 or not data.startswith(_VERSION_TAG):
        raise MeshFormatError("not a .mesh file (missing 'MeshVersionFormatted' header)")
    version = data[len(_VERSION_TAG) : first_eol].strip().decode("ascii")
    second_eol = data.find(b"\n", first_eol + 1)
    if second_eol < 0 or not data.startswith(_DIMENSION_TAG, first_eol + 1):
        raise MeshFormatError("missing 'Dimension' header line")
    dimension = data[first_eol + 1 + len(_DIMENSION_TAG) : second_eol].strip().decode("ascii")

    v_tag = _find(data, _VERTICES_TAG, second_eol, "vertices")
    n_vertices, v_start = _header_int(data, v_tag + len(_VERTICES_TAG), "vertex count")
    n_tag = _find(data, _NORMALS_TAG, v_start, "normals")
    n_normals, n_start = _header_int(data, n_tag + len(_NORMALS_TAG), "normal count")
    t_tag = _find(data, _TRIANGLES_TAG, n_start, "triangles")
    n_tris, t_start = _header_int(data, t_tag + len(_TRIANGLES_TAG), "triangle count")
    if n_normals != n_vertices:
        raise MeshFormatError(f"{n_normals} normals for {n_vertices} vertices")

    verts = _parse_block(data[v_start:n_tag], np.float64, n_vertices, 4, "vertices")
    norms = _parse_block(data[n_start:t_tag], np.float64, n_vertices, 3, "normals")
    tris = _parse_block(data[t_start:], np.int64, n_tris, 4, "triangles")
    if n_vertices and verts[:, 3].any():
        raise MeshFormatError("vertex reference column is not 0 (not written by Ortho4XP)")
    if n_vertices and norms[:, 2].any():
        raise MeshFormatError("third normal column is not 0 (not written by Ortho4XP)")
    corners = tris[:, :3] - 1
    if n_tris and (corners.min() < 0 or corners.max() >= n_vertices):
        raise MeshFormatError("triangle corners out of range")
    attr = tris[:, 3]
    if n_tris and (attr.min() < 0 or attr.max() > 255):
        raise MeshFormatError("triangle attribute out of the uint8 range")

    vertices = verts[:, :3].copy()
    vertices[:, 2] *= Z_SCALE  # read_mesh_file:864
    return MeshData(
        vertices=vertices,
        normals=norms[:, :2].astype(np.float32),
        tris=corners.astype(np.int32),
        tri_attr=attr.astype(np.uint8),
        extra={"version": version, "dimension": dimension},
    )


# -- text writer ---------------------------------------------------------------------------


def _lines(rows: list[Any], fmt: str) -> str:
    return "".join(fmt % tuple(row) for row in rows)


def write_mesh_text(path: str | Path, mesh: MeshData) -> None:
    """Write ``mesh`` in Ortho4XP's exact text layout (spec sections 2 and 4), ``.tmp`` + rename."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    n, m = mesh.n_vertices, mesh.n_triangles
    with open(tmp, "w", encoding="ascii", newline="\n") as f:
        f.write(f"MeshVersionFormatted {mesh.extra['version']}\n")
        f.write(f"Dimension {mesh.extra['dimension']}\n\n")
        f.write(f"Vertices\n{n}\n")
        scaled = mesh.vertices.copy()
        scaled[:, 2] /= Z_SCALE
        for start in range(0, n, _WRITE_CHUNK):
            f.write(_lines(scaled[start : start + _WRITE_CHUNK].tolist(), "%.15f %.15f %.15f 0\n"))
        f.write(f"\nNormals\n{n}\n")
        for start in range(0, n, _WRITE_CHUNK):
            f.write(_lines(mesh.normals[start : start + _WRITE_CHUNK].tolist(), "%.2f %.2f 0\n"))
        f.write(f"\nTriangles\n{m}\n")
        table = np.empty((m, 4), dtype=np.int64)
        table[:, :3] = mesh.tris.astype(np.int64) + 1
        table[:, 3] = mesh.tri_attr
        for start in range(0, m, _WRITE_CHUNK):
            f.write(_lines(table[start : start + _WRITE_CHUNK].tolist(), "%d %d %d %d\n"))
    tmp.replace(path)


# -- npz twin ------------------------------------------------------------------------------


def write_mesh_npz(path: str | Path, mesh: MeshData) -> None:
    """Save ``mesh`` as an uncompressed ``.npz`` (spec section 5), ``.tmp`` + rename."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez(
            f,
            format=np.array(NPZ_FORMAT),
            vertices=mesh.vertices,
            normals=mesh.normals,
            tris=mesh.tris,
            tri_attr=mesh.tri_attr,
            extra_json=np.array(json.dumps(mesh.extra, sort_keys=True)),
        )
    tmp.replace(path)


def read_mesh_npz(path: str | Path) -> MeshData:
    """Load a ``.npz`` written by :func:`write_mesh_npz`."""
    with np.load(Path(path), allow_pickle=False) as z:
        try:
            fmt = str(z["format"])
        except KeyError:
            raise MeshFormatError(
                f"{path}: not an OrthoStudio XP mesh npz (no 'format' key)"
            ) from None
        if fmt != NPZ_FORMAT:
            raise MeshFormatError(f"{path}: format {fmt!r}, expected {NPZ_FORMAT!r}")
        extra = json.loads(str(z["extra_json"]))
        return MeshData(
            vertices=z["vertices"],
            normals=z["normals"],
            tris=z["tris"],
            tri_attr=z["tri_attr"],
            extra=extra,
        )
