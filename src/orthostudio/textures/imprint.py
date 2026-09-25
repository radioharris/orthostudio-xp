"""Water masks as the alpha channel of a texture.

Ported from Ortho4XP ``needs_mask`` (``src/O4_Mask_Utils.py:38-60``), the mask crop of
``convert_texture`` (``src/O4_Imagery_Utils.py:2318-2334, 2380-2408``) and the two layer rules
of ``combine_textures`` (``src/O4_Imagery_Utils.py:2182-2189, 2255-2265``). Behaviour spec in
``docs/specs/textures-imprint.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image, ImageFilter, UnidentifiedImageError

from orthostudio.errors import OsxpError

__all__ = [
    "MASK_SIDE",
    "MASK_THRESHOLD",
    "clean_halo_mask",
    "imprint",
    "load_mask",
    "mask_cell",
    "mask_crop",
    "mask_crop_raw",
    "mask_file_name",
    "mask_for_texture",
    "masks_dir_lookup",
    "needs_mask",
    "needs_mask_for_texture",
    "sea_blur_radius",
]

MASK_SIDE = 4096
"""Masks are built on the texture grid of ``mask_zl``: one 4096² image per texture."""

MASK_THRESHOLD = 30
"""A mask crop whose maximum is at most this value does not mask its texture (DXT1)."""

HALO_WHITE = 735
"""``sum(RGB) >= 735``: nearly white imagery (no-data of many providers)."""

HALO_BLACK = 35
"""``sum(RGB) <= 35``: nearly black imagery."""

MaskLookup = Callable[[int, int], "Path | None"]


class _TextureLike(Protocol):
    @property
    def til_x(self) -> int: ...

    @property
    def til_y(self) -> int: ...

    @property
    def zl(self) -> int: ...


def mask_file_name(m_til_x: int, m_til_y: int) -> str:
    """``<til_y>_<til_x>.png``: the file name of a mask, as Ortho4XP names it."""
    return f"{m_til_y}_{m_til_x}.png"


def masks_dir_lookup(masks_dir: Path) -> MaskLookup:
    """Lookup over a directory of masks, the masks artefact (``_dist.png`` files are ignored)."""

    def lookup(m_til_x: int, m_til_y: int) -> Path | None:
        path = masks_dir / mask_file_name(m_til_x, m_til_y)
        return path if path.is_file() else None

    return lookup


def load_mask(path: Path) -> np.ndarray:
    """Read a mask PNG as an L uint8 ``(4096, 4096)`` array."""
    try:
        with Image.open(path) as im:
            arr = np.asarray(im.convert("L"))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise OsxpError("MASK_FILE_UNREADABLE", context={"path": path, "reason": str(exc)}) from exc
    if arr.shape != (MASK_SIDE, MASK_SIDE):
        raise OsxpError(
            "MASK_FILE_UNREADABLE",
            context={"path": path, "reason": f"mask is {arr.shape[1]}x{arr.shape[0]}"},
        )
    return arr


def mask_cell(t: _TextureLike, mask_zl: int) -> tuple[int, int, int, int, int]:
    """``(m_til_x, m_til_y, x0, y0, side)``: mask texture of the ``mask_zl`` grid holding ``t``
    and the pixel window of ``t`` inside it (``O4_Mask_Utils.py:41-56``).

    Requires ``t.zl >= mask_zl``.
    """
    if t.zl < mask_zl:
        raise ValueError(f"texture zl {t.zl} is below mask_zl {mask_zl}")
    factor = 2 ** (t.zl - mask_zl)
    m_til_x = (int(t.til_x / factor) // 16) * 16
    m_til_y = (int(t.til_y / factor) // 16) * 16
    rx = int((t.til_x - factor * m_til_x) / 16)
    ry = int((t.til_y - factor * m_til_y) / 16)
    x0 = int(rx * MASK_SIDE / factor)
    y0 = int(ry * MASK_SIDE / factor)
    return m_til_x, m_til_y, x0, y0, MASK_SIDE // factor


def mask_for_texture(t: _TextureLike, mask_zl: int, mask_lookup: MaskLookup) -> np.ndarray | None:
    """Alpha mask of texture ``t`` at full size, or ``None`` when the texture is not masked.

    ``None`` when ``t.zl < mask_zl``, when ``mask_lookup(m_til_x, m_til_y)`` is ``None`` or
    when the **raw** crop of the mask covering ``t`` is at most 30 everywhere (Ortho4XP
    ``needs_mask``, ``O4_Mask_Utils.py:56-60``: the threshold is tested before resampling).
    Otherwise the crop is resampled to 4096² with Pillow ``BICUBIC`` (identity when
    ``t.zl == mask_zl``). One rule for the module and the pipeline: a caller never has to
    apply :func:`needs_mask` to the resampled mask, whose bicubic overshoot could say True
    where Ortho4XP says False.
    """
    if t.zl < mask_zl:
        return None
    m_til_x, m_til_y, x0, y0, side = mask_cell(t, mask_zl)
    path = mask_lookup(m_til_x, m_til_y)
    if path is None:
        return None
    full = load_mask(path)
    if not needs_mask(mask_crop_raw(full, x0, y0, side)):
        return None
    return mask_crop(full, x0, y0, side)


def needs_mask_for_texture(t: _TextureLike, mask_zl: int, mask_lookup: MaskLookup) -> bool:
    """The Ortho4XP ``needs_mask`` decision for ``t``: a usable mask whose raw crop exceeds 30."""
    if t.zl < mask_zl:
        return False
    m_til_x, m_til_y, x0, y0, side = mask_cell(t, mask_zl)
    path = mask_lookup(m_til_x, m_til_y)
    if path is None:
        return False
    return needs_mask(mask_crop_raw(load_mask(path), x0, y0, side))


def mask_crop_raw(full: np.ndarray, x0: int, y0: int, side: int) -> np.ndarray:
    """Sub-square ``(x0, y0, side)`` of a 4096² mask, not resampled (Ortho4XP ``small_img``).

    This is what the threshold is evaluated on and what Ortho4XP saves as the external border
    PNG when ``imprint_masks_to_dds`` is false (``O4_DSF_Utils.py:737-742``).
    """
    return np.ascontiguousarray(full[y0 : y0 + side, x0 : x0 + side])


def mask_crop(full: np.ndarray, x0: int, y0: int, side: int) -> np.ndarray:
    """Sub-square ``(x0, y0, side)`` of a 4096² mask resampled to 4096² (Pillow ``BICUBIC``).

    Identity when ``side == 4096``. The window comes from :func:`mask_cell`; the pipeline
    calls this with the window it stored in the texture's recipe.
    """
    if side == MASK_SIDE and x0 == 0 and y0 == 0:
        return full
    resized = Image.fromarray(mask_crop_raw(full, x0, y0, side), mode="L").resize(
        (MASK_SIDE, MASK_SIDE), Image.Resampling.BICUBIC
    )
    return np.asarray(resized)


def needs_mask(mask: np.ndarray) -> bool:
    """``max > 30``: a mask that is (nearly) all land does not mask its texture.

    Evaluate it on the raw crop (:func:`mask_crop_raw`), as Ortho4XP does, never on the
    resampled one.
    """
    return int(mask.max()) > MASK_THRESHOLD


def sea_blur_radius(sea_texture_blur: float, zl: int) -> float:
    """Gaussian radius in pixels: ``sea_texture_blur * 2**(zl - 17)`` (metres to ZL17 px)."""
    return float(sea_texture_blur) * 2.0 ** (zl - 17)


def clean_halo_mask(mask: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Ortho4XP anti-halo: zero the mask in its transition zone over white or black imagery.

    Where ``1 <= mask <= 253`` and ``sum(RGB) >= 735`` or ``<= 35`` the mask is set to 0, so a
    layer's no-data border is not blended in (``O4_Imagery_Utils.py:2255-2265``). Returns a
    new array.
    """
    if mask.shape != rgb.shape[:2]:
        raise ValueError("mask and rgb sizes differ")
    total = rgb.astype(np.uint16).sum(axis=2)
    transition = (mask >= 1) & (mask <= 253)
    out = mask.copy()
    out[transition & ((total >= HALO_WHITE) | (total <= HALO_BLACK))] = 0
    return out


