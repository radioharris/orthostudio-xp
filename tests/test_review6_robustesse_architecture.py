# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Adversarial review 6 (P4 wave 2), robustness lens: wiring, records, writes, cost, codes.

What the airport wave did to the *architecture* of ``orthostudio.vectors@1``: who reads the airport
records back, the atomicity of the new files, what becomes of the coded events, how
cancellation and the noding cost behave once a tile holds many aerodromes, and whether the
published records agree with each other. Nothing here needs the network.

Convention of the review series: ``test_defect_*`` asserts the current, wrong behaviour so the
finding is reproducible; it turns red the day the defect is fixed and must then be turned
round. The review-6 fixes turned every defect of this file round; each renamed test says what
it was called and what changed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from orthostudio.errors import REGISTRY, OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.rule import VectorsJob, vectors_job

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_review6_robustesse_cas_limites as limits

TILE = TileRef(43, 5)
SRC = Path(__file__).resolve().parents[1] / "src" / "orthostudio"


def _two_airports() -> limits.OsmXml:
    osm = limits.OsmXml()
    limits.aerodrome(osm, 5.49, 43.49, 0.03, "LFAA")
    osm.way([(5.495, 43.5), (5.515, 43.5)], aeroway="runway", width="30")
    osm.way([(5.495, 43.502), (5.515, 43.502)], aeroway="taxiway")
    limits.aerodrome(osm, 5.19, 43.19, 0.02, "LFBB")
    osm.way([(5.192, 43.2), (5.205, 43.2)], aeroway="runway", width="25")
    return osm


def _dense(side: int) -> limits.OsmXml:
    """``side * side`` aerodromes, each with two crossing runways, a taxiway and a hangar."""
    osm = limits.OsmXml()
    step = 1.0 / side
    for i in range(side):
        for j in range(side):
            lon, lat, s = 5 + (i + 0.2) * step, 43 + (j + 0.2) * step, 0.4 * step
            limits.aerodrome(osm, lon, lat, s, f"L{i:02d}{j:02d}")
            y, x = lat + 0.5 * s, lon + 0.5 * s
            osm.way([(lon + 0.1 * s, y), (lon + 0.9 * s, y)], aeroway="runway", width="45")
            osm.way([(x, lat + 0.1 * s), (x, lat + 0.9 * s)], aeroway="runway", width="30")
            osm.way(
                [(lon + 0.1 * s, y + 0.05 * s), (lon + 0.9 * s, y + 0.05 * s)], aeroway="taxiway"
            )
            osm.way(
                limits.square(lon + 0.2 * s, lat + 0.8 * s, 0.02 * s), closed=True, aeroway="hangar"
            )
    return osm


# -- 1. the airport records and their readers ---------------------------------------------------


def test_the_mesh_and_the_dsf_read_the_same_airports(tmp_path: Path) -> None:
    """``airports.json`` (the mesh's weight map) and ``airports.npz``/``airports.wkb.json`` (the
    DSF cover) hold the same airports, in the same order, with the same bounds."""
    out = limits.run_rule(tmp_path, _two_airports())
    from orthostudio.airports_vec.artefact import read_airports
    from orthostudio.dsf.zones import airport_covers
    from orthostudio.mesh.weights import read_airport_bounds

    from_json = read_airport_bounds(out, TILE.name)
    from_record = np.array([r["boundary"].bounds for r in read_airports(out)])
    covers = airport_covers(out)
    from_covers = np.array([(c.xmin, c.ymin, c.xmax, c.ymax) for c in covers])
    np.testing.assert_array_equal(from_json, from_record)
    np.testing.assert_array_equal(from_json, from_covers)
    assert len(covers) == 2 and all(cover.is_icao for cover in covers)


def test_the_clean_record_is_what_the_dsf_node_reads() -> None:
    """Turned round (was ``test_defect_the_clean_record_has_no_reader_in_the_graph``). The
    DSF airport cover reads ``airports.npz``/``airports.wkb.json``; the mesh reads the bounds of
    ``airports.json``, a view of the same airports (the file-to-reader table is in
    ``docs/specs/airports-integration.md`` section 4)."""
    callers = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "read_airports" in path.read_text(encoding="utf-8")
    )
    assert callers == ["airports_vec/artefact.py", "dsf/zones.py"]


# -- 2. atomicity of the files wave 2 added ---------------------------------------------------


