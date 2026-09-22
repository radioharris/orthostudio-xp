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
    declare,
    make_scheduler,
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
        {"dsf": dsf, "textures": None, "overlay": None},
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
    assert set(manifest.artefacts) == {"dsf", "mesh", "masks"}  # provenance edges of the DSF
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
    assert params["own_stamp"].startswith("2:N47E011.hgt:2000:")
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