_ROWS = 512
"""Rows converted to float at a time, as in ``colour.py``."""


def _check_pair(rgb: np.ndarray, alpha: np.ndarray) -> None:
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb must be a (h, w, 3) uint8 array")
    if alpha.dtype != np.uint8 or alpha.shape != rgb.shape[:2]:
        raise ValueError("alpha must be a (h, w) uint8 array of the same size as rgb")


def imprint(
    rgb: np.ndarray,
    alpha: np.ndarray,
    *,
    sea_texture_blur: float = 0.0,
    zl: int,
    clean_halo: bool = False,
) -> np.ndarray:
    """RGBA texture: ``alpha`` becomes the fourth channel (Ortho4XP ``putalpha``).

    With ``sea_texture_blur > 0`` the imagery is Gaussian-blurred with
    :func:`sea_blur_radius` and blended in where the mask says water, weighted by
    ``(255 - alpha) / 255`` (OrthoStudio XP extension of the combined-provider rule; see the spec).
    With ``clean_halo`` the alpha is first passed through :func:`clean_halo_mask`.
    """
    _check_pair(rgb, alpha)
    if clean_halo:
        alpha = clean_halo_mask(alpha, rgb)
    out = np.empty((*rgb.shape[:2], 4), dtype=np.uint8)
    radius = sea_blur_radius(sea_texture_blur, zl) if sea_texture_blur > 0 else 0.0
    if radius > 0:
        blurred = np.asarray(Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(radius)))
        # band by band, as the colours are: mixed in one go this held five full-size float
        # arrays at once, 750 MB above the image on a 4096 texture, against the 250 MB the rule
        # declares and the scheduler counts on. Several textures encode at once, so a machine
        # with little memory swapped and lost the build (found in review, 2026-09-23). The blur
        # itself needs the whole image, since it reads across the bands.
        for start in range(0, rgb.shape[0], _ROWS):
            stop = start + _ROWS
            water = ((255 - alpha[start:stop].astype(np.float32)) / 255.0)[:, :, None]
            band = rgb[start:stop].astype(np.float32)
            band *= 1.0 - water
            band += blurred[start:stop].astype(np.float32) * water
            np.rint(band, out=band)
            out[start:stop, :, :3] = band.astype(np.uint8)
    else:
        out[:, :, :3] = rgb
    out[:, :, 3] = alpha
    return out