def test_a_failure_of_the_record_rolls_the_raster_back_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turned round (was ``test_defect_a_failure_after_the_record_leaves_final_named_files``).

    ``write_airports`` writes to the ``.part`` names ``write_elevation_and_airports`` stages, so a
    failure of the record leaves nothing behind: no raster, no window, no record, no ``.part``.
    """
    from orthostudio.vectors import rule as rule_mod
    from orthostudio.vectors.assemble import VectorLayers

    builder = limits.Store()
    limits.aerodrome(builder, 5.49, 43.49, 0.03, "LFAA")
    builder.way([(5.495, 43.5), (5.515, 43.5)], aeroway="runway", width="30")
    encoded, _, smoothed, _ = limits.run_chain(builder)
    build = rule_mod.LayerBuild(layers=VectorLayers(), dem=smoothed, airports=encoded.airports)

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rule_mod, "write_airports", disk_full)
    with pytest.raises(OSError):
        rule_mod.write_elevation_and_airports(tmp_path, TILE, build)
    assert sorted(p.name for p in tmp_path.iterdir()) == []

    monkeypatch.undo()
    rule_mod.write_elevation_and_airports(tmp_path, TILE, build)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        f"Data{TILE.name}.alt",
        "airports.npz",
        "airports.wkb.json",
        "dem.json",
    ]


# -- 3. coded events: emitted, then dropped ---------------------------------------------------


def test_a_rejected_runway_is_logged_as_a_warning_and_counted_in_stats(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Turned round (was ``test_defect_a_rejected_runway_leaves_no_trace_above_debug_nor_in_
    stats``). ``build_layers`` logs each coded event at the level of its registry severity
    (DEGRADED -> WARNING) and ``stats.json`` carries the family counters (``families``) and
    the events counted by code (``events``)."""
    osm = limits.OsmXml()
    limits.aerodrome(osm, 5.49, 43.49, 0.03, "LFAR")
    osm.way(
        [(5.50, 43.50), (5.51, 43.50), (5.505, 43.505), (5.50, 43.50)],
        closed=True,
        aeroway="runway",
    )
    caplog.set_level(logging.DEBUG)
    out = limits.run_rule(tmp_path, osm)
    assert REGISTRY["OSM_RUNWAY_REJECTED"].severity.name == "DEGRADED"
    rejected = [r for r in caplog.records if "was rejected" in r.getMessage()]
    assert rejected, "the event exists"
    assert {r.levelno for r in rejected} == {logging.WARNING}
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert stats["families"]["airports"]["runways_rejected"] == 1
    assert stats["events"]["OSM_RUNWAY_REJECTED"] == 1


