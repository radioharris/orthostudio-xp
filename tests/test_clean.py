"""``osxp clean``: what no pack needs goes, what a pack needs stays, and the sizes are real.

Found when the cache reached 27 GB for five tiles: ``Store.gc`` existed but no command called
it, and it could not have been called as it was -- a build pins nothing, and a texture
artefact is not an input of anything, so it would have collected every texture of every
installed tile.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orthostudio.clean import clean, disk_bytes, freed_bytes
from orthostudio.cli import app
from orthostudio.graph import InputRef, Store, artifact_key
from orthostudio.install import Library
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import ArtefactEntry, PackManifest

T = TileRef(43, 5)


def _put(
    store: Store,
    rule: str,
    n: int,
    *,
    data: bytes = b"",
    files: dict[str, bytes | Path] | None = None,
    inputs: tuple[InputRef, ...] = (),
) -> str:
    key, recipe = artifact_key(rule, 1, {"n": n}, {i.name: i.digest for i in inputs})
    kind = "dir" if files is not None else "file"
    with store.begin(rule, key, kind) as b:
        if files is None:
            b.out.write_bytes(data)
        for name, content in (files or {}).items():
            p = b.out / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Path):
                os.link(content, p)  # hard link, as tile.textures and the pack do
            else:
                p.write_bytes(content)
        b.commit(version=1, recipe=recipe, inputs=list(inputs))
    return key


def _ref(store: Store, name: str, key: str) -> InputRef:
    return InputRef(name, store.digest_of(key), key)


@pytest.fixture
def world(tmp_path: Path) -> dict[str, object]:
    """One pack built from a DSF, an OSM snapshot and one texture; an older build of the tile."""
    root = tmp_path / "store"
    with Store(root, fsync=False) as s:
        osm = _put(s, "orthostudio.osm", 1, data=b"o" * 300)
        dsf = _put(s, "tile.dsf", 2, data=b"n" * 1000, inputs=(_ref(s, "osm", osm),))
        old_dsf = _put(s, "tile.dsf", 3, data=b"f" * 2000, inputs=(_ref(s, "osm", osm),))
        dds = _put(s, "texture.dds", 4, data=b"K" * 5000)
        old_dds = _put(s, "texture.dds", 5, data=b"O" * 7000)
        manifest = json.dumps({"textures": [{"key": dds}]}).encode()
        textures = _put(
            s, "tile.textures", 6,
            files={"manifest.json": manifest, "textures/a.dds": s.path(dds)},
            inputs=(_ref(s, "dsf", dsf),),
        )  # fmt: skip
        old_manifest = json.dumps({"textures": [{"key": old_dds}]}).encode()
        old_textures = _put(
            s, "tile.textures", 7,
            files={"manifest.json": old_manifest, "textures/a.dds": s.path(old_dds),
                   "textures/shared.dds": s.path(dds)},
            inputs=(_ref(s, "dsf", old_dsf),),
        )  # fmt: skip
        dds_path = s.path(dds)
    pack = tmp_path / "home" / "tiles" / "zOrthoStudio_+43+005"
    (pack / "textures").mkdir(parents=True)
    os.link(dds_path, pack / "textures" / "a.dds")
    entries = {"dsf": ArtefactEntry(dsf, "0" * 64, "tile.dsf@1"),
               "textures": ArtefactEntry(textures, "0" * 64, "tile.textures@1")}  # fmt: skip
    (pack / "orthostudio.toml").write_text(PackManifest(T.name, "BI", 16, entries, {}).to_toml())
    chunks = tmp_path / "chunks" / "BI" / "16"
    chunks.mkdir(parents=True)
    (chunks / "1_2.chunks").write_bytes(b"j" * 4000)
    return {
        "store": root,
        "chunks": tmp_path / "chunks",
        "tiles": pack.parent,
        "library": tmp_path / "home" / "library.sqlite",
        "kept": {osm, dsf, dds, textures},
        "gone": {old_dsf, old_dds, old_textures},
        "freed": 2000 + 7000 + len(old_manifest),
    }


def _run(world: dict[str, object], **kw: object) -> object:
    return clean(
        world["store"],  # type: ignore[arg-type]
        world["chunks"],  # type: ignore[arg-type]
        library_path=world["library"],  # type: ignore[arg-type]
        tiles_root=world["tiles"],  # type: ignore[arg-type]
        **{"grace_s": 0.0, **kw},  # type: ignore[arg-type]
    )


def _keys(world: dict[str, object]) -> set[str]:
    with Store(world["store"], fsync=False) as s:  # type: ignore[arg-type]
        return {i.key for i in s.iter_artifacts()}


def test_what_no_pack_needs_goes_and_the_bytes_are_the_disk_s(world: dict[str, object]) -> None:
    report = _run(world)
    assert _keys(world) == world["kept"]
    assert report.removed == 3 and report.kept == 4  # type: ignore[attr-defined]
    # the old texture's two links were both collected; the shared one is still kept elsewhere
    assert report.freed_bytes == world["freed"]  # type: ignore[attr-defined]
    assert report.images_bytes == 4000 and not report.images_removed  # type: ignore[attr-defined]
    assert (Path(world["chunks"]) / "BI" / "16" / "1_2.chunks").is_file()  # type: ignore[arg-type]


def test_dry_run_deletes_nothing_and_says_the_same(world: dict[str, object]) -> None:
    before = _keys(world)
    report = _run(world, dry_run=True, images=True)
    assert _keys(world) == before
    assert report.removed == 3 and report.freed_bytes == world["freed"]  # type: ignore[attr-defined]
    assert not report.images_removed  # type: ignore[attr-defined]


def test_a_recent_artefact_is_left_to_the_build_that_may_need_it(world: dict[str, object]) -> None:
    report = _run(world, grace_s=3600.0)
    assert report.removed == 0 and _keys(world) == world["kept"] | world["gone"]  # type: ignore[attr-defined, operator]


def test_a_pack_only_the_library_knows_is_kept_and_pins_are_honoured(
    world: dict[str, object], tmp_path: Path
) -> None:
    pack = Path(world["tiles"]) / "zOrthoStudio_+43+005"  # type: ignore[arg-type]
    elsewhere = tmp_path / "elsewhere" / pack.name
    pack.rename(elsewhere.parent.mkdir(parents=True) or elsewhere)
    with Library(world["library"]) as lib:  # type: ignore[arg-type]
        lib.register(T, "BI", 16, elsewhere, "osxp", {})
    old_dsf = sorted(world["gone"])[0]  # type: ignore[call-overload]
    with Store(world["store"], fsync=False) as s:  # type: ignore[arg-type]
        s.pin("mine", old_dsf)
    _run(world)
    assert world["kept"] <= _keys(world) and old_dsf in _keys(world)  # type: ignore[operator]


def test_images_empties_the_imagery_cache(world: dict[str, object]) -> None:
    report = _run(world, images=True)
    assert report.images_removed and report.images_bytes == 4000  # type: ignore[attr-defined]
    assert list(Path(world["chunks"]).iterdir()) == []  # type: ignore[arg-type]


def test_freed_bytes_counts_a_file_once_its_last_link_is_in(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"x" * 10)
    os.link(a, tmp_path / "b")
    assert freed_bytes([a]) == 0
    assert freed_bytes([a, tmp_path / "b"]) == 10


def test_disk_bytes_counts_a_hard_linked_file_once_and_follows_no_link(tmp_path: Path) -> None:
    """The page's store size: the index added a DDS up once per artefact linking it, 50.3 GB for
    a store ``du`` measured at 24 GB."""
    folder = tmp_path / "store"
    (folder / "tile.textures").mkdir(parents=True)
    dds = folder / "a.dds"
    dds.write_bytes(b"x" * 10)
    os.link(dds, folder / "tile.textures" / "a.dds")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"y" * 1000)
    (folder / "file-link").symlink_to(outside / "big")
    (folder / "folder-link").symlink_to(outside, target_is_directory=True)
    assert disk_bytes([folder]) == 10
    assert disk_bytes([folder, dds]) == 10  # a path given twice still counts once
    assert disk_bytes([tmp_path / "missing"]) == 0 and disk_bytes([dds]) == 10
    assert freed_bytes([folder]) == 10  # both links are inside


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_an_unreadable_folder_is_not_mistaken_for_an_empty_one(tmp_path: Path) -> None:
    folder = tmp_path / "store"
    folder.mkdir()
    (folder / "a").write_bytes(b"x" * 10)
    folder.chmod(0)
    try:
        with pytest.raises(OSError):
            disk_bytes([folder])
        assert freed_bytes([folder]) == 0
    finally:
        folder.chmod(0o755)


def test_the_command(world: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSXP_HOME", str(Path(world["tiles"]).parent))  # type: ignore[arg-type]
    runner = CliRunner()
    args = ["clean", "--dry-run", "--store", str(world["store"]), "--chunks", str(world["chunks"])]
    done = runner.invoke(app, args)
    assert done.exit_code == 0, done.output
    assert "kept what 1 pack(s) need" in done.output and "would remove 0 artefact(s)" in done.output


def _clean_all(world: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setenv("OSXP_HOME", str(Path(world["tiles"]).parent))  # type: ignore[arg-type]
    args = ["clean", "--all", "--store", str(world["store"]), "--chunks", str(world["chunks"])]
    return CliRunner().invoke(app, args)


def test_all_frees_even_what_a_build_just_used_and_the_downloads(
    world: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user deleted every tile a minute after building them: the hour of grace kept 16 GB.
    ``--all`` gives it back, and the downloaded image pieces with it, keeping what a pack on disk
    still needs."""
    assert _run(world, grace_s=3600.0).removed == 0  # type: ignore[attr-defined]
    done = _clean_all(world, monkeypatch)
    assert done.exit_code == 0, done.output  # type: ignore[attr-defined]
    assert _keys(world) == world["kept"]
    assert list(Path(world["chunks"]).iterdir()) == []  # type: ignore[arg-type]
    assert "in total: freed" in done.output  # type: ignore[attr-defined]


