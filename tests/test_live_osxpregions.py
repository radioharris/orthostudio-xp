# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP's regions installed by Live (orthostudio.live.osxpregions), from a host that
answers as the real one does: Live's token asked for, a Range honoured."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from orthostudio.live import aoregions, osxpregions
from orthostudio.live.osxpregions import Installer, OsxpRegion, Zip

URL = "https://live.example"
TOKEN = "t0ken"


def zip_of(top: str, files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(f"{top}/{name}", data)
    return out.getvalue()


def entry(path: str, data: bytes) -> Zip:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        unpacked = sum(i.file_size for i in zf.infolist())
    return Zip(path, len(data), hashlib.sha256(data).hexdigest(), unpacked)


class Host:
    """The server: the files, Live's token, Range; ``cut`` drops the first
    answer of a file after that many bytes, as a line that goes down; ``failing`` answers that many
    first asks of the zips with a 500, a fault of its own."""

    def __init__(
        self,
        files: dict[str, bytes],
        cut: int | None = None,
        slow: dict[str, float] | None = None,
        failing: int = 0,
    ) -> None:
        self.files = files
        self.cut = cut
        self.slow = slow or {}  # a file answered that many seconds late
        self.failing = failing
        self.asked: list[tuple[str, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        self.asked.append((key, request.headers.get("Range")))
        time.sleep(self.slow.get(key, 0.0))
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        if self.failing and "/zips/" in key:
            self.failing -= 1
            return httpx.Response(500)
        data = self.files.get(key)
        if data is None:
            return httpx.Response(404)
        start = 0
        if rng := request.headers.get("Range"):
            start = int(rng.removeprefix("bytes=").split("-")[0])
        body = data[start:]
        if self.cut is not None and start == 0:
            self.cut, cut = None, self.cut

            def dropped():
                yield body[:cut]
                raise httpx.ReadError("the line went down")

            return httpx.Response(206 if start else 200, content=dropped())
        return httpx.Response(206 if start else 200, content=body)


@pytest.fixture
def world():
    mesh_a = zip_of(
        "z_osxp_eur_+45+005",
        {"Earth nav data/+40+000/+45+005.dsf": b"dsf a" * 400, "terrain/a.ter": b"ter a"},
    )
    mesh_b = zip_of(
        "z_osxp_eur_+46+005",
        {"Earth nav data/+40+000/+46+005.dsf": b"dsf b" * 400, "terrain/b.ter": b"ter b"},
    )
    ovl_a = zip_of("y_osxp_overlays", {"Earth nav data/+40+000/+45+005.dsf": b"roads a"})
    files = {
        "eur/zips/z_osxp_eur_+45+005.zip": mesh_a,
        "eur/zips/z_osxp_eur_+46+005.zip": mesh_b,
        "eur/overlay-zips/y_osxp_eur_+45+005.zip": ovl_a,
    }
    region = OsxpRegion(
        "eur",
        "2026-10-18",
        tuple(entry(k, v) for k, v in files.items() if "/zips/" in k),
        tuple(entry(k, v) for k, v in files.items() if "/overlay-zips/" in k),
    )
    return files, region


def install(root: Path, region: OsxpRegion, host: Host, **kw) -> tuple[Installer, list[str]]:
    lines: list[str] = []
    client = httpx.Client(
        transport=httpx.MockTransport(host), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    inst = Installer(root, region, client=client, host=(URL, TOKEN), log=lines.append, **kw)
    inst.run()
    return inst, lines


def test_a_region_installed_whole(tmp_path: Path, world, monkeypatch) -> None:
    monkeypatch.setattr(osxpregions, "_pause", lambda attempt: None)
    files, region = world
    host = Host(files, cut=100)  # the first file's line goes down: resumed where it stopped
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert inst.progress.done_bytes == inst.progress.total_bytes == region.size
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"dsf a" * 400
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"
    pack = osxpregions.overlay_pack(tmp_path)
    assert (pack / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"roads a"
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert marker["zips"] == {z.path: z.sha256 for z in region.mesh}
    assert marker["size"] == region.unpacked
    assert not (folder / osxpregions.STEP).exists()
    assert not osxpregions.stage_dir(tmp_path, "eur").exists()  # made whole there, then moved
    assert not (tmp_path / "regions" / ".download" / "osxp_eur").exists()  # the zips deleted
    assert any(r is not None and r.startswith("bytes=") for _, r in host.asked)  # resumed
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])


def test_a_fault_of_the_host_is_tried_again_as_a_cut_line_is(
    tmp_path: Path, world, monkeypatch
) -> None:
    """A 500 from the host no longer stops an install: tried again, as a cut line is; one that
    lasts stops it, said by its status alone."""
    monkeypatch.setattr(osxpregions, "_pause", lambda attempt: None)
    files, region = world
    inst, lines = install(tmp_path / "once", region, Host(files, failing=1))
    assert inst.progress.phase == "done", lines
    inst, lines = install(tmp_path / "lasting", region, Host(files, failing=99))
    assert inst.progress.phase == "failed"
    assert inst.progress.error is not None
    assert inst.progress.error.endswith(": HTTP 500")
    assert URL not in inst.progress.error


def mended(files: dict, region: OsxpRegion, which: int, content: dict[str, bytes]):
    """The world with its zip ``which`` made again with ``content``: (files, region)."""
    path = region.mesh[which].path
    data = zip_of(path.rsplit("/", 1)[-1].removesuffix(".zip"), content)
    mesh = list(region.mesh)
    mesh[which] = entry(path, data)
    return {**files, path: data}, dataclasses.replace(region, mesh=tuple(mesh))


def test_a_mended_zip_brings_the_region_whole_and_nothing_of_before_stays(
    tmp_path: Path, world
) -> None:
    """One zip of the mesh made again: the region is downloaded whole into its stage and takes
    the place of the one of before, so that no file of the mesh of before stays behind (Live's
    light world, 2026-11-16: every bake makes every zip again). Its night grids come again with
    it."""
    files, region = world
    night = zip_of("night_eur", {"night/+45+005.png": b"grid a"})
    files = {**files, "eur/night-zips/night_eur.zip": night}
    region = dataclasses.replace(region, night=(entry("eur/night-zips/night_eur.zip", night),))
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"
    files, region = mended(
        files,
        region,
        1,
        {"Earth nav data/+40+000/+46+005.dsf": b"dsf B" * 400, "terrain/b2.ter": b"ter B"},
    )
    assert osxpregions.made_whole(tmp_path, region)
    mesh, _ = osxpregions.to_fetch(tmp_path, region, True)
    assert mesh == [*region.mesh, *region.night]
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert sorted(k for k, _ in host.asked) == sorted(z.path for z in (*region.mesh, *region.night))
    assert (folder / "terrain/b2.ter").read_bytes() == b"ter B"
    assert not (folder / "terrain/b.ter").exists()  # the zip of before's file, gone
    assert (folder / "terrain/a.ter").read_bytes() == b"ter a"
    assert (folder / "night/+45+005.png").read_bytes() == b"grid a"
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert marker["zips"] == {z.path: z.sha256 for z in (*region.mesh, *region.night)}
    assert marker["size"] == region.unpacked
    left = sorted(p.name for p in folder.parent.iterdir() if p.name.startswith("."))
    assert left == [".download", ".lists"]  # no stage, no region of before
    assert not (folder.parent / ".download" / "osxp_eur").exists()  # nor its zips
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])


def test_without_overlays_and_a_damaged_file(tmp_path: Path, world) -> None:
    files, region = world
    damaged = {**files, "eur/zips/z_osxp_eur_+46+005.zip": b"x" * region.mesh[1].size}
    inst, lines = install(tmp_path, region, Host(damaged), overlays=False)
    assert inst.progress.phase == "failed" and "damaged" in inst.progress.error
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert not folder.exists()  # not used: not whole, in its stage
    assert osxpregions.to_fetch(tmp_path, region, False) == ([region.mesh[1]], [])  # the rest kept
    inst, lines = install(tmp_path, region, Host(files), overlays=False)
    assert inst.progress.phase == "done", lines
    assert not (osxpregions.overlay_pack(tmp_path) / "Earth nav data").exists()


def test_a_key_refused_and_a_region_deleted(tmp_path: Path, world) -> None:
    files, region = world
    lines: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(Host(files)))
    inst = Installer(tmp_path, region, client=client, host=(URL, "old"), log=lines.append)
    inst.run()
    assert inst.progress.phase == "failed" and "key" in inst.progress.error
    install(tmp_path, region, Host(files))
    stage = osxpregions.stage_dir(tmp_path, "eur")  # an update stopped halfway
    stage.mkdir()
    (stage / "x.dsf").write_bytes(b"half")
    osxpregions.remove(tmp_path, "eur", [(45, 5), (46, 5)], log=lines.append)
    assert not osxpregions.mesh_dir(tmp_path, "eur").exists()
    assert not stage.exists()
    pack = osxpregions.overlay_pack(tmp_path)
    assert not (pack / "Earth nav data/+40+000/+45+005.dsf").exists()
    assert json.loads((pack / osxpregions.OVERLAY_RECORD).read_text()) == {}


