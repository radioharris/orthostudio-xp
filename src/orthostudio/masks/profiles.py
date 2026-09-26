# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The three shoreline transition profiles: ``sand``, ``rocks`` and ``3steps``.

Spec: ``docs/specs/masks-build.md`` sections 2 and 4. Origin: ``O4_Mask_Utils.py:648-813``
(``blur_mask``) and ``:69-71`` (the grey level of inland water).

``sand`` and ``rocks`` are reproduced bit for bit, including Ortho4XP's uint8 truncation between
the two passes of the separable hat convolution; ``3steps`` replaces dozens of
blur-and-threshold dilations with one Euclidean distance transform and the same value ladder
(the profile only ever depended on the distance to the water boundary).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageFilter

from orthostudio.errors import OsxpError
from orthostudio.imagery.grid import webmercator_pixel_size
from orthostudio.masks.distance import edt_px, pil_blur_reach, pil_blur_support

__all__ = [
    "MASKING_MODES",
    "WATER_TRANSITION",
    "MaskingMode",
    "MasksWidth",
    "blur_mask",
    "blur_widths",
    "halo_px",
    "rocks_blur",
    "rocks_width",
    "sand_blur",
    "sand_width",
    "sea_level_for",
    "three_steps_blur",
    "three_steps_widths",
]

MaskingMode = Literal["sand", "rocks", "3steps"]
MasksWidth = float | int | list[float] | tuple[float, ...]

MASKING_MODES: tuple[str, ...] = ("sand", "rocks", "3steps")

WATER_TRANSITION: tuple[int, ...] = (
    0, 0, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 7, 7, 7, 8,
    8, 9, 9, 9, 10, 10, 11, 11, 12, 13, 13, 14, 15, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    23, 25, 27, 28, 29, 31, 32, 34, 35, 37, 38, 40, 42, 44, 46, 48, 50, 52, 55, 57, 60,
    63, 66, 69, 72, 75, 78, 81, 85, 88, 92, 95, 99, 102, 106, 111, 115, 118, 121, 125,
    129, 133, 137, 141, 145, 149, 153, 157, 161, 165, 169, 173, 177, 181, 185, 188, 192,
    196, 199, 203, 207, 210, 213, 217, 221, 223, 226, 229, 232, 235, 238, 240, 243, 245,
    247, 249, 251, 253,
)  # fmt: skip
"""One column of Ortho4XP's ``Utils/water_transition.png`` (128 rows; the 64 columns are equal).

Embedded rather than shipped as a PNG: it is 128 bytes of data and a package resource would
have to be declared in ``pyproject.toml``.
"""

_HALO_SLACK_PX = 2
"""Two pixels of safety on every computed halo; the crop is still proved by the tests."""


def sea_level_for(ratio_water: float) -> int:
    """Grey level of inland water (``O4_Mask_Utils.py:69-71``).

    Ortho4XP indexes ``water_transition.png`` at the *float* row ``127 * (1 - min(1, 0.1 + r))``
    and Pillow truncates it.
    """
    index = int(127 * (1 - min(1.0, 0.1 + ratio_water)))
    index = max(0, min(len(WATER_TRANSITION) - 1, index))
    return WATER_TRANSITION[index]


def _pxscal(lat: int, mask_zl: int) -> float:
    return webmercator_pixel_size(lat + 0.5, mask_zl)


def sand_width(masks_width: float, lat: int, mask_zl: int) -> int:
    """``int(masks_width / pxscal)`` (``O4_Mask_Utils.py:659-660``)."""
    return int(float(masks_width) / _pxscal(lat, mask_zl))


def rocks_width(masks_width: float, lat: int, mask_zl: int) -> float:
    """``masks_width / (2 * pxscal)`` (``O4_Mask_Utils.py:661-662``)."""
    return float(masks_width) / (2 * _pxscal(lat, mask_zl))


def three_steps_widths(masks_width: Sequence[float], lat: int, mask_zl: int) -> list[float]:
    """``[L / pxscal for L in masks_width]`` (``O4_Mask_Utils.py:663-664``)."""
    widths = list(masks_width)
    if len(widths) != 3:
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={
                "name": "masks_width",
                "value": masks_width,
                "type": "list of three numbers",
                "range": "three widths (in, middle, out) when masking_mode is '3steps'",
            },
        )
    pxscal = _pxscal(lat, mask_zl)
    return [float(w) / pxscal for w in widths]


def blur_widths(
    masking_mode: str, masks_width: MasksWidth, lat: int, mask_zl: int
) -> float | list[float]:
    """Ortho4XP's ``blur_width`` for a mode (``O4_Mask_Utils.py:658-664``), in pixels."""
    if masking_mode == "sand":
        return sand_width(_scalar(masks_width), lat, mask_zl)
    if masking_mode == "rocks":
        return rocks_width(_scalar(masks_width), lat, mask_zl)
    if masking_mode == "3steps":
        return three_steps_widths(_sequence(masks_width), lat, mask_zl)
    return 0.0


