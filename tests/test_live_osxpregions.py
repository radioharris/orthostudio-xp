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


def total(region: OsxpRegion, kind: str = "size", rivers: bool = False) -> int:
    """A whole install's bytes, downloaded (``size``) or unpacked: what the region wants in its
    folder from scratch (osxpregions.wanted), and its overlays for the download."""
    zips = (*region.mesh, *region.night, *(region.rivers if rivers else ()))
    if kind == "size":
        zips = (*zips, *region.overlays)
    return sum(getattr(z, kind) for z in zips)


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
    assert inst.progress.done_bytes == inst.progress.total_bytes == total(region)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"dsf a" * 400
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"
    pack = osxpregions.overlay_pack(tmp_path)
    assert (pack / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"roads a"
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert marker["zips"] == {z.path: z.sha256 for z in region.mesh}
    assert marker["size"] == total(region, "unpacked")
    assert not (folder / osxpregions.STEP).exists()
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


def test_only_the_zips_mended_come_again(tmp_path: Path, world) -> None:
    files, region = world
    install(tmp_path, region, Host(files))
    mended = zip_of(
        "z_osxp_eur_+46+005",
        {"Earth nav data/+40+000/+46+005.dsf": b"dsf B" * 400, "terrain/b.ter": b"ter B"},
    )
    files = {**files, "eur/zips/z_osxp_eur_+46+005.zip": mended}
    region = dataclasses.replace(
        region, mesh=(region.mesh[0], entry("eur/zips/z_osxp_eur_+46+005.zip", mended))
    )
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert [k for k, _ in host.asked] == ["eur/zips/z_osxp_eur_+46+005.zip"]
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "terrain/b.ter").read_bytes() == b"ter B"
    assert (folder / "terrain/a.ter").read_bytes() == b"ter a"  # the other zip's, kept


def test_without_overlays_and_a_damaged_file(tmp_path: Path, world) -> None:
    files, region = world
    damaged = {**files, "eur/zips/z_osxp_eur_+46+005.zip": b"x" * region.mesh[1].size}
    inst, lines = install(tmp_path, region, Host(damaged), overlays=False)
    assert inst.progress.phase == "failed" and "damaged" in inst.progress.error
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert not (folder / aoregions.MARKER).exists()  # not used: not whole
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
    osxpregions.remove(tmp_path, "eur", [(45, 5), (46, 5)], log=lines.append)
    assert not osxpregions.mesh_dir(tmp_path, "eur").exists()
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
    (download / first.local).parent.mkdir(parents=True)
    (download / first.local).write_bytes(files[first.path][:had])
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
    assert inst.progress.done_bytes == total(region)
    assert inst.progress.mbs * 1e6 <= total(region) - had  # a second at least between two samples


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


def test_a_region_brought_to_zips_that_name_their_terrain(tmp_path: Path, world) -> None:
    """The terrain folder of before goes once every zip lists its files, not while one zip of
    before is left (2026-10-19)."""
    files, region = world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")

    def listing(stem: str, ter: str) -> bytes:
        return zip_of(
            stem,
            {
                "Earth nav data/+40+000/x.dsf": b"7z" * 300,
                f"terrain-lists/{stem}.txt": ter.encode(),
            },
        )

    new_a = listing("z_osxp_eur_+45+005", "a.ter\n")
    files = {**files, "eur/zips/z_osxp_eur_+45+005.zip": new_a}
    region = dataclasses.replace(
        region, mesh=(entry("eur/zips/z_osxp_eur_+45+005.zip", new_a), region.mesh[1])
    )
    inst, lines = install(tmp_path, region, Host(files))
    assert inst.progress.phase == "done", lines
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"  # the other zip still of before
    new_b = listing("z_osxp_eur_+46+005", "b.ter\n")
    files = {**files, "eur/zips/z_osxp_eur_+46+005.zip": new_b}
    region = dataclasses.replace(
        region, mesh=(region.mesh[0], entry("eur/zips/z_osxp_eur_+46+005.zip", new_b))
    )
    inst, lines = install(tmp_path, region, Host(files))
    assert inst.progress.phase == "done", lines
    assert not (folder / "terrain").exists() and not (folder / ".terrain-old").exists()
    assert sorted(p.name for p in (folder / "terrain-lists").iterdir()) == [
        "z_osxp_eur_+45+005.txt",
        "z_osxp_eur_+46+005.txt",
    ]


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
    assert osxpregions.wanted(tmp_path, later, False) == (*region.mesh, *later.night)
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


