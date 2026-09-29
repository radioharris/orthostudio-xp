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
less its folder and SimBrief name). ``--zooms 17/19``: the streamer's max_zoom and max_zoom near
airports for the series. The user's own files are only ever read.

Results: ``build/flights/<date time>/<n>-<scenario>/``: ``report.md``, the scenario's part of
X-Plane's ``Log.txt``, the test pilot's ``frames.csv`` and ``events.txt``, ``screenshots/``, the
computer's ``system.csv`` (``sampler.py``); and the series' ``summary.md``.
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
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import report  # noqa: E402
import sampler  # noqa: E402
import scenario as scenarios  # noqa: E402

REPO = HERE.parents[2]
PILOT_NAME = "OrthoStudio XP Test Pilot"
PILOT_SOURCE = REPO / "native" / "xplane_test_pilot" / "test_pilot.c"
PILOT_BUILD = REPO / "native" / "xplane_test_pilot" / "build" / PILOT_NAME / "mac_x64"
RESULTS = REPO / "build" / "flights"
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


class RefusedError(Exception):
    """The series cannot run; the message says why."""


def say(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


def box(xp: Path) -> Path:
    """The test pilot's folder in X-Plane: the scenario sent, its answers."""
    return xp / "Output" / "osxp_test_pilot"


# ---- the test pilot plugin -------------------------------------------------------------------


def build_pilot(xp: Path) -> Path:
    """The plugin, compiled again when its source is newer (clang, against X-Plane's XPLM)."""
    if sys.platform != "darwin":
        raise RefusedError("the test pilot is built on macOS only for now")
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
    dest = xp / "Resources" / "plugins" / PILOT_NAME / "mac_x64" / f"{PILOT_NAME}.xpl"
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
        exe = xp / ("X-Plane.exe" if os.name == "nt" else "X-Plane-x86_64")
        subprocess.Popen(
            [str(exe), *args],
            cwd=xp,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
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
    """A folder for Live of its own: the user's Live settings, less its folder and SimBrief name."""
    from orthostudio.live import settings_file

    home.mkdir(parents=True, exist_ok=True)
    text = settings_file().read_text(encoding="utf-8")
    text = re.sub(r"(?m)^folder\s*=.*$", 'folder = ""', text)
    text = re.sub(r"(?m)^simbrief\s*=.*$", 'simbrief = ""', text)
    (home / "live.toml").write_text(text, encoding="utf-8")
    return home


def start_live(
    xp: Path, out: Path, cold: bool, zooms: tuple[int, int] | None = None
) -> subprocess.Popen:
    """Live's streamer, before X-Plane, as the user starts it; back once it is ready."""
    env = dict(os.environ)
    if cold:
        env["OSXP_HOME"] = str(cold_home(out / "live-home"))
    cmd = [sys.executable, "-m", "orthostudio.live", "stream", "--xplane", str(xp)]
    if zooms:
        cmd += ["--max-zoom", str(zooms[0]), "--max-zoom-airports", str(zooms[1])]
    log = out / "live.txt"
    with log.open("ab") as f:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO,
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # it outlives the runner: it takes its pack out after X-Plane
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
    stop_now(proc)
    raise RefusedError("Live not ready after 20 min")


def live_folder(out: Path, cold: bool) -> Path:
    from orthostudio.live.pack import live_root

    return out / "live-home" / "live" if cold else live_root()


def autoortho_runs() -> bool:
    """AutoOrtho's port taken, or one of its regions mounted."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", 5847)) == 0:
            return True
    finally:
        sock.close()
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
    stop_now(proc)
    raise RefusedError("AutoOrtho's regions not mounted after 5 min")


def stop_now(proc: subprocess.Popen | None) -> None:
    """Ctrl-C, as in its terminal (X-Plane not running)."""
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except OSError:
            proc.send_signal(signal.SIGINT)


def quit_xplane_udp() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(b"CMND\x00sim/operation/quit\x00", XPLANE_UDP)
    finally:
        sock.close()


def after_series(pid: int) -> None:
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
    if pid <= 0:
        return
    try:
        os.killpg(pid, signal.SIGINT)
    except OSError:
        return
    began = time.time()
    while time.time() - began < STOP_TIMEOUT_S:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(2.0)
    with contextlib.suppress(OSError):
        os.killpg(pid, signal.SIGTERM)


def quit_in_background(proc: subprocess.Popen | None) -> None:
    """X-Plane quit, then the streamer stopped, by ``after_series`` in a process of its own."""
    pid = proc.pid if proc is not None and proc.poll() is None else 0
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--after-series", str(pid)],
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
            os.replace(p, aside / p.name)


def send(xp: Path, sc: scenarios.Scenario) -> None:
    b = box(xp)
    tmp = b / "scenario.tmp"
    tmp.write_text(sc.for_the_pilot(), encoding="utf-8")
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


def collect(
    xp: Path, folder: Path, log_from: int, shots_before: set[str], live_from: int, out: Path
) -> None:
    """The flight's files into its folder: the test pilot's, X-Plane's log and screenshots from the
    scenario on, and Live's output."""
    b = box(xp)
    for name, dest in (
        ("events.txt", "events.txt"),
        ("frames.csv", "frames.csv"),
        ("scenario.done", "scenario.txt"),
        ("scenario.txt", "scenario.txt"),  # not flown: never flown later either
    ):
        if (b / name).exists():
            shutil.move(str(b / name), str(folder / dest))
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
            shutil.move(str(p), str(folder / "screenshots" / p.name))
    for name in ("live.txt", "autoortho.txt"):
        if (out / name).is_file():
            with (out / name).open("rb") as f:
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
    loads = sum(1 for ln in text.splitlines() if ln.startswith("- **load "))
    return [f"- {loads} scenery loads during the path", *keep]


def run(a: argparse.Namespace) -> int:
    from orthostudio.live import session

    series = [scenarios.load(s) for s in a.scenarios]
    xp = session.xplane_dir(a.xplane)
    out = a.results / datetime.now().strftime("%Y-%m-%d %H%M%S")
    out.mkdir(parents=True)
    a.out = out
    up = xplane_up()
    if up and READY not in log_text(xp):
        raise RefusedError("X-Plane runs without the test pilot: quit it, the series starts it")
    running = {"live": live_runs(), "autoortho": autoortho_runs()}
    proc, folder_of_streamer = None, None
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
            proc = start_live(xp, out, a.cold, a.zooms)
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
            live_from = size_of(
                out / ("autoortho.txt" if a.streamer == "autoortho" else "live.txt")
            )
            if up:
                clear_box(xp)
            recorder = sampler.Sampler(folder / "system.csv", proc.pid if proc else None)
            recorder.start()
            recorder.first.wait(30)  # the computer before the scenario, then the scenario
            send(xp, sc)
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
            collect(xp, folder, log_from, shots_before, live_from, out)
            info = {
                "scenario": sc.name,
                "streamer": a.streamer,
                "ao_settings": a.ao_settings if a.streamer == "autoortho" else None,
                "zooms": list(a.zooms) if a.zooms else None,
                "cold": a.cold,
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
            quit_in_background(proc)
        elif still_up:
            say("X-Plane stays up (--keep-open)")
        else:
            stop_now(proc)
        print((out / "summary.md").read_text())
    return (
        0
        if results and all(o == "done" for _, _, o in results) and len(results) == len(series)
        else 1
    )


def label(a: argparse.Namespace) -> str:
    """What a run flew with, in a few words."""
    parts = [{"none": "X-Plane alone", "live": "Live", "autoortho": "AutoOrtho"}[a.streamer]]
    if a.streamer == "autoortho":
        parts.append("its defaults" if a.ao_settings == "default" else "your settings")
    if a.zooms:
        parts.append(f"ZL{a.zooms[0]}/{a.zooms[1]}")
    if a.cold:
        parts.append("cold")
    return ", ".join(parts)


def read_campaign(ref: str) -> tuple[list[str], list[list[str]]]:
    """A campaign's scenarios, and the options of each of its runs: ``campaigns/<ref>.txt``, or a
    file. Its lines: ``scenarios NAME...`` and ``run OPTIONS...`` (the runner's own options)."""
    path = Path(ref)
    if not path.suffix and not path.exists():
        path = CAMPAIGNS / f"{ref}.txt"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in CAMPAIGNS.glob("*.txt")))
        raise RefusedError(f"no campaign {ref!r} (the campaigns: {known})")
    names, runs = [], []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        words = shlex.split(line.split("#", 1)[0])
        if not words:
            continue
        if words[0] == "scenarios":
            names += words[1:]
        elif words[0] == "run":
            runs.append(words[1:])
        else:
            raise RefusedError(f"{path.name}, line {number}: neither scenarios nor run")
    if not names or not runs:
        raise RefusedError(f"{path.name}: a scenarios line and a run line at least")
    return names, runs


def wait_quiet(timeout_s: float = 600.0) -> None:
    """Until X-Plane has quit and no streamer runs: the run before quits them in the background."""
    began = time.time()
    while time.time() - began < timeout_s:
        if xplane_gone() and not live_runs() and not autoortho_runs():
            return
        time.sleep(5.0)
    raise RefusedError("X-Plane or a streamer still runs 10 min after the run before")


def campaign(ref: str, parser: argparse.ArgumentParser, base: argparse.Namespace) -> int:
    """Each run of the campaign in turn, X-Plane started again for each (its streamer must be
    there before it), then the runs side by side in ``<date time> <campaign>.md``."""
    names, runs = read_campaign(ref)
    for s in names:
        scenarios.load(s)  # a wrong name said now, not after an hour of flights
    if xplane_up() or live_runs() or autoortho_runs():
        raise RefusedError("X-Plane, Live or AutoOrtho runs: quit them, the campaign starts each")
    extra = ["--results", str(base.results)] + (
        ["--xplane", str(base.xplane)] if base.xplane else []
    )
    done: list[tuple[str, Path]] = []
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    table = base.results / f"{stamp} {Path(ref).stem}.md"
    code = 0
    for k, options in enumerate(runs, 1):
        a = parser.parse_args([*options, *extra, *names])
        if a.cold and a.streamer == "none":
            raise RefusedError(f"run {k}: --cold is for a streamer")
        if k > 1:
            say("waiting for X-Plane and the streamer of the run before to quit")
            wait_quiet()
        say(f"campaign {Path(ref).stem}, run {k} of {len(runs)}: {label(a)}")
        code = max(code, run(a))
        done.append((label(a), a.out))
        base.results.mkdir(parents=True, exist_ok=True)
        table.write_text(f"# {Path(ref).stem}, {stamp}\n\n" + report.campaign_table(done))
    say(f"campaign done: {table}")
    print(table.read_text())
    return code


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
    p.add_argument("--streamer", choices=("none", "live", "autoortho"), default="none")
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
    p.add_argument("--xplane", type=Path, help="X-Plane 12's folder (default: detected)")
    p.add_argument("--results", type=Path, default=RESULTS)
    p.add_argument("--campaign", help="the runs of campaigns/<name>.txt, each in turn")
    p.add_argument("--after-series", type=int, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.after_series is not None:
        after_series(a.after_series)
        return 0
    if a.campaign:
        try:
            return campaign(a.campaign, p, a)
        except (RefusedError, scenarios.ScenarioError) as e:
            print(e, file=sys.stderr)
            return 2
    if not a.scenarios:
        p.error("a scenario at least")
    if a.cold and a.streamer == "none":
        p.error("--cold is for a streamer")
    try:
        return run(a)
    except (RefusedError, scenarios.ScenarioError) as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
