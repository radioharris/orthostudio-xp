# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Adversarial review 6 (P4 wave 2), robustness lens: the airport chain at its edges.

Every test here runs the **native** airport chain -- ``orthostudio.airports_vec.stage`` alone, or
the whole rule ``orthostudio.vectors@1`` through :func:`orthostudio.vectors.rule.run_vectors` -- on
small OSM layers written by hand. No fixture, no network: the reference tile
``+43+005`` exercises none of these cases (``docs/specs/vectors-assembly.md`` 8.3).

Convention of the review series: a test whose name starts with ``test_defect_`` asserts the
*current, wrong* behaviour on purpose, so that the finding is reproducible and the test turns
red the day it is fixed (it must then be turned round). The other tests pin behaviour that was
verified correct.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely import geometry

from orthostudio.airports_vec import discover as apt_discover
from orthostudio.airports_vec import stage
from orthostudio.airports_vec.artefact import read_airports
from orthostudio.airports_vec.model import Airport, SurfaceAreas
from orthostudio.dem.dem import Dem
from orthostudio.errors import OsxpError
from orthostudio.graph.rule import ResolvedInput, RunContext
from orthostudio.model import TileRef
from orthostudio.sources.osm import LAYERS, OsmNode, OsmSnapshot, OsmWay, SnapshotStore
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.rule import VECTORS, VectorsParams, run_vectors
from orthostudio.vectors.triangle_files import read_node_file, read_poly_file

TILE = TileRef(43, 5)
RUNWAY, TAXIWAY, HANGAR = 16, 32, 128


# -- builders --------------------------------------------------------------------------------


class Store:
    """An :class:`OsmData` filled as the Ortho4XP reader fills it (negative ids, reading order)."""

    def __init__(self) -> None:
        self.store = OsmData(tile=TILE)
        self._node = self._way = 0

    def node(self, lon: float, lat: float, **tags: str) -> int:
        self._node -= 1
        self.store.nodes[self._node] = (lon, lat)
        if tags:
            self.store.tags["n"][self._node] = dict(tags)
            self.store.first["n"].add(self._node)
        return self._node

    def way(self, points: list[tuple[float, float]], *, closed: bool = False, **tags: str) -> int:
        """A way; ``closed`` reuses the first node id at the end, as OSM closes a ring."""
        ids = [self.node(lon, lat) for lon, lat in (points[:-1] if closed else points)]
        if closed:
            ids.append(ids[0])
        self._way -= 1
        self.store.ways[self._way] = ids
        if tags:
            self.store.tags["w"][self._way] = dict(tags)
            self.store.first["w"].add(self._way)
        return self._way


class OsmXml:
    """The same content as an OSM layer, for the full rule: its snapshot or its ``.osm`` text."""

    def __init__(self) -> None:
        self.nodes: list[tuple[int, float, float, dict[str, str]]] = []
        self.ways: list[tuple[int, list[int], dict[str, str]]] = []

    def node(self, lon: float, lat: float, **tags: str) -> int:
        self.nodes.append((len(self.nodes) + 1, lon, lat, tags))
        return len(self.nodes)

    def way(self, points: list[tuple[float, float]], *, closed: bool = False, **tags: str) -> int:
        ids = [self.node(lon, lat) for lon, lat in (points[:-1] if closed else points)]
        if closed:
            ids.append(ids[0])
        self.ways.append((len(self.ways) + 1, ids, tags))
        return len(self.ways)

    def text(self) -> str:
        out = ["<?xml version='1.0' encoding='UTF-8'?>", "<osm version='0.6' generator='r6'>"]
        for i, lon, lat, tags in self.nodes:
            out.append(f"  <node id='{i}' lat='{lat:.7f}' lon='{lon:.7f}'>")
            out += [f"    <tag k='{k}' v='{v}' />" for k, v in tags.items()]
            out.append("  </node>")
        for i, ids, tags in self.ways:
            out.append(f"  <way id='{i}'>")
            out += [f"    <nd ref='{r}' />" for r in ids]
            out += [f"    <tag k='{k}' v='{v}' />" for k, v in tags.items()]
            out.append("  </way>")
        out.append("</osm>")
        return "\n".join(out) + "\n"

    def snapshot(self, tile: TileRef, layer: str) -> OsmSnapshot:
        return OsmSnapshot(
            tile=tile, layer=layer, selectors=LAYERS[layer].selectors, query="q", mirror="test",
            fetched_at="", generator="", osm_base="",
            nodes=tuple(OsmNode(i, lat, lon, tags) for i, lon, lat, tags in self.nodes),
            ways=tuple(OsmWay(i, tuple(ids), tags) for i, ids, tags in self.ways),
            relations=(), digest="0" * 64,
        )  # fmt: skip


