# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Read and write the files exchanged with the Triangle4XP sidecar.

Two encodings are supported for every file kind:

* the Triangle text format (``.node``, ``.poly``, ``.ele``) that Ortho4XP uses, which the
  vector stage still publishes and which is readable when a mesh must be inspected;
* the OrthoStudio XP binary format (``OSXPNOD1``, ``OSXPPOL1``, ``OSXPELE1`` magics) that the
  patched Triangle4XP reads and writes when invoked with ``-b``.

Layouts are specified in ``docs/specs/mesh-triangle-io.md``.  Integers are int32 and reals
are float64, in native byte order (little-endian on every supported platform).  Vertex
numbers inside segments and triangles are kept exactly as Triangle numbers them, i.e.
starting at ``first_number`` (1 in Ortho4XP), not converted to 0-based indices.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np
from numpy.typing import NDArray

from orthostudio.numtext import parse_numbers

NODE_MAGIC = b"OSXPNOD1"
POLY_MAGIC = b"OSXPPOL1"
ELE_MAGIC = b"OSXPELE1"
MAGIC_LEN = 8

_NODE_HEADER = struct.Struct("=5i")  # n_vertices, dim, n_attrs, n_markers, first_number
_ELE_HEADER = struct.Struct("=4i")  # n_triangles, n_corners, n_attrs, first_number
_SEG_HEADER = struct.Struct("=2i")  # n_segments, seg_markers
_COUNT = struct.Struct("=i")

F64 = np.dtype("=f8")
I32 = np.dtype("=i4")


class TriangleFormatError(ValueError):
    """A Triangle file (text or binary) is malformed."""


@dataclass(frozen=True, slots=True)
class NodeTable:
    """Vertices of a ``.node`` file: ``points[:, 0:2]`` are x, y; the rest are attributes."""

    points: NDArray[np.float64]
    markers: NDArray[np.int32] | None = None
    first_number: int = 1

    def __post_init__(self) -> None:
        if self.points.ndim != 2 or self.points.shape[1] < 2:
            raise TriangleFormatError("points must be an (n, 2 + n_attrs) array")
        if self.markers is not None and self.markers.shape != (self.n_vertices,):
            raise TriangleFormatError("markers must be an (n,) array")
        if self.first_number not in (0, 1):
            raise TriangleFormatError("first_number must be 0 or 1")

    @property
    def n_vertices(self) -> int:
        return int(self.points.shape[0])

    @property
    def n_attrs(self) -> int:
        return int(self.points.shape[1]) - 2

    @property
    def xy(self) -> NDArray[np.float64]:
        return self.points[:, :2]

    @property
    def attrs(self) -> NDArray[np.float64]:
        return self.points[:, 2:]


@dataclass(frozen=True, slots=True)
class Pslg:
    """Content of a ``.poly`` file (planar straight-line graph).

    ``nodes`` is ``None`` when the vertices live in a separate ``.node`` file (Ortho4XP
    always does this).  ``segments`` holds vertex numbers as Triangle numbers them.
    ``regions`` rows are ``(x, y, attribute, max_area)``; Triangle's text reader copies the
    attribute into ``max_area`` when the fourth column is absent, and so does
    :func:`read_poly_text`.
    """

    segments: NDArray[np.int32]
    segment_markers: NDArray[np.int32] | None = None
    holes: NDArray[np.float64] | None = None
    regions: NDArray[np.float64] | None = None
    nodes: NodeTable | None = None
    first_number: int = 1

    def __post_init__(self) -> None:
        if self.segments.ndim != 2 or self.segments.shape[1] != 2:
            raise TriangleFormatError("segments must be an (m, 2) array")
        if self.segment_markers is not None and self.segment_markers.shape != (self.n_segments,):
            raise TriangleFormatError("segment_markers must be an (m,) array")
        if self.holes is not None and (self.holes.ndim != 2 or self.holes.shape[1] != 2):
            raise TriangleFormatError("holes must be an (h, 2) array")
        if self.regions is not None and (self.regions.ndim != 2 or self.regions.shape[1] != 4):
            raise TriangleFormatError("regions must be an (r, 4) array")

    @property
    def n_segments(self) -> int:
        return int(self.segments.shape[0])


