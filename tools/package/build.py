"""Build the installer of OrthoStudio XP for the system this runs on, with Python inside.

    uv run python tools/package/build.py           # the installer, in dist/
    uv run python tools/package/build.py --check   # then check the engine it packed

What goes in (``docs/specs/packaging.md``): the standalone CPython uv installs
(python-build-standalone), the runtime dependencies pinned by ``uv.lock`` (no dev group, the
``server`` extra), OrthoStudio XP's wheel, Triangle4XP and DSFTool in ``orthostudio/bin``, and the
licences. What comes out, in ``dist/``:

- macOS: ``OrthoStudio-XP-<version>-macos-arm64.dmg``, the app and a link to Applications, and
  with ``--machine x86_64`` ``OrthoStudio-XP-<version>-macos-x86_64.dmg``, the app for Intel Macs
  (built and checked under Rosetta on Apple Silicon);
- Windows: ``OrthoStudio-XP-<version>-windows-x64-setup.exe`` (Inno Setup, for the current user,
  no administrator) and the same files as a ``.zip``;
- Linux: ``OrthoStudio-XP-<version>-linux-x86_64.tar.gz``, whose ``install.sh`` adds the menu entry.

Nothing is signed: macOS and Windows warn at the first launch (``README.md``). Triangle4XP must be
built first (``native/triangle4xp/README.md``); the Windows installer needs Inno Setup 6.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from icon import make_icns, make_ico, make_png

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "dist"
WORK = DIST / "package"
APP_NAME = "OrthoStudio XP"
FILE_STEM = "OrthoStudio-XP"
PYTHON_VERSION = (REPO / ".python-version").read_text(encoding="utf-8").strip()
ENTRY_MODULE = "orthostudio.desktop"
ENGINE_PORT = 8641
"""The port the app's engine listens on (``orthostudio.api.serve.DEFAULT_PORT``; a test keeps the
two equal): the Windows installer asks a running engine to quit there."""

DMG_TRIES = 3
DMG_RETRY_S = 20.0
"""`hdiutil create` answers "Resource busy" now and then on a build machine, where Spotlight and
the mounts of other jobs share the disk: the tag build of 0.1.9 failed on it, minutes after the
same commit had packaged cleanly. It is the machine, not the image, so it is asked again."""

INNO_CODE = Path(__file__).resolve().parent / "stop_running.pas"
INNO_WEBVIEW2 = Path(__file__).resolve().parent / "webview2.pas"
INNO_UNINSTALL = Path(__file__).resolve().parent / "uninstall_data.pas"
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
"""Microsoft's Evergreen bootstrapper, the link they give to ship with an app."""
WEBVIEW2_EXE = "MicrosoftEdgeWebview2Setup.exe"
"""The ``[Code]`` of the Windows installer: an OrthoStudio XP running from the installation folder
is stopped before its files are replaced or removed."""
UV_OFFLINE: list[str] = []
"""``["--offline"]`` with ``--offline``: the packages come from uv's cache, nothing is downloaded
but the standalone Python."""
BUNDLE_ID = "org.orthostudio-xp.app"
BUILD_METADATA = ("direct_url.json", "uv_build.json", "uv_cache.json")
"""What ``uv sync`` records in the package's ``.dist-info`` about the checkout it was built from
(its path, its dates): not shipped."""
WINDOWS_APP_ID = "{{7C8E0F52-3B1D-4E4A-9B67-2D4F1A6C9E31}"
"""Inno Setup ``AppId`` (doubled brace: Inno's escape). Fixed for good: an update replaces the
installed app because the id is the same."""
LICENCE_FILES = (
    ("LICENSE", "LICENSE-OrthoStudio-XP.txt"),
    ("NOTICE", "NOTICE-OrthoStudio-XP.txt"),
    ("native/triangle4xp/README.md", "triangle4xp/README.md"),
    ("native/triangle4xp/CHANGES.md", "triangle4xp/CHANGES.md"),
    ("native/triangle4xp/Triangle4XP.c", "triangle4xp/Triangle4XP.c"),
    ("native/triangle4xp/CMakeLists.txt", "triangle4xp/CMakeLists.txt"),
    ("native/dsftool/README.md", "dsftool/README.md"),
)
"""Shipped with every installer: the GPL of OrthoStudio XP and its notice (who holds its copyright),
and the sources and notice of modifications Triangle4XP's licence requires with its binary."""


@dataclass(frozen=True, slots=True)
class Target:
    """The system an installer is for: ``macos`` / ``windows`` / ``linux`` and its machine."""

    system: str
    machine: str

    @property
    def dsftool_dir(self) -> str:
        return {"macos": "mac", "windows": "win", "linux": "lin"}[self.system]

    def exe(self, name: str) -> str:
        return f"{name}.exe" if self.system == "windows" else name

    def python_in(self, root: Path) -> Path:
        """The interpreter of a standalone CPython installed at ``root``."""
        return root / "python.exe" if self.system == "windows" else root / "bin" / "python3"

    def python_request(self, version: str) -> str:
        """What ``uv python install`` is asked for. The version alone gives the Python of the
        machine uv runs on; on macOS the architecture is named, so that an Apple Silicon Mac
        installs the Intel Python of the Intel app."""
        if self.system != "macos":
            return version
        return f"cpython-{version}-macos-{MAC_UV_ARCH[self.machine]}-none"


