# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Quadtree partition of the tile into vertex pools, in Ortho4XP's dictionary order.

Port of ``QuadTree`` and ``float2qquad`` (``O4_DSF_Utils.py:20-107``) and of the pool
parameters (``:491-571``). Spec: ``docs/specs/dsf-encoding.md`` 3.1-3.2. Instead of inserting
nodes one by one, the split time of every bucket (the node index at which it receives its
``capacity + 1``-th node) is computed by order statistics on the nodes sorted by quadkey, and
the dict order is replayed from those times.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["QUANT_BITS", "PoolPartition", "partition", "quantize24"]

QUANT_BITS = 24
_QMAX = (1 << QUANT_BITS) - 1
INIT_LEVEL = 3
"""``quad_init_level`` (``O4_DSF_Utils.py:20``)."""


def quantize24(x: np.ndarray) -> np.ndarray:
    """``float2qquad`` (``:28-31``) as integers: ``int(2**24 * x)``, saturated at ``x >= 1``."""
    x = np.asarray(x, dtype=np.float64)
    q = (16777216 * x).astype(np.int64)  # truncation like int()
    q[x >= 1] = _QMAX
    if (q < 0).any():
        raise ValueError("node coordinates below the tile origin cannot be quantised")
    return q


def _morton(qx: np.ndarray, qy: np.ndarray) -> np.ndarray:
    """Interleave the 24 bits of ``qx`` (more significant) and ``qy`` into 48 bits."""

    def spread(v: np.ndarray) -> np.ndarray:
        v = v.astype(np.uint64)
        v = (v | (v << np.uint64(16))) & np.uint64(0x0000FFFF0000FFFF)
        v = (v | (v << np.uint64(8))) & np.uint64(0x00FF00FF00FF00FF)
        v = (v | (v << np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
        v = (v | (v << np.uint64(2))) & np.uint64(0x3333333333333333)
        v = (v | (v << np.uint64(1))) & np.uint64(0x5555555555555555)
        return v

    return (spread(qx) << np.uint64(1)) | spread(qy)


@dataclass(frozen=True)
class PoolPartition:
    """Buckets in DSF pool order and the quantised coordinates of every node."""

    node_bucket: np.ndarray
    """``(N,)`` int64: pool index of every node."""
    level: np.ndarray
    """``(P,)`` int64: quadtree depth of each pool (prefix length)."""
    key_x: np.ndarray
    """``(P,)`` int64: ``int(key[0], 2)``, the x prefix of the pool."""
    key_y: np.ndarray
    ix: np.ndarray
    """``(N,)`` uint16: the 16 bits of ``qx`` after the pool's prefix (``:524-531``)."""
    iy: np.ndarray

    @property
    def n_pools(self) -> int:
        return len(self.level)


def _split_times(
    order: np.ndarray, morton: np.ndarray, capacity: int, init_level: int
) -> list[tuple[int, int, int]]:
    """``(time, level, prefix)`` of every bucket that splits, found by descending the tree.

    ``order`` sorts the nodes by morton code; a bucket of ``level`` with morton ``prefix`` is
    the contiguous slice of ``order`` whose codes share ``2 * level`` leading bits. It splits
    when it holds more than ``capacity`` nodes (its parent split, so it exists), at the
    insertion of its ``capacity + 1``-th node in node order.
    """
    sorted_codes = morton[order]
    events: list[tuple[int, int, int]] = []
    stack: list[tuple[int, int, int, int]] = []  # (level, prefix, start, stop)
    shift = np.uint64(2 * (QUANT_BITS - init_level))
    top = sorted_codes >> shift
    for prefix in range(1 << (2 * init_level)):
        start, stop = np.searchsorted(top, [prefix, prefix + 1])
        stack.append((init_level, prefix, int(start), int(stop)))
    while stack:
        level, prefix, start, stop = stack.pop()
        if stop - start <= capacity or level >= QUANT_BITS:
            # At QUANT_BITS a bucket is one quantised position, which no split can divide: its
            # children would all hold the same nodes, and their shift, 2 * (24 - 25), is the
            # "Python integer -2 out of bounds for uint64" a mesh with 872 323 points on one spot
            # raised (+34-118, 2026-09-25). Such a bucket stays whole, and a pool that cannot
            # hold it is DSF_POOL_OVERFLOW, which says where.
            continue
        time = int(np.partition(order[start:stop], capacity)[capacity])
        events.append((time, level, prefix))
        child_level = level + 1
        child_shift = np.uint64(2 * (QUANT_BITS - child_level))
        codes = sorted_codes[start:stop] >> child_shift
        bounds = np.searchsorted(codes, [prefix * 4 + k for k in range(5)])
        for k in range(4):
            stack.append(
                (child_level, prefix * 4 + k, start + int(bounds[k]), start + int(bounds[k + 1]))
            )
    return events


def partition(
    qx: np.ndarray, qy: np.ndarray, *, capacity: int, init_level: int = INIT_LEVEL
) -> PoolPartition:
    """Buckets of the Ortho4XP quadtree, in its dict order, for nodes inserted in index order."""
    n = len(qx)
    morton = _morton(qx, qy)
    order = np.argsort(morton, kind="stable")
    events = _split_times(order, morton, capacity, init_level)
    events.sort(key=lambda e: (e[0], e[1]))  # same insertion: parent before child

    # Initial buckets in Ortho4XP order: ``for i in range(2**L): for j in range(2**L)`` (x-major).
    side = 1 << init_level
    keys: list[tuple[int, int]] = [
        (init_level, _interleave(i, j, init_level)) for i in range(side) for j in range(side)
    ]
    position = {k: i for i, k in enumerate(keys)}
    alive = [True] * len(keys)
    for _, level, prefix in events:
        alive[position[(level, prefix)]] = False
        for k in range(4):
            child = (level + 1, prefix * 4 + k)
            position[child] = len(keys)
            keys.append(child)
            alive.append(True)

    # Node -> bucket: the deepest alive key that is a prefix of the node's morton code.
    node_bucket = np.full(n, -1, dtype=np.int64)
    levels: list[int] = []
    key_x: list[int] = []
    key_y: list[int] = []
    pool = 0
    sorted_codes = morton[order]
    for (level, prefix), ok in zip(keys, alive, strict=True):
        if not ok:
            continue
        shift = np.uint64(2 * (QUANT_BITS - level))
        start, stop = np.searchsorted(sorted_codes >> shift, [prefix, prefix + 1])
        if stop == start:
            continue  # clean(): empty buckets are dropped
        node_bucket[order[start:stop]] = pool
        levels.append(level)
        key_x.append(_deinterleave(prefix, level, 1))
        key_y.append(_deinterleave(prefix, level, 0))
        pool += 1
    level_arr = np.asarray(levels, dtype=np.int64)
    node_level = level_arr[node_bucket]
    ix = _after_prefix(qx, node_level)
    iy = _after_prefix(qy, node_level)
    return PoolPartition(
        node_bucket=node_bucket,
        level=level_arr,
        key_x=np.asarray(key_x, dtype=np.int64),
        key_y=np.asarray(key_y, dtype=np.int64),
        ix=ix,
        iy=iy,
    )


def _interleave(x: int, y: int, level: int) -> int:
    """Morton prefix of ``level`` bits of ``x`` (more significant) and ``y``."""
    out = 0
    for i in range(level):
        out |= ((x >> i) & 1) << (2 * i + 1)
        out |= ((y >> i) & 1) << (2 * i)
    return out


def _deinterleave(prefix: int, level: int, which: int) -> int:
    """Bits of one axis (1 = x, more significant; 0 = y) of a ``2 * level``-bit morton prefix."""
    out = 0
    for i in range(level):
        out |= ((prefix >> (2 * i + which)) & 1) << i
    return out


def _after_prefix(q: np.ndarray, level: np.ndarray) -> np.ndarray:
    """``int(bits[level:level + 16], 2)`` of the 24-bit strings (``:524-531``)."""
    shift = np.maximum(QUANT_BITS - 16 - level, 0)
    width = np.minimum(16, QUANT_BITS - level)
    mask = (np.int64(1) << width) - 1
    return ((q >> shift) & mask).astype(np.uint16)