def _scalar(masks_width: MasksWidth) -> float:
    if isinstance(masks_width, (list, tuple)):
        raise OsxpError(
            "CFG_VALUE_INVALID",
            context={
                "name": "masks_width",
                "value": masks_width,
                "type": "number",
                "range": "a single width unless masking_mode is '3steps'",
            },
        )
    return float(masks_width)


def _sequence(masks_width: MasksWidth) -> Sequence[float]:
    if isinstance(masks_width, (list, tuple)):
        return masks_width
    raise OsxpError(
        "CFG_VALUE_INVALID",
        context={
            "name": "masks_width",
            "value": masks_width,
            "type": "list of three numbers",
            "range": "three widths (in, middle, out) when masking_mode is '3steps'",
        },
    )


def halo_px(
    masking_mode: str,
    masks_width: MasksWidth,
    lat: int,
    mask_zl: int,
    *,
    distance_masks_too: bool = False,
    full_margin: int = 1024,
) -> int:
    """Working margin the profile actually needs, at most ``full_margin``.

    ``sand`` is a convolution with a hat kernel of half-width ``blur_width - 1``, so a pixel
    of the final image can only depend on input pixels within that distance: the 1024 px
    margin of Ortho4XP can be cropped without changing a single byte (spec section 4). With
    ``distance_masks_too`` the margin also has to cover the saturation distance of the
    ``_dist`` mask. Every other mode keeps the full margin (Pillow's Gaussian chain reaches
    much further and its support is not exact).
    """
    if masking_mode == "sand":
        needed = max(0, sand_width(_scalar(masks_width), lat, mask_zl) - 1)
    elif masking_mode == "rocks":
        radius = rocks_width(_scalar(masks_width), lat, mask_zl)
        needed = max(
            pil_blur_support(radius / 1.7) + pil_blur_support(radius),
            pil_blur_support(2 ** (mask_zl - 14)),
        )
    elif masking_mode == "3steps":
        needed = _three_steps_reach(three_steps_widths(_sequence(masks_width), lat, mask_zl))
        needed += pil_blur_support(2)
    else:
        needed = 0
    if distance_masks_too:
        # the distance mask saturates at 255 units of 2 ** (16 - mask_zl) pixels
        needed = max(needed, math.ceil(255 / 2 ** (16 - mask_zl)) + 1)
    return min(full_margin, needed + _HALO_SLACK_PX)


def _hat_kernel(blur_width: int) -> NDArray[np.float64]:
    """``1, 2, ... bw, ... 2, 1`` over ``bw**2`` (``O4_Mask_Utils.py:667-671``)."""
    kernel = np.array(range(1, 2 * blur_width))
    kernel[blur_width:] = range(blur_width - 1, 0, -1)
    return kernel / blur_width**2


def _convolve_rows(array: NDArray[np.uint8], kernel: NDArray[np.float64]) -> NDArray[np.uint8]:
    """``numpy.convolve`` on every row, truncated back to uint8, identical rows computed once.

    The float64 arithmetic is Ortho4XP's: ``numpy.convolve`` sums through cblas ``ddot`` and the
    assignment into a uint8 array truncates. An exact integer double box filter (what the
    kernel mathematically is) differs on the pixels whose exact sum is a multiple of
    ``blur_width ** 2`` -- see the spec. Memoising identical rows is the only shortcut taken.
    """
    out = np.empty_like(array)
    cache: dict[bytes, NDArray[np.uint8]] = {}
    for i in range(array.shape[0]):
        row = array[i]
        key = row.tobytes()
        value = cache.get(key)
        if value is None:
            value = np.convolve(row, kernel, "same").astype(np.uint8)
            cache[key] = value
        out[i] = value
    return out


def sand_blur(pre: NDArray[np.uint8], blur_width: int) -> NDArray[np.uint8]:
    """``sand``: separable hat convolution, then ``2 * min(x, 127)`` (``:665-679``)."""
    kernel = _hat_kernel(blur_width)
    blurred = _convolve_rows(pre, kernel)
    blurred = _convolve_rows(np.ascontiguousarray(blurred.T), kernel).T
    return np.asarray(2 * np.minimum(blurred, 127), dtype=np.uint8)


def _gaussian(array: NDArray[np.uint8], radius: float) -> NDArray[np.uint8]:
    image = Image.fromarray(array).convert("L").filter(ImageFilter.GaussianBlur(radius))
    return np.array(image, dtype=np.uint8)