MAC_UV_ARCH = {"arm64": "aarch64", "x86_64": "x86_64"}
"""The machines of the macOS apps, and uv's name for each (a user asked for Intel Macs,
2026-09-17)."""
FOR_THIS_MAC = [False]
"""``--this-mac``: the app is built for the Mac it runs on, not for the release (see
:func:`check_oldest_macos`)."""
MAC_OLDEST = {"arm64": "14.0", "x86_64": "15.0"}
"""The oldest macOS the README gives for each app. The wheels are chosen for the macOS the build
runs on, and pyproj's Intel wheels ask for macOS 15: the Intel app is built on macOS 15."""


def this_target(machine: str | None = None) -> Target:
    """The system this runs on and its machine; on macOS, ``machine`` may name the other one: an
    Apple Silicon Mac builds the Intel app too, and runs it under Rosetta to check it."""
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    host = platform.machine().lower()
    host = {"amd64": "x64", "x86_64": "x64" if system == "windows" else "x86_64"}.get(host, host)
    if machine is None or machine == host:
        return Target(system, host)
    if system != "macos" or machine not in MAC_UV_ARCH:
        raise SystemExit(f"--machine {machine}: only macOS builds for another machine")
    if host == "arm64" and machine == "x86_64":
        rosetta = subprocess.run(["arch", "-x86_64", "/usr/bin/true"], check=False)
        if rosetta.returncode:
            raise SystemExit(
                "The Intel app is built and checked under Rosetta: "
                "softwareupdate --install-rosetta --agree-to-license"
            )
    return Target(system, machine)


def project_version() -> str:
    with (REPO / "pyproject.toml").open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def artefact_name(version: str, target: Target) -> str:
    """The file an installer is written to, in ``dist/``."""
    suffix = {"macos": ".dmg", "windows": "-setup.exe", "linux": ".tar.gz"}[target.system]
    system = {"macos": "macos", "windows": "windows", "linux": "linux"}[target.system]
    return f"{FILE_STEM}-{version}-{system}-{target.machine}{suffix}"


# -- the files written into the installers ------------------------------------------------------


def macos_launcher() -> str:
    """``Contents/MacOS/orthostudio``: the Python inside the app runs the desktop entry.

    It becomes that process, rather than starting it aside: the window the app opens is then the
    app's own, and macOS knows it by this bundle. A window opened by a process started aside is
    called Python and carries Python's icon (measured, 2026-09-20). The entry itself ends when it
    has no window to show, so that macOS is never left with an app that runs and shows nothing
    (``orthostudio.desktop._aside_on_macos``).
    """
    return (
        "#!/bin/bash\n"
        "# OrthoStudio XP (tools/package/build.py): the engine and its page, from the Python\n"
        "# inside the app. Its output goes to ~/Library/Logs/OrthoStudio XP/serve.log.\n"
        'CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"\n'
        f'exec "$CONTENTS/Resources/python/bin/python3" -m {ENTRY_MODULE} "$@"\n'
    )


def macos_info_plist(version: str, minimum_macos: str, machine: str = "arm64") -> dict[str, object]:
    info: dict[str, object] = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "orthostudio",
        "CFBundleIconFile": "orthostudio",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": minimum_macos,
        # the launcher is a script: without the priority, macOS starts it under Rosetta, and every
        # universal program the engine runs (DSFTool, Triangle4XP) inherits the Intel preference
        "LSArchitecturePriority": [machine],
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
    }
    if machine == "arm64":
        info["LSRequiresNativeExecution"] = True
    # the Intel app may run under Rosetta on Apple Silicon: its check does, and so does a user who
    # took the other installer
    return info


def minimum_macos(wheel_tags: Iterable[str], floor: str = "11.0") -> str:
    """The oldest macOS every installed wheel runs on: the highest ``macosx_X_Y`` of their tags
    (numpy and scipy ask for 14.0 when they link Accelerate), at least ``floor``."""
    best = tuple(int(p) for p in floor.split("."))
    for tag in wheel_tags:
        m = re.search(r"macosx_(\d+)_(\d+)_", tag)
        if m:
            best = max(best, (int(m.group(1)), int(m.group(2))))
    return ".".join(str(p) for p in best)


def check_oldest_macos(machine: str, minimum: str, *, for_this_mac: bool = False) -> None:
    """Fails when the wheels of the ``machine`` app ask for a newer macOS than the README gives
    (:data:`MAC_OLDEST`): built on a newer macOS, the app would refuse the Macs it promises.

    ``for_this_mac`` says the app is for the Mac it is built on and will not be published, so what
    it promises others does not matter (``--this-mac``).
    """

    def version(text: str) -> tuple[int, ...]:
        return tuple(int(p) for p in text.split("."))

    promised = MAC_OLDEST[machine]
    if for_this_mac and version(minimum) > version(promised):
        print(
            f"--this-mac: the app asks for macOS {minimum} where the README gives {promised}; "
            "it is not the one to publish",
            flush=True,
        )
        return
    if version(minimum) > version(promised):
        raise SystemExit(
            f"the {machine} app's wheels ask for macOS {minimum}, the README gives {promised}: "
            f"build it on macOS {promised}, where uv chooses wheels for that version"
        )


def linux_launcher() -> str:
    """``orthostudio-xp`` at the top of the extracted folder."""
    return (
        "#!/bin/sh\n"
        "# OrthoStudio XP (tools/package/build.py): the engine and its page, from the Python\n"
        "# of this folder. Its output goes to ~/.orthostudio/log/serve.log.\n"
        'HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"\n'
        f'exec "$HERE/python/bin/python3" -m {ENTRY_MODULE} "$@"\n'
    )


