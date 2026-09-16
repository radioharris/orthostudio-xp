"""Altitude post-processing of the Triangle4XP output, and the water table the masks need.

Spec: ``docs/specs/mesh-build.md`` sections 5 and 7. Origin: ``O4_Mesh_Utils.py:230-324``
(``post_process_nodes_altitudes``) and ``O4_Mask_Utils.py:432-620`` (``record_water_tris``,
mesh-side half only).

Triangle4XP returns one row per vertex -- ``x y z u v alt``: position in tile-relative
degrees, altitude sampled in the DEM (metres), the two normal components, and the altitude
the vector stage attached to the input vertex -- and one attribute per triangle, a bit set
plagued over the regions. This module turns that into the altitudes Ortho4XP writes: inland water
flattened, sea forced to zero, airports / roads / patches lifted onto their vector altitude.

Two behaviours of Ortho4XP are quirks that the byte-identity target forces us to keep; both are
behind a flag, see the spec section 5.1 and the docstrings below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "ATTRIBUTES",
    "HAS_WATER",
    "WATER_TRIS_FORMAT",
    "SeaSmoothing",
    "TriangleClasses",
    "WaterTris",
    "classify_triangles",
    "post_process_altitudes",
    "read_water_tris_npz",
    "water_tris_table",
    "write_water_tris_npz",
]

ATTRIBUTES: dict[str, int] = {
    "DUMMY": 0,
    "WATER": 1,
    "SEA": 2,
    "SEA_EQUIV": 4,
    "INTERP_ALT": 8,
    "RUNWAY": 16,
    "TAXIWAY": 32,
    "APRON": 64,
    "HANGAR": 128,
}
"""``O4_Vector_Utils.py:44-54``, the bit set Triangle4XP plagues over the regions."""

HAS_WATER = ATTRIBUTES["WATER"] | ATTRIBUTES["SEA"] | ATTRIBUTES["SEA_EQUIV"]
"""``7``: Ortho4XP's ``has_water`` for a mesh of version >= 1.3 (``O4_Mask_Utils.py:441``)."""

WATER_TRIS_FORMAT = "osxp-water-tris-1"

SeaSmoothing = Literal["zero", "mean", "positive"]
"""Ortho4XP tests for ``"zero"`` and ``"mean"``; any other string means "clamp to >= 0"."""

Z, U, V, ALT = 2, 3, 4, 5
"""Column indices of the Triangle4XP node table (``x y z u v alt``)."""


@dataclass(frozen=True, slots=True)
class TriangleClasses:
    """Row indices (into the triangle table) of the three classes Ortho4XP post-processes.

    The classes are exclusive and in Ortho4XP's order of test (``O4_Mesh_Utils.py:252-264``):
    ``attr >= INTERP_ALT`` wins over ``attr & SEA``, which wins over
    ``attr & WATER or attr & SEA_EQUIV``. A triangle whose attribute is skipped
    (``DUMMY``, and the multiples of ten when ``skip_multiples_of_ten``) is in none of them.
    """

    interp_alt: NDArray[np.int64]
    sea: NDArray[np.int64]
    water: NDArray[np.int64]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "interp_alt": int(self.interp_alt.size),
            "sea": int(self.sea.size),
            "water": int(self.water.size),
        }


