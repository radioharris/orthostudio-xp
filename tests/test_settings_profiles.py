# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Settings profiles (``docs/specs/settings.md`` section 7, ``api.md`` ``/api/profiles``): named
sets of the build settings, one of them current, kept in ``profiles.json`` beside
``config.toml``; what belongs to the computer is the same for them all."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.config import Settings, load_settings, settings_from_dict
from orthostudio.config.profiles import (
    COMPUTER_SETTINGS,
    ProfileError,
    Profiles,
    build_part,
    check_name,
    create,
    delete,
    load_profiles,
    remember,
    rename,
    replace_provider,
    save_profiles,
    switch,
    with_build_part,
)
from orthostudio.errors import OsxpError
from test_api_fakes import FakeBuild, FakeIndex, client_for
from test_api_sources import ADDRESS, _app

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane

TOKEN = "secret-key-of-the-library"


def _settings(**leaves: object) -> Settings:
    data = Settings().model_dump(mode="json")
    for path, value in leaves.items():
        here = data
        *head, last = path.split("__")
        for key in head:
            here = here[key]
        here[last] = value
    return settings_from_dict(data)


def _leaf(settings: Settings | dict, path: str) -> object:
    here = settings.model_dump(mode="json") if isinstance(settings, Settings) else settings
    for key in path.split("."):
        here = here[key]
    return here


# -- what a profile holds -------------------------------------------------------------------------


def test_a_profile_keeps_the_build_settings_and_never_the_computers_own() -> None:
    """The folders, SimBrief, the map data's source and its key, the patches, the update check and
    the memory stay out of a profile: switching profile must not send the tiles to another disk or a
    build to the public servers, and the library's key stays out of a file one may pass around."""
    s = _settings(
        essential__zoom_level=17,
        essential__xplane_dir="/X-Plane 12",
        essential__data_dir="/Volumes/Tiles",
        expert__osm_library_token=TOKEN,
        expert__build_memory="high",
    )
    part = build_part(s)
    assert _leaf(part, "essential.zoom_level") == 17
    for path in COMPUTER_SETTINGS:
        head, _, last = path.rpartition(".")
        assert last not in _leaf(part, head), path
    assert TOKEN not in json.dumps(part)


def test_a_profile_taken_keeps_the_computers_settings_and_defaults_what_it_lacks() -> None:
    here = _settings(
        essential__zoom_level=16,
        essential__provider="Arc",
        essential__xplane_dir="/X-Plane 12",
        expert__osm_library_token=TOKEN,
        expert__road_smoothing=100,
    )
    part = build_part(_settings(essential__zoom_level=18, essential__provider="BI"))
    # a profile kept by a version before road smoothing does not hold it: its default, not ours
    del part["expert"]["road_smoothing"]
    # and a value this version cannot read is left at its default, as config.toml's are
    part["advanced"]["road_level"] = 9
    got, problems = with_build_part(here, part)
    assert got.essential.zoom_level == 18 and got.essential.provider == "BI"
    assert got.essential.xplane_dir == "/X-Plane 12" and got.expert.osm_library_token == TOKEN
    assert got.expert.road_smoothing == 0
    assert got.advanced.road_level == 1 and problems and "road_level" in problems[0]


def test_names_are_one_to_forty_characters_and_unique_whatever_the_case() -> None:
    assert check_name("  Alpes  ") == "Alpes"
    for bad in ("", "   ", "x" * 41, "two\nlines", None):
        with pytest.raises(ProfileError) as info:
            check_name(bad)
        assert info.value.code == "CFG_PROFILE_NAME" and info.value.status == 422
    with pytest.raises(ProfileError) as info:
        check_name("alpes", ["Principal", "Alpes"])
    # the one that has it, as it is written (the page quoted the name typed, a campaign 2026-11-03)
    assert info.value.code == "CFG_PROFILE_TAKEN" and info.value.context == {"name": "Alpes"}


# -- what the page asks ----------------------------------------------------------------------------


def test_the_first_new_profile_names_the_settings_kept_until_then() -> None:
    before = _settings(essential__zoom_level=16)
    after = _settings(essential__zoom_level=18)
    kept = create(Profiles(), "Alpes", before, after, "Principal")
    assert kept.current == "Alpes" and kept.names == ["Principal", "Alpes"]
    assert _leaf(kept.entries[0][1], "essential.zoom_level") == 16
    assert _leaf(kept.entries[1][1], "essential.zoom_level") == 18
    with pytest.raises(ProfileError) as info:
        create(Profiles(), "Principal", before, after, "Principal")
    assert info.value.code == "CFG_PROFILE_TAKEN"


