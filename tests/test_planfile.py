# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A flight plan file, Little Navmap's or X-Plane's, as the Plan's line (idea 11, 2026-10-11).

A user saves his plans in Little Navmap (``.lnmpln``) and can export them for X-Plane (``.fms``):
the Plan's button « Open a flight plan… » takes either, the engine opens the system's own dialog
(injected here, so none opens) and the route goes the way of a SimBrief plan.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orthostudio.api.simbrief import simbrief_router
from orthostudio.errors import OsxpError
from orthostudio.planfile import PLAN_SUFFIXES, read_plan_file, route_of_text

anyio_backend = "asyncio"

LNMPLN = """<?xml version="1.0" encoding="UTF-8"?>
<LittleNavmap xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Flightplan>
    <Header><FlightplanType>IFR</FlightplanType><CruisingAlt>24000</CruisingAlt></Header>
    <Departure>
      <Name>Nice Cote d'Azur</Name><Ident>LFMN</Ident>
      <Pos Lon="7.215872" Lat="43.658411" Alt="12.00"/>
    </Departure>
    <Waypoints>
      <Waypoint>
        <Name>Nice Cote d'Azur</Name><Ident>LFMN</Ident><Type>AIRPORT</Type>
        <Pos Lon="7.215872" Lat="43.658411" Alt="12.00"/>
      </Waypoint>
      <Waypoint>
        <Ident>MINDI</Ident><Region>LF</Region><Type>WAYPOINT</Type>
        <Pos Lon="6.811667" Lat="44.556667" Alt="24000.00"/>
      </Waypoint>
      <Waypoint>
        <Name>Geneve</Name><Ident>LSGG</Ident><Type>AIRPORT</Type>
        <Pos Lon="6.109444" Lat="46.238056" Alt="1411.00"/>
      </Waypoint>
    </Waypoints>
  </Flightplan>
</LittleNavmap>
"""

ONE_POINT = """<LittleNavmap><Flightplan><Waypoints>
  <Waypoint><Ident>LFMN</Ident><Pos Lon="7.215872" Lat="43.658411"/></Waypoint>
  <Waypoint><Ident>NOPOS</Ident></Waypoint>
</Waypoints></Flightplan></LittleNavmap>
"""

FMS_1100 = """I
1100 Version
CYCLE 2510
ADEP LFMN
ADES LSGG
NUMENR 3
1 LFMN ADEP 12.000000 43.658411 7.215872
11 MINDI DRCT 24000.000000 44.556667 6.811667
1 LSGG ADES 1411.000000 46.238056 6.109444
"""

FMS_3 = """I
3 version
1
3
1 LFMN 12.000000 43.658411 7.215872
11 MINDI 24000.000000 44.556667 6.811667
1 LSGG 1411.000000 46.238056 6.109444
"""


@pytest.mark.parametrize("text", [LNMPLN, FMS_1100, FMS_3], ids=["lnmpln", "fms1100", "fms3"])
def test_both_formats_give_the_same_line(text: str) -> None:
    route = route_of_text(text)
    assert route is not None
    assert (route["from"], route["to"]) == ("LFMN", "LSGG")
    assert [p["ident"] for p in route["points"]] == ["LFMN", "MINDI", "LSGG"]
    assert route["points"][1]["lat"] == pytest.approx(44.556667)
    assert route["points"][1]["lon"] == pytest.approx(6.811667)


def test_a_file_in_neither_format_or_without_two_points_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "notes.fms"
    other.write_text("hello\nworld\n")
    with pytest.raises(OsxpError) as info:
        read_plan_file(other)
    assert info.value.code == "CFG_PLAN_FILE_INVALID"
    assert info.value.context["file"] == "notes.fms"
    one = _write(tmp_path, "one.lnmpln", ONE_POINT)
    with pytest.raises(OsxpError) as info:
        read_plan_file(one)
    assert info.value.code == "CFG_PLAN_FILE_INVALID"
    assert read_plan_file(_write(tmp_path, "ok.lnmpln", LNMPLN))["to"] == "LSGG"


def _write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def _app(dialog: object) -> FastAPI:
    app = FastAPI()
    app.include_router(simbrief_router(lambda: None, file_dialog=dialog))  # type: ignore[arg-type]
    return app


@pytest.mark.anyio
async def test_the_button_reads_the_file_the_dialog_gives(tmp_path: Path) -> None:
    """The engine opens the dialog where the page last was, offers the two kinds of file, and
    answers the route, its squares and the folder; Cancel answers ``chosen: false``."""
    plan = _write(tmp_path, "LFMN-LSGG.fms", FMS_1100)
    asked: list[tuple[str, Path | None, tuple[str, ...]]] = []

    def dialog(prompt: str, start: Path | None, suffixes: tuple[str, ...]) -> Path | None:
        asked.append((prompt, start, suffixes))
        return plan

    transport = ASGITransport(app=_app(dialog))
    async with AsyncClient(transport=transport, base_url="http://osxp") as c:
        got = await c.post(
            "/api/flightplan/file",
            json={"radius_km": 20, "prompt": "Open", "start": str(tmp_path)},
        )
    assert got.status_code == 200
    body = got.json()
    assert body["chosen"] is True and body["file"] == "LFMN-LSGG.fms"
    assert body["folder"] == str(tmp_path) and body["radius_km"] == 20.0
    assert (body["from"], body["to"]) == ("LFMN", "LSGG")
    assert "+43+007" in body["squares"]["ends"] and "+46+006" in body["squares"]["ends"]
    assert asked == [("Open", tmp_path, PLAN_SUFFIXES)]

    async with AsyncClient(
        transport=ASGITransport(app=_app(lambda *_: None)), base_url="http://osxp"
    ) as c:
        cancelled = await c.post("/api/flightplan/file", json={})
    assert cancelled.json() == {"chosen": False}


@pytest.mark.anyio
async def test_a_file_that_is_not_a_plan_says_so(tmp_path: Path) -> None:
    bad = _write(tmp_path, "bad.lnmpln", "<NotAPlan/>")
    transport = ASGITransport(app=_app(lambda *_: bad))
    async with AsyncClient(transport=transport, base_url="http://osxp") as c:
        got = await c.post("/api/flightplan/file", json={})
    assert got.status_code == 422
    assert got.json()["error"]["code"] == "CFG_PLAN_FILE_INVALID"


def test_the_dialog_opens_where_little_navmap_last_saved(tmp_path: Path) -> None:
    """A user's plans were in iCloud Drive, where Little Navmap's own dialog opens, not in its
    plan folder (2026-10-11): its settings name that folder, and the dialog goes there first."""
    from orthostudio.api.simbrief import little_navmap_folder, plan_folders

    ini = _write(
        tmp_path,
        "little_navmap.ini",
        "[Route]\nLnmPlnFileDialogDir=/Users/pilot/Library/Mobile Documents/FMS plans\n",
    )
    assert little_navmap_folder(ini) == Path("/Users/pilot/Library/Mobile Documents/FMS plans")
    assert plan_folders(None, ini)[0] == Path("/Users/pilot/Library/Mobile Documents/FMS plans")
    assert little_navmap_folder(tmp_path / "absent.ini") is None
