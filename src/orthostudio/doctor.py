# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""``osxp doctor``: is this machine ready to build a tile?

Checks (spec ``docs/specs/pipeline-textures.md`` section 9): Python, the DDS encoder, the
HTTP client (curl_cffi, libcurl, HTTP/2), free disk at the store root, the X-Plane 12 folder
(found as every command finds it), the Triangle4XP binary, one Bing tile (skipped offline), the
store and chunk roots.
Every check is data (``Check``) so the CLI can print it or emit JSON.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from orthostudio import __version__
from orthostudio.fsutil import NO_CONSOLE_WINDOW, REDIRECTION_GUARD_REMEDY, redirection_guard
from orthostudio.install.xplane import is_xplane_dir, xplane_candidates
from orthostudio.pipeline.home import data_root_missing, default_chunks_root, default_store_root
from orthostudio.programs import installed_program

__all__ = ["Check", "DoctorReport", "render_text", "run_doctor"]

MIN_PYTHON = (3, 12)
DISK_WARN_GB = 20.0
DISK_FAIL_GB = 5.0
TRIANGLE_ENV = "OSXP_TRIANGLE4XP"
BING_PROBE = (8424, 5992, 14)  # a land tile of the reference cell (+43+005, Marseille)


@dataclass(slots=True)
class Check:
    name: str
    status: str
    """``ok`` | ``warn`` | ``fail`` | ``skip``."""
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "osxp": __version__,
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
        }


def _python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    return Check(
        "python",
        "ok" if ok else "fail",
        f"Python {platform.python_version()} ({platform.machine()}, {platform.system()})",
        {"version": platform.python_version(), "executable": sys.executable},
    )


def _encoder() -> Check:
    from orthostudio.textures.encode import available_encoders, find_nvcompress

    available = available_encoders()
    details: dict[str, Any] = {"available": available}
    if "ispc" in available:
        import ispc_texcomp

        details["ispc_texcomp"] = str(getattr(ispc_texcomp, "__version__", "unknown"))
    binary = find_nvcompress()
    if binary is not None:
        details["nvcompress"] = str(binary)
    if not available:
        return Check(
            "encoder",
            "fail",
            "no BC1/BC3 encoder: pip install ispc-texcomp (or put nvcompress on PATH)",
            details,
        )
    active = available[0]
    label = (
        f"ispc_texcomp {details.get('ispc_texcomp', '')}".strip()
        if active == "ispc"
        else str(binary)
    )
    fallback = f", fallback {available[1]}" if len(available) > 1 else ""
    return Check("encoder", "ok", f"{label} (in-process){fallback}", details)


def _network_client() -> Check:
    try:
        import curl_cffi
    except ImportError:
        return Check("http_client", "fail", "curl_cffi is not installed", {})
    curl_version = str(getattr(curl_cffi, "__curl_version__", ""))
    http2 = "nghttp2" in curl_version or "HTTP2" in curl_version
    details = {"curl_cffi": str(getattr(curl_cffi, "__version__", "?")), "libcurl": curl_version}
    status = "ok" if http2 else "warn"
    summary = f"curl_cffi {details['curl_cffi']}, {curl_version.split(' ')[0] or 'libcurl'}"
    summary += ", HTTP/2" if http2 else ", HTTP/2 support not detected"
    return Check("http_client", status, summary, details)


def _existing_parent(path: Path) -> Path:
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _disk(store_root: Path, extra: dict[str, Path], missing: Path | None = None) -> Check:
    if missing is not None:  # the free space of the disk it is on cannot be known
        return Check(
            "disk",
            "fail",
            f"the data folder {missing} was not found: plug in its disk, or choose another "
            "folder in Settings",
            {"path": str(missing), "exists": False},
        )
    probe = _existing_parent(store_root)
    usage = shutil.disk_usage(probe)
    free_gb = usage.free / 1e9
    status = "ok" if free_gb >= DISK_WARN_GB else ("warn" if free_gb >= DISK_FAIL_GB else "fail")
    details: dict[str, Any] = {
        "probe": str(probe),
        "free_gb": round(free_gb, 1),
        "total_gb": round(usage.total / 1e9, 1),
    }
    for name, path in extra.items():
        u = shutil.disk_usage(_existing_parent(path))
        details[f"{name}_free_gb"] = round(u.free / 1e9, 1)
    return Check(
        "disk",
        status,
        f"{free_gb:.0f} GB free at {probe} (a ZL16 tile needs about 3 GB)",
        details,
    )


