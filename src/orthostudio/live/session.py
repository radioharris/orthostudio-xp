# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One stream, from its checks to X-Plane quitting: what ``python -m orthostudio.live stream``
and the Live window both run.

:func:`prepare` checks, sets the region up once, and puts the pack and the plugin in X-Plane; it
refuses (:class:`RefusedError`) before installing anything. :func:`follow` streams until asked to
stop. :func:`finish` takes the pack and the plugin out, but only once X-Plane has quit: it reads its
textures through the link, and a pack left behind would show neutral textures, green-grey, to an
X-Plane started without the streamer.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orthostudio.errors import OsxpError
from orthostudio.live.pack import (
    LINK_NAME,
    FolderError,
    check_folder,
    default_cache_path,
    default_pack_dir,
    live_root,
)
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

Log = Callable[[str], None]

WAIT_FOR_XPLANE_S = 2.0  # how often finish() looks whether X-Plane has quit


class RefusedError(Exception):
    """Streaming cannot start; the message says why, and what to do."""


def default_workers() -> int:
    return max(1, (os.cpu_count() or 4) - 2)


@dataclass
class Options:
    """What a stream is asked for: the command line's options, or the window's settings."""

    xplane: Path | None = None  # X-Plane 12's folder; None: the one OrthoStudio XP detects
    pack: Path | None = None  # None: <data folder>/live/pack
    start: str | None = None  # an airport (or lat,lon) to build around straight away
    provider: str = "BI"
    quality: str = "auto"  # X-Plane's texture quality: auto (from its preferences), maximum, high
    max_zoom: int = 16
    max_zoom_airports: int = 18
    start_mode: str = "around"  # what X-Plane waits for at a start: streamer.START_MODES
    simbrief_user: str = ""  # whose last SimBrief plan the path ahead follows; empty: none
    margin: float = 1.25
    camera_reach_km: float = 25.0
    workers: int = field(default_factory=default_workers)
    port: int = 49000  # X-Plane's UDP port
    source: Path | None = None  # None: AutoOrtho's z_ao_eur
    no_plugin: bool = False
    fresh: bool = False  # as the first time: every texture and all imagery forgotten
    disk_budget_gb: float = 20.0
    cache_budget_gb: float = 10.0


@dataclass
class Prepared:
    """What :func:`prepare` set up, for :func:`follow` and :func:`finish`."""

    xplane: Path
    pack: Path
    gate: Any  # orthostudio.live.gate.Gate, or None: X-Plane will not wait
    fix: Any  # orthostudio.live.streamer.Fix to build around at once, or None
    finest: int  # the sharpest level X-Plane reads (0: 4096 px)
    # {(lat, lon): pack}: squares whose ground X-Plane takes from the user's own tiles
    covered: dict = field(default_factory=dict)


def xplane_dir(explicit: Path | None) -> Path:
    try:
        return find_xplane(explicit)
    except OsxpError as exc:
        raise RefusedError(f"{exc.message} {exc.remedy}") from exc


def region_ready(pack: Path, source: Path) -> bool:
    """Whether ``pack`` holds the region of ``source`` already: set up, nothing more to clone."""
    from orthostudio.live.region import REGION_MARKER

    try:
        meta = json.loads((pack / REGION_MARKER).read_text())
    except (OSError, ValueError):
        return False
    return meta.get("source") == str(source)


def finest_quality(xplane: Path, quality: str) -> int:
    from orthostudio.live.rings import finest_for_quality

    if quality == "auto":
        value = xplane_pref(xplane, "_tex_res")
        return finest_for_quality(int(value) if value and value.isdigit() else None)
    return {"maximum": 0, "high": 1}[quality]


def imagery_bytes() -> int:
    """The disk the downloaded imagery takes: the database and its -wal and -shm."""
    cache = default_cache_path()
    total = 0
    for f in cache.parent.glob(cache.name + "*"):
        with contextlib.suppress(OSError):
            total += f.stat().st_size
    return total


