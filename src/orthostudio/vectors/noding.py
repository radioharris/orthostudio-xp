"""Noding of prioritised vector layers into a planar straight-line graph.

Successor of ``Vector_Map.insert_edge`` in Ortho4XP (O4_Vector_Utils.py:117-226). The
rules it reproduces are written down in ``docs/specs/vectors-pslg.md``; in short:

* two edges of the result either do not meet or meet at a common end node (planar graph);
* the z of a crossing node is interpolated on the edge that was there first (higher priority
  layer, or earlier segment inside a layer), never on the edge being inserted;
* a node that is a vertex of an input way keeps the z of its first insertion;
* coincident edges carry the bitwise OR of their markers;
* node identity is the coordinate rounded to 9 decimals (``snap_to_grid(9)``);
* the crossing point is ``(1 - alpha) * a + alpha * b`` on the edge being inserted, with
  ``alpha`` from the same 2x2 solve Ortho4XP runs, down to the last bit (:func:`_solve_2x2`).

Everything is vectorised with numpy and a shapely 2 ``STRtree``: no Python loop runs per edge.
Layers are processed in order, each one against the graph accumulated so far, which is what
gives the "pre-existing edge keeps its z" rule for free.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely import STRtree
from shapely.geometry.base import BaseGeometry

from orthostudio.errors import OsxpError

# Edge attributes, powers of two so that regions may carry several of them
# (O4_Vector_Utils.py:44-54).
MARKERS: dict[str, int] = {
    "DUMMY": 0,
    "WATER": 1,
    "SEA": 2,
    "SEA_EQUIV": 4,
    "INTERP_ALT": 8,
    "RUNWAY": 16,
    "TAXIWAY": 32,
    "APRON": 64,
    "HANGAR": 128,
}

#: Decimals kept for node identity and output (O4_Vector_Map.py:163 ``snap_to_grid(9)``).
SNAP_DIGITS = 9
#: Relative window around segment ends inside which a contact is not a crossing
#: (O4_Vector_Utils.py:275 ``eps = 1e-8``).
ENCROACH_EPS = 1e-8
#: Slack on the [0, 1] parameter test, to absorb the last bits of the 2x2 solve.
PARAM_TOL = 1e-12
#: Bounding boxes are widened by this much before the candidate search, so that two segments
#: touching at a node whose coordinate carries 1e-18 of noise are still tested (Ortho4XP misses
#: them: its rtree boxes are exact).
BBOX_TOL = 1e-9

Layer = tuple[BaseGeometry, int, "NDArray[np.floating] | None"]
"""(linework, marker bits, z per coordinate in ``shapely.get_coordinates`` order or None)."""


class NodedGraph(NamedTuple):
    """A planar straight-line graph: nodes with z, undirected edges, edge markers."""

    nodes: NDArray[np.float64]
    """(N, 3) float64: x, y, z in tile-local coordinates, rounded to ``SNAP_DIGITS``."""
    edges: NDArray[np.int32]
    """(M, 2) int32 node indices; each unordered pair appears once."""
    markers: NDArray[np.uint8]
    """(M,) uint8 bitwise OR of the markers of every input segment covering the edge."""


class _Segments(NamedTuple):
    """Segments of one layer, in insertion order, with the z of both ends."""

    a: NDArray[np.float64]  # (K, 2)
    b: NDArray[np.float64]  # (K, 2)
    za: NDArray[np.float64]  # (K,)
    zb: NDArray[np.float64]  # (K,)
    marker: int
    time_a: NDArray[np.float64]  # insertion time of vertex a (see _insert)
    time_b: NDArray[np.float64]
    time_seg: NDArray[np.float64]  # insertion time of the segment itself


def node_layers(layers: Sequence[Layer], *, cancel: threading.Event | None = None) -> NodedGraph:
    """Node the layers, in order of decreasing priority, into one planar graph.

    ``layers`` is a sequence of ``(geometry, marker, z)``: ``geometry`` is any linework
    (LineString, MultiLineString, LinearRing, Polygon or MultiPolygon rings, collections of
    those); ``marker`` is the attribute bits of every segment of the layer; ``z`` is one value
    per coordinate of ``shapely.get_coordinates(geometry)`` or None (then the Z of 3D
    coordinates, or 0).

    ``cancel`` is read before every pass and raises ``SYS_CANCELLED`` (review 6: a tile dense
    in aerodromes runs hundreds of passes, and the token used to be read only around the
    whole noding).
    """
    graph: _Graph | None = None
    for geometry, marker, z in layers:
        if cancel is not None and cancel.is_set():
            raise OsxpError("SYS_CANCELLED", context={"stage": "vectors noding"})
        segments = _layer_segments(geometry, int(marker), z)
        graph = _insert(graph, segments)
    if graph is None:
        return NodedGraph(
            np.zeros((0, 3), dtype=np.float64),
            np.zeros((0, 2), dtype=np.int32),
            np.zeros(0, dtype=np.uint8),
        )
    nodes = graph.nodes.copy()
    nodes[:, :2] = np.round(nodes[:, :2], SNAP_DIGITS)
    return NodedGraph(nodes, graph.edges, graph.markers)


class _Graph(NamedTuple):
    """Internal state: nodes keep their un-rounded first-seen coordinates.

    Beside the graph itself it carries the index the incremental insertion needs
    (:func:`_insert`): the node keys sorted, the node of each sorted key, and the nodes the
    last pass created without any edge (they are dropped at the start of the next pass,
    which is what re-deriving the whole graph from its edges used to do).
    """

    nodes: NDArray[np.float64]  # (N, 3)
    edges: NDArray[np.int32]  # (M, 2)
    markers: NDArray[np.uint8]  # (M,)
    keys: NDArray[np.int64]  # (N,) node keys, sorted
    key_nodes: NDArray[np.int64]  # (N,) node index of each sorted key
    isolated: NDArray[np.int64]  # node indices referenced by no edge


def linework(geometry: BaseGeometry) -> NDArray[np.object_]:
    """Flatten any geometry into an array of LineString / LinearRing parts.

    Public contract (review 5: the assembler imported this under its private name to write
    ``layers.npz``): the parts come out in the order :func:`node_layers` inserts them -- lines
    first, then the rings of the polygons, exterior before holes, then the parts of the nested
    collections -- which is also the order ``shapely.get_coordinates`` walks, so a ``z`` array
    lines up with it.
    """
    parts = shapely.get_parts(geometry)
    if len(parts) == 0:
        return parts
    types = shapely.get_type_id(parts)
    out: list[NDArray[np.object_]] = []
    gt = shapely.GeometryType
    is_line = (types == gt.LINESTRING) | (types == gt.LINEARRING)
    lines = parts[is_line]
    if len(lines):
        out.append(lines)
    polys = parts[types == shapely.GeometryType.POLYGON]
    if len(polys):
        out.append(shapely.get_rings(polys))
    nested = parts[
        (types == shapely.GeometryType.GEOMETRYCOLLECTION)
        | (types == shapely.GeometryType.MULTILINESTRING)
        | (types == shapely.GeometryType.MULTIPOLYGON)
    ]
    for geom in nested:
        out.append(linework(geom))
    return np.concatenate(out) if out else np.zeros(0, dtype=object)


_linework = linework
"""Historical private name, kept so P0 code and benchmarks keep working."""


def _layer_segments(
    geometry: BaseGeometry, marker: int, z: NDArray[np.floating] | None
) -> _Segments:
    """Explode a layer into segments; zero-length segments are dropped."""
    if not 0 <= marker <= 255:
        raise ValueError(f"marker must fit in uint8, got {marker}")
    lines = linework(geometry)
    if len(lines) == 0:
        empty = np.zeros((0, 2))
        zero = np.zeros(0)
        return _Segments(empty, empty, zero, zero, marker, zero, zero, zero)
    coords, part = shapely.get_coordinates(lines, include_z=True, return_index=True)
    xy = coords[:, :2]
    if z is None:
        zc = np.nan_to_num(coords[:, 2], nan=0.0)
    else:
        zc = np.asarray(z, dtype=np.float64)
        if zc.shape != (len(xy),):
            raise ValueError(f"z has {zc.shape} values, geometry has {len(xy)} coordinates")
    keep = (part[:-1] == part[1:]) & np.any(xy[:-1] != xy[1:], axis=1)
    idx = np.flatnonzero(keep)
    # Ortho4XP inserts vertex m, then vertex m+1, then the edge (m, m+1): times 2m, 2m+2, 2m+3.
    m = idx.astype(np.float64)
    return _Segments(
        xy[idx], xy[idx + 1], zc[idx], zc[idx + 1], marker, 2 * m, 2 * m + 2, 2 * m + 3
    )


def _cross(u: NDArray[np.float64], v: NDArray[np.float64]) -> NDArray[np.float64]:
    return u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]


def _boxes(a: NDArray[np.float64], b: NDArray[np.float64]) -> NDArray[np.object_]:
    lo = np.minimum(a, b) - BBOX_TOL
    hi = np.maximum(a, b) + BBOX_TOL
    return shapely.box(lo[:, 0], lo[:, 1], hi[:, 0], hi[:, 1])


def _bbox_pairs(
    a_old: NDArray[np.float64],
    b_old: NDArray[np.float64],
    a_new: NDArray[np.float64],
    b_new: NDArray[np.float64],
    self_pairs: bool,
) -> tuple[NDArray[np.intp], NDArray[np.intp]]:
    """Index pairs (old, new) whose (widened) bounding boxes overlap.

    The STRtree is built on the smaller set and queried with the larger one; the candidate set
    is the one Ortho4XP tests with its rtree (O4_Vector_Utils.py:127-129).
    """
    if len(a_old) == 0 or len(a_new) == 0:
        return np.zeros(0, dtype=np.intp), np.zeros(0, dtype=np.intp)
    if self_pairs or len(a_old) <= len(a_new):
        tree = STRtree(_boxes(a_old, b_old))
        query, hit = tree.query(_boxes(a_new, b_new))
        old, new = hit, query
    else:
        tree = STRtree(_boxes(a_new, b_new))
        query, hit = tree.query(_boxes(a_old, b_old))
        old, new = query, hit
    if self_pairs:
        keep = old < new  # each unordered pair once, older (lower index) first
        return old[keep], new[keep]
    return old, new


def _bbox_prefilter(
    a_old: NDArray[np.float64],
    b_old: NDArray[np.float64],
    a_new: NDArray[np.float64],
    b_new: NDArray[np.float64],
) -> NDArray[np.intp]:
    """Indices of old segments whose bbox meets the overall bbox of the new layer."""
    if len(a_new) == 0 or len(a_old) == 0:
        return np.zeros(0, dtype=np.intp)
    lo = np.minimum(a_new.min(axis=0), b_new.min(axis=0)) - 2 * BBOX_TOL
    hi = np.maximum(a_new.max(axis=0), b_new.max(axis=0)) + 2 * BBOX_TOL
    old_lo = np.minimum(a_old, b_old)
    old_hi = np.maximum(a_old, b_old)
    keep = np.all(old_hi >= lo, axis=1) & np.all(old_lo <= hi, axis=1)
    return np.flatnonzero(keep)


def _edge_prefilter(
    nodes: NDArray[np.float64],
    edges: NDArray[np.int32],
    a_new: NDArray[np.float64],
    b_new: NDArray[np.float64],
) -> NDArray[np.intp]:
    """:func:`_bbox_prefilter` on the edges of a graph, without gathering both ends in 2D.

    Same comparisons, hence the same indices: the x test runs on every edge, the y test only
    on the edges the x test kept (on a tile dense in small aerodrome passes this is most of
    the per-pass cost left, review 6).
    """
    lo = np.minimum(a_new.min(axis=0), b_new.min(axis=0)) - 2 * BBOX_TOL
    hi = np.maximum(a_new.max(axis=0), b_new.max(axis=0)) + 2 * BBOX_TOL
    xa, xb = nodes[edges[:, 0], 0], nodes[edges[:, 1], 0]
    keep = np.flatnonzero((np.maximum(xa, xb) >= lo[0]) & (np.minimum(xa, xb) <= hi[0]))
    ya, yb = nodes[edges[keep, 0], 1], nodes[edges[keep, 1], 1]
    return keep[(np.maximum(ya, yb) >= lo[1]) & (np.minimum(ya, yb) <= hi[1])]


def _solve_2x2(
    ab: NDArray[np.float64], dc: NDArray[np.float64], ac: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Solve ``alpha * ab + beta * dc = ac`` for every row, the way Ortho4XP solves it.

    ``are_encroached`` (O4_Vector_Utils.py:280-281) builds ``A = column_stack((ab, dc))`` and
    calls ``numpy.linalg.solve(A, ac)``; ``alpha`` is the parameter on the edge being inserted
    and ``beta`` the one on the pre-existing edge. The crossing coordinate is then
    ``(1 - alpha) * a + alpha * b`` (lines 149-156), so the **last bits** of ``alpha`` decide
    which side of a 9-decimal tie the node lands on -- and the orthophoto grid abscissae are
    exactly such ties (`docs/specs/vectors-pslg.md` 2.8). A mathematically equivalent formula
    (Cramer) puts some of those crossings on the other side and merges them with the border
    vertex they sit on, which is the whole of the ten-node residue of wave 1.

    So this is not "a" 2x2 solve but LAPACK's, written out: LU with partial pivoting, every
    division done as a multiplication by the reciprocal (what ``dgetf2``/``dtrsm`` do). It is
    plain IEEE-754 double arithmetic, hence deterministic on any machine, and it reproduces
    ``numpy.linalg.solve`` bit for bit (``tests/test_nodes10_solve.py``).

    The caller only passes the pairs its parallel test kept, so the pivots are non-zero; the
    ``errstate`` around the call is a safety net, not a code path.
    """
    a00, a10 = ab[:, 0], ab[:, 1]
    a01, a11 = dc[:, 0], dc[:, 1]
    swap = np.abs(a10) > np.abs(a00)  # idamax: strictly greater, first row wins a tie
    p00 = np.where(swap, a10, a00)
    p01 = np.where(swap, a11, a01)
    p10 = np.where(swap, a00, a10)
    p11 = np.where(swap, a01, a11)
    r0 = np.where(swap, ac[:, 1], ac[:, 0])
    r1 = np.where(swap, ac[:, 0], ac[:, 1])
    lower = p10 * (1.0 / p00)  # dgetf2 scales by the reciprocal of the pivot
    beta = (r1 - lower * r0) * (1.0 / (p11 - lower * p01))
    alpha = (r0 - p01 * beta) * (1.0 / p00)
    return alpha, beta


