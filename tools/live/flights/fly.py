# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Replayable test flights for OrthoStudio XP Live (``docs/specs/live/flights.md``): X-Plane flies
scenarios by itself, through the test pilot plugin (``native/xplane_test_pilot``), the same way
every time, and each leaves its files and a report in a folder of its own.

    uv run python tools/live/flights/fly.py scandinavia-columns scandinavia-rows
    uv run python tools/live/flights/fly.py --streamer live --cold <scenario>
    uv run python tools/live/flights/fly.py --streamer autoortho --ao-settings default <scenario>
    uv run python tools/live/flights/fly.py --streamer autoortho --zooms 17/19 --cold <scenario>
    uv run python tools/live/flights/fly.py --campaign ao-vs-live
    uv run python tools/live/flights/fly.py --streamer app --with-osxp --cold <scenario>
    uv run python tools/live/flights/fly.py --campaign fenetre-unique

Nobody at the controls. X-Plane starts once for the series, straight in the air at the first
scenario's start (hidden options: no main menu, the preferences never saved); each scenario after
it starts where it says, the plugin placing the aircraft there; at the end the plugin quits X-Plane
and the runner does not wait for it. ``--keep-open`` leaves X-Plane up: the next series then starts
at once.

``--streamer none``: X-Plane alone (the default). ``--streamer live``: Live's streamer from this
checkout, with the user's Live settings and folder. ``--streamer autoortho``: AutoOrtho without its
window (``-H``), from the sources the user's AutoOrtho Dev runs, always from a home of its own
under the results (it writes its settings file at every start): the user's settings copied
(``--ao-settings mine``) or its own defaults (``--ao-settings default``), the user's cache. Either
starts before X-Plane, as the user starts it, and stops once X-Plane has quit. ``--cold``: nothing
built and nothing downloaded, the streamer's files in its home under the results (Live's settings
less its folder and SimBrief name; without AutoOrtho's region in X-Plane, Live given the one it
installed in the user's folder: ``cold_source``). ``--zooms 17/19``: the streamer's max_zoom and
max_zoom near airports for the series. The user's own files are only ever read.

