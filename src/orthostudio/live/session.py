# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""One stream, from its checks to X-Plane quitting: what ``python -m orthostudio.live stream``
and the Live window both run.

:func:`prepare` checks, sets the region up once, and puts the pack and the plugin in X-Plane; it
refuses (:class:`RefusedError`) before installing anything, and a start that fails after unchecks
Live's line again. :func:`follow` streams until asked to stop. :func:`finish` unchecks Live's
lines, but only once X-Plane has quit: it reads its textures through the link, and a pack left
checked would lose the pilot's place, the plugin taking it out of an X-Plane started without the
streamer. :func:`remove` takes Live out of X-Plane for good.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orthostudio.errors import OsxpError
from orthostudio.live.filesystem import is_folder_link
from orthostudio.live.gate import read_flight_time
from orthostudio.live.pack import (
    LINK_NAME,
    OVERLAY_LINK_NAME,
    FolderError,
    check_folder,
    default_cache_path,
    default_pack_dir,
    live_root,
)
from orthostudio.live.rings import DEFAULT_MARGIN
from orthostudio.live.scenery import (
    default_sources,
    find_xplane,
    install_link,
    is_installed,
    list_live,
    ortho_streamers,
    osxp_regions,
    quality_said,
    uninstall_link,
    unlist_live,
    xplane_running,
    xplane_texture_quality,
)

Log = Callable[[str], None]

WAIT_FOR_XPLANE_S = 2.0  # how often finish() looks whether X-Plane has quit


class RefusedError(Exception):
    """Streaming cannot start; the message says why, and what to do."""


def default_workers() -> int:
    """Encoding processes: every core of this computer but two, one for each 3 GB of its memory at
    most (:mod:`orthostudio.live.machine`)."""
    from orthostudio.live import machine

    return machine.encoders(*machine.machine())


@dataclass
class Options:
    """What a stream is asked for: the command line's options, or the window's settings."""

    xplane: Path | None = None  # X-Plane 12's folder; None: the one OrthoStudio XP detects
    pack: Path | None = None  # None: <data folder>/live/pack
    start: str | None = None  # an airport (or lat,lon) to build around straight away
    provider: str = "BI"
    max_zoom: int = 16
    max_zoom_airports: int = 18
    simbrief_user: str = ""  # whose last SimBrief plan the path ahead follows; empty: none
    overlays: str = "auto"  # AutoOrtho's roads, forests and buildings where none are: "off"
    language: str = "en"  # the Live window's: the plugin's words in X-Plane
    margin: float = DEFAULT_MARGIN
    # the user's choice, 2026-10-19: none. The rings' margin covers a camera near the aircraft, and
    # one taken far is built round where it is (external views zoomed out at 17 to 84 km over
    # LGAV and LFLL: a few levels late, caught up within seconds, no neutral colour seen). 2 km
    # earlier that day, 10 from a test flight of 2026-09-29, 20 before, 25 until 2026-09-28
    camera_reach_km: float = 0.0
    workers: int = field(default_factory=default_workers)
    port: int = 49000  # X-Plane's UDP port
    source: Path | None = None  # None: every AutoOrtho region installed, AutoOrtho's or Live's
    no_plugin: bool = False
    fresh: bool = False  # as the first time: every texture and all imagery forgotten
    disk_budget_gb: float = 20.0
    cache_budget_gb: float = 10.0
    tint_8192: bool = False  # the test: the 8192 px levels built pink, where X-Plane shows them
    season: str = "auto"  # the photos' season: "auto" (X-Plane's date), or spring... winter
    looks: dict | None = None  # each season's colours (orthostudio.live.look.Look); None: Live's
    decal: str | None = None  # the land's decal on Live's squares; "" none; None: as the region has
    own_tiles: str = "first"  # the pilot's own tiles first, or "under" Live's pack while it runs
    night: bool = False  # the towns' lights at night (orthostudio.live.night); the app: on
    night_light: str = "normal"  # their strength, a key of orthostudio.live.night.LIGHTS