def test_a_zip_that_would_write_outside(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(zip_of("z_osxp_eur_+45+005", {"../../escape.txt": b"no"}))
    with pytest.raises(osxpregions.InstallError):
        osxpregions.unpack(bad, tmp_path / "region")
    assert not (tmp_path / "escape.txt").exists()


def test_the_pace_is_the_last_seconds_bytes_and_slows_down_when_nothing_comes(
    tmp_path: Path, world
) -> None:
    """The panel's MB/s: the bytes of the last five seconds, sampled at most twice a second; a
    download that stalls slows down at each look of the page (2026-10-26)."""
    _, region = world
    inst = Installer(tmp_path, region, host=(URL, TOKEN), log=lambda _: None)
    now = [100.0]
    inst.clock = lambda: now[0]
    for _ in range(20):  # 10 MB a second: 1 MB every tenth of a second
        inst._add(1_000_000)
        now[0] += 0.1
    assert inst.snapshot()["mbs"] == pytest.approx(10.0, rel=0.1)
    assert len(inst.rate.samples) <= 5  # two samples a second, not one a chunk
    now[0] += 10.0  # nothing for ten seconds
    assert inst.snapshot()["mbs"] < 1.0
    now[0] += 6.0  # a whole window more
    assert inst.snapshot()["mbs"] == 0.0


def test_what_an_earlier_try_downloaded_is_not_in_the_pace(tmp_path: Path, world) -> None:
    """A resumed install counts the bytes an earlier try left whole, but not as speed."""
    files, region = world
    first = region.mesh[0]
    download = tmp_path / "regions" / ".download" / "osxp_eur"
    download.mkdir(parents=True)
    had = len(files[first.path]) - 10
    (download / first.name).write_bytes(files[first.path][:had])
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 1.0
        return ticks[0]

    host = Host(files)
    client = httpx.Client(
        transport=httpx.MockTransport(host), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    inst = Installer(tmp_path, region, client=client, host=(URL, TOKEN), log=lambda _: None)
    inst.clock = clock
    inst.run()
    assert inst.progress.phase == "done"
    assert inst.progress.done_bytes == region.size
    assert inst.progress.mbs * 1e6 <= region.size - had  # a second at least between two samples


def test_a_mask_two_zips_hold_is_the_later_zip_s_whatever_comes_first(tmp_path: Path) -> None:
    """A photo across two squares' border: both zips hold its mask, a little different. Unpacked
    as they arrived, the one that came last won, so two computers could differ; unpacked in the
    list's order, it is the later zip's everywhere (2026-10-19)."""
    mask = "textures/19024_33744_ZL16.png"
    first = zip_of("z_osxp_eur_+59+005", {"terrain/a.ter": b"a", mask: b"mask of the first"})
    second = zip_of("z_osxp_eur_+60+005", {"terrain/b.ter": b"b", mask: b"mask of the second"})
    files = {"eur/zips/z_osxp_eur_+59+005.zip": first, "eur/zips/z_osxp_eur_+60+005.zip": second}
    region = OsxpRegion("eur", "2026-10-19", tuple(entry(k, v) for k, v in files.items()))
    # the first zip of the list comes last
    host = Host(files, slow={"eur/zips/z_osxp_eur_+59+005.zip": 0.5})
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / mask).read_bytes() == b"mask of the second"


def test_an_update_stopped_halfway_leaves_the_region_of_before_whole(tmp_path: Path, world) -> None:
    """Both zips made again, the second one damaged on the way: the region of before is untouched
    and still the one Live uses; the next install goes on in the stage with the second zip alone."""
    files, region = world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    before = (folder / aoregions.MARKER).read_text()
    files, region = mended(files, region, 0, {"terrain/a.ter": b"ter A"})
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    damaged = {**files, region.mesh[1].path: b"x" * region.mesh[1].size}
    inst, lines = install(tmp_path, region, Host(damaged))
    assert inst.progress.phase == "failed" and "damaged" in inst.progress.error
    assert (folder / aoregions.MARKER).read_text() == before
    assert (folder / "terrain/a.ter").read_bytes() == b"ter a"  # the region of before, whole
    assert (folder / "Earth nav data/+40+000/+46+005.dsf").exists()
    assert osxpregions.to_fetch(tmp_path, region, True) == ([region.mesh[1]], [])
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert [k for k, _ in host.asked] == [region.mesh[1].path]
    assert (folder / "terrain/a.ter").read_bytes() == b"ter A"
    assert (folder / "terrain/b.ter").read_bytes() == b"ter B"
    assert not (folder / "Earth nav data").exists()  # the mesh of before's DSFs, gone


def test_a_stage_holding_a_zip_made_again_since_is_started_again(tmp_path: Path, world) -> None:
    """An install stopped with the first zip in its stage; the first zip made again on the server
    before the next try: the stage is started again, so that a file only the zip of before held
    does not end in the region."""
    files, region = world
    damaged = {**files, region.mesh[1].path: b"x" * region.mesh[1].size}
    inst, _ = install(tmp_path, region, Host(damaged))
    assert inst.progress.phase == "failed"
    stage = osxpregions.stage_dir(tmp_path, "eur")
    assert (stage / "terrain/a.ter").exists()
    files, region = mended(files, region, 0, {"terrain/a2.ter": b"ter A"})
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "terrain/a2.ter").read_bytes() == b"ter A"
    assert not (folder / "terrain/a.ter").exists()
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"


