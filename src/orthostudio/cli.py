"""Command-line entry point: ``build``, ``plan``, ``install``, ``uninstall``, ``library``,
``import-ortho4xp``, ``clean``, ``why``, ``serve``, ``doctor`` and ``version``
(spec ``docs/specs/pipeline-build.md``).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from orthostudio import __version__
from orthostudio.errors import OsxpError

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="OrthoStudio XP: ortho scenery for X-Plane 12"
)

_TILE_RE = re.compile(r"^([+-]\d{2})([+-]\d{3})$")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISSING = 2
EXIT_INTERRUPTED = 130


@app.callback()
def _root() -> None:
    """OrthoStudio XP command group."""
    from orthostudio.fsutil import raise_open_files_limit

    # every command: the app's engine (`serve`) as much as `osxp build` in a terminal
    raise_open_files_limit()


@app.command()
def version() -> None:
    """Print the OrthoStudio XP version."""
    typer.echo(f"OrthoStudio XP {__version__}")


def parse_tile(tile: str) -> tuple[int, int]:
    """``+43+005`` -> ``(43, 5)``."""
    m = _TILE_RE.match(tile.strip())
    if m is None:
        raise OsxpError(
            "CFG_LATLON_INVALID",
            context={"value": tile},
            message=f"Tile {tile!r} is not of the form +43+005.",
            remedy="Give the south-west corner as sLLsLLL, e.g. +43+005 or -34-059.",
        )
    return int(m.group(1)), int(m.group(2))


def _err(exc: OsxpError) -> None:
    typer.echo(f"error {exc.code}: {exc.message}", err=True)
    if exc.remedy:
        typer.echo(f"  remedy: {exc.remedy}", err=True)


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="machine-readable output")] = False,
    online: Annotated[
        bool,
        typer.Option(
            "--online/--offline",
            help="probe one Bing tile (off by default: a local diagnostic sends nothing)",
        ),
    ] = False,
    xplane: Annotated[Path | None, typer.Option("--xplane", help="X-Plane directory")] = None,
    store: Annotated[Path | None, typer.Option("--store", help="artefact store root")] = None,
    chunks: Annotated[Path | None, typer.Option("--chunks", help="raw tile store root")] = None,
) -> None:
    """Check the machine: Python, encoder, HTTP/2 client, disk, X-Plane, Triangle4XP, Bing."""
    from orthostudio.doctor import render_text, run_doctor

    report = run_doctor(
        offline=not online,
        xplane=xplane,
        store_root=store,
        chunks_root=chunks,
    )
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=1, sort_keys=True))
    else:
        typer.echo(render_text(report))
    if not report.ok:
        raise typer.Exit(EXIT_ERROR)


# -- P2a: build, plan, install, import-ortho4xp, library, why ------------------------------


def _parse_sets(values: list[str]) -> dict[str, Any]:
    """``name=value`` overrides typed like an Ortho4XP tile cfg line (no eval).

    The 44 tile variables, plus the overlay settings (``ovl_exclude_pol=[0, '.for']``,
    ``ovl_exclude_net=[22001]``, ``keep_objects=False``: Ortho4XP application variables).
    """
    from orthostudio.pipeline.build import OVERLAY_SETTINGS, parse_overlay_setting
    from orthostudio.tilefiles import OSXP_PARAMETERS, TILE_PARAMETERS, parse_tile_cfg

    out: dict[str, Any] = {}
    for item in values:
        name, sep, value = item.partition("=")
        name = name.strip()
        if sep and name in OVERLAY_SETTINGS:
            out[name] = parse_overlay_setting(name, value)
            continue
        if not sep or (name not in TILE_PARAMETERS and name not in OSXP_PARAMETERS):
            raise OsxpError(
                "CFG_VALUE_INVALID",
                context={"name": name or item, "value": value, "type": "-", "range": "-"},
                message=f"--set {item!r}: expected <tile parameter>=<value>.",
                remedy="Tile parameters: "
                + ", ".join([*TILE_PARAMETERS, *OSXP_PARAMETERS])
                + "; overlay settings: "
                + ", ".join(OVERLAY_SETTINGS),
            )
        out.update(parse_tile_cfg(f"{name}={value}\n", strict=True))
    return out


def _resolve_xplane(xplane: Path | None) -> Path | None:
    from orthostudio.install import detect_xplane, is_xplane_dir

    if xplane is not None:
        p = Path(xplane).expanduser()
        if not is_xplane_dir(p) and not (p / "Custom Scenery").is_dir():
            raise OsxpError("XP_DIR_NOT_FOUND", context={"path": str(p)})
        return p
    return detect_xplane()


def _build_specs(
    *,
    tiles: list[str],
    provider: str,
    zl: int,
    out: Path | None,
    install: bool,
    xplane: Path | None,
    global_scenery: Path | None,
    store: Path | None,
    chunks: Path | None,
    workers: int | None,
    sets: list[str],
    overlay: bool,
    xp12_rasters: bool,
    creation_agent: str,
    link: bool,
    encoder: str,
    patches: Path | None = None,
    dem: str = "vectors",
    osm_fetch: bool = True,
    osm_refresh: str = "",
    relief: str | None = None,
    zones: Path | None = None,
) -> list:
    from orthostudio.install import custom_scenery_dir
    from orthostudio.model import TileRef
    from orthostudio.pipeline.build import BuildSpec, resolve_global_scenery
    from orthostudio.pipeline.home import (
        default_chunks_root,
        default_patches_dir,
        default_store_root,
        default_tiles_root,
        require_data_root,
    )

    if not tiles:
        raise OsxpError(
            "CFG_LATLON_INVALID",
            context={"value": ""},
            message="No tile given.",
            remedy="Give at least one --tile +43+005.",
        )
    # The names first: a typo is reported as such, not as an X-Plane that was not found.
    tile_refs = [TileRef(*parse_tile(tile)) for tile in tiles]
    # the relief, the map data and the work folder go there whatever --out, --store and --chunks say
    require_data_root()
    config = _parse_sets(sets)
    zone_lists = _zone_lists(zones, tiles, provider, config) if zones is not None else {}
    xp = _resolve_xplane(xplane) if (install or xplane is not None) else None
    if install and xp is None:
        raise OsxpError(
            "XP_DIR_NOT_FOUND",
            context={"path": "<not detected>"},
            message="--install needs X-Plane 12, which was not detected.",
            remedy="Give --xplane <X-Plane 12 folder> (osxp doctor shows what was tried).",
        )
    gs: Path | None
    if global_scenery is not None:
        from orthostudio.overlays.source import resolve_global_scenery_dir

        gs = resolve_global_scenery_dir(Path(global_scenery).expanduser())
        if not (gs / "Earth nav data").is_dir():
            raise OsxpError("XP_GLOBAL_SCENERY_NOT_FOUND", context={"path": str(global_scenery)})
    else:
        gs = resolve_global_scenery(xplane=xp)
    if gs is None and (overlay or xp12_rasters):
        remedy = (
            "Give --xplane <X-Plane 12 folder> or --global-scenery <X-Plane 12 Global Scenery>; "
            "--no-overlay --no-xp12-rasters --relief view builds without it."
        )
        if xp is None:
            raise OsxpError(
                "XP_DIR_NOT_FOUND",
                context={"path": "<not detected>"},
                message="X-Plane 12 was not found on this computer: the overlays, the sea-level "
                "and depth rasters and the relief come from its Global Scenery.",
                remedy=remedy,
            )
        raise OsxpError("XP_GLOBAL_SCENERY_NOT_FOUND", context={"path": str(xp)}, remedy=remedy)
    out_dir = Path(out).expanduser() if out is not None else default_tiles_root()
    # The page offers three reliefs and the command line only had two: --relief copernicus was
    # refused while the page built with it (2026-09-17). Copernicus is a DEM source, so it goes
    # where Ortho4XP puts one (``config.overrides._custom_dem``); --set custom_dem wins.
    choice = relief.strip().lower() if relief is not None else None
    if choice in ("copernicus", "cop30"):
        if not str(config.get("custom_dem", "") or "").strip():
            config = {**config, "custom_dem": "COP30"}
        choice = None
    specs = []
    for tile, ref in zip(tiles, tile_refs, strict=True):
        spec = BuildSpec(
            tile=ref,
            provider=provider,
            zl=zl,
            out_dir=out_dir,
            global_scenery_dir=gs,
            config=_with_zones(config, zone_lists.get(tile), ref),
            install=install,
            custom_scenery=custom_scenery_dir(xp) if xp is not None else None,
            overlay=overlay,
            xp12_rasters=xp12_rasters,
            creation_agent=creation_agent,
            patches_dir=patches if patches is not None else default_patches_dir(),
            store_root=store or default_store_root(),
            chunks_root=chunks or default_chunks_root(),
            workers=workers,
            encoder=encoder,
            link=link,
            dem=dem,
            osm_fetch=osm_fetch,
            osm_refresh=osm_refresh,
        )
        if choice is not None:
            spec.relief = choice
        specs.append(spec)
    return specs


def _zone_lists(
    path: Path, tiles: list[str], provider: str, config: dict[str, Any]
) -> dict[str, list]:
    """``--zones FILE``: the ``zone_list`` of every tile (``docs/specs/map-zones.md`` 5)."""
    from orthostudio.model import TileRef
    from orthostudio.tilefiles import TILE_PARAMETERS
    from orthostudio.zones import read_zones_file, zones_for_tile

    loaded = read_zones_file(path)
    if loaded.holes_dropped:
        typer.echo(
            f"note: {loaded.holes_dropped} hole(s) of the GeoJSON polygons of {path} dropped: "
            "a zone fills its exterior ring (as Ortho4XP does)",
            err=True,
        )
    mesh_zl = config.get("mesh_zl", TILE_PARAMETERS["mesh_zl"].default)
    out: dict[str, list] = {}
    for tile in tiles:
        lat, lon = parse_tile(tile)
        out[tile] = zones_for_tile(
            loaded.document.zones,
            TileRef(lat, lon),
            provider,
            mesh_zl=mesh_zl if isinstance(mesh_zl, int) else None,
        )
    touched = sum(1 for entries in out.values() if entries)
    typer.echo(
        f"zones: {len(loaded.document.zones)} zone(s) from {path}, "
        f"{touched} of {len(tiles)} tile(s) touched",
        err=True,
    )
    return out


def _with_zones(config: dict[str, Any], entries: list | None, tile: Any) -> dict[str, Any]:
    if not entries:
        return dict(config)
    from orthostudio.zones import with_zone_list

    return with_zone_list(config, entries, tile)


class _BuildReporter:
    """Stderr rendering of the scheduler events: one line per node, live stats on a TTY."""

    def __init__(self, quiet: bool = False, stream: Any = None) -> None:
        self.quiet = quiet
        self.stream = stream if stream is not None else sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.t0 = time.perf_counter()
        self.last_progress: dict[str, float] = {}
        self.last_stats = 0.0
        self.width = 0

    def _line(self, text: str) -> None:
        if self.quiet:
            return
        pad = " " * max(0, self.width - len(text)) if self.tty else ""
        self.stream.write(("\r" if self.tty and self.width else "") + text + pad + "\n")
        self.width = 0
        self.stream.flush()

    def __call__(self, event: Any) -> None:
        from orthostudio.sched import Done, Failed, Progress, Stats

        t = time.perf_counter() - self.t0
        if isinstance(event, Done):
            how = "hit" if event.hit else f"built {event.wall_s:.1f} s"
            self._line(f"[{t:6.1f} s] {event.node_id:<34} {how}")
        elif isinstance(event, Failed):
            if event.cause is not None:
                self._line(f"[{t:6.1f} s] {event.node_id:<34} skipped ({event.cause} failed)")
            else:
                self._line(
                    f"[{t:6.1f} s] {event.node_id:<34} FAILED {event.error.code}: "
                    f"{event.error.message}"
                )
                if event.error.remedy:
                    self._line(f"           remedy: {event.error.remedy}")
        elif isinstance(event, Progress):
            last = self.last_progress.get(event.node_id, -10.0)
            period = 1.0 if self.tty else 10.0
            if t - last >= period:
                self.last_progress[event.node_id] = t
                text = (
                    f"[{t:6.1f} s] {event.node_id:<34} {event.fraction * 100:5.1f}% {event.message}"
                )
                if self.tty and not self.quiet:
                    pad = " " * max(0, self.width - len(text))
                    self.stream.write("\r" + text + pad)
                    self.width = len(text)
                    self.stream.flush()
                else:
                    self._line(text)
        elif (
            isinstance(event, Stats) and self.tty and not self.quiet and t - self.last_stats >= 1.0
        ):
            self.last_stats = t
            text = (
                f"[{t:6.1f} s] running {event.running}, pending {event.pending}, done "
                f"{event.done} ({event.hits} hits), failed {event.failed}, ETA {event.eta_s:.0f} s"
            )
            pad = " " * max(0, self.width - len(text))
            self.stream.write("\r" + text + pad)
            self.width = len(text)
            self.stream.flush()

    def finish(self) -> None:
        if self.tty and self.width and not self.quiet:
            self.stream.write("\n")
            self.width = 0
            self.stream.flush()


_TILE_OPT = typer.Option("--tile", help="tile(s), e.g. +43+005 (repeatable)")
_PROVIDER_OPT = typer.Option("--provider", help="imagery provider code")
_ZL_OPT = typer.Option("--zl", help="zoom level of the textures")
_OUT_OPT = typer.Option(
    "--out", help="output directory of the packs (default: the tiles folder of the data folder)"
)
_XPLANE_OPT = typer.Option("--xplane", help="X-Plane 12 folder (detected by default)")
_GS_OPT = typer.Option("--global-scenery", help="X-Plane 12 Global Scenery (DEMS, overlays)")
_STORE_OPT = typer.Option("--store", help="artefact store root")
_CHUNKS_OPT = typer.Option("--chunks", help="raw tile store root")
_WORKERS_OPT = typer.Option("--workers", help="CPU workers (default cores - 2)")
_SET_OPT = typer.Option("--set", help="tile parameter override, name=value (repeatable)")
_OVERLAY_OPT = typer.Option("--overlay/--no-overlay", help="build yOrthoStudio_Overlays")
_XP12_OPT = typer.Option(
    "--xp12-rasters/--no-xp12-rasters", help="copy DEMS/DEMN from the Global Scenery"
)
_AGENT_OPT = typer.Option("--creation-agent", help="sim/creation_agent of the DSF")
_LINK_OPT = typer.Option("--link/--copy", help="hard links (or copies) in the pack")
_PATCHES_OPT = typer.Option(
    "--patches",
    help=(
        "folder of hand-made mesh patches (<tile>/*.patch.osm); "
        "default: the patches folder of $OSXP_HOME"
    ),
)
_ENCODER_OPT = typer.Option("--encoder", help="DDS encoder (auto, ispc, nvcompress)")
_JSON_FLAG = typer.Option("--json", help="machine-readable output on stdout")
_ZONES_OPT = typer.Option(
    "--zones",
    help="zones file: an osxp-zones-1 document (the page's zones.json) or a GeoJSON "
    "FeatureCollection of Polygon/MultiPolygon features whose properties give zl (provider, "
    "name, id optional); each zone is clipped into the zone_list of the tiles it covers, the "
    "first zone winning where they overlap",
)
_DEM_OPT = typer.Option(
    "--dem",
    help="elevation the mesh reads: vectors (the vector stage's own raster, smoothed over the "
    "airports) or native (the raw raster of orthostudio.dem)",
)
_OSM_OPT = typer.Option(
    "--osm-fetch/--no-osm-fetch",
    help="download the OSM layers the tile does not have yet",
)
_OSM_REFRESH_OPT = typer.Option(
    "--osm-refresh", help="label forcing a fresh OSM download (any string; it enters the key)"
)
_RELIEF_OPT = typer.Option(
    "--relief",
    help="relief of the elevation stage: xplane (X-Plane 12's own, read from its Global "
    "Scenery; the default), copernicus (downloaded and kept) or view (viewfinderpanoramas, "
    "for comparisons)",
)


@app.command()
def build(
    tiles: Annotated[list[str], _TILE_OPT],
    provider: Annotated[str, _PROVIDER_OPT] = "BI",
    zl: Annotated[int, _ZL_OPT] = 16,
    out: Annotated[Path | None, _OUT_OPT] = None,
    install: Annotated[bool, typer.Option("--install", help="link into Custom Scenery")] = False,
    xplane: Annotated[Path | None, _XPLANE_OPT] = None,
    global_scenery: Annotated[Path | None, _GS_OPT] = None,
    store: Annotated[Path | None, _STORE_OPT] = None,
    chunks: Annotated[Path | None, _CHUNKS_OPT] = None,
    workers: Annotated[int | None, _WORKERS_OPT] = None,
    sets: Annotated[list[str] | None, _SET_OPT] = None,
    overlay: Annotated[bool, _OVERLAY_OPT] = True,
    xp12_rasters: Annotated[bool, _XP12_OPT] = True,
    creation_agent: Annotated[str, _AGENT_OPT] = "osxp",
    patches: Annotated[Path | None, _PATCHES_OPT] = None,
    link: Annotated[bool, _LINK_OPT] = True,
    encoder: Annotated[str, _ENCODER_OPT] = "auto",
    dem: Annotated[str, _DEM_OPT] = "vectors",
    osm_fetch: Annotated[bool, _OSM_OPT] = True,
    osm_refresh: Annotated[str, _OSM_REFRESH_OPT] = "",
    relief: Annotated[str | None, _RELIEF_OPT] = None,
    zones: Annotated[Path | None, _ZONES_OPT] = None,
    json_output: Annotated[bool, _JSON_FLAG] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="estimate only (= osxp plan)")] = False,
    online: Annotated[
        bool, typer.Option("--online/--offline", help="--dry-run: probe the provider (20 requests)")
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="no progress on stderr")] = False,
) -> None:
    """Build one or more tiles end to end (data, mesh, masks, DSF, textures, overlay, pack,
    install)."""
    from orthostudio.pipeline.build import build_tiles

    try:
        specs = _build_specs(
            tiles=tiles, provider=provider, zl=zl, out=out, install=install, xplane=xplane,
            global_scenery=global_scenery, store=store, chunks=chunks,
            workers=workers, sets=sets or [], overlay=overlay,
            xp12_rasters=xp12_rasters, creation_agent=creation_agent, link=link,
            encoder=encoder, patches=patches, dem=dem, osm_fetch=osm_fetch,
            osm_refresh=osm_refresh, relief=relief, zones=zones,
        )  # fmt: skip
        if dry_run:
            _plan(specs, online=online, json_output=json_output)
            return
        reporter = _BuildReporter(quiet=quiet)
        report = build_tiles(specs, on_event=reporter)
        reporter.finish()
    except OsxpError as exc:
        _err(exc)
        raise typer.Exit(EXIT_ERROR) from None
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=1))
    else:
        for t in report.tiles:
            status = "ok" if t.ok else "FAILED"
            where = t.pack_dir or "-"
            extra = " (installed)" if t.installed else ""
            recipe = f"dem {t.stages.get('dem', 'vectors')}"
            typer.echo(f"{t.tile} {t.provider}{t.zl} [{recipe}]: {status} {where}{extra}")
            err = t.error
            if err is not None and err.error is not None:
                typer.echo(f"  {err.error['code']}: {err.error['message']}", err=True)
                if err.error.get("remedy"):
                    typer.echo(f"  remedy: {err.error['remedy']}", err=True)
        typer.echo(
            f"{report.built} built, {report.hits} from the store, {report.failed} failed in "
            f"{report.elapsed_s:.1f} s; store {report.store_root}"
        )
    if report.cancelled:
        raise typer.Exit(EXIT_INTERRUPTED)
    if not report.ok:
        codes = {
            n.error["code"]
            for t in report.tiles
            for n in t.nodes
            if n.error is not None and n.cause is None
        }
        raise typer.Exit(EXIT_MISSING if codes <= {"TEX_MISSING"} else EXIT_ERROR)


def _plan(specs: list, *, online: bool, json_output: bool) -> None:
    from orthostudio.estimate import estimate, render_text

    est = estimate(specs, online=online)
    if json_output:
        typer.echo(json.dumps(est.to_dict(), indent=1))
    else:
        typer.echo(render_text(est))


@app.command()
def plan(
    tiles: Annotated[list[str], _TILE_OPT],
    provider: Annotated[str, _PROVIDER_OPT] = "BI",
    zl: Annotated[int, _ZL_OPT] = 16,
    out: Annotated[Path | None, _OUT_OPT] = None,
    xplane: Annotated[Path | None, _XPLANE_OPT] = None,
    global_scenery: Annotated[Path | None, _GS_OPT] = None,
    store: Annotated[Path | None, _STORE_OPT] = None,
    chunks: Annotated[Path | None, _CHUNKS_OPT] = None,
    workers: Annotated[int | None, _WORKERS_OPT] = None,
    sets: Annotated[list[str] | None, _SET_OPT] = None,
    overlay: Annotated[bool, _OVERLAY_OPT] = True,
    xp12_rasters: Annotated[bool, _XP12_OPT] = True,
    creation_agent: Annotated[str, _AGENT_OPT] = "osxp",
    encoder: Annotated[str, _ENCODER_OPT] = "auto",
    online: Annotated[
        bool, typer.Option("--online/--offline", help="probe the provider with 20 requests")
    ] = False,
    zones: Annotated[Path | None, _ZONES_OPT] = None,
    json_output: Annotated[bool, _JSON_FLAG] = False,
) -> None:
    """Estimate a build: textures, requests, MB, GB of DDS, network and compute time, disk."""
    try:
        specs = _build_specs(
            tiles=tiles, provider=provider, zl=zl, out=out, install=False, xplane=xplane,
            global_scenery=global_scenery, store=store, chunks=chunks,
            workers=workers, sets=sets or [], overlay=overlay,
            xp12_rasters=xp12_rasters, creation_agent=creation_agent, link=True,
            encoder=encoder, zones=zones,
        )  # fmt: skip
        _plan(specs, online=online, json_output=json_output)
    except OsxpError as exc:
        _err(exc)
        raise typer.Exit(EXIT_ERROR) from None


@app.command()
def install(
    pack: Annotated[
        Path,
        typer.Argument(
            help="a tile pack: zOrthoStudio_<tile>, or zOrtho4XP_<tile> built by Ortho4XP"
        ),
    ],
    xplane: Annotated[Path | None, _XPLANE_OPT] = None,
    link: Annotated[bool, typer.Option("--link/--copy", help="symlink (or copy) the pack")] = True,
    library: Annotated[Path | None, typer.Option("--library", help="library sqlite file")] = None,
    json_output: Annotated[bool, _JSON_FLAG] = False,
) -> None:
    """Link a pack into Custom Scenery, order scenery_packs.ini, record it in the library."""
    from orthostudio.install import Library, custom_scenery_dir, install_pack, pack_tile
    from orthostudio.pipeline.pack import MANIFEST_NAME, install_receipt

    try:
        xp = _resolve_xplane(xplane)
        if xp is None:
            raise OsxpError(
                "XP_DIR_NOT_FOUND",
                context={"path": "<not detected>"},
                remedy="Give --xplane <X-Plane 12 folder>.",
            )
        pack_dir = Path(pack).expanduser().resolve()
        cs = custom_scenery_dir(xp)
        tile = pack_tile(pack_dir.name)
        receipt: dict[str, Any]
        if (pack_dir / MANIFEST_NAME).is_file() and tile is not None:
            receipt = install_receipt(pack_dir, cs, tile=tile, link=link, library_path=library)
        else:
            target = install_pack(pack_dir, cs, link=link)
            receipt = {"pack": str(pack_dir), "target": str(target), "custom_scenery": str(cs)}
            if tile is not None:
                with Library(library) as lib:
                    lib.register(tile, "", 0, pack_dir, "ortho4xp", None)
    except OsxpError as exc:
        _err(exc)
        raise typer.Exit(EXIT_ERROR) from None
    if json_output:
        typer.echo(json.dumps(receipt, indent=1, sort_keys=True))
    else:
        typer.echo(f"installed {receipt['pack']} -> {receipt['target']}")
        if receipt.get("overlay_target"):
            typer.echo(f"overlays -> {receipt['overlay_target']}")


@app.command()
def uninstall(
    tile: Annotated[str, typer.Argument(help="tile (+46+006) or pack name (zOrthoStudio_+46+006)")],
    xplane: Annotated[Path | None, _XPLANE_OPT] = None,
    delete: Annotated[
        bool,
        typer.Option(
            "--delete",
            help="delete the tile for good, installed or not: out of X-Plane, its folder, its "
            "entry in the library and the cache space no other tile needs (a tile OrthoStudio XP "
            "did not build is never deleted)",
        ),
    ] = False,
    json_output: Annotated[bool, _JSON_FLAG] = False,
) -> None:
    """Take a tile out of X-Plane: its link, its overlay, their scenery_packs.ini lines.

    With --delete, the tile is deleted for good, as the page's Delete does.
    """
    from orthostudio.install import custom_scenery_dir, pack_tile
    from orthostudio.model import pack_dir_name
    from orthostudio.pipeline.pack import uninstall_receipt

    try:
        found = pack_tile(tile)
        if found is None:
            raise OsxpError(
                "CFG_LATLON_INVALID",
                context={"value": tile},
                message=f"{tile!r} is not a tile.",
                remedy="Give a tile such as +46+006.",
            )
        xp = _resolve_xplane(xplane)
        if delete:
            pack_dir, receipt = _delete_tile(tile, xp)
        else:
            if xp is None:
                raise OsxpError(
                    "XP_DIR_NOT_FOUND",
                    context={"path": "<not detected>"},
                    remedy="Give --xplane <X-Plane 12 folder>.",
                )
            name = pack_dir_name(found) if tile == found.name else tile
            receipt = uninstall_receipt(name, custom_scenery_dir(xp))
    except OsxpError as exc:
        _err(exc)
        raise typer.Exit(EXIT_ERROR) from None
    if json_output:
        typer.echo(json.dumps(receipt, indent=1, sort_keys=True))
        return
    if delete:
        _echo_deleted(pack_dir, receipt)
        return
    if not receipt["removed"]:
        typer.echo(f"{name} is not installed in {receipt['custom_scenery']}")
        return
    typer.echo(f"uninstalled {name}")
    if receipt["overlay_parked"]:
        typer.echo("  its overlay is parked in the pack: osxp install puts it back")
    if receipt["overlay_pack_removed"]:
        typer.echo("  yOrthoStudio_Overlays removed too: no OrthoStudio XP tile is left")


def _delete_tile(name: str, xp: Path | None) -> tuple[Path, dict[str, Any]]:
    """``osxp uninstall --delete``: the page's Delete (``delete_receipt``) on the pack of ``name``,
    a tile or a pack name.

    The library's pack first (an OrthoStudio XP build before an Ortho4XP import of the same tile).
    A tile the library does not know, built without --install, is looked for where its X-Plane link
    leads and in the OrthoStudio XP output folder.
    """
    from orthostudio.install import custom_scenery_dir, is_link, pack_tile
    from orthostudio.model import pack_dir_name
    from orthostudio.pipeline.home import default_tiles_root
    from orthostudio.pipeline.pack import MANIFEST_NAME, delete_receipt, pack_to_delete

    cs = custom_scenery_dir(xp) if xp is not None else None
    try:
        entry = pack_to_delete(name)
        pack_dir, tile = entry.path, entry.tile
    except OsxpError as exc:
        found = pack_tile(name)
        if exc.code != "SYS_WORKING_DIR_INVALID" or found is None:
            raise
        folder = pack_dir_name(found) if name == found.name else name
        tiles = default_tiles_root()
        places = [tiles / folder]
        if cs is not None and is_link(cs / folder):
            places.insert(0, Path(os.path.realpath(cs / folder)))
        pack = next((p for p in places if (p / MANIFEST_NAME).is_file()), None)
        if pack is None:
            raise OsxpError(
                "SYS_WORKING_DIR_INVALID",
                context={"path": folder},
                message=f"OrthoStudio XP knows no tile {found.name}: it is neither in the library "
                f"nor in {tiles}.",
                remedy="See the tiles OrthoStudio XP knows with osxp library.",
            ) from None
        pack_dir, tile = pack, found
    return pack_dir, delete_receipt(pack_dir, tile=tile, custom_scenery=cs)


def _echo_deleted(pack_dir: Path, receipt: dict[str, Any]) -> None:
    if receipt["removed_from_xplane"]:
        typer.echo(f"took {receipt['name']} out of X-Plane")
    elif receipt["custom_scenery"] is None:
        typer.echo(
            "X-Plane was not found, so nothing was taken out of it: if the tile was installed, "
            f"take it out with osxp uninstall {receipt['tile']} --xplane <X-Plane 12 folder>"
        )
    else:
        typer.echo(f"{receipt['name']} was not installed in X-Plane")
    if receipt["pack_deleted"]:
        typer.echo(f"deleted the tile's folder {pack_dir}")
    else:
        typer.echo(f"the tile's folder {pack_dir} was already gone")
    typer.echo(f"{receipt['tile']} is no longer in the library")
    typer.echo(f"freed {_size(receipt['freed_bytes'])} of disk space")
    if receipt["warning"]:
        typer.echo(f"warning: {receipt['warning']}", err=True)


def _size(n: int) -> str:
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} B"


@app.command()
def clean(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="say what would be freed, delete nothing")
    ] = False,
    images: Annotated[
        bool,
        typer.Option("--images", help="also empty the imagery cache (downloaded again if needed)"),
    ] = False,
    everything: Annotated[
        bool,
        typer.Option(
            "--all",
            help="free all the space OrthoStudio XP can give back: every built result no tile on "
            "disk needs, even from a build that just ended, plus the downloaded image pieces and "
            "the map cache (a tile built again downloads its images again); refused while a build "
            "runs",
        ),
    ] = False,
    store: Annotated[Path | None, _STORE_OPT] = None,
    chunks: Annotated[Path | None, _CHUNKS_OPT] = None,
    json_output: Annotated[bool, _JSON_FLAG] = False,
) -> None:
    """Free the disk space the cache holds for no pack (superseded or abandoned builds)."""
    from orthostudio.clean import GRACE_S
    from orthostudio.clean import clean as run_clean
    from orthostudio.graph import Store
    from orthostudio.install.library import default_library_path
    from orthostudio.pipeline.home import (
        default_chunks_root,
        default_mapcache_root,
        default_store_root,
        default_tiles_root,
    )

    store_root = store or default_store_root()
    if everything and store_root.is_dir():
        # Without the hour of grace, a build running now could lose what it is about to use.
        with Store(store_root) as st:
            busy = sorted(st.building_pids())
        if busy:
            typer.echo(
                f"a build is running (process {', '.join(map(str, busy))}): nothing was deleted. "
                "Wait for it to finish (or stop it), then run osxp clean --all again.",
                err=True,
            )
            raise typer.Exit(EXIT_ERROR)
    report = run_clean(
        store_root,
        chunks or default_chunks_root(),
        library_path=default_library_path(),
        tiles_root=default_tiles_root(),
        images=images or everything,
        dry_run=dry_run,
        grace_s=0.0 if everything else GRACE_S,
        mapcache_root=default_mapcache_root(),
    )
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=1))
        return
    verb = "would free" if dry_run else "freed"
    typer.echo(f"kept what {len(report.packs)} pack(s) need: {report.kept} artefact(s)")
    typer.echo(
        f"{'would remove' if dry_run else 'removed'} {report.removed} artefact(s) no pack needs: "
        f"{verb} {_size(report.freed_bytes)}"
    )
    if report.images_removed:
        typer.echo(f"imagery cache emptied: freed {_size(report.images_bytes)}")
    elif images or everything:
        typer.echo(f"imagery cache: {verb} {_size(report.images_bytes)}")
    else:
        typer.echo(
            f"imagery cache: {_size(report.images_bytes)}, kept (--images empties it; a texture "
            "rebuilt later downloads its images again)"
        )
    if everything:
        typer.echo(f"in total: {verb} {_size(report.freed_bytes + report.images_bytes)}")


@app.command("import-ortho4xp")
def import_ortho4xp(
    folder: Annotated[
        Path, typer.Argument(help="the Ortho4XP folder (the one holding Ortho4XP.py)")
    ],
    library: Annotated[Path | None, typer.Option("--library", help="library sqlite file")] = None,
    json_output: Annotated[bool, _JSON_FLAG] = False,
) -> None:
    """List the tiles an Ortho4XP folder built (Tiles/, custom build dir) in the library, as
    Ortho4XP's: OrthoStudio XP shows them and never deletes them."""
    from orthostudio.install import Library

    folder = Path(folder).expanduser()
    try:
        with Library(library) as lib:
            rows = lib.import_ortho4xp(folder)
    except OsxpError as exc:
        _err(exc)
        raise typer.Exit(EXIT_ERROR) from None
    if json_output:
        typer.echo(json.dumps([_library_row(r) for r in rows], indent=1))
    else:
        typer.echo(f"{len(rows)} pack(s) registered from {folder}")
        for r in rows:
            typer.echo(f"  {r.tile.name} {r.kind} {r.provider}{r.zl or ''} {r.path}")


