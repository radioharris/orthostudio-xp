"""Endpoints of the API (spec ``docs/specs/api.md`` sections 2-4, 6): status, providers,
settings, airports, validation, security guards, plan offline, library on a temporary home
and a copied ``Custom Scenery``. ``httpx.ASGITransport``: no server, no network, no Ortho4XP."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.api.app import API_LEVEL, create_app
from orthostudio.api.jobs import JobManager
from orthostudio.net.fetch import FetchRequest, FetchResult
from orthostudio.pipeline.build import BuildEnv
from test_api_fakes import FakeBuild, FakeIndex, client_for

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane


@pytest.fixture
def app(home: Path):  # type: ignore[no-untyped-def]
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=lambda specs: None)
    application = create_app(
        env_factory=BuildEnv.create,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=home / "config.toml",
    )
    yield application
    mgr.close()


@pytest.mark.anyio
async def test_status_providers_and_language(app, home: Path, xplane: Path) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        r = await c.get("/api/status", headers={"Accept-Language": "fr-FR,fr;q=0.9"})
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["version"] and doc["home"] == str(home) and doc["language"] == "fr"
        xp_status = doc["xplane"]
        assert xp_status["path"] == str(xplane) and xp_status["detected"] is True
        # the other X-Plane 12 of the machine, for the page to name them
        assert xp_status["running"] is False and isinstance(xp_status["others"], list)
        assert doc["store_bytes"] == 0 and doc["chunks_bytes"] == 0 and doc["library_count"] == 0
        names = {c["name"] for c in doc["doctor"]}
        assert {"python", "encoder", "xplane", "disk"} <= names
        assert doc["active_job"] is None
        assert doc["api_level"] == API_LEVEL  # the page asks for a restart below its own level
        from orthostudio.api.serve import package_root

        # which installation serves: another one launched takes its place (serve.take_over)
        assert doc["engine"] == {"root": str(package_root()), "pid": os.getpid()}
        r = await c.get("/api/status")
        assert r.json()["language"] == "en"
        r = await c.get("/api/providers")
        rows = {p["code"]: p for p in r.json()}
        assert rows["BI"]["max_zl"] == 19 and rows["BI"]["alive"] is None
        assert "attribution" in rows["BI"] and "terms_url" in rows["BI"]


@pytest.mark.anyio
async def test_settings_round_trip_and_schema(app, home: Path, xplane: Path) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        r = await c.get("/api/settings")
        s = r.json()
        assert s["essential"]["provider"] == "BI" and s["essential"]["zoom_level"] == 16
        s["essential"]["zoom_level"] = 17
        s["essential"]["xplane_dir"] = str(xplane)
        r = await c.put("/api/settings", json=s)
        assert r.status_code == 200, r.text
        assert r.json()["essential"]["zoom_level"] == 17
        assert (home / "config.toml").is_file()
        r = await c.get("/api/settings")
        assert r.json()["essential"]["zoom_level"] == 17
        r = await c.put("/api/settings", json=s)  # second save keeps a .bak
        assert (home / "config.toml.bak").is_file()
        s["essential"]["zoom_level"] = 99
        r = await c.put("/api/settings", json=s)
        assert r.status_code == 422 and r.json()["error"]["code"].startswith("CFG_")
        s["essential"]["zoom_level"] = 16
        s["essential"]["xplane_dir"] = str(home / "not-xplane")
        r = await c.put("/api/settings", json=s)
        err = r.json()["error"]
        assert r.status_code == 422 and err["code"] == "XP_DIR_NOT_FOUND"
        assert err["action"] == "settings" and err["context"]["why"] == "missing"
        # a folder that exists says what it lacks (a user asked how X-Plane is recognised)
        (home / "not-xplane" / "Resources").mkdir(parents=True)
        err = (await c.put("/api/settings", json=s)).json()["error"]
        assert err["context"] == {"path": str((home / "not-xplane").resolve()), "why": "not_xplane",
                                  "lacks": "Custom Scenery"}  # fmt: skip
        assert err["message"].endswith("it holds no Custom Scenery.")
        # a folder saved before is not checked again: its disk may only be unplugged
        s["essential"]["xplane_dir"] = str(xplane)
        assert (await c.put("/api/settings", json=s)).status_code == 200
        shutil.rmtree(xplane / "Resources")
        s["essential"]["zoom_level"] = 17
        r = await c.put("/api/settings", json=s)
        assert r.status_code == 200 and r.json()["essential"]["zoom_level"] == 17
        r = await c.get("/api/settings/schema")
        schema = r.json()
        assert "properties" in schema and "essential" in str(schema)


@pytest.mark.anyio
async def test_airports_with_a_fake_index(app) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        r = await c.get("/api/airports", params={"q": "lfm"})
        assert [a["icao"] for a in r.json()] == ["LFML", "LFMN"]
        r = await c.get("/api/airports", params={"q": "nice", "limit": 1})
        assert r.json()[0]["name"].startswith("Nice") and r.json()[0]["lat"] == 43.6584
        assert (await c.get("/api/airports", params={"q": ""})).json() == []
        r = await c.get("/api/airports/lfml")
        assert r.status_code == 200 and r.json()["icao"] == "LFML"
        r = await c.get("/api/airports/ZZZZ")
        assert r.status_code == 404 and r.json()["error"]["code"] == "CFG_LATLON_INVALID"


@pytest.mark.anyio
async def test_airports_without_an_index(home: Path) -> None:
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    app = create_app(env_factory=None, jobs=mgr, airports=None, settings_path=home / "c.toml")
    async with client_for(app) as c:
        r = await c.get("/api/airports", params={"q": "LFML"})
        # no X-Plane on this home: the real index cannot be built (422) or is absent (503)
        assert r.status_code in (422, 503)
        assert r.json()["error"]["code"] in ("XP_DIR_NOT_FOUND", "SYS_RESOURCE_MISSING")
    mgr.close()


@pytest.mark.anyio
async def test_validation_errors_carry_codes(app, xplane: Path) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        r = await c.post("/api/plan", json={"tiles": ["+430+005"], "zoom_level": 14})
        assert r.status_code == 422, r.text
        err = r.json()["error"]
        assert err["code"] == "CFG_LATLON_INVALID" and err["remedy"] and err["action"] == "settings"
        r = await c.post(
            "/api/plan", json={"tiles": ["+43+005"], "zoom_level": 14, "provider": "NOPE"}
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_PROVIDER_UNKNOWN"
        r = await c.post("/api/plan", json={"tiles": ["+43+005"], "zoom_level": 14, "bogus": 1})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
        r = await c.post("/api/plan", json={"tiles": ["+43+005"], "zoom_level": 25})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
        r = await c.post("/api/plan", json={"zoom_level": 14})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_LATLON_INVALID"
        r = await c.post(
            "/api/plan",
            json={"tiles": ["+43+005"], "zoom_level": 14, "overrides": {"nope": 1}},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_VALUE_INVALID"
        r = await c.post(
            "/api/plan", json={"tiles": ["+43+005"], "zoom_level": 14, "xplane_dir": "/nowhere"}
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "XP_DIR_NOT_FOUND"
        r = await c.post("/api/jobs", json={"tiles": ["+43+005"], "zoom_level": 14, "install": True,
                                            "xplane_dir": str(xplane)})  # fmt: skip
        assert r.status_code == 201  # install with a valid X-Plane folder is accepted


def _no_xplane_on_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    from orthostudio.api import specs
    from orthostudio.pipeline import build

    monkeypatch.delenv("OSXP_XPLANE_DIR", raising=False)
    monkeypatch.setattr(specs, "detect_xplane", lambda: None)
    monkeypatch.setattr(build, "detect_xplane", lambda: None)


def _bare_xplane(root: Path) -> Path:
    """An X-Plane 12 folder without its Global Scenery."""
    (root / "Resources").mkdir(parents=True)
    (root / "Custom Scenery").mkdir()
    return root


@pytest.mark.anyio
async def test_a_plan_without_x_plane_says_what_is_missing(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """A user on a Windows virtual machine, X-Plane on the Mac around it, read "Global Scenery is
    not installed in <not detected>": no X-Plane folder is XP_DIR_NOT_FOUND in plain words, a
    saved folder gone is named, a folder without its Global Scenery is
    XP_GLOBAL_SCENERY_NOT_FOUND."""
    _no_xplane_on_this_machine(monkeypatch)
    body = {"tiles": ["+43+005"], "zoom_level": 14}
    async with client_for(app) as c:
        for path, extra in (("/api/plan", {}), ("/api/jobs", {"install": False}),
                            ("/api/jobs", {"install": True})):  # fmt: skip
            r = await c.post(path, json={**body, **extra})
            assert r.status_code == 422, r.text
            err = r.json()["error"]
            assert err["code"] == "XP_DIR_NOT_FOUND" and err["action"] == "settings", err
            assert err["message"] == (
                "X-Plane 12 was not found on this computer. OrthoStudio XP takes the relief, "
                "roads, forests and buildings from it, and adds the tiles to it."
            )
            assert err["remedy"] == "Choose the X-Plane 12 folder in Settings."

        xp = _bare_xplane(tmp_path / "X-Plane 12")
        s = (await c.get("/api/settings")).json()
        s["essential"]["xplane_dir"] = str(xp)
        assert (await c.put("/api/settings", json=s)).status_code == 200
        r = await c.post("/api/plan", json=body)
        err = r.json()["error"]
        assert r.status_code == 422 and err["code"] == "XP_GLOBAL_SCENERY_NOT_FOUND", err
        assert err["context"]["path"] == str(xp.resolve()) and err["action"] == "settings"
        assert "<not detected>" not in err["message"] + err["remedy"]

        xp.rename(tmp_path / "unplugged")
        r = await c.post("/api/plan", json=body)
        err = r.json()["error"]
        assert r.status_code == 422 and err["code"] == "XP_DIR_NOT_FOUND"
        assert err["message"].startswith(
            f"The X-Plane 12 folder saved in Settings, {xp.resolve()}, was not found."
        )


@pytest.mark.anyio
async def test_the_airport_search_tries_again_once_x_plane_is_chosen(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without X-Plane the airport index cannot be built; it was never tried again, so the ICAO
    search stayed unavailable after the user chose the X-Plane folder in Settings."""
    import orthostudio.api.app as app_module
    from orthostudio.errors import OsxpError

    _no_xplane_on_this_machine(monkeypatch)
    monkeypatch.delenv("OSXP_API_NO_AIRPORTS", raising=False)
    calls: list[Path | None] = []

    def index(xplane_dir: Path | None) -> FakeIndex:
        calls.append(xplane_dir)
        if xplane_dir is None:
            raise OsxpError("XP_DIR_NOT_FOUND", context={"path": "(none)"})
        return FakeIndex()

    monkeypatch.setattr(app_module, "default_airport_index", index)
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    app = create_app(env_factory=None, jobs=mgr, airports=None, settings_path=home / "c.toml")
    try:
        async with client_for(app) as c:
            assert (await c.get("/api/airports", params={"q": "LFML"})).status_code == 422
            assert (await c.get("/api/airports", params={"q": "LFML"})).status_code == 503
            s = (await c.get("/api/settings")).json()
            s["essential"]["zoom_level"] = 17  # another answer: nothing to try again
            assert (await c.put("/api/settings", json=s)).status_code == 200
            assert (await c.get("/api/airports", params={"q": "LFML"})).status_code == 503
            assert calls == [None]

            xp = _bare_xplane(tmp_path / "X-Plane 12")
            s["essential"]["xplane_dir"] = str(xp)
            assert (await c.put("/api/settings", json=s)).status_code == 200
            r = await c.get("/api/airports", params={"q": "LFML"})
            assert r.status_code == 200 and [a["icao"] for a in r.json()] == ["LFML"]
            assert calls == [None, xp.resolve()]
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_security_guards(app) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        r = await c.get("/api/status", headers={"Host": "evil.example"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "SYS_FORBIDDEN_HOST"
        r = await c.get("/api/providers", headers={"Host": "localhost:8641"})
        assert r.status_code == 200
        big = b'{"tiles": ["' + b"x" * (4 * 1024 * 1024 + 1) + b'"]}'  # above MAX_BODY_BYTES
        r = await c.post("/api/plan", content=big, headers={"content-type": "application/json"})
        assert r.status_code == 413
        r = await c.post("/api/plan", json={"tiles": [f"+{i:02d}+005" for i in range(10, 80)]})
        assert r.status_code == 422  # more than 64 tiles
        r = await c.get("/")
        assert r.status_code == 503 and r.json()["error"]["code"] == "SYS_RESOURCE_MISSING"
        assert "access-control-allow-origin" not in r.headers


@pytest.mark.anyio
async def test_other_websites_cannot_use_the_api(home: Path, tmp_path: Path) -> None:
    """Review finding: ``<img src="http://127.0.0.1:8641/api/map/BI/19/...">`` on any website made
    OrthoStudio XP fetch map tiles, the guard checking ``Host`` only. A browser says where a request
    comes from (``Sec-Fetch-Site``); a client that does not say (curl, the CLI) is not refused."""
    fetched: list[str] = []

    async def upstream(request: FetchRequest) -> FetchResult:
        fetched.append(request.url)
        return FetchResult(request.key, 404, b"", {}, 0.0, 1, False, None)  # no imagery: 204

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html>")
    (ui / "app.js").write_text("// page")
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=None)
    app = create_app(env_factory=None, jobs=mgr, ui_dir=ui, settings_path=home / "config.toml",
                     map_fetch=upstream)  # fmt: skip
    zones = {"format": "osxp-zones-1", "zones": []}
    try:
        async with client_for(app) as c:
            for site in ("cross-site", "same-site", ""):
                refused = [
                    await c.get("/api/map/BI/3/4/2", headers={"Sec-Fetch-Site": site}),
                    await c.get("/api/zones", headers={"Sec-Fetch-Site": site}),
                    await c.put("/api/zones", json=zones, headers={"Sec-Fetch-Site": site}),
                    await c.post("/api/plan", json={"tiles": ["+43+005"]},
                                 headers={"Sec-Fetch-Site": site}),
                ]  # fmt: skip
                for r in refused:
                    assert r.status_code == 403, (site, r.request.url, r.text)
                    err = r.json()["error"]
                    assert err["code"] == "SYS_FORBIDDEN_ORIGIN" and err["remedy"], err
            assert fetched == [] and not (home / "zones.json").exists()
            # the page itself is not the API: a link from another website still opens it
            assert (await c.get("/", headers={"Sec-Fetch-Site": "cross-site"})).status_code == 200
            r = await c.get("/static/app.js", headers={"Sec-Fetch-Site": "cross-site"})
            assert r.status_code == 200
            # the page (same-origin), a typed URL (none) and a client without the header
            for y, headers in enumerate(
                ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {})
            ):
                r = await c.get(f"/api/map/BI/3/4/{y}", headers=headers)
                assert r.status_code == 204, (headers, r.text)
                assert (await c.get("/api/zones", headers=headers)).status_code == 200
            assert len(fetched) == 3
            r = await c.put("/api/zones", json=zones, headers={"Sec-Fetch-Site": "same-origin"})
            assert r.status_code == 200, r.text
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_plan_offline_two_lines(app, home: Path, xplane: Path) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        body = {"tiles": ["+43+005"], "provider": "BI", "zoom_level": 14,
                "overrides": {"masks_width": "200"}}  # fmt: skip
        r = await c.post("/api/plan", json=body)
        assert r.status_code == 200, r.text
        doc = r.json()
        net, comp, disk = doc["network"], doc["compute"], doc["disk"]
        assert net["probed"] is False and net["mbps_measured"] is None
        assert net["requests"] > 0 and net["mb"] > 0 and net["req_per_s"] == 400.0
        assert net["seconds_high"] == pytest.approx(net["seconds_low"] * 1.5, abs=0.11)
        assert comp["textures"] > 0 and comp["seconds"] > 0 and comp["workers"] >= 1
        assert comp["cached"] == 0
        assert disk["free_gb"] > 0 and disk["dds_gb"] > 0 and isinstance(disk["ok"], bool)
        (tile,) = doc["tiles"]
        assert tile["tile"] == "+43+005" and tile["zl"] == 14 and not tile["textures"]["exact"]
        assert doc["specs"] == [{"tile": "+43+005", "provider": "BI", "zl": 14}]
        assert doc["estimate"]["probe"] is None and isinstance(doc["warnings"], list)
        # an airport area through the fake index
        r = await c.post(
            "/api/plan", json={"airport": {"icao": "LFML", "radius_km": 0}, "zoom_level": 14}
        )
        assert r.status_code == 200 and r.json()["specs"][0]["tile"] == "+43+005"
        r = await c.post("/api/plan", json={"airport": {"icao": "ZZZZ"}, "zoom_level": 14})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_LATLON_INVALID"