def _pair_events(
    a: NDArray[np.float64],
    b: NDArray[np.float64],
    za: NDArray[np.float64],
    zb: NDArray[np.float64],
    time_seg: NDArray[np.float64],
    i: NDArray[np.intp],
    j: NDArray[np.intp],
) -> tuple[NDArray, NDArray, NDArray, NDArray, NDArray]:
    """Split events from candidate pairs (i older than j).

    Returns (segment, t, xyz, time) columns concatenated for both members of each pair, plus a
    flag telling whether the event creates a node (crossing) or reuses a vertex (overlap).
    """
    pa, pb, pc, pd = a[i], b[i], a[j], b[j]
    r = pb - pa
    s = pd - pc
    qp = pc - pa
    nr = np.hypot(r[:, 0], r[:, 1])
    ns = np.hypot(s[:, 0], s[:, 1])
    den = _cross(r, s)
    eps = ENCROACH_EPS
    parallel = np.abs(den) <= eps * nr * ns

    # Segments sharing an end point exactly (consecutive edges of a way, closing of a ring)
    # never cross: Ortho4XP shortcuts them too (O4_Vector_Utils.py:269-274), except when they fold
    # back on each other, which the collinear branch below handles.
    shared = (
        np.all(pa == pc, axis=1)
        | np.all(pa == pd, axis=1)
        | np.all(pb == pc, axis=1)
        | np.all(pb == pd, axis=1)
    )

    # --- transverse pairs (O4_Vector_Utils.py:277-287) ---
    tr = np.flatnonzero(~parallel & ~shared)
    with np.errstate(divide="ignore", invalid="ignore"):
        # Ortho4XP solves alpha * (b - a) + beta * (c - d) = c - a with a, b the edge being
        # inserted (j) and c, d the pre-existing one (i): alpha is our u, beta our t.
        u, t = _solve_2x2(s[tr], -r[tr], -qp[tr])
    inside = (t >= -PARAM_TOL) & (t <= 1 + PARAM_TOL) & (u >= -PARAM_TOL) & (u <= 1 + PARAM_TOL)
    strict = ((t > eps) & (t < 1 - eps)) | ((u > eps) & (u < 1 - eps))
    tr_keep = inside & strict
    tr, t, u = tr[tr_keep], np.clip(t[tr_keep], 0, 1), np.clip(u[tr_keep], 0, 1)
    ii, jj = i[tr], j[tr]
    # Ortho4XP computes the point on the edge being inserted (the newer one) and the z on the
    # pre-existing one (O4_Vector_Utils.py:149-158).
    px = (1 - u)[:, None] * a[jj] + u[:, None] * b[jj]
    pz = (1 - t) * za[ii] + t * zb[ii]
    p_time = time_seg[jj]
    xyz = np.column_stack([px, pz])
    on_i = (t > 0) & (t < 1)
    on_j = (u > 0) & (u < 1)
    ev_seg = [ii[on_i], jj[on_j]]
    ev_t = [t[on_i], u[on_j]]
    ev_xyz = [xyz[on_i], xyz[on_j]]
    ev_time = [p_time[on_i], p_time[on_j]]
    ev_new = [np.ones(on_i.sum(), dtype=bool), np.ones(on_j.sum(), dtype=bool)]

    # --- collinear overlaps (O4_Vector_Utils.py:288-306): split at existing vertices only ---
    pl = np.flatnonzero(parallel)
    nqp = np.hypot(qp[pl, 0], qp[pl, 1])
    collinear = np.abs(_cross(r[pl], qp[pl])) <= eps * nr[pl] * nqp
    pl = pl[collinear]
    if len(pl):
        rr, ss = r[pl], s[pl]
        rr2 = np.einsum("ij,ij->i", rr, rr)
        ss2 = np.einsum("ij,ij->i", ss, ss)
        t_c = np.einsum("ij,ij->i", a[j[pl]] - a[i[pl]], rr) / rr2
        t_d = np.einsum("ij,ij->i", b[j[pl]] - a[i[pl]], rr) / rr2
        u_a = np.einsum("ij,ij->i", a[i[pl]] - a[j[pl]], ss) / ss2
        u_b = np.einsum("ij,ij->i", b[i[pl]] - a[j[pl]], ss) / ss2
        overlap = (np.maximum(t_c, t_d) > eps) & (np.minimum(t_c, t_d) < 1 - eps)
        pl, t_c, t_d, u_a, u_b = pl[overlap], t_c[overlap], t_d[overlap], u_a[overlap], u_b[overlap]
        far = np.full(len(pl), np.inf)
        for seg, tt, pt, pz2 in (
            (i[pl], t_c, a[j[pl]], za[j[pl]]),
            (i[pl], t_d, b[j[pl]], zb[j[pl]]),
            (j[pl], u_a, a[i[pl]], za[i[pl]]),
            (j[pl], u_b, b[i[pl]], zb[i[pl]]),
        ):
            k = (tt > 0) & (tt < 1)
            ev_seg.append(seg[k])
            ev_t.append(tt[k])
            ev_xyz.append(np.column_stack([pt[k], pz2[k]]))
            ev_time.append(far[k])  # never decides a z: the vertex itself does
            ev_new.append(np.zeros(k.sum(), dtype=bool))

    return (
        np.concatenate(ev_seg),
        np.concatenate(ev_t),
        np.concatenate(ev_xyz),
        np.concatenate(ev_time),
        np.concatenate(ev_new),
    )