def square(lon: float, lat: float, side: float) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + side, lat), (lon + side, lat + side), (lon, lat + side), (lon, lat)]


def synthetic_dem(tile: TileRef = TILE, side: int = 361) -> Dem:
    """A smooth, non-flat raster over ``[-0.01, 1.01]``, a function of absolute lon/lat."""
    x0, x1 = -0.01, 1.01
    xs = np.linspace(x0, x1, side)
    ys = np.linspace(x1, x0, side)
    grid_x, grid_y = np.meshgrid(xs, ys)
    alt = 200 + 80 * np.sin((tile.lon + grid_x) * 9.0) + 40 * np.cos((tile.lat + grid_y) * 13.0)
    return Dem(
        tile=tile,
        alt_dem=alt.astype(np.float32),
        x0=x0,
        y0=x0,
        x1=x1,
        y1=x1,
        nxdem=side,
        nydem=side,
    )


def run_chain(builder: Store, dem: Dem | None = None) -> tuple[Any, Dem, Dem, list[OsxpError]]:
    """Steps 1 to 15 of ``include_airports`` exactly as ``build_layers`` sequences them."""
    events: list[OsxpError] = []
    dem = dem if dem is not None else synthetic_dem()
    airports, _ = stage.build_airports(builder.store, TILE, on_event=events.append)
    smoothed = stage.smooth_elevation(dem, airports)
    encoded = stage.encode(airports, TILE, smoothed, store=builder.store, on_event=events.append)
    return encoded, dem, smoothed, events


def run_rule(root: Path, airports: OsmXml, tile: TileRef = TILE, **params: Any) -> Path:
    """``orthostudio.vectors@1`` on a snapshot store: empty coastline and water, this aeroway
    layer."""
    osm = root / "osm"
    store = SnapshotStore(osm)
    for layer, content in (("coastline", OsmXml()), ("water", OsmXml()), ("airports", airports)):
        store.save(content.snapshot(tile, layer))
    dem_dir = root / "dem"
    synthetic_dem(tile).save(dem_dir)
    out = root / "out"
    out.mkdir()
    scratch = root / "scratch"
    scratch.mkdir()
    values = {"tile": tile.name, "road_level": 0, "mesh_zl": 12, **params}
    ctx = RunContext(
        rule=VECTORS,
        key="0" * 64,
        params=VectorsParams(**values),
        inputs={
            "osm": ResolvedInput("osm", "a" * 64, osm),
            "dem": ResolvedInput("dem", "b" * 64, dem_dir),
            "patches": ResolvedInput("patches", None, None),
            "airports": ResolvedInput("airports", None, None),
        },
        out=out,
        scratch=scratch,
    )
    run_vectors(ctx)
    return out


def marked_nodes(out: Path, tile: TileRef, marker: int) -> np.ndarray:
    """``(n, 3)`` x, y, z of the nodes of every edge carrying ``marker``."""
    nodes = read_node_file(out / f"Data{tile.name}.node")
    poly = read_poly_file(out / f"Data{tile.name}.poly", first_index=nodes.first_index)
    ids = np.unique(poly.segments[(poly.markers & marker) > 0].ravel())
    xyz = np.column_stack([nodes.xy, nodes.attributes[:, 0]])
    return xyz[ids]


def codes(events: list[OsxpError]) -> list[str]:
    return [event.code for event in events]


def aerodrome(builder: Store | OsmXml, lon: float, lat: float, side: float, code: str) -> None:
    builder.way(square(lon, lat, side), closed=True, aeroway="aerodrome", icao=code, name=code)


