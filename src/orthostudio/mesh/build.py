# OrthoStudio XP, Copyright (C) 2026 radioharris. Free software under the GNU GPL: see LICENSE.
# Additional terms (GPL v3 section 7) apply to radioharris's material in this file: see NOTICE.
"""The native mesh stage: weight map, Triangle4XP sidecar, post-processing, ``.mesh``.

Spec: ``docs/specs/mesh-build.md``. Origin: ``O4_Mesh_Utils.py:540-783`` (``build_mesh``).

This module replaces stage 2 of Ortho4XP. It reads the PSLG of the vector stage
(``Data<tile>.node`` + ``Data<tile>.poly``) and the elevation raster of the DEM stage
(``Data<tile>.alt``), runs the Triangle4XP sidecar with Ortho4XP's exact command line (binary
exchange, ADR 0004), post-processes the altitudes and writes ``Data<tile>.mesh``, its npz
twin, the water table the masks stage consumes and a ``stats.json``.

It never downloads anything: the coastline used by the weight map is an argument (Ortho4XP
re-queries Overpass in the middle of its stage 2).
"""

from __future__ import annotations

import contextlib
import json
import math
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from orthostudio.errors import OsxpError
from orthostudio.fsutil import NO_CONSOLE_WINDOW
from orthostudio.mesh import triangle_io as tio
from orthostudio.mesh.mesh_file import MeshData, write_mesh_npz, write_mesh_text
from orthostudio.mesh.postprocess import (
    WaterTris,
    classify_triangles,
    post_process_altitudes,
    water_tris_table,
    write_water_tris_npz,
)
from orthostudio.mesh.weights import (
    LAT_TO_M,
    build_weight_map,
    read_airport_bounds,
    weight_stats,
    write_weight_file,
)
from orthostudio.model import TileRef

__all__ = [
    "DEFAULT_TRIANGLE_BIN",
    "DEM_SPEC_FORMAT",
    "STATS_FORMAT",
    "DemSpec",
    "MeshBuildOptions",
    "MeshBuildResult",
    "build_mesh_native",
    "mesh_file_name",
    "triangle_command",
]

DEM_SPEC_FORMAT = "osxp-dem-1"
STATS_FORMAT = "osxp-mesh-stats-1"
DEFAULT_TRIANGLE_BIN = (
    Path(__file__).resolve().parents[3]
    / "native/triangle4xp/build"
    / ("Triangle4XP.exe" if sys.platform == "win32" else "Triangle4XP")
)
"""``native/triangle4xp/build/Triangle4XP`` (``.exe`` on Windows) relative to the repository,
for dev runs."""

_SRTM_BASE = 3601
"""Samples per degree of the View / SRTM 1" grid (``O4_DEM_Utils.py:355``)."""


def mesh_file_name(tile: TileRef) -> str:
    """``Data<tile>.mesh`` (``O4_File_Names.py:192-193``)."""
    return f"Data{tile.name}.mesh"


