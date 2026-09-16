"""The wave-2 wiring itself: the seam and the published record.

They need no reference build: they exercise
:mod:`orthostudio.airports_vec.stage` and :mod:`orthostudio.airports_vec.artefact` on airports built
by hand. Spec: ``docs/specs/airports-integration.md`` sections 2, 4 and 6.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely import geometry

from orthostudio.airports_vec.artefact import (
    AIRPORTS_JSON,
    AIRPORTS_NPZ,
    read_airports,
    write_airports,
)
from orthostudio.airports_vec.model import Airport, AirportSet, RunwayPart, SurfaceAreas
from orthostudio.airports_vec.stage import AirportParams, views_of
from orthostudio.errors import OsxpError
from orthostudio.model import TileRef

TILE = TileRef(43, 5)
SRC = Path(__file__).resolve().parents[1] / "src" / "orthostudio"


def _airport(key: str = "LFXX") -> Airport:
    """One fully built airport: two runways, the four areas and a boundary."""
    square = geometry.Polygon([(0.1, 0.1), (0.2, 0.1), (0.2, 0.2), (0.1, 0.2)])
    strip = geometry.Polygon([(0.3, 0.3), (0.5, 0.3), (0.5, 0.32), (0.3, 0.32)])
    airport = Airport(
        key=key,
        key_type="icao",
        name="Test field",
        repr_node=(5.15, 43.15),
        boundary=geometry.MultiPolygon([square]),
        areas=SurfaceAreas(
            runway=geometry.MultiPolygon([strip]),
            runway_as_area=(RunwayPart(strip, np.array([0.3, 0.31]), np.array([0.5, 0.31]), 30.0),),
            runway_as_line=(
                RunwayPart(square, np.array([0.1, 0.15]), np.array([0.2, 0.15]), 18.5),
            ),
            taxiway=geometry.MultiPolygon([square]),
            apron=geometry.MultiPolygon([square]),
            hangar=geometry.MultiPolygon([square]),
        ),
    )
    airport.ways["taxiway"].extend([11, 12])
    airport.ways["apron"].append(21)
    airport.ways["hangar"].append(31)
    airport.runway_rels.append(41)
    return airport


@pytest.fixture
def airports() -> AirportSet:
    out = AirportSet()
    out.add(_airport("LFXX"))
    out.add(_airport("LFYY"))
    return out


# -- the seam (spec 2) -------------------------------------------------------------------


def test_the_view_projects_the_record_without_computing_anything(airports: AirportSet) -> None:
    """``views_of`` is a nine-field projection, in insertion order, sharing the geometries."""
    views = views_of(airports)
    assert [view.key for view in views] == airports.keys()
    for view, airport in zip(views, airports, strict=True):
        built = airport.areas
        assert view.runway_area is built.runway
        assert view.taxiway_area is built.taxiway
        assert view.apron_area is built.apron
        assert view.hangars is built.hangar
        assert view.boundary is airport.boundary
        assert view.taxiway_ways == tuple(airport.ways["taxiway"])
        assert view.apron_ways == tuple(airport.ways["apron"])
        # Ortho4XP concatenates ``runway[1] + runway[2]`` at every call site
        assert [r.polygon for r in view.runways] == [
            built.runway_as_area[0].polygon,
            built.runway_as_line[0].polygon,
        ]


def test_a_half_built_airport_views_as_empty_rather_than_raising() -> None:
    """An airport whose areas were never built encodes to nothing, not to a traceback."""
    view = views_of(AirportSet({"X": Airport("X", "name", "X", (5.0, 43.0))}))[0]
    assert view.runways == ()
    assert view.runway_area.is_empty and view.boundary.is_empty


def test_the_smoothing_parameters_carry_the_one_configurable_number() -> None:
    assert AirportParams().smoothing().apt_smoothing_pix == 8
    assert AirportParams(smoothing_pix=0).smoothing().apt_smoothing_pix == 0


# -- the published record (spec 4) ------------------------------------------------------------


def test_the_dsf_cover_reads_the_record(airports: AirportSet, tmp_path: Path) -> None:
    """``orthostudio.dsf.zones.airport_covers`` reads the bounds from the record."""
    from orthostudio.dsf import airport_covers

    write_airports(tmp_path, airports, TILE)
    covers = airport_covers(tmp_path)
    assert len(covers) == 2 and all(cover.is_icao for cover in covers)
    assert all((c.xmin, c.ymin, c.xmax, c.ymax) == (0.1, 0.1, 0.2, 0.2) for c in covers)
    assert airport_covers(tmp_path / "no-airport") == []


def test_nothing_in_the_engine_pickles() -> None:
    """Rule R-I5: OrthoStudio XP neither writes nor reads a pickle (the ``Data<tile>.apt`` bridge
    went with Ortho4XP's stages)."""
    found = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "pickle.dump" in path.read_text(encoding="utf-8")
        or "pickle.load" in path.read_text(encoding="utf-8")
    }
    assert found == set()


# -- OrthoStudio XP's own record (spec 4) ----------------------------------------------------------


def test_the_native_record_round_trips(airports: AirportSet, tmp_path: Path) -> None:
    write_airports(tmp_path, airports, TILE)
    assert (tmp_path / AIRPORTS_NPZ).is_file() and (tmp_path / AIRPORTS_JSON).is_file()
    back = read_airports(tmp_path)
    assert [record["key"] for record in back] == airports.keys()
    for record, airport in zip(back, airports, strict=True):
        assert record["key_type"] == airport.key_type
        assert record["name"] == airport.name
        assert record["ways"]["taxiway"] == airport.ways["taxiway"]
        assert record["runway_as_rel"] == airport.runway_rels
        assert record["boundary"].equals_exact(airport.boundary, 1e-12)
        assert record["runway"].equals_exact(airport.areas.runway, 1e-12)
        assert record["hangar"].equals_exact(airport.areas.hangar, 1e-12)
        assert len(record["runways"]) == 2
        assert record["runways"][0]["width"] == 30.0
        assert record["runways"][0]["polygon"].equals_exact(
            airport.areas.runway_as_area[0].polygon, 1e-12
        )


def test_a_record_of_another_format_is_refused(airports: AirportSet, tmp_path: Path) -> None:
    write_airports(tmp_path, airports, TILE)
    (tmp_path / AIRPORTS_JSON).write_text('{"format": "osxp-airports-0", "airports": []}')
    with pytest.raises(OsxpError) as excinfo:
        read_airports(tmp_path)
    assert excinfo.value.code == "SYS_INTERNAL_ERROR"


def test_a_point_keyed_airport_survives_the_round_trip(tmp_path: Path) -> None:
    """Ortho4XP keys a nameless aerodrome by its representative node (``:110``, ``:243``)."""
    airport = _airport("LFXX")
    keyed = Airport(
        key=(5.25, 43.25),
        key_type="repr_node",
        name=airport.name,
        repr_node=(5.25, 43.25),
        boundary=airport.boundary,
        areas=airport.areas,
    )
    write_airports(tmp_path, AirportSet({keyed.key: keyed}), TILE)
    assert read_airports(tmp_path)[0]["key"] == (5.25, 43.25)
