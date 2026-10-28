# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Graph declaration and node rules of ``osxp build`` (spec ``pipeline-build.md`` 2, 6): no
network, no Ortho4XP. Sharing across zoom levels and tiles, neighbour wiring, ``#2`` suffixes,
consumed subsets, and a synthetic DSF + pack run through the scheduler in thread mode."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from orthostudio.dsf.xp12 import DEMO_AREAS
from orthostudio.errors import OsxpError
from orthostudio.graph import Store
from orthostudio.imagery.providers import load_registry
from orthostudio.install import packs as install_packs
from orthostudio.masks.rule import MasksParams as OsxpMasksParams
from orthostudio.mesh.mesh_file import MeshData, write_mesh_npz, write_mesh_text
from orthostudio.mesh.rule import OSXP_MESH
from orthostudio.mesh.rule import MeshParams as OsxpMeshParams
from orthostudio.model import ArtifactRef, TileRef
from orthostudio.overlays import OverlayExclusions
from orthostudio.pipeline.build import (
    TEXTURES_JSON,
    TILE_DSF,
    BuildEnv,
    BuildSpec,
    OverlayParams,
    TileDsfParams,
    TileTexturesParams,
    _masks_ram_mb,
    declare,
    make_scheduler,
    road_points,
    source_ref,
    stored_neighbour_mesh,
    texture_jobs_from_json,
)
from orthostudio.pipeline.pack import (
    TILE_PACK,
    PackManifest,
    PackParams,
    pack_dir_name,
    pack_env,
    pack_is_intact,
)
from orthostudio.pipeline.pack import PackEnv as _PackEnv
from orthostudio.sched import Done, Node, Scheduler
from orthostudio.sched.node import run_p0_rule

T = TileRef(43, 5)
W = TileRef(43, 4)
N = TileRef(44, 5)


@pytest.fixture
def global_scenery(tmp_path: Path) -> Path:
    gs = tmp_path / "Global Scenery"
    for tile in (T, W, N):
        p = gs / tile.dsf_relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"XPLNEDSF" + tile.name.encode())
    return gs


@pytest.fixture
def env(tmp_path: Path, global_scenery: Path) -> BuildEnv:
    store = Store(tmp_path / "store", fsync=False)
    return BuildEnv(
        store=store,
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        global_scenery=global_scenery,
        dsftool=None,
        registry=load_registry(),
        workers=2,
        library_path=tmp_path / "library.sqlite",
    )


def _spec(tmp_path: Path, tile: TileRef = T, zl: int = 14, **kw) -> BuildSpec:
    kw.setdefault("out_dir", tmp_path / "out")
    return BuildSpec(
        tile=tile,
        provider="BI",
        zl=zl,
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        **kw,
    )


def _declare(*args, **kw):
    """``declare`` as an estimate calls it: the tiles' OSM layers are still to fetch."""
    return declare(*args, planning=True, **kw)


def _sched(env: BuildEnv) -> Scheduler:
    return make_scheduler(env.store, env, cpu_workers=1, cpu_in_threads=True)


def test_ids_and_sharing_across_zoom_levels(tmp_path: Path, env: BuildEnv) -> None:
    sched = _sched(env)
    # two levels of one tile need two output directories (the pack is named by the tile)
    specs = [_spec(tmp_path, zl=14), _spec(tmp_path, zl=15, out_dir=tmp_path / "out15")]
    g14, g15 = _declare(specs, sched, env)
    assert g14.vectors.id == "+43+005/vectors" and g14.dsf.id == "+43+005/BI14/dsf"
    assert g15.dsf.id == "+43+005/BI15/dsf" and g15.pack.id == "+43+005/BI15/pack"
    shared = ("dem", "vectors", "coastline", "mesh", "masks", "xp12", "overlay")
    for role in shared:
        assert g14.by_role[role] is g15.by_role[role], role
    for role in ("dsf", "textures", "pack"):
        assert g14.by_role[role] is not g15.by_role[role], role
    assert g14.install is None and g14.target is g14.pack
    ids = {n.id for n in g14.all()} | {n.id for n in g15.all()}
    assert len(ids) == len(shared) + 3 + 3 and set(sched.nodes) == ids
    assert g14.dsf.kind == "cpu" and g14.textures.kind == "net" and g14.pack.kind == "io"
    assert g14.vectors.kind == "subprocess" and g14.overlay is not None
    assert g14.overlay.kind == "subprocess" and g14.xp12 is not None and g14.xp12.kind == "io"


def test_neighbours_in_batch_and_in_store(tmp_path: Path, env: BuildEnv) -> None:
    # a mesh artefact of the northern neighbour already in the store
    key = "ab" * 32
    with env.store.begin(OSXP_MESH.name, key, "dir") as build:
        (build.out / f"Data{N.name}.mesh").write_text("MeshVersionFormatted 2\n")
        build.commit(version=1, recipe='{"format":"osxp-key-1"}', inputs=[])
    stored = stored_neighbour_mesh(env.store, N)
    assert stored is not None and stored.key == key
    assert stored_neighbour_mesh(env.store, TileRef(42, 5)) is None

    sched = _sched(env)
    g_t, g_w = _declare([_spec(tmp_path), _spec(tmp_path, tile=W)], sched, env)
    masks_inputs = g_t.masks.inputs
    assert masks_inputs["nb_w"] is g_w.mesh  # in the batch: the node
    assert isinstance(masks_inputs["nb_n"], ArtifactRef) and masks_inputs["nb_n"].key == key
    assert masks_inputs["nb_s"] is None and masks_inputs["nb_e"] is None
    assert g_w.masks.inputs["nb_e"] is g_t.mesh
    # a neighbour whose stage failed in a first pass is not wired (second pass of build_tiles)
    sched2 = _sched(env)
    g_t2, _ = _declare([_spec(tmp_path), _spec(tmp_path, tile=W)], sched2, env, unavailable={W})
    assert g_t2.masks.inputs["nb_w"] is None
    assert isinstance(g_t2.masks.inputs["nb_n"], ArtifactRef)
    # the shared Global Scenery source is one ArtifactRef per tile, keyed by content
    assert g_t.xp12 is not None and g_t.overlay is not None
    assert g_t.xp12.inputs["source"] == g_t.overlay.inputs["source"]
    src = g_t.xp12.inputs["source"]
    assert isinstance(src, ArtifactRef) and src.key == src.digest and src.rule == "global_scenery"


