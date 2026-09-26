# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Wiring of the layer builders: OSM in, :class:`VectorLayers` and the elevation out.

This is the one place that knows *which* builder produces *which* family and *where* its
input comes from. Everything geometric lives in the builders (``coast``, ``water``, ``roads``,
``patches``); everything about order and output lives in the assembler
(``orthostudio.vectors.assemble``). This module only carries data between them, which is why it is
the module :data:`orthostudio.vectors.rule.LAYER_BUILDER` points at.

Origin of the families and of their order: ``O4_Vector_Map.build_poly_file`` (``:19-179``). Wave 2
filled the two slots wave 1 left empty: :attr:`VectorLayers.airports` and
:attr:`VectorLayers.airport_bounds` now come from :mod:`orthostudio.airports_vec.stage`, and the
road builder is given the airport neighbourhood raster and the treated area instead
of :data:`orthostudio.vectors.roads.NO_AIRPORTS`.

The one thing wave 2 changed beyond those two slots is the **elevation**: Ortho4XP smooths its
raster over the airports before anything samples it (``O4_Vector_Map.py:213``), so this module
returns the smoothed raster along with the layers and every family -- patches, airports,
roads, water, coastline, and the grid the assembler builds -- reads that one
(``docs/specs/airports-integration.md`` R-I1).

Spec: ``docs/specs/vectors-assembly.md`` section 7.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from shapely.geometry.base import BaseGeometry

from orthostudio.airports_vec.stage import (
    DEFAULT_AIRPORT_PARAMS,
    AirportParams,
    build_airports,
    encode,
    require_dem,
    smooth_elevation,
)
from orthostudio.dem.dem import Dem
from orthostudio.errors import OsxpError, Severity
from orthostudio.model import TileRef
from orthostudio.sources.osm import SnapshotStore, layers_for
from orthostudio.vectors import osmdata
from orthostudio.vectors.assemble import VectorLayer, VectorLayers, to_vector_layers
from orthostudio.vectors.coast import CoastParams, build_sea_layers
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.patches import build_patch_layers
from orthostudio.vectors.roads import RoadParams, build_road_layers
from orthostudio.vectors.rule import LayerBuild
from orthostudio.vectors.water import WaterParams, build_water_layers

__all__ = [
    "LayerCounts",
    "OsmSource",
    "build_layers",
    "layer_store",
    "open_osm_source",
]

log = logging.getLogger("orthostudio.vectors.layers")

_LOG_LEVEL: dict[Severity, int] = {
    Severity.INFO: logging.INFO,
    Severity.DEGRADED: logging.WARNING,
    Severity.BLOCKING: logging.ERROR,
}
"""The level a coded event of each registry severity is logged at."""


def _seeds(
    mapping: Mapping[str, NDArray[np.floating]],
) -> dict[str | int, NDArray[np.floating]]:
    """Widen a builder's ``{name: points}`` to what :func:`to_vector_layers` accepts."""
    return {str(key): value for key, value in mapping.items()}


SNAPSHOT_INDEX = "snapshot.json"


# -- where the OSM layers come from -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OsmSource:
    """One tile's OSM input: the snapshot store ``orthostudio.osm@1`` produced."""

    root: Path
    tile: TileRef

    def path_of(self, layer: str) -> Path:
        """Where the snapshot of ``layer`` lives (a ``.osm.json.zst``)."""
        return SnapshotStore(self.root).path_for(self.tile, layer)

    def has(self, layer: str) -> bool:
        return self.path_of(layer).is_file()


def open_osm_source(path: Path | str, tile: TileRef) -> OsmSource:
    """Recognise ``path`` as an OrthoStudio XP snapshot store.

    Accepted: an ``orthostudio.osm@1`` artefact (it carries ``snapshot.json``, and its snapshots
    live under ``osm/<folder>/<tile>/``), or a store holding at least the tile's coastline.
    Anything else is refused here rather than producing a silently empty tile.
    """
    root = Path(path)
    if (root / SNAPSHOT_INDEX).is_file() or OsmSource(root, tile).has("coastline"):
        return OsmSource(root, tile)
    raise OsxpError(
        "OSM_LAYER_UNAVAILABLE",
        context={"layer": "*", "tile": tile.name, "attempts": str(root)},
        message=f"{root} is not an OrthoStudio XP OSM snapshot store.",
        remedy="Point the vectors stage at an orthostudio.osm@1 artefact.",
    )


