"""From an API request to ``BuildSpec`` objects, and from an ``Estimate`` to the plan answer.

Spec: ``docs/specs/api.md`` sections 2.2 and 4. Mirrors ``orthostudio.cli._build_specs`` with the
values of ``Settings`` underneath the request (the request wins), validated paths and the
airport index for ``{icao, radius_km}`` areas. The zones of the request, or else the zones of
the saved document touching the requested tiles, are clipped into each tile's ``zone_list``
(``docs/specs/map-zones.md`` 5).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orthostudio.api.models import AirportArea, PlanRequest
from orthostudio.errors import OsxpError
from orthostudio.estimate import Estimate
from orthostudio.imagery.providers import Provider, load_registry
from orthostudio.install import custom_scenery_dir, detect_xplane, is_xplane_dir
from orthostudio.model import TileRef
from orthostudio.pipeline.build import (
    OVERLAY_SETTINGS,
    BuildSpec,
    parse_overlay_setting,
    resolve_global_scenery,
)
from orthostudio.pipeline.home import (
    default_chunks_root,
    default_store_root,
    default_tiles_root,
    require_data_root,
)
from orthostudio.tilefiles import TILE_PARAMETERS, parse_tile_cfg
from orthostudio.zones import (
    Zone,
    ZoneEntry,
    default_zones_path,
    read_saved_zones,
    with_zone_list,
    zones_for_tile,
)

__all__ = [
    "NETWORK_HIGH_FACTOR",
    "check_ortho4xp_folder",
    "check_xplane_dir",
    "make_specs",
    "plan_answer",
    "request_zones",
    "resolve_xplane",
    "typed_overrides",
    "xplane_not_found",
]

NETWORK_HIGH_FACTOR = 1.5
"""``seconds_high`` = ``seconds_low`` x 1.5 (a warm line versus a throttled one)."""


def check_xplane_dir(value: str | Path) -> Path:
    """An X-Plane 12 folder (``Resources/`` and ``Custom Scenery/``), else ``XP_DIR_NOT_FOUND``
    saying which: ``why`` is ``missing`` (no such folder) or ``not_xplane``, with the subfolders
    it lacks in ``lacks`` (a user on a Windows virtual machine without X-Plane wondered how
    X-Plane is recognised, 2026-09-15)."""
    p = Path(str(value)).expanduser()
    with contextlib.suppress(OSError):
        p = p.resolve()
    if is_xplane_dir(p):
        return p
    if not p.is_dir():
        raise OsxpError(
            "XP_DIR_NOT_FOUND",
            context={"path": str(p), "why": "missing"},
            message=f"The folder {p} was not found.",
            remedy="Choose the folder X-Plane 12 is installed in: it holds Resources and "
            "Custom Scenery.",
        )
    lacks = " and ".join(n for n in ("Resources", "Custom Scenery") if not (p / n).is_dir())
    raise OsxpError(
        "XP_DIR_NOT_FOUND",
        context={"path": str(p), "why": "not_xplane", "lacks": lacks},
        message=f"{p} is not an X-Plane 12 folder: it holds no {lacks}.",
        remedy="Choose the folder X-Plane 12 is installed in: it holds Resources and Custom "
        "Scenery.",
    )


def check_ortho4xp_folder(value: str | Path) -> Path:
    """An Ortho4XP folder to import tiles from (``Ortho4XP.py`` at its root)."""
    p = Path(str(value)).expanduser()
    with contextlib.suppress(OSError):
        p = p.resolve()
    if not (p / "Ortho4XP.py").is_file():
        raise OsxpError(
            "SYS_WORKING_DIR_INVALID",
            context={"path": str(p)},
            remedy=(
                "Point at the folder holding Ortho4XP.py. A tile OrthoStudio XP built needs no "
                "import: it is in the Library already, where Add to X-Plane puts it in X-Plane."
            ),
        )
    return p


def xplane_not_found(saved: str | None) -> OsxpError:
    """``XP_DIR_NOT_FOUND`` when no X-Plane 12 folder is known: the one saved in Settings is not
    one any more (a drive unplugged), or none was found on this computer (X-Plane installed
    elsewhere, or not at all: a virtual machine whose X-Plane is on the host)."""
    if saved:
        where = f"The X-Plane 12 folder saved in Settings, {saved}, was not found."
    else:
        where = "X-Plane 12 was not found on this computer."
    return OsxpError(
        "XP_DIR_NOT_FOUND",
        context={"path": saved or "<not detected>"},
        message=f"{where} OrthoStudio XP takes the relief, roads, forests and buildings from it, "
        "and adds the tiles to it.",
        remedy="Choose the X-Plane 12 folder in Settings.",
    )


def resolve_xplane(explicit: str | None, settings_dir: str | None) -> Path | None:
    """The request's directory, else the settings', else detection; validated when given."""
    if explicit:
        return check_xplane_dir(explicit)
    if settings_dir:
        p = Path(settings_dir).expanduser()
        return p if is_xplane_dir(p) else None
    return detect_xplane()


def typed_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Request overrides typed like ``--set`` (strings parsed as a cfg line; JSON kept)."""
    out: dict[str, Any] = {}
    for name, value in raw.items():
        if name in OVERLAY_SETTINGS:
            out[name] = parse_overlay_setting(name, str(value)) if isinstance(value, str) else value
            continue
        if name not in TILE_PARAMETERS:
            raise OsxpError(
                "CFG_VALUE_INVALID",
                context={"name": name, "value": value, "type": "-", "range": "-"},
                message=f"{name!r} is not an Ortho4XP tile parameter.",
                remedy="Tile parameters: " + ", ".join(TILE_PARAMETERS),
            )
        if isinstance(value, str):
            out.update(parse_tile_cfg(f"{name}={value}\n", strict=True))
        else:
            out[name] = value
    return out