@dataclass(frozen=True, slots=True)
class Elements:
    """Triangles of an ``.ele`` file: vertex numbers per corner plus float attributes."""

    triangles: NDArray[np.int32]
    attributes: NDArray[np.float64]
    first_number: int = 1

    def __post_init__(self) -> None:
        if self.triangles.ndim != 2 or self.triangles.shape[1] not in (3, 6):
            raise TriangleFormatError("triangles must be an (t, 3) or (t, 6) array")
        if self.attributes.ndim != 2 or self.attributes.shape[0] != self.n_triangles:
            raise TriangleFormatError("attributes must be a (t, n_attrs) array")

    @property
    def n_triangles(self) -> int:
        return int(self.triangles.shape[0])

    @property
    def n_attrs(self) -> int:
        return int(self.attributes.shape[1])


# --------------------------------------------------------------------------- binary


def _read_exact(f: BinaryIO, n: int, what: str) -> bytes:
    data = f.read(n)
    if len(data) != n:
        raise TriangleFormatError(f"unexpected end of file while reading {what}")
    return data


def _read_array(f: BinaryIO, dtype: np.dtype, shape: tuple[int, ...], what: str) -> np.ndarray:
    count = int(np.prod(shape)) if shape else 0
    data = _read_exact(f, count * dtype.itemsize, what)
    return np.frombuffer(data, dtype=dtype).reshape(shape)


def _expect_magic(f: BinaryIO, magic: bytes, path: Path) -> None:
    got = _read_exact(f, MAGIC_LEN, "magic")
    if got != magic:
        raise TriangleFormatError(f"{path}: expected magic {magic!r}, found {got!r}")


def _write_node_body(f: BinaryIO, nodes: NodeTable) -> None:
    n_markers = 0 if nodes.markers is None else 1
    f.write(_NODE_HEADER.pack(nodes.n_vertices, 2, nodes.n_attrs, n_markers, nodes.first_number))
    f.write(np.ascontiguousarray(nodes.points, dtype=F64).tobytes())
    if nodes.markers is not None:
        f.write(np.ascontiguousarray(nodes.markers, dtype=I32).tobytes())


def _read_node_body(f: BinaryIO, path: Path) -> NodeTable:
    n, dim, n_attrs, n_markers, first = _NODE_HEADER.unpack(
        _read_exact(f, _NODE_HEADER.size, "node header")
    )
    if dim != 2 or n_attrs < 0 or n < 0:
        raise TriangleFormatError(f"{path}: bad node header {(n, dim, n_attrs, n_markers)}")
    points = _read_array(f, F64, (n, 2 + n_attrs), "vertex table")
    markers = _read_array(f, I32, (n,), "vertex markers") if n_markers else None
    return NodeTable(points=points, markers=markers, first_number=first)


def write_node_binary(path: str | Path, nodes: NodeTable) -> None:
    """Write an ``OSXPNOD1`` file."""
    with open(path, "wb") as f:
        f.write(NODE_MAGIC)
        _write_node_body(f, nodes)


def read_node_binary(path: str | Path) -> NodeTable:
    """Read an ``OSXPNOD1`` file (input or Triangle4XP output)."""
    path = Path(path)
    with open(path, "rb") as f:
        _expect_magic(f, NODE_MAGIC, path)
        return _read_node_body(f, path)


def write_poly_binary(path: str | Path, pslg: Pslg) -> None:
    """Write an ``OSXPPOL1`` file."""
    with open(path, "wb") as f:
        f.write(POLY_MAGIC)
        if pslg.nodes is None:
            f.write(_NODE_HEADER.pack(0, 2, 0, 0, pslg.first_number))
        else:
            _write_node_body(f, pslg.nodes)
        seg_markers = 0 if pslg.segment_markers is None else 1
        f.write(_SEG_HEADER.pack(pslg.n_segments, seg_markers))
        f.write(np.ascontiguousarray(pslg.segments, dtype=I32).tobytes())
        if pslg.segment_markers is not None:
            f.write(np.ascontiguousarray(pslg.segment_markers, dtype=I32).tobytes())
        holes = np.zeros((0, 2)) if pslg.holes is None else pslg.holes
        f.write(_COUNT.pack(holes.shape[0]))
        f.write(np.ascontiguousarray(holes, dtype=F64).tobytes())
        regions = np.zeros((0, 4)) if pslg.regions is None else pslg.regions
        f.write(_COUNT.pack(regions.shape[0]))
        f.write(np.ascontiguousarray(regions, dtype=F64).tobytes())