class StartClock:
    """How long each step of a start took, said in one line once Live streams: where a slow start's
    seconds go (the user, 2026-10-31: « 10 secondes AU MOINS »)."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self.now = now
        self.at = now()
        self.steps: list[tuple[str, float]] = []

    def done(self, step: str) -> None:
        now = self.now()
        self.steps.append((step, now - self.at))
        self.at = now

    def line(self) -> str:
        total = sum(s for _, s in self.steps)
        return f"start in {total:.2f} s: " + ", ".join(f"{n} {s:.2f}" for n, s in self.steps)


@dataclass
class Prepared:
    """What :func:`prepare` set up, for :func:`follow` and :func:`finish`."""

    xplane: Path
    pack: Path
    gate: Any  # orthostudio.live.gate.Gate, or None: X-Plane will not wait
    fix: Any  # orthostudio.live.streamer.Fix to build around at once, or None
    # {(lat, lon): pack}: squares whose ground X-Plane takes from the user's own tiles
    covered: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)  # the regions the pack is made of
    clock: StartClock = field(default_factory=StartClock)  # the start's steps, timed


def xplane_dir(explicit: Path | None) -> Path:
    try:
        return find_xplane(explicit)
    except OsxpError as exc:
        raise RefusedError(f"{exc.message} {exc.remedy}") from exc


def _recorded_sources(pack: Path) -> list[str]:
    from orthostudio.live.region import REGION_MARKER, sources_of

    try:
        return sources_of(json.loads((pack / REGION_MARKER).read_text()))
    except (OSError, ValueError):
        return []


def region_ready(pack: Path, source: Path | Sequence[Path]) -> bool:
    """Whether ``pack`` holds the AutoOrtho regions of ``source`` already (one, or several): set
    up, nothing more to clone."""
    wanted = {str(source)} if isinstance(source, Path) else {str(s) for s in source}
    return bool(wanted) and set(_recorded_sources(pack)) == wanted


def relayout_due(options: Options) -> bool:
    """Whether a start with ``options`` lays the pack out again for their zoom before anything
    else (:func:`orthostudio.live.region.relayout`): the window says so while it does, some
    seconds after a zoom changed (the user, 2026-10-10: it said it put the pack in X-Plane). A
    pack not set up yet: no."""
    from orthostudio.live.region import LAYOUT_FIXED, REGION_MARKER, Zooms, region_layout

    pack_dir = options.pack or default_pack_dir()
    if not (pack_dir / REGION_MARKER).is_file():
        return False
    return region_layout(pack_dir) != (
        Zooms(options.max_zoom, options.max_zoom_airports),
        LAYOUT_FIXED,
    )


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


def keep_region_lists(xplane: Path | None, log: Log) -> None:
    """Each region a start would stream onto read into its list, where that list is missing or
    older than its folder (``orthostudio.live.names.one_region``): the Windows setup does it for
    the regions already there, Live's window as it opens for one installed meanwhile (AutoOrtho's),
    so that a start reads lists only. Without X-Plane, Live's own regions alone."""
    from orthostudio.live import aoregions
    from orthostudio.live.names import one_region, region_list_file
    from orthostudio.live.region import sources_of_pack

    root = live_root()
    lists = aoregions.lists_dir(root)
    found = sources_of_pack(default_pack_dir()) + (
        default_sources(xplane, root)
        if xplane is not None
        else [aoregions.mesh_dir(root, region) for region in sorted(aoregions.installed(root))]
    )
    seen: set[Path] = set()
    for source in found:
        kept = region_list_file(lists, source)
        if kept in seen:
            continue
        seen.add(kept)
        t0 = time.monotonic()
        try:
            one = one_region(source, lists)
        except OSError as e:  # a start lists it then
            log(f"{source.name}: its textures not listed: {e}")
            continue
        if one.listed:
            state = f"its list not kept: {one.keep_error}" if one.keep_error else "its list kept"
            took = time.monotonic() - t0
            log(f"{source.name}: {one.count} textures listed in {took:.1f} s, {state}")


def reset(pack: Path, log: Log) -> None:
    """The pack and the imagery as the first time, to try a start in a new area."""
    forget_textures(pack, log)
    forget_imagery(log)


