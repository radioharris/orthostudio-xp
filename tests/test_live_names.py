# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The files of the squares round the aircraft (orthostudio.live.names): made as X-Plane comes,
taken away behind it, the textures built and those the streamer holds kept, a neutral texture's
copies past the names a file may have, and a start X-Plane waits for until they are made."""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

import pytest

from live_helpers import NAMES, FakeDownloader, live_names, make_source, square_of
from orthostudio.live.cache import ChunkCache
from orthostudio.live.names import (
    KEEP,
    RING,
    NeutralPool,
    RegionTextures,
    region_list_file,
    squares_round,
)
from orthostudio.live.region import neutral_file, setup_region, sources_of_pack
from orthostudio.live.streamer import Fix, Streamer

# ZL16 textures in a row eastwards from NAMES[0]: ROW[10] lies on the squares (46, 2) and (46, 3)
ROW = tuple(f"23296_{33152 + 16 * k}_BI16" for k in range(12))
HERE, EAST = (46, 2), (46, 3)


def quiet(_: str) -> None:
    pass


def region(root: Path, textures=ROW) -> Path:
    pack = root / "pack"
    setup_region(make_source(root, tile=(46, 2), textures=textures), pack, log=quiet)
    return pack


def files_of(pack: Path, name: str) -> list[Path]:
    return [
        pack / "terrain" / f"{name}.ter",
        pack / "terrain" / f"{name}_water_overlay.ter",
        pack / "textures" / f"{name}.dds",
    ]


def test_a_square_made_then_taken_away(tmp_path: Path) -> None:
    pack = region(tmp_path)
    names = live_names(pack)
    assert list((pack / "terrain").iterdir()) == []  # nothing before X-Plane comes
    placed, named = names.make(HERE)
    assert (placed, named) == (2 * 11, 11)  # its two .ter files and name for each of 11 textures
    for name in ROW[:11]:
        assert all(f.exists() for f in files_of(pack, name))
        assert os.path.samefile(pack / "textures" / f"{name}.dds", pack / neutral_file(0))
    assert not (pack / "terrain" / f"{ROW[11]}.ter").exists()  # the next square's
    assert names.make(HERE) == (0, 0)  # made once
    names.done.add(HERE)
    assert names.take_away(HERE) == 3 * 11
    for name in ROW:
        assert not any(f.exists() for f in files_of(pack, name))
    assert (pack / "textures" / "water_transition.png").exists()  # shared by every square
    assert (pack / neutral_file(0)).exists()


def test_what_the_streamer_holds_stays(tmp_path: Path) -> None:
    """A texture built, queued or building keeps its file: only the neutral names go."""
    pack = region(tmp_path)
    held = f"{ROW[0]}.dds"
    names = live_names(pack, busy=lambda name: name == held)
    names.make(HERE)
    built = pack / "textures" / held
    built.unlink()
    built.write_bytes(b"built")
    names.take_away(HERE)
    assert built.read_bytes() == b"built"
    assert not (pack / "terrain" / f"{ROW[0]}.ter").exists()
    assert not (pack / "textures" / f"{ROW[1]}.dds").exists()


def test_a_texture_on_two_squares_stays_while_one_is_made(tmp_path: Path) -> None:
    pack = region(tmp_path)
    names = live_names(pack)
    for square in (HERE, EAST):
        names.make(square)
        names.done.add(square)
    names.take_away(EAST)
    names.done.discard(EAST)
    assert all(f.exists() for f in files_of(pack, ROW[10]))  # on HERE too, still made
    assert not any(f.exists() for f in files_of(pack, ROW[11]))
    names.take_away(HERE)
    assert not any(f.exists() for f in files_of(pack, ROW[10]))


def test_a_texture_built_whose_file_is_gone_is_built_again(tmp_path: Path) -> None:
    pack = region(tmp_path, textures=NAMES)
    forgotten: list[str] = []
    names = live_names(pack, forget=forgotten.append)
    names.make(square_of(NAMES[0]))
    assert sorted(forgotten) == sorted(f"{n}.dds" for n in NAMES)  # each asked about, once


def test_a_texture_the_builder_wrote_as_its_name_was_made_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At a start the squares' names are made while the first textures are built: a texture the
    builder wrote over its name just made is its own, kept (168 good ones were built twice, Norway,
    2026-09-30); one whose name still leads to the neutral texture was gone, built again."""
    s = streamer(tmp_path)
    written, gone = f"{ROW[0]}.dds", f"{ROW[1]}.dds"
    s.built[written] = s.built[gone] = 4
    real_link = NeutralPool.link

    def builder_writes_right_after(pool, top, dst):
        made = real_link(pool, top, dst)
        if made and dst.name == written:  # the builder's rename, just after the name was made
            tmp = dst.with_name(".live-tmp-built")
            tmp.write_bytes(b"built")
            os.replace(tmp, dst)
        return made

    monkeypatch.setattr(NeutralPool, "link", builder_writes_right_after)
    s.names.make(HERE)
    assert s.built.get(written) == 4 and gone not in s.built
    assert (s.pack_dir / "textures" / written).read_bytes() == b"built"
    assert s.names.leads_to_neutral(gone) and not s.names.leads_to_neutral(written)