def test_a_region_in_use_waits_for_the_next_install_to_take_its_place(
    tmp_path: Path, world, monkeypatch
) -> None:
    """Windows does not rename a folder X-Plane holds open: the region of before stays as it was
    and in use, the new one whole in its stage; the next install downloads nothing and only puts
    it in place."""
    files, region = world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    rename = os.rename

    def held(src, dst):
        if Path(src) == folder:
            raise PermissionError(32, "in use by another process")
        rename(src, dst)

    monkeypatch.setattr(osxpregions.os, "rename", held)
    inst, lines = install(tmp_path, region, Host(files))
    assert inst.progress.phase == "failed" and "in use" in inst.progress.error
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"  # the region of before, in use
    assert (folder / aoregions.MARKER).exists()
    monkeypatch.setattr(osxpregions.os, "rename", rename)
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert host.asked == []
    assert (folder / "terrain/b.ter").read_bytes() == b"ter B"
    assert not osxpregions.stage_dir(tmp_path, "eur").exists()
    assert not (folder / osxpregions.STEP).exists()


def test_a_region_on_another_disk_is_made_whole_beside_it(tmp_path: Path, world) -> None:
    """A region linked from Live's folder to a folder on another disk: made whole beside that
    folder, on its disk, and put in its place there; the link still leads to it."""
    files, region = world
    elsewhere = tmp_path / "other disk" / "z_osxp_eur"
    elsewhere.parent.mkdir()
    root = tmp_path / "live"
    install(root, region, Host(files))
    folder = osxpregions.mesh_dir(root, "eur")
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    assert osxpregions.stage_dir(root, "eur") == elsewhere.with_name(".z_osxp_eur.new")
    files, region = mended(files, region, 1, {"terrain/b2.ter": b"ter B"})
    inst, lines = install(root, region, Host(files))
    assert inst.progress.phase == "done", lines
    assert folder.is_symlink() and folder.resolve() == elsewhere.resolve()
    assert (elsewhere / "terrain/b2.ter").read_bytes() == b"ter B"
    assert not (elsewhere / "terrain/b.ter").exists()
    assert sorted(p.name for p in elsewhere.parent.iterdir()) == ["z_osxp_eur"]