def prepare(options: Options, log: Log) -> Prepared:
    """Check, set the region up (once), and put the pack and the plugin in X-Plane.

    Raises :class:`RefusedError` when streaming cannot start: before installing anything, or, past
    Live's line, with the line it checked unchecked again. Without the plugin (``no_plugin``, or
    one that cannot be installed), an earlier one is taken out of an X-Plane not running."""
    from orthostudio.live import plugin
    from orthostudio.live.gate import GATE_PORT, Gate
    from orthostudio.live.region import (
        LAYOUT_FIXED,
        Zooms,
        needs_relayout,
        region_layout,
        relayout,
        remove_aside,
        remove_temps,
        set_decal,
        set_night,
        set_zooms,
        setup_region,
    )
    from orthostudio.live.route import AirportIndex, Route, airport_files, apt_dat
    from orthostudio.live.streamer import Fix

    # where a start's wait begins, in the log: the window's Start or Prepare
    log("starting" + (f" for a departure from {options.start}" if options.start else ""))
    clock = StartClock()
    xp_dir = xplane_dir(options.xplane)
    pack_dir = options.pack or default_pack_dir()
    link = xp_dir / "Custom Scenery" / LINK_NAME
    already = (
        is_folder_link(link)
        and link.resolve() == pack_dir.resolve()
        and is_installed(xp_dir, LINK_NAME)
    )
    if xplane_running() and not already:  # first: X-Plane lists scenery packs as it starts
        raise RefusedError(
            "X-Plane is running and would not see the pack: quit it, start the streamer, then "
            "X-Plane"
        )
    others = ortho_streamers(xp_dir)
    if others:
        verb, pronoun = ("is", "it") if len(others) == 1 else ("are", "them")
        raise RefusedError(
            f"{' and '.join(others)} {verb} running: quit {pronoun} first, only one program may "
            "stream ortho to X-Plane"
        )
    try:
        check_folder(live_root(), custom_scenery=xp_dir / "Custom Scenery", create=True)
    except FolderError as e:
        raise RefusedError(f"Live's folder: {e}. Choose another in the Live window.") from e
    from orthostudio.live import winsearch

    winsearch.keep_out(live_root(), log)  # Windows: its files too many for the indexer
    sources = [options.source] if options.source else default_sources(xp_dir)
    from orthostudio.live.names import terrain_folder

    if not sources or not all(terrain_folder(s).is_dir() for s in sources):
        # the files the DSFs name come from the regions while Live streams, round the aircraft
        # (orthostudio.live.names): a region taken away leaves nothing to make them from, where the
        # pack once held every one of them (2026-09-30)
        gone = [str(s) for s in sources if not terrain_folder(s).is_dir()]
        raise RefusedError(
            (
                f"the AutoOrtho region {gone[0]} is gone or incomplete: install it again, "
                if gone
                else "no mesh yet: install the region you fly over, "
            )
            + "in the Live window (Settings, first question) or with "
            "`python -m orthostudio.live regions install <region>`"
        )
    clock.done("checks")
    fix = None
    if options.start:
        try:
            lat, lon = Route.resolve(xp_dir, [options.start]).points[0]
        except SystemExit as e:  # an airport X-Plane does not know
            raise RefusedError(str(e)) from e
        fix = Fix(lat, lon)
        clock.done("departure")
    zooms = Zooms(options.max_zoom, options.max_zoom_airports)
    from orthostudio.live import machine

    # the pack's one-time work (hundreds of thousands of files) in the system's background mode for
    # the disk: every other program keeps it (machine.background_io)
    with machine.background_io():
        setup_region(sources, pack_dir, log=log, zooms=zooms, xplane_running=xplane_running)
        set_decal(pack_dir, options.decal, log=log, xplane_running=xplane_running)
        set_night(pack_dir, options.night, log=log, xplane_running=xplane_running)
        left = remove_temps(pack_dir)  # before anything writes there
        if left:
            log(f"{left} unfinished files a stopped Live left among the textures removed")
        laid_out, layout = region_layout(pack_dir)
        if (laid_out, layout) != (zooms, LAYOUT_FIXED):  # each texture at its new size
            if xplane_running() and needs_relayout(pack_dir, zooms):
                raise RefusedError(
                    "the zoom changed and X-Plane is running: it keeps a texture at the size it "
                    "first read. Quit X-Plane, then start again"
                )
            if layout < LAYOUT_FIXED:  # a pack of before: laid out once, every texture
                relayout(pack_dir, zooms, log, workers=machine.share().encoders)
            else:  # each texture as its square is made (region.set_zooms)
                set_zooms(pack_dir, zooms, log)
        elif any(pack_dir.glob(".terrain-old-*")):  # a start that stopped before they were gone
            import threading

            threading.Thread(target=remove_aside, args=(pack_dir,), daemon=True).start()
        if options.fresh:
            reset(pack_dir, log)
    clock.done("region")
    covered = own_ground(xp_dir, pack_dir, options, log)
    try:
        clock.done("own tiles")
        _overlays(xp_dir, pack_dir, sources, covered, options.overlays, xplane_running(), log)
        clock.done("overlays")
        gate = None
        # X-Plane loads plugins at start-up: if it is running, only one already there counts
        if not options.no_plugin and (
            plugin.installed(xp_dir) if xplane_running() else plugin.install(xp_dir, log=log)
        ):
            try:
                gate = Gate(xp_dir, AirportIndex(apt_dat(xp_dir), airport_files(xp_dir)))
            except OSError as e:
                # never streaming without it: the plugin, finding nobody there as X-Plane starts,
                # would take Live's scenery out of X-Plane (the review of 2026-10-01)
                raise RefusedError(
                    f"Live's port for its X-Plane plugin (UDP {GATE_PORT}) is taken: another Live "
                    "runs on this computer, or the system keeps the port for itself (on Windows, "
                    f"Hyper-V or WSL may): {e}"
                ) from e
            log(
                "X-Plane plugin installed: while a flight loads, X-Plane waits until the ortho "
                "around the start is built"
            )
        elif not xplane_running() and plugin.installed(xp_dir):
            # without the gate, one left from an earlier start would find nobody on Live's port
            # as X-Plane starts and take Live's scenery out of X-Plane (the review of 2026-11-13)
            if not plugin.uninstall(xp_dir):
                log("an earlier X-Plane plugin of Live's could not be taken out of X-Plane")
        clock.done("plugin")
        # every level of the zoom chosen, whatever X-Plane's texture quality: at High X-Plane shows
        # each texture one size smaller, which the pilot's photos then make the sharpest it can be
        # (the user, 2026-11-13: « on télécharge ce que l'utilisateur choisi 16/18 ou 17/19 »)
        log(
            f"X-Plane texture quality: {quality_said(xplane_texture_quality(xp_dir))}, Live builds "
            "every level of the zoom chosen; "
            f"sharp up to {options.camera_reach_km:.0f} km farther for external views"
        )
        return Prepared(xp_dir, pack_dir, gate, fix, covered, list(sources), clock)
    except BaseException:
        # a start that ends here unchecks the line it checked, as a stop does: left checked, it
        # would lose its place at X-Plane's next start; one checked before it (another Live, or
        # a running X-Plane this start rejoined) stays as it was (the reviews of 2026-11-13)
        if not already:
            unlist_live(xp_dir, log=log)
        raise


