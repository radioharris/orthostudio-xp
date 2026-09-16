"""Assembly of the vector layers into the PSLG Triangle4XP reads.

This is the glue of step 1: it decides *in which order* the layers are noded (which is the
altitude and attribute priority), builds the orthophoto grid and the gluing border itself,
collects the seeds and writes the artefact. It builds no geometry: coastline, water, roads and
patches are the layer builders' business, airports are wave 2 and arrive as an injected,
optional input (arbitration A2).

Origin: ``O4_Vector_Map.build_poly_file`` (lines 19-179). Spec:
``docs/specs/vectors-assembly.md``; the noder it calls is specified in
``docs/specs/vectors-pslg.md`` and implemented in ``orthostudio.vectors.noding``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry.base import BaseGeometry

from orthostudio.errors import OsxpError
from orthostudio.model import TileRef
from orthostudio.vectors.grid import BORDER_SEGMENTS, grid_and_border
from orthostudio.vectors.noding import (
    MARKERS,
    Layer,
    NodedGraph,
    check_planar,
    node_layers,
)
from orthostudio.vectors.noding import linework as _geometry_parts
from orthostudio.vectors.seeds import SeedSet, marker_label, marker_name
from orthostudio.vectors.triangle_files import write_node_file, write_poly_file

__all__ = [
    "LAYERS_NPZ",
    "STATS_JSON",
    "AssembledVectors",
    "AssemblyParams",
    "Elevation",
    "VectorLayer",
    "VectorLayers",
    "assemble_vectors",
    "node_file_name",
    "poly_file_name",
    "to_vector_layers",
]

LAYERS_NPZ = "layers.npz"
STATS_JSON = "stats.json"
AIRPORTS_JSON = "airports.json"
STATS_FORMAT = "osxp-vectors-1"


class Elevation(Protocol):
    """What the assembler needs of the elevation: sampling, and the maximum of the raster."""

    def alt_vec(self, way: NDArray[np.floating]) -> NDArray[np.float64]:
        """Altitude in metres at each ``(x, y)`` row, tile-local coordinates."""

    @property
    def alt_dem(self) -> NDArray[np.floating]:
        """The raster itself; only its maximum is read (the default seed, spec 4.2)."""


@dataclass(frozen=True, slots=True)
class VectorLayer:
    """One insertion pass: a geometry, its attribute, its altitudes and its seeds.

    ``geometry`` is any linework (the noder expands polygons into their rings); ``z`` holds one
    altitude per coordinate of ``shapely.get_coordinates(geometry)``, or ``None`` to take the
    Z of 3-D coordinates (0 when absent). ``seeds`` are the ``(K, 2)`` representative points
    the builder computed for the polygons it encoded, in encoding order
    (``orthostudio.vectors.seeds.polygon_seeds``); a layer of lines has none.
    """

    name: str
    geometry: BaseGeometry
    marker: int
    z: NDArray[np.float64] | None = None
    seeds: NDArray[np.float64] | None = None

    def __post_init__(self) -> None:
        marker_name(self.marker)  # an unnamed combination of bits is a builder bug

    def as_noding_layer(self) -> Layer:
        """The ``(geometry, marker, z)`` triple
        :func:`orthostudio.vectors.noding.node_layers` takes."""
        return (self.geometry, int(self.marker), self.z)


def to_vector_layers(
    name: str,
    layers: Sequence[Layer],
    seeds: Mapping[str | int, NDArray[np.floating]] | None = None,
) -> list[VectorLayer]:
    """Adapt what a layer builder returns into the passes the assembler orders.

    The wave-1 builders (``orthostudio.vectors.coast``, ``water``, ``roads``, ``patches``) all
    return the same pair: ``layers``, a tuple of ``(geometry, marker, z)`` triples in insertion
    order, and ``seeds``, a map from attribute *name* to ``(S, 2)`` points for the whole family.
    Seeds of one attribute are attached to the first pass carrying it, which is what the ``.poly``
    writes anyway: the order there is the attribute value, then the order inside the attribute (spec
    4.3). A seed whose attribute no pass carries is an error, not a rounding: it would be written
    into a region no edge bounds.
    """
    remaining = {
        (MARKERS[key] if isinstance(key, str) else int(key)): np.asarray(points, dtype=np.float64)
        for key, points in (seeds or {}).items()
    }
    out = []
    for index, (geometry, marker, z) in enumerate(layers):
        label = name if len(layers) == 1 else f"{name}_{index}"
        attached = remaining.pop(int(marker), None)
        altitudes = None if z is None else np.asarray(z, dtype=np.float64)
        out.append(VectorLayer(label, geometry, int(marker), altitudes, attached))
    if remaining:
        names = sorted(marker_name(marker) for marker in remaining)
        raise ValueError(f"{name}: seeds for {names} but no layer carries them")
    return out


@dataclass(frozen=True, slots=True)
class VectorLayers:
    """The layers of one tile, by family, each already geometric.

    The *order of the families* is the priority: the first one to touch a point decides its
    altitude, and a marker is the OR of everything covering an edge
    (``docs/specs/vectors-pslg.md`` 2.4-2.6). :meth:`ordered` is the only place that order is
    written down.

    ``airports`` is empty in wave 1 (arbitration A2): the assembler never calls an airport
    module, it takes the layers ready-made. ``airport_bounds`` is ``(A, 4)``
    ``(x_min, y_min, x_max, y_max)`` in tile-local degrees, written as ``airports.json`` for
    the curvature weight map of the mesh stage (``docs/specs/mesh-build.md`` 3.2).
    """

    patches: Sequence[VectorLayer] = ()
    airports: Sequence[VectorLayer] = ()
    roads: Sequence[VectorLayer] = ()
    coastline: Sequence[VectorLayer] = ()
    water: Sequence[VectorLayer] = ()
    airport_bounds: NDArray[np.float64] | None = None

    def ordered(self) -> list[VectorLayer]:
        """The layers in insertion order (``O4_Vector_Map.py:52-90``, spec 2.1).

        Patches come first: ``include_patches`` runs at ``:215``, inside ``include_airports``
        and *before* ``encode_runways_taxiways_and_aprons`` at ``:216``.
        """
        return [
            *self.patches,
            *self.airports,
            *self.roads,
            *self.coastline,
            *self.water,
        ]


@dataclass(frozen=True, slots=True)
class AssemblyParams:
    """What the assembly itself consumes; the rule's parameters are a superset."""

    mesh_zl: int = 19
    """Zoom level of the orthophoto grid (``O4_Vector_Map.py:100-117``)."""

    border_segments: int = BORDER_SEGMENTS
    """Segments per side of the gluing border; Ortho4XP has no parameter, it is 2048."""

    exact_grid_order: bool = False
    """Insert every horizontal grid line as its own pass, as Ortho4XP's sequence effectively does.

    Ortho4XP cuts the chain it has already built **while** a layer is being inserted, so the
    sub-segment it gives to the intersection solver is the one bounded by the crossings it
    has already made; the vectorised noder cuts between layers and uses the whole pre-layer
    edge. On +43+005 that leaves 3 nodes of 247 282 one unit away in the 9th decimal; the
    count depends on the input (17 on review 6's synthetic aeroway layer, ``vectors-pslg.md``
    2.8). Splitting the horizontal grid pass into one pass per line
    reproduces Ortho4XP's sequence exactly -- every node key of the reference, none invented --
    and cost 12.6 s of noding instead of 1.05 s, so it is **off** by default: it is a
    fidelity switch, not a setting (``docs/benchmarks/p4-airports.md`` section 4). Since the
    incremental insertion of review 6 (``vectors-pslg.md`` 2.13) it costs 1.67 s instead of
    1.05 s; whether that makes it the default is the architect's call (it changes the key)."""

    check_planar: bool = False
    """Run the (costly) planarity audit of the result and report it in ``stats.json``."""

    cancel: threading.Event | None = None
    """Cooperative cancellation, checked before the noding, before every noding pass
    (:func:`orthostudio.vectors.noding.node_layers`) and before the artefact is written.

    The layer builders check the token between families (``orthostudio.vectors.layers``) and the
    airport encoder between two airports; this is the checkpoint that covers the noding and
    the assembly themselves, so the worst-case latency of a Ctrl-C is one noding pass (review
    5 measured about 1.2 s for the heaviest pass of a dense tile; review 6 found the token
    read only around the whole noding, which a tile of 324 aerodromes turned into 47 s)."""


