"""The overlays of other packs on the squares of OrthoStudio XP tiles (``docs/specs/install.md``
4.3): X-Plane draws every active pack's overlays, so AutoOrtho's, XPME's or Ortho4XP's roads,
forests and buildings came twice on a square an OrthoStudio XP tile also had (a user asked,
2026-09-14). A fake X-Plane in a temporary folder; nothing of the machine's is read or written."""

from __future__ import annotations

from pathlib import Path

import pytest

import test_api_fakes as fakes
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.errors import OsxpError
from orthostudio.install import default_library_path
from orthostudio.install import packs as install_packs
from orthostudio.model import OVERLAY_PACK, TileRef
from orthostudio.pipeline.pack import (
    LEFT_OVERLAY,
    install_receipt,
    leave_overlay,
    overlay_states,
    pack_is_intact,
    read_manifest,
    take_back_overlay,
    uninstall_receipt,
    write_pack,
)
from test_api_app import _osxp_pack
from test_api_fakes import FakeBuild, FakeIndex, client_for, make_spec

anyio_backend = fakes.anyio_backend
home = fakes.home
xplane = fakes.xplane

TILE = TileRef(43, 5)
AO_LINE = "SCENERY_PACK Custom Scenery/yAutoOrtho_Overlays/\n"


def _autoortho_overlay(cs: Path, tile: TileRef = TILE) -> Path:
    """AutoOrtho's shared overlays pack holding the square, listed active above the tiles."""
    dsf = cs / "yAutoOrtho_Overlays" / tile.dsf_relpath
    dsf.parent.mkdir(parents=True, exist_ok=True)
    dsf.write_bytes(b"XPLNEDSF autoortho overlay")
    ini = cs / "scenery_packs.ini"
    text = ini.read_text()
    if AO_LINE not in text:
        text = text.replace(
            "SCENERY_PACK *GLOBAL_AIRPORTS*\n", "SCENERY_PACK *GLOBAL_AIRPORTS*\n" + AO_LINE
        )
        ini.write_text(text)
    return dsf


def _states(cs: Path) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {name: (s.state, s.others) for name, s in overlay_states(cs).items()}


