# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What Live counts as written (orthostudio.live.writes): the bytes write_sparse writes."""

from __future__ import annotations

from orthostudio.live import dds, writes
from orthostudio.live.texture import write_sparse


def _levels(top: int, file_top: int) -> list[bytes]:
    return [b"\1" * length for _, length in dds.level_layout(file_top)[top - file_top :]]


def test_the_bytes_counted_are_those_written(tmp_path) -> None:
    path = tmp_path / "t.dds"
    write_sparse(path, 2, _levels(2, 0), file_top=0)  # 1024 px, from nothing
    body, copy = writes.texture_bytes(2, 0, False, None)
    assert body == sum(len(b) for b in _levels(2, 0)) and copy == 0
    # sharper, its levels from 2 down kept: only levels 0 and 1 written
    body, copy = writes.texture_bytes(0, 0, False, 2)
    layout = dds.level_layout(0)
    assert body == layout[0][1] + layout[1][1]
    # the same level again: every level written
    assert writes.texture_bytes(2, 0, False, 2)[0] == sum(len(b) for b in _levels(2, 0))
    # an 8192 px file built from its 512 px level: the 1024 px copy above it
    assert writes.texture_bytes(3, -1, True, None)[1] == dds.level_layout(-1)[3][1]


def test_the_summary_adds_up() -> None:
    writes.reset()
    writes.add("textures 4096 px", 3_000_000_000, 300)
    writes.add("photo cache", 1_000_000_000, 5000)
    line = writes.summary()
    assert line.startswith("written by Live: 4.00 GB: textures 4096 px 3.00 GB (300)")
    writes.reset()
