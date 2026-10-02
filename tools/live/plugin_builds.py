# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OrthoStudio XP Live's X-Plane plugin for Windows and Linux, built on any computer with Docker,
into ``native/xplane_plugin/prebuilt``: X-Plane's ``win_x64`` and ``lin_x64`` folders, and
``SOURCES``, the digest of what they were built from (``orthostudio.live.plugin.sources_digest``,
which ``tests/test_live_gate.py`` checks against the sources). The test pilot of the replayable
test flights the same way, into ``native/xplane_test_pilot/prebuilt``
(``tools/live/flights/pilot_build.py``, checked by ``tests/test_live_pilot.py``).

The Mac's plugin is built with clang on a Mac: by a checkout, where Live runs, and with the Mac
installer (``orthostudio.live.plugin``).
Windows and Linux computers seldom have a compiler, so theirs are built ahead: in an Ubuntu 20.04
for x86-64, with gcc for Linux and MinGW-w64 for Windows (``plugin.LINUX_BUILD``,
``plugin.WINDOWS_BUILD``). Its C library, glibc 2.31, is the oldest the Linux plugin asks for, so it
loads in X-Plane 12 on any Linux since 2020. The Windows plugin needs nothing beyond Windows' own
libraries and X-Plane's XPLM_64.dll. Each build is checked: the five functions X-Plane calls are
there, and the Linux plugin asks for no newer glibc. Only those whose sources changed are built
again (``--all``: both).

    uv run python tools/live/plugin_builds.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from orthostudio.live import plugin

sys.path.insert(0, str(Path(__file__).resolve().parent / "flights"))
import pilot_build

IMAGE = "osxp-live-plugin-build:focal"
DOCKERFILE = """FROM --platform=linux/amd64 ubuntu:20.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
        gcc libc6-dev libx11-dev binutils gcc-mingw-w64-x86-64 binutils-mingw-w64-x86-64 \\
    && rm -rf /var/lib/apt/lists/*
"""
ENTRY_POINTS = (
    "XPluginStart",
    "XPluginStop",
    "XPluginEnable",
    "XPluginDisable",
    "XPluginReceiveMessage",
)
NEWEST_GLIBC = (2, 17)  # what the Linux plugin may ask for at most
WINDOWS_DLLS = {
    "xplm_64.dll",
    "ws2_32.dll",
    "user32.dll",
    "gdi32.dll",
    "kernel32.dll",
    "msvcrt.dll",
}
PILOT_DLLS = {"xplm_64.dll", "kernel32.dll", "msvcrt.dll"}


def docker(*args: str, stdin: str | None = None) -> str:
    done = subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"docker {args[0]} failed:\n{done.stdout}{done.stderr}")
    return done.stdout