def test_a_batch_reads_the_stored_meshes_and_x_plane_s_files_once(
    tmp_path: Path, env: BuildEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Plan of 60 tiles over a cache of 1 400 went through every stored mesh for each
    neighbour of each tile (5.4 s), and read X-Plane's scenery files again at each estimate (172
    of them, 1.1 GB, for 25 tiles): the meshes are read once a batch, and a scenery file's digest
    is kept while the file stays the same (2026-10-23)."""
    import orthostudio.graph.store as store_module

    walks: list[str] = []
    real_walk = Store.iter_made

    def counted(self: Store, rule: str):  # type: ignore[no-untyped-def]
        walks.append(rule)
        return real_walk(self, rule)

    hashed: list[Path] = []
    real_digest = store_module.digest_file

    def hashing(path):  # type: ignore[no-untyped-def]
        hashed.append(Path(path))
        return real_digest(path)

    monkeypatch.setattr(Store, "iter_made", counted)
    monkeypatch.setattr(store_module, "digest_file", hashing)
    tiles = [T, W, N]
    first = _declare([_spec(tmp_path, tile=t) for t in tiles], _sched(env), env)
    assert walks == [OSXP_MESH.name]  # once for the three tiles and their neighbours
    files = len(hashed)
    assert files > 0
    again = _declare([_spec(tmp_path, tile=t) for t in tiles], _sched(env), env)
    assert len(hashed) == files  # the same files: not read again
    assert [g.xp12.inputs["source"] for g in again] == [g.xp12.inputs["source"] for g in first]


def test_conflicting_params_get_a_suffix(tmp_path: Path, env: BuildEnv) -> None:
    sched = _sched(env)
    specs = [
        _spec(tmp_path),
        _spec(tmp_path, config={"masks_width": 200}, out_dir=tmp_path / "out-wide"),
    ]
    a, b = _declare(specs, sched, env)
    assert a.vectors is b.vectors and a.mesh is b.mesh  # masks_width is not theirs
    assert a.masks is not b.masks
    assert a.masks.id == "+43+005/masks" and b.masks.id == "+43+005/masks#2"
    assert b.dsf.id == "+43+005/BI14/dsf#2" and b.pack.id == "+43+005/BI14/pack#2"
    assert isinstance(b.masks.params, OsxpMasksParams)
    assert b.masks.params.masks_width == 200 and b.masks.params.tile == "+43+005"
    assert a.vectors.params.tile == "+43+005"


def test_same_tile_twice_into_one_out_dir_is_refused_unless_identical(
    tmp_path: Path, env: BuildEnv
) -> None:
    """P2a review: two levels (or two parameter sets) of a tile into one ``--out`` would
    write the same ``zOrthoStudio_<tile>/`` and wreck each other; ``declare`` refuses them.
    The very same spec twice is one pack node (no conflict)."""
    with pytest.raises(OsxpError) as exc:
        _declare([_spec(tmp_path, zl=14), _spec(tmp_path, zl=15)], _sched(env), env)
    assert exc.value.code == "CFG_VALUE_INVALID" and "BI15" in exc.value.message
    with pytest.raises(OsxpError):
        _declare([_spec(tmp_path), _spec(tmp_path, config={"masks_width": 200})], _sched(env), env)
    a, b = _declare([_spec(tmp_path), _spec(tmp_path)], _sched(env), env)
    assert a.pack is b.pack


def test_overlay_settings_reach_the_overlay_node(tmp_path: Path, env: BuildEnv) -> None:
    """The Ortho4XP application variables ``ovl_exclude_pol`` / ``ovl_exclude_net`` and
    OrthoStudio XP's ``keep_objects``: ``config`` or the spec fields, else the OrthoStudio XP
    defaults. An ``Ortho4XP.cfg`` lying around is never read (decision 0010)."""
    (g,) = _declare([_spec(tmp_path)], _sched(env), env)
    assert g.overlay is not None
    p = g.overlay.params
    assert isinstance(p, OverlayParams)
    assert p.ovl_exclude_net == [] and p.keep_objects is True
    assert p.ovl_exclude_pol == OverlayExclusions().ovl_exclude_pol
    spec = _spec(tmp_path, config={"ovl_exclude_net": [3], "keep_objects": False})
    spec.ovl_exclude_pol = [".for"]  # a spec field wins over config
    (g,) = _declare([spec], _sched(env), env)
    assert g.overlay is not None
    p = g.overlay.params
    assert isinstance(p, OverlayParams)
    assert p.ovl_exclude_pol == [".for"] and p.ovl_exclude_net == [3] and p.keep_objects is False
    with pytest.raises(OsxpError) as exc:
        _spec(tmp_path, config={"ovl_exclude_net": ["power"]}).tile_config()
    assert exc.value.code == "CFG_VALUE_INVALID"


def test_pack_params_carry_the_tile_cfg(tmp_path: Path, env: BuildEnv) -> None:
    """The pack writes ``Ortho4XP_<tile>.cfg`` (the 44 consumed values) like Ortho4XP did."""
    (g,) = _declare([_spec(tmp_path, config={"masks_width": 200})], _sched(env), env)
    params = g.pack.params
    assert isinstance(params, PackParams)
    assert "masks_width=200\n" in params.tile_cfg and "default_zl=14\n" in params.tile_cfg
    assert "ovl_exclude" not in params.tile_cfg


def test_the_decals_reach_the_pack_alone(tmp_path: Path, env: BuildEnv) -> None:
    """Turning decals on or off, putting them on the sea, or choosing another changes the pack's
    key and nothing upstream: the DSF and the textures hit and the terrain files alone are written
    again, setdecal's way (its author asked, 2026-09-25). A tile without decals keeps every key."""

    def keys(**config: object) -> dict[str, dict]:
        (g,) = _declare([_spec(tmp_path, config=config)], _sched(env), env)
        return {role: g.by_role[role].params.canonical() for role in ("dsf", "textures", "pack")}

    plain = keys()
    on = keys(use_decal_on_terrain=True)
    grass = keys(use_decal_on_terrain=True, decal="grass_and_stony_dirt_1.dcl")
    sea = keys(use_decal_on_terrain=True, decal_on_sea=True)
    for variant in (on, grass, sea):
        assert variant["dsf"] == plain["dsf"] and variant["textures"] == plain["textures"]
    assert plain["dsf"]["use_decal_on_terrain"] is False  # what every tile built before holds
    assert "decal" not in plain["pack"] and "decal_on_sea" not in plain["pack"]
    assert on["pack"]["decal"] == "maquify_2_green_key.dcl" and "decal_on_sea" not in on["pack"]
    assert grass["pack"]["decal"] == "grass_and_stony_dirt_1.dcl"
    assert sea["pack"]["decal_on_sea"] is True
    moved = {
        k for k in set(on["pack"]) | set(plain["pack"]) if on["pack"].get(k) != plain["pack"].get(k)
    }
    assert moved == {"decal", "tile_cfg"}  # tile_settings.cfg says use_decal_on_terrain=True
    assert keys(decal="grass_and_stony_dirt_1.dcl", decal_on_sea=True) == plain  # off: nothing
    with pytest.raises(OsxpError) as exc:
        keys(use_decal_on_terrain=True, decal="rail_dry_grd.dcl")  # setdecal's, gone from 12.4.4
    assert exc.value.code == "CFG_VALUE_INVALID"