def linux_install_script() -> str:
    """``install.sh``: the menu entry of the extracted folder, or its removal (``--remove``)."""
    return """#!/bin/sh
# Adds OrthoStudio XP to the applications menu of this user, for the folder this script is in.
# ./install.sh --remove takes the entry out. The folder itself is never moved or deleted.
set -e
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ENTRY="$APPS/orthostudio-xp.desktop"
if [ "$1" = "--remove" ]; then
    rm -f "$ENTRY"
    echo "OrthoStudio XP removed from the applications menu."
    exit 0
fi
mkdir -p "$APPS"
cat >"$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=OrthoStudio XP
Comment=Orthophoto scenery for X-Plane 12
Exec="$HERE/orthostudio-xp"
Icon=$HERE/orthostudio-xp.png
Terminal=false
Categories=Utility;
EOF
echo "OrthoStudio XP added to the applications menu ($ENTRY)."
"""


def fetch_webview2(into: Path) -> Path:
    """Microsoft's WebView2 bootstrapper (about 2 MB) into ``into``, for the installer to offer.

    It downloads and installs the runtime from Microsoft's own servers when it is run. The build
    stops when it cannot be had, rather than quietly making an installer that cannot offer it: a
    Windows build needs to reach Microsoft once, as it already reaches PyPI.
    """
    out = into / WEBVIEW2_EXE
    print("+ download", WEBVIEW2_URL, flush=True)
    try:
        with urllib.request.urlopen(WEBVIEW2_URL, timeout=120) as answer:
            body = answer.read()
    except OSError as exc:
        raise SystemExit(
            f"the WebView2 bootstrapper could not be downloaded ({exc}): {WEBVIEW2_URL}"
        ) from exc
    if len(body) < 500_000 or body[:2] != b"MZ":
        raise SystemExit(f"what came back from {WEBVIEW2_URL} is not a program ({len(body)} bytes)")
    out.write_bytes(body)
    return out


def inno_setup_script(
    version: str, bundle: Path, icon: Path, out_dir: Path, out_name: str, webview2: Path
) -> str:
    """The Inno Setup script of the Windows installer: for the current user only (no
    administrator), a Start menu entry, an optional desktop one, the app started at the end, and
    an OrthoStudio XP running from the folder stopped before its files are replaced or removed
    (:data:`INNO_CODE`).

    On a machine without the WebView2 Runtime, which OrthoStudio XP shows its window through, the
    installer offers ``webview2`` (:func:`fetch_webview2`): a task of its own, ticked, that the
    user can turn down. Turned down, or on a machine that has the runtime already, nothing is
    installed and nothing is downloaded (:data:`INNO_WEBVIEW2`).

    Uninstalling asks whether the settings and downloaded data, which live apart from the program
    and can weigh tens of gigabytes, should go too; no is where the question starts, and a data
    folder chosen on another disk is named, never taken (:data:`INNO_UNINSTALL`)."""
    parts = (INNO_CODE, INNO_WEBVIEW2, INNO_UNINSTALL)
    code = "\n".join(p.read_text(encoding="utf-8") for p in parts).replace(
        "%PORT%", str(ENGINE_PORT)
    )
    run = r"{app}\python\pythonw.exe"
    return f"""; OrthoStudio XP installer (tools/package/build.py), for Inno Setup 6
[Setup]
AppId={WINDOWS_APP_ID}
AppName={APP_NAME}
AppVersion={version}
AppPublisher=OrthoStudio XP contributors
DefaultDirName={{localappdata}}\\Programs\\{APP_NAME}
DefaultGroupName={APP_NAME}
; Inno hides the folder page on an upgrade and reuses the folder it found. It is asked for
; every time instead: the folder of a 290 MB app, which a user may want on another disk, is worth
; a page, and the folder it starts on is still the one already installed (a user asked, 2026-09-20).
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={out_dir}
OutputBaseFilename={out_name}
SetupIconFile={icon}
UninstallDisplayIcon={{app}}\\orthostudio.ico
UninstallDisplayName={APP_NAME}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
; Inno Setup 6.7 puts its setup program under Windows' RedirectionGuard, and the app its last page
; opens inherits it: that app cannot follow the junctions it makes in Custom Scenery, and every
; install of a user's tiles failed (2026-09-15). It guards an elevated setup against junctions
; planted by an unprivileged user; this one runs without elevation, as that user.
RedirectionGuard=no

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"; Flags: unchecked
Name: "webview2"; Description: "Install the Microsoft WebView2 Runtime, which {APP_NAME} shows its window in"; GroupDescription: "Missing Windows component:"; Check: WebView2Missing

[Files]
Source: "{bundle}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{webview2}"; DestDir: "{{tmp}}"; Flags: deleteafterinstall; Tasks: webview2

[Icons]
Name: "{{autoprograms}}\\{APP_NAME}"; Filename: "{run}"; Parameters: "-m {ENTRY_MODULE}"; WorkingDir: "{{app}}"; IconFilename: "{{app}}\\orthostudio.ico"
Name: "{{autodesktop}}\\{APP_NAME}"; Filename: "{run}"; Parameters: "-m {ENTRY_MODULE}"; WorkingDir: "{{app}}"; IconFilename: "{{app}}\\orthostudio.ico"; Tasks: desktopicon

[Run]
Filename: "{{tmp}}\\{WEBVIEW2_EXE}"; Parameters: "/silent /install"; StatusMsg: "Installing the Microsoft WebView2 Runtime..."; Tasks: webview2
Filename: "{run}"; Parameters: "-m {ENTRY_MODULE}"; WorkingDir: "{{app}}"; Description: "{{cm:LaunchProgram,{APP_NAME}}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{{app}}\\python"

[Code]
{code}"""  # noqa: E501


