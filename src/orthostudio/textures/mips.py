# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Mip chain generation with a 2x2 box filter, in linear light or on stored values.

nvtt (the encoder behind Ortho4XP) filters RGB in linear light with a 2.2 power law and
alpha on its stored values; ``mode='gamma22'`` reproduces that (``docs/specs/textures-dds.md``).
"""

from __future__ import annotations

from functools import cache
from typing import Literal

import numpy as np

MipMode = Literal["gamma22", "srgb", "none"]
MIP_MODES: tuple[str, ...] = ("gamma22", "srgb", "none")


def _check_rgba(rgba: np.ndarray) -> np.ndarray:
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] not in (3, 4):
        raise ValueError("expected a (height, width, 3|4) uint8 array")
    return rgba


@cache
def to_linear_lut(mode: str) -> np.ndarray:
    """256-entry float32 table: stored code -> linear light in [0, 1]."""
    v = np.arange(256, dtype=np.float64) / 255.0
    if mode == "gamma22":
        lin = v**2.2
    elif mode == "srgb":
        lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    else:
        raise ValueError(f"no transfer curve for mip mode {mode!r}")
    return lin.astype(np.float32)


def from_linear(lin: np.ndarray, mode: str) -> np.ndarray:
    """Linear light float32 in [0, 1] -> stored uint8 code, rounded to nearest."""
    if mode == "gamma22":
        enc = np.power(lin, np.float32(1 / 2.2))
    elif mode == "srgb":
        enc = np.where(
            lin <= 0.0031308,
            lin * np.float32(12.92),
            np.power(lin, np.float32(1 / 2.4)) * np.float32(1.055) - np.float32(0.055),
        )
    else:
        raise ValueError(f"no transfer curve for mip mode {mode!r}")
    enc *= np.float32(255)
    enc += np.float32(0.5)
    np.clip(enc, 0, 255, out=enc)
    return enc.astype(np.uint8)


def box_down_uint8(a: np.ndarray) -> np.ndarray:
    """Halve an (h, w, c) uint8 array with a box filter, rounding to nearest.

    Sides of length 1 are kept (the filter degenerates to 2 taps, then to a copy).
    """
    h, w = a.shape[:2]
    if h > 1 and w > 1:
        h, w = h // 2 * 2, w // 2 * 2
        s = a[0:h:2, 0:w:2].astype(np.uint16)
        s += a[1:h:2, 0:w:2]
        s += a[0:h:2, 1:w:2]
        s += a[1:h:2, 1:w:2]
        s += 2
        s >>= 2
    elif w > 1:
        w = w // 2 * 2
        s = a[:, 0:w:2].astype(np.uint16)
        s += a[:, 1:w:2]
        s += 1
        s >>= 1
    elif h > 1:
        h = h // 2 * 2
        s = a[0:h:2].astype(np.uint16)
        s += a[1:h:2]
        s += 1
        s >>= 1
    else:
        return a.copy()
    return s.astype(np.uint8)


def box_down_float(a: np.ndarray) -> np.ndarray:
    """Halve an (h, w, c) float32 array with a box filter (sides of length 1 are kept)."""
    h, w = a.shape[:2]
    if h > 1 and w > 1:
        h, w = h // 2 * 2, w // 2 * 2
        s = a[0:h:2, 0:w:2] + a[1:h:2, 0:w:2]
        s += a[0:h:2, 1:w:2]
        s += a[1:h:2, 1:w:2]
        s *= np.float32(0.25)
    elif w > 1:
        w = w // 2 * 2
        s = a[:, 0:w:2] + a[:, 1:w:2]
        s *= np.float32(0.5)
    elif h > 1:
        h = h // 2 * 2
        s = a[0:h:2] + a[1:h:2]
        s *= np.float32(0.5)
    else:
        return a.copy()
    return s


def _gather_box_linear(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Level 1 in linear light straight from uint8 level 0: LUT gathers, no full-size float."""
    h, w = rgb.shape[:2]
    if h > 1 and w > 1:
        h, w = h // 2 * 2, w // 2 * 2
        lin = lut[rgb[0:h:2, 0:w:2]]
        lin += lut[rgb[1:h:2, 0:w:2]]
        lin += lut[rgb[0:h:2, 1:w:2]]
        lin += lut[rgb[1:h:2, 1:w:2]]
        lin *= np.float32(0.25)
        return lin
    return box_down_float(lut[rgb])


def mip_chain(
    rgba: np.ndarray, *, mode: MipMode = "gamma22", levels: int | None = None
) -> list[np.ndarray]:
    """Return ``[level0, level1, ...]`` down to 1x1 (or ``levels`` entries); level0 is the input.

    Sides must be powers of two so that every level is an exact 2x2 average of the previous
    one. In ``gamma22``/``srgb`` modes the RGB chain is carried in float32 linear light and
    quantised only for output, so rounding does not accumulate; alpha is always averaged on
    its stored values. ``none`` averages stored values (fast, darkens distant mips).
    """
    rgba = _check_rgba(rgba)
    if mode not in MIP_MODES:
        raise ValueError(f"unknown mip mode {mode!r}, expected one of {MIP_MODES}")
    h, w = rgba.shape[:2]
    if h & (h - 1) or w & (w - 1):
        raise ValueError(f"mip_chain needs power-of-two sides, got {w}x{h}")
    max_levels = max(w, h).bit_length()
    count = max_levels if levels is None else max(1, min(levels, max_levels))
    out = [rgba]
    if count == 1:
        return out
    if mode == "none":
        while len(out) < count:
            out.append(box_down_uint8(out[-1]))
        return out
    lin = _gather_box_linear(rgba[:, :, :3], to_linear_lut(mode))
    alpha = box_down_uint8(rgba[:, :, 3:4]) if rgba.shape[2] == 4 else None
    while True:
        level = np.empty((*lin.shape[:2], rgba.shape[2]), dtype=np.uint8)
        level[:, :, :3] = from_linear(lin, mode)
        if alpha is not None:
            level[:, :, 3:4] = alpha
        out.append(level)
        if len(out) >= count:
            return out
        lin = box_down_float(lin)
        if alpha is not None:
            alpha = box_down_uint8(alpha)