def test_all_is_refused_while_another_process_builds(
    world: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the grace period, a build running now could lose what it is about to use."""
    key = sorted(world["gone"])[0]  # type: ignore[call-overload]
    with Store(world["store"], fsync=False) as s:  # type: ignore[arg-type]
        shard = s.path(key).parent
    building = shard / f"{key}.tmp-{os.getppid()}-abcd"  # a live process that is not this one
    building.mkdir()
    before = _keys(world)
    done = _clean_all(world, monkeypatch)
    assert done.exit_code == 1  # type: ignore[attr-defined]
    assert "a build is running" in done.output  # type: ignore[attr-defined]
    assert _keys(world) == before and building.is_dir()
    assert (Path(world["chunks"]) / "BI" / "16" / "1_2.chunks").is_file()  # type: ignore[arg-type]


# -- the map cache (docs/specs/map-zones.md section 6) ------------------------------------------


def _map_tiles(mapcache: Path) -> int:
    """A cached base-map tile and a ``.none`` marker, as ``osxp serve`` writes them."""
    folder = mapcache / "BI" / "12" / "2140"
    folder.mkdir(parents=True)
    (folder / "1490").write_bytes(b"m" * 1500)
    (folder / "1491.none").write_bytes(b"404\n")
    return 1504


def test_images_empties_the_map_cache_too_and_counts_it(
    world: dict[str, object], tmp_path: Path
) -> None:
    mapcache = tmp_path / "home" / "mapcache"
    size = _map_tiles(mapcache)
    report = _run(world, mapcache_root=mapcache)
    assert report.images_bytes == 4000 + size and report.mapcache_bytes == size  # type: ignore[attr-defined]
    assert not report.images_removed and (mapcache / "BI").is_dir()  # type: ignore[attr-defined]
    report = _run(world, dry_run=True, images=True, mapcache_root=mapcache)
    assert report.mapcache_bytes == size and (mapcache / "BI").is_dir()  # type: ignore[attr-defined]
    report = _run(world, images=True, mapcache_root=mapcache)
    assert report.images_removed and report.images_bytes == 4000 + size  # type: ignore[attr-defined]
    assert report.mapcache_bytes == size  # type: ignore[attr-defined]
    assert list(mapcache.iterdir()) == [] and list(Path(world["chunks"]).iterdir()) == []  # type: ignore[arg-type]


def test_without_mapcache_root_a_folder_beside_the_chunks_is_left_alone(
    world: dict[str, object],
) -> None:
    """Review finding: the map cache used to default to ``chunks_root.parent / "mapcache"``, so a
    caller passing its own chunks folder had whatever ``mapcache`` sat beside it emptied."""
    beside = Path(world["chunks"]).parent / "mapcache"  # type: ignore[arg-type]
    size = _map_tiles(beside)
    report = _run(world, images=True)
    assert report.images_removed and list(Path(world["chunks"]).iterdir()) == []  # type: ignore[attr-defined, arg-type]
    assert report.images_bytes == 4000 and report.mapcache_bytes == 0  # type: ignore[attr-defined]
    assert sum(f.stat().st_size for f in beside.rglob("*") if f.is_file()) == size


def test_a_map_cache_alone_is_emptied_and_never_counted_twice(tmp_path: Path) -> None:
    mapcache = tmp_path / "mapcache"
    size = _map_tiles(mapcache)
    report = clean(tmp_path / "store", tmp_path / "chunks", library_path=None, tiles_root=None,
                   images=True, grace_s=0.0, mapcache_root=mapcache)  # fmt: skip
    assert report.images_removed and report.images_bytes == report.mapcache_bytes == size
    assert list(mapcache.iterdir()) == []
    size = _map_tiles(mapcache)
    report = clean(tmp_path / "store", mapcache, library_path=None, tiles_root=None,
                   mapcache_root=mapcache, grace_s=0.0)  # fmt: skip
    assert report.images_bytes == size and report.mapcache_bytes == 0


def test_the_command_empties_the_map_cache_of_the_home(
    world: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    home = Path(world["tiles"]).parent  # type: ignore[arg-type]
    monkeypatch.setenv("OSXP_HOME", str(home))
    size = _map_tiles(home / "mapcache")
    args = ["clean", "--images", "--json", "--store", str(world["store"])]
    done = CliRunner().invoke(app, args)
    assert done.exit_code == 0, done.output
    doc = json.loads(done.output)
    assert doc["mapcache_bytes"] == size and doc["images_removed"] is True
    assert list((home / "mapcache").iterdir()) == []


def test_the_downloaded_relief_is_counted_and_can_be_freed(tmp_path: Path) -> None:
    """A user emptied everything from the Library, was told there was nothing left to free, and
    found 1.4 GB of elevation cells still there (2026-09-18): nothing counted them. They are their
    own choice, apart from the imagery, because a square of relief costs far less to fetch again.
    """
    from orthostudio.clean import clean

    store = tmp_path / "store"
    chunks = tmp_path / "chunks"
    elevation = tmp_path / "elevation"
    for folder, name, size in (
        (chunks, "BI/16/1_2.chunks", 300),
        (elevation, "+40+000/N46E006_COP30.tif", 4000),
        (elevation, "+40-080/N45W076_HRDEM.hgt", 2000),
    ):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    seen = clean(store, chunks, library_path=None, tiles_root=None, dry_run=True,
                 elevation_root=elevation)  # fmt: skip
    assert seen.relief_bytes >= 6000 and not seen.relief_removed
    assert elevation.exists()

    # the imagery alone leaves the relief where it is
    only_images = clean(store, chunks, library_path=None, tiles_root=None, images=True,
                        elevation_root=elevation)  # fmt: skip
    assert only_images.images_removed and not only_images.relief_removed
    assert list(elevation.rglob("*.tif"))

    freed = clean(store, chunks, library_path=None, tiles_root=None, elevation_root=elevation,
                  relief=True)  # fmt: skip
    assert freed.relief_removed and freed.relief_bytes >= 6000
    assert elevation.is_dir() and not any(elevation.iterdir())