def classify_triangles(
    attr: NDArray[np.integer], *, skip_multiples_of_ten: bool = True
) -> TriangleClasses:
    """Sort the triangles into Ortho4XP's three post-processing classes (spec section 5).

    ``skip_multiples_of_ten`` reproduces ``O4_Mesh_Utils.py:245``, ``if line[-2] == "0": continue``:
    Ortho4XP reads the *last decimal digit of the attribute* to detect the dummy attribute, and so
    also skips every attribute that is a multiple of ten (``10 = SEA|INTERP_ALT``,
    ``160 = TAXIWAY|HANGAR``, ...). 97 triangles of the reference tile are concerned and the
    reference ``.mesh`` cannot be reproduced without this. ``False`` skips only ``0``.
    """
    values = np.asarray(attr, dtype=np.int64).reshape(-1)
    skip = (values % 10 == 0) if skip_multiples_of_ten else (values == 0)
    live = ~skip
    interp = live & (values >= ATTRIBUTES["INTERP_ALT"])
    sea = live & ~interp & ((values & ATTRIBUTES["SEA"]) != 0)
    water = (
        live
        & ~interp
        & ~sea
        & (((values & ATTRIBUTES["WATER"]) != 0) | ((values & ATTRIBUTES["SEA_EQUIV"]) != 0))
    )
    return TriangleClasses(
        interp_alt=np.flatnonzero(interp),
        sea=np.flatnonzero(sea),
        water=np.flatnonzero(water),
    )


def _ordered_triples(corners: NDArray[np.int32], *, set_order: bool) -> list[tuple[int, int, int]]:
    """The triples in the order Ortho4XP iterates them.

    Ortho4XP collects the triangles in a Python ``set`` of ``(v1, v2, v3)`` tuples, filled in
    ``.1.ele`` order, and then iterates *the set*. Its order is CPython's hash order:
    deterministic (integer hashing is not randomised) but unrelated to the file order, and it
    matters because the smoothing is a Gauss-Seidel sweep -- iterating in file order instead
    changes 97 622 of the 603 649 vertex lines of the reference ``.mesh``. ``set_order``
    False keeps the file order (an osxp-only, arguably saner, choice).
    """
    rows: list[tuple[int, int, int]] = [(r[0], r[1], r[2]) for r in corners.tolist()]
    if not set_order:
        return rows
    # ``set(list)`` inserts one key at a time in list order, exactly like Ortho4XP's ``.add()``
    # loop, so the resulting table layout -- and therefore the iteration order -- is the same.
    return list(set(rows))


def _gauss_seidel_mean(z: list[float], triples: list[tuple[int, int, int]], passes: int) -> None:
    """``passes`` sweeps of "every corner of the triangle takes the mean of the three"."""
    for _ in range(passes):
        for v1, v2, v3 in triples:
            zmean = (z[v1] + z[v2] + z[v3]) / 3
            z[v1] = zmean
            z[v2] = zmean
            z[v3] = zmean


def post_process_altitudes(
    points: NDArray[np.float64],
    tris: NDArray[np.int32],
    classes: TriangleClasses,
    *,
    water_smoothing: int = 10,
    sea_smoothing_mode: str = "zero",
    water_in_set_order: bool = True,
) -> None:
    """Apply ``post_process_nodes_altitudes`` (``O4_Mesh_Utils.py:230-324``) to ``points``.

    ``points`` is the ``(N, 6)`` Triangle4XP node table (``x y z u v alt``) and is modified in
    place; ``tris`` the ``(M, 3)`` **0-based** corners. The three steps are applied in Ortho4XP's
    order: inland water, then sea, then the interpolated altitudes (so a vertex that is both
    keeps the interpolated one).
    """
    if water_smoothing and classes.water.size:
        z = points[:, Z].tolist()
        _gauss_seidel_mean(
            z,
            _ordered_triples(tris[classes.water], set_order=water_in_set_order),
            int(water_smoothing),
        )
        points[:, Z] = z

    if classes.sea.size:
        sea_corners = tris[classes.sea]
        if sea_smoothing_mode == "zero":
            points[np.unique(sea_corners), Z] = 0.0
        elif sea_smoothing_mode == "mean":
            z = points[:, Z].tolist()
            _gauss_seidel_mean(z, _ordered_triples(sea_corners, set_order=water_in_set_order), 1)
            points[:, Z] = z
        else:
            unique = np.unique(sea_corners)
            points[unique, Z] = np.maximum(points[unique, Z], 0.0)

    if classes.interp_alt.size:
        unique = np.unique(tris[classes.interp_alt])
        points[unique, Z] = points[unique, ALT]
        points[unique, U] = 0.0
        points[unique, V] = 0.0