def test_switching_keeps_what_the_profile_left_held_and_takes_the_others() -> None:
    kept = create(Profiles(), "Alpes", _settings(), _settings(essential__zoom_level=18), "Main")
    # Alpes saved again with ZL17 since: the settings on disk hold it, the file still ZL18
    now = _settings(essential__zoom_level=17, essential__data_dir="/Volumes/Tiles")
    kept, s, problems = switch(kept, "main", now)
    assert kept.current == "Main" and not problems
    assert s.essential.zoom_level == 16 and s.essential.data_dir == "/Volumes/Tiles"
    assert _leaf(kept.entries[1][1], "essential.zoom_level") == 17
    with pytest.raises(ProfileError) as info:
        switch(kept, "Nowhere", now)
    assert info.value.code == "CFG_PROFILE_UNKNOWN" and info.value.status == 404


def test_rename_delete_and_the_last_profile() -> None:
    kept = create(Profiles(), "Alpes", _settings(), _settings(essential__zoom_level=18), "Main")
    kept = rename(kept, "Alpes", "alpes")  # its own name in another case is no conflict
    assert kept.current == "alpes" and kept.names == ["Main", "alpes"]
    with pytest.raises(ProfileError):
        rename(kept, "alpes", "MAIN")
    left, s, _ = delete(kept, "Main", _settings(essential__zoom_level=18))
    assert s is None and left.names == ["alpes"] and left.current == "alpes"
    # one alone is no choice: none is named, as the page shows none (Works named "Main" after the
    # others were deleted, a review, 2026-11-07)
    assert kept.named == "alpes" and left.named is None
    # the current one deleted: the first of the others becomes current, its settings with it
    left, s, _ = delete(kept, "alpes", _settings(essential__zoom_level=18))
    assert left.current == "Main" and s is not None and s.essential.zoom_level == 16
    with pytest.raises(ProfileError) as info:
        delete(left, "Main", s)
    assert info.value.code == "CFG_PROFILE_LAST"


def test_a_removed_source_leaves_every_profile() -> None:
    kept = create(
        Profiles(), "Mine", _settings(), _settings(essential__provider="Mysatellite"), "Main"
    )
    kept = replace_provider(kept, "Mysatellite", "BI")
    assert [_leaf(part, "essential.provider") for _, part in kept.entries] == ["BI", "BI"]


def test_the_file_round_trips_and_an_unreadable_one_is_said(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    assert load_profiles(path) == Profiles()
    kept = create(Profiles(), "Été", _settings(), _settings(essential__zoom_level=18), "Main")
    save_profiles(kept, path)
    assert "Été" in path.read_text(encoding="utf-8")
    assert load_profiles(path) == kept
    save_profiles(remember(kept, _settings(essential__zoom_level=17)), path)
    assert (tmp_path / "profiles.json.bak").is_file()
    # a current name the file does not hold: its first profile
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["current"] = "Gone"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert load_profiles(path).current == "Main"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OsxpError) as info:
        load_profiles(path)
    assert info.value.code == "CFG_LINE_INVALID"
    # cut short: the line JSON names, where "line 0" said nothing (a campaign, 2026-11-03)
    path.write_text('{\n "profiles": [\n  {"name": "Main",', encoding="utf-8")
    with pytest.raises(OsxpError) as info:
        load_profiles(path)
    assert info.value.context["line"] == 3


# -- the API ---------------------------------------------------------------------------------------


@pytest.fixture
def app(home: Path):  # type: ignore[no-untyped-def]
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(), env_factory=lambda specs: None)
    yield create_app(env_factory=None, jobs=mgr, airports=FakeIndex(),
                     settings_path=home / "config.toml")  # fmt: skip
    mgr.close()


