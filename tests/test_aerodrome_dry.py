# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The water an aerodrome holds is taken out of it (``docs/specs/aerodrome-dry.md``).

Haneda's taxiway bridges sat under the water's surface in our tiles (forum report, 2026-10-13),
and Avranches' whole aerodrome lay in the sea of OpenStreetMap's coastline. XPME, a mesh many
pilots use, turns the water inside the aerodrome OpenStreetMap draws into land (measured on its
tiles, 2026-10-14): the same here.
"""

from __future__ import annotations

from pathlib import Path

import shapely
from shapely import geometry

from orthostudio.model import TileRef
from orthostudio.vectors.coast import build_sea_layers
from orthostudio.vectors.layers import dry_aerodromes
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.water import (
    LAT_TO_M,
    DryAerodromes,
    WaterParams,
    build_water_layers,
    lon_to_m,
)

TILE = TileRef(48, -2)
SCALE = (lon_to_m(TILE.lat + 0.5), LAT_TO_M)


def _osm(
    path: Path, ways: list[tuple[list[tuple[float, float]], dict[str, str]]], layer: str
) -> OsmData:
    """An OSM file of the given ways (tile-local coordinates), loaded as ``layer``."""
    nodes, way_xml, node_id = [], [], 0
    for way_id, (coords, tags) in enumerate(ways, start=1):
        refs = []
        for x, y in coords:
            if refs and (x, y) == coords[0]:
                refs.append(refs[0])
                continue
            node_id += 1
            nodes.append(
                f'  <node id="{node_id}" lat="{y + TILE.lat:.7f}" '
                f'lon="{x + TILE.lon:.7f}" version="1"/>\n'
            )
            refs.append(node_id)
        nd = "".join(f'    <nd ref="{r}"/>\n' for r in refs)
        tg = "".join(f'    <tag k="{k}" v="{v}"/>\n' for k, v in tags.items())
        way_xml.append(f'  <way id="{way_id}" version="1">\n{nd}{tg}  </way>\n')
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">\n'
        + "".join(nodes)
        + "".join(way_xml)
        + "</osm>"
    )
    return OsmData.load(path, layer=layer, tile=TILE)


def _box(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


AERODROME = _box(0.25, 0.24, 0.35, 0.30)
RUNWAY = [(0.26, 0.27), (0.34, 0.27)]


def test_an_aerodrome_with_a_runway_on_land_is_taken_out_of_the_water(tmp_path: Path) -> None:
    store = _osm(
        tmp_path / "a.osm",
        [(AERODROME, {"aeroway": "aerodrome"}), (RUNWAY, {"aeroway": "runway"})],
        "airports",
    )
    dry = dry_aerodromes(store, TILE)
    assert dry is not None and abs(dry.outlines.area - 0.1 * 0.06) < 1e-12
    assert dry.surfaces.equals(geometry.LineString(RUNWAY))


def test_a_seaplane_base_keeps_its_water(tmp_path: Path) -> None:
    """Its runways are water, or it has none: drying it would put a meadow where seaplanes land."""
    on_water = _osm(
        tmp_path / "s.osm",
        [
            (AERODROME, {"aeroway": "aerodrome"}),
            (RUNWAY, {"aeroway": "runway", "surface": "water"}),
        ],
        "airports",
    )
    none = _osm(tmp_path / "n.osm", [(AERODROME, {"aeroway": "aerodrome"})], "airports")
    assert dry_aerodromes(on_water, TILE) is None and dry_aerodromes(none, TILE) is None


def test_a_lake_loses_what_the_runway_touches_inside_the_aerodrome(tmp_path: Path) -> None:
    lake = _osm(tmp_path / "w.osm", [(_box(0.2, 0.2, 0.3, 0.3), {"natural": "water"})], "water")
    outline = geometry.Polygon(AERODROME)
    dry = DryAerodromes(outline, geometry.LineString(RUNWAY), SCALE)
    before = build_water_layers(lake, TILE, WaterParams())
    after = build_water_layers(lake, TILE, WaterParams(), dry=dry)
    taken = before.water.area - after.water.area
    assert abs(taken - 0.05 * 0.06) < 1e-9 and abs(after.dry.area - taken) < 1e-12
    assert after.counts["dry"] == 1 and "dry" not in before.counts
    seeds = [geometry.Point(at) for at in after.seeds["WATER"]]
    assert seeds and all(after.water.contains(at) and not outline.contains(at) for at in seeds)
    elsewhere = DryAerodromes(
        geometry.box(0.6, 0.6, 0.7, 0.7), geometry.box(0.6, 0.6, 0.7, 0.7), SCALE
    )
    away = build_water_layers(lake, TILE, WaterParams(), dry=elsewhere)
    assert away.counts == before.counts and away.dry.is_empty
    assert all(
        shapely.equals_exact(a[0], b[0], 0) for a, b in zip(away.layers, before.layers, strict=True)
    )


def test_an_aerodrome_with_a_water_runway_keeps_its_water(tmp_path: Path) -> None:
    """Fairbanks and Kenai have float ponds beside their runways, and their taxiways run into them
    (census, 2026-10-14). Kenai's water runway is tagged ``surface=unpaved``: its name says it."""
    for water_runway in (
        {"aeroway": "runway", "ref": "2W/20W", "surface": "unpaved"},
        {"aeroway": "runway", "ref": "02/20", "surface": "water"},
    ):
        store = _osm(
            tmp_path / "f.osm",
            [
                (AERODROME, {"aeroway": "aerodrome"}),
                (RUNWAY, {"aeroway": "runway", "ref": "02L/20R"}),
                ([(0.26, 0.25), (0.34, 0.25)], water_runway),
            ],
            "airports",
        )
        assert dry_aerodromes(store, TILE) is None