def test_an_overlay_left_to_another_pack_stays_left_until_taken_back(
    home: Path, xplane: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = xplane / "Custom Scenery"
    pack = _osxp_pack(home, "+43+005")
    install_receipt(pack, cs, tile=TILE, library_path=default_library_path())
    shared = pack.parent / OVERLAY_PACK / TILE.dsf_relpath
    assert _states(cs) == {"+43+005": ("own", ())}

    _autoortho_overlay(cs)
    assert _states(cs) == {"+43+005": ("double", ("yAutoOrtho_Overlays",))}

    assert leave_overlay(pack, TILE) is True
    assert not shared.exists() and (pack / LEFT_OVERLAY).read_bytes() == b"XPLNEDSF overlay"
    assert _states(cs) == {"+43+005": ("left", ("yAutoOrtho_Overlays",))}
    assert leave_overlay(pack, TILE) is False  # nothing left to move
    assert pack_is_intact(pack, read_manifest(pack))  # a build does not assemble it again

    # an uninstall and an install keep the choice
    uninstall_receipt("zOrthoStudio_+43+005", cs)
    install_receipt(pack, cs, tile=TILE, library_path=default_library_path())
    assert not shared.exists() and _states(cs) == {"+43+005": ("left", ("yAutoOrtho_Overlays",))}

    # and so does a build again: the new overlay waits in the pack
    src = home.parent / "src" / "tiles" / "+43+005"
    (src / "overlay.dsf").write_bytes(b"XPLNEDSF overlay, built again")
    write_pack(home / "tiles", TILE, dsf_dir=src / "dsf", textures_dir=src / "tex",
               overlay_file=src / "overlay.dsf")  # fmt: skip
    assert not shared.exists()
    assert (pack / LEFT_OVERLAY).read_bytes() == b"XPLNEDSF overlay, built again"

    # AutoOrtho switched off: nobody draws the square's roads any more
    ini = cs / "scenery_packs.ini"
    ini.write_text(
        ini.read_text().replace(AO_LINE, AO_LINE.replace("SCENERY_PACK ", "SCENERY_PACK_DISABLED "))
    )
    assert _states(cs) == {"+43+005": ("missing", ())}

    monkeypatch.setattr(install_packs, "xplane_running", lambda: True)
    with pytest.raises(OsxpError) as info:
        take_back_overlay(pack, cs, tile=TILE, library_path=default_library_path())
    assert info.value.code == "XP_RUNNING" and (pack / LEFT_OVERLAY).is_file()
    monkeypatch.setattr(install_packs, "xplane_running", lambda: False)

    take_back_overlay(pack, cs, tile=TILE, library_path=default_library_path())
    assert (
        shared.read_bytes() == b"XPLNEDSF overlay, built again"
        and not (pack / LEFT_OVERLAY).exists()
    )
    assert _states(cs) == {"+43+005": ("own", ())}
    assert "SCENERY_PACK Custom Scenery/yOrthoStudio_Overlays/" in ini.read_text()

    # a tile built without overlay (simHeaven X-World) keeps no overlay anywhere
    leave_overlay(pack, TILE)
    write_pack(
        home / "tiles", TILE, dsf_dir=src / "dsf", textures_dir=src / "tex", overlay_file=None
    )
    assert not (pack / LEFT_OVERLAY).exists() and not shared.exists()


def test_the_overlays_of_ortho4xp_and_xpme_count_and_an_inactive_pack_does_not(
    home: Path, xplane: Path
) -> None:
    cs = xplane / "Custom Scenery"
    pack = _osxp_pack(home, "+43+005")
    install_receipt(pack, cs, tile=TILE, library_path=default_library_path())
    for name in ("yOrtho4XP_Overlays", "XPME_Overlays", "simHeaven_X-World_Europe-7-forests"):
        dsf = cs / name / TILE.dsf_relpath
        dsf.parent.mkdir(parents=True)
        dsf.write_bytes(b"XPLNEDSF")
    ini = cs / "scenery_packs.ini"
    ini.write_text(ini.read_text() + "SCENERY_PACK Custom Scenery/XPME_Overlays/\n"
                   "SCENERY_PACK_DISABLED Custom Scenery/yOrtho4XP_Overlays/\n"
                   "SCENERY_PACK Custom Scenery/simHeaven_X-World_Europe-7-forests/\n")  # fmt: skip
    assert _states(cs) == {"+43+005": ("double", ("XPME_Overlays",))}
    ini.write_text(
        ini.read_text().replace(
            "SCENERY_PACK_DISABLED Custom Scenery/yOrtho4XP",
            "SCENERY_PACK Custom Scenery/yOrtho4XP",
        )
    )
    assert _states(cs)["+43+005"][1] == ("XPME_Overlays", "yOrtho4XP_Overlays")
    # a tile taken out of X-Plane has no state
    uninstall_receipt("zOrthoStudio_+43+005", cs)
    assert _states(cs) == {}


@pytest.mark.anyio
async def test_the_library_says_the_roads_come_twice_and_leaves_them_in_one_click(
    home: Path, xplane: Path
) -> None:
    cs = xplane / "Custom Scenery"
    pack = _osxp_pack(home, "+43+005")
    install_receipt(pack, cs, tile=TILE, library_path=default_library_path())
    _autoortho_overlay(cs)
    mgr = JobManager(jobs_dir=home / "jobs", build=FakeBuild(delay_s=0.2), env_factory=None)
    app = create_app(
        env_factory=None, jobs=mgr, airports=FakeIndex(), settings_path=home / "config.toml"
    )
    body = {"use": "others", "tiles": ["+43+005"], "xplane_dir": str(xplane)}
    try:
        async with client_for(app) as c:
            row = next(r for r in (await c.get("/api/library")).json() if r["kind"] == "ortho")
            assert row["overlay"] == {"state": "double", "others": ["yAutoOrtho_Overlays"]}

            job = mgr.start([make_spec("+43+005", home=home)])
            r = await c.post("/api/library/overlays", json=body)
            assert r.status_code == 409 and r.json()["error"]["code"] == "SYS_TILE_IN_BUILD"
            mgr.cancel(job.id)
            assert job.wait(30)

            r = await c.post("/api/library/overlays", json=body)
            assert r.status_code == 200, r.text
            left = {"state": "left", "others": ["yAutoOrtho_Overlays"]}
            assert r.json() == {"changed": ["+43+005"], "states": {"+43+005": left}}
            row = next(r for r in (await c.get("/api/library")).json() if r["kind"] == "ortho")
            assert row["overlay"]["state"] == "left"
            r = await c.post("/api/library/overlays", json={**body, "use": "own"})
            assert r.status_code == 200 and r.json()["changed"] == ["+43+005"]
            assert r.json()["states"]["+43+005"]["state"] == "double"
            for bad in (
                {**body, "tiles": ["Geneva"]},
                {**body, "use": "both"},
                {**body, "tiles": []},
            ):
                r = await c.post("/api/library/overlays", json=bad)
                assert r.status_code == 422, bad
    finally:
        mgr.close()
