# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``python -m orthostudio.live <command>``: OrthoStudio XP Live from the command line.

``stream`` follows X-Plane and keeps its textures built; ``setup`` prepares the region's pack once;
``reset`` forgets what was built and downloaded; ``status`` says what is built and installed;
``uninstall`` takes the pack and the plugin out of X-Plane. Until its launch, every command needs
``enabled = true`` in ``~/.orthostudio/live.toml`` (:func:`orthostudio.live.enabled`).
"""

from __future__ import annotations

import os
import time
import traceback
from pathlib import Path
from typing import Annotated

import typer

from orthostudio.errors import OsxpError
from orthostudio.live import enabled, settings_file, supported
from orthostudio.live.pack import LINK_NAME, default_cache_path, default_pack_dir, load_built
from orthostudio.live.scenery import (
    autoortho_mounted,
    default_source,
    find_xplane,
    install_link,
    is_installed,
    uninstall_link,
    xplane_pref,
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
    Path | None, typer.Option("--pack", help="the pack folder (default: <data folder>/live/pack)")
]


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
        return find_xplane(xplane)
    except OsxpError as exc:
        raise typer.Exit(_fail(f"{exc.message} {exc.remedy}")) from exc


def _uninstall(xplane: Path) -> None:
    from orthostudio.live import plugin

    uninstall_link(xplane, LINK_NAME)
    if plugin.installed(xplane):
        plugin.uninstall(xplane)
        typer.echo("uninstalled: the OrthoStudio XP Live plugin is out of X-Plane")


def _uninstall_when_xplane_quits(xplane: Path) -> None:
    """Take the pack and the plugin out, but never under a running X-Plane: it reads its textures
    through the link."""
    if xplane_running():
        typer.echo(
            "X-Plane is still running and reads its textures from the pack: it stays until "
            "X-Plane quits "
            "(Ctrl-C again to leave it installed; `python -m orthostudio.live uninstall` later)"
        )
        try:
            while xplane_running():
                time.sleep(2)
        except KeyboardInterrupt:
            typer.echo(
                "the pack stays installed: `python -m orthostudio.live uninstall` once X-Plane "
                "has quit"
            )
            return
    _uninstall(xplane)


def _reset(pack: Path) -> None:
    """The pack and the imagery as the first time, to try a start in a new area."""
    from orthostudio.live.region import reset_built

    if xplane_running():
        raise typer.Exit(_fail("X-Plane is running and reads these textures: quit it first"))
    reset_built(pack, log=typer.echo)
    cache = default_cache_path()
    removed = 0
    for f in cache.parent.glob(cache.name + "*"):  # the database and its -wal, -shm
        removed += f.stat().st_size
        f.unlink()
    typer.echo(f"downloaded imagery deleted ({removed / 1e9:.1f} GB)")


def _finest_quality(xplane: Path, quality: str) -> int:
    from orthostudio.live.rings import finest_for_quality

    if quality == "auto":
        value = xplane_pref(xplane, "_tex_res")
        return finest_for_quality(int(value) if value and value.isdigit() else None)
    return {"maximum": 0, "high": 1}[quality]


@app.command()
def stream(
    xplane: XPlaneOpt = None,
    pack: PackOpt = None,
    start: Annotated[
        str | None,
        typer.Option("--from", help="an airport (or lat,lon) to build around straight away"),
    ] = None,
    provider: Annotated[
        str, typer.Option(help="imagery provider code of orthostudio.live.providers")
    ] = "BI",
    quality: Annotated[
        str,
        typer.Option(
            help="X-Plane's texture quality: auto (read from its preferences), maximum or high"
        ),
    ] = "auto",
    max_zoom: Annotated[int, typer.Option(help="sharpest imagery for ZL16 textures")] = 16,
    max_zoom_airports: Annotated[int, typer.Option(help="sharpest imagery for ZL18 textures")] = 18,
    margin: Annotated[
        float, typer.Option(help="build for rings this much wider than measured")
    ] = 1.25,
    camera_reach_km: Annotated[
        float,
        typer.Option(
            help="how far external views may take the camera: built sharp that much farther"
        ),
    ] = 25.0,
    workers: Annotated[int, typer.Option(help="encoding processes")] = max(
        1, (os.cpu_count() or 4) - 2
    ),
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
    disk_budget_gb: Annotated[
        float,
        typer.Option(
            help="disk the built textures may take; beyond it, the farthest are made coarser"
        ),
    ] = 20.0,
    cache_budget_gb: Annotated[
        float, typer.Option(help="disk the downloaded imagery may take; beyond it, the oldest goes")
    ] = 10.0,
) -> None:
    """Follow X-Plane and keep the textures ahead of the aircraft built. Start it before X-Plane."""
    from orthostudio.live import plugin
    from orthostudio.live.cache import ChunkCache
    from orthostudio.live.downloader import Downloader
    from orthostudio.live.gate import Gate
    from orthostudio.live.region import setup_region
    from orthostudio.live.route import Route, airport_positions, apt_dat
    from orthostudio.live.streamer import DATAREFS, Fix, Streamer, run
    from orthostudio.live.xpudp import XPlaneUDP

    xp_dir = _xplane(xplane)
    pack_dir = pack or default_pack_dir()
    link = xp_dir / "Custom Scenery" / LINK_NAME
    already = (
        link.is_symlink()
        and link.resolve() == pack_dir.resolve()
        and is_installed(xp_dir, LINK_NAME)
    )
    if (
        xplane_running() and not already
    ):  # before anything else: X-Plane lists scenery packs as it starts
        raise typer.Exit(
            _fail(
                "X-Plane is running and would not see the pack: quit it, start the streamer, then "
                "X-Plane"
            )
        )
    if autoortho_mounted(xp_dir):
        raise typer.Exit(_fail("AutoOrtho is mounted: quit it first, it serves the same tiles"))
    setup_region(source or default_source(xp_dir), pack_dir, log=typer.echo)
    if fresh:
        _reset(pack_dir)
    install_link(xp_dir, pack_dir, LINK_NAME, log=typer.echo)
    gate = None
    # X-Plane loads plugins at start-up: if it is running, only one already there counts
    if not no_plugin and (
        plugin.installed(xp_dir) if xplane_running() else plugin.install(xp_dir, log=typer.echo)
    ):
        try:
            gate = Gate(
                xp_dir, lambda icao: airport_positions(apt_dat(xp_dir), [icao]).get(icao.upper())
            )
            typer.echo(
                "X-Plane plugin installed: while a flight loads, X-Plane waits until the ortho "
                "around the start is built"
            )
        except OSError as e:
            typer.echo(
                f"another streamer holds the plugin's port, X-Plane will not wait for this one: {e}"
            )
    finest = _finest_quality(xp_dir, quality)
    typer.echo(
        f"X-Plane texture quality: finest level {finest} ({4096 >> finest} px); "
        f"sharp up to {camera_reach_km:.0f} km farther for external views"
    )
    fix = None
    if start:
        lat, lon = Route.resolve(xp_dir, [start]).points[0]
        fix = Fix(lat, lon)
    xp = XPlaneUDP([d for _, d in DATAREFS], port=port)
    cache = default_cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    streamer = Streamer(
        pack_dir,
        ChunkCache(cache),
        lambda on_piece: Downloader(provider, on_piece),
        finest,
        max_zoom,
        max_zoom_airports,
        margin,
        workers,
        xplane=xp,
        provider=provider,
        disk_budget=int(disk_budget_gb * 1e9),
        cache_budget=int(cache_budget_gb * 1e9),
        reach_km=camera_reach_km,
        log=typer.echo,
    )
    typer.echo(
        f"streaming into {pack_dir} ({len(streamer.index)} textures): start X-Plane, Ctrl-C here "
        f"to stop"
    )
    try:
        run(streamer, xp, fix, gate=gate, start_name=start, guess_last=not fresh, log=typer.echo)
    except Exception as e:  # said now, not once X-Plane has quit and the pack could go
        typer.echo("the streamer stopped:\n" + "".join(traceback.format_exception(e)), err=True)
        raise typer.Exit(1) from e
    finally:
        xp.close()
        if gate is not None:
            gate.close()
        if not keep_installed:
            _uninstall_when_xplane_quits(xp_dir)


@app.command()
def setup(
    xplane: XPlaneOpt = None,
    pack: PackOpt = None,
    source: Annotated[
        Path | None, typer.Option(help="AutoOrtho scenery pack (default z_ao_eur)")
    ] = None,
    force: Annotated[bool, typer.Option(help="set it up again")] = False,
) -> None:
    """Make the pack hold a whole AutoOrtho region, once (stream does it too)."""
    from orthostudio.live.region import setup_region

    xp_dir = _xplane(xplane)
    setup_region(
        source or default_source(xp_dir), pack or default_pack_dir(), force=force, log=typer.echo
    )


@app.command()
def reset(pack: PackOpt = None) -> None:
    """Forget every texture built and all imagery downloaded, as the first time."""
    _reset(pack or default_pack_dir())


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
    _uninstall(xp_dir)


if __name__ == "__main__":
    app()
