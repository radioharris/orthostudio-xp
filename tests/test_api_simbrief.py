"""The Plan's flight plan, read from SimBrief and turned into squares (``flight-plan.md``).

The user of the page asked for a button that draws his last plan and chooses the squares it flies
over (2026-09-19). No test here touches the network: the upstream is injected. What a real SimBrief
answers was checked once by hand: HTTP 400 and ``{"fetch": {"status": "Error: Unknown UserID"}}``
for a name it does not know.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orthostudio.api.simbrief import SIMBRIEF_URL, route_of, simbrief_router, simbrief_url
from orthostudio.model import TileRef

anyio_backend = "asyncio"

OFP: dict[str, Any] = {
    "general": {"route": "SOSAL UN871 BEBIX"},
    "origin": {"icao_code": "LSGG", "name": "Geneva", "pos_lat": "46.238", "pos_long": "6.109"},
    "destination": {
        "icao_code": "LEPA",
        "name": "Palma de Mallorca",
        "pos_lat": "39.551",
        "pos_long": "2.738",
    },
    "navlog": {
        "fix": [
            {"ident": "SOSAL", "name": "", "pos_lat": "45.5", "pos_long": "5.4", "stage": "CLB"},
            {"ident": "TOC", "name": "Top of climb", "pos_lat": None, "pos_long": None},
            {"ident": "BEBIX", "name": "", "pos_lat": "44.2", "pos_long": "4.6", "stage": "CRZ"},
            # the log ends on the destination, which the line must not hold twice
            {"ident": "LEPA", "name": "Palma", "pos_lat": "39.551", "pos_long": "2.738"},
        ]
    },
}


def _app(
    answers: dict[str, bytes], asked: list[str], user: str | None, scenery: Path | None = None
) -> FastAPI:
    def fetch(url: str) -> tuple[bytes | None, str]:
        asked.append(url)
        body = answers.get(url)
        return (body, "") if body is not None else (None, "unknown user")

    app = FastAPI()
    app.include_router(
        simbrief_router(lambda: user, global_scenery_of=lambda: scenery, fetch=fetch)
    )
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://osxp")


def test_a_plan_becomes_the_line_an_aircraft_flies() -> None:
    """The navigation log is the real path, departure and arrival procedures included, so it says
    which squares are flown over. A fix that does not say where it is cannot be drawn."""
    line = route_of(OFP)
    assert line["from"] == "LSGG" and line["to"] == "LEPA"
    assert [p["ident"] for p in line["points"]] == ["LSGG", "SOSAL", "BEBIX", "LEPA"]
    assert line["points"][0] == {"ident": "LSGG", "name": "Geneva", "lat": 46.238, "lon": 6.109}


def test_a_plan_with_no_log_still_draws_its_leg() -> None:
    """Some plans come back without a navigation log, and a log of one fix comes back as an object
    rather than a list: both still give a line between the two airports."""
    bare = {"origin": OFP["origin"], "destination": OFP["destination"]}
    assert [p["ident"] for p in route_of(bare)["points"]] == ["LSGG", "LEPA"]
    one = {**bare, "navlog": {"fix": OFP["navlog"]["fix"][0]}}
    assert [p["ident"] for p in route_of(one)["points"]] == ["LSGG", "SOSAL", "LEPA"]
    assert route_of({})["points"] == []


@pytest.mark.anyio
async def test_the_name_of_settings_is_what_is_asked_for() -> None:
    asked: list[str] = []
    url = f"{SIMBRIEF_URL}?username=pilot&json=1"
    async with _client(_app({url: json.dumps(OFP).encode()}, asked, "  pilot ")) as c:
        answer = await c.get("/api/flightplan/simbrief")
    assert answer.status_code == 200
    assert asked == [url]  # the name, nothing else of the user, and only when asked
    plan = answer.json()
    assert plan["from"] == "LSGG" and plan["to"] == "LEPA" and len(plan["points"]) == 4
    # the line and the squares come from one computation (orthostudio.flightplan)
    ends, along = plan["squares"]["ends"], plan["squares"]["along"]
    assert "+46+006" in ends and "+39+002" in ends
    assert along and not set(along) & set(ends)
    assert plan["path"][0][0] == [46.238, 6.109] and plan["path"][-1][-1] == [39.551, 2.738]
    assert plan["left_out"] == 0 and plan["scenery_checked"] is False
    assert 600 < plan["length_km"] < 800


@pytest.mark.anyio
async def test_no_name_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty in Settings, and nothing is ever asked of simbrief.com."""
    asked: list[str] = []
    async with _client(_app({}, asked, None)) as c:
        answer = await c.get("/api/flightplan/simbrief")
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "CFG_SIMBRIEF_USER_MISSING"
    assert not asked


@pytest.mark.anyio
async def test_a_name_they_do_not_know_says_so() -> None:
    asked: list[str] = []
    async with _client(_app({}, asked, "nobody")) as c:
        answer = await c.get("/api/flightplan/simbrief")
    assert answer.status_code == 404
    error = answer.json()["error"]
    assert error["code"] == "CFG_SIMBRIEF_USER_UNKNOWN" and "Settings" in error["remedy"]


