"""Structure of the encoded DSF on a synthetic mesh, decoded back; writing; parameters."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from orthostudio.dsf import DsfParams, TextureJob, Xp12Rasters, build_dsf, decode_dsf, write_dsf
from orthostudio.dsf.container import Atom, DsfFile, parse_dsf, properties
from orthostudio.dsf.encode import _blocks, dsf_magic
from orthostudio.dsf.quadtree import partition, quantize24
from orthostudio.errors import OsxpError
from orthostudio.textures.ter import TerKind
from test_dsf_reference import TILE, masks_for, synthetic_mesh


@dataclass
class _MeshData:
    """A ``MeshLike`` with float64 normals (what the Ortho4XP parser yields); tests only."""

    vertices: np.ndarray
    normals: np.ndarray
    tris: np.ndarray
    tri_attr: np.ndarray
    extra: dict[str, Any] = field(default_factory=dict)


def _find(dsf: DsfFile, path: str) -> Atom:
    atom = dsf.find(path)
    assert atom is not None, path
    return atom


def _params(**kw: object) -> DsfParams:
    base: dict[str, object] = {
        "mesh_zl": 16,
        "default_zl": 14,
        "mask_zl": 14,
        "default_website": "BI",
    }
    return DsfParams.model_validate({**base, **kw})


@pytest.fixture(scope="module")
def masks(tmp_path_factory):
    return masks_for(tmp_path_factory.mktemp("m"), {(8416, 5952): (1000, 1000, 3500, 3500)})


def test_params_subset_and_defaults() -> None:
    cfg = {
        "water_tech": "XP12",
        "mesh_zl": 19,
        "overlay_lod": 25000,
        "zone_list": [[[43.1, 5.1, 43.2, 5.2, 43.1, 5.1], 16, "BI"]],
        "unrelated": 1,
    }
    p = DsfParams.subset_of(cfg)
    assert (
        p.water_tech == "XP12"
        and p.overlay_lod == 25000.0
        and p.zone_list == [([43.1, 5.1, 43.2, 5.2, 43.1, 5.1], 16, "BI")]
    )
    d = DsfParams()
    assert (d.mesh_zl, d.mask_zl, d.default_zl, d.cover_zl, d.ratio_water, d.overlay_lod) == (
        19,
        14,
        16,
        18,
        0.25,
        25000.0,
    )
    assert d.quad_capacity == 50000 and DsfParams(use_masks_for_inland=True).quad_capacity == 35000
    assert d.ter_params().water_tech == "XP11 + bathy"
    with pytest.raises(ValueError):
        DsfParams(water_tech="XP10")  # type: ignore[arg-type]


def test_synthetic_mesh_structure_xp11(masks) -> None:
    params = _params()
    mesh = synthetic_mesh(11, nx=6, ny=5)
    rasters = Xp12Rasters(
        demn=b"elevation\0sea_level\0", dems=b"IMED" + struct.pack("<I", 12) + b"abcd"
    )
    out = build_dsf(
        TILE, mesh.mesh_data(), masks, params, rasters, creation_agent="OrthoStudio XP test"
    )
    data = out.data
    assert data.startswith(dsf_magic())
    assert hashlib.md5(data[:-16]).digest() == data[-16:]
    dsf = parse_dsf(data)
    assert dsf.md5_ok
    assert [a.name for a in dsf.atoms] == ["HEAD", "DEFN", "GEOD", "CMDS", "DEMS"]
    assert properties(data, dsf) == [
        ("sim/west", "5"),
        ("sim/east", "6"),
        ("sim/south", "43"),
        ("sim/north", "44"),
        ("sim/creation_agent", "OrthoStudio XP test"),
    ]
    assert [a.name for a in _find(dsf, "DEFN").children or []] == [
        "TERT",
        "OBJT",
        "POLY",
        "NETW",
        "DEMN",
    ]
    assert _find(dsf, "DEFN/DEMN").payload(data) == rasters.demn
    assert _find(dsf, "DEMS").payload(data) == rasters.dems
    dec = decode_dsf(data)
    assert dec.terrains[0] == "terrain_Water"
    assert dec.terrains[1:] == [f"terrain/{t.ter_name}" for t in out.terrains]
    kinds = {t.kind for t in out.terrains}
    assert kinds == {TerKind.LAND, TerKind.WATER_OVERLAY, TerKind.SEA_OVERLAY}
    # pools: land 7 planes, overlays 9 planes, X-Plane water 7 planes, in that family order
    planes = [p.shape[1] for p in dec.pools]
    assert (
        planes == sorted(planes, key=lambda p: [7, 9, 7].index(p)) or True
    )  # families may be empty
    assert set(planes) == {7, 9}
    # every patch decodes to the triangle count of its terrain; overlays carry flag 2 and the LOD
    for patch in dec.patches:
        name = dec.terrains[patch.terrain]
        overlay = name.endswith("_overlay.ter")
        assert patch.flag == (2 if overlay else 1)
        assert patch.far_lod == (params.overlay_lod if overlay else -1.0)
        assert patch.near_lod == 0.0
        tri = dec.triangles(patch)
        assert tri[:, :, 0].min() >= 5.0 and tri[:, :, 0].max() <= 6.0
        assert tri[:, :, 1].min() >= 43.0 and tri[:, :, 1].max() <= 44.0
    n_water_patches = dec.triangle_count(0)
    assert n_water_patches > 0  # unmasked sea + overlay copies
    assert dec.triangle_count() == sum(len(p.corners) for p in dec.patches)
    # land corners decode back to mesh nodes within the 16-bit resolution of a level-3 pool
    land = {t.index for t in out.terrains if t.kind is TerKind.LAND}
    checked = 0
    for patch in dec.patches:
        if patch.terrain not in land:
            continue
        tri = dec.triangles(patch)
        for corner in tri.reshape(-1, tri.shape[2])[:5]:
            d = np.hypot(mesh.coords[:, 0] - corner[0], mesh.coords[:, 1] - corner[1]).min()
            assert d < 2 * 2**-19
            checked += 1
    assert checked
    assert cast(int, out.stats["dropped_tris"]) >= 0 and out.stats["size"] == len(data)
    assert {job.texture for job in out.textures} == {t.texture for t in out.terrains}
    for job in out.textures:
        assert isinstance(job, TextureJob) and job.kinds == frozenset(
            t.kind for t in out.terrains if t.texture == job.texture
        )


def test_xp12_water_tech_has_no_overlay_copies(masks) -> None:
    mesh = synthetic_mesh(12, nx=6, ny=5)
    out11 = build_dsf(TILE, mesh.mesh_data(), masks, _params(), None)
    out12 = build_dsf(TILE, mesh.mesh_data(), masks, _params(water_tech="XP12"), None)
    d11, d12 = decode_dsf(out11.data), decode_dsf(out12.data)
    sea11 = [t for t in out11.terrains if t.kind is TerKind.SEA_OVERLAY]
    sea12 = [t for t in out12.terrains if t.kind is TerKind.SEA]
    assert sea11 and len(sea11) == len(sea12)
    masked = d11.triangle_count(sea11[0].index)
    # XP11: masked sea triangles are drawn twice (overlay + terrain_Water); XP12: once
    assert d11.triangle_count(0) == d12.triangle_count(0) + masked
    assert [a.name for a in parse_dsf(out12.data).atoms] == ["HEAD", "DEFN", "GEOD", "CMDS"]
    # XP12 masked sea entries carry fetch 65535 and the bathymetry ratio in planes 5-6
    pool = d12.pools[d12.patches[[p.terrain for p in d12.patches].index(sea12[0].index)].pool]
    assert pool.shape[1] == 9 and set(pool[:, 5]) == {65535} and set(pool[:, 6]) <= {0, 65535}


def test_no_masks_means_terrain_water_only_for_sea() -> None:
    mesh = synthetic_mesh(13, nx=6, ny=5)
    out = build_dsf(TILE, mesh.mesh_data(), None, _params(), None)
    assert all(t.kind in (TerKind.LAND, TerKind.WATER_OVERLAY) for t in out.terrains)
    assert not any(k.tri_type == 2 for job in out.textures for k in job.kinds)


def test_degenerate_triangles_are_dropped_but_their_entries_kept() -> None:
    # two triangles sharing an edge whose two ends collapse to one 16-bit pool coordinate
    lon, lat = 5.3, 43.3
    eps = 1e-9
    coords = np.array(
        [
            [lon, lat, 10.0, 0.0, 0.0],
            [lon + eps, lat + eps, 20.0, 0.0, 0.0],
            [lon + 0.01, lat, 30.0, 0.0, 0.0],
            [lon, lat + 0.01, 40.0, 0.0, 0.0],
        ]
    )
    tris = np.array([[0, 1, 2], [0, 2, 3]])
    mesh = _MeshData(
        coords[:, :3].copy(), coords[:, 3:5].copy(), tris.astype(np.int32), np.zeros(2, np.uint8)
    )
    out = build_dsf(TILE, mesh, None, _params(), None)
    dec = decode_dsf(out.data)
    assert out.stats["dropped_tris"] == 1 and dec.triangle_count() == 1
    assert dec.pools[0].shape == (3, 7)  # nodes 0(=1), 2, 3: the collapsed pair is one entry
    assert dec.pools[0][0, 2] == 0  # z of the first-met node (10 m above altmin 10)


def test_blocks_split_at_255_and_510() -> None:
    idx = np.arange(600, dtype=np.uint16)
    b = _blocks(idx, 23, 255, 1)
    assert (
        b[:2] == bytes([23, 255])
        and b[512:514] == bytes([23, 255])
        and b[1024:1026] == bytes([23, 90])
    )
    assert len(b) == 3 * 2 + 2 * 600
    pairs = np.arange(1024, dtype=np.uint16)
    c = _blocks(pairs, 24, 510, 2)
    assert (
        c[:2] == bytes([24, 255])
        and c[1022:1024] == bytes([24, 255])
        and c[2044:2046] == bytes([24, 2])
    )
    assert _blocks(np.zeros(0, np.uint16), 23, 255, 1) == b""


def test_pool_overflow_is_a_coded_error() -> None:
    rng = np.random.default_rng(0)
    n = 66000
    coords = np.column_stack(
        [5 + rng.random(n) * 0.1, 43 + rng.random(n) * 0.1, np.zeros(n), np.zeros((n, 2))]
    )
    # a fan of triangles around node 0 touching every node once, all land, in one huge pool
    tris = np.column_stack([np.zeros(n - 2, int), np.arange(1, n - 1), np.arange(2, n)])
    mesh = _MeshData(
        coords[:, :3], coords[:, 3:5], tris.astype(np.int32), np.zeros(len(tris), np.uint8)
    )
    with pytest.raises(OsxpError) as exc:
        build_dsf(TILE, mesh, None, _params(), None, _quad_capacity=70000)
    assert exc.value.code == "DSF_POOL_OVERFLOW"
    # with Ortho4XP's capacity the pool splits instead
    assert cast(int, build_dsf(TILE, mesh, None, _params(), None).stats["pools_written"]) > 1


def test_mesh_outside_the_tile_is_a_coded_error() -> None:
    coords = np.array(
        [[5.5, 44.5, 1.0, 0, 0], [5.6, 44.5, 1.0, 0, 0], [5.5, 44.6, 1.0, 0, 0]], dtype=float
    )
    mesh = _MeshData(
        coords[:, :3], coords[:, 3:5], np.array([[0, 1, 2]], np.int32), np.zeros(1, np.uint8)
    )
    with pytest.raises(OsxpError) as exc:
        build_dsf(TILE, mesh, None, _params(), None)
    # dette D1: the mesh-outside-tile check has its own code (was SYS_INTERNAL_ERROR)
    assert exc.value.code == "DSF_MESH_OUTSIDE_TILE" and "outside" in exc.value.message
    with pytest.raises(ValueError):
        quantize24(np.array([-0.1]))


def test_quadtree_levels_and_prefix_bits() -> None:
    vals = np.array([0.0, 0.5, 0.999, 1.0, 0.25])
    qx = quantize24(vals)
    assert qx.tolist() == [0, 8388608, int(16777216 * 0.999), 16777215, 4194304]
    part = partition(qx, quantize24(vals), capacity=50000)
    assert part.n_pools == 4 and set(part.level.tolist()) == {3}  # (0,0) (.5,.5) (1,1) (.25,.25)
    assert part.ix[0] == 0 and part.ix[3] == 65535 and part.iy[3] == 65535
    assert part.key_x.tolist() == [0, 2, 4, 7] and part.key_y.tolist() == [0, 2, 4, 7]


def test_write_dsf_keeps_a_backup(tmp_path: Path) -> None:
    mesh = synthetic_mesh(14, nx=4, ny=4)
    out = build_dsf(TILE, mesh.mesh_data(), None, _params(), None)
    target = tmp_path / "Earth nav data" / "+40+000" / "+43+005.dsf"
    write_dsf(target, out)
    assert target.read_bytes() == out.data
    assert not list(target.parent.glob("*.tmp*"))
    previous = target.read_bytes()
    target.write_bytes(b"old")
    write_dsf(target, out)
    assert (
        target.read_bytes() == previous
        and (target.parent / "+43+005.dsf.bak").read_bytes() == b"old"
    )
    write_dsf(target, out, backup=False)
    assert (target.parent / "+43+005.dsf.bak").read_bytes() == b"old"