@dataclass(frozen=True, slots=True)
class AssembledVectors:
    """The PSLG of one tile and everything the artefact needs."""

    tile: TileRef
    graph: NodedGraph
    seeds: dict[int, NDArray[np.float64]]
    layers: tuple[VectorLayer, ...]
    stats: dict[str, Any] = field(default_factory=dict)
    airport_bounds: NDArray[np.float64] | None = None

    def write(self, out_dir: str | Path) -> None:
        """Write ``Data<tile>.node``, ``Data<tile>.poly``, ``layers.npz`` and ``stats.json``.

        All or nothing (review 5): every file is written under a ``.part`` name and the whole
        set is renamed into place at the end, so a full disk or a cancellation never leaves a
        ``.node`` without its ``.poly`` behind. Inside the graph store this is belt and braces
        -- the ``Store`` commits a temporary directory with a single rename -- but ``write``
        is public and the benchmarks point it at ordinary directories.
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
            write_node_file(part(node_file_name(self.tile)), self.graph.nodes)
            write_poly_file(
                part(poly_file_name(self.tile)),
                self.graph.edges,
                self.graph.markers,
                seeds={int(marker): points for marker, points in self.seeds.items()},
            )
            _write_layers_npz(part(LAYERS_NPZ), self.layers)
            if self.airport_bounds is not None and len(self.airport_bounds):
                bounds = np.asarray(self.airport_bounds, dtype=np.float64).reshape(-1, 4)
                payload = {"airports": [{"bounds": [float(v) for v in row]} for row in bounds]}
                part(AIRPORTS_JSON).write_text(
                    json.dumps(payload, indent=1) + "\n", encoding="utf-8"
                )
            part(STATS_JSON).write_text(
                json.dumps(self.stats, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
        except BaseException:
            for tmp, _ in staged:
                tmp.unlink(missing_ok=True)
            raise
        for tmp, final in staged:
            os.replace(tmp, final)


def node_file_name(tile: TileRef) -> str:
    """``Data+43+005.node`` (``O4_File_Names.input_node_file``)."""
    return f"Data{tile.name}.node"


def poly_file_name(tile: TileRef) -> str:
    """``Data+43+005.poly`` (``O4_File_Names.input_poly_file``)."""
    return f"Data{tile.name}.poly"


def assemble_vectors(
    layers: VectorLayers,
    tile: TileRef,
    dem: Elevation,
    params: AssemblyParams | None = None,
    *,
    out_dir: str | Path | None = None,
) -> AssembledVectors:
    """Node the layers of ``tile`` into its PSLG, seeds included, and write it when asked.

    The pass order is :meth:`VectorLayers.ordered` followed by the three DUMMY passes this
    module owns: vertical grid lines, horizontal grid lines, gluing border
    (``orthostudio.vectors.grid``). Seeds are accumulated in the same order and the empty map falls
    back to Ortho4XP's single SEA seed (``orthostudio.vectors.seeds``).
    """
    params = params or AssemblyParams()
    started = time.perf_counter()

    def check() -> None:
        if params is not None and params.cancel is not None and params.cancel.is_set():
            raise OsxpError("SYS_CANCELLED", context={"stage": "vectors", "tile": tile.name})

    check()
    passes = list(layers.ordered())
    for lines in grid_and_border(
        tile, params.mesh_zl, dem.alt_vec, segments=params.border_segments
    ):
        passes.extend(_grid_passes(lines, exact=params.exact_grid_order))

    seeds = SeedSet()
    for layer in passes:
        seeds.add(layer.marker, layer.seeds)

    noding_started = time.perf_counter()
    graph = node_layers([layer.as_noding_layer() for layer in passes], cancel=params.cancel)
    noding_s = time.perf_counter() - noding_started

    check()
    max_altitude = float(np.max(dem.alt_dem)) if np.size(dem.alt_dem) else 0.0
    final_seeds = seeds.finalise(max_altitude)
    violations = check_planar(graph.nodes, graph.edges) if params.check_planar else None
    assembled = AssembledVectors(
        tile=tile,
        graph=graph,
        seeds=final_seeds,
        layers=tuple(passes),
        airport_bounds=layers.airport_bounds,
        stats=_stats(
            tile,
            params,
            passes,
            graph,
            final_seeds,
            seeds,
            noding_s=noding_s,
            elapsed_s=time.perf_counter() - started,
            violations=violations,
        ),
    )
    if out_dir is not None:
        assembled.write(out_dir)
    return assembled


def _grid_passes(lines: Any, *, exact: bool) -> list[VectorLayer]:
    """One pass for a grid family, or -- for the horizontal lines under ``exact_grid_order``
    -- one pass per line (:attr:`AssemblyParams.exact_grid_order`)."""
    if not exact or lines.name != "grid_horizontal":
        return [VectorLayer(lines.name, lines.geometry, MARKERS["DUMMY"], lines.z)]
    out: list[VectorLayer] = []
    position = 0
    for index, part in enumerate(shapely.get_parts(lines.geometry)):
        size = shapely.count_coordinates(part)
        out.append(
            VectorLayer(
                f"{lines.name}_{index}",
                part,
                MARKERS["DUMMY"],
                lines.z[position : position + size],
            )
        )
        position += size
    return out


def _stats(
    tile: TileRef,
    params: AssemblyParams,
    passes: Sequence[VectorLayer],
    graph: NodedGraph,
    seeds: dict[int, NDArray[np.float64]],
    accumulated: SeedSet,
    *,
    noding_s: float,
    elapsed_s: float,
    violations: int | None,
) -> dict[str, Any]:
    """The ``stats.json`` document: what went in, what came out, how long it took.

    ``assemble_s`` covers the assembly only, not the writing of the artefact: the stats are
    part of what is written.
    """
    values, counts = np.unique(graph.markers, return_counts=True)
    return {
        "format": STATS_FORMAT,
        "tile": tile.name,
        "mesh_zl": params.mesh_zl,
        "layers": [
            {
                "name": layer.name,
                "marker": marker_name(layer.marker),
                "coordinates": int(shapely.count_coordinates(layer.geometry)),
                "seeds": 0 if layer.seeds is None else len(layer.seeds),
            }
            for layer in passes
        ],
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "edges_by_marker": {
            marker_label(int(value)): int(count)
            for value, count in zip(values, counts, strict=True)
        },
        "seeds": int(sum(len(points) for points in seeds.values())),
        "seeds_by_marker": {
            marker_name(marker): len(points) for marker, points in sorted(seeds.items())
        },
        "seeds_defaulted": not len(accumulated),
        "seeds_skipped": accumulated.skipped,
        "planarity_violations": violations,
        "noding_s": round(noding_s, 3),
        "assemble_s": round(elapsed_s, 3),
    }


def _write_layers_npz(path: Path, layers: Sequence[VectorLayer]) -> None:
    """The replay file: every part of every layer, in insertion order.

    One entry per *part* (a LineString, or one ring of a polygon) so that re-noding the file gives
    back exactly the segments this run noded; ``layer`` says which pass a part belongs to.
    """
    coords: list[NDArray[np.float64]] = []
    lengths: list[int] = []
    markers: list[int] = []
    owner: list[int] = []
    for index, layer in enumerate(layers):
        parts = _geometry_parts(layer.geometry)
        if not len(parts):
            continue
        xy, part_index = shapely.get_coordinates(parts, return_index=True)
        z = layer.z
        if z is None:
            z = np.zeros(len(xy), dtype=np.float64)
        column = np.asarray(z, dtype=np.float64).reshape(-1, 1)
        coords.append(np.hstack([xy, column]))
        sizes = np.bincount(part_index, minlength=len(parts))
        lengths.extend(int(size) for size in sizes)
        markers.extend([int(layer.marker)] * len(parts))
        owner.extend([index] * len(parts))
    offsets = (
        np.concatenate([[0], np.cumsum(lengths, dtype=np.int64)])
        if lengths
        else np.zeros(1, dtype=np.int64)
    )
    # A file object, not the path: ``savez_compressed`` would append ``.npz`` to the ``.part``
    # name the atomic write stages it under.
    with Path(path).open("wb") as handle:
        np.savez_compressed(
            handle,
            coords=np.concatenate(coords) if coords else np.zeros((0, 3), dtype=np.float64),
            offsets=offsets.astype(np.int64),
            markers=np.asarray(markers, dtype=np.uint8),
            layer=np.asarray(owner, dtype=np.int32),
            # No explicit dtype: numpy sizes the strings; a fixed '<U32' truncated a long
            # layer name without a word (review 5).
            names=np.asarray([layer.name for layer in layers]),
        )
