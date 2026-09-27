# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``python -m orthostudio.live <command>``: OrthoStudio XP Live from the command line.

``ui`` opens the Live window; ``stream`` follows X-Plane and keeps its textures built, in the
terminal; ``setup`` prepares the region's pack once; ``regions`` lists, installs and deletes
AutoOrtho's regions, the mesh Live streams onto; ``reset`` forgets what was built and downloaded;
``status`` says what is built and installed; ``uninstall`` takes the pack and the plugin out of
X-Plane. Until its launch, every command needs ``enabled = true`` in
``~/.orthostudio/live.toml`` (:func:`orthostudio.live.enabled`). ``stream`` takes the window's
settings (:mod:`orthostudio.live.settings`) for the options it is not given.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Annotated

import typer

from orthostudio.live import enabled, session, settings_file, supported
from orthostudio.live.pack import LINK_NAME, default_pack_dir, load_built
from orthostudio.live.scenery import (
    autoortho_mounted,
    default_sources,
    is_installed,
    xplane_running,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="OrthoStudio XP Live: ortho streamed into X-Plane",
)

XPlaneOpt = Annotated[
    Path | None, typer.Option("--xplane", help="X-Plane 12 folder (default: detected)")
]
PackOpt = Annotated[
    Path | None, typer.Option("--pack", help="the pack folder (default: <Live's folder>/pack)")
]

LEAVE_HINT = "Ctrl-C again to leave it installed; `python -m orthostudio.live uninstall` later"


@app.callback()
def _root() -> None:
    if not supported():
        raise typer.Exit(
            _fail(
                "OrthoStudio XP Live runs on macOS only for now (its pack relies on APFS clones)."
            )
        )
    if not enabled():
        raise typer.Exit(
            _fail(
                f"OrthoStudio XP Live is switched off: put `enabled = true` in {settings_file()}."
            )
        )


def _fail(message: str) -> int:
    typer.echo(message, err=True)
    return 1


def _xplane(xplane: Path | None) -> Path:
    try:
        return session.xplane_dir(xplane)
    except session.RefusedError as exc:
        raise typer.Exit(_fail(str(exc))) from exc


def _uninstall_when_xplane_quits(xplane: Path) -> None:
    """Take the pack and the plugin out, but never under a running X-Plane: it reads its textures
    through the link."""
    try:
        session.finish(xplane, typer.echo, hint=LEAVE_HINT)
    except KeyboardInterrupt:
        typer.echo(
            "the pack stays installed: `python -m orthostudio.live uninstall` once X-Plane has quit"
        )


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="the window's local port")] = 8642,
    browser: Annotated[
        bool, typer.Option("--browser", help="open the page in the browser, not in a window")
    ] = False,
) -> None:
    """Open the Live window: start and stop the streaming, see what it builds, set it up."""
    from orthostudio.live.web import open_window

    try:
        open_window(port, browser=browser)
    except OSError as e:
        raise typer.Exit(_fail(f"the Live window could not start: {e}")) from e


