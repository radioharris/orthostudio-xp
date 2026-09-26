# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The graph rule ``orthostudio.vectors@1``: step 1 of Ortho4XP, natively.

Spec: ``docs/specs/vectors-assembly.md`` section 6. The artefact is a directory holding
``Data<tile>.node`` and ``Data<tile>.poly``, which is what ``orthostudio.mesh`` reads, plus the
replay file and the stats.

Wave 2 completed it: the airports are built from the ``aeroway`` layer of the ``osm`` input,
the elevation is smoothed over them before anything samples it, and the artefact publishes
what the rest of the graph reads back -- the PSLG, the smoothed ``Data<tile>.alt`` with its
``dem.json`` and OrthoStudio XP's airport record (``docs/specs/airports-integration.md``). Its
measurements are in ``docs/benchmarks/p4-airports.md``.

Where the layers come from is injected, like every other native rule's outside world
(:class:`VectorsJob`, the pattern of ``MeshJob``, ``MasksJob`` and ``OsmJob``): this module
knows how to *assemble* layers, never how to *build* them.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from pathlib import Path

from pydantic import field_validator

from orthostudio.airports_vec.artefact import (
    AIRPORTS_JSON,
    AIRPORTS_NPZ,
    write_airports,
)
from orthostudio.airports_vec.model import MAX_SMOOTHING_PIX, AirportSet
from orthostudio.dem.dem import Dem, alt_file_name
from orthostudio.errors import OsxpError
from orthostudio.graph import ResolvedInput, Rule, RuleParams, RunContext, rule
from orthostudio.model import TileRef
from orthostudio.vectors.assemble import (
    AssembledVectors,
    AssemblyParams,
    Elevation,
    VectorLayers,
    assemble_vectors,
)

__all__ = [
    "DEM_JSON",
    "LAYER_BUILDER",
    "MAX_SMOOTHING_PIX",
    "VECTORS",
    "LayerBuild",
    "LayerRequest",
    "VectorsJob",
    "VectorsParams",
    "current_vectors_job",
    "layer_stats",
    "run_vectors",
    "vectors_job",
]

LAYER_BUILDER = ("orthostudio.vectors.layers", "build_layers")
"""Module and function the wave-1 layer builders converge on; resolved lazily (spec 6)."""

MIN_MESH_ZL, MAX_MESH_ZL = 0, 24
"""Range ``orthostudio.imagery.grid`` accepts; the orthophoto grid is built at ``mesh_zl``."""

DEM_JSON = "dem.json"
"""The window of the ``Data<tile>.alt`` this rule publishes (``orthostudio.mesh.build.DemSpec``)."""