def test_a_region_s_night_grids_come_with_it_and_alone_later(tmp_path: Path, world) -> None:
    """The night lights' density grids (orthostudio.live.nightwork) are a zip of their own in the
    manifest (``night``): unpacked into the region's folder as its mesh, and, added to a region
    installed before, the only thing an update downloads."""
    files, region = world
    install(tmp_path, region, Host(files))
    night = zip_of("night_eur", {"night/+45+005.png": b"grid a", "night/+46+005.png": b"grid b"})
    files = {**files, "eur/night-zips/night_eur.zip": night}
    doc = {
        "made": "2026-11-10",
        "regions": {
            "eur": {
                "mesh": [dataclasses.asdict(z) for z in region.mesh],
                "overlays": [dataclasses.asdict(z) for z in region.overlays],
                "night": [dataclasses.asdict(entry("eur/night-zips/night_eur.zip", night))],
            }
        },
    }
    later = osxpregions.parse_manifest(doc)["eur"]
    mesh, ovl = osxpregions.to_fetch(tmp_path, later, True)
    assert [z.path for z in mesh] == ["eur/night-zips/night_eur.zip"] and ovl == []
    assert later.mesh_size == region.mesh_size + len(night)
    host = Host(files)
    inst, lines = install(tmp_path, later, host)
    assert inst.progress.phase == "done", lines
    assert [k for k, _ in host.asked] == ["eur/night-zips/night_eur.zip"]
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "night/+45+005.png").read_bytes() == b"grid a"
    assert osxpregions.to_fetch(tmp_path, later, True) == ([], [])