_KEY_OFFSET = 1 << 31


def _node_keys(xy: NDArray[np.float64]) -> NDArray[np.int64]:
    """Identity of a coordinate: its rounding to SNAP_DIGITS, packed in one int64.

    ``np.round(x, 9)`` is ``rint(x * 1e9) / 1e9``, so the packed integers are exactly the
    rounded coordinates; they must stay below 2.1 in absolute value (tile-local units).
    """
    scaled = np.rint(xy * 10.0**SNAP_DIGITS)
    if np.any(np.abs(scaled) >= _KEY_OFFSET):
        raise ValueError("coordinates must be tile-local (|x|, |y| < 2)")
    packed = scaled.astype(np.int64) + _KEY_OFFSET
    return (packed[:, 0] << 32) | packed[:, 1]


def _insert_full(graph: _Graph | None, new: _Segments) -> _Graph:
    """Insert one layer by re-deriving the **whole** graph: the reference semantics.

    Every existing edge re-enters the segment table, is sorted again and de-duplicated again,
    so one pass costs ``O(M log M)`` in the size of the graph. That is the definition the
    incremental :func:`_insert` reproduces bit for bit (``tests/test_p4v2fix_noding.py``)
    and the path it falls back to on the one configuration it does not handle locally.
    """
    if graph is None:
        old_a = old_b = np.zeros((0, 2))
        old_za = old_zb = np.zeros(0)
        old_marker = np.zeros(0, dtype=np.uint8)
        old_ids = np.zeros((0, 2), dtype=np.int64)
    else:
        old_a = graph.nodes[graph.edges[:, 0], :2]
        old_b = graph.nodes[graph.edges[:, 1], :2]
        old_za = graph.nodes[graph.edges[:, 0], 2]
        old_zb = graph.nodes[graph.edges[:, 1], 2]
        old_marker = graph.markers
        old_ids = graph.edges.astype(np.int64)
    n_old = len(old_a)
    n_new = len(new.a)

    # Combined segment table: existing edges first (time -inf side), then the new layer.
    a = np.concatenate([old_a, new.a])
    b = np.concatenate([old_b, new.b])
    za = np.concatenate([old_za, new.za])
    zb = np.concatenate([old_zb, new.zb])
    marker = np.concatenate([old_marker, np.full(n_new, new.marker, dtype=np.uint8)])
    # Existing nodes were inserted before anything in this layer: give them times below every
    # new time, ordered by their current index so the output order stays stable.
    old_time = old_ids.astype(np.float64) - 1e15
    time_a = np.concatenate([old_time[:, 0], new.time_a])
    time_b = np.concatenate([old_time[:, 1], new.time_b])
    time_seg = np.concatenate([np.full(n_old, -np.inf), new.time_seg])

    # Candidate pairs: existing x new, and new x new (older index first).
    cand = _bbox_prefilter(old_a, old_b, new.a, new.b)
    i1, j1 = _bbox_pairs(old_a[cand], old_b[cand], new.a, new.b, self_pairs=False)
    i1 = cand[i1]
    i2, j2 = _bbox_pairs(new.a, new.b, new.a, new.b, self_pairs=True)
    i = np.concatenate([i1, i2 + n_old])
    j = np.concatenate([j1 + n_old, j2 + n_old])
    ev_seg, ev_t, ev_xyz, ev_time, _ = _pair_events(a, b, za, zb, time_seg, i, j)

    # Chains: every segment carries its two ends plus its split events, sorted by parameter.
    n_seg = n_old + n_new
    seg_all = np.concatenate([np.arange(n_seg), np.arange(n_seg), ev_seg])
    t_all = np.concatenate([np.zeros(n_seg), np.ones(n_seg), ev_t])
    xyz_all = np.concatenate([np.column_stack([a, za]), np.column_stack([b, zb]), ev_xyz])
    time_all = np.concatenate([time_a, time_b, ev_time])
    order = np.lexsort((t_all, seg_all))
    seg_all, xyz_all, time_all = seg_all[order], xyz_all[order], time_all[order]

    # Node identity by snapped coordinate; z and stored coordinate from the earliest event.
    keys = _node_keys(xyz_all[:, :2])
    by_time = np.argsort(time_all, kind="stable")
    _, first, inverse = np.unique(keys[by_time], return_index=True, return_inverse=True)
    creation = np.argsort(first, kind="stable")  # unique index -> rank by creation time
    rank_of_unique = np.empty(len(first), dtype=np.int64)
    rank_of_unique[creation] = np.arange(len(first))
    node_of_event = np.empty(len(keys), dtype=np.int64)
    node_of_event[by_time] = rank_of_unique[inverse]
    nodes = xyz_all[by_time][first][creation]

    # Sub-edges between consecutive events of the same segment, zero-length ones dropped.
    same = seg_all[:-1] == seg_all[1:]
    n0, n1 = node_of_event[:-1][same], node_of_event[1:][same]
    seg_of_edge = seg_all[:-1][same]
    nz = n0 != n1
    n0, n1, seg_of_edge = n0[nz], n1[nz], seg_of_edge[nz]
    lo, hi = np.minimum(n0, n1), np.maximum(n0, n1)
    edge_keys = lo * len(nodes) + hi
    uniq, first_edge, inv = np.unique(edge_keys, return_index=True, return_inverse=True)
    edge_order = np.argsort(first_edge, kind="stable")
    rank_of_edge = np.empty(len(uniq), dtype=np.int64)
    rank_of_edge[edge_order] = np.arange(len(uniq))
    markers = np.zeros(len(uniq), dtype=np.uint8)
    np.bitwise_or.at(markers, rank_of_edge[inv], marker[seg_of_edge])
    edges = np.column_stack([lo[first_edge], hi[first_edge]])[edge_order].astype(np.int32)
    return _indexed(nodes, edges, markers)


