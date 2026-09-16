"""``osxp build``: the graph of one tile (or a batch) declared on the scheduler and run.

Spec: ``docs/specs/pipeline-build.md``. Per tile: the elevation, the vector stage, the mesh and
the masks (``orthostudio.dem``, ``orthostudio.vectors``, ``orthostudio.mesh``,
``orthostudio.masks``), the XP12 rasters, the DSF (``orthostudio.dsf``), the textures
(``build_textures`` of P1), the overlay (``orthostudio.overlays``), the pack and the optional
installation (``orthostudio.pipeline.pack``). Every node is a P0 ``Rule`` whose artefact is keyed on
its consumed parameters and input digests; the scheduler (``orthostudio.sched``) runs what the
store lacks.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import signal
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import blake3

from orthostudio.dem import sources as dem_sources
from orthostudio.dem.rule import DEM_RULE, DemJob, DemParams, dem_job
from orthostudio.dem.sources import Download, default_elevation_dir, hem_latlon
from orthostudio.dem.xplane import XP12_INPUTS, XP12_SOURCE
from orthostudio.dsf import DsfParams, Xp12Rasters, airport_covers, build_dsf
from orthostudio.dsf.xp12 import global_scenery_dsf, rasters_from_dsf, read_global_scenery_dsf
from orthostudio.errors import OsxpError
from orthostudio.graph import (
    ResolvedInput,
    Rule,
    RuleParams,
    RunContext,
    Store,
    digest_file,
    key_for,
    rule,
)
from orthostudio.imagery.grid import TextureId
from orthostudio.imagery.providers import Provider, load_registry
from orthostudio.install import detect_xplane, global_scenery_dir
from orthostudio.masks.build import MAX_WORKERS as MASKS_MAX_WORKERS
from orthostudio.masks.build import env_workers as masks_env_workers
from orthostudio.masks.rule import MASKS as OSXP_MASKS
from orthostudio.masks.rule import MasksJob, masks_job
from orthostudio.masks.rule import MasksParams as OsxpMasksParams
from orthostudio.masks.water import NEIGHBOUR_OFFSETS
from orthostudio.mesh.build import mesh_file_name
from orthostudio.mesh.mesh_file import read_mesh, read_mesh_npz
from orthostudio.mesh.rule import OSXP_MESH, MeshJob, mesh_job
from orthostudio.mesh.rule import MeshParams as OsxpMeshParams
from orthostudio.model import OVERLAY_PACK, ArtifactRef, TileRef
from orthostudio.overlays import OverlayExclusions, build_overlay_detailed, find_dsftool
from orthostudio.overlays.source import resolve_global_scenery_dir
from orthostudio.pipeline.home import (
    default_chunks_root,
    default_store_root,
    default_work_root,
    require_data_root,
)
from orthostudio.pipeline.native import (
    COASTLINE_RULE,
    OSM_RULE,
    CoastlineParams,
    OsmJob,
    OsmOutcome,
    OsmParams,
    StageChoice,
    artefact_layers,
    current_osm_job,
    osm_job,
    resolve_stages,
    snapshot_label_of,
)
from orthostudio.pipeline.pack import (
    TILE_INSTALL,
    TILE_PACK,
    InstallParams,
    PackEnv,
    PackManifest,
    PackParams,
    assemble_pack,
    install_is_intact,
    install_receipt,
    pack_dir_name,
    pack_env,
    pack_is_intact,
)
from orthostudio.pipeline.textures import (
    ProgressSnapshot,
    TexturesReport,
    TexturesSpec,
    build_textures,
    default_workers,
    resolve_encoder,
    write_report,
)
from orthostudio.pipeline.textures import TextureJob as PipelineTextureJob
from orthostudio.sched import (
    Done,
    Event,
    Failed,
    Node,
    NodeContext,
    NodeKind,
    Scheduler,
    run_p0_rule,
)
from orthostudio.sources.osm import layers_for
from orthostudio.textures.ter import TerKind, TerParams
from orthostudio.tilefiles import masks_index, tile_cfg_text, tile_defaults
from orthostudio.vectors.layers import build_layers
from orthostudio.vectors.rule import VECTORS as OSXP_VECTORS
from orthostudio.vectors.rule import VectorsJob, vectors_job
from orthostudio.vectors.rule import VectorsParams as OsxpVectorsParams

__all__ = [
    "OVERLAY_SETTINGS",
    "RELIEF_SOURCES",
    "TEXTURES_JSON",
    "TILE_DSF",
    "TILE_OVERLAY",
    "TILE_TEXTURES",
    "XP12_RASTERS",
    "BuildEnv",
    "BuildEvent",
    "BuildReport",
    "BuildSpec",
    "NodeOutcome",
    "OverlayParams",
    "Phase",
    "TileDsfParams",
    "TileNodes",
    "TileOutcome",
    "TileTexturesParams",
    "Xp12RastersParams",
    "build_tile",
    "build_tiles",
    "declare",
    "parse_overlay_setting",
    "resolve_global_scenery",
    "run_osm_phase",
    "source_ref",
    "texture_jobs_from_json",
    "textures_progress_message",
]

log = logging.getLogger("orthostudio.build")

TEXTURES_JSON = "textures.json"
TEXTURES_FORMAT = "osxp-dsf-textures-1"
MANIFEST_FORMAT = "osxp-textures-1"
PER_TEXTURE_COST = "tile.textures/texture"
"""Cost-model name of the learnt seconds per texture (spec ``pipeline-build.md`` 5)."""
DEFAULT_PER_TEXTURE_S = 0.5
OVERLAY_SETTINGS: tuple[str, ...] = ("ovl_exclude_pol", "ovl_exclude_net", "keep_objects")
"""The overlay settings accepted next to the 44 tile variables (``config`` / ``--set``): the
Ortho4XP *application* variables ``ovl_exclude_pol`` / ``ovl_exclude_net``
(``O4_Config_Utils.py:93-108``) and OrthoStudio XP's ``keep_objects`` (``overlays.md`` D3)."""


# -- specification -------------------------------------------------------------------------------


RELIEF_ENV = "OSXP_RELIEF"
RELIEF_SOURCES = ("xplane", "view")
"""Where the native elevation stage takes its relief when ``custom_dem`` is empty (decision
0007): ``xplane`` = X-Plane 12's own (Global Scenery DSFs), ``view`` = the viewfinderpanoramas
cells Ortho4XP used by default."""


def default_relief() -> str:
    """``$OSXP_RELIEF``, else ``xplane``. The test suite sets ``view`` to match its references."""
    return os.environ.get(RELIEF_ENV, "").strip().lower() or "xplane"


