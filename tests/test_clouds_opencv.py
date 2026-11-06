# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OpenCV for the clouds, downloaded at their first use, not shipped (``clouds.md`` C2)."""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from orthostudio.clouds import opencv
from orthostudio.errors import OsxpError

ROOT = Path(__file__).resolve().parents[1]


def test_the_wheels_are_those_uv_lock_pins() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    (pkg,) = [p for p in lock["package"] if p["name"] == "opencv-python-headless"]
    assert pkg["version"] == opencv.OPENCV_VERSION
    pinned = {w["url"]: (w["size"], w["hash"].removeprefix("sha256:")) for w in pkg["wheels"]}
    for key, wheel in opencv.WHEELS.items():
        assert pinned.get(wheel.url) == (wheel.size, wheel.sha256), key


def test_the_installer_leaves_opencv_out() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = " ".join(project["project"]["dependencies"])
    assert "opencv" not in names  # the packaged app installs these, no dev group
    assert any("opencv" in d for d in project["dependency-groups"]["dev"])


def test_the_engine_starts_without_opencv() -> None:
    code = (
        "import sys; sys.modules['cv2'] = None\n"
        "import orthostudio.api.app, orthostudio.pipeline.textures, orthostudio.clouds.model\n"
        "from orthostudio.clouds.opencv import cv2\n"
        "print('started')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "started"


def _wheel(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
def no_opencv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OSXP_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(opencv, "_installed_here", lambda: False)
    monkeypatch.setattr(opencv, "_MODULE", None)
    return tmp_path


def _serve(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    wheel = opencv.Wheel(
        "https://example.invalid/cv.whl", len(body), hashlib.sha256(body).hexdigest()
    )
    monkeypatch.setattr(opencv, "_wheel", lambda: wheel)

    def download(url: str, *, cancel: object = None, on_bytes: object = None) -> SimpleNamespace:
        assert url == wheel.url
        if callable(on_bytes):
            on_bytes(len(body), len(body))
        return SimpleNamespace(ok=True, body=body, status=200, error=None)

    monkeypatch.setattr("orthostudio.dem.sources.http_download", download)


def test_opencv_is_downloaded_checked_and_unpacked_once(
    no_opencv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _wheel({"cv2/__init__.py": b"VERSION = 'fake'\n", "cv2/data/x.txt": b"x"})
    _serve(monkeypatch, body)
    assert not opencv.opencv_ready() and opencv.opencv_bytes() == len(body)
    steps: list[tuple[int, int]] = []
    opencv.ensure_opencv(progress=lambda d, t: steps.append((d, t)))
    assert (opencv.opencv_dir() / "cv2" / "__init__.py").is_file()
    assert opencv.opencv_ready() and opencv.opencv_bytes() == 0
    assert steps == [(len(body), len(body))]
    assert not opencv.opencv_dir().with_name(opencv.opencv_dir().name + ".part").exists()


def test_a_wheel_that_is_not_the_one_pinned_is_refused(
    no_opencv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _wheel({"cv2/__init__.py": b""})
    _serve(monkeypatch, body)
    good = opencv._wheel()
    monkeypatch.setattr(opencv, "_wheel", lambda: opencv.Wheel(good.url, good.size, "0" * 64))
    with pytest.raises(OsxpError) as err:
        opencv.ensure_opencv()
    assert err.value.code == "CLOUD_DETECTOR_MISSING" and not opencv.opencv_ready()


def test_a_wheel_naming_a_file_out_of_its_folder_is_refused(
    no_opencv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _wheel({"cv2/__init__.py": b"", "../outside.txt": b"no"}))
    with pytest.raises(OsxpError):
        opencv.ensure_opencv()
    assert not (no_opencv / "home" / "libs" / "outside.txt").exists()
    assert not opencv.opencv_ready()


def test_without_opencv_its_first_use_says_so(no_opencv: Path) -> None:
    with pytest.raises(OsxpError) as err:
        opencv.cv2.dilate  # noqa: B018 - the first use is the attribute
    assert err.value.code == "CLOUD_DETECTOR_MISSING"
