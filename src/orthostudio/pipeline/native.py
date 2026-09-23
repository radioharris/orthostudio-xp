"""The stages seen from the pipeline: what they can build, the OSM and coastline rules.

Spec: ``docs/specs/pipeline-build.md`` section 8. This module is the *integration* layer: it holds
what the graph needs and what none of the stage modules may hold -- the settings the stages
cannot build (refused before a build starts) and the two rules that only exist to feed them,
``orthostudio.osm@1`` and ``orthostudio.coastline@1``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from orthostudio.errors import OsxpError
from orthostudio.graph import Rule, RuleParams, RunContext, rule
from orthostudio.mesh.rule import triangle_binary as mesh_triangle_binary
from orthostudio.mesh.weights import write_coastline_nodes
from orthostudio.model import ArtifactRef, TileRef
from orthostudio.sources.osm import (
    LayerSpec,
    OsmSnapshot,
    OverpassClient,
    SnapshotStore,
    layers_for,
    osm_progress_message,
    shared_board,
    snapshot_label,
)

__all__ = [
    "COASTLINE_RULE",
    "OSM_RULE",
    "SNAPSHOT_JSON",
    "CoastlineParams",
    "OsmJob",
    "OsmOutcome",
    "OsmParams",
    "StageChoice",
    "coastline_nodes_from_snapshot",
    "current_osm_job",
    "native_reasons",
    "osm_job",
    "resolve_stages",
    "snapshot_label_of",
    "triangle_binary",
]

log = logging.getLogger("orthostudio.build.native")

DemSource = Literal["vectors", "native"]
SNAPSHOT_JSON = "snapshot.json"
SNAPSHOT_INDEX_FORMAT = "osxp-osm-tile-1"


def triangle_binary() -> Path:
    """The Triangle4XP sidecar, resolved by the rule that runs it (``orthostudio.mesh.rule``).

    Re-exported here because :func:`native_reasons` checks it before a build while the rule
    *executes* with it: two transcriptions could have diverged into a build accepted and then
    failing (review 4, finding R1).
    """
    return mesh_triangle_binary()


# -- stage selection ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageChoice:
    """How the stages of one tile are fed (spec 8.1)."""

    dem: DemSource = "vectors"
    """Where the mesh reads ``Data<tile>.alt``: ``vectors`` = the vector stage's own, smoothed
    over the airports; ``native`` = the raw raster of the ``orthostudio.dem@1`` node (spec 8.2).
    The vector stage reads that node either way: it is its elevation *input*."""

    @property
    def label(self) -> str:
        return f"dem={self.dem}"

    def to_dict(self) -> dict[str, Any]:
        return {"dem": self.dem}


def native_reasons(cfg: Mapping[str, Any], *, triangle: Path | None = None) -> dict[str, str]:
    """Why a stage cannot build this tile configuration; empty = it can.

    Decided **before** the graph is declared (spec 8.1): a stage that cannot run must be refused
    up front, never fail in the middle of a build.
    """
    out: dict[str, str] = {}
    binary = triangle if triangle is not None else triangle_binary()
    if not Path(binary).is_file():
        out["mesh"] = f"the Triangle4XP program is missing ({binary})"
    elif int(cfg.get("iterate", 0) or 0):
        out["mesh"] = "iterate is not 0 (refining an existing mesh is not supported)"
    if cfg.get("masks_use_DEM_too"):
        out["masks"] = "masks_use_DEM_too is not supported"
    elif str(cfg.get("masks_custom_extent") or ""):
        out["masks"] = "masks_custom_extent is not supported"
    return out


def resolve_stages(
    cfg: Mapping[str, Any], *, dem: str = "vectors", triangle: Path | None = None
) -> StageChoice:
    """Check ``cfg`` and ``--dem`` against what the stages can build.

    Raises ``SYS_TOOL_MISSING`` when Triangle4XP is missing and ``CFG_VALUE_INVALID`` for a
    setting no stage supports, so the build stops before it downloads or writes anything.
    """
    if dem not in ("vectors", "native"):
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={"name": "dem", "value": dem, "type": "str", "range": "vectors/native"},
            message=f"--dem {dem!r} is not one of vectors, native.",
        )
    for stage, reason in native_reasons(cfg, triangle=triangle).items():
        if reason.startswith("the Triangle4XP program"):
            raise OsxpError(
                "SYS_TOOL_MISSING",
                context={"tool": "Triangle4XP"},
                message=f"The mesh stage cannot run: {reason}.",
                remedy="Build it: cmake -S native/triangle4xp -B native/triangle4xp/build, then "
                "cmake --build native/triangle4xp/build (or set $OSXP_TRIANGLE4XP).",
            )
        name = reason.split(" ", 1)[0]
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={"name": name, "value": str(cfg.get(name, "")), "type": "-", "range": stage},
            message=f"The {stage} stage cannot build this tile: {reason}.",
            remedy=f"Set {name} back to its default in the settings.",
        )
    return StageChoice(dem=cast(DemSource, dem))


# -- the OSM node ------------------------------------------------------------------------------


class OsmParams(RuleParams):
    """What the OSM download consumes, and nothing else (spec 8.3)."""

    tile: str = ""
    road_level: int = 1
    """The list of layers depends on it (``osm-source.md`` 3): 4 layers at 1, 5 above."""
    refresh: str = ""
    """Free label: change it to ask for fresh data (the key follows, so the node re-runs)."""


@dataclass(slots=True)
class OsmJob:
    """How the OSM rule reaches the network (injected, so tests never do)."""

    fetch: Callable[[TileRef, Sequence[LayerSpec]], dict[str, OsmSnapshot]] | None = None
    timeout_s: float = 300.0
    cancel: threading.Event | None = None
    progress: Callable[[float, str], None] | None = None

    def run(self, tile: TileRef, specs: Sequence[LayerSpec]) -> dict[str, OsmSnapshot]:
        """Download the layers, refusing to start (and stopping) when cancelled.

        Review 4, finding C3: the job carried ``cancel`` and ``timeout_s`` and passed neither
        to the client, so a cancelled node still downloaded a whole tile.
        """
        if self.cancel is not None and self.cancel.is_set():
            raise OsxpError("SYS_CANCELLED", context={"stage": "osm", "tile": tile.name})
        if self.fetch is not None:
            return self.fetch(tile, specs)
        # The process's board: a mirror one tile found dead is not waited for by the next.
        client = OverpassClient(board=shared_board())
        return client.fetch_tile_sync(
            tile,
            layers=list(specs),
            cancel=self.cancel,
            timeout_s=self.timeout_s,
            progress=self.progress,
        )


_OSM_JOB: ContextVar[OsmJob | None] = ContextVar("osxp_osm_job", default=None)


@contextlib.contextmanager
def osm_job(job: OsmJob) -> Iterator[OsmJob]:
    """Bind ``job`` for the ``orthostudio.osm`` rules run inside the block."""
    token = _OSM_JOB.set(job)
    try:
        yield job
    finally:
        _OSM_JOB.reset(token)


def current_osm_job() -> OsmJob | None:
    """The job bound by :func:`osm_job` in *this* context, if any.

    The scheduler runs a ``net`` node in a pool thread, which does not inherit the caller's
    context variables; the pipeline therefore reads the binding while declaring (main thread)
    and re-binds it inside the node.
    """
    return _OSM_JOB.get()


def _job() -> OsmJob:
    job = _OSM_JOB.get()
    return job if job is not None else OsmJob()


def snapshot_index(tile: TileRef, road_level: int, snaps: Mapping[str, OsmSnapshot]) -> dict:
    """The ``snapshot.json`` of an ``orthostudio.osm`` artefact."""
    return {
        "format": SNAPSHOT_INDEX_FORMAT,
        "tile": tile.name,
        "road_level": int(road_level),
        "label": snapshot_label(snaps.values()),
        "layers": {name: s.digest for name, s in sorted(snaps.items())},
        "counts": {name: s.counts for name, s in sorted(snaps.items())},
    }


@rule(name="orthostudio.osm", version=1, params=OsmParams, inputs=(), ram_mb=300, kind="dir")
def osxp_osm(ctx: RunContext) -> None:
    """Download the OSM layers of one tile into a snapshot store of its own (spec 8.3)."""
    params = ctx.params
    assert isinstance(params, OsmParams)
    tile = TileRef.parse(params.tile)
    specs = layers_for(params.road_level)
    job = _job()
    if job.progress is not None:
        job.progress(0.0, osm_progress_message(tile, [s.name for s in specs], [], 0, 0.0))
    snaps = job.run(tile, specs)
    store = SnapshotStore(ctx.out)
    for snap in snaps.values():
        store.save(snap)
    (ctx.out / SNAPSHOT_JSON).write_text(
        json.dumps(snapshot_index(tile, params.road_level, snaps), indent=1) + "\n",
        encoding="utf-8",
    )


OSM_RULE: Rule = osxp_osm


def snapshot_label_of(artefact: Path) -> str | None:
    """The content label recorded in an ``orthostudio.osm`` artefact, or ``None``."""
    path = Path(artefact) / SNAPSHOT_JSON
    if not path.is_file():
        return None
    with contextlib.suppress(OSError, ValueError, KeyError):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") == SNAPSHOT_INDEX_FORMAT and doc.get("label"):
            return str(doc["label"])
    return None


def artefact_layers(artefact: Path) -> dict[str, str]:
    """``{layer: digest}`` of an ``orthostudio.osm`` artefact (empty when it has no index)."""
    path = Path(artefact) / SNAPSHOT_JSON
    if not path.is_file():
        return {}
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") == SNAPSHOT_INDEX_FORMAT:
            return {str(k): str(v) for k, v in dict(doc.get("layers", {})).items()}
    return {}


# -- the coastline node --------------------------------------------------------------------


class CoastlineParams(RuleParams):
    """Only the tile: the source of the nodes enters through the inputs (spec 8.4)."""

    tile: str = ""


def coastline_nodes_from_snapshot(snapshot: OsmSnapshot) -> list[tuple[float, float]]:
    """``(lon, lat)`` of every node of a coastline snapshot, first occurrence, deduplicated.

    Same set and same order as Ortho4XP's reader (``O4_OSM_Utils.py:60-103``) gets from the same
    data: it keeps one entry per distinct ``(lon, lat)`` pair in file order, and the weight map
    only reads the coordinates (``O4_Mesh_Utils.py:161-228``).
    """
    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float]] = []
    for node in snapshot.nodes:
        pair = (float(node.lon), float(node.lat))
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out


@rule(
    name="orthostudio.coastline",
    version=1,
    params=CoastlineParams,
    inputs=("osm",),
    ram_mb=200,
    kind="file",
)
def osxp_coastline(ctx: RunContext) -> None:
    """``coastline.npz`` for the mesh weight map, from the coastline snapshot of phase 0."""
    params = ctx.params
    assert isinstance(params, CoastlineParams)
    tile = TileRef.parse(params.tile)
    osm = ctx.inputs.get("osm")
    nodes: list[tuple[float, float]] | None = None
    if osm is not None and osm.present and osm.path is not None:
        snap = SnapshotStore(osm.path).load(tile, "coastline")
        if snap is not None:
            nodes = coastline_nodes_from_snapshot(snap)
    if nodes is not None and not nodes:
        log.warning(
            "%s: the coastline layer is empty; the mesh weight map will have no coastal term",
            tile.name,
        )
    if nodes is None:
        raise OsxpError(
            "OSM_LAYER_UNAVAILABLE",
            context={"layer": "coastline", "tile": tile.name, "attempts": "no input present"},
            message=f"No coastline snapshot for {tile.name} was wired to the node.",
            remedy="Run the build again without --no-osm-fetch.",
        )
    write_coastline_nodes(ctx.out, nodes)


COASTLINE_RULE: Rule = osxp_coastline


# -- misc ---------------------------------------------------------------------------------


@dataclass(slots=True)
class OsmOutcome:
    """What phase 0 produced for one tile (spec 8.3)."""

    tile: TileRef
    label: str | None = None
    artefact: Path | None = None
    ref: ArtifactRef | None = None
    """The stored artefact, so the coastline node can take it as an input."""
    wall_s: float = 0.0
    hit: bool = False
    skipped: str = ""
    error: OsxpError | None = None
