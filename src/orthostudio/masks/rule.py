# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The native masks rule ``orthostudio.masks@1``.

Spec: ``docs/specs/masks-build.md`` section 3.

Inputs: ``mesh`` (the tile's mesh artefact) plus the eight neighbours, each of which may be
absent -- an absent neighbour is a distinct, keyed state (``docs/specs/graph-keys.md``, K4)
and means "no sea beyond that border", exactly Ortho4XP's ``select_neighbor_meshes``. ``dem`` and
``custom_extent`` are optional inputs for ``masks_use_DEM_too`` and ``masks_custom_extent``.

Parameters: the nine values the stage reads and nothing else. ``custom_dem`` and
``fill_nodata`` are deliberately *not* parameters: the elevation reaches the rule as an
artefact whose digest already keys it.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, Rule, RuleParams, RunContext, rule
from orthostudio.masks.build import build_masks
from orthostudio.masks.profiles import MASKING_MODES, MasksWidth
from orthostudio.masks.water import NEIGHBOUR_OFFSETS, MeshWaterTris, read_water_tris
from orthostudio.mesh.mesh_file import MeshData, read_mesh, read_mesh_npz
from orthostudio.model import TileRef

__all__ = [
    "MASKS",
    "MasksJob",
    "MasksParams",
    "current_masks_job",
    "masks_job",
    "read_mesh_artifact",
    "read_water_tris_artifact",
]


class MasksParams(RuleParams):
    """Exactly what step 2.5 reads (``O4_Mask_Utils.py:64-221``, ``:648-813``)."""

    tile: str = ""
    """The tile (``"+43+005"``): the output depends on it, so it must enter the key."""

    mask_zl: int = 14
    masks_width: MasksWidth = 100
    """A number for ``sand``/``rocks``, three of them for ``3steps``."""
    masking_mode: str = "sand"
    use_masks_for_inland: bool = False
    ratio_water: float = 0.25
    masks_use_DEM_too: bool = False  # noqa: N815 (Ortho4XP name)
    distance_masks_too: bool = False
    masks_custom_extent: str = ""


def read_mesh_artifact(path: Path) -> MeshData:
    """Read a mesh out of a mesh artefact (a directory) or straight from a file.

    Prefers the binary ``mesh.npz`` twin when the artefact has one (0.06 s against 0.32 s for
    the text file of the reference tile); accepts a bare ``.mesh`` or ``.npz`` path so that
    the native mesh rule can change its directory layout without touching this module.
    """
    if path.is_file():
        return read_mesh_npz(path) if path.suffix == ".npz" else read_mesh(path)
    npz = sorted(path.glob("*.npz"))
    if npz:
        preferred = [p for p in npz if p.name == "mesh.npz"] or npz
        return read_mesh_npz(preferred[0])
    text = sorted(path.glob("*.mesh"))
    if text:
        return read_mesh(text[0])
    raise OsxpError(
        "MESH_INPUT_MISSING",
        context={"path": path, "tile": path.name},
        remedy="Build the mesh of this tile first; the masks rule needs its .mesh or mesh.npz.",
    )


def read_water_tris_artifact(path: Path) -> MeshWaterTris | None:
    """``water_tris.npz`` of a mesh artefact, when the rule that wrote it published one.

    Contract: ``docs/specs/mesh-build.md`` section 7. Absent means "search the mesh again", which
    gives the same triangles.
    """
    npz = path / "water_tris.npz" if path.is_dir() else path.with_name("water_tris.npz")
    return read_water_tris(npz) if npz.is_file() else None


def _meshes_and_water(
    ctx: RunContext, tile: TileRef
) -> tuple[dict[TileRef, MeshData], dict[TileRef, MeshWaterTris]]:
    paths = {tile: ctx.input_path("mesh")}
    for name, (dlat, dlon) in NEIGHBOUR_OFFSETS.items():
        resolved: ResolvedInput = ctx.inputs[name]
        if resolved.present and resolved.path is not None:
            paths[tile.neighbour(dlat, dlon)] = resolved.path
    meshes = {nb: read_mesh_artifact(path) for nb, path in paths.items()}
    water = {nb: w for nb, path in paths.items() if (w := read_water_tris_artifact(path))}
    return meshes, water


def _optional_cells(
    resolved: ResolvedInput | None,
) -> dict[tuple[int, int], NDArray[np.uint8]] | None:
    """Read ``<til_y>_<til_x>.npy`` cell arrays out of an optional input directory.

    TODO(P3-dem, P4-extents): the ``dem`` and ``custom_extent`` artefacts do not exist yet
    (``orthostudio.sources.dem`` and the imagery extent rasteriser). The least engaging option is
    implemented: a directory of per-cell ``.npy`` arrays, which is what those rules will be
    asked to publish; until they do, the rule refuses to guess and raises.
    """
    if resolved is None or not resolved.present or resolved.path is None:
        return None
    cells: dict[tuple[int, int], NDArray[np.uint8]] = {}
    for path in sorted(resolved.path.glob("*.npy")):
        til_y, til_x = path.stem.split("_")[:2]
        cells[(int(til_x), int(til_y))] = np.load(path).astype(np.uint8)
    return cells


@dataclass(slots=True)
class MasksJob:
    """How the node reaches the masks stage: the pool size and the cancellation token.

    Bound by the pipeline with :func:`masks_job`, exactly as ``orthostudio.dem`` and
    ``orthostudio.osm`` bind theirs. It replaces the module-level ``_Cancel`` hook of the first P3
    pass, which nothing ever set (review 4, finding C1) and which two concurrent nodes would
    have shared.

    ``workers`` is the count the declaring pipeline **budgeted RAM for**
    (``pipeline.build._masks_ram_mb``): passing it here is what makes the declared ``ram_mb``
    describe the pool that really starts (finding M1). ``None`` falls back to
    :func:`orthostudio.masks.build.worker_count`'s own default.
    """

    workers: int | None = None
    cancel: threading.Event | None = None


_JOB: ContextVar[MasksJob | None] = ContextVar("osxp_masks_job", default=None)


@contextlib.contextmanager
def masks_job(job: MasksJob) -> Iterator[MasksJob]:
    """Bind ``job`` for the ``orthostudio.masks`` rules run inside the block."""
    token = _JOB.set(job)
    try:
        yield job
    finally:
        _JOB.reset(token)


def current_masks_job() -> MasksJob | None:
    """The job bound by :func:`masks_job` in *this* context, if any."""
    return _JOB.get()


def _job() -> MasksJob:
    job = _JOB.get()
    return job if job is not None else MasksJob()


@rule(
    name="orthostudio.masks",
    version=1,
    params=MasksParams,
    inputs=("mesh", *NEIGHBOUR_OFFSETS, "dem", "custom_extent"),
    ram_mb=1100,
    kind="dir",
)
def osxp_masks(ctx: RunContext) -> None:
    """Step 2.5, natively: ``<y>_<x>.png`` masks at ``mask_zl`` plus ``index.json``."""
    params = ctx.params
    assert isinstance(params, MasksParams)
    if params.masking_mode not in MASKING_MODES:
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={
                "name": "masking_mode",
                "value": params.masking_mode,
                "type": "string",
                "range": str(MASKING_MODES),
            },
        )
    tile = TileRef.parse(params.tile)
    job = _job()
    dem_cells = _optional_cells(ctx.inputs.get("dem")) if params.masks_use_DEM_too else None
    if params.masks_use_DEM_too and dem_cells is None:
        raise OsxpError(
            "DEM_FILE_UNREADABLE",
            context={"path": "<dem input>", "tile": tile.name, "reason": "input absent"},
            remedy="masks_use_DEM_too needs an elevation artefact, which orthostudio.sources.dem "
            "does not produce yet; turn the option off.",
        )
    custom_cells = (
        _optional_cells(ctx.inputs.get("custom_extent")) if params.masks_custom_extent else None
    )
    if params.masks_custom_extent and custom_cells is None:
        raise OsxpError(
            "MASK_CUSTOM_EXTENT_INVALID",
            context={"extent": params.masks_custom_extent},
            remedy="Custom extents are rasterised by the imagery layer, which does not exist "
            "yet; clear masks_custom_extent.",
        )
    meshes, water = _meshes_and_water(ctx, tile)
    build_masks(
        ctx.out,
        meshes,
        tile,
        water_tris=water,
        mask_zl=params.mask_zl,
        masks_width=params.masks_width,
        masking_mode=params.masking_mode,
        use_masks_for_inland=params.use_masks_for_inland,
        ratio_water=params.ratio_water,
        distance_masks_too=params.distance_masks_too,
        dem_cells=dem_cells,
        custom_cells=custom_cells,
        workers=job.workers,
        cancel=job.cancel,
    )


MASKS: Rule = osxp_masks
