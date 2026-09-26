# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""P3 integration: what the stages can build, the graph, the OSM and coastline nodes.

Spec: ``docs/specs/pipeline-build.md`` section 8. Nothing here touches the network or
Ortho4XP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orthostudio.dem.xplane import XP12_INPUTS
from orthostudio.errors import OsxpError
from orthostudio.graph import Store, key_for
from orthostudio.masks.rule import MASKS as OSXP_MASKS
from orthostudio.mesh.rule import OSXP_MESH
from orthostudio.mesh.weights import read_coastline_nodes
from orthostudio.model import TileRef
from orthostudio.pipeline import native
from orthostudio.pipeline.build import (
    BuildEnv,
    BuildSpec,
    declare,
    make_scheduler,
    run_osm_phase,
    stored_neighbour_mesh,
)
from orthostudio.sources.osm import (
    LAYERS,
    OsmNode,
    OsmSnapshot,
    OsmWay,
    SnapshotStore,
)
from orthostudio.vectors.rule import VECTORS as OSXP_VECTORS

TILE = TileRef(43, 5)


# -- 1. what the stages can build -------------------------------------------------------


def _cfg(**over: Any) -> dict[str, Any]:
    base = {"iterate": 0, "masks_use_DEM_too": False, "masks_custom_extent": ""}
    base.update(over)
    return base


def test_the_stages_build_a_standard_tile(tmp_path: Path) -> None:
    binary = tmp_path / "Triangle4XP"
    binary.write_bytes(b"\x7fELF")
    choice = native.resolve_stages(_cfg(), triangle=binary)
    assert choice.dem == "vectors" and choice.to_dict() == {"dem": "vectors"}
    assert choice.label == "dem=vectors"
    assert native.resolve_stages(_cfg(), dem="native", triangle=binary).dem == "native"


def test_a_setting_no_stage_supports_is_refused_before_the_build(tmp_path: Path) -> None:
    binary = tmp_path / "Triangle4XP"
    binary.write_bytes(b"\x7fELF")
    for over in ({"masks_use_DEM_too": True}, {"masks_custom_extent": "x"}, {"iterate": 1}):
        with pytest.raises(OsxpError) as exc:
            native.resolve_stages(_cfg(**over), triangle=binary)
        assert exc.value.code == "CFG_VALUE_INVALID"
        assert next(iter(over)) in exc.value.message
    with pytest.raises(OsxpError) as missing:
        native.resolve_stages(_cfg(), triangle=tmp_path / "absent")
    assert missing.value.code == "SYS_TOOL_MISSING"


def test_an_unknown_elevation_source_is_refused() -> None:
    with pytest.raises(OsxpError):
        native.resolve_stages(_cfg(), dem="raw")


# -- 2. the declared graph ---------------------------------------------------------------


def _spec(tmp_path: Path, **kw: Any) -> BuildSpec:
    return BuildSpec(
        tile=TILE,
        provider="BI",
        zl=14,
        out_dir=tmp_path / "out",
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        overlay=False,
        xp12_rasters=False,
        **{"osm_fetch": False, **kw},
    )


def _graph(tmp_path: Path, **kw: Any) -> Any:
    """The graph of one tile, declared as an estimate does: its OSM layers still to fetch."""
    spec = _spec(tmp_path, **kw)
    env = BuildEnv.create([spec])
    return declare([spec], make_scheduler(env.store, env), env, planning=True)[0]


@pytest.fixture
def triangle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "Triangle4XP"
    binary.write_bytes(b"\x7fELF")
    binary.chmod(0o755)
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(binary))
    return binary


def test_the_graph_wires_the_stages(tmp_path: Path, triangle: Path) -> None:
    g = _graph(tmp_path)
    assert g.vectors.rule is OSXP_VECTORS and g.vectors.kind == "subprocess"
    assert g.mesh.rule is OSXP_MESH and g.mesh.kind == "subprocess"
    assert g.masks.rule is OSXP_MASKS
    # the vector stage reads the raw elevation; --dem vectors: the mesh reads the smoothed
    # Data<tile>.alt of the vector stage (spec 8.2)
    assert g.dem is not None and g.vectors.inputs["dem"] is g.dem
    assert g.mesh.inputs["dem"] is g.vectors
    assert g.masks.inputs["mesh"] is g.mesh
    assert g.masks.inputs["dem"] is None and g.masks.inputs["custom_extent"] is None


