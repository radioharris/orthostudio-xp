"""``GET`` / ``PUT /api/zones`` and the zones of ``/api/plan`` and ``/api/jobs``
(spec ``docs/specs/map-zones.md`` sections 3 and 5, package A).

The router is tested alone in a small FastAPI application, then in the real ``create_app``,
which includes it (the fixture adds nothing). ``httpx.ASGITransport``: no server, no network; a
temporary ``OSXP_HOME`` and a fake X-Plane (``test_api_fakes``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

import test_api_fakes as fakes
from orthostudio import config
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.api.models import JobRequest, PlanRequest
from orthostudio.api.specs import make_specs
from orthostudio.api.zones_api import zones_router
from orthostudio.errors import OsxpError
from orthostudio.imagery.providers import load_registry
from orthostudio.model import TileRef
from orthostudio.pipeline.build import BuildEnv
from test_api_fakes import FakeBuild, FakeIndex, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane

LSGG = [[6.090, 46.225], [6.130, 46.225], [6.130, 46.250], [6.090, 46.250]]
NO_PHOTO = {"look": None, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0}
ZONE = {"id": "lsgg-18", "name": "LSGG", "zl": 18, "provider": None, "photo": NO_PHOTO,
        "polygon": LSGG}  # fmt: skip
CAPE_TOWN = {"id": "cpt", "name": "Cape Town", "zl": 17, "provider": None, "photo": NO_PHOTO,
             "polygon": [[18.4, -33.9], [18.6, -33.9], [18.6, -33.7], [18.4, -33.7]]}  # fmt: skip


def _document(*zones: dict[str, Any]) -> dict[str, Any]:
    return {"format": "osxp-zones-1", "zones": list(zones)}


def _revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _answer(path: Path, *zones: dict[str, Any]) -> dict[str, Any]:
    """What ``GET`` answers, and ``PUT`` once saved, for a file holding exactly ``zones``."""
    return {**_document(*zones), "revision": _revision(path), "tiles": {}, "problems": []}


# -- the router alone --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_put_then_get_round_trips_in_the_home(home: Path) -> None:
    app = FastAPI()
    app.include_router(zones_router())
    path = home / "zones.json"
    async with client_for(app) as c:
        r = await c.get("/api/zones")
        assert r.status_code == 200 and r.json() == _answer(path)
        assert r.json()["revision"] == "" and r.headers["etag"] == '""'
        assert not path.exists()  # reading never creates the file
        sent = {**ZONE, "polygon": [[6.0900000000003, 46.225], *LSGG[1:], [6.09, 46.225]]}
        r = await c.put("/api/zones", json=_document(sent))
        assert r.status_code == 200, r.text
        assert r.json() == _answer(path, ZONE)  # normalised: closing vertex dropped, 9 decimals
        assert r.headers["etag"] == f'"{_revision(path)}"' and _revision(path)
        assert json.loads(path.read_text("utf-8")) == _document(ZONE)
        r = await c.get("/api/zones")
        assert r.json() == _answer(path, ZONE) and r.headers["etag"] == f'"{_revision(path)}"'
        second = {"id": "geneve", "name": "Genève", "zl": 17, "provider": "BI",
                  "polygon": [[6.0, 46.1], [6.2, 46.1], [6.2, 46.3], [6.0, 46.3]]}  # fmt: skip
        r = await c.put("/api/zones", json=_document(second, ZONE))
        assert r.status_code == 200, r.text
        assert [z["id"] for z in r.json()["zones"]] == ["geneve", "lsgg-18"]
        assert json.loads((home / "zones.json.bak").read_text("utf-8")) == _document(ZONE)
        r = await c.get("/api/zones")
        assert r.json()["zones"][0]["name"] == "Genève"  # order (priority) and UTF-8 kept


@pytest.mark.anyio
async def test_an_invalid_polygon_refuses_the_whole_document(home: Path) -> None:
    app = FastAPI()
    app.include_router(zones_router())
    async with client_for(app) as c:
        assert (await c.put("/api/zones", json=_document(ZONE))).status_code == 200
        before = (home / "zones.json").read_bytes()
        bow_tie = {"id": "bow-tie", "zl": 17,
                   "polygon": [[6.1, 46.1], [6.2, 46.2], [6.2, 46.1], [6.1, 46.2]]}  # fmt: skip
        r = await c.put("/api/zones", json=_document(ZONE, bow_tie))
        assert r.status_code == 422, r.text
        err = r.json()["error"]
        assert err["code"] == "ZONE_INVALID" and err["context"]["zone"] == "bow-tie"
        assert "Self-intersection" in err["context"]["reason"] and err["remedy"]
        assert err["severity"] == "blocking" and err["action"] == "none"
        assert (home / "zones.json").read_bytes() == before  # nothing written
        r = await c.put(
            "/api/zones", json=_document(*[{**ZONE, "id": f"z{i}"} for i in range(501)])
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "ZONE_TOO_MANY"
        r = await c.put("/api/zones", json=[ZONE])
        assert r.status_code == 422 and r.json()["error"]["code"] == "ZONE_INVALID"
        r = await c.put("/api/zones", json=_document({**ZONE, "zl": 20, "provider": "BI"}))
        assert r.status_code == 422 and "maximum of provider BI" in r.json()["error"]["message"]
        assert (await c.get("/api/zones")).json() == _answer(home / "zones.json", ZONE)


@pytest.mark.anyio
async def test_a_broken_saved_file_is_reported_not_refused_nor_emptied(tmp_path: Path) -> None:
    path = tmp_path / "custom" / "zones.json"
    registry = {"BI": load_registry()["BI"]}
    app = FastAPI()
    app.include_router(zones_router(lambda: path, registry=lambda: registry))
    async with client_for(app) as c:
        arc = {**ZONE, "id": "arc", "provider": "Arc"}
        r = await c.put("/api/zones", json=_document(arc))
        assert r.status_code == 422 and "provider Arc" in r.json()["error"]["context"]["reason"]
        assert (await c.put("/api/zones", json=_document(ZONE))).status_code == 200
        # a zone the registry of this server does not know, written by hand: listed as stored
        path.write_text(json.dumps(_document(ZONE, arc)), encoding="utf-8")
        r = await c.get("/api/zones")
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["zones"] == [ZONE, arc] and doc["revision"] == _revision(path)
        (problem,) = doc["problems"]
        assert problem["zone"] == "arc" and problem["index"] == 1
        assert problem["reason"] == "provider Arc is not in the registry"
        # a file that is not JSON: no zone, one problem naming the file, the file untouched
        path.write_text('{"format": "osxp-zones-1", "zones": [', encoding="utf-8")
        before = path.read_bytes()
        r = await c.get("/api/zones")
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["zones"] == [] and doc["revision"] == _revision(path)
        assert r.headers["etag"] == f'"{doc["revision"]}"'
        (problem,) = doc["problems"]
        assert problem["zone"] is None and problem["index"] is None
        assert problem["code"] == "ZONE_INVALID" and str(path) in problem["reason"]
        assert "not JSON" in problem["message"]
        assert path.read_bytes() == before
        path.unlink()
        path.mkdir()  # a zones file that cannot be read at all is still an error
        r = await c.get("/api/zones")
        assert r.status_code == 422 and r.json()["error"]["context"]["path"] == str(path)


@pytest.mark.anyio
async def test_a_save_based_on_another_revision_is_refused(home: Path) -> None:
    """Review finding: two windows overwrote each other's zones (``PUT`` carried no revision)."""
    app = FastAPI()
    app.include_router(zones_router())
    path = home / "zones.json"
    async with client_for(app) as c:
        loaded = (await c.get("/api/zones")).json()["revision"]  # both windows load: no file
        r = await c.put("/api/zones", json=_document(ZONE), headers={"If-Match": f'"{loaded}"'})
        assert r.status_code == 200, r.text  # window A saves first
        saved_by_a = r.json()["revision"]
        assert saved_by_a == _revision(path) and r.headers["etag"] == f'"{saved_by_a}"'
        before = path.read_bytes()
        r = await c.put("/api/zones", json=_document(), headers={"If-Match": f'"{loaded}"'})
        assert r.status_code == 409, r.text  # window B still holds what it loaded
        err = r.json()["error"]
        assert err["code"] == "ZONE_CONFLICT" and err["context"]["path"] == str(path)
        assert err["severity"] == "blocking" and "Reload the zones" in err["remedy"]
        assert path.read_bytes() == before and not (home / "zones.json.bak").exists()
        # B reloads and saves on the current revision (quotes are optional)
        r = await c.put("/api/zones", json=_document(), headers={"If-Match": saved_by_a})
        assert r.status_code == 200, r.text
        assert r.json() == _answer(path) and r.json()["revision"] not in ("", saved_by_a)
        # without If-Match nothing is compared (the command line, a script)
        r = await c.put("/api/zones", json=_document(ZONE))
        assert r.status_code == 200 and r.json()["revision"] == saved_by_a  # same bytes again