# -- the DEM window ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DemSpec:
    """What Triangle4XP needs to know about the elevation raster (spec section 2.2).

    ``x0, y0, x1, y1`` are the corners of the DEM window in **tile-relative degrees** (the
    View / SRTM grid overshoots the tile by 36 samples, hence ``-0.01 .. 1.01``), ``nodata``
    the value that means "no sample here", ``alt_path`` the raw ``nydem x nxdem`` float32
    raster, row 0 = north.
    """

    alt_path: Path
    nxdem: int
    nydem: int
    x0: float
    y0: float
    x1: float
    y1: float
    nodata: float
    epsg: int = 4326

    def check(self, tile: str = "") -> None:
        """Raise ``MESH_INPUT_MISSING`` when the raster is absent or shorter than announced."""
        expected = 4 * self.nxdem * self.nydem
        if not self.alt_path.is_file():
            raise OsxpError("MESH_INPUT_MISSING", context={"path": self.alt_path, "tile": tile})
        size = self.alt_path.stat().st_size
        if size < expected:
            raise OsxpError(
                "MESH_INPUT_MISSING",
                context={"path": self.alt_path, "tile": tile, "size": size, "expected": expected},
                message=f"{self.alt_path} holds {size} bytes, the DEM window needs {expected}.",
                remedy="Rebuild the elevation stage of this tile with the same custom_dem.",
            )

    @classmethod
    def infer(cls, alt_path: str | Path) -> DemSpec:
        """Guess the View / SRTM window from the size of ``Data<tile>.alt`` (blocage B1).

        ``O4_DEM_Utils.py:354-362``: ``nxdem = nydem = 3601 + 2 * 36`` samples over
        ``[-0.01, 1.01]``, ``nodata = -32768``. Exact for the default source, wrong for ALOS
        (3672 samples, half-pixel offset) or a user GeoTIFF: those need ``dem.json``.
        """
        path = Path(alt_path)
        if not path.is_file():
            raise OsxpError("MESH_INPUT_MISSING", context={"path": path, "tile": ""})
        samples = path.stat().st_size // 4
        side = round(math.sqrt(samples))
        if side * side * 4 != path.stat().st_size or (side - _SRTM_BASE) % 2:
            raise OsxpError(
                "MESH_INPUT_MISSING",
                context={"path": path, "tile": ""},
                message=f"{path} is not a square 1″ elevation raster ({samples} samples).",
                remedy="The elevation stage must publish a dem.json next to Data<tile>.alt.",
            )
        margin = (side - _SRTM_BASE) / 2 / (_SRTM_BASE - 1)
        return cls(
            alt_path=path,
            nxdem=side,
            nydem=side,
            x0=-margin,
            y0=-margin,
            x1=1 + margin,
            y1=1 + margin,
            nodata=-32768.0,
        )

    @classmethod
    def from_dir(cls, dem_dir: str | Path, tile: TileRef, *, iterate: int = 0) -> DemSpec:
        """Read ``dem.json`` if the DEM stage published one, else :meth:`infer`."""
        directory = Path(dem_dir)
        suffix = f".{iterate}" if iterate else ""
        alt_path = directory / f"Data{tile.name}{suffix}.alt"
        if not alt_path.is_file():  # Ortho4XP drops the suffix when the round has no own raster
            alt_path = directory / f"Data{tile.name}.alt"
        spec_path = directory / "dem.json"
        if not spec_path.is_file():
            # orthostudio.dem@1 publishes the same fields under the name of its own metadata file
            # (``dem.md`` 9); either name is accepted, the format string is what is checked.
            spec_path = directory / "meta.json"
        if spec_path.is_file():
            payload = json.loads(spec_path.read_text(encoding="utf-8"))
            if payload.get("format") != DEM_SPEC_FORMAT:
                raise ValueError(f"{spec_path}: format {payload.get('format')!r}")
            return cls(
                alt_path=alt_path,
                nxdem=int(payload["nxdem"]),
                nydem=int(payload["nydem"]),
                x0=float(payload["x0"]),
                y0=float(payload["y0"]),
                x1=float(payload["x1"]),
                y1=float(payload["y1"]),
                nodata=float(payload["nodata"]),
                epsg=int(payload.get("epsg", 4326)),
            )
        return replace(cls.infer(alt_path), alt_path=alt_path)


# -- options and result ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MeshBuildOptions:
    """Exactly the parameters the mesh computation consumes (spec section 9)."""

    curvature_tol: float = 2.0
    apt_curv_tol: float = 0.5
    apt_curv_ext: float = 0.5
    coast_curv_tol: float = 1.0
    coast_curv_ext: float = 0.5
    limit_tris: float = 3.0
    min_angle: float = 10.0
    sea_smoothing_mode: str = "zero"
    water_smoothing: int = 10
    iterate: int = 0
    skip_multiples_of_ten: bool = True
    water_in_set_order: bool = True


@dataclass(slots=True)
class MeshBuildResult:
    """What :func:`build_mesh_native` produced."""

    mesh: MeshData
    water: WaterTris
    weight: NDArray[np.float32]
    argv: list[str]
    stats: dict[str, object]
    warnings: list[OsxpError] = field(default_factory=list)
    mesh_path: Path | None = None