def test_dem_native_declares_the_dem_node(tmp_path: Path, triangle: Path) -> None:
    g = _graph(tmp_path, dem="native")
    assert g.dem is not None and g.dem.rule.name == "orthostudio.dem"
    assert g.mesh.inputs["dem"] is g.dem
    # a graph root: keyed by its params alone with Ortho4XP's relief (the suite's ``OSXP_RELIEF``);
    # with X-Plane's, the nine Global Scenery DSFs are its inputs (decision 0007)
    assert g.dem.inputs == dict.fromkeys(XP12_INPUTS)


def test_the_mesh_params_do_not_carry_the_elevation_settings(
    tmp_path: Path, triangle: Path
) -> None:
    """``custom_dem`` / ``fill_nodata`` reach the mesh through the digest of its dem input."""
    g = _graph(tmp_path)
    fields = set(type(g.mesh.params).model_fields)
    assert "custom_dem" not in fields and "fill_nodata" not in fields
    assert {"curvature_tol", "skip_multiples_of_ten", "water_in_set_order"} <= fields


def test_a_cold_tile_plans_its_coastline_from_the_layers_still_to_fetch(
    tmp_path: Path, triangle: Path
) -> None:
    """An estimate runs before phase 0: the coastline reads the same placeholder as the vector
    stage, so the planned graph has the shape of the real one."""
    g = _graph(tmp_path)
    assert g.vectors.inputs["osm"].rule == "osm_to_fetch"
    assert g.coastline is not None and g.coastline.inputs["osm"] is g.vectors.inputs["osm"]
    assert g.mesh.inputs["coastline"] is g.coastline


def test_a_native_mesh_artefact_is_a_usable_neighbour(tmp_path: Path) -> None:
    """``stored_neighbour_mesh`` finds a stored mesh of the neighbour (spec 8.5)."""
    store = Store(tmp_path / "store")
    nb = TileRef(43, 6)
    with store.begin(OSXP_MESH.name, "a1" * 32, "dir") as build:
        (build.out / f"Data{nb.name}.mesh").write_text("x")
        build.commit(version=1, recipe="{}", inputs=[])
    ref = stored_neighbour_mesh(store, nb)
    assert ref is not None and ref.rule == OSXP_MESH.name


# -- 3. the OSM node ---------------------------------------------------------------------


def _snapshot(layer: str, nodes: int = 2) -> OsmSnapshot:
    ns = tuple(
        OsmNode(id=i + 1, lat=43.0 + i / 100, lon=5.0 + i / 100, tags={}) for i in range(nodes)
    )
    ways = (OsmWay(id=10, nodes=tuple(n.id for n in ns), tags={"natural": layer}),)
    return OsmSnapshot(
        tile=TILE,
        layer=layer,
        selectors=LAYERS[layer].selectors,
        query="q",
        mirror="test",
        fetched_at="2026-09-12T00:00:00Z",
        generator="test",
        osm_base="2026-09-12T00:00:00Z",
        nodes=ns,
        ways=ways,
        relations=(),
        digest="0" * 64,
    )


def test_the_osm_node_writes_a_snapshot_store_and_a_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 0 downloads the layers of a tile the store holds no snapshot of."""
    spec = _spec(tmp_path, osm_fetch=True)
    env = BuildEnv.create([spec])
    calls: list[tuple[TileRef, int]] = []

    def fetch(tile: TileRef, specs: Any) -> dict[str, OsmSnapshot]:
        calls.append((tile, len(specs)))
        return {s.name: _snapshot(s.name) for s in specs}

    with native.osm_job(native.OsmJob(fetch=fetch)):
        outcomes = run_osm_phase([spec], env)
    assert calls == [(TILE, 4)]
    outcome = outcomes[TILE]
    assert outcome.label is not None and outcome.label.startswith("osm-")
    assert outcome.artefact is not None
    index = json.loads((outcome.artefact / native.SNAPSHOT_JSON).read_text())
    assert index["format"] == "osxp-osm-tile-1" and index["label"] == outcome.label


def test_a_stored_snapshot_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second run of a tile OrthoStudio XP fetched: no download, and the same content label (A4)."""
    spec = _spec(tmp_path, osm_fetch=True)
    env = BuildEnv.create([spec])
    with native.osm_job(native.OsmJob(fetch=lambda t, ss: {s.name: _snapshot(s.name) for s in ss})):
        first = run_osm_phase([spec], env)

    def refuse(tile: TileRef, specs: Any) -> dict[str, OsmSnapshot]:
        raise AssertionError("the node must not run twice")

    with native.osm_job(native.OsmJob(fetch=refuse)):
        second = run_osm_phase([spec], env)
    assert second[TILE].label == first[TILE].label
    assert second[TILE].ref is not None and second[TILE].skipped == "stored snapshot reused"
    key, _ = key_for(native.OSM_RULE, native.OsmParams(tile=TILE.name, road_level=1), {})
    assert second[TILE].ref.key == key


