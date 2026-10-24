# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The relief and the mesh the cache keeps in two forms: trimmed from the artefacts that hold both
once the cache is compacted, the artefacts keeping their identity, so that nothing made from them
is made again.

Builds write Ortho4XP's ``Data<tile>.alt`` beside ``dem.npy`` and its ``Data<tile>.mesh`` beside
``mesh.npz``, the same raster and the same mesh, which the versions before this one read: 90 GB and
272 GB of a user's 1.1 TB cache. ``Store.trim`` removes a file of an artefact and keeps its digest,
which the artefacts made from it were keyed with; Free space trims those files when it is asked to
compact the cache, and whatever the choices once it is (``clean._twice``), each only when the form
kept says the same size of the same thing. Temporary folders only.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

import test_api_fakes as fakes
from orthostudio.api.app import create_app
from orthostudio.api.jobs import JobManager
from orthostudio.clean import clean
from orthostudio.cli import app as cli_app
from orthostudio.graph import Store, artifact_key
from orthostudio.graph.digest import digest_file
from orthostudio.install import Library
from orthostudio.mesh.mesh_file import MeshData, write_mesh_npz, write_mesh_text
from orthostudio.model import TileRef
from orthostudio.pipeline.pack import ArtefactEntry, PackManifest, write_pack
from test_api_fakes import FakeBuild, FakeIndex, client_for

anyio_backend = fakes.anyio_backend

T = TileRef(46, 6)
SIDE = 33


def _mesh(n: int = 50) -> MeshData:
    rng = np.random.default_rng(n)
    vertices = np.column_stack([6 + rng.random(n), 46 + rng.random(n), rng.uniform(300, 900, n)])
    tris = np.array([[i, i + 1, i + 2] for i in range(n - 2)], np.int32)
    normals = rng.uniform(-1, 1, (n, 2)).astype(np.float32)
    return MeshData(vertices, normals, tris, np.zeros(n - 2, np.uint8))


def _put(store: Store, rule: str, n: int, files: dict[str, Any]) -> str:
    key, recipe = artifact_key(rule, 1, {"n": n, "tile": T.name}, {})
    with store.begin(rule, key, "dir") as b:
        for name, content in files.items():
            if callable(content):
                content(b.out / name)
            else:
                (b.out / name).write_bytes(content)
        b.commit(version=1, recipe=recipe, inputs=[])
    return key


def _relief(store: Store, n: int = 1, *, alt_side: int = SIDE) -> str:
    raster = np.random.default_rng(n).uniform(0, 3000, (SIDE, SIDE)).astype(np.float32)
    alt = np.random.default_rng(n).uniform(0, 3000, (alt_side, alt_side)).astype(np.float32)
    return _put(
        store,
        "orthostudio.dem",
        n,
        {
            "dem.npy": lambda p: np.save(p, raster, allow_pickle=False),
            f"Data{T.name}.alt": alt.tobytes(),
            "meta.json": b'{"format": "osxp-dem-1"}',
        },
    )


def _mesh_artefact(store: Store, n: int = 1, *, text_points: int | None = None) -> str:
    mesh = _mesh(40 + n)
    text = _mesh(text_points) if text_points else mesh
    return _put(
        store,
        "orthostudio.mesh",
        n,
        {
            "mesh.npz": lambda p: write_mesh_npz(p, mesh),
            f"Data{T.name}.mesh": lambda p: write_mesh_text(p, text),
            "stats.json": b"{}",
        },
    )