class VectorsParams(RuleParams):
    """Exactly what step 1 reads of the configuration (spec section 6).

    ``custom_dem`` and ``fill_nodata`` are deliberately absent: the elevation reaches the rule as an
    artefact whose digest already keys it, as in ``orthostudio.mesh`` and ``orthostudio.masks``.
    ``iterate`` is absent because ``O4_Vector_Map.py:23`` forces it to 0.

    Wave 2 added two fields: ``apt_smoothing_pix``, the only ``tile.<attribute>`` of
    ``O4_Airport_Utils.py`` that is configuration, which changes the bytes of the ``Data<tile>.alt``
    the artefact publishes (``airports-integration.md`` R-I6); and ``exact_grid_order``, a fidelity
    switch of OrthoStudio XP's own that changes the PSLG (corrected by review 6, this docstring said
    "exactly one field").
    """

    tile: str = ""
    """The tile (``"+43+005"``): the artefact is tile-specific, so it must enter the key."""

    road_level: int = 1
    """0 disables roads; the OSM layers downloaded depend on it (``O4_Vector_Map.py:63``)."""

    road_banking_limit: float = 0.5
    lane_width: float = 4.0
    max_levelled_segs: int = 200000
    water_simplification: float = 0.0
    min_area: float = 0.001
    max_area: float = 200.0
    clean_bad_geometries: bool = True
    mesh_zl: int = 19
    """Zoom level of the orthophoto grid inserted as DUMMY edges (``:96-117``)."""

    apt_smoothing_pix: int = 8
    """Airport elevation smoothing width, in DEM pixels (``O4_Config_Utils.py:117``).

    0 disables the smoothing for every airport that does not carry its own ``smoothing_pix``
    tag. It is consumed by ``orthostudio.airports_vec.smoothing`` and it decides the content of
    ``Data<tile>.alt``, hence of the mesh: it must be in the key."""

    @field_validator("apt_smoothing_pix")
    @classmethod
    def _smoothing_pix_is_sane(cls, value: int) -> int:
        """Refuse a negative or absurd width with the code the pipeline reports."""
        if not 0 <= value <= MAX_SMOOTHING_PIX:
            raise OsxpError(
                "CFG_VALUE_INVALID",
                context={
                    "name": "apt_smoothing_pix",
                    "value": value,
                    "type": "int",
                    "range": f"0..{MAX_SMOOTHING_PIX}",
                },
                remedy="Set apt_smoothing_pix between 0 and "
                f"{MAX_SMOOTHING_PIX} in the tile configuration (Ortho4XP uses 8).",
            )
        return value

    @field_validator("mesh_zl")
    @classmethod
    def _mesh_zl_in_range(cls, value: int) -> int:
        """A zoom level out of range is a configuration error, not a traceback (review 5).

        ``orthostudio.imagery.grid`` accepts 0 to 24 and raises a bare ``ValueError`` outside; the
        stage refuses the value here instead, with the code the rest of the pipeline uses.
        """
        if not MIN_MESH_ZL <= value <= MAX_MESH_ZL:
            raise OsxpError(
                "CFG_VALUE_INVALID",
                context={
                    "name": "mesh_zl",
                    "value": value,
                    "type": "int",
                    "range": f"{MIN_MESH_ZL}..{MAX_MESH_ZL}",
                },
                remedy=f"Set mesh_zl between {MIN_MESH_ZL} and {MAX_MESH_ZL} in the tile "
                "configuration (Ortho4XP uses 19).",
            )
        return value

    exact_grid_order: bool = False
    """Reproduce Ortho4XP's intra-layer cutting of the horizontal grid lines (1.6x the noding).

    Off by default. It closes the nodes that differ from Ortho4XP's by one unit of the 9th decimal
    -- their number depends on the input: 3 of 247 282 on ``+43+005``, 17 on review 6's
    synthetic aeroway layer -- and makes the PSLG equal to Ortho4XP's at the exact key (without it
    the edge sets are equal at 1.5e-9, not at the key); it does not make the mesh byte-identical,
    because Ortho4XP *numbers* its nodes in the order of its spatial index
    (``docs/benchmarks/p4-airports.md`` section 4). It changes the artefact, so it is in the
    key."""

    def assembly(self) -> AssemblyParams:
        """The subset :func:`orthostudio.vectors.assemble.assemble_vectors` consumes."""
        return AssemblyParams(mesh_zl=self.mesh_zl, exact_grid_order=self.exact_grid_order)


@dataclass(frozen=True, slots=True)
class LayerRequest:
    """Everything a layer builder needs: the tile, the parameters and the resolved inputs."""

    tile: TileRef
    params: VectorsParams
    osm: Path
    """The OSM snapshot directory (``orthostudio.osm@1``)."""
    dem: Elevation
    patches: Path | None = None
    """A folder of the tile's ``*.patch.osm`` files **itself**, not a root above it (review 5).
    A build gives none: Ortho4XP's ``Patches/`` folder is not read (decision 0010)."""
    airports: Path | None = None
    """Wave 2: the airport artefact. Always ``None`` while ``orthostudio.airports`` does
    not exist."""
    cancel: threading.Event | None = None
    progress: Callable[[float, str], None] | None = None
    """Where each family says it has started. This step said nothing at all, from beginning to
    end: a user on Linux watched his build sit at 28 % of the Data stage for eight minutes and
    had no way to tell a long road network from a program that had stopped (2026-09-23)."""


