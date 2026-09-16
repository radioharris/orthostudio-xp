"""The graph rule ``orthostudio.mesh@1``: stage 2 of Ortho4XP, natively.

Spec: ``docs/specs/mesh-build.md`` section 9. The artefact is a directory holding
``Data<tile>.mesh`` and its npz twin, the water table the masks stage needs and a ``stats.json``.

Inputs: ``vectors`` (the ``Data<tile>.{node,poly}`` of the vector stage), ``dem``
(``Data<tile>.alt`` and, when the DEM stage publishes one, ``dem.json``) and ``coastline``
(optional, ``coastline.npz``). Params: exactly the thirteen values the computation consumes.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from orthostudio.graph import Rule, RuleParams, RunContext, rule
from orthostudio.mesh.build import (
    DEFAULT_TRIANGLE_BIN,
    DemSpec,
    MeshBuildOptions,
    MeshBuildResult,
    build_mesh_native,
)
from orthostudio.mesh.weights import read_coastline_nodes
from orthostudio.model import TileRef
from orthostudio.programs import installed_program

__all__ = [
    "OSXP_MESH",
    "TRIANGLE_ENV",
    "MeshJob",
    "MeshParams",
    "current_mesh_job",
    "mesh_job",
    "triangle_binary",
]

TRIANGLE_ENV = "OSXP_TRIANGLE4XP"
"""Same environment variable as ``orthostudio.doctor``: where the sidecar binary is."""


def triangle_binary() -> Path:
    """``$OSXP_TRIANGLE4XP``, then the installer's copy, then ``Triangle4XP`` on the PATH, then
    the repository build."""
    env = os.environ.get(TRIANGLE_ENV)
    if env:
        return Path(env).expanduser()
    installed = installed_program("Triangle4XP")
    if installed is not None:
        return installed
    on_path = shutil.which("Triangle4XP")
    if on_path:
        return Path(on_path)
    return DEFAULT_TRIANGLE_BIN


class MeshParams(RuleParams):
    """The parameters ``orthostudio.mesh`` consumes, and only those (spec section 9).

    ``custom_dem`` / ``fill_nodata`` are absent on purpose: the elevation enters through the
    digest of the ``dem`` input, not through a parameter. Same for ``mesh_zl`` and the OSM data,
    which reach the stage through ``vectors`` and ``coastline``.
    """

    tile: str = ""
    """The tile (``"+43+005"``): the artefact is tile-specific, so it must enter the key."""

    curvature_tol: float = 2.0
    """Maximum deviation, in metres, between the mesh and the DEM (``argv[11]``)."""

    apt_curv_tol: float = 0.5
    apt_curv_ext: float = 0.5
    coast_curv_tol: float = 1.0
    coast_curv_ext: float = 0.5
    limit_tris: float = 3.0
    """Triangle budget in millions; drives the Steiner point budget ``-S``."""

    min_angle: float = 10.0
    sea_smoothing_mode: str = "zero"
    water_smoothing: int = 10
    iterate: int = 0
    """Refinement round (``-r``); 0 is the normal build. TODO: no fixture, see spec 4.2."""

    skip_multiples_of_ten: bool = True
    """Reproduce ``O4_Mesh_Utils.py:245`` (attributes that are multiples of ten are skipped)."""

    water_in_set_order: bool = True
    """Smooth inland water in CPython set order, as Ortho4XP does (spec section 5.1b)."""

    def options(self) -> MeshBuildOptions:
        """The subset :func:`orthostudio.mesh.build.build_mesh_native` takes."""
        return MeshBuildOptions(
            curvature_tol=self.curvature_tol,
            apt_curv_tol=self.apt_curv_tol,
            apt_curv_ext=self.apt_curv_ext,
            coast_curv_tol=self.coast_curv_tol,
            coast_curv_ext=self.coast_curv_ext,
            limit_tris=self.limit_tris,
            min_angle=self.min_angle,
            sea_smoothing_mode=self.sea_smoothing_mode,
            water_smoothing=self.water_smoothing,
            iterate=self.iterate,
            skip_multiples_of_ten=self.skip_multiples_of_ten,
            water_in_set_order=self.water_in_set_order,
        )


@dataclass(slots=True)
class MeshJob:
    """What the node gives the mesh stage beyond its params: the cancellation token.

    Bound by the pipeline with :func:`mesh_job` (same pattern as ``dem_job`` / ``osm_job``).
    Without it the sidecar could not be interrupted: a cancelled build waited for
    Triangle4XP, or left it running (review 4, finding C4).
    """

    cancel: threading.Event | None = None


_JOB: ContextVar[MeshJob | None] = ContextVar("osxp_mesh_job", default=None)


@contextlib.contextmanager
def mesh_job(job: MeshJob) -> Iterator[MeshJob]:
    """Bind ``job`` for the ``orthostudio.mesh`` rules run inside the block."""
    token = _JOB.set(job)
    try:
        yield job
    finally:
        _JOB.reset(token)


def current_mesh_job() -> MeshJob | None:
    """The job bound by :func:`mesh_job` in *this* context, if any."""
    return _JOB.get()


def run_mesh(ctx: RunContext) -> MeshBuildResult:
    """Body of the rule, callable on its own (the tests use it without a ``Store``)."""
    params = ctx.params
    assert isinstance(params, MeshParams)
    tile = TileRef.parse(params.tile)
    vectors = ctx.input_path("vectors")
    dem = DemSpec.from_dir(ctx.input_path("dem"), tile, iterate=params.iterate)
    coast = ctx.inputs.get("coastline")
    coast_nodes = None
    if coast is not None and coast.present and coast.path is not None:
        candidate = coast.path if coast.path.is_file() else coast.path / "coastline.npz"
        if candidate.is_file():
            coast_nodes = read_coastline_nodes(candidate)
    return build_mesh_native(
        tile,
        vectors,
        dem,
        params.options(),
        triangle_binary(),
        ctx.scratch,
        out_dir=ctx.out,
        coast_nodes=coast_nodes,
        cancel=job.cancel if (job := _JOB.get()) is not None else None,
    )


@rule(name="orthostudio.mesh", version=1, params=MeshParams, inputs=("vectors", "dem", "coastline"),
      ram_mb=900, kind="dir")  # fmt: skip
def osxp_mesh(ctx: RunContext) -> None:
    """Native stage 2: ``Data<tile>.mesh``, ``mesh.npz``, ``water_tris.npz``, ``stats.json``."""
    run_mesh(ctx)


OSXP_MESH: Rule = osxp_mesh