def test_the_neutral_texture_gets_copies_past_the_names_a_file_may_have(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NTFS takes 1,023 names a file: past them, a copy of the neutral texture takes the next ones
    (here 3 a file)."""
    pack = region(tmp_path)
    real_link = os.link

    def few_names(src, dst, *args, **kwargs):
        if os.stat(src).st_nlink >= 3:
            raise OSError(errno.EMLINK, "Too many links", str(dst))
        real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", few_names)
    live_names(pack).make(HERE)
    neutral = (pack / neutral_file(0)).read_bytes()
    copies = sorted((pack / "_live").glob("neutral*.dds"))
    assert len(copies) == 1 + 11 // 2  # the neutral texture and its copies, two names each
    for copy in copies:
        assert copy.read_bytes() == neutral and os.stat(copy).st_nlink <= 3
    for name in ROW[:11]:
        assert (pack / "textures" / f"{name}.dds").read_bytes() == neutral


def test_a_name_made_neutral_again(tmp_path: Path) -> None:
    pack = region(tmp_path, textures=NAMES)
    path = pack / "textures" / f"{NAMES[0]}.dds"
    path.write_bytes(b"built")
    NeutralPool(pack).replace(0, path)
    assert os.path.samefile(path, pack / neutral_file(0))


def test_the_thread_makes_the_window_and_takes_away_behind(tmp_path: Path) -> None:
    pack = region(tmp_path)
    names = live_names(pack)
    names.start()
    try:
        names.want({HERE}, {HERE}, (46.5, 2.5))
        assert names.wait_ready({HERE}, 10.0)
        assert (pack / "textures" / f"{ROW[0]}.dds").exists()
        names.want({EAST}, {EAST}, (46.5, 3.5))  # the aircraft flew east
        assert names.wait_ready({EAST}, 10.0)
        deadline = time.monotonic() + 10.0
        # the files go first, the count of squares taken away just after them
        while names.released < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not (pack / "textures" / f"{ROW[0]}.dds").exists()
        assert (pack / "textures" / f"{ROW[10]}.dds").exists()  # on EAST too
        assert names.made == 2 and names.released == 1
    finally:
        names.close()


def test_nothing_taken_away_while_xplane_loads_scenery(tmp_path: Path) -> None:
    pack = region(tmp_path)
    names = live_names(pack)
    names.yielding = lambda: True
    names.start()
    try:
        names.want({HERE}, {HERE}, None)
        assert names.wait_ready({HERE}, 10.0)
        names.want({EAST}, {EAST}, None)
        assert names.wait_ready({EAST}, 10.0)  # made even so: X-Plane will read them
        time.sleep(0.3)
        assert (pack / "textures" / f"{ROW[0]}.dds").exists()  # taken away later
    finally:
        names.close()


def test_a_file_that_fails_is_tried_again_and_stops_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that cannot be made (a full disk, a file held open on Windows) no longer stops the
    squares' files for the whole flight: its square is tried again later, the others are made."""
    from orthostudio.live import names as names_module

    pack = region(tmp_path)
    logged: list[str] = []
    names = live_names(pack, log=logged.append)
    real_link, broken = os.link, {"on": True}

    def link(src, dst, *args, **kwargs):
        if broken["on"] and str(dst).endswith(f"{ROW[3]}.dds"):
            raise OSError(errno.EPERM, "Operation not permitted", str(dst))
        real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(names_module, "FAIL_RETRY_S", 0.2)
    names.start(own_process=False)
    try:
        names.want({HERE, EAST}, {HERE, EAST}, (46.5, 2.5))
        assert names.wait_ready({EAST}, 10.0)  # the other square made all the same
        assert not names.ready({HERE}) and HERE in names.failing
        assert any("could not all be made" in line for line in logged)
        assert (pack / "textures" / f"{ROW[4]}.dds").exists()  # its other files are there
        broken["on"] = False  # the disk has room again
        assert names.wait_ready({HERE}, 10.0)
        assert HERE not in names.failing and names.crashed is None
    finally:
        names.close()


def test_a_start_is_not_held_for_ever_by_a_failing_square(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orthostudio.live import names as names_module

    pack = region(tmp_path)
    logged: list[str] = []
    names = live_names(pack, log=logged.append)
    names.failing[HERE] = [time.monotonic() - 1.0, time.monotonic() + 60, 1, "EPERM"]
    assert not names.ready({HERE})
    monkeypatch.setattr(names_module, "GIVE_UP_S", 0.5)
    assert names.ready({HERE})
    assert names.ready({HERE})
    assert sum("X-Plane goes on without the files" in line for line in logged) == 1


def test_a_texture_built_since_is_never_taken_away(tmp_path: Path) -> None:
    """Taking a square away removes a texture's name only while it leads to the neutral texture:
    one built in the meantime (renamed over it) stays, whatever was decided before."""
    pack = region(tmp_path)
    names = live_names(pack)  # nothing held by the streamer: every name may go
    names.make(HERE)
    names.done.add(HERE)
    built = pack / "textures" / f"{ROW[2]}.dds"
    tmp = built.with_name("built.tmp")
    tmp.write_bytes(b"built for real")
    os.replace(tmp, built)  # as the builder does
    names.take_away(HERE)
    assert built.read_bytes() == b"built for real"
    assert not (pack / "textures" / f"{ROW[1]}.dds").exists()


def test_the_squares_round_a_point() -> None:
    """X-Plane's window, 4 x 3 squares (more by a border), a ring round it made, two kept."""
    window = squares_round([(46.5, 2.5)], 0)
    assert {(46, 1), (46, 2), (46, 3), (46, 4)} <= window and len(window) >= 12
    made, kept = squares_round([(46.5, 2.5)], RING), squares_round([(46.5, 2.5)], KEEP)
    assert window < made < kept
    assert (47, 5) in made and (48, 6) in kept and (48, 6) not in made


# --- each region's textures, kept in a list -------------------------------------------------------


def test_a_regions_textures_are_read_from_its_list_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing a region's .ter files is a start's longest step where a disk is slow: its list is
    kept and read back, the terrain folder not listed again."""
    pack = region(tmp_path)
    sources, lists = sources_of_pack(pack), tmp_path / "lists"
    first = RegionTextures(sources, lists=lists)
    assert first.listed == sources and first.keep_error is None
    assert region_list_file(lists, sources[0]).exists()
    real_scandir = os.scandir

    def no_terrain(path):
        assert Path(path).name != "terrain", "the region's terrain folder listed again"
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", no_terrain)
    again = RegionTextures(sources, lists=lists)
    assert again.listed == []
    assert list(again.entries()) == list(first.entries()) and len(again) == len(ROW)
    assert again.others == first.others


def test_a_region_changed_is_listed_again(tmp_path: Path) -> None:
    pack = region(tmp_path, textures=ROW[:3])
    sources, lists = sources_of_pack(pack), tmp_path / "lists"
    RegionTextures(sources, lists=lists)
    terrain = sources[0] / "terrain"
    (terrain / f"{ROW[3]}.ter").write_text("A\n800\nTERRAIN\n")
    st = terrain.stat()  # its time moved, whatever the clock's step on this disk
    os.utime(terrain, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    changed = RegionTextures(sources, lists=lists)
    assert changed.listed == sources and len(changed) == 4
    assert RegionTextures(sources, lists=lists).listed == []  # kept again, as it is now


def test_a_broken_list_is_made_again_and_one_that_cannot_be_written_stops_nothing(
    tmp_path: Path,
) -> None:
    pack = region(tmp_path)
    sources, lists = sources_of_pack(pack), tmp_path / "lists"
    lists.mkdir()
    region_list_file(lists, sources[0]).write_text('{"version": 1, "folder"')
    broken = RegionTextures(sources, lists=lists)
    assert broken.listed == sources and len(broken) == len(ROW)
    assert RegionTextures(sources, lists=lists).listed == []
    blocker = tmp_path / "a file"
    blocker.write_text("")
    unkept = RegionTextures(sources, lists=blocker)
    assert unkept.listed == sources and unkept.keep_error and len(unkept) == len(ROW)
    assert RegionTextures(sources).listed == sources  # no lists given: listed each time


# --- the streamer ---------------------------------------------------------------------------------


def test_the_streamer_keeps_the_regions_lists_and_says_how_long_it_took(tmp_path: Path) -> None:
    pack = region(tmp_path)
    cache, lists = ChunkCache(tmp_path / "c.sqlite"), tmp_path / "lists"
    for said in ("z_ao_test listed from its folder", "read from their lists"):
        s = Streamer(pack, cache, FakeDownloader, 0, log=quiet, region_lists=lists)
        assert s.region_note is not None
        assert s.region_note.startswith(f"the regions' {len(ROW)} textures in ")
        assert s.region_note.endswith(f" s: {said}")


def streamer(tmp_path: Path) -> Streamer:
    pack = region(tmp_path)
    return Streamer(
        pack,
        ChunkCache(tmp_path / "c.sqlite"),
        FakeDownloader,
        finest_quality=0,
        log=quiet,
    )


def test_the_streamer_wants_the_squares_round_the_aircraft_and_a_start(tmp_path: Path) -> None:
    s = streamer(tmp_path)
    assert s.names is not None and len(s.index) == len(ROW)
    s.plan(Fix(46.5, 2.5))
    assert s.names.target == squares_round([(46.5, 2.5)], RING)
    s.plan(Fix(50.0, 8.5), x_plane_there=False, starting=True)  # a start chosen far away
    both = squares_round([(46.5, 2.5), (50.0, 8.5)], RING)
    assert s.names.target == both  # the aircraft's kept while X-Plane is still there
    s.arrived()
    s.plan(Fix(50.0, 8.5), x_plane_there=False)
    assert s.names.target == squares_round([(50.0, 8.5)], RING)


def test_where_x_plane_says_it_loads_is_made_too(tmp_path: Path) -> None:
    """The plugin's HOLD says where X-Plane loads the flight: its squares are made too, whatever
    start the click gave, until the flight is there."""
    s = streamer(tmp_path)
    s.plan(Fix(46.5, 2.5), x_plane_there=False, starting=True)  # the click's start
    s.hold_at(50.0, 8.5)  # X-Plane loads elsewhere
    assert s.names.target == squares_round([(46.5, 2.5), (50.0, 8.5)], RING)
    s.arrived()
    s.plan(Fix(50.0, 8.5), x_plane_there=False)
    assert s.names.target == squares_round([(50.0, 8.5)], RING)


def test_a_start_is_ready_once_its_squares_are_made(tmp_path: Path) -> None:
    s = streamer(tmp_path)
    s.plan(Fix(46.5, 2.5), x_plane_there=False, starting=True)
    assert not s.squares_ready(46.5, 2.5)  # the plugin holds X-Plane meanwhile
    s.names.start()
    try:
        assert s.names.wait_ready(squares_round([(46.5, 2.5)], 0), 10.0)
        assert s.squares_ready(46.5, 2.5)
        assert (s.pack_dir / "terrain" / f"{ROW[0]}.ter").exists()
    finally:
        s.names.close()


def test_a_texture_made_neutral_by_the_budget_far_away_leaves_no_file(tmp_path: Path) -> None:
    s = streamer(tmp_path)
    near, far = f"{ROW[0]}.dds", f"{ROW[11]}.dds"
    s.names.make(HERE)
    s.names.done.add(HERE)
    for name in (near, far):
        (s.pack_dir / "textures" / name).unlink(missing_ok=True)
        (s.pack_dir / "textures" / name).write_bytes(b"built")
        s.built[name] = 4
    import orthostudio.live.streamer as streamer_module

    real_pick = streamer_module.pick
    streamer_module.pick = lambda *a, **k: [(near, 5), (far, 5)]
    try:
        s.evict()
    finally:
        streamer_module.pick = real_pick
    assert os.path.samefile(s.pack_dir / "textures" / near, s.pack_dir / neutral_file(0))
    assert not (s.pack_dir / "textures" / far).exists()  # on no square made: no file at all
    assert near not in s.built and far not in s.built