def _xplane(explicit: Path | None) -> Check:
    """The X-Plane 12 folder given, else the one ``detect_xplane`` finds, in its order: the page,
    the build and the doctor never disagree on where X-Plane is (the doctor had its own list, no
    installer's list, and called X-Plane optional)."""
    tried = [Path(explicit).expanduser()] if explicit is not None else []
    tried += xplane_candidates()
    for candidate in tried:
        if is_xplane_dir(candidate):
            global_scenery = candidate / "Global Scenery"
            details = {
                "path": str(candidate),
                "global_scenery": global_scenery.is_dir(),
                "scenery_packs_ini": (candidate / "Custom Scenery" / "scenery_packs.ini").is_file(),
            }
            status = "ok" if details["global_scenery"] else "warn"
            note = (
                ""
                if details["global_scenery"]
                else " (no Global Scenery folder: XP12 sea level needs it)"
            )
            return Check("xplane", status, f"X-Plane at {candidate}{note}", details)
    return Check(
        "xplane",
        "warn",
        "X-Plane 12 not found: choose its folder in Settings, or give --xplane (OrthoStudio XP "
        "takes the relief, roads, forests and buildings from it, and adds the tiles to it)",
        {"tried": [str(c) for c in tried]},
    )


def _triangle_candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get(TRIANGLE_ENV)
    if env:
        out.append(Path(env).expanduser())
    installed = installed_program("Triangle4XP")
    if installed is not None:
        out.append(installed)
    on_path = shutil.which("Triangle4XP")
    if on_path:
        out.append(Path(on_path))
    exe = "Triangle4XP.exe" if sys.platform.startswith("win") else "Triangle4XP"
    out.append(Path(__file__).resolve().parents[2] / "native" / "triangle4xp" / "build" / exe)
    return out


def _architecture() -> Check:
    """On a Mac with Apple Silicon: whether the programs OrthoStudio XP starts run natively.

    ``sysctl.proc_translated`` of a child process says it. An app whose launcher macOS started
    under Rosetta passes the Intel preference on to every universal program below it, though the
    engine's own Python has no Intel code: DSFTool and Triangle4XP then run translated, slower, and
    macOS warns that the app "includes a component that will not work with a future release"
    (found on the first installed app, 2026-09-14; ``docs/specs/packaging.md`` section 2).
    """
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return Check("architecture", "skip", "not a Mac with Apple Silicon", {})
    try:
        done = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=NO_CONSOLE_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("architecture", "skip", f"could not be checked ({exc})", {})
    if done.stdout.strip() == "1":
        return Check(
            "architecture",
            "warn",
            "the programs OrthoStudio XP starts run under Rosetta (Intel): quit OrthoStudio XP, "
            "install its latest version, and untick Open using Rosetta in the app's Get Info",
            {"translated": True},
        )
    return Check(
        "architecture",
        "ok",
        "the programs it starts run natively (Apple Silicon)",
        {"translated": False},
    )


def _junctions() -> Check:
    """On Windows: whether the app may follow the junctions it makes in Custom Scenery, what an
    install makes when the account may not make symbolic links. Windows' RedirectionGuard forbids
    it: the app opened by the last page of an Inno Setup 6.7 installer had it, and every install of
    a user's tiles failed (2026-09-15; ``fsutil.redirection_guard``)."""
    if not sys.platform.startswith("win"):
        return Check("junctions", "skip", "not Windows", {})
    if redirection_guard():
        return Check(
            "junctions",
            "fail",
            "OrthoStudio XP runs with Windows' junction protection (RedirectionGuard): it cannot "
            f"install tiles. {REDIRECTION_GUARD_REMEDY}",
            {"redirection_guard": True},
        )
    return Check(
        "junctions",
        "ok",
        "the junctions of installed tiles can be followed",
        {"redirection_guard": False},
    )


def _triangle() -> Check:
    tried = _triangle_candidates()
    for candidate in tried:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return Check(
                "triangle4xp", "ok", f"Triangle4XP at {candidate}", {"path": str(candidate)}
            )
    # a fail and not a warning: without it no tile can be built at all, every build dies at the
    # mesh with SYS_TOOL_MISSING, and the doctor is the first thing a user runs when something
    # is wrong -- it said everything was fine (found in review, 2026-09-23). The installers ship
    # it, so this reaches a build from source, or an installation that lost a file.
    return Check(
        "triangle4xp",
        "fail",
        "Triangle4XP not found: no tile can be built without it (build native/triangle4xp, or "
        "set $OSXP_TRIANGLE4XP)",
        {"tried": [str(c) for c in tried]},
    )