@pytest.mark.anyio
async def test_a_user_keeps_two_profiles_and_goes_from_one_to_the_other(
    app, home: Path, xplane: Path
) -> None:  # type: ignore[no-untyped-def]
    """Settings changed, then kept as a new profile; the first keeps what it held; each switch
    saves the other's settings, the computer's own kept; Save goes into the current one."""
    async with client_for(app) as c:
        assert (await c.get("/api/profiles")).json() == {
            "current": None, "profiles": [], "computer": list(COMPUTER_SETTINGS)}  # fmt: skip
        s = (await c.get("/api/settings")).json()
        s["essential"]["xplane_dir"] = str(xplane)
        s["expert"]["osm_library_token"] = TOKEN
        assert (await c.put("/api/settings", json=s)).status_code == 200
        assert not (home / "profiles.json").exists(), "one profile: no file, nothing changes"

        s["essential"]["zoom_level"] = 18
        s["essential"]["provider"] = "Arc"
        r = await c.post("/api/profiles", json={"name": "Alpes", "settings": s,
                                                "first_name": "Principal"})  # fmt: skip
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["current"] == "Alpes" and got["profiles"] == ["Principal", "Alpes"]
        assert got["settings"]["essential"]["zoom_level"] == 18
        assert load_settings(home / "config.toml").essential.zoom_level == 18
        assert TOKEN not in (home / "profiles.json").read_text(encoding="utf-8")

        r = await c.post("/api/profiles/current", json={"name": "Principal"})
        assert r.status_code == 200, r.text
        back = r.json()["settings"]
        assert back["essential"]["zoom_level"] == 16 and back["essential"]["provider"] == "BI"
        assert back["essential"]["xplane_dir"] == str(xplane)
        assert back["expert"]["osm_library_token"] == TOKEN
        assert (await c.get("/api/settings")).json() == back

        back["advanced"]["road_level"] = 3  # saved into Principal
        assert (await c.put("/api/settings", json=back)).status_code == 200
        r = await c.post("/api/profiles/current", json={"name": "Alpes"})
        assert r.json()["settings"]["advanced"]["road_level"] == 1
        assert r.json()["settings"]["essential"]["zoom_level"] == 18
        r = await c.post("/api/profiles/current", json={"name": "Principal"})
        assert r.json()["settings"]["advanced"]["road_level"] == 3

        r = await c.post("/api/profiles", json={"name": "alpes", "settings": back})
        assert r.status_code == 409 and r.json()["error"]["code"] == "CFG_PROFILE_TAKEN"
        assert r.json()["error"]["context"] == {"name": "Alpes"}  # the one that has it
        assert (await c.get("/api/profiles")).json()["current"] == "Principal", "nothing changed"
        r = await c.post("/api/profiles", json={"name": " ", "settings": back})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CFG_PROFILE_NAME"