def test_batch_fields_must_agree(tmp_path: Path) -> None:
    """``BuildEnv.create`` refuses specs that disagree on what is resolved once per batch."""
    a = _spec(tmp_path)
    b = _spec(tmp_path, tile=W, workers=3)
    with pytest.raises(ValueError, match="workers"):
        BuildEnv.create([a, b], store=Store(tmp_path / "store", fsync=False))


def test_the_stage_keys_carry_the_tile() -> None:
    a = OsxpMeshParams(tile="+43+005")
    b = OsxpMeshParams(tile="+43+004")
    assert a.canonical() != b.canonical()


def test_consumed_subsets() -> None:
    cfg = {
        "masks_width": 200,
        "curvature_tol": 3.0,
        "sea_texture_blur": 2.0,
        "default_zl": 16,
        "tile": "+43+005",
        "creation_agent": "osxp",
        "encoder": "ispc",
        "encoder_version": "1",
    }
    for model in (OsxpMeshParams, TileDsfParams, TileTexturesParams):
        assert "masks_width" not in model.model_fields, model
    for model in (OsxpMeshParams, OsxpMasksParams):
        assert "default_zl" not in model.model_fields, model
    assert "sea_texture_blur" not in TileDsfParams.model_fields
    assert "sea_texture_blur" in TileTexturesParams.model_fields
    assert TileDsfParams.subset_of(cfg).creation_agent == "osxp"
    assert "creation_agent" not in TileTexturesParams.model_fields
    assert OsxpMeshParams.subset_of(cfg).curvature_tol == 3.0
    assert OverlayParams.subset_of({"tile": "+43+005"}).ovl_exclude_pol == [
        "lib/g12/beaches.bch",
        "lib/g8/beaches.bch",
    ]
    # K2 through the keys: the DSF key ignores the imagery blur
    p1 = TileDsfParams.subset_of(cfg)
    p2 = TileDsfParams.subset_of({**cfg, "sea_texture_blur": 0.0})
    assert p1.canonical() == p2.canonical()


def test_missing_global_scenery_is_a_coded_error(tmp_path: Path, env: BuildEnv) -> None:
    sched = _sched(env)
    with pytest.raises(OsxpError) as exc:
        _declare([_spec(tmp_path, tile=TileRef(42, 5))], sched, env)
    assert exc.value.code == "DSF_GLOBAL_SCENERY_MISSING"
    assert not sched.nodes  # a failed declaration leaves the scheduler untouched
    (g,) = _declare(
        [_spec(tmp_path, tile=TileRef(42, 5), overlay=False, xp12_rasters=False)], sched, env
    )
    assert g.xp12 is None and g.overlay is None
    assert g.dsf.inputs["rasters"] is None and g.pack.inputs["overlay"] is None