@dataclass(frozen=True, slots=True)
class LayerBuild:
    """What a layer builder hands back: the layers **and** the elevation they sampled.

    Wave 1 returned only the layers, because the raster the rule already held was the raster
    every family sampled. Wave 2 smooths it over the airports first
    (``O4_Vector_Map.py:213``), so the rule assembles, samples the orthophoto grid and
    publishes ``Data<tile>.alt`` from *this* raster and not from the one it read
    (``docs/specs/airports-integration.md`` R-I1).

    It lives here, beside :class:`LayerRequest`, because the pair *is* the contract of the
    injection point; the builder module imports the two and the rule imports no builder.
    """

    layers: VectorLayers
    dem: Dem
    """The elevation after the airport smoothing."""
    airports: AirportSet | None = None
    """The airport record, for the two files the rule publishes beside the PSLG (B3)."""
    counts: dict[str, dict[str, int]] = dc_field(default_factory=dict)
    """Per-family counters, published in ``stats.json`` under ``families``."""
    events: tuple[OsxpError, ...] = ()
    """The coded events the builders reported (a rejected runway, a skipped surface...),
    counted by code in ``stats.json`` under ``events`` (review 6)."""


@dataclass(slots=True)
class VectorsJob:
    """How the node reaches the world outside the store.

    ``build_layers`` turns the resolved inputs into :class:`VectorLayers`; leaving it ``None``
    resolves :data:`LAYER_BUILDER` lazily, so the rule works as soon as the layer builders
    land and fails with an explanation until then.
    """

    build_layers: Callable[[LayerRequest], LayerBuild] | None = None
    cancel: threading.Event | None = None
    progress: Callable[[float, str], None] | None = None
    """``progress(fraction, message)``, as the OSM and imagery jobs take."""
    check_planar: bool = False
    """Audit the planarity of the result (about one second on a real tile) into stats.json."""


_JOB: ContextVar[VectorsJob | None] = ContextVar("osxp_vectors_job", default=None)


@contextlib.contextmanager
def vectors_job(job: VectorsJob) -> Iterator[VectorsJob]:
    """Bind ``job`` for the ``orthostudio.vectors`` rules run inside the block."""
    token = _JOB.set(job)
    try:
        yield job
    finally:
        _JOB.reset(token)


def current_vectors_job() -> VectorsJob | None:
    """The job bound by :func:`vectors_job` in *this* context, if any."""
    return _JOB.get()


def _job() -> VectorsJob:
    job = _JOB.get()
    return job if job is not None else VectorsJob()


def _resolve_builder(job: VectorsJob) -> Callable[[LayerRequest], LayerBuild]:
    """The injected builder, or :data:`LAYER_BUILDER`, or a blocking error that says why."""
    if job.build_layers is not None:
        return job.build_layers
    module_name, attribute = LAYER_BUILDER
    try:
        module = __import__(module_name, fromlist=[attribute])
        builder = getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise OsxpError(
            "SYS_INTERNAL_ERROR",
            context={"type": type(exc).__name__, "detail": f"{module_name}.{attribute}: {exc}"},
            remedy=(
                "orthostudio.vectors@1 assembles layers, it does not build them: wire the OSM "
                f"layer builders as {module_name}.{attribute} or pass "
                "VectorsJob(build_layers=...)."
            ),
        ) from exc
    return builder  # type: ignore[no-any-return]


def _elevation(path: Path, tile: TileRef) -> Elevation:
    """The elevation of the ``dem`` input; only an ``orthostudio.dem@1`` artefact is readable."""
    if (path / "meta.json").is_file():
        return Dem.load(path)
    raise OsxpError(
        "DEM_FILE_UNREADABLE",
        context={"path": path, "tile": tile.name, "reason": "no meta.json"},
        remedy=(
            "orthostudio.vectors needs the artefact of orthostudio.dem@1 (Data<tile>.alt, dem.npy, "
            "meta.json); build with --dem native."
        ),
    )


def _optional(inputs: dict[str, ResolvedInput] | None, name: str) -> Path | None:
    resolved = (inputs or {}).get(name)
    if resolved is None or not resolved.present:
        return None
    return resolved.path


def _teller(job: VectorsJob, tile: TileRef) -> Callable[[float, str], None]:
    """Where this step says what it is doing, and nothing at all when nobody listens.

    It used to say nothing to anybody: no progress, no line, from the first second to the last.
    A step that takes eight minutes on a dense tile at road level 5 is then indistinguishable
    from one that has stopped, which is what a user reported (2026-09-23).
    """

    def say(fraction: float, what: str) -> None:
        if job.progress is None:
            return
        with contextlib.suppress(Exception):  # telling must never stop a build
            job.progress(fraction, f"{tile.name}: {what}")

    return say


