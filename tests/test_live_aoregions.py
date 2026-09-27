# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""AutoOrtho's regions installed by Live: the list GitHub gives, a download that resumes, zips
unpacked as AutoOrtho unpacks them, the region in place only once whole."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from orthostudio.live import aoregions
from orthostudio.live.aoregions import AoRegion, Asset, Installer, catalog, installed

HOST = "https://github.test/"


def mesh_zip(stem: str, squares: list[str]) -> bytes:
    """A mesh zip laid out as AutoOrtho's: ``<stem>/Earth nav data``, ``terrain``, ``textures``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{stem}/", b"")
        z.writestr(f"{stem}/textures/1_2_ZL16.png", b"a mask")
        for sq in squares:
            z.writestr(f"{stem}/Earth nav data/+40+000/{sq}.dsf", b"XPLNEDSF" + sq.encode() * 50)
        z.writestr(f"{stem}/terrain/1_2_BI16.ter", b"A\n800\nTERRAIN\n")
        z.writestr(f"{stem}/*/junk", b"left out")
    return buf.getvalue()


def overlay_zip(top: str, squares: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sq in squares:
            z.writestr(
                f"{top}/yOrtho4XP_Overlays/Earth nav data/+40+000/{sq}.dsf",
                b"overlay " + sq.encode(),
            )
    return buf.getvalue()


class FakeGitHub:
    """GitHub's API and its release files, with ranges; ``cut`` ends a file's first answer early,
    as a dropped connection would."""

    def __init__(self, files: dict[str, bytes], releases: list[dict]):
        self.files = files
        self.releases = releases
        self.cut: dict[str, int] = {}
        self.asked: list[tuple[str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(aoregions.RELEASES_URL.split("?")[0]):
            return httpx.Response(200, json=self.releases)
        name = url.removeprefix(HOST)
        if name not in self.files:
            return httpx.Response(404)
        body = self.files[name]
        rng = request.headers.get("Range")
        self.asked.append((name, rng))
        start = int(rng.removeprefix("bytes=").rstrip("-")) if rng else 0
        part = body[start:]
        if name in self.cut:  # the connection drops after that many bytes
            part = part[: self.cut.pop(name)]
            stream = _Dropping(part)
            return httpx.Response(206 if rng else 200, stream=stream)
        return httpx.Response(206 if rng else 200, content=part)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class _Dropping(httpx.SyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    def __iter__(self):
        yield self.data
        raise httpx.ReadError("connection dropped")


def asset(name: str, body: bytes, sha: bool = False) -> dict:
    return {"name": name, "browser_download_url": HOST + name, "size": len(body)}


@pytest.fixture
def github() -> FakeGitHub:
    files = {
        "eur_info.json": b"{}",
        "z_eur_00.zip": mesh_zip("z_eur_00", ["+43+005", "+44+005"]),
        "z_eur_01.zip": mesh_zip("z_eur_01", ["+45+006"]),
    }
    whole = overlay_zip("y_eur", ["+43+005", "+45+006"])
    files["y_eur_overlays.zip.00"], files["y_eur_overlays.zip.01"] = whole[:300], whole[300:]
    files["z_na_00.zip"] = mesh_zip("z_na_00", ["+40-075"])
    files["z_na_00.zip.sha256"] = (
        hashlib.sha256(files["z_na_00.zip"]).hexdigest() + "  z_na_00.zip\n"
    ).encode()
    files["na_info.json"] = b"{}"
    releases = [
        {"name": "0.0.52-beta", "prerelease": True, "assets": [asset("eur_info.json", b"")]},
        {
            "name": "0.0.51",
            "assets": [asset(n, files[n]) for n in files if "eur" in n],
        },
        {
            "name": "0.0.54",
            "assets": [asset(n, files[n]) for n in files if "_na_" in n or n.startswith("na_")],
        },
        {"name": "0.0.40", "assets": [asset("eur_info.json", b"")]},  # older: not taken
    ]
    return FakeGitHub(files, releases)


def test_the_regions_github_lists(github: FakeGitHub, tmp_path: Path) -> None:
    with github.client() as client:
        regions = catalog(tmp_path, client=client)
    assert sorted(regions) == ["eur", "na"]
    eur = regions["eur"]
    assert eur.release == "0.0.51"  # the newest release that is not a pre-release
    assert [a.name for a in eur.mesh] == ["z_eur_00.zip", "z_eur_01.zip"]
    assert [a.name for a in eur.overlays] == ["y_eur_overlays.zip.00", "y_eur_overlays.zip.01"]
    assert regions["na"].mesh[0].sha256_url == HOST + "z_na_00.zip.sha256"
    assert eur.size == sum(len(github.files[a.name]) for a in (*eur.mesh, *eur.overlays))
    # kept: the next hour, or when GitHub does not answer, the list comes from Live's folder
    offline = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert catalog(tmp_path, refresh=True, client=offline) == regions
    with pytest.raises(aoregions.CatalogError):
        catalog(tmp_path / "elsewhere", client=offline)


def test_an_install_as_autoortho_s_own(github: FakeGitHub, tmp_path: Path) -> None:
    with github.client() as client:
        region = catalog(tmp_path, client=client)["eur"]
        progress = Installer(tmp_path, region, client=client, log=lambda _: None).run()
    assert progress.phase == "done", progress.error
    assert progress.done_bytes == progress.total_bytes == region.size
    mesh = tmp_path / "regions" / "z_ao_eur"
    assert sorted(p.name for p in (mesh / "Earth nav data" / "+40+000").iterdir()) == [
        "+43+005.dsf",
        "+44+005.dsf",
        "+45+006.dsf",
    ]
    assert (mesh / "terrain" / "1_2_BI16.ter").exists()
    assert (mesh / "textures" / "1_2_ZL16.png").exists()
    assert not (mesh / "*").exists()  # AutoOrtho leaves its "*" folder out too
    overlays = tmp_path / "regions" / "y_ao_eur" / "Earth nav data" / "+40+000"
    assert sorted(p.name for p in overlays.iterdir()) == ["+43+005.dsf", "+45+006.dsf"]
    assert installed(tmp_path)["eur"]["release"] == "0.0.51"
    assert not (tmp_path / "regions" / ".download" / "eur").exists()  # nothing left behind


def test_a_dropped_download_resumes(github: FakeGitHub, tmp_path: Path, monkeypatch) -> None:
    github.cut["z_eur_01.zip"] = 100
    monkeypatch.setattr(aoregions, "_pause", lambda attempt: None)
    with github.client() as client:
        region = catalog(tmp_path, client=client)["eur"]
        progress = Installer(tmp_path, region, client=client, log=lambda _: None).run()
    assert progress.phase == "done", progress.error
    asked = [rng for name, rng in github.asked if name == "z_eur_01.zip"]
    assert asked == [None, "bytes=100-"]  # the second try asks for the rest only
    assert (
        tmp_path / "regions" / "z_ao_eur" / "Earth nav data" / "+40+000" / "+45+006.dsf"
    ).exists()


def test_a_stopped_install_goes_on_the_next_time(github: FakeGitHub, tmp_path: Path) -> None:
    with github.client() as client:
        region = catalog(tmp_path, client=client)["eur"]
        first = Installer(tmp_path, region, client=client, log=lambda _: None)
        real = first._unpack_mesh

        def stop_after_the_first(path: Path) -> None:
            real(path)
            first.cancel()

        first._unpack_mesh = stop_after_the_first
        assert first.run().phase == "cancelled"
        assert "eur" not in installed(tmp_path)  # not whole: not there for the streamer
        github.asked.clear()
        again = Installer(tmp_path, region, client=client, log=lambda _: None).run()
    assert again.phase == "done", again.error
    assert "z_eur_00.zip" not in {name for name, _ in github.asked}  # unpacked already
    assert installed(tmp_path)["eur"]["release"] == "0.0.51"


def test_a_damaged_zip_is_refused(github: FakeGitHub, tmp_path: Path) -> None:
    good = github.files["z_na_00.zip"]
    github.files["z_na_00.zip"] = good[:-5] + b"XXXXX"  # the same size, other bytes
    with github.client() as client:
        region = catalog(tmp_path, client=client)["na"]
        progress = Installer(tmp_path, region, client=client, log=lambda _: None).run()
    assert progress.phase == "failed"
    assert "SHA-256" in progress.error
    assert "na" not in installed(tmp_path)


def test_not_enough_disk(github: FakeGitHub, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(aoregions, "SPARE_BYTES", 10**18)
    with github.client() as client:
        region = catalog(tmp_path, client=client)["eur"]
        progress = Installer(tmp_path, region, client=client, log=lambda _: None).run()
    assert progress.phase == "failed" and "GB needed" in progress.error


def test_remove(github: FakeGitHub, tmp_path: Path) -> None:
    with github.client() as client:
        region = catalog(tmp_path, client=client)["eur"]
        Installer(tmp_path, region, client=client, log=lambda _: None).run()
    aoregions.remove(tmp_path, "eur", log=lambda _: None)
    assert installed(tmp_path) == {}
    assert not (tmp_path / "regions" / "y_ao_eur").exists()


def test_a_region_round_trips_through_its_json() -> None:
    region = AoRegion("eur", "0.0.51", (Asset("z_eur_00.zip", HOST + "z", 3),), ())
    assert AoRegion.of(json.loads(json.dumps(aoregions.asdict(region)))) == region


def test_the_region_of_a_place() -> None:
    """The map made from every zip's list of files (tools/live/aoregions_map.py)."""
    places = {
        (45.72, 5.08): "eur",  # Lyon
        (40.64, -73.78): "na",  # New York
        (-22.81, -43.25): "sa",  # Rio
        (-1.32, 36.93): "afr",  # Nairobi
        (35.55, 139.78): "asi",  # Tokyo
        (-33.95, 151.18): "aus_pac",  # Sydney
        (40.0, -40.0): None,  # the open Atlantic
        (95.0, 0.0): None,  # not on the planet
    }
    for (lat, lon), region in places.items():
        assert aoregions.region_of(lat, lon) == region, (lat, lon)
    doc = json.loads(aoregions.MAP_FILE.read_text())
    assert doc["regions"] == list(aoregions.REGION_IDS)
    assert doc["squares"]["eur"] == 1981  # as many DSFs as z_ao_eur has
