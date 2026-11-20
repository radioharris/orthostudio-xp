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

from orthostudio.clean import disk_bytes
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
    monkeypatch.setattr(osxpregions, "_delete_aside", osxpregions._delete)


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


def install(
    root: Path,
    region: OsxpRegion,
    host: Host,
    started: Callable[[Installer], None] | None = None,
    **kw,
) -> tuple[Installer, list[str]]:
    """One install, Live stopped and X-Plane not running unless ``may_swap`` says otherwise;
    ``started`` told of it before it runs (the pilot's Stop, from a test)."""
    lines: list[str] = []
    client = httpx.Client(
        transport=httpx.MockTransport(host), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    kw.setdefault("may_swap", lambda: True)
    inst = Installer(root, region, client=client, host=(URL, TOKEN), log=lines.append, **kw)
    if started is not None:
        started(inst)
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
    assert inst.progress.error.endswith(": the server answered HTTP 500")
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
    assert inst.progress.phase == "failed" and inst.progress.error.startswith(
        f"not enough room on the disk of {tmp_path} ("  # Live's folder, never a hidden stage
    )
    assert inst.progress.error.endswith("GB free): free some room")
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
    inst = Installer(
        tmp_path, region, host=(URL, TOKEN), log=lambda _: None, may_swap=lambda: False
    )
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
        tmp_path,
        region,
        client=client,
        host=(URL, TOKEN),
        log=lambda _: None,
        may_swap=lambda: False,
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
        tmp_path, region, client=client, host=(URL, TOKEN), log=lines.append, may_swap=lambda: False
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
    assert osxpregions.not_installed(tmp_path, region, False) == list(region.mesh)  # Update
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
    assert "close it, then try again, which downloads nothing more" in inst.progress.error
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
    stage = osxpregions.stage_dir(root, "eur")  # an update stopped halfway, or whole and waiting
    stage.mkdir()
    (stage / "x.dsf").write_bytes(b"half")
    osxpregions.remove(root, "eur", [(45, 5), (46, 5)], log=lines.append)
    assert not os.path.lexists(folder) and not elsewhere.exists()
    assert sorted(elsewhere.parent.iterdir()) == []  # its stage too, beside it on its disk
    assert hidden(root) == [".download", ".lists"]  # the link kept for it gone too


def test_a_region_on_another_disk_whose_deletion_a_quit_stopped(
    tmp_path: Path, world, monkeypatch
) -> None:
    """A region linked to another disk, deleted, the app quitting before its files are: the link
    kept within reach of Live's regions folder leads the next opening of the window to them, its
    stage and the region itself, and it goes last."""
    files, region = world
    elsewhere = tmp_path / "other disk" / "z_osxp_eur"
    elsewhere.parent.mkdir()
    root = tmp_path / "live"
    install(root, region, Host(files))
    folder = osxpregions.mesh_dir(root, "eur")
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    stage = osxpregions.stage_dir(root, "eur")
    stage.mkdir()
    (stage / "x.dsf").write_bytes(b"half")
    monkeypatch.setattr(osxpregions, "_delete_aside", lambda *f, links=(): None)  # the quit
    osxpregions.remove(root, "eur", [], log=lambda _: None)
    assert not os.path.lexists(folder) and osxpregions.installed(root) == {}
    assert len(list(elsewhere.parent.iterdir())) == 2  # the region and its stage, set aside
    kept = [p for p in osxpregions.regions_dir(root).iterdir() if p.is_symlink()]
    assert len(kept) == 1 and osxpregions.ASIDE.fullmatch(kept[0].name)
    taken: list[tuple] = []

    def delete(*folders: Path, links=()) -> None:
        taken.append(([f.name for f in folders], [link.name for link in links]))
        osxpregions._delete(*folders, links=links)

    monkeypatch.setattr(osxpregions, "_delete_aside", delete)
    away = elsewhere.parent.rename(tmp_path / "unplugged")  # its disk away: kept for later
    assert osxpregions.take_up_deletions(root) == 0
    assert [p for p in osxpregions.regions_dir(root).iterdir() if p.is_symlink()] == kept
    away.rename(elsewhere.parent)  # back
    assert osxpregions.take_up_deletions(root) == 3  # its stage, the region, then the link
    (folders, links), *rest = taken
    assert rest == [] and len(folders) == 2 and folders[-1] == kept[0].name  # one deletion
    assert links == [kept[0].name]  # the link last
    assert sorted(elsewhere.parent.iterdir()) == []
    assert hidden(root) == [".download", ".lists"]


def test_a_region_on_another_disk_is_deleted_when_its_link_is_refused(
    tmp_path: Path, world, monkeypatch
) -> None:
    """The link kept within reach refused (a junction, on Windows): the region, set aside
    already, is deleted all the same."""
    from orthostudio.errors import OsxpError

    files, region = world
    elsewhere = tmp_path / "other disk" / "z_osxp_eur"
    elsewhere.parent.mkdir()
    root = tmp_path / "live"
    install(root, region, Host(files))
    folder = osxpregions.mesh_dir(root, "eur")
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)

    def refused(target: Path, link: Path) -> None:
        raise OsxpError("XP_LINK_FAILED")

    monkeypatch.setattr(osxpregions, "folder_link", refused)
    osxpregions.remove(root, "eur", [], log=lambda _: None)
    assert not os.path.lexists(folder) and sorted(elsewhere.parent.iterdir()) == []


