"""Zones engine (spec ``docs/specs/map-zones.md`` sections 3 to 5, package A).

The ``osxp-zones-1`` document (validation, normalisation, atomic save with ``.bak``, the saved
document read zone by zone), its clipping into each tile's Ortho4XP ``zone_list`` (format, priority,
regions over several tiles, parts and holes, limits), the texture count of the estimate and
``osxp plan --zones``. No network, no Ortho4XP, no X-Plane: temporary files only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union
from typer.testing import CliRunner

from orthostudio.cli import app as cli_app
from orthostudio.dsf.params import DsfParams
from orthostudio.dsf.zones import _MAX_ZONES, _zone_image
from orthostudio.errors import OsxpError
from orthostudio.estimate import estimate, render_text
from orthostudio.graph import Store
from orthostudio.imagery.grid import texture_at, texture_bbox, textures_covering
from orthostudio.imagery.providers import load_registry
from orthostudio.model import TileRef
from orthostudio.pipeline.build import BuildEnv, BuildSpec
from orthostudio.zones import (
    MAX_TILE_ENTRIES,
    SavedZones,
    Zone,
    ZonesDocument,
    _entries,
    dumps_zones,
    load_zones,
    parse_zones_document,
    read_saved_zones,
    read_zones_file,
    save_zones,
    tiles_touched,
    with_zone_list,
    zone_list_textures,
    zone_textures,
    zones_file_revision,
    zones_for_tile,
    zones_from_geojson,
)

GENEVA = TileRef(46, 6)
LSGG = [[6.090, 46.225], [6.130, 46.225], [6.130, 46.250], [6.090, 46.250]]


def _zone(zone_id: str, polygon: list[Any], zl: int = 18, **extra: Any) -> Zone:
    return Zone.model_validate({"id": zone_id, "zl": zl, "polygon": polygon, **extra})


def _square(lon0: float, lat0: float, lon1: float, lat1: float) -> list[list[float]]:
    return [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1]]


def _shape(entry: tuple[list[float], int, str] | list[Any]) -> Polygon:
    coords = entry[0]
    return Polygon(list(zip(coords[1::2], coords[::2], strict=True)))


def _refused(code: str, data: Any, **kwargs: Any) -> OsxpError:
    with pytest.raises(OsxpError) as info:
        parse_zones_document(data, **kwargs)
    assert info.value.code == code, info.value
    return info.value


# -- the document ------------------------------------------------------------------------------


def test_a_zone_is_normalised_closing_vertex_dropped_and_nine_decimals() -> None:
    doc = parse_zones_document(
        {
            "format": "osxp-zones-1",
            "zones": [
                {
                    "id": "lsgg-18",
                    "name": "LSGG",
                    "zl": 18,
                    "provider": "",
                    "polygon": [[6.0900000000004, 46.225], *LSGG[1:], [6.09, 46.2250000000001]],
                }
            ],
        }
    )
    (zone,) = doc.zones
    assert zone.polygon == [tuple(p) for p in LSGG]  # closing vertex dropped after rounding
    assert zone.provider is None and zone.name == "LSGG"
    assert doc.model_dump(mode="json")["zones"][0] == {
        "id": "lsgg-18",
        "name": "LSGG",
        "zl": 18,
        "provider": None,
        "photo": {"look": None, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0},
        "polygon": LSGG,
    }


@pytest.mark.parametrize(
    ("zone", "needle"),
    [
        ({"polygon": [[6.1, 46.1], [6.2, 46.1]]}, "2 vertices"),
        ({"polygon": [[6.1, 46.1], [6.2, 46.1], [6.1, 46.1]]}, "2 vertices"),
        ({"polygon": [[6 + i / 5000, 46 + (i % 2) / 100] for i in range(2002)]}, "2002 vertices"),
        ({"polygon": [[181, 46.1], [6.2, 46.1], [6.2, 46.2]]}, "longitude 181"),
        ({"polygon": [[6.1, 85.5], [6.2, 85.5], [6.2, 85.4]]}, "latitude 85.5"),
        ({"polygon": [[6.1, 46.1], [6.2, 46.2], [6.2, 46.1], [6.1, 46.2]]}, "Self-intersection"),
        ({"zl": 11}, "zl"),
        ({"zl": 21}, "zl"),
        ({"provider": "NOPE"}, "provider NOPE is not in the registry"),
        ({"provider": "JP", "zl": 19}, "above 18, the maximum of provider JP"),
        ({"id": "a b"}, "id"),
        ({"id": "x" * 65}, "id"),
        ({"name": "n" * 81}, "name"),
    ],
)
def test_an_invalid_zone_refuses_the_whole_document(zone: dict[str, Any], needle: str) -> None:
    """A key this version does not know is not in this list: it is ignored, so that a file
    written by a newer OrthoStudio XP stays readable (test below, 2026-09-18)."""
    good = {"id": "good", "zl": 17, "polygon": _square(6.1, 46.1, 6.2, 46.2)}
    bad = {"id": "bad", "zl": 18, "polygon": _square(6.3, 46.3, 6.4, 46.4), **zone}
    err = _refused("ZONE_INVALID", {"format": "osxp-zones-1", "zones": [good, bad]})
    assert err.context["zone"] == bad["id"] and needle in err.context["reason"], err.context
    assert err.remedy and err.message.startswith(f"Zone {bad['id']} is invalid")


def test_document_limits_ids_and_format() -> None:
    zone = {"id": "a", "zl": 17, "polygon": _square(6.1, 46.1, 6.2, 46.2)}
    err = _refused("ZONE_INVALID", {"zones": [zone, zone]})
    assert err.context["zone"] == "a" and "used by another zone" in err.context["reason"]
    many = [{**zone, "id": f"z{i}"} for i in range(501)]
    err = _refused("ZONE_TOO_MANY", {"zones": many})
    assert err.context["count"] == 501 and err.context["limit"] == 500
    assert len(parse_zones_document({"zones": many[:500]}).zones) == 500
    err = _refused("ZONE_INVALID", {"format": "osxp-zones-2", "zones": []})
    assert err.context["zone"] == "" and "format" in err.context["reason"]
    assert _refused("ZONE_INVALID", [zone]).context["zone"] == ""
    # the registry the zones are checked against can be another one
    registry = {"BI": load_registry()["BI"].model_copy(update={"max_zl": 17})}
    doc = {"zones": [{**zone, "zl": 18, "provider": "BI"}]}
    assert "above 17" in _refused("ZONE_INVALID", doc, registry=registry).context["reason"]


def test_save_is_atomic_keeps_a_bak_and_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "home" / "zones.json"
    assert load_zones(path) == ZonesDocument()  # no file: no zones
    first = parse_zones_document({"zones": [{"id": "lsgg", "zl": 18, "polygon": LSGG}]})
    save_zones(path, first)
    assert not path.with_name("zones.json.bak").exists()
    assert json.loads(path.read_text("utf-8"))["format"] == "osxp-zones-1"
    assert load_zones(path) == first
    second = parse_zones_document(
        {"zones": [{"id": "gen", "name": "Genève", "zl": 17, "polygon": _square(6, 46, 6.3, 46.3)}]}
    )
    save_zones(path, second)
    assert load_zones(path) == second
    assert load_zones(path.with_name("zones.json.bak")) == first
    text = path.read_text("utf-8")
    assert "Genève" in text and text == dumps_zones(second)
    assert [p.name for p in path.parent.iterdir() if ".tmp-" in p.name] == []


def test_a_broken_saved_document_is_a_coded_error_never_ignored(tmp_path: Path) -> None:
    path = tmp_path / "zones.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OsxpError) as info:
        load_zones(path)
    assert info.value.code == "ZONE_INVALID" and info.value.context["path"] == str(path)
    path.write_text(json.dumps({"zones": [{"id": "x", "zl": 30, "polygon": LSGG}]}), "utf-8")
    with pytest.raises(OsxpError) as info:
        load_zones(path)
    assert info.value.context["zone"] == "x" and str(path) in info.value.message


# -- the saved document, read zone by zone ------------------------------------------------------
# Review finding: one invalid zone in zones.json refused the page and every build, even far away.

GO2 = {"id": "go2", "name": "Genève", "zl": 18, "provider": "GO2", "polygon": [*LSGG, LSGG[0]]}


def _saved(tmp_path: Path, content: Any) -> tuple[Path, SavedZones]:
    path = tmp_path / "zones.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content if isinstance(content, str) else json.dumps(content), "utf-8")
    return path, read_saved_zones(path)


def test_a_zone_with_a_problem_is_listed_as_stored_and_refuses_only_the_tiles_it_touches(
    tmp_path: Path,
) -> None:
    cape_town = {
        "id": "cpt",
        "zl": 17,
        "provider": "",
        "polygon": _square(18.4, -33.9, 18.6, -33.7),
    }
    path, saved = _saved(tmp_path, {"format": "osxp-zones-1", "zones": [cape_town, GO2]})
    assert saved.readable and saved.revision == hashlib.sha256(path.read_bytes()).hexdigest()
    assert saved.revision == zones_file_revision(path)
    with pytest.raises(OsxpError) as info:
        zones_file_revision(tmp_path)  # a folder in the way cannot be read: coded, not ignored
    assert info.value.code == "ZONE_INVALID" and info.value.context["path"] == str(tmp_path)
    # the valid zone normalised, the invalid one exactly as stored (closing vertex, name)
    empty_photo = {"look": None, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0}
    assert saved.listed == ({**cape_town, "name": "", "provider": None, "photo": empty_photo}, GO2)
    assert [zone.id for zone in saved.zones] == ["cpt"]
    (problem,) = saved.problems
    reason = "provider GO2 is not in the registry"
    message = f"Zone go2 of {path} is invalid: {reason}."
    expected = {"zone": "go2", "index": 1, "code": "ZONE_INVALID", "reason": reason}
    assert problem.to_dict() == {**expected, "message": message}
    assert saved.to_json() == {
        "format": "osxp-zones-1",
        "revision": saved.revision,
        "zones": list(saved.listed),
        "tiles": {},
        "problems": [problem.to_dict()],
    }
    assert [zone.id for zone in saved.zones_for([TileRef(-34, 18)])] == ["cpt"]
    assert saved.zones_for([TileRef(46, 7)]) == []  # nothing there, nothing refused
    with pytest.raises(OsxpError) as info:
        saved.zones_for([TileRef(-34, 18), GENEVA])
    assert info.value.code == "ZONE_INVALID" and info.value.context["zone"] == "go2"
    assert info.value.context["index"] == 1 and info.value.context["path"] == str(path)
    with pytest.raises(OsxpError) as info:
        load_zones(path)  # the strict reading refuses the file on its first problem
    assert info.value.context["zone"] == "go2"


def test_entries_that_cannot_be_shown_are_left_out_and_located_when_they_can_be(
    tmp_path: Path,
) -> None:
    ell = [[5.5, 45.5], [6.5, 45.5], [6.5, 46.5], [6.4, 46.5], [6.4, 45.6], [5.5, 45.6]]
    bow_tie = [[7.1, 47.1], [7.2, 47.2], [7.2, 47.1], [7.1, 47.2]]
    entries: list[Any] = [
        {**GO2, "id": "ell", "polygon": ell},  # listed; a valid polygon: told like a zone
        {"zl": 18, "polygon": LSGG},  # no id
        "not a zone",
        {"id": "text", "zl": "eighteen", "polygon": _square(7.2, 46.2, 7.3, 46.3)},
        {"id": "line", "zl": 18, "polygon": [[8.2, 46.2], [8.3, 46.3]]},  # listed; bounds
        {"id": "tie", "zl": 18, "polygon": bow_tie},  # listed; not simple: bounds
        {"id": "nowhere", "zl": 18},  # no polygon: touches no tile
        {"id": "wrong", "zl": 18, "polygon": [[9.2, 46.2, 420], [9.3, 46.2], [9.3, 46.3]]},
    ]
    path, saved = _saved(tmp_path, {"zones": entries})
    assert saved.readable and saved.zones == ()
    assert saved.listed == (entries[0], entries[4], entries[5])
    assert [(p.zone, p.index) for p in saved.problems] == [
        ("ell", 0), (None, 1), (None, 2), (None, 3), ("line", 4), ("tie", 5), (None, 6), (None, 7)
    ]  # fmt: skip
    messages = [p.error.message for p in saved.problems]
    assert messages[1] == f"Zone number 2 of {path} is invalid: id: Field required."
    assert "a zone is a JSON object" in messages[2] and messages[3].startswith("Zone text of")
    tiles = [TileRef(lat, lon) for lat in (45, 46, 47) for lon in (5, 6, 7, 8, 9)]
    touched = {(p.index, t.name) for p in saved.problems for t in tiles if p.touches(t)}
    assert touched == {
        (0, "+45+005"), (0, "+45+006"), (0, "+46+006"),  # not +46+005: in its bounds only
        (1, "+46+006"), (3, "+46+007"), (4, "+46+008"), (5, "+47+007"),
        (7, "+46+009"),  # the vertices that can be read
    }  # fmt: skip
    with pytest.raises(OsxpError) as info:
        saved.zones_for([TileRef(46, 5), TileRef(46, 6)])
    assert info.value.context["zone"] == "ell"
    assert saved.zones_for([TileRef(46, 5), TileRef(47, 6)]) == []


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        ('{"format": "osxp-zones-1", "zones": [', "it is not JSON"),
        (b"\xff\xfe{}", "it is not JSON"),
        ('{"format": "osxp-zones-1", "zones": [{"id": "a", "zl": NaN}]}', "NaN"),
        ('{"zones": [{"id": "a", "zl": 18, "polygon": [[1e999, 46]]}]}', "out of range"),
        ("[]", "not an osxp-zones-1 object"),
        ({"type": "FeatureCollection", "features": []}, "not an osxp-zones-1 object"),
        ({"format": "osxp-zones-2", "zones": []}, "format is not osxp-zones-1"),
        ({"format": "osxp-zones-1", "zones": {"lsgg": {}}}, "zones are not a list"),
    ],
)
def test_a_file_no_zone_can_be_read_from_is_one_problem_and_refuses_every_build(
    tmp_path: Path, content: Any, needle: str
) -> None:
    path, saved = _saved(tmp_path, content)
    before = path.read_bytes()
    assert not saved.readable and saved.listed == () and saved.zones == ()
    assert saved.revision == hashlib.sha256(before).hexdigest()
    (problem,) = saved.problems
    doc = problem.to_dict()
    assert doc["zone"] is None and doc["index"] is None and doc["code"] == "ZONE_INVALID"
    assert str(path) in doc["reason"] and needle in doc["reason"], doc
    assert "zones.json.bak" in problem.error.remedy
    with pytest.raises(OsxpError) as info:
        saved.zones_for([TileRef(-34, 18)])
    assert info.value.code == "ZONE_INVALID" and info.value.context["path"] == str(path)
    assert path.read_bytes() == before


def test_no_file_repeated_ids_the_limit_and_unexpected_keys(tmp_path: Path) -> None:
    assert read_saved_zones(tmp_path / "absent.json") == SavedZones(tmp_path / "absent.json", "")
    assert zones_file_revision(tmp_path / "absent.json") == ""
    zone = {"id": "a", "zl": 17, "polygon": _square(6.1, 46.1, 6.2, 46.2)}
    many = [{"id": f"z{i}", "zl": 17, "polygon": _square(18.4, -33.9, 18.6, -33.7)}
            for i in range(498)]  # fmt: skip
    last = {"id": "last", "zl": 17, "polygon": _square(8.1, 46.1, 8.2, 46.2)}
    lax = {**zone, "zl": "17"}  # valid (a zoom level may be a string) but not shown as stored
    document = {"format": "osxp-zones-1", "zones": [zone, lax, *many, last], "note": "by hand"}
    _path, saved = _saved(tmp_path, document)
    assert [(p.zone, p.index, p.error.code) for p in saved.problems] == [
        (None, None, "ZONE_INVALID"),
        ("a", 1, "ZONE_INVALID"),
        ("last", 500, "ZONE_TOO_MANY"),
    ]
    assert "unexpected key(s) note" in saved.problems[0].to_dict()["reason"]
    assert "used by another zone" in saved.problems[1].to_dict()["reason"]
    assert saved.problems[2].error.context["count"] == 501
    assert len(saved.listed) == 501 and len(saved.zones) == 499
    assert saved.listed[1] == {
        **zone,
        "name": "",
        "provider": None,
        "photo": {"look": None, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0},
    }  # normalised
    assert saved.listed[500] == last  # as stored
    # the unexpected key touches no tile; a zone with a problem refuses the tiles it touches
    assert saved.readable and len(saved.zones_for([TileRef(-34, 18)])) == 498
    for tile, code in ((GENEVA, "ZONE_INVALID"), (TileRef(46, 8), "ZONE_TOO_MANY")):
        with pytest.raises(OsxpError) as info:
            saved.zones_for([tile])
        assert info.value.code == code
    _path, saved = _saved(tmp_path, {"zones": [zone, *many[:1], last]})
    assert saved.problems == () and len(saved.zones) == 3


# -- clipping into zone_list -------------------------------------------------------------------


def test_zone_list_entry_format_is_the_ortho4xp_one() -> None:
    zone = _zone("lsgg", LSGG)
    assert zones_for_tile([zone], GENEVA, "BI") == [
        ([46.225, 6.09, 46.225, 6.13, 46.25, 6.13, 46.25, 6.09, 46.225, 6.09], 18, "BI")
    ]
    # the zone's provider wins over the tile's; a tile the zone does not reach gets nothing
    arc = _zone("lsgg", LSGG, provider="Arc")
    assert zones_for_tile([arc], GENEVA, "BI")[0][2] == "Arc"
    assert zones_for_tile([zone], TileRef(46, 7), "BI") == []
    assert zones_for_tile([], GENEVA, "BI") == []
    # rounded to 9 decimals, whatever the overlay library computes
    tilted = _zone("t", [[5.5, 46.123456789], [6.5, 46.2], [6.5, 46.4]])
    (entry,) = zones_for_tile([tilted], GENEVA, "BI")
    assert all(round(v, 9) == v for v in entry[0]) and entry[0][:2] == entry[0][-2:]


def test_a_region_over_four_tiles_compiles_into_four_zone_lists_whose_union_is_the_zone() -> None:
    region = _zone("region", _square(5.9, 45.9, 6.1, 46.1), zl=17)
    small = _zone("small", _square(5.95, 45.95, 6.05, 46.05), zl=19)
    tiles = tiles_touched(region)
    assert tiles == [TileRef(45, 5), TileRef(45, 6), TileRef(46, 5), TileRef(46, 6)]
    parts: list[Polygon] = []
    for tile in tiles:
        entries = zones_for_tile([small, region], tile, "BI")
        assert [e[1] for e in entries] == [19, 17], tile  # document order kept in every tile
        parts.append(_shape(entries[1]))
        assert _shape(entries[1]).within(_shape((_cell_ring(tile), 0, "")).buffer(1e-9))
    union = unary_union(parts)
    assert union.symmetric_difference(region.shape).area < 1e-12
    assert union.area == pytest.approx(region.shape.area)


def _cell_ring(tile: TileRef) -> list[float]:
    lat, lon = tile.lat, tile.lon
    return [lat, lon, lat, lon + 1, lat + 1, lon + 1, lat + 1, lon, lat, lon]


def test_priority_survives_the_clipping_in_the_real_painter() -> None:
    """Index 0 wins where zones overlap: checked on the 4096² image ``texture_map`` reads."""
    high = _zone("high", _square(6.10, 46.10, 6.30, 46.30), zl=19)
    low = _zone("low", _square(6.20, 46.20, 6.40, 46.40), zl=15)

    def zl_at(zones: list[Zone], lon: float, lat: float) -> int:
        cfg = with_zone_list({}, zones_for_tile(zones, GENEVA, "BI"), GENEVA)
        params = DsfParams.subset_of({**cfg, "default_zl": 16, "default_website": "BI"})
        image, values = _zone_image(GENEVA, params)
        x, y = round((lon - GENEVA.lon) * 4095), round((GENEVA.lat + 1 - lat) * 4095)
        return values[int(image[y, x])][0]

    assert zl_at([high, low], 6.25, 46.25) == 19
    assert zl_at([low, high], 6.25, 46.25) == 15
    assert zl_at([high, low], 6.35, 46.35) == 15 and zl_at([high, low], 6.5, 46.5) == 16


def test_a_multipolygon_clip_gives_adjacent_entries() -> None:
    # an upside-down U whose bar lies in +47+006: its two legs are two parts in +46+006
    legs = _zone(
        "legs",
        [[6.2, 46.8], [6.3, 46.8], [6.3, 47.1], [6.6, 47.1], [6.6, 46.8], [6.7, 46.8],
         [6.7, 47.2], [6.2, 47.2]],
        zl=18,
    )  # fmt: skip
    first = _zone("first", _square(6.0, 46.0, 6.1, 46.1), zl=17)
    after = _zone("after", _square(6.25, 46.85, 6.65, 46.95), zl=15)
    entries = zones_for_tile([first, legs, after], GENEVA, "BI")
    assert [e[1] for e in entries] == [17, 18, 18, 15]
    assert unary_union([_shape(e) for e in entries[1:3]]).area == pytest.approx(2 * 0.1 * 0.2)
    assert len(zones_for_tile([legs], TileRef(47, 6), "BI")) == 1  # the bar: one part


def test_holes_are_dropped_and_counted() -> None:
    ring = Polygon(
        [(6.1, 46.1), (6.5, 46.1), (6.5, 46.5), (6.1, 46.5)],
        holes=[[(6.2, 46.2), (6.3, 46.2), (6.3, 46.3), (6.2, 46.3)]],
    )
    entries, holes = _entries([ring], 18, "BI")
    assert holes == 1 and len(entries) == 1
    assert _shape(entries[0]).area == pytest.approx(0.4 * 0.4)  # the exterior ring, filled
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"zl": 17, "name": "with a hole"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[6.1, 46.1, 420.0], [6.5, 46.1], [6.5, 46.5], [6.1, 46.5], [6.1, 46.1]],
                        [[6.2, 46.2], [6.3, 46.2], [6.3, 46.3], [6.2, 46.3], [6.2, 46.2]],
                    ],
                },
            }
        ],
    }
    loaded = zones_from_geojson(geojson)
    assert loaded.holes_dropped == 1 and loaded.source == "geojson"
    (zone,) = loaded.document.zones
    assert zone.id == "feature-1" and zone.shape.area == pytest.approx(0.16)


def test_more_than_254_entries_for_one_tile_is_zone_too_many() -> None:
    assert MAX_TILE_ENTRIES == _MAX_ZONES == 254
    grid = [
        _zone(f"z{i}", _square(6 + (i % 20) / 20, 46 + (i // 20) / 20,
                               6 + (i % 20 + 1) / 20, 46 + (i // 20 + 1) / 20), zl=17)
        for i in range(255)
    ]  # fmt: skip
    assert len(zones_for_tile(grid[:254], GENEVA, "BI")) == 254
    with pytest.raises(OsxpError) as info:
        zones_for_tile(grid, GENEVA, "BI")
    err = info.value
    assert err.code == "ZONE_TOO_MANY" and err.context["tile"] == "+46+006"
    assert err.context["count"] == 255 and "254" in err.message


def test_zoom_level_above_the_tile_provider_is_refused_when_planned() -> None:
    zone = _zone("z20", LSGG, zl=20)  # provider null: accepted in the document
    assert zones_for_tile([zone], GENEVA, "Lux")[0][1] == 20  # Lux serves ZL20
    with pytest.raises(OsxpError) as info:
        zones_for_tile([zone], GENEVA, "BI")
    assert info.value.code == "ZONE_INVALID" and info.value.context["zone"] == "z20"
    assert "maximum of provider BI" in info.value.context["reason"]
    with pytest.raises(OsxpError) as info:
        zones_for_tile([zone], GENEVA, "NOPE")
    assert info.value.code == "CFG_PROVIDER_UNKNOWN"
    # the DSF gives each mesh cell one texture: a zone finer than mesh_zl would be stretched
    with pytest.raises(OsxpError) as info:
        zones_for_tile([zone], GENEVA, "Lux", mesh_zl=19)
    assert info.value.code == "ZONE_INVALID" and "above mesh_zl 19" in info.value.message
    assert "mesh_zl to 20" in info.value.remedy
    assert zones_for_tile([zone], GENEVA, "Lux", mesh_zl=20)[0][1] == 20


def test_tiles_touched_ignore_a_contact_along_an_edge() -> None:
    assert tiles_touched(_zone("lsgg", LSGG)) == [GENEVA]
    edge = _zone("edge", _square(6.5, 46.5, 7.0, 47.0))  # touches +46+007 and +47+006 on edges
    assert tiles_touched(edge) == [GENEVA]
    assert zones_for_tile([edge], TileRef(47, 6), "BI") == []
    wide = _zone("wide", _square(-0.5, 44.5, 2.5, 45.5), zl=15)
    assert len(tiles_touched(wide)) == 8


def test_with_zone_list_keeps_the_config_of_a_tile_without_zones() -> None:
    config = {"masks_width": 200, "cover_zl": 18}
    assert with_zone_list(config, [], GENEVA) == config
    entries = zones_for_tile([_zone("lsgg", LSGG)], GENEVA, "BI")
    out = with_zone_list(config, entries, GENEVA)
    assert out["zone_list"] == [[entries[0][0], 18, "BI"]] and config.get("zone_list") is None
    assert json.loads(json.dumps(out)) == out  # a job journal keeps the same value (same keys)
    assert DsfParams.subset_of(out).zone_list == entries
    with pytest.raises(OsxpError) as info:
        with_zone_list({"zone_list": [[_cell_ring(GENEVA), 15, "BI"]]}, entries, GENEVA)
    assert info.value.code == "CFG_VALUE_INVALID" and "+46+006" in info.value.message


# -- textures ----------------------------------------------------------------------------------


def test_zone_textures_count_the_squares_a_zone_overlaps() -> None:
    zone = _zone("lsgg", LSGG)
    expected = {
        t
        for t in textures_covering(46.250, 6.090, 46.225, 6.130, 18, "")
        if (b := texture_bbox(t))[1] < 6.130 and b[3] > 6.090 and b[2] < 46.250 and b[0] > 46.225
    }
    assert zone_textures(zone, GENEVA) == len(expected) > 0
    assert zone_textures(zone, TileRef(46, 7)) == 0
    entries = zones_for_tile([zone], GENEVA, "BI")
    assert zone_list_textures(entries, GENEVA) == {t._replace(provider="BI") for t in expected}
    # one texture square drawn on the grid is one texture, not its neighbours too
    t = texture_at(46.24, 6.11, 18, "BI")
    lat_max, lon_min, lat_min, lon_max = texture_bbox(t)
    square = _zone("sq", _square(lon_min, lat_min, lon_max, lat_max))
    assert zone_textures(square, GENEVA) == 1
    with pytest.raises(OsxpError) as info:
        zone_list_textures([([46.2, 6.1, 46.3], 18, "BI")], GENEVA)
    assert info.value.code == "CFG_ZONE_LIST_INVALID"


def _env(tmp_path: Path, tile: TileRef) -> BuildEnv:
    gs = tmp_path / "Global Scenery"
    p = gs / tile.dsf_relpath
    p.parent.mkdir(parents=True)
    p.write_bytes(b"XPLNEDSF")
    return BuildEnv(
        store=Store(tmp_path / "store", fsync=False),
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        global_scenery=gs,
        dsftool=None,
        registry=load_registry(),
        workers=4,
        library_path=None,
    )


def test_estimate_with_a_zl18_square_over_lsgg_counts_more_textures(tmp_path: Path) -> None:
    env = _env(tmp_path, GENEVA)

    def spec(config: dict[str, Any]) -> BuildSpec:
        return BuildSpec(
            tile=GENEVA, provider="BI", zl=16, out_dir=tmp_path / "out", config=config,
            store_root=tmp_path / "store",
            chunks_root=tmp_path / "chunks", workdir=tmp_path / "work",
        )  # fmt: skip

    (plain,) = estimate([spec({})], env=env).tiles
    zones = [_zone("lsgg-18", LSGG)]
    config = with_zone_list({}, zones_for_tile(zones, GENEVA, "BI"), GENEVA)
    est = estimate([spec(config)], env=env)
    (zoned,) = est.tiles
    extra = zone_textures(zones[0], GENEVA)
    assert plain.textures_zones == 0 and not zoned.textures_exact
    assert zoned.textures_total == plain.textures_total + extra and zoned.textures_zones == extra
    assert zoned.requests == plain.requests + 256 * extra
    assert zoned.dds_gb > plain.dds_gb and zoned.compute_s > plain.compute_s
    assert zoned.to_dict()["textures"]["zones"] == extra
    assert any("upper bound" in n and "zones" in n for n in zoned.notes)
    assert f"{extra} for the zones" in render_text(est)


# -- osxp plan / build --zones ------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("OSXP_HOME", str(h))
    return h


def _plan(tmp_path: Path, *extra: str) -> Any:
    gs = tmp_path / "Global Scenery"
    dsf = gs / GENEVA.dsf_relpath
    if not dsf.is_file():
        dsf.parent.mkdir(parents=True)
        dsf.write_bytes(b"XPLNEDSF")
    return CliRunner().invoke(
        cli_app,
        [
            "plan", "--tile", "+46+006", "--zl", "16", "--offline", "--json",
            "--global-scenery", str(gs), "--out", str(tmp_path / "out"), *extra,
        ],
    )  # fmt: skip


def test_cli_plan_with_zones_from_geojson_and_from_a_zones_document(
    home: Path, tmp_path: Path
) -> None:
    plain = _plan(tmp_path)
    assert plain.exit_code == 0, plain.output
    base = json.loads(plain.stdout)["tiles"][0]["textures"]
    geojson = tmp_path / "zones.geojson"
    feature = {
        "type": "Feature",
        "properties": {"zl": 18, "id": "lsgg", "provider": "BI"},
        "geometry": {"type": "MultiPolygon", "coordinates": [[[*LSGG, LSGG[0]]]]},
    }
    geojson.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}), "utf-8")
    result = _plan(tmp_path, "--zones", str(geojson))
    assert result.exit_code == 0, result.output
    textures = json.loads(result.stdout)["tiles"][0]["textures"]
    assert textures["zones"] > 0 and textures["total"] == base["total"] + textures["zones"]
    document = tmp_path / "zones.json"
    save_zones(document, read_zones_file(geojson).document)
    again = _plan(tmp_path, "--zones", str(document))
    assert again.exit_code == 0 and json.loads(again.stdout)["tiles"][0]["textures"] == textures
    assert read_zones_file(document).source == "osxp-zones-1"


def test_cli_refuses_a_bad_zones_file(home: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.geojson"
    feature = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon"}}
    bad.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}), "utf-8")
    result = _plan(tmp_path, "--zones", str(bad))
    assert result.exit_code == 1 and "ZONE_INVALID" in result.output
    assert "properties.zl" in result.output
    missing = _plan(tmp_path, "--zones", str(tmp_path / "nope.json"))
    assert missing.exit_code == 1 and "ZONE_INVALID" in missing.output
    neither = tmp_path / "neither.json"
    neither.write_text("[]", "utf-8")
    result = _plan(tmp_path, "--zones", str(neither))
    assert result.exit_code == 1 and "neither an osxp-zones-1 document" in result.output
    point = {"type": "Feature", "properties": {"zl": 18}, "geometry": {"type": "Point"}}
    bad.write_text(json.dumps({"type": "FeatureCollection", "features": [point]}), "utf-8")
    with pytest.raises(OsxpError) as info:
        read_zones_file(bad)
    assert "Point is not a Polygon" in info.value.context["reason"]


def test_a_document_from_a_newer_version_keeps_what_this_one_understands() -> None:
    """A user lost his zones when an engine that did not know ``tiles`` refused the file and the
    page offered to replace it (2026-09-18): unknown keys are ignored, never refused."""
    from orthostudio.zones import parse_zones_document

    doc = parse_zones_document(
        {
            "format": "osxp-zones-1",
            "invented_later": {"x": 1},
            "zones": [
                {
                    "id": "a",
                    "zl": 17,
                    "polygon": [[6.0, 46.0], [6.1, 46.0], [6.1, 46.1]],
                    "from_the_future": True,
                }
            ],
        }
    )
    assert [z.id for z in doc.zones] == ["a"] and doc.zones[0].zl == 17


def test_the_squares_own_colours_survive_a_save(tmp_path: Path) -> None:
    """``dumps_zones`` wrote the zones only: the colours of the squares were accepted by the API
    and thrown away by the writer, so nothing was ever remembered (2026-09-18)."""
    from orthostudio.zones import ZonesDocument, dumps_zones, read_saved_zones, save_zones

    doc = ZonesDocument.model_validate(
        {
            "format": "osxp-zones-1",
            "zones": [],
            "tiles": {"+46+006": {"photo": {"look": "custom", "saturation": -0.4}}},
        }
    )
    path = tmp_path / "zones.json"
    save_zones(path, doc)
    again = read_saved_zones(path)
    assert again.tiles["+46+006"].photo.look == "custom"
    assert again.tiles["+46+006"].photo.saturation == -0.4
    assert '"tiles"' in dumps_zones(doc)
    # a document without them writes exactly what it used to
    save_zones(path, ZonesDocument())
    assert '"tiles"' not in path.read_text()
