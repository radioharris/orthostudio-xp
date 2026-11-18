# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP's regions installed by Live (orthostudio.live.osxpregions), from a host that
answers as the real one does: Live's token asked for, a Range honoured. A region is made whole in
its stage and put in place with one rename; what a stop, a power cut, a full disk or a running
X-Plane leaves is made right by the next install or the next start."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import shutil
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from orthostudio.live import aoregions, osxpregions
from orthostudio.live.osxpregions import Installer, OsxpRegion, Zip

URL = "https://live.example"
TOKEN = "t0ken"
RELEASE = "2026-11-18"
MESH_A = f"eur/{RELEASE}/zips/z_osxp_eur_+45+005.zip"
MESH_B = f"eur/{RELEASE}/zips/z_osxp_eur_+46+005.zip"
OVERLAY_A = "eur/overlay-zips/y_osxp_eur_+45+005.zip"
NIGHT = "eur/night-zips/night_eur.zip"


def zip_of(top: str, files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(f"{top}/{name}", data)
    return out.getvalue()


def entry(path: str, data: bytes) -> Zip:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            unpacked = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile:  # a damaged zip the list was made from
        unpacked = len(data)
    return Zip(path, len(data), hashlib.sha256(data).hexdigest(), unpacked)


class Host:
    """The server: the files, Live's token, Range; ``cut`` drops the first answer of a file after
    that many bytes, as a line that goes down; ``failing`` answers that many first asks of the zips
    with a 500, a fault of its own; ``hook`` is called with each file asked, before it is
    answered."""

    def __init__(
        self,
        files: dict[str, bytes],
        cut: int | None = None,
        slow: dict[str, float] | None = None,
        failing: int = 0,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self.files = files
        self.cut = cut
        self.slow = slow or {}  # a file answered that many seconds late
        self.failing = failing
        self.hook = hook
        self.asked: list[tuple[str, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        self.asked.append((key, request.headers.get("Range")))
        if self.hook is not None:
            self.hook(key)
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


@pytest.fixture(autouse=True)
def delete_at_once(monkeypatch):
    """The region of before deleted before the install ends, not in the background: what each
    test looks at is then there or not, whatever the machine's speed."""
    monkeypatch.setattr(
        osxpregions, "_delete_aside", lambda folder: shutil.rmtree(folder, ignore_errors=True)
    )


def region_of(files: dict[str, bytes], release: str = RELEASE) -> OsxpRegion:
    """The region a list gives for these files, each by the folder it is in."""
    kinds: dict[str, list[Zip]] = {"mesh": [], "overlays": [], "night": [], "rivers": []}
    for path, data in files.items():
        if "/zips/rivers/" in path:
            kind = "rivers"
        elif "/overlay-zips/" in path:
            kind = "overlays"
        elif "/night-zips/" in path:
            kind = "night"
        else:
            kind = "mesh"
        kinds[kind].append(entry(path, data))
    return OsxpRegion("eur", release, *(tuple(kinds[k]) for k in kinds))


@pytest.fixture
def world():
    files = {
        MESH_A: zip_of(
            "z_osxp_eur_+45+005",
            {"Earth nav data/+40+000/+45+005.dsf": b"dsf a" * 400, "terrain/a.ter": b"ter a"},
        ),
        MESH_B: zip_of(
            "z_osxp_eur_+46+005",
            {"Earth nav data/+40+000/+46+005.dsf": b"dsf b" * 400, "terrain/b.ter": b"ter b"},
        ),
        OVERLAY_A: zip_of("y_osxp_overlays", {"Earth nav data/+40+000/+45+005.dsf": b"roads a"}),
    }
    return files, region_of(files)


def total(region: OsxpRegion, kind: str = "size", rivers: bool = False) -> int:
    """A whole install's bytes, downloaded (``size``) or unpacked: what the region wants in its
    folder from scratch (osxpregions.wanted), and its overlays for the download."""
    zips = (*region.mesh, *region.night, *(region.rivers if rivers else ()))
    if kind == "size":
        zips = (*zips, *region.overlays)
    return sum(getattr(z, kind) for z in zips)


def install(root: Path, region: OsxpRegion, host: Host, **kw) -> tuple[Installer, list[str]]:
    """One install, Live stopped and X-Plane not running unless ``may_swap`` says otherwise."""
    lines: list[str] = []
    client = httpx.Client(
        transport=httpx.MockTransport(host), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    kw.setdefault("may_swap", lambda: True)
    inst = Installer(root, region, client=client, host=(URL, TOKEN), log=lines.append, **kw)
    inst.run()
    return inst, lines


def asked(host: Host) -> list[str]:
    """The files asked of the host, once each, in the list's order whatever order they came in."""
    return sorted({key for key, _ in host.asked})


def mended(files: dict, region: OsxpRegion, which: int, content: dict[str, bytes]):
    """The world with its mesh zip ``which`` made again with ``content``: (files, region)."""
    path = region.mesh[which].path
    data = zip_of(path.rsplit("/", 1)[-1].removesuffix(".zip"), content)
    mesh = list(region.mesh)
    mesh[which] = entry(path, data)
    return {**files, path: data}, dataclasses.replace(region, mesh=tuple(mesh))


def marker_of(root: Path) -> dict:
    return json.loads((osxpregions.mesh_dir(root, "eur") / aoregions.MARKER).read_text())


def hidden(root: Path) -> list[str]:
    """The hidden entries of Live's regions folder: stages, regions of before, downloads."""
    return sorted(p.name for p in osxpregions.regions_dir(root).iterdir() if p.name[0] == ".")


# --- an install -------------------------------------------------------------------------------


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
    marker = marker_of(tmp_path)
    assert marker["zips"] == {z.path: z.sha256 for z in region.mesh}
    assert marker["size"] == total(region, "unpacked")
    assert "overlays_waiting" not in marker
    assert not (folder / osxpregions.STEP).exists()
    assert hidden(tmp_path) == [".download", ".lists"]  # no stage, no zip left
    assert not (tmp_path / "regions" / ".download" / "osxp_eur").exists()
    assert any(r is not None and r.startswith("bytes=") for _, r in host.asked)  # resumed
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])
    assert not osxpregions.made_whole(tmp_path, region)


def test_the_list_is_manifest_2_kept_apart_from_the_one_of_lives_before(
    tmp_path: Path, world
) -> None:
    """Live reads ``manifest-2.json``, its mesh and river zips in a folder of their bake, and keeps
    it in ``osxp-catalog-2.json``: ``osxp-catalog.json``, the list a Live up to 2.2 keeps in the
    same folder, is never written, so that one never sees this world."""
    _, region = world
    doc = {
        "format": 1,
        "made": "2026-11-18T01:00:00+0100",
        "regions": {
            "eur": {
                kind: [dataclasses.asdict(z) for z in getattr(region, kind)]
                for kind in ("mesh", "overlays", "night", "rivers")
            }
        },
    }
    served: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        served.append(request.url.path)
        if request.url.path == "/manifest-2.json":
            return httpx.Response(200, json=doc)
        return httpx.Response(404)

    regions_dir = osxpregions.regions_dir(tmp_path)
    regions_dir.mkdir(parents=True)
    before = b'{"regions": {"eur": {"mesh": []}}}'
    (regions_dir / "osxp-catalog.json").write_bytes(before)
    client = httpx.Client(transport=httpx.MockTransport(answer))
    got = osxpregions.catalog(tmp_path, client=client, host=(URL, TOKEN))
    assert served == ["/manifest-2.json"]
    assert got["eur"].mesh == region.mesh and got["eur"].mesh[0].path == MESH_A
    assert (regions_dir / "osxp-catalog.json").read_bytes() == before
    assert json.loads((regions_dir / osxpregions.CATALOG).read_text()) == doc
    # the host away: the list kept stands in
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    again = osxpregions.catalog(tmp_path, refresh=True, client=down, host=(URL, TOKEN))
    assert again["eur"].mesh == region.mesh


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


def test_without_overlays_and_a_damaged_file(tmp_path: Path, world) -> None:
    """A zip whose SHA-256 is not the list's: deleted, the install stopped, nothing in use; the
    next try downloads that zip alone."""
    files, region = world
    damaged = {**files, MESH_B: b"x" * region.mesh[1].size}
    inst, lines = install(tmp_path, region, Host(damaged), overlays=False)
    assert inst.progress.phase == "failed" and "damaged" in inst.progress.error
    assert not osxpregions.mesh_dir(tmp_path, "eur").exists()  # not used: not whole
    assert osxpregions.to_fetch(tmp_path, region, False) == ([region.mesh[1]], [])
    host = Host(files)
    inst, lines = install(tmp_path, region, host, overlays=False)
    assert inst.progress.phase == "done", lines
    assert asked(host) == [MESH_B]
    assert not (osxpregions.overlay_pack(tmp_path) / "Earth nav data").exists()


def test_a_zip_that_cannot_be_unpacked_is_deleted_and_downloaded_again(
    tmp_path: Path, world
) -> None:
    """A zip the list itself was made from damaged (its SHA-256 the list's): unpacked, it fails,
    and it is deleted, so that the next try downloads it again, once the list is mended, instead
    of failing on it for ever."""
    files, region = world
    broken = b"PK\x03\x04 not a zip at all" * 20
    bad = {**files, MESH_B: broken}
    listed = region_of(bad)
    inst, _ = install(tmp_path, listed, Host(bad))
    assert inst.progress.phase == "failed"
    assert "could not be unpacked" in inst.progress.error
    download = osxpregions.regions_dir(tmp_path) / ".download" / "osxp_eur"
    assert not (download / listed.mesh[1].download).exists()
    host = Host(files)  # the list mended
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert MESH_B in asked(host)


def test_a_full_disk_stops_an_install_before_its_first_byte(
    tmp_path: Path, world, monkeypatch
) -> None:
    """The room the whole install needs, checked before anything is downloaded: the region's files,
    the overlays', the largest zip, and two GB spare; the region of before as it was."""
    files, region = world
    real = shutil.disk_usage

    def full(path):
        got = real(path)
        return type(got)(got.total, got.total - 10**6, 10**6)

    monkeypatch.setattr(osxpregions.shutil, "disk_usage", full)
    host = Host(files)
    inst, _ = install(tmp_path, region, host)
    assert inst.progress.phase == "failed" and "GB needed on the disk" in inst.progress.error
    assert host.asked == []


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
    inst = Installer(tmp_path, region, host=(URL, TOKEN), log=lambda _: None, may_swap=bool)
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
    (download / first.download).write_bytes(files[first.path][:had])
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 1.0
        return ticks[0]

    host = Host(files)
    client = httpx.Client(
        transport=httpx.MockTransport(host), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    inst = Installer(
        tmp_path, region, client=client, host=(URL, TOKEN), log=lambda _: None, may_swap=bool
    )
    inst.clock = clock
    inst.run()
    assert inst.progress.phase == "done"
    assert inst.progress.done_bytes == total(region)
    assert inst.progress.mbs * 1e6 <= total(region) - had  # a second at least between two samples
    assert (first.path, f"bytes={had}-") in host.asked  # resumed where it was


def test_a_mask_two_zips_hold_is_the_later_zip_s_whatever_comes_first(tmp_path: Path) -> None:
    """A photo across two squares' border: both zips hold its mask, a little different. Unpacked
    as they arrived, the one that came last won, so two computers could differ; unpacked in the
    list's order, it is the later zip's everywhere (2026-10-19)."""
    mask = "textures/19024_33744_ZL16.png"
    first = zip_of("z_osxp_eur_+59+005", {"terrain/a.ter": b"a", mask: b"mask of the first"})
    second = zip_of("z_osxp_eur_+60+005", {"terrain/b.ter": b"b", mask: b"mask of the second"})
    one, two = (
        f"eur/{RELEASE}/zips/z_osxp_eur_+59+005.zip",
        f"eur/{RELEASE}/zips/z_osxp_eur_+60+005.zip",
    )
    files = {one: first, two: second}
    region = region_of(files)
    host = Host(files, slow={one: 0.5})  # the first zip of the list comes last
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / mask).read_bytes() == b"mask of the second"


# --- an update: the region whole again, in one rename -------------------------------------------


def test_a_mended_zip_brings_the_region_whole_and_nothing_of_before_stays(
    tmp_path: Path, world
) -> None:
    """One zip of the mesh made again: the region is downloaded whole into its stage and takes
    the place of the one of before, so that no file of the mesh of before stays behind (every bake
    makes every zip again). Its night grids come again with it; the night's own download leaves
    such a region to its update."""
    files, region = world
    night = zip_of("night_eur", {"night/+45+005.png": b"grid a"})
    files = {**files, NIGHT: night}
    region = region_of(files)
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
    assert osxpregions.night_to_fetch(tmp_path, region) == []  # the update brings them
    mesh, _ = osxpregions.to_fetch(tmp_path, region, True)
    assert mesh == [*region.mesh, *region.night]
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted(z.path for z in (*region.mesh, *region.night))
    assert (folder / "terrain/b2.ter").read_bytes() == b"ter B"
    assert not (folder / "terrain/b.ter").exists()  # the zip of before's file, gone
    assert (folder / "terrain/a.ter").read_bytes() == b"ter a"
    assert (folder / "night/+45+005.png").read_bytes() == b"grid a"
    marker = marker_of(tmp_path)
    assert marker["zips"] == {z.path: z.sha256 for z in (*region.mesh, *region.night)}
    assert marker["size"] == total(region, "unpacked")
    assert hidden(tmp_path) == [".download", ".lists"]  # no stage, no region of before, no zip
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])


def test_zip_names_gone_from_the_list_leave_nothing_behind(tmp_path: Path) -> None:
    """A new world: its zips in a folder of their bake, some names gone, some new. The region of
    before goes whole: no DSF, terrain list or mask of a zip gone stays, and its marker names the
    new world's zips only. A region a Live of before updated in place, its marker still naming
    zips gone, is made whole again too."""

    def mesh(stem: str, tag: bytes) -> bytes:
        square = stem.rsplit("_", 1)[1]
        return zip_of(
            stem,
            {
                f"Earth nav data/+40+000/{square}.dsf": b"dsf " + tag,
                f"terrain-lists/{stem}.txt": tag,
                f"textures/{square}_ZL16.png": b"mask " + tag,
            },
        )

    before = {
        f"eur/zips/z_osxp_eur_{sq}.zip": mesh(f"z_osxp_eur_{sq}", b"10-xx")
        for sq in ("+45+005", "+46+005", "+47+005")
    }
    install(tmp_path, region_of(before, "2026-11-01"), Host(before))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    after = {
        f"eur/{RELEASE}/zips/z_osxp_eur_{sq}.zip": mesh(f"z_osxp_eur_{sq}", b"11-18")
        for sq in ("+45+005", "+48+005")
    }
    region = region_of(after)
    assert osxpregions.made_whole(tmp_path, region)
    inst, lines = install(tmp_path, region, Host(after))
    assert inst.progress.phase == "done", lines
    assert sorted(p.name for p in (folder / "terrain-lists").iterdir()) == [
        "z_osxp_eur_+45+005.txt",
        "z_osxp_eur_+48+005.txt",
    ]
    assert sorted(p.name for p in (folder / "Earth nav data/+40+000").iterdir()) == [
        "+45+005.dsf",
        "+48+005.dsf",
    ]
    assert not (folder / "textures/+46+005_ZL16.png").exists()
    assert set(marker_of(tmp_path)["zips"]) == set(after)
    # a Live of before put the new world in place over the old one: a zip gone still named
    marker = marker_of(tmp_path)
    marker["zips"]["eur/zips/z_osxp_eur_+46+005.zip"] = "0" * 64
    (folder / aoregions.MARKER).write_text(json.dumps(marker))
    assert osxpregions.made_whole(tmp_path, region)
    inst, lines = install(tmp_path, region, Host(after))
    assert inst.progress.phase == "done", lines
    assert set(marker_of(tmp_path)["zips"]) == set(after)


def test_a_part_of_a_zip_made_again_since_is_deleted_not_resumed(tmp_path: Path, world) -> None:
    """An install stopped before the new world came: a part of its zip is in the download folder,
    under the name of the zip of before. Resumed onto the new one, it made a damaged file and a
    failed install; deleted, the update goes through at once. So is a Live of before's part,
    under the zip's bare name."""
    files, region = world
    install(tmp_path, region, Host(files))
    old_b = region.mesh[1]
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    download = osxpregions.regions_dir(tmp_path) / ".download" / "osxp_eur"
    download.mkdir(parents=True)
    stale = download / old_b.download
    stale.write_bytes(files[MESH_A][:50])
    before = download / old_b.name
    before.write_bytes(b"half a zip of before")
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert not stale.exists() and not before.exists()
    assert all(r is None for _, r in host.asked)  # nothing resumed onto a part of before


def test_an_update_stopped_halfway_leaves_the_region_of_before_whole(tmp_path: Path, world) -> None:
    """Both zips made again, the second one damaged on the way: the region of before is untouched
    and still the one Live uses; the next install goes on in the stage with the second zip alone."""
    files, region = world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    before = (folder / aoregions.MARKER).read_text()
    files, region = mended(files, region, 0, {"terrain/a.ter": b"ter A"})
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    damaged = {**files, MESH_B: b"x" * region.mesh[1].size}
    inst, lines = install(tmp_path, region, Host(damaged))
    assert inst.progress.phase == "failed" and "damaged" in inst.progress.error
    assert (folder / aoregions.MARKER).read_text() == before
    assert (folder / "terrain/a.ter").read_bytes() == b"ter a"  # the region of before, whole
    assert (folder / "Earth nav data/+40+000/+46+005.dsf").exists()
    assert osxpregions.to_fetch(tmp_path, region, True) == ([region.mesh[1]], [])
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert asked(host) == [MESH_B]
    assert (folder / "terrain/a.ter").read_bytes() == b"ter A"
    assert (folder / "terrain/b.ter").read_bytes() == b"ter B"
    assert not (folder / "Earth nav data").exists()  # the mesh of before's DSFs, gone


def test_an_update_stopped_while_it_downloads_goes_on_from_there(tmp_path: Path, world) -> None:
    """The window's Stop while the zips come: the region of before as it was, what came kept,
    only the rest downloaded next time."""
    files, region = world
    install(tmp_path, region, Host(files))
    before = marker_of(tmp_path)
    files, region = mended(files, region, 0, {"terrain/a.ter": b"ter A"})
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    lines: list[str] = []
    inst: Installer | None = None

    def stop_at_b(key: str) -> None:
        if key == MESH_B and inst is not None:
            inst.cancel()

    client = httpx.Client(
        transport=httpx.MockTransport(Host(files, hook=stop_at_b, slow={MESH_B: 0.2})),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    inst = Installer(
        tmp_path, region, client=client, host=(URL, TOKEN), log=lines.append, may_swap=bool
    )
    inst.run()
    assert inst.progress.phase == "cancelled", lines
    assert marker_of(tmp_path) == before
    left, _ = osxpregions.to_fetch(tmp_path, region, True)
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    # what the stage holds is not asked again, nor a zip that came whole before the stop
    assert set(asked(host)) <= {z.path for z in left} and MESH_B in asked(host)
    assert marker_of(tmp_path)["zips"] == {z.path: z.sha256 for z in region.mesh}


def test_a_stop_while_it_unpacks_goes_on_in_the_stage(tmp_path: Path, world, monkeypatch) -> None:
    """Stopped in the middle of a zip's files: its files whole or not there, the zip kept, the
    region of before in use; the next install unpacks it again, downloading nothing it has."""
    files, region = world
    install(tmp_path, region, Host(files))
    before = marker_of(tmp_path)
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    unpacking = osxpregions.unpack

    def stopped(path: Path, dest: Path, stop=None) -> int:
        if path.name.endswith("z_osxp_eur_+46+005.zip"):
            raise osxpregions.CancelledError
        return unpacking(path, dest, stop)

    monkeypatch.setattr(osxpregions, "unpack", stopped)
    inst, _ = install(tmp_path, region, Host(files))
    assert inst.progress.phase == "cancelled"
    assert marker_of(tmp_path) == before
    stage = osxpregions.stage_dir(tmp_path, "eur")
    assert json.loads((stage / osxpregions.STEP).read_text())["zips"] == {
        MESH_A: region.mesh[0].sha256
    }
    monkeypatch.setattr(osxpregions, "unpack", unpacking)
    host = Host(files)
    inst, lines = install(tmp_path, region, host)  # Live started again: a new install
    assert inst.progress.phase == "done", lines
    assert host.asked == []  # the zip was kept whole
    assert (osxpregions.mesh_dir(tmp_path, "eur") / "terrain/b.ter").read_bytes() == b"ter B"


def test_a_stage_holding_a_zip_made_again_since_is_started_again(tmp_path: Path, world) -> None:
    """An install stopped with the first zip in its stage; the first zip made again on the server
    before the next try: the stage is started again, so that a file only the zip of before held
    does not end in the region."""
    files, region = world
    damaged = {**files, MESH_B: b"x" * region.mesh[1].size}
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


def test_an_update_waits_while_x_plane_runs_and_live_s_next_start_puts_it_in_place(
    tmp_path: Path, world
) -> None:
    """X-Plane running (or Live streaming): the update is made whole and waits, the region of
    before and its overlays untouched, its own overlays downloaded and waiting; the page keeps its
    Update. Live's next start with X-Plane not running puts it in place first, then its overlays;
    with X-Plane running, the start leaves it waiting."""
    files, region = world
    install(tmp_path, region, Host(files))
    before = marker_of(tmp_path)
    pack = osxpregions.overlay_pack(tmp_path)
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    roads = zip_of("y_osxp_overlays", {"Earth nav data/+40+000/+45+005.dsf": b"roads A"})
    files = {**files, OVERLAY_A: roads}
    region = dataclasses.replace(region, overlays=(entry(OVERLAY_A, roads),))
    inst, lines = install(tmp_path, region, Host(files), may_swap=lambda: False)
    assert inst.progress.phase == "waiting", lines
    assert marker_of(tmp_path) == before
    assert (pack / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"roads a"
    assert osxpregions.staged_whole(tmp_path, "eur")
    assert osxpregions.not_installed(tmp_path, region, False) == [region.mesh[1]]  # Update
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])  # nothing to download
    said: list[str] = []
    osxpregions.settle(tmp_path, said.append, swap=False)  # a start with X-Plane running
    assert marker_of(tmp_path) == before
    osxpregions.settle(tmp_path, said.append, swap=True)  # a start with X-Plane not running
    assert any("update put in place" in s for s in said)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "terrain/b.ter").read_bytes() == b"ter B"
    assert (pack / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"roads A"
    record = json.loads((pack / osxpregions.OVERLAY_RECORD).read_text())
    assert record == {OVERLAY_A: region.overlays[0].sha256}
    assert "overlays_waiting" not in marker_of(tmp_path)
    assert hidden(tmp_path) == [".download", ".lists"]
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])


