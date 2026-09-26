# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The vectorised DSF stage against line-by-line transcriptions of Ortho4XP.

The transcriptions below (``ReferenceQuadTree``, ``reference_recut``, ``reference_zone_dico``,
``reference_build_dsf``) follow ``O4_DSF_Utils.py`` and ``O4_Bathymetry.py`` statement by
statement, with the tile object replaced by a namespace and the file I/O by bytes. They are
slow and only run on small synthetic meshes. Other ``test_dsf_*`` modules import the synthetic
mesh helpers.
"""

from __future__ import annotations

import array
import hashlib
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil, floor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from orthostudio.dsf import DsfParams, build_dsf, texture_map
from orthostudio.dsf.quadtree import partition, quantize24
from orthostudio.dsf.recut import recut_water_tris, remap_tri_types
from orthostudio.dsf.zones import AirportCover
from orthostudio.imagery.grid import TextureId, st_coord, texture_at, tile_to_wgs84
from orthostudio.model import TileRef
from orthostudio.textures.imprint import mask_file_name
from orthostudio.textures.ter import TerKind, TerParams, ter_center, ter_text

TILE = TileRef(43, 5)
MaskSpec = tuple[int, int, int, int] | str | None
"""A water box in pixels, ``"gradient"`` (a distance mask) or ``None`` (all land)."""

# ------------------------------------------------------------------ synthetic meshes


@dataclass
class _MeshData:
    """A ``MeshLike`` with float64 normals (what the Ortho4XP parser yields); tests only."""

    vertices: np.ndarray
    normals: np.ndarray
    tris: np.ndarray
    tri_attr: np.ndarray
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyntheticMesh:
    coords: np.ndarray  # (N, 5) lon lat z u v
    tris: np.ndarray  # (M, 3)
    attr: np.ndarray  # (M,) uint8

    def mesh_data(self) -> _MeshData:
        return _MeshData(
            vertices=self.coords[:, :3].copy(),
            normals=self.coords[:, 3:5].copy(),
            tris=self.tris.astype(np.int32),
            tri_attr=self.attr,
            extra={"mesh_version": 2.0},
        )


def synthetic_mesh(
    seed: int,
    *,
    nx: int = 6,
    ny: int = 5,
    tile: TileRef = TILE,
    lon0: float = 0.05,
    lat0: float = 0.6,
    width: float = 0.3,
    near_duplicates: int = 3,
) -> SyntheticMesh:
    """A jittered grid over part of the tile with land / inland water / sea regions.

    Coordinates are written and re-read with ``%.15f`` / ``%.2f`` as Ortho4XP does, so that the
    values are exactly what ``read_mesh_file`` would hold. ``near_duplicates`` extra nodes sit
    within 1e-9 degree of existing ones (same 16-bit pool coordinates: deduplicated entries and
    possibly degenerate triangles).
    """
    rng = np.random.default_rng(seed)
    xs = lon0 + width * np.arange(nx + 1) / nx
    ys = lat0 + width * np.arange(ny + 1) / ny
    pts = []
    for j in range(ny + 1):
        for i in range(nx + 1):
            jit = rng.uniform(-0.2, 0.2, 2) * width / nx
            pts.append((tile.lon + xs[i] + jit[0], tile.lat + ys[j] + jit[1]))
    pts_arr = np.array(pts)
    n = len(pts_arr)
    z = rng.uniform(-8, 900, n)
    low = rng.random(n) < 0.3
    z[low] = rng.uniform(-8, 30, int(low.sum()))
    uv = rng.integers(-100, 101, (n, 2)) / 100.0
    tris = []
    attrs = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            b, c, d = a + 1, a + nx + 1, a + nx + 2
            # regions: west third sea, middle land, east third inland water, plus noise
            region = "sea" if i < nx // 3 else ("water" if i >= 2 * nx // 3 else "land")
            for corner in ((a, b, d), (a, d, c)):
                r = rng.random()
                if region == "sea":
                    attr = 2 if r < 0.8 else (4 if r < 0.9 else 0)
                elif region == "water":
                    attr = 1 if r < 0.8 else 0
                else:
                    attr = 0 if r < 0.9 else 1
                if rng.random() < 0.15:
                    attr |= 8
                tris.append(corner)
                attrs.append(attr)
    coords = np.column_stack([pts_arr, z, uv])
    tris_arr = np.array(tris, dtype=np.int64)
    attrs_arr = np.array(attrs, dtype=np.uint8)
    for _ in range(near_duplicates):
        t = int(rng.integers(len(tris_arr)))
        k = int(rng.integers(3))
        src = tris_arr[t, k]
        dup = coords[src].copy()
        dup[0] += 1e-9
        dup[3] = rng.integers(-100, 101) / 100.0
        coords = np.vstack([coords, dup])
        tris_arr[t, k] = len(coords) - 1
    # exact Ortho4XP text round trip
    text = "\n".join(f"{c[0]:.15f} {c[1]:.15f} {c[2] / 100000:.15f}" for c in coords)
    rt = np.fromstring(text, sep=" ").reshape(-1, 3)
    coords[:, 0], coords[:, 1], coords[:, 2] = rt[:, 0], rt[:, 1], rt[:, 2] * 100000
    coords[:, 3:5] = np.fromstring(
        "\n".join(f"{c[3]:.2f} {c[4]:.2f}" for c in coords), sep=" "
    ).reshape(-1, 2)
    return SyntheticMesh(coords, tris_arr, attrs_arr)


def write_mask(path: Path, *, water_box: MaskSpec, value: int = 200) -> None:
    """A 4096² mask PNG: 0 (water) inside ``water_box`` with a ``value`` fringe, 255 elsewhere.

    ``water_box == "gradient"`` writes a horizontal ramp 0..255 (a distance mask).
    """
    if water_box == "gradient":
        ramp = np.tile(np.linspace(0, 255, 4096).astype(np.uint8), (4096, 1))
        Image.fromarray(ramp, mode="L").save(path)
        return
    im = Image.new("L", (4096, 4096), 255)
    if water_box is not None:
        assert not isinstance(water_box, str)
        d = ImageDraw.Draw(im)
        x0, y0, x1, y1 = water_box
        d.rectangle((x0 - 200, y0 - 200, x1 + 200, y1 + 200), fill=value)
        d.rectangle((x0, y0, x1, y1), fill=0)
    im.save(path)


def masks_for(tmp: Path, cells: dict[tuple[int, int], MaskSpec]):
    """Mask lookup with a PNG per listed ``(m_til_x, m_til_y)`` cell."""
    d = tmp / "masks"
    d.mkdir(parents=True, exist_ok=True)
    for (x, y), box in cells.items():
        write_mask(d / mask_file_name(x, y), water_box=box)

    def lookup(m_til_x: int, m_til_y: int) -> Path | None:
        p = d / mask_file_name(m_til_x, m_til_y)
        return p if p.is_file() else None

    return lookup


# ------------------------------------------------------------------ Ortho4XP transcriptions


def float2qquad(x: float) -> str:  # O4_DSF_Utils.py:28-31
    if x >= 1:
        return "111111111111111111111111"
    return np.binary_repr(int(16777216 * x)).zfill(24)


class ReferenceQuadTree(dict):  # O4_DSF_Utils.py:37-107
    class Bucket(dict):
        def __init__(self) -> None:
            self["size"] = 0
            self["idx_nodes"] = set()

    def __init__(self, level: int, bucket_size: int) -> None:
        self.bucket_size = bucket_size
        if level == 0:
            self[("", "")] = self.Bucket()
        else:
            for i in range(2**level):
                for j in range(2**level):
                    key = (np.binary_repr(i).zfill(level), np.binary_repr(j).zfill(level))
                    self[key] = self.Bucket()
        self.nodes: dict[int, tuple[str, str]] = {}
        self.levels: dict[int, int] = {}
        self.last_node = 0

    def split_bucket(self, key: tuple[str, str]) -> None:
        level = len(key[0]) + 1
        self[(key[0] + "0", key[1] + "0")] = self.Bucket()
        self[(key[0] + "0", key[1] + "1")] = self.Bucket()
        self[(key[0] + "1", key[1] + "0")] = self.Bucket()
        self[(key[0] + "1", key[1] + "1")] = self.Bucket()
        for idx in self[key]["idx_nodes"]:
            new_key = (self.nodes[idx][0][:level], self.nodes[idx][1][:level])
            self[new_key]["idx_nodes"].add(idx)
            self[new_key]["size"] += 1
            self.levels[idx] += 1
        del self[key]

    def insert(self, bx: str, by: str, level: int) -> None:
        while True:
            key = (bx[:level], by[:level])
            if key in self:
                break
            level += 1
        if self[key]["size"] < self.bucket_size:
            self[key]["idx_nodes"].add(self.last_node)
            self[key]["size"] += 1
            self.nodes[self.last_node] = (bx, by)
            self.levels[self.last_node] = level
            self.last_node += 1
        else:
            self.split_bucket(key)
            self.insert(bx, by, level + 1)

    def clean(self) -> None:
        for key in list(self.keys()):
            if not self[key]["size"]:
                del self[key]


def reference_recut(node_coords, tri_idx, tri_types):  # O4_Bathymetry.py:16-183
    nbr_nodes = len(node_coords) // 5
    nbr_tris = len(tri_types)
    node_types = np.zeros(nbr_nodes, dtype=np.uint8)
    for i in range(nbr_tris):
        t = 1 << tri_types[i]
        node_types[tri_idx[3 * i + 0]] |= t
        node_types[tri_idx[3 * i + 1]] |= t
        node_types[tri_idx[3 * i + 2]] |= t
    node_is_coast = ((node_types & 1) != 0) & ((node_types & 6) != 0)
    tri_is_coast = np.zeros(nbr_tris, dtype=bool)
    for i in range(nbr_tris):
        tri_is_coast[i] |= node_is_coast[tri_idx[3 * i + 0]]
        tri_is_coast[i] |= node_is_coast[tri_idx[3 * i + 1]]
        tri_is_coast[i] |= node_is_coast[tri_idx[3 * i + 2]]
    coast_tri_count = np.sum(tri_is_coast)
    edge_type: dict[tuple[int, int], int] = {}
    for i in range(nbr_tris):
        if not tri_is_coast[i]:
            continue
        t = 1 << tri_types[i]
        (a, b, c) = tri_idx[3 * i : 3 * i + 3]
        for m, n in ((a, b), (b, c), (c, a)):
            if (m, n) in edge_type or (n, m) in edge_type:
                edge_type[(m, n)] |= t
                edge_type[(n, m)] |= t
            else:
                edge_type[(m, n)] = t
                edge_type[(n, m)] = t
    node_max_count = nbr_nodes + 3 * coast_tri_count
    node_coords = np.resize(node_coords, 5 * node_max_count)
    node_types = np.resize(node_types, node_max_count)
    node_is_coast = np.resize(node_is_coast, node_max_count)
    edge_cut: dict[tuple[int, int], int] = {}
    next_n = nbr_nodes
    for (a, b), t in edge_type.items():
        if b < a:
            continue
        if (t & 1 == 0) and node_is_coast[a] and node_is_coast[b]:
            edge_cut[(a, b)] = next_n
            edge_cut[(b, a)] = next_n
            node_coords[5 * next_n : 5 * next_n + 5] = (
                node_coords[5 * a : 5 * a + 5] + node_coords[5 * b : 5 * b + 5]
            ) / 2.0
            node_types[next_n] = t
            node_is_coast[next_n] = False
            next_n += 1
    tri_max_count = nbr_tris + 3 * coast_tri_count
    tri_idx = np.resize(tri_idx, 3 * tri_max_count)
    tri_types = np.resize(tri_types, tri_max_count)
    next_t = nbr_tris
    for i in range(nbr_tris):
        if not tri_types[i] or not tri_is_coast[i]:
            continue
        (a, b, c) = tri_idx[3 * i : 3 * i + 3]
        C = (a, b) in edge_cut  # noqa: N806
        A = (b, c) in edge_cut  # noqa: N806
        B = (c, a) in edge_cut  # noqa: N806
        cuts = A + B + C
        if not cuts:
            if (
                (edge_type[(a, b)] & 1 == 0)
                or (edge_type[(b, c)] & 1 == 0)
                or (edge_type[(c, a)] & 1 == 0)
            ):
                continue
            node_coords[5 * next_n : 5 * next_n + 5] = (
                node_coords[5 * a : 5 * a + 5]
                + node_coords[5 * b : 5 * b + 5]
                + node_coords[5 * c : 5 * c + 5]
            ) / 3.0
            node_types[next_n] = tri_types[i]
            node_is_coast[next_n] = False
            tri_idx[3 * i : 3 * i + 3] = (a, b, next_n)
            tri_idx[3 * next_t : 3 * next_t + 6] = (b, c, next_n, c, a, next_n)
            tri_types[next_t] = tri_types[next_t + 1] = tri_types[i]
            next_n += 1
            next_t += 2
        else:
            L = array.array("i")  # noqa: N806
            L.append(a)
            s1 = s2 = 0
            if C:
                s1 = len(L)
                L.append(edge_cut[(a, b)])
            else:
                s2 = len(L) - 1
            L.append(b)
            if A:
                s1 = len(L)
                L.append(edge_cut[(b, c)])
            else:
                s2 = len(L) - 1
            L.append(c)
            if B:
                s1 = len(L)
                L.append(edge_cut[(c, a)])
            else:
                s2 = len(L) - 1
            L = L + L  # noqa: N806
            if cuts == 2:
                (x, y, z, t, u) = L[s2 : s2 + 5]
                tri_idx[3 * i : 3 * i + 3] = (x, y, z)
                tri_idx[3 * next_t : 3 * next_t + 6] = (z, t, u, x, z, u)
                tri_types[next_t : next_t + 2] = tri_types[i]
                next_t += 2
            elif cuts == 1:
                (x, y, z, t) = L[s1 : s1 + 4]
                tri_idx[3 * i : 3 * i + 3] = (x, y, z)
                tri_idx[3 * next_t : 3 * next_t + 3] = (x, z, t)
                tri_types[next_t] = tri_types[i]
                next_t += 1
            else:
                (x, y, z, t, u, v) = L[s1 : s1 + 6]
                tri_idx[3 * i : 3 * i + 3] = (x, y, z)
                tri_idx[3 * next_t : 3 * next_t + 9] = (z, t, u, u, v, x, x, z, u)
                tri_types[next_t : next_t + 3] = tri_types[i]
                next_t += 3
    nbr_nodes = next_n
    node_coords = np.resize(node_coords, 5 * nbr_nodes)
    node_types = np.resize(node_types, nbr_nodes)
    node_is_coast = np.resize(node_is_coast, nbr_nodes)
    nbr_tris = next_t
    tri_idx = np.resize(tri_idx, 3 * nbr_tris)
    tri_types = np.resize(tri_types, nbr_tris)
    return (nbr_nodes, node_coords, node_types, node_is_coast, nbr_tris, tri_idx, tri_types)


def _wgs84_to_orthogrid(lat, lon, zl):
    t = texture_at(lat, lon, zl, "")
    return (t.til_x, t.til_y)


def reference_zone_dico(tile, dico_airports, existing_dds):  # O4_DSF_Utils.py:110-257
    from math import cos, pi

    m_to_lat = 1 / (pi * 6378137 / 180)

    def m_to_lon(lat):
        return m_to_lat / cos(pi * lat / 180)

    masks_im = Image.new("L", (4096, 4096), "black")
    masks_draw = ImageDraw.Draw(masks_im)
    airport_array = np.zeros((4096, 4096), dtype=np.bool_)
    if tile.cover_airports_with_highres in ("True", "ICAO"):
        if tile.cover_airports_with_highres == "ICAO":
            airports_list = [a for a in dico_airports if dico_airports[a]["key_type"] == "icao"]
        else:
            airports_list = dico_airports.keys()
        for airport in airports_list:
            (xmin, ymin, xmax, ymax) = dico_airports[airport]["boundary"].bounds
            xmin -= 1000 * tile.cover_extent * m_to_lon(tile.lat)
            xmax += 1000 * tile.cover_extent * m_to_lon(tile.lat)
            ymax += 1000 * tile.cover_extent * m_to_lat
            ymin -= 1000 * tile.cover_extent * m_to_lat
            (til_x_left, til_y_top) = _wgs84_to_orthogrid(
                ymax + tile.lat, xmin + tile.lon, tile.cover_zl
            )
            (ymax, xmin) = tile_to_wgs84(til_x_left, til_y_top, tile.cover_zl)
            ymax -= tile.lat
            xmin -= tile.lon
            (til_x_left2, til_y_top2) = _wgs84_to_orthogrid(
                ymin + tile.lat, xmax + tile.lon, tile.cover_zl
            )
            (ymin, xmax) = tile_to_wgs84(til_x_left2 + 16, til_y_top2 + 16, tile.cover_zl)
            ymin -= tile.lat
            xmax -= tile.lon
            xmin = max(0, xmin)
            xmax = min(1, xmax)
            ymin = max(0, ymin)
            ymax = min(1, ymax)
            colmin = round(xmin * 4095)
            colmax = round(xmax * 4095)
            rowmax = round((1 - ymin) * 4095)
            rowmin = round((1 - ymax) * 4095)
            airport_array[rowmin : rowmax + 1, colmin : colmax + 1] = 1
    dico_customzl = {}
    dico_tmp = {}
    til_x_min, til_y_min = _wgs84_to_orthogrid(tile.lat + 1, tile.lon, tile.mesh_zl)
    til_x_max, til_y_max = _wgs84_to_orthogrid(tile.lat, tile.lon + 1, tile.mesh_zl)
    i = 1
    base_zone = (
        [
            tile.lat,
            tile.lon,
            tile.lat,
            tile.lon + 1,
            tile.lat + 1,
            tile.lon + 1,
            tile.lat + 1,
            tile.lon,
            tile.lat,
            tile.lon,
        ],
        tile.default_zl,
        tile.default_website,
    )
    for region in [base_zone, *tile.zone_list[::-1]]:
        dico_tmp[i] = (region[1], region[2])
        pol = [
            (round((x - tile.lon) * 4095), round((tile.lat + 1 - y) * 4095))
            for (x, y) in zip(region[0][1::2], region[0][::2], strict=False)
        ]
        masks_draw.polygon(pol, fill=i)
        i += 1
    for til_x in range(til_x_min, til_x_max + 1, 16):
        for til_y in range(til_y_min, til_y_max + 1, 16):
            (latp, lonp) = tile_to_wgs84(til_x + 8, til_y + 8, tile.mesh_zl)
            lonp = max(min(lonp, tile.lon + 1), tile.lon)
            latp = max(min(latp, tile.lat + 1), tile.lat)
            x = round((lonp - tile.lon) * 4095)
            y = round((tile.lat + 1 - latp) * 4095)
            (zoomlevel, provider_code) = dico_tmp[masks_im.getpixel((x, y))]
            if airport_array[y, x]:
                zoomlevel = max(zoomlevel, tile.cover_zl)
            til_x_text = 16 * (int(til_x / 2 ** (tile.mesh_zl - zoomlevel)) // 16)
            til_y_text = 16 * (int(til_y / 2 ** (tile.mesh_zl - zoomlevel)) // 16)
            dico_customzl[(til_x, til_y)] = (til_x_text, til_y_text, zoomlevel, provider_code)
    if tile.cover_airports_with_highres == "Existing":
        for f in existing_dds:
            if f[-4:] != ".dds":
                continue
            items = f.split("_")
            (til_y_text, til_x_text) = [int(x) for x in items[:2]]
            zoomlevel = int(items[-1][-6:-4])
            provider_code = "_".join(items[2:])[:-6]
            for til_x in range(
                til_x_text * 2 ** (tile.mesh_zl - zoomlevel),
                (til_x_text + 16) * 2 ** (tile.mesh_zl - zoomlevel),
            ):
                for til_y in range(
                    til_y_text * 2 ** (tile.mesh_zl - zoomlevel),
                    (til_y_text + 16) * 2 ** (tile.mesh_zl - zoomlevel),
                ):
                    if ((til_x, til_y) not in dico_customzl) or dico_customzl[(til_x, til_y)][
                        2
                    ] <= zoomlevel:
                        dico_customzl[(til_x, til_y)] = (
                            til_x_text,
                            til_y_text,
                            zoomlevel,
                            provider_code,
                        )
    return dico_customzl


def _set_depth_ratio(n, node_is_coast, node_bathy, tile):  # O4_Bathymetry.py:8-13
    if node_is_coast[n]:
        return 0
    return max(min(10 * tile.ratio_bathy * node_bathy[n] / 255, 1), 0.1)


def reference_build_dsf(  # O4_DSF_Utils.py:464-1365 without the file I/O and the download queue
    tile,
    mesh_version,
    node_coords,
    tri_idx,
    tri_types,
    dico_customzl,
    needs_mask_fn,
    node_bathy_fn,
    b_demn,
    b_dems,
    creation_agent="Ortho4XP",
):
    quad_init_level, quad_capacity_high, quad_capacity_low = 3, 50000, 35000
    nbr_tris = len(tri_types)
    has_water = 7 if (mesh_version >= 1.3) else 3
    for i in range(nbr_tris):
        t = tri_types[i] & has_water
        t = t and (2 * (t > 1 or tile.use_masks_for_inland) or 1)
        tri_types[i] = t
    (nbr_nodes, node_coords, node_types, node_is_coast, nbr_tris, tri_idx, tri_types) = (
        reference_recut(node_coords, tri_idx, tri_types)
    )
    node_bathy = node_bathy_fn(nbr_nodes, node_coords, node_types)
    quad_capacity = quad_capacity_low if tile.use_masks_for_inland else quad_capacity_high
    if getattr(tile, "quad_capacity", None):
        quad_capacity = tile.quad_capacity
    pool_quadtree = ReferenceQuadTree(quad_init_level, quad_capacity)
    for i in range(nbr_nodes):
        pool_quadtree.insert(
            float2qquad(node_coords[5 * i + 0] - tile.lon),
            float2qquad(node_coords[5 * i + 1] - tile.lat),
            quad_init_level,
        )
    pool_quadtree.clean()
    pool_nbr = len(pool_quadtree)
    idx_node_to_idx_pool = {}
    key_to_idx_pool = {}
    for idx_pool, key in enumerate(pool_quadtree):
        key_to_idx_pool[key] = idx_pool
        for idx_node in pool_quadtree[key]["idx_nodes"]:
            idx_node_to_idx_pool[idx_node] = idx_pool
    pool_param = {}
    node_icoords = np.zeros(5 * nbr_nodes, dtype=np.uint16)
    for key in pool_quadtree:
        level = len(key[0])
        plist = sorted(pool_quadtree[key]["idx_nodes"])
        node_icoords[[5 * idx_node for idx_node in plist]] = [
            int(pool_quadtree.nodes[idx_node][0][level : level + 16], 2) for idx_node in plist
        ]
        node_icoords[[5 * idx_node + 1 for idx_node in plist]] = [
            int(pool_quadtree.nodes[idx_node][1][level : level + 16], 2) for idx_node in plist
        ]
        altitudes = np.array([node_coords[5 * idx_node + 2] for idx_node in plist])
        altmin = floor(altitudes.min())
        altmax = ceil(altitudes.max())
        if altmax - altmin < 770:
            scale_z, inv_stp = 771, 85
        elif altmax - altmin < 1284:
            scale_z, inv_stp = 1285, 51
        elif altmax - altmin < 4368:
            scale_z, inv_stp = 4369, 15
        else:
            scale_z, inv_stp = 13107, 5
        scal_x = scal_y = 2 ** (-level)
        node_icoords[[5 * idx_node + 2 for idx_node in plist]] = np.round(
            (altitudes - altmin) * inv_stp
        )
        pool_param[key_to_idx_pool[key]] = (
            scal_x,
            tile.lon + int(key[0], 2) * scal_x,
            scal_y,
            tile.lat + int(key[1], 2) * scal_y,
            scale_z,
            altmin,
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
        )
    node_icoords[3::5] = np.round((1 + tile.normal_map_strength * node_coords[3::5]) / 2 * 65535)
    node_icoords[4::5] = np.round((1 - tile.normal_map_strength * node_coords[4::5]) / 2 * 65535)
    node_icoords = array.array("H", node_icoords)
    overlay_terrains = set()
    skipped_terrains_for_masking = set()
    dsf_pools = {}
    dsf_pool_nbr = 3 * pool_nbr
    for idx_dsfpool in range(dsf_pool_nbr):
        dsf_pools[idx_dsfpool] = array.array("H")
    dsf_pool_length = np.zeros(dsf_pool_nbr, "int")
    dsf_pool_plane = 7 * np.ones(dsf_pool_nbr, "int")
    dsf_pool_plane[pool_nbr : 2 * pool_nbr] = 9
    dsf_pool_plane[2 * pool_nbr : 3 * pool_nbr] = 7
    textured_nodes = {}
    textured_tris = {}
    b_tert = bytes("terrain_Water\0", "ascii")
    dico_terrains = {"terrain_Water": 0}
    textured_tris[0] = defaultdict(lambda: array.array("H"))
    ter_params = TerParams(
        water_tech=tile.water_tech,
        imprint_masks_to_dds=tile.imprint_masks_to_dds,
        mask_zl=tile.mask_zl,
        use_decal_on_terrain=tile.use_decal_on_terrain,
        terrain_casts_shadows=tile.terrain_casts_shadows,
        use_test_texture=False,
    )
    ter_files = {}

    def create_terrain_file(texture_attributes, tri_type, is_overlay):
        from orthostudio.textures.ter import ter_filename

        t = TextureId(*texture_attributes)
        kind = TerKind.of(tri_type, bool(is_overlay))
        name = ter_filename(t, kind)
        lat_med, lon_med = ter_center(t)
        ter_files[name] = ter_text(t, kind, lat_med=lat_med, lon_med=lon_med, params=ter_params)
        return name

    def water_tri(n1, n2, n3):
        tri_p = array.array("H")
        for n in (n1, n3, n2):
            node_hash = (n, 0)
            if node_hash in textured_nodes:
                (idx_dsfpool, pos_in_pool) = textured_nodes[node_hash]
            else:
                idx_dsfpool = idx_node_to_idx_pool[n] + 2 * pool_nbr
                pos_in_pool = dsf_pool_length[idx_dsfpool]
                textured_nodes[node_hash] = [idx_dsfpool, pos_in_pool]
                dsf_pools[idx_dsfpool].extend(node_icoords[5 * n : 5 * n + 3])
                dsf_pools[idx_dsfpool].extend((32768, 32768))
                ratio_bathy = _set_depth_ratio(n, node_is_coast, node_bathy, tile)
                ratio_fetch = 1
                dsf_pools[idx_dsfpool].extend((int(65535 * ratio_fetch), int(65535 * ratio_bathy)))
                dsf_pool_length[idx_dsfpool] += 1
            tri_p.extend((idx_dsfpool, pos_in_pool))
        if tri_p[0] == tri_p[2] == tri_p[4]:
            textured_tris[0][tri_p[0]].extend((tri_p[1], tri_p[3], tri_p[5]))
        else:
            textured_tris[0]["cross-pool"].extend(tri_p)

    for tri in range(nbr_tris):  # first potentially masked water tris
        tri_type = tri_types[tri]
        if tri_type != 2:
            continue
        (n1, n2, n3) = tri_idx[3 * tri : 3 * tri + 3]
        bary_lon = (node_coords[5 * n1 + 0] + node_coords[5 * n2 + 0] + node_coords[5 * n3 + 0]) / 3
        bary_lat = (node_coords[5 * n1 + 1] + node_coords[5 * n2 + 1] + node_coords[5 * n3 + 1]) / 3
        texture_attributes = dico_customzl[_wgs84_to_orthogrid(bary_lat, bary_lon, tile.mesh_zl)]
        terrain_attributes = (texture_attributes, tri_type)
        is_overlay = False
        if terrain_attributes in dico_terrains:
            terrain_idx = dico_terrains[terrain_attributes]
            is_overlay = terrain_idx in overlay_terrains
        else:
            needs_new_terrain = False
            if terrain_attributes not in skipped_terrains_for_masking:
                if needs_mask_fn(*texture_attributes):
                    needs_new_terrain = True
                else:
                    skipped_terrains_for_masking.add(terrain_attributes)
            if needs_new_terrain:
                terrain_idx = len(dico_terrains)
                textured_tris[terrain_idx] = defaultdict(lambda: array.array("H"))
                dico_terrains[terrain_attributes] = terrain_idx
                is_overlay = tile.water_tech == "XP11 + bathy"
                is_overlay |= not tile.imprint_masks_to_dds
                if is_overlay:
                    overlay_terrains.add(terrain_idx)
                terrain_file_name = create_terrain_file(texture_attributes, tri_type, is_overlay)
                b_tert += bytes("terrain/" + terrain_file_name + "\0", "ascii")
            else:
                terrain_idx = 0
        if terrain_idx:
            tri_p = array.array("H")
            for n in (n1, n3, n2):
                idx_pool = idx_node_to_idx_pool[n]
                node_hash = (idx_pool, *node_icoords[5 * n : 5 * n + 2], terrain_idx)
                if node_hash in textured_nodes:
                    (idx_dsfpool, pos_in_pool) = textured_nodes[node_hash]
                else:
                    (s, t) = st_coord(
                        node_coords[5 * n + 1], node_coords[5 * n], *texture_attributes[:3]
                    )
                    if is_overlay:
                        idx_dsfpool = idx_pool + pool_nbr
                        dsf_pools[idx_dsfpool].extend(node_icoords[5 * n : 5 * n + 5])
                        dsf_pools[idx_dsfpool].extend(
                            (
                                round(s * 65535),
                                round(t * 65535),
                                round(s * 65535),
                                round(t * 65535),
                            )
                        )
                    else:
                        idx_dsfpool = idx_pool + pool_nbr
                        dsf_pools[idx_dsfpool].extend(node_icoords[5 * n : 5 * n + 5])
                        ratio_bathy = _set_depth_ratio(n, node_is_coast, node_bathy, tile)
                        ratio_fetch = 1
                        dsf_pools[idx_dsfpool].extend(
                            (
                                int(65535 * ratio_fetch),
                                int(65535 * ratio_bathy),
                                round(s * 65535),
                                round(t * 65535),
                            )
                        )
                    pos_in_pool = dsf_pool_length[idx_dsfpool]
                    textured_nodes[node_hash] = (idx_dsfpool, pos_in_pool)
                    dsf_pool_length[idx_dsfpool] += 1
                tri_p.extend((idx_dsfpool, pos_in_pool))
            if tri_p[:2] == tri_p[2:4] or tri_p[2:4] == tri_p[4:] or tri_p[4:] == tri_p[:2]:
                continue
            if tri_p[0] == tri_p[2] == tri_p[4]:
                textured_tris[terrain_idx][tri_p[0]].extend((tri_p[1], tri_p[3], tri_p[5]))
            else:
                textured_tris[terrain_idx]["cross-pool"].extend(tri_p)
        if (not terrain_idx) or is_overlay:
            water_tri(n1, n2, n3)

    for tri in range(nbr_tris):  # second land and inland water tris
        tri_type = tri_types[tri]
        if tri_type == 2:
            continue
        (n1, n2, n3) = tri_idx[3 * tri : 3 * tri + 3]
        bary_lon = (node_coords[5 * n1] + node_coords[5 * n2] + node_coords[5 * n3]) / 3
        bary_lat = (node_coords[5 * n1 + 1] + node_coords[5 * n2 + 1] + node_coords[5 * n3 + 1]) / 3
        texture_attributes = dico_customzl[_wgs84_to_orthogrid(bary_lat, bary_lon, tile.mesh_zl)]
        terrain_attributes = (texture_attributes, tri_type)
        is_overlay = False
        if terrain_attributes in dico_terrains:
            terrain_idx = dico_terrains[terrain_attributes]
            is_overlay = terrain_idx in overlay_terrains
        else:
            terrain_idx = len(dico_terrains)
            textured_tris[terrain_idx] = defaultdict(lambda: array.array("H"))
            dico_terrains[terrain_attributes] = terrain_idx
            is_overlay = tri_type == 1
            if is_overlay:
                overlay_terrains.add(terrain_idx)
            terrain_file_name = create_terrain_file(texture_attributes, tri_type, is_overlay)
            b_tert += bytes("terrain/" + terrain_file_name + "\0", "ascii")
        tri_p = array.array("H")
        for n in (n1, n3, n2):
            idx_pool = idx_node_to_idx_pool[n]
            node_hash = (idx_pool, *node_icoords[5 * n : 5 * n + 2], terrain_idx)
            if node_hash in textured_nodes:
                (idx_dsfpool, pos_in_pool) = textured_nodes[node_hash]
            else:
                (s, t) = st_coord(
                    node_coords[5 * n + 1], node_coords[5 * n], *texture_attributes[:3]
                )
                if not tri_type:
                    idx_dsfpool = idx_pool
                    dsf_pools[idx_dsfpool].extend(node_icoords[5 * n : 5 * n + 5])
                    dsf_pools[idx_dsfpool].extend((round(s * 65535), round(t * 65535)))
                else:
                    idx_dsfpool = idx_pool + pool_nbr
                    dsf_pools[idx_dsfpool].extend(node_icoords[5 * n : 5 * n + 3])
                    dsf_pools[idx_dsfpool].extend(
                        (
                            32768,
                            32768,
                            round(s * 65535),
                            round(t * 65535),
                            0,
                            round(tile.ratio_water * 65535),
                        )
                    )
                pos_in_pool = dsf_pool_length[idx_dsfpool]
                textured_nodes[node_hash] = (idx_dsfpool, pos_in_pool)
                dsf_pool_length[idx_dsfpool] += 1
            tri_p.extend((idx_dsfpool, pos_in_pool))
        if tri_p[:2] == tri_p[2:4] or tri_p[2:4] == tri_p[4:] or tri_p[4:] == tri_p[:2]:
            continue
        if tri_p[0] == tri_p[2] == tri_p[4]:
            textured_tris[terrain_idx][tri_p[0]].extend((tri_p[1], tri_p[3], tri_p[5]))
        else:
            textured_tris[terrain_idx]["cross-pool"].extend(tri_p)
        if is_overlay:
            water_tri(n1, n2, n3)

    b_prop = bytes(
        f"sim/west\0{tile.lon}\0sim/east\0{tile.lon + 1}\0sim/south\0{tile.lat}\0"
        f"sim/north\0{tile.lat + 1}\0sim/creation_agent\0{creation_agent}\0",
        "ascii",
    )
    size_of_head_atom = 16 + len(b_prop)
    size_of_prop_atom = 8 + len(b_prop)
    size_of_defn_atom = 48 + len(b_tert) + len(b_demn)
    size_of_geod_atom = 8
    for k in range(dsf_pool_nbr):
        if dsf_pool_length[k] > 0:
            size_of_geod_atom += 21 + dsf_pool_plane[k] * (9 + 2 * dsf_pool_length[k])
    f = bytearray()
    f += b"XPLNEDSF" + struct.pack("<I", 1)
    f += (
        b"DAEH"
        + struct.pack("<I", size_of_head_atom)
        + b"PORP"
        + struct.pack("<I", size_of_prop_atom)
        + b_prop
    )
    f += b"NFED" + struct.pack("<I", size_of_defn_atom)
    f += b"TRET" + struct.pack("<I", 8 + len(b_tert)) + b_tert
    f += (
        b"TJBO"
        + struct.pack("<I", 8)
        + b"YLOP"
        + struct.pack("<I", 8)
        + b"WTEN"
        + struct.pack("<I", 8)
    )
    f += b"NMED" + struct.pack("<I", 8 + len(b_demn)) + b_demn
    f += b"DOEG" + struct.pack("<I", size_of_geod_atom)
    for k in range(dsf_pool_nbr):
        if dsf_pool_length[k] == 0:
            continue
        f += b"LOOP" + struct.pack(
            "<I", 13 + dsf_pool_plane[k] + 2 * dsf_pool_plane[k] * dsf_pool_length[k]
        )
        f += struct.pack("<I", dsf_pool_length[k]) + struct.pack("<B", dsf_pool_plane[k])
        for plane in range(dsf_pool_plane[k]):
            f += struct.pack("<B", 0)
            for m in range(dsf_pool_length[k]):
                f += struct.pack("<H", dsf_pools[k][dsf_pool_plane[k] * m + plane])
    for k in range(dsf_pool_nbr):
        if dsf_pool_length[k] == 0:
            continue
        f += b"LACS" + struct.pack("<I", 8 + 8 * dsf_pool_plane[k])
        for plane in range(2 * dsf_pool_plane[k]):
            f += struct.pack("<f", pool_param[k % pool_nbr][plane])
    dico_new_dsf_pool = {}
    new_idx_dsfpool = 0
    for k in range(dsf_pool_nbr):
        if dsf_pool_length[k] != 0:
            dico_new_dsf_pool[k] = new_idx_dsfpool
            new_idx_dsfpool += 1
    size_of_cmds_atom = 8
    for terrain_idx in textured_tris:
        if len(textured_tris[terrain_idx]) == 0:
            continue
        size_of_cmds_atom += 3
        for idx_dsfpool in textured_tris[terrain_idx]:
            n = len(textured_tris[terrain_idx][idx_dsfpool])
            if idx_dsfpool != "cross-pool":
                size_of_cmds_atom += 13 + 2 * (n + ceil(n / 255))
            else:
                size_of_cmds_atom += 13 + 2 * (n + ceil(n / 510))
    f += b"SDMC" + struct.pack("<I", size_of_cmds_atom)
    for terrain_idx in textured_tris:
        if len(textured_tris[terrain_idx]) == 0:
            continue
        f += struct.pack("<B", 4) + struct.pack("<H", terrain_idx)
        flag = 1 if terrain_idx not in overlay_terrains else 2
        lod = -1 if flag == 1 else tile.overlay_lod
        for idx_dsfpool in textured_tris[terrain_idx]:
            tl = textured_tris[terrain_idx][idx_dsfpool]
            if idx_dsfpool != "cross-pool":
                f += struct.pack("<B", 1) + struct.pack("<H", dico_new_dsf_pool[idx_dsfpool])
                f += (
                    struct.pack("<B", 18)
                    + struct.pack("<B", flag)
                    + struct.pack("<f", 0)
                    + struct.pack("<f", lod)
                )
                blocks = floor(len(tl) / 255)
                for j in range(blocks):
                    f += struct.pack("<B", 23) + struct.pack("<B", 255)
                    for k in range(255):
                        f += struct.pack("<H", tl[255 * j + k])
                remaining = len(tl) % 255
                if remaining != 0:
                    f += struct.pack("<B", 23) + struct.pack("<B", remaining)
                    for k in range(remaining):
                        f += struct.pack("<H", tl[255 * blocks + k])
            else:
                f += struct.pack("<B", 1) + struct.pack("<H", dico_new_dsf_pool[tl[0]])
                f += (
                    struct.pack("<B", 18)
                    + struct.pack("<B", flag)
                    + struct.pack("<f", 0)
                    + struct.pack("<f", lod)
                )
                blocks = floor(len(tl) / 510)
                for j in range(blocks):
                    f += struct.pack("<B", 24) + struct.pack("<B", 255)
                    for k in range(255):
                        f += struct.pack("<H", dico_new_dsf_pool[tl[510 * j + 2 * k]])
                        f += struct.pack("<H", tl[510 * j + 2 * k + 1])
                remaining = int((len(tl) % 510) / 2)
                if remaining != 0:
                    f += struct.pack("<B", 24) + struct.pack("<B", remaining)
                    for k in range(remaining):
                        f += struct.pack("<H", dico_new_dsf_pool[tl[510 * blocks + 2 * k]])
                        f += struct.pack("<H", tl[510 * blocks + 2 * k + 1])
    if b_dems != b"":
        f += b"SMED" + struct.pack("<I", 8 + len(b_dems)) + b_dems
    f += hashlib.md5(bytes(f)).digest()
    return bytes(f), ter_files


# ------------------------------------------------------------------ helpers for the comparisons


def reference_tile(params: DsfParams, tile: TileRef = TILE, **overrides):
    ns = SimpleNamespace(lat=tile.lat, lon=tile.lon, **params.model_dump())
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def reference_needs_mask(lookup, mask_zl):  # O4_Mask_Utils.py:38-60
    def fn(til_x_left, til_y_top, zl, provider):
        if int(zl) < mask_zl:
            return False
        factor = 2 ** (zl - mask_zl)
        m_til_x = (int(til_x_left / factor) // 16) * 16
        m_til_y = (int(til_y_top / factor) // 16) * 16
        rx = int((til_x_left - factor * m_til_x) / 16)
        ry = int((til_y_top - factor * m_til_y) / 16)
        path = lookup(m_til_x, m_til_y)
        if path is None:
            return False
        big = Image.open(path)
        x0 = int(rx * 4096 / factor)
        y0 = int(ry * 4096 / factor)
        small = np.array(
            big.crop((x0, y0, x0 + 4096 // factor, y0 + 4096 // factor)), dtype=np.uint8
        )
        return small.max() > 30

    return fn


def reference_node_bathy(lookup_dist, mask_zl):  # O4_Bathymetry.py:187-223
    def fn(nbr_nodes, node_coords, node_types):
        node_bathy = 255 * np.ones(nbr_nodes, dtype=np.uint8)
        if lookup_dist is None:
            return node_bathy
        for n in range(nbr_nodes):
            if node_types[n] & 4 == 0:
                continue
            lon, lat = node_coords[5 * n], node_coords[5 * n + 1]
            attr = _wgs84_to_orthogrid(lat, lon, mask_zl)
            path = lookup_dist(*attr)
            if path is None:
                continue
            mask_val = np.array(Image.open(path), dtype=np.uint8)
            (s, t) = st_coord(lat, lon, attr[0], attr[1], mask_zl)
            node_bathy[n] = mask_val[int((1 - t) * 4095), int(s * 4095)]
        return node_bathy

    return fn


def compare_with_reference(
    mesh: SyntheticMesh,
    params: DsfParams,
    *,
    masks=None,
    dist=None,
    airports=(),
    existing=(),
    quad_capacity=None,
):
    """Bytes of ``build_dsf`` and of the transcription for the same inputs."""
    tile_ns = reference_tile(params, quad_capacity=quad_capacity)
    dico_airports = {
        f"A{i}": {"key_type": "icao" if a.is_icao else "other", "boundary": _box(a)}
        for i, a in enumerate(airports)
    }
    dico = reference_zone_dico(
        tile_ns, dico_airports, [f"{t.til_y}_{t.til_x}_{t.provider}{t.zl}.dds" for t in existing]
    )
    ref, ref_ter = reference_build_dsf(
        tile_ns,
        2.0,
        mesh.coords.ravel().copy(),
        mesh.tris.ravel().astype(np.uint32),
        mesh.attr.astype(np.uint32).copy(),
        dico,
        reference_needs_mask(masks, params.mask_zl) if masks else (lambda *a: False),
        reference_node_bathy(dist, params.mask_zl),
        b"",
        b"",
    )
    out = build_dsf(
        TILE,
        mesh.mesh_data(),
        masks,
        params,
        None,
        creation_agent="Ortho4XP",  # what the transcription writes
        distance_masks=dist,
        airports=airports,
        existing_textures=existing,
        _quad_capacity=quad_capacity,
    )
    return ref, ref_ter, out


def _box(a: AirportCover):
    from shapely.geometry import box

    return box(a.xmin, a.ymin, a.xmax, a.ymax)


# ------------------------------------------------------------------ tests


@pytest.mark.parametrize("seed", range(6))
def test_recut_matches_the_transcription(seed: int) -> None:
    m = synthetic_mesh(seed, nx=8, ny=6)
    tri_types = remap_tri_types(m.attr, 2.0, False)
    ours = recut_water_tris(m.coords, m.tris, tri_types)
    n, coords, types, coast, t, tris, ttypes = reference_recut(
        m.coords.ravel().copy(),
        m.tris.ravel().astype(np.uint32),
        tri_types.astype(np.uint32).copy(),
    )
    assert ours.n_nodes == n and ours.n_tris == t
    assert np.array_equal(ours.coords.ravel(), coords)
    assert np.array_equal(ours.node_types, types)
    assert np.array_equal(ours.node_is_coast, coast)
    assert np.array_equal(ours.tris.ravel(), tris.astype(np.int64))
    assert np.array_equal(ours.tri_types, ttypes.astype(np.uint8))
    assert t > len(m.tris)  # the synthetic coast actually gets cut


@pytest.mark.parametrize("seed,capacity", [(0, 5), (1, 8), (2, 3), (3, 20), (4, 50), (5, 2)])
def test_quadtree_order_matches_the_transcription(seed: int, capacity: int) -> None:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(20, 400))
    # clustered points so that several levels split, plus exact duplicates and the x >= 1 case
    centers = rng.random((4, 2))
    pts = np.concatenate([c + rng.normal(0, 0.03, (n // 4, 2)) for c in centers])
    pts = np.clip(pts, 0, 1)
    pts[: n // 20] = pts[n // 20 : 2 * (n // 20)]
    pts[-1] = (1.0, 0.5)
    qx, qy = quantize24(pts[:, 0]), quantize24(pts[:, 1])
    part = partition(qx, qy, capacity=capacity)
    reference = ReferenceQuadTree(3, capacity)
    for x, y in pts:
        reference.insert(float2qquad(x), float2qquad(y), 3)
    reference.clean()
    keys = list(reference.keys())
    assert part.n_pools == len(keys)
    for pool, key in enumerate(keys):
        level = len(key[0])
        assert part.level[pool] == level
        assert part.key_x[pool] == int(key[0], 2) and part.key_y[pool] == int(key[1], 2)
        nodes = sorted(reference[key]["idx_nodes"])
        assert np.array_equal(np.flatnonzero(part.node_bucket == pool), np.array(nodes))
        for idx in nodes:
            assert part.ix[idx] == int(reference.nodes[idx][0][level : level + 16], 2)
            assert part.iy[idx] == int(reference.nodes[idx][1][level : level + 16], 2)


def test_zone_map_matches_the_transcription(tmp_path: Path) -> None:
    zones = [
        ([43.2, 5.1, 43.2, 5.4, 43.5, 5.4, 43.5, 5.1, 43.2, 5.1], 17, "BI"),
        ([43.3, 5.3, 43.3, 5.9, 43.9, 5.9, 43.9, 5.3, 43.3, 5.3], 15, "Arc"),
        ([43.05, 5.05, 43.05, 5.2, 43.15, 5.2, 43.15, 5.05, 43.05, 5.05], 18, "BI"),
    ]
    airports = [
        AirportCover(0.62, 0.61, 0.64, 0.625, True),
        AirportCover(0.2, 0.8, 0.22, 0.81, False),
    ]
    for mode in ("False", "True", "ICAO"):
        params = DsfParams(
            mesh_zl=16,
            default_zl=14,
            default_website="BI",
            zone_list=zones,
            cover_airports_with_highres=mode,
            cover_zl=17,
            cover_extent=0.7,
        )
        tmap = texture_map(TILE, params, airports=airports)
        dico = reference_zone_dico(
            reference_tile(params),
            {
                "LFA": {"key_type": "icao", "boundary": _box(airports[0])},
                "X": {"key_type": "other", "boundary": _box(airports[1])},
            },
            [],
        )
        for (til_x, til_y), (tx, ty, zl, prov) in dico.items():
            ours = tmap.lookup(np.array([til_x]), np.array([til_y]))
            assert (
                int(ours[0][0]),
                int(ours[1][0]),
                int(ours[2][0]),
                tmap.providers[int(ours[3][0])],
            ) == (tx, ty, zl, prov), (til_x, til_y, mode)
    existing = [
        TextureId(8448, 5984, 15, "BI"),
        TextureId(33920, 24064, 16, "Arc"),
        TextureId(8416, 5952, 13, "BI"),
    ]
    params = DsfParams(
        mesh_zl=16, default_zl=14, zone_list=zones, cover_airports_with_highres="Existing"
    )
    tmap = texture_map(TILE, params, existing_textures=existing)
    dico = reference_zone_dico(
        reference_tile(params),
        {},
        [f"{t.til_y}_{t.til_x}_{t.provider}{t.zl}.dds" for t in existing],
    )
    ny, nx = tmap.shape
    for (til_x, til_y), (tx, ty, zl, prov) in dico.items():
        if til_x % 16 or til_y % 16:
            continue
        col, row = (til_x - tmap.til_x_min) // 16, (til_y - tmap.til_y_min) // 16
        if not (0 <= col < nx and 0 <= row < ny):
            continue
        assert (
            int(tmap.tex_x[row, col]),
            int(tmap.tex_y[row, col]),
            int(tmap.zl[row, col]),
            tmap.providers[int(tmap.provider_idx[row, col])],
        ) == (tx, ty, zl, prov)


def _params(**kw: object) -> DsfParams:
    base: dict[str, object] = {
        "mesh_zl": 16,
        "default_zl": 14,
        "mask_zl": 14,
        "default_website": "BI",
    }
    return DsfParams.model_validate({**base, **kw})


@pytest.mark.parametrize(
    "seed,kw",
    [
        (0, {}),
        (1, {"water_tech": "XP12"}),
        (2, {"imprint_masks_to_dds": False}),
        (3, {"use_masks_for_inland": True, "ratio_water": 0.4, "overlay_lod": 12000.0}),
        (4, {"water_tech": "XP12", "ratio_bathy": 0.3, "normal_map_strength": 0.5}),
        (
            5,
            {
                "zone_list": [
                    ([43.6, 5.05, 43.6, 5.2, 43.9, 5.2, 43.9, 5.05, 43.6, 5.05], 15, "Arc")
                ]
            },
        ),
    ],
)
def test_build_dsf_matches_the_transcription(tmp_path: Path, seed: int, kw: dict) -> None:
    params = _params(**kw)
    mesh = synthetic_mesh(seed, nx=7, ny=6)
    # the mesh spans ZL14 cells (8416|8432, 5952|5968); its sea third is in the 8416 column
    masks = masks_for(tmp_path, {(8416, 5952): (1500, 1200, 3000, 3200), (8416, 5968): None})
    dist = (
        masks_for(tmp_path / "dist", {(8416, 5952): "gradient", (8416, 5968): "gradient"})
        if seed % 2
        else None
    )
    ref, ref_ter, out = compare_with_reference(
        mesh, params, masks=masks, dist=dist, quad_capacity=40
    )
    assert out.ter_files == ref_ter
    assert out.data == ref
    assert out.stats["tris"] > len(mesh.tris)
    assert any(t.kind.tri_type == 2 for t in out.terrains)  # the masked-sea path is exercised
    if params.use_masks_for_inland:  # inland water counts as sea: no inland overlay
        assert not any(t.kind is TerKind.WATER_OVERLAY for t in out.terrains)
    else:
        assert any(t.kind is TerKind.WATER_OVERLAY for t in out.terrains)
    if params.water_tech == "XP12" and params.imprint_masks_to_dds:
        assert any(t.kind is TerKind.SEA for t in out.terrains)
    else:
        assert any(t.kind is TerKind.SEA_OVERLAY for t in out.terrains)


def test_build_dsf_without_masks_and_with_airport_upgrade(tmp_path: Path) -> None:
    params = _params(cover_airports_with_highres="True", cover_zl=16, cover_extent=0.5)
    mesh = synthetic_mesh(7, nx=6, ny=6)
    airports = [AirportCover(0.1, 0.7, 0.14, 0.72, True)]
    ref, ref_ter, out = compare_with_reference(mesh, params, airports=airports, quad_capacity=25)
    assert out.data == ref and out.ter_files == ref_ter
    assert any(t.texture.zl == 16 for t in out.textures)
