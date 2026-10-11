# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What the disk of Live's folder can do (orthostudio.live.filesystem): clones, hard links and
holes tried on the disk, a disk with neither clones nor holes refused, files placed as cheaply as
the disks allow, a texture written with a hole where the disk clones nothing, and Windows's paths
taken on any system."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from orthostudio.live import clone as clone_module
from orthostudio.live import dds, filesystem, texture
from orthostudio.live.pack import FolderError, check_folder
from orthostudio.live.region import neutral_levels, write_neutral


def refuse(code: int):
    def refused(*args, **kwargs):
        raise OSError(code, os.strerror(code))

    return refused


def test_this_disk_holds_the_pack(tmp_path: Path) -> None:
    can = filesystem.abilities(tmp_path)
    assert can.links and (can.clones or can.holes) and can.holds_pack
    # glibc says 127 of a disk it does not know (a container's overlay, whose disk under it allows
    # 65,000): Live does not go by it, it takes a refusal as it comes (names.py)
    assert can.link_max >= 127
    assert [p.name for p in tmp_path.iterdir()] == []  # the probes gone


def test_a_disk_with_holes_and_no_clones_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NTFS and ext4: no clone, but a texture's unbuilt levels a hole."""
    monkeypatch.setattr(filesystem, "clone", refuse(errno.ENOTSUP))
    can = filesystem.abilities(tmp_path)
    assert not can.clones and can.holes and can.links and can.holds_pack


def test_a_disk_with_neither_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mac OS Extended: links, but neither clones nor holes, every texture its full 11 or 45 MB.
    exFAT and FAT32: not even links."""
    monkeypatch.setattr(filesystem, "clone", refuse(errno.ENOTSUP))
    monkeypatch.setattr(filesystem, "punch_hole", lambda *a: False)
    monkeypatch.setattr(filesystem, "allocated", lambda path: filesystem.PROBE_BYTES)
    can = filesystem.abilities(tmp_path)
    assert can.links and not can.clones and not can.holes and not can.holds_pack
    with pytest.raises(FolderError, match=r"APFS \(a Mac\), NTFS \(Windows\) or ext4"):
        check_folder(tmp_path / "live")
    monkeypatch.setattr(os, "link", refuse(errno.EPERM))
    assert not filesystem.abilities(tmp_path).links


def test_a_file_placed_as_cheaply_as_the_disks_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "a.ter"
    src.write_text("A\n800\nTERRAIN\n")
    if clone_module.clones_work(tmp_path):
        assert filesystem.place_file(src, tmp_path / "cloned.ter") == "cloned"
    monkeypatch.setattr(filesystem, "clone", refuse(errno.ENOTSUP))
    assert filesystem.place_file(src, tmp_path / "linked.ter") == "linked"
    assert os.path.samefile(src, tmp_path / "linked.ter")
    monkeypatch.setattr(os, "link", refuse(errno.EXDEV))  # another disk
    far = "copied" if filesystem.WINDOWS else "symlinked"  # a file's link: Developer Mode there
    assert filesystem.place_file(src, tmp_path / "linked-far.ter", symlink=True) == far
    assert filesystem.place_file(src, tmp_path / "copied.ter") == "copied"
    monkeypatch.setattr(filesystem, "WINDOWS", True)  # a file's link needs Developer Mode there
    assert filesystem.place_file(src, tmp_path / "copied-win.ter", symlink=True) == "copied"
    for name in ("linked-far.ter", "copied.ter", "copied-win.ter"):
        assert (tmp_path / name).read_text() == src.read_text()
    with pytest.raises(FileExistsError):
        filesystem.place_file(src, tmp_path / "copied.ter")


def test_too_many_names() -> None:
    assert filesystem.too_many_links(OSError(errno.EMLINK, "Too many links"))
    ntfs = OSError(errno.EINVAL, "An attempt was made to create more links on a file")
    ntfs.winerror = filesystem.ERROR_TOO_MANY_LINKS  # type: ignore[attr-defined]
    assert filesystem.too_many_links(ntfs)
    assert not filesystem.too_many_links(OSError(errno.EEXIST, "File exists"))


def test_windows_waits_for_a_file_open_in_x_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows a file another program holds open cannot be replaced: tried again a moment."""
    real = os.replace
    tries = []

    def held_twice(src, dst):
        tries.append(1)
        if len(tries) <= 2:
            raise PermissionError(13, "The process cannot access the file")
        real(src, dst)

    monkeypatch.setattr(filesystem, "WINDOWS", True)
    monkeypatch.setattr(os, "replace", held_twice)
    (tmp_path / "new").write_bytes(b"new")
    (tmp_path / "old").write_bytes(b"old")
    filesystem.replace(tmp_path / "new", tmp_path / "old")
    assert (tmp_path / "old").read_bytes() == b"new" and len(tries) == 3
    monkeypatch.setattr(filesystem, "REPLACE_TRIES_S", 0.05)
    monkeypatch.setattr(os, "replace", refuse(errno.EACCES))
    with pytest.raises(PermissionError):
        filesystem.replace(tmp_path / "old", tmp_path / "other")