def run_vectors(ctx: RunContext) -> AssembledVectors:
    """Body of the rule, callable without a ``Store`` (which is what the tests do)."""
    params = ctx.params
    assert isinstance(params, VectorsParams)
    tile = TileRef.parse(params.tile)
    job = _job()
    if job.cancel is not None and job.cancel.is_set():
        raise OsxpError("SYS_CANCELLED", context={"stage": "vectors", "tile": tile.name})
    say = _teller(job, tile)
    say(0.02, "reading the relief")
    dem = _elevation(ctx.input_path("dem"), tile)
    request = LayerRequest(
        tile=tile,
        params=params,
        osm=ctx.input_path("osm"),
        dem=dem,
        patches=_optional(dict(ctx.inputs), "patches"),
        airports=_optional(dict(ctx.inputs), "airports"),
        cancel=job.cancel,
        progress=job.progress,
    )
    build = _resolve_builder(job)(request)
    say(0.80, "putting the shapes together")
    assembly = replace(params.assembly(), check_planar=job.check_planar, cancel=job.cancel)
    # ``build.dem`` and not ``dem``: the airports smoothed the raster, and the orthophoto
    # grid, the gluing border and the default seed must sample the same one every family
    # sampled (``airports-integration.md`` R-I1).
    assembled = assemble_vectors(build.layers, tile, build.dem, assembly)
    assembled.stats.update(layer_stats(build))
    say(0.95, "writing what the relief must follow")
    assembled.write(ctx.out)
    write_elevation_and_airports(ctx.out, tile, build)
    return assembled


def layer_stats(build: LayerBuild) -> dict[str, object]:
    """What the builders counted, for ``stats.json``: per-family counters and coded events.

    Review 6: ``LayerBuild.counts`` was never written and the events were only logged at
    DEBUG, so nothing in the artefact said that a runway had been rejected.
    """
    events: dict[str, int] = {}
    for event in build.events:
        events[event.code] = events.get(event.code, 0) + 1
    return {
        "families": {name: dict(values) for name, values in sorted(build.counts.items())},
        "events": dict(sorted(events.items())),
    }


def write_elevation_and_airports(out_dir: Path, tile: TileRef, build: LayerBuild) -> None:
    """Publish what the artefact holds beside the PSLG (``airports-integration.md`` 3 and 4).

    * ``Data<tile>.alt`` -- the raster **after** the airport smoothing, which is the one Ortho4XP
      writes (``O4_Vector_Map.py:169``) and the one Triangle4XP, the masks and the DSF read;
    * ``dem.json`` -- its window, in the format ``orthostudio.mesh.build.DemSpec.from_dir`` reads,
      so that the mesh node can take this artefact as its ``dem`` input;
    * ``airports.npz`` / ``airports.wkb.json`` -- OrthoStudio XP's airport record.

    Every file is staged under a ``.part`` name and renamed, as :meth:`AssembledVectors.write`
    does: a full disk must not leave a ``Data<tile>.alt`` shorter than its window.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []

    def part(name: str) -> Path:
        final = directory / name
        tmp = directory / f"{name}.part"
        staged.append((tmp, final))
        return tmp

    try:
        build.dem.write_alt(part(alt_file_name(tile)))
        part(DEM_JSON).write_text(
            json.dumps(build.dem.meta(), indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        if build.airports is not None:
            write_airports(
                directory,
                build.airports,
                tile,
                npz_path=part(AIRPORTS_NPZ),
                json_path=part(AIRPORTS_JSON),
            )
    except BaseException:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise
    for tmp, final in staged:
        os.replace(tmp, final)


@rule(
    name="orthostudio.vectors",
    version=1,
    params=VectorsParams,
    inputs=("osm", "dem", "patches", "airports"),
    ram_mb=1200,
    kind="dir",
)
def osxp_vectors(ctx: RunContext) -> None:
    """Native step 1: ``Data<tile>.{node,poly}``, ``layers.npz``, ``stats.json``."""
    run_vectors(ctx)


VECTORS: Rule = osxp_vectors
"""``orthostudio.vectors@1``. Not wired by default in wave 1 (arbitration A7)."""
