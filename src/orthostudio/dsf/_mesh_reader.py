# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The mesh the DSF encoder reads: ``orthostudio.mesh.mesh_file.MeshData`` and a
structural protocol.

Format: ``O4_Mesh_Utils.py:326-372`` (``write_mesh_file``) and ``:847-894`` (``read_mesh_file``);
spec ``docs/specs/dsf-terrain-assignment.md`` 2.1 and ``docs/specs/mesh-file.md``. The whole
pipeline shares one mesh object (P2a integration): the transitional private reader that lived
here is gone.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from orthostudio.mesh.mesh_file import MeshData, read_mesh

__all__ = ["MeshData", "MeshLike", "mesh_version", "read_mesh"]


class MeshLike(Protocol):
    """What ``build_dsf`` reads from a mesh: the fields of the P2a ``MeshData`` contract."""

    @property
    def vertices(self) -> np.ndarray: ...

    @property
    def normals(self) -> np.ndarray: ...

    @property
    def tris(self) -> np.ndarray: ...

    @property
    def tri_attr(self) -> np.ndarray: ...

    @property
    def extra(self) -> dict[str, Any]: ...


def mesh_version(mesh: MeshLike) -> float:
    """The ``MeshVersionFormatted`` token as Ortho4XP reads it (``float``), default 2.

    ``MeshData.extra`` keeps it under ``"version"`` (a string, spec ``mesh-file.md``); older
    callers wrote ``"mesh_version"`` (a float). Both are honoured.
    """
    extra = mesh.extra or {}
    for name in ("version", "mesh_version"):
        if name in extra:
            return float(extra[name])
    return 2.0