def forget_textures(pack: Path, log: Log) -> None:
    """Every built texture back to the neutral one, as the first time: built again when flown to."""
    from orthostudio.live.region import reset_built

    if xplane_running():
        raise RefusedError("X-Plane is running and reads these textures: quit it first")
    reset_built(pack, log=log)


def forget_imagery(log: Log) -> None:
    """The downloaded imagery deleted: downloaded again when needed. Never while a stream runs."""
    cache = default_cache_path()
    removed = 0
    for f in cache.parent.glob(cache.name + "*"):  # the database and its -wal, -shm
        removed += f.stat().st_size
        f.unlink()
    log(f"downloaded imagery deleted ({removed / 1e9:.1f} GB)")


def reset(pack: Path, log: Log) -> None:
    """The pack and the imagery as the first time, to try a start in a new area."""
    forget_textures(pack, log)
    forget_imagery(log)


def prepare(options: Options, log: Log) -> Prepared:
    """Check, set the region up (once), and put the pack and the plugin in X-Plane.

    Raises :class:`RefusedError`, with nothing installed, when streaming cannot start."""
    from orthostudio.live import plugin
    from orthostudio.live.covered import covered_squares
    from orthostudio.live.gate import Gate
    from orthostudio.live.region import (
        LAYOUT_GROWING,
        Zooms,
        needs_relayout,
        region_layout,
        relayout,
        setup_region,
    )
    from orthostudio.live.route import Route, airport_positions, apt_dat
    from orthostudio.live.streamer import Fix

    xp_dir = xplane_dir(options.xplane)
    pack_dir = options.pack or default_pack_dir()
    link = xp_dir / "Custom Scenery" / LINK_NAME
    already = (
        link.is_symlink()
        and link.resolve() == pack_dir.resolve()
        and is_installed(xp_dir, LINK_NAME)
    )
    if xplane_running() and not already:  # first: X-Plane lists scenery packs as it starts
        raise RefusedError(
            "X-Plane is running and would not see the pack: quit it, start the streamer, then "
            "X-Plane"
        )
    if autoortho_mounted(xp_dir):
        raise RefusedError("AutoOrtho is mounted: quit it first, it serves the same tiles")
    try:
        check_folder(live_root(), custom_scenery=xp_dir / "Custom Scenery", create=True)
    except FolderError as e:
        raise RefusedError(f"Live's folder: {e}. Choose another in the Live window.") from e
    source = options.source or default_source(xp_dir)
    if not region_ready(pack_dir, source) and not (source / "terrain").is_dir():
        raise RefusedError(
            f"AutoOrtho's mesh is not there ({source}): OrthoStudio XP Live streams onto it for "
            "now. Install AutoOrtho's Europe scenery (z_ao_eur) first."
        )
    fix = None
    if options.start:
        try:
            lat, lon = Route.resolve(xp_dir, [options.start]).points[0]
        except SystemExit as e:  # an airport X-Plane does not know
            raise RefusedError(str(e)) from e
        fix = Fix(lat, lon)
    zooms = Zooms(options.max_zoom, options.max_zoom_airports)
    setup_region(source, pack_dir, log=log, zooms=zooms)
    if region_layout(pack_dir) != (zooms, LAYOUT_GROWING):  # each texture at its new size
        if xplane_running() and needs_relayout(pack_dir, zooms):
            raise RefusedError(
                "the zoom changed and X-Plane is running: it keeps a texture at the size it first "
                "read. Quit X-Plane, then start again"
            )
        relayout(pack_dir, zooms, log)
    if options.fresh:
        reset(pack_dir, log)
    install_link(xp_dir, pack_dir, LINK_NAME, log=log)
    covered = covered_squares(xp_dir, LINK_NAME)
    if covered:
        packs = sorted(set(covered.values()))
        log(
            f"your own scenery covers {len(covered)} squares ({', '.join(packs[:3])}"
            + (f" and {len(packs) - 3} more" if len(packs) > 3 else "")
            + "): X-Plane shows it there, and Live downloads nothing there"
        )
    gate = None
    # X-Plane loads plugins at start-up: if it is running, only one already there counts
    if not options.no_plugin and (
        plugin.installed(xp_dir) if xplane_running() else plugin.install(xp_dir, log=log)
    ):
        try:
            gate = Gate(
                xp_dir, lambda icao: airport_positions(apt_dat(xp_dir), [icao]).get(icao.upper())
            )
            log(
                "X-Plane plugin installed: while a flight loads, X-Plane waits until the ortho "
                "around the start is built"
            )
        except OSError as e:
            log(
                f"another streamer holds the plugin's port, X-Plane will not wait for this one: {e}"
            )
    finest = finest_quality(xp_dir, options.quality)
    log(
        f"X-Plane texture quality: finest level {finest} ({4096 >> finest} px); "
        f"sharp up to {options.camera_reach_km:.0f} km farther for external views"
    )
    return Prepared(xp_dir, pack_dir, gate, fix, finest, covered)