# -- 1. a tile with no aerodrome at all ----------------------------------------------------


def test_a_tile_without_any_airport_builds_and_publishes_empty_records(tmp_path: Path) -> None:
    """The empty aeroway layer is the common case of the planet: nothing must crash or be
    invented, and the record must still be readable."""
    out = run_rule(tmp_path, OsmXml())
    assert not (out / f"Data{TILE.name}.apt").exists()
    assert read_airports(out) == []
    # ``airports.json`` (wave 1, the mesh's own reader) is only written when there are bounds
    assert not (out / "airports.json").exists()
    from orthostudio.mesh.weights import read_airport_bounds

    assert read_airport_bounds(out, TILE.name).shape == (0, 4)
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    for name in ("RUNWAY", "TAXIWAY", "APRON", "HANGAR"):
        assert name not in stats["seeds_by_marker"]


def test_without_airports_the_smoothing_only_moves_the_border_by_float_rounding() -> None:
    """``apt_smoothing_pix`` is 8 even with no airport, so the border re-blend runs.

    Measured: it is a convex combination of a value with itself, evaluated in float32 (NEP 50),
    so a few hundred border samples move by one ulp -- as in Ortho4XP, whose arithmetic is the same.
    Nothing inside the tile moves.
    """
    dem = synthetic_dem()
    _, raw, smoothed, events = run_chain(Store(), dem)
    moved = smoothed.alt_dem != raw.alt_dem
    assert events == []
    assert np.abs(smoothed.alt_dem - raw.alt_dem).max() < 1e-3
    assert not moved[8:-8, 8:-8].any()


# -- 2. aerodromes without runways, helipads alone, tiny aerodromes ------------------------


def test_an_aerodrome_node_without_runway_is_dropped_with_a_code() -> None:
    builder = Store()
    builder.node(5.5, 43.5, aeroway="aerodrome", icao="LFAA", name="No runway")
    encoded, _, _, events = run_chain(builder)
    assert len(encoded.airports) == 0
    assert codes(events) == ["OSM_AIRPORT_TOO_SMALL"]
    assert encoded.layers.layers == ()


def test_an_aerodrome_outline_without_runway_is_kept_and_smoothed_but_not_encoded() -> None:
    builder = Store()
    aerodrome(builder, 5.49, 43.49, 0.01, "LFAB")
    encoded, raw, smoothed, _ = run_chain(builder)
    assert len(encoded.airports) == 1
    assert encoded.layers.bounds.shape == (1, 4)
    assert encoded.counts["runways"] == 0
    assert not {"RUNWAY", "TAXIWAY", "HANGAR"} & set(encoded.layers.seeds)
    assert (smoothed.alt_dem != raw.alt_dem)[8:-8, 8:-8].any(), "the footprint is smoothed"


def test_a_helipad_alone_is_flattened_without_inventing_an_airport(tmp_path: Path) -> None:
    osm = OsmXml()
    osm.node(5.5, 43.5, aeroway="helipad")
    out = run_rule(tmp_path, osm)
    assert read_airports(out) == []
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert stats["seeds_by_marker"].get("INTERP_ALT", 0) >= 1
    assert not {"RUNWAY", "TAXIWAY", "HANGAR"} & set(stats["seeds_by_marker"])


def test_a_tiny_aerodrome_is_discarded_with_a_code_not_silently() -> None:
    builder = Store()
    aerodrome(builder, 5.5, 43.5, 0.0003, "LFAH")
    encoded, _, _, events = run_chain(builder)
    assert len(encoded.airports) == 0
    assert "OSM_AIRPORT_TOO_SMALL" in codes(events)