def test_a_first_install_takes_its_place_whatever_x_plane_does(tmp_path: Path, world) -> None:
    """A region not installed yet: nothing reads it, so it takes its place as soon as it is
    whole, X-Plane running or not; used from Live's next start."""
    files, region = world
    inst, lines = install(tmp_path, region, Host(files), may_swap=lambda: False)
    assert inst.progress.phase == "done", lines
    assert marker_of(tmp_path)["zips"] == {z.path: z.sha256 for z in region.mesh}
    assert hidden(tmp_path) == [".download", ".lists"]


def test_live_quit_before_the_rename_keeps_the_update_offered(
    tmp_path: Path, world, monkeypatch
) -> None:
    """The app quit (or the computer stopped) between the last zip and the rename: the stage is
    whole, the region of before in use; the page still offers Update, and the next install,
    after Live started again, downloads nothing and only puts it in place."""
    files, region = world
    install(tmp_path, region, Host(files))
    before = marker_of(tmp_path)
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})

    def quit_now(root: Path, region_id: str, log=print) -> bool:
        if osxpregions.staged_whole(root, region_id):  # not the start's look, before it is whole
            raise KeyboardInterrupt("the app quit")
        return False

    putting = osxpregions.put_in_place
    monkeypatch.setattr(osxpregions, "put_in_place", quit_now)
    with pytest.raises(KeyboardInterrupt):
        install(tmp_path, region, Host(files))
    monkeypatch.setattr(osxpregions, "put_in_place", putting)
    assert marker_of(tmp_path) == before
    assert osxpregions.staged_whole(tmp_path, "eur")
    assert osxpregions.not_installed(tmp_path, region, False)  # the page offers Update
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done", lines
    assert host.asked == []
    assert marker_of(tmp_path)["zips"] == {z.path: z.sha256 for z in region.mesh}