def test_a_key_refused_and_a_region_deleted(tmp_path: Path, world) -> None:
    """Deleted: the region, its stage, its downloads and its overlays. A region of which a stop
    left only its stage is deleted too; one held open is not touched."""
    files, region = world
    lines: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(Host(files)))
    inst = Installer(
        tmp_path, region, client=client, host=(URL, "old"), log=lines.append, may_swap=lambda: False
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
    inst, lines = install(
        root, region, Host(files), overlays=False, rivers=True, may_swap=lambda: False
    )
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
    assert missing == [*region.mesh, *region.rivers]
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
    assert changed == [*region.mesh, *region.rivers]  # Update says them, the setting even
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
    assert (
        inst.progress.phase == "failed" and "not enough room on the disk of" in inst.progress.error
    )
    assert f"disk of {tmp_path} (" in inst.progress.error  # Live's folder, not the stage in it
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


def test_river_masks_recorded_before_a_stop_are_not_counted_twice(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """A stop between the masks' record in the marker and their stage's deletion: the next start
    finds the stage again and adds nothing more to the region's size."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    size = marker_of(tmp_path)["size"]
    inst, lines = install(
        tmp_path, region, Host(files), overlays=False, rivers=True, may_swap=lambda: False
    )
    assert inst.progress.phase == "waiting", lines
    discarding = osxpregions._discard

    def cut(path: Path, beside: Path | None = None) -> None:
        if path.name == osxpregions.RIVERS_STAGE:
            raise KeyboardInterrupt("the power cut")
        discarding(path, beside)

    monkeypatch.setattr(osxpregions, "_discard", cut)
    with pytest.raises(KeyboardInterrupt):
        osxpregions.put_rivers_in_place(tmp_path, "eur")
    monkeypatch.setattr(osxpregions, "_discard", discarding)
    rivers = sum(z.unpacked for z in region.rivers)
    assert marker_of(tmp_path)["size"] == size + rivers
    osxpregions.settle(tmp_path, lambda _: None, swap=True)
    assert marker_of(tmp_path)["size"] == size + rivers
    assert not (osxpregions.mesh_dir(tmp_path, "eur") / osxpregions.RIVERS_STAGE).exists()


def test_what_a_region_holds_is_set_aside_beside_it(
    tmp_path: Path, rivers_world, monkeypatch
) -> None:
    """River masks of before, or half come and no longer wanted, are set aside beside the region,
    never inside its folder or its stage, which a rename may take next; deleted before the install
    counts its room."""
    files, region = rivers_world
    install(tmp_path, region, Host(files))
    damaged = {**files, RIVERS_B: b"x" * region.rivers[1].size}
    install(tmp_path, region, Host(damaged), overlays=False, rivers=True)  # half come
    started: list[Path] = []
    monkeypatch.setattr(osxpregions, "_delete", lambda *f, links=(): started.extend(f))
    install(tmp_path, region, Host(files))  # Even again
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert [p.parent for p in started] == [folder.parent]
    assert started[0].name.startswith(".z_osxp_eur.rivers.new.")
    assert osxpregions.ASIDE.fullmatch(started[0].name)
    assert osxpregions.take_up_deletions(tmp_path) == 1  # within its reach


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
    inst, lines = install(
        tmp_path, region, Host(files), overlays=False, rivers=True, may_swap=lambda: False
    )
    assert inst.progress.phase == "waiting", lines
    assert osxpregions.keeps_rivers(tmp_path, "eur", False)
    files, region = mended_mesh(files, region, 1, {"terrain/b2.ter": b"ter B"})
    inst, lines = install(
        tmp_path, region, Host(files), rivers=False, may_swap=lambda: False
    )  # Even
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
    inst, _ = install(
        tmp_path, region, Host(files), overlays=False, rivers=True, may_swap=lambda: False
    )
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
    install(tmp_path, region, Host(files), may_swap=lambda: False)  # whole, waiting
    stage = osxpregions.stage_dir(tmp_path, "eur")
    assert osxpregions.staged_whole(tmp_path, "eur")

    def cut(path: Path, beside: Path | None = None) -> Path:
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
    monkeypatch.setattr(osxpregions, "_delete_aside", lambda *f, links=(): started.extend(f))
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


def test_a_stop_after_the_swap_leaves_no_record_of_the_unpacking_in_the_region(
    tmp_path: Path, world, monkeypatch
) -> None:
    """The stage keeps its record of the unpacking until it takes the region's place: a swap
    refused (a folder held open) downloads nothing more at the next try. A stop right after the
    swap carries that record into the region: the next settle drops it, a region in place never
    holding one (the campaign of 2026-11-18 found it there after a simulated power cut)."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    inst, lines = install(tmp_path, region, Host(files), may_swap=lambda: False)
    assert inst.progress.phase == "waiting", lines
    stage = osxpregions.stage_dir(tmp_path, "eur")
    rename = osxpregions._rename

    def refused(src: Path, dst: Path, what: str, again: str) -> None:
        raise osxpregions.InstallError(f"{what} is in use")

    monkeypatch.setattr(osxpregions, "_rename", refused)
    osxpregions.settle(tmp_path, lambda _: None, swap=True)  # refused: the stage waits as it was
    assert (stage / osxpregions.STEP).exists()
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])  # nothing more to download
    monkeypatch.setattr(osxpregions, "_rename", rename)
    osxpregions.settle(tmp_path, lambda _: None, swap=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert not stage.exists() and not (folder / osxpregions.STEP).exists()
    (folder / osxpregions.STEP).write_text("{}")  # a stop right after the swap
    osxpregions.settle(tmp_path, lambda _: None, swap=False)
    assert not (folder / osxpregions.STEP).exists()


def test_an_update_refused_at_a_start_says_why_until_its_next_install(
    tmp_path: Path, world, monkeypatch
) -> None:
    """A start with X-Plane closed whose swap another program refuses (Windows): the stage keeps
    why, for the region's line, never blaming X-Plane; the next install of it clears it."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = mended(files, region, 1, {"terrain/b.ter": b"ter B"})
    install(tmp_path, region, Host(files), may_swap=lambda: False)
    assert osxpregions.held(tmp_path, "eur") is None
    replace = osxpregions.replace_file

    def held_open(src: Path, dst: Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(osxpregions, "replace_file", held_open)
    osxpregions.settle(tmp_path, lambda _: None, swap=True)
    why = osxpregions.held(tmp_path, "eur")
    assert why and why.startswith("z_osxp_eur is in use by another program")
    assert "X-Plane" not in why and osxpregions.staged_whole(tmp_path, "eur")
    monkeypatch.setattr(osxpregions, "replace_file", replace)
    inst, lines = install(tmp_path, region, Host(files), may_swap=lambda: False)
    assert inst.progress.phase == "waiting", lines
    assert osxpregions.held(tmp_path, "eur") is None


def test_a_marker_naming_both_meshes_still_offers_the_update(tmp_path: Path, world) -> None:
    """A region a Live of before put back on its old mesh after an update keeps both meshes' zips
    in its marker: its Update offered all the same, whole, as its install brings it."""
    files, old = world
    install(tmp_path, old, Host(files))
    moved = {z.path: z.path.replace(RELEASE, "2026-12-01") for z in old.mesh}
    files = {moved.get(p, p): data for p, data in files.items()}
    new = dataclasses.replace(
        old, mesh=tuple(entry(moved[z.path], files[moved[z.path]]) for z in old.mesh)
    )
    marker = marker_of(tmp_path)
    both = {**marker["zips"], **{z.path: z.sha256 for z in new.mesh}}
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    (folder / aoregions.MARKER).write_text(json.dumps({**marker, "zips": both}))
    assert osxpregions.made_whole(tmp_path, new)
    assert osxpregions.not_installed(tmp_path, new, False) == list(new.mesh)
    mesh, _ = osxpregions.to_fetch(tmp_path, new, False)
    assert mesh == list(new.mesh)  # what the Update counts is what its install brings


def test_a_deletion_drops_the_overlays_record_before_their_files(
    tmp_path: Path, world, monkeypatch
) -> None:
    """A region deleted: its overlays leave the record before their files go, so that a stop in
    between leaves files no record names, downloaded again by an install of the region, never a
    record naming files that are gone (its roads and forests then missing for good; the campaign of
    2026-11-18 found that record kept after a kill in the middle of a deletion)."""
    files, region = world
    install(tmp_path, region, Host(files))
    pack = osxpregions.overlay_pack(tmp_path)
    dsf = pack / "Earth nav data/+40+000/+45+005.dsf"
    assert dsf.exists() and json.loads((pack / osxpregions.OVERLAY_RECORD).read_text())
    files_at_write: list[bool] = []
    write = osxpregions._write

    def writing(path: Path, doc: dict) -> None:
        if path.name == osxpregions.OVERLAY_RECORD:
            files_at_write.append(dsf.exists())
        write(path, doc)

    monkeypatch.setattr(osxpregions, "_write", writing)
    osxpregions.remove(tmp_path, "eur", [(45, 5)], log=lambda _: None)
    assert files_at_write == [True] and not dsf.exists()
    assert json.loads((pack / osxpregions.OVERLAY_RECORD).read_text()) == {}


def test_what_went_wrong_is_said_in_plain_words_without_the_host(tmp_path: Path, world) -> None:
    """The server's answer, the server out of reach, or the disk: one wording each, never the
    host's address; a list that did not come says its cause alone, the window saying what."""
    request = httpx.Request("GET", f"{URL}/x.zip")
    status = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(404, request=request)
    )
    assert osxpregions._why(status) == "the server answered HTTP 404"
    assert "could not be reached" in osxpregions._why(
        httpx.ConnectError("refused", request=request)
    )
    assert osxpregions._why(OSError(28, "No space left on device")) == (
        "the disk refused it (No space left on device)"
    )

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(down))
    with pytest.raises(osxpregions.CatalogError) as e:
        osxpregions.catalog(tmp_path, client=client, host=(URL, "key"))
    assert str(e.value).startswith("the server could not be reached") and URL not in str(e.value)
    for body in (b'{"regions": {"eur"', b'{"regions": {"eur": {"mesh": [{"path": "a"}]}}}'):
        cut = httpx.Client(
            transport=httpx.MockTransport(lambda r, b=body: httpx.Response(200, content=b))
        )
        with pytest.raises(osxpregions.CatalogError) as e:
            osxpregions.catalog(tmp_path, client=cut, host=(URL, "key"))
        assert str(e.value) == osxpregions.DAMAGED_LIST  # never an error's name