# -- steps -------------------------------------------------------------------------------------


def run(
    cmd: Sequence[str | Path],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> str:
    """Run ``cmd``, echoing it; its standard output, stripped. A failure shows its output."""
    print("+", " ".join(str(c) for c in cmd), flush=True)
    done = subprocess.run(
        [str(c) for c in cmd], text=True, capture_output=True, env=env, cwd=cwd, check=False
    )
    if done.stderr.strip():
        print(done.stderr.strip()[-2000:], flush=True)
    if check and done.returncode:
        print(done.stdout.strip()[-2000:], flush=True)
        raise SystemExit(f"{cmd[0]} failed with exit code {done.returncode}")
    return done.stdout.strip()


def run_again_if_it_fails(cmd: Sequence[str | Path], *, tries: int, wait_s: float) -> str:
    """:func:`run`, asked again while it fails, and raising on the last try.

    For a command whose failure says more about the machine than about what it was asked to do.
    """
    for attempt in range(1, tries + 1):
        print("+", " ".join(str(c) for c in cmd), flush=True)
        done = subprocess.run([str(c) for c in cmd], text=True, capture_output=True, check=False)
        if not done.returncode:
            return done.stdout.strip()
        if done.stderr.strip():
            print(done.stderr.strip()[-2000:], flush=True)
        if attempt == tries:
            raise SystemExit(f"{cmd[0]} failed with exit code {done.returncode}")
        print(f"{cmd[0]} failed; trying again ({attempt + 1} of {tries})", flush=True)
        time.sleep(wait_s)
    raise AssertionError("unreachable")


def clean_env() -> dict[str, str]:
    """The environment without the virtual environment ``uv run`` activates: uv would otherwise
    take that environment's Python for the one to pack, and sync into it."""
    return {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTHONHOME"}}


def standalone_root(cache: Path, version: str) -> Path:
    """The folder of the newest CPython ``version`` (``3.14``: ``cpython-3.14.7-...``) that
    ``uv python install`` put in ``cache``; not the link uv adds for the minor version."""
    found = [
        d
        for d in cache.glob(f"cpython-{version}.*")
        if d.is_dir()
        and not d.is_symlink()
        and re.fullmatch(rf"cpython-{re.escape(version)}\.\d+-.+", d.name)
    ]
    if not found:
        raise SystemExit(f"no CPython {version} in {cache}")
    return max(found, key=lambda d: [int(p) for p in d.name.split("-")[1].split(".")])


def install_python(target: Target, dest: Path) -> Path:
    """The standalone CPython of ``.python-version``, copied to ``dest``; its interpreter."""
    cache = WORK / f"python-cache-{target.machine}"
    request = target.python_request(PYTHON_VERSION)
    install = ["uv", "python", "install", "--no-bin", "--install-dir", cache, request]
    run(install, env=clean_env())
    root = standalone_root(cache, PYTHON_VERSION)
    if (root / "pyvenv.cfg").exists() or not target.python_in(root).is_file():
        raise SystemExit(f"{root} is not a standalone CPython")
    shutil.copytree(root, dest, symlinks=True)
    for marker in dest.rglob("EXTERNALLY-MANAGED"):
        marker.unlink()  # the packages go into this copy, which nothing else manages
    for data in dest.glob("lib/python*/_sysconfigdata_*.py"):
        # uv wrote its install folder there (build flags only): back to the standalone's /install
        text = data.read_text(encoding="utf-8")
        data.write_text(text.replace(str(root), "/install"), encoding="utf-8")
    return target.python_in(dest)


def install_packages(target: Target, python: Path, work: Path) -> Path:
    """The dependencies ``uv.lock`` pins (no dev group, the ``server`` extra) and OrthoStudio XP
    itself, not editable, into ``python``; the ``orthostudio`` package directory.

    ``uv sync`` into the standalone CPython (``UV_PROJECT_ENVIRONMENT`` names its root) installs
    the very wheels of the lock, the ones the tests ran with, and needs no index when they are in
    uv's cache (``--offline``). OrthoStudio XP's own wheel is built here, from this checkout, and
    installed after: ``uv sync`` took it from uv's cache, where a wheel built before a change of
    the code, the version unchanged, still counted as current (2026-09-14: the doctor of an
    installer lacked a check the checkout had)."""
    root = python.parent if target.system == "windows" else python.parents[1]
    env = {**clean_env(), "UV_PROJECT_ENVIRONMENT": str(root)}
    run(
        [
            "uv", "sync", "--frozen", *UV_OFFLINE, "--no-dev", "--extra", "server",
            "--no-install-project", "--python", python,
        ],
        cwd=REPO,
        env=env,
    )  # fmt: skip
    wheels = work / "wheel"
    shutil.rmtree(wheels, ignore_errors=True)
    run(["uv", "build", *UV_OFFLINE, "--wheel", "--out-dir", wheels], cwd=REPO, env=clean_env())
    (wheel,) = wheels.glob("*.whl")
    install = ["uv", "pip", "install", *UV_OFFLINE, "--python", python, "--no-deps", "--reinstall"]
    run([*install, wheel], cwd=REPO, env=clean_env())
    code = "import orthostudio, pathlib; print(pathlib.Path(orthostudio.__file__).parent)"
    package = Path(run([python, "-I", "-c", code]))
    for info in package.parent.glob("orthostudio_xp-*.dist-info"):
        for name in BUILD_METADATA:
            (info / name).unlink(missing_ok=True)
        record = info / "RECORD"
        lines = record.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [
            line for line in lines if line.split(",", 1)[0].rsplit("/", 1)[-1] not in BUILD_METADATA
        ]
        record.write_text("".join(kept), encoding="utf-8")
    return package


def python_root_of(site: Path) -> Path:
    """The standalone CPython folder holding ``site`` (``lib/python3.X/site-packages``, or
    ``Lib/site-packages`` on Windows)."""
    return site.parents[2] if site.parent.name.startswith("python") else site.parents[1]


def check_same_sources(package: Path) -> None:
    """Every file git tracks in ``src/orthostudio``, byte for byte, in the packed package, and
    nothing more than what the installer adds (``bin/``, the compiled ``.pyc``)."""
    source = REPO / "src" / "orthostudio"
    listed = run(["git", "-C", REPO, "ls-files", "--", "src/orthostudio"]).splitlines()
    wanted = {Path(name).relative_to("src/orthostudio") for name in listed}
    packed = {
        path.relative_to(package)
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    packed = {path for path in packed if path.parts[0] != "bin"}
    # The address and key of the prepared library are written from the repository's secrets when
    # the installers are built, and git never tracks them since the repository is public. The
    # packed package must hold the file the build wrote, so it is expected rather than extra, and
    # a release that was given the secrets and shipped without it is a release with no library
    # (2026-09-23).
    carried = Path("library.json")
    if carried in packed:
        packed.discard(carried)
        text = (package / carried).read_text(encoding="utf-8")
        if '"url"' not in text or '"token"' not in text:
            raise SystemExit(f"{carried} is packed but holds neither an address nor a key")
    elif (source / carried).is_file():
        # the secrets were given and the file written, and the packaging dropped it on the way:
        # the installers would work exactly as before and reach no library at all
        raise SystemExit(
            f"{source / carried} was written for this build and is not in the packed package"
        )
    stale = sorted(
        str(p)
        for p in wanted
        if not (package / p).is_file() or (package / p).read_bytes() != (source / p).read_bytes()
    )
    extra = sorted(str(p) for p in packed - wanted)
    if stale or extra:
        raise SystemExit(
            f"the packed package is not this checkout's: differs {stale[:5]}, extra {extra[:5]}"
        )


def prune(package: Path) -> None:
    """What the app never runs: pip, which comes with the standalone CPython, the test suites the
    dependencies ship (about 50 MB, most of them scipy's) and the packages' command scripts."""
    site = package.parent
    for path in [*site.glob("pip"), *site.glob("pip-*.dist-info")]:
        shutil.rmtree(path)
    for tests in sorted(site.glob("*/**/tests"), reverse=True):
        if tests.is_dir() and not tests.is_relative_to(package):
            shutil.rmtree(tests)
    # the scripts the packages installed name the build folder's Python in their first lines:
    # moved with the app they would run nothing (the app runs python -m instead); the standalone
    # CPython's own scripts find their Python relatively and stay
    python_root = python_root_of(site)
    shutil.rmtree(python_root / "Scripts", ignore_errors=True)
    for script in python_root.glob("bin/pip*"):
        script.unlink()
    for script in python_root.glob("bin/*"):
        if script.is_symlink() or not script.is_file():
            continue
        with script.open("rb") as f:
            head = f.read(1024)
        if head.startswith(b"#!") and str(python_root).encode() in head:
            script.unlink()


def compile_bytecode(python: Path, package: Path) -> None:
    """The ``.pyc`` of every installed package, as the app will load them, recorded relative to the
    app's ``python`` folder rather than to the build folder (Python puts the real path back when it
    loads them)."""
    site = package.parent
    python_root = python_root_of(site)
    run(
        [python, "-I", "-m", "compileall", "-q", "-j", "0", "-s", python_root, "-p", "python", site]
    )


def install_programs(target: Target, package: Path) -> None:
    """Triangle4XP and DSFTool into ``orthostudio/bin`` (``orthostudio.programs``)."""
    triangle = REPO / "native" / "triangle4xp" / "build" / target.exe("Triangle4XP")
    if not triangle.is_file():
        raise SystemExit(
            f"{triangle} is missing: build Triangle4XP first (cmake -S native/triangle4xp -B "
            "native/triangle4xp/build -DCMAKE_BUILD_TYPE=Release, then cmake --build "
            "native/triangle4xp/build --config Release)."
        )
    dsftool = REPO / "native" / "dsftool" / target.dsftool_dir / target.exe("DSFTool")
    if target.system == "macos":
        # both are universal binaries (Triangle4XP's CMakeLists.txt asks for arm64 and x86_64)
        for program in (triangle, dsftool):
            archs = run(["lipo", "-archs", program]).split()
            if target.machine not in archs:
                raise SystemExit(f"{program} has no {target.machine} code ({' '.join(archs)})")
    bin_dir = package / "bin"
    bin_dir.mkdir()
    for program in (triangle, dsftool):
        dest = bin_dir / program.name
        shutil.copy2(program, dest)
        dest.chmod(0o755)


def copy_licences(dest: Path) -> None:
    for source, name in LICENCE_FILES:
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / source, path)


def wheel_tags(package: Path) -> list[str]:
    """The ``Tag:`` lines of every installed distribution, next to the ``orthostudio`` package."""
    tags: list[str] = []
    for wheel in package.parent.glob("*.dist-info/WHEEL"):
        for line in wheel.read_text(encoding="utf-8").splitlines():
            if line.startswith("Tag:"):
                tags.append(line.split(":", 1)[1].strip())
    return tags


def build_macos(target: Target, version: str) -> tuple[Path, Path]:
    stage = WORK / "macos"
    app = stage / f"{APP_NAME}.app"
    resources = app / "Contents" / "Resources"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    resources.mkdir()
    python = install_python(target, resources / "python")
    package = install_packages(target, python, stage)
    check_same_sources(package)
    prune(package)
    compile_bytecode(python, package)
    install_programs(target, package)
    copy_licences(resources / "licences")
    launcher = app / "Contents" / "MacOS" / "orthostudio"
    launcher.write_text(macos_launcher(), encoding="utf-8")
    launcher.chmod(0o755)
    make_icns(resources / "orthostudio.icns")
    oldest = minimum_macos(wheel_tags(package))
    print(f"the app runs on macOS {oldest} and later", flush=True)
    check_oldest_macos(target.machine, oldest, for_this_mac=FOR_THIS_MAC[0])
    plist = macos_info_plist(version, oldest, target.machine)
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump(plist, f)
    image = stage / "image"
    image.mkdir()
    shutil.move(str(app), image / app.name)
    (image / "Applications").symlink_to("/Applications")
    out = DIST / artefact_name(version, target)
    out.unlink(missing_ok=True)
    # The image is written with zlib and then converted to LZMA. The same content: 134 MB with
    # zlib, 106 MB when `create` writes LZMA itself, 64 MB through `convert` -- which is also the
    # fastest of the three, since it compresses on every core (measured 2026-09-19). macOS mounts
    # LZMA images from 10.15, and this app asks for macOS 14 (MAC_OLDEST).
    plain = stage / "plain.dmg"
    make = [
        "hdiutil", "create", "-volname", f"{APP_NAME} {version}", "-srcfolder", image,
        "-ov", "-format", "UDZO", plain,
    ]  # fmt: skip
    # `hdiutil create` answers "Resource busy" now and then on a build machine, where Spotlight
    # and the mounts of other jobs are on the same disk: the tag build of 0.1.9 failed on it and
    # the same commit had packaged cleanly minutes before. It is the machine, not the image, so
    # it is asked again rather than reported.
    run_again_if_it_fails(make, tries=DMG_TRIES, wait_s=DMG_RETRY_S)
    run(["hdiutil", "convert", plain, "-format", "ULMO", "-o", out])
    plain.unlink()
    return out, image / app.name / "Contents" / "Resources" / "python" / "bin" / "python3"


def build_windows(target: Target, version: str) -> tuple[Path, Path]:
    stage = WORK / "windows"
    bundle = stage / APP_NAME
    bundle.mkdir(parents=True)
    python = install_python(target, bundle / "python")
    package = install_packages(target, python, stage)
    check_same_sources(package)
    prune(package)
    compile_bytecode(python, package)
    install_programs(target, package)
    copy_licences(bundle / "licences")
    icon = bundle / "orthostudio.ico"
    make_ico(icon)
    name = artefact_name(version, target)
    script = stage / "orthostudio-xp.iss"
    script.write_text(
        inno_setup_script(
            version, bundle, icon, DIST, name.removesuffix(".exe"), fetch_webview2(stage)
        ),
        encoding="utf-8",
    )
    iscc = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if not Path(iscc).is_file() and shutil.which(iscc) is None:
        raise SystemExit("Inno Setup 6 is missing (ISCC.exe): install it, then build again.")
    run([iscc, "/Q", script])
    archive = DIST / name.replace("-setup.exe", "")
    shutil.make_archive(str(archive), "zip", root_dir=stage, base_dir=APP_NAME)
    return DIST / name, python


def build_linux(target: Target, version: str) -> tuple[Path, Path]:
    stage = WORK / "linux"
    bundle = stage / FILE_STEM
    bundle.mkdir(parents=True)
    python = install_python(target, bundle / "python")
    package = install_packages(target, python, stage)
    check_same_sources(package)
    prune(package)
    compile_bytecode(python, package)
    install_programs(target, package)
    copy_licences(bundle / "licences")
    for name, text in (
        ("orthostudio-xp", linux_launcher()),
        ("install.sh", linux_install_script()),
    ):
        path = bundle / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    make_png(bundle / "orthostudio-xp.png")
    out = DIST / artefact_name(version, target)
    with tarfile.open(out, "w:gz") as tar:
        tar.add(bundle, arcname=FILE_STEM)
    return out, python


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def check(python: Path) -> None:
    """The packed engine, from its own Python, in a throw-away home: that Python is standalone
    (its prefix and standard library are in the installer, no ``pyvenv.cfg``), the doctor finds
    the packed Triangle4XP, DSFTool is the packed one, every module of the package imports (nothing
    the pruning removed is needed), the page and the API answer."""
    root = (python.parent if python.name == "python.exe" else python.parents[1]).resolve()
    with tempfile.TemporaryDirectory() as home:
        env = {k: v for k, v in clean_env().items() if k not in {"PYTHONPATH", "OSXP_TRIANGLE4XP"}}
        env |= {"OSXP_HOME": home, "PYTHONNOUSERSITE": "1"}
        where = "import os, sys; print(sys.prefix); print(os.__file__)"
        prefix, stdlib = (
            Path(line).resolve() for line in run([python, "-I", "-c", where], env=env).splitlines()
        )
        if (root / "pyvenv.cfg").exists() or prefix != root or not stdlib.is_relative_to(root):
            raise SystemExit(f"the packed Python is not standalone: {prefix}, {stdlib}")
        # the doctor's exit code also counts the machine (disk space, X-Plane): only its checks of
        # what the installer brings are read
        doctor = run([python, "-m", "orthostudio", "doctor", "--json"], env=env, check=False)
        checks = {c["name"]: c for c in json.loads(doctor)["checks"]}
        for name in ("python", "encoder", "http_client"):
            if checks[name]["status"] != "ok":
                raise SystemExit(f"the packed engine fails its {name} check: {checks[name]}")
        triangle = checks["triangle4xp"]
        if triangle["status"] != "ok" or not Path(
            triangle["details"]["path"]
        ).resolve().is_relative_to(root):
            raise SystemExit(f"the packed Triangle4XP is not the one found: {triangle}")
        code = "from orthostudio.overlays import find_dsftool; print(find_dsftool())"
        dsftool = Path(run([python, "-c", code], env=env)).resolve()
        if not dsftool.is_relative_to(root):
            raise SystemExit(f"the packed DSFTool is not the one found: {dsftool}")
        every_module = (
            "import importlib, pkgutil, orthostudio\n"
            "for m in pkgutil.walk_packages(orthostudio.__path__, 'orthostudio.'):\n"
            "    if not m.name.endswith('__main__'):\n"
            "        importlib.import_module(m.name)\n"
        )
        run([python, "-c", every_module], env=env)
        run([python, "-m", "orthostudio", "serve", "--check", "--port", str(free_port())], env=env)
    print("check: the packed engine runs from its own Python", flush=True)


def last_run(log: Path) -> str:
    """What the last start recorded in ``serve.log``: the lines after its last ``---`` header."""
    lines = log.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("--- ")]
    return "\n".join(lines[starts[-1] + 1 :]) if starts else ""


def run_launcher(launcher: Sequence[str | Path], args: Sequence[str], env: dict[str, str]) -> None:
    """Start the installed app the way its menu entry does, with ``args``; fails with its log."""
    print("+", *launcher, *args, flush=True)
    done = subprocess.run([*map(str, launcher), *args], env=env, check=False, capture_output=True)
    if done.returncode:
        raise SystemExit(f"the installed app failed ({done.returncode}): {' '.join(args)}")


def check_launch(
    launcher: Sequence[str | Path],
    installed: Path,
    env: dict[str, str],
    *,
    window: bool = False,
) -> None:
    """The installed app, through its launcher: the doctor, whose output is in the log, finds the
    Triangle4XP installed with it, and the server starts and answers.

    With ``window``, the app must also be able to show one (the doctor's ``window`` check): macOS
    carries the web view and the app carries the rest, so an app that would open the browser
    instead is one this build left something out of, and it does not ship."""
    where = "from orthostudio.desktop import log_path; print(log_path())"
    python = installed_python(installed)
    log = Path(run([python, "-c", where], env=env))
    run_launcher(launcher, ["doctor", "--json"], env)
    doctor = json.loads(last_run(log))
    triangle = next(c for c in doctor["checks"] if c["name"] == "triangle4xp")
    if triangle["status"] != "ok" or not Path(triangle["details"]["path"]).resolve().is_relative_to(
        installed.resolve()
    ):
        raise SystemExit(f"the installed app does not find its Triangle4XP: {triangle}")
    if window:
        shown = next(c for c in doctor["checks"] if c["name"] == "window")
        if shown["status"] != "ok":
            raise SystemExit(f"the installed app cannot show a window of its own: {shown}")
    run_launcher(launcher, ["serve", "--check", "--port", str(free_port())], env)
    print(f"check: the installed app starts ({log})", flush=True)


def start_engine(installed: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """The installed app's engine on its own port, left running: what a user has open while an
    update is installed over the app, or the app removed."""
    python = installed_python(installed)
    args = [str(python), "-m", "orthostudio", "serve", "--no-open", "--port", str(ENGINE_PORT)]
    print("+", *args, "&", flush=True)
    engine = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{ENGINE_PORT}/api/status"
    for _ in range(120):
        with contextlib.suppress(OSError), urllib.request.urlopen(url, timeout=2) as answer:
            if answer.status == 200:
                return engine
        if engine.poll() is not None:
            raise SystemExit(f"the installed engine stopped at once ({engine.returncode})")
        time.sleep(0.5)
    engine.kill()
    raise SystemExit("the installed engine did not answer within 60 s")


def engine_stopped(engine: subprocess.Popen[bytes], by: str) -> None:
    """Fails unless the engine left running has ended: ``by`` had to stop it."""
    try:
        engine.wait(timeout=60)
    except subprocess.TimeoutExpired:
        engine.kill()
        raise SystemExit(f"{by} left OrthoStudio XP running") from None


def installed_python(installed: Path) -> Path:
    """The Python of an installed app (``installed`` holds a ``python`` folder, or is the app)."""
    for candidate in (
        installed / "python" / "python.exe",
        installed / "python" / "bin" / "python3",
        installed / "Contents" / "Resources" / "python" / "bin" / "python3",
    ):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"no Python in {installed}")


LSREGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
    "/Support/lsregister"
)