@app.command()
def stream(
    xplane: XPlaneOpt = None,
    pack: PackOpt = None,
    start: Annotated[
        str | None,
        typer.Option("--from", help="an airport (or lat,lon) to build around straight away"),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(help="imagery source, a code of OrthoStudio XP's (default: BI, Bing Maps)"),
    ] = None,
    quality: Annotated[
        str,
        typer.Option(
            help="X-Plane's texture quality: auto (read from its preferences), maximum or high "
            "(less than X-Plane reads shows the neutral colour, green-grey, up close)"
        ),
    ] = "auto",
    max_zoom: Annotated[
        int | None,
        typer.Option(
            help="AutoOrtho's max_zoom, 14 to 17: the zoom away from airports (default 16)"
        ),
    ] = None,
    max_zoom_airports: Annotated[
        int | None,
        typer.Option(
            help="AutoOrtho's max_zoom_near_airports, 16 to 19: the zoom around airports "
            "(default 18)"
        ),
    ] = None,
    simbrief: Annotated[
        str | None,
        typer.Option(
            help="your SimBrief name or pilot ID: the path ahead follows your last plan while the "
            "aircraft is on it; only this name goes to simbrief.com (default: the window's)"
        ),
    ] = None,
    start_mode: Annotated[
        str | None,
        typer.Option(
            help="what X-Plane waits for at a start where the zoom makes 8192 px textures: full "
            "(all of them), around (those right around the aircraft), normal (4096 px only), "
            "reload (4096 px, then a reload of the scenery offered once 8192 px is built) "
            "(default: around)"
        ),
    ] = None,
    margin: Annotated[
        float, typer.Option(help="build for rings this much wider than measured")
    ] = 1.25,
    camera_reach_km: Annotated[
        float,
        typer.Option(
            help="how far external views may take the camera: built sharp that much farther "
            "(less shows the neutral colour, green-grey, to a camera that goes farther)"
        ),
    ] = 25.0,
    workers: Annotated[int, typer.Option(help="encoding processes")] = session.default_workers(),
    port: Annotated[int, typer.Option(help="X-Plane's UDP port")] = 49000,
    source: Annotated[
        Path | None,
        typer.Option(help="AutoOrtho scenery pack to take the region from (default z_ao_eur)"),
    ] = None,
    keep_installed: Annotated[
        bool, typer.Option(help="leave the pack in X-Plane when the streamer stops")
    ] = False,
    no_plugin: Annotated[
        bool,
        typer.Option(
            help="do not install the X-Plane plugin: X-Plane will not wait for the ortho at a start"
        ),
    ] = False,
    fresh: Annotated[
        bool,
        typer.Option(
            help="start as the first time: forget every texture built and all imagery downloaded"
        ),
    ] = False,
    tint_8192: Annotated[
        bool | None,
        typer.Option(
            "--tint-8192/--no-tint-8192",
            help="a test: the 8192 px textures built pink, to see where X-Plane shows them "
            "(default: the window's)",
        ),
    ] = None,
    disk_gb: Annotated[
        float | None,
        typer.Option(
            help="disk Live may take: half for the textures built (beyond it, the farthest are "
            "made coarser), half for the downloaded imagery (beyond it, the oldest goes) "
            "(default: the window's, 40)"
        ),
    ] = None,
) -> None:
    """Follow X-Plane and keep the textures ahead of the aircraft built. Start it before X-Plane.

    The options left out are the Live window's settings."""
    from orthostudio.live.settings import LiveSettings, load_settings
    from orthostudio.live.streamer import START_MODES

    saved = load_settings()
    space = saved
    if disk_gb is not None:
        try:  # checked as the window checks it
            space = LiveSettings.model_validate({**saved.model_dump(), "disk_gb": disk_gb})
        except ValueError as e:
            raise typer.Exit(_fail(f"--disk-gb: {e}")) from e
    if start_mode is not None and start_mode not in START_MODES:
        raise typer.Exit(_fail(f"--start-mode: one of {', '.join(START_MODES)}"))
    options = session.Options(
        xplane=xplane,
        pack=pack,
        start=start,
        provider=provider or saved.provider,
        quality=quality,
        max_zoom=saved.max_zoom if max_zoom is None else max_zoom,
        max_zoom_airports=(
            saved.max_zoom_airports if max_zoom_airports is None else max_zoom_airports
        ),
        start_mode=start_mode or saved.start_mode,
        simbrief_user=saved.simbrief_user if simbrief is None else simbrief,
        margin=margin,
        camera_reach_km=camera_reach_km,
        workers=workers,
        port=port,
        source=source,
        no_plugin=no_plugin,
        fresh=fresh,
        disk_budget_gb=space.textures_gb,
        cache_budget_gb=space.imagery_gb,
        tint_8192=saved.tint_8192 if tint_8192 is None else tint_8192,
    )
    try:
        prepared = session.prepare(options, typer.echo)
    except session.RefusedError as exc:
        raise typer.Exit(_fail(str(exc))) from exc
    try:
        session.follow(prepared, options, typer.echo)
    except Exception as e:  # said now, not once X-Plane has quit and the pack could go
        typer.echo("the streamer stopped:\n" + "".join(traceback.format_exception(e)), err=True)
        raise typer.Exit(1) from e
    finally:
        if not keep_installed:
            _uninstall_when_xplane_quits(prepared.xplane)


@app.command()
def setup(
    xplane: XPlaneOpt = None,
    pack: PackOpt = None,
    source: Annotated[
        Path | None,
        typer.Option(help="an AutoOrtho scenery pack (default: every AutoOrtho region installed)"),
    ] = None,
    force: Annotated[bool, typer.Option(help="set it up again")] = False,
) -> None:
    """Make the pack hold a whole AutoOrtho region, once (stream does it too)."""
    from orthostudio.live.region import Zooms, setup_region
    from orthostudio.live.settings import load_settings

    saved = load_settings()
    xp_dir = _xplane(xplane)
    sources = [source] if source else default_sources(xp_dir)
    if not sources:
        raise typer.Exit(
            _fail("no AutoOrtho region installed: install one first (Europe: z_ao_eur)")
        )
    setup_region(
        sources,
        pack or default_pack_dir(),
        force=force,
        log=typer.echo,
        zooms=Zooms(saved.max_zoom, saved.max_zoom_airports),
    )


@app.command()
def reset(pack: PackOpt = None) -> None:
    """Forget every texture built and all imagery downloaded, as the first time."""
    try:
        session.reset(pack or default_pack_dir(), typer.echo)
    except session.RefusedError as exc:
        raise typer.Exit(_fail(str(exc))) from exc


