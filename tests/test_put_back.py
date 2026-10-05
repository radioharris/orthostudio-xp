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
from orthostudio.errors import OsxpError
from orthostudio.graph import Store
from orthostudio.imagery.providers import load_registry
from orthostudio.install import Library, default_library_path, packs
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
    install_receipt,
    links_to,
    overlay_dsf_path,
    read_manifest,
)
from orthostudio.sched import Done, Failed, Progress, Started
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
    version is as it was, the new one waits in the workshop, no temporary folder is left."""
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
        "xplane": "XP_RUNNING",
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
    assert overlay_dsf_path(new.parent, T).read_bytes() == NEW_ROADS
    assert not old.with_name(NAME + PART_SUFFIX).exists()
    assert not old.with_name(NAME + OLD_SUFFIX).exists()


def test_a_move_that_cannot_end_goes_back_to_the_workshop(
    home: Path, xplane: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On one disk the new version is moved first: refused at the swap (a file of the old one
    held), it is moved back, without the roads it was given, and the old one is as it was."""
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
    assert not (new / PARKED_OVERLAY).exists() and not old.with_name(NAME + PART_SUFFIX).exists()


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


def _nodes(home: Path, cs: Path | None, manifest_file: Path) -> tuple[Any, Any, Any]:
    """What ``_put_back_tile`` reads of a build: its spec, its pack node's outcome, the env."""
    spec = BuildSpec(tile=T, provider="BI", zl=16, out_dir=home / "tiles", custom_scenery=cs)
    pack_id = f"{T.name}/BI16/pack"
    nodes = SimpleNamespace(spec=spec, pack=SimpleNamespace(id=pack_id), by_role={})
    done = SimpleNamespace(key="a" * 64, ref=SimpleNamespace(path=manifest_file))
    collector = SimpleNamespace(done={pack_id: done})
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
            raise RuntimeError("cancel requested")

    stopped = [False]
    _r, _i, failure = _put_back_tile(
        nodes, env, collector, old, install=False, on_event=job, stopped=stopped
    )
    assert failure is not None and failure.error is not None
    assert failure.error["code"] == "SYS_CANCELLED" and stopped == [True]
    assert _keys(old) == {"dsf": "k1"} and _keys(new) == {"dsf": "k2"}
    assert isinstance(events[-1], Failed)


def test_the_rows_of_a_tile_s_folders_gone_follow_it_to_the_workshop(home: Path) -> None:
    """A tile whose folder is gone from a disk that is here is built in the workshop as a new
    one: its rows there go, those of folders still there stay."""
    gone = Path(home) / "Gone" / NAME
    kept = _pack(Path(home) / "Kept", key="k1")
    with Library(default_library_path()) as lib:
        lib.register(T, "BI", 16, gone, "osxp", {"dsf": "k1"})
        lib.register(T, "", 0, gone.parent / "yOrthoStudio_Overlays", "osxp", None, kind="overlay")
        lib.register(T, "BI", 16, kept, "osxp", {"dsf": "k1"})
    build_mod._let_go(T, [gone], SimpleNamespace(library_path=default_library_path()))
    with Library(default_library_path()) as lib:
        assert [r.path for r in lib.list(tile=T)] == [kept]
    # the wiring, read rather than run: after the workshop's build of the tile, not before
    import inspect

    body = inspect.getsource(build_mod.build_tiles)
    assert "else:\n                _let_go(spec.tile, gone.get(name, []), env)" in body


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
