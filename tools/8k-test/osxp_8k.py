# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""OSXP 8K test: an OrthoStudio XP tile's 4K photos glued four by four into 8K, to measure.

For every OrthoStudio XP tile given, two new packs are written:

  <out>/4K/zOSXP_4K_<tile>   the tile's own 4K photos (hard links: no copy on the same disk)
  <out>/8K/zOSXP_8K_<tile>   the same photos glued four by four into 8192 px textures

Both DSFs go through the same DSFTool round trip and get one patch per terrain, so the two packs
differ only by their textures. The photos are not recompressed: their DXT blocks are copied, and
every texture coordinate is checked against the ground it covers before anything is written.

Started by its launcher ("OSXP 8K test.command" on macOS, "OSXP 8K test.bat" on Windows,
osxp-8k-test.sh on Linux), it runs with OrthoStudio XP's own Python, which has numpy and DSFTool,
and asks for the tile folders when none were given. Otherwise:
  <OrthoStudio XP's python> osxp_8k.py [--out FOLDER] [TILE_FOLDER ...]
The default <out> is an "OSXP 8K test" folder beside the folder holding the tiles.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from math import atan, cos, exp, log, pi, tan
from pathlib import Path

import numpy as np

EARTH_RADIUS = 6378137.0
FOURCC = {b"DXT1": "bc1", b"DXT5": "bc3"}
BLOCK = {"bc1": 8, "bc3": 16}
OPAQUE_ALPHA = np.array([255, 255, 0, 0, 0, 0, 0, 0], np.uint8)
WATER_TRANSITION = "../textures/water_transition.png"
# Planes of a PATCH_VERTEX holding the texture's (s, t), per terrain kind (OrthoStudio XP's
# dsf/encode.py, the same as Ortho4XP): the XP12 sea keeps a ratio in planes 5-6.
UV_PLANES = {
    "land": ((5, 6),),
    "water_overlay": ((5, 6),),
    "sea_overlay": ((5, 6), (7, 8)),
    "sea": ((7, 8),),
    "water": ((7, 8),),
}
CHECK_EVERY = 16  # one vertex in 16 checked against the ground
CHECK_MAX_PX = 8.0  # largest error allowed, in pixels of the texture


class RefusedError(Exception):
    """A tile this converter cannot handle, with the reason in plain words."""


# -- web-mercator grid (OrthoStudio XP's imagery/grid.py) ------------------------------------------


def wgs84_to_tile(lat: float, lon: float, zl: int) -> tuple[float, float]:
    scale = 2 ** (zl - 1)
    return (lon / 180 + 1) * scale, (1 - log(tan((90 + lat) * pi / 360)) / pi) * scale


def tile_to_wgs84(x: float, y: float, zl: int) -> tuple[float, float]:
    rat_x, rat_y = x / 2 ** (zl - 1) - 1, 1 - y / 2 ** (zl - 1)
    return 360 / pi * atan(exp(pi * rat_y)) - 90, rat_x * 180


def pixel_size(lat: float, zl: int) -> float:
    return 2 * pi * EARTH_RADIUS * cos(pi * lat / 180) / 2 ** (zl + 8)


# -- terrain files ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Terrain:
    """One .ter of the tile: its texture, where it is on the grid, and its kind."""

    name: str  # file name in terrain/
    texture: str  # file name in textures/
    til_x: int
    til_y: int
    zl: int
    provider: str
    kind: str
    text: str

    @property
    def group(self) -> tuple[str, int, int, int]:
        return self.provider, self.zl, self.til_y // 32 * 32, self.til_x // 32 * 32

    @property
    def quadrant(self) -> tuple[int, int]:
        """(dx east, dy south) of its texture in the 8K."""
        return (self.til_x % 32) // 16, (self.til_y % 32) // 16


def group_name(group: tuple[str, int, int, int]) -> str:
    provider, zl, gy, gx = group
    return f"{gy}_{gx}_{provider}{zl}_8k"


