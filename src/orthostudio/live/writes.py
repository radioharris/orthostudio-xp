# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""What Live writes to the disk, by kind, counted in Live's own process where each write is decided.

Over the Pyrenees Live wrote 8.3 GB where XPME wrote 4.7 (2026-10-09), and the flights' reports
counted only the whole disk. Each kind here is the bytes Live asks to be written: a texture's
levels as ``orthostudio.live.texture.write_sparse`` writes them (from the file's layout, in the
process that sent the job: the encoders are other processes), the photos put in the imagery cache
(the file writes about 1.4 times that: ``orthostudio.live.cache``), Live's record of the textures.
The disk's own figure adds what the file system writes around them.
"""

from __future__ import annotations

import threading
from collections import Counter

from . import dds

_lock = threading.Lock()
_bytes: Counter[str] = Counter()
_files: Counter[str] = Counter()


def add(kind: str, nbytes: int, files: int = 1) -> None:
    with _lock:
        _bytes[kind] += nbytes
        _files[kind] += files


def snapshot() -> dict[str, tuple[int, int]]:
    """{kind: (bytes, files)}."""
    with _lock:
        return {k: (_bytes[k], _files[k]) for k in _bytes}


def reset() -> None:
    with _lock:
        _bytes.clear()
        _files.clear()


def summary() -> str:
    """One line, the largest kinds first."""
    rows = sorted(snapshot().items(), key=lambda kv: -kv[1][0])
    total = sum(b for b, _ in (v for _, v in rows))
    parts = [f"{k} {b / 1e9:.2f} GB ({n})" for k, (b, n) in rows]
    return f"written by Live: {total / 1e9:.2f} GB: " + ", ".join(parts)


def texture_bytes(
    level: int, file_top: int, copy_above: bool, keep_from: int | None
) -> tuple[int, int]:
    """(bytes of the levels built, bytes of the copy above them) that ``write_sparse`` writes for
    a texture built to ``level`` in a file whose sharpest level is ``file_top``, its levels from
    ``keep_from`` down kept when it holds them already."""
    layout = dds.level_layout(file_top)
    lengths = [length for _, length in layout[level - file_top :]]
    if keep_from is not None and level < keep_from:
        lengths = lengths[: keep_from - level]
    copy = layout[level - file_top - 1][1] if copy_above and level > file_top else 0
    return sum(lengths), copy
