# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The cloud detector: the two networks of OmniCloudMask, run by OpenCV (``clouds.md`` 4.1).

OmniCloudMask (DPIRD, MIT licence, https://github.com/DPIRD-DMA/OmniCloudMask, version 1.8.1)
tells clear ground, thick cloud, thin cloud and cloud shadow apart on satellite images of 10 to
50 m. Its networks were exported to ONNX and simplified (``tools/clouds/export_detector.py``),
the only form OpenCV runs both of; their answer is the original's on 99.995 % of pixels
(2026-11-03). They ask for red, green and near infrared; given red, green and blue, which is all
a photo source has, they found the clouds of the tiles tried and left their snow alone.

The files are downloaded once, with OpenCV (``clouds.opencv``) when it is not there, at the
user's word before the first search or outline, and checked against their digest.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from orthostudio.clouds.opencv import cv2, ensure_opencv, opencv_bytes, opencv_ready
from orthostudio.errors import OsxpError
from orthostudio.fsutil import atomic_write_bytes
from orthostudio.home import osxp_home

__all__ = [
    "CLEAR",
    "SHADOW",
    "THICK",
    "THIN",
    "classify",
    "detector_bytes",
    "detector_dir",
    "detector_ready",
    "ensure_detector",
]

CLEAR, THICK, THIN, SHADOW = 0, 1, 2, 3

DETECTOR_VERSION = "clouds-detector-1"
DOWNLOAD_BASE = (
    "https://github.com/radioharris/orthostudio-xp/releases/download/" + DETECTOR_VERSION + "/"
)


@dataclass(frozen=True, slots=True)
class Network:
    file: str
    size: int
    sha256: str


NETWORKS = (
    Network(
        "regnety_004.onnx",
        26_948_304,
        "d0416e4bac4bfccf367726991e77c493960c06846f91ca36cad1f9441a6e4103",
    ),
    Network(
        "edgenext_small.onnx",
        36_789_862,
        "da5cf204fec14ab6c05ffa3585564ca8f9ca89fbb7d37157ee8e33baf59f6ecb",
    ),
)
"""OmniCloudMask's two networks, regnety first: ``classify(regnety_alone=True)`` takes the first."""

PATCH = 1000
OVERLAP = 300
"""The networks' patch and how much two patches share, OmniCloudMask's own."""

Progress = Callable[[int, int], None]


def detector_dir() -> Path:
    return osxp_home() / "models" / DETECTOR_VERSION


def detector_ready() -> bool:
    """OpenCV there and both networks on the disk at their size (their digest was checked when
    they came)."""
    folder = detector_dir()
    return opencv_ready() and all(_present(folder, n) for n in NETWORKS)


def detector_bytes() -> int:
    """What the detector's download weighs here: OpenCV when it is not there, and the networks
    missing; 0 when it is ready."""
    folder = detector_dir()
    return opencv_bytes() + sum(n.size for n in NETWORKS if not _present(folder, n))


def ensure_detector(
    *, progress: Progress | None = None, cancel: threading.Event | None = None
) -> Path:
    """The detector's folder, OpenCV and its networks downloaded and checked first when they are
    not there (``CLOUD_DETECTOR_MISSING`` when they cannot be); ``progress(done, total)`` in
    bytes, for the whole download."""
    from orthostudio.dem.sources import http_download

    folder = detector_dir()
    total = detector_bytes()
    done = opencv_bytes()
    ensure_opencv(
        progress=None if progress is None else lambda now, _all: progress(now, total),
        cancel=cancel,
    )
    missing = [n for n in NETWORKS if not _present(folder, n)]
    for n in missing:
        got = http_download(
            DOWNLOAD_BASE + n.file,
            cancel=cancel,
            on_bytes=None
            if progress is None
            else lambda now, _all, base=done: progress(base + now, total),
        )
        if not got.ok:
            raise OsxpError(
                "CLOUD_DETECTOR_MISSING",
                context={"reason": f"{n.file}: {got.error or f'HTTP {got.status}'}"},
            )
        if len(got.body) != n.size or hashlib.sha256(got.body).hexdigest() != n.sha256:
            raise OsxpError(
                "CLOUD_DETECTOR_MISSING",
                context={"reason": f"{n.file} is not the expected file (digest differs)"},
            )
        atomic_write_bytes(folder / n.file, got.body)
        done += n.size
    return folder


def _present(folder: Path, n: Network) -> bool:
    path = folder / n.file
    return path.is_file() and path.stat().st_size == n.size


_NETS: dict[Path, list[cv2.dnn.Net]] = {}
_LOCK = threading.Lock()
_RUN = threading.Lock()
"""One answer of the networks at a time: a network read once is shared, and is no object two
threads may run at once (three images got at once by Tick all failed in OpenCV's
``buf.shape() == m.shape()``, a user, 2026-11-06). OpenCV runs each answer on every core."""


def _networks() -> list[cv2.dnn.Net]:
    """The two networks, read once per process."""
    folder = detector_dir()
    with _LOCK:
        nets = _NETS.get(folder)
        if nets is None:
            if not detector_ready():
                raise OsxpError("CLOUD_DETECTOR_MISSING", context={"reason": "not downloaded"})
            nets = [cv2.dnn.readNetFromONNX(str(folder / n.file)) for n in NETWORKS]
            _NETS[folder] = nets
        return nets


def _ramp() -> np.ndarray:
    """How much a patch's answer counts, pixel by pixel: less towards its edges, where the next
    patch sees the same ground with more around it (OmniCloudMask's gradient)."""
    side = np.full(PATCH, OVERLAP, np.float32)
    side[:OVERLAP] = np.arange(1, OVERLAP + 1)
    side[-OVERLAP:] = np.arange(OVERLAP, 0, -1)
    side /= OVERLAP
    return side[:, None] * side[None, :]


def _starts(length: int) -> list[int]:
    stride = PATCH - OVERLAP
    out = sorted({min(s, length - PATCH) for s in range(0, length, stride)})
    return out or [0]


def classify(
    rgb: np.ndarray,
    valid: np.ndarray | None = None,
    *,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    regnety_alone: bool = False,
) -> np.ndarray:
    """``CLEAR``, ``THICK``, ``THIN`` or ``SHADOW`` for every pixel of ``rgb`` (``uint8`` RGB).

    The answer of both networks, averaged; ``regnety_alone``, of the first one alone, in half the
    time: what the fine search's second reading and the sources' verdicts take (``detect``).
    Measured on two tiles (2026-11-04, the Mac): the fine reading 97 -> 44 s and 104 -> 48 s, the
    app's clouds found but one of 4 ha and twelve of 107 ha in 53 000, more small cumulus found,
    and the same clouds and nearly always the same best source when it judges the sources; the
    quick reading alone kept both, its list changing too much with one (21 clouds became 37).

    ``valid`` (``bool``) leaves out where there is no image: a patch with none is skipped, and
    what is missing is left out of a patch's statistics, as OmniCloudMask does with its no-data
    value. An image smaller than a patch is reflected out to one."""
    h0, w0 = rgb.shape[:2]
    if valid is None:
        valid = np.ones((h0, w0), bool)
    ph, pw = max(0, PATCH - h0), max(0, PATCH - w0)
    if ph or pw:
        rgb = cv2.copyMakeBorder(rgb, 0, ph, 0, pw, cv2.BORDER_REFLECT)
        valid = np.pad(valid, ((0, ph), (0, pw)), mode="reflect")
    h, w = rgb.shape[:2]
    nets = _networks()[:1] if regnety_alone else _networks()
    ramp = _ramp()
    ys, xs = _starts(h), _starts(w)
    total = len(ys) * len(xs)
    out = np.zeros((h, w), np.uint8)
    # the answers of one row of patches, a patch high: the rows above the next row's start take
    # nothing more and are decided then. The whole tile's took 2.4 GB on a tile read at 9 m for
    # the fine search (Mexico, 12 805 x 11 652 pixels, 2026-11-04)
    acc = np.zeros((4, PATCH, w), np.float32)
    top = ys[0]
    for j, y in enumerate(ys):
        if y > top:
            shift = y - top
            out[top:y] = acc[:, :shift].argmax(axis=0)
            acc[:, : PATCH - shift] = acc[:, shift:]
            acc[:, PATCH - shift :] = 0
            top = y
        for i, x in enumerate(xs):
            if cancel is not None and cancel.is_set():
                raise OsxpError("SYS_CANCELLED")
            ok = valid[y : y + PATCH, x : x + PATCH]
            if ok.any():
                patch = rgb[y : y + PATCH, x : x + PATCH].astype(np.float32) + 1.0
                chans = np.moveaxis(patch, 2, 0)
                mean = np.array([c[ok].mean() for c in chans], np.float32)[:, None, None]
                std = np.array([c[ok].std() for c in chans], np.float32)[:, None, None]
                norm = np.where(ok, (chans - mean) / np.where(std == 0, 1, std), 0)
                blob = np.ascontiguousarray(norm[None].astype(np.float32))
                logits = np.zeros((4, PATCH, PATCH), np.float32)
                with _RUN:
                    for net in nets:
                        net.setInput(blob)
                        logits += net.forward()[0]
                acc[:, :, x : x + PATCH] += logits / len(nets) * ramp
            if progress is not None:
                progress(j * len(xs) + i + 1, total)
    out[top : top + PATCH] = acc.argmax(axis=0)
    out[~valid] = CLEAR
    return out[:h0, :w0]


def patch_count(h: int, w: int) -> int:
    """How many patches :func:`classify` reads on a picture of ``h`` x ``w``: its progress, ahead
    of it."""
    return len(_starts(max(h, PATCH))) * len(_starts(max(w, PATCH)))