def test_every_code_the_airport_package_emits_is_registered() -> None:
    """Review 5's registry scan covers ``src/orthostudio/vectors`` only and misses the emission
    helpers of ``airports_vec`` (``_event``, ``_report``, ``_drop``); this one covers them."""
    patterns = (
        r'OsxpError\(\s*"([A-Z0-9_]+)"',
        r'_event\(\s*on_event,\s*"([A-Z0-9_]+)"',
        r'_report\(\s*on_event,\s*"([A-Z0-9_]+)"',
        r'on_skip\(\s*"([A-Z0-9_]+)"',
        r'_skip\([^,]+,\s*"([A-Z0-9_]+)"',
    )
    used: set[str] = set()
    for path in sorted((SRC / "airports_vec").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            used |= set(re.findall(pattern, text))
    assert {"OSM_RUNWAY_REJECTED", "OSM_AIRPORT_TOO_SMALL", "OSM_AIRPORT_SURFACE_INVALID"} <= used
    assert sorted(code for code in used if code not in REGISTRY) == []


# -- 4. cancellation and cost on a tile dense in aerodromes -----------------------------------


def test_cancellation_is_seen_at_the_next_noding_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turned round (was ``test_defect_cancellation_waits_for_every_noding_pass``).

    ``node_layers`` reads the token before every pass: a token set during the first pass
    stops the noding before the second one (review 6 measured 47 s of latency on 324
    synthetic aerodromes before the fix).
    """
    from orthostudio.vectors import noding

    cancel = threading.Event()
    calls: list[int] = []
    original = noding._insert

    def insert(graph, new):  # type: ignore[no-untyped-def]
        calls.append(len(new.a))
        cancel.set()
        return original(graph, new)

    monkeypatch.setattr(noding, "_insert", insert)
    with vectors_job(VectorsJob(cancel=cancel)), pytest.raises(OsxpError) as err:
        limits.run_rule(tmp_path, _dense(3))
    assert err.value.code == "SYS_CANCELLED"
    assert len(calls) == 1, f"{len(calls)} passes ran after the token was set"


def test_the_noding_no_longer_rebuilds_the_whole_graph_at_every_airport_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turned round (was ``test_defect_the_noding_rebuilds_the_whole_graph_at_every_airport_
    pass``). ``noding._insert`` now re-processes only the existing edges the layer's box meets
    (``_insert_full`` stays as the reference and as the fallback). Measured after the fix
    (nice -n 10, load ~1.2): 324 synthetic aerodromes 3.8 s of noding instead of 53.2 s.

    Deterministic form: quadrupling the aerodromes quadruples the passes, and the existing
    edges the passes re-process grow about as much, not ten times.
    """
    from orthostudio.vectors import noding

    original_full = noding._insert_full
    original_filter = noding._edge_prefilter

    def run(side: int) -> tuple[int, int, int]:
        touched: list[int] = []
        fallbacks: list[int] = []

        def prefilter(nodes, edges, a_new, b_new):  # type: ignore[no-untyped-def]
            cand = original_filter(nodes, edges, a_new, b_new)
            touched.append(len(cand))
            return cand

        def full(graph, new):  # type: ignore[no-untyped-def]
            if graph is not None:
                fallbacks.append(len(graph.edges))
            return original_full(graph, new)

        monkeypatch.setattr(noding, "_edge_prefilter", prefilter)
        monkeypatch.setattr(noding, "_insert_full", full)
        limits.run_rule(tmp_path / str(side), _dense(side))
        return len(touched), sum(touched), sum(fallbacks)

    passes_small, edges_small, full_small = run(2)
    passes_large, edges_large, full_large = run(4)
    print(f"passes {passes_small}->{passes_large}, edges re-processed {edges_small}->{edges_large}")
    assert passes_large >= 3.5 * passes_small
    assert edges_large <= 6 * edges_small, (passes_small, edges_small, passes_large, edges_large)
    # the grid and border passes of a whole tile legitimately re-process most of the graph;
    # the fallback never runs on these layers
    assert full_small == full_large == 0


# -- 5. documentation that reaches the user ----------------------------------------------------


def test_no_speed_up_is_announced_that_nobody_measured() -> None:
    """Turned round (was ``test_defect_the_cli_help_announces_a_speed_up_nobody_measured``):
    the CLI help said *6x faster*; ``docs/benchmarks/p4-airports.md`` measures x4.9 on the stage
    and x4.0 on the tile."""
    cli = (SRC / "cli.py").read_text(encoding="utf-8")
    assert "6x faster" not in cli
    bench = (SRC.parents[1] / "docs" / "benchmarks" / "p4-airports.md").read_text(encoding="utf-8")
    assert "\u00d74.9" in bench
    assert not re.search(r"[\u00d7x]6\b|6x", bench)


def test_nothing_in_the_engine_reads_or_writes_a_pickle() -> None:
    """Inventory, so that a pickle cannot come back unnoticed: the ``Data<tile>.apt`` bridge and
    its allow-listed reader went with Ortho4XP's stages."""
    found = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if re.search(
            r"pickle\.(load|dump)s?\(|Unpickler\)|\.Unpickler\(", path.read_text(encoding="utf-8")
        )
    )
    assert found == []


def test_the_geodesy_has_one_definition() -> None:
    """Amended by the review-6 fixes: ``airports_vec.model`` now imports ``LAT_TO_M`` and
    ``M_TO_LAT`` from ``orthostudio.vectors.water`` and ``helipads`` uses ``model.m_to_lon`` (its
    private ``_m_to_lon`` is gone). The bit-for-bit agreement stays as a guard."""
    from orthostudio.airports_vec import helipads, model
    from orthostudio.vectors import water

    assert model.LAT_TO_M is water.LAT_TO_M
    assert model.M_TO_LAT is water.M_TO_LAT
    assert helipads.m_to_lon is model.m_to_lon
    assert not hasattr(helipads, "_m_to_lon")
    for lat in (-89.5, -43.0, 0.0, 1.0, 43.0, 43.5, 60.0, 89.0):
        assert model.m_to_lon(lat) == water.M_TO_LAT / np.cos(np.pi * lat / 180)