# -- the command line --------------------------------------------------------------------------


def steiner_budget(limit_tris: float, input_nodes: int) -> float:
    """``max_steiner`` of ``O4_Mesh_Utils.py:640-650``, arithmetic included.

    ``limit_tris`` is in millions; a value outside ``(0, 50)`` falls back to 5 M triangles,
    and the budget never drops below 500 000 Steiner points.
    """
    try:
        max_tris = float(limit_tris) * 1e6
    except (TypeError, ValueError):
        max_tris = 5e6
    if max_tris <= 0 or max_tris >= 5e7:
        max_tris = 5e6
    return max(max_tris / 1.9 - input_nodes, 5e5)


def triangle_switches(
    options: MeshBuildOptions, input_nodes: int, *, binary: bool, min_angle: float | None = None
) -> str:
    """``-pq{min_angle}{A|r}uYBQP[b]S{steiner}`` (spec section 4)."""
    angle = options.min_angle if min_angle is None else min_angle
    do_refine = "r" if options.iterate else "A"
    return (
        f"-pq{angle:.9g}{do_refine}uYBQP{'b' if binary else ''}"
        f"S{steiner_budget(options.limit_tris, input_nodes)}"
    )


def triangle_command(
    triangle_bin: str | Path,
    switches: str,
    *,
    lat: int,
    dem: DemSpec,
    curvature_tol: float,
    alt_path: str | Path,
    weight_path: str | Path,
    poly_path: str | Path,
) -> list[str]:
    """The 15-element command line of ``O4_Mesh_Utils.py:658-673`` (spec section 4).

    ``{:n}`` on the DEM dimensions is replaced by ``str(int(...))``: Ortho4XP's format is
    locale-dependent and would emit ``3,673`` under a grouping locale.
    """
    return [
        str(triangle_bin),
        switches,
        f"{LAT_TO_M * math.cos(math.pi * lat / 180):.9g}",
        f"{LAT_TO_M:.9g}",
        str(int(dem.nxdem)),
        str(int(dem.nydem)),
        f"{dem.x0:.9g}",
        f"{dem.y0:.9g}",
        f"{dem.x1:.9g}",
        f"{dem.y1:.9g}",
        f"{dem.nodata:.9g}",
        f"{curvature_tol:.9g}",
        str(alt_path),
        str(weight_path),
        str(poly_path),
    ]


# -- the stage ---------------------------------------------------------------------------------


SIDECAR_POLL_S = 0.1
"""How often the sidecar is polled while it runs (cancellation latency)."""


def _run_sidecar(
    argv: list[str], timeout_s: float, cancel: threading.Event | None = None
) -> subprocess.CompletedProcess[str]:
    """Run Triangle4XP, polling so that a cancelled build can actually stop it.

    ``subprocess.run`` blocks until the child exits, so a Ctrl-C left the sidecar running for
    up to ``timeout_s`` (review 4, finding C4).
    """
    if cancel is None:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
            creationflags=NO_CONSOLE_WINDOW,
        )
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=NO_CONSOLE_WINDOW,
    )
    deadline = time.monotonic() + timeout_s
    try:
        while proc.poll() is None:
            if cancel.is_set():
                _kill_sidecar(proc)
                raise OsxpError("SYS_CANCELLED", context={"stage": "mesh", "argv": argv[0]})
            if time.monotonic() >= deadline:
                _kill_sidecar(proc)
                raise subprocess.TimeoutExpired(argv, timeout_s)
            time.sleep(SIDECAR_POLL_S)
    except BaseException:
        _kill_sidecar(proc)
        raise
    out, err = proc.communicate()
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _check_cancelled(cancel: threading.Event | None) -> None:
    """Raise ``SYS_CANCELLED`` between two stages of the build, so nothing gets committed."""
    if cancel is not None and cancel.is_set():
        raise OsxpError("SYS_CANCELLED", context={"stage": "mesh"})