# -- the real application: plan and jobs --------------------------------------------------------


@pytest.fixture
def app(home: Path, xplane: Path) -> Iterator[FastAPI]:
    for tile in (TileRef(46, 6), TileRef(-34, 18)):
        dsf = xplane / "Global Scenery" / "X-Plane 12 Global Scenery" / tile.dsf_relpath
        dsf.parent.mkdir(parents=True, exist_ok=True)
        dsf.write_bytes(b"XPLNEDSF")
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=lambda specs: None)
    application = create_app(
        env_factory=BuildEnv.create,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=home / "config.toml",
    )  # the zones routes are create_app's own: nothing is added here
    yield application
    mgr.close()


@pytest.mark.anyio
async def test_create_app_serves_the_zones_itself(app: FastAPI, home: Path) -> None:
    """Review finding: this fixture used to include ``zones_router()`` a second time, so the
    tests of the application passed even if ``create_app`` did not include it."""
    async with client_for(app) as c:
        r = await c.put("/api/zones", json=_document(ZONE))
        assert r.status_code == 200, r.text
        assert (await c.get("/api/zones")).json() == _answer(home / "zones.json", ZONE)


async def _job_specs(c: Any, app: FastAPI, body: dict[str, Any]) -> dict[str, Any]:
    r = await c.post("/api/jobs", json=body)
    assert r.status_code == 201, r.text
    job = app.state.orthostudio["jobs"].get(r.json()["job_id"])
    assert job.wait(10)
    return {s.tile.name: s for s in job.specs}