def test_a_line_entering_the_water_by_less_than_20_m_does_not_count(tmp_path: Path) -> None:
    """Lake Hood's lake, the seaplane base of Anchorage, is entered by 9 m of a line, a map that
    hardly meets the other: its water stays; 30 m of a taxiway is a taxiway over the water."""
    lake = _osm(tmp_path / "w.osm", [(_box(0.2, 0.2, 0.3, 0.3), {"natural": "water"})], "water")
    before = build_water_layers(lake, TILE, WaterParams())
    for metres, taken in ((9, False), (30, True)):
        into = 0.3 - metres / SCALE[0]
        dry = DryAerodromes(
            geometry.Polygon(AERODROME), geometry.LineString([(0.32, 0.27), (into, 0.27)]), SCALE
        )
        after = build_water_layers(lake, TILE, WaterParams(), dry=dry)
        assert (not after.dry.is_empty) is taken, metres
        assert (after.counts == before.counts) is not taken, metres


def test_water_inside_the_aerodrome_that_nothing_touches_stays_water(tmp_path: Path) -> None:
    """Cazaux (2026-10-14): the air base's outline holds 419 ha of its lake, far from any runway,
    taxiway or apron; drying it laid a flat, blurred meadow on the lake. XPME keeps that water."""
    lake = _osm(tmp_path / "w.osm", [(_box(0.2, 0.2, 0.3, 0.3), {"natural": "water"})], "water")
    before = build_water_layers(lake, TILE, WaterParams())
    dry = DryAerodromes(
        geometry.Polygon(AERODROME), geometry.LineString([(0.31, 0.27), (0.34, 0.27)]), SCALE
    )
    after = build_water_layers(lake, TILE, WaterParams(), dry=dry)
    assert after.dry.is_empty and after.counts == before.counts
    assert all(
        shapely.equals_exact(a[0], b[0], 0)
        for a, b in zip(after.layers, before.layers, strict=True)
    )


def test_the_sea_stops_at_the_aerodrome(tmp_path: Path) -> None:
    """A coastline across the tile, land to its left (south), sea to the north; an aerodrome
    reaching into the sea with a runway in it: the sea loses it, its outline joins the SEA lines,
    no seed is in it."""
    coast = geometry.MultiLineString([[(1.0, 0.5), (0.0, 0.5)]])
    box = geometry.box(0.4, 0.45, 0.45, 0.7)
    dry = DryAerodromes(box, geometry.LineString([(0.425, 0.46), (0.425, 0.69)]), SCALE)
    before = build_sea_layers(coast, TILE)
    after = build_sea_layers(coast, TILE, dry=dry)
    assert abs(after.dry.area - 0.05 * 0.2) < 1e-9
    assert abs(before.sea_polygons.area - after.sea_polygons.area - 0.05 * 0.2) < 1e-9
    assert len(after.sea_lines.geoms) > len(before.sea_lines.geoms)
    assert not any(box.contains(geometry.Point(p)) for p in after.seeds)
    assert after.sea_polygons.contains(geometry.MultiPoint([tuple(p) for p in after.seeds]))
    on_land = geometry.box(0.4, 0.1, 0.45, 0.2)
    untouched = build_sea_layers(coast, TILE, dry=DryAerodromes(on_land, on_land, SCALE))
    assert untouched.sea_lines.equals(before.sea_lines) and untouched.dry.is_empty
