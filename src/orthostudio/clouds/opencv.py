# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OpenCV for the clouds, downloaded at their first use (``clouds.md`` C2).

The detector's networks run on OpenCV, and a cloud's replacement does its pixel work with it.
Inside the installer it would have made it half as large again (35 to 61 MB more, compressed,
on 74 MB on the Mac, much of it video codecs the clouds never use), for a feature some never
open: it is fetched with the networks instead, once, as PyPI publishes it (one abi3 wheel per
system, which any CPython the app ships can import), checked against the digest ``uv.lock``
holds, and unpacked in ``$OSXP_HOME/libs/``. Where it is installed already (a checkout's
environment, the tests), that one is used.

``cv2`` here stands for the module: it is imported at its first use, so that the engine starts,
and builds the tiles without clouds, where it is not there.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import platform
import shutil
import sys
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from orthostudio.errors import OsxpError
from orthostudio.home import osxp_home

__all__ = ["OPENCV_VERSION", "WHEELS", "cv2", "ensure_opencv", "opencv_bytes", "opencv_ready"]

OPENCV_VERSION = "5.0.0.93"
"""opencv-python-headless, the version ``uv.lock`` pins (a test keeps the two the same)."""


@dataclass(frozen=True, slots=True)
class Wheel:
    url: str
    size: int
    sha256: str


_PYPI = "https://files.pythonhosted.org/packages/"
WHEELS: dict[tuple[str, str], Wheel] = {
    ("darwin", "arm64"): Wheel(
        _PYPI + "53/7c/8c8097891c509d98cd128493835c95631c80be6a8f37ed9d25716c2e16f1/"
        "opencv_python_headless-5.0.0.93-cp37-abi3-macosx_13_0_arm64.whl",
        48_322_581,
        "030ca5e0837a2963ab36ef896baa9767eb8d2b83353fb28af5a521e40dd8756f",
    ),
    ("darwin", "x86_64"): Wheel(
        _PYPI + "90/8c/eab2ad388c3cbab2a350c10c2ef19ce6bd099240afc31789032c996bab52/"
        "opencv_python_headless-5.0.0.93-cp37-abi3-macosx_14_0_x86_64.whl",
        34_782_894,
        "1e55af3abfb462eeeabe5c775f12bdb36216d8a93a3583d69e6bd6e1d6ba7d00",
    ),
    ("win32", "amd64"): Wheel(
        _PYPI + "b8/88/763b967f7efd7226b82c9fae16d560cba049b1f0c036647e65c610fd636e/"
        "opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl",
        43_825_962,
        "829717b6a95554f273e49e357cee3b3a2a26b6f4842fbc1bed2b45bdd8f87e0e",
    ),
    # manylinux2014 (glibc 2.17), the oldest Linux the app's own Python runs on
    ("linux", "x86_64"): Wheel(
        _PYPI + "2b/97/8170e9819764c47e436c130d3ff6cfb73b58f923eae9d3a03d8982b04aec/"
        "opencv_python_headless-5.0.0.93-cp37-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        56_563_598,
        "09a872a157c1376ab922a69bbf22f9a95bcc7b658a9d8b436a60212b02b2eeb4",
    ),
    ("linux", "aarch64"): Wheel(
        _PYPI + "ec/78/afca939f40ffe2b2380bfa86f812b2f7d4acc5a27b27dc41b49cad7ce7b4/"
        "opencv_python_headless-5.0.0.93-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        36_521_085,
        "10818d91510e05c04568ae12b5cd120779c70c01bf897b001a6221fe430df80f",
    ),
}

Progress = Callable[[int, int], None]
_LOCK = threading.Lock()
_MODULE: ModuleType | None = None


def opencv_dir() -> Path:
    return osxp_home() / "libs" / f"opencv-{OPENCV_VERSION}"


def _installed_here() -> bool:
    """Whether the environment has OpenCV of its own (a checkout's, the tests')."""
    return importlib.util.find_spec("cv2") is not None


def _unpacked() -> bool:
    return (opencv_dir() / "cv2" / "__init__.py").is_file()


def opencv_ready() -> bool:
    """Whether OpenCV can be imported without a download."""
    return _MODULE is not None or _installed_here() or _unpacked()


def opencv_bytes() -> int:
    """What a download of OpenCV weighs here, 0 when it is not needed."""
    if opencv_ready():
        return 0
    wheel = _wheel()
    return wheel.size if wheel is not None else 0


def _wheel() -> Wheel | None:
    """This system's wheel (Windows says ``AMD64``)."""
    return WHEELS.get((sys.platform, platform.machine().lower()))


def ensure_opencv(
    *, progress: Progress | None = None, cancel: threading.Event | None = None
) -> None:
    """Download and unpack OpenCV when it is not there (``CLOUD_DETECTOR_MISSING`` when it
    cannot be); ``progress(done, total)`` in bytes."""
    from orthostudio.dem.sources import http_download

    with _LOCK:
        if opencv_ready():
            return
        wheel = _wheel()
        if wheel is None:
            raise OsxpError(
                "CLOUD_DETECTOR_MISSING",
                context={"reason": f"no OpenCV for {sys.platform} {platform.machine()}"},
            )
        got = http_download(
            wheel.url,
            cancel=cancel,
            on_bytes=None if progress is None else lambda now, _all: progress(now, wheel.size),
        )
        if not got.ok:
            raise OsxpError(
                "CLOUD_DETECTOR_MISSING",
                context={"reason": f"OpenCV: {got.error or f'HTTP {got.status}'}"},
            )
        if len(got.body) != wheel.size or hashlib.sha256(got.body).hexdigest() != wheel.sha256:
            raise OsxpError(
                "CLOUD_DETECTOR_MISSING",
                context={"reason": "OpenCV is not the expected file (digest differs)"},
            )
        _unpack(got.body, opencv_dir())


def _unpack(body: bytes, target: Path) -> None:
    """The wheel's files under ``target``, put in place whole or not at all."""
    import io

    work = target.with_name(target.name + ".part")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for member in archive.infolist():
            dest = (work / member.filename).resolve()
            if not dest.is_relative_to(work.resolve()):  # a name climbing out of the folder
                raise OsxpError(
                    "CLOUD_DETECTOR_MISSING", context={"reason": f"OpenCV: {member.filename}"}
                )
            archive.extract(member, work)
            mode = member.external_attr >> 16
            if mode & 0o111:  # what was executable stays so (the libraries, on Linux)
                dest.chmod(mode & 0o777)
    shutil.rmtree(target, ignore_errors=True)
    work.rename(target)


def opencv() -> ModuleType:
    """The cv2 module: the environment's, or the one downloaded (``CLOUD_DETECTOR_MISSING``
    before it is)."""
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    with _LOCK:
        if _MODULE is None:
            if not _installed_here():
                if not _unpacked():
                    raise OsxpError(
                        "CLOUD_DETECTOR_MISSING", context={"reason": "OpenCV not downloaded"}
                    )
                sys.path.insert(0, str(opencv_dir()))
            _MODULE = importlib.import_module("cv2")
    return _MODULE


class _Lazy:
    """``cv2.name`` imports OpenCV at its first use."""

    def __getattr__(self, name: str) -> Any:
        return getattr(opencv(), name)


cv2: Any = _Lazy()
