"""BC1/BC3 encoding and the ``encode_dds`` entry point.

Encoder chain: ``ispc_texcomp`` in-process (preferred), then an ``nvcompress`` binary run as a
subprocess if one can be found, else ``EncoderUnavailableError`` with a remedy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import cache
from pathlib import Path
from typing import Literal

import numpy as np

from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.textures import dds
from orthostudio.textures.mips import MipMode, mip_chain
from orthostudio.textures.refine import refine_blocks

Encoder = Literal["auto", "ispc", "nvcompress"]

NVCOMPRESS_ENV = "OSXP_NVCOMPRESS"


class EncoderUnavailableError(RuntimeError):
    """No usable BC1/BC3 encoder; the message says how to get one."""


def _ispc():
    try:
        import ispc_texcomp
    except ImportError:  # pragma: no cover - depends on the environment
        return None
    return ispc_texcomp


def find_nvcompress() -> Path | None:
    """Locate an nvcompress binary: ``$OSXP_NVCOMPRESS``, then PATH."""
    explicit = os.environ.get(NVCOMPRESS_ENV)
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    on_path = shutil.which("nvcompress")
    return Path(on_path) if on_path else None


def available_encoders() -> list[str]:
    """Encoders usable right now, in priority order."""
    found: list[str] = []
    if _ispc() is not None:
        found.append("ispc")
    if find_nvcompress() is not None:
        found.append("nvcompress")
    return found


def _resolve(encoder: str) -> str:
    if encoder == "auto":
        available = available_encoders()
        if not available:
            raise EncoderUnavailableError(
                "no BC1/BC3 encoder available: install the 'ispc-texcomp' wheel "
                "(pip install ispc-texcomp) or put an 'nvcompress' binary on PATH / in "
                f"${NVCOMPRESS_ENV}"
            )
        return available[0]
    if encoder == "ispc" and _ispc() is None:
        raise EncoderUnavailableError("ispc_texcomp is not importable in this environment")
    if encoder == "nvcompress" and find_nvcompress() is None:
        raise EncoderUnavailableError(f"nvcompress not found on PATH or in ${NVCOMPRESS_ENV}")
    if encoder not in ("ispc", "nvcompress"):
        raise ValueError(f"unknown encoder {encoder!r}")
    return encoder


def as_rgba(image: np.ndarray) -> np.ndarray:
    """Return a C-contiguous (h, w, 4) uint8 view/copy; an RGB input gets an opaque alpha."""
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError("expected a (height, width, 3|4) uint8 array")
    if image.shape[2] == 3:
        rgba = np.empty((*image.shape[:2], 4), dtype=np.uint8)
        rgba[:, :, :3] = image
        rgba[:, :, 3] = 255
        return rgba
    return np.ascontiguousarray(image)


def _pad_to_blocks(rgba: np.ndarray) -> np.ndarray:
    """Pad with edge replication so both sides are multiples of 4 (nvtt clamps the same way)."""
    h, w = rgba.shape[:2]
    ph, pw = (-h) % 4, (-w) % 4
    if ph == 0 and pw == 0:
        return rgba
    return np.pad(rgba, ((0, ph), (0, pw), (0, 0)), mode="edge")


def force_four_colour_mode(blocks: bytes | bytearray, fmt: str) -> bytes:
    """Rewrite BC1 colour blocks so that ``color0 > color1`` (or indices 0 when equal).

    X-Plane samples DXT1 in 3-colour mode as transparent black for index 3; both encoders in
    the chain already avoid it, this makes the invariant hold whatever the encoder.
    """
    fmt = dds.check_format(fmt)
    bs = dds.BLOCK_BYTES[fmt]
    arr = np.frombuffer(blocks, dtype=np.uint8).reshape(-1, bs).copy()
    colour = arr[:, bs - 8 :]
    c0 = colour[:, 0].astype(np.uint16) | (colour[:, 1].astype(np.uint16) << 8)
    c1 = colour[:, 2].astype(np.uint16) | (colour[:, 3].astype(np.uint16) << 8)
    swap = c0 < c1
    if swap.any():
        ends = colour[swap, 0:4].copy()
        colour[swap, 0:2] = ends[:, 2:4]
        colour[swap, 2:4] = ends[:, 0:2]
        colour[swap, 4:8] ^= 0x55  # idx 0<->1, 2<->3 in every 2-bit field
    equal = c0 == c1
    if equal.any():
        colour[equal, 4:8] = 0
    return arr.tobytes()


def count_three_colour_blocks(blocks: bytes | memoryview, fmt: str) -> tuple[int, int]:
    """Return ``(color0 < color1, color0 == color1 with a non-zero index)`` block counts."""
    fmt = dds.check_format(fmt)
    bs = dds.BLOCK_BYTES[fmt]
    arr = np.frombuffer(blocks, dtype=np.uint8).reshape(-1, bs)
    colour = arr[:, bs - 8 :]
    c0 = colour[:, 0].astype(np.uint16) | (colour[:, 1].astype(np.uint16) << 8)
    c1 = colour[:, 2].astype(np.uint16) | (colour[:, 3].astype(np.uint16) << 8)
    equal_bad = (c0 == c1) & (colour[:, 4:8] != 0).any(axis=1)
    return int((c0 < c1).sum()), int(equal_bad.sum())


@cache
def _single_colour_table(bits: int) -> tuple[np.ndarray, np.ndarray]:
    """For every 8-bit value, the endpoint pair whose 2/3-1/3 interpolant is closest to it."""
    e = np.arange(1 << bits)
    expanded = (e << (8 - bits)) | (e >> (2 * bits - 8))
    interp = (2 * expanded[:, None] + expanded[None, :] + 1) // 3  # decoder's palette entry 2
    err = np.abs(interp[None, :, :] - np.arange(256)[:, None, None])
    best = err.reshape(256, -1).argmin(axis=1)
    return (best // (1 << bits)).astype(np.uint16), (best % (1 << bits)).astype(np.uint16)


def encode_flat_blocks(blocks: bytes, rgba: np.ndarray, fmt: str) -> bytes:
    """Re-encode every block whose 16 pixels share one RGB with the optimal endpoint pair.

    ispc_texcomp quantises a uniform block to the nearest 5:6:5 colour (up to 4 codes off);
    nvtt, like stb_dxt, picks two endpoints whose interpolant lands within one code. Sea
    textures are 75-85 % uniform blocks, where this is worth 4-6 dB.
    """
    fmt = dds.check_format(fmt)
    h, w = rgba.shape[:2]
    if h % 4 or w % 4 or rgba.shape[2] != 4 or not rgba.flags.c_contiguous:
        raise ValueError("expected a C-contiguous RGBA image with sides multiple of 4")
    pixels = rgba.view(np.uint32).reshape(h // 4, 4, w // 4, 4) & np.uint32(0x00FFFFFF)
    same = (pixels == pixels[:, :1, :, :1]).all(axis=(1, 3))
    idx = np.flatnonzero(same)
    if idx.size == 0:
        return blocks
    colours = rgba.reshape(h // 4, 4, w // 4, 4, 4)[:, 0, :, 0, :3].reshape(-1, 3)[idx]
    r0, r1 = (t[colours[:, 0]] for t in _single_colour_table(5))
    g0, g1 = (t[colours[:, 1]] for t in _single_colour_table(6))
    b0, b1 = (t[colours[:, 2]] for t in _single_colour_table(5))
    c0 = (r0 << 11) | (g0 << 5) | b0
    c1 = (r1 << 11) | (g1 << 5) | b1
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    # every pixel uses palette entry 2, or entry 3 when the endpoints had to be swapped
    indices = np.where(swap, np.uint32(0xFFFFFFFF), np.uint32(0xAAAAAAAA))
    indices = np.where(c0 == c1, np.uint32(0), indices).astype(np.uint32)
    packed = np.empty((idx.size, 8), dtype=np.uint8)
    packed[:, 0] = c0 & 0xFF
    packed[:, 1] = c0 >> 8
    packed[:, 2] = c1 & 0xFF
    packed[:, 3] = c1 >> 8
    packed[:, 4:8] = indices.view(np.uint8).reshape(-1, 4)
    bs = dds.BLOCK_BYTES[fmt]
    arr = np.frombuffer(blocks, dtype=np.uint8).reshape(-1, bs).copy()
    arr[idx, bs - 8 :] = packed
    return arr.tobytes()


def _encode_blocks_ispc(rgba: np.ndarray, fmt: str) -> bytes:
    mod = _ispc()
    assert mod is not None
    padded = _pad_to_blocks(rgba)
    h, w = padded.shape[:2]
    surface = mod.RGBASurface(padded, w, h)
    if fmt == "bc1":
        # ispc_texcomp 1.0.1 allocates width*height bytes for BC1 (twice the block data);
        # the blocks are contiguous at the start, the tail is uninitialised.
        return mod.compress_blocks_bc1(surface)[: dds.level_size(w, h, fmt)]
    return mod.compress_blocks_bc3(surface)


def _run_nvcompress(rgba: np.ndarray, fmt: str, *, mips: bool) -> bytes:
    """Encode through nvcompress -fast, the way Ortho4XP does (PNG round-trip)."""
    from PIL import Image

    binary = find_nvcompress()
    if binary is None:
        raise EncoderUnavailableError("nvcompress not found")
    with tempfile.TemporaryDirectory(prefix="osxp-nvcompress-") as tmp:
        src = Path(tmp) / "in.png"
        dst = Path(tmp) / "out.dds"
        mode = "RGBA" if fmt == "bc3" else "RGB"
        Image.fromarray(rgba if fmt == "bc3" else rgba[:, :, :3], mode).save(src, compress_level=1)
        cmd = [str(binary), "-bc1" if fmt == "bc1" else "-bc3", "-fast"]
        if not mips:
            cmd.append("-nomips")
        cmd += [str(src), str(dst)]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, creationflags=NO_CONSOLE_WINDOW
        )
        if proc.returncode != 0 or not dst.is_file():
            raise EncoderUnavailableError(
                f"nvcompress failed (exit {proc.returncode}): {proc.stderr.strip()[:400]}"
            )
        return dst.read_bytes()


def _finish_level(blocks: bytes, rgba: np.ndarray, fmt: str, refine_passes: int) -> bytes:
    padded = _pad_to_blocks(rgba)
    blocks = encode_flat_blocks(blocks, padded, fmt)
    if refine_passes > 0:
        blocks = refine_blocks(blocks, padded, fmt, refine_passes)
    return force_four_colour_mode(blocks, fmt)


def encode_level(image: np.ndarray, fmt: str, *, refine_passes: int = 0) -> bytes:
    """ispc_texcomp blocks of one level plus the flat-block and 4-colour post-passes."""
    rgba = as_rgba(image)
    return _finish_level(_encode_blocks_ispc(rgba, fmt), rgba, fmt, refine_passes)


def encode_blocks(
    image: np.ndarray, fmt: str, *, encoder: Encoder = "auto", refine_passes: int = 0
) -> bytes:
    """Encode one level to raw ``fmt`` blocks (no header), 4-colour mode guaranteed."""
    fmt = dds.check_format(fmt)
    rgba = as_rgba(image)
    chosen = _resolve(encoder)
    if chosen == "ispc":
        blocks = _encode_blocks_ispc(rgba, fmt)
    else:
        _header, levels = dds.split_levels(_run_nvcompress(rgba, fmt, mips=False))
        blocks = bytes(levels[0])
    return _finish_level(blocks, rgba, fmt, refine_passes)


def encode_dds(
    image: np.ndarray,
    fmt: str,
    mips: bool = True,
    *,
    mip_mode: MipMode = "gamma22",
    encoder: Encoder = "auto",
    refine_passes: int = 0,
) -> bytes:
    """Encode an (h, w, 3|4) uint8 image to a complete DDS file image (header + levels).

    ``fmt`` is ``'bc1'`` (DXT1, alpha dropped) or ``'bc3'`` (DXT5). With ``mips`` the full
    chain down to 1x1 is generated with ``mip_chain(mode=mip_mode)``; sides must then be
    powers of two. ``refine_passes`` adds least-squares endpoint refinement (slow, numpy;
    2 passes match nvcompress -fast quality). The nvcompress fallback filters its own mips
    (linear light, box, truncating quantisation) and ignores ``mip_mode``.
    """
    fmt = dds.check_format(fmt)
    rgba = as_rgba(image)
    chosen = _resolve(encoder)
    h, w = rgba.shape[:2]
    if chosen == "nvcompress":
        data = _run_nvcompress(rgba, fmt, mips=mips)
        header, nv_levels = dds.split_levels(data)
        fixed = [force_four_colour_mode(bytes(lvl), fmt) for lvl in nv_levels]
        return dds.assemble(header.width, header.height, fmt, fixed)
    levels = mip_chain(rgba, mode=mip_mode) if mips else [rgba]
    blobs = [encode_level(lvl, fmt, refine_passes=refine_passes) for lvl in levels]
    return dds.assemble(w, h, fmt, blobs)