@pytest.mark.anyio
async def test_a_job_with_the_lsgg_zone_has_a_zone_list_on_its_tile_only(app: FastAPI) -> None:
    body = {"tiles": ["+46+006", "+46+007"], "provider": "BI", "zoom_level": 16}
    async with client_for(app) as c:
        plain = await _job_specs(c, app, body)
        assert all("zone_list" not in s.config for s in plain.values())
        zoned = await _job_specs(c, app, {**body, "zones": [ZONE]})
        (entry,) = zoned["+46+006"].config["zone_list"]
        assert entry == [[46.225, 6.09, 46.225, 6.13, 46.25, 6.13, 46.25, 6.09, 46.225, 6.09],
                         18, "BI"]  # fmt: skip
        # the tile no zone touches is built exactly as without zones: same spec, same keys
        assert zoned["+46+007"] == plain["+46+007"]
        assert zoned["+46+006"].config == {**plain["+46+006"].config, "zone_list": [entry]}
        # zones: null reads the saved document; zones: [] ignores it
        assert (await c.put("/api/zones", json=_document(ZONE))).status_code == 200
        saved = await _job_specs(c, app, body)
        assert saved["+46+006"].config == zoned["+46+006"].config
        none = await _job_specs(c, app, {**body, "zones": []})
        assert none == plain


