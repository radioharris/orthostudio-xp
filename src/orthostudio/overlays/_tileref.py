# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Backwards-compatible re-export of :class:`orthostudio.model.TileRef`.

This module used to carry its own ``NamedTuple`` because ``src/orthostudio/model.py`` did not exist
yet (P2 ``sched`` work). It exists now and is the single definition of a tile reference, so
the duplicate is gone: only the import path is kept, for code that imports from here.
"""

from __future__ import annotations

from orthostudio.model import TileRef

__all__ = ["TileRef"]