def own_ground(
    xp_dir: Path, pack_dir: Path, options: Options, log: Log
) -> dict[tuple[int, int], str]:
    """Live's line in X-Plane's list of sceneries (:func:`orthostudio.live.scenery.list_live`:
    checked where it is listed, never moved), and the squares whose ground X-Plane takes from the
    packs listed above it then (``{(lat, lon): pack}``): at a start, and again between two flights
    when that list changed (:func:`follow`)."""
    from orthostudio.live.covered import covered_squares
    from orthostudio.live.geo import dsf_tile_of

    list_live(xp_dir, pack_dir, options.own_tiles, log=log)
    # only the squares Live's pack has: another pack's elsewhere does not matter to it
    region = {
        square
        for dsf in (pack_dir / "Earth nav data").glob("*/*.dsf")
        if (square := dsf_tile_of(str(dsf))) is not None
    }
    covered = covered_squares(xp_dir, LINK_NAME, region)
    if covered:
        packs = sorted(set(covered.values()))
        log(
            f"your own scenery covers {len(covered)} squares ({', '.join(packs[:3])}"
            + (f" and {len(packs) - 3} more" if len(packs) > 3 else "")
            + "): X-Plane shows it there, and Live downloads nothing there"
        )
    return covered


def own_ground_watch(
    prepared: Prepared, options: Options, streamer: Any, log: Log
) -> Callable[[], None]:
    """What the stream does between two flights: once X-Plane's list of sceneries changed (a tile
    built, taken out or put back in OrthoStudio XP) and X-Plane is not running, which reads that
    list as it starts, the pilot's own squares are found again (:func:`own_ground`), Live's
    overlays made for them, and the streamer told: a tile taken out was left alone until Live's
    next start (Haneda, 2026-10-18)."""
    from orthostudio.live.scenery import _ini

    ini = _ini(prepared.xplane)

    def stamp() -> int:
        try:
            return ini.stat().st_mtime_ns
        except OSError:
            return 0

    seen = [stamp()]

    def rescan() -> None:
        if stamp() == seen[0] or xplane_running():
            return
        log("X-Plane's list of sceneries changed: your own sceneries looked for again")
        covered = own_ground(prepared.xplane, prepared.pack, options, log)
        _overlays(
            prepared.xplane, prepared.pack, prepared.sources, covered, options.overlays, False, log
        )
        seen[0] = stamp()  # its own lines just written in it
        if covered != prepared.covered:
            streamer.set_covered(covered)
            prepared.covered = covered
            if not covered:
                log("your own sceneries cover none of Live's squares now: Live draws them all")

    return rescan


