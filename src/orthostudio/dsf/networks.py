# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The roads of a DSF: its network chains, as X-Plane's own scenery writes them.

A DSF lays its roads as chains of points in its 32-bit pools (``PO32``, scaled by ``SC32``:
longitude, latitude, elevation and a junction number per point), each chain of the network
definition and the road subtype selected before it (commands 3 to 5 and 6). The subtype is the
road's type in the definition's ``.net`` file with its last two digits left out: subtype 50 of
``lib/g10/roads_EU.net`` is its ``ROAD_TYPE`` 5001 to 5005, a local road (:func:`road_class`).
Everything else of the command stream (objects, polygons, terrain patches, comments) is stepped
over.

Read for the night textures (``orthostudio.live.night``): X-Plane's roads are on every user's disk,
in the Global Scenery's DSF of each square, and on Nice's square they are nearly OpenStreetMap's own
(7,457 km against 9,261 km of which 2,610 km of service ways, measured 2026-11-10).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from orthostudio.dsf.container import parse_dsf

__all__ = ["Chain", "chains_of", "read_pool32", "road_class", "walk_networks"]


@dataclass(frozen=True)
class Chain:
    """One road: its subtype and its points, ``(n, 2)`` longitude and latitude."""

    definition: int
    subtype: int
    points: np.ndarray


def read_pool32(payload: bytes) -> np.ndarray:
    """A ``PO32`` atom's table (n x planes, uint32): planes raw, differenced, run-length encoded or
    both, as :func:`orthostudio.dsf.strips.read_pool` reads the 16-bit ones."""
    n, planes = struct.unpack_from("<IB", payload, 0)
    pos = 5
    table = np.empty((n, planes), dtype=np.uint32)
    for j in range(planes):
        encoding = payload[pos]
        pos += 1
        if encoding & 2:
            col = np.empty(n, dtype=np.uint32)
            got = 0
            while got < n:
                run = payload[pos]
                pos += 1
                count = run & 0x7F
                if run & 0x80:
                    (value,) = struct.unpack_from("<I", payload, pos)
                    pos += 4
                    col[got : got + count] = value
                else:
                    col[got : got + count] = np.frombuffer(payload, "<u4", count, pos)
                    pos += 4 * count
                got += count
        else:
            col = np.frombuffer(payload, "<u4", n, pos).copy()
            pos += 4 * n
        if encoding & 1:
            col = np.cumsum(col, dtype=np.uint64).astype(np.uint32)  # wraps as the file's sums do
        table[:, j] = col
    return table


def walk_networks(cmds: bytes, pools: list[np.ndarray]) -> list[Chain]:
    """The chains of a command stream, their points taken from ``pools`` (decoded 32-bit pools,
    ``(n, >= 2)`` longitude and latitude first). Raises ``ValueError`` on a command the DSF format
    does not have."""
    chains: list[Chain] = []
    pos, pool, definition, subtype, offset = 0, 0, 0, 0, 0
    end = len(cmds)

    def chain(index: np.ndarray) -> None:
        if len(index) >= 2 and pool < len(pools):
            chains.append(Chain(definition, subtype, pools[pool][index + offset, :2]))

    while pos < end:
        cmd = cmds[pos]
        pos += 1
        if cmd == 1:  # pool select
            (pool,) = struct.unpack_from("<H", cmds, pos)
            pos += 2
        elif cmd == 2:  # junction offset
            (offset,) = struct.unpack_from("<I", cmds, pos)
            pos += 4
        elif cmd in (3, 4, 5):  # definition select
            fmt, size = {3: ("<B", 1), 4: ("<H", 2), 5: ("<I", 4)}[cmd]
            (definition,) = struct.unpack_from(fmt, cmds, pos)
            pos += size
        elif cmd == 6:  # road subtype
            subtype = cmds[pos]
            pos += 1
        elif cmd == 9:  # network chain
            n = cmds[pos]
            chain(np.frombuffer(cmds, "<u2", n, pos + 1).astype(np.int64))
            pos += 1 + 2 * n
        elif cmd == 10:  # network chain range
            first, last = struct.unpack_from("<HH", cmds, pos)
            pos += 4
            chain(np.arange(first, last, dtype=np.int64))
        elif cmd == 11:  # network chain, 32-bit indices
            n = cmds[pos]
            chain(np.frombuffer(cmds, "<u4", n, pos + 1).astype(np.int64))
            pos += 1 + 4 * n
        else:
            pos = _skip(cmd, cmds, pos)
    return chains


