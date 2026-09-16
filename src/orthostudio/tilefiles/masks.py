"""Index of the mask PNGs of a tile (``O4_Mask_Utils.py:23-60``, ``O4_File_Names.py:334-345``).

Spec: ``docs/specs/tile-files.md`` section 3.3. The masks artefact holds them at ``mask_zl``
as ``{m_til_y}_{m_til_x}.png``, named as Ortho4XP names them; a texture at ``zl >= mask_zl``
reads a sub-window of the mask texture that contains it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

__all__ = ["MaskIndex", "MaskWindow", "mask_tile_for", "masks_index"]

_MASK_RE = re.compile(r"^(?P<til_y>\d+)_(?P<til_x>\d+)\.png$")
MASK_SIZE = 4096


class MaskWindow(NamedTuple):
    """Where a texture reads its alpha in the mask texture at ``mask_zl``."""

    m_til_x: int
    m_til_y: int
    factor: int  # 2 ** (zl - mask_zl)
    x0: int  # pixel offset of the window in the 4096x4096 mask
    y0: int
    side: int  # 4096 // factor

    @property
    def file_name(self) -> str:
        """``{m_til_y}_{m_til_x}.png`` (O4_File_Names.py:334-335)."""
        return f"{self.m_til_y}_{self.m_til_x}.png"


def mask_tile_for(til_x: int, til_y: int, zl: int, mask_zl: int) -> MaskWindow | None:
    """Mask texture and sub-window of a texture (O4_Mask_Utils.py:23-35 and 38-53).

    ``None`` when ``zl < mask_zl`` (such textures never get a mask).
    """
    if zl < mask_zl:
        return None
    factor = 2 ** (zl - mask_zl)
    m_til_x = (int(til_x / factor) // 16) * 16
    m_til_y = (int(til_y / factor) // 16) * 16
    rx = int((til_x - factor * m_til_x) / 16)
    ry = int((til_y - factor * m_til_y) / 16)
    side = MASK_SIZE // factor
    return MaskWindow(
        m_til_x, m_til_y, factor, int(rx * MASK_SIZE / factor), int(ry * MASK_SIZE / factor), side
    )


@dataclass(frozen=True, slots=True)
class MaskIndex:
    """Lookup ``(m_til_x, m_til_y) -> Path | None`` over the masks present for one tile."""

    root: Path
    mask_zl: int
    entries: dict[tuple[int, int], Path]

    def __call__(self, til_x: int, til_y: int) -> Path | None:
        return self.entries.get((til_x, til_y))

    def __len__(self) -> int:
        return len(self.entries)

    def for_texture(self, til_x: int, til_y: int, zl: int) -> tuple[MaskWindow, Path] | None:
        """Window and mask file of a texture at ``zl``, or ``None`` when no mask applies."""
        window = mask_tile_for(til_x, til_y, zl, self.mask_zl)
        if window is None:
            return None
        path = self(window.m_til_x, window.m_til_y)
        return None if path is None else (window, path)


def masks_index(directory: Path, mask_zl: int) -> MaskIndex:
    """Index the ``{m_til_y}_{m_til_x}.png`` masks of one tile's directory (spec 3.3).

    Only names of that exact form with indices that are multiples of 16 and fit in the
    ``mask_zl`` grid are indexed; ``_dist.png`` and subdirectories are ignored. A missing
    directory indexes nothing.
    """
    root = Path(directory)
    limit = 2**mask_zl - 16
    entries: dict[tuple[int, int], Path] = {}
    if root.is_dir():
        for path in sorted(root.glob("*.png")):
            m = _MASK_RE.match(path.name)
            if not m:
                continue
            til_x, til_y = int(m["til_x"]), int(m["til_y"])
            if til_x % 16 or til_y % 16 or til_x > limit or til_y > limit:
                continue
            entries[(til_x, til_y)] = path
    return MaskIndex(root=root, mask_zl=mask_zl, entries=entries)


LookupFn = Callable[[int, int], Path | None]
