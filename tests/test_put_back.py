# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""A tile filed elsewhere, built again (the atelier, step 4): ``filing.tile_home``, which says
where the new version goes, ``filing.put_back``, which puts it there in place of the old one, and
the build's ``_put_back_tile``, which does it at the end of a build. Temporary folders and fake
X-Planes only; nothing of the machine's is read or written."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import test_api_fakes as fakes
from orthostudio.api.jobs import CancelRequested, Job
from orthostudio.errors import OsxpError
from orthostudio.graph import Store
from orthostudio.imagery.providers import load_registry
from orthostudio.install import Library, default_library_path, packs
from orthostudio.model import ArtifactRef, TileRef
from orthostudio.pipeline import build as build_mod
from orthostudio.pipeline import filing, native
from orthostudio.pipeline.build import PUT_BACK_ROLE, BuildEnv, BuildSpec, _put_back_tile
from orthostudio.pipeline.filing import (
    OLD_SUFFIX,
    PART_SUFFIX,
    FilingStoppedError,
    put_back,
    tile_home,
)
from orthostudio.pipeline.pack import (
    PARKED_OVERLAY,
    ArtefactEntry,
    PackManifest,
    install_receipt,
    links_to,
    overlay_dsf_path,
    read_manifest,
    write_pack,
)
from orthostudio.sched import Done, Failed, Progress, Scheduler, Started
from test_library_find import NAME, T, _pack

home = fakes.home
xplane = fakes.xplane

NEW_ROADS = b"XPLNEDSF the new build's roads"


def _builds(home: Path, tmp_path: Path) -> tuple[Path, Path]:
    """+43+005 filed in Alps (build k1, its roads beside it), and a new build of it in the
    workshop (k2, other bytes in a texture, other roads)."""
    old = _pack(tmp_path / "Alps", key="k1")
    new = _pack(home / "tiles", key="k2")
    (new / "textures" / "a.dds").write_bytes(b"DDS " + b"\2" * 64)
    overlay_dsf_path(new.parent, T).write_bytes(NEW_ROADS)
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, old, "osxp", {"dsf": "k1"})
    return old, new


def _keys(pack: Path) -> dict[str, Any]:
    return read_manifest(pack).keys


def _other_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filing, "same_disk", lambda a, b: False)


# -- where the new version goes ----------------------------------------------------------------


def test_the_new_version_goes_where_x_plane_takes_the_tile(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """The folder X-Plane takes; else the workshop when the Library lists the tile there; else
    the latest of its folders elsewhere. A folder gone from a disk that is here does not count;
    one on a disk away does, for the build to refuse it."""
    cs = xplane / "Custom Scenery"
    atelier = home / "tiles"

    def where() -> tuple[Path | None, list[Path]]:
        return tile_home(T, atelier=atelier, custom_scenery=cs, library_path=default_library_path())

    assert where() == (None, [])  # a new tile
    alps = _pack(tmp_path / "Alps", key="k1")
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, alps, "osxp", {"dsf": "k1"})
    assert where() == (alps, [])  # filed elsewhere
    here = _pack(atelier, key="k1")
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, here, "osxp", {"dsf": "k1"})
    assert where() == (None, [])  # in the workshop too, X-Plane taking neither
    install_receipt(alps, cs, tile=T, library_path=default_library_path())
    assert where() == (alps, [])  # X-Plane takes the one filed
    install_receipt(here, cs, tile=T, library_path=default_library_path())
    assert where() == (None, [])  # X-Plane takes the workshop's
    with Library(default_library_path()) as lib:
        lib.forget(T, kind="ortho", path=here)
    (cs / NAME).unlink()
    dolomites = _pack(tmp_path / "Dolomites", key="k1")
    time.sleep(0.01)
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, dolomites, "osxp", {"dsf": "k1"})
    assert where() == (dolomites, [])  # the latest of two
    gone = tmp_path / "Gone" / NAME
    with Library(default_library_path()) as lib:
        lib.forget(T, kind="ortho", path=alps)
        lib.forget(T, kind="ortho", path=dolomites)
        lib.register(T, "BI", 16, gone, "osxp", {"dsf": "k1"})
    assert where() == (None, [gone])  # gone: built in the workshop, its row follows
    away = Path("Q:\\Tiles" if sys.platform == "win32" else "/Volumes/No such disk/Tiles") / NAME
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, away, "osxp", {"dsf": "k1"})
    assert where() == (away, [gone])  # on a disk away: the build refuses it