def _tiles_of(area: AirportArea, airports: Any) -> list[str]:
    if airports is None:
        raise OsxpError(
            "SYS_RESOURCE_MISSING",
            context={"path": "airports index"},
            message="The airport index is not available on this machine.",
            remedy="Set the X-Plane folder in Settings (apt.dat is needed), or give tiles.",
        )
    try:
        refs = airports.tiles_around(area.icao, area.radius_km)
    except KeyError:
        refs = []
    if not refs:
        raise OsxpError(
            "CFG_LATLON_INVALID",
            context={"value": area.icao},
            message=f"Airport {area.icao} is not in the index.",
            remedy="Check the ICAO code (search with /api/airports?q=).",
        )
    return [TileRef(int(r.lat), int(r.lon)).name for r in refs]


def request_zones(
    req: PlanRequest,
    tiles: Sequence[str],
    *,
    zones_path: Path | None = None,
    registry: dict[str, Provider] | None = None,
) -> list[Zone]:
    """The zones a request builds ``tiles`` with: ``req.zones`` when given (possibly empty), else
    the zones of the saved document (``zones_path``, default ``$OSXP_HOME/zones.json``; none when
    it does not exist) that touch at least one of ``tiles``.

    The saved document is read zone by zone (``read_saved_zones``): a zone with a problem that
    touches one of ``tiles`` refuses the request with its error (``ZONE_INVALID`` naming it),
    a problem elsewhere does not; a file no zone can be read from refuses every request.
    """
    if req.zones is not None:
        return list(req.zones)
    path = zones_path if zones_path is not None else default_zones_path()
    saved = read_saved_zones(path, registry=registry)
    return saved.zones_for(TileRef.parse(name) for name in tiles)