# -- 3. degenerate geometry ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("single_node_runway", "OSM_AIRPORT_SURFACE_INVALID"),
        ("single_node_taxiway", "OSM_AIRPORT_SURFACE_INVALID"),
        ("degenerate_hangar", "OSM_AIRPORT_SURFACE_INVALID"),
        ("bowtie_outline", "OSM_AIRPORT_BOUNDARY_INVALID"),
        ("bowtie_runway_area", None),
        ("collinear_runway_area", None),
        ("zero_length_runway", None),
    ],
)
def test_degenerate_osm_geometry_never_crashes_the_chain(case: str, expected: str | None) -> None:
    builder = Store()
    if case == "bowtie_outline":
        builder.way(
            [(5.49, 43.49), (5.52, 43.52), (5.52, 43.49), (5.49, 43.52), (5.49, 43.49)],
            closed=True,
            aeroway="aerodrome",
            icao="LFAD",
            name="bowtie",
        )
    else:
        aerodrome(builder, 5.49, 43.49, 0.03, "LFAC")
    good_runway = case not in ("single_node_runway", "zero_length_runway")
    if good_runway:
        builder.way([(5.495, 43.5), (5.515, 43.5)], aeroway="runway", width="30")
    if case == "single_node_runway":
        builder.way([(5.5, 43.5)], aeroway="runway")
    elif case == "zero_length_runway":
        builder.way([(5.5, 43.5), (5.5, 43.5)], aeroway="runway")
    elif case == "single_node_taxiway":
        builder.way([(5.5, 43.502)], aeroway="taxiway")
    elif case == "degenerate_hangar":
        builder.way([(5.5, 43.505), (5.501, 43.505), (5.5, 43.505)], closed=True, aeroway="hangar")
    elif case == "bowtie_runway_area":
        builder.way(
            [(5.50, 43.50), (5.51, 43.505), (5.51, 43.50), (5.50, 43.505), (5.50, 43.50)],
            closed=True,
            aeroway="runway",
        )
    elif case == "collinear_runway_area":
        builder.way(
            [(5.50, 43.50), (5.51, 43.50), (5.52, 43.50), (5.50, 43.50)],
            closed=True,
            aeroway="runway",
        )
    encoded, _, smoothed, events = run_chain(builder)
    assert np.isfinite(smoothed.alt_dem).all()
    for geom, _marker, z in encoded.layers.layers:
        assert geom.is_valid or geom.geom_type.endswith("LineString")
        assert z is None or np.isfinite(z).all()
    if expected is not None:
        assert expected in codes(events)


def test_a_rejected_area_runway_is_reported_with_a_code() -> None:
    """A closed runway that is not a rectangle is not flattened: the event says so."""
    builder = Store()
    aerodrome(builder, 5.49, 43.49, 0.03, "LFAR")
    builder.way(
        [(5.50, 43.50), (5.51, 43.50), (5.505, 43.505), (5.50, 43.50)],
        closed=True,
        aeroway="runway",
    )
    encoded, _, _, events = run_chain(builder)
    assert "OSM_RUNWAY_REJECTED" in codes(events)
    assert encoded.counts["runways"] == 0


# -- 4. the tile border ----------------------------------------------------------------------


def test_a_runway_crossing_the_tile_border_is_cut_to_the_tile(tmp_path: Path) -> None:
    osm = OsmXml()
    aerodrome(osm, 4.99, 43.49, 0.03, "LFAE")
    osm.way([(4.995, 43.5), (5.015, 43.5)], aeroway="runway", width="30")
    out = run_rule(tmp_path, osm)
    runway = marked_nodes(out, TILE, RUNWAY)
    assert len(runway) > 0
    assert runway[:, 0].min() >= 0.0 and runway[:, 0].max() <= 1.0
    assert runway[:, 1].min() >= 0.0 and runway[:, 1].max() <= 1.0
    # its footprint is published untruncated (the DSF cover reads it, arbitration B4)
    (record,) = read_airports(out)
    assert record["boundary"].bounds[0] < 0.0


def test_a_runway_across_the_north_east_corner_does_not_crash() -> None:
    builder = Store()
    aerodrome(builder, 5.985, 43.985, 0.03, "LFAF")
    builder.way([(5.99, 43.999), (6.01, 44.001)], aeroway="runway", width="45")
    encoded, _, smoothed, _ = run_chain(builder)
    assert encoded.counts["runways"] == 1
    assert np.isfinite(smoothed.alt_dem).all()
    for geom, marker, _ in encoded.layers.layers:
        if marker == RUNWAY:
            xmin, ymin, xmax, ymax = geom.bounds
            assert xmin >= -1e-9 and xmax <= 1 + 1e-9 and ymin >= -1e-9 and ymax <= 1 + 1e-9