def resolve_global_scenery(
    explicit: Path | None = None, *, xplane: Path | None = None
) -> Path | None:
    """The X-Plane 12 Global Scenery directory (spec 2.4), or ``None``.

    Order: ``explicit`` (the Global Scenery folder or the X-Plane folder), ``xplane``, then
    ``detect_xplane()``.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser())
    for xp in (xplane, detect_xplane()):
        if xp is not None:
            candidates.append(global_scenery_dir(Path(xp).expanduser()))
    for c in candidates:
        root = resolve_global_scenery_dir(c)
        if (root / "Earth nav data").is_dir():
            return root
    return None


def today() -> str:
    return dt.date.today().isoformat()


def parse_overlay_setting(name: str, raw: str) -> Any:
    """Type a ``--set`` / cfg value of an overlay setting like an Ortho4XP cfg line (no ``eval``).

    ``ovl_exclude_pol`` / ``ovl_exclude_net`` are Python list literals (``[0, ".for"]``,
    ``[22001]``); ``keep_objects`` is ``True`` / ``False``. Raises ``CFG_VALUE_INVALID``.
    """
    text = raw.strip()
    if name == "keep_objects":
        if text in ("True", "true", "1"):
            return True
        if text in ("False", "false", "0"):
            return False
        value: Any = None
    else:
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            value = None
    try:
        return _check_overlay_setting(name, value)
    except OsxpError:
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={
                "name": name,
                "value": raw,
                "type": "bool" if name == "keep_objects" else "list",
                "range": "-",
            },
            message=f"{name}={raw!r} is not a valid overlay setting.",
            remedy="ovl_exclude_pol / ovl_exclude_net take a list such as [0, '.for'] or "
            "[22001]; keep_objects takes True or False.",
        ) from None


def _check_overlay_setting(name: str, value: Any) -> Any:
    """Validate ``value`` through ``OverlayExclusions`` (its validators); returns it."""
    if name not in OVERLAY_SETTINGS:
        raise ValueError(name)
    try:
        checked = OverlayExclusions(**{name: value})
    except ValueError as exc:
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={"name": name, "value": value, "type": "-", "range": "-"},
            message=f"{name}={value!r}: {exc}",
        ) from None
    return getattr(checked, name)


@dataclass(slots=True)
class BuildSpec:
    """One tile to build (spec section 1). Paths are resolved by :class:`BuildEnv`."""

    tile: TileRef
    provider: str
    zl: int
    out_dir: Path
    global_scenery_dir: Path | None = None
    config: dict[str, Any] = field(default_factory=dict)
    """Overrides of the 44 tile variables of Ortho4XP (typed, ``tilefiles.TILE_PARAMETERS``)
    and of the overlay settings (``OVERLAY_SETTINGS``)."""
    ovl_exclude_pol: list[str | int] | None = None
    """Ortho4XP ``ovl_exclude_pol``; ``None`` = ``config``, else the OrthoStudio XP default
    (beaches by name)."""
    ovl_exclude_net: list[str | int] | None = None
    """Ortho4XP ``ovl_exclude_net`` (road types, ``22001`` = power lines); same resolution."""
    keep_objects: bool | None = None
    """Keep ``OBJECT`` lines in the overlay (OrthoStudio XP default True, ``overlays.md`` D3;
    Ortho4XP dropped them: ``False`` gives its exact output on sources with objects)."""
    dem: str = "vectors"
    """``vectors`` = the ``Data<tile>.alt`` of the vector stage (smoothed over the airports);
    ``native`` = the raw raster of the ``orthostudio.dem@1`` node (spec 8.2)."""
    osm_fetch: bool = True
    """Download the OSM layers the tile does not have yet (spec 8.3)."""
    osm_refresh: str = ""
    """Free label entering the OSM key: change it to ask for fresh OSM data."""
    relief: str = field(default_factory=default_relief)
    """``xplane`` / ``view`` (:data:`RELIEF_SOURCES`): the relief of the elevation stage when
    ``custom_dem`` is empty."""
    install: bool = False
    custom_scenery: Path | None = None
    overlay: bool = True
    xp12_rasters: bool = True
    creation_agent: str = "osxp"
    store_root: Path = field(default_factory=default_store_root)
    chunks_root: Path = field(default_factory=default_chunks_root)
    workdir: Path | None = None
    """Logs (default ``<OSXP_HOME>/work``)."""
    workers: int | None = None
    encoder: str = "auto"
    link: bool = True
    dsftool: Path | None = None
    library_path: Path | None = None
    registry_path: Path | None = None
    max_in_flight: int | None = None
    hedge_after_s: float = 1.0

    @property
    def level(self) -> str:
        """``BI14``: the imagery level used in node ids."""
        return f"{self.provider}{self.zl}"

    def tile_config(self) -> dict[str, Any]:
        """The 44 tile variables (Ortho4XP defaults, provider and zl, then ``config``), plus the
        overlay settings given by ``config`` or the spec fields."""
        values = tile_defaults()
        values["default_website"] = self.provider
        values["default_zl"] = int(self.zl)
        for name, value in self.config.items():
            if name in OVERLAY_SETTINGS:
                values[name] = _check_overlay_setting(name, value)
                continue
            if name not in values:
                raise OsxpError(
                    "CFG_VALUE_INVALID",
                    context={"name": name, "value": value, "type": "-", "range": "-"},
                    message=f"{name!r} is not an Ortho4XP tile parameter.",
                    remedy="See the 44 tile parameters in docs/specs/tile-files.md "
                    f"(overlay settings: {', '.join(OVERLAY_SETTINGS)}).",
                )
            values[name] = value
        for name in OVERLAY_SETTINGS:
            explicit = getattr(self, name)
            if explicit is not None:
                values[name] = _check_overlay_setting(name, explicit)
        return values


# -- environment -----------------------------------------------------------------------------

_BATCH_FIELDS = (
    "store_root",
    "chunks_root",
    "workdir",
    "global_scenery_dir",
    "dsftool",
    "workers",
    "library_path",
    "registry_path",
    "max_in_flight",
    "hedge_after_s",
)
"""Spec fields resolved once per batch: they must agree across the specs (else ValueError)."""


@dataclass(slots=True)
class BuildEnv:
    """Resolved locations and lazily created tools shared by the nodes of a batch."""

    store: Store
    store_root: Path
    chunks_root: Path
    workdir: Path
    global_scenery: Path | None
    dsftool: Path | None
    registry: dict[str, Provider]
    workers: int
    library_path: Path | None
    max_in_flight: int | None = None
    hedge_after_s: float = 1.0

    @classmethod
    def create(cls, specs: Sequence[BuildSpec], *, store: Store | None = None) -> BuildEnv:
        if not specs:
            raise ValueError("no tile to build")
        first = specs[0]
        for s in specs[1:]:
            for name in _BATCH_FIELDS:
                if getattr(s, name) != getattr(first, name):
                    raise ValueError(
                        f"all tiles of a batch must share {name} "
                        f"({getattr(first, name)!r} != {getattr(s, name)!r} for {s.tile.name})"
                    )
        global_scenery = first.global_scenery_dir
        if global_scenery is None:
            global_scenery = resolve_global_scenery()
        elif not (resolve_global_scenery_dir(global_scenery) / "Earth nav data").is_dir():
            raise OsxpError("XP_GLOBAL_SCENERY_NOT_FOUND", context={"path": str(global_scenery)})
        else:
            global_scenery = resolve_global_scenery_dir(global_scenery)
        dsftool = first.dsftool or find_dsftool()
        if first.workdir is not None:
            workdir = Path(first.workdir)
        else:  # never on the computer's own disk while the data folder's disk is unplugged
            require_data_root()
            workdir = default_work_root()
        workdir.mkdir(parents=True, exist_ok=True)
        return cls(
            store=store if store is not None else Store(first.store_root),
            store_root=Path(first.store_root),
            chunks_root=Path(first.chunks_root),
            workdir=workdir,
            global_scenery=global_scenery,
            dsftool=dsftool,
            registry=load_registry(first.registry_path),
            workers=first.workers if first.workers is not None else default_workers(),
            library_path=first.library_path,
            max_in_flight=first.max_in_flight,
            hedge_after_s=first.hedge_after_s,
        )

    @property
    def logs(self) -> Path:
        return self.workdir / "logs"

    def provider(self, code: str) -> Provider:
        try:
            return self.registry[code]
        except KeyError:
            known = ", ".join(sorted(self.registry))
            raise OsxpError(
                "CFG_PROVIDER_UNKNOWN",
                context={"provider": code, "known": known},
                message=f"Provider {code!r} is not in the registry ({known}).",
            ) from None


@dataclass(frozen=True, slots=True)
class _Active:
    env: BuildEnv
    ctx: NodeContext


_ACTIVE: ContextVar[_Active | None] = ContextVar("osxp_build_active", default=None)


@contextlib.contextmanager
def _active_env(env: BuildEnv, ctx: NodeContext) -> Iterator[_Active]:
    act = _Active(env, ctx)
    token = _ACTIVE.set(act)
    try:
        yield act
    finally:
        _ACTIVE.reset(token)


def _active() -> _Active:
    act = _ACTIVE.get()
    if act is None:
        raise RuntimeError("this rule runs under orthostudio.pipeline.build only")
    return act


# -- sources -------------------------------------------------------------------------------------


def source_ref(path: Path, *, label: str = "source") -> ArtifactRef:
    """An external file as a graph input: key and digest are both its content digest.

    The store never holds that key, so the edge is recorded as a *source* (P0 ``Source``).
    """
    p = Path(path)
    digest = digest_file(p)
    return ArtifactRef(digest, digest, p, label, "file", p.stat().st_size)


# -- params ------------------------------------------------------------------------------------


class Xp12RastersParams(RuleParams):
    tile: str


class TileDsfParams(DsfParams):
    """``DsfParams`` plus the tile and the ``sim/creation_agent`` property."""

    tile: str
    creation_agent: str = "osxp"


class TileTexturesParams(RuleParams):
    """What the textures artefact depends on beyond the DSF and masks digests."""

    tile: str
    encoder: str
    encoder_version: str
    mip_mode: str = "gamma22"
    refine_passes: int = 0
    sea_texture_blur: float = 0.0
    clean_halo: bool = False
    parent_levels: int = 5
    mask_zl: int = 14
    water_tech: str = "XP11 + bathy"
    imprint_masks_to_dds: bool = True
    use_decal_on_terrain: bool = False
    terrain_casts_shadows: bool = True
    use_test_texture: bool = False

    def ter_params(self) -> TerParams:
        return TerParams(
            water_tech=cast(Any, self.water_tech),
            imprint_masks_to_dds=self.imprint_masks_to_dds,
            mask_zl=self.mask_zl,
            use_decal_on_terrain=self.use_decal_on_terrain,
            terrain_casts_shadows=self.terrain_casts_shadows,
            use_test_texture=self.use_test_texture,
        )


class OverlayParams(OverlayExclusions):
    tile: str


# -- rules ------------------------------------------------------------------------------------


def _xp12_rasters(ctx: RunContext) -> None:
    """DEMN and clamped DEMS payloads of the Global Scenery DSF (spec ``dsf-xp12-rasters.md``)."""
    params = ctx.params
    assert isinstance(params, Xp12RastersParams)
    tile = TileRef.parse(params.tile)
    data = read_global_scenery_dsf(ctx.input_path("source"), tile)
    rasters = rasters_from_dsf(data, tile=tile)
    (ctx.out / "demn.bin").write_bytes(rasters.demn)
    (ctx.out / "dems.bin").write_bytes(rasters.dems)


def _read_rasters(path: Path) -> Xp12Rasters:
    return Xp12Rasters((path / "demn.bin").read_bytes(), (path / "dems.bin").read_bytes())


def _distance_lookup(masks_dir: Path) -> Callable[[int, int], Path | None] | None:
    """``(m_til_x, m_til_y) -> <y>_<x>_dist.png`` from the masks artefact, or ``None``."""
    index = masks_dir / "index.json"
    entries: dict[tuple[int, int], Path] = {}
    if index.is_file():
        with contextlib.suppress(ValueError, KeyError, TypeError):
            doc = json.loads(index.read_text(encoding="utf-8"))
            for m in doc.get("masks", []):
                if m.get("dist"):
                    entries[(int(m["til_x"]), int(m["til_y"]))] = masks_dir / str(m["dist"])
    if not entries:
        for p in masks_dir.glob("*_dist.png"):
            y, x, _ = p.name.split("_", 2)
            entries[(int(x), int(y))] = p
    if not entries:
        return None

    def lookup(m_til_x: int, m_til_y: int) -> Path | None:
        return entries.get((m_til_x, m_til_y))

    return lookup


def texture_jobs_to_json(jobs: Sequence[Any]) -> dict[str, Any]:
    """``textures.json`` of the DSF artefact from ``build_dsf``'s ``TextureJob`` list."""
    return {
        "format": TEXTURES_FORMAT,
        "textures": [
            {
                "til_x": j.texture.til_x,
                "til_y": j.texture.til_y,
                "zl": j.texture.zl,
                "provider": j.texture.provider,
                "kinds": sorted(k.value for k in j.kinds),
            }
            for j in jobs
        ],
    }