def overlay_dir() -> Path:
    """Live's overlay pack (orthostudio.live.overlays), beside its pack in Live's folder."""
    return live_root() / "overlays"


def _overlays(
    xp_dir: Path,
    pack_dir: Path,
    sources: Sequence[Path],
    covered: dict,
    setting: str,
    running: bool,
    log: Log,
) -> None:
    """AutoOrtho's roads, forests and buildings on Live's squares where nothing else draws them,
    in a pack of their own listed above the ortho; none, and the pack out of X-Plane, else. Made
    again only while X-Plane is not running: it reads the overlays it has loaded."""
    from orthostudio.live import overlays

    folder = overlay_dir()
    if running:  # the pack stays as X-Plane found it
        return
    placed = overlays.build(xp_dir, pack_dir, folder, sources, live_root(), covered, setting, log)
    if placed:
        # right above Live's pack, wherever "Live everywhere" put it, and back there at each start:
        # listed under it, X-Plane read none of them (2026-10-26)
        install_link(
            xp_dir, folder, OVERLAY_LINK_NAME, log=log, kind="overlay", above=LINK_NAME, again=True
        )
    elif is_installed(xp_dir, OVERLAY_LINK_NAME) or is_folder_link(
        xp_dir / "Custom Scenery" / OVERLAY_LINK_NAME
    ):
        uninstall_link(xp_dir, OVERLAY_LINK_NAME, log=log)