def layer_store(
    source: OsmSource,
    layer: str,
    *,
    required: bool = True,
    on_skip: Callable[[str, dict[str, object]], None] | None = None,
) -> OsmData | None:
    """Read one layer into an :class:`OsmData`, or ``None`` when it is absent and optional."""
    snapshot = SnapshotStore(source.root).load(source.tile, layer)
    if snapshot is None:
        return _missing(source, layer, required)
    return osmdata.load(snapshot, layer=layer, tile=source.tile, on_skip=on_skip)


def _missing(source: OsmSource, layer: str, required: bool) -> OsmData | None:
    if not required:
        log.info("%s: no %s layer in %s", source.tile.name, layer, source.root)
        return None
    raise OsxpError(
        "OSM_LAYER_UNAVAILABLE",
        context={"layer": layer, "tile": source.tile.name, "attempts": str(source.path_of(layer))},
        message=f"The {layer} layer of {source.tile.name} is missing from {source.root}.",
        remedy="Run the build again without --no-osm-fetch so OrthoStudio XP downloads it, "
        "or with --osm-refresh to download every layer of the tile again.",
    )


# -- the builders -------------------------------------------------------------------------


@dataclass(slots=True)
class LayerCounts:
    """What each family produced and how long it took; folded into ``stats.json``."""

    read_s: float = 0.0
    patches_s: float = 0.0
    airports_s: float = 0.0
    roads_s: float = 0.0
    coastline_s: float = 0.0
    water_s: float = 0.0
    counts: dict[str, dict[str, int]] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "read_s": round(self.read_s, 3),
            "patches_s": round(self.patches_s, 3),
            "airports_s": round(self.airports_s, 3),
            "roads_s": round(self.roads_s, 3),
            "coastline_s": round(self.coastline_s, 3),
            "water_s": round(self.water_s, 3),
            "counts": self.counts or {},
        }