# --- the mesh of before kept beside the new one -----------------------------------------------


def kept_of(root: Path) -> Path:
    return osxpregions.kept_dir(root, "eur")


def updated(files: dict, region: OsxpRegion, tag: bytes):
    """The world with its second mesh zip made again: a new release of the region's mesh."""
    return mended(
        files, region, 1, {"Earth nav data/+40+000/+46+005.dsf": tag * 400, "terrain/b.ter": tag}
    )


def test_an_update_keeps_the_mesh_of_before_where_the_pilot_said_so(tmp_path: Path, world) -> None:
    """Kept: at the swap the region of before is renamed beside the region, its marker, its files
    and its dates as they were; never a copy. A newer update kept replaces it, one per region; one
    not kept deletes the region of before as before and leaves the kept one alone."""
    files, region = world
    install(tmp_path, region, Host(files))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    before = marker_of(tmp_path)
    nav = folder / "Earth nav data"
    dated = os.stat(nav).st_mtime_ns
    inode = os.stat(folder / "terrain/b.ter").st_ino
    files, region = updated(files, region, b"second")
    inst, lines = install(tmp_path, region, Host(files), keep_old=True)
    assert inst.progress.phase == "done", lines
    kept = kept_of(tmp_path)
    assert kept.name == ".z_osxp_eur.kept" and kept.parent == folder.parent
    assert osxpregions.kept_mesh(tmp_path, "eur") == kept
    assert json.loads((kept / aoregions.MARKER).read_text()) == before
    assert (kept / "terrain/b.ter").read_bytes() == b"ter b"
    assert os.stat(kept / "terrain/b.ter").st_ino == inode  # renamed, not copied
    assert os.stat(kept / "Earth nav data").st_mtime_ns == dated  # its dates of before
    assert (folder / "terrain/b.ter").read_bytes() == b"second"
    assert not (folder / osxpregions.STEP).exists() and not (kept / osxpregions.STEP).exists()
    assert hidden(tmp_path) == [".download", ".lists", ".z_osxp_eur.kept"]
    assert osxpregions.installed(tmp_path) == {"eur": marker_of(tmp_path)}  # never a region
    assert any("kept beside it" in line for line in lines)
    # a newer one kept: it replaces the one kept before
    files, region = updated(files, region, b"third")
    install(tmp_path, region, Host(files), keep_old=True)
    assert (kept / "terrain/b.ter").read_bytes() == b"second"
    assert (folder / "terrain/b.ter").read_bytes() == b"third"
    assert hidden(tmp_path) == [".download", ".lists", ".z_osxp_eur.kept"]
    # one not kept: the region of before goes, the one kept stays as it was
    files, region = updated(files, region, b"fourth")
    inst, lines = install(tmp_path, region, Host(files), keep_old=False)
    assert inst.progress.phase == "done", lines
    assert (kept / "terrain/b.ter").read_bytes() == b"second"
    assert (folder / "terrain/b.ter").read_bytes() == b"fourth"
    assert hidden(tmp_path) == [".download", ".lists", ".z_osxp_eur.kept"]


def test_an_update_not_kept_leaves_nothing_of_before(tmp_path: Path, world) -> None:
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    inst, lines = install(tmp_path, region, Host(files))  # the default: not kept
    assert inst.progress.phase == "done", lines
    assert osxpregions.kept_mesh(tmp_path, "eur") is None and not kept_of(tmp_path).exists()
    assert hidden(tmp_path) == [".download", ".lists"]


def test_an_update_put_in_place_at_a_later_start_honours_the_last_answer(
    tmp_path: Path, world
) -> None:
    """X-Plane open: the update waits whole, the answer in its stage's record. Clicked again with
    another answer, nothing is downloaded and the record says the new one; Live's next start with
    X-Plane closed keeps the region of before or not, as said last."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    inst, _ = install(tmp_path, region, Host(files), keep_old=True, may_swap=lambda: False)
    assert inst.progress.phase == "waiting"
    stage = osxpregions.stage_dir(tmp_path, "eur")
    assert json.loads((stage / osxpregions.STEP).read_text())["keep_old"] is True
    host = Host(files)
    inst, _ = install(tmp_path, region, host, keep_old=False, may_swap=lambda: False)
    assert inst.progress.phase == "waiting" and host.asked == []
    assert json.loads((stage / osxpregions.STEP).read_text())["keep_old"] is False
    osxpregions.settle(tmp_path, lambda _: None, swap=True)  # Live's start, X-Plane closed
    assert marker_of(tmp_path)["zips"] == {z.path: z.sha256 for z in region.mesh}
    assert not kept_of(tmp_path).exists()
    # and kept: the next update waits, said kept, and the start keeps the region of before
    files, region = updated(files, region, b"third")
    install(tmp_path, region, Host(files), keep_old=True, may_swap=lambda: False)
    said: list[str] = []
    osxpregions.settle(tmp_path, said.append, swap=True)
    assert (kept_of(tmp_path) / "terrain/b.ter").read_bytes() == b"second"
    assert any("kept beside it" in s for s in said)
    assert hidden(tmp_path) == [".download", ".lists", ".z_osxp_eur.kept"]


def test_a_power_cut_between_the_renames_of_an_update_kept_is_put_right(
    tmp_path: Path, world, monkeypatch
) -> None:
    """Kept, the power cut at each rename: before the stage took the region's place, the region
    of before is put back and the update waits whole with its answer; after it, before the region
    of before was renamed as the one kept, the next settle keeps it as the record says, never
    deletes it."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    stage = osxpregions.stage_dir(tmp_path, "eur")
    old = folder.with_name(".z_osxp_eur.old")
    renaming = osxpregions.replace_file

    def cut_at(which: Path):
        def cut(src, dst):
            if Path(src) == which:
                raise KeyboardInterrupt("the power cut")
            renaming(src, dst)

        return cut

    monkeypatch.setattr(osxpregions, "replace_file", cut_at(stage))  # before the stage's rename
    with pytest.raises(KeyboardInterrupt):
        install(tmp_path, region, Host(files), keep_old=True)
    monkeypatch.setattr(osxpregions, "replace_file", renaming)
    assert not folder.exists() and old.exists()
    osxpregions.settle(tmp_path, lambda _: None, swap=False)  # the window opens
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b" and not old.exists()
    assert osxpregions.staged_whole(tmp_path, "eur")
    monkeypatch.setattr(osxpregions, "replace_file", cut_at(old))  # after it, before the keeping
    with pytest.raises(KeyboardInterrupt):
        osxpregions.settle(tmp_path, lambda _: None, swap=True)
    monkeypatch.setattr(osxpregions, "replace_file", renaming)
    assert (folder / "terrain/b.ter").read_bytes() == b"second" and old.exists()
    assert json.loads((folder / osxpregions.STEP).read_text())["keep_old"] is True
    said: list[str] = []
    osxpregions.settle(tmp_path, said.append, swap=False)  # the window opens again
    assert (kept_of(tmp_path) / "terrain/b.ter").read_bytes() == b"ter b"
    assert not old.exists() and not (folder / osxpregions.STEP).exists()
    assert any("kept beside it" in s for s in said)
    assert hidden(tmp_path) == [".download", ".lists", ".z_osxp_eur.kept"]