# --- the river masks (2026-11-17) ---------------------------------------------------------------

MASK_A, MASK_B = "rivers/textures/23296_33152_ZL16.png", "rivers/textures/23280_33152_ZL16.png"


def river_zip(stem: str, masks: dict[str, bytes]) -> bytes:
    """A river zip as the bake packs it: beside its mesh zip, the same top folder, its masks in
    rivers/textures."""
    return zip_of(stem, masks)


@pytest.fixture
def rivers_world(world):
    """The world's two mesh zips, each with its river zip beside it (``rivers`` in the list)."""
    files, region = world
    rivers = {
        "eur/zips/rivers/z_osxp_eur_+45+005.zip": river_zip(
            "z_osxp_eur_+45+005", {MASK_A: b"river a"}
        ),
        "eur/zips/rivers/z_osxp_eur_+46+005.zip": river_zip(
            "z_osxp_eur_+46+005", {MASK_B: b"river b"}
        ),
    }
    region = dataclasses.replace(region, rivers=tuple(entry(k, v) for k, v in rivers.items()))
    return {**files, **rivers}, region


def asked(host: Host) -> list[str]:
    """The files asked of the host, once each, in the list's order whatever order they came in."""
    return sorted({key for key, _ in host.asked})


def mended_rivers(files: dict, region: OsxpRegion, i: int, masks: dict[str, bytes]):
    """River zip ``i`` made again with ``masks``: the files and the region the list then gives."""
    old = region.rivers[i]
    data = river_zip(old.name.removesuffix(".zip"), masks)
    rivers = list(region.rivers)
    rivers[i] = entry(old.path, data)
    return {**files, old.path: data}, dataclasses.replace(region, rivers=tuple(rivers))


def mended_mesh(files: dict, region: OsxpRegion, i: int, extra: dict[str, bytes]):
    """Mesh zip ``i`` made again, ``extra`` files added to it."""
    old = region.mesh[i]
    with zipfile.ZipFile(io.BytesIO(files[old.path])) as zf:
        top = zf.namelist()[0].split("/")[0]
        members = {n.split("/", 1)[1]: zf.read(n) for n in zf.namelist()}
    data = zip_of(top, {**members, **extra})
    mesh = list(region.mesh)
    mesh[i] = entry(old.path, data)
    return {**files, old.path: data}, dataclasses.replace(region, mesh=tuple(mesh))


def test_the_zips_a_region_wants_follow_the_setting_and_what_it_has(
    tmp_path: Path, rivers_world
) -> None:
    """Its mesh and night grids always; its river masks with soft banks, or once it has them,
    whatever the setting: one rule for the install, the sizes and the update counts."""
    files, region = rivers_world
    assert osxpregions.wanted(tmp_path, region, False) == region.mesh
    assert osxpregions.wanted(tmp_path, region, True) == (*region.mesh, *region.rivers)
    install(tmp_path, region, Host(files), rivers=True)
    assert osxpregions.has_rivers(tmp_path, "eur")
    assert osxpregions.wanted(tmp_path, region, False) == (*region.mesh, *region.rivers)


def test_a_first_install_brings_the_river_masks_only_with_soft_banks(
    tmp_path: Path, rivers_world
) -> None:
    files, region = rivers_world
    host = Host(files)
    inst, lines = install(tmp_path / "even", region, host)
    assert inst.progress.phase == "done", lines
    assert not any("/rivers/" in key for key in asked(host))
    folder = osxpregions.mesh_dir(tmp_path / "even", "eur")
    assert not (folder / "rivers").exists() and not (folder / osxpregions.RIVERS_STAGE).exists()
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert set(marker["zips"]) == {z.path for z in region.mesh}
    host = Host(files)
    inst, lines = install(tmp_path / "soft", region, host, rivers=True)
    assert inst.progress.phase == "done", lines
    assert inst.progress.total_bytes == total(region, rivers=True)
    folder = osxpregions.mesh_dir(tmp_path / "soft", "eur")
    assert (folder / MASK_A).read_bytes() == b"river a"
    assert (folder / MASK_B).read_bytes() == b"river b"
    assert not (folder / osxpregions.RIVERS_STAGE).exists()
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert marker["zips"] == {z.path: z.sha256 for z in (*region.mesh, *region.rivers)}
    assert marker["size"] == total(region, "unpacked", rivers=True)  # the region counts them
    assert osxpregions.to_fetch(tmp_path / "soft", region, True, rivers=True) == ([], [])
    assert any("1 river zips" in ln or "2 river zips" in ln for ln in lines)