def test_the_night_grids_alone_leave_the_region_as_it_was(tmp_path: Path, world) -> None:
    """Live fetches the night grids of a region installed before them by itself
    (orthostudio.live.control): only their zip, the region's mesh, its step and the dates the
    set-up compares untouched, so that the next start does not set the region up again."""
    files, region = world
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert osxpregions.night_to_fetch(tmp_path, region) == []  # not installed: nothing
    install(tmp_path, region, Host(files))
    night = zip_of("night_eur", {"night/+45+005.png": b"grid a"})
    files = {**files, "eur/night-zips/night_eur.zip": night}
    later = dataclasses.replace(region, night=(entry("eur/night-zips/night_eur.zip", night),))
    assert [z.path for z in osxpregions.night_to_fetch(tmp_path, later)] == [
        "eur/night-zips/night_eur.zip"
    ]
    nav = folder / "Earth nav data"
    os.utime(nav, (1_000_000, 1_000_000))
    before = json.loads((folder / osxpregions.MARKER).read_text())
    # an update the pilot stopped: what it downloaded is kept to go on from it
    stopped = (
        osxpregions.regions_dir(tmp_path) / ".download" / "osxp_eur" / "z_osxp_eur_+46+005.zip"
    )
    stopped.parent.mkdir(parents=True)
    stopped.write_bytes(b"half a zip")
    host = Host(files)
    inst, lines = install(tmp_path, later, host, overlays=False, night_only=True)
    assert inst.progress.phase == "done", lines
    assert stopped.read_bytes() == b"half a zip"
    assert [k for k, _ in host.asked] == ["eur/night-zips/night_eur.zip"]
    assert (folder / "night/+45+005.png").read_bytes() == b"grid a"
    marker = json.loads((folder / osxpregions.MARKER).read_text())
    assert marker["zips"] == {
        **before["zips"],
        "eur/night-zips/night_eur.zip": later.night[0].sha256,
    }
    assert marker["size"] == before["size"] + later.night[0].unpacked
    assert marker["installed"] == before["installed"]
    assert nav.stat().st_mtime == 1_000_000  # the set-up not made again
    assert not (folder / osxpregions.STEP).exists()
    assert osxpregions.night_to_fetch(tmp_path, later) == []
    assert osxpregions.to_fetch(tmp_path, later, True) == ([], [])
    assert any("night lights' grids" in ln for ln in lines)