def check_native_launch(app: Path, home: Path, machine: str = "arm64") -> None:
    """The app opened by macOS itself, as a double-click opens it. The Apple Silicon app's programs
    must not run under Rosetta (the doctor's ``architecture`` check): started from a terminal the
    launcher is always native; opened by macOS, a script launcher ran under Rosetta until the app
    declared ``LSArchitecturePriority`` (``macos_info_plist``). The Intel app, whose Python has no
    Apple Silicon code, only has to start and run its doctor.

    macOS opens a copy under another bundle identifier: given the path of an app whose identifier
    and version an installed app shares, ``open`` started the installed one."""
    copy = home / "launch" / app.name
    shutil.copytree(app, copy, symlinks=True)
    info_path = copy / "Contents" / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info["CFBundleIdentifier"] = f"{BUNDLE_ID}.check"
    info_path.write_bytes(plistlib.dumps(info))
    try:
        run(
            [
                "open", "-W", "-n", "--env", f"HOME={home}", "--env", f"OSXP_HOME={home / 'home'}",
                copy, "--args", "doctor", "--json",
            ]
        )  # fmt: skip
    finally:
        run([LSREGISTER, "-u", copy], check=False)
    log = home / "Library" / "Logs" / APP_NAME / "serve.log"
    checks = {c["name"]: c for c in json.loads(last_run(log))["checks"]}
    if machine != "arm64":
        print(f"check: opened by macOS, the {machine} app starts", flush=True)
        return
    arch = checks.get("architecture")
    if arch is None or arch["status"] != "ok":
        raise SystemExit(f"opened by macOS, the app runs its programs under Rosetta: {arch}")
    print("check: opened by macOS, the app runs its programs natively", flush=True)