def test_a_tile_of_x_planes_demo_areas_is_built_from_them(tmp_path: Path, env: BuildEnv) -> None:
    """The relief, the XP12 rasters and the overlay read a tile X-Plane 12 keeps in its Demo Areas
    only, beside the Global Scenery (Oahu, 2026-09-22), and a neighbour's relief there too."""
    oahu = TileRef(21, -158)
    assert env.global_scenery is not None
    demo = env.global_scenery.parent / DEMO_AREAS
    for tile in (oahu, oahu.neighbour(0, -1)):
        p = demo / tile.dsf_relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"XPLNEDSF" + tile.name.encode())
    (g,) = _declare([_spec(tmp_path, tile=oahu, relief="xplane")], _sched(env), env)
    dsf = demo / oahu.dsf_relpath
    assert g.xp12 is not None and g.xp12.inputs["source"].path == dsf
    assert g.overlay is not None and g.overlay.inputs["source"].path == dsf
    assert g.by_role["dem"].inputs["xp12"].path == dsf
    assert g.by_role["dem"].inputs["xp12_w"].path == demo / oahu.neighbour(0, -1).dsf_relpath


def test_install_needs_custom_scenery(tmp_path: Path, env: BuildEnv) -> None:
    with pytest.raises(OsxpError) as exc:
        _declare([_spec(tmp_path, install=True)], _sched(env), env)
    assert exc.value.code == "XP_DIR_NOT_FOUND"
    spec = _spec(tmp_path, install=True, custom_scenery=tmp_path / "cs")
    (g,) = _declare([spec], _sched(env), env)
    assert g.install is not None and g.target is g.install and g.install.inputs["pack"] is g.pack


def test_unknown_tile_parameter(tmp_path: Path) -> None:
    with pytest.raises(OsxpError) as exc:
        _spec(tmp_path, config={"nope": 1}).tile_config()
    assert exc.value.code == "CFG_VALUE_INVALID"
    cfg = _spec(tmp_path, config={"masks_width": 200}).tile_config()
    assert cfg["default_website"] == "BI" and cfg["default_zl"] == 14 and cfg["masks_width"] == 200


# -- synthetic run of the DSF and pack nodes ------------------------------------------------------


def _tiny_mesh() -> MeshData:
    lon, lat = 5.1, 43.1
    vertices = np.array(
        [
            [lon, lat, 10.0],
            [lon + 0.01, lat, 20.0],
            [lon + 0.01, lat + 0.01, 30.0],
            [lon, lat + 0.01, 40.0],
        ]
    )
    normals = np.zeros((4, 2), dtype=np.float32)
    tris = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    return MeshData(vertices, normals, tris, np.zeros(2, dtype=np.uint8))


def _artefact_dir(store: Store, rule: str, key: str, fill) -> ArtifactRef:
    with store.begin(rule, key, "dir") as build:
        fill(build.out)
        info, _ = build.commit(version=1, recipe='{"format":"osxp-key-1"}', inputs=[])
    return ArtifactRef(info.key, info.digest, info.path, info.rule, info.kind, info.size)


def _no_images(store: Store) -> ArtifactRef:
    """A textures folder with no image: a pack takes its images from a folder, and one missing
    stops it (``write_pack``), where it was taken for one with no image."""
    return _artefact_dir(store, "tile.textures", "3f" * 32, lambda out: (out / "textures").mkdir())


def _on_rounding_boundaries(count: int) -> list[float]:
    """Altitudes (m) that the DSF rounds one way at Triangle4XP's precision and the other once
    rounded as the text writes them: just off the middle of a step of a pool spanning under 770 m
    (``dsf.encode._pool_scales``: 85 steps a metre from the pool's floor, 0 here)."""
    found: list[float] = []
    for k in range(1, 10_000):
        for eps in (1e-11, -1e-11, 2e-11, -2e-11, 3e-11, -3e-11):
            z = (k + 0.5) / 85 + eps
            text = float("%.15f" % (z / 100000)) * 100000
            if np.round(z * 85) != np.round(text * 85):
                found.append(z)
                break
        if len(found) == count:
            return found
    raise AssertionError("no altitude on a boundary")


