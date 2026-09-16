"""DSFTool (Laminar Research's X-Plane scenery tools) as a checked subprocess.

Ortho4XP read ``Popen.returncode`` without ``wait()`` (``O4_Overlay_Utils.py:98``), so
a crash of DSFTool was never detected, and never read it at all after ``text2dsf`` (line
189-197). Here every run is ``subprocess.run`` with a timeout, its exit code is checked, the
``# Result code: N`` trailer written by ``dsf2text`` is checked (DSFTool 2.3 exits 0 with
``Result code: 1`` on a file that is not a DSF), and the output file must exist.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orthostudio.errors import OsxpError
from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.programs import installed_program

__all__ = ["DSF_MAGIC", "DsfToolRun", "dsftool_platform_dir", "find_dsftool", "run_dsftool"]

DSF_MAGIC = b"XPLNEDSF"
_RESULT_PREFIX = b"# Result code:"
_TAIL_BYTES = 4096

Mode = Literal["dsf2text", "text2dsf"]


@dataclass(frozen=True, slots=True)
class DsfToolRun:
    """One successful DSFTool invocation."""

    command: tuple[str, ...]
    returncode: int
    seconds: float
    stdout: str


DSFTOOL_DIR = Path(__file__).resolve().parents[3] / "native" / "dsftool"
"""OrthoStudio XP's own DSFTool binaries, one per platform (``native/dsftool/README.md``)."""


def dsftool_platform_dir() -> str:
    """``mac``, ``win`` or ``lin``: the sub-directory of the binary for this platform, the same
    names as Ortho4XP's ``Utils/`` (``O4_Overlay_Utils.py:17-25``)."""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "win"
    return "lin"


def find_dsftool(*, own_dir: Path | None = None) -> Path | None:
    """OrthoStudio XP's DSFTool for this platform, if it is there and executable: the installer's
    copy, else ``native/dsftool/<os>/DSFTool[.exe]`` of the repository (or of ``own_dir`` in
    tests)."""
    if own_dir is None:
        installed = installed_program("DSFTool")
        if installed is not None:
            return installed
    sub = dsftool_platform_dir()
    exe = "DSFTool.exe" if sub == "win" else "DSFTool"
    path = (DSFTOOL_DIR if own_dir is None else Path(own_dir)) / sub / exe
    return path if path.is_file() and os.access(path, os.X_OK) else None


def _tail(text: str) -> str:
    return text[-_TAIL_BYTES:]


def _failed(
    cmd: list[str], src: Path, returncode: int | None, reason: str, stdout: str
) -> OsxpError:
    return OsxpError(
        "DSF_OVERLAY_TOOL_FAILED",
        context={
            "path": str(src),
            "returncode": returncode if returncode is not None else -1,
            "command": cmd,
            "reason": reason,
            "output_tail": _tail(stdout),
        },
        message=f"DSFTool failed on {src} ({reason}).",
    )


def _result_code(text_path: Path) -> int | None:
    """The ``# Result code: N`` trailer of a ``dsf2text`` output, ``None`` when absent."""
    size = text_path.stat().st_size
    with open(text_path, "rb") as f:
        f.seek(max(0, size - _TAIL_BYTES))
        tail = f.read()
    k = tail.rfind(_RESULT_PREFIX)
    if k < 0:
        return None
    try:
        return int(tail[k + len(_RESULT_PREFIX) :].split()[0])
    except (IndexError, ValueError):
        return None


def run_dsftool(
    dsftool: Path, mode: Mode, src: Path, dst: Path, *, timeout_s: float = 600.0
) -> DsfToolRun:
    """``DSFTool --<mode> src dst`` with every failure turned into ``DSF_OVERLAY_TOOL_FAILED``.

    ``dsf2text`` also writes raster sidecars ``<dst>.<name>.raw`` next to ``dst``: run it in
    a work directory. ``text2dsf`` output is checked for the ``XPLNEDSF`` magic.
    """
    dsftool, src, dst = Path(dsftool), Path(src), Path(dst)
    cmd = [str(dsftool), f"--{mode}", str(src), str(dst)]
    if not dsftool.is_file():
        raise _failed(cmd, src, None, f"DSFTool binary {dsftool} not found", "")
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
            cwd=str(dst.parent),
            creationflags=NO_CONSOLE_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        raise _failed(cmd, src, None, f"timeout after {timeout_s:.0f} s", out) from exc
    except OSError as exc:
        raise _failed(cmd, src, None, f"could not start: {exc}", "") from exc
    seconds = time.perf_counter() - t0
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise _failed(cmd, src, proc.returncode, f"exit code {proc.returncode}", stdout)
    if not dst.is_file():
        raise _failed(cmd, src, proc.returncode, f"no output file {dst}", stdout)
    if mode == "dsf2text":
        code = _result_code(dst)
        if code is None:
            raise _failed(cmd, src, proc.returncode, "text output has no result code", stdout)
        if code != 0:
            raise _failed(cmd, src, code, f"result code {code} in text output", stdout)
    else:
        with open(dst, "rb") as f:
            magic = f.read(len(DSF_MAGIC))
        if magic != DSF_MAGIC:
            raise _failed(cmd, src, proc.returncode, "output is not a DSF file", stdout)
    return DsfToolRun(tuple(cmd), proc.returncode, seconds, stdout)
