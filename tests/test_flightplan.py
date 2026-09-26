# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A flight plan turned into its line and its squares (``orthostudio.flightplan``).

The first flight plan drew one route, counted squares on a second and measured a third; on
KJFK-EGLL the squares counted and the great circle parted by 83 squares (review of 2026-09-23).
Every test here holds the one rule that replaced them: the squares are those the drawn path enters.
"""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import pytest

from orthostudio.airports import haversine_km, tiles_within
from orthostudio.flightplan import (
    great_circle,
    has_scenery,
    path_of,
    pieces_of,
    plan_of,
    squares_along,
    squares_near,
)
from orthostudio.model import TileRef

LSGG = (46.2381, 6.1089)
LFMN = (43.6584, 7.2159)
KJFK = (40.6413, -73.7781)
EGLL = (51.4700, -0.4543)
RJTT = (35.5494, 139.7798)
PHNL = (21.3187, -157.9225)


def _sampled(points: list[tuple[float, float]], step_km: float = 0.2) -> set[TileRef]:
    """The squares a very fine walk along the same great circles stands in: what the exact grid
    walk must never miss."""
    out = set()
    for lat, lon in path_of(points, step_km):
        out.add(TileRef(max(-90, min(89, math.floor(lat))), ((math.floor(lon) + 180) % 360) - 180))
    return out


def test_a_great_circle_starts_on_its_point_and_steps_evenly() -> None:
    steps = great_circle(LSGG, LFMN, 10.0)
    assert steps[0] == LSGG
    assert LFMN not in steps  # the next leg starts there
    gaps = [haversine_km(*a, *b) for a, b in pairwise(steps)]
    assert max(gaps) <= 10.0 and max(gaps) - min(gaps) < 0.01
    assert haversine_km(*steps[-1], *LFMN) <= 10.0


def test_the_great_circle_is_the_short_way_and_bends_north() -> None:
    """New York to London flies north of both: the great circle, not a straight line in degrees."""
    path = path_of([KJFK, EGLL])
    assert path[0] == KJFK and path[-1] == EGLL
    assert max(lat for lat, _ in path) > 53.0  # the drawn line reached 51.5 at most
    total = sum(haversine_km(*a, *b) for a, b in pairwise(path))
    assert abs(total - haversine_km(*KJFK, *EGLL)) < 1.0


@pytest.mark.parametrize(
    "points",
    [[LSGG, LFMN], [KJFK, EGLL], [RJTT, PHNL], [LSGG, (45.5, 5.4), (44.2, 4.6), (39.551, 2.738)]],
    ids=["geneva-nice", "new-york-london", "tokyo-honolulu", "a-navigation-log"],
)
def test_the_squares_are_every_square_the_path_enters(points: list[tuple[float, float]]) -> None:
    exact = squares_along(path_of(points))
    assert len(exact) == len(set(exact))  # each once
    sampled = _sampled(points)
    assert sampled <= set(exact), sorted(s.name for s in sampled - set(exact))
    # what the fine walk did not stand in can only be a corner clipped by a few hundred metres
    assert len(set(exact) - sampled) <= 2


def test_a_square_clipped_at_its_corner_is_counted() -> None:
    """The first version missed +01+000 on a line that clips it (review of 2026-09-23): this one
    enters it at 0.97 E on the parallel and leaves it at 1.03 N on the meridian."""
    names = [s.name for s in squares_along([(0.05, 0.0), (1.05, 1.02)])]
    assert names == ["+00+000", "+01+000", "+01+001"]
    # and one that crosses the meridian first goes through the other neighbour
    names = [s.name for s in squares_along([(0.05, 0.0), (1.05, 1.5)])]
    assert names == ["+00+000", "+00+001", "+01+001"]


def test_the_squares_come_in_the_order_they_are_flown() -> None:
    names = [s.name for s in squares_along(path_of([LSGG, LFMN]))]
    assert names[0] == "+46+006" and names[-1] == "+43+007"
    lats = [int(n[:3]) for n in names]
    assert lats == sorted(lats, reverse=True)  # southbound all the way


def test_the_pacific_is_crossed_the_short_way_and_drawn_within_the_world() -> None:
    path = path_of([RJTT, PHNL])
    pieces = pieces_of(path)
    assert len(pieces) == 2
    for piece in pieces:
        assert all(-180.0 <= lon <= 180.0 for _, lon in piece)
    (lat_a, lon_a), (lat_b, lon_b) = pieces[0][-1], pieces[1][0]
    assert {lon_a, lon_b} == {180.0, -180.0} and lat_a == lat_b
    squares = squares_along(path)
    lons = {s.lon for s in squares}
    assert 179 in lons and -180 in lons
    assert all(-180 <= s.lon <= 179 for s in squares)
    assert not any(-100 < s.lon < 100 for s in squares)  # never round the long way


def test_the_ends_are_the_airport_fields_squares_departure_first() -> None:
    plan = plan_of([LSGG, LFMN], radius_km=15.0)
    ends = plan["squares"]["ends"]
    dep = [s.name for s in tiles_within(*LSGG, 15.0)]
    arr = [s.name for s in tiles_within(*LFMN, 15.0)]
    assert ends == dep + [n for n in arr if n not in dep]


def test_along_leaves_out_the_ends_and_keeps_the_flying_order() -> None:
    """With no radius, the route is exactly the squares its line enters."""
    plan = plan_of([LSGG, LFMN], radius_km=0.0)
    ends, along = plan["squares"]["ends"], plan["squares"]["along"]
    assert not set(ends) & set(along)
    flown = [s.name for s in squares_along(path_of([LSGG, LFMN]))]
    assert along == [n for n in flown if n not in ends]
    assert plan["left_out"] == 0
    assert plan["length_km"] == pytest.approx(haversine_km(*LSGG, *LFMN), abs=0.1)


def _within(points: list[tuple[float, float]], radius_km: float) -> set[TileRef]:
    """Every square whose nearest point is within ``radius_km`` of a very fine walk along the
    route, plus the squares it stands in: the corridor, found the slow way."""
    out = set(_sampled(points))
    for lat, lon in path_of(points, 0.2):
        out.update(tiles_within(lat, lon, radius_km))
    return out


@pytest.mark.parametrize(
    "points", [[LSGG, LFMN], [KJFK, EGLL], [RJTT, PHNL]], ids=["alps", "atlantic", "pacific"]
)
def test_the_corridor_is_every_square_within_the_radius_of_the_route(
    points: list[tuple[float, float]],
) -> None:
    """A square seen from the aircraft is chosen: every square within the airport field's radius
    of the route, not only those the line enters (a user, 2026-09-25). Sampled every kilometre,
    it finds every square nearer than the radius less half a kilometre, and none beyond it."""
    got = set(squares_near(points, 15.0))
    assert _within(points, 14.5) <= got <= _within(points, 15.0)
    assert set(squares_along(path_of(points))) <= got  # the line's own squares, always
    assert len(got) == len(squares_near(points, 15.0))  # each once


def test_a_square_seven_kilometres_beside_the_route_is_taken_with_the_radius() -> None:
    """The route of the pilot's test passed some 7 km from the corner of +46+011 without choosing
    it (2026-09-25): taken with a radius of 10 km, not with 5, never with none."""
    route = [(47.36, 11.0), (46.76, 13.0)]
    names = lambda r: {s.name for s in squares_near(route, r)}  # noqa: E731
    assert "+46+011" not in names(0.0) and "+46+011" not in names(5.0)
    assert "+46+011" in names(10.0)
    assert {"+47+011", "+47+012", "+46+012"} <= names(0.0)


def test_the_corridor_comes_in_the_order_it_is_flown() -> None:
    near = [s.name for s in squares_near([LSGG, LFMN], 15.0)]
    assert near.index("+46+006") < near.index("+45+006") < near.index("+44+007")


def test_squares_x_plane_has_no_scenery_for_are_left_out_and_counted() -> None:
    kept = {"+46+006", "+45+006", "+43+007"}
    plan = plan_of([LSGG, LFMN], radius_km=15.0, has_land=lambda s: s.name in kept)
    assert set(plan["squares"]["ends"]) | set(plan["squares"]["along"]) <= kept
    everything = plan_of([LSGG, LFMN], radius_km=15.0)
    total = len(everything["squares"]["ends"]) + len(everything["squares"]["along"])
    shown = len(plan["squares"]["ends"]) + len(plan["squares"]["along"])
    assert plan["left_out"] == total - shown > 0


def test_the_bounds_hold_a_pacific_crossing_together() -> None:
    bounds = plan_of([RJTT, PHNL], radius_km=15.0)["bounds"]
    assert bounds["west"] == pytest.approx(RJTT[1], abs=1e-4)
    assert bounds["east"] == pytest.approx(PHNL[1] + 360.0, abs=1e-4)
    assert bounds["east"] - bounds["west"] < 180.0


def test_the_drawn_path_is_small_and_rounded() -> None:
    plan = plan_of([KJFK, EGLL], radius_km=15.0)
    points = [p for piece in plan["path"] for p in piece]
    assert len(points) < 700  # 5 540 km in steps of 10
    assert all(len(str(v).split(".")[-1]) <= 5 for p in points for v in p)


def test_a_plan_needs_two_points() -> None:
    with pytest.raises(ValueError):
        plan_of([LSGG], radius_km=15.0)


def test_scenery_is_looked_for_where_x_plane_keeps_it(tmp_path: Path) -> None:
    """The Global Scenery's own files, and X-Plane 12's Demo Areas beside them."""
    root = tmp_path / "Global Scenery" / "X-Plane 12 Global Scenery"
    own = root / TileRef(46, 6).dsf_relpath
    own.parent.mkdir(parents=True)
    own.write_bytes(b"dsf")
    demo = tmp_path / "Global Scenery" / "X-Plane 12 Demo Areas" / TileRef(47, 11).dsf_relpath
    demo.parent.mkdir(parents=True)
    demo.write_bytes(b"dsf")
    has = has_scenery(root)
    assert has(TileRef(46, 6))
    assert has(TileRef(47, 11))
    assert not has(TileRef(40, -40))  # the middle of the Atlantic