def read_poly_binary(path: str | Path) -> Pslg:
    """Read an ``OSXPPOL1`` file."""
    path = Path(path)
    with open(path, "rb") as f:
        _expect_magic(f, POLY_MAGIC, path)
        nodes: NodeTable | None = _read_node_body(f, path)
        assert nodes is not None
        first = nodes.first_number
        if nodes.n_vertices == 0:
            nodes = None
        n_seg, seg_markers = _SEG_HEADER.unpack(_read_exact(f, _SEG_HEADER.size, "segments"))
        segments = _read_array(f, I32, (n_seg, 2), "segment table")
        markers = _read_array(f, I32, (n_seg,), "segment markers") if seg_markers else None
        (n_holes,) = _COUNT.unpack(_read_exact(f, _COUNT.size, "hole count"))
        holes = _read_array(f, F64, (n_holes, 2), "holes")
        (n_regions,) = _COUNT.unpack(_read_exact(f, _COUNT.size, "region count"))
        regions = _read_array(f, F64, (n_regions, 4), "regions")
    return Pslg(
        segments=segments,
        segment_markers=markers,
        holes=holes,
        regions=regions,
        nodes=nodes,
        first_number=first,
    )


def write_ele_binary(path: str | Path, elements: Elements) -> None:
    """Write an ``OSXPELE1`` file (what Triangle4XP ``-b`` produces; used in tests)."""
    with open(path, "wb") as f:
        f.write(ELE_MAGIC)
        f.write(
            _ELE_HEADER.pack(
                elements.n_triangles,
                elements.triangles.shape[1],
                elements.n_attrs,
                elements.first_number,
            )
        )
        f.write(np.ascontiguousarray(elements.triangles, dtype=I32).tobytes())
        f.write(np.ascontiguousarray(elements.attributes, dtype=F64).tobytes())


def read_ele_binary(path: str | Path) -> Elements:
    """Read an ``OSXPELE1`` file."""
    path = Path(path)
    with open(path, "rb") as f:
        _expect_magic(f, ELE_MAGIC, path)
        n, corners, n_attrs, first = _ELE_HEADER.unpack(
            _read_exact(f, _ELE_HEADER.size, "element header")
        )
        if corners not in (3, 6) or n < 0 or n_attrs < 0:
            raise TriangleFormatError(f"{path}: bad element header {(n, corners, n_attrs)}")
        triangles = _read_array(f, I32, (n, corners), "triangle table")
        attributes = _read_array(f, F64, (n, n_attrs), "triangle attributes")
    return Elements(triangles=triangles, attributes=attributes, first_number=first)


# ----------------------------------------------------------------------------- text


def _numeric_lines(path: Path) -> Iterator[list[str]]:
    """Yield the fields of every line that is neither blank nor a ``#`` comment."""
    with open(path, encoding="ascii", errors="replace") as f:
        for line in f:
            stripped = line.lstrip()
            if not stripped or stripped[0] == "#":
                continue
            yield stripped.split("#", 1)[0].split()


def _table(rows: list[list[str]], n_cols: int, what: str) -> NDArray[np.float64]:
    """Parse ``rows`` (each ``n_cols`` numeric fields) into a float64 array, fast."""
    if not rows:
        return np.zeros((0, n_cols))
    flat = " ".join(" ".join(r[:n_cols]) for r in rows)
    values = parse_numbers(flat, np.float64)
    if values.size != len(rows) * n_cols:
        raise TriangleFormatError(f"malformed {what}: expected {n_cols} fields per line")
    return values.reshape(len(rows), n_cols)


def _parse_node_rows(
    rows: list[list[str]], n: int, n_attrs: int, n_markers: int, what: str
) -> NodeTable:
    if len(rows) != n:
        raise TriangleFormatError(f"{what}: {n} vertices announced, {len(rows)} found")
    table = _table(rows, 1 + 2 + n_attrs + n_markers, what)
    first = int(table[0, 0]) if n else 1
    if first not in (0, 1):
        first = 1
    points = np.ascontiguousarray(table[:, 1 : 3 + n_attrs])
    markers = table[:, 3 + n_attrs].astype(np.int32) if n_markers else None
    return NodeTable(points=points, markers=markers, first_number=first)


def _header(fields: list[str], defaults: tuple[int, ...]) -> tuple[int, ...]:
    values = list(defaults)
    for i, field in enumerate(fields[: len(defaults)]):
        values[i] = int(field)
    return tuple(values)


