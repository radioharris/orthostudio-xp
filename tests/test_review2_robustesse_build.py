# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""P2a adversarial review, robustness lens: ``build_tiles`` on a batch where nothing can build.

No network, no Ortho4XP: the OSM download fails, there is no DSFTool, and the Global Scenery
holds fake DSF bytes. What must hold: no exception escapes, every tile of the batch is reported
with a coded first cause, the report serialises to JSON, nothing partial is left, and the
second-pass logic only retries tiles hurt by a *neighbour*.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from orthostudio.errors import OsxpError
from orthostudio.graph import Store
from orthostudio.imagery.providers import load_registry
from orthostudio.model import ArtifactRef, TileRef
from orthostudio.pipeline import build as buildmod
from orthostudio.pipeline import native
from orthostudio.pipeline.build import BuildEnv, BuildSpec, _foreign_cause, build_tiles
from orthostudio.sched import Event, NodeContext
from orthostudio.sources.osm import LAYERS, OsmSnapshot

T = TileRef(43, 5)
W = TileRef(43, 4)


@pytest.fixture
def env(tmp_path: Path) -> BuildEnv:
    gs = tmp_path / "Global Scenery"
    for tile in (T, W):
        p = gs / tile.dsf_relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"XPLNEDSF-not-really-" + tile.name.encode())
    return BuildEnv(
        store=Store(tmp_path / "store", fsync=False),
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        global_scenery=gs,
        dsftool=None,
        registry=load_registry(),
        workers=1,
        library_path=tmp_path / "library.sqlite",
    )


def _spec(tmp_path: Path, tile: TileRef) -> BuildSpec:
    # X-Plane's relief, from the fake DSF bytes: a tile whose OSM download fails still runs its
    # elevation, which needs no OSM data, and a downloaded relief would reach for the network
    return BuildSpec(
        tile=tile, provider="BI", zl=14, out_dir=tmp_path / "out", store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks", workdir=tmp_path / "work", relief="xplane",
    )  # fmt: skip


def _no_network(tile: TileRef, layers: object) -> dict[str, object]:
    raise OsxpError("NET_CONNECTION_FAILED", context={"url": "overpass", "reason": "test"})


def _empty_layer(tile: TileRef, layer: str) -> OsmSnapshot:
    return OsmSnapshot(
        tile=tile, layer=layer, selectors=LAYERS[layer].selectors, query="q", mirror="test",
        fetched_at="2026-09-12T00:00:00Z", generator="test", osm_base="2026-09-12T00:00:00Z",
        nodes=(), ways=(), relations=(), digest="0" * 64,
    )  # fmt: skip


def _empty_layers(tile: TileRef, layers: list) -> dict[str, OsmSnapshot]:
    """An OSM download that answers every layer with an empty extract."""
    return {spec.name: _empty_layer(tile, spec.name) for spec in layers}


def test_batch_of_failing_tiles_is_reported_not_raised(tmp_path: Path, env: BuildEnv) -> None:
    specs = [_spec(tmp_path, T), _spec(tmp_path, W)]
    events: list[Event] = []
    with native.osm_job(native.OsmJob(fetch=_no_network)):
        report = build_tiles(
            specs, on_event=events.append, cpu_in_threads=True, env=env, handle_sigint=False
        )
    assert not report.ok and not report.cancelled
    assert [t.tile for t in report.tiles] == ["+43+005", "+43+004"]
    for tile in report.tiles:
        assert not tile.ok and tile.pack_dir is None and not tile.installed
        first = tile.error
        # the first own failure is coded and carries a remedy, never a bare traceback: the
        # download's own, which the graph's OSM node reports
        assert first is not None and first.error is not None and first.role == "osm"
        assert first.error["code"] == "NET_CONNECTION_FAILED" and first.error["remedy"]
        assert tile.osm["status"] == "failed"
        vectors = next(n for n in tile.nodes if n.role == "vectors")
        # both its inputs fell (the fake DSF breaks the elevation too): the first to fall
        assert vectors.status == "skipped"
        assert vectors.cause in (f"{tile.tile}/osm", f"{tile.tile}/dem")
    # nothing partial: the store holds no artefact, the out dir is untouched
    assert list(env.store.iter_artifacts()) == []
    assert not env.store.fsck().tmp_leftovers
    assert not (tmp_path / "out").exists()
    # the report is what `osxp build --json` prints
    doc = json.loads(json.dumps(report.to_dict()))
    rows = [n for t in report.tiles for n in t.nodes if n.status in ("failed", "skipped")]
    assert doc["failed"] == report.failed == len(rows) and len(doc["tiles"]) == 2


