"""Backwards-compatible re-export of :class:`orthostudio.model.TileRef`.

This module used to carry a stand-in ``NamedTuple`` until the P2a contract module
``orthostudio.model`` was merged. It is merged, so the stand-in is gone and
``orthostudio.model.TileRef`` is the single definition; only the import path is kept for code that
imports from here.
"""

from __future__ import annotations

from orthostudio.model import TileRef

__all__ = ["TileRef"]