def texture_jobs_from_json(path: Path) -> list[PipelineTextureJob]:
    """The ``TextureJob`` list (pipeline flavour) of a DSF artefact's ``textures.json``."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != TEXTURES_FORMAT:
        raise ValueError(f"{path}: not an {TEXTURES_FORMAT} document")
    return [
        PipelineTextureJob(
            TextureId(int(t["til_x"]), int(t["til_y"]), int(t["zl"]), str(t["provider"])),
            tuple(TerKind(k) for k in t["kinds"]),
        )
        for t in doc["textures"]
    ]


def _tile_dsf(ctx: RunContext) -> None:
    """The DSF of the tile (``build_dsf``), its ``.ter`` texts and the texture list.

    Pure function of params and inputs: runs in a worker process.
    """
    params = ctx.params
    assert isinstance(params, TileDsfParams)
    tile = TileRef.parse(params.tile)
    mesh_dir = ctx.input_path("mesh")
    mesh_text = mesh_dir / mesh_file_name(tile)
    npz = mesh_dir / "mesh.npz"
    # The text file is authoritative when the artefact has one: it is what Ortho4XP's step 3
    # reads, and `orthostudio.mesh` publishes an npz carrying the *full precision* doubles of
    # Triangle4XP, which the text file truncates to 15 significant digits of z/100000. That
    # 5e-11 m is enough to move elevations across a u16 rounding boundary: the DSF of
    # +43+005 built from the npz has the same size but differs from Ortho4XP's on 23 bytes
    # spread over several pools (the first is GEOD/POOL[5]).
    # Cost of reading the text instead: 0.32 s against 0.006 s (spec 8.8).
    mesh = read_mesh(mesh_text) if mesh_text.is_file() else read_mesh_npz(npz)
    masks_input = ctx.inputs["masks"]
    lookup = None
    distance = None
    if masks_input.path is not None:
        index = masks_index(masks_input.path, params.mask_zl)
        lookup = index if len(index) else None
        distance = _distance_lookup(masks_input.path)
    rasters_input = ctx.inputs["rasters"]
    rasters = _read_rasters(rasters_input.path) if rasters_input.path is not None else None
    airports: list[Any] = []
    cover = params.cover_airports_with_highres
    if cover == "Existing":
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={
                "name": "cover_airports_with_highres",
                "value": cover,
                "type": "str",
                "range": "False, True, ICAO",
            },
            message="cover_airports_with_highres=Existing is not supported by osxp build yet.",
            remedy="Use True or ICAO.",
        )
    vectors_input = ctx.inputs["vectors"]
    if cover in ("True", "ICAO") and vectors_input.path is not None:
        airports = airport_covers(vectors_input.path)
    out = build_dsf(
        tile,
        mesh,
        lookup,
        params,
        rasters,
        creation_agent=params.creation_agent,
        distance_masks=distance,
        airports=airports,
    )
    (ctx.out / f"{tile.name}.dsf").write_bytes(out.data)
    terrain = ctx.out / "terrain"
    terrain.mkdir()
    for name, text in out.ter_files.items():
        (terrain / name).write_text(text, encoding="ascii", newline="\n")
    (ctx.out / TEXTURES_JSON).write_text(
        json.dumps(texture_jobs_to_json(out.textures), indent=1) + "\n", encoding="utf-8"
    )
    stats = {
        k: v for k, v in out.stats.items() if not k.endswith("_s")
    }  # counts only: timings would defeat early cutoff
    stats["textures"] = len(out.textures)
    stats["terrains"] = len(out.terrains)
    (ctx.out / "stats.json").write_text(
        json.dumps(stats, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


TEXTURES_RATE_WINDOW_S = 4.0
"""Seconds of progress reports the download rate in MB/s is measured over."""


def textures_progress_message(
    level: str, s: ProgressSnapshot, mb_per_s: float | None = None
) -> str:
    """The line the textures node reports: counts, rate, and the second pass in plain words.

    The page shows engine messages as they are, and the second pass pauses between its tries:
    say so ("asking again for 3 image piece(s), try 2 of 3, in 8 s"), or the step looks stuck.
    ``mb_per_s``, when known and above zero, is written as ", 21.6 MB/s" inside the brackets:
    the Works page reads it there to show the download rate of the Imagery step.
    """
    rate = f"{s.req_per_s:.0f} req/s"
    if mb_per_s is not None and mb_per_s > 0:
        rate += f", {mb_per_s:.1f} MB/s"
    message = (
        f"{level}: tiles {s.tiles_done}/{s.tiles_total}, textures "
        f"{s.built + s.hits}/{s.textures_total} ({rate})"
    )
    if s.second_pass_chunks and not s.second_pass_round:
        message += f", {s.second_pass_chunks} image piece(s) to ask for again"
    elif s.second_pass_chunks:
        wait = f", in {s.second_pass_wait_s:.0f} s" if s.second_pass_wait_s > 0 else ""
        message += (
            f", asking again for {s.second_pass_chunks} image piece(s), try "
            f"{s.second_pass_round} of {s.second_pass_rounds}{wait}"
        )
    return message


def _tile_textures(ctx: RunContext) -> None:
    """Every texture the DSF references: ``build_textures`` (P1) per (provider, zl) group."""
    act = _active()
    env, node = act.env, act.ctx
    params = ctx.params
    assert isinstance(params, TileTexturesParams)
    tile = TileRef.parse(params.tile)
    jobs = texture_jobs_from_json(ctx.input_path("dsf") / TEXTURES_JSON)
    masks_input = ctx.inputs["masks"]
    lookup: Any = None
    if masks_input.path is not None:
        index = masks_index(masks_input.path, params.mask_zl)
        lookup = index if len(index) else None
    (ctx.out / "textures").mkdir(exist_ok=True)
    (ctx.out / "terrain").mkdir(exist_ok=True)
    groups: dict[tuple[str, int], list[PipelineTextureJob]] = {}
    for job in jobs:
        groups.setdefault((job.texture.provider, job.texture.zl), []).append(job)
    total = len(jobs)
    done_before = 0
    outcomes: list[dict[str, Any]] = []
    cancel = node.cancel_event
    stop = cancel if isinstance(cancel, threading.Event) else threading.Event()
    for (code, zl), group in sorted(groups.items()):
        provider = env.provider(code)
        n = len(group)
        base = done_before

        samples: deque[tuple[float, int]] = deque()

        def relay(
            s: ProgressSnapshot,
            n: int = n,
            base: int = base,
            level: str = f"{code}{zl}",
            samples: deque[tuple[float, int]] = samples,
        ) -> None:
            tiles = s.tiles_done / s.tiles_total if s.tiles_total else 1.0
            done_tex = s.built + s.hits + s.failed + s.incomplete
            tex = done_tex / s.textures_total if s.textures_total else 1.0
            fraction = (base + (0.5 * tiles + 0.5 * tex) * n) / max(1, total)
            # MB/s over the last few seconds of reports: s.bytes only grows within a run.
            now = time.perf_counter()
            samples.append((now, s.bytes))
            while len(samples) > 2 and now - samples[0][0] > TEXTURES_RATE_WINDOW_S:
                samples.popleft()
            since, bytes_then = samples[0]
            mb_per_s = (s.bytes - bytes_then) / (now - since) / 1e6 if now - since >= 1.0 else None
            node.progress(fraction, textures_progress_message(level, s, mb_per_s))

        spec = TexturesSpec(
            lat=tile.lat,
            lon=tile.lon,
            provider=provider,
            zl=zl,
            jobs=group,
            out_dir=ctx.out,
            chunks_root=env.chunks_root,
            store_root=env.store_root,
            mask_lookup=lookup,
            mask_zl=params.mask_zl,
            ter_params=params.ter_params(),
            sea_texture_blur=params.sea_texture_blur,
            clean_halo=params.clean_halo,
            workers=env.workers,
            encoder=params.encoder,
            mip_mode=params.mip_mode,
            refine_passes=params.refine_passes,
            max_in_flight=env.max_in_flight,
            hedge_after_s=env.hedge_after_s,
            parent_levels=params.parent_levels,
            link=True,
            progress=relay,
            quiet=True,
            cancel=stop,
            fsync=env.store.fsync,
        )
        report: TexturesReport = build_textures(spec)
        env.logs.mkdir(parents=True, exist_ok=True)
        write_report(report, env.logs / f"textures-{tile.name}-{code}{zl}-{ctx.key[:12]}.json")
        if report.cancelled:
            raise OsxpError("SYS_CANCELLED", context={"node": node.node_id})
        if not report.ok:
            missing = report.missing
            codes = sorted({str((o.error or {}).get("code", "?")) for o in missing})
            raise OsxpError(
                "TEX_MISSING",
                context={
                    "count": len(missing),
                    "tile": tile.name,
                    "codes": codes,
                    "textures": [o.name for o in missing][:20],
                },
                message=f"{len(missing)} of {len(group)} {code}{zl} texture(s) of tile "
                f"{tile.name} could not be built ({', '.join(codes)}); nothing is committed.",
                remedy="Run osxp build again: only the missing tiles are downloaded and only the "
                f"missing textures encoded. Details: {env.logs}.",
            )
        outcomes.extend(
            {
                "name": o.name,
                "kinds": list(o.kinds),
                "status": o.status,
                "fmt": o.fmt,
                "key": o.key,
                "digest": o.digest,
            }
            for o in report.outcomes
        )
        done_before += n
    manifest = {
        "format": MANIFEST_FORMAT,
        "tile": tile.name,
        "textures": sorted(outcomes, key=lambda o: str(o["name"])),
    }
    (ctx.out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def _tile_overlay(ctx: RunContext) -> None:
    """The overlay DSF of the tile from its Global Scenery source (``build_overlay``)."""
    act = _active()
    env = act.env
    params = ctx.params
    assert isinstance(params, OverlayParams)
    tile = TileRef.parse(params.tile)
    src = ctx.input_path("source")
    if env.dsftool is None:
        raise OsxpError(
            "SYS_TOOL_MISSING",
            context={"tool": "DSFTool"},
            remedy="Reinstall OrthoStudio XP: its DSFTool (native/dsftool/) is missing.",
        )
    exclusions = OverlayExclusions(
        ovl_exclude_pol=params.ovl_exclude_pol,
        ovl_exclude_net=params.ovl_exclude_net,
        keep_objects=params.keep_objects,
    )
    result = build_overlay_detailed(
        src.parents[2],
        tile,
        exclusions,
        dsftool=env.dsftool,
        out_root=ctx.scratch / "out",
        workdir=ctx.scratch,
    )
    os.replace(result.path, ctx.out)
    env.logs.mkdir(parents=True, exist_ok=True)
    (env.logs / f"overlay-{tile.name}-{ctx.key[:12]}.json").write_text(
        json.dumps(result.stats.to_dict(), indent=1) + "\n", encoding="utf-8"
    )


XP12_RASTERS: Rule = rule(
    name="xp12.rasters",
    version=1,
    params=Xp12RastersParams,
    inputs=("source",),
    ram_mb=200,
    kind="dir",
)(_xp12_rasters)
TILE_DSF: Rule = rule(
    name="tile.dsf",
    version=1,
    params=TileDsfParams,
    inputs=("mesh", "masks", "rasters", "vectors"),
    ram_mb=3000,
    kind="dir",
)(_tile_dsf)
TILE_TEXTURES: Rule = rule(
    name="tile.textures",
    version=1,
    params=TileTexturesParams,
    inputs=("dsf", "masks"),
    ram_mb=3000,
    kind="dir",
)(_tile_textures)
TILE_OVERLAY: Rule = rule(
    name="tile.overlay",
    version=1,
    params=OverlayParams,
    inputs=("source",),
    ram_mb=300,
    kind="file",
)(_tile_overlay)


# -- graph declaration ---------------------------------------------------------------------------


@dataclass(slots=True)
class TileNodes:
    """The nodes of one spec (some shared with other specs of the batch)."""

    spec: BuildSpec
    vectors: Node
    mesh: Node
    masks: Node
    xp12: Node | None
    dsf: Node
    textures: Node
    overlay: Node | None
    pack: Node
    install: Node | None
    dem: Node | None = None
    coastline: Node | None = None
    stages: StageChoice = field(default_factory=StageChoice)
    osm: Node | None = None
    """The tile's OSM download, when the graph downloads it (a stored snapshot is an input)."""

    @property
    def target(self) -> Node:
        return self.install if self.install is not None else self.pack

    def all(self) -> list[Node]:
        nodes = [self.vectors, self.mesh, self.masks, self.dsf, self.textures, self.pack]
        for n in (self.osm, self.dem, self.coastline, self.xp12, self.overlay, self.install):
            if n is not None:
                nodes.append(n)
        return nodes

    @property
    def by_role(self) -> dict[str, Node]:
        roles = {
            "osm": self.osm,
            "dem": self.dem,
            "coastline": self.coastline,
            "vectors": self.vectors,
            "mesh": self.mesh,
            "masks": self.masks,
            "xp12": self.xp12,
            "dsf": self.dsf,
            "textures": self.textures,
            "overlay": self.overlay,
            "pack": self.pack,
            "install": self.install,
        }
        return {k: v for k, v in roles.items() if v is not None}