def test_a_power_cut_between_the_two_renames_is_put_right(
    tmp_path: Path, world, monkeypatch
) -> None:
    """The region of before renamed aside, the power cut before the stage took its place: no
    region at all. The window, as it opens, puts the region of before back; Live's next start
    with X-Plane not running then puts the update in place. A region of before left beside its
    successor is deleted."""
    files, region = world
    install(tmp_path, region, Host(files))
    before = marker_of(tmp_path)
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    stage = osxpregions.stage_dir(tmp_path, "eur")
    renaming = osxpregions.replace_file

    def cut(src, dst):
        if Path(src) == stage:
            raise KeyboardInterrupt("the power cut")
        renaming(src, dst)

    monkeypatch.setattr(osxpregions, "replace_file", cut)
    with pytest.raises(KeyboardInterrupt):
        install(tmp_path, region, Host(files))
    monkeypatch.setattr(osxpregions, "replace_file", renaming)
    assert not folder.exists() and osxpregions.installed(tmp_path) == {}
    assert osxpregions.present(tmp_path, "eur")
    said: list[str] = []
    osxpregions.settle(tmp_path, said.append, swap=False)  # the window opens
    assert marker_of(tmp_path) == before and any("put back" in s for s in said)
    assert osxpregions.staged_whole(tmp_path, "eur")
    osxpregions.settle(tmp_path, said.append, swap=True)  # Live starts, X-Plane not running
    assert marker_of(tmp_path)["zips"] == {z.path: z.sha256 for z in region.mesh}
    old = folder.with_name(".z_osxp_eur.old")
    old.mkdir()  # a region of before whose deletion a cut stopped
    (old / "x.dsf").write_bytes(b"of before")
    aside = folder.with_name(".z_osxp_eur.old.1795000000000000000")
    aside.mkdir()  # one being deleted in the background as the app quit
    (aside / "y.dsf").write_bytes(b"of before")
    osxpregions.settle(tmp_path, said.append, swap=False)
    assert not old.exists() and aside.exists()  # a deletion maybe under way: left to it
    assert osxpregions.take_up_deletions(tmp_path) == 1  # once, as the window opens
    assert hidden(tmp_path) == [".download", ".lists"]