def make_specs(
    req: PlanRequest,
    *,
    settings: Any,
    install: bool = False,
    airports: Any = None,
    registry: dict[str, Provider] | None = None,
    config_module: Any = None,
    zones_path: Path | None = None,
) -> list[BuildSpec]:
    """The specs of a plan or a job (spec 2.2); raises ``OsxpError`` on any bad input.

    Every tile a zone touches gets ``config["zone_list"]``, the zones clipped to it; a tile
    no zone touches keeps exactly the config it had without zones (same keys). The data folder
    must be there (``CFG_DATA_DIR_MISSING``): nothing is downloaded while its disk is unplugged.
    """
    require_data_root()
    tiles, area = req.area()
    if area is not None:
        tiles = _tiles_of(area, airports)
    assert tiles is not None
    essential = settings.essential
    provider = req.provider or essential.provider
    zl = req.zoom_level if req.zoom_level is not None else int(essential.zoom_level)
    reg = registry if registry is not None else load_registry()
    if provider not in reg:
        known = ", ".join(sorted(reg))
        raise OsxpError(
            "CFG_PROVIDER_UNKNOWN",
            context={"provider": provider, "known": known},
            message=f"Provider {provider!r} is not in the registry ({known}).",
        )
    # A source of one country does not cover a tile elsewhere (a user found them mixed in the list)
    source = reg[provider]
    refs = [TileRef.parse(name) for name in tiles]
    uncovered = [ref.name for ref in refs if not source.covers(ref.lat, ref.lon)]
    if uncovered:
        raise OsxpError(
            "CFG_PROVIDER_OUT_OF_COVERAGE",
            context={"provider": provider, "extent": source.extent, "tiles": " ".join(uncovered)},
        )
    # hand-made mesh patches, as Ortho4XP holds them (a user of the page asked, 2026-09-17)
    patches = str(getattr(settings.expert, "patches_dir", "") or "").strip()
    patches_dir = Path(patches).expanduser() if patches else None
    max_zl = reg[provider].max_zl
    if zl > max_zl:
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={"name": "zoom_level", "value": zl, "type": "int", "range": f"10..{max_zl}"},
        )
    config: dict[str, Any] = {}
    if config_module is not None:
        config.update(config_module.to_build_overrides(settings))
    config.update(typed_overrides(req.overrides))
    config.pop("default_website", None)
    config.pop("default_zl", None)
    zones = request_zones(req, tiles, zones_path=zones_path, registry=reg)
    mesh_zl = config.get("mesh_zl", TILE_PARAMETERS["mesh_zl"].default)
    zone_lists: dict[str, list[ZoneEntry]] = {
        name: zones_for_tile(
            zones,
            TileRef.parse(name),
            provider,
            registry=reg,
            mesh_zl=mesh_zl if isinstance(mesh_zl, int) else None,
        )
        if zones
        else []
        for name in tiles
    }
    xp = resolve_xplane(req.xplane_dir, essential.xplane_dir)
    if install and xp is None:
        raise xplane_not_found(essential.xplane_dir)
    gs = resolve_global_scenery(xplane=xp)
    # "none": the overlays come from another pack (simHeaven X-World): OrthoStudio XP builds none
    overlay = bool(req.overlay) and getattr(essential, "overlays", "xplane") != "none"
    if gs is None and (overlay or req.xp12_rasters):
        if xp is None:
            raise xplane_not_found(essential.xplane_dir)
        raise OsxpError(
            "XP_GLOBAL_SCENERY_NOT_FOUND",
            context={"path": str(xp)},
            remedy="Install X-Plane 12's scenery with the X-Plane installer, or choose another "
            "X-Plane 12 folder in Settings.",
        )
    out_dir = default_tiles_root()
    specs: list[BuildSpec] = []
    for name in tiles:
        tile = TileRef.parse(name)
        specs.append(
            BuildSpec(
                tile=tile,
                provider=provider,
                zl=zl,
                out_dir=out_dir,
                global_scenery_dir=gs,
                config=with_zone_list(config, zone_lists[name], tile),
                install=install,
                custom_scenery=custom_scenery_dir(xp) if xp is not None else None,
                overlay=overlay,
                xp12_rasters=req.xp12_rasters,
                store_root=default_store_root(),
                chunks_root=default_chunks_root(),
                patches_dir=patches_dir,
            )
        )
    return specs


def plan_answer(est: Estimate, specs: Sequence[BuildSpec]) -> dict[str, Any]:
    """The two-line answer of spec section 4 around ``Estimate.to_dict()``."""
    doc = est.to_dict()
    requests = sum(t.requests for t in est.tiles)
    mb = sum(t.download_mb for t in est.tiles)
    textures = sum(t.textures_total for t in est.tiles)
    cached = sum(t.hits for t in est.tiles)
    probe = est.probe
    mbps = None if probe is None or probe.bytes == 0 else round(probe.mb_per_s * 8, 2)
    warnings: list[str] = []
    if not est.disk_ok:
        warnings.append("SYS_DISK_FULL")
    return {
        "network": {
            "requests": requests,
            "mb": round(mb, 1),
            "seconds_low": round(est.network_s, 1),
            "seconds_high": round(est.network_s * NETWORK_HIGH_FACTOR, 1),
            "mbps_measured": mbps,
            "req_per_s": round(est.req_per_s, 1),
            "probed": probe is not None,
        },
        "compute": {
            "seconds": round(est.compute_s, 1),
            "textures": textures,
            "cached": cached,
            "workers": est.workers,
        },
        "disk": {
            "free_gb": round(est.disk_free_gb, 1),
            "dds_gb": round(sum(t.dds_gb for t in est.tiles), 3),
            "needed_gb": round(est.disk_needed_gb, 2),
            "ok": est.disk_ok,
        },
        "tiles": doc["tiles"],
        "warnings": warnings,
        "specs": [{"tile": s.tile.name, "provider": s.provider, "zl": s.zl} for s in specs],
        "estimate": doc,
    }
