# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The kit to fly OrthoStudio XP Live's test flights on a Windows PC, a user's Shadow (2026-10-01;
since 2026-10-04 those of the app's one window): one zip, in ``dist``, which the user unpacks there
and runs in three steps (``tools/live/windows_kit``, its ``LISEZ-MOI.txt``):

1. Live's tests on that Windows (``uv``, installed once asked, runs them), with the stand-ins for
   X-Plane built ahead here (``tests/data/live/xpl_host.c``, ``fake_xplane.c`` and their fake
   ``XPLM_64.dll``): a Windows has no compiler, and with them the tests load the very plugins
   X-Plane will load, and fly a test flight's series from its start to its end;
2. the app, in its one window, to see Live's region is there;
3. the replayable test flights (``tools/live/flights``): Live alone in the app, and with
   OrthoStudio XP open beside it, each cold, side by side; zipped to be sent back.

The code is the commit given (``HEAD``), as git has it: nothing uncommitted, nothing ignored. The
stand-in is built with Docker, as the plugins are (``tools/live/plugin_builds.py``).

    uv run python tools/live/windows_kit.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from plugin_builds import DOCKERFILE, IMAGE, docker

REPO = Path(__file__).resolve().parents[2]
KIT = Path(__file__).resolve().parent / "windows_kit"
CODE = "OrthoStudio XP Live"  # the code's folder in the zip
HELPERS = "essai-windows"  # the scripts' own files, and the stand-in
STEPS = (
    "1 - Tests de Live.cmd",
    "2 - Ouvrir l'app.cmd",
    "3 - Vols de test.cmd",
    "LISEZ-MOI.txt",
)
HELPER_FILES = ("uv.cmd", "tests.py", "vol.py")
NAME = "OrthoStudio XP - vols de la fenêtre unique - Windows"


STAND_INS = ("xpl_host.exe", "fake_xplane.exe", "XPLM_64.dll")


def stand_in(out: Path) -> None:
    """``xpl_host.exe``, ``fake_xplane.exe`` (X-Plane's stand-in for a test flight's series,
    ``tests/test_live_flights_series.py``) and their fake ``XPLM_64.dll`` into ``out``, built with
    MinGW-w64."""
    docker("build", "--platform", "linux/amd64", "-t", IMAGE, "-", stdin=DOCKERFILE)
    script = (
        "set -e; cd /out; "
        "x86_64-w64-mingw32-gcc -O2 -Wall -Werror -shared -DNO_MAIN -static-libgcc -s "
        "-o XPLM_64.dll /data/fake_xplm.c -Wl,--out-implib,/tmp/libXPLM_64.a; "
        "x86_64-w64-mingw32-gcc -O2 -Wall -Werror -static-libgcc -s "
        "-o xpl_host.exe /data/xpl_host.c /tmp/libXPLM_64.a; "
        "x86_64-w64-mingw32-gcc -O2 -Wall -Werror -static-libgcc -s "
        "-o fake_xplane.exe /data/fake_xplane.c /tmp/libXPLM_64.a -lws2_32"
    )
    data = REPO / "tests" / "data" / "live"
    docker(
        "run", "--rm", "--platform", "linux/amd64", "-v", f"{data}:/data:ro", "-v", f"{out}:/out",
        IMAGE, "sh", "-c", script,
    )  # fmt: skip


def crlf(data: bytes) -> bytes:
    """Windows' line ends: cmd.exe misreads a script's labels without them."""
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def build(commit: str, dist: Path) -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    target = dist / f"{NAME}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        code = work / "code.zip"
        subprocess.run(
            ["git", "-C", str(REPO), "archive", "--format=zip", f"--prefix={NAME}/{CODE}/",
             "-o", str(code), commit],
            check=True,
        )  # fmt: skip
        stand_in(work)
        tmp_zip = target.with_suffix(".tmp")
        with (
            zipfile.ZipFile(code) as source,
            zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z,
        ):
            for item in source.infolist():
                z.writestr(item, source.read(item))
            for step in STEPS:
                z.writestr(f"{NAME}/{step}", crlf((KIT / step).read_bytes()))
            for helper in HELPER_FILES:
                data = (KIT / helper).read_bytes()
                z.writestr(
                    f"{NAME}/{HELPERS}/{helper}", crlf(data) if helper.endswith(".cmd") else data
                )
            for built in STAND_INS:
                z.write(work / built, f"{NAME}/{HELPERS}/{built}")
        shutil.move(tmp_zip, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", default="HEAD", help="the code put in the kit (HEAD)")
    parser.add_argument("--dist", type=Path, default=REPO / "dist")
    args = parser.parse_args()
    if shutil.which("docker") is None:
        print("Docker is needed: https://www.docker.com", file=sys.stderr)
        return 1
    target = build(args.commit, args.dist)
    print(f"{target} ({target.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