def test_the_dsf_of_a_mesh_kept_once_is_the_dsf_of_its_text(
    env: BuildEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mesh is kept as its npz alone since 2026-10-23, the text being the same mesh again
    (272 GB of a user's cache): the DSF reads the npz as the text would have given it, and comes
    out byte for byte as from the text. Read as it is, Triangle4XP's full precision changes the
    DSF wherever an altitude sits on a rounding boundary, as 23 bytes of +43+005's did
    (pipeline/build.py ``_tile_dsf``): the mesh here has some, and the npz read as it is gives
    another DSF."""
    import orthostudio.pipeline.build as build_mod

    store = env.store
    rng = np.random.default_rng(23)
    side = 40
    lon, lat = np.meshgrid(np.linspace(5.0, 6.0, side), np.linspace(43.0, 44.0, side))
    vertices = np.column_stack(
        [
            lon.ravel() + rng.uniform(-1e-9, 1e-9, side * side),
            lat.ravel() + rng.uniform(-1e-9, 1e-9, side * side),
            rng.uniform(1.0, 600.0, side * side) + rng.uniform(0, 1e-9, side * side),
        ]
    )
    boundary = _on_rounding_boundaries(12)
    vertices[side + 1 : side + 1 + len(boundary), 2] = boundary
    vertices[:, :2] = np.clip(vertices[:, :2], [5.0, 43.0], [6.0, 44.0])
    i = np.arange(side - 1)
    a_, b_ = np.meshgrid(i, i)
    corner = (b_ * side + a_).ravel()
    tris = np.vstack(
        [
            np.column_stack([corner, corner + 1, corner + side + 1]),
            np.column_stack([corner, corner + side + 1, corner + side]),
        ]
    ).astype(np.int32)
    normals = rng.uniform(-0.3, 0.3, (side * side, 2)).astype(np.float32)
    mesh = MeshData(vertices, normals, tris, np.zeros(len(tris), dtype=np.uint8))

    def with_text(out: Path) -> None:
        write_mesh_text(out / f"Data{T.name}.mesh", mesh)
        write_mesh_npz(out / "mesh.npz", mesh)

    def once(out: Path) -> None:
        write_mesh_npz(out / "mesh.npz", mesh)

    def once_again(out: Path) -> None:  # another digest, or its DSF is the one above
        once(out)
        (out / "stats.json").write_text("{}")

    def masks(out: Path) -> None:
        (out / "index.json").write_text(json.dumps({"format": "osxp-masks-1", "masks": []}))

    masks_ref = _artefact_dir(store, "orthostudio.masks", "44" * 32, masks)
    dsfs = []
    for key, fill in (("55" * 32, with_text), ("66" * 32, once), ("77" * 32, once_again)):
        if key == "77" * 32:  # the npz as it is: what the DSF read before
            monkeypatch.setattr(build_mod, "as_written", lambda mesh: mesh)
        mesh_ref = _artefact_dir(store, "orthostudio.mesh", key, fill)
        node = Node(
            f"{T.name}/BI14/dsf-{key[:2]}",
            TILE_DSF,
            TileDsfParams(tile=T.name, default_website="BI", default_zl=14),
            {"mesh": mesh_ref, "masks": masks_ref, "rasters": None, "vectors": None},
            kind="cpu",
        )
        sched = make_scheduler(store, env, cpu_workers=1, cpu_in_threads=True)
        sched.add(node)
        refs = asyncio.run(sched.run([node.id]))
        assert not sched.failed, sched.failed
        dsfs.append((refs[node.id].path / f"{T.name}.dsf").read_bytes())
    assert dsfs[0] == dsfs[1]
    assert dsfs[2] != dsfs[0]  # the test sees what full precision does


def test_dsf_and_pack_nodes_run_and_hit(tmp_path: Path, env: BuildEnv) -> None:
    store = env.store
    mesh = _tiny_mesh()

    def fill_mesh(out: Path) -> None:
        write_mesh_text(out / f"Data{T.name}.mesh", mesh)
        write_mesh_npz(out / "mesh.npz", mesh)

    def fill_masks(out: Path) -> None:
        (out / "index.json").write_text(json.dumps({"format": "osxp-masks-1", "masks": []}))

    mesh_ref = _artefact_dir(store, "orthostudio.mesh", "11" * 32, fill_mesh)
    masks_ref = _artefact_dir(store, "orthostudio.masks", "22" * 32, fill_masks)
    dsf_params = TileDsfParams(tile=T.name, default_website="BI", default_zl=14)
    dsf = Node(
        "+43+005/BI14/dsf",
        TILE_DSF,
        dsf_params,
        {"mesh": mesh_ref, "masks": masks_ref, "rasters": None, "vectors": None},
        kind="cpu",
    )
    out_root = tmp_path / "out"
    penv = _PackEnv(store, out_root, None, None)

    def pack_run(ctx):
        with pack_env(penv):
            return run_p0_rule(ctx)

    pack = Node(
        "+43+005/BI14/pack",
        TILE_PACK,
        PackParams(tile=T.name, provider="BI", zl=14, out_dir=str(out_root)),
        {"dsf": dsf, "textures": _no_images(store), "overlay": None},
        kind="io",
        run=pack_run,
    )
    sched = make_scheduler(store, env, cpu_workers=1, cpu_in_threads=True)
    sched.add(pack)
    events: list = []
    refs = asyncio.run(sched.run([pack.id], on_event=events.append))
    assert not sched.failed, sched.failed
    dones = {e.node_id: e for e in events if isinstance(e, Done)}
    assert not dones[dsf.id].hit and not dones[pack.id].hit
    dsf_dir = dones[dsf.id].ref.path
    assert (dsf_dir / f"{T.name}.dsf").read_bytes().startswith(b"XPLNEDSF")
    jobs = texture_jobs_from_json(dsf_dir / TEXTURES_JSON)
    assert len(jobs) == 1 and jobs[0].texture.provider == "BI" and jobs[0].texture.zl == 14
    assert sorted(p.name for p in (dsf_dir / "terrain").glob("*.ter")) == [
        f"{jobs[0].texture.til_y}_{jobs[0].texture.til_x}_BI14.ter"
    ]
    pack_dir = out_root / pack_dir_name(T)
    manifest = PackManifest.from_toml(refs[pack.id].path.read_text())
    assert manifest.tile == T.name and manifest.files["terrain"] == 1
    assert manifest.files["textures"] == 0 and manifest.files["overlay"] == ""
    # its inputs and the provenance edges of the DSF
    assert set(manifest.artefacts) == {"dsf", "textures", "mesh", "masks"}
    assert manifest.artefacts["mesh"].key == mesh_ref.key
    assert (pack_dir / T.dsf_relpath).read_bytes() == (dsf_dir / f"{T.name}.dsf").read_bytes()
    assert pack_is_intact(pack_dir, manifest)
    assert (pack_dir / "orthostudio.toml").read_text() == manifest.to_toml()

    # second run: hits, nothing rewritten
    mtime = (pack_dir / T.dsf_relpath).stat().st_mtime_ns
    sched2 = make_scheduler(store, env, cpu_workers=1, cpu_in_threads=True)
    sched2.add(pack)
    events2: list = []
    asyncio.run(sched2.run([pack.id], on_event=events2.append))
    assert all(e.hit for e in events2 if isinstance(e, Done))
    assert (pack_dir / T.dsf_relpath).stat().st_mtime_ns == mtime
    # a tampered pack is detected
    (pack_dir / T.dsf_relpath).unlink()
    assert not pack_is_intact(pack_dir, manifest)


def test_the_pack_forces_the_tiles_files_before_x_plane_is_handed_any(
    tmp_path: Path, env: BuildEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile's textures and ``.ter`` files are no longer forced one by one (``graph/store.py``
    ``DEFERRED_RULES``, 2026-09-28): the pack forces them as a group before it writes the tile's
    folder, so X-Plane is never handed a file a power cut could still tear."""
    from orthostudio.graph import store as store_mod

    monkeypatch.setattr(store_mod, "DURABLE_MARGIN_S", 0.0)
    store = env.store

    def fill_dsf(out: Path) -> None:
        (out / f"{T.name}.dsf").write_bytes(b"XPLNEDSF")
        (out / "terrain").mkdir()

    dsf = _artefact_dir(store, "tile.dsf", "44" * 32, fill_dsf)
    out_root = tmp_path / "out"
    pack_dir = out_root / pack_dir_name(T)
    forced_while_the_pack_was: list[bool] = []
    make_durable = store.make_durable

    def spy() -> int:
        forced_while_the_pack_was.append(pack_dir.exists())
        return make_durable()

    monkeypatch.setattr(store, "make_durable", spy)
    penv = _PackEnv(store, out_root, None, None)

    def pack_run(ctx):
        with pack_env(penv):
            return run_p0_rule(ctx)

    params = PackParams(tile=T.name, provider="BI", zl=14, out_dir=str(out_root))
    inputs = {"dsf": dsf, "textures": _no_images(store), "overlay": None}
    sched = _sched(env)
    sched.add(Node("+43+005/BI14/pack", TILE_PACK, params, inputs, kind="io", run=pack_run))
    asyncio.run(sched.run(["+43+005/BI14/pack"]))
    assert not sched.failed, sched.failed
    assert forced_while_the_pack_was == [False] and pack_dir.is_dir()
    info = store.info(dsf.key)
    until = store.durable_until()
    assert info is not None and until is not None and info.created_at <= until


def test_a_tile_whose_file_another_program_holds_is_packed_all_the_same(
    tmp_path: Path, env: BuildEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file of the tile another program holds (a scanner, a sync tool) cannot be forced to
    disk on Windows. Failing the tile for it (``XP_PACK_CONFLICT``, 2026-09-28) stopped a build for
    a file almost always whole: the tile is packed, and the durable point stops before that
    file, which the next build checks (``graph-keys.md`` 6)."""
    from orthostudio.graph import store as store_mod

    monkeypatch.setattr(store_mod, "DURABLE_MARGIN_S", 0.0)
    store = env.store

    def fill_dsf(out: Path) -> None:
        (out / f"{T.name}.dsf").write_bytes(b"XPLNEDSF")
        (out / "terrain").mkdir()

    dsf = _artefact_dir(store, "tile.dsf", "55" * 32, fill_dsf)

    def held(p: Path) -> None:
        raise PermissionError(13, "in use", str(p))

    monkeypatch.setattr(store_mod, "_flush_data", held)
    out_root = tmp_path / "out"
    penv = _PackEnv(store, out_root, None, None)

    def pack_run(ctx):
        with pack_env(penv):
            return run_p0_rule(ctx)

    params = PackParams(tile=T.name, provider="BI", zl=14, out_dir=str(out_root))
    inputs = {"dsf": dsf, "textures": _no_images(store), "overlay": None}
    sched = _sched(env)
    sched.add(Node("+43+005/BI14/pack", TILE_PACK, params, inputs, kind="io", run=pack_run))
    asyncio.run(sched.run(["+43+005/BI14/pack"]))
    assert not sched.failed, sched.failed
    assert (out_root / pack_dir_name(T) / T.dsf_relpath).is_file()
    info = store.info(dsf.key)
    until = store.durable_until()
    assert info is not None and until is not None and until < info.created_at


def test_the_pack_records_the_colours_its_textures_are_encoded_with(
    tmp_path: Path, env: BuildEnv
) -> None:
    """The Library reads a tile's colours in its manifest first (``api.md`` 2.3), and ``declare``
    left them out of the pack's params: no pack recorded any from 2026-09-18 on, and the page
    could only ask the store, which has no answer once the data folder changed (found
    2026-09-26). Through ``declare`` and the rule itself; a plain tile keeps its pack's key."""
    much_softer = {"photo_brightness": -0.06, "photo_contrast": -0.03, "photo_saturation": -0.3}
    (g,) = _declare([_spec(tmp_path, config=much_softer)], _sched(env), env)
    for node in (g.textures, g.pack):
        assert {name: getattr(node.params, name) for name in much_softer} == much_softer
    (plain,) = _declare([_spec(tmp_path)], _sched(env), env)
    assert not set(much_softer) & set(plain.pack.params.canonical())  # left out while zero

    # the rule itself, with the params declare gave it, over a DSF artefact of its own
    params = g.pack.params
    assert isinstance(params, PackParams)
    penv = _PackEnv(env.store, Path(params.out_dir), None, None)

    def pack_run(ctx):
        with pack_env(penv):
            return run_p0_rule(ctx)

    def fill_dsf(out: Path) -> None:
        (out / f"{T.name}.dsf").write_bytes(b"XPLNEDSF")
        (out / "terrain").mkdir()

    dsf = _artefact_dir(env.store, "tile.dsf", "33" * 32, fill_dsf)
    inputs = {"dsf": dsf, "textures": _no_images(env.store), "overlay": None}
    sched = _sched(env)
    sched.add(Node(g.pack.id, TILE_PACK, params, inputs, kind="io", run=pack_run))
    refs = asyncio.run(sched.run([g.pack.id]))
    assert not sched.failed, sched.failed
    recorded = {"brightness": -0.06, "contrast": -0.03, "saturation": -0.3}
    assert PackManifest.from_toml(refs[g.pack.id].path.read_text()).photo == recorded
    written = Path(params.out_dir) / pack_dir_name(T) / "orthostudio.toml"
    assert PackManifest.from_toml(written.read_text()).photo == recorded


@pytest.mark.parametrize("filed", [False, True], ids=["in-its-place", "filed-elsewhere"])
def test_a_pack_x_plane_shows_is_not_rewritten_in_its_place_while_x_plane_runs(
    tmp_path: Path, env: BuildEnv, monkeypatch: pytest.MonkeyPatch, filed: bool
) -> None:
    """On every system, X-Plane running: a pack it shows is not rewritten in its place, and the
    refusal has words of its own (build again, not install); a tile filed elsewhere, whose new
    version is built in the workshop, is assembled there, only its put back waiting. The refusal
    was Windows only and looked at the tile's link alone: on Windows a filed tile was refused
    before it was built (found on the owner's Shadow, 2026-10-06)."""
    (g,) = _declare([_spec(tmp_path)], _sched(env), env)
    params = g.pack.params
    assert isinstance(params, PackParams)
    pack_dir = Path(params.out_dir) / pack_dir_name(T)
    cs = tmp_path / "X-Plane 12" / "Custom Scenery"
    cs.mkdir(parents=True)
    shown = tmp_path / "Alps" / pack_dir_name(T) if filed else pack_dir
    shown.mkdir(parents=True)
    (cs / pack_dir_name(T)).symlink_to(shown, target_is_directory=True)
    monkeypatch.setattr(install_packs, "xplane_running", lambda: True)
    penv = _PackEnv(env.store, Path(params.out_dir), cs, None)

    def pack_run(ctx):
        with pack_env(penv):
            return run_p0_rule(ctx)

    def fill_dsf(out: Path) -> None:
        (out / f"{T.name}.dsf").write_bytes(b"XPLNEDSF")
        (out / "terrain").mkdir()

    dsf = _artefact_dir(env.store, "tile.dsf", "34" * 32, fill_dsf)
    inputs = {"dsf": dsf, "textures": _no_images(env.store), "overlay": None}
    sched = _sched(env)
    sched.add(Node(g.pack.id, TILE_PACK, params, inputs, kind="io", run=pack_run))
    asyncio.run(sched.run([g.pack.id]))
    written = (pack_dir / "orthostudio.toml").is_file()
    if filed:
        assert not sched.failed, sched.failed
        assert written
    else:
        assert sched.failed[g.pack.id].code == "SYS_REBUILD_XP_RUNNING"
        assert sched.failed[g.pack.id].context["tile"] == T.name
        assert not written and not any(pack_dir.iterdir())


def test_source_ref_is_content_addressed(tmp_path: Path) -> None:
    p = tmp_path / "a.dsf"
    p.write_bytes(b"XPLNEDSF" * 3)
    ref = source_ref(p)
    assert ref.key == ref.digest and ref.size == 24 and ref.path == p and ref.kind == "file"


def test_a_folder_of_ones_own_rides_over_the_relief_and_marks_the_file_it_takes(
    tmp_path: Path,
) -> None:
    """Settings hand the folder down inside ``custom_dem``; the node keeps the relief chosen as its
    base and adds the folder as an overlay, with the mark of the file of that square in its key.

    Without that mark the path alone would be the key, and replacing a file with a better version
    of itself would answer from the store, unchanged (a user of the X-Plane.Org page, 2026-09-19).
    """
    from orthostudio.config.models import Settings
    from orthostudio.config.overrides import to_build_overrides
    from orthostudio.pipeline.build import dem_declaration

    ref = ArtifactRef("a" * 64, "b" * 64, tmp_path / "dsf", "fake", "file", 1)

    own = tmp_path / "Sonny" / "Austria"
    own.mkdir(parents=True)
    (own / "N47E011.hgt").write_bytes(b"\x00" * 2000)
    settings = Settings.model_validate(
        {"essential": {"relief": {"source": "copernicus", "folder": str(tmp_path / "Sonny")}}}
    )
    cfg = to_build_overrides(settings)
    assert cfg["custom_dem"] == f"COP30;{tmp_path / 'Sonny'}"

    spec = _spec(tmp_path, TileRef(47, 11), relief="copernicus")
    params, _ = dem_declaration(spec, cfg, lambda _tile: None, None)
    assert params["custom_dem"] == f"COP30;{tmp_path / 'Sonny'}"
    # the file's bytes, not its date: a backup restore kept every byte and rebuilt everything
    assert params["own_stamp"].startswith("2:N47E011.hgt:")
    # a square the folder does not hold: the same node, and nothing of its own to mark
    other, _ = dem_declaration(
        _spec(tmp_path, TileRef(47, 10), relief="copernicus"), cfg, lambda _tile: None, None
    )
    assert "own_stamp" not in other  # nothing of its own here: the same key as ever
    # a relief with no overlay at all keeps the key it has always had
    plain = to_build_overrides(
        Settings.model_validate({"essential": {"relief": {"source": "copernicus"}}})
    )
    alone, _ = dem_declaration(
        _spec(tmp_path, TileRef(47, 11), relief="copernicus"), plain, lambda _tile: None, None
    )
    assert "own_stamp" not in alone

    # the X-Plane relief keeps its own base, and the folder still rides over it
    with_xp = Settings.model_validate(
        {"essential": {"relief": {"source": "auto", "folder": str(tmp_path / "Sonny")}}}
    )
    xp_cfg = to_build_overrides(with_xp)
    assert xp_cfg["custom_dem"] == f";{tmp_path / 'Sonny'}"
    xp_spec = _spec(tmp_path, TileRef(47, 11), relief="xplane")
    xp_params, xp_inputs = dem_declaration(xp_spec, xp_cfg, lambda tile: ref, None)
    assert xp_params["custom_dem"] == f"XP12;{tmp_path / 'Sonny'}"
    assert any(
        value is ref for value in xp_inputs.values()
    )  # the Global Scenery DSFs are still read


def _osm_counts(tmp_path: Path, counts: dict[str, int]) -> ArtifactRef:
    folder = tmp_path / "osm-counts"
    folder.mkdir()
    index = {"counts": {layer: {"nodes": n, "ways": n // 10} for layer, n in counts.items()}}
    (folder / "snapshot.json").write_text(json.dumps(index), encoding="utf-8")
    return ArtifactRef("0" * 64, "0" * 64, folder, "orthostudio.osm", "dir", 0)


ATLANTA = {"big_roads": 176_616, "small_roads": 2_312_997, "water": 588_745, "airports": 18_803}


def test_the_road_points_of_the_tiles_level_are_counted(tmp_path: Path) -> None:
    """Atlanta's OSM counts (2026-10-28): the road layers its level reads, nothing else."""
    osm = _osm_counts(tmp_path, ATLANTA)
    assert road_points(osm, 4) == 2_489_613
    assert road_points(osm, 1) == 176_616
    assert road_points(osm, 0) == 0
    bare = tmp_path / "bare"
    bare.mkdir()
    assert road_points(ArtifactRef("1" * 64, "1" * 64, bare, "x", "dir", 0), 4) is None
    assert road_points(None, 4) is None


@pytest.mark.parametrize(
    ("config", "counts", "ram_mb"),
    [
        # a user's settings, service roads, round ends, 2 000 000 points: Atlanta took 6.0 to 6.8
        (
            {"road_level": 4, "rounded_road_ends": True, "max_levelled_segs": 2_000_000},
            ATLANTA,
            6400,
        ),
        # the same settings on a country tile, 379 047 points: it took 1.6 to 1.7 GB
        (
            {"road_level": 4, "rounded_road_ends": True, "max_levelled_segs": 2_000_000},
            {"big_roads": 40_000, "small_roads": 339_047},
            1800,
        ),
        ({}, ATLANTA, 1600),  # the defaults: level 1, angular ends: Atlanta took 1.7 GB
        ({"road_level": 0}, ATLANTA, 1500),  # no roads: 1.0 GB
        # an estimate, before the OSM data: 2 500 000 points, as many as Atlanta's
        ({"road_level": 4, "rounded_road_ends": True, "max_levelled_segs": 2_000_000}, None, 6400),
    ],
)
def test_a_tracing_reserves_what_its_roads_take(
    tmp_path: Path,
    env: BuildEnv,
    config: dict[str, object],
    counts: dict[str, int] | None,
    ram_mb: int,
) -> None:
    """``TRACING_RAM``, from the tile's road points, read once its OSM data is in: the line
    through the middle of what the tracings took. Atlanta's 6.6 GB for every tile kept a batch of
    country tiles to one tracing at a time on 16 GB (found by a campaign, 2026-10-28)."""
    (g,) = _declare([_spec(tmp_path, config=config)], _sched(env), env)
    assert g.vectors.ram_mb == 1200 and g.vectors.ram_from is not None
    osm = _osm_counts(tmp_path, counts) if counts is not None else None
    assert g.vectors.ram_from({"osm": osm}) == ram_mb


def _mesh_stats(tmp_path: Path, name: str, water_bits: dict[str, int] | None) -> ArtifactRef:
    folder = tmp_path / f"mesh-{name}"
    folder.mkdir()
    if water_bits is not None:
        stats = {"format": "osxp-mesh-stats-1", "water_bits_counts": water_bits}
        (folder / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    digest = ("a" if name == "tile" else "b") * 64
    return ArtifactRef(digest, digest, folder, "orthostudio.mesh", "dir", 0)


@pytest.mark.parametrize(
    ("neighbour_bits", "inland", "pool"),
    [
        ({"1": 300_000}, False, False),  # lakes and rivers only, inland: no pool (+33-086)
        ({"1": 182_715, "4": 161_146}, False, True),  # water the masks draw (+33-083)
        (None, False, True),  # a mesh that does not say: as before
        ({"1": 300_000}, True, True),  # masks for inland water too
    ],
)
def test_the_masks_reserve_their_pool_only_where_there_is_water_to_draw(
    tmp_path: Path,
    env: BuildEnv,
    neighbour_bits: dict[str, int] | None,
    inland: bool,
    pool: bool,
) -> None:
    """Every masks node reserved its pool, 5.1 GB on 8 threads, and inland the masks took a second
    and no pool: on 16 GB no tile's masks, so no images, started beside a big city's tracing (a
    campaign of 12 tiles, 2026-10-28)."""
    config = {"use_masks_for_inland": inland}
    (g,) = _declare([_spec(tmp_path, config=config)], _sched(env), env)
    masks = g.masks
    assert masks.ram_mb == 300 and masks.ram_from is not None
    inputs = {name: None for name in masks.inputs}
    inputs["mesh"] = _mesh_stats(tmp_path, "tile", {"1": 100_000})
    inputs[next(n for n in masks.inputs if n not in ("mesh", "dem", "custom_extent"))] = (
        _mesh_stats(tmp_path, "nb", neighbour_bits)
    )
    expected = _masks_ram_mb(env) if pool else 300
    assert masks.ram_from(inputs) == expected