def test_a_region_in_use_keeps_its_place_until_the_next_try(
    tmp_path: Path, world, monkeypatch
) -> None:
    """A folder a program holds open is not renamed (Windows): the region of before stays as it
    was and in use, the new one whole in its stage; the page keeps Update, and the next install
    downloads nothing and only puts it in place."""
    files, region = world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    renaming = osxpregions.replace_file

    def held(src, dst):
        if Path(src) == folder:
            raise PermissionError(32, "in use by another process")
        renaming(src, dst)

    monkeypatch.setattr(osxpregions, "replace_file", held)
    inst, lines = install(tmp_path, region, Host(files))
    assert inst.progress.phase == "failed" and "in use" in inst.progress.error
    assert "close it" in inst.progress.error
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"  # the region of before, in use
    assert osxpregions.staged_whole(tmp_path, "eur")
    assert osxpregions.not_installed(tmp_path, region, False)
    monkeypatch.setattr(osxpregions, "replace_file", renaming)
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
    lines = []
    osxpregions.remove(root, "eur", [(45, 5), (46, 5)], log=lines.append)
    assert not os.path.lexists(folder) and not elsewhere.exists()


def test_a_key_refused_and_a_region_deleted(tmp_path: Path, world) -> None:
    """Deleted: the region, its stage, its downloads and its overlays. A region of which a stop
    left only its stage is deleted too; one held open is not touched."""
    files, region = world
    lines: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(Host(files)))
    inst = Installer(
        tmp_path, region, client=client, host=(URL, "old"), log=lines.append, may_swap=bool
    )
    inst.run()
    assert inst.progress.phase == "failed" and "key" in inst.progress.error
    install(tmp_path, region, Host(files))
    stage = osxpregions.stage_dir(tmp_path, "eur")  # an update stopped halfway
    stage.mkdir()
    (stage / "x.dsf").write_bytes(b"half")
    osxpregions.remove(tmp_path, "eur", [(45, 5), (46, 5)], log=lines.append)
    assert not osxpregions.mesh_dir(tmp_path, "eur").exists()
    assert not stage.exists() and not osxpregions.present(tmp_path, "eur")
    pack = osxpregions.overlay_pack(tmp_path)
    assert not (pack / "Earth nav data/+40+000/+45+005.dsf").exists()
    assert json.loads((pack / osxpregions.OVERLAY_RECORD).read_text()) == {}
    # a first install stopped: its stage alone
    damaged = {**files, MESH_B: b"x" * region.mesh[1].size}
    install(tmp_path, region, Host(damaged))
    assert osxpregions.present(tmp_path, "eur") and not osxpregions.installed(tmp_path)
    osxpregions.remove(tmp_path, "eur", [], log=lines.append)
    assert not osxpregions.present(tmp_path, "eur")