def _map_data(offline: bool) -> Check:
    """Which map data servers answer, and what they say.

    The question nobody could answer on 2026-09-22: two of the three public Overpass machines the
    app asked had become unusable -- one silent, one refusing everyone -- and it was found by
    three users' failed builds. ``check_health`` existed and nothing called it. Now the doctor
    does, so the answer takes five seconds and comes before the complaints.
    """
    if offline:
        return Check("map_data", "skip", "network probe skipped (pass --online to run it)", {})
    from orthostudio.sources.osm import MIRRORS, OverpassClient

    async def probe() -> dict[str, Any]:
        client = OverpassClient(health_timeout_s=5.0)
        async with client:
            health = await client.check_health()
        return {
            code: {
                "state": h.state,
                "status": h.status,
                "seconds": round(h.elapsed_s, 2),
                "error": h.error,
            }
            for code, h in health.items()
        }

    t0 = time.perf_counter()
    try:
        answers = asyncio.run(probe())
    except Exception as exc:  # the probe must never crash the doctor
        return Check("map_data", "fail", f"the probe failed: {type(exc).__name__}: {exc}", {})
    details: dict[str, Any] = {
        "seconds": round(time.perf_counter() - t0, 2),
        "servers": answers,
        "asked": [m.code for m in MIRRORS],
    }
    ordinary = [m.code for m in MIRRORS if not m.last_resort]
    answered = [code for code in ordinary if answers.get(code, {}).get("state") != "open"]
    names = ", ".join(f"{c} {answers[c].get('status') or answers[c].get('error') or '?'}"
                      for c in ordinary)  # fmt: skip
    if not answered:
        return Check("map_data", "fail", f"no map data server answered ({names})", details)
    if len(answered) < len(ordinary):
        silent = ", ".join(c for c in ordinary if c not in answered)
        return Check(
            "map_data",
            "warn",
            f"{len(answered)} of {len(ordinary)} answered; silent: {silent}",
            details,
        )
    return Check("map_data", "ok", f"{len(answered)} map data servers answered", details)


def _prepared_library(offline: bool) -> Check:
    """Whether this build carries a prepared map library, and whether it answers.

    A release is built with the library's address and key written in from the repository's
    secrets. If that step is skipped, or the key is rotated, the app works exactly as before and
    downloads every tile from the public servers: slower, quota-bound, and with nothing at all to
    say that a whole layer of the design is missing (2026-09-23).
    """
    from orthostudio.sources.library import LibrarySource, shipped_library

    url, token = shipped_library()
    if not url:
        return Check(
            "map_library",
            "skip",
            "this build carries no prepared map library (a build from source never does)",
            {"carried": False},
        )
    if offline:
        return Check("map_library", "skip", "network probe skipped", {"carried": True})
    t0 = time.perf_counter()
    try:
        index = LibrarySource(url, token)._load_index()
    except Exception as exc:  # the probe must never crash the doctor
        return Check("map_library", "fail", f"the probe failed: {type(exc).__name__}: {exc}", {})
    took = round(time.perf_counter() - t0, 2)
    if index is None:
        return Check(
            "map_library",
            "warn",
            "the map library did not answer, or refused this build's key; tiles will be "
            "downloaded from the public servers",
            {"carried": True, "seconds": took},
        )
    details = {
        "carried": True,
        "seconds": took,
        "bake": index.bake,
        "extracted": index.extracted,
        "road_level": index.road_level,
        "tiles": len(index.tiles),
    }
    return Check(
        "map_library",
        "ok",
        f"{len(index.tiles)} tiles ready, cut {index.extracted[:10]}",
        details,
    )


def _bing(offline: bool) -> Check:
    if offline:
        return Check("bing", "skip", "network probe skipped (pass --online to run it)", {})
    from orthostudio.imagery.providers import is_placeholder, load_registry, tile_url
    from orthostudio.net import FetchRequest, fetch_all

    provider = load_registry()["BI"]
    x, y, zl = BING_PROBE
    url = tile_url(provider, x, y, zl)
    t0 = time.perf_counter()
    try:
        result = fetch_all(
            [FetchRequest(key="probe", url=url, headers=dict(provider.headers), host_group="BI")],
            max_in_flight=1,
            start_in_flight=1,
            hedge_after_s=3.0,
            timeout_s=10.0,
            max_attempts=2,
        )[0]
    except Exception as exc:  # the probe must never crash the doctor
        return Check(
            "bing", "fail", f"Bing probe failed: {type(exc).__name__}: {exc}", {"url": url}
        )
    elapsed = time.perf_counter() - t0
    details: dict[str, Any] = {
        "url": url,
        "status": result.status,
        "bytes": len(result.body),
        "elapsed_s": round(elapsed, 3),
        "error": result.error,
        "content_type": result.headers.get("content-type"),
    }
    if result.error is not None:
        return Check("bing", "fail", f"Bing unreachable ({result.error})", details)
    if result.status != 200:
        return Check("bing", "fail", f"Bing answered HTTP {result.status}", details)
    if is_placeholder(provider, result.headers, result.body):
        return Check(
            "bing",
            "warn",
            "Bing answered a placeholder for a land tile (provider changed?)",
            details,
        )
    return Check(
        "bing",
        "ok",
        f"Bing tile {x}/{y}@{zl}: {len(result.body)} bytes in {elapsed * 1000:.0f} ms (HTTP/2)",
        details,
    )


