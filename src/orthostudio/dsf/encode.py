# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Vectorised DSF encoder, byte-identical to Ortho4XP ``build_dsf``.

Specs: ``docs/specs/dsf-encoding.md`` (pools, commands, atoms) and
``docs/specs/dsf-terrain-assignment.md`` (terrains, textures, ``.ter``). Origin:
``O4_DSF_Utils.py:464-1365``. The two triangle loops of Ortho4XP become array passes: a
first-encounter deduplication (``numpy.unique``) gives the pool entries and their positions,
the triangle lists are grouped by (terrain, pool) in first-use order, and the atoms are
serialised with ``tobytes``.
"""

from __future__ import annotations

import hashlib
import struct
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from orthostudio.dsf._mesh_reader import MeshLike, mesh_version
from orthostudio.dsf.bathy import (
    depth_ratio_u16,
    node_bathy_from_distance_masks,
    orthogrid,
    st_coord_arrays,
)
from orthostudio.dsf.params import DsfParams
from orthostudio.dsf.quadtree import PoolPartition, partition, quantize24
from orthostudio.dsf.recut import INLAND, LAND, SEA, RecutMesh, recut_water_tris, remap_tri_types
from orthostudio.dsf.xp12 import Xp12Rasters
from orthostudio.dsf.zones import AirportCover, texture_map
from orthostudio.errors import OsxpError
from orthostudio.imagery.grid import TextureId
from orthostudio.model import TileRef
from orthostudio.textures.imprint import load_mask, mask_cell, mask_crop_raw, needs_mask
from orthostudio.textures.ter import TerKind, sea_kind, ter_center, ter_filename, ter_text

__all__ = [
    "BUILTIN_WATER",
    "DsfBuild",
    "MaskLookup",
    "Terrain",
    "TextureJob",
    "build_dsf",
    "dsf_magic",
]

MaskLookup = Callable[[int, int], "Path | None"]

BUILTIN_WATER = "terrain_Water"
DSF_MAGIC = b"XPLNEDSF"
DSF_VERSION = 1
MAX_U16 = 65535

# Commands written by Ortho4XP (X-Plane DSF specification numbers).
CMD_POOL_SELECT = 1
CMD_SET_DEFINITION16 = 4
CMD_PATCH_FLAGS = 18
CMD_PATCH_TRIANGLE = 23
CMD_PATCH_TRIANGLE_CROSS = 24

FLAG_PHYSICAL = 1
FLAG_OVERLAY = 2
NEAR_LOD = 0.0
PHYSICAL_FAR_LOD = -1.0

PLANES_LAND = 7
PLANES_MASKED = 9
PLANES_WATER = 7
FAMILY_LAND, FAMILY_MASKED, FAMILY_WATER = 0, 1, 2
"""DSF pool families: ``[0, P)`` land, ``[P, 2P)`` masked/overlay, ``[2P, 3P)`` X-Plane water."""

RATIO_FETCH_U16 = 65535
"""``int(65535 * ratio_fetch)`` with ``ratio_fetch = 1`` (``O4_DSF_Utils.py:813, 861, 1029``)."""
HALF_U16 = 32768
"""Flat normal ``(32768, 32768)`` of water entries (``:857, 976, 1026``)."""

_CROSS = -1


def dsf_magic() -> bytes:
    """``XPLNEDSF`` + version 1 (``:1104-1105``)."""
    return DSF_MAGIC + struct.pack("<I", DSF_VERSION)


@dataclass(frozen=True)
class TextureJob:
    """One texture the DSF references and the terrain kinds it is drawn with."""

    texture: TextureId
    kinds: frozenset[TerKind]


@dataclass(frozen=True)
class Terrain:
    """One ``DEFN/TERT`` entry after ``terrain_Water``."""

    index: int
    texture: TextureId
    kind: TerKind

    @property
    def overlay(self) -> bool:
        return self.kind.overlay

    @property
    def ter_name(self) -> str:
        return ter_filename(self.texture, self.kind)


@dataclass
class DsfBuild:
    """The encoded DSF and what the tile needs next to it."""

    data: bytes
    textures: list[TextureJob]
    ter_files: dict[str, str]
    """``<name>.ter`` -> text, for ``terrain/``."""
    stats: dict[str, object] = field(default_factory=dict)
    terrains: list[Terrain] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _first_seen(keys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(unique keys in first-appearance order, rank of each element's key in that order)``."""
    uniq, first, inv = np.unique(keys, return_index=True, return_inverse=True)
    order = np.argsort(first, kind="stable")
    rank = np.empty(len(uniq), dtype=np.int64)
    rank[order] = np.arange(len(uniq))
    return uniq[order], rank[inv.ravel()]