def follow(
    prepared: Prepared,
    options: Options,
    log: Log,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    report: Callable[[dict], None] | None = None,
    stop_hint: str = "Ctrl-C here to stop",
    reload_asked: Callable[[], bool] = lambda: False,
) -> None:
    """Follow X-Plane and keep the textures ahead of the aircraft built, until ``should_stop``
    (or Ctrl-C). The pack stays installed: :func:`finish` takes it out. ``reload_asked``: the Live
    window's button, for the reload offered after a start (``start_mode`` "reload")."""
    from orthostudio.live.cache import ChunkCache
    from orthostudio.live.downloader import Downloader
    from orthostudio.live.providers import cache_key
    from orthostudio.live.streamer import DATAREFS, Streamer, run
    from orthostudio.live.xpudp import XPlaneUDP

    try:
        xp = XPlaneUDP([d for _, d in DATAREFS], port=options.port)
        try:
            path = default_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            cache = ChunkCache(path)
            try:
                streamer = Streamer(
                    prepared.pack,
                    cache,
                    lambda on_piece: Downloader(options.provider, on_piece),
                    prepared.finest,
                    options.max_zoom,
                    options.max_zoom_airports,
                    options.margin,
                    options.workers,
                    xplane=xp,
                    provider=cache_key(options.provider),
                    disk_budget=int(options.disk_budget_gb * 1e9),
                    cache_budget=int(options.cache_budget_gb * 1e9),
                    reach_km=options.camera_reach_km,
                    log=log,
                    start_mode=options.start_mode,
                    covered=prepared.covered,
                    simbrief_user=options.simbrief_user,
                )
                log(
                    f"streaming into {prepared.pack} ({len(streamer.index)} textures): "
                    f"start X-Plane, {stop_hint}"
                )
                run(
                    streamer,
                    xp,
                    prepared.fix,
                    should_stop=should_stop,
                    gate=prepared.gate,
                    start_name=options.start,
                    guess_last=not options.fresh,
                    log=log,
                    report=report,
                    reload_asked=reload_asked,
                )
            finally:
                cache.close()
        finally:
            xp.close()
    finally:
        if prepared.gate is not None:
            prepared.gate.close()


def uninstall(xplane: Path, log: Log) -> None:
    """The pack and the plugin out of X-Plane (the pack itself stays)."""
    from orthostudio.live import plugin

    uninstall_link(xplane, LINK_NAME, log=log)
    if plugin.installed(xplane):
        plugin.uninstall(xplane)
        log("uninstalled: the OrthoStudio XP Live plugin is out of X-Plane")


def finish(
    xplane: Path, log: Log, *, hint: str, give_up: Callable[[], bool] = lambda: False
) -> bool:
    """Take the pack and the plugin out of X-Plane once it has quit: it reads its textures through
    the link. False, both left in, when ``give_up`` says so first (``hint`` says how)."""
    if xplane_running():
        log(
            "X-Plane is still running and reads its textures from the pack: it stays until "
            f"X-Plane quits ({hint})"
        )
        while xplane_running():
            if give_up():
                return False
            time.sleep(WAIT_FOR_XPLANE_S)
    uninstall(xplane, log)
    return True