def check_installer(target: Target, artefact: Path) -> None:
    """The installer itself, installed into a throw-away place and started from there: the image
    mounted read-only (macOS), the setup program run silently then its uninstaller (Windows), the
    archive extracted and its menu entry added then removed (Linux)."""
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        env = {k: v for k, v in clean_env().items() if k not in {"PYTHONPATH", "OSXP_TRIANGLE4XP"}}
        env |= {"OSXP_HOME": str(tmp / "home"), "PYTHONNOUSERSITE": "1"}
        if target.system == "macos":
            mount = tmp / "image"
            run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, artefact])
            try:
                app = mount / f"{APP_NAME}.app"
                env["HOME"] = str(tmp)
                check_launch([app / "Contents" / "MacOS" / "orthostudio"], app, env, window=True)
                check_native_launch(app, tmp, target.machine)
            finally:
                run(["hdiutil", "detach", "-force", mount], check=False)
        elif target.system == "windows":
            folder = tmp / APP_NAME
            setup = [artefact, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={folder}"]
            run([*setup, f"/LOG={tmp / 'setup.log'}"])
            check_launch([folder / "python" / "pythonw.exe", "-m", ENTRY_MODULE], folder, env)
            # Installed again, then removed, while the app runs: its python3.dll is in use, and
            # a setup program that does not stop it cannot replace the file (stop_running.pas).
            engine = start_engine(folder, env)
            run([*setup, f"/LOG={tmp / 'setup-over-running.log'}"])
            engine_stopped(engine, "installing over the app")
            engine = start_engine(folder, env)
            run([folder / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            engine_stopped(engine, "the uninstaller")
            for _ in range(60):  # the uninstaller works from a copy of itself, after it returns
                if not (folder / "python").exists():
                    break
                time.sleep(1)
            else:
                raise SystemExit(f"the uninstaller left {folder / 'python'}")
        else:
            with tarfile.open(artefact) as tar:
                tar.extractall(tmp, filter="tar")
            folder = tmp / FILE_STEM
            env["HOME"] = str(tmp)
            check_launch([folder / "orthostudio-xp"], folder, env)
            env["XDG_DATA_HOME"] = str(tmp / "data")
            entry = tmp / "data" / "applications" / "orthostudio-xp.desktop"
            run([folder / "install.sh"], env=env)
            if f'Exec="{folder}/orthostudio-xp"' not in entry.read_text(encoding="utf-8"):
                raise SystemExit(f"install.sh wrote a wrong menu entry: {entry}")
            run([folder / "install.sh", "--remove"], env=env)
            if entry.exists():
                raise SystemExit(f"install.sh --remove left {entry}")
    print("check: the installer installs an app that starts", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--check", action="store_true", help="then check the packed engine and the installer"
    )
    parser.add_argument(
        "--offline", action="store_true", help="take the packages from uv's cache only"
    )
    parser.add_argument(
        "--this-mac",
        action="store_true",
        help="macOS only: an app for this Mac, not for the release (its wheels may ask for a "
        "newer macOS than the README gives)",
    )
    parser.add_argument(
        "--machine",
        choices=sorted(MAC_UV_ARCH),
        help="macOS only: the machine of the app (x86_64: the Intel app, on Apple Silicon too)",
    )
    args = parser.parse_args(argv)
    if args.offline:
        UV_OFFLINE.append("--offline")
    FOR_THIS_MAC[0] = bool(getattr(args, "this_mac", False))
    target = this_target(args.machine)
    version = project_version()
    shutil.rmtree(WORK / target.system, ignore_errors=True)
    DIST.mkdir(exist_ok=True)
    builder = {"macos": build_macos, "windows": build_windows, "linux": build_linux}
    out, python = builder[target.system](target, version)
    print(f"{out} ({out.stat().st_size / 1e6:.0f} MB)", flush=True)
    if args.check:
        check(python)
        check_installer(target, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