def test_a_kept_mesh_never_changes(tmp_path: Path, world) -> None:
    """Never compared to the list, never updated, never downloaded again: the region's Update
    counts its own marker alone, and a start, an install, the window's take-up of deletions leave
    the kept mesh as it is."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    kept = kept_of(tmp_path)
    marker = (kept / aoregions.MARKER).read_text()
    assert osxpregions.to_fetch(tmp_path, region, True) == ([], [])  # up to date: no Update
    assert osxpregions.not_installed(tmp_path, region, False) == []
    assert not osxpregions.made_whole(tmp_path, region)
    host = Host(files)
    inst, lines = install(tmp_path, region, host)
    assert inst.progress.phase == "done" and host.asked == [], lines
    osxpregions.settle(tmp_path, lambda _: None, swap=True)
    assert osxpregions.take_up_deletions(tmp_path) == 0
    assert (kept / aoregions.MARKER).read_text() == marker
    assert (kept / "terrain/b.ter").read_bytes() == b"ter b"
    files, region = updated(files, region, b"third")  # a newer list: the region's Update only
    assert osxpregions.not_installed(tmp_path, region, False) == list(region.mesh)  # whole
    assert (kept / aoregions.MARKER).read_text() == marker


def test_the_kept_mesh_deleted_alone_keeps_the_shared_overlays(tmp_path: Path, world) -> None:
    """The kept mesh's Delete: the kept mesh alone, set aside then deleted; the region, its
    overlays and their record stay, which its new mesh has too."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    pack = osxpregions.overlay_pack(tmp_path)
    record = (pack / osxpregions.OVERLAY_RECORD).read_text()
    lines: list[str] = []
    osxpregions.remove_kept(tmp_path, "eur", log=lines.append)
    assert lines == ["the old mesh of OrthoStudio XP's region eur deleted"]
    assert not kept_of(tmp_path).exists() and osxpregions.kept_mesh(tmp_path, "eur") is None
    assert (pack / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"roads a"
    assert (pack / osxpregions.OVERLAY_RECORD).read_text() == record
    assert (osxpregions.mesh_dir(tmp_path, "eur") / "terrain/b.ter").read_bytes() == b"second"
    assert hidden(tmp_path) == [".download", ".lists"]
    osxpregions.remove_kept(tmp_path, "eur")  # none left: nothing done
    assert (osxpregions.mesh_dir(tmp_path, "eur") / "terrain/b.ter").read_bytes() == b"second"


def test_the_region_s_delete_puts_its_kept_mesh_back_in_its_place(tmp_path: Path, world) -> None:
    """The region's Delete while it keeps an old mesh: the reverse of the update that kept it.
    The new mesh is deleted, the old one renamed into the region's place, its marker with it, so
    that its Update is offered again; dated now, the next start sets Live's pack up again from
    it. The overlays stay, which both meshes have. Delete again: the region goes whole, as any
    region keeping no old mesh."""
    files, region = world
    install(tmp_path, region, Host(files))
    before = marker_of(tmp_path)
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    kept = kept_of(tmp_path)
    inode = os.stat(kept / "terrain/b.ter").st_ino
    os.utime(kept / "Earth nav data", ns=(10**9, 10**9))  # its dates of before
    pack = osxpregions.overlay_pack(tmp_path)
    record = (pack / osxpregions.OVERLAY_RECORD).read_text()
    lists = aoregions.lists_dir(tmp_path)
    from orthostudio.live.names import region_list_file

    for listed in (folder, kept):
        region_list_file(lists, listed).write_text("{}")
    lines: list[str] = []
    osxpregions.remove(tmp_path, "eur", [(45, 5), (46, 5)], log=lines.append)
    assert lines == [
        "the new mesh of OrthoStudio XP's region eur deleted, its old mesh back in its place"
    ]
    assert marker_of(tmp_path) == before and osxpregions.installed(tmp_path) == {"eur": before}
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"
    assert os.stat(folder / "terrain/b.ter").st_ino == inode  # renamed, not copied
    assert os.stat(folder / "Earth nav data").st_mtime_ns > 10**9  # the pack set up again
    assert osxpregions.kept_mesh(tmp_path, "eur") is None and not kept.exists()
    assert not osxpregions.keeps_any_old(tmp_path)  # the Settings' Mesh then back to New
    assert hidden(tmp_path) == [".download", ".lists"]
    assert (pack / "Earth nav data/+40+000/+45+005.dsf").read_bytes() == b"roads a"
    assert (pack / osxpregions.OVERLAY_RECORD).read_text() == record
    assert not any(region_list_file(lists, f).exists() for f in (folder, kept))
    assert osxpregions.made_whole(tmp_path, region)  # its Update offered again, whole
    assert osxpregions.not_installed(tmp_path, region, False) == list(region.mesh)
    osxpregions.remove(tmp_path, "eur", [(45, 5), (46, 5)], log=lines.append)
    assert not osxpregions.present(tmp_path, "eur")
    assert not (pack / "Earth nav data/+40+000/+45+005.dsf").exists()
    assert hidden(tmp_path) == [".download", ".lists"]


def test_the_region_s_delete_takes_its_waiting_update_with_its_new_mesh(
    tmp_path: Path, world
) -> None:
    """An update waiting whole (X-Plane was open) when the region's Delete puts its kept mesh
    back: the update goes with the new mesh, never put in place at a later start over the old one
    with its answer (« Don't keep » would delete it for good)."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    files, region = updated(files, region, b"third")
    inst, lines = install(tmp_path, region, Host(files), keep_old=False, may_swap=lambda: False)
    assert inst.progress.phase == "waiting", lines
    assert osxpregions.staged_whole(tmp_path, "eur")
    osxpregions.remove(tmp_path, "eur", [], log=lambda _: None)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"
    assert not osxpregions.stage_dir(tmp_path, "eur").exists()
    assert not osxpregions.download_dir(tmp_path, "eur").exists()
    osxpregions.settle(tmp_path, lambda _: None, swap=True)  # Live's next start
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"
    assert hidden(tmp_path) == [".download", ".lists"]


def test_a_power_cut_while_the_region_s_delete_puts_its_kept_mesh_back(
    tmp_path: Path, world, monkeypatch
) -> None:
    """The power cut at each rename of the swap reversed, as an update's: before the kept mesh
    took the region's place, the next opening puts the new mesh back, the old one still kept;
    after it, the next opening deletes the new mesh."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    kept = kept_of(tmp_path)
    old = folder.with_name(".z_osxp_eur.old")
    renaming = osxpregions.replace_file

    def cut_at(which: Path):
        def cut(src, dst):
            if Path(src) == which:
                raise KeyboardInterrupt("the power cut")
            renaming(src, dst)

        return cut

    monkeypatch.setattr(osxpregions, "replace_file", cut_at(kept))  # before the kept's rename
    with pytest.raises(KeyboardInterrupt):
        osxpregions.remove(tmp_path, "eur", [], log=lambda _: None)
    monkeypatch.setattr(osxpregions, "replace_file", renaming)
    assert not folder.exists() and old.exists() and kept.exists()
    osxpregions.settle(tmp_path, lambda _: None, swap=False)  # the window opens
    assert (folder / "terrain/b.ter").read_bytes() == b"second" and not old.exists()
    assert (kept / "terrain/b.ter").read_bytes() == b"ter b"
    monkeypatch.setattr(osxpregions, "replace_file", cut_at(old))  # after it
    with pytest.raises(KeyboardInterrupt):
        osxpregions.remove(tmp_path, "eur", [], log=lambda _: None)
    monkeypatch.setattr(osxpregions, "replace_file", renaming)
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b" and old.exists()
    assert not kept.exists()
    osxpregions.settle(tmp_path, lambda _: None, swap=False)  # the window opens again
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b" and not old.exists()
    assert osxpregions.kept_mesh(tmp_path, "eur") is None
    assert hidden(tmp_path) == [".download", ".lists"]


def test_a_region_held_open_keeps_its_two_meshes(tmp_path: Path, world, monkeypatch) -> None:
    """Windows: a rename of the swap reversed refused, a folder held open: the region's Delete
    says so, and both meshes stay as they were, the new one put back where it was renamed aside
    already, and an update waiting stays ready."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    files, region = updated(files, region, b"third")
    inst, lines = install(tmp_path, region, Host(files), may_swap=lambda: False)
    assert inst.progress.phase == "waiting", lines
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    kept = kept_of(tmp_path)
    renaming = osxpregions.replace_file
    for which in (folder, kept):  # the new mesh held, then the old one

        def held(src, dst, which=which):
            if Path(src) == which:
                raise PermissionError(32, "in use by another process")
            renaming(src, dst)

        monkeypatch.setattr(osxpregions, "replace_file", held)
        with pytest.raises(osxpregions.InstallError, match="close it, then delete it again"):
            osxpregions.remove(tmp_path, "eur", [], log=lambda _: None)
        assert (folder / "terrain/b.ter").read_bytes() == b"second"
        assert (kept / "terrain/b.ter").read_bytes() == b"ter b"
        assert osxpregions.staged_whole(tmp_path, "eur")  # Update ready still


def test_a_kept_mesh_whose_deletion_a_quit_stopped_is_deleted_at_the_next_opening(
    tmp_path: Path, world, monkeypatch
) -> None:
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    monkeypatch.setattr(osxpregions, "_delete_aside", lambda *f, links=(): None)  # the quit
    osxpregions.remove_kept(tmp_path, "eur")
    aside = [p for p in osxpregions.regions_dir(tmp_path).iterdir() if ".kept." in p.name]
    assert len(aside) == 1 and osxpregions.ASIDE.fullmatch(aside[0].name)
    assert osxpregions.kept_mesh(tmp_path, "eur") is None  # never taken for a kept one
    monkeypatch.setattr(osxpregions, "_delete_aside", osxpregions._delete)
    assert osxpregions.take_up_deletions(tmp_path) == 1
    assert hidden(tmp_path) == [".download", ".lists"]


def test_a_region_on_another_disk_keeps_its_mesh_of_before_on_that_disk(
    tmp_path: Path, world
) -> None:
    files, region = world
    elsewhere = tmp_path / "other disk" / "z_osxp_eur"
    elsewhere.parent.mkdir()
    root = tmp_path / "live"
    install(root, region, Host(files))
    folder = osxpregions.mesh_dir(root, "eur")
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    files, region = updated(files, region, b"second")
    inst, lines = install(root, region, Host(files), keep_old=True)
    assert inst.progress.phase == "done", lines
    assert kept_of(root) == elsewhere.with_name(".z_osxp_eur.kept")
    assert (kept_of(root) / "terrain/b.ter").read_bytes() == b"ter b"
    assert sorted(p.name for p in elsewhere.parent.iterdir()) == [".z_osxp_eur.kept", "z_osxp_eur"]
    assert hidden(root) == [".download", ".lists"]
    # the region's Delete: the kept mesh back in its place on that disk, the link leading to it
    osxpregions.remove(root, "eur", [], log=lambda _: None)
    assert folder.is_symlink() and folder.resolve() == elsewhere.resolve()
    assert (elsewhere / "terrain/b.ter").read_bytes() == b"ter b"
    assert sorted(p.name for p in elsewhere.parent.iterdir()) == ["z_osxp_eur"]
    assert hidden(root) == [".download", ".lists"]


# --- no room for an update beside the region --------------------------------------------------


@pytest.fixture
def short_disk(monkeypatch):
    """A disk ``by`` bytes short of an update's room (one by default), where each folder or file
    named, deleted (or made again under its name), frees what the test says it holds: a file its
    size, a mesh or a stage the size its marker gave, ``(folder, bytes)`` those bytes (0 for a
    kept mesh the pack holds still, its files shared). No rule of its own on what is shared: each
    test says it."""
    real = shutil.disk_usage
    monkeypatch.setattr(osxpregions, "SPARE_BYTES", 0)
    state: dict = {"base": None, "freeable": []}

    def usage(path):
        got = real(path)
        if state["base"] is None:
            return got
        free = state["base"] + sum(n for folder, n, ino in state["freeable"] if gone(folder, ino))
        return type(got)(got.total, got.total - free, free)

    def gone(path: Path, ino: int) -> bool:
        try:
            return path.stat().st_ino != ino  # made again since: a new folder
        except OSError:
            return True

    monkeypatch.setattr(osxpregions.shutil, "disk_usage", usage)

    def short(root: Path, region: OsxpRegion, *folders: Path | tuple, by: int = 1) -> None:
        state["base"], state["freeable"] = 0, []  # the disk empty: what the install needs
        mesh, ovl = osxpregions.to_fetch(root, region, True)
        stage = osxpregions.stage_dir(root, region.id)
        download = osxpregions.download_dir(root, region.id)
        pack = osxpregions.overlay_pack(root)
        (need,) = (
            r.need for r in osxpregions._short_of_room(mesh, ovl, stage, pack, download, True)
        )

        def holds(f: Path | tuple) -> tuple[Path, int]:
            if isinstance(f, tuple):
                return f
            if f.is_file():
                return f, f.stat().st_size
            return f, json.loads((f / aoregions.MARKER).read_text())["size"]

        state["freeable"] = [(f, n, f.stat().st_ino) for f, n in map(holds, folders)]
        state["base"] = need - by

    return short


def pack_made_of(pack: Path, *sources: Path) -> Path:
    """Live's pack, its record saying it is made from these regions as they are now, their
    fingerprints with them, as a start sets it up (``region.setup_region``)."""
    from orthostudio.live.region import REGION_MARKER, _fingerprints

    (pack / REGION_MARKER).parent.mkdir(parents=True, exist_ok=True)
    record = {"sources": [str(s) for s in sources], "fingerprints": _fingerprints(sources)}
    (pack / REGION_MARKER).write_text(json.dumps(record))
    return pack


def test_no_room_beside_the_region_says_it_and_deletes_nothing(
    tmp_path: Path, world, short_disk
) -> None:
    """The window's word before an update (update_room), by the install's own count: the room it
    needs and what is free; no kept mesh to delete, nothing offered. The install, told to make room
    all the same, checks again, deletes nothing, and stops on its words, the region as it was:
    never deleted to make room."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert osxpregions.update_room(tmp_path, region, True, False) is None  # room enough
    short_disk(tmp_path, region, folder)
    room = osxpregions.update_room(tmp_path, region, True, False)
    assert room == {
        "need": room["need"],
        "free": room["need"] - 1,
        "kept": False,
        "shared": False,  # no kept mesh at all
        "live": False,  # the region's own disk
        "cleared": False,  # nothing an update stopped earlier left
    }
    for make_room in (False, True):
        host = Host(files)
        inst, _ = install(tmp_path, region, host, make_room=make_room)
        assert (
            inst.progress.phase == "failed"
            and "not enough room on the disk of" in inst.progress.error
        )
        assert host.asked == [] and (folder / "terrain/b.ter").read_bytes() == b"ter b"


def test_no_room_the_kept_mesh_deleted_first_and_the_install_runs_with_no_gap(
    tmp_path: Path, world, short_disk
) -> None:
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    files, region = updated(files, region, b"third")
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    short_disk(tmp_path, region, kept_of(tmp_path), folder)
    room = osxpregions.update_room(tmp_path, region, True, False)
    assert room is not None and room["kept"] is True
    inst, _ = install(tmp_path, region, Host(files), make_room=True, may_swap=lambda: False)
    assert inst.progress.phase == "failed" and "X-Plane is running" in inst.progress.error
    assert kept_of(tmp_path).exists()  # never under a running X-Plane
    seen: list[bool] = []
    host = Host(files, hook=lambda key: seen.append((folder / aoregions.MARKER).is_file()))
    inst, lines = install(tmp_path, region, host, make_room=True)
    assert inst.progress.phase == "done", lines
    assert seen and all(seen)  # the region of before in place all along: no gap
    assert "the old mesh of OrthoStudio XP's region eur deleted" in lines
    assert not kept_of(tmp_path).exists()
    assert (folder / "terrain/b.ter").read_bytes() == b"third"
    assert hidden(tmp_path) == [".download", ".lists"]


def test_no_room_a_kept_mesh_that_frees_too_little_or_nothing_is_never_deleted(
    tmp_path: Path, world, short_disk
) -> None:
    """A kept mesh whose deletion would leave the disk short, or that frees nothing (Live's pack
    made from it: its DSFs and masks stay there as clones or hard links), is not offered, and the
    install told to make room all the same deletes nothing: it could not be downloaded again."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    files, region = updated(files, region, b"third")
    folder, kept = osxpregions.mesh_dir(tmp_path, "eur"), kept_of(tmp_path)
    pack_made_of(tmp_path / "pack", folder)  # the last start flew the new mesh: kept unshared
    short_disk(tmp_path, region, kept)
    assert osxpregions.update_room(tmp_path, region, True, False)["kept"] is True
    big = json.loads((kept / aoregions.MARKER).read_text())["size"] + 1
    short_disk(tmp_path, region, kept, by=big)  # too little
    assert osxpregions.update_room(tmp_path, region, True, False)["kept"] is False
    inst, _ = install(tmp_path, region, Host(files), make_room=True)
    assert inst.progress.phase == "failed" and kept.exists()
    pack_made_of(tmp_path / "pack", kept)  # the last start flew the kept mesh: frees nothing
    short_disk(tmp_path, region, (kept, 0))
    room = osxpregions.update_room(tmp_path, region, True, False)
    assert room["kept"] is False and room["shared"] is True  # its room after the next start
    inst, _ = install(tmp_path, region, Host(files), make_room=True)
    assert inst.progress.phase == "failed" and kept.exists()
    assert (folder / "terrain/b.ter").read_bytes() == b"second"


def test_no_room_on_two_disks_the_kept_mesh_is_deleted_only_if_it_makes_both_enough(
    tmp_path: Path, world, monkeypatch
) -> None:
    """A region on another disk, linked from Live's folder, its kept mesh beside it there: that
    disk short, and Live's own disk too (the downloads, the overlays). Deleting the kept mesh frees
    the first one only: never offered nor deleted, the install stopping on its words; Live's disk
    with room, it is (the reviews of 2026-11-18)."""
    files, region = world
    other = tmp_path / "other disk"
    elsewhere = other / "z_osxp_eur"
    other.mkdir()
    root = tmp_path / "live"
    install(root, region, Host(files))
    folder = osxpregions.mesh_dir(root, "eur")
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    files, region = updated(files, region, b"second")
    install(root, region, Host(files), keep_old=True)
    files, region = updated(files, region, b"third")
    kept = kept_of(root)
    assert kept.parent == other

    def disk(path) -> int:
        p = Path(path)
        return 2 if (p == other or other in p.parents) else 1

    monkeypatch.setattr(osxpregions, "_disk_of", disk)
    monkeypatch.setattr(osxpregions, "SPARE_BYTES", 0)
    real = shutil.disk_usage
    lives_free = [1]

    def usage(path):
        got = real(path)
        # the kept mesh gone: what the disk says then, more than its marker (each file a block)
        free = (1 if kept.exists() else 10**9) if disk(path) == 2 else lives_free[0]
        return type(got)(got.total, got.total - free, free)

    monkeypatch.setattr(osxpregions.shutil, "disk_usage", usage)
    room = osxpregions.update_room(root, region, True, False)
    assert room is not None and room["kept"] is False  # Live's disk would stay short
    assert room["live"] is False  # the region's disk, short too, said first
    other_free = usage  # Live's folder's disk alone short: said as Live's

    def lives_alone(path):
        got = real(path)
        if disk(path) == 2:
            return type(got)(got.total, 0, 10**12)
        return other_free(path)

    monkeypatch.setattr(osxpregions.shutil, "disk_usage", lives_alone)
    room = osxpregions.update_room(root, region, True, False)
    assert room is not None and room["live"] is True and room["kept"] is False
    monkeypatch.setattr(osxpregions.shutil, "disk_usage", usage)
    inst, _ = install(root, region, Host(files), make_room=True)
    assert inst.progress.phase == "failed" and "GB needed" in inst.progress.error
    assert kept.exists()  # never deleted for nothing: it cannot be downloaded again
    lives_free[0] = 10**12
    room = osxpregions.update_room(root, region, True, False)
    assert room is not None and room["kept"] is True and room["live"] is False
    inst, lines = install(root, region, Host(files), make_room=True)
    assert inst.progress.phase == "done", lines
    assert not kept.exists() and (elsewhere / "terrain/b.ter").read_bytes() == b"third"


def test_stale_downloads_count_as_room_for_the_window_as_for_the_install(
    tmp_path: Path, world, short_disk
) -> None:
    """Parts of zips made again since, in the region's download folder, which the install deletes
    before it counts its room: the window counts them freed too, and asks its keep question rather
    than say there is no room; the install then finds it."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    stale = osxpregions.download_dir(tmp_path, "eur") / "0000000000000000-z_osxp_eur_+46+005.zip"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x" * 50_000)  # each unpacked file then counted a whole block: room for it
    short_disk(tmp_path, region, stale, by=40_000)
    assert osxpregions.update_room(tmp_path, region, True, False) is None
    inst, lines = install(tmp_path, region, Host(files))
    assert inst.progress.phase == "done", lines
    assert not stale.exists()


