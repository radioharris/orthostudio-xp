# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Text ``.node`` / ``.poly`` files as Triangle4XP reads them and Ortho4XP writes them.

Layout (O4_Vector_Utils.py:537-615):

``.node``: header ``N 2 1 0`` (N vertices, 2 dimensions, 1 attribute = vector altitude z, no
boundary marker), then ``i x y z`` with 1-based ``i`` and 9 decimals.

``.poly``: ``0 2 1 0`` (vertices live in the ``.node`` file), blank line, ``M 1`` (M segments,
1 boundary marker), ``i n0 n1 marker``, blank line, ``H`` holes then ``i x y``, blank line, ``S``
region seeds then ``i x y marker`` with 15 decimals (Triangle4XP reads four fields per seed:
x, y, attribute, area constraint; Ortho4XP writes the marker as the attribute and nothing as the
area, Triangle4XP.c:14939-15030). Seeds are written sorted by marker value.

The binary variants of ADR 0004 are not here (``orthostudio.mesh.triangle_io``): the vector stage
publishes these text files, the triangulation reads the binary ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from orthostudio.numtext import parse_numbers
from orthostudio.vectors.noding import MARKERS


class TriangleNodes(NamedTuple):
    """Content of a Triangle ``.node`` file (indices normalised to 0-based)."""

    xy: NDArray[np.float64]
    """(N, 2) coordinates."""
    attributes: NDArray[np.float64]
    """(N, k) vertex attributes; k = 1 (z) for an Ortho4XP input file."""
    markers: NDArray[np.int64]
    """(N,) boundary markers, zeros when the file has none."""
    first_index: int
    """Index of the first vertex in the file (0 or 1); segments refer to it."""


class TrianglePoly(NamedTuple):
    """Content of a Triangle ``.poly`` file, segment indices 0-based into the ``.node`` file."""

    segments: NDArray[np.int32]
    """(M, 2) vertex indices."""
    markers: NDArray[np.uint8]
    """(M,) segment markers (0 when the file has none)."""
    holes: NDArray[np.float64]
    """(H, 2) hole points."""
    seeds: NDArray[np.float64]
    """(S, 3) region seeds: x, y, marker (Triangle4XP regional attribute)."""


def _tokens(path: Path) -> list[str]:
    """Non-empty, non-comment lines of a Triangle text file."""
    out = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _block(lines: list[str], start: int, count: int, columns: int) -> NDArray[np.float64]:
    """Parse ``count`` lines of ``columns`` numbers each starting at ``start``."""
    if count == 0:
        return np.zeros((0, columns))
    text = "\n".join(lines[start : start + count])
    values = parse_numbers(text, np.float64)
    if values.size != count * columns:
        raise ValueError(f"expected {count} lines of {columns} fields, got {values.size} values")
    return values.reshape(count, columns)


def read_node_file(path: str | Path) -> TriangleNodes:
    """Read a Triangle ``.node`` file (input or ``.1.node`` output)."""
    lines = _tokens(Path(path))
    n, dim, n_attr, n_mark = (int(v) for v in lines[0].split()[:4])
    if dim != 2:
        raise ValueError(f"only 2-D node files are supported, got dimension {dim}")
    cols = 1 + 2 + n_attr + n_mark
    block = _block(lines, 1, n, cols)
    first_index = int(block[0, 0]) if n else 1
    markers = block[:, 3 + n_attr].astype(np.int64) if n_mark else np.zeros(n, dtype=np.int64)
    return TriangleNodes(block[:, 1:3], block[:, 3 : 3 + n_attr], markers, first_index)


def read_poly_file(path: str | Path, first_index: int | None = None) -> TrianglePoly:
    """Read a Triangle ``.poly`` file whose vertices live in a separate ``.node`` file."""
    lines = _tokens(Path(path))
    n_vert = int(lines[0].split()[0])
    pos = 1
    if n_vert:  # vertices inside the .poly (not what Ortho4XP writes, but legal)
        pos += n_vert
    m, seg_mark = (int(v) for v in lines[pos].split()[:2])
    pos += 1
    seg = _block(lines, pos, m, 3 + seg_mark)
    pos += m
    if first_index is None:
        first_index = int(seg[:, 1].min()) if m else 1
    segments = (seg[:, 1:3] - first_index).astype(np.int32)
    markers = seg[:, 3].astype(np.uint8) if seg_mark else np.zeros(m, dtype=np.uint8)
    holes_n = int(lines[pos].split()[0])
    pos += 1
    holes = _block(lines, pos, holes_n, 3)[:, 1:3]
    pos += holes_n
    seeds = np.zeros((0, 3))
    if pos < len(lines):
        seeds_n = int(lines[pos].split()[0])
        pos += 1
        if seeds_n:
            # Ortho4XP writes "i x y marker"; standard Triangle allows a 5th field (max area).
            rows = [ln.split() for ln in lines[pos : pos + seeds_n]]
            seeds = np.array([[float(r[1]), float(r[2]), float(r[3])] for r in rows])
    return TrianglePoly(segments, markers, holes, seeds)


def write_node_file(path: str | Path, nodes: NDArray[np.float64]) -> None:
    """Write nodes (N, 3) as Ortho4XP does: 1-based, 9 decimals, one attribute (z)."""
    nodes = np.asarray(nodes, dtype=np.float64)
    idx = np.arange(1, len(nodes) + 1)
    with Path(path).open("w") as f:
        f.write(f"{len(nodes)} 2 1 0\n")
        np.savetxt(f, np.column_stack([idx, nodes]), fmt=["%d", "%.9f", "%.9f", "%.9f"])


def write_poly_file(
    path: str | Path,
    edges: NDArray[np.integer],
    markers: NDArray[np.integer],
    seeds: dict[str | int, NDArray[np.float64]] | None = None,
    holes: NDArray[np.float64] | None = None,
) -> None:
    """Write a ``.poly`` in the Ortho4XP layout; ``edges`` are 0-based, written 1-based.

    ``seeds`` maps a marker (name or value) to an (S, 2) array of seed points; they are written
    sorted by marker value as Ortho4XP does (O4_Vector_Utils.py:596-615).
    """
    edges = np.asarray(edges)
    markers = np.asarray(markers)
    with Path(path).open("w") as f:
        f.write("0 2 1 0\n\n")
        f.write(f"{len(edges)} 1\n")
        idx = np.arange(1, len(edges) + 1)
        np.savetxt(f, np.column_stack([idx, edges + 1, markers]), fmt="%d")
        holes = np.zeros((0, 2)) if holes is None else np.asarray(holes)
        f.write(f"\n{len(holes)}\n")
        for k, hole in enumerate(holes, 1):
            f.write(f"{k} {hole[0]:.15f} {hole[1]:.15f}\n")
        rows: list[tuple[int, float, float]] = []
        for key, pts in (seeds or {}).items():
            marker = MARKERS[key] if isinstance(key, str) else int(key)
            rows.extend((marker, float(p[0]), float(p[1])) for p in np.asarray(pts))
        rows.sort(key=lambda r: r[0])
        f.write(f"\n{len(rows)}\n")
        for k, (marker, x, y) in enumerate(rows, 1):
            f.write(f"{k} {x:.15f} {y:.15f} {marker}\n")