class _Registry:
    """Node ids of a batch: one node per (base id, params, inputs); ``#n`` on conflicts.

    Nodes reach the scheduler in :meth:`register`, after the whole batch validated, so a
    declaration that raises leaves the scheduler untouched.
    """

    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler
        self._by_base: dict[str, list[Node]] = {}

    def node(
        self,
        base_id: str,
        rule_: Rule,
        params: RuleParams,
        inputs: dict[str, Node | ArtifactRef | None],
        **kw: Any,
    ) -> Node:
        known = self._by_base.setdefault(base_id, [])
        for n in known:
            if n.rule is rule_ and n.params == params and n.inputs == dict(inputs):
                return n
        node_id = base_id if not known else f"{base_id}#{len(known) + 1}"
        node = Node(node_id, rule_, params, dict(inputs), **kw)
        known.append(node)
        return node

    def register(self) -> None:
        """Add every node to the scheduler, once the whole batch validated."""
        for nodes in self._by_base.values():
            for node in nodes:
                self.scheduler.add(node)


def dem_download_message(tile: TileRef, files: int, received: int, elapsed_s: float) -> str:
    """The progress line of a relief that downloads, ``+46+006: elevation, 2 file(s) (3.1 MB/s)``:
    the files received and the rate since the node started, in the brackets where the Works page
    reads the rate of any step (``ui.md`` 2.2; a user asked for it wherever the network works)."""
    text = f"{tile.name}: elevation, {files} file(s)"
    if received > 0 and elapsed_s > 0:
        text += f" ({received / 1e6 / elapsed_s:.1f} MB/s)"
    return text


def _dem_run(env: BuildEnv, spec: BuildSpec) -> Callable[[NodeContext], Any]:
    """Bind the elevation job (where the cells live, how to download) for ``orthostudio.dem@1``;
    a relief that downloads reports each file received and the rate (X-Plane's downloads none)."""

    def run(ctx: NodeContext) -> ArtifactRef:
        start = time.perf_counter()
        got = [0, 0]  # files, bytes

        def download(url: str) -> Download:
            answer = dem_sources.http_download(url)
            if answer.body:
                got[0] += 1
                got[1] += len(answer.body)
                elapsed = time.perf_counter() - start
                ctx.progress(0.0, dem_download_message(spec.tile, got[0], got[1], elapsed))
            return answer

        job = DemJob(
            tile=spec.tile,
            elevation_dir=default_elevation_dir(),
            download=download,
            cancel=cast(Any, ctx.cancel_event),
        )
        with dem_job(job):
            return run_p0_rule(ctx)

    return run


def _osm_run(env: BuildEnv) -> Callable[[NodeContext], Any]:
    """Bind the Overpass client (and the node's progress) for ``orthostudio.osm@1``.

    A binding made by the caller (``osm_job(...)``, which the tests use to inject a stub) is
    read *here*, in the declaring thread, and re-applied inside the node: the scheduler runs
    a ``net`` node in a pool thread, which does not inherit context variables.
    """
    outer = current_osm_job()

    def run(ctx: NodeContext) -> ArtifactRef:
        job = OsmJob(
            fetch=outer.fetch if outer is not None else None,
            timeout_s=outer.timeout_s if outer is not None else 300.0,
            cancel=cast(Any, ctx.cancel_event),
            progress=ctx.progress,
        )
        with osm_job(job):
            return run_p0_rule(ctx)

    return run


def _env_run(env: BuildEnv) -> Callable[[NodeContext], Any]:
    def run(ctx: NodeContext) -> ArtifactRef:
        with _active_env(env, ctx):
            return run_p0_rule(ctx)

    return run


def _masks_run(env: BuildEnv) -> Callable[[NodeContext], Any]:
    """Bind the masks job: the pool size the node budgeted for, and the cancellation token.

    Review 4, findings C1 and M1: the rule used to read a module-level hook nothing ever set
    (no cancellation, shared between concurrent nodes) and no worker count at all (the pool
    started ``os.cpu_count()`` processes against a budget computed for ``min(8, workers)``).
    """
    workers = masks_workers(env)

    def run(ctx: NodeContext) -> ArtifactRef:
        job = MasksJob(workers=workers, cancel=cast(Any, ctx.cancel_event))
        with _active_env(env, ctx), masks_job(job):
            return run_p0_rule(ctx)

    return run


def _mesh_run(env: BuildEnv) -> Callable[[NodeContext], Any]:
    """Bind the mesh job so that Triangle4XP can be interrupted (review 4, finding C4)."""

    def run(ctx: NodeContext) -> ArtifactRef:
        with _active_env(env, ctx), mesh_job(MeshJob(cancel=cast(Any, ctx.cancel_event))):
            return run_p0_rule(ctx)

    return run


def _pack_run(env: BuildEnv, spec: BuildSpec) -> Callable[[NodeContext], Any]:
    penv = PackEnv(env.store, Path(spec.out_dir), spec.custom_scenery, env.library_path)

    def run(ctx: NodeContext) -> ArtifactRef:
        with pack_env(penv):
            return run_p0_rule(ctx)

    return run


def overlay_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """The ``OverlayExclusions`` fields of a tile: ``cfg`` (spec fields / ``config``), else the
    OrthoStudio XP defaults."""
    defaults = OverlayExclusions()
    return {
        name: cfg[name] if name in cfg else getattr(defaults, name) for name in OVERLAY_SETTINGS
    }


def _refuse_pack_dir_conflict(
    seen: dict[tuple[str, str], tuple[BuildSpec, Node, Node]],
    spec: BuildSpec,
    out_dir: str,
    dsf: Node,
    textures: Node,
) -> None:
    """Two specs of a batch writing different content to the same ``<out>/zOrthoStudio_<tile>/``.

    The pack directory is named by the tile only (X-Plane loads one base mesh per square): the same
    tile at two zoom levels, or with two parameter sets, into one output directory would overwrite
    each other's DSF (``.bak``) and textures (removed as stale) and flip-flop at every run.
    ``CFG_VALUE_INVALID``; one level per output directory.
    """
    slot = (spec.tile.name, out_dir)
    prior = seen.get(slot)
    if prior is None:
        seen[slot] = (spec, dsf, textures)
        return
    other, other_dsf, other_textures = prior
    if other_dsf is dsf and other_textures is textures:
        return  # the same pack twice: one node, one execution
    raise OsxpError(
        "CFG_VALUE_INVALID",
        context={
            "name": "out_dir",
            "value": out_dir,
            "type": "-",
            "range": f"{other.level} and {spec.level} of {spec.tile.name}",
        },
        message=f"Tile {spec.tile.name} is requested twice ({other.level} and {spec.level}) "
        f"into the same output directory {out_dir}: both would write "
        f"{pack_dir_name(spec.tile)}/ and destroy each other.",
        remedy="Give each level its own --out directory (the pack is named by the tile, as in "
        "Ortho4XP), or build one level per run.",
    )


def stored_neighbour_mesh(store: Store, neighbour: TileRef) -> ArtifactRef | None:
    """The most recently used ``orthostudio.mesh@1`` artefact holding ``Data<neighbour>.mesh``
    (spec 8.5)."""
    best: ArtifactRef | None = None
    best_used = -1.0
    for info in store.iter_artifacts(OSXP_MESH.name):
        if (info.path / mesh_file_name(neighbour)).is_file() and info.last_used_at > best_used:
            best_used = info.last_used_at
            best = ArtifactRef(info.key, info.digest, info.path, info.rule, info.kind, info.size)
    return best


def _masks_ram_mb(env: BuildEnv) -> int:
    """RAM of the native masks node: it runs its own process pool inside (spec 8.6)."""
    workers = masks_workers(env)
    return 300 + 800 * workers


def masks_workers(env: BuildEnv) -> int:
    """Processes ``orthostudio.masks`` may use: ``$OSXP_MASKS_WORKERS``, else the batch's workers
    (<= 8).

    Eight is where the measured gain stops (``masks-build.md`` 6: 0.74 s at 8 workers against 1.40 s
    alone, the pool start-up being 0.45 s of it). The environment variable is parsed by
    :func:`orthostudio.masks.build.env_workers` -- the single parser (review 4, finding M2) -- and
    the number returned here is **both** what ``_masks_ram_mb`` budgets and what the node hands the
    rule through :class:`MasksJob`, so the two can no longer disagree (finding M1).
    """
    explicit = masks_env_workers()
    if explicit is not None:
        return explicit
    return max(1, min(MASKS_MAX_WORKERS, env.workers))


def _coastline_node(reg: _Registry, spec: BuildSpec, osm_input: Node | ArtifactRef) -> Node:
    """The ``orthostudio.coastline`` node of a tile (spec 8.4).

    It reads the OSM data the vector stage reads (``osm_input``): the tile's OSM node, a stored
    snapshot, or the placeholder of the layers still to fetch in an estimate.
    The mesh and the vector stage therefore never read two different extracts of the coast, and
    there always is one: the stage demands the ``coastline`` layer at every road level.
    """
    return reg.node(
        f"{spec.tile.name}/coastline",
        COASTLINE_RULE,
        CoastlineParams(tile=spec.tile.name),
        {"osm": osm_input},
        kind="io",
    )