@pytest.mark.anyio
async def test_rename_and_delete_through_the_api(app, home: Path) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app) as c:
        s = (await c.get("/api/settings")).json()
        s["essential"]["zoom_level"] = 17
        r = await c.post("/api/profiles", json={"name": "VFR", "settings": s,
                                                "first_name": "Main"})  # fmt: skip
        assert r.status_code == 201, r.text
        r = await c.post("/api/profiles/rename", json={"name": "VFR", "new_name": "VFR Alpes"})
        assert r.json()["profiles"] == ["Main", "VFR Alpes"] and r.json()["current"] == "VFR Alpes"
        r = await c.post("/api/profiles/rename", json={"name": "Gone", "new_name": "X"})
        assert r.status_code == 404 and r.json()["error"]["code"] == "CFG_PROFILE_UNKNOWN"
        # the current one deleted: Main becomes current and its settings are saved
        r = await c.post("/api/profiles/delete", json={"name": "VFR Alpes"})
        assert r.status_code == 200, r.text
        assert r.json()["current"] == "Main" and r.json()["profiles"] == ["Main"]
        assert r.json()["settings"]["essential"]["zoom_level"] == 16
        assert load_settings(home / "config.toml").essential.zoom_level == 16
        r = await c.post("/api/profiles/delete", json={"name": "Main"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "CFG_PROFILE_LAST"


@pytest.mark.anyio
async def test_a_page_left_behind_by_another_saves_into_no_profile_it_never_showed(
    app, home: Path
) -> None:  # type: ignore[no-untyped-def]
    """Two pages of one engine (the app started again opens one in the browser): the second
    makes Alpes current, the first, still on Main, saves. Its answers went into Alpes, whose own
    settings were lost without a word (a campaign, 2026-11-04); now nothing is saved."""
    async with client_for(app) as c:
        s = (await c.get("/api/settings")).json()
        assert (await c.put("/api/settings?profile=", json=s)).status_code == 200, "no profile yet"
        s["essential"]["zoom_level"] = 18
        r = await c.post("/api/profiles", json={"name": "Alpes", "settings": s,
                                                "first_name": "Main"})  # fmt: skip
        assert r.status_code == 201, r.text
        page = (await c.post("/api/profiles/current", json={"name": "Main"})).json()["settings"]
        await c.post("/api/profiles/current", json={"name": "Alpes"})  # the other page
        kept = (home / "profiles.json").read_text(encoding="utf-8")
        saved = (home / "config.toml").read_text(encoding="utf-8")

        page["advanced"]["road_level"] = 3
        r = await c.put("/api/settings?profile=Main", json=page)
        assert r.status_code == 409 and r.json()["error"]["code"] == "CFG_PROFILE_CHANGED"
        assert r.json()["error"]["context"] == {"name": "Alpes"}
        assert (home / "profiles.json").read_text(encoding="utf-8") == kept
        assert (home / "config.toml").read_text(encoding="utf-8") == saved
        # renamed in the other page: the name this page holds is gone, nothing saved either
        await c.post("/api/profiles/rename", json={"name": "Alpes", "new_name": "Alpes VFR"})
        r = await c.put("/api/settings?profile=Alpes", json=page)
        assert r.json()["error"] == r.json()["error"] | {"context": {"name": "Alpes VFR"}}
        # the page reads the list again: Save names Alpes VFR, and goes into it
        r = await c.put("/api/settings?profile=Alpes%20VFR", json=page)
        assert r.status_code == 200, r.text
        got = (await c.post("/api/profiles/current", json={"name": "Main"})).json()["settings"]
        assert got["advanced"]["road_level"] == 1, "Main kept what it held"
        # a save that names no profile (an older page) is saved as before
        assert (await c.put("/api/settings", json=got)).status_code == 200


@pytest.mark.anyio
async def test_a_build_says_its_profile_and_works_keeps_it(  # type: ignore[no-untyped-def]
    app, home: Path, xplane: Path
) -> None:
    # the tests' own X-Plane: without one on the machine, the build was refused (the ci's)
    async with client_for(app) as c:
        plan = {"tiles": ["+43+005"], "zoom_level": 14, "install": False}
        r = await c.post("/api/jobs", json=plan)
        assert r.status_code == 201, r.text
        first = (await c.get(f"/api/jobs/{r.json()['job_id']}")).json()
        assert first["profile"] is None and "profile" not in first["request"]
        s = (await c.get("/api/settings")).json()
        r = await c.post("/api/profiles", json={"name": "Alpes", "settings": s,
                                                "first_name": "Main"})  # fmt: skip
        assert r.status_code == 201
        for job in (await c.get("/api/jobs")).json():
            assert job["profile"] is None
        r = await c.post("/api/jobs", json={**plan, "queue": True})
        assert r.status_code == 201, r.text
        job = (await c.get(f"/api/jobs/{r.json()['job_id']}")).json()
        assert job["profile"] == "Alpes" and job["request"]["profile"] == "Alpes"
        assert {j["id"]: j["profile"] for j in (await c.get("/api/jobs")).json()}[
            job["id"]
        ] == "Alpes"
        # down to one profile: a build names none, as the page shows none
        r = await c.post("/api/profiles/delete", json={"name": "Main"})
        assert r.status_code == 200, r.text
        r = await c.post("/api/jobs", json={**plan, "queue": True})
        assert r.status_code == 201, r.text
        job = (await c.get(f"/api/jobs/{r.json()['job_id']}")).json()
        assert job["profile"] is None and "profile" not in job["request"]


@pytest.mark.anyio
async def test_a_source_removed_leaves_the_profiles_too(home: Path) -> None:
    app, mgr = _app(home)
    try:
        async with client_for(app) as c:
            r = await c.post("/api/sources", json={"name": "My satellite", "url_template": ADDRESS})
            assert r.status_code == 201, r.text
            s = (await c.get("/api/settings")).json()
            s["essential"]["provider"] = "Mysatellite"
            r = await c.post("/api/profiles", json={"name": "Mine", "settings": s,
                                                    "first_name": "Main"})  # fmt: skip
            assert r.status_code == 201, r.text
            await c.post("/api/profiles/current", json={"name": "Main"})
            assert (await c.delete("/api/sources/Mysatellite")).status_code == 200
            r = await c.post("/api/profiles/current", json={"name": "Mine"})
            assert r.json()["settings"]["essential"]["provider"] == "BI"
    finally:
        mgr.close()