def test_no_osm_fetch_declares_nothing(tmp_path: Path) -> None:
    spec = _spec(tmp_path, osm_fetch=False)
    env = BuildEnv.create([spec])
    outcomes = run_osm_phase([spec], env)
    assert outcomes[TILE].skipped == "--no-osm-fetch"


def test_a_build_downloads_each_tile_in_its_own_graph(tmp_path: Path) -> None:
    """Ten tiles waited 4 min for the OSM downloads of all of them before any relief, mesh or
    image started (2026-09-14). A build declares each tile's download as a node of its graph,
    on the Overpass lane beside the imagery's network slot, and the vector stage and the
    coastline read that node; a stored snapshot stays an input."""
    from orthostudio.pipeline.build import OVERPASS_LANE

    spec = _spec(tmp_path, osm_fetch=True)
    env = BuildEnv.create([spec])
    sched = make_scheduler(env.store, env)
    (g,) = declare([spec], sched, env, fetch_osm=True)
    assert g.osm is not None and (g.osm.kind, g.osm.lane) == ("net", OVERPASS_LANE)
    assert g.osm.params == native.OsmParams(tile=TILE.name, road_level=1)
    assert g.vectors.inputs["osm"] is g.osm
    assert g.coastline is not None and g.coastline.inputs == {"osm": g.osm}
    assert next(iter(g.by_role)) == "osm" and g.osm in g.all()
    assert sched.lanes == {OVERPASS_LANE: 1} and sched.slots["net"] == 1

    with native.osm_job(native.OsmJob(fetch=lambda t, ss: {s.name: _snapshot(s.name) for s in ss})):
        outcomes = run_osm_phase([spec], env)
    refs = {t: o.ref for t, o in outcomes.items() if o.ref is not None}
    (stored,) = declare(
        [spec], make_scheduler(env.store, env), env, osm_artefacts=refs, fetch_osm=True
    )
    assert stored.osm is None and stored.vectors.inputs["osm"] is refs[TILE]
    assert "osm" not in stored.by_role


def test_the_snapshot_feeds_the_vector_stage(tmp_path: Path, triangle: Path) -> None:
    spec = _spec(tmp_path, osm_fetch=True)
    env = BuildEnv.create([spec])
    with native.osm_job(native.OsmJob(fetch=lambda t, ss: {s.name: _snapshot(s.name) for s in ss})):
        outcomes = run_osm_phase([spec], env)
    refs = {t: o.ref for t, o in outcomes.items() if o.ref is not None}
    g = declare([spec], make_scheduler(env.store, env), env, osm_artefacts=refs)[0]
    assert g.vectors.inputs["osm"] is refs[TILE]
    # the mesh and the vector stage read the same extract of the coast (spec 8.4)
    assert g.coastline is not None and g.coastline.inputs == {"osm": refs[TILE]}


# -- 4. the coastline readers ------------------------------------------------------------


def test_the_coastline_rule_writes_a_readable_npz(tmp_path: Path) -> None:
    snap = _snapshot("coastline", nodes=4)
    osm_dir = tmp_path / "osm"
    SnapshotStore(osm_dir).save(snap)
    from orthostudio.graph import ResolvedInput, RunContext

    out = tmp_path / "coastline.npz"
    ctx = RunContext(
        rule=native.COASTLINE_RULE,
        key="a1" * 32,
        params=native.CoastlineParams(tile=TILE.name),
        inputs={"osm": ResolvedInput("osm", "d", osm_dir)},
        out=out,
        scratch=tmp_path / "scratch",
    )
    native.COASTLINE_RULE.fn(ctx)
    nodes = read_coastline_nodes(out)
    assert nodes.shape == (4, 2)
    assert nodes[0].tolist() == [snap.nodes[0].lon, snap.nodes[0].lat]