def _ortho4xp_folder(root: Path) -> Path:
    ortho4xp_dir = root / "Ortho4XP"
    (ortho4xp_dir / "Ortho4XP.py").parent.mkdir(parents=True)
    (ortho4xp_dir / "Ortho4XP.py").write_text("# Ortho4XP\n")
    pack = ortho4xp_dir / "Tiles" / "zOrtho4XP_+43+005"
    dsf = pack / "Earth nav data" / "+40+000" / "+43+005.dsf"
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"XPLNEDSF")
    (pack / "Ortho4XP_+43+005.cfg").write_text("default_website=BI\ndefault_zl=16\n")
    return ortho4xp_dir


@pytest.mark.anyio
async def test_library_import_install_uninstall(
    app, home: Path, xplane: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    ortho4xp_dir = _ortho4xp_folder(tmp_path)
    cs = xplane / "Custom Scenery"
    real = Path.home() / "X-Plane 12" / "Custom Scenery" / "zOrtho4XP_+43+005"
    real_before = os.readlink(real) if real.is_symlink() else None  # the user may fly it
    async with client_for(app) as c:
        assert (await c.get("/api/library")).json() == []
        nope = {"folder": str(tmp_path / "nope")}
        r = await c.post("/api/library/import-ortho4xp", json=nope)
        assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_WORKING_DIR_INVALID"
        r = await c.post("/api/library/import-ortho4xp", json={"folder": str(ortho4xp_dir)})
        assert r.status_code == 200, r.text
        (row,) = r.json()
        assert row["tile"] == "+43+005" and row["built_by"] == "ortho4xp" and row["zl"] == 16
        r = await c.get("/api/library")
        (row,) = r.json()
        assert row["name"] == "zOrtho4XP_+43+005" and row["installed"] is False
        assert (await c.get("/api/status")).json()["library_count"] == 1
        r = await c.post("/api/library/+43+005/install", json={"xplane_dir": str(xplane)})
        assert r.status_code == 200, r.text
        target = cs / "zOrtho4XP_+43+005"
        assert target.is_symlink() and (target / "Earth nav data").is_dir()
        ini = (cs / "scenery_packs.ini").read_text()
        assert "SCENERY_PACK Custom Scenery/zOrtho4XP_+43+005/" in ini
        assert ini.index("zOrtho4XP_+43+005") < ini.index("z_autoortho")
        assert (cs / "scenery_packs.ini.bak").is_file()
        r = await c.get("/api/library")
        assert r.json()[0]["installed"] is True
        r = await c.post(
            "/api/library/zOrtho4XP_+43+005/uninstall", json={"xplane_dir": str(xplane)}
        )
        assert r.status_code == 200 and r.json()["removed"] is True
        assert not target.exists() and not target.is_symlink()
        assert "zOrtho4XP_+43+005" not in (cs / "scenery_packs.ini").read_text()
        r = await c.post("/api/library/+44+005/install", json={"xplane_dir": str(xplane)})
        assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_WORKING_DIR_INVALID"
        r = await c.post("/api/library/what/install")
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_LATLON_INVALID"
    # the real X-Plane folder of this machine was never touched: everything happened in tmp
    assert (os.readlink(real) if real.is_symlink() else None) == real_before


# -- delete, sizes, counts (docs/specs/api.md 2.3, install.md 4.2) ----------------------------


def _osxp_pack(home: Path, tile: str, out: Path | None = None) -> Path:
    """A pack of ``tile`` as ``osxp build`` writes it in ``out`` (the home's output folder by
    default), with its overlay DSF in the overlay pack beside it (no store behind it:
    ``test_delete.py`` has one)."""
    from orthostudio.model import TileRef
    from orthostudio.pipeline.pack import OVERLAY_PACK, PackManifest, write_pack

    t = TileRef.parse(tile)
    out = out or home / "tiles"
    src = home.parent / "src" / out.name / tile
    (src / "dsf" / "terrain").mkdir(parents=True)
    (src / "dsf" / f"{tile}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (src / "dsf" / "terrain" / "a.ter").write_text("A\n800\nTERRAIN\n")
    (src / "tex" / "textures").mkdir(parents=True)
    (src / "tex" / "textures" / "a.dds").write_bytes(b"DDS " + b"\1" * 64)
    (src / "overlay.dsf").write_bytes(b"XPLNEDSF overlay")
    files = write_pack(out, t, dsf_dir=src / "dsf", textures_dir=src / "tex",
                       overlay_file=src / "overlay.dsf")  # fmt: skip
    listed = {
        "dsf": t.dsf_relpath.as_posix(),
        "dsf_size": 108,
        "textures": 1,
        "terrain": 1,
        "overlay": f"../{OVERLAY_PACK}/{t.dsf_relpath.as_posix()}",
        "cfg": "",
    }
    manifest = PackManifest(tile, "BI", 16, {}, listed)
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    return files.pack_dir


@pytest.mark.anyio
async def test_library_delete_an_installed_tile(app, home: Path, xplane: Path) -> None:  # type: ignore[no-untyped-def]
    from orthostudio.clean import disk_bytes
    from orthostudio.install import default_library_path
    from orthostudio.model import TileRef
    from orthostudio.pipeline.pack import install_receipt

    cs = xplane / "Custom Scenery"
    pack = _osxp_pack(home, "+43+005")
    install_receipt(pack, cs, tile=TileRef(43, 5), library_path=default_library_path())
    async with client_for(app) as c:
        rows = {r["kind"]: r for r in (await c.get("/api/library")).json()}
        assert rows["ortho"]["present"] is True and rows["ortho"]["installed"] is True
        assert rows["ortho"]["size_bytes"] == disk_bytes([pack]) > 0
        assert rows["overlay"]["present"] is True and rows["overlay"]["size_bytes"] is None
        assert (await c.get("/api/status")).json()["library_count"] == 1  # one tile, two rows

        r = await c.post("/api/library/+43+005/delete", json={"xplane_dir": str(xplane)})

        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["freed_bytes"] > 0
        assert doc == {"format": "osxp-delete-1", "name": "zOrthoStudio_+43+005", "tile": "+43+005",
                       "removed_from_xplane": True, "pack_deleted": True,
                       "freed_bytes": doc["freed_bytes"], "custom_scenery": str(cs),
                       "warning": None}  # fmt: skip
        assert not os.path.lexists(cs / pack.name) and not pack.exists()
        assert "zOrthoStudio_+43+005" not in (cs / "scenery_packs.ini").read_text()
        assert (await c.get("/api/library")).json() == []
        assert (await c.get("/api/status")).json()["library_count"] == 0
        r = await c.post("/api/library/+43+005/delete")
        assert r.status_code == 422 and r.json()["error"]["code"] == "SYS_WORKING_DIR_INVALID"


@pytest.mark.anyio
async def test_library_delete_refuses_what_osxp_did_not_build_and_waits_for_builds(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    from orthostudio.install import Library, default_library_path
    from orthostudio.model import TileRef
    from test_api_fakes import make_spec

    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(delay_s=0.2), env_factory=None)
    app = create_app(env_factory=None, jobs=mgr, airports=FakeIndex(),
                     settings_path=home / "config.toml")  # fmt: skip
    ortho4xp_dir = _ortho4xp_folder(tmp_path)
    imported_pack = ortho4xp_dir / "Tiles" / "zOrtho4XP_+43+005"
    osxp_pack = _osxp_pack(home, "+43+005")  # the same tile, built
    mine = home / "tiles" / "zOrthoStudio_+45+005"  # a folder of the user's, no orthostudio.toml
    mine.mkdir(parents=True)
    (mine / "notes.txt").write_text("mine")
    try:
        async with client_for(app) as c:
            assert (
                await c.post("/api/library/import-ortho4xp", json={"folder": str(ortho4xp_dir)})
            ).status_code == 200
            with Library(default_library_path()) as lib:
                lib.register(TileRef(43, 5), "BI", 16, osxp_pack, "osxp", {})
                lib.register(TileRef(45, 5), "BI", 16, mine, "osxp", {})
            assert (await c.get("/api/status")).json()["library_count"] == 2  # +43+005 counts once

            r = await c.post("/api/library/+43+005/delete", json={"path": str(imported_pack)})
            assert r.status_code == 409, r.text
            err = r.json()["error"]
            assert err["code"] == "SYS_PACK_NOT_OSXP" and "Uninstall" in err["remedy"]
            assert err["message"] and imported_pack.is_dir()
            r = await c.post("/api/library/zOrthoStudio_+45+005/delete")
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_PACK_NOT_OSXP"
            assert (mine / "notes.txt").read_text() == "mine"
            for name, body, code in (
                ("+43+005", {"path": str(tmp_path / "elsewhere")}, "SYS_WORKING_DIR_INVALID"),
                ("+44+005", {}, "SYS_WORKING_DIR_INVALID"),
                ("what", {}, "CFG_LATLON_INVALID"),
                ("+43+005", {"bogus": 1}, "CFG_VALUE_INVALID"),
            ):
                r = await c.post(f"/api/library/{name}/delete", json=body)
                assert r.status_code == 422 and r.json()["error"]["code"] == code, (name, r.text)

            job = mgr.start([make_spec("+46+006", home=home)])
            r = await c.post("/api/library/+43+005/delete", json={"path": str(osxp_pack)})
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY"
            assert r.json()["error"]["remedy"] and osxp_pack.is_dir()
            mgr.cancel(job.id)
            assert job.wait(30)

            r = await c.post("/api/library/+43+005/delete", json={"path": str(osxp_pack)})
            assert r.status_code == 200, r.text
            assert r.json()["pack_deleted"] is True and not osxp_pack.exists()
            rows = (await c.get("/api/library")).json()
            assert {(row["tile"], row["built_by"]) for row in rows} == {
                ("+43+005", "ortho4xp"), ("+45+005", "osxp")
            }  # fmt: skip
            assert imported_pack.is_dir()
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_library_delete_without_xplane_and_of_a_folder_already_gone(
    app, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from orthostudio.api import specs
    from orthostudio.install import Library, default_library_path, packs
    from orthostudio.model import TileRef

    monkeypatch.delenv("OSXP_XPLANE_DIR", raising=False)
    monkeypatch.setattr(specs, "detect_xplane", lambda: None)  # no X-Plane on this machine
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    pack = _osxp_pack(home, "+43+005")
    gone = home / "tiles" / "zOrthoStudio_+44+005"
    with Library(default_library_path()) as lib:
        lib.register(TileRef(43, 5), "BI", 16, pack, "osxp", {})
        lib.register(TileRef(44, 5), "BI", 16, gone, "osxp", {})
    async with client_for(app) as c:
        rows = {r["tile"]: r for r in (await c.get("/api/library")).json()}
        assert rows["+44+005"]["present"] is False and rows["+44+005"]["size_bytes"] is None
        assert rows["+43+005"]["installed"] is False

        r = await c.post("/api/library/+43+005/delete")

        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["custom_scenery"] is None and doc["removed_from_xplane"] is False
        assert doc["pack_deleted"] is True and not pack.exists()

        r = await c.post("/api/library/+44+005/delete")

        assert r.status_code == 200, r.text
        assert r.json()["pack_deleted"] is False
        assert (await c.get("/api/library")).json() == []


@pytest.mark.anyio
async def test_status_counts_a_hard_linked_store_file_once(app, home: Path, xplane: Path) -> None:  # type: ignore[no-untyped-def]
    """``store_bytes`` said 50.3 GB for a store ``du`` measured at 24 GB: the index adds a DDS up
    once per artefact that hard-links it (``texture.dds`` and ``tile.textures``)."""
    dds = home / "store" / "texture.dds" / "ab" / ("ab" + "0" * 62)
    dds.parent.mkdir(parents=True)
    dds.write_bytes(b"D" * 5000)
    listed = home / "store" / "tile.textures" / "cd" / ("cd" + "0" * 62) / "textures" / "a.dds"
    listed.parent.mkdir(parents=True)
    os.link(dds, listed)
    (home / "chunks").mkdir()
    (home / "chunks" / "1_2.chunks").write_bytes(b"j" * 300)
    async with client_for(app) as c:
        doc = (await c.get("/api/status")).json()
    assert doc["store_bytes"] == 5000 and doc["chunks_bytes"] == 300


@pytest.mark.anyio
async def test_library_marks_installed_only_the_linked_pack(
    app, home: Path, xplane: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """A tile imported from Ortho4XP and the same tile built by OrthoStudio XP: only the pack whose
    link is in Custom Scenery counts as installed."""
    from orthostudio.install import default_library_path
    from orthostudio.model import TileRef
    from orthostudio.pipeline.pack import install_receipt

    ortho4xp_dir = _ortho4xp_folder(tmp_path)
    pack = _osxp_pack(home, "+43+005")
    async with client_for(app) as c:
        r = await c.post("/api/library/import-ortho4xp", json={"folder": str(ortho4xp_dir)})
        assert r.status_code == 200, r.text
        cs = xplane / "Custom Scenery"
        install_receipt(pack, cs, tile=TileRef(43, 5), library_path=default_library_path())
        rows = (await c.get("/api/library")).json()
    ortho = {r["built_by"]: r["installed"] for r in rows if r["kind"] == "ortho"}
    assert ortho == {"osxp": True, "ortho4xp": False}
    assert [r["installed"] for r in rows if r["kind"] == "overlay"] == [True]


@pytest.mark.anyio
async def test_no_build_starts_while_a_tile_is_deleted(
    app, xplane: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """The store clean that ends a delete could take an artefact a new build is about to reuse:
    a build asked for meanwhile is refused (409 SYS_BUSY), and a second delete waits its turn."""
    import asyncio
    import threading
    from types import SimpleNamespace

    from orthostudio.api import app as app_module
    from orthostudio.model import TileRef

    started = threading.Event()
    release = threading.Event()
    deleted: list[str] = []

    def pack_to_delete(name: str, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(path=Path("/packs") / name, tile=TileRef.parse(name))

    def delete_receipt(pack_dir: Path, **_kwargs: object) -> dict[str, object]:
        deleted.append(pack_dir.name)
        started.set()
        assert release.wait(10)
        return {"format": "osxp-delete-1", "name": pack_dir.name}

    monkeypatch.setattr(app_module, "pack_to_delete", pack_to_delete)
    monkeypatch.setattr(app_module, "delete_receipt", delete_receipt)
    async with client_for(app) as c:
        first = asyncio.ensure_future(c.post("/api/library/+43+005/delete"))
        assert await asyncio.to_thread(started.wait, 10)
        r = await c.post("/api/jobs", json={"tiles": ["+43+005"], "zoom_level": 14})
        assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_BUSY", r.text
        second = asyncio.ensure_future(c.post("/api/library/+44+005/delete"))
        await asyncio.sleep(0.2)
        assert deleted == ["+43+005"]  # the second delete waits for the first
        release.set()
        assert (await first).status_code == 200 and (await second).status_code == 200
        assert deleted == ["+43+005", "+44+005"]
        r = await c.post("/api/jobs", json={"tiles": ["+43+005"], "zoom_level": 14})
        assert r.status_code == 201, r.text


@pytest.mark.anyio
async def test_install_and_uninstall_act_on_the_row_clicked(
    app, home: Path, xplane: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """Review finding 7: with a tile imported from Ortho4XP and the same tile built by OrthoStudio
    XP, the buttons of either row acted on the newest one. The page sends the row's ``path`` now;
    without it (the first page, scripts) the newest row still answers. Both can be in X-Plane at
    once, under their own names: the built one is listed above, so it is the one drawn."""
    from orthostudio.install import Library, default_library_path
    from orthostudio.model import TileRef

    cs = xplane / "Custom Scenery"
    ortho4xp_dir = _ortho4xp_folder(tmp_path)
    imported_pack = ortho4xp_dir / "Tiles" / "zOrtho4XP_+43+005"
    osxp_pack = _osxp_pack(home, "+43+005")
    async with client_for(app) as c:
        assert (
            await c.post("/api/library/import-ortho4xp", json={"folder": str(ortho4xp_dir)})
        ).is_success
        with Library(default_library_path()) as lib:  # the osxp build: the newest row
            lib.register(TileRef(43, 5), "BI", 16, osxp_pack, "osxp", {})

        body = {"xplane_dir": str(xplane), "path": str(osxp_pack)}
        r = await c.post("/api/library/+43+005/install", json=body)
        assert r.status_code == 200, r.text
        assert os.path.realpath(cs / "zOrthoStudio_+43+005") == os.path.realpath(osxp_pack)
        rows = (await c.get("/api/library")).json()
        installed = {row["built_by"]: row["installed"] for row in rows if row["kind"] == "ortho"}
        assert installed == {"osxp": True, "ortho4xp": False}

        # the Ortho4XP row's Uninstall must not take the OrthoStudio XP pack out of X-Plane
        ini = (cs / "scenery_packs.ini").read_bytes()
        body = {"xplane_dir": str(xplane), "path": str(imported_pack)}
        r = await c.post("/api/library/zOrtho4XP_+43+005/uninstall", json=body)
        assert r.status_code == 200 and r.json()["removed"] is False, r.text
        assert os.path.islink(cs / "zOrthoStudio_+43+005")
        assert (cs / "scenery_packs.ini").read_bytes() == ini

        for route in ("install", "uninstall"):
            body = {"xplane_dir": str(xplane), "path": str(tmp_path / "elsewhere")}
            r = await c.post(f"/api/library/+43+005/{route}", json=body)
            assert r.status_code == 422, r.text
            assert r.json()["error"]["code"] == "SYS_WORKING_DIR_INVALID"

        body = {"xplane_dir": str(xplane), "path": str(osxp_pack)}
        r = await c.post("/api/library/zOrthoStudio_+43+005/uninstall", json=body)
        assert r.status_code == 200 and r.json()["removed"] is True, r.text
        assert not os.path.lexists(cs / "zOrthoStudio_+43+005")

        body = {"xplane_dir": str(xplane), "path": str(imported_pack)}
        r = await c.post("/api/library/+43+005/install", json=body)
        assert r.status_code == 200, r.text
        assert os.path.realpath(cs / "zOrtho4XP_+43+005") == os.path.realpath(imported_pack)

        # both in X-Plane: the tile OrthoStudio XP built is listed above the imported one
        from orthostudio.install import SceneryPacks

        body = {"xplane_dir": str(xplane), "path": str(osxp_pack)}
        r = await c.post("/api/library/+43+005/install", json=body)
        assert r.status_code == 200, r.text
        names = SceneryPacks.load(cs / "scenery_packs.ini").names()
        assert names.index("zOrthoStudio_+43+005") < names.index("zOrtho4XP_+43+005")


@pytest.mark.anyio
async def test_a_tile_in_a_build_keeps_its_pack_where_it_is(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """While its tile is in a build, running or queued, a pack OrthoStudio XP built is neither
    added to X-Plane nor taken out of it (409 ``SYS_TILE_IN_BUILD``): the end of the build decides.
    The pack of another tile, and the one Ortho4XP built of the same tile, stay free."""
    from orthostudio.install import Library, default_library_path
    from orthostudio.model import TileRef
    from orthostudio.pipeline.pack import install_receipt
    from test_api_fakes import make_spec

    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(delay_s=0.2), env_factory=None)
    app = create_app(env_factory=None, jobs=mgr, airports=FakeIndex(),
                     settings_path=home / "config.toml")  # fmt: skip
    cs = xplane / "Custom Scenery"
    ortho4xp_dir = _ortho4xp_folder(tmp_path)
    imported_pack = ortho4xp_dir / "Tiles" / "zOrtho4XP_+43+005"
    building = _osxp_pack(home, "+43+005")
    install_receipt(building, cs, tile=TileRef(43, 5), library_path=default_library_path())
    other = _osxp_pack(home, "+44+005")
    try:
        async with client_for(app) as c:
            r = await c.post("/api/library/import-ortho4xp", json={"folder": str(ortho4xp_dir)})
            assert r.status_code == 200, r.text
            with Library(default_library_path()) as lib:
                lib.register(TileRef(44, 5), "BI", 16, other, "osxp", {})
            job = mgr.start([make_spec("+45+005", home=home)])
            waiting = mgr.start([make_spec("+43+005", home=home)], queue=True)
            for route in ("uninstall", "install"):
                body = {"xplane_dir": str(xplane), "path": str(building)}
                r = await c.post(f"/api/library/zOrthoStudio_+43+005/{route}", json=body)
                assert r.status_code == 409, r.text
                err = r.json()["error"]
                assert err["code"] == "SYS_TILE_IN_BUILD" and err["remedy"]
                assert err["context"] == {"tiles": ["+43+005"], "jobs": [waiting.id]}
            assert os.path.realpath(cs / building.name) == os.path.realpath(building)
            r = await c.post("/api/library/+44+005/install", json={"xplane_dir": str(xplane)})
            assert r.status_code == 200, r.text
            body = {"xplane_dir": str(xplane), "path": str(imported_pack)}
            r = await c.post("/api/library/+43+005/install", json=body)
            assert r.status_code == 200, r.text
            mgr.cancel_all()
            assert job.wait(30) and waiting.wait(30)
            body = {"xplane_dir": str(xplane), "path": str(building)}
            r = await c.post("/api/library/zOrthoStudio_+43+005/uninstall", json=body)
            assert r.status_code == 200 and r.json()["removed"] is True, r.text
    finally:
        mgr.close()


@pytest.mark.anyio
async def test_uninstall_of_a_row_leaves_the_pack_another_row_built_in_custom_scenery(
    app, home: Path, xplane: Path
) -> None:  # type: ignore[no-untyped-def]
    """The same tile built twice by OrthoStudio XP, once straight into Custom Scenery (A, installed
    as it is) and once into the output folder (B): the Uninstall of B took A for B's copy and
    deleted it (``uninstall_receipt`` removes a real folder holding orthostudio.toml)."""
    from orthostudio.install import Library, default_library_path
    from orthostudio.model import TileRef
    from orthostudio.pipeline.pack import install_receipt

    cs = xplane / "Custom Scenery"
    a = _osxp_pack(home, "+43+005", out=cs)
    install_receipt(a, cs, tile=TileRef(43, 5), library_path=default_library_path())
    b = _osxp_pack(home, "+43+005")
    with Library(default_library_path()) as lib:
        lib.register(TileRef(43, 5), "BI", 16, b, "osxp", {})
    async with client_for(app) as c:
        body = {"xplane_dir": str(xplane), "path": str(b)}
        r = await c.post("/api/library/+43+005/uninstall", json=body)
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "XP_PACK_CONFLICT" and "Nothing was changed" in err["message"]
    assert (a / "orthostudio.toml").is_file() and "zOrthoStudio_+43+005" in (
        cs / "scenery_packs.ini"
    ).read_text()


@pytest.mark.anyio
async def test_a_delete_whose_cache_cannot_be_freed_answers_200_with_a_warning(
    app, home: Path, xplane: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Review finding 5b: the tile is gone once its rows are forgotten; a store clean failing
    after that is the receipt's ``warning`` (null otherwise), not an error card."""
    import orthostudio.clean
    from orthostudio.install import Library, default_library_path
    from orthostudio.model import TileRef

    def locked(*_args: object, **_kwargs: object) -> None:
        raise OSError("database is locked")

    monkeypatch.setattr(orthostudio.clean, "clean_after_delete", locked)
    pack = _osxp_pack(home, "+43+005")
    with Library(default_library_path()) as lib:
        lib.register(TileRef(43, 5), "BI", 16, pack, "osxp", {"dsf": "0" * 64})
    async with client_for(app) as c:
        r = await c.post("/api/library/+43+005/delete", json={"xplane_dir": str(xplane)})
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["pack_deleted"] is True and not pack.exists() and doc["freed_bytes"] > 0
        assert (
            doc["warning"].startswith("The tile is deleted, but") and "osxp clean" in doc["warning"]
        )
        assert (await c.get("/api/library")).json() == []


@pytest.mark.anyio
async def test_a_form_of_another_website_cannot_post_to_the_api(
    app, xplane: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Review finding 8 (v8): a <form method=POST> of another website, in a browser that sends
    no Sec-Fetch-Site, reached the delete route (no path: the newest osxp build). A form cannot
    send JSON; the page always does, or sends no body at all."""
    from types import SimpleNamespace

    from orthostudio.api import app as app_module
    from orthostudio.model import TileRef

    reached: list[str] = []

    def pack_to_delete(name: str, **_kwargs: object) -> SimpleNamespace:
        reached.append(name)
        return SimpleNamespace(path=Path("/packs") / name, tile=TileRef.parse(name))

    def delete_receipt(pack_dir: Path, **_kwargs: object) -> dict[str, object]:
        return {"format": "osxp-delete-1", "name": pack_dir.name, "warning": None}

    monkeypatch.setattr(app_module, "pack_to_delete", pack_to_delete)
    monkeypatch.setattr(app_module, "delete_receipt", delete_receipt)
    async with client_for(app) as c:
        for media in ("application/x-www-form-urlencoded", "multipart/form-data; boundary=x",
                      "text/plain", ""):  # fmt: skip
            r = await c.post(
                "/api/library/+43+005/delete", content=b"", headers={"content-type": media}
            )
            assert r.status_code == 415, (media, r.text)
            err = r.json()["error"]
            assert err["code"] == "SYS_BAD_CONTENT_TYPE" and "JSON" in err["message"], media
            assert err["remedy"]
        r = await c.put("/api/zones", content=b"zones", headers={"content-type": "text/plain"})
        assert r.status_code == 415
        assert reached == []

        r = await c.post("/api/library/+43+005/delete", content=b"{}",
                         headers={"content-type": "application/json; charset=utf-8"})  # fmt: skip
        assert r.status_code == 200, r.text
        r = await c.post("/api/library/+44+005/delete")  # no body, no Content-Type: the page's way
        assert r.status_code == 200, r.text
        assert reached == ["+43+005", "+44+005"]
        r = await c.get("/api/status", headers={"content-type": "text/plain"})
        assert r.status_code == 200  # a GET carries no body to guard


# -- serve wiring ------------------------------------------------------------------------------


def test_serve_check_starts_the_real_server_and_stops_it(home: Path) -> None:
    """``osxp serve --check`` is what CI runs: it must bind, answer, and leave nothing behind."""
    import socket
    import threading
    import time

    from orthostudio.api import serve as serve_mod

    with socket.socket() as probe:
        probe.bind((serve_mod.HOST, 0))
        port = probe.getsockname()[1]
    before = threading.active_count()

    status = serve_mod.check(port=port, timeout_s=30.0)

    assert status["page_status"] == 200
    assert status["version"]
    assert isinstance(status["doctor"], list) and status["doctor"]
    with socket.socket() as freed:  # the port is free again
        freed.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        freed.bind((serve_mod.HOST, port))
    for _ in range(100):
        if threading.active_count() <= before:
            break
        time.sleep(0.05)
    assert threading.active_count() <= before


def test_serve_refuses_a_busy_port(home: Path) -> None:
    import socket

    from orthostudio.api import serve as serve_mod
    from orthostudio.errors import OsxpError

    with socket.socket() as busy:
        busy.bind((serve_mod.HOST, 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        with pytest.raises(OsxpError) as exc:
            serve_mod.check(port=port, timeout_s=2.0)
    assert "in use" in exc.value.message


@pytest.mark.anyio
async def test_library_installed_is_read_against_the_given_xplane(
    app, home: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """``GET /api/library?xplane_dir=`` answers about that install, not the detected one."""
    from orthostudio.install import Library, default_library_path
    from orthostudio.model import TileRef

    pack = tmp_path / "tiles" / "zOrthoStudio_+43+005"
    pack.mkdir(parents=True)
    with Library(default_library_path()) as lib:
        lib.register(TileRef(43, 5), kind="ortho", path=pack, provider="BI", zl=14, built_by="osxp")

    other = tmp_path / "other-xplane"
    (other / "Custom Scenery").mkdir(parents=True)
    (other / "Resources").mkdir()

    async with client_for(app) as c:
        rows = (await c.get("/api/library", params={"xplane_dir": str(other)})).json()
        assert [r["installed"] for r in rows] == [False]
        (other / "Custom Scenery" / pack.name).symlink_to(pack, target_is_directory=True)
        rows = (await c.get("/api/library", params={"xplane_dir": str(other)})).json()
        assert [r["installed"] for r in rows] == [True]


@pytest.mark.anyio
async def test_the_page_files_are_revalidated_on_every_load(tmp_path: Path) -> None:
    """An old ``geo.js`` cached next to a new ``map.js`` stopped the page after an update: the
    page's files carry ``Cache-Control: no-cache`` (``app.PAGE_CACHE_CONTROL``)."""
    from orthostudio.api.app import PAGE_CACHE_CONTROL, create_app
    from orthostudio.api.jobs import JobManager
    from orthostudio.ui import ui_dir

    manager = JobManager(jobs_dir=tmp_path / "jobs", build=fakes.FakeBuild(), env_factory=None)
    app = create_app(
        env_factory=None, jobs=manager, settings_path=tmp_path / "config.toml", ui_dir=ui_dir()
    )
    try:
        async with client_for(app) as c:
            for path in ("/", "/static/app.js", "/static/geo.js", "/static/map.js"):
                r = await c.get(path)
                assert r.status_code == 200, path
                assert r.headers["cache-control"] == PAGE_CACHE_CONTROL, path
    finally:
        manager.close()