def test_pwrite_without_os_pwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no os.pwrite."""
    path = tmp_path / "f"
    path.write_bytes(b"." * 16)
    monkeypatch.delattr(os, "pwrite", raising=False)
    fd = os.open(path, os.O_WRONLY | filesystem.BINARY)
    try:
        filesystem.pwrite(fd, b"abc", 5)
    finally:
        os.close(fd)
    assert path.read_bytes() == b".....abc........"


def test_a_texture_where_the_disk_clones_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NTFS, ext4: a texture built to 512 px holds its levels from there down, and a hole above
    them (zeros if read, no disk taken), not the neutral texture's blocks."""
    monkeypatch.setattr(clone_module, "clone", refuse(errno.ENOTSUP))
    monkeypatch.setattr(texture, "_UNCLONED", set())
    neutral = write_neutral(tmp_path)
    path = tmp_path / "23296_33152_BI16.dds"
    levels = [bytes([10 + k]) * n for k, (_, n) in enumerate(dds.mip_layout())][3:]
    texture.write_sparse(path, 3, levels, base=neutral)
    data = path.read_bytes()
    assert len(data) == dds.file_size() and data[: len(dds.header())] == dds.header()
    layout = dds.mip_layout()
    for level in range(3, len(layout)):
        start, length = layout[level]
        assert data[start : start + length] == levels[level - 3]
    start, length = layout[0]
    assert set(data[start : start + length]) == {0}  # the hole
    assert filesystem.allocated(path) < dds.file_size() // 4
    # built sharper later, its coarser levels given again, the rest still a hole
    sharper = [bytes([20 + k]) * n for k, (_, n) in enumerate(layout)][1:]
    texture.write_sparse(path, 1, sharper, base=neutral, keep_from=3)
    data = path.read_bytes()
    for level in range(1, len(layout)):
        start, length = layout[level]
        assert data[start : start + length] == sharper[level - 1]
    assert neutral.read_bytes()[layout[0][0] : layout[0][0] + 8] == neutral_levels(0)[0][:8]


@pytest.mark.skipif(not filesystem.WINDOWS, reason="Windows' C library writes the zeros")
def test_a_file_extended_on_windows_takes_no_disk(tmp_path: Path) -> None:
    """os.ftruncate wrote the zeros there, 45 MB for an 8192 px texture: SetEndOfFile writes none,
    and the parts the disk gave are counted as fsutil counts them."""
    path = tmp_path / "t.dds"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | filesystem.BINARY, 0o644)
    try:
        assert filesystem.make_sparse(fd)
        filesystem.extend(fd, 45_000_000)
        filesystem.pwrite(fd, b"z" * 1_000_000, 44_000_000)
    finally:
        os.close(fd)
    assert path.stat().st_size == 45_000_000
    assert 1_000_000 <= filesystem.allocated(path) < 2_000_000


def test_a_name_replaced_by_another_name_of_the_same_file_leaves_no_temporary(
    tmp_path: Path,
) -> None:
    """A rename between two names of one file does nothing (POSIX): the temporary name stayed
    beside the textures, 11,865 of them after one session (2026-10-10). Now removed instead."""
    from orthostudio.live.filesystem import replace

    neutral = tmp_path / "neutral.dds"
    neutral.write_bytes(b"N" * 64)
    name, tmp = tmp_path / "a.dds", tmp_path / ".live-tmp-1-a.dds"
    os.link(neutral, name)
    os.link(neutral, tmp)
    replace(tmp, name)
    assert not tmp.exists() and name.read_bytes() == b"N" * 64
    other = tmp_path / ".live-tmp-1-b.dds"  # another file: renamed over it, as always
    other.write_bytes(b"B")
    replace(other, name)
    assert not other.exists() and name.read_bytes() == b"B"