def test_a_region_being_deleted_is_never_put_back(tmp_path: Path, world, monkeypatch) -> None:
    """A region is renamed aside under a name of its own before it is deleted: a stop in the
    middle of its deletion leaves it to be deleted at the next look, never put back half
    deleted as an update stopped halfway would be."""
    files, region = world
    install(tmp_path, region, Host(files))
    deleting = shutil.rmtree

    def stopped(path, ignore_errors=False):
        if ".old." in Path(path).name:
            (Path(path) / "terrain/a.ter").unlink()  # half deleted, then the app quit
            raise KeyboardInterrupt("the app quit")
        deleting(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(osxpregions.shutil, "rmtree", stopped)
    with pytest.raises(KeyboardInterrupt):
        osxpregions.remove(tmp_path, "eur", [], log=lambda _: None)
    monkeypatch.setattr(osxpregions.shutil, "rmtree", deleting)
    osxpregions.settle(tmp_path, lambda _: None, swap=True)  # a start: never put back
    assert not osxpregions.mesh_dir(tmp_path, "eur").exists()
    assert osxpregions.installed(tmp_path) == {}
    assert osxpregions.take_up_deletions(tmp_path) == 1  # as the window opens: deleted
    assert hidden(tmp_path) == [".download", ".lists"]


def test_a_stage_made_again_under_the_same_name_is_started_again(tmp_path: Path, world) -> None:
    """A zip stopped halfway through its unpacking, then made again on the server under the same
    path: the stage holding half of the zip of before is started again, never mixed with the new
    one."""
    files, region = world
    first = osxpregions.stage_dir(tmp_path, "eur")
    first.mkdir(parents=True)
    (first / "terrain").mkdir()
    (first / "terrain/a-of-before.ter").write_bytes(b"half of the zip of before")
    step = {"zips": {}, "overlays": {}, "unpacking": {MESH_A: "0" * 64}}
    (first / osxpregions.STEP).write_text(json.dumps(step))
    inst, lines = install(tmp_path, region, Host(files))
    assert inst.progress.phase == "done", lines
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert not (folder / "terrain/a-of-before.ter").exists()
    assert (folder / "terrain/a.ter").read_bytes() == b"ter a"


def test_a_region_held_open_is_not_deleted_at_all(tmp_path: Path, world, monkeypatch) -> None:
    files, region = world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")

    def held(src, dst):
        raise PermissionError(32, "in use by another process")

    monkeypatch.setattr(osxpregions, "replace_file", held)
    with pytest.raises(osxpregions.InstallError, match="in use"):
        osxpregions.remove(tmp_path, "eur", [(45, 5)], log=lambda _: None)
    assert (folder / aoregions.MARKER).exists() and (folder / "terrain/a.ter").exists()


# --- night grids ------------------------------------------------------------------------------


def test_a_region_s_night_grids_come_with_it_and_alone_later(tmp_path: Path, world) -> None:
    """The night lights' density grids (orthostudio.live.nightwork) are a zip of their own in the
    manifest (``night``): unpacked into the region's folder as its mesh, and, added to a region
    installed before, the only thing an update downloads."""
    files, region = world
    install(tmp_path, region, Host(files))
    night = zip_of("night_eur", {"night/+45+005.png": b"grid a", "night/+46+005.png": b"grid b"})
    files = {**files, NIGHT: night}
    doc = {
        "made": "2026-11-10",
        "regions": {
            "eur": {
                "mesh": [dataclasses.asdict(z) for z in region.mesh],
                "overlays": [dataclasses.asdict(z) for z in region.overlays],
                "night": [dataclasses.asdict(entry(NIGHT, night))],
            }
        },
    }
    later = osxpregions.parse_manifest(doc)["eur"]
    assert not osxpregions.made_whole(tmp_path, later)
    mesh, ovl = osxpregions.to_fetch(tmp_path, later, True)
    assert [z.path for z in mesh] == [NIGHT] and ovl == []
    assert osxpregions.wanted(tmp_path, later, False) == (*region.mesh, *later.night)
    host = Host(files)
    inst, lines = install(tmp_path, later, host)
    assert inst.progress.phase == "done", lines
    assert asked(host) == [NIGHT]
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
    files = {**files, NIGHT: night}
    later = dataclasses.replace(region, night=(entry(NIGHT, night),))
    assert [z.path for z in osxpregions.night_to_fetch(tmp_path, later)] == [NIGHT]
    nav = folder / "Earth nav data"
    os.utime(nav, (1_000_000, 1_000_000))
    before = marker_of(tmp_path)
    # an install the pilot stopped: what it downloaded is kept to go on from it
    stopped = osxpregions.regions_dir(tmp_path) / ".download" / "osxp_eur" / "part.zip"
    stopped.parent.mkdir(parents=True)
    stopped.write_bytes(b"half a zip")
    host = Host(files)
    inst, lines = install(tmp_path, later, host, overlays=False, night_only=True)
    assert inst.progress.phase == "done", lines
    assert stopped.read_bytes() == b"half a zip"
    assert asked(host) == [NIGHT]
    assert (folder / "night/+45+005.png").read_bytes() == b"grid a"
    marker = marker_of(tmp_path)
    assert marker["zips"] == {**before["zips"], NIGHT: later.night[0].sha256}
    assert marker["size"] == before["size"] + later.night[0].unpacked
    assert marker["installed"] == before["installed"]
    assert nav.stat().st_mtime == 1_000_000  # the set-up not made again
    assert not (folder / osxpregions.STEP).exists()
    assert osxpregions.night_to_fetch(tmp_path, later) == []
    assert osxpregions.to_fetch(tmp_path, later, True) == ([], [])
    assert any("night lights' grids" in ln for ln in lines)


# --- the river masks --------------------------------------------------------------------------

MASK_A, MASK_B = "rivers/textures/23296_33152_ZL16.png", "rivers/textures/23280_33152_ZL16.png"
RIVERS_A = f"eur/{RELEASE}/zips/rivers/z_osxp_eur_+45+005.zip"
RIVERS_B = f"eur/{RELEASE}/zips/rivers/z_osxp_eur_+46+005.zip"


def river_zip(stem: str, masks: dict[str, bytes]) -> bytes:
    """A river zip as the bake packs it: beside its mesh zip, the same top folder, its masks in
    rivers/textures."""
    return zip_of(stem, masks)


@pytest.fixture
def rivers_world(world):
    """The world's two mesh zips, each with its river zip beside it (``rivers`` in the list)."""
    files, region = world
    rivers = {
        RIVERS_A: river_zip("z_osxp_eur_+45+005", {MASK_A: b"river a"}),
        RIVERS_B: river_zip("z_osxp_eur_+46+005", {MASK_B: b"river b"}),
    }
    region = dataclasses.replace(region, rivers=tuple(entry(k, v) for k, v in rivers.items()))
    return {**files, **rivers}, region


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
    """With soft banks, the river masks come in the region's stage with its mesh: one rename puts
    them all in place."""
    files, region = rivers_world
    host = Host(files)
    inst, lines = install(tmp_path / "even", region, host)
    assert inst.progress.phase == "done", lines
    assert not any("/rivers/" in key for key in asked(host))
    folder = osxpregions.mesh_dir(tmp_path / "even", "eur")
    assert not (folder / "rivers").exists() and not (folder / osxpregions.RIVERS_STAGE).exists()
    assert set(marker_of(tmp_path / "even")["zips"]) == {z.path for z in region.mesh}
    host = Host(files)
    inst, lines = install(tmp_path / "soft", region, host, rivers=True)
    assert inst.progress.phase == "done", lines
    assert inst.progress.total_bytes == total(region, rivers=True)
    folder = osxpregions.mesh_dir(tmp_path / "soft", "eur")
    assert (folder / MASK_A).read_bytes() == b"river a"
    assert (folder / MASK_B).read_bytes() == b"river b"
    assert not (folder / osxpregions.RIVERS_STAGE).exists()
    marker = marker_of(tmp_path / "soft")
    assert marker["zips"] == {z.path: z.sha256 for z in (*region.mesh, *region.rivers)}
    assert marker["size"] == total(region, "unpacked", rivers=True)  # the region counts them
    assert osxpregions.to_fetch(tmp_path / "soft", region, True, rivers=True) == ([], [])
    assert any("2 river zips" in ln for ln in lines)


def test_soft_banks_chosen_later_brings_the_river_masks_alone(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """The region's mesh the list's, Live stopped and X-Plane not running: its river zips alone,
    unpacked beside, put in place whole, then recorded in its marker, last; its mesh, its set-up's
    dates and its list untouched."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert osxpregions.rivers_to_add(tmp_path, region) == list(region.rivers)
    nav = folder / "Earth nav data"
    os.utime(nav, (1_000_000, 1_000_000))
    before = marker_of(tmp_path)
    writing = osxpregions._write

    def write(path: Path, doc: dict) -> None:
        if path == folder / aoregions.MARKER:  # the region's: the masks whole in place already
            assert (folder / MASK_A).exists() and (folder / MASK_B).exists()
            assert not (folder / osxpregions.RIVERS_STAGE / "rivers").exists()
        writing(path, doc)

    monkeypatch.setattr(osxpregions, "_write", write)
    host = Host(files)
    inst, lines = install(tmp_path, region, host, overlays=False, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted(z.path for z in region.rivers)
    marker = marker_of(tmp_path)
    assert marker["zips"] == {**before["zips"], **{z.path: z.sha256 for z in region.rivers}}
    assert marker["installed"] == before["installed"]
    assert marker["size"] == before["size"] + sum(z.unpacked for z in region.rivers)
    assert nav.stat().st_mtime == 1_000_000  # the mesh as it was: no set-up for it
    assert not (folder / osxpregions.STEP).exists()
    assert not (folder / osxpregions.RIVERS_STAGE).exists()
    assert osxpregions.rivers_to_add(tmp_path, region) == []
    assert any("river masks installed" in ln for ln in lines)


def test_soft_banks_chosen_in_flight_wait_for_the_next_start_with_x_plane_closed(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """Soft banks saved while X-Plane runs: the river masks are downloaded, and wait whole in
    their stage, by the rule of every change of a region. A start with X-Plane still open is not
    refused (the region is as the pack was set up from it) and leaves them waiting; a start with
    X-Plane closed puts them in place, recorded, and sets the region up again for them."""
    from live_helpers import NAMES, make_source, make_xplane
    from orthostudio.live import session, winsearch
    from orthostudio.live.filesystem import folder_link, is_folder_link
    from orthostudio.live.pack import live_root

    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(winsearch, "keep_out", lambda folder, log=None: None)
    files, region = rivers_world
    root = live_root()
    folder = osxpregions.mesh_dir(root, "eur")  # a region of Live's mesh, as installed
    folder.parent.mkdir(parents=True)
    make_source(tmp_path / "made", tile=(46, 2), textures=NAMES).rename(folder)
    zips, size = {z.path: z.sha256 for z in region.mesh}, total(region, "unpacked")
    (folder / aoregions.MARKER).write_text(json.dumps({"id": "eur", "size": size, "zips": zips}))
    xp = make_xplane(tmp_path)
    options = session.Options(xplane=xp, pack=tmp_path / "pack", source=folder, no_plugin=True)
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(options, log=lambda _: None)  # the pack set up from the region
    link = xp / "Custom Scenery" / "z_osxp_live"
    if not is_folder_link(link):
        folder_link(tmp_path / "pack", link)
    monkeypatch.setattr(session, "is_installed", lambda *a: True)
    monkeypatch.setattr(session, "xplane_running", lambda: True)
    inst, lines = install(root, region, Host(files), overlays=False, rivers=True, may_swap=bool)
    assert inst.progress.phase == "waiting", lines
    assert not (folder / "rivers").exists() and osxpregions.staged_whole(root, "eur")
    assert osxpregions.rivers_to_add(root, region) == []  # nothing more to download
    said: list[str] = []
    session.prepare(options, log=said.append)  # X-Plane open: not refused, the masks wait
    assert not (folder / "rivers").exists()
    monkeypatch.setattr(session, "xplane_running", lambda: False)
    session.prepare(options, log=said.append)  # X-Plane closed: in place, the region set up again
    assert (folder / MASK_A).read_bytes() == b"river a"
    assert not (folder / osxpregions.RIVERS_STAGE).exists()
    marker = marker_of(root)
    assert marker["zips"] == {**zips, **{z.path: z.sha256 for z in region.rivers}}
    assert marker["size"] == size + sum(z.unpacked for z in region.rivers)
    assert any("river masks put in place" in s for s in said)
    assert any("changed since the pack was set up" in s for s in said)


def test_soft_banks_with_a_changed_mesh_waits_for_the_update(tmp_path: Path, rivers_world) -> None:
    """A region whose mesh changed since: nothing comes alone; its update brings it whole, its
    river masks in, in one stage and one rename."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    files, region = mended_mesh(files, region, 1, {"terrain/b2.ter": b"ter B"})
    assert osxpregions.rivers_to_add(tmp_path, region) == []
    missing = osxpregions.not_installed(tmp_path, region, True)
    assert missing == [region.mesh[1], *region.rivers]
    host = Host(files)
    inst, lines = install(tmp_path, region, host, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == sorted(z.path for z in (*region.mesh, *region.rivers))
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


def test_even_again_deletes_river_masks_not_yet_in_place(tmp_path: Path, rivers_world) -> None:
    """Soft banks chosen, its river masks stopped halfway, then Even again: the half come is
    deleted, from their own stage and from an update's stage alike; the mesh an update holds stays
    and is not downloaded again."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    damaged = {**files, RIVERS_B: b"x" * region.rivers[1].size}
    install(tmp_path, region, Host(damaged), overlays=False, rivers=True)
    assert (folder / osxpregions.RIVERS_STAGE / MASK_A).exists()
    inst, lines = install(tmp_path, region, Host(files))  # even again
    assert inst.progress.phase == "done", lines
    assert not (folder / osxpregions.RIVERS_STAGE).exists()
    # an update with soft banks stopped once its first river zip was in the stage
    files, region = mended_mesh(files, region, 1, {"terrain/b2.ter": b"ter B"})
    damaged = {**files, RIVERS_B: b"x" * region.rivers[1].size}
    install(tmp_path, region, Host(damaged), rivers=True)
    stage = osxpregions.stage_dir(tmp_path, "eur")
    assert (stage / MASK_A).exists()
    host = Host(files)
    inst, lines = install(tmp_path, region, host)  # even again
    assert inst.progress.phase == "done", lines
    assert host.asked == []  # the mesh in the stage kept, no river mask wanted
    assert not (folder / "rivers").exists()
    assert set(marker_of(tmp_path)["zips"]) == {z.path for z in region.mesh}


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
    assert asked(host) == sorted(z.path for z in (*region.mesh, *region.rivers))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / MASK_A).read_bytes() == b"river A"
    assert (folder / "rivers/textures/9_9_ZL16.png").read_bytes() == b"new"
    assert not (folder / MASK_B).exists()  # only the zip of before had it
    marker = marker_of(tmp_path)
    assert all(marker["zips"][z.path] == z.sha256 for z in region.rivers)


def test_a_stop_halfway_leaves_no_half_rivers(tmp_path: Path, rivers_world) -> None:
    """The second river zip damaged on the way: the region has no rivers folder yet, nothing
    recorded, the first one kept in the stage; the next try downloads the second alone."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    damaged = {**files, RIVERS_B: b"x" * region.rivers[1].size}
    inst, lines = install(tmp_path, region, Host(damaged), overlays=False, rivers=True)
    assert inst.progress.phase == "failed" and "damaged" in inst.progress.error
    assert not (folder / "rivers").exists()
    assert (folder / osxpregions.RIVERS_STAGE / MASK_A).exists()
    assert not any(z.path in marker_of(tmp_path)["zips"] for z in region.rivers)
    assert osxpregions.to_fetch(tmp_path, region, False, rivers=True) == ([region.rivers[1]], [])
    host = Host(files)
    inst, lines = install(tmp_path, region, host, overlays=False, rivers=True)
    assert inst.progress.phase == "done", lines
    assert asked(host) == [RIVERS_B]
    assert (folder / MASK_A).exists() and (folder / MASK_B).exists()


def test_a_stopped_update_leaves_the_river_masks_of_before_whole(
    tmp_path: Path, rivers_world
) -> None:
    files, region = rivers_world
    install(tmp_path, region, Host(files), rivers=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    files, region = mended_rivers(files, region, 0, {MASK_A: b"river A"})
    files, region = mended_rivers(files, region, 1, {MASK_B: b"river B"})
    damaged = {**files, RIVERS_B: b"x" * region.rivers[1].size}
    inst, _ = install(tmp_path, region, Host(damaged), rivers=True)
    assert inst.progress.phase == "failed"
    assert (folder / MASK_A).read_bytes() == b"river a"  # those of before, both, in use
    assert (folder / MASK_B).read_bytes() == b"river b"


def test_a_stage_holding_a_river_zip_made_again_since_is_started_again(
    tmp_path: Path, rivers_world
) -> None:
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    damaged = {**files, RIVERS_B: b"x" * region.rivers[1].size}
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
    A disk without room for a zip stops the install before it, the rivers as they were, the zip
    kept for the next try."""
    small = tmp_path / "small.zip"
    small.write_bytes(zip_of("z", {"a": b"x", "b": b"x" * 4096, "c": b"x" * 4097}))
    assert osxpregions.disk_need(small) == 4 * 4096
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    needing = osxpregions.disk_need
    full = region.rivers[1].download
    monkeypatch.setattr(
        osxpregions, "disk_need", lambda p: 10**18 if p.name == full else needing(p)
    )
    inst, _ = install(tmp_path, region, Host(files), overlays=False, rivers=True)
    assert inst.progress.phase == "failed" and "GB needed on the disk" in inst.progress.error
    assert region.rivers[1].local in inst.progress.error
    assert not (folder / "rivers").exists()
    download = osxpregions.regions_dir(tmp_path) / ".download" / "osxp_eur"
    assert (download / full).exists()


def test_river_masks_in_use_wait_for_the_next_try(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """A folder a program holds open is not renamed: those of before stay as they were, the new
    ones whole in their stage, the region showing its update; the next try downloads nothing and
    only puts them in place."""
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


def test_river_masks_a_power_cut_left_aside_are_put_back(tmp_path: Path, rivers_world) -> None:
    """Those of before renamed aside, the power cut before the new ones took their place: put
    back as Live's window opens; those of before left beside the new ones are deleted."""
    files, region = rivers_world
    install(tmp_path, region, Host(files), rivers=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    (folder / "rivers").rename(folder / ".rivers.old")
    said: list[str] = []
    osxpregions.settle(tmp_path, said.append, swap=False)
    assert (folder / MASK_A).read_bytes() == b"river a"
    assert any("river masks of eur: put back" in s for s in said)
    (folder / ".rivers.old").mkdir()
    osxpregions.settle(tmp_path, said.append, swap=False)
    assert not (folder / ".rivers.old").exists() and (folder / MASK_A).exists()
    assert not [p.name for p in folder.iterdir() if p.name.startswith(".rivers.old")]


def test_river_masks_waiting_stay_through_even_and_an_update_while_x_plane_runs(
    tmp_path: Path, rivers_world
) -> None:
    """Soft banks chosen while X-Plane runs (the masks wait whole), then Even again, then the
    region made again on the server and its Update while X-Plane still runs: the masks are the
    region's, whatever the setting, and come in the update's stage with its mesh."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))  # Even
    inst, lines = install(tmp_path, region, Host(files), overlays=False, rivers=True, may_swap=bool)
    assert inst.progress.phase == "waiting", lines
    assert osxpregions.keeps_rivers(tmp_path, "eur", False)
    files, region = mended_mesh(files, region, 1, {"terrain/b2.ter": b"ter B"})
    inst, lines = install(tmp_path, region, Host(files), rivers=False, may_swap=bool)  # Even
    assert inst.progress.phase == "waiting", lines
    stage = osxpregions.stage_dir(tmp_path, "eur")
    assert (stage / MASK_A).exists() and (stage / MASK_B).exists()
    osxpregions.settle(tmp_path, lambda _: None, swap=True)  # the next start, X-Plane closed
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / MASK_A).read_bytes() == b"river a"
    assert (folder / "terrain/b2.ter").read_bytes() == b"ter B"
    assert all(marker_of(tmp_path)["zips"][z.path] == z.sha256 for z in region.rivers)


def test_river_masks_a_stop_left_unrecorded_are_not_downloaded_again(
    tmp_path: Path, rivers_world
) -> None:
    """A stop between the masks' rename and their record: their stage, whole, is kept, nothing is
    downloaded again, and the next try with Live stopped and X-Plane closed records them."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    inst, _ = install(tmp_path, region, Host(files), overlays=False, rivers=True, may_swap=bool)
    assert inst.progress.phase == "waiting"
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    (folder / osxpregions.RIVERS_STAGE / "rivers").rename(folder / "rivers")  # renamed, then cut
    for may in (bool, lambda: True):  # with X-Plane open, then closed
        host = Host(files)
        install(tmp_path, region, host, overlays=False, rivers=True, may_swap=may)
        assert host.asked == []
    assert all(marker_of(tmp_path)["zips"][z.path] == z.sha256 for z in region.rivers)
    assert not (folder / osxpregions.RIVERS_STAGE).exists()


def test_a_stage_discarded_is_never_trusted_half_deleted(
    tmp_path: Path, world, monkeypatch
) -> None:
    """A stage is discarded one way: its marker first, then set aside and deleted in the
    background; a stop in the middle never leaves one whole for a start to put in place."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    install(tmp_path, region, Host(files), may_swap=bool)  # whole, waiting
    stage = osxpregions.stage_dir(tmp_path, "eur")
    assert osxpregions.staged_whole(tmp_path, "eur")

    def cut(path: Path) -> Path:
        raise KeyboardInterrupt("the power cut")

    monkeypatch.setattr(osxpregions, "_set_aside", cut)
    with pytest.raises(KeyboardInterrupt):
        osxpregions._discard(stage)
    assert stage.exists() and not osxpregions.staged_whole(tmp_path, "eur")


def test_a_region_is_deleted_in_the_background(tmp_path: Path, world, monkeypatch) -> None:
    """Deleting a region sets it aside in one rename and deletes it in the background: the
    request does not wait for its files."""
    files, region = world
    install(tmp_path, region, Host(files))
    started: list[Path] = []
    monkeypatch.setattr(osxpregions, "_delete_aside", started.append)
    osxpregions.remove(tmp_path, "eur", [], log=lambda _: None)
    assert [p.name.rsplit(".", 1)[0] for p in started] == [".z_osxp_eur.old"]
    assert osxpregions.ASIDE.fullmatch(started[0].name) and started[0].exists()
    assert osxpregions.installed(tmp_path) == {} and not osxpregions.present(tmp_path, "eur")


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
    files = {**files, NIGHT: night}
    later = dataclasses.replace(region, night=(entry(NIGHT, night),))
    inst, lines = install(tmp_path, later, Host(files), overlays=False, night_only=True)
    assert inst.progress.phase == "done", lines
    assert not (folder / "rivers").exists() and (stage / MASK_A).exists()


def test_a_list_without_river_masks_changes_nothing(tmp_path: Path, world) -> None:
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