# -- the water table the masks stage consumes --------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaterTris:
    """Mesh-side half of Ortho4XP's ``record_water_tris`` (spec section 7).

    One row per triangle that carries at least one water bit, in ``.mesh`` triangle order.
    The corner coordinates are *not* duplicated here: they are
    ``mesh.vertices[corners][:, :, :2]`` of the ``mesh.npz`` of the same artefact.
    """

    tile: str
    tri_index: NDArray[np.int32]
    corners: NDArray[np.int32]
    water_bits: NDArray[np.uint8]
    attr: NDArray[np.uint8]
    bary: NDArray[np.float64]

    def __len__(self) -> int:
        return int(self.tri_index.size)

    @property
    def counts(self) -> dict[int, int]:
        """Number of triangles per ``water_bits`` value."""
        values, counts = np.unique(self.water_bits, return_counts=True)
        return {int(v): int(c) for v, c in zip(values.tolist(), counts.tolist(), strict=True)}


def water_tris_table(
    tile: str,
    vertices: NDArray[np.float64],
    tris: NDArray[np.int32],
    attr: NDArray[np.integer],
) -> WaterTris:
    """Select the water triangles of a mesh and pre-compute their barycentres.

    ``vertices`` is the ``(N, 3)`` ``MeshData.vertices`` (absolute lon, lat, z in metres),
    ``tris`` the ``(M, 3)`` 0-based corners, ``attr`` the ``(M,)`` attributes. The kept rows
    are those with ``attr != 0 and attr & 7 != 0``, which is Ortho4XP's first filter
    (``O4_Mask_Utils.py:474-482``) before the ``use_masks_for_inland`` policy -- that policy
    belongs to the masks stage and is not applied here.

    The barycentre is ``(l1 + l2 + l3) / 3`` per axis, in corner order and left to right, the
    same doubles Ortho4XP computes from the text ``.mesh``.
    """
    values = np.asarray(attr, dtype=np.int64).reshape(-1)
    bits = values & HAS_WATER
    keep = np.flatnonzero((values != 0) & (bits != 0)).astype(np.int32)
    corners = np.ascontiguousarray(tris[keep], dtype=np.int32)
    lon = vertices[corners[:, 0], 0] + vertices[corners[:, 1], 0] + vertices[corners[:, 2], 0]
    lat = vertices[corners[:, 0], 1] + vertices[corners[:, 1], 1] + vertices[corners[:, 2], 1]
    bary = np.empty((keep.size, 2), dtype=np.float64)
    bary[:, 0] = lon / 3
    bary[:, 1] = lat / 3
    return WaterTris(
        tile=tile,
        tri_index=keep,
        corners=corners,
        water_bits=bits[keep].astype(np.uint8),
        attr=values[keep].astype(np.uint8),
        bary=bary,
    )


def write_water_tris_npz(path: str | Path, table: WaterTris) -> None:
    """Write ``water_tris.npz`` (spec section 7), ``.tmp`` + rename."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez(
            f,
            format=np.array(WATER_TRIS_FORMAT),
            tile=np.array(table.tile),
            tri_index=table.tri_index,
            corners=table.corners,
            water_bits=table.water_bits,
            attr=table.attr,
            bary=table.bary,
        )
    tmp.replace(path)


def read_water_tris_npz(path: str | Path) -> WaterTris:
    """Read a ``water_tris.npz``; a foreign format string is a ``ValueError``."""
    path = Path(path)
    if path.is_dir():
        path = path / "water_tris.npz"
    with np.load(path, allow_pickle=False) as z:
        fmt = str(z["format"]) if "format" in z else ""
        if fmt != WATER_TRIS_FORMAT:
            raise ValueError(f"{path}: format {fmt!r}, expected {WATER_TRIS_FORMAT!r}")
        return WaterTris(
            tile=str(z["tile"]),
            tri_index=z["tri_index"],
            corners=z["corners"],
            water_bits=z["water_bits"],
            attr=z["attr"],
            bary=z["bary"],
        )