def test_an_aerodrome_entirely_outside_the_tile_is_kept_but_draws_nothing_in_it() -> None:
    """The Overpass box has a margin: an aerodrome just west of the tile arrives in the layer.

    It is kept (Ortho4XP keeps it) and published in the footprints, but no runway edge and no
    seed of it may land in the tile.
    """
    builder = Store()
    aerodrome(builder, 4.97, 43.49, 0.02, "LFAG")
    builder.way([(4.972, 43.5), (4.985, 43.5)], aeroway="runway", width="30")
    encoded, _, _, _ = run_chain(builder)
    assert len(encoded.airports) == 1
    assert encoded.layers.bounds[0, 2] < 0.0
    assert all(marker != RUNWAY for _, marker, _ in encoded.layers.layers)
    assert "RUNWAY" not in encoded.layers.seeds


def test_defect_a_runway_across_two_tiles_steps_at_the_seam(tmp_path: Path) -> None:
    """The two tiles an aerodrome straddles each fit the runway altitude on their own raster.

    Kept as a defect on purpose after the review-6 fixes: it is inherited from Ortho4XP and a fix
    would be a wanted difference with a cost to measure; it is documented as a known limit in
    ``docs/specs/vectors-assembly.md`` 8.3.

    ``AltitudeField`` fits a degree-7 polynomial of the smoothed raster **of the tile** along
    the whole axis, and each tile's raster is re-blended to raw at its own border. Measured
    on a runway crossing lon 5.0: the vertices the two tiles put on the seam are not the same
    ones (3 in common) and the common ones differ by 0.74 m. Ortho4XP does the same arithmetic,
    so this is inherited, not a fidelity defect -- but it is a step on a runway at a DSF seam.
    """
    osm = OsmXml()
    osm.way(
        [(4.98, 43.49), (5.02, 43.49), (5.02, 43.52), (4.98, 43.52), (4.98, 43.49)],
        closed=True,
        aeroway="aerodrome",
        icao="LFSE",
        name="Seam",
    )
    osm.way([(4.985, 43.503), (5.015, 43.507)], aeroway="runway", width="45")
    seams: dict[str, dict[float, float]] = {}
    for tile, border_x in ((TileRef(43, 5), 0.0), (TileRef(43, 4), 1.0)):
        out = run_rule(tmp_path / tile.name, osm, tile)
        runway = marked_nodes(out, tile, RUNWAY)
        on_seam = runway[np.abs(runway[:, 0] - border_x) < 1e-9]
        assert len(on_seam) >= 2, f"{tile.name}: the runway must reach the seam"
        seams[tile.name] = {round(float(y), 7): float(z) for _, y, z in on_seam}
    common = set(seams["+43+005"]) & set(seams["+43+004"])
    assert len(common) < len(seams["+43+005"]), "the two tiles do not share their seam vertices"
    step = max(abs(seams["+43+005"][y] - seams["+43+004"][y]) for y in common)
    assert step > 0.1, f"runway step at the seam: {step:.3f} m"


# -- 5. crossed runways ---------------------------------------------------------------------


def test_crossed_runways_leave_a_seed_inside_their_crossing() -> None:
    builder = Store()
    aerodrome(builder, 5.49, 43.49, 0.03, "LFAI")
    builder.way([(5.495, 43.505), (5.515, 43.505)], aeroway="runway", width="30")
    builder.way([(5.505, 43.495), (5.505, 43.515)], aeroway="runway", width="30")
    encoded, _, _, _ = run_chain(builder)
    assert encoded.counts["runways"] == 2
    (airport,) = list(encoded.airports)
    parts = [part.polygon for part in airport.areas.runway_as_line]
    crossing = parts[0].intersection(parts[1])
    assert crossing.area > 0
    seeds = encoded.layers.seeds["RUNWAY"]
    inside = [crossing.contains(geometry.Point(p)) for p in seeds]
    assert any(inside), "Triangle4XP would leave the crossing without the RUNWAY attribute"


# -- 6. what the chain reads of the outside world --------------------------------------------