def _edge_keys(lo: NDArray[np.integer], hi: NDArray[np.integer]) -> NDArray[np.int64]:
    """One int64 per undirected edge ``lo < hi`` (node indices stay below 2**31)."""
    return (lo.astype(np.int64) << 32) | hi.astype(np.int64)


def _indexed(
    nodes: NDArray[np.float64], edges: NDArray[np.int32], markers: NDArray[np.uint8]
) -> _Graph:
    """Attach the key index and the isolated nodes to a graph built from scratch."""
    keys = _node_keys(nodes[:, :2]) if len(nodes) else np.zeros(0, dtype=np.int64)
    order = np.argsort(keys, kind="stable")
    used = np.zeros(len(nodes), dtype=bool)
    used[edges.ravel()] = True
    return _Graph(nodes, edges, markers, keys[order], order.astype(np.int64), np.flatnonzero(~used))


def _drop_isolated(graph: _Graph) -> _Graph:
    """Remove the nodes no edge references, renumbering the others in order.

    Re-deriving the graph from its edges (:func:`_insert_full`) forgets such a node -- one
    whose only segment was shorter than the 9-decimal key -- at the next pass; the
    incremental insertion does the same, explicitly.
    """
    if not len(graph.isolated):
        return graph
    keep = np.ones(len(graph.nodes), dtype=bool)
    keep[graph.isolated] = False
    remap = np.cumsum(keep, dtype=np.int64) - 1
    in_index = keep[graph.key_nodes]
    return _Graph(
        graph.nodes[keep],
        remap[graph.edges].astype(np.int32),
        graph.markers,
        graph.keys[in_index],
        remap[graph.key_nodes[in_index]],
        np.zeros(0, dtype=np.int64),
    )