def build_one(
    out: Path, name: str, sources: tuple[Path, ...], linux: str, windows: str, dlls: set[str]
) -> None:
    """A plugin into ``out``/win_x64 and ``out``/lin_x64, checked (:func:`check`)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for source in sources:
            shutil.copy2(source, work / source.name)
        xpl = f"{name}.xpl"
        script = (
            "set -e; cd /work; mkdir -p lin_x64 win_x64; "
            f'{linux} -o "lin_x64/{xpl}"; '
            f'{windows} -o "win_x64/{xpl}"; '
            f'nm -D --defined-only "lin_x64/{xpl}" > lin_x64.defined; '
            f'objdump -T "lin_x64/{xpl}" > lin_x64.versions; '
            f'x86_64-w64-mingw32-objdump -p "win_x64/{xpl}" > win_x64.pe'
        )
        docker(
            "run", "--rm", "--platform", "linux/amd64", "-v", f"{work}:/work", IMAGE,
            "sh", "-c", script,
        )  # fmt: skip
        check(work, dlls)
        for folder in ("lin_x64", "win_x64"):
            (out / folder).mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / folder / xpl, out / folder / xpl)


def built_from(sources: Path, digest: str) -> bool:
    """Whether the files built ahead come from the sources of ``digest`` (their SOURCES file)."""
    try:
        return sources.read_text().split()[0] == digest
    except (OSError, IndexError):
        return False


def build(every: bool = False) -> list[str]:
    """Live's plugin and the test pilot, for Windows and Linux, with their sources' digests: those
    whose sources changed, or ``every`` one (a Windows build carries its time: built again for
    nothing, it would differ). Their names."""
    docker("build", "--platform", "linux/amd64", "-t", IMAGE, "-", stdin=DOCKERFILE)
    made = []
    live_sources = plugin.PREBUILT_DIR / "SOURCES"
    if every or not built_from(live_sources, plugin.sources_digest()):
        build_one(
            plugin.PREBUILT_DIR,
            plugin.NAME,
            (plugin.SOURCE, plugin.WINDOWS_PANEL, plugin.LINUX_PANEL, plugin.XPLM_DEF),
            plugin.LINUX_BUILD,
            plugin.WINDOWS_BUILD,
            WINDOWS_DLLS,
        )
        live_sources.write_text(
            f"{plugin.sources_digest()}  live_gate.c live_panel_win.c live_panel_x11.c "
            "XPLM_64.def, and the build commands (orthostudio/live/plugin.py)\n"
        )
        made.append(plugin.NAME)
    if every or not built_from(pilot_build.SOURCES, pilot_build.sources_digest()):
        build_one(
            pilot_build.PREBUILT_DIR,
            pilot_build.NAME,
            (pilot_build.SOURCE, pilot_build.XPLM_DEF),
            pilot_build.LINUX_BUILD,
            pilot_build.WINDOWS_BUILD,
            PILOT_DLLS,
        )
        pilot_build.SOURCES.write_text(
            f"{pilot_build.sources_digest()}  test_pilot.c XPLM_64.def, and the build commands "
            "(tools/live/flights/pilot_build.py)\n"
        )
        made.append(pilot_build.NAME)
    return made


def check(work: Path, allowed: set[str]) -> None:
    """The entry points X-Plane calls are there; Linux asks for no newer glibc than NEWEST_GLIBC;
    Windows' plugin needs only Windows' libraries and XPLM_64.dll (``allowed``)."""
    defined = (work / "lin_x64.defined").read_text()
    pe = (work / "win_x64.pe").read_text()
    for entry in ENTRY_POINTS:
        if not re.search(rf"\b{entry}\b", defined):
            raise SystemExit(f"the Linux plugin lacks {entry}")
        if not re.search(rf"\b{entry}\b", pe.split("Export Address Table", 1)[-1]):
            raise SystemExit(f"the Windows plugin lacks {entry}")
    versions = re.findall(r"GLIBC_(\d+)\.(\d+)", (work / "lin_x64.versions").read_text())
    newest = max((int(a), int(b)) for a, b in versions)
    if newest > NEWEST_GLIBC:
        raise SystemExit(f"the Linux plugin asks for glibc {newest[0]}.{newest[1]}")
    dlls = {d.lower() for d in re.findall(r"DLL Name: (\S+)", pe)}
    if not dlls <= allowed:
        raise SystemExit(f"the Windows plugin needs {sorted(dlls - allowed)}")


def main(argv: list[str] | None = None) -> int:
    every = "--all" in (sys.argv[1:] if argv is None else argv)
    if shutil.which("docker") is None:
        print("Docker is needed: https://www.docker.com", file=sys.stderr)
        return 1
    made = build(every)
    for name, folder, digest in (
        (plugin.NAME, plugin.PREBUILT_DIR, plugin.sources_digest()),
        (pilot_build.NAME, pilot_build.PREBUILT_DIR, pilot_build.sources_digest()),
    ):
        done = "built" if name in made else "up to date"
        print(f"{name}: {done} in {folder}: win_x64, lin_x64 ({digest[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