def test_the_chain_needs_no_apt_dat_and_never_imports_the_icao_index() -> None:
    """Arbitration B5, checked in a fresh interpreter rather than by reading the source."""
    import subprocess
    import sys

    probe = (
        "import sys, orthostudio.vectors.layers, orthostudio.vectors.rule, "
        "orthostudio.airports_vec.stage;print('orthostudio.airports' in sys.modules)"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=120
    )
    assert done.stdout.strip() == "False"


# -- 7. an untrusted OSM tag no longer reaches the smoothing unbounded ------------------------


def _one_airport_with_tag(smoothing_pix: int) -> Airport:
    box = geometry.MultiPolygon([geometry.box(0.40, 0.40, 0.50, 0.50)])
    airport = Airport(
        key="LFTG",
        key_type="icao",
        name="tagged",
        repr_node=(5.45, 43.45),
        boundary=box,
        smoothing_pix=smoothing_pix,
    )
    airport.areas = SurfaceAreas(runway=box, taxiway=box, apron=box, hangar=box)
    return airport


def test_a_negative_smoothing_pix_tag_is_ignored_with_a_code() -> None:
    """Turned round (was ``test_defect_a_negative_smoothing_pix_tag_crashes_with_an_uncoded_
    error``). ``discover._smoothing_pix`` ignores a value outside ``0..MAX_SMOOTHING_PIX``
    and reports ``OSM_AIRPORT_SMOOTHING_INVALID``; a footprint built by other means is bounded
    the same way by ``smoothing._tag_pix``, so ``smooth_elevation`` no longer dies of a bare
    ``ValueError`` in ``numpy.convolve`` (``airports-geometry.md`` difference 8)."""
    reported: list[tuple[str, dict[str, Any]]] = []
    tag = {"smoothing_pix": "-3"}
    assert apt_discover._smoothing_pix(tag, lambda c, x: reported.append((c, x)), "LFTG") is None
    assert [code for code, _ in reported] == ["OSM_AIRPORT_SMOOTHING_INVALID"]
    from orthostudio.airports_vec.model import AirportSet

    airports = AirportSet()
    airports.add(_one_airport_with_tag(-3))
    dem = synthetic_dem(side=201)
    smoothed = stage.smooth_elevation(dem, airports)
    reference = AirportSet()
    reference.add(_one_airport_with_tag(8))  # the tile default the tag falls back to
    assert np.array_equal(smoothed.alt_dem, stage.smooth_elevation(dem, reference).alt_dem)

    builder = Store()
    builder.way(
        square(5.49, 43.49, 0.03), closed=True, aeroway="aerodrome", icao="LFNG", smoothing_pix="-3"
    )
    builder.way([(5.495, 43.5), (5.515, 43.5)], aeroway="runway", width="30")
    encoded, _, _, events = run_chain(builder)
    assert "OSM_AIRPORT_SMOOTHING_INVALID" in codes(events)
    assert [a.smoothing_pix for a in encoded.airports] == [None]


def test_a_huge_smoothing_pix_tag_is_ignored_not_materialised() -> None:
    """Turned round (was ``test_defect_a_huge_smoothing_pix_tag_is_accepted_without_bound``):
    a tag of 1 000 000 000 used to reach ``_triangular_kernel`` as is (review 6 killed a
    30 000 000 run after five minutes at 1.15 GB); it is now ignored like any value outside
    ``0..MAX_SMOOTHING_PIX``, and the region takes the tile setting."""
    from orthostudio.airports_vec.smoothing import (
        SmoothingParams,
        airport_regions,
        max_smoothing_pix,
    )
    from orthostudio.vectors.rule import MAX_SMOOTHING_PIX

    huge = 1_000_000_000
    assert apt_discover._smoothing_pix({"smoothing_pix": str(huge)}) is None
    assert apt_discover._smoothing_pix({"smoothing_pix": str(MAX_SMOOTHING_PIX)}) == 1000
    footprint = _one_airport_with_tag(huge)
    regions = airport_regions(synthetic_dem(side=201), [footprint], SmoothingParams())
    assert [region.pix for region in regions] == [8]
    assert max_smoothing_pix([footprint]) == 8
    assert huge > MAX_SMOOTHING_PIX