def _insert(graph: _Graph | None, new: _Segments) -> _Graph:
    """Insert one layer of segments into the graph, resolving every intersection.

    Same result as :func:`_insert_full`, to the bit and in the same order, but the work is
    proportional to the layer and to the existing edges its bounding box meets, not to the
    whole graph (review 6: every aerodrome adds about five passes, and re-sorting the whole
    graph at each one made a tile of 324 aerodromes spend 53 s in the noding).

    Why the local computation is the global one:

    * an existing edge outside the prefilter (:func:`_bbox_prefilter`, the very candidates
      the full insertion tests) has no event: its chain is its two end nodes and it comes out
      unchanged, at its place;
    * existing nodes keep their index and their coordinate (their insertion time precedes
      every new one), so an event is matched against **all** of them through the sorted key
      index, and a new node is numbered after them by its first event, exactly as the full
      ranking does -- the local sequence is a subsequence of the full one, in the same order;
    * the edges of the candidates and of the layer are de-duplicated among themselves in
      order of first occurrence, and each candidate's sub-edges replace it in place. A new
      sub-edge equal to an existing edge **outside** the candidates would take that edge's
      place instead; it cannot happen geometrically (both ends lie in the layer's box), and
      if it ever does the pass falls back to :func:`_insert_full`.
    """
    if graph is None:
        return _insert_full(None, new)
    graph = _drop_isolated(graph)
    nodes, edges = graph.nodes, graph.edges
    n_nodes, n_edges, n_new = len(nodes), len(edges), len(new.a)
    if n_new == 0 or n_edges == 0:
        return _insert_full(graph, new)

    cand = _edge_prefilter(nodes, edges, new.a, new.b)
    ends = edges[cand].astype(np.int64)
    n_c = len(cand)
    old_a, old_b = nodes[ends[:, 0], :2], nodes[ends[:, 1], :2]
    a = np.concatenate([old_a, new.a])
    b = np.concatenate([old_b, new.b])
    za = np.concatenate([nodes[ends[:, 0], 2], new.za])
    zb = np.concatenate([nodes[ends[:, 1], 2], new.zb])
    marker = np.concatenate([graph.markers[cand], np.full(n_new, new.marker, dtype=np.uint8)])
    old_time = ends.astype(np.float64) - 1e15
    time_a = np.concatenate([old_time[:, 0], new.time_a])
    time_b = np.concatenate([old_time[:, 1], new.time_b])
    time_seg = np.concatenate([np.full(n_c, -np.inf), new.time_seg])

    i1, j1 = _bbox_pairs(old_a, old_b, new.a, new.b, self_pairs=False)
    i2, j2 = _bbox_pairs(new.a, new.b, new.a, new.b, self_pairs=True)
    i = np.concatenate([i1, i2 + n_c])
    j = np.concatenate([j1 + n_c, j2 + n_c])
    ev_seg, ev_t, ev_xyz, ev_time, _ = _pair_events(a, b, za, zb, time_seg, i, j)

    n_seg = n_c + n_new
    seg_all = np.concatenate([np.arange(n_seg), np.arange(n_seg), ev_seg])
    t_all = np.concatenate([np.zeros(n_seg), np.ones(n_seg), ev_t])
    xyz_all = np.concatenate([np.column_stack([a, za]), np.column_stack([b, zb]), ev_xyz])
    time_all = np.concatenate([time_a, time_b, ev_time])
    order = np.lexsort((t_all, seg_all))
    seg_all, xyz_all, time_all = seg_all[order], xyz_all[order], time_all[order]

    # Node identity: an existing key keeps its node; a new key becomes a node numbered after
    # every existing one, in the order of its first event (time, then sequence position).
    keys = _node_keys(xyz_all[:, :2])
    by_time = np.argsort(time_all, kind="stable")
    ukeys, first, inverse = np.unique(keys[by_time], return_index=True, return_inverse=True)
    pos = np.searchsorted(graph.keys, ukeys)
    pos_c = np.minimum(pos, max(n_nodes - 1, 0))
    known = (pos < n_nodes) & (graph.keys[pos_c] == ukeys)
    fresh = np.flatnonzero(~known)
    fresh_rank = np.argsort(first[fresh], kind="stable")
    node_of_unique = np.empty(len(ukeys), dtype=np.int64)
    node_of_unique[known] = graph.key_nodes[pos_c[known]]
    node_of_unique[fresh[fresh_rank]] = n_nodes + np.arange(len(fresh))
    node_of_event = np.empty(len(keys), dtype=np.int64)
    node_of_event[by_time] = node_of_unique[inverse]
    new_nodes = xyz_all[by_time][first[fresh[fresh_rank]]]
    new_keys = ukeys[fresh[fresh_rank]]

    # Sub-edges of the candidates and of the layer, de-duplicated in order of first occurrence.
    same = seg_all[:-1] == seg_all[1:]
    n0, n1 = node_of_event[:-1][same], node_of_event[1:][same]
    seg_of_edge = seg_all[:-1][same]
    nz = n0 != n1
    n0, n1, seg_of_edge = n0[nz], n1[nz], seg_of_edge[nz]
    lo, hi = np.minimum(n0, n1), np.maximum(n0, n1)
    local_keys = _edge_keys(lo, hi)
    uniq, first_edge, inv = np.unique(local_keys, return_index=True, return_inverse=True)
    edge_order = np.argsort(first_edge, kind="stable")
    rank_of_edge = np.empty(len(uniq), dtype=np.int64)
    rank_of_edge[edge_order] = np.arange(len(uniq))
    sub_markers = np.zeros(len(uniq), dtype=np.uint8)
    np.bitwise_or.at(sub_markers, rank_of_edge[inv], marker[seg_of_edge])
    pick = first_edge[edge_order]
    sub_edges = np.column_stack([lo[pick], hi[pick]])
    sub_seg = seg_of_edge[pick]

    if _meets_an_edge_outside(graph, cand, ends, sub_edges, n_nodes):
        return _insert_full(graph, new)

    # Splice: each candidate's sub-edges replace it in place, the layer's edges go last.
    on_cand = sub_seg < n_c
    per_cand = np.bincount(sub_seg[on_cand], minlength=n_c)
    n_on_cand = int(on_cand.sum())
    if np.all(per_cand == 1):
        out_edges = edges.copy()
        out_markers = graph.markers.copy()
        out_edges[cand] = sub_edges[on_cand]
        out_markers[cand] = sub_markers[on_cand]
    else:
        counts = np.ones(n_edges, dtype=np.int64)
        counts[cand] = per_cand
        starts = np.cumsum(counts) - counts
        total = int(starts[-1] + counts[-1])
        out_edges = np.empty((total, 2), dtype=np.int32)
        out_markers = np.empty(total, dtype=np.uint8)
        untouched = np.ones(n_edges, dtype=bool)
        untouched[cand] = False
        out_edges[starts[untouched]] = edges[untouched]
        out_markers[starts[untouched]] = graph.markers[untouched]
        group_start = np.cumsum(per_cand) - per_cand
        seg_c = sub_seg[on_cand]
        slot = starts[cand[seg_c]] + (np.arange(n_on_cand) - group_start[seg_c])
        out_edges[slot] = sub_edges[on_cand]
        out_markers[slot] = sub_markers[on_cand]
    out_edges = np.concatenate([out_edges, sub_edges[~on_cand].astype(np.int32)])
    out_markers = np.concatenate([out_markers, sub_markers[~on_cand]])

    all_nodes = np.concatenate([nodes, new_nodes]) if len(new_nodes) else nodes
    if len(new_keys):
        by_key = np.argsort(new_keys)  # np.insert keeps the given order at a shared slot
        at = np.searchsorted(graph.keys, new_keys[by_key])
        index_keys = np.insert(graph.keys, at, new_keys[by_key])
        index_nodes = np.insert(graph.key_nodes, at, n_nodes + by_key)
    else:
        index_keys, index_nodes = graph.keys, graph.key_nodes
    referenced = np.zeros(len(new_keys), dtype=bool)
    ids = sub_edges.ravel()
    referenced[ids[ids >= n_nodes] - n_nodes] = True
    isolated = n_nodes + np.flatnonzero(~referenced)
    return _Graph(all_nodes, out_edges, out_markers, index_keys, index_nodes, isolated)