def build_layers(request: object) -> LayerBuild:
    """Build every layer family of ``request.tile`` (the rule's injection point).

    ``request`` is a :class:`orthostudio.vectors.rule.LayerRequest`; it is typed loosely here so
    that this module never imports the rule that imports it. The families are built in Ortho4XP's
    order, which is the order the elevation is sampled in and the order the seeds are accumulated in
    -- except that the *sea-equivalent* large lakes belong to the water family (``include_water``,
    ``O4_Vector_Map.py:454-590``) and are therefore built with it and carried to the coastline
    result (arbitration A2).

    The order is ``include_airports`` (``:181-222``) then ``include_roads`` (``:223``) then
    ``include_sea`` / ``include_water``: the airports own the elevation smoothing, the patch
    names and areas they must not cover, and the two values the roads read back.
    """
    tile: TileRef = request.tile  # type: ignore[attr-defined]
    params = request.params  # type: ignore[attr-defined]
    dem: Dem = require_dem(request.dem, tile)  # type: ignore[attr-defined]
    cancel = getattr(request, "cancel", None)
    source = open_osm_source(request.osm, tile)  # type: ignore[attr-defined]
    counts: dict[str, dict[str, int]] = {}
    timing = LayerCounts(counts=counts)

    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise OsxpError("SYS_CANCELLED", context={"stage": "vectors", "tile": tile.name})

    teller = getattr(request, "progress", None)

    def say(fraction: float, what: str) -> None:
        """What this family is, before it starts. A dense tile at road level 5 spends minutes
        here, and this step used to say nothing at all from beginning to end (2026-09-23)."""
        if teller is None:
            return
        try:
            teller(fraction, f"{tile.name}: {what}")
        except Exception:  # telling must never stop a build
            log.debug("%s: the progress of the vectors could not be told", tile.name)

    events: list[OsxpError] = []

    def on_skip(code: str, context: dict[str, object]) -> None:
        log.debug("%s: %s %s", tile.name, code, context)

    def on_event(error: OsxpError) -> None:
        # Review 6: every coded event used to go to DEBUG, so a runway that was not flattened
        # (OSM_RUNWAY_REJECTED, DEGRADED) was invisible at the default level. The level now
        # follows the registry severity, and the events reach stats.json through LayerBuild.
        events.append(error)
        log.log(_LOG_LEVEL.get(error.severity, logging.WARNING), "%s: %s", tile.name, error)

    _no_airport_artefact(request)
    road_level = int(params.road_level)
    airport_params = _airport_params(int(params.apt_smoothing_pix))

    # -- airports, steps 1 to 9 of include_airports (:196-213) -----------------------------
    say(0.05, "airports")
    #
    # Everything here happens **before** any other family: the record and its geometry need
    # only OSM, and the smoothing that follows produces the raster all of them sample.
    check()
    started = time.perf_counter()
    airport_store = _required(layer_store(source, "airports", on_skip=on_skip), source, "airports")
    timing.read_s += time.perf_counter() - started
    started = time.perf_counter()
    airports, airport_counts = build_airports(
        airport_store, tile, airport_params, on_event=on_event
    )
    dem = smooth_elevation(dem, airports, airport_params)
    timing.airports_s = time.perf_counter() - started

    # -- patches (include_patches at :214, inside include_airports) ------------------------
    check()
    patch_started = time.perf_counter()
    patches: list[VectorLayer] = []
    patch_names: tuple[str, ...] = ()
    patches_area: BaseGeometry | None = None
    patch_root = getattr(request, "patches", None)
    if patch_root is not None:
        # The input **is** the tile's patch folder. Appending ``Patches/<tile>`` to it again
        # dropped every patch in silence (review 5, blocking finding).
        patched = build_patch_layers(Path(patch_root), tile, dem, on_event=on_event)
        patches = to_vector_layers("patches", patched.layers, _seeds(patched.seeds))
        patch_names = patched.names
        patches_area = patched.area
        counts["patches"] = dict(patched.counts)
        used = patched.counts["polygons"] + patched.counts["lines"] + patched.counts["objects"]
        if used:
            # Silence read as "the patches do not work": what was used is said, and a file none of
            # whose ways could be used says so itself (``patches.py``, 2026-09-17).
            log.warning(
                "%s: %d patch polygon(s), %d line(s) and %d object(s) from %s",
                tile.name,
                patched.counts["polygons"],
                patched.counts["lines"],
                patched.counts["objects"],
                ", ".join(patched.names),
            )
    timing.patches_s = time.perf_counter() - patch_started

    # -- airports, steps 11 to 15 (:215-222) -----------------------------------------------
    check()
    started = time.perf_counter()
    stage = encode(
        airports,
        tile,
        dem,
        airport_params,
        store=airport_store,
        patch_names=patch_names,
        patches_area=patches_area,
        on_event=on_event,
        cancel=cancel,
    )
    airport_layers = to_vector_layers("airports", stage.layers.layers, _seeds(stage.layers.seeds))
    counts["airports"] = {**airport_counts, **stage.counts}
    timing.airports_s += time.perf_counter() - started

    # -- roads (include_roads, :223-355) ---------------------------------------------------
    # at level 0 nothing here is built, and saying "at level 0" said the opposite (found in
    # review, 2026-09-23)
    say(
        0.25,
        f"roads and railways at level {road_level}" if road_level else "no roads at this setting",
    )
    check()
    started = time.perf_counter()
    roads: list[VectorLayer] = []
    if road_level:
        names = [spec.name for spec in layers_for(road_level) if spec.name.endswith("roads")]
        # Every layer the road level asks for is required: Ortho4XP would download the missing one
        # (``O4_Vector_Map.py:63``), so building without it is a different tile, not a lighter
        # one (review 5).
        stores = [
            store
            for store in (layer_store(source, name, on_skip=on_skip) for name in names)
            if store is not None
        ]
        timing.read_s += time.perf_counter() - started
        started = time.perf_counter()
        if stores:
            roaded = build_road_layers(
                stores,
                tile,
                dem,
                RoadParams(
                    road_level=road_level,
                    road_banking_limit=float(params.road_banking_limit),
                    lane_width=float(params.lane_width),
                    max_levelled_segs=int(params.max_levelled_segs),
                ),
                airports=stage.layers.airport_areas(),
                on_event=on_event,
            )
            roads = to_vector_layers("roads", roaded.layers, _seeds(roaded.seeds))
            counts["roads"] = dict(roaded.counts)
    timing.roads_s = time.perf_counter() - started

    # -- water first, because its large lakes are the sea-equivalent of the coast (A2) -----
    say(0.55, "lakes and rivers")
    check()
    started = time.perf_counter()
    water_store = _required(layer_store(source, "water", on_skip=on_skip), source, "water")
    timing.read_s += time.perf_counter() - started
    started = time.perf_counter()
    water_result = build_water_layers(
        water_store,
        tile,
        WaterParams(
            min_area=float(params.min_area),
            max_area=float(params.max_area),
            water_simplification=float(params.water_simplification),
            clean_bad_geometries=bool(params.clean_bad_geometries),
        ),
        dem,
        on_event=on_event,
    )
    water = to_vector_layers("water", water_result.layers, _seeds(water_result.seeds))
    counts["water"] = dict(water_result.counts)
    timing.water_s = time.perf_counter() - started

    # -- coastline (include_sea, :363-451) -------------------------------------------------
    say(0.7, "the coastline")
    check()
    started = time.perf_counter()
    coast_store = _required(layer_store(source, "coastline", on_skip=on_skip), source, "coastline")
    timing.read_s += time.perf_counter() - started
    started = time.perf_counter()
    coast_result = build_sea_layers(
        coast_store.ways_with(),
        tile,
        CoastParams(),
        sea_equiv=water_result.sea_equiv,
    )
    coastline = to_vector_layers(
        "coastline",
        coast_result.to_layers(dem.alt_vec),
        _seeds({"SEA": coast_result.seeds}) if len(coast_result.seeds) else None,
    )
    counts["coastline"] = {
        "ways": coast_result.stats.ways_read,
        "lines": coast_result.stats.sea_lines,
        "islands": coast_result.stats.islands,
        "polygons": coast_result.stats.sea_polygons,
        "seeds": coast_result.stats.seeds,
    }
    timing.coastline_s = time.perf_counter() - started

    log.info(
        "%s: vector layers built (%s)",
        tile.name,
        " ".join(f"{k}={v:.2f}s" for k, v in timing.as_dict().items() if k.endswith("_s")),
    )
    return LayerBuild(
        layers=VectorLayers(
            patches=tuple(patches),
            airports=tuple(airport_layers),
            roads=tuple(roads),
            coastline=tuple(coastline),
            water=tuple(water),
            airport_bounds=stage.layers.bounds,
        ),
        dem=dem,
        airports=airports,
        counts=counts,
        events=tuple(events),
    )