def _vectors_osm_input(
    spec: BuildSpec,
    cfg: dict[str, Any],
    osm_ref: ArtifactRef | None,
    *,
    planning: bool = False,
) -> ArtifactRef:
    """The OSM layers ``orthostudio.vectors@1`` reads: the snapshot phase 0 produced (spec 8.5).

    It must hold every layer of the tile's ``road_level``, ``airports`` included since the stage
    builds the aerodromes from it: without it the tile would have none, which is a different tile
    and not a lighter one. An estimate declares the graph *before* phase 0 runs, so a cold tile
    has no layers yet; that is not an error, and a placeholder makes the node plan as "to build".
    """
    tile = spec.tile
    road_level = int(cfg.get("road_level", 1) or 0)
    wanted = [s.name for s in layers_for(road_level)]
    have = set(artefact_layers(osm_ref.path)) if osm_ref is not None else set()
    if osm_ref is not None and set(wanted) <= have:
        return osm_ref
    if planning:
        label = f"osm-to-fetch:{tile.name}:{','.join(sorted(wanted))}"
        digest = blake3.blake3(label.encode()).hexdigest()
        return ArtifactRef(digest, digest, Path(), "osm_to_fetch", "dir", 0)
    missing = [name for name in wanted if name not in have]
    raise OsxpError(
        "OSM_LAYER_UNAVAILABLE",
        context={"layer": ", ".join(missing), "tile": tile.name, "attempts": "snapshot"},
        message=f"The vector stage needs the {', '.join(wanted)} layers of {tile.name}: "
        f"{', '.join(missing)} could not be downloaded.",
        remedy=(
            "Remove --no-osm-fetch: OrthoStudio XP downloads these layers itself before building."
            if not spec.osm_fetch
            else "OrthoStudio XP's OSM client could not get these layers from any mirror (see "
            "the osm step above). Check the network and run the build again, or add "
            "--osm-refresh to force a fresh download."
        ),
    )


def _vectors_run(env: BuildEnv, spec: BuildSpec) -> Callable[[NodeContext], Any]:
    """Bind the layer builders and the cancellation token for ``orthostudio.vectors@1``."""

    def run(ctx: NodeContext) -> ArtifactRef:
        job = VectorsJob(build_layers=build_layers, cancel=cast(Any, ctx.cancel_event))
        with _active_env(env, ctx), vectors_job(job):
            return run_p0_rule(ctx)

    return run


def stage_choices(specs: Sequence[BuildSpec]) -> list[StageChoice]:
    """Check the settings of every spec of a batch against what the stages can build.

    Called by ``build_tiles`` **before** phase 0, so that a setting the stages cannot honour is
    refused before an OSM download (review 4, finding V1); ``declare`` reuses the result instead
    of resolving again.
    """
    for s in specs:
        if s.relief not in RELIEF_SOURCES:
            raise OsxpError(
                "CFG_VALUE_INVALID",
                context={
                    "name": "relief",
                    "value": s.relief,
                    "type": "str",
                    "range": " | ".join(RELIEF_SOURCES),
                },
            )
    return [resolve_stages(s.tile_config(), dem=s.dem) for s in specs]


def dem_declaration(
    spec: BuildSpec,
    cfg: Mapping[str, Any],
    global_source: Callable[[TileRef], ArtifactRef | None],
    global_scenery: Path | None,
) -> tuple[dict[str, Any], dict[str, Node | ArtifactRef | None]]:
    """Params config and inputs of the ``orthostudio.dem`` node of ``spec`` (decision 0007).

    With ``relief = xplane`` and no ``custom_dem``, the source is ``XP12`` and the nine inputs are
    the Global Scenery DSFs of the 3x3 block (absent for a cell X-Plane has no DSF for). The tile's
    own DSF is required: without it the relief would be flat, and OrthoStudio XP refuses that here,
    before anything runs, rather than in the node.
    """
    params: dict[str, Any] = {**cfg, "tile": spec.tile.name}
    inputs: dict[str, Node | ArtifactRef | None] = dict.fromkeys(XP12_INPUTS)
    if spec.relief != "xplane" or cfg.get("custom_dem"):
        return params, inputs
    if global_source(spec.tile) is None:
        missing = (
            global_scenery_dsf(global_scenery, spec.tile) if global_scenery is not None else None
        )
        where = (
            str(missing) if missing is not None else "no X-Plane 12 Global Scenery folder was found"
        )
        # the tile and its file named: a user without X-Plane read only "no relief" (2026-09-15)
        context = {
            "cell": hem_latlon(spec.tile.lat, spec.tile.lon),
            "source": XP12_SOURCE,
            "reason": f"no Global Scenery DSF: {where}",
            "tile": spec.tile.name,
        }
        if missing is not None:
            context["path"] = str(missing)
        raise OsxpError(
            "DEM_TILE_UNAVAILABLE",
            context=context,
            remedy="Install this region of the X-Plane 12 Global Scenery with the X-Plane "
            "installer (or give --global-scenery / --xplane), or build with --relief view.",
        )
    params["custom_dem"] = XP12_SOURCE
    for name, (dlat, dlon) in XP12_INPUTS.items():
        lat = spec.tile.lat + dlat
        inputs[name] = global_source(spec.tile.neighbour(dlat, dlon)) if -90 <= lat < 90 else None
    return params, inputs


def declare(
    specs: Sequence[BuildSpec],
    scheduler: Scheduler,
    env: BuildEnv,
    *,
    unavailable: Collection[TileRef] = (),
    osm_artefacts: Mapping[TileRef, ArtifactRef] | None = None,
    choices: Sequence[StageChoice] | None = None,
    planning: bool = False,
    fetch_osm: bool = False,
) -> list[TileNodes]:
    """Declare the graphs of ``specs`` on ``scheduler`` (spec sections 2 and 8); their nodes.

    ``unavailable`` names tiles of the batch whose mesh must not be wired as a neighbour (their
    stage failed in a first pass): the neighbour falls back to the store, else absent.
    ``osm_artefacts`` maps a tile to its stored ``orthostudio.osm`` artefact (spec 8.3); the vector
    stage and the coastline node both read it. ``fetch_osm`` (a build) gives a tile without one,
    whose spec downloads, an OSM node of its own, on the Overpass lane, which the two read instead:
    the tile goes on as soon as its own layers are in. ``planning`` (an estimate) lets a tile
    without one plan its OSM layers as still to fetch. ``choices``
    are the stage choices already resolved by :func:`stage_choices` (the caller validates the
    settings before phase 0 runs); ``None`` resolves them here.
    """
    reg = _Registry(scheduler)
    configs = [s.tile_config() for s in specs]
    if choices is None:
        choices = [resolve_stages(cfg, dem=s.dem) for s, cfg in zip(specs, configs, strict=True)]
    elif len(choices) != len(specs):
        raise ValueError("choices must hold one StageChoice per spec")
    osm_refs = dict(osm_artefacts or {})
    source_cache: dict[TileRef, ArtifactRef | None] = {}

    def global_source(tile: TileRef) -> ArtifactRef | None:
        if tile not in source_cache:
            ref = None
            if env.global_scenery is not None:
                path = global_scenery_dsf(env.global_scenery, tile)
                if path.is_file():
                    ref = source_ref(path, label="global_scenery")
            source_cache[tile] = ref
        return source_cache[tile]

    mesh_by_tile: dict[TileRef, Node] = {}
    osm_nodes: list[Node | None] = []
    vectors_nodes: list[Node] = []
    mesh_nodes: list[Node] = []
    dem_nodes: list[Node | None] = []
    coast_nodes: list[Node] = []
    final_choices: list[StageChoice] = []
    for spec, cfg, choice in zip(specs, configs, choices, strict=True):
        name = spec.tile.name
        # ``orthostudio.vectors@1`` needs the raw elevation as its input, whatever the mesh reads.
        dem_cfg, dem_inputs = dem_declaration(spec, cfg, global_source, env.global_scenery)
        if dem_cfg.get("custom_dem") == XP12_SOURCE:
            log.info("%s: relief from X-Plane 12's Global Scenery", name)
        dem = reg.node(
            f"{name}/dem",
            DEM_RULE,
            DemParams.subset_of(dem_cfg),
            dem_inputs,
            # X-Plane 12's relief is read from its own DSFs, not downloaded: it has no business
            # queueing on the imagery's network slot, where a batch's elevations ran one after
            # the other and the vector stages waited for them
            kind="subprocess" if dem_cfg.get("custom_dem") == XP12_SOURCE else "net",
            run=_dem_run(env, spec),
        )
        osm_ref = osm_refs.get(spec.tile)
        osm_input: Node | ArtifactRef
        osm: Node | None = None
        if fetch_osm and spec.osm_fetch and osm_ref is None:
            osm = osm_input = reg.node(
                f"{name}/osm",
                OSM_RULE,
                OsmParams(
                    tile=name,
                    road_level=int(cfg.get("road_level", 1) or 0),
                    refresh=spec.osm_refresh,
                ),
                {},
                kind="net",
                lane=OVERPASS_LANE,
                run=_osm_run(env),
            )
        else:
            osm_input = _vectors_osm_input(spec, cfg, osm_ref, planning=planning)
        vectors = reg.node(
            f"{name}/vectors",
            OSXP_VECTORS,
            OsxpVectorsParams.subset_of({**cfg, "tile": name}),
            {
                "osm": osm_input,
                "dem": dem,
                # Ortho4XP's hand-written patches came from its own folder, which the build no
                # longer reads (decision 0010): the input keeps its place in the key, absent.
                "patches": None,
                # Wave 2 builds the airports inside the stage, from the aeroway layer of
                # ``osm`` (``airports-integration.md`` 1); the input keeps its place in the key
                # and stays absent.
                "airports": None,
            },
            # Not "cpu": a cpu node is shipped to a spawned worker process, and neither this
            # node's run closure (it binds the build env) nor the decorated rule function
            # pickles -- every real build failed with a PicklingError before the first layer was
            # read (found by the review-6 fix pass; the tests ran the scheduler with
            # cpu_in_threads). It runs in the scheduler's thread pool, in a subprocess slot;
            # numpy, shapely and Triangle-free noding release the GIL enough.
            kind="subprocess",
            run=_vectors_run(env, spec),
        )
        coast = _coastline_node(reg, spec, osm_input)
        mesh = reg.node(
            f"{name}/mesh",
            OSXP_MESH,
            OsxpMeshParams.subset_of({**cfg, "tile": name}),
            {
                "vectors": vectors,
                # ``--dem vectors`` (the default) is the vector stage's own raster, smoothed
                # over the airports; ``--dem native`` is the raw one of ``orthostudio.dem@1``
                # (``airports-integration.md`` 3).
                "dem": dem if choice.dem == "native" else vectors,
                "coastline": coast,
            },
            kind="subprocess",
            run=_mesh_run(env),
        )
        osm_nodes.append(osm)
        vectors_nodes.append(vectors)
        mesh_nodes.append(mesh)
        dem_nodes.append(dem)
        coast_nodes.append(coast)
        final_choices.append(choice)
        mesh_by_tile.setdefault(spec.tile, mesh)

    out: list[TileNodes] = []
    pack_dirs: dict[tuple[str, str], tuple[BuildSpec, Node, Node]] = {}
    for spec, cfg, choice, vectors, mesh, dem, coast, osm in zip(
        specs,
        configs,
        final_choices,
        vectors_nodes,
        mesh_nodes,
        dem_nodes,
        coast_nodes,
        osm_nodes,
        strict=True,
    ):
        tile, name = spec.tile, spec.tile.name
        neighbours: dict[str, Node | ArtifactRef | None] = {}
        for input_name, (dlat, dlon) in NEIGHBOUR_OFFSETS.items():
            nb = tile.neighbour(dlat, dlon)
            node_nb = None if nb in unavailable else mesh_by_tile.get(nb)
            neighbours[input_name] = (
                node_nb if node_nb is not None else stored_neighbour_mesh(env.store, nb)
            )
        masks = reg.node(
            f"{name}/masks",
            OSXP_MASKS,
            OsxpMasksParams.subset_of({**cfg, "tile": name}),
            {"mesh": mesh, **neighbours, "dem": None, "custom_extent": None},
            kind="subprocess",
            ram_mb=_masks_ram_mb(env),
            run=_masks_run(env),
        )
        xp12: Node | None = None
        overlay: Node | None = None
        if spec.xp12_rasters or spec.overlay:
            source = global_source(tile)
            if source is None:
                where = env.global_scenery or "<not found>"
                raise OsxpError(
                    "DSF_GLOBAL_SCENERY_MISSING",
                    context={"tile": name, "path": str(global_scenery_dsf(Path(where), tile))},
                    remedy="Give --global-scenery (the X-Plane 12 Global Scenery folder) or "
                    "--xplane; --no-xp12-rasters --no-overlay builds without it (no XP12 sea "
                    "level, no overlay pack).",
                )
            if spec.xp12_rasters:
                xp12 = reg.node(
                    f"{name}/xp12",
                    XP12_RASTERS,
                    Xp12RastersParams(tile=name),
                    {"source": source},
                    kind="io",
                )
            if spec.overlay:
                overlay = reg.node(
                    f"{name}/overlay",
                    TILE_OVERLAY,
                    OverlayParams(tile=name, **overlay_settings(cfg)),
                    {"source": source},
                    kind="subprocess",
                    run=_env_run(env),
                )
        dsf_params = TileDsfParams.subset_of(
            {**cfg, "tile": name, "creation_agent": spec.creation_agent}
        )
        needs_apt = dsf_params.cover_airports_with_highres in ("True", "ICAO")
        dsf = reg.node(
            f"{name}/{spec.level}/dsf",
            TILE_DSF,
            dsf_params,
            {
                "mesh": mesh,
                "masks": masks,
                "rasters": xp12,
                "vectors": vectors if needs_apt else None,
            },
            kind="cpu",
        )
        encoder, encoder_version = resolve_encoder(spec.encoder)
        tex_params = TileTexturesParams.subset_of(
            {**cfg, "tile": name, "encoder": encoder, "encoder_version": encoder_version}
        )
        textures = reg.node(
            f"{name}/{spec.level}/textures",
            TILE_TEXTURES,
            tex_params,
            {"dsf": dsf, "masks": masks},
            kind="net",
            ram_mb=max(500, 250 * env.workers),
            run=_env_run(env),
        )
        out_dir = str(Path(spec.out_dir).expanduser().resolve())
        _refuse_pack_dir_conflict(pack_dirs, spec, out_dir, dsf, textures)
        pack = reg.node(
            f"{name}/{spec.level}/pack",
            TILE_PACK,
            PackParams(
                tile=name,
                provider=spec.provider,
                zl=spec.zl,
                out_dir=out_dir,
                link=spec.link,
                tile_cfg=tile_cfg_text(cfg),
            ),
            {"dsf": dsf, "textures": textures, "overlay": overlay},
            kind="io",
            run=_pack_run(env, spec),
        )
        install: Node | None = None
        if spec.install:
            if spec.custom_scenery is None:
                raise OsxpError(
                    "XP_DIR_NOT_FOUND",
                    context={"path": "<none>"},
                    message="--install needs the X-Plane folder (not detected).",
                    remedy="Give --xplane DIR (osxp doctor shows what was tried).",
                )
            install = reg.node(
                f"{name}/{spec.level}/install",
                TILE_INSTALL,
                InstallParams(
                    tile=name,
                    custom_scenery=str(Path(spec.custom_scenery).expanduser()),
                    link=spec.link,
                ),
                {"pack": pack},
                kind="io",
                run=_pack_run(env, spec),
            )
        out.append(
            TileNodes(
                spec,
                vectors,
                mesh,
                masks,
                xp12,
                dsf,
                textures,
                overlay,
                pack,
                install,
                dem=dem,
                coastline=coast,
                stages=choice,
                osm=osm,
            )
        )
    reg.register()
    return out