@pytest.mark.anyio
async def test_the_plan_with_the_lsgg_zone_counts_more_textures(app: FastAPI) -> None:
    body = {"tiles": ["+46+006"], "provider": "BI", "zoom_level": 16, "zones": []}
    async with client_for(app) as c:
        r = await c.post("/api/plan", json=body)
        assert r.status_code == 200, r.text
        plain = r.json()
        r = await c.post("/api/plan", json={**body, "zones": [ZONE]})
        assert r.status_code == 200, r.text
        zoned = r.json()
        extra = zoned["tiles"][0]["textures"]["zones"]
        assert extra > 0 and plain["tiles"][0]["textures"]["zones"] == 0
        assert zoned["compute"]["textures"] == plain["compute"]["textures"] + extra
        assert zoned["network"]["requests"] == plain["network"]["requests"] + 256 * extra
        assert zoned["specs"] == plain["specs"]
        # a zone above what the tile's provider serves is refused when the build is planned
        r = await c.post("/api/plan", json={**body, "zones": [{**ZONE, "zl": 20}]})
        assert r.json()["error"]["code"] == "ZONE_INVALID"
        assert r.json()["error"]["context"]["zone"] == "lsgg-18"
        r = await c.post("/api/plan", json={**body, "zones": [{**ZONE, "polygon": LSGG[:2]}]})
        assert r.json()["error"]["code"] == "ZONE_INVALID"


@pytest.mark.anyio
async def test_a_saved_zone_with_an_unknown_provider_refuses_only_its_tiles(
    app: FastAPI, home: Path, xplane: Path
) -> None:
    """Review finding: one invalid zone in zones.json refused the page (422) and every build."""
    go2 = {**ZONE, "id": "go2", "provider": "GO2"}
    path = home / "zones.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_document(CAPE_TOWN, go2)), encoding="utf-8")
    before = path.read_bytes()
    body = {"provider": "BI", "zoom_level": 16}
    async with client_for(app) as c:
        r = await c.get("/api/zones")
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["zones"] == [CAPE_TOWN, go2] and doc["revision"] == _revision(path)
        (problem,) = doc["problems"]
        assert problem == {
            "zone": "go2",
            "index": 1,
            "code": "ZONE_INVALID",
            "reason": "provider GO2 is not in the registry",
            "message": f"Zone go2 of {path} is invalid: provider GO2 is not in the registry.",
        }
        # far from the bad zone: planned and built with the saved zones that are valid
        r = await c.post("/api/plan", json={**body, "tiles": ["-34+018"]})
        assert r.status_code == 200, r.text
        assert r.json()["tiles"][0]["textures"]["zones"] > 0  # Cape Town's zone is used
        specs = await _job_specs(c, app, {**body, "tiles": ["-34+018"]})
        assert specs["-34+018"].config["zone_list"][0][1:] == [17, "BI"]
        # the tile the bad zone touches: refused, naming it
        for route in ("/api/plan", "/api/jobs"):
            r = await c.post(route, json={**body, "tiles": ["-34+018", "+46+006"]})
            assert r.status_code == 422, r.text
            err = r.json()["error"]
            assert err["code"] == "ZONE_INVALID" and err["context"]["zone"] == "go2"
            assert err["context"]["index"] == 1 and err["context"]["path"] == str(path)
        # the page sends the zones it shows: the saved file is not read
        r = await c.post("/api/plan", json={**body, "tiles": ["+46+006"], "zones": [ZONE]})
        assert r.status_code == 200, r.text
    assert path.read_bytes() == before


