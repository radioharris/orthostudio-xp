# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""Decoder of the DSF subset written by Ortho4XP and OrthoStudio XP (terrain patches only).

For tests and for semantic comparisons: pools and scales are read back, the command stream
is walked (definition / pool selection / patch flags / triangle commands 23-31), and every
patch comes back as ``(pool, index)`` corners or as decoded plane values. Objects, polygons
and networks (never written by the mesh encoder) raise ``NotImplementedError``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from orthostudio.dsf.container import DsfFile, parse_dsf, properties

__all__ = ["DecodedDsf", "Patch", "decode_dsf"]


@dataclass
class Patch:
    """One terrain patch: a run of triangles after a definition / pool / flags selection."""

    terrain: int
    pool: int
    flag: int
    near_lod: float
    far_lod: float
    corners: np.ndarray
    """``(k, 3, 2)`` int64: ``(pool, index)`` of the three corners of every triangle."""


@dataclass
class DecodedDsf:
    """Pools, scales, terrain names and patches of a DSF."""

    properties: list[tuple[str, str]]
    terrains: list[str]
    pools: list[np.ndarray]
    """Raw ``uint16`` tables ``(n, planes)``, in file order."""
    scales: list[np.ndarray]
    """``(planes, 2)`` float32 ``(scale, offset)`` per pool."""
    patches: list[Patch] = field(default_factory=list)
    raster_names: list[str] = field(default_factory=list)
    md5_ok: bool = False

    def vertex(self, pool: int, index: int) -> np.ndarray:
        """Decoded planes of one vertex: ``offset + raw * scale / 65535`` (float64)."""
        raw = self.pools[pool][index].astype(np.float64)
        sc = self.scales[pool].astype(np.float64)
        return sc[:, 1] + raw * sc[:, 0] / 65535

    def triangles(self, patch: Patch) -> np.ndarray:
        """``(k, 3, planes)`` decoded corners of a patch (all its corners share a plane count)."""
        c = patch.corners
        planes = self.pools[int(c[0, 0, 0])].shape[1]
        out = np.empty((len(c), 3, planes), dtype=np.float64)
        for i in range(len(c)):
            for j in range(3):
                out[i, j] = self.vertex(int(c[i, j, 0]), int(c[i, j, 1]))
        return out

    def triangle_count(self, terrain: int | None = None) -> int:
        return sum(len(p.corners) for p in self.patches if terrain is None or p.terrain == terrain)


def _strings(payload: bytes) -> list[str]:
    parts = payload.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    return [p.decode("ascii", "replace") for p in parts]


def _read_pool(payload: bytes) -> np.ndarray:
    n, planes = struct.unpack_from("<IB", payload, 0)
    pos = 5
    table = np.empty((n, planes), dtype=np.uint16)
    for j in range(planes):
        encoding = payload[pos]
        pos += 1
        if encoding == 0:  # raw
            table[:, j] = np.frombuffer(payload, dtype="<u2", count=n, offset=pos)
            pos += 2 * n
        else:  # run-length encoded plane (never written by Ortho4XP/OrthoStudio XP)
            out: list[int] = []
            while len(out) < n:
                run = payload[pos]
                pos += 1
                if run & 0x80:
                    (v,) = struct.unpack_from("<H", payload, pos)
                    pos += 2
                    out.extend([v] * (run & 0x7F))
                else:
                    vals = np.frombuffer(payload, dtype="<u2", count=run, offset=pos)
                    pos += 2 * run
                    out.extend(int(x) for x in vals)
            table[:, j] = out
            if encoding & 2:  # differenced
                table[:, j] = np.cumsum(table[:, j], dtype=np.uint16)
    return table


def _walk_commands(data: bytes, cmds: bytes, decoded: DecodedDsf) -> None:
    pos = 0
    pool = 0
    terrain = 0
    flag, near, far = 0, 0.0, 0.0
    end = len(cmds)

    def tri(corners: list[tuple[int, int]]) -> None:
        arr = np.asarray(corners, dtype=np.int64).reshape(-1, 3, 2)
        decoded.patches.append(Patch(terrain, pool, flag, near, far, arr))

    while pos < end:
        cmd = cmds[pos]
        pos += 1
        if cmd == 1:
            (pool,) = struct.unpack_from("<H", cmds, pos)
            pos += 2
        elif cmd == 2:
            pos += 4
        elif cmd in (3, 4, 5):
            fmt, size = {3: ("<B", 1), 4: ("<H", 2), 5: ("<I", 4)}[cmd]
            (terrain,) = struct.unpack_from(fmt, cmds, pos)
            pos += size
        elif cmd == 18:
            flag, near, far = struct.unpack_from("<Bff", cmds, pos)
            pos += 9
        elif cmd == 23:
            n = cmds[pos]
            pos += 1
            idx = struct.unpack_from(f"<{n}H", cmds, pos)
            pos += 2 * n
            tri([(pool, i) for i in idx])
        elif cmd == 24:
            n = cmds[pos]
            pos += 1
            vals = struct.unpack_from(f"<{2 * n}H", cmds, pos)
            pos += 4 * n
            tri([(vals[2 * i], vals[2 * i + 1]) for i in range(n)])
        elif cmd == 25:
            first, last = struct.unpack_from("<HH", cmds, pos)
            pos += 4
            tri([(pool, i) for i in range(first, last)])
        elif cmd in (26, 27, 28, 29, 30, 31):
            raise NotImplementedError(f"DSF command {cmd} (strip/fan) is not decoded")
        elif cmd in (32, 33, 34):
            fmt, size = {32: ("<B", 1), 33: ("<H", 2), 34: ("<I", 4)}[cmd]
            (n,) = struct.unpack_from(fmt, cmds, pos)
            pos += size + n
        else:
            raise NotImplementedError(f"DSF command {cmd} is not decoded")


def decode_dsf(data: bytes) -> DecodedDsf:
    """Decode the pools, scales, terrain names and patches of a DSF."""
    dsf: DsfFile = parse_dsf(data)
    tert = dsf.find("DEFN/TERT")
    demn = dsf.find("DEFN/DEMN")
    geod = dsf.find("GEOD")
    cmds = dsf.find("CMDS")
    if tert is None or geod is None or cmds is None:
        raise ValueError("not a mesh DSF (missing TERT, GEOD or CMDS)")
    pools = [_read_pool(a.payload(data)) for a in (geod.children or []) if a.name == "POOL"]
    scales = [
        np.frombuffer(a.payload(data), dtype="<f4").reshape(-1, 2)
        for a in (geod.children or [])
        if a.name == "SCAL"
    ]
    if len(pools) != len(scales):
        raise ValueError(f"{len(pools)} POOL atoms but {len(scales)} SCAL atoms")
    decoded = DecodedDsf(
        properties=properties(data, dsf),
        terrains=_strings(tert.payload(data)),
        pools=pools,
        scales=scales,
        raster_names=_strings(demn.payload(data)) if demn else [],
        md5_ok=dsf.md5_ok,
    )
    _walk_commands(data, cmds.payload(data), decoded)
    return decoded
