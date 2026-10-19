# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A DSF's pools and its triangle commands as the decoder reads them: planes raw, differenced or
run-length encoded, triangles listed one by one, or as strips and fans, as X-Plane's own files
write them."""

from __future__ import annotations

import struct

import numpy as np

TRI, STRIP, FAN = 23, 26, 29


def read_pool(payload: bytes) -> tuple[np.ndarray, bool]:
    """A ``POOL`` atom's table (n x planes, uint16), and whether its planes were all raw. Plane
    encodings: bit 0 differenced, bit 1 run-length (``0`` raw ... ``3`` both)."""
    n, planes = struct.unpack_from("<IB", payload, 0)
    pos = 5
    table = np.empty((n, planes), dtype=np.uint16)
    raw = True
    for j in range(planes):
        encoding = payload[pos]
        pos += 1
        raw = raw and encoding == 0
        if encoding & 2:
            out = np.empty(n, dtype=np.uint16)
            got = 0
            while got < n:
                run = payload[pos]
                pos += 1
                if run & 0x80:
                    (v,) = struct.unpack_from("<H", payload, pos)
                    pos += 2
                    out[got : got + (run & 0x7F)] = v
                    got += run & 0x7F
                else:
                    out[got : got + run] = np.frombuffer(
                        payload, dtype="<u2", count=run, offset=pos
                    )
                    pos += 2 * run
                    got += run
            col = out
        else:
            col = np.frombuffer(payload, dtype="<u2", count=n, offset=pos).copy()
            pos += 2 * n
        if encoding & 1:
            col = np.cumsum(col, dtype=np.uint16)
        table[:, j] = col
    return table, raw


def _triangles(kind: int, arrays: list[np.ndarray]) -> np.ndarray:
    """(n, 3) points of the triangles commands of one kind make, each in its winding (a strip's
    or fan's triangles counted from its own command's start)."""
    if kind == TRI:
        return np.concatenate(arrays).reshape(-1, 3)
    lengths = np.array([len(a) for a in arrays])
    counts = np.maximum(lengths - 2, 0)
    flat = np.concatenate(arrays)
    starts = np.repeat(np.cumsum(lengths) - lengths, counts)  # each triangle's command start
    k = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)  # its place in it
    at = starts + k
    if kind == STRIP:
        even = k % 2 == 0
        a = np.where(even, flat[at], flat[at + 1])
        b = np.where(even, flat[at + 1], flat[at])
        return np.stack([a, b, flat[at + 2]], axis=1)
    return np.stack([flat[starts], flat[at + 1], flat[at + 2]], axis=1)  # fan
