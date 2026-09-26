# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Review-6 fixes outside the noder: the airport dictionary, smoothing tag, events, writes.

Specs: ``docs/specs/airports-integration.md`` 4 (the records, atomicity, events) and
``docs/specs/airports-geometry.md`` 6 (differences 8 and 9). No network.
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pytest
from shapely import geometry

from orthostudio.airports_vec.model import Airport, RunwayPart, SurfaceAreas
from orthostudio.errors import REGISTRY, OsxpError
from orthostudio.model import TileRef

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_review6_robustesse_cas_limites as limits

TILE = TileRef(43, 5)
REPO = Path(__file__).resolve().parents[1]


def _airport(key: str, *, orphan: bool = False, tag: int | None = None) -> Airport:
    square = geometry.Polygon([(0.1, 0.1), (0.2, 0.1), (0.2, 0.2), (0.1, 0.2)])
    area = geometry.MultiPolygon([square])
    airport = Airport(
        key=key,
        key_type="name" if orphan else "icao",
        name=key,
        repr_node=(5.15, 43.15),
        boundary=area,
        areas=SurfaceAreas(
            runway=area,
            runway_as_area=(
                RunwayPart(square, np.array([0.1, 0.15]), np.array([0.2, 0.15]), 30.0),
            ),
            taxiway=area,
            apron=area,
            hangar=area,
        ),
        smoothing_pix=tag,
        orphan=orphan,
    )
    return airport


# -- 1. Ortho4XP's dictionary, in its key order
# ----------------------------------------------------------


def test_discovery_marks_the_orphans() -> None:
    builder = limits.Store()
    limits.aerodrome(builder, 5.49, 43.49, 0.03, "LFAA")
    builder.way([(5.495, 43.5), (5.515, 43.5)], aeroway="runway", width="30")
    builder.way([(5.9, 43.9), (5.92, 43.9)], aeroway="runway", width="30", name="Far strip")
    encoded, _, _, _ = limits.run_chain(builder)
    kinds = {airport.key: airport.orphan for airport in encoded.airports}
    assert kinds == {"LFAA": False, "Far strip": True}


# -- 2. the smoothing tag ----------------------------------------------------------------------


def test_the_smoothing_code_is_registered_and_documented() -> None:
    spec = REGISTRY["OSM_AIRPORT_SMOOTHING_INVALID"]
    assert spec.severity.value == "degraded"
    text = OsxpError(
        "OSM_AIRPORT_SMOOTHING_INVALID",
        context={"airport": "LFXX", "value": -3, "range": "0..1000"},
    ).message
    assert "LFXX" in text and "-3" in text and "0..1000" in text
    errors_md = (REPO / "docs" / "specs" / "errors.md").read_text(encoding="utf-8")
    assert "| OSM_AIRPORT_SMOOTHING_INVALID |" in errors_md


@pytest.mark.parametrize(("tag", "kept"), [("0", 0), ("12", 12), ("1000", 1000)])
def test_a_smoothing_tag_in_range_is_kept_without_event(tag: str, kept: int) -> None:
    from orthostudio.airports_vec import discover

    events: list[str] = []
    assert discover._smoothing_pix({"smoothing_pix": tag}, lambda c, _: events.append(c)) == kept
    assert events == []


@pytest.mark.parametrize("tag", ["-1", "1001", "30000000"])
def test_a_smoothing_tag_out_of_range_is_dropped_with_an_event(tag: str) -> None:
    from orthostudio.airports_vec import discover

    events: list[str] = []
    assert discover._smoothing_pix({"smoothing_pix": tag}, lambda c, _: events.append(c)) is None
    assert events == ["OSM_AIRPORT_SMOOTHING_INVALID"]


# -- 3. stats.json and atomic writes -----------------------------------------------------------


def test_stats_json_has_families_and_events_even_without_airports(tmp_path: Path) -> None:
    out = limits.run_rule(tmp_path, limits.OsmXml())
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert set(stats["families"]) >= {"airports", "coastline", "water"}
    assert stats["events"] == {}
    assert not [p for p in out.iterdir() if p.name.endswith(".part")]


# -- 4. every cpu node of a build crosses a process boundary -----------------------------


def test_every_cpu_node_of_a_native_build_pickles(tmp_path: Path) -> None:
    """Found by the review-6 fix pass when running the real build: the vector node was declared
    ``cpu`` with a run closure and a decorated rule function, neither of which pickles, so
    every native build died of a ``PicklingError`` in the spawned worker. The graph tests use
    ``cpu_in_threads=True`` and never noticed. This declares the graph and pickles what the
    scheduler ships to a worker for every ``cpu`` node. The graph is declared as an estimate
    declares it (the OSM layers still to fetch): the nodes are the same."""
    import test_build_graph as graph_tests
    from orthostudio.graph import Store
    from orthostudio.imagery.providers import load_registry
    from orthostudio.pipeline.build import BuildEnv, declare, make_scheduler

    scenery = tmp_path / "Global Scenery"
    for tile in (graph_tests.T, graph_tests.W, graph_tests.N):
        dsf = scenery / tile.dsf_relpath
        dsf.parent.mkdir(parents=True, exist_ok=True)
        dsf.write_bytes(b"XPLNEDSF")
    with Store(tmp_path / "store", fsync=False) as store:
        env = BuildEnv(
            store=store, store_root=tmp_path / "store", chunks_root=tmp_path / "chunks",
            workdir=tmp_path / "work",
            global_scenery=scenery, dsftool=None, registry=load_registry(), workers=2,
            library_path=tmp_path / "library.sqlite",
        )  # fmt: skip
        spec = graph_tests._spec(tmp_path)
        scheduler = make_scheduler(store, env, cpu_workers=1, cpu_in_threads=True)
        (graph,) = declare([spec], scheduler, env, planning=True)
        nodes = list(graph.all())
        assert graph.vectors.kind == "subprocess"
        cpu = [node for node in nodes if node.kind == "cpu"]
        assert cpu, "a native build has at least the DSF on the process pool"
        for node in cpu:
            pickle.dumps((node.rule, node.params, node.run))