def _meets_an_edge_outside(
    graph: _Graph,
    cand: NDArray[np.intp],
    ends: NDArray[np.int64],
    sub_edges: NDArray[np.int64],
    n_nodes: int,
) -> bool:
    """Whether a local sub-edge between two existing nodes is an edge outside ``cand``."""
    both_old = (sub_edges[:, 0] < n_nodes) & (sub_edges[:, 1] < n_nodes)
    if not both_old.any():
        return False
    suspects = _edge_keys(sub_edges[both_old, 0], sub_edges[both_old, 1])
    suspects = np.setdiff1d(suspects, _edge_keys(ends[:, 0], ends[:, 1]))
    if not len(suspects):
        return False
    marked = np.zeros(n_nodes, dtype=bool)
    marked[(suspects >> 32).astype(np.int64)] = True
    marked[(suspects & 0xFFFFFFFF).astype(np.int64)] = True
    rows = np.flatnonzero(marked[graph.edges[:, 0]] & marked[graph.edges[:, 1]])
    if not len(rows):
        return False
    outside = np.setdiff1d(rows, cand)
    existing = _edge_keys(graph.edges[outside, 0], graph.edges[outside, 1])
    return bool(np.isin(suspects, existing).any())


def check_planar(
    nodes: NDArray[np.float64], edges: NDArray[np.integer], eps: float = ENCROACH_EPS
) -> int:
    """Number of edge pairs that violate the planar-graph invariant (0 for a valid PSLG).

    Two edges violate it when they cross, when a vertex of one lies strictly inside the other,
    or when they overlap collinearly, using the same eps window as the noder.
    """
    a = nodes[edges[:, 0], :2]
    b = nodes[edges[:, 1], :2]
    i, j = _bbox_pairs(a, b, a, b, self_pairs=True)
    if len(i) == 0:
        return 0
    z = np.zeros(len(a))
    ev_seg, _, _, _, _ = _pair_events(a, b, z, z, np.zeros(len(a)), i, j)
    return len(np.unique(ev_seg))