def read_terrain(path: Path) -> Terrain:
    text = path.read_text(encoding="ascii")
    fields = {}
    for line in text.splitlines()[3:]:
        key, _, rest = line.partition(" ")
        fields.setdefault(key, rest.strip())
    if (
        "LOAD_CENTER_BORDER" in fields
        or fields.get("BORDER_TEX", WATER_TRANSITION) != WATER_TRANSITION
    ):
        raise RefusedError(
            f"{path.name} takes its sea mask from a separate file: tiles built with "
            "'imprint masks to DDS' turned off are not handled"
        )
    texture = Path(fields.get("BASE_TEX_NOWRAP", "")).name
    tex_stem, ter_stem = Path(texture).stem, path.stem
    m = re.match(r"^(\d+)_(\d+)_(.+)$", tex_stem)
    if not m or not ter_stem.startswith(tex_stem) or "LOAD_CENTER" not in fields:
        raise RefusedError(f"{path.name} is not an OrthoStudio XP terrain file")
    til_y, til_x, provider_zl = int(m[1]), int(m[2]), m[3]
    lat_c, lon_c = (float(v) for v in fields["LOAD_CENTER"].split()[:2])
    for digits in (2, 1):  # the zoom level is what puts the texture's centre at LOAD_CENTER
        provider, zl_s = provider_zl[:-digits], provider_zl[-digits:]
        if not provider or not zl_s.isdigit() or til_x % 16 or til_y % 16:
            continue
        lat, lon = tile_to_wgs84(til_x + 8, til_y + 8, int(zl_s))
        if abs(lat - lat_c) < 1e-4 and abs(lon - lon_c) < 1e-4:
            break
    else:
        raise RefusedError(f"{path.name}: its LOAD_CENTER matches no zoom level of its name")
    kind = ter_stem[len(tex_stem) :].lstrip("_") or "land"
    if kind not in UV_PLANES:
        raise RefusedError(f"{path.name}: unknown terrain kind '{kind}'")
    return Terrain(path.name, texture, til_x, til_y, int(zl_s), provider, kind, text)


def ter_8k(t: Terrain) -> str:
    _, zl, gy, gx = t.group
    lat, lon = tile_to_wgs84(gx + 16, gy + 16, zl)
    size = int(pixel_size(lat, zl) * 8192)
    lines = []
    for line in t.text.splitlines():
        if line.startswith("LOAD_CENTER "):
            line = f"LOAD_CENTER {lat:.5f} {lon:.5f} {size} 8192"
        elif line.startswith("BASE_TEX_NOWRAP"):
            line = f"BASE_TEX_NOWRAP ../textures/{group_name(t.group)}.dds"
        lines.append(line)
    return "\n".join(lines) + "\n"


# -- DDS -------------------------------------------------------------------------------------------