@app.command()
def status(xplane: XPlaneOpt = None, pack: PackOpt = None) -> None:
    """Say what is built and installed."""
    from orthostudio.live import plugin

    xp_dir = _xplane(xplane)
    pack_dir = pack or default_pack_dir()
    textures = load_built(pack_dir).get("textures", {})
    typer.echo(f"pack:      {pack_dir}: {len(textures)} textures built")
    for level in range(5):
        n = sum(1 for v in textures.values() if v == level)
        if n:
            typer.echo(f"           level {level} ({4096 >> level:4d} px): {n}")
    typer.echo(
        f"installed: {'yes' if is_installed(xp_dir, LINK_NAME) else 'no'}"
        f" (X-Plane plugin: {'yes' if plugin.installed(xp_dir) else 'no'})"
    )
    typer.echo(f"AutoOrtho: {'mounted' if autoortho_mounted(xp_dir) else 'not mounted'}")


@app.command()
def uninstall(xplane: XPlaneOpt = None) -> None:
    """Take the pack and the plugin out of X-Plane (the pack itself stays)."""
    xp_dir = _xplane(xplane)
    if xplane_running():
        raise typer.Exit(
            _fail("X-Plane is running and reads its textures from the pack: quit it first")
        )
    session.uninstall(xp_dir, typer.echo)


regions_app = typer.Typer(
    no_args_is_help=True, help="AutoOrtho's regions: the mesh Live streams onto"
)
app.add_typer(regions_app, name="regions")


def _catalog() -> dict:
    from orthostudio.live import aoregions
    from orthostudio.live.pack import live_root

    try:
        return aoregions.catalog(live_root())
    except aoregions.CatalogError as exc:
        raise typer.Exit(_fail(str(exc))) from exc


@regions_app.command("list")
def regions_list(xplane: XPlaneOpt = None) -> None:
    """Each region, its size, and whether AutoOrtho or Live installed it."""
    from orthostudio.live import aoregions
    from orthostudio.live.pack import live_root
    from orthostudio.live.scenery import autoortho_regions

    regions = _catalog()
    mine = aoregions.installed(live_root())
    theirs = {p.name.removeprefix("z_ao_") for p in autoortho_regions(_xplane(xplane))}
    for region_id in aoregions.REGION_IDS:
        region = regions.get(region_id)
        size = f"{region.size / 1e9:5.1f} GB" if region else "     ?"
        if region_id in theirs:
            state = "installed by AutoOrtho"
        elif region_id in mine:
            state = f"installed by Live ({mine[region_id].get('release')})"
        else:
            state = "not installed"
        typer.echo(f"{region_id:8s} {size}  {state}")


@regions_app.command("install")
def regions_install(region: str) -> None:
    """Download a region from AutoOrtho's GitHub into Live's folder; Ctrl-C stops, and the next
    install goes on from there."""
    import threading

    from orthostudio.live import aoregions
    from orthostudio.live.pack import live_root

    regions = _catalog()
    if region not in regions:
        raise typer.Exit(_fail(f"no region {region!r}: one of {', '.join(sorted(regions))}"))
    installer = aoregions.Installer(live_root(), regions[region], log=typer.echo)
    thread = threading.Thread(target=installer.run, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(10.0)
            p = installer.snapshot()
            if thread.is_alive():
                done, total = p["done_bytes"] / 1e9, p["total_bytes"] / 1e9
                typer.echo(
                    f"  {p['phase']}: {done:.1f} of {total:.1f} GB,"
                    f" {p['unpacked']} of {p['zips']} files unpacked"
                )
    except KeyboardInterrupt:
        installer.cancel()
        thread.join()
    if installer.snapshot()["phase"] != "done":
        raise typer.Exit(1)


@regions_app.command("remove")
def regions_remove(region: str, xplane: XPlaneOpt = None) -> None:
    """Delete a region from the disk. One AutoOrtho installed goes as AutoOrtho's own uninstall
    takes it out: AutoOrtho no longer has it either."""
    from orthostudio.live import aoregions
    from orthostudio.live.pack import live_root
    from orthostudio.live.scenery import autoortho_regions

    xp_dir = _xplane(xplane)
    if region in {p.name.removeprefix("z_ao_") for p in autoortho_regions(xp_dir)}:
        if autoortho_mounted(xp_dir) or xplane_running():
            raise typer.Exit(_fail("AutoOrtho or X-Plane is running: quit them first"))
        aoregions.remove_autoortho(xp_dir, region, log=typer.echo)
    elif region in aoregions.installed(live_root()):
        aoregions.remove(live_root(), region, log=typer.echo)
    else:
        raise typer.Exit(_fail(f"the region {region!r} is not installed"))


if __name__ == "__main__":
    app()