@pytest.mark.anyio
async def test_a_saved_file_that_is_not_json_is_one_problem_and_refuses_builds_without_zones(
    app: FastAPI, home: Path, xplane: Path
) -> None:
    path = home / "zones.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"format": "osxp-zones-1", "zones": [{"id": "lsgg-18", ')  # cut short
    before = path.read_bytes()
    body = {"tiles": ["-34+018"], "provider": "BI", "zoom_level": 16}
    async with client_for(app) as c:
        r = await c.get("/api/zones")
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["zones"] == [] and doc["revision"] == hashlib.sha256(before).hexdigest()
        (problem,) = doc["problems"]
        assert problem["zone"] is None and problem["index"] is None
        assert problem["code"] == "ZONE_INVALID" and str(path) in problem["reason"]
        r = await c.post("/api/plan", json=body)
        assert r.status_code == 422, r.text
        err = r.json()["error"]
        assert err["code"] == "ZONE_INVALID" and err["context"]["path"] == str(path)
        r = await c.post("/api/plan", json={**body, "zones": None})
        assert r.status_code == 422 and r.json()["error"]["code"] == "ZONE_INVALID"
        r = await c.post("/api/plan", json={**body, "zones": []})
        assert r.status_code == 200, r.text  # the request's own zones: the file is not read
    assert path.read_bytes() == before


def test_a_square_may_carry_its_own_detail_level(home: Path, xplane: Path) -> None:
    """A flight plan gives its departure and arrival one level and the squares along the route
    another (a user, 2026-09-22): ``tiles_zl`` names the squares that differ, the others take
    ``zoom_level``. A level above what the source gives is refused like ``zoom_level``."""
    settings = config.Settings()
    req = PlanRequest.model_validate(
        {
            "tiles": ["+46+006", "+45+006", "+44+006"],
            "zoom_level": 14,
            "tiles_zl": {"+46+006": 17},
            "overlay": False,
            "xp12_rasters": False,
        }
    )
    specs = make_specs(req, settings=settings)
    assert [(s.tile.name, s.zl) for s in specs] == [
        ("+46+006", 17),
        ("+45+006", 14),
        ("+44+006", 14),
    ]
    too_sharp = PlanRequest.model_validate(
        {**req.model_dump(), "provider": "EOX", "tiles_zl": {"+46+006": 17}}  # EOX stops at ZL14
    )
    with pytest.raises(OsxpError) as exc:
        make_specs(too_sharp, settings=settings)
    assert exc.value.code == "CFG_VALUE_INVALID"
    assert exc.value.context["name"] == "tiles_zl[+46+006]"
    with pytest.raises(ValidationError):  # a level is a whole number from 10 to 19
        PlanRequest.model_validate({"tiles": ["+46+006"], "tiles_zl": {"+46+006": 42}})