``--streamer app`` (2026-10-04): the app itself, OrthoStudio XP and Live in its one window, as the
user opens it: Live's side shown and started as its Start does; ``--with-osxp``, OrthoStudio XP's
side open too, its engine started and its page loaded, idle, to see what it costs beside Live. Its
engine on a port of its own (APP_PORT), its log of its own, beside the usual one; on a Mac opened
by macOS from an app made for it (``app_bundle``), so that its web view's processes are counted as
its own. ``--cold`` there: its home of its own too (OrthoStudio XP's settings and library empty),
the zooms written in its Live settings. It quits as the installer of an update quits it.

Results: ``build/flights/<date time>/<n>-<scenario>/``: ``report.md``, the scenario's part of
X-Plane's ``Log.txt``, the test pilot's ``frames.csv`` and ``events.txt``, ``screenshots/``, the
computer's ``system.csv`` (``sampler.py``); and the series' ``summary.md``.

On a Mac and on Windows (a user's Shadow PC, 2026-10-01); on Linux too, its sampler reading only
``ps``. On Windows and Linux the test pilot is the one built ahead
(``native/xplane_test_pilot/prebuilt``). On Windows Live is stopped with a Ctrl-Break to its own
process group, which Live takes as a Ctrl-C (a Ctrl-C cannot be sent to a group there); and the
runner waits itself for X-Plane to quit and for Live to stop, where a Mac's leaves that to a
process of its own: on Windows that one could send Live no Ctrl-Break, nor outlive the console it
was started from. AutoOrtho's runs: on a Mac only. Flown from its start to its end with a stand-in
X-Plane on Windows and Linux (``tests/test_live_flights_series.py``).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pilot_build  # noqa: E402
import report  # noqa: E402
import sampler  # noqa: E402
import scenario as scenarios  # noqa: E402

REPO = HERE.parents[2]
WINDOWS = sys.platform == "win32"
PILOT_NAME = pilot_build.NAME
PILOT_SOURCE = pilot_build.SOURCE
PILOT_BUILD = pilot_build.BUILD_DIR / "mac_x64"
RESULTS = REPO / "build" / "flights"
CAMERAS = "cameras"  # in the results: the user's camera recorded for each scenario
CAMPAIGNS = HERE / "campaigns"
READY = f"{report.PILOT}ready"
START_TIMEOUT_S = 20 * 60  # X-Plane's start, and Live's plugin holding it while the ortho is built
APPEAR_TIMEOUT_S = 120  # X-Plane's process there after it was launched
SLACK_S = 10 * 60  # beyond the path's own time: X-Plane's clock runs slow under 20 frames a second
LIVE_READY_TIMEOUT_S = 20 * 60  # Live's first setup of a cold folder clones the region's files
LIVE_READY = "X-Plane texture quality"  # the last line of Live's preparation (session.prepare)
LOG_LATE_S = 90  # X-Plane writes its log late: the test pilot's "done" in the file within this
AO_SOURCES = Path.home() / "Desktop" / "Dev" / "autoortho4xplane" / "autoortho"  # AutoOrtho Dev's
AO_PYTHON = Path.home() / ".autoortho-dev-venv" / "bin" / "python"
AO_READY_TIMEOUT_S = 5 * 60
STOP_TIMEOUT_S = 120  # a streamer given that long to stop after its Ctrl-C
XPLANE_UDP = ("127.0.0.1", 49000)  # X-Plane's UDP port, as Live's stream --port's default
QUIT_TRIES_S = 180  # X-Plane asked to quit over UDP this long, then left to the user
POLL_S = 2.0
# Windows: a process group of its own (CREATE_NEW_PROCESS_GROUP), as a Mac's new session
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
# a cold run's home for Live, under the series' results: ".noindex", so that Spotlight does not
# index the hundreds of thousands of files Live makes there during a flight (2026-10-01)
LIVE_HOME = "live-home.noindex"
TELEMETRY = Path("Output") / "diagnostic reports"  # X-Plane's telemetry_0.tlm, written as it quits
# the app's engine in a test flight (--streamer app): neither the usual app's port (8641) nor the
# preview's (8651), whose OrthoStudio XP it would ask to stop as it starts (desktop.ENGINE_PORT)
APP_PORT = 8671
APP_UP_TIMEOUT_S = 5 * 60  # the app's window up, each side loaded, OrthoStudio XP's engine started
APP_NAME = "OrthoStudio XP (vols de test)"  # the Mac's app the runner opens (app_bundle)
LIVE_PORT = 8642  # Live's window's, the app's too (orthostudio.live.app_start.LIVE_PORT)
LIVE_API = f"http://127.0.0.1:{LIVE_PORT}/api/live/"


class RefusedError(Exception):
    """The series cannot run; the message says why."""


def say(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


def box(xp: Path) -> Path:
    """The test pilot's folder in X-Plane: the scenario sent, its answers."""
    return xp / "Output" / "osxp_test_pilot"


# ---- the test pilot plugin -------------------------------------------------------------------


def build_pilot(xp: Path) -> Path:
    """The plugin, compiled again when its source is newer (clang, against X-Plane's XPLM); on
    Windows and Linux the one built ahead (tools/live/plugin_builds.py)."""
    if sys.platform != "darwin":
        ahead = pilot_build.xpl(pilot_build.PREBUILT_DIR)
        if not ahead.is_file():
            raise RefusedError(f"no test pilot built for this system ({ahead})")
        return ahead
    out = PILOT_BUILD / f"{PILOT_NAME}.xpl"
    if out.exists() and out.stat().st_mtime >= PILOT_SOURCE.stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    cmd = [
        "clang", "-O2", "-Wall", "-Wextra", "-arch", "arm64", "-arch", "x86_64",
        "-mmacosx-version-min=11.0", "-dynamiclib", "-fvisibility=hidden",
        f"-Wl,-install_name,@loader_path/{PILOT_NAME}.xpl",
        "-F", str(xp / "Resources" / "plugins"), "-framework", "XPLM",
        "-o", str(tmp), str(PILOT_SOURCE),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RefusedError(f"the test pilot did not build:\n{done.stderr.strip()}")
    os.replace(tmp, out)
    return out


def install_pilot(xp: Path) -> bool:
    """The plugin in X-Plane's plugins, as a new file (never written over the one a running X-Plane
    has loaded); True when it changed."""
    src = build_pilot(xp)
    dest = pilot_build.xpl(xp / "Resources" / "plugins" / PILOT_NAME)
    if dest.is_file() and dest.read_bytes() == src.read_bytes():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    return True


# ---- X-Plane ----------------------------------------------------------------------------------


def xplane_up() -> bool:
    from orthostudio.live.scenery import xplane_running

    return xplane_running()


def xplane_gone() -> bool:
    """X-Plane gone, asked three times over a few seconds and checked with ``ps``: the process
    list can fail to answer under memory pressure while X-Plane still runs."""
    for _ in range(3):
        if xplane_up():
            return False
        time.sleep(1.0)
    if sys.platform != "win32":
        ps = subprocess.run(["ps", "-axo", "comm"], capture_output=True, text=True)
        if ps.returncode == 0 and any(
            Path(line.strip()).name in ("X-Plane", "X-Plane-x86_64")
            for line in ps.stdout.splitlines()
        ):
            return False
    return True


def log_text(xp: Path) -> str:
    try:
        return (xp / "Log.txt").read_text(errors="replace")
    except OSError:
        return ""


def log_size(xp: Path) -> int:
    try:
        return (xp / "Log.txt").stat().st_size
    except OSError:
        return 0


def launch(xp: Path, sc: scenarios.Scenario) -> list[str]:
    """X-Plane, straight into a flight in the air at the scenario's start (the altitude in feet)."""
    s = sc.start
    args = [
        "--pref:_show_qfl_on_start=0",  # no main menu
        "--no_save_prefs",  # the user's preferences never written
        "--no_modal_alerts",
        f"--load_acf={sc.aircraft}",
        f"--load_llah={s.lat:.6f},{s.lon:.6f},{s.feet:.0f},{s.heading:.0f}",
        "--start_running",
    ]
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", str(xp / "X-Plane.app"), "--args", *args], check=True)
    else:
        exe = xp / ("X-Plane.exe" if WINDOWS else "X-Plane-x86_64")
        if not exe.is_file():
            raise RefusedError(f"no {exe.name} in {xp}")
        subprocess.Popen(
            [str(exe), *args],
            cwd=xp,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=not WINDOWS,
            creationflags=NEW_GROUP,  # a Ctrl-C in the runner's console never reaches it
        )
    return args


# ---- Live ---------------------------------------------------------------------------------------


def live_runs() -> bool:
    """Whether a Live (the window's, or a stream) holds its plugin's port."""
    from orthostudio.live.gate import GATE_PORT

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", GATE_PORT))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def cold_home(home: Path) -> Path:
    """A folder for Live of its own: the user's Live settings, less its folder and SimBrief name
    (Live's defaults where it has none)."""
    from orthostudio.live import settings_file

    home.mkdir(parents=True, exist_ok=True)
    try:
        text = settings_file().read_text(encoding="utf-8")
    except FileNotFoundError:
        text = "enabled = true\n"
    text = re.sub(r"(?m)^folder\s*=.*$", 'folder = ""', text)
    text = re.sub(r"(?m)^(simbrief(?:_user)?)\s*=.*$", r'\1 = ""', text)
    (home / "live.toml").write_text(text, encoding="utf-8")
    cold_lists(home)
    return home


def cold_lists(home: Path) -> None:
    """The lists of the regions' textures the user's Live keeps (``aoregions.lists_dir``) into the
    cold home's Live folder: a region's list is made as it installs, so a user's start reads it in
    a second, where the cold folder, without it, read the region's 400,000 files again, minutes on
    a Shadow PC (2026-10-04). A list holds the region's names alone: nothing built, nothing
    downloaded, the run as cold as before."""
    from orthostudio.live import aoregions
    from orthostudio.live.pack import live_root

    mine = aoregions.lists_dir(live_root())
    theirs = aoregions.lists_dir(home / "live")
    with contextlib.suppress(OSError):
        for kept in sorted(mine.glob("*.json")):
            theirs.mkdir(parents=True, exist_ok=True)
            shutil.copy2(kept, theirs / kept.name)


def cold_source(xp: Path, series: list[scenarios.Scenario]) -> Path | None:
    """The region a cold Live streams onto when X-Plane holds no AutoOrtho region of its own for
    the scenarios: the one Live installed in the user's own folder, which the cold folder does not
    hold (a Shadow PC without AutoOrtho, 2026-10-01). None where X-Plane holds AutoOrtho's, found
    there as always (the developer's Mac), or where the scenarios start over no region's mesh."""
    from orthostudio.live import aoregions
    from orthostudio.live.pack import live_root
    from orthostudio.live.scenery import autoortho_regions

    wanted = {aoregions.region_of(sc.start.lat, sc.start.lon) for sc in series} - {None}
    theirs = {p.name.removeprefix("z_ao_") for p in autoortho_regions(xp)}
    missing = sorted(r for r in wanted if r not in theirs)
    if not missing:
        return None
    if len(missing) > 1:  # Live streams onto one region it was given
        raise RefusedError(
            f"a cold series over several regions ({', '.join(missing)}): one region a series"
        )
    mine = aoregions.mesh_dir(live_root(), missing[0])
    if not (mine / "terrain").is_dir():
        raise RefusedError(
            f"the region {missing[0]} is not installed: install it in the Live window first"
        )
    return mine


def start_live(
    xp: Path,
    out: Path,
    cold: bool,
    zooms: tuple[int, int] | None = None,
    frozen: bool = False,
    source: Path | None = None,
) -> subprocess.Popen:
    """Live's streamer, before X-Plane, as the user starts it; back once it is ready. ``frozen``:
    Live builds nothing once the aircraft flies, a test of 2026-10-01
    (orthostudio.live.streamer.FROZEN_IN_FLIGHT). ``source``: the region it streams onto
    (``cold_source``), else those it finds itself."""
    env = dict(os.environ)
    if cold:
        env["OSXP_HOME"] = str(cold_home(out / LIVE_HOME))
    if frozen:
        env["OSXP_LIVE_FROZEN_IN_FLIGHT"] = "1"
    cmd = [sys.executable, "-m", "orthostudio.live", "stream", "--xplane", str(xp)]
    if zooms:
        cmd += ["--max-zoom", str(zooms[0]), "--max-zoom-airports", str(zooms[1])]
    if source is not None:
        cmd += ["--source", str(source)]
    log = out / "live.txt"
    with log.open("ab") as f:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO,
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=not WINDOWS,  # it outlives the runner: its pack out after X-Plane
            creationflags=NEW_GROUP,  # Windows: stopped by a Ctrl-Break to its group (stop_tree)
        )
    say(f"Live started{' (cold: a folder of its own)' if cold else ''}; its output in {log}")
    began = time.time()
    while time.time() - began < LIVE_READY_TIMEOUT_S:
        time.sleep(POLL_S)
        text = log.read_text(errors="replace")
        if LIVE_READY in text:
            return proc
        if proc.poll() is not None:
            tail = "\n".join(text.splitlines()[-15:])
            raise RefusedError(f"Live stopped before it was ready:\n{tail}")
    quit_in_background(proc, out if cold else None, xp)
    raise RefusedError("Live not ready after 20 min")


# ---- the app itself, in its one window (2026-10-04) ------------------------------------------


def app_bundle() -> Path:
    """A Mac's app that opens this checkout's OrthoStudio XP, made for the runner: opened by macOS
    (``open -n``), the app is a program of its own, and the processes of its web view, which the
    system starts, are counted as its (``sampler.responsible``); started under the runner, they
    would be the runner's."""
    import plistlib

    app = REPO / "build" / "flights" / "app" / f"{APP_NAME}.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    script = macos / "osxp-test-flights"
    script.write_text(
        "#!/bin/bash\n"
        f"cd {shlex.quote(str(REPO))} || exit 1\n"
        f"exec {shlex.quote(sys.executable)} -m orthostudio.desktop\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "org.orthostudio-xp.launcher.test-flights",
        "CFBundleExecutable": script.name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1",
        "LSMinimumSystemVersion": "12.0",
        "LSArchitecturePriority": ["arm64"],  # a script launcher: not under Rosetta
        "LSRequiresNativeExecution": True,
        "NSHighResolutionCapable": True,
    }
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    return app


def app_settings(home: Path, zooms: tuple[int, int] | None) -> None:
    """A cold app's Live settings (``cold_home``'s copy of the user's): Live switched on, and the
    series' zooms, which the app takes from its settings (it has no ``--max-zoom``)."""
    import tomllib

    from orthostudio.config.store import toml_dumps

    path = home / "live.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    data["enabled"] = True
    if zooms:
        data.setdefault("stream", {}).update(max_zoom=zooms[0], max_zoom_airports=zooms[1])
    path.write_text(toml_dumps(data), encoding="utf-8")


def _http(url: str, body: dict | None = None, timeout_s: float = 5.0) -> dict | None:
    """The app's JSON answer, to a GET or a POST of ``body``; None when nothing answers."""
    import urllib.request

    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="GET" if data is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as answer:
            return json.load(answer)
    except (OSError, ValueError):
        return None


def _port_open(port: int) -> bool:
    with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.3):
        return True
    return False


def _alive(pid: int) -> bool:
    """Whether the process ``pid`` runs."""
    if WINDOWS:
        return any(p == pid for p, *_ in sampler.processes())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class App:
    """The app in a test flight (``--streamer app``): the program that shows its one window, Live
    set up in it, OrthoStudio XP's side too with ``--with-osxp``."""

    pid: int  # the root of what is counted as the app's: the program, or a venv's launcher above it
    window: int  # the program that shows the window, whose web view is the app's (a Mac's)
    home: Path  # its OSXP_HOME
    log: Path  # its own log, where serve.log is kept, under a name of its own
    live_log: Path  # Live's LogLive.txt
    engine_pid: int | None  # OrthoStudio XP's engine, with --with-osxp


def _app_said(said: Path) -> str:
    try:
        return "\n".join(said.read_text(errors="replace").splitlines()[-15:])
    except OSError:
        return ""


def _wait_until(ready: object, what: str, timeout_s: float, check: object = None) -> None:
    """Until ``ready()``; ``check()`` may refuse meanwhile (the app gone)."""
    began = time.time()
    while time.time() - began < timeout_s:
        if ready():  # type: ignore[operator]
            return
        if check is not None:
            check()  # type: ignore[operator]
        time.sleep(POLL_S)
    raise RefusedError(f"{what} not there after {timeout_s / 60:.0f} min")


def start_app(
    xp: Path,
    out: Path,
    cold: bool,
    zooms: tuple[int, int] | None = None,
    frozen: bool = False,
    source: Path | None = None,
    with_osxp: bool = False,
) -> App:
    """The app itself, before X-Plane, as the user opens it: its one window on Live's side, Live
    set up behind it, or with ``with_osxp`` OrthoStudio XP's side first (its engine started, its
    page loaded), then Live's; then Live started as its Start starts it; back once Live is ready
    for X-Plane. ``cold``: a home of its own under the results (``cold_home``, ``app_settings``).
    ``source``: the region Live streams onto (``cold_source``), as ``stream --source``."""
    from orthostudio import desktop
    from orthostudio.home import osxp_home

    out = out.resolve()  # Live refuses a folder that is not a full path
    if _port_open(LIVE_PORT) or _port_open(APP_PORT):
        # another Live would answer for the run's: the user's own app, the preview
        raise RefusedError(
            f"Live or the app runs already (port {LIVE_PORT} or {APP_PORT}): quit it, the series "
            "opens the app itself"
        )
    home = cold_home(out / LIVE_HOME) if cold else osxp_home()
    passed = {
        "OSXP_START": "build" if with_osxp else "live",
        "OSXP_PORT": str(APP_PORT),
        "OSXP_LOG_NAME": f"serve-test-flight-{out.name.replace(' ', '-')}.log",
        "PYTHONUNBUFFERED": "1",  # its output in app.txt as it comes, read while it runs
    }
    if cold:
        app_settings(home, zooms)
        passed["OSXP_HOME"] = str(home)
    if frozen:
        passed["OSXP_LIVE_FROZEN_IN_FLIGHT"] = "1"
    if source is not None:
        from orthostudio.live.control import SOURCE_ENV

        passed[SOURCE_ENV] = str(source)
    listening = home / "run" / f"osxp-window-{APP_PORT}.json"  # app_start.osxp_window_file
    with contextlib.suppress(OSError):
        listening.unlink()  # an earlier app's, left behind
    said = out / "app.txt"
    proc: subprocess.Popen | None = None
    if sys.platform == "darwin":
        options = [x for k, v in passed.items() for x in ("--env", f"{k}={v}")]
        cmd = ["open", "-n", "-a", str(app_bundle()), *options, "--stdout", str(said)]
        subprocess.run([*cmd, "--stderr", str(said)], check=True, timeout=60, capture_output=True)
    else:
        env = {k: v for k, v in os.environ.items() if k != "OSXP_FRAME"} | passed
        with said.open("ab") as f:
            proc = subprocess.Popen(
                [sys.executable, "-m", "orthostudio.desktop"],
                cwd=REPO,
                env=env,
                stdout=f,
                stderr=subprocess.STDOUT,
                creationflags=NEW_GROUP,
            )
    sides = "OrthoStudio XP, then Live" if with_osxp else "Live"
    own = " (cold: a home of its own)" if cold else ""
    say(f"the app opening on {sides}{own}; its output in {said}")

    def gone() -> None:
        if proc is not None and proc.poll() is not None:
            raise RefusedError(f"the app stopped before it was ready:\n{_app_said(said)}")

    window, engine_pid = 0, None
    try:
        _wait_until(listening.is_file, "the app's window", APP_UP_TIMEOUT_S, gone)
        shown = json.loads(listening.read_text(encoding="utf-8"))
        window = int(shown["pid"])
        if with_osxp:
            _wait_until(
                lambda: _http(f"http://127.0.0.1:{APP_PORT}/api/status") is not None,
                "OrthoStudio XP's engine",
                APP_UP_TIMEOUT_S,
                gone,
            )
            engine = (_http(f"http://127.0.0.1:{APP_PORT}/api/engine") or {}).get("engine") or {}
            engine_pid = int(engine["pid"]) if engine.get("pid") else None
            with socket.create_connection(("127.0.0.1", int(shown["port"])), timeout=5.0) as w:
                w.settimeout(30.0)
                w.sendall(b"live\n")  # Live's side shown, as its button shows it (app_start)
                w.recv(8)
        _wait_until(
            lambda: (_http(LIVE_API + "status") or {}).get("state") == "idle",
            "Live, set up in the app",
            APP_UP_TIMEOUT_S,
            gone,
        )
        if _http(LIVE_API + "start", {}) is None:
            raise RefusedError(f"Live did not start:\n{_app_said(said)}")
        began = time.time()
        while time.time() - began < LIVE_READY_TIMEOUT_S:
            status = _http(LIVE_API + "status") or {}
            if status.get("state") == "streaming":
                break
            if status.get("error"):
                raise RefusedError(f"Live stopped before it was ready: {status['error']}")
            gone()
            time.sleep(POLL_S)
        else:
            raise RefusedError("Live not ready after 20 min")
    except BaseException:
        # an app left open holds Live's port and its engine's: no run after it could start
        with contextlib.suppress(Exception):
            stop_app(proc.pid if proc is not None else window, timeout_s=60.0)
        raise
    if cold:
        live_log = home / "live" / "LogLive.txt"
    else:
        from orthostudio.live.logfile import log_path as live_log_path

        live_log = live_log_path()
    if sys.platform.startswith("linux"):
        log = home / "log" / passed["OSXP_LOG_NAME"]
    else:
        log = desktop.log_path().with_name(passed["OSXP_LOG_NAME"])
    engine_said = f", engine {engine_pid}" if engine_pid else ""
    say(f"Live is ready in the app (program {window}{engine_said})")
    return App(proc.pid if proc else window, window, home, log, live_log, engine_pid)


def stop_app(app_pid: int, timeout_s: float = STOP_TIMEOUT_S) -> None:
    """The app quit as the installer of an update quits it (``tools/package/stop_running.pas``):
    its engine asked to stop, then Live, which takes its files out of X-Plane, and its window goes
    by itself (``orthostudio.live.frame``, ``Frame.gone``); a Ctrl-C and a SIGTERM (``stop_tree``)
    if it is still there ``timeout_s`` later. Only once X-Plane has quit."""
    _http(f"http://127.0.0.1:{APP_PORT}/api/quit", {"force": True})
    _http(LIVE_API + "quit", {"force": True})
    if app_pid <= 0:  # the program not known: asked to quit, nothing more
        return
    began = time.time()
    while time.time() - began < timeout_s:
        if not _alive(app_pid):
            return
        time.sleep(1.0)
    stop_tree(app_pid, 30.0)


def live_folder(out: Path, cold: bool) -> Path:
    from orthostudio.live.pack import live_root

    return out / LIVE_HOME / "live" if cold else live_root()


def autoortho_runs() -> bool:
    """AutoOrtho's port taken, or one of its regions mounted (a Mac's ``mount``)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", 5847)) == 0:
            return True
    finally:
        sock.close()
    if WINDOWS:
        return False
    return "z_ao_" in subprocess.run(["mount"], capture_output=True, text=True).stdout


AO_KEEP = ("xplane_path", "scenery_path")  # the user's, whatever the settings: where X-Plane is


def _ao_value(text: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{key}\s*=[ \t]*(.*)$", text)
    return m[1].strip() if m else None


def autoortho_home(home: Path, settings: str, cold: bool, zooms: tuple[int, int] | None) -> Path:
    """A home for AutoOrtho of its own, since it writes its settings file at every start: the
    user's own (``~/.autoortho``, only read) or AutoOrtho's defaults (``settings`` "mine" or
    "default"), with the user's paths to X-Plane; ``zooms`` its max_zoom and near airports; its
    cache in this home when ``cold``, else the user's. Its log is always this home's."""
    user = (Path.home() / ".autoortho").read_text(encoding="utf-8")
    values = {key: _ao_value(user, key) for key in AO_KEEP}
    if not all(values.values()):
        raise RefusedError("~/.autoortho does not say where X-Plane is: set AutoOrtho up first")
    data = home / ".autoortho-data"
    if cold:
        values["cache_dir"] = str(data / "cache")
        values["dsf_dir"] = str(data / "dsf")
    values["log_file"] = str(data / "logs" / "autoortho.log")
    values["download_dir"] = str(data / "downloads")
    values["showconfig"] = "False"
    if zooms:
        values["max_zoom"], values["max_zoom_near_airports"] = str(zooms[0]), str(zooms[1])
    if settings == "mine":
        text = user
        for key, value in values.items():
            text, n = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", text)
            if n == 0 and key in ("cache_dir", "max_zoom", "max_zoom_near_airports"):
                raise RefusedError(
                    f"~/.autoortho has no {key}: its settings changed, update fly.py"
                )
    else:  # AutoOrtho's defaults for everything else: it fills them in itself
        sections = {
            "general": ["showconfig"],
            "paths": [
                "xplane_path",
                "scenery_path",
                "cache_dir",
                "dsf_dir",
                "log_file",
                "download_dir",
            ],
            "autoortho": ["max_zoom", "max_zoom_near_airports"],
        }
        lines = []
        for section, keys in sections.items():
            lines.append(f"[{section}]")
            lines += [f"{k} = {values[k]}" for k in keys if k in values]
            lines.append("")
        text = "\n".join(lines)
    for folder in ("cache", "dsf", "logs", "downloads"):
        (data / folder).mkdir(parents=True, exist_ok=True)
    (home / ".autoortho").write_text(text, encoding="utf-8")
    return home


def autoortho_folder(home: Path) -> Path:
    """Where the AutoOrtho of ``home`` keeps its cache."""
    value = _ao_value((home / ".autoortho").read_text(encoding="utf-8"), "cache_dir")
    return Path(value) if value else home / ".autoortho-data" / "cache"


def start_autoortho(
    xp: Path, home: Path, out: Path, settings: str, cold: bool, zooms: tuple[int, int] | None
) -> subprocess.Popen:
    """AutoOrtho without its window, before X-Plane, from ``home`` (``autoortho_home``); back once
    its regions are mounted."""
    from orthostudio.live.scenery import autoortho_mounted

    if not (AO_PYTHON.is_file() and (AO_SOURCES / "__main__.py").is_file()):
        raise RefusedError(f"AutoOrtho's sources or Python not found ({AO_SOURCES}, {AO_PYTHON})")
    env = dict(os.environ)
    env["HOME"] = str(autoortho_home(home, settings, cold, zooms))
    log = out / "autoortho.txt"
    with log.open("ab") as f:
        proc = subprocess.Popen(
            [str(AO_PYTHON), "__main__.py", "-H"],
            cwd=AO_SOURCES,
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    what = f"{'its defaults' if settings == 'default' else 'your settings'}" + (
        f", ZL{zooms[0]}/{zooms[1]}" if zooms else ""
    )
    say(f"AutoOrtho started ({what}{', cold' if cold else ''}); its output in {log}")
    began = time.time()
    while time.time() - began < AO_READY_TIMEOUT_S:
        time.sleep(POLL_S)
        if autoortho_mounted(xp):
            return proc
        if proc.poll() is not None:
            tail = "\n".join(log.read_text(errors="replace").splitlines()[-15:])
            raise RefusedError(f"AutoOrtho stopped before it mounted its regions:\n{tail}")
    quit_in_background(proc, out if cold else None, xp)
    raise RefusedError("AutoOrtho's regions not mounted after 5 min")


def process_tree(pid: int) -> list[tuple[int, str]]:
    """``pid`` and every process under it, with its command, whatever its process group:
    AutoOrtho's mount worker has a group of its own, which a Ctrl-C to AutoOrtho's group did not
    reach (2026-09-29: its region stayed mounted after the flight)."""
    rows = sampler.processes()
    command = {p: c for p, _, _, _, c in rows}
    children: dict[int, list[int]] = {}
    for p, parent, *_ in rows:
        children.setdefault(parent, []).append(p)
    out: list[tuple[int, str]] = []
    todo = [pid]
    while todo:
        p = todo.pop()
        if p in command and all(p != q for q, _ in out):
            out.append((p, command[p]))
            todo += children.get(p, [])
    return out


def _still(pid: int, command: str) -> bool:
    """The same process still there (its number not given to another since)."""
    if WINDOWS:
        return any(p == pid and c == command for p, _, _, _, c in sampler.processes())
    now = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True)
    return now.returncode == 0 and now.stdout.strip() == command


def stop_tree(pid: int, timeout_s: float = STOP_TIMEOUT_S) -> None:
    """Ctrl-C to the streamer ``pid`` and to every process under it, each group once, as in its
    terminal (it takes its files out of X-Plane); SIGTERM to those still there ``timeout_s`` later.
    Only once X-Plane has quit: X-Plane reads its textures through it."""
    tree = process_tree(pid)
    if WINDOWS:  # its group's Ctrl-Break, which Live takes as a Ctrl-C (the module's docstring)
        with contextlib.suppress(OSError):
            os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    groups: list[int] = []
    for p, _ in tree if not WINDOWS else ():
        with contextlib.suppress(OSError):
            group = os.getpgid(p)
            if group not in groups:
                groups.append(group)
    for group in groups:
        with contextlib.suppress(OSError):
            os.killpg(group, signal.SIGINT)
    began = time.time()
    while time.time() - began < timeout_s:
        if not any(_still(p, c) for p, c in tree):
            return
        time.sleep(1.0)
    for p, c in tree:
        if _still(p, c):
            with contextlib.suppress(OSError):
                os.kill(p, signal.SIGTERM)


def quit_xplane_udp() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(b"CMND\x00sim/operation/quit\x00", XPLANE_UDP)
    finally:
        sock.close()


def keep_record(out: Path) -> None:
    """The user's own Live's record of what it built, as it stands after the flight, into the
    series' folder: the window's map of the flight (``benchmap.py``) reads it later."""
    from orthostudio.live.pack import default_pack_dir

    record = default_pack_dir() / "_live" / "ortho.json"
    with contextlib.suppress(OSError):
        shutil.copy2(record, out / "live-built.json")


def clean_cold(out: Path, xp: Path | None = None) -> None:
    """A cold run's heavy files deleted once its streamer has stopped: Live's own folder there (its
    pack, the textures it built, its downloaded imagery), AutoOrtho's cache and DSFs. A cold
    AutoOrtho run of 2 min left 3.1 GB, and the disk had 77 GB free (2026-09-29). What stays is
    small: the settings, the logs, and Live's record of what it built, as ``live-built.json`` in the
    series' folder, for its map. Only the homes the runner made under ``out``; X-Plane's link to a
    pack there taken out first, if the streamer left it."""
    out = out.resolve()
    record = out / LIVE_HOME / "live" / "pack" / "_live" / "ortho.json"
    if record.is_file():
        shutil.copy2(record, out / "live-built.json")
    log = out / LIVE_HOME / "live" / "LogLive.txt"  # Live's whole log, the start's included
    with contextlib.suppress(OSError):
        if log.is_file():
            shutil.copy2(log, out / "LogLive.txt")
    if xp is not None:
        from orthostudio.live.filesystem import is_folder_link, link_target, remove_folder_link
        from orthostudio.live.pack import LINK_NAME, OVERLAY_LINK_NAME

        for name in (LINK_NAME, OVERLAY_LINK_NAME):
            link = xp / "Custom Scenery" / name  # a symbolic link, or on Windows a junction
            with contextlib.suppress(OSError):
                if is_folder_link(link) and out in link_target(link).resolve().parents:
                    remove_folder_link(link)
    data = out / "ao-home" / ".autoortho-data"
    for heavy in (out / LIVE_HOME / "live", data / "cache", data / "dsf"):
        if heavy.is_dir() and out in heavy.resolve().parents:
            shutil.rmtree(heavy, ignore_errors=True)


def keep_telemetry(xp: Path, series: Path) -> Path | None:
    """X-Plane's telemetry of the series (``telemetry_0.tlm``, written as it quits: its texture
    pager's counts, its GPU's memory) into the series' folder, when written since the series
    began; None otherwise."""
    try:
        began = datetime.strptime(series.name, "%Y-%m-%d %H%M%S").timestamp()
        src = xp / TELEMETRY / "telemetry_0.tlm"
        if src.stat().st_mtime < began:
            return None
        dest = series / "telemetry.tlm"
        shutil.copy2(src, dest)
    except (OSError, ValueError):
        return None
    return dest


def after_series(
    pid: int,
    clean: Path | None = None,
    xp: Path | None = None,
    series: Path | None = None,
    app_log: Path | None = None,
) -> None:
    """In a process of its own, left running by the runner. X-Plane asks "Quit the simulator?" at
    the test pilot's quit and waits for a click, plugins stopped meanwhile; a second quit over UDP
    is its answer, and X-Plane shuts down at once, cleanly (2026-09-29). So: quit over UDP every
    2 s until X-Plane is gone (for QUIT_TRIES_S, then left to the user). Then the streamer ``pid``,
    if any, gets its Ctrl-C (it takes its files out of X-Plane), and a SIGTERM if it is still there
    STOP_TIMEOUT_S later. Never before: X-Plane reads its textures through it."""
    time.sleep(3.0)  # the test pilot's own quit first
    began = time.time()
    while not xplane_gone():
        if time.time() - began < QUIT_TRIES_S:
            with contextlib.suppress(OSError):
                quit_xplane_udp()
        time.sleep(2.0)
    if xp is not None and series is not None:
        keep_telemetry(xp, series)
    if pid > 0 and app_log is not None:  # the app: quit as an update's installer quits it
        stop_app(pid)
    elif pid > 0:
        stop_tree(pid)
    if app_log is not None and series is not None:  # its own log, with the series
        with contextlib.suppress(OSError):
            shutil.move(str(app_log), str(series / "app-serve.log"))
    if clean is not None:
        clean_cold(clean, xp)


def quit_in_background(
    proc: subprocess.Popen | App | None,
    clean: Path | None = None,
    xp: Path | None = None,
    series: Path | None = None,
) -> None:
    """X-Plane quit, then its telemetry kept in ``series`` (its folder), the streamer stopped and a
    cold run's heavy files deleted (``clean``, its series' folder), by ``after_series`` in a
    process of its own; the streamer only, at once, when X-Plane is not running. On Windows the
    runner does it itself, and waits (the module's docstring)."""
    app_log = proc.log if isinstance(proc, App) else None
    if isinstance(proc, App):
        pid = proc.pid if _alive(proc.pid) else 0
    else:
        pid = proc.pid if proc is not None and proc.poll() is None else 0
    if WINDOWS:
        say("X-Plane quits, then Live stops: a few minutes at most")
        after_series(pid, clean, xp, series, app_log)
        return
    cmd = [sys.executable, str(Path(__file__).resolve()), "--after-series", str(pid)]
    if app_log is not None:
        cmd += ["--app-log", str(app_log)]
    if clean is not None:
        cmd += ["--clean", str(clean)]
    if series is not None:
        cmd += ["--series", str(series)]
    if xp is not None:
        cmd += ["--xplane", str(xp)]
    subprocess.Popen(
        cmd,
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# ---- one flight ---------------------------------------------------------------------------------


def clear_box(xp: Path) -> None:
    """The test pilot's files of an earlier flight (one the runner did not see to its end) set
    aside."""
    b = box(xp)
    b.mkdir(parents=True, exist_ok=True)
    old = [b / n for n in ("scenario.txt", "scenario.done", "events.txt", "frames.csv", "quit")]
    old = [p for p in old if p.exists()]
    if old:
        aside = b / "earlier"
        aside.mkdir(exist_ok=True)
        for p in old:
            # Windows: one still open in X-Plane (a flight not flown to its end) stays, and the
            # test pilot starts it again at the next scenario
            with contextlib.suppress(PermissionError):
                os.replace(p, aside / p.name)


def camera_file(results: Path, name: str) -> Path:
    """Where the user's camera recorded for the scenario ``name`` is kept."""
    return results / CAMERAS / f"{name}.csv"


def keep_camera(recorded: Path, results: Path, name: str) -> Path | None:
    """A camera just recorded kept as the scenario's (the one before set aside, its date in its
    name); None when it holds no pose."""
    try:
        lines = recorded.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 3:
        return None
    dest = camera_file(results, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        stamp = datetime.fromtimestamp(dest.stat().st_mtime).strftime("%Y-%m-%d %H%M%S")
        os.replace(dest, dest.with_name(f"{name} {stamp}.csv"))
    shutil.copy2(recorded, dest)
    return dest


def send(
    xp: Path, sc: scenarios.Scenario, record_camera: bool = False, track: Path | None = None
) -> None:
    b = box(xp)
    tmp = b / "scenario.tmp"
    tmp.write_text(sc.for_the_pilot(record_camera, track), encoding="utf-8")
    os.replace(tmp, b / "scenario.txt")


def last_path_s(frames: Path) -> float | None:
    try:
        with frames.open("rb") as f:
            f.seek(max(0, frames.stat().st_size - 400))
            tail = f.read().decode(errors="replace").splitlines()
        for line in reversed(tail):
            parts = line.split(",")
            if len(parts) > 2:
                return float(parts[1])
    except (OSError, ValueError):
        pass
    return None


def wait_flight(xp: Path, sc: scenarios.Scenario, sent_at: float) -> tuple[str, dict]:
    """Until the test pilot says "done"; what happened (``done``, or why not) and when."""
    b = box(xp)
    shown, flying_at, told, seen = 0, None, 0.0, False
    times: dict = {}
    while True:
        time.sleep(POLL_S)
        try:
            lines = (b / "events.txt").read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines[shown:]:
            say("  pilot: " + (line.split(" ", 1)[1] if " " in line else line))
        shown = max(shown, len(lines))
        now = time.time()
        if any(line.endswith(" done") for line in lines):
            times["flight_s"] = now - (flying_at or now)
            times["done_wall"] = now
            return "done", times
        if any("no start or no leg" in line for line in lines):
            return "the test pilot refused the scenario", times
        if flying_at is None and any("flying the path" in line for line in lines):
            flying_at = now
            times["to_flying_s"] = now - sent_at
            times["flying_wall"] = now
        if flying_at is None and now - sent_at > START_TIMEOUT_S:
            return f"the path not begun {START_TIMEOUT_S // 60} min after the scenario", times
        if flying_at is not None and now - flying_at > sc.duration + SLACK_S:
            return "the path not flown in time", times
        if flying_at is not None and now - told >= 60:
            told = now
            t = last_path_s(b / "frames.csv")
            if t is not None:
                say(f"  flying: {t:.0f} of {sc.duration:.0f} s")
        if not seen:  # just launched: its process comes a moment later
            seen = xplane_up()
            if not seen and now - sent_at > APPEAR_TIMEOUT_S:
                return "X-Plane did not start", times
        elif xplane_gone():
            return "X-Plane quit", times


def wait_log(xp: Path, log_from: int, line: str) -> bool:
    """Until X-Plane's Log.txt, from ``log_from``, holds ``line``: X-Plane keeps its own lines until
    a plugin writes one, then writes them all in order, so what came before is there too. False when
    it did not come in time."""
    began = time.time()
    while time.time() - began < LOG_LATE_S:
        try:
            with (xp / "Log.txt").open("rb") as f:
                f.seek(log_from)
                if line.encode() in f.read():
                    return True
        except OSError:
            pass
        time.sleep(POLL_S)
    return False


def take(src: Path, dest: Path) -> None:
    """``src`` moved to ``dest``; copied where X-Plane still has it open on Windows, which then
    refuses its move: the test pilot's files of a flight not flown to its end."""
    try:
        shutil.move(str(src), str(dest))
    except PermissionError:
        shutil.copyfile(src, dest)


def collect(
    xp: Path,
    folder: Path,
    log_from: int,
    shots_before: set[str],
    live_from: int,
    out: Path,
    live_log: Path | None = None,
) -> None:
    """The flight's files into its folder: the test pilot's, X-Plane's log and screenshots from the
    scenario on, and Live's output."""
    b = box(xp)
    for name, dest in (
        ("events.txt", "events.txt"),
        ("frames.csv", "frames.csv"),
        ("scenario.done", "scenario.txt"),
        ("scenario.txt", "scenario.txt"),  # not flown: never flown later either
        ("camera.csv", "camera.csv"),  # the user's camera, recorded
    ):
        if (b / name).exists():
            take(b / name, folder / dest)
    try:
        with (xp / "Log.txt").open("rb") as f:
            f.seek(log_from)
            (folder / "Log.txt").write_bytes(f.read())
    except OSError:
        pass
    shots = xp / "Output" / "screenshots"
    new = (
        [p for p in shots.glob("*") if p.is_file() and p.name not in shots_before]
        if shots.is_dir()
        else []
    )
    if new:
        (folder / "screenshots").mkdir(exist_ok=True)
        for p in new:
            take(p, folder / "screenshots" / p.name)
    sources = {"live.txt": live_log or out / "live.txt", "autoortho.txt": out / "autoortho.txt"}
    for name, source in sources.items():  # the app's Live writes its own log (LogLive.txt)
        if source.is_file():
            with source.open("rb") as f:
                f.seek(live_from)
                (folder / name).write_bytes(f.read())


def screenshots_now(xp: Path) -> set[str]:
    shots = xp / "Output" / "screenshots"
    return {p.name for p in shots.glob("*")} if shots.is_dir() else set()


def size_of(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---- the series ---------------------------------------------------------------------------------


def summary_lines(folder: Path) -> list[str]:
    text = (folder / "report.md").read_text()
    keep = [
        ln.strip()
        for ln in text.splitlines()
        if ln.startswith(
            ("- OUTCOME", "- streamer", "- frame time", "- over 50", "- **st", "- **pa")
        )
        or "OUTSIDE LIVE'S MODEL" in ln
        or "LOADED AGAIN" in ln
    ]
    # each load once: the report names it again in what X-Plane and the computer did during it
    loads = sum(1 for ln in text.splitlines() if ln.startswith("- **load ") and ", aircraft " in ln)
    return [f"- {loads} scenery loads during the path", *keep]


def run(a: argparse.Namespace) -> int:
    from orthostudio.live import session

    series = [scenarios.load(s) for s in a.scenarios]
    if WINDOWS and a.streamer == "autoortho":
        raise RefusedError("AutoOrtho's runs: on a Mac only for now")
    xp = session.xplane_dir(a.xplane)
    # a full path: a cold streamer's home is given to it, which refuses a folder that is not one
    out = (a.results / datetime.now().strftime("%Y-%m-%d %H%M%S")).resolve()
    out.mkdir(parents=True)
    a.out = out
    if a.streamer in ("live", "app"):
        # Windows: the user's Live folder, its region Live streams onto, out of Windows Search,
        # whose listing of their files kept half a core busy through the flights of 2026-10-02
        from orthostudio.live import winsearch
        from orthostudio.live.pack import live_root

        winsearch.keep_out(live_root(), say)
    up = xplane_up()
    if up and READY not in log_text(xp):
        raise RefusedError("X-Plane runs without the test pilot: quit it, the series starts it")
    running = {"live": live_runs(), "autoortho": autoortho_runs()}
    running["app"] = running["live"] or _port_open(LIVE_PORT) or _port_open(APP_PORT)
    proc: subprocess.Popen | App | None = None
    folder_of_streamer = None
    if a.streamer != "none" and up:
        if not running[a.streamer]:
            raise RefusedError(
                f"X-Plane runs without {a.streamer} (it reads the scenery packs at its start): "
                "quit X-Plane, the series starts both"
            )
        say(f"X-Plane and {a.streamer} run already: the series goes on with them")
    elif a.streamer != "none":
        if any(running.values()):
            raise RefusedError(
                "Live or AutoOrtho runs already (a window of theirs?): stop it, the series starts "
                "its own"
            )
        if a.streamer == "live":
            source = cold_source(xp, series) if a.cold else None
            proc = start_live(xp, out, a.cold, a.zooms, a.freeze_in_flight, source)
            folder_of_streamer = live_folder(out, a.cold)
        elif a.streamer == "app":
            source = cold_source(xp, series) if a.cold else None
            proc = start_app(
                xp, out, a.cold, a.zooms, a.freeze_in_flight, source, getattr(a, "with_osxp", False)
            )
            folder_of_streamer = live_folder(out, a.cold)
        else:
            home = out / "ao-home"
            proc = start_autoortho(xp, home, out, a.ao_settings, a.cold, a.zooms)
            folder_of_streamer = autoortho_folder(home)
    elif any(running.values()):
        say("note: Live or AutoOrtho runs, and X-Plane will show what it gives")
    disks = {"X-Plane": sampler.whole_disk(xp)}
    if folder_of_streamer is not None:
        disks["streamer"] = sampler.whole_disk(folder_of_streamer)
    if install_pilot(xp) and up:
        say("note: X-Plane keeps the test pilot it loaded at its start; the new one from its next")
    if not up:
        clear_box(xp)
    say(f"series of {len(series)} in {out}")
    results = []
    try:
        for n, sc in enumerate(series, 1):
            folder = out / f"{n}-{sc.name}"
            folder.mkdir()
            say(f"{n}/{len(series)} {sc.name}: {sc.duration / 60:.1f} min of path")
            log_from = log_size(xp) if up else 0
            shots_before = screenshots_now(xp)
            live_log = proc.live_log if isinstance(proc, App) else None
            live_from = size_of(
                live_log or out / ("autoortho.txt" if a.streamer == "autoortho" else "live.txt")
            )
            if up:
                clear_box(xp)
            if isinstance(proc, App):  # the app, its engine and its web view apart (sampler)
                recorder = sampler.Sampler(
                    folder / "system.csv",
                    proc.pid,
                    engine_pid=proc.engine_pid,
                    web_of=proc.window if sys.platform == "darwin" else proc.pid,
                )
            else:
                recorder = sampler.Sampler(folder / "system.csv", proc.pid if proc else None)
            recorder.start()
            recorder.first.wait(30)  # the computer before the scenario, then the scenario
            track = camera_file(a.results, sc.name) if a.camera == "mine" else None
            if track is not None and not track.is_file():
                say(f"  no camera of yours for {sc.name}: its own cameras")
                track = None
            send(xp, sc, a.record_camera, track)
            sent_at = time.time()
            started = "running already, the aircraft placed by the test pilot"
            if not up:
                launch(xp, sc)
                up = True
                started = f"started for the series, in the air at the start ({sc.aircraft})"
                say("X-Plane starting")
            outcome, times = wait_flight(xp, sc, sent_at)
            if outcome == "done" and not wait_log(xp, log_from, f"{report.PILOT}done"):
                say(f"  X-Plane's log still without the flight's end after {LOG_LATE_S} s")
            recorder.stop()
            collect(xp, folder, log_from, shots_before, live_from, out, live_log)
            if a.streamer in ("live", "app") and not a.cold:
                keep_record(out)
            if a.record_camera:
                kept = keep_camera(folder / "camera.csv", a.results, sc.name)
                say(
                    f"  your camera kept for {sc.name}: {kept}"
                    if kept
                    else f"  no camera recorded for {sc.name}"
                )
            info = {
                "scenario": sc.name,
                "streamer": a.streamer,
                "ao_settings": a.ao_settings if a.streamer == "autoortho" else None,
                "with_osxp": getattr(a, "with_osxp", False) if a.streamer == "app" else None,
                "zooms": list(a.zooms) if a.zooms else None,
                "cold": a.cold,
                "frozen_in_flight": a.freeze_in_flight,
                "camera": (
                    "recorded"
                    if a.record_camera
                    else f"yours ({datetime.fromtimestamp(track.stat().st_mtime):%Y-%m-%d %H:%M})"
                    if track is not None
                    else "the scenario's"
                ),
                "xplane_start": started,
                "outcome": outcome,
                "sent_wall": sent_at,
                "disks": disks,
                **times,
            }
            (folder / "run.json").write_text(json.dumps(info, indent=2))
            (folder / "report.md").write_text(report.report(folder))
            results.append((sc.name, folder, outcome))
            say(f"{sc.name}: {outcome}; report in {folder / 'report.md'}")
            if outcome != "done":
                break
    finally:
        lines = [f"# Series of {datetime.now():%Y-%m-%d %H:%M}", ""]
        for name, folder, outcome in results:
            lines += [f"## {name}: {outcome}", *summary_lines(folder), ""]
        (out / "summary.md").write_text("\n".join(lines))
        still_up = not xplane_gone()
        if still_up and not a.keep_open:
            (box(xp) / "quit").write_text("")
            say("X-Plane quits by itself, in the background")
            quit_in_background(proc, out if a.cold else None, xp, out)
        elif still_up:
            say("X-Plane stays up (--keep-open)")
        else:
            quit_in_background(proc, out if a.cold else None, xp, out)
        print((out / "summary.md").read_text())
    return (
        0
        if results and all(o == "done" for _, _, o in results) and len(results) == len(series)
        else 1
    )


def label(a: argparse.Namespace) -> str:
    """What a run flew with, in a few words."""
    names = {"none": "X-Plane alone", "live": "Live", "autoortho": "AutoOrtho"}
    parts = [names.get(a.streamer, "Live in the app")]
    if a.streamer == "autoortho":
        parts.append("its defaults" if a.ao_settings == "default" else "your settings")
    if a.streamer == "app":
        parts.append(
            "with OrthoStudio XP open" if getattr(a, "with_osxp", False) else "alone in it"
        )
    if a.zooms:
        parts.append(f"ZL{a.zooms[0]}/{a.zooms[1]}")
    if a.cold:
        parts.append("cold")
    if getattr(a, "freeze_in_flight", False):
        parts.append("frozen in flight")
    return ", ".join(parts)


@dataclass
class Pick:
    """A comparison chosen (or a run, in a campaign without comparisons)."""

    number: int  # its own, in the campaign, from 1
    ids: list[str]  # the runs it compares
    scenarios: list[str]  # the flights it is flown on


@dataclass
class Campaign:
    """A campaign file (``campaigns/<name>.txt``). Its lines: ``title WORDS`` for the window,
    ``scenarios NAME...``, ``run [ID] OPTIONS...`` (the runner's own options, a flight's setup),
    and ``compare ID ID...``: runs to read side by side. A run two comparisons share is flown
    once."""

    name: str
    title: str
    scenarios: list[str]
    runs: list[tuple[str, list[str]]]  # (its id, its options), in the file's order
    comparisons: list[list[str]]  # the ids compared, in the file's order

    def picked(
        self, picks: list[tuple[int, list[str] | None]] | None
    ) -> tuple[list[Pick], list[tuple[str, list[str], list[str]]]]:
        """The comparisons ``picks`` (from 1, each with its own flights or the campaign's; all when
        None), and the runs they need, each once, in the order first needed, with its options and
        the flights of every comparison that needs it: one X-Plane flies them in turn. Without
        comparisons, ``picks`` are runs."""
        groups = self.comparisons or [[i] for i, _ in self.runs]
        wanted = picks or [(k, None) for k in range(1, len(groups) + 1)]
        chosen = [Pick(k, groups[k - 1], list(names or self.scenarios)) for k, names in wanted]
        by_id = dict(self.runs)
        order: list[str] = []
        flights: dict[str, list[str]] = {}
        for pick in chosen:
            for i in pick.ids:
                if i not in order:
                    order.append(i)
                got = flights.setdefault(i, [])
                got += [name for name in pick.scenarios if name not in got]
        return chosen, [(i, by_id[i], flights[i]) for i in order]


def read_campaign(ref: str) -> Campaign:
    path = Path(ref)
    if not path.suffix and not path.exists():
        path = CAMPAIGNS / f"{ref}.txt"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in CAMPAIGNS.glob("*.txt")))
        raise RefusedError(f"no campaign {ref!r} (the campaigns: {known})")
    title, names, runs, comparisons = "", [], [], []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("title "):
            title = line[len("title ") :].split("#", 1)[0].strip()
            continue
        words = shlex.split(line.split("#", 1)[0])
        if not words:
            continue
        if words[0] == "scenarios":
            names += words[1:]
        elif words[0] == "run":
            rest = words[1:]
            run_id = rest.pop(0) if rest and not rest[0].startswith("--") else f"run{len(runs) + 1}"
            if any(run_id == i for i, _ in runs):
                raise RefusedError(f"{path.name}, line {number}: a second run {run_id!r}")
            runs.append((run_id, rest))
        elif words[0] == "compare":
            ids = {i for i, _ in runs}
            unknown = [w for w in words[1:] if w not in ids]
            if unknown or len(words) < 3:
                raise RefusedError(f"{path.name}, line {number}: compare two runs named before")
            comparisons.append(words[1:])
        else:
            raise RefusedError(f"{path.name}, line {number}: not title, scenarios, run or compare")
    if not names or not runs:
        raise RefusedError(f"{path.name}: a scenarios line and a run line at least")
    return Campaign(path.stem, title, names, runs, comparisons)


COOL_TIMEOUT_S = 15 * 60  # a Mac's heat back to nominal between two runs, this long at most


def wait_quiet(timeout_s: float = 600.0, cool_s: float = COOL_TIMEOUT_S) -> None:
    """Until X-Plane has quit and no streamer runs: the run before quits them in the background.
    Then, on a Mac, until its heat is nominal again (``cool_s`` at most): a run begun on the heat
    the one before left flew slower, and the comparison read the heat rather than the setup (the
    variants of 2026-10-01; the one window's, 2026-10-04)."""
    began = time.time()
    while time.time() - began < timeout_s:
        if xplane_gone() and not live_runs() and not autoortho_runs() and not _port_open(APP_PORT):
            break
        time.sleep(5.0)
    else:
        raise RefusedError("X-Plane or a streamer still runs 10 min after the run before")
    began = time.time()
    while (sampler.heat() or 0) > 0 and time.time() - began < cool_s:
        if time.time() - began < 5.0:
            say("the Mac cools down before the next run")
        time.sleep(5.0)


def picks_of(text: str | None) -> list[tuple[int, list[str] | None]] | None:
    """``1,3``: the campaign's comparisons (or runs) chosen, counted from 1, on the campaign's
    flights; ``1:toulouse-low,3:scandinavia-columns+scandinavia-rows``: each on its own."""
    if not text:
        return None
    picks: list[tuple[int, list[str] | None]] = []
    for item in (w.strip() for w in text.split(",")):
        if not item:
            continue
        number, _, names = item.partition(":")
        try:
            picks.append((int(number), [n for n in names.split("+") if n] or None))
        except ValueError:
            raise RefusedError(f"--pick {text!r}: e.g. 1,3 or 1:toulouse-low,3") from None
    return picks or None


def campaign(ref: str, parser: argparse.ArgumentParser, base: argparse.Namespace) -> int:
    """The runs the comparisons chosen need, each in turn and once, X-Plane started again for each
    (its streamer must be there before it); then ``<date time> <campaign>.md``: every run side by
    side, then each comparison's."""
    camp = read_campaign(ref)
    picks = picks_of(base.pick)
    count = len(camp.comparisons) or len(camp.runs)
    if picks and any(not 1 <= k <= count for k, _ in picks):
        raise RefusedError(f"--pick: from 1 to {count}")
    comps, runs = camp.picked(picks)
    for s in {name for _, _, names in runs for name in names}:
        scenarios.load(s)  # a wrong name said now, not after an hour of flights
    if xplane_up() or live_runs() or autoortho_runs():
        raise RefusedError("X-Plane, Live or AutoOrtho runs: quit them, the campaign starts each")
    extra = ["--results", str(base.results)] + (
        ["--xplane", str(base.xplane)] if base.xplane else []
    )
    if base.camera == "mine":
        extra += ["--camera", "mine"]  # each run replays the user's camera, where recorded
    done: dict[str, tuple[str, Path]] = {}  # run id: (what ran, its series' folder)
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    table = base.results / f"{stamp} {camp.name}.md"
    code = 0
    for k, (run_id, options, flights) in enumerate(runs, 1):
        a = parser.parse_args([*options, *extra, *flights])
        wrong = refused_options(a)
        if wrong:
            raise RefusedError(f"run {run_id}: {wrong}")
        if k > 1:
            say("waiting for X-Plane and the streamer of the run before to quit")
            wait_quiet()
        say(f"campaign {camp.name}, run {k} of {len(runs)}: {label(a)}")
        try:
            code = max(code, run(a))
        except RefusedError as e:  # this run could not fly: the next ones still can
            say(f"run {run_id} refused: {e}")
            code = max(code, 1)
            if getattr(a, "out", None) is None:
                continue
        done[run_id] = (label(a), a.out)
        base.results.mkdir(parents=True, exist_ok=True)
        text = f"# {camp.title or camp.name}, {stamp}\n\n"
        each = comps if camp.comparisons else []
        if len(each) != 1:  # one comparison is the whole campaign: its table alone
            text += report.campaign_table(list(done.values()))
        for pick in each:
            if all(i in done for i in pick.ids):
                names = " against ".join(done[i][0] for i in pick.ids)
                text += f"\n# Comparison {pick.number}: {names}\n\n"
                text += report.campaign_table([done[i] for i in pick.ids], only=set(pick.scenarios))
        table.write_text(text)
    say(f"campaign done: {table}")
    print(table.read_text())
    return code


def refused_options(a: argparse.Namespace) -> str | None:
    """Why options that do not go together are refused, else None (from the runner's parser, or
    a window's: an option it does not give is not set)."""

    def given(name: str) -> object:
        return getattr(a, name, None)

    streamer = given("streamer") or "none"
    if given("cold") and streamer == "none":
        return "--cold is for a streamer"
    if given("freeze_in_flight") and streamer not in ("live", "app"):
        return "--freeze-in-flight is for Live"
    if given("with_osxp") and streamer != "app":
        return "--with-osxp is for the app (--streamer app)"
    if streamer == "app" and given("zooms") and not given("cold"):
        return "--zooms with the app needs --cold: it takes them from its settings, the run's own"
    if given("record_camera") and given("camera") == "mine":
        return "--record-camera records a camera: it replays none"
    return None


def zooms_of(text: str) -> tuple[int, int]:
    """``17/19``: AutoOrtho's max_zoom and max_zoom_near_airports (Live's the same)."""
    try:
        far, near = (int(v) for v in text.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError("two zooms, e.g. 17/19") from None
    if not (12 <= far <= 19 and far <= near <= 19):
        raise argparse.ArgumentTypeError("zooms from 12 to 19, the airports' not below the rest")
    return far, near


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("scenarios", nargs="*", help="names in tools/live/flights/scenarios, or files")
    p.add_argument("--streamer", choices=("none", "live", "autoortho", "app"), default="none")
    p.add_argument(
        "--with-osxp",
        action="store_true",
        help="with --streamer app: OrthoStudio XP's side open too, its engine and its page, idle",
    )
    p.add_argument("--cold", action="store_true", help="the streamer from a home of its own, empty")
    p.add_argument(
        "--ao-settings",
        choices=("mine", "default"),
        default="mine",
        help="AutoOrtho: your settings (~/.autoortho, only read), or AutoOrtho's own defaults",
    )
    p.add_argument(
        "--zooms",
        type=zooms_of,
        help="the streamer's max_zoom and near airports for this series, e.g. 17/19",
    )
    p.add_argument("--keep-open", action="store_true", help="X-Plane stays up after the series")
    p.add_argument(
        "--freeze-in-flight",
        action="store_true",
        help="Live builds nothing once the aircraft flies (a test: X-Plane's loads without Live)",
    )
    p.add_argument(
        "--record-camera",
        action="store_true",
        help="the view left to you, your camera recorded as the scenario's (build/flights/cameras)",
    )
    p.add_argument(
        "--camera",
        choices=("scenario", "mine"),
        default="scenario",
        help="the scenario's cameras, or yours where you recorded one (--record-camera)",
    )
    p.add_argument("--xplane", type=Path, help="X-Plane 12's folder (default: detected)")
    p.add_argument("--results", type=Path, default=RESULTS)
    p.add_argument("--campaign", help="the runs of campaigns/<name>.txt, each in turn")
    p.add_argument(
        "--pick", help="with --campaign: its comparisons chosen, e.g. 1,3 (all by default)"
    )
    p.add_argument("--after-series", type=int, help=argparse.SUPPRESS)
    p.add_argument("--app-log", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--clean", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--series", type=Path, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.after_series is not None:
        after_series(a.after_series, a.clean, a.xplane, a.series, a.app_log)
        return 0
    if a.campaign:
        if a.record_camera:
            p.error("--record-camera is for a scenario, not a campaign")
        try:
            return campaign(a.campaign, p, a)
        except (RefusedError, scenarios.ScenarioError) as e:
            print(e, file=sys.stderr)
            return 2
    if not a.scenarios:
        p.error("a scenario at least")
    wrong = refused_options(a)
    if wrong:
        p.error(wrong)
    try:
        return run(a)
    except (RefusedError, scenarios.ScenarioError) as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