@pytest.mark.anyio
async def test_a_plan_that_says_nothing_and_an_answer_that_is_not_a_plan() -> None:
    """Both leave the Plan as it was, with a sentence that says what to do."""
    url = f"{SIMBRIEF_URL}?username=pilot&json=1"
    async with _client(_app({url: b"not json at all"}, [], "pilot")) as c:
        answer = await c.get("/api/flightplan/simbrief")
    assert answer.status_code == 502 and answer.json()["error"]["code"] == "NET_SIMBRIEF_FAILED"
    empty = json.dumps({"origin": {}, "destination": {}}).encode()
    async with _client(_app({url: empty}, [], "pilot")) as c:
        answer = await c.get("/api/flightplan/simbrief")
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "CFG_SIMBRIEF_PLAN_EMPTY"


def test_a_name_goes_encoded_and_a_pilot_id_under_its_own_key() -> None:
    """A space or an ``&`` in the name changed the question (review of 2026-09-23); SimBrief
    takes a pilot ID as ``userid``, not as a name."""
    assert simbrief_url("Jean Pierre&Co") == f"{SIMBRIEF_URL}?username=Jean%20Pierre%26Co&json=1"
    assert simbrief_url("123456") == f"{SIMBRIEF_URL}?userid=123456&json=1"


@pytest.mark.anyio
async def test_errors_come_in_the_engines_one_shape() -> None:
    """The first route built its error bodies by hand, without context, so the page could not put
    the name or the host in its own words (review of 2026-09-23)."""
    async with _client(_app({}, [], "nobody")) as c:
        error = (await c.get("/api/flightplan/simbrief")).json()["error"]
    assert error["code"] == "CFG_SIMBRIEF_USER_UNKNOWN"
    assert error["severity"] == "blocking" and error["context"]["user"] == "nobody"
    assert error["message"] and error["remedy"]


@pytest.mark.anyio
async def test_squares_x_plane_has_no_scenery_for_are_left_out(tmp_path: Path) -> None:
    """A build stops on a square X-Plane has no scenery for, so the plan leaves it out and counts
    it: open sea, or a region the installer was not asked for."""
    root = tmp_path / "X-Plane 12 Global Scenery"
    for square in (TileRef(46, 6), TileRef(39, 2)):
        path = root / square.dsf_relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"dsf")
    url = f"{SIMBRIEF_URL}?username=pilot&json=1"
    async with _client(_app({url: json.dumps(OFP).encode()}, [], "pilot", root)) as c:
        plan = (await c.get("/api/flightplan/simbrief")).json()
    kept = set(plan["squares"]["ends"]) | set(plan["squares"]["along"])
    assert kept == {"+46+006", "+39+002"}
    assert plan["left_out"] > 0 and plan["scenery_checked"] is True


@pytest.mark.anyio
async def test_the_radius_is_the_pages_and_has_bounds() -> None:
    url = f"{SIMBRIEF_URL}?username=pilot&json=1"
    async with _client(_app({url: json.dumps(OFP).encode()}, [], "pilot")) as c:
        tight = (await c.get("/api/flightplan/simbrief", params={"radius_km": 0})).json()
        wide = (await c.get("/api/flightplan/simbrief", params={"radius_km": 60})).json()
        refused = await c.get("/api/flightplan/simbrief", params={"radius_km": 400})
    assert tight["squares"]["ends"] == ["+46+006", "+39+002"]  # the airports' own squares
    assert len(wide["squares"]["ends"]) > len(tight["squares"]["ends"])
    assert refused.status_code == 422


@pytest.mark.anyio
async def test_a_kept_route_is_computed_again_as_it_was_read() -> None:
    """The page keeps the route, never its squares, and sends it back at the next visit: the
    engine answers what it answered from SimBrief, and asks nothing of simbrief.com."""
    asked: list[str] = []
    url = f"{SIMBRIEF_URL}?username=pilot&json=1"
    async with _client(_app({url: json.dumps(OFP).encode()}, asked, "pilot")) as c:
        read = (await c.get("/api/flightplan/simbrief", params={"radius_km": 20})).json()
        kept = {key: read[key] for key in ("from", "to", "points", "radius_km")}
        again = await c.post("/api/flightplan", json=kept)
    assert again.status_code == 200 and asked == [url]
    assert again.json() == read and read["radius_km"] == 20.0


@pytest.mark.anyio
async def test_a_kept_route_takes_no_squares_from_the_page() -> None:
    """A page that kept the squares showed a plan read before the corridor came with the line's
    squares alone (2026-09-25): the squares of a kept route are only ever computed, by the version
    that answers, and a route is enough without a SimBrief name."""
    point = {"ident": "LSGG", "name": "Geneva", "lat": 46.238, "lon": 6.109}
    route = {"from": "LSGG", "to": "LEPA", "points": [point, {**point, "lat": 39.551}]}
    async with _client(_app({}, [], None)) as c:
        fine = await c.post("/api/flightplan", json=route)
        squares = await c.post(
            "/api/flightplan", json={**route, "squares": {"ends": [], "along": []}}
        )
        one = await c.post("/api/flightplan", json={**route, "points": [point]})
        pole = {**point, "lat": 91}
        north = await c.post("/api/flightplan", json={**route, "points": [point, pole]})
        wide = await c.post("/api/flightplan", json={**route, "radius_km": 400})
        many = await c.post("/api/flightplan", json={**route, "points": [point] * 403})
    assert fine.status_code == 200 and fine.json()["radius_km"] == 15.0
    assert [a.status_code for a in (squares, one, north, wide, many)] == [422] * 5