def _library_row(r: Any) -> dict[str, Any]:
    return {
        "tile": r.tile.name,
        "kind": r.kind,
        "provider": r.provider,
        "zl": r.zl,
        "path": str(r.path),
        "built_by": r.built_by,
        "keys": r.keys,
        "registered_at": r.registered_at,
        "updated_at": r.updated_at,
    }


@app.command()
def library(
    library: Annotated[Path | None, typer.Option("--library", help="library sqlite file")] = None,
    json_output: Annotated[bool, _JSON_FLAG] = False,
) -> None:
    """List the packs known to OrthoStudio XP (built by OrthoStudio XP or imported
    from Ortho4XP)."""
    from orthostudio.install import Library

    with Library(library) as lib:
        rows = lib.list()
    if json_output:
        typer.echo(json.dumps([_library_row(r) for r in rows], indent=1))
        return
    if not rows:
        typer.echo("library is empty (osxp build --install, osxp install, osxp import-ortho4xp)")
        return
    for r in rows:
        level = f"{r.provider}{r.zl}" if r.provider else "-"
        typer.echo(f"{r.tile.name}  {r.kind:<8} {level:<8} {r.built_by:<8} {r.path}")


_HEX = re.compile(r"^[0-9a-f]{8,64}$")


def _find_key(store: Any, target: str) -> str | None:
    """A full key, a prefix, or a path (artefact, file inside one, pack directory)."""
    p = Path(target).expanduser()
    if p.exists():
        for cand in (p, *p.parents):
            if _HEX.match(cand.name) and len(cand.name) == 64 and store.info(cand.name):
                return cand.name
        return None
    if _HEX.match(target):
        if len(target) == 64:
            return target if store.info(target) else None
        matches = [i.key for i in store.iter_artifacts() if i.key.startswith(target)]
        return matches[0] if len(matches) == 1 else None
    return None