def test_soft_banks_chosen_later_brings_the_river_masks_alone(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """The region's mesh the list's: its river zips alone, unpacked beside, put in place whole,
    then recorded in its marker, last; its mesh, its set-up's dates and its list untouched."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert osxpregions.rivers_to_add(tmp_path, region) == list(region.rivers)
    nav = folder / "Earth nav data"
    os.utime(nav, (1_000_000, 1_000_000))
    before = json.loads((folder / aoregions.MARKER).read_text())
    writing = osxpregions._write

    def write(path: Path, doc: dict) -> None:
        if path.name == aoregions.MARKER:  # the marker: the masks whole in place already
            assert (folder / MASK_A).exists() and (folder / MASK_B).exists()
            assert not (folder / osxpregions.RIVERS_STAGE / "rivers").exists()
        writing(path, doc)

    monkeypatch.setattr(osxpregions, "_write", write)
    host = Host(files)
    inst, lines = install(tmp_path, region, host, overlays=False, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted(z.path for z in region.rivers)
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert marker["zips"] == {**before["zips"], **{z.path: z.sha256 for z in region.rivers}}
    assert marker["installed"] == before["installed"]
    assert marker["size"] == before["size"] + sum(z.unpacked for z in region.rivers)
    assert nav.stat().st_mtime == 1_000_000  # the mesh as it was: no set-up for it
    assert not (folder / osxpregions.STEP).exists()
    assert not (folder / osxpregions.RIVERS_STAGE).exists()
    assert osxpregions.rivers_to_add(tmp_path, region) == []
    assert any("river masks installed" in ln for ln in lines)


def test_soft_banks_with_a_changed_mesh_waits_for_the_update(tmp_path: Path, rivers_world) -> None:
    """A region whose mesh changed since: nothing comes alone; its update brings it whole, its
    river masks in."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    files, region = mended_mesh(files, region, 1, {"terrain/b2.ter": b"ter B"})
    assert osxpregions.rivers_to_add(tmp_path, region) == []
    missing = osxpregions.not_installed(tmp_path, region, True)
    assert missing == [region.mesh[1], *region.rivers]
    host = Host(files)
    inst, lines = install(tmp_path, region, host, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted([region.mesh[1].path, *(z.path for z in region.rivers)])
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "terrain/b2.ter").read_bytes() == b"ter B"
    assert (folder / MASK_A).exists() and (folder / MASK_B).exists()
    assert osxpregions.not_installed(tmp_path, region, True) == []


def test_even_again_keeps_the_river_masks_and_soft_banks_again_downloads_nothing(
    tmp_path: Path, rivers_world
) -> None:
    """Once there they are part of the region: Even leaves them on the disk, recorded, unused;
    Soft banks chosen again finds them and downloads nothing."""
    files, region = rivers_world
    install(tmp_path, region, Host(files), rivers=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    marker = (folder / aoregions.MARKER).read_text()
    for rivers in (False, True):  # even, then soft banks again
        assert osxpregions.to_fetch(tmp_path, region, True, rivers) == ([], [])
        assert osxpregions.not_installed(tmp_path, region, rivers) == []
        assert osxpregions.rivers_to_add(tmp_path, region) == []
        host = Host(files)
        inst, lines = install(tmp_path, region, host, rivers=rivers)
        assert inst.progress.phase == "done" and host.asked == [], lines
        assert (folder / MASK_A).read_bytes() == b"river a"
        assert (folder / aoregions.MARKER).read_text() == marker


def test_an_update_brings_the_river_masks_installed_whatever_the_setting(
    tmp_path: Path, rivers_world
) -> None:
    """Even chosen since, the region's update brings their new version with it, whole: a mask only
    those of before had is gone."""
    files, region = rivers_world
    install(tmp_path, region, Host(files), rivers=True)
    files, region = mended_mesh(files, region, 0, {"terrain/a2.ter": b"ter A"})
    files, region = mended_rivers(files, region, 0, {MASK_A: b"river A"})
    files, region = mended_rivers(files, region, 1, {"rivers/textures/9_9_ZL16.png": b"new"})
    changed = osxpregions.not_installed(tmp_path, region, False)
    assert changed == [region.mesh[0], *region.rivers]  # Update says them, the setting even
    host = Host(files)
    inst, lines = install(tmp_path, region, host, rivers=False)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted([region.mesh[0].path, *(z.path for z in region.rivers)])
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / MASK_A).read_bytes() == b"river A"
    assert (folder / "rivers/textures/9_9_ZL16.png").read_bytes() == b"new"
    assert not (folder / MASK_B).exists()  # only the zip of before had it
    assert not (folder / osxpregions.RIVERS_OLD).exists()
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert all(marker["zips"][z.path] == z.sha256 for z in region.rivers)


def test_a_stop_halfway_leaves_no_half_rivers(tmp_path: Path, rivers_world) -> None:
    """The second river zip damaged on the way: the region has no rivers folder yet, nothing
    recorded, the first one kept in the stage; the next try downloads the second alone."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    damaged = {**files, region.rivers[1].path: b"x" * region.rivers[1].size}
    inst, lines = install(tmp_path, region, Host(damaged), overlays=False, rivers=True)
    assert inst.progress.phase == "failed" and "damaged" in inst.progress.error
    assert not (folder / "rivers").exists()
    assert (folder / osxpregions.RIVERS_STAGE / MASK_A).exists()
    marker = json.loads((folder / aoregions.MARKER).read_text())
    assert not any(z.path in marker["zips"] for z in region.rivers)
    assert osxpregions.to_fetch(tmp_path, region, False, rivers=True) == ([region.rivers[1]], [])
    host = Host(files)
    inst, lines = install(tmp_path, region, host, overlays=False, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == [region.rivers[1].path]
    assert (folder / MASK_A).exists() and (folder / MASK_B).exists()


def test_a_stopped_update_leaves_the_river_masks_of_before_whole(
    tmp_path: Path, rivers_world
) -> None:
    files, region = rivers_world
    install(tmp_path, region, Host(files), rivers=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    files, region = mended_rivers(files, region, 0, {MASK_A: b"river A"})
    files, region = mended_rivers(files, region, 1, {MASK_B: b"river B"})
    damaged = {**files, region.rivers[1].path: b"x" * region.rivers[1].size}
    inst, _ = install(tmp_path, region, Host(damaged), rivers=True)
    assert inst.progress.phase == "failed"
    assert (folder / MASK_A).read_bytes() == b"river a"  # those of before, both, in use
    assert (folder / MASK_B).read_bytes() == b"river b"


def test_a_stage_holding_a_river_zip_made_again_since_is_started_again(
    tmp_path: Path, rivers_world
) -> None:
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    damaged = {**files, region.rivers[1].path: b"x" * region.rivers[1].size}
    install(tmp_path, region, Host(damaged), overlays=False, rivers=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / osxpregions.RIVERS_STAGE / MASK_A).exists()
    files, region = mended_rivers(files, region, 0, {"rivers/textures/7_7_ZL16.png": b"a2"})
    host = Host(files)
    inst, lines = install(tmp_path, region, host, overlays=False, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted(z.path for z in region.rivers)
    assert not (folder / MASK_A).exists()  # the zip of before's, not left among the new
    assert (folder / "rivers/textures/7_7_ZL16.png").exists()


def test_a_full_disk_stops_before_a_zip_and_counts_each_file_a_block(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """Each file takes a whole block at least: three masks of 1, 4096 and 4097 bytes need 16 KiB.
    A disk without room for a zip stops the install before it, the rivers as they were."""
    small = tmp_path / "small.zip"
    small.write_bytes(zip_of("z", {"a": b"x", "b": b"x" * 4096, "c": b"x" * 4097}))
    assert osxpregions.disk_need(small) == 4 * 4096
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    needing = osxpregions.disk_need
    full = region.rivers[1].local
    monkeypatch.setattr(
        osxpregions, "disk_need", lambda p: 10**18 if p.as_posix().endswith(full) else needing(p)
    )
    inst, _ = install(tmp_path, region, Host(files), overlays=False, rivers=True)
    assert inst.progress.phase == "failed" and "GB needed on the disk" in inst.progress.error
    assert full in inst.progress.error
    assert not (folder / "rivers").exists()


def test_river_masks_in_use_wait_for_the_next_try(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """Windows does not rename a folder a program holds open: those of before stay as they were,
    the new ones whole in their stage, the region showing its update; the next try downloads
    nothing and only puts them in place."""
    files, region = rivers_world
    install(tmp_path, region, Host(files), rivers=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    files, region = mended_rivers(files, region, 0, {MASK_A: b"river A"})
    files, region = mended_rivers(files, region, 1, {MASK_B: b"river B"})
    rename = osxpregions.replace_file

    def held(src, dst):
        if Path(src) == folder / "rivers":
            raise PermissionError(32, "in use by another process")
        rename(src, dst)

    monkeypatch.setattr(osxpregions, "replace_file", held)
    inst, lines = install(tmp_path, region, Host(files), rivers=True)
    assert inst.progress.phase == "failed" and "in use" in inst.progress.error
    assert "close it" in inst.progress.error
    assert (folder / MASK_A).read_bytes() == b"river a"
    assert osxpregions.to_fetch(tmp_path, region, True, rivers=True) == ([], [])
    assert osxpregions.not_installed(tmp_path, region, True) == list(region.rivers)
    monkeypatch.setattr(osxpregions, "replace_file", rename)
    host = Host(files)
    inst, lines = install(tmp_path, region, host, rivers=True)
    assert inst.progress.phase == "done" and host.asked == [], lines
    assert (folder / MASK_A).read_bytes() == b"river A"
    assert osxpregions.not_installed(tmp_path, region, True) == []
    assert not (folder / osxpregions.RIVERS_STAGE).exists()


def test_the_night_grids_alone_leave_the_river_stage_alone(tmp_path: Path, rivers_world) -> None:
    """The night grids' own download runs while Live streams: river masks whole in their stage
    wait for an install of the pilot's, never put in place by it."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    stage = folder / osxpregions.RIVERS_STAGE
    (stage / "rivers" / "textures").mkdir(parents=True)
    (stage / MASK_A).write_bytes(b"river a")
    (stage / MASK_B).write_bytes(b"river b")
    whole = {"zips": {z.path: z.sha256 for z in region.rivers}}
    osxpregions._write(stage / osxpregions.STEP, whole)
    night = zip_of("night_eur", {"night/+45+005.png": b"grid a"})
    files = {**files, "eur/night-zips/night_eur.zip": night}
    later = dataclasses.replace(region, night=(entry("eur/night-zips/night_eur.zip", night),))
    inst, lines = install(tmp_path, later, Host(files), overlays=False, night_only=True)
    assert inst.progress.phase == "done", lines
    assert not (folder / "rivers").exists() and (stage / MASK_A).exists()


def test_a_list_of_before_without_river_masks_changes_nothing(tmp_path: Path, world) -> None:
    """A list with no ``rivers``: no river zip, whatever the setting, the install as before."""
    files, region = world
    doc = {
        "made": "2026-11-10",
        "regions": {
            "eur": {
                "mesh": [dataclasses.asdict(z) for z in region.mesh],
                "overlays": [dataclasses.asdict(z) for z in region.overlays],
            }
        },
    }
    listed = osxpregions.parse_manifest(doc)["eur"]
    assert listed.rivers == ()
    assert osxpregions.wanted(tmp_path, listed, True) == region.mesh
    host = Host(files)
    inst, lines = install(tmp_path, listed, host, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted(files)
    assert not (osxpregions.mesh_dir(tmp_path, "eur") / "rivers").exists()
    assert osxpregions.rivers_to_add(tmp_path, listed) == []


def test_an_older_live_reads_the_new_list(rivers_world) -> None:
    """Every Live since 2.0 reads each kind it knows with ``Zip(**z)``, strict, and leaves a kind
    it does not know aside: the list with its rivers reads as before for them (their own reading
    copied from the tags v2.0.0 and v2.2.0), and with them for this one."""
    _, region = rivers_world
    doc = {
        "format": 1,
        "made": "2026-11-17",
        "regions": {
            "eur": {
                kind: [dataclasses.asdict(z) for z in getattr(region, kind)]
                for kind in ("mesh", "overlays", "night", "rivers")
            }
        },
    }
    doc = json.loads(json.dumps(doc))  # as it comes from the host

    @dataclasses.dataclass(frozen=True)
    class OldZip:  # every Live's Zip: its four fields
        path: str
        size: int
        sha256: str
        unpacked: int

    for known in (("mesh", "overlays"), ("mesh", "overlays", "night")):  # 2.0, 2.1 to 2.2
        for kinds in doc["regions"].values():
            read = {kind: tuple(OldZip(**z) for z in kinds.get(kind, ())) for kind in known}
            assert [z.path for z in read["mesh"]] == [z.path for z in region.mesh]
    assert osxpregions.parse_manifest(doc)["eur"].rivers == region.rivers
    with pytest.raises(TypeError):  # what an added field would do to them
        OldZip(**{**doc["regions"]["eur"]["mesh"][0], "files": 3})