def _kill_sidecar(proc: subprocess.Popen[str]) -> None:
    """SIGTERM then SIGKILL; the sidecar has no children of its own."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(OSError):
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
            return
        except subprocess.TimeoutExpired:
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2.0)


def _stem(tile: TileRef, iterate: int) -> str:
    return f"Data{tile.name}.{iterate}" if iterate else f"Data{tile.name}"


def _prepare_inputs(
    tile: TileRef, vectors_dir: Path, workdir: Path, *, iterate: int, binary: bool
) -> tuple[Path, int, int]:
    """Copy or convert ``.node`` / ``.poly`` into ``workdir``; return the poly path and counts."""
    stem = _stem(tile, iterate)
    node_src = vectors_dir / f"{stem}.node"
    poly_src = vectors_dir / f"{stem}.poly"
    for path in (node_src, poly_src):
        if not path.is_file():
            raise OsxpError("MESH_INPUT_MISSING", context={"path": path, "tile": tile.name})
    nodes = tio.read_node(node_src)
    pslg = tio.read_poly(poly_src)
    if binary:
        tio.write_node_binary(workdir / f"{stem}.node", nodes)
        tio.write_poly_binary(workdir / f"{stem}.poly", pslg)
    else:
        shutil.copyfile(node_src, workdir / f"{stem}.node")
        shutil.copyfile(poly_src, workdir / f"{stem}.poly")
    if iterate:  # -r also reads the previous .ele
        ele_src = vectors_dir / f"{stem}.ele"
        if ele_src.is_file():
            shutil.copyfile(ele_src, workdir / f"{stem}.ele")
    return workdir / f"{stem}.poly", nodes.n_vertices, pslg.n_segments


def build_mesh_native(
    tile: TileRef,
    vectors_dir: str | Path,
    dem: DemSpec,
    params: MeshBuildOptions,
    triangle_bin: str | Path = DEFAULT_TRIANGLE_BIN,
    workdir: str | Path = ".",
    *,
    out_dir: str | Path | None = None,
    coast_nodes: NDArray[np.float64] | None = None,
    apt_bounds: NDArray[np.float64] | None = None,
    binary: bool | None = None,
    timeout_s: float = 1800,
    cancel: threading.Event | None = None,
) -> MeshBuildResult:
    """Run stage 2 natively and, when ``out_dir`` is given, write the artefact into it.

    ``vectors_dir`` holds ``Data<tile>.{node,poly}`` (and ``airports.json`` when the tile has
    airports); ``dem`` describes ``Data<tile>.alt``; ``workdir`` is a scratch
    directory (the ``.weight`` and the sidecar's outputs land there). ``coast_nodes`` is the
    ``(C, 2)`` array of absolute ``(lon, lat)`` coastline nodes -- absent means "no coastline
    refinement", recorded as a degraded ``MESH_WEIGHT_MAP_INCOMPLETE``.

    ``binary`` forces the exchange format with the sidecar; the default (``None``) is the
    binary one except when refining, which ``Triangle4XP.c:3448`` refuses to combine with
    ``-b``. The text mode is kept for debugging and for the equality test of the two.

    ``cancel`` is the scheduler's token: it is polled while the sidecar runs and between the
    stages of the post-processing, and it makes the function raise ``SYS_CANCELLED`` (so the
    store never commits a partial mesh) after killing the child.

    Returns the :class:`MeshBuildResult`; raises :class:`OsxpError` (``MESH_INPUT_MISSING``,
    ``MESH_TRIANGULATION_FAILED``, ``SYS_CANCELLED``) on a blocking failure.
    """
    vectors_dir = Path(vectors_dir)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    warnings: list[OsxpError] = []
    timings: dict[str, float] = {}
    t_total = time.perf_counter()
    if binary is None:
        binary = not params.iterate
    elif binary and params.iterate:
        raise ValueError("Triangle4XP refuses the binary exchange (-b) while refining (-r)")
    dem.check(tile.name)

    # -- inputs
    t = time.perf_counter()
    poly_path, input_nodes, input_segments = _prepare_inputs(
        tile, vectors_dir, workdir, iterate=params.iterate, binary=binary
    )
    timings["inputs"] = time.perf_counter() - t

    # -- weight map
    t = time.perf_counter()
    if apt_bounds is None:
        apt_bounds = read_airport_bounds(vectors_dir, tile.name)
    if apt_bounds.size == 0 and params.apt_curv_tol != params.curvature_tol:
        warnings.append(
            OsxpError(
                "MESH_WEIGHT_MAP_INCOMPLETE",
                context={"tile": tile.name, "reason": "no airport boundary available"},
            )
        )
    if coast_nodes is None and params.coast_curv_tol != params.curvature_tol:
        warnings.append(
            OsxpError(
                "MESH_WEIGHT_MAP_INCOMPLETE",
                context={"tile": tile.name, "reason": "no coastline layer given"},
            )
        )
    weight = build_weight_map(
        lat=tile.lat,
        lon=tile.lon,
        curvature_tol=params.curvature_tol,
        apt_curv_tol=params.apt_curv_tol,
        apt_curv_ext=params.apt_curv_ext,
        coast_curv_tol=params.coast_curv_tol,
        coast_curv_ext=params.coast_curv_ext,
        apt_bounds=apt_bounds,
        coast_nodes=coast_nodes,
    )
    weight_path = workdir / f"Data{tile.name}.weight"
    write_weight_file(weight_path, weight)
    timings["weights"] = time.perf_counter() - t

    # -- sidecar
    switches = triangle_switches(params, input_nodes, binary=binary)
    argv = triangle_command(
        triangle_bin,
        switches,
        lat=tile.lat,
        dem=dem,
        curvature_tol=params.curvature_tol,
        alt_path=dem.alt_path,
        weight_path=weight_path,
        poly_path=poly_path,
    )
    t = time.perf_counter()
    proc = _run_sidecar(argv, timeout_s, cancel)
    retried = False
    if proc.returncode:
        # Ortho4XP means to retry without the angle constraint but overwrites mesh_cmd[-5], which
        # is `nodata` (O4_Mesh_Utils.py:711). OrthoStudio XP rebuilds the switch string instead.
        retried = True
        warnings.append(
            OsxpError(
                "MESH_QUALITY_RELAXED",
                context={"tile": tile.name, "min_angle": params.min_angle},
            )
        )
        argv[1] = triangle_switches(params, input_nodes, binary=binary, min_angle=0.0)
        proc = _run_sidecar(argv, timeout_s, cancel)
        if proc.returncode:
            raise OsxpError(
                "MESH_TRIANGULATION_FAILED",
                context={
                    "tile": tile.name,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-2000:],
                },
            )
    timings["triangle"] = time.perf_counter() - t
    _check_cancelled(cancel)

    # -- outputs of the sidecar
    stem = _stem(tile, params.iterate)
    out_stem = f"Data{tile.name}.{params.iterate + 1}"
    t = time.perf_counter()
    node_out = tio.read_node(workdir / f"{stem}.1.node")
    ele_out = tio.read_ele(workdir / f"{stem}.1.ele")
    timings["read_outputs"] = time.perf_counter() - t
    points = np.array(node_out.points, dtype=np.float64)  # a writable copy: post-processed
    if points.shape[1] < 6:
        raise OsxpError(
            "MESH_TRIANGULATION_FAILED",
            context={"tile": tile.name, "returncode": 0},
            message=f"Triangle4XP returned {points.shape[1]} columns per vertex, expected 6.",
            remedy="Rebuild native/triangle4xp; the sidecar must be the osxp build.",
        )
    if ele_out.attributes.shape[1] < 1:
        raise OsxpError(
            "MESH_TRIANGULATION_FAILED",
            context={"tile": tile.name, "returncode": 0},
            message="Triangle4XP returned no region attribute; the -A switch was lost.",
            remedy="Report this: the mesh command must carry -A (or -r when iterating).",
        )
    tris = np.ascontiguousarray(ele_out.triangles, dtype=np.int64) - ele_out.first_number
    attr = np.rint(ele_out.attributes[:, 0]).astype(np.int64)

    # -- post-processing
    t = time.perf_counter()
    classes = classify_triangles(attr, skip_multiples_of_ten=params.skip_multiples_of_ten)
    post_process_altitudes(
        points,
        tris.astype(np.int32),
        classes,
        water_smoothing=params.water_smoothing,
        sea_smoothing_mode=params.sea_smoothing_mode,
        water_in_set_order=params.water_in_set_order,
    )
    timings["postprocess"] = time.perf_counter() - t

    vertices = np.empty((points.shape[0], 3), dtype=np.float64)
    vertices[:, 0] = points[:, 0] + tile.lon
    vertices[:, 1] = points[:, 1] + tile.lat
    vertices[:, 2] = points[:, 2]
    mesh = MeshData(
        vertices=vertices,
        normals=points[:, 3:5].astype(np.float32),
        tris=tris.astype(np.int32),
        tri_attr=attr.astype(np.uint8),
    )
    water = water_tris_table(tile.name, mesh.vertices, mesh.tris, mesh.tri_attr)

    changed, weight_max = weight_stats(weight)
    limit = params.limit_tris * 1e6
    if limit > 0 and mesh.n_triangles >= 0.99 * limit:
        warnings.append(
            OsxpError(
                "MESH_TRIANGLE_BUDGET_REACHED",
                context={"tile": tile.name, "limit_tris": params.limit_tris},
            )
        )

    mesh_path: Path | None = None
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        t = time.perf_counter()
        mesh_path = out / mesh_file_name(tile)
        write_mesh_text(mesh_path, mesh)
        timings["mesh_text"] = time.perf_counter() - t
        t = time.perf_counter()
        write_mesh_npz(out / "mesh.npz", mesh)
        write_water_tris_npz(out / "water_tris.npz", water)
        timings["npz"] = time.perf_counter() - t
    if params.iterate:  # the next round reads the post-processed node table (TODO: untested)
        _write_post_nodes(workdir / f"{out_stem}.node", points)

    timings["total"] = time.perf_counter() - t_total
    stats: dict[str, object] = {
        "format": STATS_FORMAT,
        "tile": tile.name,
        "n_input_nodes": input_nodes,
        "n_input_segments": input_segments,
        "n_vertices": mesh.n_vertices,
        "n_triangles": mesh.n_triangles,
        "n_water_tris": len(water),
        "water_bits_counts": {str(k): v for k, v in water.counts.items()},
        "triangle_classes": classes.counts,
        "weight_cells_changed": changed,
        "weight_max": weight_max,
        "n_coast_nodes": int(coast_nodes.shape[0]) if coast_nodes is not None else 0,
        "n_airports": int(apt_bounds.shape[0]),
        "steiner_budget": steiner_budget(params.limit_tris, input_nodes),
        "triangle_argv": argv,
        "retried_without_min_angle": retried,
        "binary_exchange": binary,
        "timings_s": {k: round(v, 4) for k, v in timings.items()},
        "warnings": [{"code": w.code, "context": w.context} for w in warnings],
    }
    if out_dir is not None:
        (Path(out_dir) / "stats.json").write_text(
            json.dumps(stats, indent=1, default=str) + "\n", encoding="utf-8"
        )
    return MeshBuildResult(
        mesh=mesh,
        water=water,
        weight=weight,
        argv=argv,
        stats=stats,
        warnings=warnings,
        mesh_path=mesh_path,
    )


def _write_post_nodes(path: Path, points: NDArray[np.float64]) -> None:
    """Rewrite the ``.node`` table Ortho4XP feeds to the next ``iterate`` round (``:314-322``).

    TODO (spec section 4.2): the ``iterate`` path has no fixture and is not part of the
    fidelity claim.
    """
    n = points.shape[0]
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(f"{n}  2  {points.shape[1] - 2}  0\n")
        for i, row in enumerate(points[:, :6].tolist(), start=1):
            f.write(f"{i} " + " ".join(f"{x:.15f}" for x in row) + "\n")
        f.write("# Generated by OrthoStudio XP\n")