def _positions(pool: np.ndarray, first: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Position of every entry inside its pool, by first encounter; also the (pool, pos) order."""
    order = np.lexsort((first, pool))
    sorted_pool = pool[order]
    starts = np.searchsorted(sorted_pool, sorted_pool, side="left")
    pos = np.empty(len(pool), dtype=np.int64)
    pos[order] = np.arange(len(pool)) - starts
    return pos, order


def _check_pool_sizes(
    pos: np.ndarray, tile: TileRef, pool: np.ndarray, part: PoolPartition
) -> None:
    """A pool holds 65536 entries at most, the DSF indexing them on 16 bits.

    The error says where the fullest pool lies, in ``serve.log``: a pool overflows when the mesh
    piles far more points on one spot than a scenery has, which is a fault upstream, and knowing
    the spot is most of finding it (+34-118, 2026-09-25).
    """
    if len(pos) and int(pos.max()) > MAX_U16:
        bucket = int(pool[int(np.argmax(pos))]) % part.n_pools
        side = 2.0 ** -int(part.level[bucket])
        lat = tile.lat + float(part.key_y[bucket]) * side
        lon = tile.lon + float(part.key_x[bucket]) * side
        raise OsxpError(
            "DSF_POOL_OVERFLOW",
            context={
                "tile": tile.name,
                "entries": int(pos.max()) + 1,
                "at": f"{lat:.6f}, {lon:.6f}",
            },
        )


def _pool_scales(part: PoolPartition, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(altmin, scale_z, inv_stp)`` per pool (``O4_DSF_Utils.py:532-548``)."""
    order = np.argsort(part.node_bucket, kind="stable")
    zs = z[order]
    bounds = np.searchsorted(part.node_bucket[order], np.arange(part.n_pools))
    altmin = np.floor(np.minimum.reduceat(zs, bounds)).astype(np.int64)
    altmax = np.ceil(np.maximum.reduceat(zs, bounds)).astype(np.int64)
    span = altmax - altmin
    scale_z = np.select([span < 770, span < 1284, span < 4368], [771, 1285, 4369], default=13107)
    inv_stp = np.select([span < 770, span < 1284, span < 4368], [85, 51, 15], default=5)
    return altmin, scale_z.astype(np.int64), inv_stp.astype(np.int64)


def _blocks(values: np.ndarray, cmd: int, block_values: int, unit: int) -> bytes:
    """Command blocks of at most ``block_values`` u16 values, count byte in ``unit``s."""
    values = np.ascontiguousarray(values, dtype="<u2")
    full, rem = divmod(len(values), block_values)
    out = bytearray()
    if full:
        blk = np.empty((full, 2 + 2 * block_values), dtype=np.uint8)
        blk[:, 0] = cmd
        blk[:, 1] = block_values // unit
        blk[:, 2:] = values[: full * block_values].reshape(full, block_values).view(np.uint8)
        out += blk.tobytes()
    if rem:
        out += bytes((cmd, rem // unit)) + values[full * block_values :].tobytes()
    return bytes(out)


def _atom(name: str, payload: bytes) -> bytes:
    """An atom: reversed 4-char name, little-endian size including the 8-byte header."""
    return name.encode("ascii")[::-1] + struct.pack("<I", 8 + len(payload)) + payload


NORMAL_DECIMALS = 2
"""``write_mesh_file`` writes the normals with ``%.2f`` (``O4_Mesh_Utils.py:355-360``)."""


def _normals_as_ortho4xp(normals: np.ndarray) -> np.ndarray:
    """The normals as ``read_mesh_file`` holds them: float64 parsed from the 2-decimal text.

    ``orthostudio.mesh.mesh_file.MeshData`` stores them as float32, which cannot hold
    ``float("-0.40")`` exactly; ``(1 - v) / 2 * 65535`` then lands on a different side of a rounding
    tie (1 630 entries of the reference tile). Rounding the float32 back to 2 decimals in float64
    recovers the parsed value exactly (verified for every 2-decimal value in [-1, 1]). Float64 input
    is taken as is.
    """
    if normals.dtype == np.float64:
        return normals
    return np.round(normals.astype(np.float64), NORMAL_DECIMALS)


class _MaskDecision:
    """``needs_mask`` (``O4_Mask_Utils.py:38-60``) with each mask PNG loaded once per build."""

    def __init__(self, mask_zl: int, masks: MaskLookup) -> None:
        self.mask_zl = mask_zl
        self.masks = masks
        self.cache: dict[tuple[int, int], np.ndarray | None] = {}

    def __call__(self, t: TextureId) -> bool:
        if t.zl < self.mask_zl:
            return False
        m_til_x, m_til_y, x0, y0, side = mask_cell(t, self.mask_zl)
        if (m_til_x, m_til_y) not in self.cache:
            path = self.masks(m_til_x, m_til_y)
            self.cache[(m_til_x, m_til_y)] = None if path is None else load_mask(Path(path))
        full = self.cache[(m_til_x, m_til_y)]
        return full is not None and needs_mask(mask_crop_raw(full, x0, y0, side))


# --------------------------------------------------------------------------- terrains


@dataclass
class _Terrains:
    """Terrain registry of the two Ortho4XP loops (``:640-1042``)."""

    of_tri: np.ndarray  # (M,) terrain index per triangle, 0 = terrain_Water
    table: list[Terrain]  # index 1.. in creation order
    tex_x: np.ndarray  # (T + 1,) texture attributes per terrain (0 unused)
    tex_y: np.ndarray
    zl: np.ndarray
    overlay: np.ndarray  # (T + 1,) bool
    family: np.ndarray  # (T + 1,) pool family
    kind_code: np.ndarray  # (T + 1,) 0 land, 1 inland overlay, 2 sea overlay, 3 sea masked (XP12)


def _terrains(
    tile: TileRef,
    rm: RecutMesh,
    params: DsfParams,
    masks: MaskLookup | None,
    airports: Sequence[AirportCover],
    existing_textures: Iterable[TextureId],
) -> _Terrains:
    tmap = texture_map(tile, params, airports=airports, existing_textures=existing_textures)
    lon, lat = rm.coords[:, 0], rm.coords[:, 1]
    t = rm.tris
    bary_lon = (lon[t[:, 0]] + lon[t[:, 1]] + lon[t[:, 2]]) / 3
    bary_lat = (lat[t[:, 0]] + lat[t[:, 1]] + lat[t[:, 2]]) / 3
    til_x, til_y = orthogrid(bary_lat, bary_lon, params.mesh_zl)
    tex_x, tex_y, zl, prov = tmap.lookup(til_x, til_y)
    packed = (
        ((tex_x.astype(np.int64) << 40) | (tex_y.astype(np.int64) << 16))
        | (zl.astype(np.int64) << 8)
        | prov.astype(np.int64)
    )
    uniq_tex, tex_inv = np.unique(packed, return_inverse=True)
    tex_inv = tex_inv.ravel()
    textures = [
        tmap.texture(int(k >> 40), int((k >> 16) & 0xFFFFFF), int((k >> 8) & 0xFF), int(k & 0xFF))
        for k in uniq_tex
    ]

    tri_types = rm.tri_types
    sea_idx = np.flatnonzero(tri_types == SEA)
    need = np.zeros(len(textures), dtype=np.bool_)
    if masks is not None:
        decide = _MaskDecision(params.mask_zl, masks)
        for k in np.unique(tex_inv[sea_idx]):
            need[k] = decide(textures[k])
    masked_sea = sea_idx[need[tex_inv[sea_idx]]]
    nonsea = np.flatnonzero(tri_types != SEA)

    of_tri = np.zeros(len(t), dtype=np.int64)
    table: list[Terrain] = []
    sea_k = sea_kind(params.ter_params())
    keys1, rank1 = _first_seen(tex_inv[masked_sea])
    of_tri[masked_sea] = 1 + rank1
    for k in keys1:
        table.append(Terrain(len(table) + 1, textures[k], sea_k))
    n1 = len(keys1)
    keys2, rank2 = _first_seen(tex_inv[nonsea] * 3 + tri_types[nonsea])
    of_tri[nonsea] = 1 + n1 + rank2
    for key in keys2:
        k, tt = divmod(int(key), 3)
        table.append(
            Terrain(
                len(table) + 1, textures[k], TerKind.LAND if tt == LAND else TerKind.WATER_OVERLAY
            )
        )
    if len(table) > MAX_U16:
        raise OsxpError("DSF_POOL_OVERFLOW", context={"tile": tile.name, "terrains": len(table)})

    n = len(table) + 1
    ttx = np.zeros(n, dtype=np.int64)
    tty = np.zeros(n, dtype=np.int64)
    tzl = np.zeros(n, dtype=np.int64)
    overlay = np.zeros(n, dtype=np.bool_)
    family = np.zeros(n, dtype=np.int64)
    kind_code = np.zeros(n, dtype=np.int64)
    for ter in table:
        i = ter.index
        ttx[i], tty[i], tzl[i] = ter.texture.til_x, ter.texture.til_y, ter.texture.zl
        overlay[i] = ter.overlay
        if ter.kind is TerKind.LAND:
            family[i], kind_code[i] = FAMILY_LAND, 0
        elif ter.kind is TerKind.WATER_OVERLAY:
            family[i], kind_code[i] = FAMILY_MASKED, 1
        elif ter.overlay:
            family[i], kind_code[i] = FAMILY_MASKED, 2
        else:
            family[i], kind_code[i] = FAMILY_MASKED, 3
    return _Terrains(of_tri, table, ttx, tty, tzl, overlay, family, kind_code)


# --------------------------------------------------------------------------- entries


@dataclass
class _Entries:
    """Pool entries of one family group and the (pool, position) of every corner."""

    pool: np.ndarray  # (E,) DSF pool index
    pos: np.ndarray  # (E,)
    order: np.ndarray  # (E,) entries sorted by (pool, pos)
    values: np.ndarray  # (E, 9) uint16 plane values (7 used for 7-plane pools)
    corner_pool: np.ndarray  # (K, 3)
    corner_pos: np.ndarray  # (K, 3)


def _terrain_entries(
    tile: TileRef,
    rm: RecutMesh,
    part: PoolPartition,
    terrains: _Terrains,
    seq: np.ndarray,
    icoords: np.ndarray,
    ratio: np.ndarray,
    params: DsfParams,
) -> _Entries:
    """Entries keyed by ``(bucket, ix, iy, terrain)`` (``:776-780``), corners ``(n1, n3, n2)``."""
    corners = rm.tris[seq][:, [0, 2, 1]]
    nodes = corners.ravel()
    ter = np.repeat(terrains.of_tri[seq], 3)
    bucket = part.node_bucket[nodes]
    key = (
        ter.astype(np.uint64) << np.uint64(48)
        | bucket.astype(np.uint64) << np.uint64(32)
        | part.ix[nodes].astype(np.uint64) << np.uint64(16)
        | part.iy[nodes].astype(np.uint64)
    )
    _, first, inv = np.unique(key, return_index=True, return_inverse=True)
    inv = inv.ravel()
    e_node, e_ter = nodes[first], ter[first]
    e_pool = bucket[first] + part.n_pools * terrains.family[e_ter]
    pos, order = _positions(e_pool, first)
    _check_pool_sizes(pos, tile, e_pool, part)

    lat, lon = rm.coords[e_node, 1], rm.coords[e_node, 0]
    s, t = st_coord_arrays(
        lat, lon, terrains.tex_x[e_ter], terrains.tex_y[e_ter], terrains.zl[e_ter]
    )
    su = np.rint(s * 65535).astype(np.uint16)
    tu = np.rint(t * 65535).astype(np.uint16)
    code = terrains.kind_code[e_ter]
    values = np.zeros((len(first), PLANES_MASKED), dtype=np.uint16)
    values[:, :3] = icoords[e_node, :3]
    land, inland, sea_ovl, sea_xp12 = code == 0, code == 1, code == 2, code == 3
    with_normals = land | sea_ovl | sea_xp12
    values[with_normals, 3:5] = icoords[e_node[with_normals], 3:5]
    values[land, 5], values[land, 6] = su[land], tu[land]
    values[sea_ovl, 5], values[sea_ovl, 6] = su[sea_ovl], tu[sea_ovl]
    values[sea_ovl, 7], values[sea_ovl, 8] = su[sea_ovl], tu[sea_ovl]
    values[sea_xp12, 5] = RATIO_FETCH_U16
    values[sea_xp12, 6] = ratio[e_node[sea_xp12]]
    values[sea_xp12, 7], values[sea_xp12, 8] = su[sea_xp12], tu[sea_xp12]
    values[inland, 3:5] = HALF_U16
    values[inland, 5], values[inland, 6] = su[inland], tu[inland]
    values[inland, 7] = 0
    values[inland, 8] = round(params.ratio_water * 65535)
    return _Entries(e_pool, pos, order, values, e_pool[inv].reshape(-1, 3), pos[inv].reshape(-1, 3))


def _water_entries(
    tile: TileRef,
    rm: RecutMesh,
    part: PoolPartition,
    seq: np.ndarray,
    icoords: np.ndarray,
    ratio: np.ndarray,
) -> _Entries:
    """``terrain_Water`` entries keyed by node (``:842, 1011``), pools ``[2P, 3P)``."""
    corners = rm.tris[seq][:, [0, 2, 1]]
    nodes = corners.ravel()
    _, first, inv = np.unique(nodes, return_index=True, return_inverse=True)
    inv = inv.ravel()
    e_node = nodes[first]
    e_pool = part.node_bucket[e_node] + FAMILY_WATER * part.n_pools
    pos, order = _positions(e_pool, first)
    _check_pool_sizes(pos, tile, e_pool, part)
    values = np.zeros((len(first), PLANES_MASKED), dtype=np.uint16)
    values[:, :3] = icoords[e_node, :3]
    values[:, 3:5] = HALF_U16
    values[:, 5] = RATIO_FETCH_U16
    values[:, 6] = ratio[e_node]
    return _Entries(e_pool, pos, order, values, e_pool[inv].reshape(-1, 3), pos[inv].reshape(-1, 3))


# --------------------------------------------------------------------------- commands


@dataclass
class _Group:
    terrain: int
    pool: int  # DSF pool index, or _CROSS
    corner_pool: np.ndarray  # (k, 3)
    corner_pos: np.ndarray  # (k, 3)


def _groups(terrain: np.ndarray, corner_pool: np.ndarray, corner_pos: np.ndarray) -> list[_Group]:
    """Triangle lists per (terrain, pool | cross-pool) in first-use order (``defaultdict``)."""
    if len(terrain) == 0:
        return []
    same = (corner_pool[:, 0] == corner_pool[:, 1]) & (corner_pool[:, 1] == corner_pool[:, 2])
    group = np.where(same, corner_pool[:, 0], _CROSS)
    gkey = terrain * np.int64(1 << 32) + (group + 1)
    _, first, inv = np.unique(gkey, return_index=True, return_inverse=True)
    gfirst = first[inv.ravel()]
    order = np.lexsort((np.arange(len(terrain)), gfirst, terrain))
    sorted_key = gkey[order]
    bounds = np.flatnonzero(np.diff(sorted_key)) + 1
    out: list[_Group] = []
    for start, stop in zip(
        np.concatenate(([0], bounds)), np.concatenate((bounds, [len(order)])), strict=True
    ):
        idx = order[start:stop]
        out.append(
            _Group(int(terrain[idx[0]]), int(group[idx[0]]), corner_pool[idx], corner_pos[idx])
        )
    return out


def _commands(
    groups: list[_Group], overlay: np.ndarray, new_pool: np.ndarray, overlay_lod: float
) -> bytes:
    """The ``CMDS`` payload (``:1203-1325``)."""
    out = bytearray()
    current = -1
    for g in groups:
        if g.terrain != current:
            out += struct.pack("<BH", CMD_SET_DEFINITION16, g.terrain)
            current = g.terrain
        flag = FLAG_OVERLAY if overlay[g.terrain] else FLAG_PHYSICAL
        lod = overlay_lod if flag == FLAG_OVERLAY else PHYSICAL_FAR_LOD
        if g.pool != _CROSS:
            out += struct.pack("<BH", CMD_POOL_SELECT, int(new_pool[g.pool]))
            out += struct.pack("<BBff", CMD_PATCH_FLAGS, flag, NEAR_LOD, lod)
            out += _blocks(g.corner_pos.ravel(), CMD_PATCH_TRIANGLE, 255, 1)
        else:
            first_pool = int(new_pool[g.corner_pool[0, 0]])
            out += struct.pack("<BH", CMD_POOL_SELECT, first_pool)
            out += struct.pack("<BBff", CMD_PATCH_FLAGS, flag, NEAR_LOD, lod)
            pairs = np.stack([new_pool[g.corner_pool], g.corner_pos], axis=2).reshape(-1)
            out += _blocks(pairs, CMD_PATCH_TRIANGLE_CROSS, 510, 2)
    return bytes(out)


# --------------------------------------------------------------------------- pools


def _pool_atoms(
    tile: TileRef,
    part: PoolPartition,
    altmin: np.ndarray,
    scale_z: np.ndarray,
    terrain_entries: _Entries,
    water_entries: _Entries,
) -> tuple[bytes, np.ndarray, int]:
    """``GEOD`` payload (POOL atoms then SCAL atoms), pool renumbering, number of pools written."""
    n_pools = part.n_pools
    planes = np.array(
        [PLANES_LAND] * n_pools + [PLANES_MASKED] * n_pools + [PLANES_WATER] * n_pools
    )
    pools: list[tuple[int, np.ndarray]] = []
    for entries in (terrain_entries, water_entries):
        sorted_pool = entries.pool[entries.order]
        bounds = np.searchsorted(sorted_pool, np.arange(3 * n_pools + 1))
        for k in np.unique(sorted_pool):
            start, stop = bounds[k], bounds[k + 1]
            pools.append((int(k), entries.values[entries.order[start:stop], : planes[k]]))
    pools.sort(key=lambda kt: kt[0])
    geod = bytearray()
    for k, table in pools:
        p = int(planes[k])
        body = struct.pack("<IB", len(table), p)
        cols = np.ascontiguousarray(table.T.astype("<u2"))
        parts = [b"\x00" + cols[j].tobytes() for j in range(p)]
        geod += _atom("POOL", body + b"".join(parts))
    for k, _ in pools:
        b = k % n_pools
        p = int(planes[k])
        scal = 2.0 ** (-int(part.level[b]))
        values = [
            scal,
            tile.lon + int(part.key_x[b]) * scal,
            scal,
            tile.lat + int(part.key_y[b]) * scal,
            float(scale_z[b]),
            float(altmin[b]),
            2,
            -1,
            2,
            -1,
            1,
            0,
            1,
            0,
            1,
            0,
            1,
            0,
        ]
        geod += _atom("SCAL", struct.pack(f"<{2 * p}f", *values[: 2 * p]))
    new_pool = np.full(3 * n_pools, -1, dtype=np.int64)
    written = [k for k, _ in pools]
    new_pool[written] = np.arange(len(written))
    return bytes(geod), new_pool, len(written)


# --------------------------------------------------------------------------- build_dsf


def build_dsf(
    tile: TileRef,
    mesh: MeshLike,
    masks: MaskLookup | None,
    params: DsfParams,
    rasters: Xp12Rasters | None,
    *,
    creation_agent: str = "osxp",
    distance_masks: MaskLookup | None = None,
    airports: Sequence[AirportCover] = (),
    existing_textures: Iterable[TextureId] = (),
    _quad_capacity: int | None = None,
) -> DsfBuild:
    """Encode the DSF of ``tile`` from an Ortho4XP mesh (``O4_DSF_Utils.py:464-1365``).

    ``masks`` maps ``(m_til_x, m_til_y)`` at ``params.mask_zl`` to the mask PNG deciding
    which sea textures are masked; ``distance_masks`` likewise for ``_dist.png`` files;
    ``rasters`` are the Global Scenery DEMN/DEMS payloads (``extract_xp12_rasters``).
    ``_quad_capacity`` overrides the 50 000 / 35 000 pool capacity (tests only: it makes small
    meshes split pools).
    """
    t0 = time.perf_counter()
    stats: dict[str, object] = {}
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    coords = np.empty((len(vertices), 5), dtype=np.float64)
    coords[:, :3] = vertices[:, :3]
    coords[:, 3:5] = _normals_as_ortho4xp(np.asarray(mesh.normals))[:, :2]
    tri_types = remap_tri_types(
        np.asarray(mesh.tri_attr), mesh_version(mesh), params.use_masks_for_inland
    )
    rm = recut_water_tris(coords, np.asarray(mesh.tris), tri_types)
    stats["recut_s"] = time.perf_counter() - t0
    stats["nodes"], stats["tris"] = rm.n_nodes, rm.n_tris

    bathy = node_bathy_from_distance_masks(rm.coords, rm.node_types, params.mask_zl, distance_masks)
    ratio = depth_ratio_u16(rm.node_is_coast, bathy, params.ratio_bathy)

    t1 = time.perf_counter()
    qx = quantize24(rm.coords[:, 0] - tile.lon)
    qy = quantize24(rm.coords[:, 1] - tile.lat)
    part = partition(qx, qy, capacity=_quad_capacity or params.quad_capacity)
    if part.n_pools > MAX_U16 // 3:
        raise OsxpError("DSF_POOL_OVERFLOW", context={"tile": tile.name, "pools": part.n_pools})
    altmin, scale_z, inv_stp = _pool_scales(part, rm.coords[:, 2])
    icoords = np.empty((rm.n_nodes, 5), dtype=np.uint16)
    icoords[:, 0], icoords[:, 1] = part.ix, part.iy
    icoords[:, 2] = np.round(
        (rm.coords[:, 2] - altmin[part.node_bucket]) * inv_stp[part.node_bucket]
    )
    strength = params.normal_map_strength
    icoords[:, 3] = np.round((1 + strength * rm.coords[:, 3]) / 2 * 65535)
    icoords[:, 4] = np.round((1 - strength * rm.coords[:, 4]) / 2 * 65535)
    stats["quadtree_s"] = time.perf_counter() - t1
    stats["pools"] = part.n_pools

    t2 = time.perf_counter()
    terrains = _terrains(tile, rm, params, masks, airports, existing_textures)
    stats["terrains_s"] = time.perf_counter() - t2

    t3 = time.perf_counter()
    sea_idx = np.flatnonzero(rm.tri_types == SEA)
    masked_sea = sea_idx[terrains.of_tri[sea_idx] != 0]
    nonsea = np.flatnonzero(rm.tri_types != SEA)
    seq = np.concatenate([masked_sea, nonsea])
    te = _terrain_entries(tile, rm, part, terrains, seq, icoords, ratio, params)
    pp = te.corner_pool * (MAX_U16 + 1) + te.corner_pos
    dropped = (pp[:, 0] == pp[:, 1]) | (pp[:, 1] == pp[:, 2]) | (pp[:, 2] == pp[:, 0])
    kept = ~dropped
    kept_tri = np.zeros(rm.n_tris, dtype=np.bool_)
    kept_tri[seq] = kept
    ter_of_seq = terrains.of_tri[seq]
    groups = _groups(ter_of_seq[kept], te.corner_pool[kept], te.corner_pos[kept])

    w1 = sea_idx[
        (terrains.of_tri[sea_idx] == 0)
        | (terrains.overlay[terrains.of_tri[sea_idx]] & kept_tri[sea_idx])
    ]
    inland = nonsea[rm.tri_types[nonsea] == INLAND]
    w2 = inland[kept_tri[inland]]
    wseq = np.concatenate([w1, w2])
    we = _water_entries(tile, rm, part, wseq, icoords, ratio)
    water_groups = _groups(np.zeros(len(wseq), dtype=np.int64), we.corner_pool, we.corner_pos)
    stats["entries_s"] = time.perf_counter() - t3
    stats["entries"] = int(len(te.pool) + len(we.pool))
    stats["dropped_tris"] = int(dropped.sum())
    stats["cross_pool_tris"] = int(
        sum(len(g.corner_pos) for g in groups + water_groups if g.pool == _CROSS)
    )

    t4 = time.perf_counter()
    geod, new_pool, n_written = _pool_atoms(tile, part, altmin, scale_z, te, we)
    cmds = _commands(water_groups + groups, terrains.overlay, new_pool, params.overlay_lod)
    stats["pools_written"] = n_written

    prop = (
        f"sim/west\0{tile.lon}\0sim/east\0{tile.lon + 1}\0sim/south\0{tile.lat}\0"
        f"sim/north\0{tile.lat + 1}\0sim/creation_agent\0{creation_agent}\0"
    ).encode("ascii")
    tert = (
        BUILTIN_WATER + "\0" + "".join(f"terrain/{t.ter_name}\0" for t in terrains.table)
    ).encode("ascii")
    demn = rasters.demn if rasters is not None else b""
    defn = (
        _atom("TERT", tert)
        + _atom("OBJT", b"")
        + _atom("POLY", b"")
        + _atom("NETW", b"")
        + _atom("DEMN", demn)
    )
    body = bytearray(dsf_magic())
    body += _atom("HEAD", _atom("PROP", prop))
    body += _atom("DEFN", defn)
    body += _atom("GEOD", geod)
    body += _atom("CMDS", cmds)
    if rasters is not None and rasters.dems:
        body += _atom("DEMS", rasters.dems)
    body += hashlib.md5(body).digest()  # the DSF footer, not a security hash
    stats["serialize_s"] = time.perf_counter() - t4
    stats["total_s"] = time.perf_counter() - t0
    stats["size"] = len(body)

    ter_params = params.ter_params()
    ter_files: dict[str, str] = {}
    jobs: dict[TextureId, set[TerKind]] = {}
    for ter in terrains.table:
        lat_med, lon_med = ter_center(ter.texture)
        ter_files[ter.ter_name] = ter_text(
            ter.texture, ter.kind, lat_med=lat_med, lon_med=lon_med, params=ter_params
        )
        jobs.setdefault(ter.texture, set()).add(ter.kind)
    textures = [TextureJob(t, frozenset(k)) for t, k in jobs.items()]
    return DsfBuild(bytes(body), textures, ter_files, stats, list(terrains.table))