# -- running -------------------------------------------------------------------------------------


@dataclass(slots=True)
class NodeOutcome:
    id: str
    role: str
    rule: str
    key: str | None
    status: str
    """``hit`` / ``built`` / ``failed`` / ``skipped`` (upstream failure) / ``pending``."""
    wall_s: float = 0.0
    error: dict[str, Any] | None = None
    cause: str | None = None


@dataclass(slots=True)
class TileOutcome:
    tile: str
    provider: str
    zl: int
    ok: bool
    pack_dir: str | None
    overlay_dsf: str | None
    installed: bool
    nodes: list[NodeOutcome] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    """Effects redone after a hit (``pack``, ``install``)."""
    stages: dict[str, Any] = field(default_factory=dict)
    """The elevation the mesh read (spec 8.1)."""
    osm: dict[str, Any] = field(default_factory=dict)
    """What phase 0 did for this tile (spec 8.3)."""

    @property
    def error(self) -> NodeOutcome | None:
        """The first node that failed on its own (not because of an upstream)."""
        for n in self.nodes:
            if n.status == "failed" and n.cause is None:
                return n
        return None


@dataclass(slots=True)
class BuildReport:
    tiles: list[TileOutcome]
    elapsed_s: float
    built: int
    hits: int
    failed: int
    cancelled: bool
    store_root: str
    out_dir: str

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.tiles) and not self.cancelled

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "ok": self.ok,
            "elapsed_s": round(self.elapsed_s, 3),
            "built": self.built,
            "hits": self.hits,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "store_root": self.store_root,
            "out_dir": self.out_dir,
            "tiles": [
                {
                    "tile": t.tile,
                    "provider": t.provider,
                    "zl": t.zl,
                    "ok": t.ok,
                    "pack_dir": t.pack_dir,
                    "overlay_dsf": t.overlay_dsf,
                    "installed": t.installed,
                    "repaired": t.repaired,
                    "stages": t.stages,
                    "osm": t.osm,
                    "nodes": [
                        {
                            "id": n.id,
                            "role": n.role,
                            "rule": n.rule,
                            "key": n.key,
                            "status": n.status,
                            "wall_s": round(n.wall_s, 3),
                            "error": n.error,
                            "cause": n.cause,
                        }
                        for n in t.nodes
                    ],
                }
                for t in self.tiles
            ],
        }


def default_subprocess_slots() -> int:
    """How many relief, vector, mesh, masks and overlay nodes run at once: the cores less one, 3
    at most. It was a quarter of the cores: 3 on a 14-core Mac, but 1 on a 4-core Windows virtual
    machine, where these stages of a batch ran one after the other and a user saw every tile's
    data before any terrain (2026-09-15). The RAM budget still holds back what memory cannot."""
    return min(3, max(1, (os.cpu_count() or 4) - 1))


def physical_memory_mb() -> int | None:
    """Installed RAM in MB, or ``None`` when the platform does not say.

    ``os.sysconf`` does not exist on Windows, where the scheduler then ran with **no** RAM
    budget at all and every ``ram_mb`` the native nodes declare became inert (review 4,
    portability finding); ``GlobalMemoryStatusEx`` is the answer there, and a build with no
    budget at all now says so.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return _windows_memory_mb()
    if pages <= 0 or page <= 0:
        return None
    return int(pages * page / 2**20)


def _windows_memory_mb() -> int | None:
    """``GlobalMemoryStatusEx().ullTotalPhys``; ``None`` anywhere else."""
    if os.name != "nt":
        return None
    import ctypes

    class _Status(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _Status()
    status.dwLength = ctypes.sizeof(_Status)
    with contextlib.suppress(OSError, AttributeError):
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.ullTotalPhys / 2**20)
    return None


OVERPASS_LANE = "overpass"
"""The lane of the OSM downloads (``Node.lane``): one tile at a time, since the Overpass client
already takes its mirrors' quota (two requests a cluster), and apart from the imagery's network
slot, so that a tile's download never waits for another tile's images."""


def make_scheduler(
    store: Store,
    env: BuildEnv,
    *,
    cpu_workers: int | None = None,
    cpu_in_threads: bool = False,
) -> Scheduler:
    """The batch scheduler with the pool sizes of spec section 4."""
    mem = physical_memory_mb()
    if mem is None:
        log.warning(
            "this platform does not report its physical memory: the scheduler runs without a "
            "RAM budget, so the ram_mb of the mask and mesh nodes is not enforced"
        )
    return Scheduler(
        store,
        cpu_workers=cpu_workers if cpu_workers is not None else env.workers,
        subprocess_slots=default_subprocess_slots(),
        net_slots=1,
        io_slots=2,
        ram_budget_mb=None if mem is None else int(mem * 0.6),
        workdir=env.workdir / "sched",
        cpu_in_threads=cpu_in_threads,
        lanes={OVERPASS_LANE: 1},
    )


@dataclass(frozen=True, slots=True)
class Phase:
    """The batch enters a phase (spec section 4.1). ``build_tiles`` emits it through
    ``on_event``, next to the scheduler's events; the scheduler never does.

    ``data`` is phase 0, the OSM downloads on a scheduler of their own; ``build`` is the main
    graph. Each scheduler's ``Stats`` only knows its own nodes, so a consumer that shows the
    whole job needs to know where one phase ends and which nodes the next one declared.

    ``nodes`` holds ``(node id, kind, rule name)`` of every node the phase declared, or is
    ``None`` when the phase has begun but not declared yet (``build`` is announced as soon as
    phase 0 returns). ``reused`` names the rows that will not run because what they produce is
    already there -- the ``osm`` row of a tile whose snapshot the store already holds -- with the
    key of that snapshot; they are known before any download starts.
    """

    name: Literal["data", "build"]
    nodes: tuple[tuple[str, NodeKind, str], ...] | None = None
    reused: tuple[tuple[str, str | None], ...] = ()


BuildEvent = Event | Phase
"""What ``build_tiles`` hands ``on_event``: the scheduler's events and :class:`Phase`."""