def follow(
    prepared: Prepared,
    options: Options,
    log: Log,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    report: Callable[[dict], None] | None = None,
    stop_hint: str = "Ctrl-C here to stop",
    language: Callable[[], str] | None = None,
    attach: Callable[[Any], None] | None = None,
    departure: Callable[[], tuple[float, float, str] | None] | None = None,
) -> None:
    """Follow X-Plane and keep the textures ahead of the aircraft built, until ``should_stop``
    (or Ctrl-C). The pack stays installed: :func:`finish` takes it out. ``language``: the Live
    window's, asked each time the plugin is answered (``options.language`` by default).
    ``attach``: told of the streamer once it runs, and of None once it stops (the window's map
    reads what it builds from its memory). ``departure``: a start the window asks to prepare
    before X-Plane loads it (``streamer.run``)."""
    from orthostudio.live import aoregions, machine
    from orthostudio.live.cache import ChunkCache
    from orthostudio.live.downloader import Downloader
    from orthostudio.live.providers import cache_key
    from orthostudio.live.region import night_on
    from orthostudio.live.streamer import DATAREFS, Streamer, run
    from orthostudio.live.xpudp import XPlaneUDP

    clock = prepared.clock
    computer = machine.share(options.workers)
    for line in computer.notes(options.max_zoom, options.max_zoom_airports):
        log(line)
    clock.done("computer")
    season_for = None
    if options.season == "auto":
        from orthostudio.live.look import SEASONS
        from orthostudio.live.pack import load_built

        season_for = _season_for(prepared.xplane)
        built_in = load_built(prepared.pack).get("season")
        if prepared.fix is None and built_in in SEASONS:
            # no start yet: the textures built keep their season until a flight's date says
            # another (a guess from the date saved last threw them away, then built them again)
            season, why = built_in, "the textures built, until a flight's date"
        else:
            saved = read_flight_time(_freeflight(prepared.xplane))
            lat = prepared.fix.lat if prepared.fix is not None else (saved and saved[1])
            season, why = season_for(lat if lat is not None else 45.0)
    else:
        season, why = options.season, "chosen in Live's settings"
    log(f"season: {season} ({why})")
    clock.done("season")
    try:
        xp = XPlaneUDP([d for _, d in DATAREFS], port=options.port)
        try:
            path = default_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            cache = ChunkCache(path)
            clock.done("photos")
            try:
                streamer = Streamer(
                    prepared.pack,
                    cache,
                    lambda on_piece: Downloader(options.provider, on_piece, log=log),
                    options.max_zoom,
                    options.max_zoom_airports,
                    options.margin,
                    computer.encoders,  # --workers, 61 at most on Windows
                    xplane=xp,
                    provider=cache_key(options.provider),
                    disk_budget=int(options.disk_budget_gb * 1e9),
                    cache_budget=int(options.cache_budget_gb * 1e9),
                    reach_km=options.camera_reach_km,
                    log=log,
                    covered=prepared.covered,
                    simbrief_user=options.simbrief_user,
                    tint_8192=options.tint_8192,
                    looks=options.looks,
                    season=season,
                    decal=options.decal,
                    flight_encodes=computer.flight_encodes,
                    sharp_builds=computer.sharp_builds,
                    installed_regions=lambda: {
                        p.name.removeprefix("z_osxp_") for p in osxp_regions(live_root())
                    },
                    region_lists=aoregions.lists_dir(live_root()),
                    # as the pack's terrain files name them, made only when the settings say so:
                    # turned off while X-Plane ran, those made before were neither counted nor
                    # followed (the review of 2026-11-11)
                    night=_night_work(prepared, log, options.night_light, options.night)
                    if night_on(prepared.pack)
                    else None,
                )
                clock.done("textures")
                if streamer.region_note:
                    log(streamer.region_note)
                log(
                    f"streaming into {prepared.pack} ({len(streamer.index)} textures): "
                    f"start X-Plane, {stop_hint}"
                )
                log(f"  {clock.line()}")
                if options.tint_8192:
                    log("test: the 8192 px textures are built pink, where X-Plane shows them")
                if attach is not None:
                    attach(streamer)
                rescan = own_ground_watch(prepared, options, streamer, log)
                # a new location chosen in flight loads no aircraft, which the plugin would hold:
                # Live pauses X-Plane itself then (orthostudio.live.pause)
                pauser = None
                if prepared.gate is not None:
                    from orthostudio.live.pause import Pauser
                    from orthostudio.live.scenery import xplane_pid

                    pauser = Pauser(prepared.gate.prefs, xplane_pid, log)
                    pauser.start()
                try:
                    run(
                        streamer,
                        xp,
                        prepared.fix,
                        should_stop=should_stop,
                        gate=prepared.gate,
                        start_name=options.start,
                        log=log,
                        report=report,
                        language=language or (lambda: options.language),
                        departure=departure or (lambda: None),
                        season_for=season_for,
                        between=rescan,
                        pauser=pauser,
                    )
                finally:
                    if pauser is not None:
                        pauser.stop()  # X-Plane never left paused by a Live that stops
            finally:
                if attach is not None:
                    attach(None)
                cache.close()
        finally:
            xp.close()
    finally:
        if prepared.gate is not None:
            prepared.gate.close()