def read_node_text(path: str | Path) -> NodeTable:
    """Read a Triangle text ``.node`` file (Ortho4XP input or Triangle4XP text output)."""
    path = Path(path)
    lines = _numeric_lines(path)
    try:
        n, dim, n_attrs, n_markers = _header(next(lines), (0, 2, 0, 0))
    except StopIteration as exc:
        raise TriangleFormatError(f"{path}: empty file") from exc
    if dim != 2:
        raise TriangleFormatError(f"{path}: only 2-D node files are supported")
    return _parse_node_rows(list(lines), n, n_attrs, n_markers, str(path))


def read_ele_text(path: str | Path) -> Elements:
    """Read a Triangle text ``.ele`` file."""
    path = Path(path)
    lines = _numeric_lines(path)
    try:
        n, corners, n_attrs = _header(next(lines), (0, 3, 0))
    except StopIteration as exc:
        raise TriangleFormatError(f"{path}: empty file") from exc
    rows = list(lines)
    if len(rows) != n:
        raise TriangleFormatError(f"{path}: {n} triangles announced, {len(rows)} found")
    table = _table(rows, 1 + corners + n_attrs, str(path))
    first = int(table[0, 0]) if n else 1
    triangles = np.ascontiguousarray(table[:, 1 : 1 + corners]).astype(np.int32)
    attributes = np.ascontiguousarray(table[:, 1 + corners :])
    return Elements(triangles=triangles, attributes=attributes, first_number=first)


def read_poly_text(path: str | Path) -> Pslg:
    """Read a Triangle text ``.poly`` file (as written by Ortho4XP)."""
    path = Path(path)
    lines = _numeric_lines(path)
    try:
        n, dim, n_attrs, n_markers = _header(next(lines), (0, 2, 0, 0))
    except StopIteration as exc:
        raise TriangleFormatError(f"{path}: empty file") from exc
    if dim != 2:
        raise TriangleFormatError(f"{path}: only 2-D poly files are supported")
    nodes = None
    first = 1
    if n > 0:
        rows = [next(lines) for _ in range(n)]
        nodes = _parse_node_rows(rows, n, n_attrs, n_markers, str(path))
        first = nodes.first_number
    n_seg, seg_markers = _header(next(lines), (0, 0))
    seg_rows = [next(lines) for _ in range(n_seg)]
    seg_table = _table(seg_rows, 3 + seg_markers, f"{path} segments")
    segments = np.ascontiguousarray(seg_table[:, 1:3]).astype(np.int32)
    markers = seg_table[:, 3].astype(np.int32) if seg_markers else None
    (n_holes,) = _header(next(lines), (0,))
    holes = _table([next(lines) for _ in range(n_holes)], 3, f"{path} holes")[:, 1:3]
    regions = np.zeros((0, 4))
    region_header = next(lines, None)
    if region_header is not None:
        (n_regions,) = _header(region_header, (0,))
        region_rows = []
        for _ in range(n_regions):
            fields = next(lines)
            if len(fields) < 4:
                raise TriangleFormatError(f"{path}: region line with fewer than 3 values")
            # Triangle: a missing max_area column defaults to the attribute value.
            area = fields[4] if len(fields) > 4 else fields[3]
            region_rows.append([*fields[1:4], area])
        regions = _table(region_rows, 4, f"{path} regions")
    return Pslg(
        segments=segments,
        segment_markers=markers,
        holes=np.ascontiguousarray(holes),
        regions=regions,
        nodes=nodes,
        first_number=first,
    )


# ----------------------------------------------------------------------- dispatch


def _starts_with(path: Path, magic: bytes) -> bool:
    with open(path, "rb") as f:
        return f.read(MAGIC_LEN) == magic


def read_node(path: str | Path) -> NodeTable:
    """Read a ``.node`` file in either encoding, sniffing the magic."""
    path = Path(path)
    return read_node_binary(path) if _starts_with(path, NODE_MAGIC) else read_node_text(path)


def read_ele(path: str | Path) -> Elements:
    """Read an ``.ele`` file in either encoding, sniffing the magic."""
    path = Path(path)
    return read_ele_binary(path) if _starts_with(path, ELE_MAGIC) else read_ele_text(path)


def read_poly(path: str | Path) -> Pslg:
    """Read a ``.poly`` file in either encoding, sniffing the magic."""
    path = Path(path)
    return read_poly_binary(path) if _starts_with(path, POLY_MAGIC) else read_poly_text(path)