def rocks_blur(pre: NDArray[np.uint8], blur_width: float, mask_zl: int) -> NDArray[np.uint8]:
    """``rocks``: grow, blur, ``tan``-based gamma curve, shore smoothing (``:680-724``)."""
    grown = (_gaussian(pre, blur_width / 1.7) > 0).astype(np.uint8) * 255
    blurred = _gaussian(grown, blur_width)
    gamma = 2.5
    curved = (
        (
            (
                np.tan((blurred.astype(np.float32) - 127.5) / 128 * math.atan(3))
                - np.tan(-127.5 / 128 * math.atan(3))
            )
            * 254
            / (2 * np.tan(127.5 / 128 * math.atan(3)))
        )
        ** gamma
        / (255 ** (gamma - 1))
    ).astype(np.uint8)
    return np.maximum(curved, _gaussian(pre, 2 ** (mask_zl - 14)))


def _transition_profile(ratio: float, kind: str) -> float:
    """``O4_Mask_Utils.py:650-656``."""
    if kind == "spline":
        return 3 * ratio**2 - 2 * ratio**3
    if kind == "linear":
        return ratio
    return 2 * ratio - ratio**2  # parabolic


def _three_steps_ladder(widths: list[float], sea_level: int) -> tuple[list[float], list[int]]:
    """Distance thresholds (pixels) and the value painted up to each of them.

    Ortho4XP grows the water mask one ``GaussianBlur(1) > 0`` step at a time and paints whatever
    the step newly covers, so the value of a pixel depends on nothing but how many steps are
    needed to reach it (``O4_Mask_Utils.py:726-806``).
    """
    transin, midzone, transout = widths
    shore_level = 255
    steps_in = int(transin / 3)
    steps_out = int(transout / 3)
    step = pil_blur_reach(1.0)
    sea_b_radius = midzone / 3
    sea_b_radius_buffered = (midzone + transout) / 3
    mid = pil_blur_reach(sea_b_radius_buffered) - pil_blur_reach(
        sea_b_radius_buffered - sea_b_radius
    )
    bounds: list[float] = []
    values: list[int] = []
    for i in range(steps_in):
        ratio = (i + 1) / steps_in
        bounds.append((i + 1) * step)
        ramp = _transition_profile(ratio, "parabolic") * (sea_level - shore_level)
        values.append(int(shore_level + ramp))
    base = steps_in * step + mid
    bounds.append(base)
    values.append(sea_level)
    for i in range(steps_out):
        ratio = (i + 1) / steps_out
        bounds.append(base + (i + 1) * step)
        values.append(int(sea_level * (1 - _transition_profile(ratio, "linear"))))
    return bounds, values


def _three_steps_reach(widths: list[float]) -> int:
    """How far from the shore the ``3steps`` profile can still paint a non-zero value."""
    bounds, _ = _three_steps_ladder(widths, 0)
    return math.ceil(bounds[-1]) if bounds else 0


def three_steps_blur(
    pre: NDArray[np.uint8], widths: list[float], sea_level: int, mask_zl: int
) -> NDArray[np.uint8]:
    """``3steps`` by distance transform (spec section 4; Ortho4XP's loop is ``:726-810``).

    The value ladder of :func:`_three_steps_ladder` is applied to a Euclidean distance
    transform instead of to dozens of blur-and-threshold dilations. Ortho4XP's dilation is a
    separable, 8-bit-quantised square: it reaches ``3 k`` pixels along the axes after ``k``
    steps but only about ``2.5 k`` along a diagonal, so the Euclidean transform overshoots
    the diagonals by about a sixth of a step. Tolerance measured in the spec.
    """
    del mask_zl
    bounds, values = _three_steps_ladder(widths, sea_level)
    if not bounds:
        return _gaussian(np.array(pre), 2)
    water = pre == 0
    distance = edt_px(water)
    table = np.array([*values, 0], dtype=np.uint8)
    index = np.searchsorted(np.asarray(bounds, dtype=np.float32), distance, side="left")
    out = np.where(water, table[index], pre)
    return _gaussian(np.ascontiguousarray(out, dtype=np.uint8), 2)


def blur_mask(
    pre: NDArray[np.uint8],
    *,
    masking_mode: str,
    masks_width: MasksWidth,
    lat: int,
    mask_zl: int,
    sea_level: int,
) -> NDArray[np.uint8]:
    """``blur_mask`` of Ortho4XP (``O4_Mask_Utils.py:648-813``), profile by profile."""
    if masking_mode == "sand":
        width = sand_width(_scalar(masks_width), lat, mask_zl)
        return sand_blur(pre, width) if width else np.array(pre)
    if masking_mode == "rocks":
        radius = rocks_width(_scalar(masks_width), lat, mask_zl)
        return rocks_blur(pre, radius, mask_zl) if radius else np.array(pre)
    if masking_mode == "3steps":
        widths = three_steps_widths(_sequence(masks_width), lat, mask_zl)
        return three_steps_blur(pre, widths, sea_level, mask_zl)
    return np.array(pre)