def _window() -> Check:
    """Whether the app opens in a window of its own, or in the browser instead.

    macOS and Windows carry the web view it needs, and the installers carry the rest, so this
    says ``ok`` on an installed app; a build refuses an app that answers otherwise
    (``tools/package/build.py``). Linux asks for a library of its own, which OrthoStudio XP does
    not install on a system it does not own: the answer is then ``warn``, and it names the
    library (``orthostudio.window.hint``).
    """
    from orthostudio import window

    if window.possible():
        return Check("window", "ok", "A window of its own", {"browser_only": False})
    hint = window.hint()
    # nothing to do about it is not a warning: it is how this build works on this system, and an
    # orange pill nobody can ever clear is noise (measured in a container, 2026-09-20)
    nothing_to_do = window.install() is None
    return Check(
        "window",
        "skip" if nothing_to_do else "warn",
        hint or "The browser opens instead of a window of its own",
        # the page puts its own words around "install" and shows it as a command or a link: the
        # line at the foot of the page was the only place this was said, and it is a fold (a user
        # asked, 2026-09-20)
        {"browser_only": True, "hint": hint, "install": window.install()},
    )


def _store(store_root: Path) -> Check:
    root = Path(store_root).expanduser()
    if not (root / "index.sqlite").exists():
        return Check(
            "store",
            "ok",
            f"artefact store {root} (empty, created on first build)",
            {"path": str(root), "exists": False},
        )
    from orthostudio.graph import Store

    try:
        with Store(root) as store:
            count, size = len(store), store.total_size()
    except Exception as exc:
        return Check(
            "store", "fail", f"artefact store {root} unreadable: {exc}", {"path": str(root)}
        )
    return Check(
        "store",
        "ok",
        f"artefact store {root}: {count} artefact(s), {size / 1e9:.2f} GB",
        {"path": str(root), "artifacts": count, "bytes": size},
    )


def _chunks(chunks_root: Path) -> Check:
    root = Path(chunks_root).expanduser()
    if not root.is_dir():
        return Check(
            "chunks",
            "ok",
            f"tile store {root} (empty, created on first build)",
            {"path": str(root), "exists": False},
        )
    count, size = _containers(root)
    return Check(
        "chunks",
        "ok",
        f"tile store {root}: {count} texture container(s), {size / 1e9:.2f} GB",
        {"path": str(root), "containers": count, "bytes": size},
    )


def _containers(root: Path) -> tuple[int, int]:
    """How many ``*.chunks`` files lie under ``root``, and their bytes, read from the folder
    listings: asked of each file, as before, it opened every one on Windows, and a big cache
    behind an antivirus held the status, and the page's first screen, a long time (2026-09-22).
    Links are not followed; what cannot be read is left out."""
    count = size = 0
    folders = [os.fspath(root)]
    while folders:
        try:
            with os.scandir(folders.pop()) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    folders.append(entry.path)
                elif entry.name.endswith(".chunks") and entry.is_file(follow_symlinks=False):
                    count += 1
                    size += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return count, size


def run_doctor(
    *,
    offline: bool = False,
    xplane: Path | None = None,
    store_root: Path | None = None,
    chunks_root: Path | None = None,
) -> DoctorReport:
    """Run every check; never raises."""
    missing = data_root_missing() if store_root is None else None
    store_root = Path(store_root) if store_root is not None else default_store_root()
    chunks_root = Path(chunks_root) if chunks_root is not None else default_chunks_root()
    report = DoctorReport()
    for fn in (
        _python,
        _encoder,
        _network_client,
        lambda: _disk(store_root, {"chunks": chunks_root}, missing),
        lambda: _xplane(xplane),
        _triangle,
        _architecture,
        _junctions,
        _window,
        lambda: _bing(offline),
        lambda: _map_data(offline),
        lambda: _prepared_library(offline),
        lambda: _store(store_root),
        lambda: _chunks(chunks_root),
    ):
        try:
            report.checks.append(fn())
        except Exception as exc:  # a broken check is reported, not raised
            report.checks.append(
                Check(
                    getattr(fn, "__name__", "check").strip("_"),
                    "fail",
                    f"{type(exc).__name__}: {exc}",
                )
            )
    return report


_MARK = {"ok": "ok  ", "warn": "warn", "fail": "FAIL", "skip": "skip"}


def render_text(report: DoctorReport) -> str:
    lines = [f"OrthoStudio XP {__version__} doctor"]
    for c in report.checks:
        lines.append(f"  [{_MARK[c.status]}] {c.name:<12} {c.summary}")
    lines.append("everything a build needs is here" if report.ok else "some checks failed")
    return "\n".join(lines)