# -- putting it back ---------------------------------------------------------------------------


def test_on_another_disk_the_new_version_is_copied_read_back_and_takes_the_old_one_s_place(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copied under the temporary name, read back, then in place of the old version, which goes;
    its new roads where the old ones were drawn; the workshop keeps nothing of it."""
    _other_disk(monkeypatch)
    old, new = _builds(home, tmp_path)
    heard: list[tuple[str, int, int]] = []
    got = put_back(new, old, tile=T, overlay=True, progress=lambda *step: heard.append(step))
    assert got == {"tile": T.name, "to": str(old), "moved": False, "left": None}
    assert _keys(old) == {"dsf": "k2"} and (old / "textures" / "a.dds").read_bytes()[4:5] == b"\2"
    assert overlay_dsf_path(old.parent, T).read_bytes() == NEW_ROADS
    assert not new.exists() and not overlay_dsf_path(new.parent, T).exists()
    assert not old.with_name(NAME + PART_SUFFIX).exists()
    assert not old.with_name(NAME + OLD_SUFFIX).exists()
    assert {phase for phase, _d, _t in heard} == {"copy", "check"}


def test_on_the_workshop_s_disk_the_new_version_is_moved_in_one_step(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """Its images stay the cache's files: nothing more on the disk."""
    old, new = _builds(home, tmp_path)
    inode = (new / "textures" / "a.dds").stat().st_ino
    got = put_back(new, old, tile=T, overlay=True)
    assert got["moved"] is True and (old / "textures" / "a.dds").stat().st_ino == inode
    assert _keys(old) == {"dsf": "k2"} and not new.exists()
    assert overlay_dsf_path(old.parent, T).read_bytes() == NEW_ROADS


def test_its_new_roads_are_parked_in_it_when_the_old_ones_were_not_drawn(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """A tile in no X-Plane has its roads parked in its folder: the new ones too, none beside."""
    old, new = _builds(home, tmp_path)
    beside = overlay_dsf_path(old.parent, T)
    os.replace(beside, old / PARKED_OVERLAY)
    put_back(new, old, tile=T, overlay=True)
    assert (old / PARKED_OVERLAY).read_bytes() == NEW_ROADS and not beside.exists()


def test_a_new_version_without_roads_takes_the_old_ones_away(
    home: Path, xplane: Path, tmp_path: Path
) -> None:
    """Built without roads (simHeaven X-World's instead): the old ones are not drawn any more."""
    old, new = _builds(home, tmp_path)
    overlay_dsf_path(new.parent, T).unlink()
    put_back(new, old, tile=T, overlay=False)
    assert _keys(old) == {"dsf": "k2"} and not overlay_dsf_path(old.parent, T).exists()


@pytest.mark.parametrize("why", ["xplane", "room", "stop", "read back"])
def test_a_put_back_that_cannot_end_leaves_the_old_version_and_the_new_one_in_the_workshop(
    why: str, home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane running, no room on that disk, a Stop, a copy that reads back wrong: the old
    version is as it was, the new one waits in the workshop, its new roads parked in it, where
    X-Plane does not read them (left beside it, they were drawn with the old ones: twice), and no
    temporary folder is left."""
    _other_disk(monkeypatch)
    old, new = _builds(home, tmp_path)
    stop = None
    if why == "xplane":
        monkeypatch.setattr(packs, "xplane_running", lambda: True)
    elif why == "room":
        monkeypatch.setattr(
            filing.shutil,
            "disk_usage",
            lambda p: filing.shutil._ntuple_diskusage(10**12, 10**12, 10),
        )
    elif why == "stop":
        stop = lambda: True  # noqa: E731
    else:
        real = filing.send_to_disk

        def worn(p: Path) -> None:
            if p.suffix == ".dds":
                p.write_bytes(b"DDS worn")
            real(p)

        monkeypatch.setattr(filing, "send_to_disk", worn)
    expected = {
        "xplane": "SYS_PUT_BACK_XP_RUNNING",
        "room": "SYS_DISK_FULL",
        "read back": "SYS_TILE_COPY_DIFFERS",
    }
    with pytest.raises((OsxpError, FilingStoppedError)) as err:
        put_back(new, old, tile=T, overlay=True, stop=stop)
    if why == "stop":
        assert isinstance(err.value, FilingStoppedError)
    else:
        assert isinstance(err.value, OsxpError) and err.value.code == expected[why]
    assert _keys(old) == {"dsf": "k1"} and _keys(new) == {"dsf": "k2"}
    assert overlay_dsf_path(old.parent, T).read_bytes() != NEW_ROADS
    assert not overlay_dsf_path(new.parent, T).exists()
    assert (new / PARKED_OVERLAY).read_bytes() == NEW_ROADS
    assert not old.with_name(NAME + PART_SUFFIX).exists()
    assert not old.with_name(NAME + OLD_SUFFIX).exists()


def test_a_move_that_cannot_end_goes_back_to_the_workshop(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On one disk the new version is moved first: refused at the swap (a file of the old one
    held), it is moved back with its roads parked in it, and the old one is as it was."""
    old, new = _builds(home, tmp_path)
    real = filing.replace

    def held(src: Path, dst: Path) -> None:
        if Path(src) == old:
            raise PermissionError(13, "held by another program", str(src))
        real(src, dst)

    monkeypatch.setattr(filing, "replace", held)
    with pytest.raises(PermissionError):
        put_back(new, old, tile=T, overlay=True)
    assert _keys(old) == {"dsf": "k1"} and _keys(new) == {"dsf": "k2"}
    assert (new / PARKED_OVERLAY).read_bytes() == NEW_ROADS
    assert not overlay_dsf_path(new.parent, T).exists()
    assert not old.with_name(NAME + PART_SUFFIX).exists()


def test_a_put_back_cut_between_its_two_renames_finds_the_old_version_first(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A power cut between the old version's rename and the new one's: the old one is put back
    under its name, then the new one takes its place as usual."""
    _other_disk(monkeypatch)
    old, new = _builds(home, tmp_path)
    os.replace(old, old.with_name(NAME + OLD_SUFFIX))
    # refused next (X-Plane running): the old version is under its name again all the same
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    with pytest.raises(OsxpError):
        put_back(new, old, tile=T, overlay=True)
    assert _keys(old) == {"dsf": "k1"} and not old.with_name(NAME + OLD_SUFFIX).exists()
    os.replace(old, old.with_name(NAME + OLD_SUFFIX))
    monkeypatch.setattr(packs, "xplane_running", lambda: False)
    put_back(new, old, tile=T, overlay=True)
    assert _keys(old) == {"dsf": "k2"} and not old.with_name(NAME + OLD_SUFFIX).exists()


# -- at the end of a build ---------------------------------------------------------------------


def _nodes(
    home: Path, cs: Path | None, manifest_file: Path, roads: Path | None = None
) -> tuple[Any, Any, Any]:
    """What ``_put_back_tile`` reads of a build: its spec, its pack node's outcome (and its overlay
    node's, the roads' DSF in the store, when ``roads`` is given), the env."""
    spec = BuildSpec(tile=T, provider="BI", zl=16, out_dir=home / "tiles", custom_scenery=cs)
    pack_id = f"{T.name}/BI16/pack"
    overlay = None if roads is None else SimpleNamespace(id=f"{T.name}/overlay")
    nodes = SimpleNamespace(
        spec=spec, pack=SimpleNamespace(id=pack_id), overlay=overlay, by_role={}
    )
    done = {pack_id: SimpleNamespace(key="a" * 64, ref=SimpleNamespace(path=manifest_file))}
    if overlay is not None:
        done[overlay.id] = SimpleNamespace(key="c" * 64, ref=SimpleNamespace(path=roads))
    collector = SimpleNamespace(done=done)
    env = SimpleNamespace(library_path=default_library_path(), store=None)
    return nodes, collector, env


def _lines(cs: Path) -> list[str]:
    text = (cs / "scenery_packs.ini").read_text()
    return [line for line in text.splitlines() if line.startswith("SCENERY_PACK")]


def test_a_build_puts_the_tile_back_x_plane_s_line_as_it_was_and_one_library_row(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tile X-Plane shows from Alps, disabled there by the user, built again: the new version
    is in Alps, X-Plane's link and line as they were, the Library lists it once, in Alps, as the
    new build, and the workshop keeps nothing of it. Built again unchanged, nothing is copied."""
    _other_disk(monkeypatch)
    cs = xplane / "Custom Scenery"
    old, new = _builds(home, tmp_path)
    install_receipt(old, cs, tile=T, library_path=default_library_path())
    ini = cs / "scenery_packs.ini"
    line = f"Custom Scenery/{NAME}/"
    ini.write_text(ini.read_text().replace(f"SCENERY_PACK {line}", f"SCENERY_PACK_DISABLED {line}"))
    before = _lines(cs)
    with Library(default_library_path()) as lib:  # a copy the workshop listed too
        lib.register(T, "BI", 16, new, "osxp", {"dsf": "k2"})
    manifest_file = tmp_path / "pack-artefact.toml"
    manifest_file.write_text((new / "orthostudio.toml").read_text())
    nodes, collector, env = _nodes(home, cs, manifest_file)
    events: list[Any] = []
    repaired, installed, failure = _put_back_tile(
        nodes, env, collector, old, install=False, on_event=events.append, stopped=[False]
    )
    assert failure is None and repaired == [] and installed is True
    assert _keys(old) == {"dsf": "k2"} and not new.exists() and links_to(cs / NAME, old)
    assert _lines(cs) == before  # the line disabled, in its place
    with Library(default_library_path()) as lib:
        rows = [(r.path, r.keys) for r in lib.list(tile=T, kind="ortho")]
    assert rows == [(old, {"dsf": "k2"})]
    node = f"{T.name}/BI16/{PUT_BACK_ROLE}"
    kinds = [type(e) for e in events if e.node_id == node]
    assert kinds[0] is Started and kinds[-1] is Done and Progress in kinds
    # built again unchanged: its folder holds this build, nothing is copied, a copy the workshop
    # was given (a hit writes the pack there again) goes
    shutil.copytree(old, new)
    events.clear()
    _put_back_tile(
        nodes, env, collector, old, install=False, on_event=events.append, stopped=[False]
    )
    assert not new.exists() and _keys(old) == {"dsf": "k2"}
    assert [type(e) for e in events] == [Started, Done]


def test_a_stop_at_the_end_of_a_build_leaves_the_old_version(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job's Stop raises in its callback: the put back stops before its next file, says it
    was stopped, and the next tiles are not put back either."""
    _other_disk(monkeypatch)
    old, new = _builds(home, tmp_path)
    manifest_file = tmp_path / "pack-artefact.toml"
    manifest_file.write_text((new / "orthostudio.toml").read_text())
    nodes, collector, env = _nodes(home, None, manifest_file)
    events: list[Any] = []

    def job(event: Any) -> None:
        events.append(event)
        if isinstance(event, Progress):
            raise CancelRequested("the job")  # the job's own Stop, a BaseException

    stopped = [False]
    _r, _i, failure = _put_back_tile(
        nodes, env, collector, old, install=False, on_event=job, stopped=stopped
    )
    assert failure is not None and failure.error is not None
    assert failure.error["code"] == "SYS_CANCELLED" and stopped == [True]
    assert _keys(old) == {"dsf": "k1"} and _keys(new) == {"dsf": "k2"}
    assert isinstance(events[-1], Failed)


def test_the_rows_of_a_tile_s_folders_gone_follow_it_to_the_workshop(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile whose folder is gone from a disk that is here is built in the workshop as a new
    one, through the build: its rows of that folder go once it is, and it is listed there."""
    gone = Path(home) / "Gone" / NAME
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 14, gone, "osxp", {"dsf": "k1"})
        lib.register(T, "", 0, gone.parent / "yOrthoStudio_Overlays", "osxp", None, kind="overlay")
    here = _pack_of(T, home / "tiles", "k3")
    specs = _specs(home, xplane, tmp_path, (T,))
    report = _build(specs, {f"{T.name}/BI14/pack": _artefact(here, tmp_path, "t")}, tmp_path,
                    monkeypatch)  # fmt: skip
    assert report.tiles[0].ok
    with Library(default_library_path()) as lib:
        assert [r.path for r in lib.list(tile=T)] == [here]


# -- the build itself ------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path) -> BuildEnv:
    gs = tmp_path / "Global Scenery"
    p = gs / T.dsf_relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"XPLNEDSF-not-really")
    return BuildEnv(
        store=Store(tmp_path / "store", fsync=False),
        store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks",
        workdir=tmp_path / "work",
        global_scenery=gs,
        dsftool=None,
        registry=load_registry(),
        workers=1,
        library_path=tmp_path / "library.sqlite",
    )


def _no_network(tile: Any, layers: Any) -> dict[str, Any]:
    raise OsxpError("NET_CONNECTION_FAILED", context={"url": "overpass", "reason": "test"})


def _spec(tmp_path: Path, **kw: Any) -> BuildSpec:
    return BuildSpec(
        tile=T, provider="BI", zl=14, out_dir=tmp_path / "out", store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks", workdir=tmp_path / "work", relief="xplane", **kw,
    )  # fmt: skip


def test_a_tile_filed_on_a_disk_away_is_not_built(tmp_path: Path, env: BuildEnv) -> None:
    """Nothing of it runs; it says why, in the Installation cell's step."""
    away = Path("Q:\\Tiles" if sys.platform == "win32" else "/Volumes/No such disk/Tiles") / NAME
    with Library(env.library_path) as lib:
        lib.register(T, "BI", 14, away, "osxp", {"dsf": "k1"})
    events: list[Any] = []
    report = build_mod.build_tiles(
        [_spec(tmp_path)], on_event=events.append, cpu_in_threads=True, env=env,
        handle_sigint=False,
    )  # fmt: skip
    (tile,) = report.tiles
    assert not tile.ok and tile.error is not None and tile.error.role == PUT_BACK_ROLE
    assert tile.error.error is not None and tile.error.error["code"] == "SYS_TILE_DISK_ABSENT"
    assert not any(isinstance(e, Started) for e in events)  # nothing of it ran


def test_a_tile_filed_elsewhere_is_never_installed_from_the_workshop(
    tmp_path: Path, env: BuildEnv, xplane: Path
) -> None:
    """Asked to install, its graph has no install: X-Plane keeps leading to its folder, which
    the put back fills."""
    alps = _pack(tmp_path / "Alps", key="k1")
    with Library(env.library_path) as lib:
        lib.register(T, "BI", 14, alps, "osxp", {"dsf": "k1"})
    events: list[Any] = []
    spec = _spec(tmp_path, install=True, custom_scenery=xplane / "Custom Scenery")
    with native.osm_job(native.OsmJob(fetch=_no_network)):
        build_mod.build_tiles(
            [spec], on_event=events.append, cpu_in_threads=True, env=env, handle_sigint=False
        )
    declared = [e for e in events if isinstance(e, build_mod.Phase) and e.nodes]
    assert declared and not any(n.endswith("/install") for n, _k, _r in declared[-1].nodes)


# -- the review of 2026-10-05: each point through the path the app takes -----------------------

W = TileRef(43, 4)


def _pack_of(tile: TileRef, where: Path, key: str) -> Path:
    """A pack of ``tile`` as a build writes it into ``where``, without roads, at ZL14."""
    src = where.parent / f"src-{tile.name}-{key}"
    (src / "dsf" / "terrain").mkdir(parents=True)
    (src / "dsf" / f"{tile.name}.dsf").write_bytes(b"XPLNEDSF" + b"\0" * 100)
    (src / "dsf" / "terrain" / "a.ter").write_text("A\n800\nTERRAIN\n")
    (src / "tex" / "textures").mkdir(parents=True)
    (src / "tex" / "textures" / "a.dds").write_bytes(b"DDS " + b"\1" * 64)
    files = write_pack(
        where, tile, dsf_dir=src / "dsf", textures_dir=src / "tex", overlay_file=None
    )
    listed = {
        "dsf": tile.dsf_relpath.as_posix(), "dsf_size": 108, "textures": 1, "terrain": 1,
        "overlay": "", "cfg": "",
    }  # fmt: skip
    entry = ArtefactEntry(key, "d" * 64, "tile.dsf@1")
    manifest = PackManifest(tile.name, "BI", 14, {"dsf": entry}, listed)
    (files.pack_dir / "orthostudio.toml").write_text(manifest.to_toml())
    return files.pack_dir


def _artefact(pack: Path, tmp_path: Path, label: str) -> Path:
    """The pack node's artefact of ``pack``: its manifest, at ZL14 as the build's specs."""
    text = (pack / "orthostudio.toml").read_text().replace("zl = 16", "zl = 14")
    (pack / "orthostudio.toml").write_text(text)
    artefact = tmp_path / f"artefact-{label}.toml"
    artefact.write_text(text)
    return artefact


def _specs(home: Path, xplane: Path, tmp_path: Path, tiles: tuple[TileRef, ...]) -> list[BuildSpec]:
    return [
        BuildSpec(
            tile=tile,
            provider="BI",
            zl=14,
            out_dir=home / "tiles",
            store_root=tmp_path / "store",
            chunks_root=tmp_path / "chunks",
            workdir=tmp_path / "work",
            relief="xplane",
            install=False,
            custom_scenery=xplane / "Custom Scenery",
            overlay=False,
            xp12_rasters=False,
        )
        for tile in tiles
    ]


def _build(
    specs: list[BuildSpec],
    artefacts: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    on_event: Any = None,
) -> Any:
    """``build_tiles`` as the app runs it, its scheduler answering every node as already built
    (the pack nodes with the manifests of ``artefacts``): the end of the build, the put back and
    the job's events are the real ones."""
    gs = tmp_path / "Global Scenery"
    for spec in specs:
        dsf = gs / spec.tile.dsf_relpath
        dsf.parent.mkdir(parents=True, exist_ok=True)
        dsf.write_bytes(b"XPLNEDSF-not-really")
    env = BuildEnv(
        store=Store(tmp_path / "store", fsync=False), store_root=tmp_path / "store",
        chunks_root=tmp_path / "chunks", workdir=tmp_path / "work", global_scenery=gs,
        dsftool=None, registry=load_registry(), workers=1, library_path=default_library_path(),
    )  # fmt: skip
    dummy = tmp_path / "dummy"
    dummy.write_text("x")

    async def every_node_built(self: Any, targets: Any, on_event: Any = None) -> dict[str, Any]:
        for node in list(self.nodes.values()):
            path = artefacts.get(node.id, dummy)
            ref = ArtifactRef("a" * 64, "b" * 64, path, node.rule.name, "file")
            on_event(Done(node.id, "a" * 64, True, 0.0, ref))
        return {}

    monkeypatch.setattr(Scheduler, "run", every_node_built)
    monkeypatch.setattr(build_mod, "snapshot_label_of", lambda p: "x")
    with native.osm_job(native.OsmJob(fetch=lambda tile, layers: {})):
        return build_mod.build_tiles(
            specs, on_event=on_event, cpu_in_threads=True, env=env, handle_sigint=False
        )


def _filed_and_workshop(
    home: Path, xplane: Path, tmp_path: Path
) -> tuple[Path, Path, list[BuildSpec], dict[str, Path]]:
    """+43+005 filed in Alps and changed (its new build in the workshop, its new roads beside
    it), then +43+004, a tile of the workshop built without install, after it in the batch."""
    old, new = _builds(home, tmp_path)
    w = _pack_of(W, home / "tiles", "w1")
    artefacts = {
        f"{T.name}/BI14/pack": _artefact(new, tmp_path, "t"),
        f"{W.name}/BI14/pack": _artefact(w, tmp_path, "w"),
    }
    return old, new, _specs(home, xplane, tmp_path, (T, W)), artefacts


def test_1_the_job_s_stop_while_a_tile_is_put_back_ends_the_build_stopped(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job's real Stop (CancelRequested, a BaseException) raised while +43+005 is put back
    escaped the build: its report was lost and the tiles after it never settled. It ends the
    build, stopped: the tile after it is in the Library, the filed one is where it was, the new
    version waits in the workshop, its roads parked in it, and Works says stopped, not done."""
    _other_disk(monkeypatch)
    old, new, specs, artefacts = _filed_and_workshop(home, xplane, tmp_path)
    job = Job(specs, install=False, request=None, journal_path=tmp_path / "job.jsonl")

    def on_event(event: Any) -> None:
        job.on_event(event)
        if isinstance(event, Started) and event.node_id.endswith(f"/{PUT_BACK_ROLE}"):
            job.cancel()  # Stop pressed while the tile is copied back

    report = _build(specs, artefacts, tmp_path, monkeypatch, on_event)
    assert report.cancelled
    with Library(default_library_path()) as lib:
        assert len(lib.list(tile=W, kind="ortho")) == 1
    assert _keys(old) == {"dsf": "k1"} and _keys(new) == {"dsf": "k2"}
    assert (new / PARKED_OVERLAY).read_bytes() == NEW_ROADS
    assert not overlay_dsf_path(new.parent, T).exists()
    assert job._tiles[T.name].status("cancelled") == "cancelled"


def test_2_a_put_back_x_plane_refuses_leaves_no_roads_drawn_twice(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Plane running at the end of the build: the put back waits, with words of its own, the
    failure is counted, and the new roads are parked in the workshop's version, not drawn from
    the workshop's overlays pack beside the old ones."""
    _other_disk(monkeypatch)
    old, new, specs, artefacts = _filed_and_workshop(home, xplane, tmp_path)
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    report = _build(specs, artefacts, tmp_path, monkeypatch)
    tile = next(t for t in report.tiles if t.tile == T.name)
    assert not tile.ok and tile.error is not None and tile.error.error is not None
    assert tile.error.error["code"] == "SYS_PUT_BACK_XP_RUNNING" and report.failed == 1
    assert _keys(old) == {"dsf": "k1"} and not overlay_dsf_path(new.parent, T).exists()
    assert (new / PARKED_OVERLAY).read_bytes() == NEW_ROADS


def test_3_built_again_unchanged_while_x_plane_runs_it_is_already_there(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filed tile X-Plane shows, built again unchanged while X-Plane runs: nothing to do, not a
    failure (its install was asked again, which X-Plane running refuses); it says already there,
    and roads of it parked go beside it, no link or line of X-Plane changed."""
    cs = xplane / "Custom Scenery"
    old, new = _builds(home, tmp_path)
    put_back(new, old, tile=T, overlay=True)  # its folder holds the new build
    install_receipt(old, cs, tile=T, library_path=default_library_path())
    before = _lines(cs)
    os.replace(overlay_dsf_path(old.parent, T), old / PARKED_OVERLAY)  # its roads parked
    manifest_file = tmp_path / "pack-artefact.toml"
    manifest_file.write_text((old / "orthostudio.toml").read_text())
    nodes, collector, env = _nodes(home, cs, manifest_file)
    monkeypatch.setattr(packs, "xplane_running", lambda: True)
    events: list[Any] = []
    _r, installed, failure = _put_back_tile(
        nodes, env, collector, old, install=False, on_event=events.append, stopped=[False]
    )
    assert failure is None and installed is True
    assert [type(e) for e in events] == [Started, Done] and events[-1].hit is True
    assert overlay_dsf_path(old.parent, T).read_bytes() == NEW_ROADS and _lines(cs) == before


def test_4_x_plane_started_during_the_copy_is_seen_before_the_swap(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closed when the copy starts, X-Plane runs when it ends: the old version stays, the new one
    waits in the workshop; it was put in place, then refused."""
    _other_disk(monkeypatch)
    old, new = _builds(home, tmp_path)
    answers = iter([False, True])
    monkeypatch.setattr(packs, "xplane_running", lambda: next(answers, True))
    with pytest.raises(OsxpError) as err:
        put_back(new, old, tile=T, overlay=True)
    assert err.value.code == "SYS_PUT_BACK_XP_RUNNING"
    assert _keys(old) == {"dsf": "k1"} and _keys(new) == {"dsf": "k2"}
    assert not old.with_name(NAME + PART_SUFFIX).exists()


def test_5_built_again_its_lost_roads_come_back(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found again without its roads, a filed tile built again unchanged has them back, as the
    page promises: from the store, parked in it, put beside it by its next install."""
    old, new = _builds(home, tmp_path)
    put_back(new, old, tile=T, overlay=True)
    overlay_dsf_path(old.parent, T).unlink()  # lost
    roads = tmp_path / "roads.dsf"
    roads.write_bytes(NEW_ROADS)
    manifest_file = tmp_path / "pack-artefact.toml"
    manifest_file.write_text((old / "orthostudio.toml").read_text())
    nodes, collector, env = _nodes(home, None, manifest_file, roads=roads)
    repaired, _i, failure = _put_back_tile(
        nodes, env, collector, old, install=False, on_event=None, stopped=[False]
    )
    assert failure is None and repaired == ["roads"]
    assert (old / PARKED_OVERLAY).read_bytes() == NEW_ROADS


def test_6_a_put_back_cut_between_its_renames_is_mended_by_the_next_build(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old version left under its temporary name, its folder gone: the next build gives it its
    name back, then puts the new version in; its row stays, nothing is left behind."""
    _other_disk(monkeypatch)
    old, _new, specs, artefacts = _filed_and_workshop(home, xplane, tmp_path)
    os.replace(old, old.with_name(NAME + OLD_SUFFIX))
    report = _build(specs, artefacts, tmp_path, monkeypatch)
    assert next(t for t in report.tiles if t.tile == T.name).ok
    assert _keys(old) == {"dsf": "k2"} and not old.with_name(NAME + OLD_SUFFIX).exists()
    with Library(default_library_path()) as lib:
        assert [r.path for r in lib.list(tile=T, kind="ortho")] == [old]


def test_7_an_old_version_a_file_kept_is_said_then_taken_away(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file held in the old version kept it, GBs nothing listed: the build says where it is,
    and the next one, the tile's folder whole, takes it away."""
    _other_disk(monkeypatch)
    old, new = _builds(home, tmp_path)
    aside = old.with_name(NAME + OLD_SUFFIX)
    real, real_rmtree = filing._delete_pack_dir, shutil.rmtree

    def held(folder: Path) -> None:
        if Path(folder) == aside:
            raise PermissionError(13, "held by another program", str(folder))
        real(folder)

    def held_tree(folder: Any, *args: Any, **kwargs: Any) -> None:
        if Path(folder) == aside:
            raise PermissionError(13, "held by another program", str(folder))
        real_rmtree(folder, *args, **kwargs)

    monkeypatch.setattr(filing, "_delete_pack_dir", held)
    monkeypatch.setattr(shutil, "rmtree", held_tree)
    manifest_file = tmp_path / "pack-artefact.toml"
    manifest_file.write_text((new / "orthostudio.toml").read_text())
    nodes, collector, env = _nodes(home, None, manifest_file)
    events: list[Any] = []
    _put_back_tile(
        nodes, env, collector, old, install=False, on_event=events.append, stopped=[False]
    )
    said = [e.message for e in events if isinstance(e, Progress) and "stays in" in e.message]
    assert said and str(aside) in said[0] and aside.is_dir()
    monkeypatch.setattr(filing, "_delete_pack_dir", real)
    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    _put_back_tile(nodes, env, collector, old, install=False, on_event=None, stopped=[False])
    assert not aside.exists() and _keys(old) == {"dsf": "k2"}


def test_8_what_follows_the_swap_cannot_fail_the_put_back(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new version in place, its roads not put beside it (a file held): it is filed, its
    roads parked in it for its next install, and the put back has not failed."""
    _other_disk(monkeypatch)
    old, new = _builds(home, tmp_path)

    def held(pack: Path, tile: TileRef) -> None:
        raise PermissionError(13, "held by another program", str(pack))

    monkeypatch.setattr(filing, "_unpark_overlay", held)
    got = put_back(new, old, tile=T, overlay=True)
    assert got["left"] is None and _keys(old) == {"dsf": "k2"}
    assert (old / PARKED_OVERLAY).read_bytes() == NEW_ROADS


def test_9_a_tile_on_a_disk_away_shows_nothing_of_it_done(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not built, its map data reads neither done nor already there."""
    away = Path("Q:\\\\Tiles" if sys.platform == "win32" else "/Volumes/No such disk/Tiles") / NAME
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 14, away, "osxp", {"dsf": "k1"})
    specs = _specs(home, xplane, tmp_path, (T,))
    job = Job(specs, install=False, request=None, journal_path=tmp_path / "job.jsonl")
    _build(specs, {}, tmp_path, monkeypatch, job.on_event)
    rows = {n.role: n.status for n in job._tiles[T.name].nodes.values()}
    assert rows == {PUT_BACK_ROLE: "failed"}


def test_11_the_put_back_weighs_what_its_copy_takes(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declared with the build's nodes, the put back weighs its bytes copied then read back on
    another disk (it weighed a second: Works read 100 % and no time left during a copy of
    minutes), a moment on the workshop's disk; and the tile is in the build until it is back."""
    old, _new, specs, artefacts = _filed_and_workshop(home, xplane, tmp_path)
    monkeypatch.setattr(build_mod, "same_disk", lambda a, b: False)
    monkeypatch.setattr(build_mod, "pack_bytes", lambda p: 4_000_000_000)
    job = Job(specs, install=False, request=None, journal_path=tmp_path / "job.jsonl")
    phases: list[Any] = []

    def on_event(event: Any) -> None:
        if isinstance(event, build_mod.Phase) and event.nodes:
            phases.append(event)
            job.on_event(event)
            node = job._tiles[T.name].nodes[f"{T.name}/BI14/{PUT_BACK_ROLE}"]
            assert node.status == "pending" and T.name in job.unfinished_tiles()
            return
        job.on_event(event)

    _build(specs, artefacts, tmp_path, monkeypatch, on_event)
    node_id = f"{T.name}/BI14/{PUT_BACK_ROLE}"
    assert (node_id, "io", PUT_BACK_ROLE) in phases[0].nodes
    assert (node_id, 80.0) in phases[0].learned
    monkeypatch.setattr(build_mod, "same_disk", lambda a, b: True)
    _rows, seconds = build_mod._put_back_rows(specs, {T.name: old})
    assert seconds == ((node_id, 1.0),)