GRIDS_ENV = "OSXP_LIVE_NIGHT_GRIDS"  # a folder of density grids looked in first: the test flights'


def _night_work(prepared: Prepared, log: Log, light: str = "normal", lights: bool = True):
    """The night textures of the textures built (orthostudio.live.nightwork): X-Plane's roads from
    its Global Scenery, else from the overlays of Live's regions, NASA's grids from each
    region's night folder, else Live's; their lights as bright as ``light`` says; none made
    without ``lights``, those there counted and followed."""
    from orthostudio.live import aoregions
    from orthostudio.live.night import GAIN, LIGHTS
    from orthostudio.live.nightwork import NightWork
    from orthostudio.live.osxpregions import OVERLAY_PACK

    return NightWork(
        prepared.pack,
        prepared.xplane / "Global Scenery" / "X-Plane 12 Global Scenery",
        aoregions.regions_dir(live_root()) / OVERLAY_PACK,
        ([Path(os.environ[GRIDS_ENV])] if os.environ.get(GRIDS_ENV) else [])
        + [Path(source) / "night" for source in prepared.sources]
        + [live_root() / "night"],
        log=log,
        prefs=_freeflight(prepared.xplane),
        gain=LIGHTS.get(light, GAIN),
        lights=lights,
    )


def _freeflight(xplane: Path) -> Path:
    return xplane / "Output" / "preferences" / "Freeflight.prf"


def _season_for(xplane: Path) -> Callable[..., tuple[str, str]]:
    """The season of a start at a latitude, and why, for the log: from ``day``, the flight's date
    the plugin gives as X-Plane loads it, else the date of the flight X-Plane saved last
    (``gate.read_flight_time``: the one it starts, read at the click), today's when none is
    saved."""
    import datetime

    from orthostudio.live.look import season_on, today

    prefs = _freeflight(xplane)

    def season_for(lat: float, day: int | None = None) -> tuple[str, str]:
        whose = "the flight's date in X-Plane"
        if day is None:
            saved = read_flight_time(prefs)
            day, whose = (today(), "today") if saved is None else (saved[0], "X-Plane's date")
        date = datetime.date(datetime.date.today().year, 1, 1) + datetime.timedelta(days=day - 1)
        return season_on(day, lat), f"{whose}, {date.day} {date:%b}, at {lat:.1f}°"

    return season_for


def uninstall(xplane: Path, log: Log) -> None:
    """Live out of X-Plane, at a stop and from the window: its lines unchecked where they are, their
    links kept (:func:`orthostudio.live.scenery.unlist_live`), so that X-Plane keeps the place the
    pilot gave them. The plugin stays, idle without Live: at X-Plane's start it takes Live's links
    out only when Live's line is checked or gone (``take_out_if_alone``). The pack itself stays."""
    unlist_live(xplane, log=log)


def remove(xplane: Path, log: Log) -> None:
    """Live out of X-Plane for good (the app uninstalled, ``python -m orthostudio.live
    uninstall``): its lines and links, and its plugin. The pack itself stays."""
    from orthostudio.live import plugin

    for name in (LINK_NAME, OVERLAY_LINK_NAME):
        uninstall_link(xplane, name, log=log)
    if plugin.installed(xplane) and not plugin.uninstall(xplane):
        log("the OrthoStudio XP Live plugin stays in X-Plane: Windows still holds its file")


def finish(
    xplane: Path, log: Log, *, hint: str, give_up: Callable[[], bool] = lambda: False
) -> bool:
    """Live out of X-Plane once it has quit (:func:`uninstall`): it reads its textures through the
    link. False, Live's line left checked, when ``give_up`` says so first (``hint`` says how)."""
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