def _keep(store_root: Path, library: Path, tiles: Path, keys: dict[str, str]) -> Path:
    """A pack whose manifest names ``keys``, as a build leaves one: what the clean keeps."""
    with Store(store_root, fsync=False) as s:
        dsf_key, recipe = artifact_key("tile.dsf", 1, {"tile": T.name}, {})
        with s.begin("tile.dsf", dsf_key, "dir") as b:
            (b.out / "terrain").mkdir()
            (b.out / f"{T.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
            b.commit(version=1, recipe=recipe, inputs=[])
        dsf_dir = s.path(dsf_key)
        tex_key, recipe = artifact_key("tile.textures", 1, {"tile": T.name}, {})
        with s.begin("tile.textures", tex_key, "dir") as b:
            (b.out / "textures").mkdir()  # no image: a pack takes them from a folder
            b.commit(version=1, recipe=recipe, inputs=[])
        tex_dir = s.path(tex_key)
    files = write_pack(tiles, T, dsf_dir=dsf_dir, textures_dir=tex_dir, overlay_file=None)
    artefacts = {
        role: ArtefactEntry(key, "d" * 64, f"{rule}@1")
        for role, (key, rule) in {
            "dsf": (dsf_key, "tile.dsf"),
            **{r: (k, f"orthostudio.{r}") for r, k in keys.items()},
        }.items()
    }
    listed = {"dsf": T.dsf_relpath.as_posix(), "dsf_size": 108, "textures": 0, "terrain": 0}
    manifest = PackManifest(T.name, "BI", 16, artefacts, listed)
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    with Library(library) as lib:
        lib.register(T, "BI", 16, files.pack_dir, "osxp", manifest.keys)
    return files.pack_dir


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OSXP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OSXP_API_NO_AIRPORTS", "1")
    paths = {
        "store": tmp_path / "data" / "store",
        "chunks": tmp_path / "data" / "chunks",
        "tiles": tmp_path / "data" / "tiles",
        "library": tmp_path / "home" / "library.sqlite",
    }
    for key in ("chunks", "tiles"):
        paths[key].mkdir(parents=True)
    paths["library"].parent.mkdir(parents=True)
    return paths


def _run(h: dict[str, Path], **kw: Any) -> Any:
    return clean(
        h["store"], h["chunks"], library_path=h["library"], tiles_root=h["tiles"],
        **{"grace_s": 0.0, **kw},
    )  # fmt: skip


# -- the store ----------------------------------------------------------------------------------


def test_a_trimmed_artefact_keeps_its_identity(tmp_path: Path) -> None:
    """Its key and digest stay, which what was made from it was keyed with; its size in the index
    and the store's are what is left; the files that went are listed; a check of the store finds
    it whole and leaves it; trimmed again, nothing more goes."""
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        before = s.info(key)
        disk = s.disk_bytes()
        alt = f"Data{T.name}.alt"
        size = (before.path / alt).stat().st_size  # type: ignore[union-attr]
        assert s.trim(key, [alt]) == size
        after = s.info(key)
        assert after is not None and before is not None
        assert (after.key, after.digest) == (before.key, before.digest)
        assert after.size == before.size - size and s.disk_bytes() == disk - size
        assert not (after.path / alt).exists() and (after.path / "dem.npy").is_file()
        assert s.trimmed(key) == {alt: size}
        report = s.fsck(verify=True, repair=True)
        assert report.clean and s.has(key)
        assert s.trim(key, [alt]) == 0 and s.trimmed(key) == {alt: size}


def test_only_files_inside_a_folder_artefact_are_trimmed(tmp_path: Path) -> None:
    """A path outside the artefact, one not there, a folder, a file artefact: nothing goes."""
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        outside = tmp_path / "outside.alt"
        outside.write_bytes(b"x")
        for rel in ("../../../../outside.alt", str(outside), "missing.alt", "."):
            assert s.trim(key, [rel]) == 0
        assert outside.is_file() and s.trimmed(key) == {}
        file_key, recipe = artifact_key("texture.dds", 1, {"n": 1}, {})
        with s.begin("texture.dds", file_key, "file") as b:
            b.out.write_bytes(b"DDS")
            b.commit(version=1, recipe=recipe, inputs=[])
        assert s.trim(file_key, ["x"]) == 0 and s.path(file_key).is_file()


def test_a_file_another_program_holds_stays_untrimmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows a file another program holds cannot be removed: it stays, listed nowhere, and
    the index's size with it."""
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        before = s.info(key)
        real = Path.unlink

        def held(self: Path, *a: Any, **kw: Any) -> None:
            if self.suffix == ".alt":
                raise PermissionError(13, "held by another program", str(self))
            real(self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", held)
        assert s.trim(key, [f"Data{T.name}.alt"]) == 0
        monkeypatch.undo()
        assert s.trimmed(key) == {} and s.info(key).size == before.size  # type: ignore[union-attr]


def test_a_deleted_artefact_takes_its_list_with_it(tmp_path: Path) -> None:
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        s.trim(key, [f"Data{T.name}.alt"])
        s.delete(key, force=True)
        db = sqlite3.connect(s.root / "index.sqlite")
        assert db.execute("SELECT COUNT(*) FROM trimmed").fetchone() == (0,)
        db.close()


def test_an_index_from_before_gains_the_list(tmp_path: Path) -> None:
    root = tmp_path / "store"
    Store(root, fsync=False).close()
    db = sqlite3.connect(root / "index.sqlite")
    db.execute("DROP TABLE trimmed")
    db.commit()
    db.close()
    with Store(root, fsync=False) as s:
        key = _relief(s)
        assert s.trim(key, [f"Data{T.name}.alt"]) > 0


# -- Free space ---------------------------------------------------------------------------------


def test_free_space_trims_the_second_forms_when_asked_to_compact(home: dict[str, Path]) -> None:
    """Measured, then left as they are by a Free space that is not asked to compact: the versions
    before this one go on reading the cache. Asked, the cache is compacted and the relief's
    ``.alt`` and the mesh's text go from the artefacts a tile needs, the forms OrthoStudio XP reads
    stay, every artefact keeps its key; the figures are what the disk gets back; a second time
    there is nothing."""
    with Store(home["store"], fsync=False) as s:
        relief, mesh = _relief(s), _mesh_artefact(s)
        digests = {k: s.digest_of(k) for k in (relief, mesh)}
        sizes = {
            k: sum(f.stat().st_size for f in s.path(k).glob(p))
            for k, p in ((relief, "Data*.alt"), (mesh, "Data*.mesh"))
        }
    _keep(home["store"], home["library"], home["tiles"], {"dem": relief, "mesh": mesh})
    seen = _run(home, dry_run=True)
    assert seen.twice_files == 2 and seen.twice_bytes == sum(sizes.values())
    assert seen.freed_bytes == 0 and not seen.compacted
    kept = _run(home, images=True, relief=True, filed=True, apart=True)
    assert not kept.compacted
    with Store(home["store"], fsync=False) as s:
        assert not s.compacted() and not s.trimmed(relief) and not s.trimmed(mesh)
        assert len(list(s.path(relief).glob("Data*.alt"))) == 1
    done = _run(home, compact=True)
    assert done.compacted and done.twice_files == 2 and done.twice_bytes == seen.twice_bytes
    with Store(home["store"], fsync=False) as s:
        assert s.compacted()
        assert {k: s.digest_of(k) for k in (relief, mesh)} == digests
        assert sorted(p.name for p in s.path(relief).iterdir()) == ["dem.npy", "meta.json"]
        assert sorted(p.name for p in s.path(mesh).iterdir()) == ["mesh.npz", "stats.json"]
    again = _run(home, dry_run=True)
    assert again.compacted and again.twice_files == 0 and again.twice_bytes == 0


def test_the_second_forms_of_the_tiles_filed_outside_count_with_the_compaction(
    home: dict[str, Path],
) -> None:
    """Measured with the filed tiles' choice, as the page measures, their relief's second form was
    left out of the figure, though compacting alone trims it too (a review, 2026-10-24). It is
    counted apart: in the figure without their choice, out of it with their choice, which deletes
    their data whole."""
    with Store(home["store"], fsync=False) as s:
        relief = _relief(s)
        size = sum(f.stat().st_size for f in s.path(relief).glob("Data*.alt"))
    away = home["tiles"].parent / "elsewhere"
    away.mkdir()
    _keep(home["store"], home["library"], away, {"dem": relief})
    with_theirs = _run(home, dry_run=True, filed=True)
    assert with_theirs.filed_tiles == 1
    assert with_theirs.twice_bytes == 0 and with_theirs.twice_filed_bytes == size
    alone = _run(home, dry_run=True)
    assert alone.twice_bytes == size and alone.twice_filed_bytes == size
    done = _run(home, compact=True)
    assert done.compacted and done.twice_bytes == size
    with Store(home["store"], fsync=False) as s:
        assert not list(s.path(relief).glob("Data*.alt"))


def test_a_compacted_cache_trims_what_is_left_whatever_the_choices(home: dict[str, Path]) -> None:
    """A compaction cut short (the program closed meanwhile) leaves some second forms in a cache
    compacted already: the next Free space trims them, asked or not."""
    with Store(home["store"], fsync=False) as s:
        relief = _relief(s)
        s.compact()
    _keep(home["store"], home["library"], home["tiles"], {"dem": relief})
    done = _run(home)
    assert done.compacted and done.twice_files == 1
    with Store(home["store"], fsync=False) as s:
        assert sorted(p.name for p in s.path(relief).iterdir()) == ["dem.npy", "meta.json"]


def test_a_second_form_that_does_not_match_stays(home: dict[str, Path]) -> None:
    """The kept form must say the same size of the same thing: a raster of another size, a mesh
    text naming another number of points, an npz that is not one, stay as they are."""
    with Store(home["store"], fsync=False) as s:
        relief = _relief(s, alt_side=SIDE + 1)
        mesh = _mesh_artefact(s, text_points=77)
        broken = _put(
            s, "orthostudio.mesh", 9,
            {"mesh.npz": b"not a zip", f"Data{T.name}.mesh": b"MeshVersionFormatted 2\n"},
        )  # fmt: skip
    _keep(home["store"], home["library"], home["tiles"], {"dem": relief, "mesh": mesh})
    with Library(home["library"]) as lib:  # the broken one is another tile's, kept as well
        other = TileRef(47, 6)
        lib.register(other, "BI", 16, home["tiles"] / "elsewhere", "osxp", {"mesh": broken})
    assert _run(home, compact=True).twice_files == 0
    with Store(home["store"], fsync=False) as s:
        assert len(list(s.path(relief).glob("Data*.alt"))) == 1
        assert len(list(s.path(mesh).glob("Data*.mesh"))) == 1


def test_what_goes_anyway_was_just_used_or_is_pinned_is_not_trimmed(
    home: dict[str, Path],
) -> None:
    """Data no tile needs goes whole, counted once; what a build may be reading (used within the
    grace period) and what is pinned keep both forms."""
    with Store(home["store"], fsync=False) as s:
        unused = _relief(s, 1)
        recent = _mesh_artefact(s, 2)
        pinned = _relief(s, 3)
        s.pin("kept", pinned)
        old = time.time() - 7200
        s._db.execute("UPDATE artifacts SET last_used_at = ?, created_at = ?", (old, old))
        s.touch(recent)
    _keep(home["store"], home["library"], home["tiles"], {"mesh": recent, "dem": pinned})
    seen = _run(home, dry_run=True, grace_s=3600.0, compact=True)
    assert seen.twice_files == 0 and seen.removed == 1
    done = _run(home, grace_s=3600.0, compact=True)
    with Store(home["store"], fsync=False) as s:
        assert not s.has(unused) and s.trimmed(recent) == {} and s.trimmed(pinned) == {}
    assert done.twice_bytes == 0


def test_the_command_says_it(home: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    with Store(home["store"], fsync=False) as s:
        relief = _relief(s)
        old = time.time() - 7200
        s._db.execute("UPDATE artifacts SET last_used_at = ?, created_at = ?", (old, old))
    _keep(home["store"], home["library"], home["tiles"], {"dem": relief})
    with Store(home["store"], fsync=False) as s:
        s._db.execute("UPDATE artifacts SET last_used_at = ?, created_at = ?", (old, old))
    out = CliRunner().invoke(cli_app, ["clean", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "which older versions read: 1, " in out.output and "kept (--compact" in out.output
    out = CliRunner().invoke(cli_app, ["clean", "--compact", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "would compact the cache" in out.output
    assert "relief and mesh files kept in a second form: 1, would free" in out.output
    assert CliRunner().invoke(cli_app, ["clean", "--all"]).exit_code == 0  # not part of --all
    with Store(home["store"], fsync=False) as s:
        assert not s.compacted() and not s.trimmed(relief)
    out = CliRunner().invoke(cli_app, ["clean", "--compact"])
    assert out.exit_code == 0, out.output
    assert "compacted the cache" in out.output and "nothing to compact" not in out.output
    with Store(home["store"], fsync=False) as s:
        assert s.compacted() and s.trimmed(relief)


def test_the_command_compacts_no_cache_that_is_not_there(
    home: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no cache yet, ``--compact`` said "compacted the cache" while nothing was, and the first
    build made one in both forms (a review, 2026-10-24)."""
    out = CliRunner().invoke(cli_app, ["clean", "--compact"])
    assert out.exit_code == 0, out.output
    assert "nothing to compact" in out.output and "compacted the cache" not in out.output
    assert not (home["store"] / "index.sqlite").exists()


@pytest.fixture
def app(home: dict[str, Path], tmp_path: Path) -> Any:
    mgr = JobManager(jobs_dir=tmp_path / "home" / "jobs", build=FakeBuild(), env_factory=None)
    application = create_app(
        env_factory=None,
        jobs=mgr,
        airports=FakeIndex(),
        settings_path=tmp_path / "home" / "config.toml",
    )
    yield application
    mgr.close()


@pytest.mark.anyio
async def test_the_page_is_told_and_free_space_trims_them(app: Any, home: dict[str, Path]) -> None:
    with Store(home["store"], fsync=False) as s:
        relief, mesh = _relief(s), _mesh_artefact(s)
    _keep(home["store"], home["library"], home["tiles"], {"dem": relief, "mesh": mesh})
    async with client_for(app) as c:
        disk = (await c.get("/api/disk")).json()
        assert disk["twice_bytes"] > 0 and disk["compacted"] is False
        kept = (await c.post("/api/clean", json={})).json()
        assert kept["twice_freed_bytes"] == 0 and kept["compacted"] is False
        assert (await c.get("/api/disk")).json()["twice_bytes"] == disk["twice_bytes"]
        done = (await c.post("/api/clean", json={"compact": True})).json()
        assert done["twice_freed_bytes"] == disk["twice_bytes"] and done["compacted"] is True
        after = (await c.get("/api/disk")).json()
        assert after["twice_bytes"] == 0 and after["compacted"] is True


# -- what the store reads once ------------------------------------------------------------------


def _made(store: Store, rule: str, n: int, *, version: int = 1, dsf: str = "a" * 64) -> str:
    key, recipe = artifact_key(rule, version, {"tile": T.name}, {"dsf": dsf, "n": f"{n:064x}"})
    with store.begin(rule, key, "dir") as b:
        (b.out / "x").write_text(str(n))
        b.commit(version=version, recipe=recipe, inputs=[])
    return key


def test_the_last_build_is_found_from_rows_read_once_and_read_again_when_they_change(
    tmp_path: Path,
) -> None:
    """``last_made`` reads a rule's rows once while the index stays as it was: what this store
    writes, and what another process writes (another connection), is seen at the next ask; what
    went is not returned; the version and the inputs asked are kept to."""
    rule = "tile.textures"
    with Store(tmp_path / "store", fsync=False) as s, Store(tmp_path / "store", fsync=False) as o:
        assert s.last_made(rule, {"tile": T.name}, 1) is None
        first = _made(s, rule, 1)
        assert s.last_made(rule, {"tile": T.name}, 1) == first  # its own write
        time.sleep(0.01)
        second = _made(o, rule, 2, dsf="b" * 64)  # another process's
        assert s.last_made(rule, {"tile": T.name}, 1) == second
        assert s.last_made(rule, {"tile": T.name}, 1, {"dsf": "a" * 64}) == first
        assert s.last_made(rule, {"tile": T.name}, 1, {"dsf": "c" * 64}) is None
        newer = _made(o, rule, 3, version=2)
        assert s.last_made(rule, {"tile": T.name}, 1) == second  # made by a later version
        assert s.last_made(rule, {"tile": T.name}, 2) == newer
        o.delete(newer, force=True)
        o.delete(second, force=True)
        assert s.last_made(rule, {"tile": T.name}, 2) == first
        assert s.last_made(rule, {"tile": "+00+000"}, 2) is None


def test_a_file_s_digest_is_kept_while_it_stays_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane's scenery files were read again at each estimate. Kept by path while the file has
    the same size, times and inode: written again (even with its modification time put back),
    or replaced by another file, it is read again."""
    import orthostudio.graph.store as store_module

    read: list[Path] = []
    real = store_module.digest_file
    monkeypatch.setattr(store_module, "digest_file", lambda p: read.append(Path(p)) or real(p))
    dsf = tmp_path / "+46+006.dsf"
    dsf.write_bytes(b"A" * 1000)
    with Store(tmp_path / "store", fsync=False) as s:
        first = s.file_digest(dsf)
        assert s.file_digest(dsf) == first and len(read) == 1
    with Store(tmp_path / "store", fsync=False) as s:  # kept in the index, for the next Plan
        assert s.file_digest(dsf) == first and len(read) == 1
        stamp = dsf.stat()
        time.sleep(0.02)
        dsf.write_bytes(b"B" * 1000)  # same size
        os.utime(dsf, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))  # its time put back
        second = s.file_digest(dsf)
        assert second != first and len(read) == 2
        other = tmp_path / "other.dsf"
        other.write_bytes(b"A" * 1000)
        other.replace(dsf)  # another file under the name
        assert s.file_digest(dsf) == first and len(read) == 3


# -- read whole before anything goes ------------------------------------------------------------


def test_nothing_is_trimmed_from_an_artefact_that_is_not_as_built(tmp_path: Path) -> None:
    """Every file is read whole first: if the kept form, or the one that would go, is no longer
    what was built (a disk that failed), the artefact is left as it is, and the good form with
    it."""
    with Store(tmp_path / "store", fsync=False) as s:
        for damaged in ("dem.npy", f"Data{T.name}.alt"):
            key = _relief(s, 1 if damaged == "dem.npy" else 2)
            path = s.path(key) / damaged
            data = bytearray(path.read_bytes())
            data[-1] ^= 0x01
            path.write_bytes(bytes(data))
            assert s.trim(key, [f"Data{T.name}.alt"]) == 0
            assert (s.path(key) / f"Data{T.name}.alt").is_file() and s.trimmed(key) == {}


def test_a_trimmed_artefact_is_still_checked_whole(tmp_path: Path) -> None:
    """The digest of each file that went is kept: a check of the store gives the artefact's own
    digest again from the files left and those, and finds a file left that changed since."""
    with Store(tmp_path / "store", fsync=False) as s:
        key = _mesh_artefact(s)
        assert s.trim(key, [f"Data{T.name}.mesh"]) > 0
        assert s.content_digest(key) == s.digest_of(key)
        assert s.fsck(verify=True).clean
        npz = s.path(key) / "mesh.npz"
        data = bytearray(npz.read_bytes())
        data[-1] ^= 0x01
        npz.write_bytes(bytes(data))
        assert s.content_digest(key) != s.digest_of(key)
        assert s.fsck(verify=True).digest_mismatches == (key,)


def test_files_trimmed_in_two_goes_or_before_their_digests_were_kept(tmp_path: Path) -> None:
    """Trimmed a file at a time, the artefact stays provable; a row of the table's first form,
    without its digest, leaves the artefact as it is from then on, and unchecked."""
    with Store(tmp_path / "store", fsync=False) as s:
        key = _put(
            s, "orthostudio.mesh", 5,
            {"mesh.npz": b"n" * 100, "a.mesh": b"a" * 50, "b.mesh": b"b" * 60},
        )  # fmt: skip
        assert s.trim(key, ["a.mesh"]) == 50 and s.trim(key, ["b.mesh"]) == 60
        assert s.content_digest(key) == s.digest_of(key)
        other = _put(s, "orthostudio.mesh", 6, {"mesh.npz": b"n" * 10, "c.mesh": b"c" * 5})
        s._db.execute(
            "INSERT INTO trimmed (key, path, size, digest) VALUES (?, 'gone.mesh', 7, NULL)",
            (other,),
        )
        assert s.trim(other, ["c.mesh"]) == 0 and s.content_digest(other) is None
        assert s.fsck(verify=True).clean


# -- a review's findings, 2026-10-23 -------------------------------------------------------------


def test_a_file_number_too_big_for_an_sqlite_integer_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On ReFS (a Windows Dev Drive) a file's number is 128 bits: bound as an SQLite integer, it
    raised OverflowError at every Plan and build. It is kept as text; a row an earlier version
    wrote, with an integer, is read again once."""
    import orthostudio.graph.store as store_module

    read: list[Path] = []
    real_digest = store_module.digest_file
    monkeypatch.setattr(store_module, "digest_file", lambda p: read.append(p) or real_digest(p))
    dsf = tmp_path / "+46+006.dsf"
    dsf.write_bytes(b"A" * 1000)
    real_stat = Path.stat
    number = [2**63]

    def stat(self: Path, *a: Any, **kw: Any) -> Any:
        st = real_stat(self, *a, **kw)
        if self.name != dsf.name:
            return st
        return SimpleNamespace(
            st_size=st.st_size, st_mtime_ns=st.st_mtime_ns, st_ctime_ns=st.st_ctime_ns,
            st_ino=number[0], st_mode=st.st_mode,
        )  # fmt: skip

    monkeypatch.setattr(Path, "stat", stat)
    with Store(tmp_path / "store", fsync=False) as s:
        for big in (2**63, 2**64 + 5, 2**127 + 3):
            number[0] = big
            first = s.file_digest(dsf)
            assert s.file_digest(dsf) == first
        assert len(read) == 3  # each number once: another number is another file
        s._db.execute("UPDATE sources SET ino = 5")  # an earlier version's row
        number[0] = 5
        assert s.file_digest(dsf) == first and len(read) == 4
        assert s.file_digest(dsf) == first and len(read) == 4


def test_a_trim_asked_again_keeps_its_rows_and_takes_the_size_off_once(tmp_path: Path) -> None:
    """Interrupted after its rows were written, before its files went, then asked again; or asked
    by two stores at once, the other removing the file first: the rows stay, and the size in the
    index is taken off once."""
    alt = f"Data{T.name}.alt"
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        before = s.info(key)
        assert before is not None
        size = (before.path / alt).stat().st_size
        digest = digest_file(before.path / alt)
        with s._tx():  # what an interrupted trim left: its row and its size, the file still there
            s._db.execute(
                "INSERT INTO trimmed (key, path, size, digest) VALUES (?, ?, ?, ?)",
                (key, alt, size, digest),
            )
            s._db.execute("UPDATE artifacts SET size = size - ? WHERE key = ?", (size, key))
        s.trim(key, [alt])
        assert not (before.path / alt).exists() and s.trimmed(key) == {alt: size}
        assert s.info(key).size == before.size - size  # type: ignore[union-attr]
        assert s.content_digest(key) == before.digest

        other = _relief(s, 2)
        was = s.info(other)
        assert was is not None
        real = Path.unlink

        def first_removed_by_the_other(self: Path, *a: Any, **kw: Any) -> None:
            real(self, *a, **kw)
            raise FileNotFoundError(2, "removed by another trim", str(self))

        with pytest.MonkeyPatch.context() as m:
            m.setattr(Path, "unlink", first_removed_by_the_other)
            s.trim(other, [alt])
        assert s.trimmed(other) == {alt: size} and s.content_digest(other) == was.digest
        assert s.info(other).size == was.size - size  # type: ignore[union-attr]


def test_a_trim_of_an_artefact_deleted_meanwhile_does_nothing(tmp_path: Path) -> None:
    """Deleted by another process between the reading and the rows: nothing goes, nothing is
    listed, and no error."""
    alt = f"Data{T.name}.alt"
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        folder = s.path(key)
        real = s._trimmed_files

        def deleted_meanwhile(k: str) -> Any:
            found = real(k)
            other = sqlite3.connect(s.root / "index.sqlite")
            other.execute("DELETE FROM artifacts WHERE key = ?", (k,))
            other.commit()
            other.close()
            return found

        s._trimmed_files = deleted_meanwhile  # type: ignore[method-assign]
        assert s.trim(key, [alt]) == 0
        assert (folder / alt).is_file()


def test_a_trim_writes_its_rows_to_the_disk_before_any_file_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A power cut must not leave a file gone that the index does not list: the rows are
    committed with ``synchronous = FULL`` (and macOS's full sync), and the index goes back to its
    usual setting after."""
    alt = f"Data{T.name}.alt"
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        seen: list[tuple[int, int, bool]] = []
        real_tx = Store._tx

        @contextlib.contextmanager
        def tx(store: Store) -> Any:
            with real_tx(store) as db:
                sync = int(db.execute("PRAGMA synchronous").fetchone()[0])
                full = int(db.execute("PRAGMA fullfsync").fetchone()[0])
                yield db
                seen.append((sync, full, (s.path(key) / alt).exists()))

        monkeypatch.setattr(Store, "_tx", tx)
        s.trim(key, [alt])
        assert seen[0] == (2, 1, True)  # FULL, full sync, the file still there
        assert int(s._db.execute("PRAGMA synchronous").fetchone()[0]) == 1  # NORMAL again
        assert int(s._db.execute("PRAGMA fullfsync").fetchone()[0]) == 0


def test_a_check_of_the_store_never_deletes_what_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An artefact whose files cannot be read now (a disk that answers badly) is not taken for a
    damaged one: ``repair`` leaves it."""
    import orthostudio.graph.store as store_module

    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        real = store_module.digest_path

        def unreadable(path: Path, *a: Any, **kw: Any) -> Any:
            if Path(path) == s.path(key):
                raise PermissionError(13, "cannot be read now", str(path))
            return real(path, *a, **kw)

        monkeypatch.setattr(store_module, "digest_path", unreadable)
        report = s.fsck(verify=True, repair=True)
        assert key not in report.digest_mismatches and s.has(key)


def test_a_row_an_interrupted_trim_wrote_stays_when_its_file_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupted after its row, then asked again while another program holds the file: the row
    stays with its size taken off, and the next trim takes nothing off a second time (a review:
    the row went, its size stayed off, and the next trim took it off again)."""
    alt = f"Data{T.name}.alt"
    with Store(tmp_path / "store", fsync=False) as s:
        key = _relief(s)
        before = s.info(key)
        assert before is not None
        size = (before.path / alt).stat().st_size
        with s._tx():
            s._db.execute(
                "INSERT INTO trimmed (key, path, size, digest) VALUES (?, ?, ?, ?)",
                (key, alt, size, digest_file(before.path / alt)),
            )
            s._db.execute("UPDATE artifacts SET size = size - ? WHERE key = ?", (size, key))
        real = Path.unlink

        def held(self: Path, *a: Any, **kw: Any) -> None:
            if self.name == alt:
                raise PermissionError(13, "held by another program", str(self))
            real(self, *a, **kw)

        with pytest.MonkeyPatch.context() as m:
            m.setattr(Path, "unlink", held)
            s.trim(key, [alt])
        assert s.trimmed(key) == {alt: size}
        s.trim(key, [alt])
        assert not (before.path / alt).exists()
        assert s.info(key).size == before.size - size  # type: ignore[union-attr]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="rights a folder cannot lose")
def test_a_folder_that_cannot_be_listed_is_not_taken_for_an_empty_one(tmp_path: Path) -> None:
    """Walked past, it counted as empty: a check of the store took the artefact for a damaged one,
    which a repair deleted. It cannot be read: it is left."""
    with Store(tmp_path / "store", fsync=False) as s:
        key = _mesh_artefact(s)
        assert s.trim(key, [f"Data{T.name}.mesh"]) > 0
        folder = s.path(key)
        inner = folder / "inner"
        inner.mkdir(exist_ok=True)
        os.chmod(inner, 0)
        try:
            assert s.content_digest(key) is None
            report = s.fsck(verify=True, repair=True)
            assert key not in report.digest_mismatches and s.has(key)
        finally:
            os.chmod(inner, 0o755)