def _skip(cmd: int, cmds: bytes, pos: int) -> int:
    """Past the arguments of a command that lays no road."""
    if cmd == 7:  # object
        return pos + 2
    if cmd in (8, 25, 28, 31):  # object range, triangle / strip / fan ranges
        return pos + 4
    if cmd == 12:  # polygon: parameter, count, indices
        return pos + 3 + 2 * cmds[pos + 2]
    if cmd == 13:  # polygon range
        return pos + 6
    if cmd == 14:  # nested polygon: parameter, windings, each a count and its indices
        windings = cmds[pos + 2]
        pos += 3
        for _ in range(windings):
            pos += 1 + 2 * cmds[pos]
        return pos
    if cmd == 15:  # nested polygon range: parameter, count, count + 1 indices
        return pos + 3 + 2 * (cmds[pos + 2] + 1)
    if cmd == 16:  # terrain patch
        return pos
    if cmd == 17:  # patch flags
        return pos + 1
    if cmd == 18:  # patch flags and LOD
        return pos + 9
    if cmd in (23, 26, 29):  # triangles, strip, fan: count and 16-bit indices
        return pos + 1 + 2 * cmds[pos]
    if cmd in (24, 27, 30):  # the same across pools: count and (pool, index) pairs
        return pos + 1 + 4 * cmds[pos]
    if cmd in (32, 33, 34):  # comments
        fmt, size = {32: ("<B", 1), 33: ("<H", 2), 34: ("<I", 4)}[cmd]
        (n,) = struct.unpack_from(fmt, cmds, pos)
        return pos + size + n
    raise ValueError(f"DSF command {cmd} at byte {pos - 1} does not exist")


def chains_of(data: bytes) -> tuple[list[str], list[Chain]]:
    """The network definitions of a DSF's bytes (``DEFN/NETW``) and all its chains, their points in
    degrees."""
    dsf = parse_dsf(data)
    netw = dsf.find("DEFN/NETW")
    names = (
        [t.decode("ascii", "replace") for t in netw.payload(data).split(b"\0") if t] if netw else []
    )
    geod = dsf.find("GEOD")
    cmds = dsf.find("CMDS")
    if geod is None or cmds is None:
        return names, []
    raw = [read_pool32(a.payload(data)) for a in (geod.children or []) if a.name == "PO32"]
    scales = [
        np.frombuffer(a.payload(data), "<f4").reshape(-1, 2).astype(np.float64)
        for a in (geod.children or [])
        if a.name == "SC32"
    ]
    pools = [
        sc[:2, 1] + table[:, :2].astype(np.float64) * sc[:2, 0] / 4294967295.0
        for table, sc in zip(raw, scales, strict=True)
    ]
    return names, walk_networks(cmds.payload(data), pools)


def road_class(subtype: int) -> tuple[str, bool] | None:
    """The class of road an X-Plane 12 road subtype is (``roads_EU.net`` and its kin), with whether
    it is a rural one, and ``None`` for what lays no street: railways (151 to 153) and power lines
    (220). The tens are the family (1 and 2 primary, 3 and 4 secondary, 5 to 7 local, 10 and 11
    highway, 12 ramp), the unit the variant: 4 to 8 with sidewalks, 9 a rural edge."""
    if 100 <= subtype < 120:
        return "motorway", False
    if 120 <= subtype < 130:
        return "motorway_link", False
    if subtype >= 130 or subtype < 10:
        return None
    family, variant = divmod(subtype, 10)
    name = {1: "primary", 2: "primary", 3: "secondary", 4: "secondary", 5: "residential",
            6: "residential", 7: "residential"}.get(family)  # fmt: skip
    return (name, variant == 9) if name else None