def test_make_specs_with_a_zones_file_and_an_override_conflict(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    settings = config.Settings()
    path = tmp_path / "elsewhere.json"
    path.write_text(json.dumps(_document(ZONE)), encoding="utf-8")
    req = PlanRequest.model_validate(
        {"tiles": ["+46+006"], "zoom_level": 16, "overlay": False, "xp12_rasters": False}
    )
    (spec,) = make_specs(req, settings=settings, zones_path=path)
    assert spec.config["zone_list"][0][1:] == [18, "BI"]
    # overlays "none" (simHeaven X-World): no overlay even when the request asks for one
    asks = PlanRequest.model_validate({**req.model_dump(), "overlay": True})
    assert make_specs(asks, settings=settings, zones_path=path)[0].overlay is True
    none = config.Settings.model_validate({"essential": {"overlays": "none"}})
    assert make_specs(asks, settings=none, zones_path=path)[0].overlay is False
    (spec,) = make_specs(req, settings=settings, zones_path=tmp_path / "absent.json")
    assert "zone_list" not in spec.config
    empty_override = JobRequest.model_validate(
        {**req.model_dump(), "zones": [ZONE], "overrides": {"zone_list": "[]"}}
    )
    assert make_specs(empty_override, settings=settings)[0].config["zone_list"][0][1] == 18
    clash = JobRequest.model_validate(
        {**req.model_dump(), "zones": [ZONE],
         "overrides": {"zone_list": "[[[46.1, 6.1, 46.1, 6.2, 46.2, 6.2, 46.1, 6.1], 15, 'BI']]"}}
    )  # fmt: skip
    with pytest.raises(OsxpError) as info:
        make_specs(clash, settings=settings)
    assert info.value.code == "CFG_VALUE_INVALID" and "zone_list" in info.value.message
    with pytest.raises(OsxpError) as info:
        PlanRequest.model_validate({**req.model_dump(), "zones": [ZONE, ZONE]})
    assert info.value.code == "ZONE_INVALID" and "used by another zone" in info.value.message
    # Luxembourg's source does not cover Geneva: refused before any zone is looked at
    elsewhere = {**req.model_dump(), "provider": "Lux", "zones": [{**ZONE, "zl": 20}]}
    with pytest.raises(OsxpError) as info:
        make_specs(PlanRequest.model_validate(elsewhere), settings=settings)
    assert info.value.code == "CFG_PROVIDER_OUT_OF_COVERAGE"
    assert info.value.context == {"provider": "Lux", "extent": "Luxembourg", "tiles": "+46+006"}
    # a ZL20 zone needs a provider serving ZL20 and a mesh at ZL20 (mesh_zl caps the textures)
    square = [[6.1, 49.6], [6.2, 49.6], [6.2, 49.7], [6.1, 49.7]]
    lux_zone = {**ZONE, "id": "elvl-20", "name": "ELLX", "zl": 20, "polygon": square}
    lux = {**req.model_dump(), "tiles": ["+49+006"], "provider": "Lux", "zones": [lux_zone]}
    with pytest.raises(OsxpError) as info:
        make_specs(PlanRequest.model_validate(lux), settings=settings)
    assert info.value.code == "ZONE_INVALID" and "mesh_zl 19" in info.value.message
    finer = PlanRequest.model_validate({**lux, "overrides": {"mesh_zl": "20"}})
    (spec,) = make_specs(finer, settings=settings)
    assert spec.config["mesh_zl"] == 20 and spec.config["zone_list"][0][1:] == [20, "Lux"]


def test_a_request_carries_the_squares_colours_with_its_zones(home: Path, xplane: Path) -> None:
    """A user built a black and white square and X-Plane showed it as usual (2026-09-18).

    The page sends the zones of a build with it, and ``request_tiles`` took that as "this request
    carries everything", so the squares were built without their colours. It now reads
    ``tiles_settings`` when it is there and the saved document otherwise.
    """
    from orthostudio.zones import ZonesDocument, save_zones

    body = {
        "tiles": ["+46+006"],
        "zoom_level": 16,
        "overlay": False,
        "xp12_rasters": False,
        "xplane_dir": str(xplane),
    }
    black = {"photo": {"look": "custom", "brightness": 0.5, "contrast": -0.5, "saturation": -1.0}}
    with_own = PlanRequest.model_validate(
        {**body, "zones": [], "tiles_settings": {"+46+006": black}}
    )
    (spec,) = make_specs(with_own, settings=config.Settings(), config_module=config)
    assert spec.config["photo_brightness"] == 0.5
    assert spec.config["photo_contrast"] == -0.5
    assert spec.config["photo_saturation"] == -1.0
    # without it, the saved document answers, zones given or not
    path = home / "zones.json"
    save_zones(path, ZonesDocument.model_validate({"tiles": {"+46+006": black}}))
    (saved,) = make_specs(
        PlanRequest.model_validate({**body, "zones": []}),
        settings=config.Settings(),
        zones_path=path,
        config_module=config,
    )
    assert saved.config["photo_saturation"] == -1.0
    # and a square that names none keeps the settings' answer
    (plain,) = make_specs(
        PlanRequest.model_validate({**body, "tiles": ["+46+007"], "zones": []}),
        settings=config.Settings(),
        zones_path=path,
        config_module=config,
    )
    assert plain.config["photo_saturation"] == 0.0
