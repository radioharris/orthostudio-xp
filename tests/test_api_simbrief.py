"""The Plan's flight plan, read from SimBrief (``docs/specs/api.md``).

The user of the page asked for a button that draws his last plan and chooses the squares it flies
over (2026-09-19). No test here touches the network: the upstream is injected. What a real SimBrief
answers was checked once by hand: HTTP 400 and ``{"fetch": {"status": "Error: Unknown UserID"}}``
for a name it does not know.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orthostudio.api.simbrief import SIMBRIEF_URL, route_of, simbrief_router

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


def _app(answers: dict[str, bytes], asked: list[str], user: str | None) -> FastAPI:
    def fetch(url: str) -> tuple[bytes | None, str]:
        asked.append(url)
        body = answers.get(url)
        return (body, "") if body is not None else (None, "unknown user")

    app = FastAPI()
    app.include_router(simbrief_router(lambda: user, fetch=fetch))
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
        answer = await c.get("/api/simbrief")
    assert answer.status_code == 200
    assert asked == [url]  # the name, nothing else of the user, and only when asked
    assert answer.json()["to"] == "LEPA" and len(answer.json()["points"]) == 4


@pytest.mark.anyio
async def test_no_name_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty in Settings, and nothing is ever asked of simbrief.com."""
    asked: list[str] = []
    async with _client(_app({}, asked, None)) as c:
        answer = await c.get("/api/simbrief")
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "CFG_SIMBRIEF_USER_MISSING"
    assert not asked


@pytest.mark.anyio
async def test_a_name_they_do_not_know_says_so() -> None:
    asked: list[str] = []
    async with _client(_app({}, asked, "nobody")) as c:
        answer = await c.get("/api/simbrief")
    assert answer.status_code == 404
    error = answer.json()["error"]
    assert error["code"] == "CFG_SIMBRIEF_USER_UNKNOWN" and "Settings" in error["remedy"]


@pytest.mark.anyio
async def test_a_plan_that_says_nothing_and_an_answer_that_is_not_a_plan() -> None:
    """Both leave the Plan as it was, with a sentence that says what to do."""
    url = f"{SIMBRIEF_URL}?username=pilot&json=1"
    async with _client(_app({url: b"not json at all"}, [], "pilot")) as c:
        answer = await c.get("/api/simbrief")
    assert answer.status_code == 502 and answer.json()["error"]["code"] == "NET_CONNECTION_FAILED"
    empty = json.dumps({"origin": {}, "destination": {}}).encode()
    async with _client(_app({url: empty}, [], "pilot")) as c:
        answer = await c.get("/api/simbrief")
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "CFG_SIMBRIEF_PLAN_EMPTY"
