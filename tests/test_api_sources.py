"""The imagery sources: what each covers, and the sources a user adds (``docs/specs/api.md`` 2,
``docs/specs/imagery-providers.md`` 6). No request leaves the machine: the tile of a source that
is tried comes from a stub."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.errors import OsxpError
from orthostudio.imagery.providers import read_user_sources, user_sources_path
from orthostudio.net.fetch import FetchRequest, FetchResult
from orthostudio.zones import Zone
from test_api_fakes import FakeBuild, FakeIndex, client_for, make_spec

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 2000
ADDRESS = "https://tiles.example.com/vt?lyrs=s&x={x}&y={y}&z={zoom}"
PROVIDER_KEYS = {"code", "name", "max_zl", "attribution", "terms_url", "alive", "extent",
                 "extent_bounds", "same_as", "custom"}  # fmt: skip


def _app(
    home: Path, fetched: list[str] | None = None, body: bytes = JPEG, build: FakeBuild | None = None
) -> tuple[object, JobManager]:
    async def fetch(request: FetchRequest) -> FetchResult:
        if fetched is not None:
            fetched.append(request.url)
        return FetchResult(request.key, 200, body, {}, 0.01, 1, False, None)

    mgr = JobManager(jobs_dir=home / "jobs", build=build or FakeBuild(), env_factory=None)
    app = create_app(env_factory=None, jobs=mgr, airports=FakeIndex(),
                     settings_path=home / "config.toml", source_fetch=fetch)  # fmt: skip
    return app, mgr


@pytest.mark.anyio
async def test_the_sources_say_what_they_cover_bing_and_esri_first(home: Path) -> None:
    """A user found the sources of several countries mixed in one list: each says the country it
    covers and its rectangle, the registry's order puts Bing Maps then Esri first, and NL, the
    same imagery as PDOK, says so."""
    app, mgr = _app(home)
    try:
        async with client_for(app) as c:
            rows = (await c.get("/api/providers")).json()
    finally:
        mgr.close()
    assert [r["code"] for r in rows][:3] == ["BI", "Arc", "Arc@"]
    assert all(set(r) == PROVIDER_KEYS for r in rows)
    by = {r["code"]: r for r in rows}
    assert by["BI"]["name"] == "Bing Maps" and by["BI"]["extent"] is None
    assert by["BI"]["extent_bounds"] is None and by["BI"]["attribution"]
    assert by["PDOK20"]["extent"] == "Netherlands"
    assert by["PDOK20"]["extent_bounds"] == [3.06, 50.72, 7.26, 53.76]
    assert by["NL"]["same_as"] == "PDOK" and by["PDOK"]["same_as"] is None
    assert by["JP"]["extent"] == "Japan" and by["USGS"]["extent"] == "United States"
    assert not any(r["custom"] for r in rows)


@pytest.mark.anyio
async def test_a_source_of_one_country_is_refused_for_a_tile_elsewhere(
    home: Path, xplane: Path
) -> None:
    """PDOK chosen for a tile in Egypt used to download nothing valid: the estimate and the build
    refuse it (422 ``CFG_PROVIDER_OUT_OF_COVERAGE``, naming the tiles), and so does a zone."""
    app, mgr = _app(home)
    body = {"tiles": ["+27+034", "+52+004"], "provider": "PDOK20", "zoom_level": 16,
            "xplane_dir": str(xplane), "overlay": False, "xp12_rasters": False}  # fmt: skip
    try:
        async with client_for(app) as c:
            for route, extra in (("/api/plan", {}), ("/api/jobs", {"install": False})):
                r = await c.post(route, json={**body, **extra})
                assert r.status_code == 422, (route, r.text)
                err = r.json()["error"]
                assert err["code"] == "CFG_PROVIDER_OUT_OF_COVERAGE", route
                assert err["context"] == {
                    "provider": "PDOK20", "extent": "Netherlands", "tiles": "+27+034"
                }  # fmt: skip
                assert "Bing Maps" in err["remedy"]
            r = await c.post("/api/plan", json={**body, "tiles": ["+52+004"]})
            assert r.status_code == 200, r.text
    finally:
        mgr.close()
    square = [[34.1, 27.9], [34.2, 27.9], [34.2, 28.0], [34.1, 28.0]]
    with pytest.raises(OsxpError) as info:
        Zone.model_validate({"id": "hesh", "name": "HESH", "zl": 18, "provider": "PDOK20",
                             "polygon": square})  # fmt: skip
    assert info.value.code == "ZONE_INVALID" and "covers Netherlands only" in info.value.message
    assert Zone.model_validate({"id": "hesh", "zl": 18, "provider": "BI", "polygon": square})


@pytest.mark.anyio
async def test_a_user_tries_adds_and_removes_a_source_of_their_own(
    home: Path, xplane: Path
) -> None:
    """A user asked for sources OrthoStudio XP does not ship: they add their own, at their own
    risk. An address is tried on one tile first, refused when it cannot give a tile's place, saved
    in ``$OSXP_HOME/sources.toml`` under a code of its own, usable at once, and removed unless a
    build or a zone uses it; step 1's saved source goes back to Bing Maps when it was this one."""
    fetched: list[str] = []
    app, mgr = _app(home, fetched, build=FakeBuild(delay_s=0.2))
    try:
        async with client_for(app) as c:
            r = await c.post("/api/sources/test", json={"url_template": ADDRESS, "lat": 46.2,
                                                        "lon": 6.1})  # fmt: skip
            assert r.status_code == 200, r.text
            got = r.json()
            assert got["ok"] is True and got["image"] == "jpeg" and got["status"] == 200
            assert fetched == [got["url"]] and "&z=15" in got["url"] and "{" not in got["url"]

            for bad in (
                "ftp://tiles.example.com/{x}/{y}/{zoom}",
                "https://tiles.example.com/15/1/2.jpg",
                "https://tiles.example.com/{x}/{y}/{zoom}/{colour}",
            ):
                r = await c.post("/api/sources", json={"name": "Bad", "url_template": bad})
                assert r.status_code == 422, bad
                assert r.json()["error"]["code"] == "CFG_VALUE_INVALID", bad

            mine = {"name": "My satellite", "url_template": ADDRESS, "max_zl": 20}
            r = await c.post("/api/sources", json=mine)
            assert r.status_code == 201, r.text
            added = r.json()
            assert set(added) == PROVIDER_KEYS | {"url_template"}
            assert added["code"] == "Mysatellite" and added["custom"] is True
            assert added["name"] == "My satellite" and added["max_zl"] == 20
            assert added["url_template"] == ADDRESS and added["extent"] is None
            twice = await c.post(
                "/api/sources", json={"name": "My satellite", "url_template": ADDRESS}
            )
            shipped = await c.post("/api/sources", json={"name": "BI", "url_template": ADDRESS})
            assert twice.json()["code"] == "Mysatellite_2" and shipped.json()["code"] == "BI_2"
            codes = [row["code"] for row in (await c.get("/api/providers")).json()]
            assert codes[-3:] == ["Mysatellite", "Mysatellite_2", "BI_2"] and codes[0] == "BI"
            assert user_sources_path() == home / "sources.toml"
            assert list(read_user_sources()[0]) == ["Mysatellite", "Mysatellite_2", "BI_2"]

            plan = {"tiles": ["+46+006"], "provider": "Mysatellite", "zoom_level": 16,
                    "xplane_dir": str(xplane), "overlay": False, "xp12_rasters": False}  # fmt: skip
            r = await c.post("/api/plan", json=plan)
            assert r.status_code == 200, r.text

            assert (await c.delete("/api/sources/BI")).status_code == 404
            polygon = [[6.1, 46.2], [6.2, 46.2], [6.2, 46.3], [6.1, 46.3]]
            zone = {"id": "lsgg", "name": "LSGG", "zl": 18, "provider": "Mysatellite",
                    "polygon": polygon}  # fmt: skip
            (home / "zones.json").write_text(
                json.dumps({"format": "osxp-zones-1", "zones": [zone]})
            )
            r = await c.delete("/api/sources/Mysatellite")
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_SOURCE_IN_USE"
            assert r.json()["error"]["context"] == {"zones": ["LSGG"]}

            job = mgr.start([dataclasses.replace(make_spec("+46+006", home=home), provider="BI_2")])
            r = await c.delete("/api/sources/BI_2")
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
            mgr.cancel(job.id)
            assert job.wait(30)

            settings = (await c.get("/api/settings")).json()
            settings["essential"]["provider"] = "Mysatellite_2"
            assert (await c.put("/api/settings", json=settings)).status_code == 200
            r = await c.delete("/api/sources/Mysatellite_2")
            assert r.status_code == 200, r.text
            assert r.json() == {"removed": "Mysatellite_2", "settings_provider": "BI"}
            assert (await c.get("/api/settings")).json()["essential"]["provider"] == "BI"
            assert list(read_user_sources()[0]) == ["Mysatellite", "BI_2"]

            r = await c.post("/api/sources/test", json={"url_template": ADDRESS})
            assert r.json()["ok"] is True
    finally:
        mgr.close()

    # an answer that is not an image is not a working source
    app, mgr = _app(home, body=b"<html>quota exceeded</html>")
    try:
        async with client_for(app) as c:
            got = (await c.post("/api/sources/test", json={"url_template": ADDRESS})).json()
            assert got["ok"] is False and got["image"] is None and got["status"] == 200
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_the_colour_preview_says_when_a_source_has_no_photo_there(home: Path) -> None:
    """Over open water Bing answers its own "no imagery" tile rather than a 404: 1033 bytes of
    flat grey with a crossed-out picture on it. Drawn as it came, the page showed it twice under
    the words "the ground where the map is looking", and a user read that as a broken preview
    (2026-09-20, at 45.921 -6.372, in the Atlantic). The registry holds the signals of such a
    tile already, and the page has the sentence for it."""
    placeholder = b"\x89PNG\r\n\x1a\n" + b"\0" * (1033 - 8)  # Bing's, by its size in registry.toml
    app, mgr = _app(home, body=placeholder)
    try:
        async with client_for(app) as c:
            empty = await c.get(
                "/api/photo-sample",
                params={"provider": "BI", "lat": 45.921, "lon": -6.372},
            )
    finally:
        mgr.close()
    assert empty.status_code >= 400, "a tile that holds no photo was served as if it did"
    body = empty.json()["error"]
    assert body["code"] == "IMG_TILE_PLACEHOLDER"
    assert "no photo at this place" in body["message"]

    # and what is no image at all answers an error too: NET_UNAVAILABLE stood there, which is
    # not a code this program has, so the route threw a ValueError instead (2026-09-20)
    app, mgr = _app(home, body=b"<html>nope</html>")
    try:
        async with client_for(app) as c:
            nothing = await c.get(
                "/api/photo-sample", params={"provider": "BI", "lat": 46.2, "lon": 6.14}
            )
    finally:
        mgr.close()
    assert nothing.status_code >= 400
    assert nothing.json()["error"]["code"] == "NET_UNEXPECTED_STATUS"

    app, mgr = _app(home, body=JPEG)  # and a real photo is still served as one
    try:
        async with client_for(app) as c:
            ground = await c.get(
                "/api/photo-sample", params={"provider": "BI", "lat": 46.2, "lon": 6.14}
            )
    finally:
        mgr.close()
    assert ground.status_code == 200 and ground.headers["content-type"].startswith("image/")