def _airport_params(smoothing_pix: int) -> AirportParams:
    """The airport constants of this run; only the smoothing width comes from the tile.

    A sweep of ``O4_Airport_Utils.py`` for ``tile.<attribute>`` finds ``dem``, ``lat``,
    ``lon`` and ``apt_smoothing_pix``, and only the last is configuration
    (``docs/specs/airports-integration.md`` 5). Every other number of the chain is hard-coded
    in Ortho4XP and lives in the dataclasses of :mod:`orthostudio.airports_vec.stage`.
    """
    return replace(DEFAULT_AIRPORT_PARAMS, smoothing_pix=smoothing_pix)


def _required(store: OsmData | None, source: OsmSource, layer: str) -> OsmData:
    """``layer_store(required=True)`` already raised; this is what says so to mypy.

    Review 5: the two ``assert store is not None`` this replaces vanish under ``python -O``,
    and the stage then died of an ``AttributeError`` inside shapely instead of a coded error.
    """
    if store is None:  # pragma: no cover - layer_store(required=True) raises first
        raise OsxpError(
            "OSM_LAYER_UNAVAILABLE",
            context={"layer": layer, "tile": source.tile.name, "attempts": str(source.root)},
        )
    return store


def _no_airport_artefact(request: object) -> None:
    """Refuse an airport artefact instead of consuming it and throwing it away.

    ``LayerRequest.airports`` is a declared input of the rule, so it enters the cache key:
    accepting one and ignoring it would key an artefact on data it does not contain (review 5).
    Wave 2 builds the airports **inside** this stage, from the ``aeroway`` OSM layer, exactly
    as ``include_airports`` does (``O4_Vector_Map.py:184-197``); the input therefore has no
    reader and stays absent. It is kept declared so that every key minted so far keeps its
    meaning.
    """
    if getattr(request, "airports", None) is None:
        return
    raise OsxpError(
        "SYS_INTERNAL_ERROR",
        context={"type": "NotImplementedError", "detail": "the airports input has no reader"},
        message="orthostudio.vectors@1 was given an airport artefact; it builds the airports "
        "itself, from the aeroway layer of its osm input.",
        remedy="Leave the airports input unset.",
    )


def stores_of(source: OsmSource, names: Sequence[str]) -> list[OsmData]:
    """Every named layer of ``source`` that exists, in order (used by the benchmarks)."""
    out = []
    for name in names:
        store = layer_store(source, name, required=False)
        if store is not None:
            out.append(store)
    return out