def test_a_kept_mesh_replaced_stays_until_the_new_one_has_its_place(
    tmp_path: Path, world, monkeypatch
) -> None:
    """Kept again, the rename of the region of before into the kept mesh refused a moment
    (Windows): the mesh kept until then stays in its place, never deleted first; the next settle
    renames the waiting one, which then replaces it."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    files, region = updated(files, region, b"third")
    kept = kept_of(tmp_path)
    renaming = osxpregions.replace_file

    def held(src, dst):
        if Path(dst) == kept and Path(src).name == ".z_osxp_eur.old":
            raise PermissionError(32, "in use by another process")
        renaming(src, dst)

    monkeypatch.setattr(osxpregions, "replace_file", held)
    inst, lines = install(tmp_path, region, Host(files), keep_old=True)
    assert inst.progress.phase == "done", lines
    assert (kept / "terrain/b.ter").read_bytes() == b"ter b"  # the one kept before, still there
    assert osxpregions.keeps_old(tmp_path, "eur")
    monkeypatch.setattr(osxpregions, "replace_file", renaming)
    osxpregions.settle(tmp_path, lambda _: None, swap=False)
    assert (kept / "terrain/b.ter").read_bytes() == b"second"
    assert hidden(tmp_path) == [".download", ".lists", ".z_osxp_eur.kept"]


def test_room_is_made_only_for_an_update_made_whole(tmp_path: Path, world, short_disk) -> None:
    """Told to make room, an install that only adds to the region (its mesh the list's) deletes
    nothing."""
    files, region = world
    install(tmp_path, region, Host(files))
    roads = zip_of("y_osxp_overlays", {"Earth nav data/+40+000/+45+005.dsf": b"roads A"})
    files = {**files, OVERLAY_A: roads}
    region = dataclasses.replace(region, overlays=(entry(OVERLAY_A, roads),))
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert not osxpregions.made_whole(tmp_path, region)
    short_disk(tmp_path, region, folder, by=10**6)
    inst, _ = install(tmp_path, region, Host(files), make_room=True)
    assert inst.progress.phase == "failed" and "GB needed" in inst.progress.error
    assert (folder / "terrain/b.ter").read_bytes() == b"ter b"


# --- the last answer wins ---------------------------------------------------------------------


@pytest.mark.parametrize("first, last", [(False, True), (True, False)])
def test_update_clicked_again_with_x_plane_closed_takes_the_last_answer(
    tmp_path: Path, world, first: bool, last: bool
) -> None:
    """Update with X-Plane open, one answer: the update waits whole. X-Plane closed, Update
    again and the other answer: the stage takes its place with that one, never the one of
    before (« Don't keep » first, then « Keep », lost the mesh for good: the reviews of
    2026-11-18)."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    inst, _ = install(tmp_path, region, Host(files), keep_old=first, may_swap=lambda: False)
    assert inst.progress.phase == "waiting"
    host = Host(files)
    inst, lines = install(tmp_path, region, host, keep_old=last, may_swap=lambda: True)
    assert inst.progress.phase == "done" and host.asked == [], lines
    assert (osxpregions.mesh_dir(tmp_path, "eur") / "terrain/b.ter").read_bytes() == b"second"
    if last:
        assert (kept_of(tmp_path) / "terrain/b.ter").read_bytes() == b"ter b"
    else:
        assert osxpregions.kept_mesh(tmp_path, "eur") is None


def test_a_newer_release_over_a_waiting_update_keeps_the_mesh_flown(tmp_path: Path, world) -> None:
    """A flown, B waiting whole (X-Plane open). C comes on the list; X-Plane closed, Update and
    Keep: B, no longer the list's, is never put in place, and A, the mesh flown, is the one
    kept, C in place (one swap, not two)."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    inst, _ = install(tmp_path, region, Host(files), keep_old=True, may_swap=lambda: False)
    assert inst.progress.phase == "waiting"
    files, region = updated(files, region, b"third")
    inst, lines = install(tmp_path, region, Host(files), keep_old=True, may_swap=lambda: True)
    assert inst.progress.phase == "done", lines
    assert (kept_of(tmp_path) / "terrain/b.ter").read_bytes() == b"ter b"
    assert (osxpregions.mesh_dir(tmp_path, "eur") / "terrain/b.ter").read_bytes() == b"third"
    assert hidden(tmp_path) == [".download", ".lists", ".z_osxp_eur.kept"]


def test_a_swap_keeps_a_mesh_an_earlier_one_could_not_rename_yet(tmp_path: Path) -> None:
    """The region of before an earlier swap left beside the region, its rename into the kept
    mesh refused a moment (Windows): the next swap keeps it as its record says, then honours its
    own answer, never deleting the mesh to keep."""
    regions = tmp_path / "regions"
    real, old, new = (regions / n for n in ("z_osxp_eur", ".z_osxp_eur.old", ".z_osxp_eur.new"))
    for folder, tag in ((real, b"B"), (old, b"A"), (new, b"C")):
        folder.mkdir(parents=True)
        (folder / "x.dsf").write_bytes(tag)
    (real / osxpregions.STEP).write_text(json.dumps({"keep_old": True}))  # B's answer: keep A
    (new / osxpregions.STEP).write_text(json.dumps({"keep_old": False}))  # C's: B not kept
    assert osxpregions._swap_in(new, real, "eur") is False
    assert (real / "x.dsf").read_bytes() == b"C"
    assert (regions / ".z_osxp_eur.kept" / "x.dsf").read_bytes() == b"A"
    assert not old.exists() and not (real / osxpregions.STEP).exists()


def test_a_stale_stage_counts_as_room_for_the_window_as_for_the_install(
    tmp_path: Path, world, short_disk
) -> None:
    """Release B waiting whole (X-Plane open), C listed since: B's stage, no longer the list's, is
    deleted by the install before it counts its room, at once, so the window counts it freed too:
    no « no room » for ever, no kept mesh offered for deletion when deleting B was enough (the
    reviews of 2026-11-18)."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)  # A kept
    files, region = updated(files, region, b"third")
    inst, _ = install(tmp_path, region, Host(files), may_swap=lambda: False)
    assert inst.progress.phase == "waiting"
    stage = osxpregions.stage_dir(tmp_path, "eur")
    files, region = updated(files, region, b"fourth")  # D listed: the stage is stale
    assert osxpregions._staged(tmp_path, region)["zips"] == {}
    short_disk(tmp_path, region, stage, by=10**9)  # short however much the stage frees
    room = osxpregions.update_room(tmp_path, region, True, False)
    assert room is not None and room["cleared"] is True  # the window says what it counts
    short_disk(tmp_path, region, stage)  # one byte short while the stage is there
    assert osxpregions.update_room(tmp_path, region, True, False) is None  # the keep question
    inst, lines = install(tmp_path, region, Host(files), make_room=True)
    assert inst.progress.phase == "done", lines
    assert kept_of(tmp_path).exists()  # never deleted when the stale stage was enough
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    assert (folder / "terrain/b.ter").read_bytes() == b"fourth"


def test_mesh_back_to_new_waits_for_a_region_whose_disk_is_away(tmp_path: Path, world) -> None:
    """A region linked to another disk, that disk not connected: its kept mesh cannot be seen,
    so Live counts it as keeping one, and Mesh stays as it is."""
    files, region = world
    elsewhere = tmp_path / "other disk" / "z_osxp_eur"
    elsewhere.parent.mkdir()
    root = tmp_path / "live"
    install(root, region, Host(files))
    folder = osxpregions.mesh_dir(root, "eur")
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    assert not osxpregions.keeps_any_old(root)
    elsewhere.parent.rename(tmp_path / "unplugged")
    assert osxpregions.keeps_any_old(root)


def test_a_stop_while_a_stale_stage_is_deleted_deletes_nothing_more(
    tmp_path: Path, world, short_disk, monkeypatch
) -> None:
    """The pilot's Stop while the install deletes what an update stopped earlier left (a minute
    or more, in its phase of its own): honoured once that is done, before the kept old mesh is
    deleted to make room, which could not be downloaded again (the reviews of 2026-11-18)."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)  # A kept
    files, region = updated(files, region, b"third")
    inst, _ = install(tmp_path, region, Host(files), may_swap=lambda: False)  # B waits
    files, region = updated(files, region, b"fourth")  # D listed: B's stage stale
    stage = osxpregions.stage_dir(tmp_path, "eur")
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    # short by one byte more than B's stage frees: deleting the kept mesh too would be enough
    short_disk(tmp_path, region, kept_of(tmp_path), folder, stage, by=disk_bytes([stage]) + 1)
    room = osxpregions.update_room(tmp_path, region, True, False)
    assert room is not None and room["kept"] is True and room["cleared"] is True
    running: list[Installer] = []
    phases: list[str] = []
    dropping = osxpregions._drop_stale

    def drop_then_stop(stale):
        phases.append(running[0].progress.phase)
        dropping(stale)
        running[0].cancel()  # the pilot's Stop during the deletion

    monkeypatch.setattr(osxpregions, "_drop_stale", drop_then_stop)
    inst, lines = install(tmp_path, region, Host(files), running.append, make_room=True)
    assert phases == ["clearing"]
    assert inst.progress.phase == "cancelled", lines
    assert kept_of(tmp_path).exists()  # never deleted after the Stop
    assert not stage.exists()  # what was stale is gone
    monkeypatch.setattr(osxpregions, "_drop_stale", dropping)
    _, lines = install(tmp_path, region, Host(files), make_room=True)  # Update again, no Stop
    assert "the old mesh of OrthoStudio XP's region eur deleted" in lines  # asked, and needed


def test_a_kept_mesh_the_pack_still_holds_from_before_its_swap_frees_nothing(
    tmp_path: Path, world, short_disk
) -> None:
    """The pack set up at the last start from the region holding A, its DSFs hard links of A's
    files; an update kept A with Live stopped, at once, the pack's record still naming the region,
    which changed since. Deleting A frees nothing until the next start sets the pack up again: not
    offered, never deleted by an install told to make room (the reviews of 2026-11-18). Once a
    start set the pack up from the new mesh, it frees its size."""
    files, region = world
    install(tmp_path, region, Host(files))  # A installed
    folder = osxpregions.mesh_dir(tmp_path, "eur")
    pack = pack_made_of(tmp_path / "pack", folder)  # the last start, on A
    dsf = "Earth nav data/+40+000/+46+005.dsf"
    (pack / dsf).parent.mkdir(parents=True)
    os.link(folder / dsf, pack / dsf)  # as region.place_file does
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)  # A kept, B in place, no start since
    kept = kept_of(tmp_path)
    assert os.stat(kept / dsf).st_nlink == 2  # the pack holds A's files still
    files, region = updated(files, region, b"third")  # C listed before the next start
    short_disk(tmp_path, region, (kept, 0))  # its files shared: deleting it frees nothing
    room = osxpregions.update_room(tmp_path, region, True, False)
    assert room is not None and room["kept"] is False and room["shared"] is True
    inst, _ = install(tmp_path, region, Host(files), make_room=True)
    assert inst.progress.phase == "failed" and kept.exists()  # A never lost for nothing
    pack_made_of(pack, folder)  # a start set the pack up from B: A shared no more
    short_disk(tmp_path, region, kept)
    assert osxpregions.update_room(tmp_path, region, True, False)["kept"] is True


def test_a_kept_mesh_the_pack_no_longer_holds_frees_its_size(tmp_path: Path, world, short_disk):
    """A kept, a start on B, then C installed without keeping B, Live stopped since: the pack's
    record names the region, which changed, but holds B's files, not A's (its fingerprints are
    B's): deleting A frees its size, offered. The river masks' date never counts: put in place
    in A's place they would not make the pack hold A's files."""
    files, region = world
    install(tmp_path, region, Host(files))  # A
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)  # A kept, B in place
    folder, kept = osxpregions.mesh_dir(tmp_path, "eur"), kept_of(tmp_path)
    pack = pack_made_of(tmp_path / "pack", folder)  # a start on B
    files, region = updated(files, region, b"third")
    install(tmp_path, region, Host(files), keep_old=False)  # C, B deleted, A still kept
    assert (kept / "terrain/b.ter").read_bytes() == b"ter b"
    assert osxpregions._kept_frees(tmp_path, "eur") == marker_of_kept(tmp_path)["size"]
    files, region = updated(files, region, b"fourth")
    short_disk(tmp_path, region, kept)
    room = osxpregions.update_room(tmp_path, region, True, False)
    assert room is not None and room["kept"] is True and room["shared"] is False
    pack_made_of(pack, kept)  # a start on A: held, whatever its river masks' date
    (kept / osxpregions.RIVERS).mkdir()
    assert osxpregions._kept_frees(tmp_path, "eur") == 0


def test_a_pack_on_another_disk_holds_nothing_of_the_kept_mesh(
    tmp_path: Path, world, monkeypatch
) -> None:
    """Live's pack on another disk than the kept mesh links to the folders it reads there
    (``region._place_dir``), never a clone: its record naming the kept mesh, deleting it still
    frees its size."""
    files, region = world
    install(tmp_path, region, Host(files))
    files, region = updated(files, region, b"second")
    install(tmp_path, region, Host(files), keep_old=True)
    kept = kept_of(tmp_path)
    pack = pack_made_of(tmp_path / "pack", kept)
    assert osxpregions._kept_frees(tmp_path, "eur") == 0  # the same disk: held
    disk_of = osxpregions._disk_of
    monkeypatch.setattr(osxpregions, "_disk_of", lambda p: -1 if Path(p) == pack else disk_of(p))
    assert osxpregions._kept_frees(tmp_path, "eur") == marker_of_kept(tmp_path)["size"]


def marker_of_kept(root: Path) -> dict:
    return json.loads((kept_of(root) / aoregions.MARKER).read_text())