def test_skipped_nodes_are_blamed_on_their_own_tile_first() -> None:
    """P2a review (minor): when +43+005/vectors and +43+004/vectors both fail, the scheduler
    blames +43+005/masks on whichever fell first; the report prefers the tile's own root."""
    from orthostudio.pipeline.build import NodeOutcome, _prefer_own_root_cause

    skipped = {"code": "SYS_UPSTREAM_FAILED", "context": {"root": "+43+004/vectors"}}
    outcomes = [
        NodeOutcome("+43+005/vectors", "vectors", "r", None, "failed", 0.0, {"code": "X"}),
        NodeOutcome("+43+005/mesh", "mesh", "r", None, "skipped", 0.0, skipped, "+43+004/vectors"),
        NodeOutcome("+43+005/masks", "masks", "r", None, "skipped", 0.0, skipped, "+43+005/mesh"),
    ]
    _prefer_own_root_cause(outcomes, T)
    assert outcomes[1].cause == "+43+005/vectors"
    assert outcomes[1].error is not None
    assert outcomes[1].error["context"]["root"] == "+43+005/vectors"
    assert outcomes[2].cause == "+43+005/mesh"  # already its own tile: untouched
    only_foreign = [
        NodeOutcome("+43+005/masks", "masks", "r", None, "skipped", 0.0, skipped, "+43+004/mesh")
    ]
    _prefer_own_root_cause(only_foreign, T)
    assert only_foreign[0].cause == "+43+004/mesh"  # no own root: the neighbour is the cause


def test_second_pass_selection_only_blames_neighbours() -> None:
    failed = {W}
    assert _foreign_cause("+43+004/vectors", T, failed)
    assert not _foreign_cause("+43+005/vectors", T, failed)  # its own stage
    assert not _foreign_cause("+44+005/mesh", T, failed)  # a neighbour that did not fail
    assert not _foreign_cause(None, T, failed)


def _stand_in(fail: TileRef) -> Callable[..., Callable[[NodeContext], ArtifactRef]]:
    """A run factory shaped like ``_vectors_run`` & co: each node writes its own id, and the vector
    stage of ``fail`` raises, as a stage that cannot build its tile."""

    def factory(*_args: object) -> Callable[[NodeContext], ArtifactRef]:
        def run(ctx: NodeContext) -> ArtifactRef:
            if ctx.node_id == f"{fail.name}/vectors":
                raise OsxpError(
                    "SYS_INTERNAL_ERROR", context={"type": "ValueError", "detail": "test"}
                )

            def write(out: Path) -> None:
                (out / "id" if out.is_dir() else out).write_text(ctx.node_id, encoding="utf-8")

            return ctx.produce(write)

        return run

    return factory


def test_a_neighbour_that_fails_its_vector_stage_does_not_cost_the_tile_its_masks(
    tmp_path: Path, env: BuildEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec ``pipeline-build.md`` 4: the masks of +43+005 are skipped because its western neighbour
    of the batch failed, and the second pass builds them again without that neighbour. The stages
    are stand-ins; the graph, the scheduler and the second pass are the real ones."""
    triangle = tmp_path / "Triangle4XP"
    triangle.write_bytes(b"")
    monkeypatch.setenv("OSXP_TRIANGLE4XP", str(triangle))
    stand_in = _stand_in(fail=W)
    for name in ("_dem_run", "_vectors_run", "_mesh_run", "_masks_run"):
        monkeypatch.setattr(buildmod, name, stand_in)
    specs = [_spec(tmp_path, tile) for tile in (T, W)]
    for spec in specs:
        spec.overlay = spec.xp12_rasters = False
    with native.osm_job(native.OsmJob(fetch=_empty_layers)):  # empty extracts, no network
        report = build_tiles(specs, cpu_in_threads=True, env=env, handle_sigint=False)

    west = {n.role: n for n in next(t for t in report.tiles if t.tile == W.name).nodes}
    assert west["vectors"].status == "failed" and west["vectors"].cause is None
    assert west["mesh"].status == "skipped"
    east = {n.role: n for n in next(t for t in report.tiles if t.tile == T.name).nodes}
    assert east["masks"].status == "built", east["masks"]  # by the second pass
    assert east["vectors"].status == "hit" and east["mesh"].status == "hit"
    masks = env.store.info(east["masks"].key)
    assert masks is not None and masks.path.joinpath("id").read_text() == f"{T.name}/masks"