def dds_header(side: int, fmt: str, mips: int) -> bytes:
    flags = (
        0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    )  # caps height width pixelformat mips linear
    caps = 0x1000 | 0x8 | 0x400000  # texture, complex, mipmap
    fourcc = b"DXT1" if fmt == "bc1" else b"DXT5"
    linear = (side // 4) ** 2 * BLOCK[fmt]
    return struct.pack(
        "<4s7I44x2I4s5I5I", b"DDS ", 124, flags, side, side, linear, 0, mips, 32, 0x4, fourcc,
        0, 0, 0, 0, 0, caps, 0, 0, 0, 0,
    )  # fmt: skip


def read_dds(path: Path) -> tuple[str, list[np.ndarray]]:
    b = path.read_bytes()
    if b[:4] != b"DDS " or b[84:88] not in FOURCC:
        raise RefusedError(f"{path.name} is not a DXT1/DXT5 DDS")
    fmt = FOURCC[b[84:88]]
    h, w, count = (
        struct.unpack_from("<I", b, 12)[0],
        struct.unpack_from("<I", b, 16)[0],
        struct.unpack_from("<I", b, 28)[0],
    )
    if (w, h, count) != (4096, 4096, 13):
        raise RefusedError(f"{path.name} is {w}x{h} with {count} levels, not 4096x4096 with 13")
    levels, off = [], 128
    for k in range(13):
        nb = max(1, (4096 >> k) // 4)
        size = nb * nb * BLOCK[fmt]
        levels.append(np.frombuffer(b, np.uint8, size, off).reshape(nb, nb, BLOCK[fmt]))
        off += size
    if off != len(b):
        raise RefusedError(f"{path.name} has {len(b) - off} unexpected bytes")
    return fmt, levels


def rgb565(c: np.ndarray) -> np.ndarray:
    return np.stack([(c >> 11 & 31) * 255 / 31, (c >> 5 & 63) * 255 / 63, (c & 31) * 255 / 31], -1)


def decode(blocks: np.ndarray, fmt: str) -> np.ndarray:
    """RGBA pixels (..., 16, 4) of blocks (..., B), pixel i at row i // 4, column i % 4."""
    col = blocks[..., -8:]
    c0 = col[..., 0].astype(np.int32) | col[..., 1].astype(np.int32) << 8
    c1 = col[..., 2].astype(np.int32) | col[..., 3].astype(np.int32) << 8
    p0, p1 = rgb565(c0), rgb565(c1)
    four = (c0 > c1)[..., None] | (fmt == "bc3")
    pal = np.stack(
        [
            p0,
            p1,
            np.where(four, (2 * p0 + p1) / 3, (p0 + p1) / 2),
            np.where(four, (p0 + 2 * p1) / 3, 0),
        ],
        -2,
    )
    idx = np.ascontiguousarray(col[..., 4:8]).view(np.uint32)[..., 0]
    sel = np.stack([idx >> 2 * i & 3 for i in range(16)], -1)
    px = np.take_along_axis(pal, sel[..., None].repeat(3, -1), -2)
    alpha = np.full((*px.shape[:-1], 1), 255.0)
    if fmt == "bc3":
        a = blocks[..., :8]
        a0, a1 = a[..., 0].astype(float), a[..., 1].astype(float)
        bits = np.zeros(a.shape[:-1], np.uint64)
        for i in range(6):
            bits |= a[..., 2 + i].astype(np.uint64) << np.uint64(8 * i)
        ai = np.stack([bits >> np.uint64(3 * i) & np.uint64(7) for i in range(16)], -1).astype(int)
        eight = a0 > a1
        tab = [a0, a1]
        for j in range(2, 8):
            v8 = ((8 - j) * a0 + (j - 1) * a1) / 7
            v6 = (
                ((6 - j) * a0 + (j - 1) * a1) / 5
                if j < 6
                else np.full_like(a0, 0.0 if j == 6 else 255.0)
            )
            tab.append(np.where(eight, v8, v6))
        alpha = np.take_along_axis(np.stack(tab, -1), ai, -1)[..., None]
    return np.concatenate([px, alpha], -1)


def encode(px: np.ndarray, fmt: str) -> np.ndarray:
    """Blocks (n, B) of RGBA pixels (n, 16, 4): plain min/max end points, nearest entries."""
    n = len(px)
    rgb = px[..., :3].astype(np.float32)
    hi, lo = rgb.max(1), rgb.min(1)

    def q(c):
        return (np.rint(c[:, 0] * 31 / 255).astype(np.int32) << 11
                | np.rint(c[:, 1] * 63 / 255).astype(np.int32) << 5
                | np.rint(c[:, 2] * 31 / 255).astype(np.int32))  # fmt: skip

    c0, c1 = q(hi), q(lo)
    c0, c1 = np.maximum(c0, c1), np.minimum(c0, c1)
    p0, p1 = rgb565(c0), rgb565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1).astype(np.float32)
    sel = ((rgb[:, :, None, :] - pal[:, None]) ** 2).sum(-1).argmin(-1)
    sel[c0 == c1] = 0
    colour = np.zeros(n, dtype=[("c0", "<u2"), ("c1", "<u2"), ("idx", "<u4")])
    colour["c0"], colour["c1"] = c0, c1
    colour["idx"] = (sel.astype(np.uint64) << (2 * np.arange(16, dtype=np.uint64))).sum(1)
    colour = colour.view(np.uint8).reshape(n, 8)
    if fmt == "bc1":
        return colour
    al = px[..., 3].astype(np.float32)
    a0, a1 = np.rint(al.max(1)), np.rint(al.min(1))
    tab = np.stack([a0, a1] + [((8 - j) * a0 + (j - 1) * a1) / 7 for j in range(2, 8)], 1)
    ai = np.abs(al[:, :, None] - tab[:, None]).argmin(-1)
    ai[a0 == a1] = 0
    bits = (ai.astype(np.uint64) << (3 * np.arange(16, dtype=np.uint64))).sum(1)
    alpha = np.zeros((n, 8), np.uint8)
    alpha[:, 0], alpha[:, 1] = a0, a1
    for i in range(6):
        alpha[:, 2 + i] = (bits >> np.uint64(8 * i)) & np.uint64(255)
    return np.concatenate([alpha, colour], 1)


def to_bc3(blocks: np.ndarray) -> np.ndarray:
    """DXT1 blocks as DXT5: opaque alpha, the colour block copied, except the blocks in DXT1's
    three-colour mode, which DXT5 cannot read: those are encoded again from their pixels."""
    out = np.empty((*blocks.shape[:-1], 16), np.uint8)
    out[..., :8] = OPAQUE_ALPHA
    out[..., 8:] = blocks
    c0 = blocks[..., 0].astype(np.uint16) | blocks[..., 1].astype(np.uint16) << 8
    c1 = blocks[..., 2].astype(np.uint16) | blocks[..., 3].astype(np.uint16) << 8
    idx = np.ascontiguousarray(blocks[..., 4:8]).view(np.uint32)[..., 0]
    uses_23 = np.zeros(idx.shape, bool)
    for i in range(16):
        uses_23 |= (idx >> 2 * i & 3) >= 2
    three = (c0 <= c1) & uses_23
    if three.any():
        out[three, 8:] = encode(decode(blocks[three], "bc1"), "bc1")
    return out


def glue(quads: dict[tuple[int, int], Path], out: Path) -> str:
    """Write the 8K DDS of up to four 4K textures keyed (dx, dy); answer its format.

    The levels from 8192 down to 8 px are the 4K levels side by side; the three smallest are
    averaged from the 8 px one. A missing quarter stays black: no ground uses it.
    """
    read = {k: read_dds(p) for k, p in quads.items()}
    fmt = "bc3" if any(f == "bc3" for f, _ in read.values()) else "bc1"
    parts = [dds_header(8192, fmt, 14)]
    for k in range(11):
        nb = (4096 >> k) // 4
        level = np.zeros((2 * nb, 2 * nb, BLOCK[fmt]), np.uint8)
        for (dx, dy), (f, lv) in read.items():
            level[dy * nb : (dy + 1) * nb, dx * nb : (dx + 1) * nb] = (
                lv[k] if f == fmt else to_bc3(lv[k])
            )
        parts.append(level.tobytes())
    img = decode(level, fmt).reshape(2, 2, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(8, 8, 4)
    for size in (4, 2, 1):
        f = 8 // size
        small = img.reshape(size, f, size, f, 4).mean((1, 3))
        block = np.tile(small, (4 // size, 4 // size, 1)).reshape(1, 16, 4)
        parts.append(encode(block, fmt).tobytes())
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(b"".join(parts))
    os.replace(tmp, out)
    return fmt


# -- the DSF text ----------------------------------------------------------------------------------


@dataclass
class DsfText:
    """Where things are in a DSFTool text: header lines, terrain names, patch spans, the rest."""

    path: Path
    head: list[str]  # with None where the TERRAIN_DEF lines go
    defs: list[str]
    patches: list[tuple[int, tuple[str, ...], int, int]]  # terrain, params, body start, end
    tail: tuple[int, int]


def scan(path: Path) -> DsfText:
    head: list = []
    defs: list[str] = []
    patches = []
    tail_start = None
    with open(path, "rb") as f:
        pos = 0
        current = None
        for raw in f:
            line = raw.decode("ascii")
            start, pos = pos, pos + len(raw)
            if current is not None:
                if line.startswith("END_PATCH"):
                    patches.append((*current, start))
                    current = None
                continue
            if line.startswith("BEGIN_PATCH"):
                if tail_start is not None:
                    raise RefusedError("its DSF has patches after its other contents")
                p = line.split()
                current = (int(p[1]), tuple(p[2:]), pos)
                continue
            if patches:
                if tail_start is None and line.strip():
                    tail_start = start
                continue
            if line.startswith("TERRAIN_DEF"):
                if not defs:
                    head.append(None)
                defs.append(line.split(None, 1)[1].strip())
                continue
            head.append(line)
    return DsfText(path, head, defs, patches, (pos if tail_start is None else tail_start, pos))


@dataclass
class Check:
    """Texture coordinates measured against the ground, in pixels of their texture."""

    worst: float = 0.0
    count: int = 0
    where: str = ""

    def add(self, err_px: float, line: str) -> None:
        self.count += 1
        if err_px > self.worst:
            self.worst, self.where = err_px, line.strip()[:100]


def write_text(
    src: DsfText, dst: Path, terrains: dict[str, Terrain], glue8k: bool, check: Check
) -> int:
    """The DSF text with one patch per terrain (and per patch parameters), terrains glued by four
    when ``glue8k``; answer the number of terrains."""
    new_defs: list[str] = []
    new_index: dict[str, int] = {}
    mapping = []  # old terrain index -> (new index, Terrain or None)
    for d in src.defs:
        ter = terrains.get(d.removeprefix("terrain/")) if d.startswith("terrain/") else None
        if ter is None and d.startswith("terrain/"):
            raise RefusedError(f"its DSF names {d}, which is not in its terrain folder")
        key = (
            d
            if ter is None or not glue8k
            else f"terrain/{group_name(ter.group)}{ter.name[len(Path(ter.texture).stem) :]}"
        )
        if key not in new_index:
            new_index[key] = len(new_defs)
            new_defs.append(key)
        mapping.append((new_index[key], ter))
    order: dict[tuple, list[tuple[int, int, Terrain | None]]] = {}
    for terrain, params, start, end in src.patches:
        new, ter = mapping[terrain]
        order.setdefault((new, *params), []).append((start, end, ter))
    side = 32 if glue8k else 16
    with open(src.path, "rb") as f, open(dst, "w", encoding="ascii", newline="\n") as out:
        for line in src.head:
            out.write("".join(f"TERRAIN_DEF {d}\n" for d in new_defs) if line is None else line)
        for (new, *params), spans in order.items():
            out.write(f"BEGIN_PATCH {new} {' '.join(params)}\n")
            for start, end, ter in spans:
                f.seek(start)
                body = f.read(end - start).decode("ascii")
                if ter is None:
                    out.write(body)
                    continue
                planes = UV_PLANES[ter.kind]
                dx, dy = ter.quadrant
                gy, gx = (ter.group[2], ter.group[3]) if glue8k else (ter.til_y, ter.til_x)
                n = 0
                for line in body.splitlines(keepends=True):
                    if not line.startswith("PATCH_VERTEX"):
                        out.write(line)
                        continue
                    v = line.split()
                    if glue8k:
                        for ps, pt in planes:
                            v[ps + 1] = f"{(float(v[ps + 1]) + dx) / 2:.9f}"
                            v[pt + 1] = f"{(float(v[pt + 1]) + 1 - dy) / 2:.9f}"
                        line = " ".join(v) + "\n"
                    n += 1
                    if n % CHECK_EVERY == 0:
                        x, y = wgs84_to_tile(float(v[2]), float(v[1]), ter.zl)
                        se = min(max((x - gx) / side, 0.0), 1.0)
                        te = min(max(1 - (y - gy) / side, 0.0), 1.0)
                        for ps, pt in planes:
                            err = max(abs(float(v[ps + 1]) - se), abs(float(v[pt + 1]) - te))
                            check.add(err * side * 256, line)
                    out.write(line)
            out.write("END_PATCH\n")
        f.seek(src.tail[0])
        out.write(f.read(src.tail[1] - src.tail[0]).decode("ascii"))
    if check.worst > CHECK_MAX_PX:
        raise RefusedError(
            f"a texture coordinate is {check.worst:.0f} px away from its ground in the "
            f"{'8K' if glue8k else '4K'} copy ({check.where}): nothing was written for this tile"
        )
    return len(new_defs)


# -- DSFTool ---------------------------------------------------------------------------------------


def find_dsftool(given: str | None) -> Path:
    exe = "DSFTool.exe" if sys.platform == "win32" else "DSFTool"
    candidates = [Path(given)] if given else []
    try:
        import orthostudio

        root = Path(orthostudio.__file__).resolve().parent
        sub = "win" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "lin"
        candidates += [root / "bin" / exe, root.parents[1] / "native" / "dsftool" / sub / exe]
    except ImportError:
        pass
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit(
        "DSFTool not found: run this with OrthoStudio XP's own Python, or give --dsftool PATH"
    )


def dsftool(tool: Path, mode: str, src: Path, dst: Path) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        [str(tool), f"--{mode}", str(src), str(dst)], cwd=dst.parent, capture_output=True,
        creationflags=flags, check=False,
    )  # fmt: skip
    ok = proc.returncode == 0 and dst.is_file()
    if ok and mode == "dsf2text":
        with open(dst, "rb") as f:
            f.seek(max(0, dst.stat().st_size - 4096))
            ok = b"# Result code: 0" in f.read()
    if ok and mode == "text2dsf":
        with open(dst, "rb") as f:
            ok = f.read(8) == b"XPLNEDSF"
    if not ok:
        tail = (proc.stdout + proc.stderr).decode(errors="replace")[-600:]
        raise RefusedError(f"DSFTool --{mode} failed on {src.name}: {tail.strip()}")


# -- one tile --------------------------------------------------------------------------------------


def link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def convert(pack: Path, out_root: Path, tool: Path, say) -> None:
    pack = pack.resolve()
    dsfs = sorted((pack / "Earth nav data").glob("*/*.dsf"))
    if len(dsfs) != 1 or not (pack / "terrain").is_dir():
        raise RefusedError("not an OrthoStudio XP tile (one DSF, a terrain folder)")
    dsf = dsfs[0]
    tile = dsf.stem
    terrains = {p.name: read_terrain(p) for p in sorted((pack / "terrain").glob("*.ter"))}
    groups: dict[tuple, dict[tuple[int, int], Path]] = defaultdict(dict)
    for t in terrains.values():
        groups[t.group][t.quadrant] = pack / "textures" / t.texture
    dests = {size: out_root / size / f"zOSXP_{size}_{tile}" for size in ("4K", "8K")}
    for dest in dests.values():
        if dest.exists():
            raise RefusedError(f"{dest} is already there: move it away first")
    need = sum(p.stat().st_size for g in groups.values() for p in g.values()) * 1.15
    free = shutil.disk_usage(out_root).free
    if free < need:
        raise RefusedError(
            f"{need / 1e9:.1f} GB needed on the disk of {out_root}, {free / 1e9:.1f} GB free"
        )
    work = out_root / f"work-{tile}"
    work.mkdir()
    try:
        say(f"DSF to text ({dsf.stat().st_size / 1e6:.0f} MB)")
        text = work / "tile.txt"
        dsftool(tool, "dsf2text", dsf, text)
        src = scan(text)
        say(
            f"{len(terrains)} terrain files, {len(src.patches)} patches, "
            f"{len(groups)} 8K textures to make"
        )
        built = {}
        for size, glue8k in (("4K", False), ("8K", True)):
            check = Check()
            n = write_text(src, work / f"{size}.txt", terrains, glue8k, check)
            built[size] = work / f"{size}.dsf"
            dsftool(tool, "text2dsf", work / f"{size}.txt", built[size])
            (work / f"{size}.txt").unlink()
            say(
                f"{size} DSF: {n} terrains, texture coordinates within {check.worst:.2f} px "
                f"of their ground ({check.count} checked)"
            )
        for size, dest in dests.items():
            (dest / "Earth nav data" / dsf.parent.name).mkdir(parents=True)
            (dest / "terrain").mkdir()
            (dest / "textures").mkdir()
            shutil.move(str(built[size]), dest / "Earth nav data" / dsf.parent.name / dsf.name)
        for f in (pack / "terrain").iterdir():
            link_or_copy(f, dests["4K"] / "terrain" / f.name)
        for f in (pack / "textures").iterdir():
            link_or_copy(f, dests["4K"] / "textures" / f.name)
            if f.suffix.lower() != ".dds":
                link_or_copy(f, dests["8K"] / "textures" / f.name)
        for t in terrains.values():
            name = group_name(t.group) + t.name[len(Path(t.texture).stem) :]
            (dests["8K"] / "terrain" / name).write_text(ter_8k(t), encoding="ascii", newline="\n")
        formats: dict[str, int] = defaultdict(int)
        t0 = time.monotonic()
        for i, (g, quads) in enumerate(sorted(groups.items())):
            formats[glue(quads, dests["8K"] / "textures" / f"{group_name(g)}.dds")] += 1
            if (i + 1) % 25 == 0 or i + 1 == len(groups):
                say(f"8K textures {i + 1}/{len(groups)} ({time.monotonic() - t0:.0f} s)")
        say(
            f"done: {len(groups)} 8K textures ({formats['bc1']} DXT1, {formats['bc3']} DXT5) "
            f"for {sum(len(q) for q in groups.values())} 4K ones"
        )
    finally:
        for f in work.iterdir():
            f.unlink()
        work.rmdir()


def ask_tiles() -> list[Path]:
    """The tile folders dragged into the window: a terminal writes them on one line, quoted the
    way its shell reads them (double quotes on Windows)."""
    print("Drag your OrthoStudio XP tile folders (zOrthoStudio_...) into this window,")
    print("then press Enter.")
    try:
        line = input("> ").strip()
    except EOFError:
        line = ""
    if sys.platform == "win32":
        return [Path(a or b) for a, b in re.findall(r'"([^"]+)"|(\S+)', line)]
    return [Path(p) for p in shlex.split(line)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "tiles", nargs="*", type=Path, help="OrthoStudio XP tile folders (zOrthoStudio_...)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        help='where the two copies go (default: "OSXP 8K test" beside the tiles\' folder)',
    )
    parser.add_argument("--dsftool", help="DSFTool to use (default: OrthoStudio XP's)")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    tool = find_dsftool(args.dsftool)
    tiles = args.tiles or ask_tiles()
    if not tiles:
        print("No tile folder given: nothing done.")
        return 1
    failed = 0
    for i, pack in enumerate(tiles, 1):
        prefix = f"[{i}/{len(tiles)}] {pack.name}:"

        def say(msg: str, prefix: str = prefix) -> None:
            print(prefix, msg, flush=True)

        out_root = args.out or pack.resolve().parent.parent / "OSXP 8K test"
        try:
            out_root.mkdir(parents=True, exist_ok=True)
            say(f"into {out_root}")
            convert(pack, out_root, tool, say)
        except RefusedError as exc:
            failed += 1
            say(f"SKIPPED: {exc}")
    print(f"{len(tiles) - failed} tile(s) converted, {failed} skipped.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