def _emit_phase(on_event: Callable[[BuildEvent], None] | None, phase: Phase) -> None:
    if on_event is not None:
        on_event(phase)


def _declared(scheduler: Scheduler) -> tuple[tuple[str, NodeKind, str], ...]:
    return tuple((n.id, n.kind, n.rule.name) for n in scheduler.nodes.values())


async def _run_phase0(
    scheduler: Scheduler,
    nodes: Mapping[TileRef, Node],
    collector: _Collector,
    *,
    handle_sigint: bool,
) -> dict[str, ArtifactRef]:
    """Run the OSM nodes under a SIGINT handler of phase 0's own (review 4, finding V2).

    ``build_tiles`` installs its handler inside the *main* run; a Ctrl-C during phase 0 used
    to escape as a ``KeyboardInterrupt`` while the download thread kept the process alive.
    Same shape as the main handler: first Ctrl-C cancels the scheduler, a second one cancels
    the task.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    installed = False

    def on_sigint() -> None:
        if not scheduler.cancelled:
            scheduler.cancel()
        elif task is not None:
            task.cancel()

    previous: Any = None
    if handle_sigint:
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError, AttributeError):
            loop.add_signal_handler(signal.SIGINT, on_sigint)
            installed = True
        if not installed:
            with contextlib.suppress(ValueError, OSError, AttributeError):
                previous = signal.signal(signal.SIGINT, lambda *_: on_sigint())
    try:
        return await scheduler.run([n.id for n in nodes.values()], on_event=collector)
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGINT)
        if previous is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGINT, previous)


def run_osm_phase(
    specs: Sequence[BuildSpec],
    env: BuildEnv,
    *,
    on_event: Callable[[BuildEvent], None] | None = None,
    handle_sigint: bool = True,
) -> dict[TileRef, OsmOutcome]:
    """Phase 0: download the OSM layers a tile does not have yet (spec section 8.3).

    Runs on a scheduler of its own because what it produced decides the graph: which OSM data
    the vector stage and the coastline read (``_vectors_osm_input``), and whether a tile can be
    built at all. Tiles whose snapshot the store already holds are not even declared: it is why a
    warm tile sends nothing.

    A tile whose download fails is reported here and fails at its vector stage, which names the
    missing layers.

    ``on_event`` first receives ``Phase("data")``: the OSM rows that will download, and those
    that will not because the tile's data is already there (``reused``).
    """
    outcomes, todo = osm_plan(specs, env)
    reused = _reused_osm_rows(outcomes)
    if todo or reused:
        declared: tuple[tuple[str, NodeKind, str], ...] = tuple(
            (f"{tile.name}/osm", "net", OSM_RULE.name) for tile in todo
        )
        _emit_phase(on_event, Phase("data", nodes=declared, reused=tuple(reused)))
    if not todo:
        return outcomes
    scheduler = make_scheduler(env.store, env, cpu_in_threads=True)
    nodes: dict[TileRef, Node] = {}
    for tile, (road_level, refresh) in todo.items():
        node = Node(
            f"{tile.name}/osm",
            OSM_RULE,
            OsmParams(tile=tile.name, road_level=road_level, refresh=refresh),
            {},
            kind="net",
            lane=OVERPASS_LANE,
            run=_osm_run(env),
        )
        scheduler.add(node)
        nodes[tile] = node
    collector = _Collector(on_event)
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(_run_phase0(scheduler, nodes, collector, handle_sigint=handle_sigint))
    for tile, node in nodes.items():
        road_level = todo[tile][0]
        done = collector.done.get(node.id)
        if done is None:
            failed = collector.failed.get(node.id)
            outcomes[tile] = OsmOutcome(tile, error=failed.error if failed is not None else None)
            log.warning("%s: the OSM download failed", tile.name)
            continue
        outcome = OsmOutcome(
            tile,
            label=snapshot_label_of(done.ref.path),
            artefact=done.ref.path,
            ref=done.ref,
            wall_s=done.wall_s,
            hit=done.hit,
        )
        outcomes[tile] = outcome
    return outcomes


def osm_plan(
    specs: Sequence[BuildSpec], env: BuildEnv
) -> tuple[dict[TileRef, OsmOutcome], dict[TileRef, tuple[int, str]]]:
    """What the OSM data of each tile of a batch will be (spec 8.3), before anything runs.

    Returns the tiles whose data is settled -- a snapshot the store holds, or ``--no-osm-fetch``
    -- and those to download, with the road level and refresh label of their node. The first
    spec of a tile decides, as the vector stage reads one snapshot a tile.
    """
    todo: dict[TileRef, tuple[int, str]] = {}
    outcomes: dict[TileRef, OsmOutcome] = {}
    for spec in specs:
        tile = spec.tile
        if tile in outcomes or tile in todo:
            continue
        if not spec.osm_fetch:
            outcomes[tile] = OsmOutcome(tile, skipped="--no-osm-fetch")
            continue
        cfg = spec.tile_config()
        road_level = int(cfg.get("road_level", 1) or 0)
        # A snapshot this store already holds is used first: it carries the content label (A4)
        # and the coastline the mesh reads. ``--osm-refresh`` changes its key, so it downloads.
        stored = _stored_osm(env, tile, road_level, spec.osm_refresh)
        if stored is not None:
            outcomes[tile] = stored
            continue
        todo[tile] = (road_level, spec.osm_refresh)
    return outcomes, todo


def _reused_osm_rows(outcomes: Mapping[TileRef, OsmOutcome]) -> tuple[tuple[str, str | None], ...]:
    """The ``osm`` rows that will not run because the store holds their snapshot, with its key."""
    return tuple(
        (f"{tile.name}/osm", o.ref.key)
        for tile, o in outcomes.items()
        if o.ref is not None and o.skipped == "stored snapshot reused"
    )


def _osm_outcome(tile: TileRef, node: Node, collector: _Collector) -> OsmOutcome:
    """What the OSM node a build declared for ``tile`` did, as the report's ``osm`` tells it."""
    done = collector.done.get(node.id)
    if done is not None:
        return OsmOutcome(
            tile,
            label=snapshot_label_of(done.ref.path),
            artefact=done.ref.path,
            ref=done.ref,
            wall_s=done.wall_s,
            hit=done.hit,
        )
    failed = collector.failed.get(node.id)
    return OsmOutcome(tile, error=failed.error if failed is not None else None)


def _stored_osm(env: BuildEnv, tile: TileRef, road_level: int, refresh: str) -> OsmOutcome | None:
    """The ``orthostudio.osm`` artefact this store already holds for the tile, if any.

    The rule is a graph root, so its key is a pure function of its params: it can be computed
    without declaring anything and looked up directly.
    """
    params = OsmParams(tile=tile.name, road_level=road_level, refresh=refresh)
    key, _recipe = key_for(OSM_RULE, params, {})
    if not env.store.has(key):
        return None
    info = env.store.info(key)
    if info is None:
        return None
    env.store.touch(key, [])
    ref = ArtifactRef(info.key, info.digest, info.path, info.rule, info.kind, info.size)
    return OsmOutcome(
        tile,
        label=snapshot_label_of(info.path),
        artefact=info.path,
        ref=ref,
        hit=True,
        skipped="stored snapshot reused",
    )


def osm_artefact_refs(outcomes: Mapping[TileRef, OsmOutcome]) -> dict[TileRef, ArtifactRef]:
    """The ``ArtifactRef`` of every tile phase 0 produced a snapshot for."""
    return {tile: o.ref for tile, o in outcomes.items() if o.ref is not None}


class _Collector:
    """Records every node's outcome from the events (and forwards them)."""

    def __init__(self, on_event: Callable[[Event], None] | None) -> None:
        self.on_event = on_event
        self.done: dict[str, Done] = {}
        self.failed: dict[str, Failed] = {}

    def __call__(self, event: Event) -> None:
        if isinstance(event, Done):
            self.done[event.node_id] = event
        elif isinstance(event, Failed):
            self.failed[event.node_id] = event
        if self.on_event is not None:
            self.on_event(event)

    def outcome(self, role: str, node: Node) -> NodeOutcome:
        rule_name = f"{node.rule.name}@{node.rule.version}"
        if node.id in self.done:
            d = self.done[node.id]
            return NodeOutcome(
                node.id, role, rule_name, d.key, "hit" if d.hit else "built", d.wall_s
            )
        if node.id in self.failed:
            f = self.failed[node.id]
            status = "skipped" if f.cause is not None else "failed"
            return NodeOutcome(
                node.id, role, rule_name, None, status, 0.0, f.error.to_dict(), f.cause
            )
        return NodeOutcome(node.id, role, rule_name, None, "pending")


def _resolved(name: str, ref: ArtifactRef | None) -> ResolvedInput:
    if ref is None:
        return ResolvedInput(name, None, None)
    return ResolvedInput(name, ref.digest, ref.path, ref.key)


def _verify_effects(
    nodes: TileNodes, env: BuildEnv, collector: _Collector
) -> tuple[list[str], bool]:
    """Redo the pack / install effects after a hit whose destination was tampered with."""
    repaired: list[str] = []
    spec = nodes.spec
    pack_done = collector.done.get(nodes.pack.id)
    if pack_done is None:
        return repaired, False
    out_root = Path(spec.out_dir).expanduser().resolve()
    pack_dir = out_root / pack_dir_name(spec.tile)
    manifest = PackManifest.from_toml(pack_done.ref.path.read_text(encoding="utf-8"))
    if not pack_is_intact(pack_dir, manifest):
        refs = {
            r: collector.done[n.id].ref for r, n in nodes.by_role.items() if n.id in collector.done
        }
        assemble_pack(
            env.store,
            out_root,
            spec.tile,
            provider=spec.provider,
            zl=spec.zl,
            dsf=_resolved("dsf", refs["dsf"]),
            textures=_resolved("textures", refs.get("textures")),
            overlay=_resolved("overlay", refs.get("overlay")),
            link=spec.link,
            tile_cfg=cast(PackParams, nodes.pack.params).tile_cfg,
        )
        repaired.append("pack")
    installed = False
    if nodes.install is not None and nodes.install.id in collector.done:
        receipt = json.loads(collector.done[nodes.install.id].ref.path.read_text(encoding="utf-8"))
        if not install_is_intact(receipt):
            assert spec.custom_scenery is not None
            install_receipt(
                pack_dir,
                Path(spec.custom_scenery).expanduser(),
                tile=spec.tile,
                link=spec.link,
                library_path=env.library_path,
            )
            repaired.append("install")
        installed = True
    return repaired, installed