@app.command()
def why(
    target: Annotated[str, typer.Argument(help="artefact key (or prefix), store path or pack dir")],
    store: Annotated[Path | None, _STORE_OPT] = None,
    depth: Annotated[int, typer.Option("--depth", help="levels of inputs to expand")] = 1,
) -> None:
    """Explain where an artefact comes from: rule, params, inputs, dependents (Store.why)."""
    from orthostudio.graph import Store
    from orthostudio.pipeline import default_store_root
    from orthostudio.pipeline.pack import MANIFEST_NAME, PackManifest

    with Store(store or default_store_root()) as st:
        p = Path(target).expanduser()
        manifest = p / MANIFEST_NAME if p.is_dir() else (p if p.name == MANIFEST_NAME else None)
        if manifest is not None and manifest.is_file():
            m = PackManifest.from_toml(manifest.read_text(encoding="utf-8"))
            typer.echo(f"pack {m.tile} {m.provider}{m.zl} ({manifest})")
            for name in ("dsf", "textures", "overlay", "masks", "mesh", "vectors", "xp12"):
                e = m.artefacts.get(name)
                if e is None:
                    continue
                typer.echo(f"\n[{name}]")
                if st.info(e.key):
                    typer.echo(st.explain(e.key, depth=depth))
                else:
                    typer.echo(f"  {e.rule} key {e.key[:16]} is no longer in the store")
            return
        key = _find_key(st, target)
        if key is None:
            typer.echo(f"error: no artefact matches {target!r} in {st.root}", err=True)
            raise typer.Exit(EXIT_ERROR)
        typer.echo(st.explain(key, depth=depth))


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", help="loopback port")] = 8641,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="open the page in a browser")
    ] = True,
    ui_dir: Annotated[
        Path | None, typer.Option("--ui-dir", help="page directory (default: the bundled one)")
    ] = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="start, read /api/status, stop (what CI runs)"),
    ] = False,
    quit_when_closed: Annotated[
        bool,
        typer.Option(
            "--quit-when-closed",
            help="stop 5 min after the last page closed, unless a build runs (what the app runs)",
        ),
    ] = False,
) -> None:
    """Serve the page and the local API on 127.0.0.1 (never another address)."""
    from orthostudio.api import serve as serve_mod

    if check:
        try:
            status = serve_mod.check(port=port, ui_dir=ui_dir)
        except OsxpError as exc:
            _err(exc)
            raise typer.Exit(EXIT_ERROR) from None
        typer.echo(json.dumps(status, indent=1, sort_keys=True))
        if status.get("page_status") != 200:
            typer.echo(f"error: the page answered {status.get('page_status')}", err=True)
            raise typer.Exit(EXIT_ERROR)
        return
    code = serve_mod.main(
        port=port, open_browser=open_browser, ui_dir=ui_dir, quit_when_closed=quit_when_closed
    )
    if code:
        raise typer.Exit(code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