def _learn_texture_cost(scheduler: Scheduler, graphs: Sequence[TileNodes], col: _Collector) -> None:
    for g in graphs:
        d = col.done.get(g.textures.id)
        if d is None or d.hit or d.wall_s <= 0:
            continue
        try:
            manifest = json.loads((d.ref.path / "manifest.json").read_text(encoding="utf-8"))
            n = len(manifest.get("textures", []))
        except (OSError, ValueError):
            continue
        if n <= 0:
            continue
        per = d.wall_s / n
        entry = scheduler.costs.entry(PER_TEXTURE_COST)
        if entry is None:
            scheduler.costs.set(PER_TEXTURE_COST, per)
        else:
            scheduler.costs.set(PER_TEXTURE_COST, 0.3 * per + 0.7 * entry.ewma, entry.n + 1)
    scheduler.costs.flush()


def _prefer_own_root_cause(outcomes: list[NodeOutcome], tile: TileRef) -> None:
    """A skipped node blamed on another tile's failure while its own tile also failed a stage
    upstream: report the tile's own root (the scheduler keeps the first upstream that fell,
    whatever its tile), so the user is not sent to the wrong tile."""
    prefix = tile.name + "/"
    own_roots = [o.id for o in outcomes if o.status == "failed" and o.cause is None]
    if not own_roots:
        return
    for o in outcomes:
        if o.cause is not None and not o.cause.startswith(prefix):
            o.cause = own_roots[0]
            if o.error is not None:
                context = {**o.error.get("context", {}), "root": own_roots[0]}
                o.error = {**o.error, "context": context}


def _foreign_cause(cause: str | None, tile: TileRef, failed_tiles: set[TileRef]) -> bool:
    """True when ``cause`` (a node id) belongs to another tile of ``failed_tiles``."""
    if cause is None:
        return False
    owner = cause.split("/", 1)[0]
    return owner != tile.name and any(t.name == owner for t in failed_tiles)


def build_tiles(
    specs: Sequence[BuildSpec],
    *,
    on_event: Callable[[BuildEvent], None] | None = None,
    cpu_workers: int | None = None,
    cpu_in_threads: bool = False,
    env: BuildEnv | None = None,
    handle_sigint: bool = True,
) -> BuildReport:
    """Build (and optionally install) every spec with one scheduler (spec section 4)."""
    t0 = time.perf_counter()
    env = env if env is not None else BuildEnv.create(specs)
    store = env.store
    # Validate the stage options *before* anything is downloaded or written (finding V1).
    choices = stage_choices(specs)
    # The OSM downloads are nodes of the graph, on a lane of their own (spec 8.3): each tile goes
    # on as soon as its own layers are in, where every tile of a batch used to wait for the
    # downloads of all of them (a phase 0 of its own, 4 min for ten tiles on 2026-09-14).
    osm, _todo = osm_plan(specs, env)
    osm_refs = osm_artefact_refs(osm)
    # A tile whose OSM layers cannot be had is reported as failed, not raised: the others of the
    # batch still build (review 2, robustness). One whose graph downloads them fails there.
    no_osm: dict[int, OsxpError] = {}
    for spec in specs:
        if spec.osm_fetch and spec.tile not in osm_refs:
            continue
        try:
            _vectors_osm_input(spec, spec.tile_config(), osm_refs.get(spec.tile))
        except OsxpError as exc:
            no_osm[id(spec)] = exc
    runnable = [s for s in specs if id(s) not in no_osm]
    choices = [c for s, c in zip(specs, choices, strict=True) if id(s) not in no_osm]
    _emit_phase(on_event, Phase("build"))
    scheduler = make_scheduler(store, env, cpu_workers=cpu_workers, cpu_in_threads=cpu_in_threads)
    graphs = declare(
        runnable, scheduler, env, osm_artefacts=osm_refs, choices=choices, fetch_osm=True
    )
    _emit_phase(on_event, Phase("build", nodes=_declared(scheduler), reused=_reused_osm_rows(osm)))
    targets = [g.target.id for g in graphs]
    collector = _Collector(on_event)

    async def main() -> dict[str, ArtifactRef]:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        installed = False

        def on_sigint() -> None:
            if not scheduler.cancelled:
                scheduler.cancel()
            elif task is not None:
                task.cancel()

        previous: Any = None
        if handle_sigint:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError, AttributeError):
                loop.add_signal_handler(signal.SIGINT, on_sigint)
                installed = True
            if not installed:
                # No loop signal handler (Windows): a plain handler that asks the scheduler
                # to stop (thread-safe) instead of a KeyboardInterrupt that would leave the
                # running subprocess stages alive until _stop_pools waits for them.
                with contextlib.suppress(ValueError, OSError, AttributeError):
                    previous = signal.signal(signal.SIGINT, lambda *_: on_sigint())
        try:
            return await scheduler.run(targets, on_event=collector)
        finally:
            if installed:
                loop.remove_signal_handler(signal.SIGINT)
            if previous is not None:
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(signal.SIGINT, previous)

    cancelled = False
    try:
        if graphs:
            asyncio.run(main())
    except asyncio.CancelledError:
        cancelled = True
    cancelled = cancelled or scheduler.cancelled
    _learn_texture_cost(scheduler, graphs, collector)
    for g in graphs:
        if g.osm is not None and g.spec.tile not in osm:
            osm[g.spec.tile] = _osm_outcome(g.spec.tile, g.osm, collector)
    osm_refs = osm_artefact_refs(osm)

    # Second pass: a tile whose masks were skipped only because a *neighbour* of the batch
    # failed its own stage is built again with that neighbour treated as absent (spec 2.1).
    if not cancelled:
        failed_tiles = {
            g.spec.tile
            for g in graphs
            if any(
                n is not None and n.id in collector.failed and collector.failed[n.id].cause is None
                for n in (g.osm, g.vectors, g.mesh)
            )
        }
        retry = [
            g
            for g in graphs
            if g.spec.tile not in failed_tiles
            and g.masks.id in collector.failed
            and _foreign_cause(collector.failed[g.masks.id].cause, g.spec.tile, failed_tiles)
        ]
        if retry:
            log.info("second pass without the failed neighbours %s", sorted(failed_tiles))
            for g in retry:
                for node in g.all():
                    collector.failed.pop(node.id, None)
            scheduler2 = make_scheduler(
                store, env, cpu_workers=cpu_workers, cpu_in_threads=cpu_in_threads
            )
            by_id = {id(g.spec): c for g, c in zip(graphs, choices, strict=False)}
            redone = declare(
                [g.spec for g in retry],
                scheduler2,
                env,
                unavailable=failed_tiles,
                osm_artefacts=osm_refs,
                choices=[by_id[id(g.spec)] for g in retry],
            )
            by_spec = {id(g.spec): new for g, new in zip(retry, redone, strict=True)}
            graphs = [by_spec.get(id(g.spec), g) for g in graphs]
            _emit_phase(on_event, Phase("build", nodes=_declared(scheduler2)))
            try:
                asyncio.run(scheduler2.run([g.target.id for g in redone], on_event=collector))
            except asyncio.CancelledError:
                cancelled = True
            cancelled = cancelled or scheduler2.cancelled
            _learn_texture_cost(scheduler2, redone, collector)

    by_spec_graph = {id(g.spec): g for g in graphs}
    tiles: list[TileOutcome] = []
    built = hits = failed = 0
    for spec in specs:
        if id(spec) in no_osm:
            tiles.append(_unbuilt_tile(spec, no_osm[id(spec)], osm.get(spec.tile)))
            continue
        g = by_spec_graph[id(spec)]
        outcomes = [collector.outcome(role, node) for role, node in g.by_role.items()]
        _prefer_own_root_cause(outcomes, g.spec.tile)
        repaired: list[str] = []
        installed = False
        if g.pack.id in collector.done:
            try:
                repaired, installed = _verify_effects(g, env, collector)
            except OsxpError as exc:
                outcomes.append(
                    NodeOutcome(
                        g.pack.id + "/repair", "repair", "-", None, "failed", 0.0, exc.to_dict()
                    )
                )
        ok = g.target.id in collector.done and not any(o.status == "failed" for o in outcomes)
        out_root = Path(g.spec.out_dir).expanduser().resolve()
        pack_dir = out_root / pack_dir_name(g.spec.tile)
        overlay_path = out_root / OVERLAY_PACK / g.spec.tile.dsf_relpath
        tiles.append(
            TileOutcome(
                tile=g.spec.tile.name,
                provider=g.spec.provider,
                zl=g.spec.zl,
                ok=ok,
                pack_dir=str(pack_dir) if g.pack.id in collector.done else None,
                overlay_dsf=str(overlay_path) if overlay_path.is_file() and g.overlay else None,
                installed=installed,
                nodes=outcomes,
                repaired=repaired,
                stages=g.stages.to_dict(),
                osm=_osm_report(osm.get(g.spec.tile)),
            )
        )
    for d in collector.done.values():
        if d.hit:
            hits += 1
        else:
            built += 1
    failed = len(collector.failed) + len(no_osm)
    return BuildReport(
        tiles=tiles,
        elapsed_s=time.perf_counter() - t0,
        built=built,
        hits=hits,
        failed=failed,
        cancelled=cancelled,
        store_root=str(env.store_root),
        out_dir=str(Path(specs[0].out_dir).expanduser().resolve()),
    )


def _unbuilt_tile(spec: BuildSpec, error: OsxpError, osm: OsmOutcome | None) -> TileOutcome:
    """The outcome of a tile left out of the graph because its OSM layers could not be had."""
    node = NodeOutcome(
        f"{spec.tile.name}/osm", "osm", OSM_RULE.name, None, "failed", 0.0, error.to_dict()
    )
    return TileOutcome(
        tile=spec.tile.name,
        provider=spec.provider,
        zl=spec.zl,
        ok=False,
        pack_dir=None,
        overlay_dsf=None,
        installed=False,
        nodes=[node],
        repaired=[],
        stages=StageChoice(dem=cast(Any, spec.dem)).to_dict(),
        osm=_osm_report(osm),
    )


def _osm_report(outcome: OsmOutcome | None) -> dict[str, Any]:
    if outcome is None:
        return {}
    if outcome.artefact is not None:
        status = "hit" if outcome.hit else "built"
    elif outcome.error is not None:
        status = "failed"  # review 4: a failure used to be reported as a skip
    else:
        status = "skipped"
    out: dict[str, Any] = {
        "label": outcome.label,
        "wall_s": round(outcome.wall_s, 3),
        "status": status,
    }
    if outcome.skipped:
        out["skipped"] = outcome.skipped
    if outcome.error is not None:
        out["error"] = outcome.error.to_dict()
    return out


def build_tile(spec: BuildSpec, **kw: Any) -> BuildReport:
    """``build_tiles([spec])``."""
    return build_tiles([spec], **kw)
